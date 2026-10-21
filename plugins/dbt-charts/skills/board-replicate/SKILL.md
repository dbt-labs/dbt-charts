---
name: board-replicate
description: Reproduce a dashboard the user supplies as a screenshot, image, or export
  — its structure and data, in dbt charts' own styling. Use for 'replicate', 'recreate',
  'rebuild this in dbt charts', 'migrate from Looker', or when an image of a dashboard
  is attached. Do NOT use for building from scratch, or for copying a board already
  in this project (chart).
metadata:
  author: fivetran
---
# Dashboard Replicate

The user handed you an existing dashboard — a screenshot, an export, a page
from another tool — and wants *that* in dbt charts. This is a copy/migration,
not a redesign. **Set your design opinions aside for the whole job**: the
one-screen/≤8-visualization guidance in `get_skill('chart')` does
not apply, chart-choice preferences do not apply, "this would be cleaner
as…" does not apply. The source is the spec. A 40-tile source means a 40-tile
replica — sectioned and scrolling if the source is.

Work in two phases: **replicate the look on fake data first, wire real data
second.** The phase-1 board doubles as your notes — every rough value you
read off the image is recorded as inline data, ready to check phase-2 queries
against.

## Two phases, one job — do not hand back at the seam

The phases are your working order, not a delivery boundary. **The default is
to run straight through both and deliver a dashboard on live data.** Nothing
runs between turns: stopping after phase 1 without a question ends the job
with a board of invented numbers as the deliverable.

Count the tiles that need a query before you start, and say what you're doing:

- **Roughly ten or fewer** — no check-in. Phase 1, then phase 2, one delivery.
  Asking permission to write six queries costs the user more than it saves.
- **More than that** — open by telling the user you'll build the replica on
  placeholder data first and come back before wiring queries, so they can
  correct the structure while it's cheap. Deliver phase 1, ask whether the
  structure is right, and wire the queries as soon as they say go.

Both paths are bound by two rules:

- **A phase-1 board is never "recreated".** Say "phase 1 of 2 — structure
  only, every number is a placeholder", never anything that reads as finished.
- **Every phase-1 delivery ends with a next step or a question** — "wiring
  the real queries now" or "does this structure look right before I wire it?"
  A phase-1 delivery with neither is the failure this section exists to stop.

## The fidelity contract

**Copy from the source:**

- Section structure, order, and tile placement
- Every tile's chart type (a bar stays a bar, a table stays a table)
- Chart options that change what the reader sees (the checklist below)
- Data semantics — metrics, dimensions, grain, filters, sort order

**Keep dbt charts' own (do not try to match):**

- Colors and palettes
- Fonts and typography
- Spacing, gridlines, backgrounds, logos
- Display idioms — direct labels over legends, format aliases, `—` for NULL

## Phase 1 — visual replica on fake data

### 1. Start with the warning banner

Open a **new** board whose first and last rows are warning callouts, so nobody
mistakes the replica for live numbers while entering or leaving the board:

```yaml
charts:
  fake_data_warning:
    type: callout
    title: "Fake data"
    message: >-
      This is a visual replica of the source dashboard and a starting point.
      Every number below is a placeholder read off the source image; no live
      query is wired yet.
    style:
      tone: warning
rows:
  - fake_data_warning
  # Rebuild the source sections here.
  - fake_data_warning
```

Both callouts stay until phase 2 has wired every tile.

### 2. Rebuild every tile with inline fake data

Work through the source section by section. For each tile, write an inline
`values:` query holding roughly the data you can read off the image, and a
chart that matches the source tile:

```yaml
queries:
  weekly_active_orgs:
    notes: "Phase-1 placeholder — rough values read off the source image"
    columns: [week, active_orgs]
    values:
      - [2026-06-01, 210]
      - [2026-06-08, 240]
      - [2026-06-15, 265]
```

Read the values as carefully as the image allows — KPI numbers exactly,
line/bar shapes as a handful of representative points, tables as their header
row plus a few visible rows. These recorded values are what phase 2 verifies
real queries against, so rough is fine but invented-from-nowhere is not.

**Chart options to replicate deliberately** (check each against the source):

- Orientation — vertical vs horizontal bars
- Stacking — stacked vs grouped vs single series
- Multi-series encoding — which column splits the series
- Value labels on marks — present or not
- Legend — present or not
- Axis titles and which values sit on which axis
- Number format family — currency, percent, number (use format aliases)
- Sort order — by value, by category, by time
- Date grain and range
- KPI extras — comparison/delta line, trend sparkline
- Table columns — set, order, and header names

