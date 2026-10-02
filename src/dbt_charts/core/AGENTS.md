# dbt_charts/core

Engine internals: compile, resolve, execute, render, inspect, serve. The CLI and AI surfaces are thin wrappers over this. Read the package root `AGENTS.md` first for project-wide rules; this file covers core-specific invariants.

## Implementation philosophy

Everything below is reviewer-enforced. This section exceeds the usual length budget deliberately: core is the engine's invariant home.

### Module dependency direction

```
diagnostics/, warnings/, text/, utils/, dialects/, fonts.py, font_measure.py,
colors.py, aliases.py, links.py
    (leaf — no deps on compile/execute/render)
    ↑
compile/
    ↑
    ├── execute/   (imports compile/ for types)
    │       ↑
    └── render/    (imports compile/ + execute/)

serve/, inspect/   → compile + execute + render
cli/, ai/          → core modules (thin wrappers only)
```

`core/diagnostics/`, `core/text/`, `core/utils.py`, `core/dialects/`,
`core/fonts.py`, `core/font_measure.py`, `core/colors.py`, `core/aliases.py`,
and `core/links.py`
hold types and services shared by compile *and* render/execute — and, for
`aliases.py`, by any host that resolves a board's `aliases:` without running the
`serve/` HTTP application
(`DbtChartsError`, `ChartDataError`, `Diagnostic`, `slug_to_text`,
`to_plain_dict`, `is_year_shaped`, `SQLDialect`/`get_dialect`/`VALID_OPERATORS`,
strict font measurement, `sanitize_color`/`is_sanitizable_color`,
`link_scheme`/`is_safe_href`, …) — anything
`compile/` needs that would otherwise force `compile → render` or
`compile → execute`. `tach.toml` enforces `compile ↛ render` and
`compile ↛ execute`, except pre-existing compile→execute edges each carrying an
inline `# tach-ignore(...)` — accepted debt, not yet relocated (the remaining
edges are real adapter-class usage — `DbtAdapter`/`SqlAdapter` — not leaf
behavior; `sql_guard.py` lives inside `compile/` itself since its only real
dependency is `compile.config`).
`compile ↛ render` is no longer just
intent — it's structurally enforced by `dbt_charts.core.compile`'s `depends_on`
list (which omits `render`/`execute`) plus `tests/core/test_layering.py`.

The `dct mcp serve` lazy import of `run_server` in `dbt_charts.cli.main` is debt carrying a `# tach-ignore` — the remaining edge is `cli → ai`, whose fix is an `agent_api` entry point (burn-down task). Its optional-dependency gate already routes through `dbt_charts.cli._extras`.

No circular imports. If a violation is your ergonomic shortcut, the shortcut is the bug.

### Invalid states must be unrepresentable

The authored-schema goal is that a bad board **cannot be typed**, not that it gets caught later. Prefer scoping a field to the model/variant where it's meaningful — a discriminated-union member, `extra="forbid"`, a field declared only on the relevant chart family — over accepting it broadly and rejecting the bad combination with a runtime check. The chart-family enforcement in "Authored chart surface" below (`extra="forbid"` plus structural narrowing of `size`/`shape`/`conditional_formatting` to the families where they apply) is the canonical pattern.

Smell to flag in review: a bespoke `if chart.field and chart.type != "x": raise ...` (or any hand-written check) on a normalized or authored model. That's a sign the schema should have made the combination impossible to construct — narrow the field to a union member or a family-scoped mixin instead of policing it after the fact.

### Two validation boundaries

The codebase has TWO distinct validation boundaries. Knowing which one you're at decides whether to trust the input or validate it.

**1. Compile boundary (YAML → Board).** The normalizer (`dbt_charts/core/compile/normalize/dispatch.py`) validates all YAML configuration. After it returns, downstream code (execute, render, serve, inspect) **trusts** the result and does NOT re-validate.

```python
# WRONG — re-validating normalized data
def render_chart(chart: Chart):
    if chart.type not in VALID_TYPES:   # Normalizer already checked this
        raise ValueError("Invalid chart type")

# RIGHT — trust the normalizer
def render_chart(chart: Chart):
    return RENDERERS[chart.type](chart)
```

