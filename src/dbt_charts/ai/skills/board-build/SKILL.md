---
name: board-build
kind: workflow
description: >
  Build-test-iterate workflow for dbt charts boards and reports: edit board YAML,
  add charts, write parameterized queries, duplicate an existing board. Use for
  'build a dashboard', 'add a chart', 'duplicate this dashboard'. Do NOT use for
  errors after something breaks ({{ s_skill_name_troubleshooting }}) or for chart-type and
  layout choices ({{ s_skill_name_design_board }}).
metadata:
  author: fivetran
---

# Building dbt charts Boards

**`{{ s_render_board }}` is how you deliver a dashboard** — call it and return its output. Do not skip it.

## Delivery discipline

- `{{ s_render_board }}` delivers the dashboard — call it and let the result stand. Do not also paste the rendered output or the full YAML back as prose; that just duplicates the deliverable as an unreadable dump.
- Act, don't ask. Apply edits and render directly — do not ask for permission to do the work you were handed ("if you want, I can save this", "would you like me to…"). This bans permission-seeking, not commentary — see `{{ s_skill_analyst_runbook }}` for what to deliver alongside the render.
- Fail loud. If a tool returns an error, report it and either fix the input and call the tool again, or stop and say what's blocking. Never route around a failed tool by fabricating output, dumping query rows as a text table in place of a chart, or improvising a different format. A failed render is a failure to report, not to paper over.
- **dbt charts is the only visualization tool you may use.** Your environment may offer other ways to draw a chart — a plotting library, an HTML or SVG canvas, a notebook, a document/artifact builder, a bundled charting skill. None of them is an acceptable substitute here, however good the result would look. The deliverable is board YAML rendered by dbt charts; anything else is the wrong artifact even when the picture is right. If dbt charts genuinely cannot express what was asked, say so and stop — do not quietly reach for another renderer.
- **A single chart is just a board with one chart in it.** There is no separate "only a chart" artifact, and no reason to leave dbt charts to produce one. Write the `queries` / `charts` / `rows` triple, leave `title:` off, and the render is the chart with no board title above it.
- Never claim a render, query, or save succeeded unless the tool returned success. Never invent columns, tables, or data — if a schema or query call fails, fix it against the real schema.
- **A clean render is not evidence the edit was right.** It says the YAML compiles and the queries ran — nothing about whether the thing the user asked for is now true. Before reporting an edit as done, check the *specific* claim against the rendered result: the axis really is a percentage, the legend really is gone, the series really is the one they named. If you cannot see it in the result you have, say what you verified and what you did not. "It rendered" answers a question nobody asked.
- **"Remove the legend" is not always the legend.** A multi-series line, area, or *stacked* bar chart names its series on the chart by default, in a label strip beside the plot, and that suppresses the color legend. On those, `legend: visible: false` changes nothing, because there is no legend left to hide; the strip is `style.endpoint_labels.visible`. A grouped bar is the other way round: it keeps a real legend and has no strip. Look at the render to see which one you have rather than guessing from the chart type, and when an edit that should have removed something leaves the render unchanged, suspect you turned off the wrong mechanism rather than that the render is stale.
- **Say what you are about to do, then do it in the same turn.** Naming the plan is a preface to the work, never a substitute for it — a turn that ends on "I'll compare X and then render Y" has delivered nothing and hands the user a step they now have to ask for. Never close on "here's how I could solve it"; solve it and report what happened.

## Previewing vs. saving a board

`{{ s_render_board }}` works two ways: pass `yaml_content` to render YAML directly **without writing a file** (an ephemeral preview), or pass `path` to render a saved board. **Which one is the default depends on the surface you are running on — your host's instructions say which.** A terminal/CLI session is file-first (the saved board is the deliverable); an embedded chat or editor host previews ephemerally and lets the user save explicitly. Follow your host; don't assume.

These rules hold on **every** surface, regardless of the default:

- **Never silently append to or mutate an existing board the user did not name** (e.g. `overview.yml`). Editing someone's saved dashboard because it happened to be the nearest file changes state they never asked you to change.
- When a save is intended but no target is named, write a **new** board rather than folding into an existing one.
{{#if_tool write_file}}- **A direct instruction is work to do, not work to hand back.** When the user asks you to save, copy, or edit a file, do it rather than describing how they could. A preview-first default governs work you were *not* asked to save. **Scope is your host's to define** — how many files one request may touch, and where you may write, is a permission question your host's instructions answer; follow them, and don't infer a broader license from this rule.
{{/if_tool}}
Build dashboards and reports **incrementally** — one chart at a time, validating at every step. Never one-shot an entire dashboard.
{{#if_tool write_file}}
### Duplicating a board

Copy a dashboard with `{{ s_read_file }}` then `{{ s_write_file }}` to the new path — there is no copy verb and you don't need one. Never `extends:` (the copy stays coupled to the original) and never a rebuild from a render (inlines result data, loses the SQL).
{{/if_tool}}

## Companion Skills

- **`{{ s_skill_design_board }}`** — chart-type, layout, and color decisions
- **`{{ s_skill_replicate_board }}`** — reproducing an existing dashboard from a screenshot or export
- **`{{ s_skill_design_report }}`** — narrative reports (text + charts)
- **`{{ s_skill_review }}`** — self-review checklist run at delivery
- **`{{ s_skill_troubleshooting }}`** — when something breaks

{{#if_tool docs}}{{ s_docs_discovery }}
{{/if_tool}}## Labels and defaults

A variable's `label` is inferred from its object key, and its `input: auto`, `required: false`, and `visible: true` are schema defaults — omit all four unless you are intentionally changing the inferred/default value. A chart's own caption is **not** inferred from its object key for any chart type: set `label:` on every `type: kpi` chart and `title:` on every other chart type, or it renders with no visible caption at all.

## The Workflow

### Step 1 — Explore the Data

Explore the schema with `{{ s_execute_query }}` against the warehouse's
metadata views (e.g. `INFORMATION_SCHEMA.TABLES`, then
`INFORMATION_SCHEMA.COLUMNS` filtered to the tables you care about) before
writing any query. Never invent a table or column name you have not seen.

{{#if_tool search_boards}}Use `{{ s_search_boards }}` when the user asks for something similar to an
existing dashboard or when you need validated query patterns to reuse. Reuse
returned dashboard paths and query names exactly; never invent file globs such
as `data/*.csv` or guess paths from memory.{{/if_tool}}

Use `{{ s_execute_query }}` to verify cardinality and sample values before writing any YAML:

```sql
SELECT status, COUNT(*) FROM orders GROUP BY status;
```

**Do not skip this step.** Writing SQL against assumed column names is the #1 source of errors.

### Step 2 — Build One Chart

Write a minimal YAML with a single query and chart:

```yaml
source: my_profile
queries:
  revenue:
    sql: SELECT date, SUM(amount) AS total FROM orders GROUP BY date ORDER BY date
charts:
  revenue_trend:
    query: revenue
    type: line
    x: date
    y: total
rows:
  - revenue_trend
```

Then verify with two calls:

1. `{{ s_execute_query }}` — confirm the data shape and columns
2. `{{ s_render_board }}` — compile + render to see the visual

### Step 3 — Iterate on That Chart

Adjust SQL, chart type, labels, colors. {{ s_render_to_verify }}

### Step 4 — Add the Next Chart

Repeat steps 2–3 for each additional chart. One at a time.

### Step 5 — Compose the Layout

Once all charts work individually, arrange them in `rows:` / `cols:`. The layout is the easy part — getting the queries right is the hard part.

```yaml
rows:
  - cols: [kpi_revenue, kpi_users, kpi_orders]
  - revenue_trend
  - cols: [by_region, by_product]
```

`cols:` entries are chart names or nested layouts, and share the row equally by
default — a bare number in the list (`cols: [revenue_trend, 2]`) is not a span
and does not validate. For an uneven split, wrap each side and give the wrapper
a `width:` — `"70%"` or `"300px"`. A bare number is **pixels**, not a share, and
nothing rejects it: `width: 2` renders a 2-pixel column, so reach for a
percentage whenever you mean a proportion:

```yaml
rows:
  - cols:
      - width: "70%"
        rows: [revenue_trend]
      - width: "30%"
        rows: [by_region]
```

Reach for `grid:` when you need explicit row/column placement, not merely
uneven widths.

Don't put a `title:` on every row. A section heading over already-labeled charts is repetition, not structure — group with proximity instead. Reach for a row `title:` only when the dashboard is becoming more of a narrative — when there's accompanying `text:` prose, or the heading genuinely says something the charts don't (a shared scope like "Last 30 days", a real mode boundary). A title with no text alongside it usually means the title and the sectioning weren't needed — drop it. (This is dashboard guidance — in a **report**, sections are good: narrative `## …` headings in `text:` blocks carry the prose between charts — see `{{ s_skill_design_report }}`.)

### Step 6 — Save the Final YAML (when saving applies)

Skip this step when you are previewing ephemerally — see "Previewing vs. saving a board" above; your host decides the default. On hosts that offer the user a Save control over the rendered preview (the Cloud chat, for example), saving an unprompted preview is the user's click — don't write that file yourself. {{#if_tool write_file}}That default does not apply when the user explicitly asked you to save, copy, duplicate, move, or rename something: an explicit ask is your authorization on every host, so carry it out. {{/if_tool}}When saving applies, write the YAML to a **new** board under `charts/` (never silently fold into an existing one) with your normal file-edit mechanism.

Confirm it renders with `{{ s_render_board }}` — a failed compile reports the validation diagnostics, so fix whatever it reports and re-render until it passes.

**YAML style:** write block style — one key per line. Avoid JSON-like inline flow maps (`{ type: bar, x: month }`); they read and diff worse than indented blocks. Inline arrays (`y: [revenue, cost]`) are fine.

## Parameterized Queries — Use From the Start

Use `{{ variable_name }}` syntax for any configurable values, even during exploration:

```sql
SELECT * FROM orders WHERE region = '{{ region }}' AND date >= '{{ start_date }}'
```

Pass concrete values via the `variables` parameter when testing. When the query moves into dashboard YAML it works identically — and results are already cached.

**If you hardcode values during exploration and switch to variables later, the cache won't help because the SQL template changed.**

## Validate Early and Often

| Tool | When | What It Catches |
|------|------|-----------------|
| `{{ s_execute_query }}` | While drafting raw SQL | SQL errors, missing tables, wrong column names |
| `{{ s_execute_named_query }}` | After saving YAML with named queries | Actual named-query columns and sample rows |
| `{{ s_render_board }}` | After every YAML edit and before delivery | YAML schema errors, unknown fields, broken chart/query/layout references, query execution, render errors, layout issues, misleading visuals |

These tools are fast. Use them after every change, not just at the end.

`{{ s_render_board }}` also returns a `warnings` list alongside errors. Each has a stable code flagging either a **data** problem (all-null series, zero rows, a single temporal point, fan-out / re-aggregation) or a **design/visual** problem (a pie dominated by one slice, too many color or x categories, a missing currency/percent format, a mismatched shared y-axis). If `warnings` is non-empty, read each one and fix its cause — the query/data for data warnings, the **chart config** (chart type, encodings, formatting in the correct `style:` slot — see [Formatting Numbers](#formatting-numbers)) for design warnings — then re-render. Treat warnings like errors: don't deliver a dashboard while any remain unresolved (or say which one is a deliberate exception and why).

## Self-Review Before Delivering

Run the review skill as the final step before declaring the board done:

- {{ s_review_pointer }}

It orchestrates structural and visual (PNG + vision) passes and returns a ranked findings list. Fix each `blocker`, then re-run the review. Rendering and validation are cached — the loop is cheap.

## Data Requirements Per Chart Type

**Inline data** (no database) — columnar format only. Each row is an array, column names declared once:
```yaml
queries:
  my_data:
    columns: [month, revenue, churn]
    values:
      - [2025-01-01, 284000, 3.2]
      - [2025-02-01, 301000, 2.9]
```
Record-shape `rows: [{col: val}, ...]` also works, but repeats every column name per row; prefer the columnar form. `type: values` is optional and changes nothing; omit it.

Each chart type expects data in a specific shape. dbt charts validates this and errors fast — no silent magic.

| Chart Type | Expected Data | Key Fields |
|------------|---------------|------------|
| `kpi`      | **Exactly 1 row** with a value column | `value` (column reference) |
| `line`     | Multiple rows, temporal x + numeric y | `x`, `y` |
| `bar`      | One row per category, numeric y       | `x`, `y` — categorical x auto-flips to horizontal; override with `style.orientation: vertical` only when needed |
| `area`     | Multiple rows, temporal x + numeric y | `x`, `y` |
| `scatter`  | Multiple rows, both x and y numeric   | `x`, `y` |
| `pie`      | One row per segment (pre-aggregated)  | `theta` (numeric) + `color` (category) |
| `heatmap`  | One row per cell (pre-aggregated)     | `x`, `y`, `color` |
| `table`    | Any number of rows and columns        | Per-column format under `style.columns.<column_name>.format` (dict keyed by column — not a list) |

**Critical rules:**

- **KPI charts require exactly 1 row.** Aggregate to a single row: `SELECT SUM(amount) AS total FROM orders`. Multiple KPIs need separate single-row queries (or one query with multiple columns).
- **Pie and heatmap expect pre-aggregated data.** Use `GROUP BY` in the query. dbt charts does NOT aggregate for you.
- **Don't mix metrics with very different magnitudes on one y-axis.** Metrics like churn (2–7%) and NRR (90–110%) on a shared axis will crush the smaller series flat. Use separate charts stacked on a shared x-axis instead. A layer's `axis_y.position` does give a second y scale, but it triggers `WARN-DUAL-AXIS-COMPETING-SCALES` because the two scales make crossings and relative height meaningless.
- **Multi-series line/bar/area: use `y: [col1, col2, col3]`.** Pass a list of column names to `y:` for multiple series on the same axis. Use `layers:` directly on a `bar`/`line`/`area`/`scatter` chart only when layers need different chart types (e.g., bar base + line overlay).
- **Trend lines are computed in SQL, not configured on the chart.** Fit the line with a regression aggregate (`REGR_SLOPE`/`REGR_INTERCEPT`, supported in DuckDB, Postgres, and Snowflake) and overlay the fitted series as a `line` layer on the base chart — the query layer owns computed series, same as any other aggregation.
- **Bar `color` creates grouped bars.** Setting `color` to a different field than `x` allocates one sub-band per color value per x-band. If each x-value belongs to only one color group (e.g. each person is in one team), all other sub-bands are empty and every bar is razor-thin. Only use a different `color` field when each x-value truly has multiple rows with different color values. Otherwise set `color` to the same field as `x`, or omit it.

## Formatting Numbers

dbt charts does not auto-detect format from column names at compile time. Set formatting explicitly wherever the number type matters. A column prints the same way on every chart that shows it, so say how it prints **once, on the query**:

```yaml
queries:
  sales_by_month:
    sql: SELECT month, won_amount, win_rate FROM ...
    formats:
      won_amount: currency_whole
      win_rate: percent
```

A chart sets a format in its own slot only for an exception, and there is no chart-wide format key. One board-wide look is a redefinition of the engine name in `style.formats` (`currency: "$,.0f"`), not a key repeated on every chart. A date axis reads by its grain with no key at all.

### Where format goes

When a chart needs its own format, put it **in the slot for that chart family**, not at chart root.

`axis_x`/`axis_y` name the channel, not the visual edge: on every cartesian family the measure is `axis_y`, including a horizontal bar that draws it along the bottom. A number preset belongs on the measure.

Put a number preset on an axis whose ticks can't carry it and the engine raises `ERR-LABEL-FORMAT-AXIS-MISMATCH` — on `axis_x` or `axis_y`, the mirror ghost (`axis_y.mirror.format`), or heatmap's y, whichever axis the format actually lives on. Two cases raise:

- The axis resolves to a **band** scale (nominal/ordinal) and its ticks are not already readable as numbers. Numeric categories (`stage_id: 1, 2, 3`), numeric strings and booleans format cleanly and stay legal — a bar over `stage_id` paints `$1 $2 $3` and never raises.
- The axis resolves to a **temporal** scale at all — dates get no numeric-tick exemption, since there's no reading of `$,.0f` over a date the author wanted.

| Shape | Dimension scale |
|---|---|
| `bar` over a category | band |
| `bar` over `yearweek` / `yearmonthdate` buckets | band, always |
| `bar` over `year` / `yearquarter` / `yearmonth` buckets | band, up to 60 distinct buckets; temporal at 61+ |
| `line` / `area` / `scatter` over a text category | band |
| `line` / `area` / `scatter` over bucketed dates | temporal |
| `line` / `area` with `curve: step` over bucketed dates | band — the step plateau needs a band scale |
| `heatmap` x/y | band, always nominal |
| `scatter` over a genuinely numeric x, or its usual numeric y | quantitative — applies normally |
| `scatter` over a categorical y (a dot plot) | band |

A temporal x — `line`/`area`/`scatter` over dates, or a bar past 60 buckets — raises the same code; use a time token (`"%b %Y"`) there instead. A `heatmap` has no measure axis at all: both axes are grid dimensions and the value lives on the color channel, which carries no label format of its own, so a number preset on either axis — including its `y` — raises too. `style.axis_y.mirror.format` follows the same rule whenever the mirrored edge is categorical — a dot plot's y, and also a default-orientation (horizontal) bar, where the rotation puts the category on that edge. Every one of these used to render something wrong instead of raising.

| Chart family | YAML slot | Example |
|--------------|-----------|---------|
| `line`, `bar`, `area`, `scatter` — the measure | `style.axis_y.labels.format` | `style.axis_y.labels.format: currency_whole` |
| same — the dimension, when it is a **date** | reads by its grain with no key; a time token on `style.axis_x.labels.format` shows another form | `style.axis_x.labels.format: "%b %Y"` |
| same — the dimension, when it is a **category** | no format applies — the tick text *is* the label | — |
| same — the dimension, when it is genuinely **numeric** (e.g. a scatter's x column) | `style.axis_x.labels.format` — `axis_y` never reaches the dimension axis | `style.axis_x.labels.format: integer` |
| `heatmap` | no axis format applies — the value is on the color channel | — |
| `kpi` headline value | `style.value.format` | `style.value.format: currency_whole` |
| `kpi` support delta | `style.support.format` | `style.support.format: percent_delta` |
| `table` column | `style.columns.<col>.format` | `style.columns.revenue.format: currency_whole` |

**Never** put `format:` at chart root on `line` / `bar` / `area` / `scatter` / `kpi` — compile rejects it (`ERR-EXTRA-FIELD`).

Cartesian example:

```yaml
charts:
  monthly_sales_trend:
    type: line
    query: sales_by_month
    x: month
    y: won_amount
    style:
      axis_y:
        labels:
          format: currency_whole
```

Table example:

```yaml
charts:
  pipeline_table:
    type: table
    query: deals
    style:
      columns:
        # styling-only: every query column renders whether listed or not.
        # Hide a column with `visible: false`.
        amount:
          label: Amount
          format: currency_whole
          align: right
```

To change a non-pivot table's column order, reorder the query's `SELECT` list — `style.columns` key order is inert (except a zero-row result, which falls back to `style.columns`' own key order). A `visible: false` column doesn't render at all; the remaining columns close up around it.

Named presets (prefer these over raw D3 strings):

| Preset | Example output | Notes |
|--------|---------------|-------|
| `integer` | `1,234` | |
| `year` | `2025` — no grouping, no SI scaling | an identifier, not a quantity |
| `currency_whole` | `$1,234` | |
| `currency` | `$1.2M` | compacts; the name to reach for on a dashboard |
| `currency_full` | `$1,234.56` | cents at every magnitude |
| `currency_compact` | `$1.2M` | two significant digits: `$45K`, `$1.2B`; analytic (K, M, B) in every slot, KPIs included |
| `number` | `1.2M` | compacts; also the engine default when no format is set |
| `number_full` | `1,234.56` | every digit at every magnitude |
| `compact` | `1.2M` | two significant digits: `45K`, `1.2B`; analytic (K, M, B) in every slot, KPIs included |
| `percent` | `2.3%` — input is decimal fraction (0.023) | line/bar/area/scatter via the query's `formats:` or `style.axis_y.labels.format` |
| `percent_number` | `2.3%` — input is whole-number percent (2.3) | **KPI only** (`style.value.format`) |
| `percent_delta` | `+2.3%` — input is decimal fraction | |
| `percent_number_delta` | `+2.3%` — input is whole-number (2.3) | **KPI only** |
| `percentage_points_delta` | `+3.2 pts`; input is whole points (3.2), not a fraction (0.032) | for a point delta, not a ratio delta |

The `$` in these specs prints the board locale's currency: `$` in `en-US`, `€` after the digits in `de-DE`. `regional.currency: EUR` (an ISO code; also in `charts/meta.yml` for a folder) swaps the symbol, keeping the locale's placement.


**`percent_number` and `percent_number_delta` only work on `type: kpi`.** Using them on line/bar/area/scatter causes a render error. For those chart types use `percent` or `percent_delta` (decimal-fraction input) or omit formatting and accept axis defaults.

## Common Mistakes

| Mistake | Fix |
|---------|-----|
| Building the entire dashboard in one pass | Build one chart at a time |
| Writing SQL without checking column names | Check INFORMATION_SCHEMA via `{{ s_execute_query }}` first |
| Hardcoding values during exploration | Use `{{ variables }}` from the start so the query cache survives |
| Skipping the render between changes | `{{ s_render_board }}` after every YAML edit |
| Too many charts (>8) the user didn't ask for | Split into multiple dashboards |
| Using chart-type names as keys in layout | `table:` or `bar:` as keys cause parse errors — use descriptive names like `revenue_table` |
| Referencing query names in layout | Layout references charts, not queries — create a chart that wraps the query |
| KPI query returns multiple rows | Use `SUM()`/`COUNT()`/`AVG()` to aggregate to 1 row |
| Pie with raw unaggregated data | `GROUP BY` in query to aggregate before charting |
| Putting `height:` or `aspect_ratio:` under `style:` | Move them to chart root: `charts.my_chart.height: 400` — `style:` is paint only |
| Writing raw D3 format strings (`"$,.2s"`, `".1%"`) | Use a named preset (`currency`, `percent`) — clearer and theme-consistent |
| Scaling values for display in SQL (`/1000`, `_k`/`_m` columns) | Query in natural units; compacting is the chart format's job (`currency`) |
| Writing a raw hex in a color slot (`color: "#4C78A8"`) | Author a palette token{{#if_tool docs}} — `{{ s_docs_color }}` —{{/if_tool}} or no color at all, and let the theme pick |
| Putting `format:` on a line/bar/area/scatter chart | Name the column in the query's `formats:`, or use `style.axis_y.labels.format:` for the measure — chart-root `format:` is rejected on cartesian chart families |
| Putting a number preset on `style.axis_x.labels.format` | `axis_x` is the dimension channel regardless of chart orientation. It raises `ERR-LABEL-FORMAT-AXIS-MISMATCH` on a band scale whose ticks are not already numbers, and on a temporal x too (dates get no exemption). Format the measure (the query's `formats:` / `style.axis_y.labels.format`) instead — a number preset belongs on the dimension only when its ticks are themselves numbers |
| Using `percent_number` or `percent_number_delta` on a line/bar/area chart | These are KPI-only — use `percent` or `percent_delta` instead (input must be decimal fraction 0–1) |
| Nesting `columns`/`values` under `sql:` for inline data | `sql:` is a string — inline data lives directly under the query name: `queries.my_data.columns: [...]` and `queries.my_data.values: [[...]]` |
| Putting `layers:` on a non-cartesian chart type | `layers:` is only valid on `bar`, `line`, `area`, and `scatter` — use `y: [col1, col2]` on any of those for simple multi-series without overlay marks |

## Rationalizations to Resist

| Excuse | Reality |
|--------|---------|
| "User asked for the whole dashboard at once" | Build incrementally anyway. Iterate to the result, don't one-shot it. |
| "I know what the columns are called" | You don't. Check INFORMATION_SCHEMA with `{{ s_execute_query }}`. |
| "Validation is slow, I'll do it at the end" | Validation is instant. Skipping it costs more time debugging later. |
| "It's just a quick chart, no need for variables" | Variables cost nothing and enable caching. Use them. |
| "15 charts is too many, I'll trim their list" | An explicit user ask wins. Suggest a split once, then build all 15. The 8-max default applies when the design is yours to choose. |

## Red Flags — STOP

- About to write SQL without having explored the actual schema first
- Building more than one new chart before validating the previous one
- Dashboard has more than 8 visualizations the user didn't ask for
- A chart exists without a clear question it answers
- Using hardcoded values that should be variables
- Delivering without running the self-review checklist
