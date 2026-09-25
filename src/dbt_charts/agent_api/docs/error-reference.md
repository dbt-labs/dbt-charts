# dbt charts Error Reference


Auto-generated from `dbt_charts.core.diagnostics.REGISTRY`. Every `ERR-*` code dbt charts can raise, grouped by docs topic.


## board


### ERR-EXTENDS-UNRESOLVED: extends entry names neither a theme nor a board

- **Level:** error
- **Domain:** compile
- **Suppressible:** no

**Message template:**

```
Unresolvable `theme:`/`extends:` value {entry!r}: not a built-in theme, and no board by that name at the project root. Use a built-in theme, a project-root board name, or a relative path such as `./base.yaml`. Available built-in themes: {available}.
```

Fired by the extends layer when a board's `extends:` entry resolves to nothing: not a built-in theme, and no matching `.yaml`/`.yml` at the project root. This is the project lane's counterpart to ERR-UNKNOWN-THEME; it fires only where the board lookup was actually attempted, so it can offer that lookup as a fix.

### ERR-EXTRA-FIELD: Unknown field in board YAML

- **Level:** error
- **Domain:** compile
- **Suppressible:** no

**Message template:**

```
Unknown field {field_path!r} in board YAML. Remove it or check the schema for supported keys.
```

Fired when the board YAML contains a field name that the schema does not recognize. Remove the unknown field or refer to the YAML reference for the supported keys.

### ERR-INVALID-DEFAULT-THEME: Invalid theme name in configuration

- **Level:** error
- **Domain:** serve
- **Suppressible:** no

**Message template:**

```
{source} ({theme!r}) is not a valid theme name. Available built-in themes: {available}.
```

Fired when the configured default theme name is not a recognized built-in theme. Check the available theme names and correct the configuration.

### ERR-META-SCHEMA: meta.yml contains an unknown or invalid field

- **Level:** error
- **Domain:** compile
- **Suppressible:** no

**Message template:**

```
meta.yml schema error: {message}. Check that all keys are valid board fields.
```

Fired when a meta.yml file contains a field that is not recognized by the board schema, or a field with an invalid value. Check that all keys match the supported board fields and remove any extras.

### ERR-UNKNOWN-THEME: Theme name is not a built-in theme

- **Level:** error
- **Domain:** compile
- **Suppressible:** no

**Message template:**

```
Unknown theme {theme!r}. Available built-in themes: {available}.
```

Fired during board validation when `theme:`, or a plain (non-path) `extends:` entry, names something that is not a built-in theme. Without it the name is dropped and the board renders on the default theme with no sign the request was ignored. Raised at positions where nothing resolves the entry as a board: a standalone or in-memory compile, and any nested board. A project board compiled through its file gets ERR-EXTENDS-UNRESOLVED from the extends layer instead, which can also report the board lookup it tried.

### ERR-VALIDATION-FIELD: Board YAML field failed Pydantic validation

- **Level:** error
- **Domain:** compile
- **Suppressible:** no

**Message template:**

```
Field {field_path!r}: {pydantic_msg}
```

Fired when a board YAML field fails Pydantic's type or constraint validation, or a normalize-stage rule on one field that Pydantic cannot express (a cross-field exclusion, a root-only constraint). The message carries the specific error. Check the field value against the schema.

### ERR-WRONG-SHAPE: Field expects a mapping but got a scalar

- **Level:** error
- **Domain:** compile
- **Suppressible:** no

**Message template:**

```
Field {field_path!r} expects a mapping, not a scalar. Provide a YAML block with keys: {available_keys}.
```

Fired when a board YAML field that expects a mapping (nested object) receives a plain scalar value instead. Provide a YAML block with the appropriate keys.

## charts


### ERR-AREA-ENCODING-SWAPPED: Area chart x/y encoding looks swapped

- **Level:** error
- **Domain:** compile
- **Suppressible:** no

**Message template:**

```
Chart {chart_id!r} (area): {reason} Area charts always plot x as the dimension and y as the value; there is no orientation knob to rotate an area chart, so a swapped x/y silently bakes a broken axis.
```

Fired when an area chart's encoding looks incorrect: either y is non-numeric (should be the measure), or x is a numeric measure with stack enabled. Area charts always plot x as the dimension and y as the value; there is no orientation knob.

### ERR-AREA-LOG-SCALE-INDEPENDENT-MULTIPLES: Log-scale area and independent-scale multiples are incompatible

- **Level:** error
- **Domain:** compile
- **Suppressible:** no

**Message template:**

```
Chart {chart_id!r} (area): axis_y.scale.type: log with multiples scale: independent is not supported: the explicit domain area bakes on a log scale (to avoid Vega-Lite's degenerate log-area rendering) is computed once from every panel's data combined, so it would apply the same domain to every panel regardless of scale: independent. Use multiples scale: shared, or drop the log scale.
```

Fired when an area chart authors both `axis_y.scale.type: log` and `multiples: {scale: independent}`. Area on a log scale needs an explicit baked domain to avoid Vega-Lite's degenerate rendering (see ERR-AREA-STACKED-LOG-SCALE-NOT-SUPPORTED's sibling note), but that domain is necessarily one shared value; baking it would contradict `scale: independent`'s promise of a per-panel domain, and suppressing it would silently reintroduce the degenerate rendering. Use `scale: shared`, or drop the log scale.

### ERR-AREA-STACKED-LOG-SCALE-NOT-SUPPORTED: Stacked area and log scale are incompatible

- **Level:** error
- **Domain:** compile
- **Suppressible:** no

**Message template:**

```
Chart {chart_id!r} (area): `style.stack: {stack}` with axis_y.scale.type: log is not supported: a stacked band's top encodes a cumulative sum, which a log scale can't represent (the baked domain would be computed from unstacked values and clip the real stacked extent). Use `stack: none`, or drop the log scale.
```

Fired when a stacked area chart (`style.stack: zero/normalize/center`) is combined with `axis_y.scale.type: log`. A cumulative stack top is meaningless on a log scale. Use `stack: none`, or drop the log scale.

### ERR-AREA-STACKED-MARK-STYLE-CLEARED: marks.area.stacked was cleared to null

- **Level:** error
- **Domain:** compile
- **Suppressible:** no

**Message template:**

```
Chart {chart_id!r} (area): `marks.area.stacked` resolved to null. Stacked and single-series area charts both need this recipe -- a single-series area takes it in full even when the chart itself isn't stacked -- but a board or chart in this chart's style cascade authored `marks.area.stacked: null` explicitly, which clears the inherited theme default instead of leaving it alone. Remove that null override.
```

Fired when `marks.area.stacked` resolves to null after the style cascade. Every built-in theme declares this key, so the only way to reach null is a board or chart explicitly authoring `marks.area.stacked: null` -- which clears the inherited value rather than leaving it untouched. Remove the null override.

### ERR-AREA-STACKED-STROKE-INCOMPLETE: marks.area.stacked.stroke is missing cap or join

- **Level:** error
- **Domain:** compile
- **Suppressible:** no

**Message template:**

```
Chart {chart_id!r} (area): `marks.area.stacked.stroke` must declare both `cap` and `join`. Stacked and single-series area charts replace `marks.line.stroke` with it wholesale, so a property it omits is dropped rather than inherited and the edge falls back to SVG butt/miter, spiking each vertex. Set both explicitly (`cap: butt` for the renderer's own default).
```

Fired when the area stacked recipe's stroke is missing a cap or a join, usually because a board or chart authored one of them as null. The recipe's stroke replaces the top-edge line stroke wholesale rather than merging into it, so an omitted property is not inherited. Set both cap and join explicitly.

### ERR-AXIS-COLUMN-REQUIRES-TABULAR-FONT: A column-forming quantitative axis needs a tabular label font

- **Level:** error
- **Domain:** compile
- **Suppressible:** no

**Message template:**

```
Chart {chart_id!r}: axis label font {family!r} does not guarantee tabular figures, but its tick ladder compacts to a shared magnitude and forms a column; the digits only stack into a column when every digit shares one advance, which a proportional board does not give, so the column misaligns silently. Set a tabular family (e.g. 'dbt Sans Tabular') on style.axis_y.labels.font.family for this chart, or on charts.axis_quantitative.labels.font.family to cover every chart.
```

Fired at resolve when a column-forming quantitative axis (a vertical ruler) resolves a shared magnitude scale but its label font is not vendor-registered as tabular. Ticks that carry no magnitude suffix are padded to the width of the one that does, so their digits stack under it; that column only holds when every digit shares a single advance, which is what a tabular board guarantees and a proportional one does not. A horizontal ruler is exempt (its ticks form no column) regardless of font.

### ERR-BAR-DUPLICATE-ROWS: Bar chart data has duplicate rows that require aggregation

- **Level:** error
- **Domain:** render
- **Suppressible:** no

**Message template:**

```
{chart_type} chart {chart_id!r} requires pre-aggregated data with at most one row per plotted key ({field_list}). Found duplicate rows for {duplicate_preview}. Aggregate in the query before rendering.
```

Fired when a bar, horizontal-bar, or grouped-bar chart receives data with more than one row per plotted key. These chart types require pre-aggregated data; aggregate in the query before rendering.

### ERR-BAR-LOG-SCALE-NOT-SUPPORTED: Log scale is not supported on bar charts

- **Level:** error
- **Domain:** compile
- **Suppressible:** no

**Message template:**

```
Chart {chart_id!r} (bar): axis_y.scale.continuous.type: log is not supported: a bar's length encodes magnitude from zero, which a log scale makes meaningless. Use axis_y.scale.continuous.type: symlog on the bar chart, or switch to a line or area chart for a log scale.
```

Fired when a bar chart's y axis is set to `axis_y.scale.continuous.type: log`. A bar's length encodes magnitude from zero, which a log scale makes meaningless. Use `axis_y.scale.continuous.type: symlog` on the bar chart, or switch to a line or area chart for a log scale.

### ERR-BAR-Y-NOT-NUMERIC: Bar chart y column is not numeric

- **Level:** error
- **Domain:** compile
- **Suppressible:** no

**Message template:**

```
Chart {chart_id!r} (bar): y column {y_field!r} is not numeric. Bar charts always plot x as the category and y as the measure, regardless of orientation; use a numeric column for y.
```

Fired when a bar chart's `y:` column contains non-numeric data. Bar charts always use y as the measure axis regardless of orientation; use a numeric column for y.

### ERR-BAR-Y-START-KIND: Bar chart y and y_start are different kinds

- **Level:** error
- **Domain:** compile
- **Suppressible:** no

**Message template:**

```
Chart {chart_id!r} (bar): column {y_field!r} is {y_kind} but column {y_start_field!r} is {y_start_kind}. A bar's start and end, and every bar on the chart, sit on one value axis: make them all numeric, or all dates.
```

Fired when a bar's `y:` and `y_start:` columns (or a bar layer's, against the chart's own) are not the same kind. Both must be numeric, or both dates.

