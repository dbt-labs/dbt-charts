# render/chart

Chart rendering: resolved charts + query data → Vega-Lite specs (or custom SVG for KPI/table/spark families). Read core's `AGENTS.md` (`../../AGENTS.md`) for the compile→render boundary (no reach-backs); this file owns the chart-layer invariants.

## Implementation philosophy

1. **Extended Vega-Lite grammar.** For anything Vega-Lite supports natively, dbt charts YAML is a thin wrapper over Vega-Lite's own concepts — `x`/`y`/`color`/`size`/`shape`/`theta` map to VL encoding channels; `type: bar` means VL `mark: bar`. Never a parallel chart language.
2. **The translation is mechanical.** Read YAML fields, build the spec, inject query data, apply theme config. No hidden mutations, no data-driven rewrites, no surprise layers (no selection params, rule layers, or overlays the user didn't author). The output spec must be predictable from the input YAML alone.
3. **Data belongs to queries.** No chart-layer aggregation, regrouping, bucketing, or semantic reordering. Wrong grain or ordering → fix the query, never the renderer. Carve-out: replay the baked partition, then transform within a panel. `chart_rows()` (`feature.py`) regroups a chart's rows into its small-multiples panels via `regroup(chart.panel_axes, ...)` — this is applying a partition decision `resolve()` already baked (`panel_axes`), not inventing one. `gap_fill_ordinal_time_per_panel`'s `map_panels()` (`_channels.py`) goes one step further and synthesizes missing-bucket rows *within* each already-baked panel — still not deciding panel membership or dataset meaning, only completing the ordinal axis's own scaffold inside a partition compile already drew. The banned case is a renderer *deciding* dataset meaning (which rows belong to which panel, or what the panels even are); here render only replays a decision compile already made, then fills gaps inside it — the same relationship it already has with the baked tick ladder. Second carve-out: pivot leaf-column order. The pivot reshape (`pivot_table_data`, `table.py`) synthesizes a column axis that exists nowhere in the query's own shape — a query orders *rows*, and no SQL surface addresses the header sequence directly: it reaches it only through first-seen order, which cannot express chronological columns from a non-chronological row order. Ordering that synthesized axis is therefore part of the reshape's own output contract, not a mutation of query data: `_order_col_tuples` sorts a pivot column dimension only where its values carry one canonical order every reference tool applies (temporal → chronological, numeric → ascending) and otherwise preserves first-seen order, which keeps `ORDER BY` the author's lever for categorical dimensions. Wide *row* order remains query-owned and is never touched.
4. **Native-option-first fixes.** A VL chart bug is fixed by wiring through a native VL property (`encoding`, `axis`, `scale`, `legend`, `mark`, top-level), not by adding `_apply_*` policy helpers that inspect data. Before adding renderer logic, answer: which VL property expresses this? can existing settings/config expose it? if neither, name the exact VL gap in code and PR.
5. **Config is the single source of truth — no fallbacks in code.** The narrow engine-config getters (`get_chart_rendering()`, `get_rendering_config()`, `get_terminal_config()`) and the theme cascade are guaranteed complete. Never `config.get("x", 200)`, never a literal default in Python, never re-specifying a value that `vega.config.*` already carries. Missing default → add it to the theme YAML (`defaults/themes/stark.yaml` for structural roots) or `default_config.yml` for engine knobs.

   ```python
   # WRONG — hardcoded default duplicating/contradicting config
   y_axis_config = {"orient": "right", "labelLimit": 200}
   # RIGHT — orient comes from the style preset via vega.config.axisY;
   # label cap from style.charts.axis.labels.max_width in the theme
   ```
6. **Semantic state stays separate from presentation defaults.** Semantic fields (`type`, inferred `format`/`zero`, chosen `x`/`y`) may be unresolved with `auto`/`null` states; presentation defaults (label limits, mark styling, heights) always come from config. Presentation config never silently fills semantic meaning.
7. **Where defaults live:** scaffold + visual chart defaults → `style.charts.*` in theme YAML (emitted by `style_to_vega_lite()`, the sole VL mapper); chart dimensions → `chart.*` in `default_config.yml`; type-specific → `style.charts.<type>.*`; layout → `style.layout.*`. Scaffold = whether/where an element exists (axis side, grid presence, legend placement); visual = how it's painted (color, font, stroke).
8. **No parallel names.** Don't invent `settings.y_axis` when VL has `encoding.y.axis`. And **snake_case only** in authored YAML and Python config — `cornerRadius` → `corner_radius`; `style_to_vega_lite()` translates back to camelCase at the boundary.
9. **Paint order is a dbt charts contract.** The base chart's own series paints first (bottom); `layers` entries paint in authored order on top — `layers[0]` paints second, `layers[-1]` last (front). Order is verbatim positional, never auto-reordered by chart type or mark family, and must be preserved across renderer replacements.
10. **Layer color is a dbt charts contract.** Each data-series layer gets a distinct `color: {datum: <label>}` (label cascade: authored `label:`, else `field_title(layer.y, chart.field_labels)`, NOT title case, matching the base series in the same scale) so successive palette slots build the legend. Injection fires only on data-series marks (bar/line/area/circle/square/scatter/trail/rect), never on annotation marks, and is suppressed when an explicit `color:` encoding exists on the layer.
11. **Table numeric columns center on one midpoint.** Header center = number tspan center = header rule center = cell content midpoint (`cell_midpoint = (content_left + content_right) / 2`; numbers anchor `end` at `midpoint + max_number_w/2`). Text columns left-align. One arithmetic expression per element — no alignment fallback branches. Cluster equal-width runs only over *consecutive* numeric columns. **A column's alignment is one verdict, decided once — never per cell.** `TableColumnConfig.align` is final by the time render reads it: authored value, else classified from every cell's shape (`fill_table_column_defaults` / `classify_date_column_align` in `dbt_charts.core.utils`, called once at resolve for an ordinary column or at render's own pivot leaf synthesis — never re-decided from scratch elsewhere). The numeric three-lane path is unconditional on `align` (invariant above). The date path trusts the resolved `align == "right"` as *whether* the column right-aligns, gated by whether the column actually has date-like content — so a plain right-aligned text column (a pie attachment's "share" percent-string column, e.g.) never earns a content-fit lane it wasn't meant to have, and an authored `align: "left"` on a uniformly date-like column has no lane at all, so neither its cells nor its header centers on one. Inside an already-lane-eligible column (numeric or date), a per-cell `is_date_like` check may still route an individual outlier (e.g. a bare year mixed into real numbers) onto the lane its neighbors established — it can route onto a lane, never create or preserve one against an explicit override.
12. **New chart families are registered, never special-cased.** A VL-native family gets an emitter in `emitters/` wired into `get_emitter()` (`emitters/__init__.py`) plus a test in `tests/core/render/chart/test_render_emitters.py`. Non-VL families (kpi, table, spark_bar, callout) are custom SVG: they short-circuit through `BoardRenderSession.render_svg_family` and deliberately have no emitter — `get_emitter` raises `ERR-EMITTER-NOT-FOUND` for them (pinned by `test_get_emitter_rejects_non_vl`).
13. **A documented table-formatting behavior needs a pinning test in the same PR.** Before adding or changing a `style.columns`/`style.table` behavior in `table.py`/`table_support.py`, find the pinning test for the behavior you are touching; a new behavior needs its own pinning test in the same PR. In the monorepo it also needs a row in the table-formatting QA matrix, which is the acceptance surface for these PRs.

Before adding chart logic, ask: does VL already support this (pass through)? am I renaming something VL names? am I hardcoding a default? am I mutating the spec based on data (make it opt-in)? would a user reading the YAML be surprised by the output?

### If you catch yourself thinking…

| Objection | Response |
|---|---|
| "the data's shape is wrong, I'll fix it in the renderer" | Data belongs to queries. Raise `ChartDataError` or fix the query — a chart-local fix is a hidden second transformation pipeline. |
| "just default it to 300 if the config is missing" | The cascade is guaranteed complete; a missing value is a theme bug. Add the default to the theme YAML and read it. |
| "a small `_apply_*` helper can pick a nicer value from the data" | That's renderer-side policy. Wire the native VL option; data-driven presentation must be explicitly opt-in. |
| "users will want lines painted over bars, reorder for them" | Paint order is the authored `layers:` order — a contract, not a bug. Users who want line-on-top write the line layer last. |
| "bake hover/crosshair layers into the spec for static outputs" | Surprise layers violate the thin-wrapper contract (the old crosshair generator was ~140 lines for what should be 5 — deleted). Interactivity belongs to the embedding surface. |
| "this needs a chart-root escape hatch (`spec`, `mark`, `encoding`)" | The authored surface accepts typed fields only — see the accepted/rejected table in core's `AGENTS.md` (`../../AGENTS.md`). |

## Where dbt charts adds value beyond Vega-Lite

| Feature | Why it exists |
|---------|--------------|
| KPI charts (`type: kpi`) | Single-number display — not a VL chart type |
| Tables (`type: table`) | Custom SVG, not VL |
| Spark bars (`type: spark_bar`) | Inline sparkline bars, custom SVG |
| Geo maps (`type: map`, `point_map`) | Built-in geo sources, join logic, projection defaults on VL's geoshape |
| Theming / style presets | Theme cascade merged into VL `config` |
| Data binding | Query results injected into `data.values` |
| YAML shorthand | `x: date` instead of verbose VL encoding objects |

## File layout

| File | Responsibility |
|------|---------------|
| `vega_lite.py` | Thin public entrypoints for chart rendering and spec generation |
| `rendering.py` | Chart / layout-item orchestration: execute the query, resolve to render-ready semantics, dispatch chart vs nested board |
| `session.py` | `BoardRenderSession` — per-board coordinator. `render_svg_family` short-circuits non-VL families to their SVG renderer; VL families run `emit_chart` → `finalize_vl` |
| `emitter.py`, `emitters/` | `ChartEmitter` protocol plus one emitter per VL family (`area`, `bar`, `geo`, `heatmap`, `line`, `pie`, `scatter`), dispatched by `get_emitter()` in `emitters/__init__.py`. `emit()`'s `dataset` is a `ChartDataset` (`compile.resolve.chart._chart_rows`, panel-split, `axes == ()` for a non-faceted chart) — emitters take `dataset.all_rows()` as their first line (commonly rebound to a local `data`) for flat/column-wise work; an aggregating helper must iterate `dataset.panels` instead. Shared internals, also under `emitters/`: `_cartesian`, `_channels`, `_layers`, `_overlay` (authored `layers` paint order), `_tooltip`, `_label_overlap`, `_measured_label_padding` |
| `feature.py`, `features/` | `ChartFeature` protocol, `FeaturePipeline`, and the cross-family overlays (`baseline`, `endpoint_labels`, `value_labels`, `click_interactivity`, `structured_tooltip`, `mirror_axis`, `facet`). `chart_rows(chart, datasets)` returns a `ChartDataset`, regrouped by the chart's baked `panel_axes` (the N=1 case for a non-faceted or non-cartesian chart) — most feature callers want `.all_rows()` immediately; a caller doing per-panel work reads `.panels` directly. `DEFAULT_FEATURES` list order **is** application order |
| `spec.py`, `_types.py` | `ChartSpec`, the render-local intermediate emitters produce and features mutate; `VLDict`, the single sanctioned `dict[str, Any]` alias for VL spec fragments |
| `translate.py` | `assemble_final_vl` — the one `ChartSpec` → Vega-Lite assembly point (config, background, title) |
| `presentation.py` | Shared presentation helpers (axis config, tooltips, config-driven defaults) |
| `spec_builders.py`, `serialization.py`, `artifacts.py`, `type_inference.py` | Spec scaffolding, JSON helpers, render output types, VL type inference from data |
| `emitters/geo.py`, `geo_tooltip.py` | Geo specs and tooltips (data joins, projections, TopoJSON) |
| `kpi.py`, `table.py`, `spark.py`, `spark_bar.py`, `callout.py` | Non-VL renderers (custom SVG) |
| `validation.py` | Data-shape validation policy |
| `../converters/chart.py` | SVG/PNG/PDF/JSON artifact conversion |
