---
name: board-design
kind: workflow
description: >
  Design principles for at-a-glance dbt charts boards: chart selection, information
  hierarchy, layout, color, dashboard vs report. Use for 'what chart type', 'how
  should I lay this out', 'dashboard design'. Do NOT use for narrative analyses
  ({{ s_skill_name_design_report }}) or the build-test-iterate cycle ({{ s_skill_name_build }}).
metadata:
  author: fivetran
---

# Dashboard Design

Design dashboards for **at-a-glance monitoring** — clear, scannable, action-oriented displays.

> Implementing in YAML? Switch to `{{ s_skill_build }}` for the build-test-iterate workflow.
> Reproducing an existing dashboard from a screenshot or export? That's
> `{{ s_skill_replicate_board }}` — there, fidelity to the source beats the
> defaults below.

**These principles are defaults, not policy.** Generally they produce the best
dashboard — but when the user explicitly asks for something different ("add
more charts", "all 12 on one page"), their ask wins. State the trade-off once,
then build what they asked for.

## Dashboard vs. Report

| | Dashboard | Report |
|--|-----------|--------|
| **Purpose** | Monitor status at a glance | Answer a question, tell a story |
| **Frequency** | Daily / continuous | One-time or periodic |
| **Text** | Minimal — titles and labels | Extensive — narrative + recommendations |
| **Charts** | Primary content | Supporting evidence for the narrative |
| **Interaction** | Scan in seconds | Read in minutes |

If the user needs a narrative analysis, use the `{{ s_skill_name_design_report }}` skill instead.

## Core Principles

1. **Single screen** — generally no scrolling. If it doesn't fit, propose splitting into multiple dashboards.
2. **Scannable in seconds** — a glance should convey status.
3. **Every element earns its place** — remove anything that doesn't aid understanding.
4. **Context for every number** — a number without comparison (trend, target, prior period) is meaningless.
5. **Purpose-driven** — every chart answers a specific question.

## Metadata Requirement

Whenever you output or edit dbt charts YAML, add `notes` metadata (never rendered — AI
search and maintainer context only):

- `queries.*.notes` for query intent
- `charts.*.notes` for chart intent
- `variables.*.notes` for filter semantics
- Layout object `notes` fields (`rows`/`cols`/`grid.items`/`tabs.items`) when they add context

Notes should be concise and optimized for AI retrieval.

## Thinking Process

Before designing, work through these in order:

### 1. Audience & Purpose

- **Who** views this? (executive, analyst, operator)
- **When** do they view it? (morning standup, weekly review, always-on monitor)
- **What decision** does this inform?

### 2. Information Hierarchy

- What is the **#1 most important metric**? → top-left, largest
- What are the **3-5 supporting KPIs**? → KPI row
- What **trends** matter? → line/area charts
- What **comparisons** are useful? → bar charts
- What **detail** supports drill-down? → tables at the bottom

### 3. Chart Selection

| Question | Chart Type | Notes |
|----------|-----------|-------|
| Current value? | `kpi` | Big number, prominent position |
| Trending over time? | `line` | Continuous data, time on x-axis |
| Category comparison? | `bar` | Categorical x-axis, include zero baseline |
| Composition over time? | `area` (stacked) | Parts of a whole changing |
| Portion of whole? | `pie` | **Only for 2-3 segments** — use bar for more |
| Relationship? | `scatter` | Two numeric variables |
| Precise values? | `table` | When exact numbers matter |

**Avoid:**
- Pie for >3 categories (humans compare angles poorly — use bar)
- Multiple chart types for visual variety (consistency aids scanning)
- Scrolling layouts (if it scrolls, it's usually a report — unless the user asked for more on one page or you're reproducing a scrolling source)

### 4. Layout — Inverted Pyramid

Most important information first, following Western reading pattern (top-left → bottom-right):

```
┌──────────────────────────────────────────────┐
│  KPI 1  │  KPI 2  │  KPI 3  │  KPI 4       │  ← HEADLINE
├──────────────────────────────────────────────┤
│  Primary trend chart (line/area)             │  ← STORY
├──────────────────────┬───────────────────────┤
│  Comparison chart    │  Comparison chart     │  ← CONTEXT
├──────────────────────┴───────────────────────┤
│  Detail table (if needed)                    │  ← EVIDENCE
└──────────────────────────────────────────────┘
```

- **4-8 total visualizations is the sweet spot** (more only when the user asks for more)
- Related metrics grouped by proximity
- Consistent styling throughout

**Section titles — use sparingly.** A `title:` on a `rows:`/`cols:`/`grid` item renders a heading above that group. On a dense, chart-only dashboard it is almost always noise: the KPIs and chart titles inside already say what each group is, so a row header like "Core Metrics", "Trendlines", or "Overview" just repeats them. Group with proximity and whitespace, not labels. Reach for a section title only when the dashboard is becoming more of a narrative — when there's accompanying `text:` prose that introduces the section, or when the heading genuinely says something the charts don't (a scoping qualifier the charts share like "Last 30 days" or "North America", a real mode boundary the layout alone wouldn't signal). A title with no text alongside it is the tell: usually it means neither the title nor the sectioning was needed — delete it and let the charts speak. This is guidance for designing **dashboards** — in a **report**, sections are good: there the narrative is the point, and `## …` headings in `text:` blocks carry the prose that introduces each section.

### 5. Color & Visual Design

- **Gray is default** — muted everything, ONE accent color for emphasis
- **Color carries meaning** — same color = same meaning everywhere
- **Direct labels** on charts when possible, not separate legends. This is
  `style.endpoint_labels`, on by default for multi-series line, area, and
  *stacked* bar (a grouped bar keeps a legend); where it applies it hides the
  color legend, so `visible: false` there, not `legend:`, is what puts those
  series names back in a legend
- **Series labels stay tight** — short, unique, clear. A word every series
  shares describes the chart, not a series: say it once in the title or
  `y_label` — `Created` / `Completed` under "Document Volume", not
  `Documents Created` / `Documents Completed`. Keep a repeat only when a label
  would be unclear without it. These labels come from the query — the column
  alias for `y: [a, b]`, the column's own values for `color:`
- **Maximize data-ink ratio** — every pixel should represent data

**Never write a raw hex.** `color: "#4C78A8"` is the Vega default, not ours — it
pins a color that stops answering to the theme. Author palette tokens
(`category[1]`, `negative.solid`, `dbt-grays.muted`) — or a palette name where
one is asked for (`palette: dbt-seq-blue`).{{#if_tool docs}} `{{ s_docs_color }}` has the full table.{{/if_tool}} Best of all, author no color and let the theme pick.

**"Same color = same meaning everywhere" is enforced for you, board-wide.**
Once two or more charts color by the same field, dbt charts assigns each
value one palette slot and every chart honors it — no per-chart matching
needed. Pin a specific value to a specific slot under
`style.charts.category_colors.<field>.values`.{{#if_tool docs}} `{{ s_docs_charts }}` has the
full syntax.{{/if_tool}}

### 6. Numeric Display

Apply standard numeric-display judgment (two-numeral rule, no false precision, tabular numerals in columns). dbt charts-specific defaults:

- **Put format in the family slot — not chart root.** Cartesian charts: the measure → `style.number_format` (equivalently `style.axis_y.labels.format` — the measure, even on a horizontal bar where it draws along the bottom). The dimension (`style.axis_x.labels.format`) takes a number preset only when its ticks are themselves numbers; a text-category axis takes no number format, and a date axis takes a time token instead. A band-scale dimension raises `ERR-LABEL-FORMAT-AXIS-MISMATCH`; so does a temporal one now — dates get no numeric-tick exemption, so use a time token or `style.time_format` there instead. `heatmap` has no measure axis at all — its value is on the color channel, so a preset on either axis raises. `style.axis_y.mirror.format` follows the same rule whenever the mirrored edge is categorical — a dot plot's y, and also a default-orientation (horizontal) bar, where the rotation puts the category on that edge. KPI value → `style.value.format`. Table column → `style.columns.<col>.format`. Chart-root `format:` is rejected on cartesian families.
- **Use format aliases, not raw d3 specs.** `currency`, `currency_whole`, `percent`, `percent_delta`, `integer`, `number` inside the slot above — not bare `format: currency_whole` at chart root. Raw d3 (`"$,.0f"`) only when no alias fits.
- **Drop cents above $10.** `style.number_format: currency_whole` (or `style.value.format` on KPIs) is the dashboard default. Reserve `currency_full` (with cents) for reconciliation surfaces — billing, financial statements.
- **Notation family: analytic for chrome, narrative for prose.** Analytic (`$2.5 M`, space, uppercase K/M/B) for axes, KPIs, tables, tooltips. Narrative (`$2.5mn`, no space, lowercase) only for text cards, titles, annotations. Independent of theme choice.
- **Zero strips trailing decimals even when siblings have them.** A `$0` KPI uses `currency_whole` even if its partner uses `currency`. A `0%` KPI uses `percent_whole` even if its partner uses `.2%`. The rule generalizes to any unit.
- **Percent precision is a group decision.** Default by magnitude: ≥20% → `.0%`; 1–20% → `.1%`; <1% → `.2%`. **Modulate by surface:** a single KPI or short rail can afford one more decimal; a long table column or axis wants the simpler form. **Override:** when tenths carry signal regardless of magnitude (A/B rates, churn, conversion in a tight range), use `format: ".2%"`.
- **Compaction is a group decision, not per-value.** Compact when ≥4 similar-magnitude values exceed 10,000, or when surface density demands it. Adjacent surfaces showing the same metric may compact differently — a reconciliation table can show full precision while the headline KPI uses `currency`.
- **NULL renders as `—` (em-dash), never `0`.** Different claims about the data.
- **Tables anchor the currency symbol.** Default `symbol_mode: anchors` — first row carries the `$`, rows below don't.

### 7. Variables & Interactivity

Add variables when the dashboard serves different time windows or segments:

- Date ranges → `daterange` input
- Categorical filters → `select` input
- Set sensible defaults (last 30 days, most common category)
- **NOT for executive dashboards** — those show the single most important view

## Dashboard Patterns

### Executive Dashboard
4-6 KPIs → main trend → 2-3 breakdowns. No interaction. Show the single most important view.

### Operational Dashboard
Status KPIs with alerts → recent activity → issues table. Emphasize what needs attention now.

### Analytical Dashboard
KPIs → trends with filters → comparisons across dimensions → detail table. Include `variables:` for interactive filtering.

## Quality Checklist

Before delivering:

- [ ] Fits on one screen (≤8 visualizations, no scrolling) — or the user explicitly asked for more
- [ ] KPIs are first and most prominent (top row)
- [ ] Every number has context (comparison, trend, or target)
- [ ] Chart types match the analytical question
- [ ] Related metrics grouped by proximity
- [ ] Color is purposeful, not decorative — and authored as palette tokens, with no raw hex anywhere
- [ ] Titles are informative ("Revenue by Region, Last 30 Days" not "Chart 1")
- [ ] User's most important question answered at a glance
- [ ] Query/chart/layout/variable `notes` metadata is filled for AI context
- [ ] No word repeats across every series label — it lives once in the title

## Common Mistakes

| Mistake | Fix |
|---------|-----|
| Too many charts (>8) the user didn't ask for | Split into multiple focused dashboards |
| KPIs without context | Add comparison period, trend, or target |
| Pie chart with 7 segments | Use a bar chart instead |
| Decorative color | Color must encode data or meaning |
| A hex literal in a color slot | Use a palette token — `category[1]`, `negative.solid`, `dbt-grays.muted` — so the theme still owns the color |
| Generic titles | Titles should state what the chart answers |
| Section title over every row | Drop it — labeled charts don't need a heading repeating them. Reserve titles for reports or a real scoping/mode boundary |
| Every series label repeats a word (`Documents Created` / `Documents Completed`) | Say it once in the title or `y_label`; alias the query column for `y: [a, b]`, or shorten the `color:` column's values |
| Scrolling dashboard | Reduce charts or split dashboards |

## Rationalizations to Resist

| Excuse | Reality |
|--------|---------|
| "12 metrics is too many, I'll trim their list" | An explicit user ask wins. Suggest a split once, then build all 12. The 8-max default applies when the design is yours to choose. |
| "Pie chart is fine for 6 categories" | It's not — humans compare angles poorly. Use bar. |
| "The legend explains the colors" | Direct labels are always better than legends. |
| "I'll just pick a hex that looks right" | It looks right against nothing. The palette is already CVD-checked and contrast-checked as a set — a hand-picked hex is one color that no longer belongs to it, and it survives a theme switch that everything around it answers. |
| "More charts = more value" | More charts = more noise. Each must earn its place. |
| "Section titles organize the dashboard" | They organize a *report*. On a dense dashboard the charts are already labeled — a heading per row is repetition, not structure. |