### ERR-BAR-Y-START-NULL: Bar chart y_start column has an empty cell

- **Level:** error
- **Domain:** compile
- **Suppressible:** no

**Message template:**

```
Chart {chart_id!r} (bar): y_start column {y_start_field!r} is empty in row {row}. Every row needs a start; write 0 for a bar that starts at zero.
```

Fired when a bar chart's `y_start:` column holds a NULL. A missing start is never read as zero: have the query write an explicit 0 for a bar that starts at zero.

### ERR-CATEGORY-COLOR-PALETTE-EXHAUSTED: An authored category_colors field has more values than the palette has swatches

- **Level:** error
- **Domain:** compile
- **Suppressible:** no

**Message template:**

```
{value_count} values for `{field}`, but the board's categorical palette has {swatch_count} swatches. Pick a wider palette, or reduce the distinct count `style.charts.category_colors.{field}` has to cover.
```

Fired when an authored `style.charts.category_colors.<field>` field has more distinct values than the board's categorical palette has swatches. Two categories must never share a swatch, so an authored field raises instead of silently declining to bind (an unauthored field just declines). Pick a wider palette, or reduce the distinct count the field has to cover.

### ERR-CATEGORY-COLOR-PIN-DUPLICATE: Two category_colors pins name the same color

- **Level:** error
- **Domain:** compile
- **Suppressible:** no

**Message template:**

```
`style.charts.category_colors.{field}` pins both `{first_value}` and `{second_value}` to `{color}`: two categories cannot share a swatch. Give one of them a different color.
```

Fired when two values pinned under `style.charts.category_colors.<field>` resolve to the same color (case-insensitively), which would seat two categories on one swatch. Give one of the pins a different color.

### ERR-CHART-PAINTED-NO-MARKS: Chart received rows but painted no marks

- **Level:** error
- **Domain:** render
- **Suppressible:** no

**Message template:**

```
Chart {chart_id!r} received {row_count} row(s) but every mark it drew has zero width or height; check whether the x/y fields and scale types match the data's actual shape.
```

Fired when a plotting-family chart's query returns at least one row but the rendered SVG contains no mark with visible extent: every bar, line, area, point, wedge, or shape it drew is degenerate. `WARN-QUERY-RETURNED-ZERO-ROWS` covers the honest empty case (no rows); this covers the dishonest one: rows arrived, the renderer just didn't paint anything visible with them.

### ERR-COLOR-NULL-SERIES: Color column contains NULL values

- **Level:** error
- **Domain:** render
- **Suppressible:** no

**Message template:**

```
{chart_type} chart {chart_id!r} has {null_rows} row(s) with a NULL value in its color column {color_field!r}. A NULL category cannot be painted or named in the legend, but its rows still occupy stack space; the chart would read as bars floating off the baseline. Give every row a category in the query (e.g. COALESCE({color_field}, 'Unknown')).
```

Fired when the column bound to a chart's `color` channel contains NULL values. The renderer cannot assign a NULL a palette slot or a legend entry, so the series would consume stack space while being invisible and unattributable. Fix the grain in the query: the most common cause is a `CASE` with no `ELSE`, or an `ELSE` that passes the raw column through unchanged.

### ERR-CONCAT-OVERSHOOT-NONPOSITIVE: Overshoot correction produced a non-positive pane width

- **Level:** error
- **Domain:** render
- **Suppressible:** no

**Message template:**

```
Overshoot correction produced a non-positive pane width ({new_w:.1f}px): pane width {orig_w:.1f}px minus overshoot {overshoot:.1f}px. The chart content (title, subtitle, axis labels, series labels, or legend) is wider than the available canvas ({target_width:.1f}px).
```

Fired when the overshoot correction algorithm for a concatenated layout produces a non-positive pane width. The chart content (title, subtitle, axis labels, series labels, or legend) is wider than the available canvas. Series labels come from the column bound to `color:`, and are the usual cause when that column holds long text.

### ERR-ENDPOINT-LABELS-CENTER-STACK: stack: center is not supported for horizontal endpoint labels

- **Level:** error
- **Domain:** render
- **Suppressible:** no

**Message template:**

```
Chart {chart_id!r}: stack: center is not supported for horizontal stacked bar endpoint labels — the rail anchors on the cumulative (0..Σ) axis, which a center stack (-Σ/2..+Σ/2) does not have. Use stack: zero, or set style.endpoint_labels.visible: false on this chart.
```

Fired when a horizontal stacked bar chart uses `stack: center` while its endpoint-label rail is forced on. The rail anchors each label at its segment's cumulative midpoint from zero; a center stack has no such axis. Use `stack: zero`, or turn the rail off with `style.endpoint_labels.visible: false`.

### ERR-ENDPOINT-LABELS-NEGATIVE-STACK: negative values are not supported for stacked endpoint labels

- **Level:** error
- **Domain:** render
- **Suppressible:** no

**Message template:**

```
Chart {chart_id!r}: negative values are not supported for stacked bar/area endpoint labels — they break the cumulative-midpoint computation. Set style.endpoint_labels.visible: false on this chart.
```

Fired when a stacked bar or area chart's data carries a negative measure while its endpoint-label rail is visible. The rail anchors each label at its segment's cumulative midpoint, a computation that assumes every segment in the stack contributes in the same direction; a negative value crosses zero and breaks it. Turn the rail off with `style.endpoint_labels.visible: false`.

### ERR-ENDPOINT-LABELS-UNORDERABLE-SORT: sort by a non-numeric column cannot be combined with stacked endpoint labels

- **Level:** error
- **Domain:** render
- **Suppressible:** no

**Message template:**

```
Chart {chart_id!r}: chart.sort by {sort_by!r} cannot be combined with stacked bar endpoint labels. The rail places its labels from the order the sorted axis draws, and dbt Charts confirms that order only for a column carrying numeric values. Sort by a measure instead, or set style.endpoint_labels.visible: false on this chart.
```

Fired when a stacked bar chart authors `sort:` by a column that carries no numeric values while its endpoint-label rail is visible. The rail places its labels from the order the sorted axis draws (a vertical rail anchors each series at its own last drawn column, a horizontal one anchors every series on the top row), so it has to know that order before the chart renders. dbt Charts confirms it only for a numeric sort column, and labels placed from the wrong order name series in the wrong place, which is worse than a legend. Sort by a measure, or turn the rail off with `style.endpoint_labels.visible: false`.

### ERR-FORMAT-INVALID: Format spec is not a predefined name, a style.formats alias, or a valid d3-format spec

- **Level:** error
- **Domain:** compile
- **Suppressible:** no

**Message template:**

```
Unknown number format {spec!r} at {field_path}. It is not an engine-predefined format name, not a key in `style.formats`, and is not a valid d3-format spec ({reason} at position {position}).
```

Fired when an authored `format:` string is not one of the engine's predefined format names (e.g. `currency`, `number`, `percent_number`), not a key defined in `style.formats`, and fails to parse as a d3-format spec. Check for typos against the predefined names or your `style.formats` keys, or use a valid d3-format spec (https://d3js.org/d3-format).

### ERR-FORMAT-KIND-MISMATCH: Predefined format name is the wrong kind for this slot

- **Level:** error
- **Domain:** compile
- **Suppressible:** no

**Message template:**

```
Format {spec!r} at {field_path} is not a {kind} format. This slot takes only the {kind} half of the engine's vocabulary: {available}. {escape_hatch}
```

Fired when a predefined format name from one half of the vocabulary lands in a slot that takes the other. `number_format` feeds a quantitative axis and `time_format` a temporal one, so each accepts only its own names; `time_format: currency` resolves to the d3 number spec `$.3~s`, which Vega bakes onto a date axis as garbage tick labels rather than failing. Use `date_short` or a strftime spec (`%b %Y`) for `time_format`; use a number name (`currency`, `number`, `percent`) or a d3 spec for `number_format`. A plain `format:` slot is judged by the column it paints and takes either.

### ERR-FORMAT-NATIVE-IN-VEGA-SLOT: Native formatter used in a Vega-rendered format slot

- **Level:** error
- **Domain:** compile
- **Suppressible:** no

**Message template:**

```
Format {spec!r} at {field_path} is a Python-only native formatter and cannot be used in Vega-rendered slots (axis labels, mark value labels, number_format, time_format, support_table). Use it only in KPI or table format fields. Valid alternatives: {available}.
```

Fired when `percent_number`, `percent_number_delta`, or `percentage_points_delta` appears in a Vega-rendered format slot such as an axis label, mark value-label format, number_format, time_format, or support_table format. These names bypass d3 entirely and are only valid in Python-rendered slots (KPI headline and table cells). For Vega-rendered slots, use a d3 percent spec (e.g. `.1%`) or another predefined name.

### ERR-FORMAT-PREDEFINED-SHADOW: style.formats key shadows an engine-predefined format name

- **Level:** error
- **Domain:** compile
- **Suppressible:** no

**Message template:**

```
Cannot define {spec!r} in style.formats at {field_path}: this name is engine-predefined and cannot be overridden. Choose a project-specific name (e.g. 'revenue', 'arr') for custom format aliases.
```

Fired when a `style.formats` key collides with an engine-owned predefined format name such as `number`, `currency`, or `date_short`. Predefined names resolve via engine rules and cannot be shadowed. Define your custom alias under a different name.

### ERR-GAP-FILL-BUCKET-COLLISION: Two rows collapse to the same gap-fill bucket

- **Level:** error
- **Domain:** render
- **Suppressible:** no

**Message template:**

```
Rows with {x_field!r} values {value_a!r} and {value_b!r} both collapse to the {time_unit!r} bucket {bucket!r}{dim_desc}. Aggregate to {time_unit} grain in the query before rendering.
```

Fired when gap-filling an ordinal bucketed-time axis finds two rows whose x-values fall inside the same bucket: two timestamps on one calendar day under `yearmonthdate`, or a finer-grained series under a coarser authored `time_unit` (monthly rows under `yearquarter`, daily rows under `yearmonth`). A coarser grain places rows in buckets; it does not combine them, and a last-wins merge would silently discard all but one. Aggregate to the bucket grain in the query before rendering.

### ERR-HISTOGRAM-NON-NUMERIC: Histogram x field is not numeric

- **Level:** error
- **Domain:** render
- **Suppressible:** no

**Message template:**

```
Histogram chart {chart_id!r} requires a numeric x field for binning, but {field!r} is {vl_type!r}. Use a quantitative (numeric) column as x.
```

Fired when a histogram chart's x field is not numeric (quantitative). Histograms bin values into ranges, which requires a numeric column. Use a quantitative column as x.

### ERR-HISTOGRAM-PREAGGREGATED: Histogram data looks pre-aggregated, not raw rows

- **Level:** error
- **Domain:** render
- **Suppressible:** no

**Message template:**

