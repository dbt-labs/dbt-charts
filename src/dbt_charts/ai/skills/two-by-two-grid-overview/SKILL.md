---
name: two-by-two-grid-overview
kind: pattern
description: >
  Pattern for a 2×2 (or 2×3) grid of equally-weighted summary charts giving a
  balanced multi-metric overview. Use when four to six metrics are equally
  important and no single chart dominates. Triggers on: '2x2', 'grid overview',
  'equal weight', 'four charts', 'overview dashboard', 'balanced layout',
  'summary grid'. Each cell carries the same visual weight — no chart is
  emphasized over another. Do NOT use when one chart is the hero (use
  single-metric-bignum or put it in a wider column). Do NOT use when the four
  metrics belong in a KPI row (use kpi-row for aggregated numbers).
metadata:
  author: fivetran
---

# Two-by-Two Grid Overview

Four charts arranged in a 2×2 grid, each with equal column width, giving users
a balanced multi-metric overview before diving into detail. Implemented as two
`rows:` of `cols:` with two items each — dbt charts splits the width evenly by
default.

## When to reach for this

- A board opens with a balanced overview ("revenue / cost / margin / users")
- All four charts have equal analytical importance
- Each chart answers a different question about the same domain

## When NOT to use this

- One chart is clearly the hero → enlarge it via `width:` or move it to its own row
- All four are KPI numbers → use `kpi-row` instead (it handles delta and format)
- More than 6 equally-weighted charts → split into tabs or multiple boards

## The pattern

```yaml
rows:
  - cols:
      - revenue_trend    # top-left
      - cost_trend       # top-right
  - cols:
      - by_region        # bottom-left
      - by_product       # bottom-right
```

No section title — the four labeled charts are self-explanatory, and a heading over them just repeats their titles.

Each chart is defined as usual in `charts:`. The 2×2 is purely a layout
decision — swap in any chart type per cell.

See `examples/two-by-two-grid-overview.yml` for the inline-data worked example.

## Variations

| Variation | YAML knob | When |
|---|---|---|
| 2×3 (six cells) | Add a third `cols:` row | Six equally-weighted metrics |
| Asymmetric | `width: "60%"` on one cell | Slightly more emphasis on one chart |
| Section title | `title:` on a `rows:` item | Rarely needed — only when a heading adds something the charts don't (a shared scope, a mode boundary). Default to omitting it |
| Grid layout | `grid: columns: 24` + `col_span: 12` | Finer column control |

## Common pitfalls

| Pitfall | Why it breaks | Fix |
|---|---|---|
| Charts with very different heights | Mismatched row heights look accidental | Use charts of the same type per row |
| All four showing the same metric | No additional insight | Ensure each cell answers a distinct question |
| Nesting cols in cols | Unexpected layout behavior | Use rows at the top level, cols inside rows |

## Worked example

See `examples/two-by-two-grid-overview.yml` — four charts (two line + two bar)
arranged 2×2 from two inline-data queries. No warehouse required.

{{#if_tool docs}}## YAML Reference

For syntax and field details: {{ s_yaml_reference_footer }}
{{/if_tool}}
