---
name: single-metric-bignum
kind: pattern
description: >
  Pattern for a single hero KPI displayed at large scale with a prior-period
  delta in the support row. Use when one metric is the primary signal of the
  board and should visually dominate. Triggers on: 'hero metric', 'big number',
  'single KPI', 'headline metric', 'north star', 'key metric', 'monthly
  recurring revenue', 'MRR'. Optionally add a sparkline for trend context.
  Do NOT use for three or more metrics (use kpi-row). Do NOT use when the
  chart type is more important than the number (use time-series-trend).
metadata:
  author: fivetran
---

# Single-Metric Bignum

One KPI rendered at the largest readable size, backed by a one-row query,
with a support row showing the prior-period delta. The visual emphasis signals
"this is what matters most on this board."

## When to reach for this

- A board exists to answer one question: "what is our MRR today?"
- The metric is a North Star that stakeholders check every day
- You want delta context (up X% vs last period) without a full chart

## When NOT to use this

- Three or more equally-important metrics → `kpi-row`
- Trend history is the point, not the current value → `time-series-trend`
- Sparkline is critical (needs array data) → record this in the chart's `notes:`

## The pattern

```yaml
queries:
  hero:
    # Compute current and prior totals upstream (e.g. in your dbt model) and
    # select two columns. Inline window math against an aggregate row tends
    # to break across warehouses — keep this query trivial.
    sql: |
      SELECT mrr, prior_mrr, (mrr - prior_mrr) / prior_mrr AS delta
      FROM mrr_summary

charts:
  mrr_kpi:
    type: kpi
    query: hero
    label: Monthly Recurring Revenue
    value: mrr
    style:
      value:
        format: "$,.2s"       # e.g. $1.84M
    support:
      value: delta
      label: vs last month
      format: "+.1%"
      glyph: "▲"
      tone: positive          # positive / negative / warning
```

See `examples/single-metric-bignum.yml` for the worked example.

## Variations

| Variation | YAML knob | When |
|---|---|---|
| Large currency | `format: "$,.2s"` | Millions/billions (e.g., `$1.84M`) |
| Plain count | `format: ",.0f"` | Raw integer |
| Percent | `format: ".1%"` | Conversion rate, margin |
| Negative delta | `tone: negative`, `glyph: "▼"` | Unfavorable direction |
| Status string | `query: { rows: [{ status: "On track" }] }` + `value: status` | Qualitative health status |
| Sparkline | add a `spark` column in the query (array type) | Requires a warehouse that supports arrays |

## Common pitfalls

| Pitfall | Why it breaks | Fix |
|---|---|---|
| Query returns multiple rows | ChartDataError | Use `SUM()`/`MAX()` to collapse to exactly 1 row |
| `format: "$,.2s"` on a small number | Rounds aggressively (e.g. `$175` → `$0.00k`) | Use `"$,.0f"` for values under $10k |
| `tone:` without `glyph:` | Delta has semantic color but no directional icon | Add `glyph: "▲"` or `"▼"` |
| Overlong label | Label wraps awkwardly at large sizes | ≤ 4 words |

## Worked example

See `examples/single-metric-bignum.yml` — a hero MRR KPI with a +14.2%
support row. Inline data, no warehouse required.

{{#if_tool docs}}## YAML Reference

For syntax and field details: {{ s_yaml_reference_footer }}
{{/if_tool}}
