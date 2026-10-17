---
name: board-visual-review
description: Render a dbt charts board to PNG and review the image against a visual-design
  checklist using vision. Catches problems YAML inspection can't see — overlapping
  text, contrast failures, axis-label collisions, whitespace imbalance, KPI precision
  mismatches. Use when asked to 'visually review', 'check how it looks', 'is this
  rendered correctly', 'review the rendered output', or when structural review surfaces
  ambiguous 'feels off but I can't say why' findings. More expensive than structural
  review (render + vision cost). Do NOT use for YAML schema or data-shape problems
  (use board-structural-review). Do NOT use for comparing two versions of the same
  board.
metadata:
  author: fivetran
---
# Dashboard Visual Review

Render the board to PNG, then evaluate the image against a visual-design
checklist. The agent host (Claude, GPT-4o, Gemini) provides the vision
capability — no external API call.

## When to use

- Final pre-delivery polish pass
- When structural review surfaces "feels wrong but I can't say why" findings
- When the user reports a layout / readability complaint
- Second pass inside the `board-review` orchestrator after structural

## When NOT to use

- YAML schema or data-shape problems — use `board-structural-review`
- Comparing two versions of a board — out of scope for this skill
- Pixel-exact regression testing — vision is the wrong tool; brittle

## Protocol

1. **Render the board to PNG.** Call `render_board`:

   ```text
render_board(path="charts/finance/revenue-overview.yml", format="png")
```


   If render fails, report the error and stop — there's nothing visual to
   review.

2. **Read the PNG** with the agent's image-loading capability.

3. **Evaluate against the checklist below.** Emit findings in the same format
   as `board-structural-review` so the orchestrator can merge them.

## Checklist

### Visual hierarchy

- [ ] Eye is drawn to the most important element first (largest KPI, headline
      chart, top-left position)
- [ ] Reading flow is top-down / left-to-right — no important data in
      bottom-right that should be top-left
- [ ] Whitespace separates groups without leaving large dead zones

### Text legibility

- [ ] No text overflow (clipped numbers, ellipsized labels that hide the value)
- [ ] No wrap thrash (axis labels wrapping awkwardly mid-word)
- [ ] Axis tick labels readable at rendered size (rotate if cramped)
- [ ] Legend labels match the chart's encoding (no orphan legend entries)

### Color and contrast

- [ ] Foreground text meets WCAG AA contrast against its background
- [ ] Color palette is consistent across charts (same series = same color)
- [ ] No clashing or vibrating color pairs (red on green, complementary hues
      at full saturation)
- [ ] Conditional formatting communicates severity, not decoration

### KPIs and numbers

- [ ] KPI values render with appropriate precision (revenue in `$1.2M`, not
      `1247392.7438`)
- [ ] Units are consistent (don't mix `1,247` and `1.2K` in the same row)
- [ ] Prior-period deltas have a sign and a directional cue (▲ / ▼ / color)
- [ ] Currency / percent suffixes are present where expected

### Composition and balance

- [ ] No crammed corners — every chart has padding within its cell
- [ ] Row heights match the visual weight of their content (KPIs short,
      charts tall)
- [ ] Tables don't overflow the page width (horizontal scroll only if
      unavoidable)
- [ ] Empty / null states render gracefully (no raw `None` or empty rectangles)

## Output format

Same shape as `board-structural-review` so findings merge cleanly:

```markdown
**Findings**

- `blocker` `charts.revenue_kpi`: KPI shows `1247392.74` — should be `$1.2M`.
  Add `style.value.format: currency` to the chart.
- `warning` `charts.regions_bar`: x-axis labels overlap at rendered width.
  Rotate to -45° or shorten labels.
- `warning` `chart sequence`: revenue trend (most important) sits bottom-right
  while a smaller breakdown sits top-left. Swap positions.
- `nit` `charts.users_pie`: colors are vibrant and clash. Switch to a muted
  qualitative palette.
```

If there are no findings: emit exactly `**No findings.**`.

## Severity rubric

Use the same tags as `board-structural-review`:

| Tag | Meaning |
|---|---|
| `blocker` | User cannot read or trust the rendered output |
| `warning` | Renders, but violates a clear visual principle |
| `nit` | Stylistic — author can take or leave |

## Common mistakes

| Mistake | Fix |
|---|---|
| Reviewing without rendering | The PNG is the artifact under review — render first, no shortcuts |
| Critiquing the YAML instead of the image | That's structural review's job — stay on visual signals only |
| Inventing visual problems that aren't in the rendered output | Anchor every finding to what's visible in the PNG |
| Re-flagging issues already raised by structural review | Visual review covers what YAML inspection can't see; trust the orchestrator to dedupe |

## Rationalizations to resist

| Excuse | Reality |
|---|---|
| "I can guess what it looks like from the YAML" | You can't — that's why visual review exists. Render or skip the pass. |
| "Contrast looks fine to me" | If you're unsure, flag it as a `warning`. Accessibility is not subjective when it fails WCAG. |
| "The vision model is expensive, I'll skim" | Don't run the skill at all if you're going to skim — the orchestrator can route to structural-only. |