Options **not** on that list (colors, fonts, exact pixel sizing) are the
theme's business — leave them alone.

### 3. Iterate until it looks right

Render after every tile (`get_skill('chart')` owns the build-validate loop —
one tile at a time, never one-shot). When all tiles are in, render the whole
board with `render_board` and compare its chart list with the
source's tiles.

Judge structure parity — sections, tile count, chart types, options from the
checklist — not colors or fonts; those are intentionally dbt charts' own.

Close phase 1 with a tally against the source: "8 sections, 41 tiles — 41
replicated." On the checkpoint path that tally is the message you send with
the question; on the straight-through path it rolls into the final delivery.
A tile you cannot represent (unmappable chart type) is **flagged to the user
now, never silently dropped**.

## Phase 2 — wire real data

### 4. Find the real tables

Explore the warehouse vocabulary with `execute_sql` against
INFORMATION_SCHEMA and map each tile's placeholder query to candidate tables
and columns.

### 5. Verify candidates against your recorded values — expect a stale snapshot

The phase-1 inline values are your expectations. Compare each candidate
query's results against them, knowing the screenshot is usually days or weeks
old, so live data will look phase-shifted:

- Read the date ranges off the source's axis labels before comparing.
- Match on **grain, units, and order of magnitude** — not exact values.
- Roughly matching magnitudes at the right grain → you found the right table.
- A wild mismatch (you recorded 335, the query returns 7M) → wrong table or
  wrong query. Investigate the query, not the data.
- Never bend a correct query to reproduce a stale number.

### 6. Swap tile by tile, then drop the banner

Replace each placeholder `values:` query with its real `sql:` query one at a
time, validating and re-rendering as you go. A tile with no plausible source
keeps its placeholder and gets marked **blocked** — keep going.

Remove the warning banner only when every tile is wired to live data or
explicitly flagged. Deliver with the completeness tally and the observed
snapshot offset: "39/41 tiles on live data; 2 blocked — no source columns for
X, Y. Source image shows data through June; live data runs through July."

## Common mistakes

| Mistake | Fix |
|---------|-----|
| Stopping at the phase seam with no question | Small board: keep going into phase 2. Big board: announce the checkpoint up front and end phase 1 with the question |
| Reporting phase 1 as the recreated dashboard | Placeholder numbers are not a recreation — label it "phase 1 of 2", and name what's still unwired |
| Summarizing instead of replicating | The source tile count is the spec — rebuild every tile or flag it |
| Redesigning while copying | Design skills are suspended — copy the source's choices from the options checklist |
| Copying the source's colors and fonts | Structure, options, and data are copied; styling stays dbt charts' |
| Skipping the warning banner | Fake numbers with no banner look like a finished dashboard — that's a lie in YAML form |
| Wiring real queries before the replica looks right | Phase 1 first: the recorded values are what phase 2 verifies against |
| Chasing stale numbers | Check the image's axis dates first — phase offset is expected |
| Silently dropping hard tiles | Blocked tiles go in the delivery note, not down a memory hole |
| One-shotting the whole board | Build per tile via `get_skill('chart')`, validating each step |

## Rationalizations to Resist

| Excuse | Reality |
|--------|---------|
| "Phase 1 is a natural place to hand back" | Only if you announced the checkpoint and asked a question. An unannounced stop reads as finished, and nothing continues while you wait. |
| "Wiring 40 queries is a lot — I'll let them come back to me" | Volume is the reason to check in *before* phase 2, not a reason to end the job. Ask, then wire them. |
| "It'd be cleaner as an 8-chart summary" | The user asked for *that* dashboard. Condensing without asking is a silent failure, not good taste. |
| "I'll skip the fake-data pass and go straight to real queries" | Then you have no recorded expectations to verify tables against, and no reviewable replica while queries are unsettled. |
| "The banner is ugly, I'll leave it off" | The banner is the contract that phase 1 is a mock. Ugly and honest beats polished and misleading. |
| "Close enough — most tiles are there" | "Most" is not a tally. Account for every source tile: wired, blocked, or flagged. |
| "My numbers don't match the image, the query must be wrong" | Check the image's date range first — a stale snapshot off by one period is expected. Magnitude mismatch is what signals a wrong query. |
| "Matching their brand colors would be more faithful" | Fidelity is structure, options, and data. Styling is deliberately dbt charts' own. |
