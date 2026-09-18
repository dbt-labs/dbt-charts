# compile/models

Pydantic type definitions for the compile stage, organized by domain.

## Layout

Each domain folder splits types by stage:

- `authored.py` — what users write in YAML; loose, optional fields, defaults welcome. Includes all `*Patch` overlay types.
- `normalized.py` — post-normalizer document tree; references resolved, IDs assigned, trusted by execute/render
- `resolved.py` — frozen render contracts; config baked, cascade complete, no validation at render boundary

Single-file domains (`config.py`, `source.py`, `primitives.py`, `factories.py`) live at the package root when the domain has no meaningful stage split.

`models/primitives.py`, `compile/normalize/dispatch.py`, and `compile/support_table.py` import validators and helpers from `compile/resolve/style/` (e.g. `resolve.style.palette` for palette-token validation). This is the intended direction: `models/` types depend on `resolve/`'s resolution-stage helpers where a value needs resolving at model-construction time, never the reverse — no file under `models/` is itself part of `resolve/`.

## Migrations are mandatory

A structural change to an `authored.py` model or a `*Patch` type (a field
rename, move, or removal) is a change to the dbt charts YAML grammar. It must
ship with a corresponding migration module in
`src/dbt_charts/core/compile/migrations/versions/`, or a clear reason
one is not needed. A pure addition qualifies. "It cannot be migrated safely"
does not, unless a test in `tests/core/compile/` demonstrates the
failure — that claim has twice been asserted and twice been wrong. Read the
migrations `AGENTS.md` (`../migrations/AGENTS.md`, its `## Implementation
philosophy`) before declaring the change unmigratable; a PR missing this is a
CRITICAL review finding.

## Implementation philosophy

Exceeds the usual length budget deliberately: the default_factory decision tree and
inherit-marker semantics are dense and load-bearing, and splitting them loses the thread.

### Stage-suffix conventions

Two equivalent forms are allowed for the authored stage:

| Pattern | Used by | Example |
|---|---|---|
| `Authored*` prefix | board | `AuthoredBoard` |
| Domain-prefixed names | chart, query | `AuthoredChart` (discriminated union alias), `ChartSort`, `AuthoredQuery` |
| `*Patch` suffix | style | `ChartStylePatch`, `ScaleStylePatch`, `AxisStylePatch` |

These coexist because each form is the most natural fit for its domain — board has a single root authored shape, chart spreads authored input across several shapes that share a `Chart` root, and style is entirely patch-shaped (all-Optional cascade overlays). For the normalized stage, names are always unprefixed (`Board`, `Chart`, `Style`, `FrameStyle`, `AxisStyle`). For the resolved tier, names use the `Resolved*` prefix (`ResolvedBoard`, `ResolvedStyle`, `ResolvedChart`). These are frozen dataclasses produced by merging a normalized `Style` with optional `*Patch` overlays; they are the final resolved shape consumed by renderers — construction-final, never copied-with-update (`dataclasses.replace()`/`model_copy(update=...)` on a `Resolved*` value is a boundary violation; see `tests/test_no_replace_on_resolved.py`). Theme-stage Pydantic classes (`TitleStyle`, `TextStyle`, `LayoutStyle`, etc.) are themselves the final shape where no per-board cascade is needed — they live in `style/theme/` and carry no prefix.

`ChartStyleContext` (`style/context.py`) is the deliberate exception to the `Resolved*` naming rule: it is compiler *working state* for the chart-local style cascade — sparse axis overlays, chart-local patch sentinels, palette/role token bindings, and the pre-inherit `Style` tree — produced alongside `ResolvedStyle` by the same board-level cascade (`resolve_style_and_context()`) but never exposed by `ResolvedBoard` itself (no field on `ResolvedBoard`/`ResolvedChart`/`ResolvedStyle` carries it, and it never serializes into the board-resolved artifact). It does cross into a handful of render entry points that perform runtime chart resolution rather than mechanical emission — `render/layout_sizing.py`'s sizing pass, `render/board_resolve.py`'s static resolvers, `render/board_to_dict.py`'s row-truncated resolution, and the two documented `render/chart/vega_lite.py` entry points (`render_chart()`, `generate_vega_lite_spec()`) — see core's `AGENTS.md` (`../../AGENTS.md`) render-boundary section for the full list and why each is resolution, not emission. Runtime chart resolution (`compile/resolve/`, `compile/support_table.py`, normalized `Board.chart_style_context`, execute orchestration, and those render entry points) consumes it directly; render's other, mechanical consumers only ever see the final `Resolved*` outputs it projects into (`ResolvedStyle`, `ResolvedChartDefaults`, and each chart's own `Resolved<Family>Style`). Because it carries no `Resolved` prefix, `dataclasses.replace()` on a `ChartStyleContext` is legitimate — that is exactly the working-state cascade this split exists to keep off `Resolved*` types.