```
Histogram chart {chart_id!r} received data where {field!r} forms a gapless run of whole numbers alongside unused numeric column(s) {count_fields}: this looks like pre-aggregated data (one row per bucket, e.g. `GROUP BY {field}`), not the raw, ungrouped rows a histogram bins itself. Vega-Lite would bin and count these already-counted rows again, silently discarding whatever real measure they carry. Use `type: bar` with `x: {field}` and one of {count_fields} as `y` to chart pre-aggregated data instead.
```

Fired when a histogram chart receives data where its x field forms a gapless run of whole numbers alongside an unused numeric column, the shape of already-aggregated, one-row-per-bucket data. Histograms rely on Vega-Lite's own binning + counting over raw, ungrouped rows; pre-aggregated data silently produces a wrong, miscounted histogram instead of erroring. Aggregate in the query and use `type: bar` instead.

### ERR-INPUT-INVALID: Invalid render input

- **Level:** error
- **Domain:** render
- **Suppressible:** no

**Message template:**

```
{message}
```

Fired when the render layer receives input data that fails a structural check. The message carries the specific validation error.

### ERR-KPI-FORMAT-KIND-MISMATCH: KPI support format does not match a temporal value

- **Level:** error
- **Domain:** render
- **Suppressible:** no

**Message template:**

```
KPI chart {chart_id!r} support value is a date/time, but its format {spec!r} is not a date format. Use date_short, a strftime spec via a style.formats alias, or remove format: to use date_short.
```

Fired when a KPI's support.format is a number format (not date_short, time_short, or a style.formats alias resolving to a strftime spec) and support.value is a date/datetime column. support.format is always authored for that one chart, unlike the headline value's format, which can be a board-wide cascade default -- so a mismatch here is never ambiguous.

### ERR-KPI-MULTIROW: KPI query returned more than one row

- **Level:** error
- **Domain:** render
- **Suppressible:** no

**Message template:**

```
KPI chart {chart_id!r} expects exactly 1 row, got {row_count}. Use a query that returns a single row (e.g. SELECT SUM(...) or LIMIT 1).
```

Fired when a KPI chart's query returns more than one row. KPI charts display exactly one value; use a query that returns a single row (e.g. SELECT SUM(...) or LIMIT 1).

### ERR-KPI-TEMPORAL-FORMAT-INVALID: KPI temporal format spec could not be applied

- **Level:** error
- **Domain:** render
- **Suppressible:** no

**Message template:**

```
KPI chart {chart_id!r} could not format value {cell!r} with spec {spec!r}: {reason}
```

Fired when a KPI's resolved date/time format spec is itself invalid (an unknown strftime directive) or the cell's value cannot be parsed as a calendar date/time (e.g. an out-of-range hour in an ISO timestamp string).

### ERR-LABEL-FORMAT-AXIS-MISMATCH: a non-time axis format needs numeric tick values

- **Level:** error
- **Domain:** render
- **Suppressible:** no

**Message template:**

```
style.{setting} ({fmt!r}) cannot be read on this axis: {field!r} holds non-numeric tick labels. {remedy}
```

Fired when `style.axis_x.labels.format`, `style.axis_y.labels.format` or `style.axis_y.mirror.format` is anything but a d3 time spec on an axis whose field resolves to nominal, ordinal or temporal. All three matter: nominal is a plain category column, ordinal covers the date-like buckets (`2024-01`, `Q1 2024`, `FY2024`) that are still band-scale strings, and temporal is a real date/time field. On a band scale Vega coerces every tick to `NaN` rather than failing; on a temporal one it reads the spec as a time spec instead and paints its literal text (`$,.0f`) across the axis. Either way it raises here now. A horizontal bar is the case worth calling out: its `axis_x` addresses the categories, which the rotation draws down the left edge, so an author formatting what looks like the value axis reaches the wrong channel. The measure is `axis_y` in both orientations; on a heatmap the value is the color channel and neither axis carries it; on a scatter dot plot the measure is `axis_x` instead, since the categorical channel there is `axis_y`. Numeric categories on a band scale are NOT gated: they format cleanly, so nothing distinguishes an intended format from a misaddressed one — a temporal axis has no such exemption, since no reading of a number format over dates was ever the author's intent.

### ERR-LABEL-VALUES-INVALID-DATE: labels.values entry is not a valid ISO date

- **Level:** error
- **Domain:** render
- **Suppressible:** no

**Message template:**

```
style.axis_x.labels.values entries must be ISO date or datetime strings (e.g. '2024-01-01') or date/datetime objects; got {value!r}.
```

Fired when an entry in `style.axis_x.labels.values` cannot be parsed as an ISO date or datetime. Use ISO date strings (e.g. '2024-01-01') or date/datetime objects.

### ERR-LABEL-VALUES-NOT-TEMPORAL: labels.values requires a temporal x-axis

- **Level:** error
- **Domain:** render
- **Suppressible:** no

**Message template:**

```
style.axis_x.labels.values isn't usable on {field!r}: {cause}. {remedy}
```

Fired when `style.axis_x.labels.values` is set on an x-axis that can't honor it: either the x-axis values aren't valid ISO dates or datetime objects, or the chart's horizontal-bar categorical axis never applies label filtering regardless of date format.

### ERR-LABELS-FIELD-NOT-FOUND: labels.field names a column not in the query result

- **Level:** error
- **Domain:** render
- **Suppressible:** no

**Message template:**

```
Chart {chart_id!r}: labels.field {field!r} on {source} names a column not present in its data. Available columns: {available}.
```

Fired when `labels.field` names a column that is not present in the query result. `source` identifies which slot fired: the base chart's own labels, or a specific overlay layer (by position, type, and query), since a chart's overlay `layers:` can each carry their own `labels.field`. Check the column name against the actual columns returned by that slot's query.

### ERR-LAYER-AXIS-POSITION-ENDPOINT-LABELS: axis_y.position on a layer cannot be combined with endpoint labels

- **Level:** error
- **Domain:** render
- **Suppressible:** no

**Message template:**

```
Chart {chart_id!r}: style.endpoint_labels.visible is not supported on a layered chart whose layers pin an explicit axis_y.position — the label rail anchors on one shared y-scale, and a dual-axis layer renders on a different one. Remove the per-layer axis_y.position override, or set style.endpoint_labels.visible: false on this chart.
```

Fired when a layered chart's endpoint-label rail is visible (authored or theme-set) while one of its layers pins its own `axis_y.position`. The rail anchors every layer's labels on the base chart's single shared y-scale; a layer with a pinned `axis_y.position` is a genuine dual-axis layer rendering on a different scale, which the rail has no way to represent. Remove the per-layer `axis_y.position`, or turn the rail off with `style.endpoint_labels.visible: false`.

### ERR-LAYER-AXIS-POSITION-ORIENTATION: axis_y.position on a layer needs a vertical base chart

- **Level:** error
- **Domain:** render
- **Suppressible:** no

**Message template:**

```
Chart {chart_id!r}: a layer's `axis_y.position` names a left or right side, and `style.orientation: horizontal` measures along the horizontal axis, whose sides are top and bottom. Drop `axis_y.position`, or set `style.orientation: vertical`.
```

Fired when a layer pins `axis_y.position` on a chart whose base is horizontal. The resolved model always measures on `y` and carries its category on `x`; a horizontal bar paints that same model with the pair swapped, so its measure axis runs horizontally and has no left or right side to pin a second axis to. Whether left/right should map onto bottom/top is a design decision nobody has taken, so this refuses rather than drawing the layer against a side the author did not ask for.

### ERR-LAYER-STEP-ORIENTATION: curve: step on a layer needs a vertical base chart

- **Level:** error
- **Domain:** render
- **Suppressible:** no

**Message template:**

```
Chart {chart_id!r}: a layer's `curve: step` builds its band offset along the horizontal axis, which `style.orientation: horizontal` uses for the measure. Drop `curve: step` on the layer, or set `style.orientation: vertical`.
```

Fired when a layer authors `style.marks.line.curve: step` on a chart whose base is horizontal. The band-aware step transform doubles each row to its band edges and separates them with an `xOffset` scale sized by `bandwidth('x')`: the channel a horizontal base measures on rather than the one it bands. Refused rather than offsetting the layer along an axis that has no bands to measure.

### ERR-LAYERS-AMBIGUOUS-Y-DOMAIN: Chart-level y domain is ambiguous with independent layer scales

- **Level:** error
- **Domain:** compile
- **Suppressible:** no

**Message template:**

```
Chart {chart_id!r} sets axis_y.scale.domain={domain!r} but the layers use independent y scales (left and right sides differ). A chart-level domain is ambiguous when each side has its own scale; set axis_y.scale.domain on the individual layer instead.
```

Fired when a chart sets `axis_y.scale.domain` at the chart level but the layers use independent (split) y scales. A chart-level domain is ambiguous when left and right sides have different scales. Set `axis_y.scale.domain` on the individual layer instead.

### ERR-LINE-Y-NOT-NUMERIC: Line chart y column is not numeric

- **Level:** error
- **Domain:** compile
- **Suppressible:** no

**Message template:**

```
Chart {chart_id!r} (line): y column {y_field!r} is not numeric. Line charts always plot x as the dimension and y as the value; use a numeric column for y.
```

Fired when a line chart's `y:` column contains non-numeric data. Line charts always use y as the value axis; use a numeric column for y.

### ERR-LOG-SCALE-REQUIRES-POSITIVE-DATA: Log scale requires strictly positive data

- **Level:** error
- **Domain:** compile
- **Suppressible:** no

**Message template:**

```
Chart {chart_id!r}: column {y_field!r} has a value <= 0, but axis_y.scale.type: log requires strictly positive data; a log domain is undefined at and below zero. Filter out the non-positive rows, or drop the log scale.
```

Fired when `axis_y.scale.type: log` is used but the y column contains a value ≤ 0. A log domain is undefined at and below zero. Filter out the non-positive rows, or drop the log scale.

### ERR-MAP-LOOKUP-KEY-MISMATCH: Map chart cannot join on mismatched key format

- **Level:** error
- **Domain:** render
- **Suppressible:** no

**Message template:**

```
Map chart {chart_id!r} cannot join lookup field {lookup_field!r} to geo source {geo_source!r} on key {geo_key!r}: the geo source expects {expected_format} keys like {expected_samples}, but the query returned values like {query_samples}.
```

Fired when a map chart's lookup field values do not match the format expected by the geo source. Check that the lookup field uses the same key format (e.g. FIPS codes, ISO country codes) as the geo source.

### ERR-MIRROR-ENDPOINT-LABELS: axis_y.mirror cannot be combined with endpoint labels

- **Level:** error
- **Domain:** render
- **Suppressible:** no

**Message template:**

```
Chart {chart_id!r}: axis_y.mirror is not supported together with endpoint labels; the endpoint-label rail occupies the opposite edge. Use one or the other on this chart.
```