If you need a guarantee downstream, strengthen the normalizer — don't add defensive code in render.

**2. Runtime boundary (user input → query execution).** Variable values from UI / API / CLI are NOT seen by the normalizer. They enter at query-execution time and MUST be validated where they're consumed (Jinja filters, query parameters, adapter calls).

```python
def filter_date_range(column: str, date_range: Any) -> str:
    # date_range comes from the user at runtime, not the normalizer.
    if not isinstance(date_range, (list, tuple)) or len(date_range) != 2:
        raise ValueError(f"date_range must be [start, end], got {date_range!r}")
    start, end = date_range
    return f"{column} BETWEEN '{start}' AND '{end}'"
```

Summary: normalized data → trust it; runtime user input → validate at point of use, fail fast on bad input. Don't conflate the two.

Compile-stage invariants (variables board-global, one layout per board, ids from tree position) live in `compile/AGENTS.md` — auto-loaded when you edit that package.

### Project-file access goes through `Project`, never raw `Path`

`Project` / `ProjectPath` / `ProjectDirectory` (`core/project.py`) model a project; core reads/writes project content (board YAML, `meta.yml`, extends/includes, data files) only through these handles — a raw `Path` read hard-codes local disk and breaks under any other store.

- Never touch the filesystem for a project path: no `Path.read_text()` / `.write_text()` / `.exists()` / `open()` / `os.walk` — go through a `Project` handle.
- `Project` is an ABC with no filesystem default: `read_text`, `read_bytes`, `exists`, `iter_files`, `write_text`, `sources`, and `config_document()` are abstract, so each host supplies its own store. Hosts: `FilesystemProject` (`dbt_charts.cli.filesystem_project`, built at the `dbt_charts.cli` composition root, imported by core only under `TYPE_CHECKING`) and Cloud's `CloudManagedProject` (git-blob store).
- Adding a file-access method? Declare it abstract — the type checker then rejects any host that doesn't implement it.
- The base `Project` holds no `Path`: `root`, `charts_dir`, `path_for_fspath`, `directory_for_fspath`, `dbt_root`, `dbt_project`, and `data_path()` live only on `FilesystemProject`, and a base-typed read of them is a pyright error. `data_path()` is for the execution layer materializing data files — don't use it to turn a relpath into a disk path for reading.
- Project config comes from `Project.config_document()` (base default `None`; `FilesystemProject` looks for `dbt_charts.yml`); there is no `config_file` on the base.
- File identity is the POSIX relpath (`ProjectPath.relpath`). Use `PurePosixPath` for lexical path math (joins, `.name` / `.parent` / `.suffix`, comparison); `pathlib.Path` is banned in core. Never rebuild an absolute `Path` for identity, cache keys, or cycle detection.
- Special-casing the filesystem host on a base-`Project` param uses an `isinstance(project, FilesystemProject)` narrow with a `# tach-ignore(core->cli: host-type guard; …)` marker (`core/execute/adapters/adapter_registry.py`, `core/compile/normalize/queries.py`); a runtime `FilesystemProject` construction/import from core is a tach break.

**Mechanically enforced.** `core/ruff.toml` `TID251` bans `pathlib.Path` + disk sinks at the import; `tests/core/test_no_disk_io_in_core.py` bans builtin `open()`; `tests/core/test_no_base_project_fspath_reads.py` bans base-typed reads of the moved members (ratcheting on top of the type checker). A sanctioned edge carries a reasoned `# noqa: TID251 — <why>` / `ALLOWED_OPEN` / allowlist entry — empty is the goal, and a bare noqa or unreasoned widening is review-blocking.

### Package assets: `importlib.resources`, never `Path(__file__)`

Read shipped package data via `files("dbt_charts.<pkg>").joinpath(...).read_text(encoding="utf-8")` / `.read_bytes()`; `iterdir()` for directories (no `glob`); `PackageLoader` for Jinja. Exception: `core/fonts.py` hands a real directory to foreign libs (StaticFiles, vl-convert, ReportLab/fontTools/PIL), so it resolves one via `importlib_resources.as_file()`, funneled through `get_fonts_dir()`.

