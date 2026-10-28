---
name: board-migrate
description: 'Migrate a dashboard from another BI tool (Looker, Sigma, Hex, Omni)
  or a screenshot into a dbt Charts board: the same structure, controls, formats and
  numbers, in dbt Charts'' own styling, proven by a parity table. Reads a source bundle
  (outline, compiled SQL, renders, KPI values) or images. Use for ''migrate'', ''replicate'',
  ''recreate'', ''port this dashboard'', ''move off Looker'', a BI dashboard URL,
  or an attached dashboard image. Do NOT use for building from scratch, or for copying
  a board already in this project (chart).'
metadata:
  author: fivetran
---
# Board migrate

The user wants an existing dashboard (a Looker, Sigma, Hex or Omni page, an
export, a screenshot) as a dbt Charts board. This is a migration, not a
redesign. **Set your design opinions aside for the whole job**: the
one-screen/≤8-visualization guidance in `get_skill('chart')` does not apply,
chart-choice preferences do not apply, "this would be cleaner as…" does not
apply. The source is the spec. A 40-tile source means a 40-tile board.

Work from the source's own files, never from memory of what the dashboard
"probably" shows. **Write only the target board**: never delete, move or
overwrite project files, the bundle, or anything you did not create.

## Getting a bundle

A **source bundle** is a directory with one layout for every tool.
If the user already has a bundle, use it. For any other tool,
either write a small fetcher against its API that emits the layout below, or
work from screenshots (§ Sources → Screenshots). Never paste credentials into a
file, commit or chat.

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

## Modes — ask once, before any YAML

Ask one question up front, unless the caller already set it: **keep the
source's SQL verbatim, or rewrite it clean?** Never pick silently.

- **Verbatim.** Each tile keeps the source's compiled query, changed only where
  variables and the project's table qualifiers require. Faithful first step;
  every cleanup is then an offered pass.
- **Clean.** Rewrite each query to equivalent idiomatic SQL over the same
  models, applying the clean-mode rules in § Rules. Nothing that changes a
  value.

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

**Copy from the source:**

- Pages, sections, order and tile placement; every tile's chart type
- Titles, series names, axis titles and column labels, verbatim
- Every control, its label, default and the tiles it reaches
- Each tile's number and label formats, axis ranges, value labels, totals
- Data semantics: metrics, dimensions, grain, filters, sort, date window
- Text tiles: every line, field and link

**Keep dbt Charts' own (do not match):** colors and palettes, fonts, spacing,
gridlines, backgrounds, logos, and display idioms such as `—` for NULL. Pin a
color only for a fixed meaning (good/bad, a brand), and then with theme tokens,
never hex.

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

Both callouts stay until phase 2 has wired every tile.

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
only the PNG: orientation; stacking; which column splits series; dual-axis
sides; value labels and stack totals; legend; axis titles (and their absence);
axis range; number and label formats; sort; date grain and window; KPI
comparison, target and tone; table columns, header labels, page size.

### 3. Iterate until it looks right

Render after every tile (`get_skill('chart')` owns the build-validate loop —
one tile at a time, never one-shot). Then render the whole board with
`render_board` and compare its chart list with the source's tiles.

Close phase 1 with a tally: "8 sections, 41 tiles — 41 rebuilt." A tile you
cannot represent is **flagged to the user now, never silently dropped**.

## Phase 2 — wire real data

### 4. Find the tables

With a bundle, `queries/` names the source tables: read from the same model the
source reads, never rebuild its logic from upstream tables. Read any hidden or
data-prep element's SQL first; its `WHERE` holds the workbook-wide rules.
Without SQL, explore the warehouse with `execute_sql` against
INFORMATION_SCHEMA.

### 5. Swap tile by tile

Replace each placeholder with its real query, in the chosen mode, validating and
re-rendering as you go. One base query per source dataset with filters applied
once; tile queries read `FROM {{ queries.<base> }}`. A tile whose filters differ
from its siblings gets its own query. A tile with no plausible source keeps its
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
  board-wide variable shows on every tab.
- Name variables for what they set; options from the data, `static:` only for
  true enums; multiselects get `default: []`. Labels are the source's.
- The default window is the source's (30 days stays 30 days, not 28 or a
  rolling 12 months): wrong windows move every number.

**What the reader sees**
- Titles, series names, axis titles and labels verbatim; `style.title.font.case:
  none` stops the theme recasing them. The SQL mode goes in `notes`, not titles.
