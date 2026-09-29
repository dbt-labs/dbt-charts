# core/compile

YAML → normalized document. Parses board/config YAML, resolves the meta/extends
cascade, validates, and emits the normalized document that `execute/` and
`render/` trust. Pydantic models live in `compile/models/` (see its `AGENTS.md`).

A field rename, move, or removal is a grammar change and ships its migration
in the same PR — see `migrations/AGENTS.md`.

## Package layout

The pipeline's four stages are packages: `parse/` (YAML/markdown text →
`AuthoredBoard`), `validate/` (semantic checks Pydantic can't express),
`normalize/` (`AuthoredBoard` → `Board`), `resolve/` (normalized chart →
`ResolvedChart`). `validate/` and `normalize/` are separate packages by content,
not by runtime pass — `compiler.py` interleaves the two, and the `validate/`
format and board-warning passes run after `normalize_board`.

`normalize/` and `validate/` each enter through `dispatch.py` and re-export
nothing: consumers import modules by path, which is also what keeps
`compiler.py` and `models/board/normalized.py` clear of an import cycle through
those packages. `parse/` has no single entry point (`parser.parse_yaml` is the
main one) and likewise re-exports nothing. `resolve/` is the exception — it
re-exports its `chart/` surface, and `compile/sizing.py` depends on that.

Four more packages group what isn't pipeline-stage-shaped: `template/` (Jinja
templating and SQL parameterization — `jinja.py`, `parameterized.py`,
`_helpers.py`, `labels_env.py`, `variables.py`), `authoring/` (the write
surface over authored YAML that Cloud's editing endpoints consume —
`yaml_patch.py`), `schema/` (schema introspection and its derived artifacts —
`introspection.py`, `renderers/`; `get_schema_for_prompt` lives in
`schema/__init__.py`, a small delegate that keeps
`from ...compile.schema import get_schema_for_prompt` — the codegen entry
point `justfile:159` uses — resolving unchanged), and `sources/` (source and
dbt-profile detection — `detection.py`, `dbt_jinja.py`; `dbt_jinja.py` renders
Jinja inside source/profile config values, a sources concern despite the
mechanism).

Everything left at the package root is the orchestrator (`compiler.py`) plus a
primitive every stage needs: `config.py` (engine config), `errors.py`,
`merge.py` (cross-stage patch-merge engine), `format.py` (format resolution),
`sizing.py` and `support_table.py` (both import from `resolve/`, so must sit
above it), `sql_guard.py` (cross-cutting SQL primitive with no single owning
stage), and `board_artifact.py` (resolved-style codec — serialization, not
pipeline, kept flat as a one-file exception). A new file belongs in a stage or
domain package only if its consumers are that stage/domain; file it flat only
if it's a genuine cross-stage primitive.

## Implementation philosophy

Exceeds the usual length budget deliberately: the load-bearing invariants list is the
compile stage's whole-system contract, and a reader who sees only part of it
will violate the rest.

### Load-bearing invariants

Whole-system truths a locally-plausible change can violate *without the test suite
obviously screaming*. Hold these when touching `compile/`:

- **Variables are board-global.** One namespace per board — a name cannot be reused
  lower down (a deliberate simplification). Values are global runtime state, read
  everywhere, never copied per nested board.
- **Exactly one layout per board.** `rows` / `cols` / `grid` / `tabs` are mutually
  exclusive.
- **Board ids and authoring paths both derive from tree position**, not authoring: a
  nested board's id is `<parent-id>_nested<depth>` (`_generate_board_id`), and its
  `LayoutItem.source_path` is the absolute dotted path to the same spot
  (`rows.0.cols.1`). Compose them together, at the one call site that knows where the
  item sits — two independently threaded position values are free to drift. Don't
  invent per-board id or path state beyond that. The path spelling is not a local
  choice: it must match `build_source_index` keys and pydantic `loc` tuples, or a
  diagnostic naming an item silently resolves to no line at all.
- **`base_dir` is board-global** — the `ProjectDirectory` a board resolves relative
  refs against, used as `base_dir / ref` and threaded through normalized calls. A
  nested import's child anchors at the imported file's own directory. The one ref
  with a second anchor is a query's inline file `source:`, which also tries the
  project root and errors when both hold the file; it never adds anchors beyond
  those two.