### Patterns to never write

- `default_factory=lambda: <literal>` — same antipattern as `= <literal>`, just disguised. Distinct from `default_factory=SomeStyleType` (which is correct for fully-defaulted containers). No exceptions; add new cases only with explicit rationale and reviewer sign-off.
- Hand-written all-Optional duplicates of a style/config type. Use `build_patch_model()`.
- Process-global state: `@cache` / `lru_cache` / a module-level `dict`/`set`/`list` filled at runtime, keyed on a caller-supplied object, `id()`, a board, a query, or any authored value. Entries live for the life of long-lived hosts (`dct serve`, the LSP); "today's callers only pass X" is not a bound. A memo is allowed only when zero-argument or keyed on a fixed finite set (a theme or font name) over immutable package data. Otherwise precompute into the owning context object (the migration context, `Board`, a resolved model) and pass it, or don't cache. Existing `@cache` sites are not precedent.
- `_apply_legacy_*`, `_legacy_*_to_*`, `*_compat`, deprecation shims, `# deprecated`, `# backwards compat`, `# legacy alias`. Internal code carries no aliases — rename in one PR and delete the old path; a user-facing board-grammar change carries a parse-time migration instead of a shim.
- `assert not hasattr(...)` / `field not in model_fields` / source-grep for removed symbols. Tests Python introspection, not our logic. Banned.
- Test-side re-implementations of production formulas (`def _n_cols(...) — mirrors render-time computation`). The test validates its own copy. Extract a single helper, import it from the test.
- Dual mappers for the same concept (`axis_to_vl` AND `_axis_patch_to_vl`). One canonical mapper per concept.
- Silent stringification or fallback when data shape is wrong. `str(chart.value) if isinstance(chart.value, (int, float)) else ...` is exactly this. Raise `ChartDataError` instead.

### Engine config: narrow getters preferred

`compile/config.py` defines narrow getters for engine config slices:
- `get_chart_rendering()` — chart engine constants (kpi, spark_bar, facet, pie, bar, type_inference, frame, support_table subsections); `preferred_width` is a theme value on `resolved_style.chart_defaults.preferred_width` (or per-family, on `ChartStyleContext` during runtime chart resolution)
- `get_inspector_config()` — inspector / directory-tree config (e.g. `tree_max_depth`)
- `get_rendering_config()` — rendering metadata (timestamp, png scale)
- `get_terminal_config()` — terminal renderer constants

Render-layer code reading engine config should call the narrow getter, not the whole-config `get_config()`. Style reads go through `resolve_style(get_theme_style())` instead — that's the cascade-aware path. Markdown prose colors are not engine config: `get_compact_style()` derives them from the resolved theme style (no `get_markdown_config()` getter). Don't add a `get_placeholder()` style narrow getter; style is theme-cascaded, not engine-config.

**New engine-internal numeric constants belong in `default_config.yml`, not as bare Python `UPPER_CASE` module constants.** Add the value under the appropriate `chart_rendering.*` subsection (or `inspector.*` for directory/inspection knobs), type it in the corresponding `ConfigNode` subclass in `compile/models/config.py`, and read it via the narrow getter. This makes the constant overridable in `dbt_charts.yml` and visible to introspection without a code change.

Worked example — the `chart_rendering.pie.*` group (wedge label thresholds, placement fractions): values live in `default_config.yml` under `chart_rendering.pie:`, typed on `ChartRenderingConfig.PieConfig(ConfigNode)` in `models/config.py`, read in `render/chart/arc_attached_table.py` via `get_chart_rendering().pie.wedge_label_min_share`. No bare `WEDGE_LABEL_MIN_SHARE` constant in Python source.

Markdown prose colors (`code_background`, `blockquote_border_color`, etc.) are now theme tokens under `style.text.code` and `style.text.blockquote` — read them from `resolve_style(get_theme_style()).text.*`, not from a config getter.

### Theme YAML completeness

When you promote a theme-populated field to required, the theme YAML must supply it for every theme. The theme corpus smoke test (`get_theme_style(name)` over every built-in theme) catches misses.

