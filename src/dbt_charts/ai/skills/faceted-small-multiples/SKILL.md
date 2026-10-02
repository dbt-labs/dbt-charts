---
name: faceted-small-multiples
kind: pattern
description: >
  Pattern for showing the same metric sliced by a categorical dimension —
  either as one multi-series chart (color encoding) or as N side-by-side
  charts (manual small multiples). Use when comparing a trend or distribution
  across segments. Triggers on: 'by segment', 'by region', 'by category',
  'small multiples', 'faceted', 'breakdown', 'compare across groups',
  'stacked area'. Use color encoding for ≤5 segments in one chart; use manual
  multiples when segments need separate y-scales or spatial separation. Do NOT
  use for aggregate totals (use kpi-row or two-by-two-grid-overview). Do NOT
  use for two-series comparison (use before-after-comparison).
metadata:
  author: fivetran
---

# Faceted Small Multiples

Two approaches for showing the same metric broken down by a category:

**Option A — Color encoding (one chart):** Add `color: segment_col` to any
chart. dbt charts renders one series per segment value, with a legend. Best for
≤5 segments, same y-scale.

**Option B — Manual multiples (side-by-side charts):** Author N separate
charts — one per segment — and arrange them in `cols:` or a `grid:`. Best
when segments need independent y-scales or when you want spatial separation.

## When to reach for this

- "Show me revenue by region across months" (time × segment)
- "Compare the distribution of orders across product categories" (category × count)
- Segments are the primary lens; aggregate total is secondary

## When NOT to use this

- Two series only → `before-after-comparison`
- Composition (parts add to 100%) → stacked area (`type: area` + `color:`)
- More than 8 segments → group the long tail into "Other" in SQL

## The pattern — Option A (color encoding)

```yaml
queries:
  revenue_by_segment:
    sql: |
      SELECT month, region, SUM(revenue) AS revenue
      FROM orders
      GROUP BY month, region
      ORDER BY month, region

charts:
  region_trend:
    type: line
    query: revenue_by_segment
    x: month
    y: revenue
    color: region            # one series per region value
    title: Revenue by Region
```

## The pattern — Option B (manual multiples)

```yaml
queries:
  east_data:
    sql: SELECT month, SUM(revenue) AS revenue FROM orders WHERE region='East' GROUP BY 1
  west_data:
    sql: SELECT month, SUM(revenue) AS revenue FROM orders WHERE region='West' GROUP BY 1

charts:
  east_chart:
    type: line
    query: east_data
    x: month
    y: revenue
    title: East
  west_chart:
    type: line
    query: west_data
    x: month
    y: revenue
    title: West

rows:
  - cols: [east_chart, west_chart]   # same visual encoding, side by side
```

See `examples/faceted-small-multiples.yml` for the worked example (Option A).

## Variations

| Variation | YAML knob | When |
|---|---|---|
| Stacked area | `type: area` + `color:` | Show composition — parts of a whole over time |
| Side-by-side bar | `type: bar` + `color:` | Grouped bars per category |
| Manual N-up | N charts in a `cols:` row | Independent scales per segment |
| Grid layout | `grid: columns: 24, col_span: 8` | 3+ multiples in a row |

## Common pitfalls

| Pitfall | Why it breaks | Fix |
|---|---|---|
| Too many color segments (>8) | Legend overflows, colors repeat | Limit to top-N in SQL + "Other" bucket |
| Stacked area with `color:` but no GROUP BY | Wrong aggregation, chart errors | Ensure one row per (x, color) combination |
| Manual multiples with different y-scales | Misleading comparisons | Use same y-axis domain, or make the difference visible in chart titles |

## Worked example

See `examples/faceted-small-multiples.yml` — monthly signups by channel using
color encoding (Option A). Inline data, no warehouse required.

{{#if_tool docs}}## YAML Reference

For syntax and field details: {{ s_yaml_reference_footer }}
{{/if_tool}}
