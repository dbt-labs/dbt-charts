# dbt charts Warning Reference


Auto-generated from `dbt_charts.core.diagnostics.REGISTRY`. Every `WARN-*` code dbt charts can emit, grouped by docs topic. All warnings are suppressible via a query's `ignore:` or a chart's `warnings_ignore:` field.


## board


### WARN-DOUBLE-HEADER: Board title is repeated by a heading at the top of the body

- **Level:** warning
- **Domain:** compile
- **Suppressible:** yes

**Message template:**

```
Board title {title!r} is followed immediately by a level-{level} markdown heading {heading!r}; the board renders both, so the dashboard headers itself twice.
```

**Fix:** Delete the heading line from the body; `title:` already renders as the board header. If you would rather keep the heading, drop `title:` instead and let it be the header.

Fires when a board sets `title:` and the first block of its body markdown is a heading that either is level 1 (a document has one document title) or repeats the board title. The board prints the board title as its header and the markdown heading directly beneath it. A heading further down the body, or a lower-level heading with different text (`## Overview` under `title: Sales`), is ordinary section structure and does not fire. Heading detection uses the same markdown parser that renders the text, so a fenced code block opening with a `#` comment is not mistaken for a heading. Checked on the board itself, not on nested boards; a section card pairing its title with a styled body heading is a deliberate pattern. Emitted by `compile/validate/board_warnings.py`.

### WARN-H1-BODY-NO-TITLE: Body opens with a level-1 heading but the board has no title

- **Level:** warning
- **Domain:** compile
- **Suppressible:** yes

**Message template:**

```
Body opens with a level-1 markdown heading {heading!r} and the board has no `title:` set; the heading renders as plain body text instead of the styled board header.
```

**Fix:** Set `title: {heading}` and delete the `# {heading}` line from the body text.

Fires when a board has no `title:` and the first block of its body markdown is a level-1 heading; the author likely reached for a markdown heading instead of the purpose-built `title:` field, so the board renders as unstyled prose with no header. Only a literal level-1 heading counts; a lower-level heading (`## Overview`) with no title is ordinary section structure and does not fire, since there is no title text to compare it against. Heading detection uses the same markdown parser that renders the text, so a fenced code block opening with a `#` comment is not mistaken for a heading. Checked on the board itself, not on nested boards; a section card's own opening heading is not inspected. Emitted by `compile/validate/board_warnings.py`.

### WARN-HTML-POLICY-CAPPED: Board html_policy downgraded by deployment ceiling

- **Level:** warning
- **Domain:** compile
- **Suppressible:** yes

**Message template:**

```
Board requested html_policy={requested!r} but the {ceiling_source} ceiling is {ceiling!r}; effective policy is {effective!r}. Raw HTML in this board will be treated as {effective!r}.
```

**Fix:** Either lower the board's `html_policy:` to {effective!r} to match what is actually rendered, or raise the deployment ceiling (DCT_HTML_POLICY_CEILING or markdown.html_policy_ceiling in dbt_charts.yml) if the deployment operator permits it.

Fires when a board's authored `html_policy` is above the effective deployment ceiling (set by DCT_HTML_POLICY_CEILING env var or `markdown.html_policy_ceiling` in dbt_charts.yml). The board compiles successfully but the policy is downgraded at normalize time; this warning makes the downgrade visible so the author knows their html_policy setting is not being honored. Emitted by `compile/compiler.py`.

### WARN-SINGLE-CHART-REDUNDANT-TITLE: Single-chart dashboard has both a board title and a chart title

- **Level:** warning
- **Domain:** compile
- **Suppressible:** yes

**Message template:**

```
Board title {title!r} sits above a single chart that carries its own title {chart_title!r}; two headers for one chart.
```

**Fix:** Give the board one header: drop the chart's `title:` (the board title already heads the board, and it is what listings, search, and nav show), or drop the board `title:` and let the chart title stand.

Fires when a board's whole content is one chart and both the board and the chart carry a title, so the board stacks two headers over a single piece of content. A single-chart dashboard does not need a separate dashboard title. Does not fire when only one of the two is titled, when the board holds more than one chart, or when body prose sits between the two titles. KPI and callout charts are excluded: a KPI labels itself with `label:` and a callout's title is a prose lead-in, so neither stacks a chart header under the board's. Checked on the board itself, not on nested section boards. Emitted by `compile/validate/board_warnings.py`.

## charts


### WARN-AREA-UNSTACKED-READS-AS-STACKED: Unstacked area is hard to tell apart from a stacked one

- **Level:** warning
- **Domain:** render
- **Suppressible:** yes

**Message template:**

```
Chart {chart_id!r}: hard to tell this area chart apart from a stacked one. Its {count} series from {field!r} overlap, so the outer edge represents an individual series value, not the total.
```

**Fix:** Set `style.stack: "zero"` if the series compose a total, or use `type: line` if they are independent trends. Which one this occurrence recommends leading with depends on how much of the real total the chart's outer edge is hiding.

Fires when an area chart resolves to an unstacked mode (`stack: none`), paints two or more series (from a `color:` column or a wide `y: [a, b, c]` measure list), and no pair of those series visibly trades places anywhere on the axis. A pair is compared at the x values they share, on values coercible to a number, and only where both sit on one side of the zero baseline: a series painting above the baseline and one painting below it never warn about each other, since their bands occupy disjoint regions rather than nesting. A log y axis is not judged, since position there is logarithmic and no single ratio converts a gap at every magnitude. dbt charts already signals stacking through fill weight: an unstacked area's fill is translucent, a stacked one's is solid. A series that visibly trades places with another declares itself an overlap; the reader sees two bands swap and reads them as independent. Where that never visibly happens, each band looks nested inside the next at every x, indistinguishable from a real stack whatever the fill opacity says. A reader takes the outer edge for the total; it instead represents an individual series value, and the real total's magnitude may be several times larger. The render is unchanged by this warning; it reports a chart that is very likely either a stacked area missing its `stack:` key or a line chart drawn with the wrong mark. Small multiples split by the series column itself give each series its own panel, so no panel holds a pair to compare and nothing is reported. A chart with overlay layers is never judged: a layer can paint the very total the base series' outer edge only looks like, and judging the base alone would fire on a chart that already resolves the ambiguity. A series that repeats the same x value on two or more rows within one panel abstains the whole panel, since there is no principled way to pick which of the repeated values the chart actually paints.

### WARN-AXIS-ALIGN-DISCARDED: Authored axis_y.labels.align has no effect on a house-format quantitative axis

- **Level:** warning
- **Domain:** compile
- **Suppressible:** yes

**Message template:**

```
Chart {chart_id!r}: axis_y.labels.align = {authored_align!r} has no effect: the format alias {format_alias!r} forces label.align = 'right' on right-edge quantitative axes for correct digit alignment.
```

**Fix:** Remove axis_y.labels.align from this chart. To opt out of forced alignment, use a literal d3 format spec (e.g. '.1%') instead of the alias.

Fired when a chart authors axis_y.labels.align alongside a house-rule format alias (percent, currency, etc.) on a right-edge quantitative axis. The alias forces label.align = 'right' for place-value alignment; the authored align value is silently discarded.

### WARN-AXIS-LABEL-COLLISION: X-axis tick labels overlap with no room left to fix it

- **Level:** warning
- **Domain:** render
- **Suppressible:** yes

