---
name: table-heavy-ops-dashboard
kind: pattern
description: >
  Pattern for operational dashboards dominated by sortable tables with
  per-column formatting, labels, and conditional formatting. Use when users
  need to monitor status, triage incidents, or review row-level data with
  currency/percent/date formatting and visual severity cues. Triggers on:
  'ops dashboard', 'incident table', 'ticket queue', 'status monitor',
  'formatted table', 'sortable list', 'operational view'. Do NOT use when a
  chart communicates the pattern better (use top-n-with-detail for rankings).
  Do NOT use for purely aggregated summaries (use kpi-row or
  two-by-two-grid-overview).
metadata:
  author: fivetran
---

# Table-Heavy Ops Dashboard

An operational view where one or more tables dominate. Each table uses
`style.columns` to configure labels, number formats, and per-column widths.
Conditional formatting adds severity colors without changing the data.

## When to reach for this

- Users need to scan, sort, and triage rows (incidents, tickets, pipelines)
- Columns have mixed types: currency, percent, status strings, dates
- Severity or thresholds matter (P1 red, P2 yellow, resolved gray)

## When NOT to use this

- Data is better shown as a chart → pair with `top-n-with-detail`
- Data has only 1–5 aggregated rows → use `kpi-row` instead
- Table has hundreds of columns → subset in SQL first

## The pattern

```yaml
charts:
  incidents:
    type: table
    query: open_incidents
    title: Open Incidents
    style:
      columns:
        ticket_id:
          label: Ticket
          visible: true   # styling-only: unlisted query columns still render
        priority:
          label: Priority
        status:
          label: Status
        revenue_impact:
          label: Revenue Impact
          format: currency_whole
          align: right
          width: 140
        created_at:
          label: Opened
          format: date_short
    conditional_formatting:
      priority:
        when:
          - eq: P1
            background: negative.bg
            font:
              color: negative.text
              weight: "600"
          - eq: P2
            background: warning.bg
            font:
              color: warning.text
          - default: true
            font:
              color: dbt-grays.ink
```

See `examples/table-heavy-ops-dashboard.yml` for the inline-data worked example.

## Variations

| Variation | YAML knob | When |
|---|---|---|
| Currency column | `style.columns.<col>.format: currency_whole` | Revenue, cost, spend |
| Percent column | `style.columns.<col>.format: percent` | Rate, margin, conversion |
| Date column | `style.columns.<col>.format: date_short` | ISO dates |
| Link cell | `link: "/detail?id={{ ticket_id }}"` | Click-through to detail |
| Header overflow | `header_overflow: clip \| truncate \| wrap` | Overriding the `wrap-two` default |
| Spark cell | `spark: line` or `spark.type: bar-normalize` | Trend arrays or % values |
| Continuous color | `scale.background.palette` | Numeric intensity |
| Header style | `style.table.header.*` | BI or minimal header tone |

## Common pitfalls

| Pitfall | Why it breaks | Fix |
|---|---|---|
| Too many columns | Hard to scan, horizontal scroll | Limit to 6–8 most important columns |
| Formatting strings not D3/Excel format | Wrong output | Use D3 format specs: `"$,.0f"`, `".1%"` |
| Conditional rule with no style override | `ConditionalRule` validation error | Set `background:`, `font.color`, `font.weight`, or `glyph:` on each rule |
| Sorting expectation mismatch | Default sort is query order | Add `ORDER BY priority` in SQL for triage queues |
| Reordering `style.columns` keys to move a column | Column order is styling-only there; a non-pivot table follows the query's `SELECT` order (zero rows excepted) | Reorder the `SELECT` list, not `style.columns` |

Conditional predicates are `eq`, `ne`, `lt`, `lte`, `gt`, `gte`, `between`,
`in`, `is_null`, and `default`. Use `in:` in YAML, not `in_:`. `default: true`
must be the last rule.

## Worked example

See `examples/table-heavy-ops-dashboard.yml` — five incident rows with
priority, status, revenue impact, and owner columns. Inline data, no warehouse
needed.

{{#if_tool docs}}## YAML Reference

For syntax and field details: {{ s_yaml_reference_footer }}
{{/if_tool}}