Do not add a `Compiled*` prefix to any new class in this tree. The `scripts/check_models.py` checker enforces this.

### Enforcement

`scripts/check_models.py` enforces five rules on this tree; run it directly with `uv run python scripts/check_models.py` (`dbt-charts/scripts/check_models.py` from the monorepo root, where it is also wired into pre-commit and lint CI).

1. No bare-dict `model_config = {...}` — use `ConfigDict(...)`.
2. Every direct `BaseModel` subclass declares `model_config = ConfigDict(extra="forbid")`.
3. No class names starting with `Compiled`.
4. No `*_types.py` module names.
5. `T | None = None` fields on theme-populated classes (`style/theme/`, `config.py`, `chart/normalized.py`) require a justification comment.

Add new rules by editing `scripts/check_models.py` and `tests/core/test_model_conventions.py`.

### `Field(description=...)` is published API copy, not an engineering note

A description is not an engineering note — it ships verbatim to users, across five regenerated artifacts (the docs site, the wheel's `dct docs reference`, and the JSON Schema the IDE serves as hover text among them). Write for a board author who has never seen this repo and cannot open any file you name.

Banned: Python symbols, module/file paths, compile-stage jargon, change narrative, review rationale. The test: **would this sentence help someone who has only ever seen the YAML?** If it only lands for someone holding the diff, move it to a `#` comment, the PR body, or the task worksheet — none of those ship. Hold internal-tier models (`normalized.py`, `resolved.py`) to the same standard; tiers get promoted.

Regenerating after a description edit takes five commands, each gated by its own drift test: `just gen-yaml-reference`, `just gen-highlight-artifacts`, `just vscode_extension schema`, `just playground gen-completion-schema`, `just gen-board-resolved-schema`. Three of those are monorepo-only: `gen-yaml-reference` and the `vscode_extension` / `playground` legs, the latter two regenerating other packages' artifacts. Standalone, `just gen-references` replaces `gen-yaml-reference` and writes the yaml, error, and warning references in one pass.

### Design + coding patterns

For the broader Pydantic conventions that `check_models.py` can't enforce mechanically — discriminated unions, inheritance for variant style slots, the three-stage cascade discipline, `Field(description=...)` requirements, `Annotated` style preference, the validator hierarchy, `exclude_unset` merging, `frozen=True` for immutables, and the full red-flag list — the rules above are the contract; an existing model that breaks one is debt, not precedent. Never hand-write an all-Optional patch group: `build_patch_model` recurses per field to generate them.

### Engine config types in `config.py`

`config.py` owns typed `ConfigNode` subclasses for each engine-config namespace. **New engine-internal numeric/tuning constants belong here as required typed fields, not as bare Python `UPPER_CASE` module constants in `compile/` or `render/`.** The pattern:

1. Add the constant's default value (with a rationale comment) to `defaults/default_config.yml` under the appropriate namespace (e.g. `chart_rendering.pie:`).
2. Declare a typed `ConfigNode` subclass in `config.py` (e.g. `ChartRenderingConfig.PieConfig`) with each constant as a required `float` or `int` field — no in-code defaults.
3. Wire the new class into the parent config model as a required field.
4. Read via the narrow getter in `compile/config.py` (e.g. `get_chart_rendering().pie.wedge_label_min_share`).

Worked example: `chart_rendering.pie.wedge_label_min_share` (was `WEDGE_LABEL_MIN_SHARE = 0.08` in `arc_attached_table.py`). Value in `default_config.yml`, typed on `ChartRenderingConfig.PieConfig`, read via `get_chart_rendering().pie.wedge_label_min_share`.

### Style/config types: theme-populated, required by default

Pydantic models in `compile/models/style/theme/`, `compile/models/config.py` follow strict rules:

1. **No in-code defaults on theme-populated types.** Every default value lives in `defaults/themes/stark.yaml` (the structural root every other theme transitively extends) or `defaults/default_config.yml` (engine constants). The field is required `T` — Pydantic raises `ValidationError` if the cascade fails to populate it. That's the desired loud-failure contract.

   This applies to every runtime-config field supplied by `default_config.yml`,
   including non-numeric strings and feature flags; the Python model must not
   repeat the shipped YAML value.

   ```python
   # WRONG — hardcoded literal default
   class FrameStyle(BaseModel):
       width: float = 1200

   # WRONG — default_factory wrapping a literal
   class FrameStyle(BaseModel):
       width: float = Field(default_factory=lambda: 1200)

   # RIGHT — required, theme YAML supplies it
   class FrameStyle(BaseModel):
       width: float
   ```

   **Exception — fully-defaulted container types.** When a field's type is a structured container where every sub-field has a default (all-Optional or `default_factory`), the container itself carries no literal values. An empty instance is not a semantic default — it is a cascade placeholder that `apply_inherit` will populate from the parent slot. Use `default_factory=TypeName` so theme YAML can omit the key rather than forcing a structural `{}` marker with no content.

   The test: can `TypeName()` be constructed with no arguments, **and can mypy verify it?** If yes and it contains only `None`-defaulted fields → `default_factory`. If it contains any required scalar field → leave required. **Exception**: types that extend `_ChartStyleBaseAllOptional` (generated by `build_patch_model_ext`) have a TYPE_CHECKING stub that shows the required fields from `_ChartStyleBase`, so mypy rejects `default_factory=TypeName` with a `Callable[[], Never]` error. These must stay as required fields with explicit `{}` in theme YAML.

   ```python
   # WRONG — forces theme YAML to write `grid: {}` as a structural no-op
   class AxisStyle(BaseModel):
       grid: AxisGridStyle  # all sub-fields are None-defaulted; {} adds nothing

   # RIGHT — cascade fills the sub-fields; theme omits the key entirely
   class AxisStyle(BaseModel):
       grid: AxisGridStyle = Field(default_factory=AxisGridStyle, description="...")

   # STILL WRONG — type has required scalar sub-fields; cascade must supply them
   class SomeStyle(BaseModel):
       border: BorderStyle  # has required `width: float` — stays required
   ```

2. **No `T | None = None` fallback patterns on theme-populated fields.** Optional-with-None silently re-hides theme-population failures at the type level — the same antipattern as a hardcoded default, just typed differently. If theme YAML supplies the field, make it required.

3. **Optional fields on theme-populated types are only allowed as cascade-managed sentinels, and require a justification comment.** Examples:

   ```python
   class AxisYStyle(BaseAxisStyle):
       # y-axis only: None means "no mirror" — SkipInheritSlots, no theme default
       # populates this, an author opts in per chart/theme layer.
       mirror: bool | AxisMirrorStyle | None = None
   ```

   Without the comment, reviewers will (and should) flag the field as a defaulted-theme-populated antipattern.

4. **`*Patch` types are all-Optional by design.** They represent partial overlays in the cascade. Generate them via `build_patch_model(StyleClass)` rather than hand-writing all-Optional duplicates. Do not add literal defaults to Patch fields.

5. **Authored config types** (`TextColumnStyle`, project config, etc.) MAY use `T | None = None` to distinguish "not authored" from explicit values when that distinction matters semantically. This is the only place Optional-with-None defaults are unconditionally fine.

### Inherit markers (`markers.py`)

Three marker types live in `compile/models/markers.py`. They are placed on `Annotated[T, marker]` fields in `style/theme/` to declare the inherit graph:

- **`Inherit(from_path="Style.charts.font.color")`** — leaf scalar: declares a single direct parent path for this leaf. Used when the parent path is known absolutely (e.g. per-family fields inheriting from the canonical charts-level field).

- **`InheritSlot(from_path="Style.charts.marks")`** — nested model: declares that all leaf descendants of this field inherit from corresponding positions under `from_path`. The entire subtree under this field is expanded into individual leaf links pointing to `from_path + suffix`. Shared model types (e.g. `AxisStyle`) can carry a single `InheritSlot` that applies across all slot positions.

- **`SkipInheritSlots(cascade=False)`** — nested model, suppresses slot expansion:
  - `cascade=False` (default): fully suppresses slot expansion for this field and its subtree. Use when the field should NOT inherit at all (e.g. `axis.scale` — family axes share the same scale config, no cascade needed).
  - `cascade=True`: emits a **container-level link** (`child_path → parent_path`) so `apply_inherit` can copy the whole nested object when the child is `None`. Also emits individual leaf links for all fields inside the container (so partially-set containers get their `None` fields filled field-by-field from the parent). Use for `T | None` optional nested models inside a slot context where the whole object may be absent (e.g. `LineMarkStyle.stroke`, `SliceMarkStyle.labels`).

After adding or removing markers, regenerate the committed registry artifact:

```bash
just generate-inherit-registry
```

A drift test (`test_inherit_registry.py::test_inherit_registry_matches_graph`) fails if the committed YAML diverges from regeneration.