**Message template:**

```
Chart {chart_id!r}: x field {field!r} has {label_count} tick labels that overlap even after skipping and tilting labels — they will render on top of each other.
```

**Fix:** Widen the chart, reduce the number of x categories (roll up to a coarser time grain, filter the data), or shorten the labels.

Fires on a line/area/scatter chart whose x-axis tick labels still overlap after the render tried every enabled overlap strategy (`style.axis_x.labels.overlap.skip`/`.tilt`, both on by default) — the same collision a table's cramped-columns warning reports for width, applied to axis tick text. Unlike `WARN-TOO-MANY-X-CATEGORIES` (a fixed category-count ceiling on a categorical band axis), this fires on any x-axis shape, including a continuous temporal axis whose auto-picked ticks simply don't fit the chart's width.

### WARN-AXIS-TITLE-TRUNCATED: Axis title was too long and was truncated with an ellipsis

- **Level:** warning
- **Domain:** render
- **Suppressible:** yes

**Message template:**

```
Chart {chart_id!r}: {authored_field!r} was truncated: the authored text {authored_text!r} did not fit within two lines at the available extent.
```

**Fix:** Shorten the axis title, or widen the chart so the title has more room.

Fires when an axis title is pre-wrapped to at most two lines (to prevent Vega-Lite's autosize from collapsing the plot) and the authored text is still too long: the last line is cut with a Unicode ellipsis (…) and the remainder of the title is lost. The title text in the message is the full authored text before truncation, so you can see exactly what was cut.

### WARN-BAR-BAND-WIDTH-TOO-NARROW: Bar chart bands are too narrow to read

- **Level:** warning
- **Domain:** render
- **Suppressible:** yes

**Message template:**

```
Chart {chart_id!r} has {distinct} bands x {series} series across {render_width:.0f}px (~{bar_width:.2f}px per bar); {min_band_width:.0f}px is the threshold this configuration crosses, so bars will read as a merged block instead of separate marks.
```

**Fix:** Widen the chart (or, for a horizontal bar, make it taller), reduce the number of categories, or (for time series) roll up to a coarser grain (e.g. day -> week or month).

Fires on bar (vertical + horizontal) charts that pack so many bands into the plot's bounding dimension (width for vertical, height for horizontal) that each band's fill drops below a readability floor: the fill disappears and the bar's own border stroke merges neighbors into a "ghost band" smear. Classic trigger: daily-granularity data (hundreds of distinct days) rendered as bars at a normal chart size, or a grouped/wide bar whose per-series sub-band is too thin even though the outer band is not. Also fires on a numeric x (no band scale, vertical bars only) when the bar's width, authored or computed from gap/min_size/max_size, exceeds the gap between the closest two x values, so adjacent bars visually overlap.

### WARN-BAR-GROUPED-SERIES-COINCIDE: Grouped bar series paint on top of each other

- **Level:** warning
- **Domain:** render
- **Suppressible:** yes

**Message template:**

```
Chart {chart_id!r}: {field!r} is quantitative (or a wide temporal range), so its color-grouped bars have no band scale to offset series within: every series paints at the same position, hiding all but the last one drawn.
```

**Fix:** Switch the x field to a categorical or bucketed-time column (so bars group side by side), or use a line/area/scatter mark, which already draw overlapping series legibly on a continuous x.

Fires when a color-grouped bar (color + stack: none) sits on a quantitative x, or a temporal x wide enough to promote to Vega-Lite's continuous temporal type. Vega-Lite's xOffset/yOffset sub-scale needs a discrete band to divide bars within; a continuous position has none, so every series paints at the identical position and width, each one occluding the series drawn before it. The render is unchanged by this warning: bar renders the same overlapping marks a line/area/scatter chart already draws on the same data, just without a legible way to tell the series apart.

### WARN-CALLOUT-TEXT-TRUNCATED: Callout text was truncated

- **Level:** warning
- **Domain:** render
- **Suppressible:** yes

**Message template:**

```
Chart {chart_id!r}: callout {authored_field!r} was truncated: the authored text {authored_text!r} exceeded the maximum lines.
```

**Fix:** Shorten the callout text, or increase the chart height/width.

Fires when a callout chart's title, message, or hint text is wrapped and the last line is cut with a Unicode ellipsis because the text exceeds the maximum line count.

### WARN-CATEGORY-COLOR-PIN-UNSEEN: A category_colors pin names a value this render never draws

- **Level:** warning
- **Domain:** render
- **Suppressible:** yes

**Message template:**

```
`style.charts.category_colors.{field}` pins {values}, which this render does not draw; the pin was skipped.
```

**Fix:** Check the value's spelling against the data. A pinned value can also be missing because a variable filter or a narrowed layout excluded it, not necessarily a typo.

Fires when a value pinned under `style.charts.category_colors.<field>` names something none of the charts on this render actually draw for that field. The pin is skipped rather than seated — seating it would claim a palette slot for a category nothing draws and push every real value along one — but a misspelled pin would otherwise do nothing with no signal at all. The value can be genuinely absent for reasons other than a typo: a variable filter, a narrowed `dct render --chart` layout, or a sibling chart whose query failed.

### WARN-CHART-TITLE-TRUNCATED: Chart title or subtitle was truncated with an ellipsis

- **Level:** warning
- **Domain:** render
- **Suppressible:** yes

**Message template:**

```
Chart {chart_id!r}: {authored_field!r} was truncated: the authored text {authored_text!r} did not fit within the available width.
```

**Fix:** Shorten the title or subtitle, or widen the chart.

Fires when a chart title or subtitle is wrapped and the last line is cut with a Unicode ellipsis (…) because the authored text exceeds the available width. The message shows the full authored text before truncation.

### WARN-ENDPOINT-LABEL-GAP-OVERFLOW: Endpoint-label rail is too cramped for its intended spacing

- **Level:** warning
- **Domain:** render
- **Suppressible:** yes

**Message template:**

```
Chart {chart_id!r} packs {series_count} endpoint labels needing {gap_px:.0f}px apart into a plot shorter than that: labels are distributed evenly across the plot instead of at their intended spacing.
```

**Fix:** Give the chart more height, reduce the number of series, or switch to a color legend instead of an endpoint-label rail.

Fires when an endpoint-label rail's intended gap (`font_size * chart_rendering.endpoint_labels.line_height_multiplier`) cannot fit between the rail's series count and the plot's actual height. This is advisory, not a floor: the rail still renders, with labels distributed evenly across the available plot rather than piled onto the domain edges.

### WARN-ENDPOINT-LABEL-RAIL-OVERFLOW: Endpoint-label rail dropped series that had no room, despite fitting overall

- **Level:** warning
- **Domain:** render
- **Suppressible:** yes

**Message template:**

```
Chart {chart_id!r}: {dropped_count} of {series_count} endpoint labels could not be placed {gap_px:.0f}px apart and were dropped from the rail rather than piled onto the same spot.
```

**Fix:** Unlike a rail that is cramped everywhere, more height reliably helps here: it gives the clustered anchors room to spread apart from whichever series they crowded against. Reducing the series count or switching to a color legend also works.

Fires when an endpoint-label rail's real anchors are clustered such that some labels cannot be placed at the intended gap, even though `(n - 1) * gap` fits the plot height in the best case: that global check only proves the *block* of labels fits somewhere in the domain, not that the anchors' own positions leave room for all of them. Distinct from `WARN-ENDPOINT-LABEL-GAP-OVERFLOW`, which keeps every label and compresses the spacing instead: this fires only when some labels are dropped from the rail entirely.

### WARN-ENDPOINT-LABEL-RAIL-TIED: Endpoint-label rail has no spread to place labels along

- **Level:** warning
- **Domain:** render
- **Suppressible:** yes

**Message template:**

```
Chart {chart_id!r}: all {series_count} series end on the same value, so the rail has no vertical spread to space labels by: they are distributed evenly across the plot instead of stacking on one point.
```

**Fix:** This is a property of the data, not the layout; more height will not change it. Check whether the trailing rows are null or zero for every series; if that is expected, a color legend names the series without implying distinct endpoints.

Fires when an endpoint-label rail's labels cannot be spaced because no pixels-per-data-unit could be measured from them: every series ends on the same value (a trailing all-null or all-zero column is the usual cause), or the y scale collapsed them onto a single pixel. Distinct from `WARN-ENDPOINT-LABEL-GAP-OVERFLOW`, which is a height problem: this one is a data property and adding height cannot clear it. Advisory, not a floor: the rail still renders.

### WARN-FACET-PANEL-WIDTH-BELOW-MINIMUM: Small-multiples panel width shrank below the legibility floor

- **Level:** warning
- **Domain:** render
- **Suppressible:** yes

**Message template:**

```
Chart {chart_id!r} facets into {panel_cols} column panel(s) at {panel_width:.0f}px each, below the {min_panel_px:.0f}px floor small multiples need to stay legible.
```

**Fix:** Widen the chart, reduce the column-facet's cardinality, or move part of the split from `multiples.columns` to `multiples.rows` (rows stack vertically instead of dividing the card's width).

