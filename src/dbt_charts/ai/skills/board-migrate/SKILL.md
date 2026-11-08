---
name: board-migrate
kind: workflow
description: >-
  Migrate a dashboard from another BI tool (Looker, Sigma, Hex, Omni) or a screenshot
  into a dbt Charts board: the same structure, controls, data and metric names, in dbt
  Charts' own visual design, proven by a parity table. Reads a source bundle (outline, compiled
  SQL, renders, KPI values) or images. Use for 'migrate', 'replicate', 'recreate',
  'port this dashboard', 'move off Looker', a BI dashboard URL, or an attached dashboard
  image. Do NOT use for building from scratch, or for copying a board already in this
  project (chart).
metadata:
  author: fivetran
---
# Board migrate

The user wants an existing dashboard (a Looker, Sigma, Hex or Omni page, an
export, a screenshot) as a dbt Charts board. This is a migration, not a
redesign. **Set your design opinions aside for the whole job**: the
one-screen/≤8-visualization guidance in `{{ s_skill_chart }}` does not apply,
chart-choice preferences do not apply, "this would be cleaner as…" does not
apply. The source is the spec. A 40-tile source means a 40-tile board.

Work from the source's own files, never from memory of what the dashboard
"probably" shows. **Write only the target board**: never delete, move or
overwrite project files, the bundle, or anything you did not create.

## Getting a bundle

A **source bundle** is a directory with one layout for every tool.
{{#if_surface cli}}For supported tools, fetch one (credentials come from
environment variables; `--help` names them):

```bash
dct import looker <dashboard URL or id>   # LOOKER_CLIENT_ID / _SECRET
dct import sigma <workbook URL or id>     # SIGMA_CLIENT_ID / _SECRET; --list <text> to find one
dct import hex <project URL or id>        # HEX_API_TOKEN; --list
dct import omni <dashboard URL or id>     # OMNI_API_KEY; --list
```

Each writes `./<tool>-<id>/` (`--out` to change it) and prints the path.

### API keys

`dct import` reads keys from the environment only. When one is missing,
follow the project's own instructions for keys if it has them (an `AGENTS.md`,
a skill, a `.env.example`). Otherwise ask once: a gitignored `.env` at the
project root (offer this), or their own way (shell profile, a secrets
manager), which you then follow. For a `.env`:

1. Confirm `git check-ignore .env` prints `.env`; if not, add `.env` to
   `.gitignore` first.
2. Append the variable names the tool needs, values empty
   (`LOOKER_CLIENT_ID=`), without touching lines already there.
