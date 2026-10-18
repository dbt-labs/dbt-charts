---
name: analyst-runbook
description: 'The analyst process wrapped around anything you build: triage the request,
  reuse existing work, pick the response shape, verify the answer, then deliver it
  with a written read — observations plus where to look next. Use at the start of
  an analytical request ("show me…", "why did X change") and again when handing the
  work back. Not for chart-type or layout decisions (use board-design).'
metadata:
  author: fivetran
---
# Analyst Runbook

The process a senior analyst follows. Run the **intake triage** first, every time —
it decides what you build and prevents redundant or wrong-shaped answers. Then hand
off to the build skills, **verify the answer**, and deliver it with a written read.

## 1. Intake triage (first, every request)

**Reuse first.** Call `search_files` for boards that already answer
this or come close. Adapt an existing dashboard instead of building from scratch
whenever one fits — and say which you reused.

1. **Pick the response shape.** Match the deliverable to the ask; don't default to a
   dashboard:
   - **a number / short answer** — one metric or a one-line factual answer
   - **one chart** — a single trend, ranking, or breakdown
   - **a dashboard** — several related charts that explore a topic together
   - **a report** — narrative plus charts that walk through an analysis
   - **a replication** — the user supplied an existing dashboard (screenshot,
     image, export) to reproduce → `get_skill('board-replicate')`;
     fidelity to the source, not a redesign
2. **Clarify only what blocks you.** If the metric, grain, time window, or filters
   are ambiguous *and* the choice changes the answer, ask one focused question.
   Otherwise state the assumption and proceed.
3. **Open with the plan.** One or two sentences naming the deliverable and the
   data you are about to go look at ("I'll pull ticket volume and resolution
   time by priority and put them side by side"), written before you start the
   work. Hosts collapse tool activity behind a single row, so without this the
   reader watches a spinner with nothing to read. Write it once, then work —
   this is not a play-by-play of each tool call, and it does not replace the
   read in step 4.

## 2. Do the analysis

Explore the data, then build incrementally — hand off to the skill that owns each step:

- **`get_skill('board-build')`** — build/edit boards (explore schema → write → validate → render)
- **`get_skill('board-design')`** — chart-type, layout, and color decisions
- **`get_skill('report-design')`** — narrative reports
- Use `execute_sql` against the warehouse metadata views
  (INFORMATION_SCHEMA and friends) to find the real vocabulary, and to confirm
  values before you claim them.

## 3. Verify the answer (not only the artifact)

`get_skill('board-review')` checks the *artifact* — does it render, is it well formed.
Before delivering, also verify the *answer*:

- **Right source.** Numbers come from the table that actually owns this metric, not a
  lookalike column. Confirm with `execute_sql`.
- **Right question.** The result answers what was asked — right grain, right filters,
  right time window — with no extra or missing slices.
- **Sanity.** Totals, row counts, and units are plausible; nulls, duplicates, or
  fan-out haven't silently skewed the number.
- **Stated assumptions.** Any choice you made (window, definition, exclusions) is
  surfaced to the user, not hidden.

If a check fails, fix it before delivering — never ship a number you can't stand behind.

## 4. Deliver with a read

Charts are evidence, not the answer. Whenever you hand back a chart, dashboard, or
report, write a short read next to it — the two paragraphs a colleague would say when
they turn the laptop around:

- **What it shows.** Two to four sentences on what is actually in the rendered numbers:
  the level, the direction, the outlier, the thing that surprised you. Every figure you
  quote must be one you read out of a tool result. Don't restate chart titles, don't
  narrate the build ("I created three charts…"), and don't pad with what the user can
  see at a glance.
- **Where next.** Two to four bullets: the follow-up cuts worth taking, a caveat in the
  data you noticed while building, or a question only the user can answer (which
  definition of active, which segment matters). Suggest them outright — this is not
  asking permission, and it never replaces doing the work you were already asked to do.

Skip the read only when there is nothing to read: a bare one-number answer, or a pure
formatting or layout edit. Keep it well shorter than the dashboard.

## Analysis-method patterns

Methods, not layouts (layout lives in board-design). Reach for the right shape:

- **Retention / cohort.** Group entities by their first-activity period (the cohort),
  then measure the active fraction at each later period. Anchor on the cohort, not the
  calendar; report as a retention curve or triangle.
- **Funnel / conversion.** Order the steps, count distinct entities reaching each, and
  report step-to-step conversion. Define the entity and the window once — don't mix
  per-step windows — and watch for entities that skip or re-enter steps.
- **Rate decomposition.** When a ratio (e.g. revenue per user) moves, split the change
  into numerator vs denominator (and mix vs within-segment) so you can say *why* it
  moved, not just that it did.
