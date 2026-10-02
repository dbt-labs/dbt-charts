---
name: kpi-row
kind: pattern
description: >
  Pattern for a row of 3–5 KPI cards at the top of a board. Use when showing
  summary metrics (revenue, orders, conversion rate, avg order value) from a
  single aggregated query. Triggers on: 'KPI row', 'summary metrics', 'top
  metrics', 'hero numbers', 'scorecard'. Each KPI needs a separate single-row
  query OR one query with multiple columns. Do NOT use for time-series trends
  (use time-series-trend). Do NOT use for a single hero metric (use
  single-metric-bignum). Do NOT use if the data has more than 1 row per KPI.
metadata:
  author: fivetran
---

# KPI Row

A horizontal row of 3–5 KPI cards placed at the top of a board. Each KPI
displays one aggregate value (a number, currency, or percent) with a label and
an optional prior-period delta in the support row.

## When to reach for this

- The board opens with a "how are we doing overall?" summary before detail charts
- You have 3–5 numeric aggregates drawn from the same time window
- Some metrics have a prior-period comparison to show trend direction

## When NOT to use this

- Single hero metric with a sparkline → use `single-metric-bignum`
- Data has multiple rows per KPI → aggregate to one row first
- More than 6 KPIs → split into two rows or a `two-by-two-grid-overview`

## The pattern

```yaml
queries:
  kpis:
    columns: [revenue, orders, avg_order, rev_delta, ord_delta]
    values: [[248500, 1420, 175, 0.12, 0.08]]

charts:
  revenue_kpi:
    type: kpi
    query: kpis
    label: Revenue
    value: revenue
    style:
      value:
        format: currency_whole
    support:
      value: rev_delta
      label: vs last period
      format: percent_delta
      glyph: "▲"
      tone: positive

  orders_kpi:
    type: kpi
    query: kpis
    label: Orders
    value: orders
    support:
      value: ord_delta
      label: vs last period
      format: percent_delta
      glyph: "▲"
      tone: positive

rows:
  - height: 120
    cols: [revenue_kpi, orders_kpi]
```

See `examples/kpi-row.yml` for the full 3-KPI worked example.

## Variations

| Variation | YAML knob | When |
|---|---|---|
| No delta | omit `support:` | Prior period not available |
| Currency | `style.value.format: currency_whole` | Revenue, spend, cost |
| Percent | `style.value.format: percent` | Rate, conversion, margin |
| Negative delta | `tone: negative`, `glyph: "▼"` | Unfavorable direction |
| Cap row height | `height: 120` on the row | Prevent KPIs growing taller than charts below. It is a ceiling, not a reservation — if the KPIs need less, the row shrinks to fit rather than leaving an empty band. |

## Common pitfalls

| Pitfall | Why it breaks | Fix |
|---|---|---|
| Query returns multiple rows | `ChartDataError` — KPI expects exactly 1 row | Add `SUM()`/`COUNT()` to aggregate to one row |
| Delta column missing from query | Render error — column not found | Include delta columns in the same query |
| `glyph: "▲"` without `tone:` | Arrow renders but no semantic color | Add `tone: positive` or `tone: negative` |
| Overloaded label | Hard to scan at a glance | ≤ 3 words per label |

## Worked example

See `examples/kpi-row.yml` — three KPIs (Revenue, Orders, Avg Order Value)
from one inline-data query. Compiles and renders without a warehouse.

{{#if_tool docs}}## YAML Reference

For syntax and field details: {{ s_yaml_reference_footer }}
{{/if_tool}}