- **Presentation lives in the board cascade, never `dbt_charts.yml`.** Board dimensions,
  accents, and theme choices merge outermost→innermost: `charts/meta.yml` →
  `charts/<dir>/meta.yml` → `charts/<name>.yml`, each an `AuthoredBoard`/`BoardPatch`
  deep-merged into the one above.
- **`theme:` is permanent authoring sugar for `extends:`** — rewritten at parse time;
  using both is an error. Prefer `extends:` (it accepts lists and path refs).
- **Underscore-prefixed boards are hidden building blocks**: excluded from listings
  and search, valid as extends targets, and renderable directly like any board.
  `dct validate`/the LSP check a private YAML file standalone as a `BoardPatch`
  fragment, like `meta.yml`: its own top-level validators are dropped and
  required fields inside `grid`/`tabs`/`details` become optional, so it can pass
  validate and still fail at render. Nested boards under `rows:`/`cols:` run
  `AuthoredBoard` in full.
- **Root-only board frame.** Outer dimensions and padding are computed once at
  the root; nested boards render into the parent's grid and their `FrameStyle`
  is ignored.
- **Theme YAMLs are `BoardPatch` fragments** (`extends:` + `style:`), loadable via
  `build_patch_model_ext(AuthoredBoard, ...)`.
- **Cross-board chart imports scope queries lexically, variables board-globally.** An
  imported chart resolves `query:` names — bare, `#`-external, and the chains behind
  them — against the *source* file's `queries:`, anchored at its own directory, under
  reserved-prefix keys. `{{ filter(...) }}` resolves against the importer's.
- **Board width is two keys: `frame.width` binds, `frame.max_width` bounds.**
  `width` authored anywhere in the cascade (board, extends template,
  meta.yml; root `width:` sugar) is the board's exact width. With no
  `width`, the board hugs its charts' `preferred_width` measurement up to
  `max_width` — so a lone generated chart stays a small card, never a
  full-width one. Never reintroduce a provenance split ("binds if the board's
  own file wrote it") — that design was built and deleted; the key is the
  intent. Corollaries: `rows:` slots pin an authored item/chart width (a
  percentage means a fraction of the row and never feeds the hug), and
  `chart_focus` sets the focused board's `width` to the pinned slot plus
  margins — WYSIWYG even when the slot exceeds `max_width`.
  Pinned by `tests/core/compile/test_authored_board_width.py`.
- **The type ladder is coupled to board geometry.** Object-title width tiers
  (`resolve/style/typography.py`) derive from the default board's 24-column grid via a single
  global reference. Changing `frame.width` / `frame.margin` in `_base.yaml` moves the
  thresholds, and the failure is silent (titles shrink and lose their serif).

See `../AGENTS.md` → **Two validation boundaries** for the boundary these sit behind.

### Two-model config split

- **`Config`** (`compile/config.py`) — engine knobs (sources, server port, execution
  limits, rendering tweaks, palette overrides) from `dbt_charts.yml`, `extra="forbid"`.
- **`Board`** — presentation (layout, queries, charts, style, theme, extends) from
  board YAML; lives entirely in the board cascade, never in `Config`.

Presentation keys (`board:`, `style:`, `theme:`) in `dbt_charts.yml` raise a
`ValidationError`; the project-wide default theme's home is
`charts/meta.yml: extends: <theme>`.

### `extends:` and theme resolution

`extends:` is a board's inheritance list. The normalizer scans it for a built-in theme
name (`_theme_from_extends`, **last match wins** — it scans reversed) and applies it;
other entries are board path refs (`./_template.yaml`) or board names resolved at
the *project root* (`compiler.py` passes `boards_root=project.directory(".")`),
merged via `merge_extends`. Later entries have higher style-merge priority,
so a template listed after a theme overrides it — put the theme first:

```yaml
extends:
  - paper                # base style, lower priority; also the resolved theme
  - ./_report-base.yml   # overrides specific style fields
```

Built-in theme names are the YAML stems under `defaults/themes/` (user-facing:
`clarity`, `paper`, `vivid`, `neon`, `stark`). The chain bottoms out at
`stark`, the structural root every other built-in theme transitively
extends; `_base.yaml` is the hidden completeness floor beneath that.
`clarity` is the configured default. Working example:
`examples/playground/charts/composition/_report-base.yml`, a template any
board extends via `./_report-base.yml`; unit test:
`test_merge_extends_relative_path_title`.