Fires when a small-multiples chart's column-facet cardinality leaves each panel narrower than the configured legibility floor. The floor is `chart_rendering.facet.min_panel_px`. The card's declared width is never negotiable, panels shrink below the floor rather than push painted content past the card's edge, so this is the author's only signal that the panel count has outgrown the card.

### WARN-KPI-ALIGN-OVERFLOW: KPI align was ignored because a text run overflowed the card

- **Level:** warning
- **Domain:** render
- **Suppressible:** yes

**Message template:**

```
Chart {chart_id!r}: a text run is wider than the card, so align: center/right was not applied; every run on the card stays left-aligned and the overflowing one spills past the right edge.
```

**Fix:** Widen the card, shorten the value with a more compact format, or drop align: so the left-aligned overflow is expected.

Fires when a KPI authors align: center or align: right and any of its text runs (value, label, or support) is wider than the available content width. Alignment is a whole-card choice, so it is dropped for every run rather than applied to the ones that fit: clamping per run would leave the card with two different alignments. Shifting the run would give it a negative x, and the card's SVG viewport clips at x=0; destroying the value's LEADING characters, so a right-aligned 1,234,567,890 would read as a well-formed but wrong 234,567,890. The renderer keeps the run at the left edge instead, where overflow spills right and reads as visibly truncated, and reports that the authored align did not take effect.

### WARN-KPI-INLINE-VARIANT-FALLBACK-TO-STACKED: KPI inline variant fell back to stacked

- **Level:** warning
- **Domain:** render
- **Suppressible:** yes

**Message template:**

```
Chart {chart_id!r}: variant: {authored_text!r} did not fit the card at the available width: value, label, and support fell back to the stacked arrangement instead of painting past the card edge.
```

**Fix:** Widen the card, shorten the label or support text, or author variant: stacked directly.

Fires when an inline KPI's assembled value + label + support run does not fit the card at its available width. The renderer falls back to the stacked arrangement for that card rather than paint past the card edge, so the card's actual layout no longer matches the authored variant: inline.

### WARN-KPI-LABEL-TRUNCATED: KPI card label was truncated

- **Level:** warning
- **Domain:** render
- **Suppressible:** yes

**Message template:**

```
Chart {chart_id!r}: KPI label was truncated: the authored text {authored_text!r} did not fit within the card at the available width.
```

**Fix:** Shorten the KPI label, widen the card, or use a smaller font size.

Fires when the KPI card label text is clipped or wrapped with an ellipsis because it exceeds the card width. The message shows the full authored label before truncation.

### WARN-LAYER-X-DOMAIN-PAINT-ORDER: Layered chart x-axis is ordered by paint order, not by the data

- **Level:** warning
- **Domain:** render
- **Suppressible:** yes

**Message template:**

```
Chart {chart_id!r}: layer queries contribute {n_new} x categor{plural} ({sample}) that the base query never returns on {x_field!r}, and there is no order the base states to place them into, so the axis is drawn base-query rows first, then each layer's, and the left-to-right reading order is paint order rather than an ordering the data states.
```

**Fix:** Return every x category from one query: join the layers onto a shared spine so the axis order is that query's. An authored `sort:` is not a fix: it orders only the categories the base query returns and leaves the layer's appended in paint order.

Fires on a layered chart whose typed layers contribute x categories the base query does not, either from their own `query:`, or from their own `x:` column on the shared one, when the engine cannot place them into an order the base states. Two shapes reach that: the categories have no derivable order at all (not dates, not numbers), or they do but the base query's own rows are not in it, so there is no direction to extend. Date-like buckets and numeric categories over a base already in that order are placed into it instead, and never warn. Plain labels (month abbreviations, region names) cannot be: the union is base-query order followed by each layer's own, which is the order the layers happened to be painted in. On the migrated shape this reads as a chronology it is not: `Jan, Mar, May, Feb, Apr, Jun`. Sorting them lexically would be worse than paint order, so the engine leaves the order alone and says so rather than guessing.

### WARN-LAYERED-CHART-SHARED-Y-AXIS-SCALE-MISMATCH: Layered chart y series have a large scale mismatch

- **Level:** warning
- **Domain:** render
- **Suppressible:** yes

**Message template:**

```
Chart {chart_id!r}: y columns {col_a!r} and {col_b!r} share a y-axis but their value ranges differ by {ratio:.0f}×: the smaller series will be visually crushed to a flat line.
```

**Fix:** Split into two y-axes by adding `axis_y:` on one of the layers, or normalize the series to a common scale in the query.

Fires on a layered chart where the base chart's own y series and/or its layers share the y-axis but their value ranges differ by ≥100×: the smaller series is visually crushed to a flat line. Classic example: revenue (millions) overlaid with conversion rate ([0, 1]).

### WARN-LAYOUT-MIN-EXCEEDS-HEIGHT: Chart's category count needs more height than its row allows

- **Level:** warning
- **Domain:** render
- **Suppressible:** yes

**Message template:**

```
Chart {chart_id!r} has {n_categories} category bands, which need at least {min_height:.0f}px to stay readable, but its row/tile height caps it at {authored_height:.0f}px. The chart rendered at {min_height:.0f}px anyway: the row's authored height was not honored, and any sibling charts sharing the row grew to match.
```

**Fix:** Raise the row's height to fit the category count, or reduce the categories (filter, paginate, or roll up to a coarser grain) so the readable minimum fits inside the authored height.