Fired when a chart authors (or its theme sets) `style.axis_y.mirror` while its endpoint-label rail is also visible. Both want the opposite edge from the chart's primary y-axis; the mirrored scale and the label rail can't share it. Distinct from ERR-MULTIPLES-ENDPOINT-LABELS: this code fires when the chart has no `multiples:` at all, so `axis_y.mirror` is the field actually responsible for the collision; a `multiples:` grid's collision (even one that also happens to auto-derive a both-edge axis internally) is reported as ERR-MULTIPLES-ENDPOINT-LABELS instead, since `multiples:` is the field the author wrote.

### ERR-MIRROR-LAYERS: axis_y.mirror is not supported with layers

- **Level:** error
- **Domain:** render
- **Suppressible:** no

**Message template:**

```
Chart {chart_id!r}: axis_y.mirror is not supported on a chart with `layers:`; each layer owns its own y encoding, so there is no single shared y encoding for the mirrored edge to restate. Drop `style.axis_y.mirror` or the `layers:`.
```

Fired when a chart carries `layers:` while `style.axis_y.mirror` is on. Overlay layers move the y encodings onto the layers themselves (the base's own y included), so the composed spec has no shared y encoding for the ghost axis to bind; yet a real y axis paints, so silently skipping the mirror would be a wrong result that looks right. Reported at render rather than by `dct validate` because mirror is cascade-resolved: a theme layer can turn it on for a chart that never authored it.

### ERR-MIRROR-MULTI-SERIES: axis_y.mirror requires a single y field

- **Level:** error
- **Domain:** render
- **Suppressible:** no

**Message template:**

```
Chart {chart_id!r}: axis_y.mirror is not supported on multi-series charts (y = {y}); a mirrored axis restates one shared y-scale, and folded measures have no single scale to restate. Collapse `y:` to one field, or drop `style.axis_y.mirror`.
```

Fired when a chart carries a list-valued `y:` while `style.axis_y.mirror` is on. Mirror draws the same y-scale on both edges of a wide chart, which only means something when there is exactly one measure scale to draw. Reported at render rather than by `dct validate` because mirror is cascade-resolved: a theme layer can turn it on for a chart that never authored it, so the combination is not visible on the authored chart alone.

### ERR-MULTI-Y-COLOR-CONFLICT: Multi-y chart's color: must name a column

- **Level:** error
- **Domain:** compile
- **Suppressible:** no

**Message template:**

```
Chart {chart_id!r} ({chart_type}): y: [...] folds measures into a color series, and color: {color_field!r} is bound to a gradient or conditional scale, which names no series to cross them with. Bind color: to a plain column (each of its values x each measure becomes a series) or drop it.
```

Fired when a bar, area, line, or scatter chart authors y: [a, b] together with a color: that is not a plain series column -- a gradient or a conditional scale. A column composes with the fold: the series become `<value> - <measure>` composites, one per dimension value per measure.

### ERR-MULTI-Y-LAYERS-CONFLICT: Multi-y chart cannot also have layers:

- **Level:** error
- **Domain:** compile
- **Suppressible:** no

**Message template:**

```
Chart {chart_id!r} ({chart_type}): y: [...] folds measures into a color series automatically -- overlay layers: are not supported with multi-y charts. Keep a single y field and overlay the other measures as layers: entries instead.
```

Fired when a bar, area, line, or scatter chart authors both y: [a, b] and layers: at the same time.

### ERR-MULTIPLES-ENDPOINT-LABELS: multiples cannot be combined with endpoint labels

- **Level:** error
- **Domain:** render
- **Suppressible:** no

**Message template:**

```
Chart {chart_id!r}: multiples cannot be combined with endpoint labels; the endpoint-label rail names series for a single panel, and a faceted chart has no single panel for it to sit beside. Set style.endpoint_labels.visible: false on this chart to keep the small multiples, or remove multiples to keep the labels.
```

Fired when a chart authors `multiples:` while its endpoint-label rail is explicitly switched on. The rail names series for one panel; a faceted chart has no single panel for it to sit beside. The shipped default switches the rail off wherever `multiples:` is set (a legend above the panels names the series instead), so this fires only where the rail was asked for by name. Turn off `style.endpoint_labels.visible` to keep the small multiples, or remove `multiples:` to keep the labels.

### ERR-MULTIPLES-FIELD-NOT-FOUND: multiples partition field not found in the query result

- **Level:** error
- **Domain:** compile
- **Suppressible:** no

**Message template:**

```
multiples field(s) {fields} not found in the query result. Available columns: {available}.
```

Fired when `multiples.rows` or `multiples.columns` names a column that is not present in the chart's query result. Check for a typo, or add the column to the query.

### ERR-MULTIPLES-INDEPENDENT-SCALE-MIRROR: multiples with scale: independent cannot use a both-edge mirror axis

- **Level:** error
- **Domain:** render
- **Suppressible:** no

**Message template:**

```
Chart {chart_id!r}: multiples with scale: independent cannot use a both-edge (mirror) y-axis: mirroring a shared scale to both edges is meaningless when each panel has its own scale. Use scale: shared, or set style.axis_y.mirror: false.
```

Fired when a chart authors `multiples: {scale: independent}` together with `style.axis_y.mirror` (bool true or an AxisMirrorStyle format/expr override). Mirroring reflects one shared scale to both edges; meaningless once every panel has its own independent scale. Use `scale: shared`, or turn mirror off.

### ERR-MULTIPLES-LAYER-PARTITION: multiples cannot be combined with an own-query layer that returns the partition field

- **Level:** error
- **Domain:** render
- **Suppressible:** no

**Message template:**

```
Chart {chart_id!r}: multiples cannot be combined with an own-query overlay layer whose query returns the partition field(s) {fields}; Vega-Lite cannot split one inline layer dataset per panel. Remove the partition column(s) from the layer's query to repeat the layer in every panel, or drop multiples.
```

Fired when a chart authors both `multiples:` and a `layers:` entry with its own `query:` whose result includes the partition column(s). Vega-Lite's facet operator only partitions the root dataset, never a layer's own inline dataset, so per-panel layer data cannot be represented. A layer whose query does not return the partition column is unaffected; it repeats identically in every panel, which is correct for a global reference line.

### ERR-MULTIPLES-RESOLVED-WITHOUT-DATA: a faceted chart resolved without data is being rendered with real rows

- **Level:** error
- **Domain:** render
- **Suppressible:** no

**Message template:**

```
Chart {chart_id!r}: multiples is set but this chart was resolved against no data (panel_axes is empty) and is now being rendered against real rows. Re-resolve the chart against its actual data before rendering.
```

Fired when a chart authors `multiples:`, was resolved with an empty query result (baking `panel_axes == ()`, the same N=1 state a non-faceted chart gets), and is then rendered against non-empty rows. Silently falling through would render the chart unfaceted instead of raising on data resolve never saw. Re-resolve the chart against its actual data before rendering.

### ERR-MULTIPLES-ROW-MISSING-PARTITION-FIELD: row is missing a multiples partition field entirely

- **Level:** error
- **Domain:** render
- **Suppressible:** no

**Message template:**

```
Row is missing the multiples partition field {field!r} entirely (it carries: {available}); every row must carry every partition field, even one whose value is null.
```

Fired when a chart is rendered against a row that does not carry a partition column at all, as distinct from carrying it with a null value (a legitimate panel `resolve()` may have baked). A ragged row silently defaulting the missing field to `None` could misroute into that legitimate null panel by accident, so this is checked and raised separately.

### ERR-MULTIPLES-ROW-OUTSIDE-PANEL-AXES: row's multiples value is not among the resolved panel_axes

- **Level:** error
- **Domain:** render
- **Suppressible:** no

**Message template:**

```
Row's {field!r} value {value!r} is not among the resolved panel_axes values {allowed} for this chart's multiples partition.
```

Fired when a chart is rendered against rows whose partition column carries a value `resolve()` never saw: the row-truncated render path can only ever hold a subset of the axis values baked at resolve, never one the axes lack, so this firing means the chart is being rendered against data resolve never saw. Re-resolve the chart against its actual data before rendering.

### ERR-MULTIPLES-SELF-CROSSED: multiples.rows and multiples.columns name the same field

- **Level:** error
- **Domain:** compile
- **Suppressible:** no

**Message template:**

```
multiples.rows and multiples.columns both name {field!r}. Only the diagonal of the resulting grid can ever hold a row; the rest are structurally empty, whatever the data says.
```

Fired when `multiples.rows` and `multiples.columns` name the same column. Crossing a field with itself builds a panel grid where a row of data can only ever land on the diagonal (the row's value matches itself); every other cell in the grid is guaranteed empty. Name a different column for `rows` or `columns`, or drop one of them and keep a single-direction partition.

### ERR-MULTIPLES-SUPPORT-TABLE: multiples cannot be combined with a chart support_table

- **Level:** error
- **Domain:** render
- **Suppressible:** no

**Message template:**

```
Chart {chart_id!r}: multiples is not supported together with a chart support_table; the attached table has no meaning per panel. Use one or the other on this chart.
```

Fired when a chart authors both `multiples:` and a `support_table:`. The attached table is a single per-chart element; once the chart is faceted into a small-multiples grid it has no per-panel meaning. Remove one or the other.

### ERR-MULTIPLES-VALUE-COLLISION: two distinct multiples values stringify to the same panel key

- **Level:** error
- **Domain:** compile
- **Suppressible:** no

**Message template:**

```
multiples field {field!r}: values {previous!r} and {value!r} both stringify to {canonical!r}; rename one to disambiguate.
```

Fired when `multiples.rows` or `multiples.columns` names a column whose distinct values collide once canonicalized to a string panel key (e.g. the string `'1'` and the integer `1`). Merging them into one panel would silently combine two genuinely distinct groups. Rename one value so the two panel keys stay distinct.

### ERR-PALETTE-UNKNOWN: Palette name is not a theme palette role or a shipped palette

- **Level:** error
- **Domain:** compile
- **Suppressible:** no

**Message template:**

```
Unknown palette {name!r} at {field_path}. Palette roles are theme-scoped, resolved only from a theme's `palettes:` block; this name is not a role the active theme binds, and not a shipped dbt charts palette.
```

Fired when an authored `palette:` string names neither a role in the theme's `palettes:` block (`category`, `sequence`) nor a shipped dbt charts palette (`editorial-10`). Roles are theme-scoped; board- and chart-level `style:` cannot author one, only a palette name. Checked in two places: once the theme cascade is complete (a theme's own `palettes:` binding), and once each board/chart is normalized (every other palette field); both are the first point a role name can be told apart from a typo at that scope.

### ERR-PERCENT-RANGE: Percent format received a 0-100-shaped value instead of a ratio

- **Level:** error
- **Domain:** render
- **Suppressible:** no

**Message template:**

```
Value {value!r} passed to percent format {format_spec!r} looks 0-100-shaped, but percent formats expect a 0-1 ratio (0.182, not 18.2). To fix: divide by 100 in SQL so the value is a ratio, or switch to the `percent_number`/`percent_number_delta` formats if the value is already in the 0-100 scale (e.g. 18.2 means 18.2%).
```

Fired when a percent format receives a value that looks like it is already in the 0-100 scale rather than the 0-1 ratio scale that percent formats expect. Divide by 100 in SQL so the value is a ratio, or switch to a `percent_number` format.

### ERR-PIE-NEGATIVE-THETA: Pie chart theta column contains a negative value

- **Level:** error
- **Domain:** render
- **Suppressible:** no

**Message template:**

```
Pie chart {chart_id!r} has {negative_rows} row(s) with a negative value in its theta column {theta_field!r}. A pie slice can't represent a negative share of the whole. Filter or transform the value in the query.
```

Fired when the column bound to a pie or donut chart's `theta` channel contains a negative value. A pie's slices are angles summing to a full circle; a negative theta has no geometric meaning, so it must be filtered or transformed in the query.

### ERR-PIE-NULL-THETA: Pie chart theta column contains NULL values

- **Level:** error
- **Domain:** render
- **Suppressible:** no

**Message template:**

```
Pie chart {chart_id!r} has {null_rows} row(s) with a NULL value in its theta column {theta_field!r}. A pie slice can't represent a missing value. Filter the null rows or give them a real number in the query (e.g. COALESCE({theta_field}, 0)).
```

Fired when the column bound to a pie or donut chart's `theta` channel contains NULL values. A pie slice's angle comes directly from theta, so a NULL cannot be drawn or labeled; fix the grain in the query instead of letting the renderer guess a value.

### ERR-RESOLVED-PIE-DATA-MISMATCH: Resolved pie rows do not match its recording

- **Level:** error
- **Domain:** render
- **Suppressible:** no

**Message template:**

```
Resolved pie {chart_id!r} was finalized from different query rows. Resolve the chart and record its data in the same emission.
```

Fired when replay data differs from the rows used to finalize pie label and attached-table policy. Resolved board artifacts and recordings must come from the same emission.

### ERR-RESOLVED-PIE-WIDTH-MISMATCH: Resolved pie width does not match its render slot

- **Level:** error
- **Domain:** render
- **Suppressible:** no

**Message template:**

```
Resolved pie {chart_id!r} was finalized at width {resolved_width}, not {render_width}. Resolve the chart again for the target slot.
```

Fired when a resolved pie is rendered at a different width from the one used to finalize its label and attached-table layout. Resolve the chart again with the actual target width before rendering.

### ERR-SCALE-DOMAIN-REQUIRES-CONTINUOUS-X: axis_x.scale.domain requires a continuous x-axis scale

- **Level:** error
- **Domain:** render
- **Suppressible:** no

**Message template:**

```
Chart {chart_id!r}: axis_x.scale.domain is set, but the x-axis resolved to a {vl_type!r} (categorical) scale, not a continuous one. An explicit [low, high] domain only extends a continuous scale: on a categorical scale Vega-Lite reads it as exactly two category values, collapsing every mark onto the first one. If the x field is a date, add `axis_x.scale.type: temporal` to force a continuous temporal scale.
```

Fired when `axis_x.scale.domain` is set but the x-axis resolves to a categorical (ordinal/nominal) scale rather than a continuous one. An explicit [low, high] domain only extends a continuous scale; on a categorical scale Vega-Lite reads it as exactly two category values. Add `axis_x.scale.type: temporal` to force a continuous temporal scale if needed.

### ERR-SCATTER-MULTI-Y-NOT-NUMERIC: Scatter chart multi-metric y column is not numeric

- **Level:** error
- **Domain:** compile
- **Suppressible:** no

**Message template:**

```
Chart {chart_id!r} (scatter): y: [...] column {y_field!r} is not numeric. A single y: column may be categorical (a dot plot), but every measure in a y: [...] list is folded onto one numeric axis; use numeric columns.
```

Fired when a scatter chart's y: [a, b] list contains a non-numeric column. A single y: column may be categorical (a dot plot), but a list y: folds every measure onto one shared numeric axis, so each measure must be numeric.

### ERR-SPAN-MIDDLE-ALIGNED-LABELS: labels.position: middle_aligned is not meaningful on a bar with y_start

- **Level:** error
- **Domain:** render
- **Suppressible:** no

**Message template:**

```
labels.position 'middle_aligned' lines every label up at one height measured from zero, which a bar with y_start does not start from. Use 'middle' to center each label between its bar's two ends.
```

Fired when `labels.position: middle_aligned` is set on a bar with `y_start`. That position places every label at one common height measured from zero; a bar that starts elsewhere has no such height. Use `middle`.

### ERR-SPARK-BAR-VALUE-FIELD-NOT-FOUND: spark_bar x names a column not in the query result

- **Level:** error
- **Domain:** render
- **Suppressible:** no

**Message template:**

```
spark_bar chart {chart_id!r}: x field {field!r} names a column not present in its data. Available columns: {available}. On spark_bar, x is the bar magnitude and must name a numeric column from the query.
```

Fired when a spark_bar chart's `x` names a column that is not in the query result at all; usually a typo or a column renamed in the query. Distinct from ERR-SPARK-BAR-VALUE-NOT-NUMERIC, which fires when the column exists but holds no usable numbers: that one's fix is to swap `x` and `y`, which would be the wrong advice for a name that simply isn't there.

### ERR-SPARK-BAR-VALUE-NOT-NUMERIC: spark_bar value field is not numeric

- **Level:** error
- **Domain:** render
- **Suppressible:** no

**Message template:**

```
spark_bar chart {chart_id!r}: x field {field!r} is not usable as the magnitude: {reason}. spark_bar reverses the usual convention: x is the magnitude (the number) and y is the label (the text), the opposite of every other chart family. Set x to a numeric column and y to the text column.
```

Fired when a spark_bar chart's x field (the magnitude channel) is missing, holds no numeric values, or holds a non-numeric value in one of the rows being rendered. spark_bar reverses the x/y convention used by every other chart family: x is the magnitude, y is the label. This usually means x and y were authored in the cartesian order (swap them), or that x was left unset with no numeric column to auto-detect.

### ERR-STACKED-MIDDLE-ALIGNED-LABELS: labels.position: middle_aligned is not meaningful on a stacked bar

- **Level:** error
- **Domain:** render
- **Suppressible:** no

**Message template:**

```
labels.position 'middle_aligned' aligns every bar's label to one shared height, which has no meaning for the segments of a stacked bar. Use 'middle' to center each label in its own segment, or 'top'/'bottom' to pin it to a segment edge.
```

Fired when `labels.position: middle_aligned` is set on a stacked bar. `middle_aligned` places every label at a single common height (the mean bar height, halved) so a row of labels reads as one line, a whole-bar idea with no per-segment reading. Stacked segments each need their own center: use `middle`.

### ERR-SUPPORT-TABLE-POSITION-INVALID: style.support_table.position is invalid for the chart's orientation

- **Level:** error
- **Domain:** compile
- **Suppressible:** no

**Message template:**

```
{message}
```

Fired when `style.support_table.position` names a side the chart's own category-axis orientation can't place. A horizontal category axis (vertical bar, line, area) only accepts `top`/`bottom`; a vertical one (a horizontal bar) only accepts `left`/`right`. The message carries the specific value and orientation.

### ERR-TABLE-FORMAT-KIND-MISMATCH: a table column's format spec does not match its cell values

- **Level:** error
- **Domain:** render
- **Suppressible:** no

**Message template:**

```
table column format {fmt!r} does not match its cell values. {remedy}
```

Fired when a table column's `format:` spec is the wrong kind for the values it formats: a strftime-style time spec (a predefined name like `time_short`, or an explicit string containing a `%`-prefixed directive such as `%B` or `%W`) applied to a numeric value, or a d3 numeric spec applied to a date/datetime value. `format:` is kind-agnostic at compile time, so `dct validate` accepts either mismatch; this is caught per cell at render time instead, once the actual value kind is known.

### ERR-TICKS-COUNT-REQUIRES-NON-LOG-SCALE: ticks.count is not supported with log scale

- **Level:** error
- **Domain:** compile
- **Suppressible:** no

**Message template:**

```
Chart {chart_id!r}: axis_y.ticks.count is not supported with scale.type: log: a target tick count on a log axis is nonsense; Vega-Lite computes log-decade ticks natively. Remove ticks.count.
```

Fired when `axis_y.ticks.count` is combined with `axis_y.scale.type: log`. A target tick count on a log axis is meaningless because Vega-Lite computes log-decade ticks natively. Remove `ticks.count`.

### ERR-TICKS-INTERVAL-MEASURE-AXIS: ticks.time_unit is not supported on the measure axis

- **Level:** error
- **Domain:** compile
- **Suppressible:** no

**Message template:**

```
style.axis_y.ticks.time_unit/step is not supported: the measure axis (axis_y) is never temporal in dbt charts' cartesian model, and its tick ladder is computed from ticks.count. Use axis_y.ticks.count here; ticks.time_unit/step apply to axis_x.
```

Fired when `style.axis_y.ticks.time_unit` or `step` is set. The measure axis (axis_y) is never temporal in dbt charts' cartesian model, and its tick ladder is computed from `ticks.count`. Use `axis_y.ticks.count` here; `ticks.time_unit/step` apply to `axis_x`.

### ERR-TICKS-INTERVAL-NOT-TEMPORAL: ticks.time_unit requires a continuous temporal x-axis

- **Level:** error
- **Domain:** render
- **Suppressible:** no

**Message template:**

```
style.axis_x.ticks.time_unit requires a continuous temporal x-axis, but {field!r} resolved to a {vl_type!r} scale. Force a continuous scale with axis_x.type: temporal (or axis_x.time_unit: none), or remove ticks.time_unit.
```

Fired when `style.axis_x.ticks.time_unit` is set but the x-axis does not resolve to a continuous temporal scale. Force a continuous scale with `axis_x.type: temporal` (or `axis_x.time_unit: none`), or remove `ticks.time_unit`.

### ERR-TICKS-STEP-NOT-QUANTITATIVE: a bare ticks.step requires a quantitative x-axis

- **Level:** error
- **Domain:** render
- **Suppressible:** no

**Message template:**

```
style.axis_x.ticks.step without ticks.time_unit is a numeric tick interval, but {field!r} resolved to a {vl_type!r} scale. {remedy}
```

Fired when `style.axis_x.ticks.step` is set without `ticks.time_unit` on an x-axis that does not resolve to a quantitative scale. A bare `step` is the numeric cadence lever (it emits Vega-Lite's `tickMinStep`) and only a quantitative scale has a numeric tick interval. On a temporal axis, author `ticks.time_unit` alongside `step` to name a calendar cadence. On a discrete axis (ordinal or nominal) there is no tick interval to set; remove `ticks.step`. A horizontal bar is the case worth calling out: its `axis_x` is the categorical axis and its measure is `axis_y`, so `orientation: vertical` is usually what the author wanted.

### ERR-VEGA-LITE-UNSUPPORTED-TYPE: Chart type does not render to a Vega-Lite spec

- **Level:** error
- **Domain:** render
- **Suppressible:** no

**Message template:**

```
Chart type {chart_type!r} does not render to a Vega-Lite spec.
```

Fired when a chart type is asked to render a Vega-Lite spec but does not support that output format. Use a Vega-Lite-compatible chart type or choose a different output format.

### ERR-WIDE-MEASURE-NAME-CONTAINS-SEPARATOR: Wide measure name contains the dimension composite separator

- **Level:** error
- **Domain:** compile
- **Suppressible:** no

**Message template:**

```
Chart authors y: [...] with color: as a dimension, and measure column {measure!r} contains the {separator!r} composite separator, so the composite series name cannot be split back into a dimension value and a measure. Rename the measure column, or alias it in the query.
```

A wide chart authoring a list of y: measures can also author color: as a dimension the measures cross with. Fires when one of the measure column names itself contains the separator the `<dimension value> - <measure>` composite string uses. The composite cannot be split back apart unambiguously in that case, so the measure column must be renamed, or aliased in the query, to avoid the separator.

## errors


### ERR-BOARD-ARTIFACT-INVALID: Resolved-board artifact does not match the expected schema

- **Level:** error
- **Domain:** render
- **Suppressible:** no

**Message template:**

```
Board artifact is invalid: {detail}. It may have been produced by an incompatible dct version, hand-edited, or truncated. Re-emit it with `dct artifact emit`.
```

Fired when a resolved-board artifact fails to validate against `ResolvedBoard` while loading it for replay. The artifact is the published, versioned contract a resolved board serializes to; this means the file is not a valid instance of that contract.

### ERR-BOARD-RECORDING-INVALID: Board recording sidecar does not match the expected schema

- **Level:** error
- **Domain:** render
- **Suppressible:** no

**Message template:**

```
Board recording is invalid: {detail}. It may have been produced by an incompatible dct version, hand-edited, or truncated. Re-emit it with `dct artifact emit`.
```

Fired when a board recording sidecar fails to validate against `BoardRecording` while loading it for replay.

### ERR-BOARD-RECORDING-MISMATCH: Board recording does not match the artifact it was replayed against

- **Level:** error
- **Domain:** render
- **Suppressible:** no

**Message template:**

```
Board recording does not match this artifact: {detail} The artifact and its recording must come from the same `dct artifact emit` run.
```

Fired when replaying a resolved-board artifact against a recording that either lacks rows for one of the artifact's queries, or recorded them under different variable values. Both mean the artifact and recording came from different emits (or a truncated one); replaying anyway would render an empty or wrong chart that looks like real data.

### ERR-DUPLICATE-CHART-ID: Two charts share an id across nested boards

- **Level:** error
- **Domain:** render
- **Suppressible:** no

**Message template:**

```
Two charts share the id {chart_id!r} across nested boards, which the flat {format!r} format cannot represent without dropping one. Rename one of them, or use --format json, which keeps the layout nesting.
```

Chart ids are unique within a board but not across a board tree: two imported partials, including the same partial imported twice, can declare the same id. The flat output formats key charts by id, so a collision would silently drop every chart but the last. Rename the colliding chart, or render with a format that preserves the layout.

### ERR-EMITTER-NOT-FOUND: No emitter registered for the resolved chart type

- **Level:** error
- **Domain:** render
- **Suppressible:** no

**Message template:**

```
No emitter registered for resolved chart type {resolved_type!r}. This indicates an engine bug; the normalizer should have rejected this chart before it reached render.
```

Fired when the render engine cannot find an emitter for the resolved chart type. This indicates an engine bug; the normalizer should have rejected this chart before it reached render.

### ERR-FILE-NOT-FOUND: File not found

- **Level:** error
- **Domain:** compile
- **Suppressible:** no

**Message template:**

```
File not found: {path}
```

Fired when a file path given to a dbt charts verb does not exist on the filesystem. Check for typos in the path and ensure the file exists.

### ERR-FORMAT-CONVERSION-FAILED: Format conversion failed

- **Level:** error
- **Domain:** render
- **Suppressible:** no

**Message template:**

```
{format!r} export failed while converting the rendered board ({detail}). Try exporting {alt_formats} instead, or split the board into smaller boards.
```

Fired when the vl-convert-python converter raises while turning a rendered board into PNG or PDF bytes. On PDF exports, the most common permanent cause is the PDF format's own 28-level graphics-state nesting limit: a sufficiently large or deeply nested board can exceed it. Try a different export format, or split the board into smaller boards. The underlying converter message is preserved in the diagnostic detail.

### ERR-FORMAT-CONVERTER-UNAVAILABLE: Format conversion library is not installed

- **Level:** error
- **Domain:** render
- **Suppressible:** no

**Message template:**

```
{format!r} rendering requires vl-convert-python. Install it with: pip install vl-convert-python
```

Fired when a PNG or PDF export (or any render path that needs vl-convert-python to turn a Vega-Lite spec into SVG/PNG/PDF) runs in an environment where vl-convert-python is not installed. Install it: `pip install vl-convert-python`.

### ERR-FORMAT-UNSUPPORTED: Unknown render format

- **Level:** error
- **Domain:** render
- **Suppressible:** no

**Message template:**

```
Unknown format: {format!r}
```

Fired when a render verb is called with an output format that is not supported. Check the supported formats in the CLI reference.

### ERR-INTERNAL: Internal error

- **Level:** error
- **Domain:** unknown
- **Suppressible:** no

**Message template:**

```
{message}
```

Fired when an unclassified internal failure occurs that does not map to a more specific error code. Check the full traceback for details. If this appears in normal usage, file a bug report.

### ERR-NUMERAL-EXPR-EMPTY-SPEC: Empty format spec cannot build a numeral Vega expression

- **Level:** error
- **Domain:** render
- **Suppressible:** no

**Message template:**

```
numeral_vega_expr() requires a non-empty format_spec. format_d3 treats an empty spec as a distinct 'no d3 formatting' path (the bare Python value) that a Vega expression cannot reproduce byte-for-byte.
```

Fired when numeral_vega_expr() is called with an empty format_spec. format_d3 short-circuits an empty spec to the bare Python value, bypassing d3 entirely: a Vega expression cannot reproduce that byte-for-byte (Python and JS do not stringify numbers identically), so building the expression is rejected rather than silently diverging. This indicates an engine bug: callers should always resolve a concrete d3 spec before reaching this emitter.

### ERR-STARTUP-FAILED: Server failed to start

- **Level:** error
- **Domain:** serve
- **Suppressible:** no

**Message template:**

```
Server failed to start: {detail}.
```

Fired when the uvicorn server process fails to start. The detail carries the inner error message from uvicorn.

## layout


### ERR-NO-LAYOUT: Board defines charts but has no layout

- **Level:** error
- **Domain:** render
- **Suppressible:** no

**Message template:**

```
Board defines charts ({charts}) but no layout: would render with no visible charts. Add a `rows:`/`cols:`/`grid:`/`tabs:` block that references them.
```

Fired when a board defines charts but no layout block (rows/cols/grid/tabs). Without a layout, the board would render with no visible charts. Add a layout block that references the charts.

## queries


### ERR-ADAPTER-RELATIVE-PATH-NO-DATA-DIR: Relative source path needs a data directory to resolve against

- **Level:** error
- **Domain:** execute
- **Suppressible:** no

**Message template:**

```
Relative {adapter} path {path!r} requires a data_dir to resolve against; none was configured for this adapter. Use an absolute path or configure a data directory.
```

Fired when a file-backed source (DuckDB, SQLite) declares a relative `path:` but the adapter has no data directory to resolve it against. Resolving against the process working directory would make the source depend on where `dct` was invoked from, so dbt charts refuses. Use an absolute path or configure a data directory for the project.

### ERR-BINDER-TYPE-MISMATCH: Warehouse rejected the query due to a type mismatch

- **Level:** error
- **Domain:** execute
- **Suppressible:** no

**Message template:**

```
Warehouse rejected the query due to a type mismatch: {detail}. Add explicit type casts to resolve ambiguity.
```

Fired when the warehouse reports a type mismatch during query binding. Add explicit type casts to resolve the ambiguity.

### ERR-BINDER-UNKNOWN-COLUMN: Warehouse rejected an unknown column or table reference

- **Level:** error
- **Domain:** execute
- **Suppressible:** no

**Message template:**

```
Warehouse could not resolve a column or table reference: {detail}. Check that all referenced columns and tables exist in the source.
```

Fired when the warehouse reports that a column or table reference could not be resolved during query binding. Check that all referenced columns and tables exist in the source.

### ERR-CHART-COLUMN-NOT-IN-RESULT: Chart channel references a column absent from the query result

- **Level:** error
- **Domain:** execute
- **Suppressible:** no

**Message template:**

```
Chart '{chart}' channel '{channel}' references column '{column}' which is not in the result of query '{name}'. Available columns: {columns}
```

Fired during `dct validate --warehouse` when a chart's channel (x, y, color, value, etc.) names a column that the backing query does not return. Rename the channel to match a returned column, or update the query to return the expected column.

### ERR-DBT-CALL-UNSUPPORTED: dbt call form this engine cannot resolve

- **Level:** error
- **Domain:** execute
- **Suppressible:** no

**Message template:**

```
Could not resolve {call!r} against the dbt manifest: dbt charts reads the relation name from the call itself, so each argument must be a plain quoted string: {{{{ ref('model') }}}} or {{{{ source('source', 'table') }}}}.
```

**Fix:** Package-qualified `ref('package', 'model')`, versioned `ref('model', v=2)`, and macro-computed arguments are not supported: each needs information the manifest lookup does not use, so resolving one would mean guessing which relation you meant. Name the model directly, or write the relation out.

Fired when a query's SQL calls dbt's `ref()` or `source()` in a form dbt charts cannot resolve to a single relation. dbt charts rewrites these calls textually against the manifest rather than executing dbt's Jinja, so it reads the names straight out of the call and every argument must be a plain quoted string. Rather than let an unrecognized call through to the warehouse (where it fails as a SQL syntax error naming `{{`, far from the cause), dbt charts reports it here.

### ERR-DBT-MANIFEST-MISSING: SQL uses a dbt macro but no manifest is available

- **Level:** error
- **Domain:** execute
- **Suppressible:** no

**Message template:**

```
SQL uses {kind} but no dbt manifest was found (looked for {paths}). Build one with `dbt parse`.
```

Fired when a query's SQL calls `ref()` or `source()` but the project has no dbt manifest to resolve the call against. The manifest is what maps a model name to its warehouse relation, so without it dbt charts cannot know which table the query means. Run `dbt parse` (or any command that writes `target/manifest.json`) in the dbt project.

### ERR-DBT-MANIFEST-UNREADABLE: dbt manifest could not be read

- **Level:** error
- **Domain:** execute
- **Suppressible:** no

**Message template:**

```
The dbt manifest at {relpath!r} could not be read: {detail}. Rebuild it with `dbt parse`.
```

Fired when the manifest exists but cannot be read: the file is unreadable (OSError) or the JSON is malformed: usually a truncated write from an interrupted dbt run. Rebuild with `dbt parse`. The manifest's schema version is not checked: dbt charts reads the manifest as plain JSON rather than through dbt's typed contract, so a manifest written by a different dbt version still resolves refs normally.

### ERR-DBT-MODEL-COLUMN-MISSING: Query references a column the dbt model no longer produces

- **Level:** error
- **Domain:** execute
- **Suppressible:** no

**Message template:**

```
Query {query_name!r} references column {column_name!r} of dbt model {model!r}, but the model's SQL does not produce it. Model columns: {available}.
```

Fired when a board query references a column of a dbt model whose output columns, derived statically from the model's SQL in `target/manifest.json`, do not include it. This catches a column renamed or dropped in the model *before* `dbt run` rebuilds the warehouse, when `--warehouse` validation still passes against the old table. If the model was just changed on purpose, update the board; if the manifest is stale, re-run `dbt parse`.

### ERR-DBT-REF-UNKNOWN-NODE: ref() names a node that isn't in the dbt manifest

- **Level:** error
- **Domain:** execute
- **Suppressible:** no

**Message template:**

```
SQL references {{{{ ref({ref_name!r}) }}}}, but no model, seed, or snapshot named {ref_name!r} exists in the dbt manifest. Available: {available}.
```

Fired when a query's SQL calls the dbt `ref()` Jinja function with a name that is not present in the loaded manifest. `ref()` addresses models, seeds, and snapshots, not data tests or sources (use `source()` for those). Check for a typo, or refresh the manifest (`dbt parse`) if the node was added recently.

### ERR-DBT-SOURCE-UNKNOWN-TABLE: source() names a table that isn't in the dbt manifest

- **Level:** error
- **Domain:** execute
- **Suppressible:** no

**Message template:**

```
SQL references {{{{ source({source_name!r}, {table_name!r}) }}}}, but no matching source table exists in the dbt manifest. Available sources: {available}.
```

Fired when a query's SQL calls the dbt `source()` Jinja function with a source/table pair that is not present in the loaded manifest. Check for a typo, or refresh the manifest (`dbt parse`) if the source was added recently.

### ERR-FILE-SOURCE-AMBIGUOUS: Inline file source path exists at both candidate locations

- **Level:** error
- **Domain:** compile
- **Suppressible:** no

**Message template:**

```
Query {query_name!r}: inline file source {ref!r} exists at both {candidates}. Rename or remove one so only a single candidate exists.
```

Fired when a query's inline `source: <path>` ref resolves to a real file at both of its two candidate locations, the board's own directory and the project root (a bare path is tried against both anchors so it works from any board depth). dbt charts never silently prefers one anchor; move or rename one of the two files so only a single candidate remains.

### ERR-FILE-SOURCE-DECIMAL-TOO-WIDE: File source decimal column exceeds precision 38

- **Level:** error
- **Domain:** execute
- **Suppressible:** no

**Message template:**

```
File source {source_name!r}, table {table_name!r}: column {column!r} in {relpath!r} is {column_type}, and at least one value needs more than the 38 significant digits the query engine's DECIMAL can hold. Re-export the column at precision 38 or less (BigQuery: CAST(col AS NUMERIC) instead of BIGNUMERIC), or split the digits across columns.
```

Fired when a decimal column in a file source holds a value needing more than 38 significant digits. Parquet's decimal type allows more precision than the query engine's DECIMAL, and BigQuery BIGNUMERIC exports routinely use it. Re-export the column at precision 38 or less, or split the digits across columns.

### ERR-FILE-SOURCE-NOT-FOUND: File source path could not be read from disk

- **Level:** error
- **Domain:** execute
- **Suppressible:** no

**Message template:**

```
File source {source_name!r}: {relpath!r} could not be read ({detail}). Restore the missing file, fix the path, or fix its permissions.
```

Fired when a `type: csv`/`json`/`parquet` source's `files:` mapping, or a query's inline `source: <path>` ref, names a literal (non-glob) path that cannot be opened on disk: the leaf is missing, a path component traverses through an existing file instead of a directory, or the OS denies read access. `{detail}` carries the OS error string (e.g. "No such file or directory", "Not a directory", "Permission denied") so the message doesn't call a permissions problem a missing file. Unlike an empty glob match (`ERR-GLOB-EMPTY`), a literal path is never expanded, so this is the only place these failures surface. Compile probes an inline ref's two candidate locations (board directory, project root) only to choose between them; a path that exists at neither, like a `files:` path, fails here at query-execution time (`dct render`/`dct serve`), one chart at a time. Fix the path, add the missing file, or fix its permissions.

### ERR-FILE-SOURCE-TOO-LARGE: File source relation exceeded the uncompressed-size cap

- **Level:** error
- **Domain:** execute
- **Suppressible:** no

**Message template:**

```
File source {source_name!r}, table {table_name!r}: reading {relpath!r} ({raw_mb:.1f} MB on disk) took this table to {size_mb:.1f} MB uncompressed, exceeding the {cap_mb:.1f} MB cap. Load less into this table — fewer files, fewer columns, or a pre-aggregated extract — or use a database connection for data this size.
```

Fired when the uncompressed size of a file-source relation (the file(s) backing one `files:` table entry) exceeds the effective `execution.file_source_max_bytes` limit. Every format is measured on the same basis: the file's own bytes for CSV and JSON, and for Parquet the uncompressed total its footer records, so choosing the compact format is never what gets a relation rejected. The message names no config key deliberately — the effective limit is the lower of the project's own `execution.file_source_max_bytes` and any deployment ceiling (`DCT_FILE_SOURCE_MAX_BYTES_CEILING`), so where a ceiling is what fired, raising the project setting does nothing. Lower the data volume, or use a database connection for data this size.

### ERR-FILE-SOURCE-TOO-MANY-TABLES: File source exceeded the table-count cap

- **Level:** error
- **Domain:** execute
- **Suppressible:** no

**Message template:**

```
File source {source_name!r}: `files:` has {count} tables, exceeding the {cap}-table cap. Split the source, raise execution.file_source_max_tables in dbt_charts.yml, or use a database connection for large-scale data.
```

Fired when a file source's `files:` mapping declares more table entries than the configured `execution.file_source_max_tables` limit. A wide `files:` map can quietly fill the shared cache disk, split the source into multiple sources, raise the cap in `dbt_charts.yml` under `execution: file_source_max_tables: <N>`, or use a database connection for large-scale data.

### ERR-FILE-SOURCE-UNSUPPORTED-TYPE: File source column type is not supported by the query engine

- **Level:** error
- **Domain:** execute
- **Suppressible:** no

**Message template:**

```
File table {table_name!r}: the query engine cannot load one of its column types. {detail} Re-export the file with the column cast to a standard SQL type.
```

Fired when a file carries a column type the query engine cannot represent. dbt charts converts the cases it can (a half-precision float widens, a 256-bit decimal narrows) and reports this for the rest. Re-export the file with the column cast to a standard SQL type.

### ERR-GLOB-EMPTY: Glob pattern in file source matched no files

- **Level:** error
- **Domain:** execute
- **Suppressible:** no

**Message template:**

```
File source {source_name!r}, table {table_name!r}: glob pattern {pattern!r} matched no files. Provide at least one matching file or fix the pattern.
```

Fired when a glob pattern in a file source's `files:` mapping expands to zero files. Every glob must match at least one file; an empty match is always a configuration error. Check the pattern for typos, verify the files exist at the expected paths, and confirm the path is relative to the project root.

### ERR-GLOB-SCHEMA-MISMATCH: Glob-matched files have inconsistent column schemas

- **Level:** error
- **Domain:** execute
- **Suppressible:** no

**Message template:**

```
File source {source_name!r}, table {table_name!r}: {path!r} disagrees with {first_path!r} on column names or types. {detail}All files matched by a glob must share the same column names and types.
```

Fired when a glob pattern in a file source's `files:` mapping expands to files whose column schemas disagree — either a differing set of column names, or the same column carrying a different type in one file than another. Align the schemas across all matched files, or split the source into separate entries with non-overlapping patterns.

### ERR-GLOB-TOO-MANY: Glob pattern in file source exceeded the file-count cap

- **Level:** error
- **Domain:** execute
- **Suppressible:** no

**Message template:**

```
File source {source_name!r}, table {table_name!r}: glob pattern {pattern!r} matched {count} files, exceeding the {cap}-file cap. Increase execution.max_glob_file_count in dbt_charts.yml if needed.
```

Fired when a glob pattern in a file source's `files:` mapping matches more files than the configured `execution.max_glob_file_count` limit. Narrow the pattern, or raise the cap in `dbt_charts.yml` under `execution: max_glob_file_count: <N>`.

### ERR-JINJA-ERROR: Jinja template in a query failed to render

- **Level:** error
- **Domain:** compile
- **Suppressible:** no

**Message template:**

```
Jinja template error: {message}
```

Fired when a Jinja template in a query or pre-query raises a rendering error. Check the template syntax and ensure all referenced values and filters are available.

### ERR-MUTATING-SQL: Non-read-only SQL is not allowed

- **Level:** error
- **Domain:** execute
- **Suppressible:** no

**Message template:**

```
dct refuses to execute non-read-only SQL. Statement: {rejected_node_kind}. Preview: {fragment_preview}.
```

Fired when dbt charts detects a non-SELECT statement (INSERT, UPDATE, DELETE, DROP, etc.) in a query. dbt charts only executes read-only SQL to prevent accidental data modification.

### ERR-NO-DEFAULT-SOURCE: Source name required but none specified

- **Level:** error
- **Domain:** execute
- **Suppressible:** no

**Message template:**

```
Source name required: {available}. Name a source for the query.
```

Fired when a query reaches execution without a source name and there is no default source configured. Specify a source name on the query or configure a default source.

### ERR-QUERY-DURATION-EXCEEDED: Query exceeded the maximum allowed duration

- **Level:** error
- **Domain:** execute
- **Suppressible:** no

**Message template:**

```
Query exceeded max_query_duration_seconds={seconds}s on source {source!r}.
```

Fired when a query runs longer than the configured `max_query_duration_seconds` limit on a source. Optimize the query, raise the limit, or add a WHERE clause to reduce the result set.

### ERR-REPO-FILE-TOO-LARGE: Repository file exceeds the per-file size limit

- **Level:** error
- **Domain:** execute
- **Suppressible:** no

**Message template:**

```
Repo file too large: {file_path} is at or over the {limit_mb:.0f} MB per-file limit in a connected repo. Use a database connection for data this size.
```

Fired when a file in a connected repository is at or over the deployment's per-file size limit. dbt charts imposes this limit because large files are better served by a direct database connection rather than loading the entire file into memory.

### ERR-SOURCE-CONFIG-INVALID: Source configuration fails validation

- **Level:** error
- **Domain:** compile
- **Suppressible:** no

**Message template:**

```
Source {source_name!r} in {filename}: {detail}
```

Fired when a `sources:` entry in dbt_charts.yml fails typed SourceConfig validation. Check the source definition for missing required fields or invalid values, and refer to the sources reference for the expected schema.

### ERR-SOURCE-CREDENTIAL-LITERAL: Source contains a raw credential literal

- **Level:** error
- **Domain:** compile
- **Suppressible:** no

**Message template:**

```
Source {source_name!r}: field {field!r} holds a raw credential literal. dbt_charts.yml is committed to git, so inline secrets leak on push. Reference the secret instead (e.g. {field}: {{{{ env_var('SECRET_NAME') }}}}), or use a `type: dbt_profile` source that delegates to an out-of-repo profiles.yml.
```

Fired when a `sources:` entry in the committed dbt_charts.yml contains a raw secret literal (password, API key, etc.). Since dbt_charts.yml is committed to git, inline secrets would be leaked on push. Reference the secret via `env_var()` or use a `dbt_profile` source type that delegates to an out-of-repo profiles.yml.

### ERR-SOURCE-CROSS-FILE-FORBIDDEN: Cross-file source reference is not allowed

- **Level:** error
- **Domain:** execute
- **Suppressible:** no

**Message template:**

```
Cross-file source reference (`#` anchor form) is not allowed: {offending_value!r}.
```

Fired when a source reference uses the YAML anchor cross-file form (`#`). Cross-file source references are not allowed; use a named source from the project allowlist instead.

### ERR-SOURCE-INLINE-FORBIDDEN: Inline source definition is not allowed

- **Level:** error
- **Domain:** compile
- **Suppressible:** no

**Message template:**

```
Query {query_name!r}: inline source definitions are not allowed. Reference a source by name (`source: my_db`) instead. Got: {offending_value}
```

**Fix:** Use a named source declared under `sources:` in your dbt_charts.yml instead of inline connection parameters.

Fired when a query's `source:` is set to an inline dictionary instead of a named source reference. Inline source definitions are forbidden for security reasons: connection parameters in the committed YAML would leak credentials. Use a named source declared under `sources:`.

### ERR-SOURCE-INVALID-TYPE: Unknown source type

- **Level:** error
- **Domain:** execute
- **Suppressible:** no

**Message template:**

```
Unknown source type {offending_value!r}. Valid types: {available}.
```

Fired when a source's `type:` field names a source type that is not registered. Check for typos and refer to the documentation for the supported source types.

### ERR-SOURCE-MISSING-TYPE: Source is missing the required type field

- **Level:** error
- **Domain:** execute
- **Suppressible:** no

**Message template:**

```
Source is missing the required `type` field: {offending_value!r}.
```

Fired when a source definition omits the required `type:` field. Add a `type:` field naming the source adapter to use.

### ERR-SOURCE-NOT-FOUND: Query references an unknown source

- **Level:** error
- **Domain:** compile
- **Suppressible:** no

**Message template:**

```
Query {query_name!r} references unknown source {source!r}. Available sources: {available}. dct sources are declared under `sources:` in your dbt_charts.yml; the source name is not the dbt project name.
```

Fired when a query's `source:` names a source that is not declared in the sources registry. Check for typos and ensure the source is declared under `sources:` in your dbt_charts.yml. Also includes the execute-side failure of the same name (source lookup at query time).

### ERR-SOURCE-NOT-FOUND-EMPTY: No source profiles are configured

- **Level:** error
- **Domain:** execute
- **Suppressible:** no

**Message template:**

```
Source {source!r} not found. No source profiles are configured. Declare sources under `sources:` in your dbt_charts.yml.
```

Fired when a query references a source but no source profiles are configured at all. Declare sources under `sources:` in your dbt_charts.yml.

### ERR-SOURCE-REQUIRED: Query has no source configured

- **Level:** error
- **Domain:** compile
- **Suppressible:** no

**Message template:**

```
Query {query_name!r}: SQL queries must have a source. Set it on the query (`source: my_db`), or at the board or folder meta.yml level (`source: my_db`).
```

Fired when a SQL query has no `source:` set at the query, board, or folder meta.yml level and no default source is configured. Set `source: my_db` on the query or as a default at a higher level.

### ERR-SQL-DATE-LITERAL-VARIABLE: Variable quoted as a date/time/timestamp literal will not compile

- **Level:** error
- **Domain:** compile
- **Suppressible:** no

**Message template:**

```
Query {query_name!r} {field_label} casts a variable with {keyword} '{{{{ {variable} }}}}'. This source binds the variable as a query parameter, so its quotes are stripped and this compiles to {keyword} $N, which the warehouse does not parse. Use cast('{{{{ {variable} }}}}' as {keyword}) instead.
```

Fired when a query casts a Jinja variable to a date/time/timestamp using the SQL literal syntax (`date '{{{{ var }}}}'`) on a source whose adapter binds variables as real query parameters (duckdb, sqlite). The parameterizer strips the quotes around every placeholder, so `date '{{{{ var }}}}'` compiles to `date $1` -- syntax the warehouse does not accept. Other sources (postgres, snowflake, bigquery, dbt_profile, etc.) render variables as inline literal text instead, so the same SQL is valid there and this check does not fire. Use `cast('{{{{ var }}}}' as date)` (or `time`/`timestamp`) instead, which keeps the variable an ordinary string parameter and works on every source.

### ERR-SQL-LITERAL-NEWLINES: SQL contains literal backslash-n from single-quoted YAML

- **Level:** error
- **Domain:** compile
- **Suppressible:** no

**Message template:**

```
Query {query_name!r} {field_label} contains literal \n (backslash + n). Use YAML block scalar `{field_label}: |` to write multiline SQL:

  queries:
    my_query:
      {field_label}: |
        SELECT
          col
        FROM t
```

Fired when a query's SQL or pre-query string contains a literal backslash followed by 'n', which typically means YAML single-quote escaping swallowed an intended newline. Use a YAML block scalar (`field: |`) for multiline SQL to avoid this.

### ERR-TEMPLATE-OUTPUT-TOO-LARGE: Board render's cumulative template output exceeded the cap

- **Level:** error
- **Domain:** compile
- **Suppressible:** no

**Message template:**

```
This board render's templated fields (queries, titles, markdown, …) emitted more than {ceiling} bytes combined, stopping after {emitted_bytes} bytes. This is a render-wide cumulative cap, not a per-field one — a nested loop, a runaway variable expansion, or genuinely oversized content anywhere in the board can trip it. Reduce the amount of text a templated field (or their combination) produces.
```

Fired when the cumulative bytes emitted by every templated field (queries, titles, markdown, chart labels) in one board render exceed the effective `execution.max_template_output_bytes` limit — a hard error, not a truncation-with-warning like `WARN-QUERY-RESULT-TRUNCATED`: a truncated SVG or SQL string is a corrupt document, never a usable-with-a-caveat result. The message names no config key deliberately: the effective limit is the lower of the project's own config value and any deployment ceiling (`DCT_MAX_TEMPLATE_OUTPUT_BYTES_CEILING`), so it cannot be raised past the ceiling.

### ERR-UNKNOWN-QUERY: Chart references an unknown query

- **Level:** error
- **Domain:** compile
- **Suppressible:** no

**Message template:**

```
Chart {chart_name!r} references unknown query {query_name!r}. Declare the query under `queries:` or fix the typo in `query:`.
```

Fired when a chart's `query:` names a query that is not declared under `queries:` in the board or any included meta.yml. Check for typos and ensure the query is declared.

### ERR-UNPARSEABLE-SQL: SQL could not be parsed for static checks

- **Level:** error
- **Domain:** execute
- **Suppressible:** no

**Message template:**

```
Could not parse this SQL: {cause}
```

**Fix:** If the warehouse accepts this query, the SQL is fine; the parser just does not model that dialect or macro yet, and only dbt charts' static checks (read-only enforcement, fanout and reaggregation lint) are skipped for it. If the warehouse rejects it too, fix the syntax at the reported position.

Fired when dbt charts' static SQL parser cannot parse a query. The query is still sent to the warehouse; what is lost is the static read-only check and the semantic lint that run on parseable SQL. It is not necessarily an error in the SQL itself; unmodeled dialect syntax and dbt macros land here too.

### ERR-UNRESOLVED-REFERENCE: Reference points to an unknown name

- **Level:** error
- **Domain:** compile
- **Suppressible:** no

**Message template:**

```
Reference {ref!r} not found{context}.
```

Fired when a chart or layout reference names a target that cannot be found in the current board or any included files. Check for typos and ensure the referenced chart, query, or layout item is declared.

### ERR-WAREHOUSE-CONNECTION: Could not open the warehouse

- **Level:** error
- **Domain:** execute
- **Suppressible:** no

**Message template:**

```
Could not open the warehouse: {reason}
```

Fired when opening the connection fails, before any SQL is sent: a database file that is missing, unreadable, or lock-held by another process (DuckDB, SQLite), or bad credentials, an unreachable host, or a missing database/role on a network warehouse (Postgres, Snowflake, BigQuery, Databricks, …). Distinct from ERR-WAREHOUSE-RUNTIME because nothing ever read the SQL: the query may be perfectly good, so callers that judge queries (`dct validate --warehouse`) must not report it as a query defect. `{reason}` names the classified cause; the driver's own text (which may carry hostnames, PIDs, or usernames) is not shown here but is available in `dct`'s diagnostics output and server logs. Check the source's credentials, host, and network reachability in `dbt_charts.yml` or `profiles.yml`.

### ERR-WAREHOUSE-QUERY-INVALID: Query failed warehouse validation

- **Level:** error
- **Domain:** execute
- **Suppressible:** no

**Message template:**

```
Query '{name}' failed warehouse validation ({mechanism}): {warehouse_message}
```

Fired when a query's SQL is rejected by the warehouse during `dct validate --warehouse`. The warehouse message carries the specific failure reason (unknown column, syntax error, etc.). Fix the SQL or re-run `dbt parse` if a referenced model was recently renamed.

### ERR-WAREHOUSE-RUNTIME: Warehouse rejected the query at runtime

- **Level:** error
- **Domain:** execute
- **Suppressible:** no

**Message template:**

```
Warehouse rejected the query: {detail}.
```

Fired when the warehouse rejects a query for an unclassified runtime error. The detail carries the warehouse's original error message.

## variables


### ERR-UNKNOWN-VARIABLE: Unknown variable referenced in template

- **Level:** error
- **Domain:** compile
- **Suppressible:** no

**Message template:**

```
{surface} {owner_name!r} references unknown variable {var_name!r}. Declare it under `variables:` or fix the typo.
```

Fired when a Jinja template references a variable name that is not declared under `variables:`. Check for typos and ensure the variable is declared before the template that uses it.