- Each tile's value and label format (`value_format_name`, `formatString`,
  `label_value_format`), written as the built-in name that prints the same:
  `usd_0`, `$#,##0`, `$,.0f` → `currency_whole`; `decimal_0`, `#,##0`, `,d` →
  `integer`; `percent_0`, `0%` → `percent_whole`; `percent_1`, `0.0%` →
  `percent`; Looker's K/M ladder (`[>=1000000]$#.0,,"M";[>=1000]$#,"K"`) →
  `currency_compact`. A d3 spec only where no name prints the same (`$.2s` is
  not compact currency: it prints `$7.0` and `$250m`). IDs → `id`.
- Axis ranges as the source sets them, and check filtered states: a pinned
  domain that clips when a filter widens the data is a bug.
- Totals only where the source shows them, and visible on page one when it does.
- Text and header tiles keep every line, field and link; README prose stays
  verbatim (spelling included).
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
- Dates are typed: return `DATE`/`TIMESTAMP` at the grain, never
  `FORMAT_DATE`/`FORMAT_TIMESTAMP`/`TO_CHAR` on an axis column; the chart formats.
- Select only the columns the chart encodes; no `SELECT *`.
- Relative windows use the picker's presets and `filter_date_range`; the
  default window lives once, in the base query's `{% else %}` branch; no literal
  date in any `default:`.
- Variables: one per concept, named for what it sets, options from the data;
  drop controls nobody sets only if the source has none (else keep, per Controls).
- Strip source commentary and generated aliases (`cast_39`); name columns as a
  reader would.
- One base query per dataset, not a per-tile `WITH` stack re-deriving it.
- Pivots: return long format and let the table's `rows:`/`columns:`/`values:`
  pivot it; no cross joins or `CUBE` grids to fill empty cells. Each pivot
  field the source lists is its own `columns:` level, in the source's order
  (two pivots → `columns: [creator_bucket, channel]`), never one concatenated
  column.

**Cleanup passes** (offered after parity holds, in this order, one at a time:
"I can do X; here's what it changes; go?"; applied only on a yes, re-run against
parity, shown as a diff; never applied unasked)
1. **Timezone.** Read the source's query timezone (`source.json`) and the
   warehouse session timezone (probe the adapter). Drop wrapping that converts
   nothing (same zone, or a `DATE` column); centralize the rest into one derived
   column in the base query.
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
- Merged results and table calculations: one query, window functions.
- Symmetric aggregates and `convert_tz` are on every query; pass 2 and pass 1.

### Sigma
- Hidden pages are data prep: fold their SQL into base queries; don't render
  them. Their `WHERE` holds workbook-wide rules.
- A list control with `mode="exclude"` is `NOT IN`; label it "Exclude …", and
  decide null rows on purpose.
- A control's `targets` reach every element built from the target's
  `source` chain.
- `MovingAvg(x, 0, 4)` is current row plus 4 **following**. A "rolling" label
  means trailing: write `ROWS BETWEEN 3 PRECEDING AND CURRENT ROW` and say so.
  `MovingAvg(x, n)` already trails.
- Date-range controls are relative and UTC; leave `default:` blank, set
  `placeholder:` to the window ("Last 30 days"), apply it in the base query.
- KPI `comparison.display`: `delta` is value − target; absent means percent
  difference. Carry `dataLabel: shown` and `totals: shown`; skip `values=[]`
  and disabled element filters.

### Hex
- Input parameters are the controls; relative date defaults carry a timezone.
- SQL cells chain: a cell can select from an earlier cell's dataframe, and use
  Jinja parameters. Inline the chain into base queries.
- `png/` holds one render per chart cell, not the whole app; layout comes from
  `outline.md`.
- A dropdown whose options come from a dataframe column gets data options.

### Omni
- Dashboard filters list every control; duplicates with ids are distinct
  controls. Fiscal flags on time filters change the window.
- `source.json` `query_timezones` is pass 1's source timezone.
- Topics and the semantic model carry joins the SQL only reflects; read the
  compiled SQL, and note the dbt model it should read from.

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
| Rolling 12 months instead of the source's window | The source's default window, applied once in the base query |
| Recased or reworded titles | Verbatim, `style.title.font.case: none` |
| Losing label formats, axis ranges or time of day | Read the format, range and type per tile from the raw definition |
| Inventing totals, or dropping them | Totals exactly where the source shows them |
| Truncated wide tables, header tiles without fields and links | Full width, every column, every line and link |
| Tab-only filters on every tab | Nested-board `variables:` on that tab |
| Migrating hidden or graveyard pages | List them as dropped |
| Copying a forward-looking moving average | Trailing window, and say so |
| Cleaning SQL before parity, or unasked | Parity first; passes are offers |
| Delivering with validate errors | Fix every one first |
| Deleting files to "start clean" | Write only the target board |
| Copying the source's colors and fonts | Theme only; tokens for fixed meanings |

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
