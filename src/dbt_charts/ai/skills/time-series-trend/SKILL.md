---
name: time-series-trend
kind: pattern
description: >
  Pattern for a line (or area) chart tracking one or more numeric metrics over
  time. Use when the question is "how has X changed over time?" with a date
  dimension on x. Triggers on: 'trend', 'over time', 'time series', 'daily',
  'weekly', 'monthly', 'historical'. Add a date-range variable to make it
  interactive. Do NOT use for category comparisons without a time axis (use
  top-n-with-detail or before-after-comparison). Do NOT use for composition
  over time (use faceted-small-multiples with area type).
metadata:
  author: fivetran
---

# Time-Series Trend

A line chart with a date dimension on x and a numeric metric on y. Data must
be monotonically ordered by date; add `ORDER BY date` to every time-series
query. Add a `date_range` variable when users need to zoom into a window.

## When to reach for this

- The data has a date/timestamp column and a numeric value per period
- The question is directional: "is this metric going up or down?"
- You want to compare two metrics on the same time axis (multi-series)

## When NOT to use this

- Comparing categories without time → `top-n-with-detail`
- Showing composition across time → area chart in `faceted-small-multiples`
- Single current-period metric → `single-metric-bignum`

## The pattern

```yaml
variables:
  date_range:
    input: daterange
    column: orders.order_date
    default: ["2025-01-01", "2025-12-31"]

queries:
  monthly_revenue:
    sql: |
      SELECT DATE_TRUNC('month', order_date) AS month,
             SUM(revenue)                    AS revenue
      FROM orders
      WHERE {{ filter_date_range('order_date', date_range) }}
      GROUP BY 1
      ORDER BY 1

charts:
  revenue_trend:
    type: line
    query: monthly_revenue
    x: month
    y: revenue
    title: Monthly Revenue
    style:
      axis_x:
        time_unit: yearmonth
```

See `examples/time-series-trend.yml` for the inline-data worked example.

## Variations

| Variation | YAML knob | When |
|---|---|---|
| Multi-series | `color: series_col` | Comparing two segments over time |
| Area fill | `type: area` | Emphasize magnitude, not just direction |
| Label cadence | `style.axis_x.label.time_unit` | Label a finer data grain at a coarser readable cadence |
| Date-range variable | `variables: date_range: input: daterange` | User-controlled window |
| Rolling window | wrap SQL in a window function | Smooth noisy daily data |
| Trendline | fit in a second query, plot via `layers:` | Show overall direction through noise (see below) |

## Adding a trendline

dbt charts has no built-in trendline toggle — data belongs to queries, so compute
the fit in SQL and plot it as a line layer on the same chart (or as its own chart).
Reference the base query with `{{ queries.<name> }}` so the fit always covers
exactly the series the chart shows:

```yaml
queries:
  revenue_fit:
    sql: |
      WITH base AS (
        SELECT month, revenue, epoch(month) AS x
        FROM {{ queries.monthly_revenue }}
      ),
      fit AS (
        SELECT regr_slope(revenue, x) AS m, regr_intercept(revenue, x) AS b
        FROM base
      )
      SELECT base.month, fit.m * base.x + fit.b AS trend
      FROM base, fit
      ORDER BY base.month

charts:
  revenue_trend:
    type: line
    query: monthly_revenue
    x: month
    y: revenue
    layers:
      - type: line
        query: revenue_fit
        y: trend
        label: Trend
```

Two warehouse notes: map a temporal x to a number before fitting (`epoch()` is
DuckDB — use your warehouse's date-to-number equivalent, e.g. Postgres
`EXTRACT(EPOCH FROM month)`), and `REGR_SLOPE`/`REGR_INTERCEPT` ship on DuckDB,
Postgres, and Snowflake — on BigQuery and Redshift, which ship neither, derive
the slope as `(AVG(x*y) - AVG(x)*AVG(y)) / (AVG(x*x) - AVG(x)*AVG(x))` and the
intercept as `AVG(y) - slope*AVG(x)`. Do not reach for `COVAR_POP` on Redshift:
it is on the same unsupported list as `REGR_SLOPE`.

## Common pitfalls

| Pitfall | Why it breaks | Fix |
|---|---|---|
| Missing `ORDER BY date` | Jagged, non-temporal line | Always order by the date column |
| Date column as string without cast | Wrong sort, string ordering | `CAST(date AS DATE)` or use `DATE_TRUNC` |
| Too many series (>5) | Legend unreadable | Filter or group remainder into "Other" |
| Hourly data on a monthly board | Too many points, slow | Pre-aggregate to the right grain in SQL |

## Worked example

See `examples/time-series-trend.yml` — six months of revenue, no warehouse
needed. Add `variables:` + `WHERE` clause when connecting to a live source.

{{#if_tool docs}}## YAML Reference

For syntax and field details: {{ s_yaml_reference_footer }}
{{/if_tool}}
