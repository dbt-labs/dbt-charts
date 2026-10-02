---
name: drill-down-link
kind: pattern
description: >
  Pattern for cross-board navigation: a chart or table whose elements link to a
  detail board, passing a row value as a URL path segment or query parameter.
  Use when users need to click from a summary view into a per-item detail board.
  Triggers on: 'drill down', 'click through', 'detail page', 'per-item board',
  'link to detail', 'cross-board navigation'. Do NOT use for filtering within
  the same board (use filter-bar-with-variables). Do NOT use for tabs within a
  single board (use the tabs layout).
metadata:
  author: fivetran
---

# Drill-Down Link

Two boards wired together: a *source* board whose chart elements are clickable
links, and a *target* board that receives the clicked value as a URL parameter
and filters its queries accordingly.

The source uses `link:` on a chart (channel placeholder substitution) or
`link:` on a table column (per-row URL). The target reads the URL parameter
via a `variables:` entry with `input: text`.

## When to reach for this

- Users need to click from a ranking or list into a per-item detail view
- The detail content is rich enough to justify its own board (not just a tooltip)
- The linking dimension is a natural identifier (region, product, customer ID)

## When NOT to use this

- Filtering within the same board → `filter-bar-with-variables`
- Show/hide sections → tabs layout
- Simple tooltip detail → hover tooltips work automatically, no extra YAML

## The pattern — source board

```yaml
charts:
  region_bars:
    type: bar
    query: revenue_by_region
    x: region
    y: revenue
    title: Revenue by Region — click to drill
    link: "/region-detail?region={{ x }}"   # {{ x }} = clicked bar's region
```

For tables, a chart-root `link:` makes the whole row clickable (a row-wide band
that highlights on hover), and a per-column `link:` in `style.columns` makes
that column's cells their own links. A cell link wins the click over the row
band:

```yaml
charts:
  customers_table:
    type: table
    query: top_customers
    link: "/customer-detail?id={{ customer_id }}"   # whole-row drill-down
    style:
      columns:
        customer_name:
          label: Customer
          # no column link → clicking the row navigates to customer-detail
        status:
          label: Status
          link: "/status-detail?status={{ status }}"   # this column links elsewhere
        customer_id:
          visible: false   # feeds the row link only — never a display column
```

A column `link` applies only to that column's cells; it does not fall through
to other columns. Columns without their own `link` are just part of the
clickable row, not separate links.

## The pattern — target board

```yaml
variables:
  region:
    input: text            # populated from ?region= query param
    default: "North"       # fallback when opened standalone

queries:
  region_detail:
    sql: SELECT * FROM orders WHERE {{ filter('region', region) }}
```

See `examples/drill-down-link.yml` for the source board worked example.

## Variations

| Variation | YAML knob | When |
|---|---|---|
| Path segment | `link: "/detail/{{ x }}"` | Clean URLs, server-side routing |
| Query param | `link: "/detail?id={{ x }}"` | Multiple params, easier debugging |
| Table cell link | `link:` on column in `style.columns` | Per-row links in a table |
| In-page filter | `link: "?var={{ x }}"` | Update a variable without navigation |
| External link | `link: "https://external.com/{{ x }}"` | Link out to another system |

## Common pitfalls

| Pitfall | Why it breaks | Fix |
|---|---|---|
| Using `{{ color }}` placeholder on a chart with no color channel | Compile error: `link template references channel 'color' but chart has no 'color' encoding assigned` | Use only the chart's mapped channels: `x`, `y`, `color`, `theta` |
| Target board has no matching variable | Parameter ignored, shows all data | Add a `variables:` entry on the target with the same name |
| Using `href:` instead of `link:` | Compile error: `'href:' was renamed to 'link:'` | Use `link:` on all chart types |

## Worked example

See `examples/drill-down-link.yml` — the source board: a bar chart with
`link:` that passes the clicked region to a detail board. Inline data, no
warehouse required. The target board is documented in the body above — author
it as a separate YAML file in the same `charts/` directory.

{{#if_tool docs}}## YAML Reference

For syntax and field details: {{ s_yaml_reference_footer }}
{{/if_tool}}
