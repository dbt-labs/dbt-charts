---
name: chart-recipes
description: Named chart shapes no `type:` draws (bullet, waterfall, lollipop, dumbbell,
  slope, bump, gantt), bars against a target per period, and pacers, each built from
  bar and line charts with layers.
metadata:
  author: fivetran
---
# Chart recipes

- bullet: type: bar with style.stack: zero, style.stack_order: data and color: on the qualitative range column — one row per (category, band) — plus a layers: bar on the value and a layers: line on the target, both reading their own one-row-per-category source via layers[].query (sharing the ranges' rows multiplies each by the band count). Thin the value bar with style.marks.bar.band_width so the ranges stay visible, give the target style.marks.line.curve: step with connect: false for the goal tick, and set style.orientation: vertical
- waterfall, bridge chart: type: bar with y_start and y from a running total the query computes over one row per step (SUM(delta) OVER (ORDER BY ord) - delta and SUM(delta) OVER (ORDER BY ord), ord unique per step; the opening and closing totals are rows starting at 0), color: on the query's own direction column pinned with board category_colors, style.overlap: full so each step's bar fills its band instead of splitting into a grouped slot, and style.orientation: vertical
- lollipop: type: bar with style.marks.bar.band_width thinned to a stem, plus a layers: scatter on the same y (style.marks.bar.size is a separate fixed-pixel mode and does not thin the bar)
- dumbbell, barbell, connected dot plot: type: bar with y_start for one end and y for the other, thinned to a stem with style.marks.bar.band_width (0.03), plus a layers: scatter on each end. The y dot wears the stem's legend color, so paint only the y_start dot, with layers[].style.marks.point.color
- slope: type: line with a two-category x and color: on the series column (year-shaped x values resolve to a continuous temporal scale, which fills in the span between the pair)
- bump: type: line on a rank column with color: on the series column (the data is entity x period x rank, so without it the rows collide on the period key) and a descending style.axis_y.scale.continuous.domain (e.g. [6, 1]) so rank 1 is on top
- gantt: type: bar with a date y and a date y_start, style.orientation: horizontal so time runs left to right, keeping the query's row order rather than the usual value-descending default

## Targets and pacers

A value against a target per period (monthly revenue against plan) is bars with the target drawn as a flat tick on each bar, never a line across the bars. A period still in progress gets a dashed pace tick at the target times the share of the period elapsed, from a one-row `pace` query, so a partial bar reads against pace. Every layer's `x` has the bars' type (cast both to DATE):

```yaml
type: bar
x: month
y: revenue
layers:
  - type: line
    y: target
    style:
      marks:
        line:
          curve: step
          connect: false
  - type: line
    query: pace
    y: pace
    label: On pace
    style:
      marks:
        line:
          curve: step
          connect: false
          stroke:
            dasharray: 4 3
```

One target for the whole chart is a `rule` annotation at `y:`. A cumulative pacer is a running-total `line` with a `layers:` line from 0 at the period's start to the target at its end.