`defaults/themes/stark.yaml` is the structural root that all built-in themes inherit transitively via `extends` (`clarity` → `stark`; `paper` → `clarity` → `stark`; `vivid`/`neon` → `stark`). Add new defaults there; the cascade propagates them everywhere unless overridden. The shipped user-facing default is `clarity.yaml`, which adds the editorial voice on top of `stark`.

### Render layer

Render-layer invariants live in `render/chart/AGENTS.md` (its `## Implementation philosophy`) — re-read it before adding anything that touches data shape. Quick highlights:

- All chart-local axis variants go through encoding-level (`resolved_axis_style()` in `compile/resolve/style/axis_cascade.py`), not config-level. No half-state. The cascade runs theme tier (1 global, 2 channel, 3 quantitative, 4 chart-type), then a label-forced title default (5), then board tier (6-9, the author's own `style.charts.*` in the same slot order), then the chart's own format fallback (10), then chart-local patches (11-13). Layer 4 sits after type-conditional so chart-type patches win over `axis_quantitative`/`axis_band`; the board tier sits above the whole theme tier so an authored leaf is never overwritten by a theme default, and the label default sits below the board tier so a board-level `title.visible: false` still wins.

#### Compile → render boundary: render does not reach back

**Data stages: authored → normalized → resolved → rendered.** *Resolve* turns
normalized models into `Resolved*` (`ResolvedChart`/`ResolvedStyle`) — cascade
complete, config baked, frozen. The **resolved boundary** is that `Resolved*`
contract (the output of the compile phase); render sits behind it. ("compile" is an
overloaded word here — the `compile/` package spans authored→normalized→resolved; the
line render consumes is the *resolved* one. The separate **compile/validation
boundary** — YAML→normalized — is the trust gate in "Two validation boundaries".)

Render consumes `Resolved*` plus the rows used to emit foreign targets. One
question classifies every computation:

> **Was the input available before this `Resolved*` was constructed?**
> - **Yes** → the decision belongs baked in `Resolved*`. Final resolution may run
>   after query rows, the final slot width, and neutral font metrics are available;
>   data-aware layout based on those inputs is resolution work, not render work.
> - **No** → compute the factual value in render and flow it forward without
>   reconstructing or copying a `Resolved*` model.

Recomputing a known dbt charts value at render by reaching into
`compile.palette` / `style_cascade` / `channel` remains a **reach-back —
a boundary violation.** Normalization happens before runtime inputs; final
resolution is the last opportunity to make semantic and presentation decisions.

**Translation is not reach-back either.** Turning an *already-resolved* dbt charts value
into a **foreign target's** vocabulary (Vega-Lite property names / marks, MIME types)
is render's own job and **stays local to render** — do NOT push VL vocabulary into
`Resolved*`/compile (that couples the canonical resolved contract to one renderer; we
have more than one render target). The ban is on re-deriving *dbt charts* values, never
on translating resolved values to a foreign format.

The sanctioned compile touch-points from render are the **narrow engine-config
getters** (`get_chart_rendering()`, `get_rendering_config()`, `get_terminal_config()`)
for process constants, plus two small-multiples modules render reads directly rather
than duplicating: `compile.resolve.chart.adaptive_stroke` (`facet_panel_width()`,
`panel_axis_cardinality()` — the one per-panel-geometry formula, shared so resolve's
baked adaptive stroke and render's actual panel box can never drift) and
`compile.resolve.chart._chart_rows` — the small-multiples panel split and its
row-level combinators: `ChartDataset`, `PanelRows` (the data types), `regroup()`
(`chart_rows()` in `render/chart/feature.py` calls `regroup(chart.panel_axes, ...)`
to replay the partition `resolve()` already baked, not to decide one), `restripe()`
(re-splits a render-time-mutated flat row list back into that same baked partition
by recorded index), `restamp()` (re-stamps a partition field's value onto a panel's
rows when a caller needs it back on the row dict), and `map_panels()` (runs a
transform once per panel and reassembles — gap-fill's per-panel completion). Plus
`compile.resolve.chart.plot_height_floor` (`plot_height_floor_px()` — the one
definition of the plot-height floor, shared so the resolver that decides a plot is
starved and the warning that prints the floor to the author can never disagree;
same shape as `facet_panel_width()` above). Plus `compile.resolve.chart._wide_fields`
— the wide fold's synthetic field names (`WIDE_LABEL_FIELD`, `WIDE_VALUE_FIELD`,
`WIDE_KEY_FIELD`, `WIDE_ORDER_FIELD`, `WIDE_SERIES_SEPARATOR`) and its Python mirror
(`unfold_wide_rows()`, `wide_series_names()` (measures, dimension, data,
wide_measure_labels), `wide_dimension_values()`, `raw_wide_series_names()`,
`humanize_wide_series_name()`, `wide_measure_labels_for()`, `wide_legend_aliases()`,
`resolve_wide_measure_labels()`): the one definition of how `y: [a, b]` (crossed with
a `color:` dimension) becomes series, shared so the resolver's rail-crowding check,
the emitters' VL fold, and the endpoint-label feature can never name a wide chart's
series differently. All eight are data types
/ pure formulas keyed off a `Resolved*` field or an already-baked partition, not a
reach-back into cascade logic — see `render/chart/AGENTS.md` philosophy #3 for why
this doesn't reopen the data-belongs-to-queries rule. Don't add further reach-backs
beyond these eight; if you need a dbt charts value at render time, add it to
`Resolved*` at the resolved boundary.

**Enforced, empty, no allowlist.** `tests/core/render/chart/test_render_boundary.py`
scans every `.py` file under `render/` (not just `render/chart/`) for the banned
imports (`compile.resolve.chart.channel`, `compile.resolve.style.palette`,
`compile.resolve.style.axis_cascade`, `compile.resolve.style.chart_context`,
`compile.resolve.style.scale`, `compile.resolve.chart.enrich`, plus
reintroduction guards for deleted compile/v1-render modules) and fails
immediately on any hit — there is no allowlist. The one
structural exclusion is `render/terminal.py` + `render/terminal_charts.py`:
dct's terminal/CLI preview renders a *normalized* `Chart` directly and never
constructs or receives a `Resolved*` chart, so the reach-back question above
("was this available before `Resolved*` was constructed?") has no `Resolved*`
to apply against there — a structurally different rendering surface, not a
tracked exception. `sanitize_color`/`is_sanitizable_color` (CSS/SVG color
validation) and `is_year_shaped` (value-shape classification) live in the
neutral `dbt_charts.core.colors` and `dbt_charts.core.utils` modules, so render's
imports of them are not boundary violations. Axis style, support-table format
inheritance, dark-companion palette ink, callout tone colors, table
pagination, layout padding, and scale-palette hex stops are each a field on a
`Resolved*` contract (`ResolvedStyle`, `ResolvedChartDefaults`, or a
per-family `Resolved*Style`), baked once at resolve time; render reads the
field directly and never recomputes it. Four render modules legitimately read
the non-Resolved `ChartStyleContext`, all for the same reason — each drives
runtime chart resolution (calls `resolve()`/`resolve_chart_with_runtime_inputs`),
not mechanical emission, so each sits on the execute-orchestration side of the
boundary described in `compile/models/AGENTS.md`'s `ChartStyleContext` note:
`render/layout_sizing.py`'s sizing pass (via `SizingRenderCtx.chart_style_context`,
sourced from `Board.chart_style_context`); `render/board_resolve.py`'s resolvers,
which pass `board.chart_style_context` into `resolve()` and into
`resolve_chart_with_runtime_inputs` to build a `ResolvedBoard` — both are
runtime-resolution entry points, which is the sanctioned reason;
`render/board_to_dict.py`'s
row-truncated chart resolution, which resolves against `resolve_chart_style_context(get_theme_style())`;
and the two documented `render/chart/vega_lite.py` entry points, `render_chart()`
and `generate_vega_lite_spec()`, which take it as a parameter for the same
runtime-resolution purpose rather than reaching for it themselves. Every other
render module reads `resolved.style.*` off the already-resolved chart its
caller holds rather than calling `build_chart_style_context` itself.

### A board is a picture — interaction never ships inside it

A render's SVG is the same bytes on a live page, in a PNG/PDF export, and in a
golden, so nothing that only matters when a user can interact goes in it: no
`:hover`/`:focus`/`cursor`/`pointer-events`/`transition`, no `.dbt-interactive`
rule, no inline `style=`, no script. Interaction lives with the host:
`controls_stylesheet()` (shipped by every host, `to_html` included) and
`controls_runtime_source()` (the chart-hover runtime bundled ahead of the
controls runtime). The board only *publishes* what a host binds to — a class
(`dbt-pointer-inert`, `dbt-page-target`), a CSS custom property for a per-board
color (`--dbt-link`), a `data-dbt-*` attribute naming what a control drives
(`data-dbt-page-var`; the hover runtime's theme facts on the root) — never an
`onclick` or a class minted per color. Guard:
`tests/core/render/test_board_stylesheet_is_static.py`. One exception:
`table_pagination.js`, embedded only in a static multi-page export so a
download can page with no host (whether downloads should carry interaction at
all is a filed follow-up).

### Resolved models are read-only in render

Render is a strict consumer of `Resolved*` — it must never construct a mutated copy of one. `model_copy(update=...)` or `dataclasses.replace(...)` on a `ResolvedChart`/`ResolvedStyle` (or any nested field) inside `render/` is the reach-back above in disguise: it patches a value after the fact instead of getting it right at the resolved boundary. If render needs a different value, fix resolution (`compile/resolve/style/` or wherever the field is baked) — don't build a second, edited copy of the resolved object downstream.

**Enforced, empty, no allowlist.** `tests/test_no_replace_on_resolved.py` AST-scans every `.py` file under `src` — render included — for any `.model_copy(...)` or `dataclasses.replace(...)` call whose operand is a locally-tracked `Resolved*`-typed value, and fails immediately on a hit. It supersedes the render-only, type-blind guard this section used to cite (`test_no_resolved_model_copy.py`, deleted): that one banned every `model_copy`/`replace` call under `render/` regardless of operand type, which happened to match today's code but stated the rule more bluntly than intended. `compile/` is not exempted by directory this time — the guard is type-aware instead: `model_copy`/`replace` remains the sanctioned mechanism for *building* `ChartStyleContext` (non-`Resolved*`) during cascade resolution there, and the guard simply never flags a non-`Resolved*`-typed operand.

### Authored chart surface — accepted vs rejected fields

**Rule**: if a field is not listed as ACCEPTED below, it is REJECTED. No exceptions without a task, a policy argument, and an update to this table.

#### Accepted on the authored chart surface

Query selection: `query`

Channel fields: `x`, `y`, `color`, `size`, `shape`, `theta`, `value`, `latitude`, `longitude`, `geo`, `geo_source`, `lookup`

Presentation: `title`, `subtitle`, `notes`, `link`, `sort`, `orientation`, `style`

Mirror y-axis: `style.axis_y.mirror: true` (draws the y-scale on both left+right edges of wide cartesian charts; single-series only; distinct from the per-layer `layer.axis_y` dual-scale placement). Object form `mirror: {format: ...}` / `mirror: {expr: ...}` relabels only the mirrored edge (e.g. $ left / % right) off the same shared scale — never a second scale.

Small multiples: `multiples` (cartesian-only — bar/line/area/scatter/heatmap; object `{rows?, columns?, scale}` with at least one of rows/columns — rows-only stacks vertically, columns-only side by side, both a grid; compiles to Vega-Lite `facet`; V2-only at render — the V1 path refuses it)

Geo: `projection` (legitimate dbt charts geo surface — NOT a VL escape hatch), `collapse` (point_map/bubble_map only — collapses marks sharing an exact latitude/longitude into one mark sized by count via VL's native `size: {aggregate: "count"}`; mutually exclusive with `size` and with a color channel, field or conditional — both would widen VL's implicit groupby)

KPI: `glyph`, `support` (its `tone` sub-field colors the support row; the headline value has no tone field — it stays neutral), `variant` (`stacked` default / `inline` / `compact` — selects the card layout), `background` (channel field — `{column, scale}` shape; gradient-paints the card background by value position in the scale)

Cartesian overlays: `layers` on any bar/line/area/scatter chart (each typed overlay layer accepts: `type`, `query`, `x`, `y`, `label`, `color`, `axis_y`, `style`; a bar layer also takes `y_start`)

Data attachments: `support_table`

Data attachments (family-scoped): `conditional_formatting` — only on `table` and `kpi`, the two families whose render path meaningfully honors it (full rule-output set on `table`; `background`/`font.color` on `kpi`). Structurally absent on every other family, same mechanism as `size`/`shape` below.

#### Rejected on the authored chart surface

| Field | Reason |
|---|---|
| `spec` | Catch-all VL escape hatch. Rejected since the original schema. |
| `mark` | VL internals. Use `type:` and `style:` instead. |
| `encoding` | VL internals. Use channel fields (`x`, `y`, etc.) instead. |
| `config` | VL override. Use `style:` and typed dbt charts fields. |
| `transform` | VL transform pipeline. No authored use case; engine emits transforms internally. |
| `params` | VL selections/variables. Engine may emit params internally; authors must not. |
| `resolve` | VL scale/axis resolution. Engine derives resolve from typed surface (e.g. `axis_y.orient`). |
| `hconcat` | VL composition. No authored use case. |
| `vconcat` | VL composition. No authored use case. |
| `concat` | VL composition. No authored use case. |
| `repeat` | VL composition. No authored use case. |

#### Enforcement points

Two gates enforce these rejections:

1. **`type:` is mandatory** — `_SharedChartFields.type` is declared `str` (required, no default). Missing or unknown `type:` raises a `union_tag_not_found` ValidationError from the `AuthoredChart` discriminated union before any family-level validation runs. There is no fallback catch-all class. The concept of an untyped chart is rejected at the authored-model level.

2. **`extra="forbid"` on every per-family chart class** (`BarChart`, `KpiChart`, etc.) — inherited from `_BaseChartFields.model_config = ConfigDict(extra="forbid")`. Any field not declared on the family is unconditionally rejected by Pydantic. Structural narrowing also applies: for example, `size` and `shape` are only declared on `ScatterChart` where they are meaningful — attempting to set `size` on a `LineChart` raises `extra_forbidden` (pinned by `test_line_patch_rejects_size` in `tests/core/compile/test_chart_discriminated_union.py`). `conditional_formatting` follows the same pattern via a standalone `_ConditionalFormattingField` mixin applied individually to `KpiChart` and `TableChart` — the only two families whose render path meaningfully honors it; authoring it on any other family (e.g. `BarChart`, `CalloutChart`, `HeatmapChart`) raises `extra_forbidden` (pinned by `test_callout_patch_rejects_conditional_formatting` in `tests/core/compile/test_chart_discriminated_union.py`). `stack` is a style-cascade field on the authored surface (`style.stack` / `style.<family>.stack`); chart-root `stack:` is rejected on every authored chart family (pinned by `test_chart_families_reject_root_stack` in `tests/core/compile/test_chart_discriminated_union.py`). The compiled `Chart.stack` field exists on the compiled-stage model and may be populated directly by tests that construct compiled Chart objects; on real authored input the cascade output lives on `ResolvedChart.stack`, resolved from `style.<family>.stack` in the per-family resolver modules under `src/dbt_charts/core/compile/resolve/`.

Test coverage: `tests/core/compile/test_removed_vl_passthrough_fields.py` parametrizes over all 11 rejected fields.

### Test patterns

- **Distinctive-value-plus-propagation** for cascade tests. Don't pin theme YAML literals; use `base.model_copy(update={...})` to build a new style tree with the distinctive value, then assert the value propagates through resolution. `model_copy(deep=True)` + direct attribute assignment does not work on compiled style models (they are `frozen=True`).
- **`pytest.raises(ValidationError, match=...)`** for required-field promotion regression tests.
- **No introspection ceremony** (`assert not hasattr`, `inspect.signature`, `inspect.getsource`).
- **No hex/px/literal pins on theme-populated fields**. Test structure, presence, override behavior, pipeline correctness.
- **Boolean feature-flag defaults** (e.g., `variables.visible is True`) are an exception worth keeping — they assert UX-default identity, not aesthetic value.