3. Open it for them (`code .env`, or their editor) and tell them where the
   tool's keys come from: Looker → Account → API keys (an admin may need to
   enable them); Sigma → Administration → Developer Access (a Sigma admin
   creates them; also set `SIGMA_API_BASE` for the org's region); Hex and
   Omni → the workspace's API token settings. With a bare id rather than a
   URL, also set `<TOOL>_BASE_URL`. Values go in single quotes
   (`LOOKER_CLIENT_SECRET='…'`) so the shell keeps `$` and spaces.
4. Once they say it is saved, load it in the same command as the import:
   `set -a; . ./.env; set +a; dct import looker <URL>`.

Never read, print or `cat` `.env` or a key.
{{/if_surface}}If the user already has a bundle, use it. For any other tool,
either write a small fetcher against its API that emits the layout below, or
work from screenshots (§ Sources → Screenshots). Never write a credential
value into a file, commit or chat yourself.

## Bundle layout

| Path | Use it for |
|---|---|
| `outline.md` | **Read first.** Pages → tiles in reading order: kind, title, grid position, encodings, formats, filters, controls and which tiles each control reaches. A digest, not the truth |
| raw definition (`dashboard.json`, `contents.json`, `project.hex.yaml`) | **Authoritative.** Grep it whenever the outline is silent or ambiguous: control modes, label formats, totals, axis ranges, hidden flags, text-tile templates |
| `queries/` | The source's compiled SQL per tile. Source tables, business rules, and the workbook-wide filters the outline never shows |
| `png/` | The source's own render of each page (Hex: each chart cell). The visual target; look at every one before writing YAML |
| `kpi/` | Current value of each single-number tile. Your parity numbers |
| `source.json` | Tool, URL, fetch time, and extras such as `query_timezones` |

A screenshot-only bundle has `png/` and `source.json`, nothing else.

A tile or image the import could not fetch is named in `outline.md` with the source's error.

## Modes — ask once, before any YAML

Ask one question up front, unless the caller already set it: **keep the
source's SQL verbatim, or rewrite it clean?** Never pick silently.

- **Verbatim.** Each tile keeps the source's compiled query, changed only where
  variables and the project's table qualifiers require. Faithful first step;
  every cleanup is then an offered pass.
- **Clean.** Rethink each query from what its chart needs and write it fresh
  over the project's models, applying the clean-mode rules in § Rules. The
  source's SQL is evidence of what each number means, never a template.
  Nothing that changes a value.

Parity (§ Prove it) is the acceptance test in both modes. On a large board
(below) the structure checkpoint is the only other question; the cleanup offers
come after parity. Three questions at most.

## Two phases, one job — do not hand back at the seam

Work in two phases: **the look on placeholder data first, real data second.**
The phase-1 board doubles as your notes: every value you read off the source is
recorded as inline data, ready to check phase-2 queries against. With a bundle
that has `queries/` and `kpi/`, phase 1 is quick; do not skip it.

The phases are your working order, not a delivery boundary. **Run straight
through both and deliver a board on live data.** Nothing runs between turns:
stopping after phase 1 without a question ends the job with invented numbers.

- **Roughly ten or fewer tiles that need a query** — no check-in.
- **More than that** — say up front you'll build the structure on placeholder
  data and come back before wiring queries. Deliver phase 1, ask whether the
  structure is right, and wire the queries as soon as they say go.

A phase-1 board is never "migrated": call it "phase 1 of 2 — structure only,
every number is a placeholder", and end it with a next step or a question.

## The fidelity contract

Data faithful, visually default.

**Copy from the source (the data and what it means):**

- Pages, sections, tile order, and every tile's chart type, orientation and
  stacking (stacked, grouped, 100%) exactly as the raw definition states:
  a stacked bar stays stacked. dbt Charts groups a colored bar by default, so a
  stacked source needs `style:` `stack: zero` (100%: `stack: normalize`)
- Metric and dimension names: the words that carry meaning in titles, series,
  columns and controls. Strip decoration: emoji, a leading `#`, `Sum of …`/`Count
  of …` generator prefixes, and typos (fix the spelling)
- Every control, its label, default and the tiles it reaches
- Data semantics: metrics, dimensions, grain, filters, sort, date window, totals
- Text tiles: every line, field and link

**Keep dbt Charts' defaults (do not match the source's look):** colors, fonts,
spacing, gridlines, title casing, tick formats and rotation, legend position,
data labels, column widths, axis titles, and number formats (compact by
default). Override a default only for a reason the reader would miss without
it, and say why in the chart's `notes:`. "The source does it" is not a reason.
- Color: only for a fixed meaning that is in the board (a target, a threshold,
  good/bad), with theme tokens, never hex.
- Data labels: only when there are five or fewer marks, or one value is the point;
  never on every point of a series.
- Dual axes: never by default. Two measures on different scales (a count and
  hours, thousands and tens) become two charts side by side on the same query;
  a reader compares their shapes, not their values. Several noisy series on two
  scales become an index chart (each series over its own period-average
  baseline, one axis) or small multiples.
- Precision: keep a source format only when its precision means something
  (a rate to one decimal); otherwise use the built-in name for the kind
  (`currency_compact`, `integer`, `percent`). A KPI tile is the exception:
  its number is the content, so it keeps the source's digits (`currency_whole`,
  `integer`) and is compact only when the source itself abbreviates.
- No invisible layers or helper columns to fake a feature (an axis floor, stack
  totals). Note the gap instead.
- Never invent data. A table the warehouse will not read, a query that fails,
  a value you can only see in the screenshot: the chart stays blocked with a
  `notes:` naming what is missing. No typed-in values, no `UNNEST([STRUCT(...)])`
  or `generator` rows, no "placeholder" or "fake data" banners. A board that
  shows a number must have run the query that produced it.
- `warnings_ignore` needs a reason in `notes:`. Fix copy-edit warnings by fixing
  the text.
- YAML in block style: every mapping key on its own line, never an inline
  `{ position: right }`. Inline lists (`[a, b]`) are fine.

## Phase 1 — structure on placeholder data

### 1. Plan and banner

Write the mapping in chat first: page → tab or section, tile → chart name and
type, control → variable, and what you drop and why. Then open a **new** board
whose first and last rows are warning callouts:

```yaml
charts:
  fake_data_warning:
    type: callout
    title: "Fake data"
    message: >-
      This board mirrors the source dashboard's structure. Every number below
      is a placeholder read off the source; no live query is wired yet.
    style:
      tone: warning
rows:
  - fake_data_warning
  # Rebuild the source sections here.
  - fake_data_warning
```

Both callouts stay until phase 2 has wired every tile; then delete them.

### 2. Rebuild every tile with inline values

Section by section, give each tile an inline `values:` query holding what the
source shows (KPIs exactly from `kpi/`, lines and bars as a handful of points,
tables as headers plus a few rows) and a chart that matches the source tile:

```yaml
queries:
  weekly_active_orgs:
    notes: "Phase-1 placeholder — values read off the source"
    columns: [week, active_orgs]
    values:
      - [2026-06-01, 210]
      - [2026-06-08, 240]
```

**Options to copy deliberately**, each checked against the raw definition, not
only the PNG: orientation; stacking; which column splits series; sort; date
grain and window; KPI comparison, target and tone; table columns and page size;
totals. Everything visual beyond that starts from the dbt Charts default.

### 3. Iterate until it looks right

Render after every tile (`{{ s_skill_chart }}` owns the build-validate loop —
one tile at a time, never one-shot). Then render the whole board{{#if_surface cli}}
to PNG, every tab, and put each next to its `png/` page. A render shows one
tab: give `tabs:` an `id:` and pass each tab's slug (its title, lowercased,
punctuation dropped, spaces and hyphens as `_`) as that variable, and fix every error on every tab:

{{ s_render_png_example }}{{/if_surface}}{{#if_surface tool}} with
`{{ s_render_board }}` and compare its chart list with the source's tiles{{/if_surface}}.

Build the board in this session. If you hand pieces to sub-agents, wait for
every one and assemble its work before you finish; a session that ends with
work pending leaves an unfinished board.

Close phase 1 with a tally: "8 sections, 41 tiles — 41 rebuilt." A tile you
cannot represent is **flagged to the user now, never silently dropped**.

## Phase 2 — wire real data

### 4. Find the tables

With a bundle, `queries/` names the source tables: read from the same model the
source reads, never rebuild its logic from upstream tables. Read any hidden or
data-prep element's SQL first; its `WHERE` holds the workbook-wide rules.
Without SQL, explore the warehouse with `{{ s_execute_sql }}` against
INFORMATION_SCHEMA.

### 5. Swap tile by tile

Replace each placeholder with its real query, in the chosen mode, validating and
re-rendering as you go. In clean mode each chart gets its own query, written
from its intent (see Clean-mode SQL); in verbatim mode it keeps its tile's
compiled query. Name every query for what it returns (`arr_by_month`),
never by a source tile or element ID. A tile with no plausible source keeps its
placeholder and is marked **blocked**; keep going.

Fix every validate and render error before delivering; never hide one with
`--allow-chart-errors`. Remove the banner only when every tile is live or
flagged.

## Rules

Each rule is one a reviewed migration showed being broken.

**Controls**
- Keep every source control, including ones empty by default — the raw
  definition is the list, not the PNG's filter line.
- Wire each control to exactly the tiles it reaches (`listens`, `targets`, and
  the children of a targeted parent); a tile no control reaches stays unfiltered.
- Scope a page-only control to its tab (nested-board `variables:`); a
  board-wide variable shows on every tab. Only a control whose targets span
  every page goes in the board's top-level `variables:`.
- Name variables for what they set; options from the data, `static:` only for
  true enums; multiselects get `default: []`. Labels are the source's.
- The default window is the source's (30 days stays 30 days, not 28 or a
  rolling 12 months): wrong windows move every number.

**What the reader sees**
- Titles and labels keep the source's meaning in the theme's casing, without
  decoration. The SQL mode goes in `notes`, not titles.
- Source text: a heading is its section's `title`, a note or description is
  `notes:` or a plain text tile, a README page is a text tile. The finished
  board has no `callout`.
- Formats: the built-in name for the value's kind (`currency_compact`,
  `integer`, `percent`, `id`) on charts; a KPI headline keeps the source's
  digits (`currency_whole`, `integer`). Keep the source's precision only when it
  means something. The source's `value_format_name`/`formatString` tells you
  the kind and the unit, not the exact print. Currency stays currency and a rate
  stays a percent; a plain count or a date takes no `format:`. Never write the
  source's pattern (`,d`, `$#,##0.00`, `%m/%d/%Y`).
- Axis ranges: the default, unless the source sets one (Looker `minValue`/
  `maxValue`, Sigma axis bounds) or a zoom is the point (an uptime chart near
  100%); say so in `notes:`. Both bounds set: `scale: continuous: domain: [min,
  max]`; only a minimum above zero: `scale: continuous: zero: false`. A pinned
  domain that clips filtered data is a bug.
- Totals only where the source shows them, and visible on page one when it does.
  A table shows a total row only when its source says so: Looker, the tile's
  query has `total: true` (`row_total: right` adds a total column; the
  `vis_config` `show_totals` toggle alone draws nothing); Sigma, the element
  lists `axisTotals` or `totals:` (`totalPosition: first` puts it on top).
  Otherwise the table has none. A total row comes from its query
  (`GROUP BY ROLLUP` or `GROUPING SETS`) with a role column,
  `IF(GROUPING(<dim>) = 1, 'total', 'value') AS row_role`,
  and `row_role: row_role` on the table chart, so the table styles it as a total
  row with no row number. Its label cell reads `Total`.
- Data bars (Looker `series_cell_visualizations`, Sigma data bars): a table spark
  bar prints no number, so where the source shows data bars, show the value as its own column and the bar in a
  second column right after it (select the value twice, `n` and `n AS n_bar`;
  the bar column's label empty).
- Text and header tiles keep every line, field and link; README prose keeps
  its content, with typos fixed.
- Wide detail tables keep every row and visible column, readable: full width,
  `max_width`s, no `LIMIT` the source lacks.
- Timestamps keep time of day; dates keep their year.
- KPI targets carry good/bad tone (`percent_delta` with `tone`); visible KPI
  descriptions are drawn text, never `notes:`.

**Scope and evidence**
- Skip hidden, graveyard, draft and "to deprecate" pages; list them as dropped.
- Trust the definition over README prose: a README can be false; keep it
  verbatim and list the false sentences.
- The source can be wrong (fan-out, a mislabeled axis, a forward-looking moving
  average). Flag it with both numbers; fix it only with the user's yes.

**Clean-mode SQL** (always on in clean mode; none changes a value)

BI tools generate SQL for a semantic layer: they join every table an explore
might need and then de-duplicate, compute one wide base for many tiles,
protect every aggregate against fan-out, and convert time zones and format
labels in every query. None of that belongs on a board. Per chart:
- Write down the chart's intent first (measure and its definition, grain,
  dimensions, filters), from the compiled SQL and the source's field names.
- Query the narrowest existing model that has those columns, directly, at the
  chart's grain. Never a shared *pre-aggregated* base or CTE spine feeding many
  charts. One exception: when three or more charts need the same joins and
  filters, write one row-level base (joins and shared filters, no `GROUP BY`)
  and have those charts aggregate `FROM {{ queries.<base> }}`. Don't repeat the
  same join block in every chart.
- `{{ queries.x }}` inlines as `(…) AS x`: refer to its columns as `x.col` and
  never write a second alias after it (`FROM {{ queries.x }} AS r` fails to
  parse).
- Join only tables whose columns are selected or filtered; no join-then-undo.
  Filter with a plain `WHERE`, not an `EXISTS` subquery a join or column filter
  can replace.
- No `COALESCE(SUM(x), 0)` or `IFNULL` around an aggregate unless the chart must
  print 0 for an empty group.
- Before dropping a `SUM(DISTINCT …)`/`COUNT(DISTINCT …)` guard, run the grain
  check on the joined rows (`SELECT COUNT(*), COUNT(DISTINCT key)`): equal →
  plain `SUM`/`COUNT`; different → aggregate at the right grain, then join.
- Date windows live on the board clock, not in SQL: set the board's time zone
  (and fiscal start month) once, and filter with
  `{{ filter_date_range('col', var_or_preset) }}` (a timestamp column is passed
  bare) or a daterange variable whose `default:` is a preset (`last_13_months`,
  `this_fiscal_quarter`). No `CURRENT_DATE('<zone>')` arithmetic per query.
  The source's default window is the variable's preset `default:`, fiscal
  included ("this fiscal quarter" → `this_fiscal_quarter`); never frozen dates.
  Fiscal windows are fiscal presets with the fiscal start month set once on the
  board; never `DATE_TRUNC(DATE_ADD(…, INTERVAL 11 MONTH), QUARTER)` math in SQL.
- Dates are typed at the grain; no `FORMAT_DATE`/`TO_CHAR` on an axis column.
- No display text built in SQL (`FORMAT('%.1fK', …)`, label columns, helper
  columns for axis floors): the chart's `format` and axis settings do that.
- Select only the columns the chart encodes; no `SELECT *` from a raw table.
- Variables: one per concept, named for what it sets, options from the data.
- Strip source commentary and generated aliases; name columns as a reader would.
- Pivots: return long format and let the table's `rows:`/`columns:`/`values:`
  pivot it; each pivot field its own `columns:` level, in the source's order.

**Cleanup passes** (verbatim mode only: clean mode already applies them).
Offered after parity holds, in this order, one at a time: "I can do X; here's
what it changes; go?"; applied only on a yes, re-run against parity, shown as a
diff; never applied unasked.
1. **Timezone.** Read the source's query timezone (`source.json`) and the
   warehouse session timezone (probe the adapter). Drop wrapping that converts
   nothing (same zone, or a `DATE` column); move the rest to the board's
   `regional.time_zone`.
2. **Fan-out precautions.** Before replacing a symmetric aggregate
   (`SUM(DISTINCT … MD5/HASH …)`) run the grain check on the joined set
   (`SELECT COUNT(*), COUNT(DISTINCT key)`) and report it. No fan-out → plain
   `SUM`/`COUNT`; fan-out → aggregate each side at its grain, then join.
3. **Pivot-then-aggregate.** One aggregation at the chart's grain; window
   functions replace table calculations.
4. **Re-source.** Read from the narrowest table or model that has the columns.

Report cruft counts per pattern (timezone wraps, date strings, symmetric
aggregates, double aggregations, `SELECT *`, cross joins) before and after.

## Prove it

Deliver two tables; every mismatch gets a cause.

| KPI | Source (`kpi/`) | Board | Δ | Cause if ≠ |
|---|---|---|---|---|

| Tile | Rows / series | Matches? | Cause if not |
|---|---|---|---|

Run the board at the source's defaults. Usual causes: late-arriving data since
the fetch, a different default window, a hidden-page rule you missed, a source
bug. When the bundle has `queries/`, re-run the source's SQL now and compare
against that, not only the snapshot. Then deliver the tally ("39/41 live; 2
blocked — why"), what you dropped and why, source bugs found, and the cleanup
passes offered.

## Sources

### Looker
- Controls: `dashboard_filters` in `dashboard.json`, in its `row` order; each
  tile's `listen` names the filters it obeys, and a listener overrides the
  tile's own pinned filter.
- Formats: `value_format_name` for values, `label_value_format` for labels;
  `show_totals_labels`, `y_axes` (sides, `showLabels`), `series_cell_visualizations`
  (data bars), and pivot `total`/`row_total`.
- Header tiles often render a LookML `html:` template the bundle lacks; look in
  the project's LookML, or flag the missing fields.
- Looker draws a null dimension value as `∅` (legend, axis, table cell). Keep
  it, `COALESCE(<dim>, '∅')` (cast a non-string dimension to a string first),
  rather than inventing `Unknown` or `No segment`.
- `offset(x, k)` and `offset_list(x, start, n)` step through the result's rows
  in the query's `sorts`. With a date sorted descending, +1 is the row before:
  `mean(offset_list(x, 0, 7))` is this day and the 6 before it, `AVG(x) OVER
  (ORDER BY date ROWS BETWEEN 6 PRECEDING AND CURRENT ROW)`. A window of N rows
  is N − 1 PRECEDING.
- Merged results and table calculations: one query, window functions. Every
  merged query's fields stay on the tile (a merged-in target or goal is the
  merge's point): join the queries on `merge_fields`.
- Symmetric aggregates and `convert_tz` are on every query; pass 2 and pass 1.

### Sigma
- Hidden pages are data prep: fold their SQL into the chart queries; don't render
  them. Their `WHERE` holds workbook-wide rules.
- A column marked `[hidden: true]` in the outline is not drawn: no series, no
  table column. A hidden `Goal = 0.85` is no goal line.
- A list control with `mode="exclude"` is `NOT IN`; label it "Exclude …", and
  decide null rows on purpose.
- A control's `targets` reach every element built from the target's
  `source` chain.
- `MovingAvg(x, 0, 4)` is current row plus 4 **following**. A "rolling" label
  means trailing: write `ROWS BETWEEN 3 PRECEDING AND CURRENT ROW` and say so.
  `MovingAvg(x, n)` already trails.
- Date-range controls are relative and UTC: the variable's `default:` is the
  matching preset, applied with `filter_date_range`. "Last N days" →
  `last_N_days`; "last N <unit> including this <unit>" → `last_N_<unit>`
  (calendar-aligned: the current unit plus the N−1 before it); "excluding this
  <unit>" → `previous_N_<unit>`.
- A table's `summary: [ids]` names columns Sigma shows as totals in a bar
  above the table (`# Open Tickets = CountDistinct([Url Link])`). Each is a
  KPI over the table's rows with the table's filters, placed in a row directly
  above the table, titled with the column's name. Don't drop them.
- KPI `comparison.display`: `delta` is value − target; absent means percent
  difference. Carry `dataLabel: shown` and `totals: shown`; skip `values=[]`
  and disabled element filters.

### Hex
- Input parameters are the controls; relative date defaults carry a timezone.
- SQL cells chain: a cell can select from an earlier cell's dataframe, and use
  Jinja parameters. Inline the chain into the chart queries.
- `png/` holds one render per chart cell, not the whole app; layout comes from
  `outline.md`.
- A dropdown whose options come from a dataframe column gets data options.

### Omni
- Dashboard filters list every control; duplicates with ids are distinct
  controls. Fiscal flags on time filters change the window.
- `source.json` `query_timezones` is pass 1's source timezone.
- Topics and the semantic model carry joins the SQL only reflects; read the
  compiled SQL, and note the dbt model it should read from.
- `queries/<id>.sql` is the warehouse query, runnable as is. A tile with a
  `post-step:` outline line also has `queries/<id>.post.sql`, DuckDB over that
  result (`step_ref__1`): the tile's final shape is computed there, so fold its
  logic into your own SQL.

### Screenshots
- Say up front what a screenshot cannot tell: controls empty by default, hidden
  filters, exact formats, totals off-screen, SQL.
- Read values as expectations, not answers: the image is a stale snapshot. Read
  its date range off the axes, match on grain, units and magnitude, and never
  bend a correct query to reproduce a stale number.
- A wild mismatch (335 vs 7M) is a wrong table or query, not drift.

## Common mistakes

| Mistake | Fix |
|---------|-----|
| Stopping at the phase seam with no question | Small board: keep going. Big board: announce the checkpoint, end phase 1 with the question |
| Picking the SQL mode yourself | Ask once, before any YAML, unless the caller set it |
| Dropping controls empty by default, or wiring them to the wrong tiles | The raw definition's filters and listeners are the list |
| Rolling 12 months instead of the source's window | The source's default window as the variable's preset `default:` |
| Copying the source's look (emoji, forced tick formats, labels on every point, dual axes, bottom legends, fixed widths) | dbt Charts defaults; override only with a reader-facing reason in `notes:` |
| Reworded metric names, or losing time of day | Keep the meaning of every name; read the type per column from the raw definition |
| Inventing totals, or dropping them | Totals exactly where the source shows them |
| Truncated wide tables, header tiles without fields and links | Full width, every column, every line and link |
| Tab-only filters on every tab | Nested-board `variables:` on that tab |
| Migrating hidden or graveyard pages | List them as dropped |
| Copying a forward-looking moving average | Trailing window, and say so |
| Cleaning SQL before parity, or unasked | Parity first; passes are offers |
| Delivering with validate errors | Fix every one first |
| Deleting files to "start clean" | Write only the target board |
| Copying the source's colors and fonts | Theme only; tokens for fixed meanings |
| A callout for a heading, note or README | The section's `title`, `notes:` or a plain text tile |
| Grouped bars where the source stacks them | The source's stacking, from the raw definition |
| Keeping a leading `#` (`# Bugs out of SLA`) | Drop it (`Bugs out of SLA`): the number already says it is a count |
| Copying the source's number or date pattern | The kind's built-in name, or no `format:` for counts and dates |

## Rationalizations to resist

| Excuse | Reality |
|--------|---------|
| "Phase 1 is a natural place to hand back" | Only if you announced the checkpoint and asked. An unannounced stop reads as finished. |
| "It'd be cleaner as an 8-chart summary" | The user asked for *that* dashboard. Condensing is a silent failure. |
| "That control is empty, nobody uses it" | It is in the source; readers set it. Keep it. |
| "Last 12 months is close enough" | Every count moves. Use the source's window. |
| "The README says filters apply everywhere" | The definition decides; the README can be wrong. |
| "This SQL is ugly, I'll tidy it while I'm here" | Mode decides. Cleanups that change shape are offers after parity. |
| "The source number looks wrong, I'll fix it" | Flag it with both numbers; fix only on a yes. |
| "Close enough — most tiles are there" | "Most" is not a tally. Every tile is live, blocked, or flagged. |
