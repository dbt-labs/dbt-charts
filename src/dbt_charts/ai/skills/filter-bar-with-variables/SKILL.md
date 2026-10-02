---
name: filter-bar-with-variables
kind: pattern
description: >
  Pattern for interactive dashboards with dropdown/date/numeric filter controls
  wired into queries via Jinja variable substitution. Use when users need to
  slice data by dimension, date range, or threshold without editing YAML.
  Triggers on: 'filter', 'dropdown', 'date range', 'interactive', 'variable',
  'select', 'picker', 'parameterized'. Variables render as UI controls above
  the charts. Do NOT use for drill-through navigation between boards (use
  drill-down-link). Do NOT use when all data fits on one chart without
  filtering.
metadata:
  author: fivetran
---

# Filter Bar with Variables

Variables add interactive controls to a board. Each `variables:` entry renders
a UI widget (select, date picker, number slider). Queries reference variables
with `{{ var_name }}` Jinja placeholders — dbt charts substitutes the current
widget value at query time.

## When to reach for this

- Users need to slice the data by region, category, time window, or threshold
- The same board serves multiple audiences who want different views
- Query results would be too large without a default filter

## When NOT to use this

- Cross-board navigation → `drill-down-link`
- Static content, no user controls needed

## The pattern

```yaml
variables:
  region:
    label: Region
    notes: Filter all charts to this region
    options:
      static: [North, South, East, West]

  date_range:
    label: Date Range
    input: daterange
    column: orders.order_date
    default: ["2025-01-01", "2025-12-31"]

queries:
  monthly:
    notes: Monthly revenue filtered by region and date window
    sql: |
      SELECT DATE_TRUNC('month', order_date) AS month,
             SUM(revenue) AS revenue
      FROM orders
      WHERE {{ filter('region', region) }}
        AND {{ filter_date_range('order_date', date_range) }}
      GROUP BY 1
      ORDER BY 1
```

See `examples/filter-bar-with-variables.yml` for the worked example.

## Variable input types

| Input | YAML | Widget |
|---|---|---|
| Dropdown (static list) | `options: static: [A, B, C]` | Select element |
| Date range | `input: daterange` | Two date pickers |
| Single date | `input: date` | One date picker |
| Numeric input | `input: number` | Free numeric entry |
| Range slider | `input: slider` (or `range`), `min:`, `max:`, `step:` | Bounded numeric range |
| Free text | `input: text` | Text input |

## Filtering helpers in SQL

| Helper | Use for |
|---|---|
| `{{ filter('col', var) }}` | Single-value dropdown — emits `col = 'val'` or `1=1` when unset |
| `{{ filter_date_range('col', date_range) }}` | Date range variable |
| `{{ filter('col', var, '>=') }}` | Numeric threshold with operator |

Always use these helpers — never write `WHERE col = '{{ var }}'` directly. The
helpers are null-safe (emit `1=1` when the variable is unset) and handle quoting
correctly.

## Common pitfalls

| Pitfall | Why it breaks | Fix |
|---|---|---|
| Variable name contains a hyphen | Jinja can't parse `{{ my-var }}` | Use underscores: `my_var` |
| No `default:` on a required variable | Blank widget, query may fail | Add a sensible `default:` value |
| Scoping variables globally but only one chart needs them | Re-queries all charts | Nest variables + queries + charts in a nested board |

## Worked example

See `examples/filter-bar-with-variables.yml` — region dropdown and date
pickers wired to a sales query. Uses inline data to compile without a
warehouse; swap `columns/values` for `sql:` when connecting to a live source.

{{#if_tool docs}}## YAML Reference

For syntax and field details: {{ s_yaml_reference_footer }}
{{/if_tool}}