Fires when a horizontal bar chart's category count forces a minimum height (one readable band per category) that exceeds the row/tile height its author assigned. The engine expands the chart to the computed minimum regardless (squashing labels past legibility to honor an impossible authored height would be worse), so the row's authored height silently loses; any sibling chart in the same row inherits the expansion. This warns so the author learns why the row grew, instead of measuring it by hand.

### WARN-LEGEND-POSITION-WIDTH-FALLBACK: Tiny-width tier overrode an authored legend position

- **Level:** warning
- **Domain:** render
- **Suppressible:** yes

**Message template:**

```
Chart {chart_id!r} authored `legend.position: {authored_position}`, but the tiny width tier forced the legend back to `top`: a card this narrow cannot hold a side legend.
```

**Fix:** Widen the chart past the tiny width tier, or drop `legend.position` and accept the automatic top legend.

Fires when a cartesian chart (bar, line, area, scatter, heatmap) authors a non-top `legend.position` and the card's width falls into the tiny tier, where the engine forces the legend back to a compact top strip regardless. The fallback itself is not a defect (a tiny card cannot physically fit a side legend); this warning exists only because the override was otherwise silent: the resolved chart renders a different legend position than the one the author wrote, with no signal that happened.

### WARN-LEGEND-VALUES-UNRESOLVED: style.legend.values entry does not resolve against the legend domain

- **Level:** warning
- **Domain:** render
- **Suppressible:** yes

**Message template:**

```
Chart {chart_id!r} authored `legend.values` entries {values} that did not resolve to exactly one legend entry. Legend domain: {domain}.
```

**Fix:** Check the spelling against the legend domain, or remove the entry if it's expected to be legitimately absent sometimes.

Fires when `style.legend.values` names an entry that matches none of the chart's legend entries, or matches more than one. The entry is dropped rather than shown or causing a render failure. If every authored entry misses, the legend falls back to its default order. Covers every nominal/ordinal `color:` field on bar, area, line, scatter, heatmap, and pie, a wide `y: [...]` chart, an overlay's `layers:` labels, and a geoshape choropleth. Not checked: `point_map`/`bubble_map` (no legend resolution there), a quantitative, temporal, or boolean `color:` column (`legend.values` is used exactly as authored, with no diagnostic on a miss), and an overlay whose base `y` is non-quantitative (its shared color scale is never built, so `legend.values` passes through verbatim).

### WARN-LIKELY-CURRENCY-OR-PERCENT-MISSING-FORMATTER: Y-axis field looks like money or a percentage but uses a generic format

- **Level:** warning
- **Domain:** render
- **Suppressible:** yes

**Message template:**

```
Chart {chart_id!r}: field {field!r} looks like {kind} but the y-axis format is {format!r}.
```

**Fix:** Set `style.axis_y.labels.format` to a currency format (e.g. `$,.2f`) or a percent format (e.g. `.1%`) to match the field's meaning.

Fires when a chart's y-encoding field name looks like money or a percentage but the chart's baked y-axis format is unfit to render that kind. Detection is name-based: fields ending in _usd, _revenue, _amount, _pct, _rate, etc. (or bare names like `share`, `mrr`) trigger when the resolved y-axis format does not carry the matching symbol (`$` for money, `%` for a percentage).

### WARN-LOCAL-TIME-LABEL-EXPR-ON-BUCKETED-AXIS: Authored axis label expression uses local time on a bucketed axis

- **Level:** warning
- **Domain:** render
- **Suppressible:** yes

**Message template:**

```
Chart {chart_id!r}: axis_x.labels.expr calls {accessor}(), which reads datum.value in the render process's time zone. Static rendering pins that to UTC, so the {time_unit!r} bucketing's UTC-midnight tick values resolve in UTC today, not necessarily the zone you or the chart's viewers are actually in, and dbt charts has no way to render this in a different zone.
```

**Fix:** Replace local-time accessors with their utc-prefixed equivalents (utcFormat()/utcyear()/utcmonth()/...), e.g. utcFormat(toDate(datum.value), '%b %Y'), to make the UTC result explicit instead of implying a local-time read that never happens.

Fires on bar (vertical + horizontal, single-metric + multi-metric), line, and area charts when axis_x.labels.expr contains a local-time accessor (timeFormat(), year(), month(), date(), quarter(), ...) while the chart's x-axis buckets to a UTC-midnight calendar grain (yearmonth, yearquarter, year, ...), whether authored via axis_x.time_unit or auto-detected from the query data. Vega-Lite's bucketed timeUnit transform produces UTC-midnight Date values, and dbt charts always renders statically in UTC, so a local-time accessor reads them as UTC today, the same result its utc-prefixed equivalent would give, and no longer dependent on which machine renders the chart. It still cannot be made to read in a different zone: there is no per-board or per-viewer timezone setting today, in any dbt charts surface. Heatmap and scatter are not yet covered. dbt charts never rewrites an authored label expression, so this only warns; switch to utcFormat() or utcmonth()/utcyear()/... to say what actually happens.

### WARN-NORMALIZE-PERCENT-FORMAT-READS-RAW-VALUE: Percent format on a 100% stack formats the raw value, not the share

- **Level:** warning
- **Domain:** render
- **Suppressible:** yes

**Message template:**

```
Chart {chart_id!r}: percent format {format!r} formats the raw {field!r} value, not the share the normalized stack paints: one stack group's values sum to {total}, not 1, so the hover rows and the value labels multiply a raw number by 100 and print it as a percentage.
```

**Fix:** Drop the percent format (a normalized stack already labels its axis 0-100%, so the format never reaches that axis), or make the measure a real 0..1 share in SQL by dividing each value by its group's total, so the raw value and the painted share are the same number.

