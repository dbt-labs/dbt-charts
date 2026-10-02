---
name: top-n-with-detail
kind: pattern
description: >
  Pattern pairing a top-N bar chart with a detail table on the same query.
  Use when the question is "which are the top performers?" and the user also
  needs row-level detail. Triggers on: 'top N', 'top products', 'best
  customers', 'ranked list', 'leaderboard', 'ranked + detail'. The bar chart
  gives the ranking at a glance; the table provides drill-in context. Do NOT
  use when there is no natural ranking (use two-by-two-grid-overview). Do NOT
  use when detail is not needed (a plain bar chart suffices).
metadata:
  author: fivetran
---

# Top-N with Detail

A bar chart ranking the top N categories by a metric, sitting beside a detail
table that exposes the same rows. One query powers both — add `ORDER BY metric
DESC LIMIT N` in the SQL so the data arrives pre-ranked.

## When to reach for this

- You have a "who/what is biggest?" question with a categorical dimension
- Row-level context matters (e.g., rank + order count + avg order value)
- The list is bounded and the user benefits from scanning the full ranked table

## When NOT to use this

- No natural ranking or comparison → plain `bar` chart
- Too many categories for a table (>50 rows) → add pagination or a filter
- Time-based ranking → pair with `time-series-trend` instead

## The pattern

```yaml
queries:
  top_products:
    sql: |
      SELECT product, SUM(revenue) AS revenue, COUNT(*) AS orders
      FROM orders
      GROUP BY product
      ORDER BY revenue DESC
      LIMIT 10

charts:
  ranking_bar:
    type: bar
    query: top_products
    x: product
    y: revenue
    title: Top Products by Revenue

  detail_table:
    type: table
    query: top_products
    title: Product Detail
    style:
      columns:
        product:
          label: Product
        revenue:
          label: Revenue
          format: "$,.0f"
        orders:
          label: Orders

rows:
  - cols: [ranking_bar, detail_table]
```

See `examples/top-n-with-detail.yml` for the inline-data worked example.

## Variations

| Variation | YAML knob | When |
|---|---|---|
| Color by category | `color: category_col` on the bar | Distinguish groups within the ranking |
| Horizontal bars | `type: bar` with long labels reads naturally as horizontal | Long category names |
| Click-through | add `link: "/detail?id={{ x }}"` to bar chart | Link to a per-item board |
| Tighter N | `LIMIT 5` | Space-constrained layouts |

## Common pitfalls

| Pitfall | Why it breaks | Fix |
|---|---|---|
| Forgetting `ORDER BY` | Arbitrary bar ordering | Always `ORDER BY metric DESC` |
| Two separate queries | Bar and table can diverge | Use one query for both |
| Too many bars (>15) | Bar chart unreadable | Set `LIMIT 10` or fewer |

## Worked example

See `examples/top-n-with-detail.yml` — top 5 products, bar + table, inline
data. No warehouse required.

{{#if_tool docs}}## YAML Reference

For syntax and field details: {{ s_yaml_reference_footer }}
{{/if_tool}}
