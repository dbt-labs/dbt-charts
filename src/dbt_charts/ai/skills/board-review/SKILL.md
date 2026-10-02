---
name: board-review
kind: workflow
description: >
  Entry point for reviewing a board: runs the structural pass, adds the visual
  pass when needed, and merges both into one ranked findings list. Use for
  'review this dashboard', 'is this dashboard good', or before delivering a board
  you just built. Do NOT use when one pass is enough — invoke
  {{ s_skill_structural_review }} or {{ s_skill_visual_review }} directly.
metadata:
  author: fivetran
---

# Dashboard Review

Orchestrate `{{ s_skill_name_structural_review }}` and `{{ s_skill_name_visual_review }}` into a
single ranked findings list. This is the default entry point — most "review"
requests should land here, not on either leaf skill.

## When to use

- Any open-ended "review this dashboard" request
- Pre-delivery final pass
- After a substantive edit (new chart, new query, layout change)

## When NOT to use

- The user explicitly asked for only structural OR only visual — invoke that
  leaf skill directly
- Comparing two versions of a board — use the `looker-compare-diff` pattern

## Protocol

### Step 1: Always run structural review

Invoke `{{ s_skill_structural_review }}` first. It's cheap (no rendering, no
vision tokens) and catches every problem that's visible in the YAML.

### Step 2: Decide whether to run visual review

Run `{{ s_skill_visual_review }}` when **any** of the following is true:

- The user asked for visual review explicitly ("how does it look", "review the
  rendered output", "visual review", "check the design")
- Structural review surfaced a finding that hints at a visual problem (KPI
  precision, generic title that may collide with axis labels, dense chart
  count that may crowd the canvas)
- Structural review returned `**No findings.**` and the user asked for a
  thorough review — do the visual pass to confirm

Skip visual review when:

- Structural review surfaced `blocker` findings — fix those first; rendering
  may be misleading until the YAML is correct
- The user asked for a quick / cheap review{{#if_tool validate_board}}
- `{{ s_validate_board }}` failed inside
  structural review — there's nothing valid to render yet{{/if_tool}}

### Step 3: Synthesize

Merge findings from both passes into a single list:

1. **Deduplicate.** If both passes surface the same issue (e.g. structural
   notes the KPI has no format spec and visual notes the rendered number is
   `1247392.74`), keep the more concrete finding.
2. **Order by severity.** All `blocker` findings before `warning` before
   `nit`. Within a severity, keep the order each pass produced.
3. **Cap at 5** by default. If you have to drop findings, drop `nit` first,
   then `warning`. Never drop a `blocker`.
4. **Tag the source** so the user knows which pass surfaced what.

## Output format

```markdown
**Dashboard review**

- `blocker` [structural] `charts.revenue_kpi`: query returns 12 rows but
  `type: kpi` requires 1. Aggregate with `SUM()`.
- `warning` [visual] `charts.regions_bar`: x-axis labels overlap at rendered
  width. Rotate to -45° or shorten.
- `warning` [structural] `queries.orders`: no `notes:` field. Add one
  sentence stating intent.
- `nit` [visual] `charts.users_pie`: muted palette would reduce visual noise.

**Passes run:** structural, visual
**Skipped:** —
```

If neither pass found anything: emit `**Dashboard review: no findings.**`.

If a pass was skipped, name it in `**Skipped:**` with a one-phrase reason
(`Skipped: visual — blockers in structural pass`).

## Common mistakes

| Mistake | Fix |
|---|---|
| Running visual review before structural | Always structural first. Visual depends on a render that won't be meaningful until the YAML is valid. |
| Skipping structural because "it's just visual" | The YAML is the source of truth — never skip structural. |
| Padding the synthesized list to fill a cap | If both passes agreed there are only 2 findings, return 2. |
| Forgetting to tag pass source | The user needs `[structural]` / `[visual]` to know where to look. |

## Rationalizations to resist

| Excuse | Reality |
|---|---|
| "User said 'review' — they probably only want a quick check" | Default to thorough. The user can ask for cheap explicitly. |
| "Visual review is slow, let's skip it" | If a render is fast on this board (most are), do both passes by default. Cost is a real concern only at scale. |
| "Both passes are saying the same thing, I'll keep both" | Dedupe. Showing the same issue twice erodes trust. |