Fires when a bar or area chart resolves to `style.stack: normalize`, its measure carries a percent format, and the raw y values in some stack group do not already sum to about 1. The normalized stack pins the measure axis to 0-100% itself, so an authored format never reaches that axis; it reaches the hover rows, the printed value labels, and the stack-total label, and every one of those reads the RAW column value. A count of 20 under a percent format therefore prints as `2000%`. Both authoring doors reach the same baked format and both fire: `style.number_format` (or a chart's `format:`) and an authored `style.axis_y.labels.format`. A chart whose measure is already a 0..1 share, every group summing to about 1, is the honest case and never fires: there the raw value and the painted share are the same number. A non-percent format (currency, plain digits) never fires either: it prints the true raw number, and only its unit differs from the axis. A stack group summing to 0 is not judged at all, since a share is undefined at a zero total. `stack: zero` and `stack: center` are not judged either: an absolute stack labels its axis with that same authored format, so the chart is self-consistent. Small multiples are judged one panel at a time, since a normalized stack normalizes within a panel. The render is unchanged by this warning.

### WARN-PALETTE-UNSUPPORTED: Palette name is a known anti-pattern

- **Level:** warning
- **Domain:** render
- **Suppressible:** yes

**Message template:**

```
Chart {chart_id!r}: palette {requested!r} is a known anti-pattern; resolved to {resolved} instead.
```

**Fix:** Author a supported palette name; see the anti-patterns table in docs/guides/palette-resolver.md#anti-patterns.

Fires when a chart authors a palette name on the known anti-pattern list (e.g. 'RdYlGn', 'parula'); these are CVD-hostile or superseded by a dbt-charts-native palette. palette() resolves the substitute silently at compile time; this detector is the only place the nudge surfaces.

### WARN-PIE-DOMINANT-SEGMENT: Pie chart is dominated by a single segment

- **Level:** warning
- **Domain:** render
- **Suppressible:** yes

**Message template:**

```
Pie chart {chart_id!r}: {dominant_field!r} holds {dominant_share:.0%} of the total; the chart conveys a single value.
```

**Fix:** Use a KPI chart for the dominant share and a bar or table for the breakdown, rather than a pie dominated by one slice.

Fires on pie/donut charts where one slice is so large that the chart conveys a single value; the other slices are visually negligible. A near-single-value pie should be a KPI (the dominant share) plus a breakdown elsewhere.

### WARN-PIE-TOO-MANY-SEGMENTS: Pie has too many slices to read

- **Level:** warning
- **Domain:** render
- **Suppressible:** yes

**Message template:**

```
Pie chart {chart_id!r} has {segment_count} segments; angles are hard to compare past {max_segments} slices.
```

**Fix:** Use a bar chart sorted by value, or group small segments into 'Other'.

Fires on pie/donut charts whose query returns more segments than a reader can compare by angle. Humans judge angle poorly past a handful of slices; a pie with many segments is unreadable and should be a sorted bar chart.

### WARN-PIE-TOTAL-EXCEEDS-INNER-RADIUS: Donut center total is too wide for the hole

- **Level:** warning
- **Domain:** render
- **Suppressible:** yes

**Message template:**

```
Donut chart {chart_id!r}: center total {formatted_value!r} is {text_width:.1f}px wide but the hole is only {hole_diameter:.1f}px across (slot {slot_width:.0f}×{slot_height:.0f}px).
```

**Fix:** Use a compacting number format (e.g. `format: number`) to shorten the total, or enlarge whichever slot dimension is smaller so the hole diameter increases.

Fires on donut charts whose formatted center total is wider than the hole it sits in, measured with the engine's font measurer. The hole diameter is `min(slot_width, slot_height) * outer_fraction * inner_radius`, where slot_width is the laid-out width minus the slice labels' reach and slot_height is the laid-out card height (for attached-table wheels, the wheel width and the theme's continuous view height).

### WARN-PLOT-HEIGHT-BELOW-MINIMUM: Chart chrome squeezes the plot below its readability floor

- **Level:** warning
- **Domain:** render
- **Suppressible:** yes

**Message template:**

```
Chart {chart_id!r} squeezes its plot to an estimated {plot_height:.0f}px on a {card_height:.0f}px card, below the {floor_px:.0f}px floor it needs to stay readable.
```

**Fix:** Give the chart more height, drop authored chrome that is not earning its space here (subtitle, axis titles, a high-cardinality legend), or accept the smaller size deliberately and suppress this warning per-chart with `warnings_ignore`.

Fires when a bar chart's estimated plot height falls below its calibrated readability floor. The floor is `chart_rendering.plot_height_floor.ratio` of the card's own height, checked at every width: a short, wide card carrying heavy chrome starves its plot the same way a narrow one does. ERR-CHART-PAINTED-NO-MARKS already hard-fails around 247px, where marks paint with zero extent; this covers the band above it where the chart still renders but its plot has shrunk to a squashed sliver. No chrome is removed automatically to fix this: the author decides whether to widen the card, trim what they authored, or keep the chart small on purpose.

### WARN-PLOT-WIDTH-BELOW-MINIMUM: A support_table column block claims most of the card's width

- **Level:** warning
- **Domain:** render
- **Suppressible:** yes

**Message template:**

```
Chart {chart_id!r}: the support_table column block ({block_width:.0f}px) already claims most of the {card_width:.0f}px card, leaving the plot an estimated {plot_width:.0f}px.
```

**Fix:** Drop a support_table column, widen the card, or move the block to `position: right` — or accept it deliberately and suppress this warning per-chart with `warnings_ignore`.

Fires when a horizontal bar's support_table column block already claims more than `chart_rendering.support_table.column_block_share_warn_ratio` of the width it shares with the plot, measured from the column block's own font-measured reservation before any further axis-label or legend chrome is subtracted. The missing twin of WARN-PLOT-HEIGHT-BELOW-MINIMUM: no chrome is removed automatically — the author decides whether to widen the card, drop a column, or move the block. A plot that would fall below its readability floor once the remaining axis chrome is accounted for raises ERR-INPUT-INVALID instead of warning: a zero-width plot is a missing chart, not a squeezed one.

### WARN-POINT-MAP-NEGATIVE-SIZE-VALUES: Point map size measure has negative values

- **Level:** warning
- **Domain:** render
- **Suppressible:** yes

**Message template:**

```
Point map {chart_id!r}: {dropped_count} of {total_count} points have a negative {size_field!r} value and were not drawn; mark area cannot be negative.
```

**Fix:** Size by a magnitude instead of a signed value (e.g. `size: abs({size_field})` in the query) and encode direction with a diverging `color:` instead.

Fires when a bubble_map's `size:` measure contains negative values. Mark area cannot be negative, so rows with a negative size value are dropped before Vega-Lite sees them rather than drawn at the smallest visible size; a negative value clamped to the scale's zero floor would read as "nearly zero", misrepresenting a large-magnitude negative measurement. Size by a magnitude (e.g. `abs(...)` in the query) and encode direction with a diverging `color:` instead. A zero-valued row is legitimate data with a legitimate area of nothing and is never dropped or counted here.

### WARN-POINT-MAP-OUT-OF-PROJECTION: Point map has data outside the projection boundary

- **Level:** warning
- **Domain:** render
- **Suppressible:** yes

**Message template:**

```
Point map {chart_id!r}: {dropped_count} of {total_count} points are outside the {projection!r} projection boundary and were dropped.
```

**Fix:** Filter the data to the projection's region, or switch to a projection that covers the full data extent.

Fires when a point_map chart uses a bounded projection (e.g. albersUsa) and some data points fall outside its mapped region. The emitter drops those rows from spec.data before Vega-Lite sees them; this warning reports how many points were dropped and why.

### WARN-QUERY-RESULT-TRUNCATED: Chart query result truncated

- **Level:** warning
- **Domain:** render
- **Suppressible:** yes

**Message template:**

```
{subject}: query result exceeded the {reason} limit; truncated to {kept_row_count} rows.
```

**Fix:** Add a LIMIT to the query, narrow its filters, or raise execution.max_rows/max_result_bytes in dbt_charts.yml if the full result is genuinely needed.

Fires when a query's result exceeded the execution.max_rows or max_result_bytes safety ceiling and was truncated before it ever reached the result cache. The chart still renders with the truncated data; this is a safety net against an unbounded query exhausting memory or bloating the cache, not a hard error.

### WARN-QUERY-RETURNED-ZERO-ROWS: Chart query returned zero rows

- **Level:** warning
- **Domain:** render
- **Suppressible:** yes

**Message template:**

```
Chart {chart_id!r}: query returned zero rows.
```

**Fix:** Check the WHERE clause or date filter: it may be excluding all data for the current filter values.

Fires on any chart whose query returned zero rows. An empty chart renders as a blank panel with axes; no signal to the viewer that the query returned nothing. Most common cause: a WHERE clause or date filter that excludes all data.

### WARN-REDUNDANT-ENCODING: Same column bound to two visual channels

- **Level:** warning
- **Domain:** render
- **Suppressible:** yes

**Message template:**

```
Chart {chart_id!r}: field {field!r} is bound to channels {channels}: binding the same field twice adds no information.
```

**Fix:** Remove one of the channel bindings, or use different fields for each channel to encode distinct dimensions.

Fires when one query column is bound to two or more visual channels of the same chart (e.g. `y` and `color` both set to the same field), or when a `multiples.rows`/`multiples.columns` facet field is also bound to `x` or `y`. Binding the same field twice adds no information; the second channel is redundant, and for a facet collision, the axis repeats what the panel's own header already says. The bar `x==color` case is excluded: it renders full-width category-colored bars, a useful pattern. Faceting by a field also bound to `color`/`size`/`shape` is excluded too: those legends are drawn once, board-wide, never duplicated per panel, so pairing one with the facet gives every panel a consistent identifying color at no extra cost.

### WARN-SERIES-LABEL-TRUNCATED: Series label was too long for the endpoint-label rail and was truncated

- **Level:** warning
- **Domain:** render
- **Suppressible:** yes

**Message template:**

```
Chart {chart_id!r}: {count} series {labels_noun} from {authored_field!r} {were} truncated in the endpoint-label rail, which is capped at a fraction of the chart's width: {labels}.
```

**Fix:** Shorten the {authored_field} values, widen the chart, or set style.endpoint_labels.visible: false to keep the series names in the legend.

Fires when a chart's series labels are drawn in the right-hand endpoint-label rail and do not fit. The rail may claim only a fraction of the chart's width, so longer names are cut with an ellipsis (…). The labels are the values of the column bound to `color:`; for a wide-form chart authored `y: [a, b, …]`, the measure names themselves, prefixed by the `color:` column's value (`<value> - <measure>`) when one is authored; the warning names whichever key holds the long part. The label text in the message is the full value before truncation, so you can see exactly what was cut. Only the drawn label is shortened: the underlying values, the color scale, and tooltips still carry the full text.

### WARN-SPARK-LABEL-TRUNCATED: Spark-bar row label was truncated

- **Level:** warning
- **Domain:** render
- **Suppressible:** yes

**Message template:**

```
Chart {chart_id!r}: {truncation_count} spark-bar label(s) were truncated; the label column is too narrow to show the full text.
```

**Fix:** Widen the chart or increase the label column width in the chart style.

Fires when one or more spark-bar row labels are truncated because the label column is too narrow to fit the full text. One warning fires per chart with any truncated labels.

### WARN-STATIC-PAGINATION-CAPPED: Static export stopped short of every table page

- **Level:** warning
- **Domain:** render
- **Suppressible:** yes

**Message template:**

```
Table {chart_id!r}: static export pre-rendered {rendered_pages} of {total_pages} pages; rows past page {rendered_pages} are not in this file.
```

**Fix:** Reduce the row count, raise style.pagination.page_rows so fewer pages are needed, or view the table on an interactive host (dct serve, Cloud) instead of a static export.

Fires when a static export (dct render --format html/svg) has a table with more pages than the renderer will pre-draw. A static export ships no JS runtime that can ask a server for another page, so every page's rows are pre-rendered into the artifact as toggle groups; left uncapped, that makes file size scale with total row count instead of page size. Past the cap, the renderer stops pre-rendering; the artifact shows only the first N pages, and states so in the exported file itself.

### WARN-TABLE-COLUMNS-OVERFLOW: Table is wider than its dashboard slot

- **Level:** warning
- **Domain:** render
- **Suppressible:** yes

**Message template:**

```
Table {chart_id!r} overflows its slot: needed {needed_width:.0f}px but only {available_width:.0f}px available.
```

**Fix:** Widen the table's dashboard slot, reduce the number of columns, or add explicit column widths to control how the table distributes its available space.

Fires when a table needs more width than the slot it was given. A table sizes each column to its minimum readable width; when those widths sum past the available width, the renderer widens the whole table past its slot, so in a dashboard it spills over its neighbor or is clipped, printing columns on top of each other.

### WARN-TABLE-CRAMPED: Table columns were cramped under their width demand

- **Level:** warning
- **Domain:** render
- **Suppressible:** yes

**Message template:**

```
Table {chart_id!r} is cramped: {wrapped_headers} of {column_count} column headers wrapped onto a second line. Columns need {needed_width:.0f}px but have {available_width:.0f}px.
```

**Fix:** Set the board's style.frame.width to about {suggested_width:.0f}px, widen the table's slot, drop columns, or set explicit pixel column widths.

Fires when a table still fits its box widthwise but only because the renderer degraded it: the columns were divided into less than their content demanded and headers were forced onto a second line. Distinct from WARN-TABLE-COLUMNS-OVERFLOW, which is the physical case where columns cannot fit at all and the table paints past its slot; from WARN-TABLE-TEXT-TRUNCATED, which fires only once text is actually cut with an ellipsis; the last rung of the same ladder; and from WARN-TABLE-PAGE-SQUEEZED, the height axis, where the slot cuts the rows-per-page down and the fix is to grow the slot rather than the width.

### WARN-TABLE-PAGE-SQUEEZED: Layout slot forced a smaller table page than the table asked for

- **Level:** warning
- **Domain:** render
- **Suppressible:** yes

**Message template:**

```
Table {chart_id!r}: the layout slot fits {drawn_rows} of the {page_rows} rows a page holds ({total_rows} rows total); the rest moved onto later pages.
```

**Fix:** Give the tile more height (layout height:, a taller row, or fewer siblings sharing the row), or set style.pagination.page_rows to the page size you actually want so the sizer reserves room for it.

Fires when a table's layout slot is shorter than the height the sizer reserved for it, so the paginator draws fewer rows per page than the table's own pagination settled on. The two estimates are computed independently: layout_sizing._get_table_height_from_data reserves the slot, table._largest_safe_page_rows decides what fits, and when they disagree the paginator wins in silence: the render exits 0 and the export photographs as a faithful table while showing a fraction of its rows. Paginating because the data is genuinely longer than the page is not this warning; only a page cut down by the slot is.

### WARN-TABLE-TEXT-TRUNCATED: Table column header or cell text was truncated

- **Level:** warning
- **Domain:** render
- **Suppressible:** yes

**Message template:**

```
Chart {chart_id!r}: column {authored_field!r} has truncated text: {truncation_count} value(s) were cut with an ellipsis.
```

**Fix:** Widen the column, shorten the values, or increase the chart width.

Fires when a table column header or one or more cell values are clipped with an ellipsis because they exceed the column width. One warning fires per column that has any truncation.

### WARN-TEMPORAL-SINGLE-POINT: Temporal line or area chart has only one data point

- **Level:** warning
- **Domain:** render
- **Suppressible:** yes

**Message template:**

```
Chart {chart_id!r} ({chart_type}): temporal x-axis has exactly one data point; a one-point line/area conveys no trend.
```

**Fix:** Widen the date filter to include more time periods, or switch to a KPI or stat tile if a single-point value is intentional.

Fires on line and area charts where the x-axis is temporal and the query result has exactly one row. A one-point line is rendered as a single dot; a one-point area is a vertical line. Both render but convey nothing about a trend; this almost always means the date filter is too narrow.

### WARN-TOO-MANY-COLOR-CATEGORIES: Color encoding has more categories than the palette can distinguish

- **Level:** warning
- **Domain:** render
- **Suppressible:** yes

**Message template:**

```
Chart {chart_id!r}: color encoding on {field!r} yields {count} distinct series; the palette only has {max_categories} distinct colors before recycling.
```

**Fix:** Reduce the number of color categories by grouping small values into 'Other', or filter the data to the most significant categories.

Fires when a categorical color encoding has more distinct values than the palette can distinguish; colors recycle and the legend becomes unreadable. Gated on the Vega-Lite color encoding type so a continuous (quantitative) color gradient never trips it. A wide chart (y: [a, b]) is counted by the series its fold renders -- the measures, crossed with the color: dimension's values when one is authored.

### WARN-TOO-MANY-X-CATEGORIES: x-axis has too many distinct values to read

- **Level:** warning
- **Domain:** render
- **Suppressible:** yes

**Message template:**

```
Chart {chart_id!r}: x field {field!r} has {count} distinct values; labels collide and marks are too thin to read (limit: {max_categories}).
```

**Fix:** Filter to the top N categories by value, roll up to a coarser grouping, or switch to a scrollable table for wide categorical data.

Fires when a categorical (nominal/ordinal) x-axis has more distinct values than fit legibly; labels collide and the marks are too thin to read. For a bar chart, also fires on a temporal x-axis: bars still draw one band per distinct x value even where the density gate has moved bucketed temporal data off the ordinal scale. On that axis the warning is about band width only: a temporal axis thins its own tick labels, so nothing is claimed about label collision, and the fix is to widen the chart, roll up to a coarser time grain, or switch to a line chart. Never fires on a quantitative axis, or on a temporal axis for line/area/scatter charts, where a dense axis is a continuous draw, not a crowded band.

### WARN-UNREFERENCED-CHART: Chart is defined but not placed in any layout

- **Level:** warning
- **Domain:** compile
- **Suppressible:** yes

**Message template:**

```
Chart {chart_id!r} is defined but not referenced in any layout (rows/cols/grid/tabs). It will not appear in the rendered dashboard.
```

**Fix:** Add the chart to a layout block, or delete it if it is no longer needed.

Fires at compile time for charts that are defined somewhere in the board tree but never placed in any layout (rows/cols/grid/tabs). The board still compiles because content-only boards are valid; this warning surfaces lazy authoring: an author defined a chart and forgot to display it. Emitted by `compile/validate/board_warnings.py`.

### WARN-VALUE-LABELS-CROWD-WIDTH: Value labels are wider than their per-mark slot

- **Level:** warning
- **Domain:** render
- **Suppressible:** yes

**Message template:**

```
Chart {chart_id!r}: widest value label is {label_width:.0f}px but each mark only has {slot_width:.0f}px; labels will overflow and collide with neighbors.
```

**Fix:** Shorten the number format (e.g. use SI suffix `.2~s` instead of full precision), reduce the number of labeled marks, or widen the chart.

Fires when a chart's value labels are wider than the horizontal room each one gets. Value labels are drawn at the mark, fixed size, with no adaptive avoidance, so they are the label kind that genuinely overflows. The check uses the panel's real rendered width and font metrics; it fires exactly when the widest label is wider than its slot.

### WARN-WIDE-MEASURE-LABEL-COLLISION: Two wide y: measures humanize to the same legend label

- **Level:** warning
- **Domain:** render
- **Suppressible:** yes

**Message template:**

```
Chart {chart_id!r} authors y: measures {measures}, which all show the label {label!r}. Each is now shown under its own column name instead.
```

**Fix:** Author distinct `y:` column names, or rename one of them in the query so they no longer produce the same label.

Fires when two or more measures in a wide chart's y: list produce the same legend and axis label. For example, `churn_pct` and `churn_percent` both read `churn (%)`. Each colliding measure is shown under its own column name instead, keeping them as separate series. Other measures in the list are unaffected.

### WARN-Y-ENCODING-MOSTLY-NULL: Y-encoding field is mostly NULL in the query result

- **Level:** warning
- **Domain:** render
- **Suppressible:** yes

**Message template:**

```
Chart {chart_id!r}: y field {field!r} is {null_pct:.0%} NULL across {row_count} rows.
```

**Fix:** Check for a broken join or a nullable source column. A COALESCE or WHERE clause may be needed to filter the empty rows.

Fires on any chart where the y-encoding field is more than 50% NULL in the query result rows. Mostly-empty visual marks with no explanation usually indicate a broken join or a nullable source column. NULL-only differs from NULL+zero: zero is a valid measurement.

## layout


### WARN-ADJACENT-TEXT-ROWS: Consecutive text-only rows each flow into their own columns

- **Level:** warning
- **Domain:** compile
- **Suppressible:** yes

**Message template:**

```
{count} text-only rows in a row: each is measured and flowed on its own, so the column grid restarts at every one; the column edges do not line up, and whichever block half-fills its last column leaves a gap mid-page.
```

**Fix:** Merge them into one `- text:` block. Prose in a single block flows through one set of columns and fills them evenly; the headings that separated the rows still separate the sections inside it.

Fires when two or more consecutive `rows:` items are body prose and nothing else. Every prose block picks its own column count from its own line count and balances its own lines, with no flow between blocks, so stacking them renders unrelated grids rather than one continuous passage. One diagnostic per run, marking the second row of it; a run of four rows is one authoring decision, not three. A `title:` on the row does not exempt it: `- title:` + `text:` is how a section of prose is written, and two of those fragment the same way, so the fix is to merge them and let the later titles become headings inside the merged block. Four shapes do not participate, and each of them also ends a run rather than being skipped over; a row between two passages is something the author put there. They are: a row holding a layout of its own (a section, not a block of prose); a row carrying anything only a slot can carry: `style:`, `visible:`, a `details:` disclosure, an authored height, since a merged block has one of each and merging would have to discard one; a row with no flowing prose in it, a markdown table or a code block on its own, because merging one of those into a prose column squeezes it to the measure; and prose too short to reach a second column, which is one column wide at any board width and so cannot misalign against anything; a caption rather than a passage. A `cols:` layout never fires: prose side by side is an authored spread. A row imported from another file or generated by a `foreach` is not reported either; it has no authored coordinates to mark, and the merge it would ask for is not the author's to make here. Emitted by `compile/validate/board_warnings.py`.

### WARN-FLAT-COLS-UNSIZED-OVERFLOW: Flat cols: row has too many unsized non-KPI cells

- **Level:** warning
- **Domain:** compile
- **Suppressible:** yes

**Message template:**

```
cols: has {growable_count} unsized non-KPI cells (max recommended {max_unsized}). Each inherits the row's full vertical budget, so a wide flat row makes row height unpredictable. KPI cards are height-stable and don't count.
```

**Fix:** Nest cells into sized rows-in-cols groups instead, e.g.:
  cols:
  - width: "17%"
    rows:
      - kpi_a
      - kpi_b
  - width: "55%"
    rows:
      - hero

Fires when a flat `cols:` row has more unsized non-KPI cells than the recommended limit. Each unsized cell inherits the row's full vertical budget, making row height unpredictable with many columns. KPI cards are carved out because they are height-stable. Emitted by `compile/validate/authoring_warnings.py`.

## queries


### WARN-COLUMN-CHECK-UNAVAILABLE: Column check unavailable on this adapter

- **Level:** warning
- **Domain:** execute
- **Suppressible:** yes

**Message template:**

```
Column check unavailable on {adapter_type} ({mechanism} validates the query but returns no result schema).
```

**Fix:** Chart column checks need a schema-bearing mechanism (DuckDB, BigQuery dry run). Verify chart columns against a sample run, e.g. dct query <board> <name>.

Fired during `dct validate --warehouse` when the adapter can validate that the query is accepted by the warehouse but cannot return a result schema, so chart channel column checks are skipped. Use a DuckDB or BigQuery source for full column verification.

### WARN-DBT-MANIFEST-MISSING: Queries use dbt macros but no manifest was found

- **Level:** warning
- **Domain:** execute
- **Suppressible:** yes

**Message template:**

```
Queries use {kind} but no dbt manifest was found (looked for {paths}); refs were not validated.
```

**Fix:** Run 'dbt parse' in the dbt project.

Fired once per validate run and once per board render when one or more queries call ref() or source() but no manifest is present at any of the looked-for paths. The refs were not checked against the manifest. Run 'dbt parse' to generate target/manifest.json.

### WARN-DBT-MODEL-COLUMNS-UNRESOLVED: A dbt model's output columns could not be derived statically

- **Level:** warning
- **Domain:** execute
- **Suppressible:** yes

**Message template:**

```
Query {query_name!r} reads dbt model {model!r}, whose output columns could not be derived from its SQL ({reason}); column references against it were not checked.
```

**Fix:** The reason names what blocks static derivation (a `SELECT *` wants explicit projections; an unaliased cast or expression wants an alias; seeds and snapshots are never derivable); the columns can always be verified with `dct validate --warehouse` after `dbt run`.

Fired when a board query reads a dbt model whose output columns cannot be derived statically from its manifest SQL: a `SELECT *`, a macro in projection position, a snapshot (dbt injects meta columns at build time), or SQL that does not parse. Reported rather than silently skipped: an unchecked column reference is not a verified one.

### WARN-DBT-QUERY-COLUMNS-INDETERMINATE: A query's dbt column references could not be determined

- **Level:** warning
- **Domain:** execute
- **Suppressible:** yes

**Message template:**

```
Query {query_name!r} uses dbt ref()/source() but the columns it reads could not be determined ({reason}); they were not checked against the dbt models.
```

**Fix:** The reason names what blocks the analysis (a `SELECT *` wants explicit columns; a templated identifier is decided at render time); the query can always be verified with `dct validate --warehouse` after `dbt run`.

Fired when a board query that calls ref()/source() cannot be statically analyzed for the (table, column) pairs it consumes: a `SELECT *`, a templated identifier, an ambiguous unqualified column, or SQL that does not parse. The model-column drift check makes no claim either way for such a query; this warning keeps that gap visible instead of passing it in silence. Queries with no dbt calls are out of scope; the SQL lint tiers own those.

### WARN-FANOUT-RISK: Join may multiply rows beyond chart aggregation

- **Level:** warning
- **Domain:** query
- **Suppressible:** yes

**Message template:**

```
Query {query_name!r}: {message}
```

**Fix:** Add a GROUP BY or aggregation in the query to collapse the duplicate rows before they reach the chart.

Fires when query validation detects a join that may multiply rows beyond what the chart's aggregation can recover. When a cached relationship context (super-schema profiles) is available, severity is calibrated against known multiplicities. Emitted by `validate_compiled_queries` for every named query.

### WARN-MISSING-JOIN-PREDICATE: Join is missing a predicate and may produce a cross join

- **Level:** warning
- **Domain:** query
- **Suppressible:** yes

**Message template:**

```
Query {query_name!r}: {message}
```

**Fix:** Add an ON clause to the join to specify how the tables relate.

Fires when query validation detects an implicit cross join or an explicit CROSS JOIN without a predicate. These almost always indicate a missing ON clause and produce wildly fanned-out result sets. Emitted by `validate_compiled_queries` via the query validator.

### WARN-PARSE-ERROR: SQL query could not be parsed for semantic validation

- **Level:** warning
- **Domain:** query
- **Suppressible:** yes

**Message template:**

```
Query {query_name!r}: {message}
```

**Fix:** Check the SQL syntax. The query may still execute, but semantic validation (fanout detection, reaggregation) cannot run on unparseable SQL.

Fires when a SQL query cannot be parsed as a structured AST. The query may still execute against the warehouse; this warning surfaces that semantic validation (fanout, reaggregation) cannot run on unparseable SQL. Emitted by `compile()` for authored queries whose dialect resolves and whose failure carries a specific position.

### WARN-REAGGREGATION: Aggregation applied on top of an already-aggregated input

- **Level:** warning
- **Domain:** query
- **Suppressible:** yes

**Message template:**

```
Query {query_name!r}: {message}
```

**Fix:** Refactor the query to aggregate only once at the correct level, or verify the double-aggregation is intentional.

Fires when query validation detects aggregation applied on top of an already-aggregated input (e.g. SUM(SUM(...)) patterns or aggregation over a query result that itself aggregates). The result is usually not what the author intended. Emitted by `validate_compiled_queries` via the query validator.

Looker's symmetric aggregate is exempt. Migrated Looker queries read a measure across a fan-out join by packing it with a hash of the dedup key, `SUM(DISTINCT ...)`-ing so duplicate keys collapse, then subtracting a second `SUM(DISTINCT hash-only)`: an identity over a per-key value, not a second aggregation. Both halves of that subtraction must be present for the exemption to apply, so a plain `SUM(DISTINCT already_summed_column)` still warns.

### WARN-WAREHOUSE-CHECK-UNAVAILABLE: Warehouse check unavailable on this adapter

- **Level:** warning
- **Domain:** execute
- **Suppressible:** yes

**Message template:**

```
Query '{name}' was not checked: {reason}.
```

**Fix:** The query is unverified, not verified. Check it against a source with a check mechanism (DuckDB, BigQuery, Postgres, Redshift, Snowflake), or run it directly, e.g. dct query <board> <name>.

Fired during `dct validate --warehouse` when a query could not be checked without running it at full cost: the adapter offers no mechanism (DESCRIBE, EXPLAIN, or a dry run), the query composes another query's cached result, or the warehouse was never reached. The query is reported as unchecked rather than valid: nothing inspected the SQL. `--warehouse` will never run the query itself to find out.

## variables


### WARN-REDUNDANT-AUTHORED-DEFAULT: Variable field is explicitly set to its default value

- **Level:** warning
- **Domain:** compile
- **Suppressible:** yes

**Message template:**

```
Variable {var_name!r} explicitly sets {field_name} to the default value {default_value!r}.
```

**Fix:** Omit `{field_name}:` when the default is intended.

Fires when a variable explicitly sets a field to its default value. The explicit setting is redundant and adds noise without changing behavior. Emitted by `compile/validate/authoring_warnings.py`.

### WARN-REDUNDANT-AUTHORED-LABEL: Variable label is the same as the inferred display name

- **Level:** warning
- **Domain:** compile
- **Suppressible:** yes

**Message template:**

```
Variable {var_name!r} sets label to {authored_label!r}, which is already inferred from the variable name.
```

**Fix:** Omit `label:` unless the display label should differ from the inferred name.

Fires when a variable's authored `label:` is exactly the same as the label dbt charts would infer from the variable name (title-cased, underscores to spaces). The explicit label is redundant and can be omitted. Emitted by `compile/validate/authoring_warnings.py`.
