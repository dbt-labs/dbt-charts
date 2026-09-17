"""ERR-* and WARN-* codes for the render domain.

Error codes: KPI multirow, KPI format kind mismatch, KPI temporal format
invalid, bar duplicate rows, map key mismatch, format
unsupported, format converter unavailable, format conversion failed, no
layout, input invalid, Vega-Lite unsupported type, histogram
non-numeric, histogram pre-aggregated, label/ticks validation, percent range,
emitter not found, labels field not found, scale domain, concat overshoot,
chart painted no marks, pie null theta, pie negative theta, multiples +
endpoint labels, mirror + endpoint labels, multiples + support_table,
multiples independent-scale + mirror, gap-fill bucket collision,
endpoint labels + unorderable sort, endpoint labels + negative stack,
layer axis position + endpoint labels.

Warning codes: every render-time detector code, declared here (not in the
detector modules, which sit above this leaf) so the registry is complete on
import of core.diagnostics. Detectors import their constants back from this
package.
"""

from __future__ import annotations

from dbt_charts.core.diagnostics.registry import REGISTRY, ErrorCode, WarningCode

ERR_KPI_MULTIROW = REGISTRY.register(
    ErrorCode(
        code="ERR-KPI-MULTIROW",
        domain="render",
        title="KPI query returned more than one row",
        message_template=(
            "KPI chart {chart_id!r} expects exactly 1 row, got {row_count}. "
            "Use a query that returns a single row (e.g. SELECT SUM(...) or LIMIT 1)."
        ),
        doc=(
            "Fired when a KPI chart's query returns more than one row. KPI charts "
            "display exactly one value; use a query that returns a single row "
            "(e.g. SELECT SUM(...) or LIMIT 1)."
        ),
        docs_topic="charts",
    )
)

ERR_KPI_FORMAT_KIND_MISMATCH = REGISTRY.register(
    ErrorCode(
        code="ERR-KPI-FORMAT-KIND-MISMATCH",
        domain="render",
        title="KPI support format does not match a temporal value",
        message_template=(
            "KPI chart {chart_id!r} support value is a date/time, but its "
            "format {spec!r} is not a date format. Use date_short, a "
            "strftime spec via a style.formats alias, or remove format: to "
            "use date_short."
        ),
        doc=(
            "Fired when a KPI's support.format is a number format (not "
            "date_short, time_short, or a style.formats alias resolving to "
            "a strftime spec) and support.value is a date/datetime column. "
            "support.format is always authored for that one chart, unlike "
            "the headline value's format, which can be a board-wide cascade "
            "default -- so a mismatch here is never ambiguous."
        ),
        summary="Fired when a KPI support format doesn't match a temporal value.",
        docs_topic="charts",
    )
)

ERR_KPI_TEMPORAL_FORMAT_INVALID = REGISTRY.register(
    ErrorCode(
        code="ERR-KPI-TEMPORAL-FORMAT-INVALID",
        domain="render",
        title="KPI temporal format spec could not be applied",
        message_template=(
            "KPI chart {chart_id!r} could not format value {cell!r} with "
            "spec {spec!r}: {reason}"
        ),
        doc=(
            "Fired when a KPI's resolved date/time format spec is itself "
            "invalid (an unknown strftime directive) or the cell's value "
            "cannot be parsed as a calendar date/time (e.g. an out-of-range "
            "hour in an ISO timestamp string)."
        ),
        summary="Fired when a KPI's date/time format spec can't be applied.",
        docs_topic="charts",
    )
)

ERR_BAR_DUPLICATE_ROWS = REGISTRY.register(
    ErrorCode(
        code="ERR-BAR-DUPLICATE-ROWS",
        domain="render",
        title="Bar chart data has duplicate rows that require aggregation",
        message_template=(
            "{chart_type} chart {chart_id!r} requires pre-aggregated data "
            "with at most one row per plotted key ({field_list}). "
            "Found duplicate rows for {duplicate_preview}. "
            "Aggregate in the query before rendering."
        ),
        doc=(
            "Fired when a bar, horizontal-bar, or grouped-bar chart receives data "
            "with more than one row per plotted key. These chart types require "
            "pre-aggregated data; aggregate in the query before rendering."
        ),
        docs_topic="charts",
    )
)

ERR_GAP_FILL_BUCKET_COLLISION = REGISTRY.register(
    ErrorCode(
        code="ERR-GAP-FILL-BUCKET-COLLISION",
        domain="render",
        title="Two rows collapse to the same gap-fill bucket",
        message_template=(
            "Rows with {x_field!r} values {value_a!r} and {value_b!r} both "
            "collapse to the {time_unit!r} bucket {bucket!r}{dim_desc}. "
            "Aggregate to {time_unit} grain in the query before rendering."
        ),
        doc=(
            "Fired when gap-filling an ordinal bucketed-time axis finds two "
            "rows whose x-values fall inside the same bucket: two timestamps "
            "on one calendar day under `yearmonthdate`, or a finer-grained "
            "series under a coarser authored `time_unit` (monthly rows under "
            "`yearquarter`, daily rows under `yearmonth`). A coarser grain "
            "places rows in buckets; it does not combine them, and a "
            "last-wins merge would silently discard all but one. Aggregate "
            "to the bucket grain in the query before rendering."
        ),
        summary="Fired when two rows collapse to the same gap-fill bucket.",
        docs_topic="charts",
    )
)

ERR_COLOR_NULL_SERIES = REGISTRY.register(
    ErrorCode(
        code="ERR-COLOR-NULL-SERIES",
        domain="render",
        title="Color column contains NULL values",
        message_template=(
            "{chart_type} chart {chart_id!r} has {null_rows} row(s) with a NULL "
            "value in its color column {color_field!r}. A NULL category cannot "
            "be painted or named in the legend, but its rows still occupy "
            "stack space; the chart would read as bars floating off the "
            "baseline. Give every row a category in the query "
            "(e.g. COALESCE({color_field}, 'Unknown'))."
        ),
        doc=(
            "Fired when the column bound to a chart's `color` channel contains "
            "NULL values. The renderer cannot assign a NULL a palette slot or a "
            "legend entry, so the series would consume stack space while being "
            "invisible and unattributable. Fix the grain in the query: the most "
            "common cause is a `CASE` with no `ELSE`, or an `ELSE` that passes "
            "the raw column through unchanged."
        ),
        summary="Fired when a chart's color column contains NULL values.",
        docs_topic="charts",
    )
)

ERR_PIE_NULL_THETA = REGISTRY.register(
    ErrorCode(
        code="ERR-PIE-NULL-THETA",
        domain="render",
        title="Pie chart theta column contains NULL values",
        message_template=(
            "Pie chart {chart_id!r} has {null_rows} row(s) with a NULL value "
            "in its theta column {theta_field!r}. A pie slice can't represent "
            "a missing value. Filter the null rows or give them a real number "
            "in the query (e.g. COALESCE({theta_field}, 0))."
        ),
        doc=(
            "Fired when the column bound to a pie or donut chart's `theta` "
            "channel contains NULL values. A pie slice's angle comes directly "
            "from theta, so a NULL cannot be drawn or labeled; fix the grain "
            "in the query instead of letting the renderer guess a value."
        ),
        summary="Fired when a pie chart's theta column contains NULL values.",
        docs_topic="charts",
    )
)

ERR_PIE_NEGATIVE_THETA = REGISTRY.register(
    ErrorCode(
        code="ERR-PIE-NEGATIVE-THETA",
        domain="render",
        title="Pie chart theta column contains a negative value",
        message_template=(
            "Pie chart {chart_id!r} has {negative_rows} row(s) with a "
            "negative value in its theta column {theta_field!r}. A pie slice "
            "can't represent a negative share of the whole. Filter or "
            "transform the value in the query."
        ),
        doc=(
            "Fired when the column bound to a pie or donut chart's `theta` "
            "channel contains a negative value. A pie's slices are angles "
            "summing to a full circle; a negative theta has no geometric "
            "meaning, so it must be filtered or transformed in the query."
        ),
        summary=("Fired when a pie chart's theta column contains a negative value."),
        docs_topic="charts",
    )
)

ERR_MAP_LOOKUP_KEY_MISMATCH = REGISTRY.register(
    ErrorCode(
        code="ERR-MAP-LOOKUP-KEY-MISMATCH",
        domain="render",
        title="Map chart cannot join on mismatched key format",
        message_template=(
            "Map chart {chart_id!r} cannot join lookup field {lookup_field!r} "
            "to geo source {geo_source!r} on key {geo_key!r}: the geo source "
            "expects {expected_format} keys like {expected_samples}, but the "
            "query returned values like {query_samples}."
        ),
        doc=(
            "Fired when a map chart's lookup field values do not match the format "
            "expected by the geo source. Check that the lookup field uses the same "
            "key format (e.g. FIPS codes, ISO country codes) as the geo source."
        ),
        docs_topic="charts",
    )
)

ERR_FORMAT_UNSUPPORTED = REGISTRY.register(
    ErrorCode(
        code="ERR-FORMAT-UNSUPPORTED",
        domain="render",
        title="Unknown render format",
        message_template="Unknown format: {format!r}",
        doc=(
            "Fired when a render verb is called with an output format that is not "
            "supported. Check the supported formats in the CLI reference."
        ),
        docs_topic="errors",
    )
)

ERR_FORMAT_CONVERTER_UNAVAILABLE = REGISTRY.register(
    ErrorCode(
        code="ERR-FORMAT-CONVERTER-UNAVAILABLE",
        domain="render",
        title="Format conversion library is not installed",
        message_template=(
            "{format!r} rendering requires vl-convert-python. Install it "
            "with: pip install vl-convert-python"
        ),
        doc=(
            "Fired when a PNG or PDF export (or any render path that needs "
            "vl-convert-python to turn a Vega-Lite spec into SVG/PNG/PDF) "
            "runs in an environment where vl-convert-python is not "
            "installed. Install it: `pip install vl-convert-python`."
        ),
        docs_topic="errors",
    )
)

ERR_FORMAT_CONVERSION_FAILED = REGISTRY.register(
    ErrorCode(
        code="ERR-FORMAT-CONVERSION-FAILED",
        domain="render",
        title="Format conversion failed",
        message_template=(
            "{format!r} export failed while converting the rendered board "
            "({detail}). Try exporting {alt_formats} instead, or split the "
            "board into smaller boards."
        ),
        doc=(
            "Fired when the vl-convert-python converter raises while "
            "turning a rendered board into PNG or PDF bytes. On PDF "
            "exports, the most common permanent cause is the PDF format's "
            "own 28-level graphics-state nesting limit: a sufficiently "
            "large or deeply nested board can exceed it. Try a different "
            "export format, or split the board into smaller boards. The "
            "underlying converter message is preserved in the diagnostic "
            "detail."
        ),
        docs_topic="errors",
    )
)

ERR_RESOLVED_PIE_WIDTH_MISMATCH = REGISTRY.register(
    ErrorCode(
        code="ERR-RESOLVED-PIE-WIDTH-MISMATCH",
        domain="render",
        title="Resolved pie width does not match its render slot",
        message_template=(
            "Resolved pie {chart_id!r} was finalized at width {resolved_width}, "
            "not {render_width}. Resolve the chart again for the target slot."
        ),
        doc=(
            "Fired when a resolved pie is rendered at a different width from the "
            "one used to finalize its label and attached-table layout. Resolve the "
            "chart again with the actual target width before rendering."
        ),
        docs_topic="charts",
    )
)

ERR_RESOLVED_PIE_DATA_MISMATCH = REGISTRY.register(
    ErrorCode(
        code="ERR-RESOLVED-PIE-DATA-MISMATCH",
        domain="render",
        title="Resolved pie rows do not match its recording",
        message_template=(
            "Resolved pie {chart_id!r} was finalized from different query rows. "
            "Resolve the chart and record its data in the same emission."
        ),
        doc=(
            "Fired when replay data differs from the rows used to finalize pie "
            "label and attached-table policy. Resolved board artifacts and recordings "
            "must come from the same emission."
        ),
        docs_topic="charts",
    )
)

ERR_DUPLICATE_CHART_ID = REGISTRY.register(
    ErrorCode(
        code="ERR-DUPLICATE-CHART-ID",
        domain="render",
        title="Two charts share an id across nested boards",
        message_template=(
            "Two charts share the id {chart_id!r} across nested boards, which the "
            "flat {format!r} format cannot represent without dropping one. "
            "Rename one of them, or use --format json, which keeps the layout "
            "nesting."
        ),
        doc=(
            "Chart ids are unique within a board but not across a board tree: two "
            "imported partials, including the same partial imported twice, can "
            "declare the same id. The flat output formats key charts by id, so a "
            "collision would silently drop every chart but the last. Rename the "
            "colliding chart, or render with a format that preserves the layout."
        ),
        docs_topic="errors",
    )
)

ERR_NO_LAYOUT = REGISTRY.register(
    ErrorCode(
        code="ERR-NO-LAYOUT",
        domain="render",
        title="Board defines charts but has no layout",
        message_template=(
            "Board defines charts ({charts}) but no layout: would render with no visible "
            "charts. Add a `rows:`/`cols:`/`grid:`/`tabs:` block that references them."
        ),
        doc=(
            "Fired when a board defines charts but no layout block "
            "(rows/cols/grid/tabs). Without a layout, the board would render with "
            "no visible charts. Add a layout block that references the charts."
        ),
        docs_topic="layout",
    )
)

ERR_INPUT_INVALID = REGISTRY.register(
    ErrorCode(
        code="ERR-INPUT-INVALID",
        domain="render",
        title="Invalid render input",
        message_template="{message}",
        doc=(
            "Fired when the render layer receives input data that fails a "
            "structural check. The message carries the specific validation error."
        ),
        docs_topic="charts",
    )
)

ERR_VEGA_LITE_UNSUPPORTED_TYPE = REGISTRY.register(
    ErrorCode(
        code="ERR-VEGA-LITE-UNSUPPORTED-TYPE",
        domain="render",
        title="Chart type does not render to a Vega-Lite spec",
        message_template=(
            "Chart type {chart_type!r} does not render to a Vega-Lite spec."
        ),
        doc=(
            "Fired when a chart type is asked to render a Vega-Lite spec but does "
            "not support that output format. Use a Vega-Lite-compatible chart type "
            "or choose a different output format."
        ),
        docs_topic="charts",
    )
)

# WHY: Histogram requires a numeric x field for binning. A missing or non-numeric
# x field would silently pass bin:True to VL on a nominal column (garbage output)
# or crash vl_convert on a null field.
ERR_HISTOGRAM_NON_NUMERIC = REGISTRY.register(
    ErrorCode(
        code="ERR-HISTOGRAM-NON-NUMERIC",
        domain="render",
        title="Histogram x field is not numeric",
        message_template=(
            "Histogram chart {chart_id!r} requires a numeric x field for binning, "
            "but {field!r} is {vl_type!r}. Use a quantitative (numeric) column as x."
        ),
        doc=(
            "Fired when a histogram chart's x field is not numeric (quantitative). "
            "Histograms bin values into ranges, which requires a numeric column. "
            "Use a quantitative column as x."
        ),
        docs_topic="charts",
    )
)

# WHY: see validate_raw_rows_for_histogram's docstring (render/chart/validation.py)
# for the full reasoning behind the two-condition check this code reports.
ERR_HISTOGRAM_PREAGGREGATED = REGISTRY.register(
    ErrorCode(
        code="ERR-HISTOGRAM-PREAGGREGATED",
        domain="render",
        title="Histogram data looks pre-aggregated, not raw rows",
        message_template=(
            "Histogram chart {chart_id!r} received data where {field!r} forms "
            "a gapless run of whole numbers alongside unused numeric column(s) "
            "{count_fields}: this looks like pre-aggregated data (one row "
            "per bucket, e.g. `GROUP BY {field}`), not the raw, ungrouped "
            "rows a histogram bins itself. Vega-Lite would bin and count "
            "these already-counted rows again, silently discarding whatever "
            "real measure they carry. Use `type: bar` with `x: {field}` and "
            "one of {count_fields} as `y` to chart pre-aggregated data instead."
        ),
        doc=(
            "Fired when a histogram chart receives data where its x field forms "
            "a gapless run of whole numbers alongside an unused numeric column, "
            "the shape of already-aggregated, one-row-per-bucket data. "
            "Histograms rely on Vega-Lite's own binning + counting over raw, "
            "ungrouped rows; pre-aggregated data silently produces a wrong, "
            "miscounted histogram instead of erroring. Aggregate in the query "
            "and use `type: bar` instead."
        ),
        docs_topic="charts",
    )
)

ERR_LABEL_FORMAT_AXIS_MISMATCH = REGISTRY.register(
    ErrorCode(
        code="ERR-LABEL-FORMAT-AXIS-MISMATCH",
        domain="render",
        title="a non-time axis format needs numeric tick values",
        # The message states what the FIELD holds rather than calling the
        # spec a number format — a spec Vega rejects outright (the `%%`
        # literal-percent escape) reaches here too, and is not a number
        # format by any reading.
        message_template=(
            "style.{setting} ({fmt!r}) cannot be read on this "
            "axis: {field!r} holds non-numeric tick labels. {remedy}"
        ),
        doc=(
            "Fired when `style.axis_x.labels.format`, "
            "`style.axis_y.labels.format` or `style.axis_y.mirror.format` is "
            "anything but a d3 time spec on an axis whose field resolves to "
            "nominal, ordinal or temporal. "
            "All three matter: nominal is a plain category column, ordinal "
            "covers the date-like buckets (`2024-01`, `Q1 2024`, `FY2024`) "
            "that are still band-scale strings, and temporal is a real "
            "date/time field. On a band scale Vega coerces every tick to "
            "`NaN` rather than failing; on a temporal one it reads the spec "
            "as a time spec instead and paints its literal text (`$,.0f`) "
            "across the axis. Either way it raises here now. A horizontal "
            "bar is the case worth calling out: its `axis_x` addresses the "
            "categories, which the rotation draws down the left edge, so an "
            "author formatting what looks like the value axis reaches the "
            "wrong channel. The measure is `axis_y` in both orientations; on "
            "a heatmap the value is the color channel and neither axis "
            "carries it; on a scatter dot plot the measure is `axis_x` "
            "instead, since the categorical channel there is `axis_y`. "
            "Numeric categories on a band scale are NOT gated: they format "
            "cleanly, so nothing distinguishes an intended format from a "
            "misaddressed one — a temporal axis has no such exemption, since "
            "no reading of a number format over dates was ever the author's "
            "intent."
        ),
        summary=(
            "Fired when a non-time format is authored on an axis holding "
            "category or date tick labels."
        ),
        docs_topic="charts",
    )
)

ERR_LABEL_VALUES_NOT_TEMPORAL = REGISTRY.register(
    ErrorCode(
        code="ERR-LABEL-VALUES-NOT-TEMPORAL",
        domain="render",
        title="labels.values requires a temporal x-axis",
        # {cause}/{remedy} are supplied per raise site rather than hardcoded,
        # because two unrelated conditions share this code: the x-axis values
        # aren't valid ISO dates (a data problem — fixable by using ISO dates),
        # and a horizontal bar's categorical axis never honors labels.values at
        # all regardless of date format (an orientation problem — no date fix
        # applies). A single fixed message asserting "your dates aren't ISO"
        # would be false on the second path.
        message_template="style.axis_x.labels.values isn't usable on {field!r}: {cause}. {remedy}",
        doc=(
            "Fired when `style.axis_x.labels.values` is set on an x-axis that can't "
            "honor it: either the x-axis values aren't valid ISO dates or datetime "
            "objects, or the chart's horizontal-bar categorical axis never applies "
            "label filtering regardless of date format."
        ),
        summary="Fired when `labels.values` is set on an axis that can't honor it.",
        docs_topic="charts",
    )
)

ERR_TICKS_STEP_NOT_QUANTITATIVE = REGISTRY.register(
    ErrorCode(
        code="ERR-TICKS-STEP-NOT-QUANTITATIVE",
        domain="render",
        title="a bare ticks.step requires a quantitative x-axis",
        message_template=(
            "style.axis_x.ticks.step without ticks.time_unit is a numeric tick "
            "interval, but {field!r} resolved to a {vl_type!r} scale. {remedy}"
        ),
        doc=(
            "Fired when `style.axis_x.ticks.step` is set without "
            "`ticks.time_unit` on an x-axis that does not resolve to a "
            "quantitative scale. A bare `step` is the numeric cadence lever "
            "(it emits Vega-Lite's `tickMinStep`) and only a quantitative "
            "scale has a numeric tick interval. On a temporal axis, author "
            "`ticks.time_unit` alongside `step` to name a calendar cadence. "
            "On a discrete axis (ordinal or nominal) there is no tick "
            "interval to set; remove `ticks.step`. A horizontal bar is the "
            "case worth calling out: its `axis_x` is the categorical axis and "
            "its measure is `axis_y`, so `orientation: vertical` is usually "
            "what the author wanted."
        ),
        summary="Fired when a bare `ticks.step` is set on a non-quantitative x-axis.",
        docs_topic="charts",
    )
)

ERR_TICKS_INTERVAL_NOT_TEMPORAL = REGISTRY.register(
    ErrorCode(
        code="ERR-TICKS-INTERVAL-NOT-TEMPORAL",
        domain="render",
        title="ticks.time_unit requires a continuous temporal x-axis",
        message_template=(
            "style.axis_x.ticks.time_unit requires a continuous temporal "
            "x-axis, but {field!r} resolved to a {vl_type!r} scale. Force a "
            "continuous scale with axis_x.type: temporal (or "
            "axis_x.time_unit: none), or remove ticks.time_unit."
        ),
        doc=(
            "Fired when `style.axis_x.ticks.time_unit` is set but the x-axis "
            "does not resolve to a continuous temporal scale. Force a continuous "
            "scale with `axis_x.type: temporal` (or `axis_x.time_unit: none`), "
            "or remove `ticks.time_unit`."
        ),
        summary="Fired when `ticks.time_unit` is set but the x-axis doesn't resolve to a continuous temporal scale.",
        docs_topic="charts",
    )
)

ERR_LABEL_VALUES_INVALID_DATE = REGISTRY.register(
    ErrorCode(
        code="ERR-LABEL-VALUES-INVALID-DATE",
        domain="render",
        title="labels.values entry is not a valid ISO date",
        message_template=(
            "style.axis_x.labels.values entries must be ISO date or datetime "
            "strings (e.g. '2024-01-01') or date/datetime objects; got {value!r}."
        ),
        doc=(
            "Fired when an entry in `style.axis_x.labels.values` cannot be parsed "
            "as an ISO date or datetime. Use ISO date strings (e.g. '2024-01-01') "
            "or date/datetime objects."
        ),
        summary="Fired when an axis label value can't be parsed as an ISO date or datetime.",
        docs_topic="charts",
    )
)

ERR_PERCENT_RANGE = REGISTRY.register(
    ErrorCode(
        code="ERR-PERCENT-RANGE",
        domain="render",
        title="Percent format received a 0-100-shaped value instead of a ratio",
        message_template=(
            "Value {value!r} passed to percent format {format_spec!r} looks "
            "0-100-shaped, but percent formats expect a 0-1 ratio (0.182, not "
            "18.2). To fix: divide by 100 in SQL so the value is a ratio, or "
            "switch to the `percent_number`/`percent_number_delta` formats if "
            "the value is already in the 0-100 scale (e.g. 18.2 means 18.2%)."
        ),
        doc=(
            "Fired when a percent format receives a value that looks like it is "
            "already in the 0-100 scale rather than the 0-1 ratio scale that "
            "percent formats expect. Divide by 100 in SQL so the value is a ratio, "
            "or switch to a `percent_number` format."
        ),
        docs_topic="charts",
    )
)

ERR_NUMERAL_EXPR_EMPTY_SPEC = REGISTRY.register(
    ErrorCode(
        code="ERR-NUMERAL-EXPR-EMPTY-SPEC",
        domain="render",
        title="Empty format spec cannot build a numeral Vega expression",
        message_template=(
            "numeral_vega_expr() requires a non-empty format_spec. format_d3 "
            "treats an empty spec as a distinct 'no d3 formatting' path (the "
            "bare Python value) that a Vega expression cannot reproduce "
            "byte-for-byte."
        ),
        doc=(
            "Fired when numeral_vega_expr() is called with an empty format_spec. "
            "format_d3 short-circuits an empty spec to the bare Python value, "
            "bypassing d3 entirely: a Vega expression cannot reproduce that "
            "byte-for-byte (Python and JS do not stringify numbers identically), "
            "so building the expression is rejected rather than silently "
            "diverging. This indicates an engine bug: callers should always "
            "resolve a concrete d3 spec before reaching this emitter."
        ),
        docs_topic="errors",
    )
)

ERR_EMITTER_NOT_FOUND = REGISTRY.register(
    ErrorCode(
        code="ERR-EMITTER-NOT-FOUND",
        domain="render",
        title="No emitter registered for the resolved chart type",
        message_template=(
            "No emitter registered for resolved chart type {resolved_type!r}. "
            "This indicates an engine bug; the normalizer should have rejected "
            "this chart before it reached render."
        ),
        doc=(
            "Fired when the render engine cannot find an emitter for the resolved "
            "chart type. This indicates an engine bug; the normalizer should have "
            "rejected this chart before it reached render."
        ),
        docs_topic="errors",
    )
)

ERR_STACKED_MIDDLE_ALIGNED_LABELS = REGISTRY.register(
    ErrorCode(
        code="ERR-STACKED-MIDDLE-ALIGNED-LABELS",
        domain="render",
        title="labels.position: middle_aligned is not meaningful on a stacked bar",
        message_template=(
            "labels.position 'middle_aligned' aligns every bar's label to one "
            "shared height, which has no meaning for the segments of a stacked "
            "bar. Use 'middle' to center each label in its own segment, or "
            "'top'/'bottom' to pin it to a segment edge."
        ),
        doc=(
            "Fired when `labels.position: middle_aligned` is set on a stacked "
            "bar. `middle_aligned` places every label at a single common height "
            "(the mean bar height, halved) so a row of labels reads as one line, "
            "a whole-bar idea with no per-segment reading. Stacked segments "
            "each need their own center: use `middle`."
        ),
        summary=(
            "Fired when `labels.position: middle_aligned` is set on a stacked bar."
        ),
        docs_topic="charts",
    )
)

ERR_LABELS_FIELD_NOT_FOUND = REGISTRY.register(
    ErrorCode(
        code="ERR-LABELS-FIELD-NOT-FOUND",
        domain="render",
        title="labels.field names a column not in the query result",
        message_template=(
            "Chart {chart_id!r}: labels.field {field!r} on {source} names a "
            "column not present in its data. Available columns: {available}."
        ),
        doc=(
            "Fired when `labels.field` names a column that is not present in "
            "the query result. `source` identifies which slot fired: the base "
            "chart's own labels, or a specific overlay layer (by position, "
            "type, and query), since a chart's overlay `layers:` can each "
            "carry their own `labels.field`. Check the column name against the "
            "actual columns returned by that slot's query."
        ),
        summary="Fired when `labels.field` names a column that isn't present in the query result.",
        docs_topic="charts",
    )
)

# WHY: an authored 2-element scale.domain only makes sense against a continuous
# (temporal/quantitative) x scale. On an ordinal/nominal (band) x scale
# Vega-Lite reads a 2-element domain as exactly two category values,
# collapsing every mark onto the first one.
ERR_SCALE_DOMAIN_REQUIRES_CONTINUOUS_X = REGISTRY.register(
    ErrorCode(
        code="ERR-SCALE-DOMAIN-REQUIRES-CONTINUOUS-X",
        domain="render",
        title="axis_x.scale.domain requires a continuous x-axis scale",
        message_template=(
            "Chart {chart_id!r}: axis_x.scale.domain is set, but the x-axis "
            "resolved to a {vl_type!r} (categorical) scale, not a continuous "
            "one. An explicit [low, high] domain only extends a continuous "
            "scale: on a categorical scale Vega-Lite reads it as exactly two "
            "category values, collapsing every mark onto the first one. If "
            "the x field is a date, add `axis_x.scale.type: temporal` to "
            "force a continuous temporal scale."
        ),
        doc=(
            "Fired when `axis_x.scale.domain` is set but the x-axis resolves to "
            "a categorical (ordinal/nominal) scale rather than a continuous one. "
            "An explicit [low, high] domain only extends a continuous scale; on a "
            "categorical scale Vega-Lite reads it as exactly two category values. "
            "Add `axis_x.scale.type: temporal` to force a continuous temporal scale "
            "if needed."
        ),
        summary="Fired when an explicit x-axis domain is set but the x-axis resolves to a categorical scale.",
        docs_topic="charts",
    )
)

ERR_CONCAT_OVERSHOOT_NONPOSITIVE = REGISTRY.register(
    ErrorCode(
        code="ERR-CONCAT-OVERSHOOT-NONPOSITIVE",
        domain="render",
        title="Overshoot correction produced a non-positive pane width",
        message_template=(
            "Overshoot correction produced a non-positive pane width "
            "({new_w:.1f}px): pane width {orig_w:.1f}px minus overshoot "
            "{overshoot:.1f}px. The chart content (title, subtitle, axis labels, "
            "series labels, or legend) is wider than the available canvas "
            "({target_width:.1f}px)."
        ),
        doc=(
            "Fired when the overshoot correction algorithm for a concatenated "
            "layout produces a non-positive pane width. The chart content (title, "
            "subtitle, axis labels, series labels, or legend) is wider than the "
            "available canvas. Series labels come from the column bound to "
            "`color:`, and are the usual cause when that column holds long text."
        ),
        docs_topic="charts",
    )
)

ERR_BOARD_ARTIFACT_INVALID = REGISTRY.register(
    ErrorCode(
        code="ERR-BOARD-ARTIFACT-INVALID",
        domain="render",
        title="Resolved-board artifact does not match the expected schema",
        message_template=(
            "Board artifact is invalid: {detail}. It may have been produced by "
            "an incompatible dct version, hand-edited, or truncated. Re-emit it "
            "with `dct artifact emit`."
        ),
        doc=(
            "Fired when a resolved-board artifact fails to validate against "
            "`ResolvedBoard` while loading it for replay. The artifact is the "
            "published, versioned contract a resolved board serializes to; this "
            "means the file is not a valid instance of that contract."
        ),
        docs_topic="errors",
    )
)

ERR_BOARD_RECORDING_INVALID = REGISTRY.register(
    ErrorCode(
        code="ERR-BOARD-RECORDING-INVALID",
        domain="render",
        title="Board recording sidecar does not match the expected schema",
        message_template=(
            "Board recording is invalid: {detail}. It may have been produced by "
            "an incompatible dct version, hand-edited, or truncated. Re-emit it "
            "with `dct artifact emit`."
        ),
        doc=(
            "Fired when a board recording sidecar fails to validate against "
            "`BoardRecording` while loading it for replay."
        ),
        docs_topic="errors",
    )
)

ERR_BOARD_RECORDING_MISMATCH = REGISTRY.register(
    ErrorCode(
        code="ERR-BOARD-RECORDING-MISMATCH",
        domain="render",
        title="Board recording does not match the artifact it was replayed against",
        message_template=(
            "Board recording does not match this artifact: {detail} The artifact "
            "and its recording must come from the same `dct artifact emit` run."
        ),
        doc=(
            "Fired when replaying a resolved-board artifact against a recording "
            "that either lacks rows for one of the artifact's queries, or "
            "recorded them under different variable values. Both mean the "
            "artifact and recording came from different emits (or a truncated "
            "one); replaying anyway would render an empty or wrong chart that "
            "looks like real data."
        ),
        docs_topic="errors",
    )
)

ERR_CHART_PAINTED_NO_MARKS = REGISTRY.register(
    ErrorCode(
        code="ERR-CHART-PAINTED-NO-MARKS",
        domain="render",
        title="Chart received rows but painted no marks",
        message_template=(
            "Chart {chart_id!r} received {row_count} row(s) but every mark it drew "
            "has zero width or height; check whether the x/y fields and scale "
            "types match the data's actual shape."
        ),
        doc=(
            "Fired when a plotting-family chart's query returns at least one row "
            "but the rendered SVG contains no mark with visible extent: every "
            "bar, line, area, point, wedge, or shape it drew is degenerate. "
            "`WARN-QUERY-RETURNED-ZERO-ROWS` covers the honest empty case (no "
            "rows); this covers the dishonest one: rows arrived, the renderer "
            "just didn't paint anything visible with them."
        ),
        docs_topic="charts",
    )
)

ERR_MULTIPLES_ENDPOINT_LABELS = REGISTRY.register(
    ErrorCode(
        code="ERR-MULTIPLES-ENDPOINT-LABELS",
        domain="render",
        title="multiples cannot be combined with endpoint labels",
        message_template=(
            "Chart {chart_id!r}: multiples cannot be combined with endpoint "
            "labels; the endpoint-label rail names series for a single "
            "panel, and a faceted chart has no single panel for it to sit "
            "beside. Set style.endpoint_labels.visible: false on this chart "
            "to keep the small multiples, or remove multiples to keep the "
            "labels."
        ),
        doc=(
            "Fired when a chart authors `multiples:` while its endpoint-label "
            "rail is explicitly switched on. The rail names series for one "
            "panel; a faceted chart has no single panel for it to sit beside. "
            "The shipped default switches the rail off wherever `multiples:` "
            "is set (a legend above the panels names the series instead), so "
            "this fires only where the rail was asked for by name. Turn off "
            "`style.endpoint_labels.visible` to keep the small multiples, or "
            "remove `multiples:` to keep the labels."
        ),
        docs_topic="charts",
    )
)

ERR_MIRROR_ENDPOINT_LABELS = REGISTRY.register(
    ErrorCode(
        code="ERR-MIRROR-ENDPOINT-LABELS",
        domain="render",
        title="axis_y.mirror cannot be combined with endpoint labels",
        message_template=(
            "Chart {chart_id!r}: axis_y.mirror is not supported together "
            "with endpoint labels; the endpoint-label rail occupies the "
            "opposite edge. Use one or the other on this chart."
        ),
        doc=(
            "Fired when a chart authors (or its theme sets) "
            "`style.axis_y.mirror` while its endpoint-label rail is also "
            "visible. Both want the opposite edge from the chart's primary "
            "y-axis; the mirrored scale and the label rail can't share it. "
            "Distinct from ERR-MULTIPLES-ENDPOINT-LABELS: this code fires "
            "when the chart has no `multiples:` at all, so `axis_y.mirror` "
            "is the field actually responsible for the collision; a "
            "`multiples:` grid's collision (even one that also happens to "
            "auto-derive a both-edge axis internally) is reported as "
            "ERR-MULTIPLES-ENDPOINT-LABELS instead, since `multiples:` is "
            "the field the author wrote."
        ),
        summary=(
            "Fired when a chart authors (or its theme sets) "
            "`style.axis_y.mirror` while its endpoint-label rail is also "
            "visible."
        ),
        docs_topic="charts",
    )
)

ERR_ENDPOINT_LABELS_UNORDERABLE_SORT = REGISTRY.register(
    ErrorCode(
        code="ERR-ENDPOINT-LABELS-UNORDERABLE-SORT",
        domain="render",
        title="sort by a non-numeric column cannot be combined with stacked endpoint labels",
        message_template=(
            "Chart {chart_id!r}: chart.sort by {sort_by!r} cannot be "
            "combined with stacked bar endpoint labels. The rail places "
            "its labels from the order the sorted axis draws, and dbt "
            "Charts confirms that order only for a column carrying numeric "
            "values. Sort by a measure instead, or set "
            "style.endpoint_labels.visible: false on this chart."
        ),
        doc=(
            "Fired when a stacked bar chart authors `sort:` by a column "
            "that carries no numeric values while its endpoint-label rail "
            "is visible. The rail places its labels from the order the "
            "sorted axis draws (a vertical rail anchors each series at its "
            "own last drawn column, a horizontal one anchors every series "
            "on the top row), so it has to know that order before the "
            "chart renders. dbt Charts confirms it only for a numeric "
            "sort column, and labels placed from the wrong order name "
            "series in the wrong place, which is worse than a legend. Sort "
            "by a measure, or turn the rail off with "
            "`style.endpoint_labels.visible: false`."
        ),
        docs_topic="charts",
    )
)

ERR_ENDPOINT_LABELS_NEGATIVE_STACK = REGISTRY.register(
    ErrorCode(
        code="ERR-ENDPOINT-LABELS-NEGATIVE-STACK",
        domain="render",
        title="negative values are not supported for stacked endpoint labels",
        message_template=(
            "Chart {chart_id!r}: negative values are not supported for "
            "stacked bar/area endpoint labels — they break the "
            "cumulative-midpoint computation. Set "
            "style.endpoint_labels.visible: false on this chart."
        ),
        doc=(
            "Fired when a stacked bar or area chart's data carries a "
            "negative measure while its endpoint-label rail is visible. "
            "The rail anchors each label at its segment's cumulative "
            "midpoint, a computation that assumes every segment in the "
            "stack contributes in the same direction; a negative value "
            "crosses zero and breaks it. Turn the rail off with "
            "`style.endpoint_labels.visible: false`."
        ),
        docs_topic="charts",
    )
)

ERR_ENDPOINT_LABELS_CENTER_STACK = REGISTRY.register(
    ErrorCode(
        code="ERR-ENDPOINT-LABELS-CENTER-STACK",
        domain="render",
        title="stack: center is not supported for horizontal endpoint labels",
        message_template=(
            "Chart {chart_id!r}: stack: center is not supported for horizontal "
            "stacked bar endpoint labels — the rail anchors on the cumulative "
            "(0..Σ) axis, which a center stack (-Σ/2..+Σ/2) does not have. Use "
            "stack: zero, or set style.endpoint_labels.visible: false on this "
            "chart."
        ),
        doc=(
            "Fired when a horizontal stacked bar chart uses `stack: center` "
            "while its endpoint-label rail is forced on. The rail anchors each "
            "label at its segment's cumulative midpoint from zero; a center "
            "stack has no such axis. Use `stack: zero`, or turn the rail off "
            "with `style.endpoint_labels.visible: false`."
        ),
        docs_topic="charts",
    )
)

ERR_MIRROR_MULTI_SERIES = REGISTRY.register(
    ErrorCode(
        code="ERR-MIRROR-MULTI-SERIES",
        domain="render",
        title="axis_y.mirror requires a single y field",
        message_template=(
            "Chart {chart_id!r}: axis_y.mirror is not supported on multi-series "
            "charts (y = {y}); a mirrored axis restates one shared y-scale, "
            "and folded measures have no single scale to restate. Collapse "
            "`y:` to one field, or drop `style.axis_y.mirror`."
        ),
        doc=(
            "Fired when a chart carries a list-valued `y:` while "
            "`style.axis_y.mirror` is on. Mirror draws the same y-scale on both "
            "edges of a wide chart, which only means something when there is "
            "exactly one measure scale to draw. Reported at render rather than "
            "by `dct validate` because mirror is cascade-resolved: a theme "
            "layer can turn it on for a chart that never authored it, so the "
            "combination is not visible on the authored chart alone."
        ),
        summary=(
            "Fired when a chart with a list-valued `y:` also has "
            "`style.axis_y.mirror` on, from either the chart or its theme."
        ),
        docs_topic="charts",
    )
)

ERR_LAYER_AXIS_POSITION_ORIENTATION = REGISTRY.register(
    ErrorCode(
        code="ERR-LAYER-AXIS-POSITION-ORIENTATION",
        domain="render",
        title="axis_y.position on a layer needs a vertical base chart",
        message_template=(
            "Chart {chart_id!r}: a layer's `axis_y.position` names a left or "
            "right side, and `style.orientation: horizontal` measures along "
            "the horizontal axis, whose sides are top and bottom. Drop "
            "`axis_y.position`, or set `style.orientation: vertical`."
        ),
        doc=(
            "Fired when a layer pins `axis_y.position` on a chart whose base "
            "is horizontal. The resolved model always measures on `y` and "
            "carries its category on `x`; a horizontal bar paints that same "
            "model with the pair swapped, so its measure axis runs horizontally "
            "and has no left or right side to pin a second axis to. "
            "Whether left/right should map onto bottom/top is a design "
            "decision nobody has taken, so this refuses rather than drawing "
            "the layer against a side the author did not ask for."
        ),
        summary=("Fired when a layer sets `axis_y.position` on a horizontal base."),
        docs_topic="charts",
    )
)

ERR_LAYER_AXIS_POSITION_ENDPOINT_LABELS = REGISTRY.register(
    ErrorCode(
        code="ERR-LAYER-AXIS-POSITION-ENDPOINT-LABELS",
        domain="render",
        title="axis_y.position on a layer cannot be combined with endpoint labels",
        message_template=(
            "Chart {chart_id!r}: style.endpoint_labels.visible is not "
            "supported on a layered chart whose layers pin an explicit "
            "axis_y.position — the label rail anchors on one shared "
            "y-scale, and a dual-axis layer renders on a different one. "
            "Remove the per-layer axis_y.position override, or set "
            "style.endpoint_labels.visible: false on this chart."
        ),
        doc=(
            "Fired when a layered chart's endpoint-label rail is visible "
            "(authored or theme-set) while one of its layers pins its own "
            "`axis_y.position`. The rail anchors every layer's labels on "
            "the base chart's single shared y-scale; a layer with a pinned "
            "`axis_y.position` is a genuine dual-axis layer rendering on a "
            "different scale, which the rail has no way to represent. "
            "Remove the per-layer `axis_y.position`, or turn the rail off "
            "with `style.endpoint_labels.visible: false`."
        ),
        summary=(
            "Fired when a layered chart's endpoint-label rail is visible "
            "while one of its layers pins its own axis_y.position."
        ),
        docs_topic="charts",
    )
)

ERR_LAYER_STEP_ORIENTATION = REGISTRY.register(
    ErrorCode(
        code="ERR-LAYER-STEP-ORIENTATION",
        domain="render",
        title="curve: step on a layer needs a vertical base chart",
        message_template=(
            "Chart {chart_id!r}: a layer's `curve: step` builds its band "
            "offset along the horizontal axis, which "
            "`style.orientation: horizontal` uses for the measure. Drop "
            "`curve: step` on the layer, or set "
            "`style.orientation: vertical`."
        ),
        doc=(
            "Fired when a layer authors `style.marks.line.curve: step` on a "
            "chart whose base is horizontal. The band-aware step transform "
            "doubles each row to its band edges and separates them with an "
            "`xOffset` scale sized by `bandwidth('x')`: the channel a "
            "horizontal base measures on rather than the one it bands. "
            "Refused rather than offsetting the layer along an axis that has "
            "no bands to measure."
        ),
        summary=("Fired when a layer sets `curve: step` on a horizontal base."),
        docs_topic="charts",
    )
)

ERR_MIRROR_LAYERS = REGISTRY.register(
    ErrorCode(
        code="ERR-MIRROR-LAYERS",
        domain="render",
        title="axis_y.mirror is not supported with layers",
        message_template=(
            "Chart {chart_id!r}: axis_y.mirror is not supported on a chart "
            "with `layers:`; each layer owns its own y encoding, so there is "
            "no single shared y encoding for the mirrored edge to restate. "
            "Drop `style.axis_y.mirror` or the `layers:`."
        ),
        doc=(
            "Fired when a chart carries `layers:` while `style.axis_y.mirror` "
            "is on. Overlay layers move the y encodings onto the layers "
            "themselves (the base's own y included), so the composed spec has "
            "no shared y encoding for the ghost axis to bind; yet a real y "
            "axis paints, so silently skipping the mirror would be a wrong "
            "result that looks right. Reported at render rather than by "
            "`dct validate` because mirror is cascade-resolved: a theme layer "
            "can turn it on for a chart that never authored it."
        ),
        summary=(
            "Fired when a chart with `layers:` also has `style.axis_y.mirror` "
            "on, from either the chart or its theme."
        ),
        docs_topic="charts",
    )
)

ERR_MULTIPLES_SUPPORT_TABLE = REGISTRY.register(
    ErrorCode(
        code="ERR-MULTIPLES-SUPPORT-TABLE",
        domain="render",
        title="multiples cannot be combined with a chart support_table",
        message_template=(
            "Chart {chart_id!r}: multiples is not supported together with a "
            "chart support_table; the attached table has no meaning per panel. "
            "Use one or the other on this chart."
        ),
        doc=(
            "Fired when a chart authors both `multiples:` and a "
            "`support_table:`. The attached table is a single per-chart "
            "element; once the chart is faceted into a small-multiples grid "
            "it has no per-panel meaning. Remove one or the other."
        ),
        docs_topic="charts",
    )
)

ERR_MULTIPLES_LAYER_PARTITION = REGISTRY.register(
    ErrorCode(
        code="ERR-MULTIPLES-LAYER-PARTITION",
        domain="render",
        title="multiples cannot be combined with an own-query layer that returns the partition field",
        message_template=(
            "Chart {chart_id!r}: multiples cannot be combined with an "
            "own-query overlay layer whose query returns the partition "
            "field(s) {fields}; Vega-Lite cannot split one inline layer "
            "dataset per panel. Remove the partition column(s) from the "
            "layer's query to repeat the layer in every panel, or drop "
            "multiples."
        ),
        doc=(
            "Fired when a chart authors both `multiples:` and a `layers:` "
            "entry with its own `query:` whose result includes the "
            "partition column(s). Vega-Lite's facet operator only "
            "partitions the root dataset, never a layer's own inline "
            "dataset, so per-panel layer data cannot be represented. A "
            "layer whose query does not return the partition column is "
            "unaffected; it repeats identically in every panel, which is "
            "correct for a global reference line."
        ),
        docs_topic="charts",
    )
)

ERR_MULTIPLES_ROW_OUTSIDE_PANEL_AXES = REGISTRY.register(
    ErrorCode(
        code="ERR-MULTIPLES-ROW-OUTSIDE-PANEL-AXES",
        domain="render",
        title="row's multiples value is not among the resolved panel_axes",
        message_template=(
            "Row's {field!r} value {value!r} is not among the resolved "
            "panel_axes values {allowed} for this chart's multiples "
            "partition."
        ),
        doc=(
            "Fired when a chart is rendered against rows whose partition "
            "column carries a value `resolve()` never saw: the row-truncated "
            "render path can only ever hold a subset of the axis values "
            "baked at resolve, never one the axes lack, so this firing means "
            "the chart is being rendered against data resolve never saw. "
            "Re-resolve the chart against its actual data before rendering."
        ),
        summary="Fired when a rendered row's multiples value is absent from the resolve-time panel_axes.",
        docs_topic="charts",
    )
)

ERR_MULTIPLES_ROW_MISSING_PARTITION_FIELD = REGISTRY.register(
    ErrorCode(
        code="ERR-MULTIPLES-ROW-MISSING-PARTITION-FIELD",
        domain="render",
        title="row is missing a multiples partition field entirely",
        message_template=(
            "Row is missing the multiples partition field {field!r} "
            "entirely (it carries: {available}); every row must carry "
            "every partition field, even one whose value is null."
        ),
        doc=(
            "Fired when a chart is rendered against a row that does not "
            "carry a partition column at all, as distinct from carrying it "
            "with a null value (a legitimate panel `resolve()` may have "
            "baked). A ragged row silently defaulting the missing field to "
            "`None` could misroute into that legitimate null panel by "
            "accident, so this is checked and raised separately."
        ),
        summary="Fired when a rendered row is missing a multiples partition column entirely, not merely null.",
        docs_topic="charts",
    )
)

ERR_MULTIPLES_RESOLVED_WITHOUT_DATA = REGISTRY.register(
    ErrorCode(
        code="ERR-MULTIPLES-RESOLVED-WITHOUT-DATA",
        domain="render",
        title="a faceted chart resolved without data is being rendered with real rows",
        message_template=(
            "Chart {chart_id!r}: multiples is set but this chart was "
            "resolved against no data (panel_axes is empty) and is now "
            "being rendered against real rows. Re-resolve the chart "
            "against its actual data before rendering."
        ),
        doc=(
            "Fired when a chart authors `multiples:`, was resolved with an "
            "empty query result (baking `panel_axes == ()`, the same N=1 "
            "state a non-faceted chart gets), and is then rendered against "
            "non-empty rows. Silently falling through would render the "
            "chart unfaceted instead of raising on data resolve never saw. "
            "Re-resolve the chart against its actual data before rendering."
        ),
        summary="Fired when a chart resolved without data (empty panel_axes) is rendered against real rows.",
        docs_topic="charts",
    )
)

ERR_MULTIPLES_INDEPENDENT_SCALE_MIRROR = REGISTRY.register(
    ErrorCode(
        code="ERR-MULTIPLES-INDEPENDENT-SCALE-MIRROR",
        domain="render",
        title="multiples with scale: independent cannot use a both-edge mirror axis",
        message_template=(
            "Chart {chart_id!r}: multiples with scale: independent cannot "
            "use a both-edge (mirror) y-axis: mirroring a shared scale to "
            "both edges is meaningless when each panel has its own scale. "
            "Use scale: shared, or set style.axis_y.mirror: false."
        ),
        doc=(
            "Fired when a chart authors `multiples: {scale: independent}` "
            "together with `style.axis_y.mirror` (bool true or an "
            "AxisMirrorStyle format/expr override). Mirroring reflects one "
            "shared scale to both edges; meaningless once every panel has "
            "its own independent scale. Use `scale: shared`, or turn mirror "
            "off."
        ),
        summary=(
            "Fired when a chart authors `multiples: {scale: independent}` "
            "together with a both-edge mirrored y-axis."
        ),
        docs_topic="charts",
    )
)

ERR_SPARK_BAR_VALUE_NOT_NUMERIC = REGISTRY.register(
    ErrorCode(
        code="ERR-SPARK-BAR-VALUE-NOT-NUMERIC",
        domain="render",
        title="spark_bar value field is not numeric",
        message_template=(
            "spark_bar chart {chart_id!r}: x field {field!r} is not usable "
            "as the magnitude: {reason}. spark_bar reverses the usual "
            "convention: x is the magnitude (the number) and y is the "
            "label (the text), the opposite of every other chart family. "
            "Set x to a numeric column and y to the text column."
        ),
        doc=(
            "Fired when a spark_bar chart's x field (the magnitude channel) "
            "is missing, holds no numeric values, or holds a non-numeric "
            "value in one of the rows being rendered. spark_bar reverses "
            "the x/y convention used by every other chart family: x is the "
            "magnitude, y is the label. This usually means x and y were "
            "authored in the cartesian order (swap them), or that x was "
            "left unset with no numeric column to auto-detect."
        ),
        docs_topic="charts",
    )
)


ERR_SPARK_BAR_VALUE_FIELD_NOT_FOUND = REGISTRY.register(
    ErrorCode(
        code="ERR-SPARK-BAR-VALUE-FIELD-NOT-FOUND",
        domain="render",
        title="spark_bar x names a column not in the query result",
        message_template=(
            "spark_bar chart {chart_id!r}: x field {field!r} names a column "
            "not present in its data. Available columns: {available}. "
            "On spark_bar, x is the bar magnitude and must name a numeric "
            "column from the query."
        ),
        doc=(
            "Fired when a spark_bar chart's `x` names a column that is not in "
            "the query result at all; usually a typo or a column renamed in "
            "the query. Distinct from ERR-SPARK-BAR-VALUE-NOT-NUMERIC, which "
            "fires when the column exists but holds no usable numbers: that "
            "one's fix is to swap `x` and `y`, which would be the wrong "
            "advice for a name that simply isn't there."
        ),
        summary=(
            "Fired when a spark_bar chart's `x` names a column that isn't in "
            "the query result."
        ),
        docs_topic="charts",
    )
)


# ──────────────────────────────── Warning codes ───────────────────────────────
# Every render-time warning code is declared here in the leaf; detectors
# import their constants back rather than declaring codes in-module.

WARN_BAR_BAND_WIDTH_TOO_NARROW = REGISTRY.register(
    WarningCode(
        code="WARN-BAR-BAND-WIDTH-TOO-NARROW",
        domain="render",
        title="Bar chart bands are too narrow to read",
        # This message_template is the CATEGORICAL (band-scale) branch's own
        # wording — "N bands x M series" is a band-scale concept with no
        # continuous-x equivalent. The continuous (quantitative-x) branch
        # shares this diagnostic CODE (one warning, not a second parallel
        # one) but formats its own message
        # (``_CONTINUOUS_MESSAGE_TEMPLATE`` in
        # ``render/warnings/bar_band_width_too_narrow.py``): its comparison
        # direction is inverted (bar_width exceeds the gap rather than
        # falling below a floor), and printing both numbers through this
        # template's shared rounded shape reads self-contradictory once the
        # threshold is a live measured step rather than a fixed floor
        # constant.
        message_template=(
            "Chart {chart_id!r} has {distinct} bands x {series} series across "
            "{render_width:.0f}px (~{bar_width:.2f}px per bar); "
            "{min_band_width:.0f}px is the threshold this configuration crosses, "
            "so bars will read as a merged block instead of separate marks."
        ),
        fix_template=(
            "Widen the chart (or, for a horizontal bar, make it taller), reduce "
            "the number of categories, or (for time series) roll up to a coarser "
            "grain (e.g. day -> week or month)."
        ),
        doc=(
            "Fires on bar (vertical + horizontal) charts that pack so many bands "
            "into the plot's bounding dimension (width for vertical, height for "
            "horizontal) that each band's fill drops below a readability floor: "
            "the fill disappears and the bar's own border stroke merges "
            'neighbors into a "ghost band" smear. Classic trigger: daily-'
            "granularity data (hundreds of distinct days) rendered as bars at a "
            "normal chart size, or a grouped/wide bar whose per-series sub-band "
            "is too thin even though the outer band is not. Also fires on a "
            "numeric x (no band scale, vertical bars only) when the bar's "
            "width, authored or computed from gap/min_size/max_size, exceeds "
            "the gap between the closest two x values, so adjacent bars "
            "visually overlap."
        ),
        docs_topic="charts",
    )
)

WARN_BAR_GROUPED_SERIES_COINCIDE = REGISTRY.register(
    WarningCode(
        code="WARN-BAR-GROUPED-SERIES-COINCIDE",
        domain="render",
        title="Grouped bar series paint on top of each other",
        message_template=(
            "Chart {chart_id!r}: {field!r} is quantitative (or a wide temporal "
            "range), so its color-grouped bars have no band scale to offset "
            "series within: every series paints at the same position, hiding "
            "all but the last one drawn."
        ),
        fix_template=(
            "Switch the x field to a categorical or bucketed-time column "
            "(so bars group side by side), or use a line/area/scatter mark, "
            "which already draw overlapping series legibly on a continuous x."
        ),
        doc=(
            "Fires when a color-grouped bar (color + stack: none) sits on a "
            "quantitative x, or a temporal x wide enough to promote to Vega-"
            "Lite's continuous temporal type. Vega-Lite's xOffset/yOffset "
            "sub-scale needs a discrete band to divide bars within; a "
            "continuous position has none, so every series paints at the "
            "identical position and width, each one occluding the series "
            "drawn before it. The render is unchanged by this warning: bar "
            "renders the same overlapping marks a line/area/scatter chart "
            "already draws on the same data, just without a legible way to "
            "tell the series apart."
        ),
        docs_topic="charts",
    )
)

WARN_FACET_PANEL_WIDTH_BELOW_MINIMUM = REGISTRY.register(
    WarningCode(
        code="WARN-FACET-PANEL-WIDTH-BELOW-MINIMUM",
        domain="render",
        title="Small-multiples panel width shrank below the legibility floor",
        message_template=(
            "Chart {chart_id!r} facets into {panel_cols} column panel(s) at "
            "{panel_width:.0f}px each, below the {min_panel_px:.0f}px floor "
            "small multiples need to stay legible."
        ),
        fix_template=(
            "Widen the chart, reduce the column-facet's cardinality, or move "
            "part of the split from `multiples.columns` to `multiples.rows` "
            "(rows stack vertically instead of dividing the card's width)."
        ),
        doc=(
            "Fires when a small-multiples chart's column-facet cardinality "
            "leaves each panel narrower than the configured legibility floor. "
            "The floor is `chart_rendering.facet.min_panel_px`. The card's "
            "declared width is never negotiable, panels shrink below the "
            "floor rather than push painted content past the card's edge, "
            "so this is the author's only signal that the panel count has "
            "outgrown the card."
        ),
        docs_topic="charts",
    )
)

WARN_ENDPOINT_LABEL_GAP_OVERFLOW = REGISTRY.register(
    WarningCode(
        code="WARN-ENDPOINT-LABEL-GAP-OVERFLOW",
        domain="render",
        title="Endpoint-label rail is too cramped for its intended spacing",
        message_template=(
            "Chart {chart_id!r} packs {series_count} endpoint labels needing "
            "{gap_px:.0f}px apart into a plot shorter than that: labels are "
            "distributed evenly across the plot instead of at their intended "
            "spacing."
        ),
        fix_template=(
            "Give the chart more height, reduce the number of series, or "
            "switch to a color legend instead of an endpoint-label rail."
        ),
        summary=(
            "Fired when an endpoint-label rail cannot fit its intended label "
            "spacing in the plot's height."
        ),
        doc=(
            "Fires when an endpoint-label rail's intended gap "
            "(`font_size * chart_rendering.endpoint_labels.line_height_multiplier`) "
            "cannot fit between the "
            "rail's series count and the plot's actual height. This is "
            "advisory, not a floor: the rail still renders, with labels "
            "distributed evenly across the available plot rather than piled "
            "onto the domain edges."
        ),
        docs_topic="charts",
    )
)

WARN_ENDPOINT_LABEL_RAIL_TIED = REGISTRY.register(
    WarningCode(
        code="WARN-ENDPOINT-LABEL-RAIL-TIED",
        domain="render",
        title="Endpoint-label rail has no spread to place labels along",
        message_template=(
            "Chart {chart_id!r}: all {series_count} series end on the same "
            "value, so the rail has no vertical spread to space labels by: "
            "they are distributed evenly across the plot instead of stacking "
            "on one point."
        ),
        fix_template=(
            "This is a property of the data, not the layout; more height will "
            "not change it. Check whether the trailing rows are null or zero "
            "for every series; if that is expected, a color legend names the "
            "series without implying distinct endpoints."
        ),
        summary=(
            "Fired when every series in an endpoint-label rail ends on the "
            "same value, leaving no spread to place labels along."
        ),
        doc=(
            "Fires when an endpoint-label rail's labels cannot be spaced "
            "because no pixels-per-data-unit could be measured from them: "
            "every series ends on the same value (a trailing all-null or "
            "all-zero column is the usual cause), or the y scale collapsed "
            "them onto a single pixel. Distinct from "
            "`WARN-ENDPOINT-LABEL-GAP-OVERFLOW`, which is a height problem: "
            "this one is a data property and adding height cannot clear it. "
            "Advisory, not a floor: the rail still renders."
        ),
        docs_topic="charts",
    )
)

WARN_ENDPOINT_LABEL_RAIL_OVERFLOW = REGISTRY.register(
    WarningCode(
        code="WARN-ENDPOINT-LABEL-RAIL-OVERFLOW",
        domain="render",
        title="Endpoint-label rail dropped series that had no room, despite fitting overall",
        message_template=(
            "Chart {chart_id!r}: {dropped_count} of {series_count} endpoint "
            "labels could not be placed {gap_px:.0f}px apart and were dropped "
            "from the rail rather than piled onto the same spot."
        ),
        fix_template=(
            "Unlike a rail that is cramped everywhere, more height reliably "
            "helps here: it gives the clustered anchors room to spread apart "
            "from whichever series they crowded against. Reducing the series "
            "count or switching to a color legend also works."
        ),
        summary=(
            "Fired when an endpoint-label rail cannot place every series at "
            "its intended spacing and drops the labels that do not fit."
        ),
        doc=(
            "Fires when an endpoint-label rail's real anchors are clustered "
            "such that some labels cannot be placed at the intended gap, even "
            "though `(n - 1) * gap` fits the plot height in the best case: "
            "that global check only proves the *block* of labels fits "
            "somewhere in the domain, not that the anchors' own positions "
            "leave room for all of them. Distinct from "
            "`WARN-ENDPOINT-LABEL-GAP-OVERFLOW`, which keeps every label and "
            "compresses the spacing instead: this fires only when some "
            "labels are dropped from the rail entirely."
        ),
        docs_topic="charts",
    )
)

WARN_LAYERED_CHART_SHARED_Y_AXIS_SCALE_MISMATCH = REGISTRY.register(
    WarningCode(
        code="WARN-LAYERED-CHART-SHARED-Y-AXIS-SCALE-MISMATCH",
        domain="render",
        title="Layered chart y series have a large scale mismatch",
        message_template=(
            "Chart {chart_id!r}: y columns {col_a!r} and {col_b!r} share a y-axis "
            "but their value ranges differ by {ratio:.0f}×: the smaller series "
            "will be visually crushed to a flat line."
        ),
        fix_template=(
            "Split into two y-axes by adding `axis_y:` on one of the layers, "
            "or normalize the series to a common scale in the query."
        ),
        doc=(
            "Fires on a layered chart where the base chart's own y series and/or "
            "its layers share the y-axis but their value ranges differ by ≥100×: "
            "the smaller series is visually crushed to a flat line. Classic example: "
            "revenue (millions) overlaid with conversion rate ([0, 1])."
        ),
        docs_topic="charts",
    )
)

WARN_LAYER_X_DOMAIN_PAINT_ORDER = REGISTRY.register(
    WarningCode(
        code="WARN-LAYER-X-DOMAIN-PAINT-ORDER",
        domain="render",
        title="Layered chart x-axis is ordered by paint order, not by the data",
        message_template=(
            "Chart {chart_id!r}: layer queries contribute {n_new} x "
            "categor{plural} ({sample}) that the base query never returns on "
            "{x_field!r}, and there is no order the base states to place them "
            "into, so the axis is drawn base-query rows first, then each "
            "layer's, and the left-to-right reading order is paint order "
            "rather than an ordering the data states."
        ),
        fix_template=(
            "Return every x category from one query: join the layers onto a "
            "shared spine so the axis order is that query's. An authored "
            "`sort:` is not a fix: it orders only the categories the base "
            "query returns and leaves the layer's appended in paint order."
        ),
        doc=(
            "Fires on a layered chart whose typed layers contribute x "
            "categories the base query does not, either from their own "
            "`query:`, or from their own `x:` column on the shared one, "
            "when the engine cannot place them into an order the base states. "
            "Two shapes reach that: the categories have no derivable order at "
            "all (not dates, not numbers), or they do but the base query's own "
            "rows are not in it, so there is no direction to extend. Date-like "
            "buckets and numeric categories over a base already in that order "
            "are placed into it instead, and never warn. Plain labels (month "
            "abbreviations, region names) cannot be: "
            "the union is base-query order followed by each layer's own, which "
            "is the order the layers happened to be painted in. On the "
            "migrated shape this reads as a chronology it is not: "
            "`Jan, Mar, May, Feb, Apr, Jun`. Sorting them lexically would be "
            "worse than paint order, so the engine leaves the order alone and "
            "says so rather than guessing."
        ),
        docs_topic="charts",
    )
)

WARN_LAYOUT_MIN_EXCEEDS_HEIGHT = REGISTRY.register(
    WarningCode(
        code="WARN-LAYOUT-MIN-EXCEEDS-HEIGHT",
        domain="render",
        title="Chart's category count needs more height than its row allows",
        message_template=(
            "Chart {chart_id!r} has {n_categories} category bands, which need "
            "at least {min_height:.0f}px to stay readable, but its row/tile "
            "height caps it at {authored_height:.0f}px. The chart rendered at "
            "{min_height:.0f}px anyway: the row's authored height was not honored, "
            "and any sibling charts sharing the row grew to match."
        ),
        fix_template=(
            "Raise the row's height to fit the category count, or reduce the "
            "categories (filter, paginate, or roll up to a coarser grain) so "
            "the readable minimum fits inside the authored height."
        ),
        doc=(
            "Fires when a horizontal bar chart's category count forces a "
            "minimum height (one readable band per category) that exceeds "
            "the row/tile height its author assigned. The engine expands the "
            "chart to the computed minimum regardless (squashing labels past "
            "legibility to honor an impossible authored height would be worse), "
            "so the row's authored height silently loses; any sibling chart in "
            "the same row inherits the expansion. This warns so the author "
            "learns why the row grew, instead of measuring it by hand."
        ),
        docs_topic="charts",
    )
)

WARN_LOCAL_TIME_LABEL_EXPR_ON_BUCKETED_AXIS = REGISTRY.register(
    WarningCode(
        code="WARN-LOCAL-TIME-LABEL-EXPR-ON-BUCKETED-AXIS",
        domain="render",
        title="Authored axis label expression uses local time on a bucketed axis",
        message_template=(
            "Chart {chart_id!r}: axis_x.labels.expr calls {accessor}(), which "
            "reads datum.value in the render process's time zone. Static "
            "rendering pins that to UTC, so the {time_unit!r} bucketing's "
            "UTC-midnight tick values resolve in UTC today, not necessarily "
            "the zone you or the chart's viewers are actually in, and dbt "
            "charts has no way to render this in a different zone."
        ),
        fix_template=(
            "Replace local-time accessors with their utc-prefixed equivalents "
            "(utcFormat()/utcyear()/utcmonth()/...), e.g. "
            "utcFormat(toDate(datum.value), '%b %Y'), to make the UTC result "
            "explicit instead of implying a local-time read that never happens."
        ),
        doc=(
            "Fires on bar (vertical + horizontal, single-metric + multi-metric), "
            "line, and area charts when axis_x.labels.expr contains a local-time "
            "accessor (timeFormat(), year(), month(), date(), quarter(), ...) while "
            "the chart's x-axis buckets to a UTC-midnight calendar grain "
            "(yearmonth, yearquarter, year, ...), whether authored via "
            "axis_x.time_unit or auto-detected from the query data. Vega-Lite's "
            "bucketed timeUnit transform produces UTC-midnight Date values, and "
            "dbt charts always renders statically in UTC, so a local-time "
            "accessor reads them as UTC today, the same result its "
            "utc-prefixed equivalent would give, and no longer dependent on "
            "which machine renders the chart. It still cannot be made to read "
            "in a different zone: there is no per-board or per-viewer "
            "timezone setting today, in any dbt charts surface. Heatmap and "
            "scatter are not yet covered. dbt charts never rewrites an "
            "authored label expression, so this only warns; switch to "
            "utcFormat() or utcmonth()/utcyear()/... to say what actually "
            "happens."
        ),
        summary=(
            "Fired on bar/line/area charts when an authored axis label expression "
            "calls a local-time accessor (timeFormat(), year(), month(), ...) on a "
            "bucketed temporal axis; static rendering resolves it as UTC "
            "regardless, so the accessor's name misleadingly implies a "
            "local-time read that never happens."
        ),
        docs_topic="charts",
    )
)

WARN_LIKELY_CURRENCY_OR_PERCENT_MISSING_FORMATTER = REGISTRY.register(
    WarningCode(
        code="WARN-LIKELY-CURRENCY-OR-PERCENT-MISSING-FORMATTER",
        domain="render",
        title="Y-axis field looks like money or a percentage but uses a generic format",
        message_template=(
            "Chart {chart_id!r}: field {field!r} looks like {kind} "
            "but the y-axis format is {format!r}."
        ),
        fix_template=(
            "Set `style.axis_y.labels.format` to a currency format (e.g. `$,.2f`) "
            "or a percent format (e.g. `.1%`) to match the field's meaning."
        ),
        doc=(
            "Fires when a chart's y-encoding field name looks like money or a "
            "percentage but the chart's baked y-axis format is unfit to render "
            "that kind. Detection is name-based: fields ending in _usd, _revenue, "
            "_amount, _pct, _rate, etc. (or bare names like `share`, `mrr`) "
            "trigger when the resolved y-axis format does not carry the "
            "matching symbol (`$` for money, `%` for a percentage)."
        ),
        docs_topic="charts",
    )
)

WARN_NORMALIZE_PERCENT_FORMAT_READS_RAW_VALUE = REGISTRY.register(
    WarningCode(
        code="WARN-NORMALIZE-PERCENT-FORMAT-READS-RAW-VALUE",
        domain="render",
        title="Percent format on a 100% stack formats the raw value, not the share",
        message_template=(
            "Chart {chart_id!r}: percent format {format!r} formats the raw "
            "{field!r} value, not the share the normalized stack paints: one "
            "stack group's values sum to {total}, not 1, so the hover rows and "
            "the value labels multiply a raw number by 100 and print it as a "
            "percentage."
        ),
        fix_template=(
            "Drop the percent format (a normalized stack already labels its "
            "axis 0-100%, so the format never reaches that axis), or make the "
            "measure a real 0..1 share in SQL by dividing each value by its "
            "group's total, so the raw value and the painted share are the "
            "same number."
        ),
        doc=(
            "Fires when a bar or area chart resolves to `style.stack: "
            "normalize`, its measure carries a percent format, and the raw y "
            "values in some stack group do not already sum to about 1. The "
            "normalized stack pins the measure axis to 0-100% itself, so an "
            "authored format never reaches that axis; it reaches the hover "
            "rows, the printed value labels, and the stack-total label, and "
            "every one of those reads the RAW column value. A count of 20 "
            "under a percent format therefore prints as `2000%`. Both "
            "authoring doors reach the same baked format and both fire: "
            "`style.number_format` (or a chart's `format:`) and an authored "
            "`style.axis_y.labels.format`. A chart whose measure is already a "
            "0..1 share, every group summing to about 1, is the honest case "
            "and never fires: there the raw value and the painted share are "
            "the same number. A non-percent format (currency, plain digits) "
            "never fires either: it prints the true raw number, and only its "
            "unit differs from the axis. A stack group summing to 0 is not "
            "judged at all, since a share is undefined at a zero total. "
            "`stack: zero` and `stack: center` are not judged either: an "
            "absolute stack labels its axis with that same authored format, "
            "so the chart is self-consistent. Small multiples are judged one "
            "panel at a time, since a normalized stack normalizes within a "
            "panel. The render is unchanged by this warning."
        ),
        summary=(
            "Fired on a 100% stacked bar or area chart whose measure carries a "
            "percent format but is not already a 0..1 share; the normalized "
            "stack pins its own axis, so that format reaches only the hover "
            "rows, the value labels and the stack total, where it multiplies "
            "the raw value by 100."
        ),
        docs_topic="charts",
    )
)

WARN_PIE_DOMINANT_SEGMENT = REGISTRY.register(
    WarningCode(
        code="WARN-PIE-DOMINANT-SEGMENT",
        domain="render",
        title="Pie chart is dominated by a single segment",
        message_template=(
            "Pie chart {chart_id!r}: {dominant_field!r} holds "
            "{dominant_share:.0%} of the total; the chart conveys a single value."
        ),
        fix_template=(
            "Use a KPI chart for the dominant share and a bar or table for the "
            "breakdown, rather than a pie dominated by one slice."
        ),
        doc=(
            "Fires on pie/donut charts where one slice is so large that the chart "
            "conveys a single value; the other slices are visually negligible. "
            "A near-single-value pie should be a KPI (the dominant share) plus a "
            "breakdown elsewhere."
        ),
        docs_topic="charts",
    )
)

WARN_PIE_TOO_MANY_SEGMENTS = REGISTRY.register(
    WarningCode(
        code="WARN-PIE-TOO-MANY-SEGMENTS",
        domain="render",
        title="Pie has too many slices to read",
        message_template=(
            "Pie chart {chart_id!r} has {segment_count} segments; "
            "angles are hard to compare past {max_segments} slices."
        ),
        fix_template=(
            "Use a bar chart sorted by value, or group small segments into 'Other'."
        ),
        doc=(
            "Fires on pie/donut charts whose query returns more segments than a "
            "reader can compare by angle. Humans judge angle poorly past a handful "
            "of slices; a pie with many segments is unreadable and should be a "
            "sorted bar chart."
        ),
        docs_topic="charts",
    )
)

WARN_PIE_TOTAL_EXCEEDS_INNER_RADIUS = REGISTRY.register(
    WarningCode(
        code="WARN-PIE-TOTAL-EXCEEDS-INNER-RADIUS",
        domain="render",
        title="Donut center total is too wide for the hole",
        message_template=(
            "Donut chart {chart_id!r}: center total {formatted_value!r} "
            "is {text_width:.1f}px wide but the hole is only {hole_diameter:.1f}px "
            "across (slot {slot_width:.0f}×{slot_height:.0f}px)."
        ),
        fix_template=(
            "Use a compacting number format (e.g. `format: number`) to shorten the "
            "total, or enlarge whichever slot dimension is smaller so the hole "
            "diameter increases."
        ),
        doc=(
            "Fires on donut charts whose formatted center total is wider than the "
            "hole it sits in, measured with the engine's font measurer. The hole "
            "diameter is `min(slot_width, slot_height) * outer_fraction * "
            "inner_radius`, where slot_width is the laid-out width minus the slice "
            "labels' reach and slot_height is the laid-out card height (for "
            "attached-table wheels, the wheel width and the theme's continuous "
            "view height)."
        ),
        docs_topic="charts",
    )
)

WARN_PLOT_HEIGHT_BELOW_MINIMUM = REGISTRY.register(
    WarningCode(
        code="WARN-PLOT-HEIGHT-BELOW-MINIMUM",
        domain="render",
        title="Chart chrome squeezes the plot below its readability floor",
        message_template=(
            "Chart {chart_id!r} squeezes its plot to an estimated "
            "{plot_height:.0f}px on a {card_height:.0f}px card, below the "
            "{floor_px:.0f}px floor it needs to stay readable."
        ),
        fix_template=(
            "Give the chart more height, drop authored chrome that is not "
            "earning its space here (subtitle, axis titles, a "
            "high-cardinality legend), or accept the smaller size "
            "deliberately and suppress this warning per-chart with "
            "`warnings_ignore`."
        ),
        doc=(
            "Fires when a bar chart's estimated plot height falls below its "
            "calibrated readability floor. The floor is "
            "`chart_rendering.plot_height_floor.ratio` of the card's own "
            "height, checked at every width: a short, wide card carrying "
            "heavy chrome starves its plot the same way a narrow one does. "
            "ERR-CHART-PAINTED-NO-MARKS already hard-fails around 247px, "
            "where marks paint with zero extent; this covers the band above "
            "it where the chart still renders but its plot has shrunk to a "
            "squashed sliver. No chrome is removed automatically to fix this: "
            "the author decides whether to widen the card, trim what they "
            "authored, or keep the chart small on purpose."
        ),
        docs_topic="charts",
    )
)

WARN_PLOT_WIDTH_BELOW_MINIMUM = REGISTRY.register(
    WarningCode(
        code="WARN-PLOT-WIDTH-BELOW-MINIMUM",
        domain="render",
        title="A support_table column block claims most of the card's width",
        message_template=(
            "Chart {chart_id!r}: the support_table column block "
            "({block_width:.0f}px) already claims most of the "
            "{card_width:.0f}px card, leaving the plot an estimated "
            "{plot_width:.0f}px."
        ),
        fix_template=(
            "Drop a support_table column, widen the card, or move the "
            "block to `position: right` — or accept it deliberately and "
            "suppress this warning per-chart with `warnings_ignore`."
        ),
        summary=(
            "Fires when a horizontal bar's support_table column block claims "
            "most of the width it shares with the plot."
        ),
        doc=(
            "Fires when a horizontal bar's support_table column block "
            "already claims more than "
            "`chart_rendering.support_table.column_block_share_warn_ratio` "
            "of the width it shares with the plot, measured from the "
            "column block's own font-measured reservation before any "
            "further axis-label or legend chrome is subtracted. The "
            "missing twin of WARN-PLOT-HEIGHT-BELOW-MINIMUM: no chrome is "
            "removed automatically — the author decides whether to widen "
            "the card, drop a column, or move the block. A plot that "
            "would fall below its readability floor once the remaining "
            "axis chrome is accounted for raises ERR-INPUT-INVALID instead "
            "of warning: a zero-width plot is a missing chart, not a "
            "squeezed one."
        ),
        docs_topic="charts",
    )
)

WARN_POINT_MAP_NEGATIVE_SIZE_VALUES = REGISTRY.register(
    WarningCode(
        code="WARN-POINT-MAP-NEGATIVE-SIZE-VALUES",
        domain="render",
        title="Point map size measure has negative values",
        message_template=(
            "Point map {chart_id!r}: {dropped_count} of {total_count} points "
            "have a negative {size_field!r} value and were not drawn; mark "
            "area cannot be negative."
        ),
        fix_template=(
            "Size by a magnitude instead of a signed value (e.g. "
            "`size: abs({size_field})` in the query) and encode direction "
            "with a diverging `color:` instead."
        ),
        doc=(
            "Fires when a bubble_map's `size:` measure contains negative "
            "values. Mark area cannot be negative, so rows with a negative "
            "size value are dropped before Vega-Lite sees them rather than "
            "drawn at the smallest visible size; a negative value clamped "
            'to the scale\'s zero floor would read as "nearly zero", '
            "misrepresenting a large-magnitude negative measurement. Size "
            "by a magnitude (e.g. `abs(...)` in the query) and encode "
            "direction with a diverging `color:` instead. A zero-valued row "
            "is legitimate data with a legitimate area of nothing and is "
            "never dropped or counted here."
        ),
        summary=(
            "Fires when a point_map's `size:` measure contains negative "
            "values; those rows are dropped rather than drawn misleadingly small."
        ),
        docs_topic="charts",
    )
)

WARN_POINT_MAP_OUT_OF_PROJECTION = REGISTRY.register(
    WarningCode(
        code="WARN-POINT-MAP-OUT-OF-PROJECTION",
        domain="render",
        title="Point map has data outside the projection boundary",
        message_template=(
            "Point map {chart_id!r}: {dropped_count} of {total_count} points "
            "are outside the {projection!r} projection boundary and were dropped."
        ),
        fix_template=(
            "Filter the data to the projection's region, or switch to a "
            "projection that covers the full data extent."
        ),
        doc=(
            "Fires when a point_map chart uses a bounded projection (e.g. "
            "albersUsa) and some data points fall outside its mapped region. "
            "The emitter drops those rows from spec.data before Vega-Lite sees "
            "them; this warning reports how many points were dropped and why."
        ),
        summary=(
            "Fires when a point_map chart uses a bounded projection and some "
            "data points fall outside its mapped region."
        ),
        docs_topic="charts",
    )
)

WARN_QUERY_RETURNED_ZERO_ROWS = REGISTRY.register(
    WarningCode(
        code="WARN-QUERY-RETURNED-ZERO-ROWS",
        domain="render",
        title="Chart query returned zero rows",
        message_template="Chart {chart_id!r}: query returned zero rows.",
        fix_template=(
            "Check the WHERE clause or date filter: it may be excluding all data "
            "for the current filter values."
        ),
        doc=(
            "Fires on any chart whose query returned zero rows. An empty chart "
            "renders as a blank panel with axes; no signal to the viewer that "
            "the query returned nothing. Most common cause: a WHERE clause or "
            "date filter that excludes all data."
        ),
        docs_topic="charts",
    )
)

WARN_QUERY_RESULT_TRUNCATED = REGISTRY.register(
    WarningCode(
        code="WARN-QUERY-RESULT-TRUNCATED",
        domain="render",
        title="Chart query result truncated",
        message_template=(
            "{subject}: query result exceeded the {reason} limit; "
            "truncated to {kept_row_count} rows."
        ),
        fix_template=(
            "Add a LIMIT to the query, narrow its filters, or raise "
            "execution.max_rows/max_result_bytes in dbt_charts.yml if the full "
            "result is genuinely needed."
        ),
        doc=(
            "Fires when a query's result exceeded the execution.max_rows or "
            "max_result_bytes safety ceiling and was truncated before it ever "
            "reached the result cache. The chart still renders with the "
            "truncated data; this is a safety net against an unbounded query "
            "exhausting memory or bloating the cache, not a hard error."
        ),
        summary=(
            "Fired when a query result exceeded the max_rows/max_result_bytes "
            "safety ceiling and was truncated."
        ),
        docs_topic="charts",
    )
)

WARN_REDUNDANT_ENCODING = REGISTRY.register(
    WarningCode(
        code="WARN-REDUNDANT-ENCODING",
        domain="render",
        redundant=True,
        title="Same column bound to two visual channels",
        message_template=(
            "Chart {chart_id!r}: field {field!r} is bound to channels "
            "{channels}: binding the same field twice adds no information."
        ),
        fix_template=(
            "Remove one of the channel bindings, or use different fields for "
            "each channel to encode distinct dimensions."
        ),
        doc=(
            "Fires when one query column is bound to two or more visual channels "
            "of the same chart (e.g. `y` and `color` both set to the same field), "
            "or when a `multiples.rows`/`multiples.columns` facet field is also "
            "bound to `x` or `y`. Binding the same field twice adds no "
            "information; the second channel is redundant, and for a facet "
            "collision, the axis repeats what the panel's own header already "
            "says. The bar `x==color` case is excluded: it renders full-width "
            "category-colored bars, a useful pattern. Faceting by a field also "
            "bound to `color`/`size`/`shape` is excluded too: those legends are "
            "drawn once, board-wide, never duplicated per panel, so pairing one "
            "with the facet gives every panel a consistent identifying color at "
            "no extra cost."
        ),
        summary=(
            "Fires when one query column is bound to two or more visual "
            "channels of the same chart."
        ),
        docs_topic="charts",
    )
)

WARN_STATIC_PAGINATION_CAPPED = REGISTRY.register(
    WarningCode(
        code="WARN-STATIC-PAGINATION-CAPPED",
        domain="render",
        title="Static export stopped short of every table page",
        message_template=(
            "Table {chart_id!r}: static export pre-rendered {rendered_pages} "
            "of {total_pages} pages; rows past page {rendered_pages} are not "
            "in this file."
        ),
        fix_template=(
            "Reduce the row count, raise style.pagination.page_rows so fewer "
            "pages are needed, or view the table on an interactive host (dct "
            "serve, Cloud) instead of a static export."
        ),
        doc=(
            "Fires when a static export (dct render --format html/svg) has a "
            "table with more pages than the renderer will pre-draw. A static "
            "export ships no JS runtime that can ask a server for another "
            "page, so every page's rows are pre-rendered into the artifact "
            "as toggle groups; left uncapped, that makes file size scale with "
            "total row count instead of page size. Past the cap, the "
            "renderer stops pre-rendering; the artifact shows only the "
            "first N pages, and states so in the exported file itself."
        ),
        docs_topic="charts",
    )
)

WARN_TABLE_COLUMNS_OVERFLOW = REGISTRY.register(
    WarningCode(
        code="WARN-TABLE-COLUMNS-OVERFLOW",
        domain="render",
        title="Table is wider than its dashboard slot",
        message_template=(
            "Table {chart_id!r} overflows its slot: "
            "needed {needed_width:.0f}px but only {available_width:.0f}px available."
        ),
        fix_template=(
            "Widen the table's dashboard slot, reduce the number of columns, "
            "or add explicit column widths to control how the table distributes "
            "its available space."
        ),
        doc=(
            "Fires when a table needs more width than the slot it was given. A "
            "table sizes each column to its minimum readable width; when those "
            "widths sum past the available width, the renderer widens the whole "
            "table past its slot, so in a dashboard it spills over its neighbor "
            "or is clipped, printing columns on top of each other."
        ),
        docs_topic="charts",
    )
)

WARN_TABLE_PAGE_SQUEEZED = REGISTRY.register(
    WarningCode(
        code="WARN-TABLE-PAGE-SQUEEZED",
        domain="render",
        title="Layout slot forced a smaller table page than the table asked for",
        message_template=(
            "Table {chart_id!r}: the layout slot fits {drawn_rows} of the "
            "{page_rows} rows a page holds ({total_rows} rows total); the rest "
            "moved onto later pages."
        ),
        fix_template=(
            "Give the tile more height (layout height:, a taller row, or fewer "
            "siblings sharing the row), or set style.pagination.page_rows to the "
            "page size you actually want so the sizer reserves room for it."
        ),
        doc=(
            "Fires when a table's layout slot is shorter than the height the "
            "sizer reserved for it, so the paginator draws fewer rows per page "
            "than the table's own pagination settled on. The two estimates are "
            "computed independently: layout_sizing._get_table_height_from_data "
            "reserves the slot, table._largest_safe_page_rows decides what fits, "
            "and when they disagree the paginator wins in silence: the render "
            "exits 0 and the export photographs as a faithful table while "
            "showing a fraction of its rows. Paginating because the data is "
            "genuinely longer than the page is not this warning; only a page "
            "cut down by the slot is."
        ),
        docs_topic="charts",
    )
)

WARN_TEMPORAL_SINGLE_POINT = REGISTRY.register(
    WarningCode(
        code="WARN-TEMPORAL-SINGLE-POINT",
        domain="render",
        title="Temporal line or area chart has only one data point",
        message_template=(
            "Chart {chart_id!r} ({chart_type}): temporal x-axis has exactly "
            "one data point; a one-point line/area conveys no trend."
        ),
        fix_template=(
            "Widen the date filter to include more time periods, "
            "or switch to a KPI or stat tile if a single-point value is intentional."
        ),
        doc=(
            "Fires on line and area charts where the x-axis is temporal and the "
            "query result has exactly one row. A one-point line is rendered as a "
            "single dot; a one-point area is a vertical line. Both render but "
            "convey nothing about a trend; this almost always means the date "
            "filter is too narrow."
        ),
        docs_topic="charts",
    )
)

WARN_TOO_MANY_COLOR_CATEGORIES = REGISTRY.register(
    WarningCode(
        code="WARN-TOO-MANY-COLOR-CATEGORIES",
        domain="render",
        title="Color encoding has more categories than the palette can distinguish",
        message_template=(
            "Chart {chart_id!r}: color encoding on {field!r} yields {count} "
            "distinct series; the palette only has {max_categories} distinct "
            "colors before recycling."
        ),
        fix_template=(
            "Reduce the number of color categories by grouping small values into "
            "'Other', or filter the data to the most significant categories."
        ),
        doc=(
            "Fires when a categorical color encoding has more distinct values than "
            "the palette can distinguish; colors recycle and the legend becomes "
            "unreadable. Gated on the Vega-Lite color encoding type so a continuous "
            "(quantitative) color gradient never trips it. A wide chart "
            "(y: [a, b]) is counted by the series its fold renders -- the "
            "measures, crossed with the color: dimension's values when one is "
            "authored."
        ),
        docs_topic="charts",
    )
)

WARN_PALETTE_UNSUPPORTED = REGISTRY.register(
    WarningCode(
        code="WARN-PALETTE-UNSUPPORTED",
        domain="render",
        title="Palette name is a known anti-pattern",
        message_template=(
            "Chart {chart_id!r}: palette {requested!r} is a known anti-pattern; "
            "resolved to {resolved} instead."
        ),
        fix_template=(
            "Author a supported palette name; see the anti-patterns table in "
            "docs/guides/palette-resolver.md#anti-patterns."
        ),
        doc=(
            "Fires when a chart authors a palette name on the known anti-pattern "
            "list (e.g. 'RdYlGn', 'parula'); these are CVD-hostile or superseded "
            "by a dbt-charts-native palette. palette() resolves the substitute silently "
            "at compile time; this detector is the only place the nudge surfaces."
        ),
        summary=(
            "Fires when a chart authors a palette name on the known anti-pattern "
            "list; these are CVD-hostile or superseded by a dbt-charts-native palette."
        ),
        docs_topic="charts",
    )
)

WARN_TOO_MANY_X_CATEGORIES = REGISTRY.register(
    WarningCode(
        code="WARN-TOO-MANY-X-CATEGORIES",
        domain="render",
        title="x-axis has too many distinct values to read",
        message_template=(
            "Chart {chart_id!r}: x field {field!r} has {count} distinct "
            "values; labels collide and marks are too thin to read "
            "(limit: {max_categories})."
        ),
        fix_template=(
            "Filter to the top N categories by value, roll up to a coarser "
            "grouping, or switch to a scrollable table for wide categorical data."
        ),
        doc=(
            "Fires when a categorical (nominal/ordinal) x-axis has more distinct "
            "values than fit legibly; labels collide and the marks are too thin "
            "to read. For a bar chart, also fires on a temporal x-axis: bars still "
            "draw one band per distinct x value even where the density gate has "
            "moved bucketed temporal data off the ordinal scale. On that axis the "
            "warning is about band width only: a temporal axis thins its own tick "
            "labels, so nothing is claimed about label collision, and the fix is to "
            "widen the chart, roll up to a coarser time grain, or switch to a line "
            "chart. Never fires on a quantitative axis, "
            "or on a temporal axis for line/area/scatter charts, where a dense axis "
            "is a continuous draw, not a crowded band."
        ),
        docs_topic="charts",
    )
)

WARN_VALUE_LABELS_CROWD_WIDTH = REGISTRY.register(
    WarningCode(
        code="WARN-VALUE-LABELS-CROWD-WIDTH",
        domain="render",
        title="Value labels are wider than their per-mark slot",
        message_template=(
            "Chart {chart_id!r}: widest value label is {label_width:.0f}px "
            "but each mark only has {slot_width:.0f}px; labels will overflow "
            "and collide with neighbors."
        ),
        fix_template=(
            "Shorten the number format (e.g. use SI suffix `.2~s` instead of "
            "full precision), reduce the number of labeled marks, or widen the chart."
        ),
        doc=(
            "Fires when a chart's value labels are wider than the horizontal room "
            "each one gets. Value labels are drawn at the mark, fixed size, with "
            "no adaptive avoidance, so they are the label kind that genuinely "
            "overflows. The check uses the panel's real rendered width and font "
            "metrics; it fires exactly when the widest label is wider than its slot."
        ),
        docs_topic="charts",
    )
)

WARN_AXIS_TITLE_TRUNCATED = REGISTRY.register(
    WarningCode(
        code="WARN-AXIS-TITLE-TRUNCATED",
        domain="render",
        title="Axis title was too long and was truncated with an ellipsis",
        message_template=(
            "Chart {chart_id!r}: {authored_field!r} was truncated: "
            "the authored text {authored_text!r} did not fit within two "
            "lines at the available extent."
        ),
        fix_template="Shorten the axis title, or widen the chart so the title has more room.",
        doc=(
            "Fires when an axis title is pre-wrapped to at most two lines "
            "(to prevent Vega-Lite's autosize from collapsing the plot) and "
            "the authored text is still too long: the last line is cut with "
            "a Unicode ellipsis (…) and the remainder of the title is lost. "
            "The title text in the message is the full authored text before "
            "truncation, so you can see exactly what was cut."
        ),
        docs_topic="charts",
    )
)

WARN_SERIES_LABEL_TRUNCATED = REGISTRY.register(
    WarningCode(
        code="WARN-SERIES-LABEL-TRUNCATED",
        domain="render",
        title="Series label was too long for the endpoint-label rail and was truncated",
        message_template=(
            "Chart {chart_id!r}: {count} series {labels_noun} from {authored_field!r} "
            "{were} truncated in the endpoint-label rail, which is capped at a "
            "fraction of the chart's width: {labels}."
        ),
        fix_template=(
            "Shorten the {authored_field} values, widen the chart, or set "
            "style.endpoint_labels.visible: false to keep the series names in "
            "the legend."
        ),
        doc=(
            "Fires when a chart's series labels are drawn in the right-hand "
            "endpoint-label rail and do not fit. The rail may claim only a "
            "fraction of the chart's width, so longer names are cut with an "
            "ellipsis (…). The labels are the values of the column bound to "
            "`color:`; for a wide-form chart authored `y: [a, b, …]`, the "
            "measure names themselves, prefixed by the `color:` column's value "
            "(`<value> - <measure>`) when one is authored; the warning names "
            "whichever key holds the long part. The label text in the message is the "
            "full value before "
            "truncation, so you can see exactly what was cut. Only the drawn "
            "label is shortened: the underlying values, the color scale, and "
            "tooltips still carry the full text."
        ),
        docs_topic="charts",
    )
)

WARN_CHART_TITLE_TRUNCATED = REGISTRY.register(
    WarningCode(
        code="WARN-CHART-TITLE-TRUNCATED",
        domain="render",
        title="Chart title or subtitle was truncated with an ellipsis",
        message_template=(
            "Chart {chart_id!r}: {authored_field!r} was truncated: "
            "the authored text {authored_text!r} did not fit within the "
            "available width."
        ),
        fix_template="Shorten the title or subtitle, or widen the chart.",
        doc=(
            "Fires when a chart title or subtitle is wrapped and the last "
            "line is cut with a Unicode ellipsis (…) because the authored "
            "text exceeds the available width. The message shows the full "
            "authored text before truncation."
        ),
        docs_topic="charts",
    )
)

WARN_KPI_LABEL_TRUNCATED = REGISTRY.register(
    WarningCode(
        code="WARN-KPI-LABEL-TRUNCATED",
        domain="render",
        title="KPI card label was truncated",
        message_template=(
            "Chart {chart_id!r}: KPI label was truncated: "
            "the authored text {authored_text!r} did not fit within the "
            "card at the available width."
        ),
        fix_template=(
            "Shorten the KPI label, widen the card, or use a smaller font size."
        ),
        doc=(
            "Fires when the KPI card label text is clipped or wrapped with "
            "an ellipsis because it exceeds the card width. The message shows "
            "the full authored label before truncation."
        ),
        docs_topic="charts",
    )
)

WARN_KPI_INLINE_VARIANT_FALLBACK_TO_STACKED = REGISTRY.register(
    WarningCode(
        code="WARN-KPI-INLINE-VARIANT-FALLBACK-TO-STACKED",
        domain="render",
        title="KPI inline variant fell back to stacked",
        message_template=(
            "Chart {chart_id!r}: variant: {authored_text!r} did not fit the "
            "card at the available width: value, label, and support fell "
            "back to the stacked arrangement instead of painting past the "
            "card edge."
        ),
        fix_template=(
            "Widen the card, shorten the label or support text, or author "
            "variant: stacked directly."
        ),
        doc=(
            "Fires when an inline KPI's assembled value + label + support "
            "run does not fit the card at its available width. The renderer "
            "falls back to the stacked arrangement for that card rather than "
            "paint past the card edge, so the card's actual layout no longer "
            "matches the authored variant: inline."
        ),
        docs_topic="charts",
    )
)

WARN_KPI_ALIGN_OVERFLOW = REGISTRY.register(
    WarningCode(
        code="WARN-KPI-ALIGN-OVERFLOW",
        domain="render",
        title="KPI align was ignored because a text run overflowed the card",
        message_template=(
            "Chart {chart_id!r}: a text run is wider than the card, so "
            "align: center/right was not applied; every run on the card stays "
            "left-aligned and the overflowing one spills past the right edge."
        ),
        fix_template=(
            "Widen the card, shorten the value with a more compact format, or "
            "drop align: so the left-aligned overflow is expected."
        ),
        doc=(
            "Fires when a KPI authors align: center or align: right and any of "
            "its text runs (value, label, or support) is wider than the "
            "available content width. Alignment is a whole-card choice, so it "
            "is dropped for every run rather than applied to the ones that fit: "
            "clamping per run would leave the card with two different "
            "alignments. Shifting the "
            "run would give it a negative x, and the card's SVG viewport clips "
            "at x=0; destroying the value's LEADING characters, so a "
            "right-aligned 1,234,567,890 would read as a well-formed but wrong "
            "234,567,890. The renderer keeps the run at the left edge instead, "
            "where overflow spills right and reads as visibly truncated, and "
            "reports that the authored align did not take effect."
        ),
        docs_topic="charts",
    )
)

WARN_TABLE_CRAMPED = REGISTRY.register(
    WarningCode(
        code="WARN-TABLE-CRAMPED",
        domain="render",
        title="Table columns were cramped under their width demand",
        message_template=(
            "Table {chart_id!r} is cramped: {wrapped_headers} of {column_count} "
            "column headers wrapped onto a second line. Columns need "
            "{needed_width:.0f}px but have {available_width:.0f}px."
        ),
        fix_template=(
            "Set the board's style.frame.width to about {suggested_width:.0f}px, "
            "widen the table's slot, drop columns, or set explicit pixel column widths."
        ),
        doc=(
            "Fires when a table still fits its box widthwise but only because "
            "the renderer degraded it: the columns were divided into less than "
            "their content demanded and headers were forced onto a second line. "
            "Distinct from WARN-TABLE-COLUMNS-OVERFLOW, which is the physical "
            "case where columns cannot fit at all and the table paints past its "
            "slot; from WARN-TABLE-TEXT-TRUNCATED, which fires only once text "
            "is actually cut with an ellipsis; the last rung of the same "
            "ladder; and from WARN-TABLE-PAGE-SQUEEZED, the height axis, where "
            "the slot cuts the rows-per-page down and the fix is to grow the "
            "slot rather than the width."
        ),
        docs_topic="charts",
    )
)

WARN_TABLE_TEXT_TRUNCATED = REGISTRY.register(
    WarningCode(
        code="WARN-TABLE-TEXT-TRUNCATED",
        domain="render",
        title="Table column header or cell text was truncated",
        message_template=(
            "Chart {chart_id!r}: column {authored_field!r} has truncated text: "
            "{truncation_count} value(s) were cut with an ellipsis."
        ),
        fix_template=(
            "Widen the column, shorten the values, or increase the chart width."
        ),
        doc=(
            "Fires when a table column header or one or more cell values are "
            "clipped with an ellipsis because they exceed the column width. "
            "One warning fires per column that has any truncation."
        ),
        docs_topic="charts",
    )
)

WARN_CALLOUT_TEXT_TRUNCATED = REGISTRY.register(
    WarningCode(
        code="WARN-CALLOUT-TEXT-TRUNCATED",
        domain="render",
        title="Callout text was truncated",
        message_template=(
            "Chart {chart_id!r}: callout {authored_field!r} was truncated: "
            "the authored text {authored_text!r} exceeded the maximum lines."
        ),
        fix_template=("Shorten the callout text, or increase the chart height/width."),
        doc=(
            "Fires when a callout chart's title, message, or hint text is "
            "wrapped and the last line is cut with a Unicode ellipsis because "
            "the text exceeds the maximum line count."
        ),
        docs_topic="charts",
    )
)

WARN_SPARK_LABEL_TRUNCATED = REGISTRY.register(
    WarningCode(
        code="WARN-SPARK-LABEL-TRUNCATED",
        domain="render",
        title="Spark-bar row label was truncated",
        message_template=(
            "Chart {chart_id!r}: {truncation_count} spark-bar label(s) were "
            "truncated; the label column is too narrow to show the full text."
        ),
        fix_template=(
            "Widen the chart or increase the label column width in the chart style."
        ),
        doc=(
            "Fires when one or more spark-bar row labels are truncated because "
            "the label column is too narrow to fit the full text. One warning "
            "fires per chart with any truncated labels."
        ),
        docs_topic="charts",
    )
)

WARN_Y_ENCODING_MOSTLY_NULL = REGISTRY.register(
    WarningCode(
        code="WARN-Y-ENCODING-MOSTLY-NULL",
        domain="render",
        title="Y-encoding field is mostly NULL in the query result",
        message_template=(
            "Chart {chart_id!r}: y field {field!r} is {null_pct:.0%} NULL "
            "across {row_count} rows."
        ),
        fix_template=(
            "Check for a broken join or a nullable source column. "
            "A COALESCE or WHERE clause may be needed to filter the empty rows."
        ),
        doc=(
            "Fires on any chart where the y-encoding field is more than 50% NULL "
            "in the query result rows. Mostly-empty visual marks with no explanation "
            "usually indicate a broken join or a nullable source column. "
            "NULL-only differs from NULL+zero: zero is a valid measurement."
        ),
        docs_topic="charts",
    )
)

WARN_LEGEND_POSITION_WIDTH_FALLBACK = REGISTRY.register(
    WarningCode(
        code="WARN-LEGEND-POSITION-WIDTH-FALLBACK",
        domain="render",
        title="Tiny-width tier overrode an authored legend position",
        # Authored explicitly: the doc's first sentence ends inside the inline
        # code span `legend.position`, which the agent_api summary deriver
        # refuses to split on rather than guess.
        summary=(
            "Fired when a card too narrow for a side legend overrides the "
            "position its author wrote."
        ),
        message_template=(
            "Chart {chart_id!r} authored `legend.position: {authored_position}`, "
            "but the tiny width tier forced the legend back to `top`: a card "
            "this narrow cannot hold a side legend."
        ),
        fix_template=(
            "Widen the chart past the tiny width tier, or drop "
            "`legend.position` and accept the automatic top legend."
        ),
        doc=(
            "Fires when a cartesian chart (bar, line, area, scatter, heatmap) "
            "authors a non-top `legend.position` and the card's width falls "
            "into the tiny tier, where the engine forces the legend back to "
            "a compact top strip regardless. The fallback itself is not a "
            "defect (a tiny card cannot physically fit a side legend); this "
            "warning exists only because the override was otherwise silent: "
            "the resolved chart renders a different legend position than the "
            "one the author wrote, with no signal that happened."
        ),
        docs_topic="charts",
    )
)

WARN_CATEGORY_COLOR_PIN_UNSEEN = REGISTRY.register(
    WarningCode(
        code="WARN-CATEGORY-COLOR-PIN-UNSEEN",
        domain="render",
        title="A category_colors pin names a value this render never draws",
        message_template=(
            "`style.charts.category_colors.{field}` pins {values}, which "
            "this render does not draw; the pin was skipped."
        ),
        fix_template=(
            "Check the value's spelling against the data. A pinned value can "
            "also be missing because a variable filter or a narrowed layout "
            "excluded it, not necessarily a typo."
        ),
        doc=(
            "Fires when a value pinned under `style.charts.category_colors."
            "<field>` names something none of the charts on this render "
            "actually draw for that field. The pin is skipped rather than "
            "seated — seating it would claim a palette slot for a category "
            "nothing draws and push every real value along one — but a "
            "misspelled pin would otherwise do nothing with no signal at "
            "all. The value can be genuinely absent for reasons other than "
            "a typo: a variable filter, a narrowed `dct render --chart` "
            "layout, or a sibling chart whose query failed."
        ),
        summary=(
            "Fired when a category_colors pin names a value none of the "
            "charts on this render actually draw."
        ),
        docs_topic="charts",
    )
)

WARN_LEGEND_VALUES_UNRESOLVED = REGISTRY.register(
    WarningCode(
        code="WARN-LEGEND-VALUES-UNRESOLVED",
        domain="render",
        title="style.legend.values entry does not resolve against the legend domain",
        summary=(
            "Fired when an authored legend entry doesn't resolve against a "
            "chart's legend domain."
        ),
        message_template=(
            "Chart {chart_id!r} authored `legend.values` entries {values} "
            "that did not resolve to exactly one legend entry. Legend "
            "domain: {domain}."
        ),
        fix_template=(
            "Check the spelling against the legend domain, or remove the "
            "entry if it's expected to be legitimately absent sometimes."
        ),
        doc=(
            "Fires when `style.legend.values` names an entry that matches "
            "none of the chart's legend entries, or matches more than one. "
            "The entry is dropped rather than shown or causing a render "
            "failure. If every authored entry misses, the legend falls "
            "back to its default order. Covers every nominal/ordinal "
            "`color:` field on bar, area, line, scatter, heatmap, and pie, "
            "a wide `y: [...]` chart, an overlay's `layers:` labels, and a "
            "geoshape choropleth. Not checked: `point_map`/`bubble_map` "
            "(no legend resolution there), a quantitative, temporal, or "
            "boolean `color:` column (`legend.values` is used exactly as "
            "authored, with no diagnostic on a miss), and an overlay whose "
            "base `y` is non-quantitative (its shared color scale is never "
            "built, so `legend.values` passes through verbatim)."
        ),
        docs_topic="charts",
    )
)

WARN_WIDE_MEASURE_LABEL_COLLISION = REGISTRY.register(
    WarningCode(
        code="WARN-WIDE-MEASURE-LABEL-COLLISION",
        domain="render",
        title="Two wide y: measures humanize to the same legend label",
        summary=(
            "Fired when two or more wide `y:` measures produce the same "
            "legend and axis label; each renders under its own column "
            "name instead."
        ),
        message_template=(
            "Chart {chart_id!r} authors y: measures {measures}, which "
            "all show the label {label!r}. Each is now shown under its "
            "own column name instead."
        ),
        fix_template=(
            "Author distinct `y:` column names, or rename one of them in "
            "the query so they no longer produce the same label."
        ),
        doc=(
            "Fires when two or more measures in a wide chart's y: list "
            "produce the same legend and axis label. For example, "
            "`churn_pct` and `churn_percent` both read `churn (%)`. Each "
            "colliding measure is shown under its own column name "
            "instead, keeping them as separate series. Other measures in "
            "the list are unaffected."
        ),
        docs_topic="charts",
    )
)

WARN_AXIS_LABEL_COLLISION = REGISTRY.register(
    WarningCode(
        code="WARN-AXIS-LABEL-COLLISION",
        domain="render",
        title="X-axis tick labels overlap with no room left to fix it",
        message_template=(
            "Chart {chart_id!r}: x field {field!r} has {label_count} tick "
            "labels that overlap even after skipping and tilting labels — "
            "they will render on top of each other."
        ),
        fix_template=(
            "Widen the chart, reduce the number of x categories (roll up to "
            "a coarser time grain, filter the data), or shorten the labels."
        ),
        doc=(
            "Fires on a line/area/scatter chart whose x-axis tick labels "
            "still overlap after the render tried every enabled overlap "
            "strategy (`style.axis_x.labels.overlap.skip`/`.tilt`, both on "
            "by default) — the same collision a table's cramped-columns "
            "warning reports for width, applied to axis tick text. Unlike "
            "`WARN-TOO-MANY-X-CATEGORIES` (a fixed category-count ceiling on "
            "a categorical band axis), this fires on any x-axis shape, "
            "including a continuous temporal axis whose auto-picked ticks "
            "simply don't fit the chart's width."
        ),
        summary=(
            "Fired when an x-axis's tick labels overlap with no remaining "
            "overlap-avoidance strategy to try."
        ),
        docs_topic="charts",
    )
)

WARN_AREA_UNSTACKED_READS_AS_STACKED = REGISTRY.register(
    WarningCode(
        code="WARN-AREA-UNSTACKED-READS-AS-STACKED",
        domain="render",
        title="Unstacked area is hard to tell apart from a stacked one",
        message_template=(
            "Chart {chart_id!r}: hard to tell this area chart apart from a "
            "stacked one. Its {count} series from {field!r} overlap, so the "
            "outer edge represents an individual series value, not the total."
        ),
        fix_template=(
            'Set `style.stack: "zero"` if the series compose a total, or use '
            "`type: line` if they are independent trends. Which one this "
            "occurrence recommends leading with depends on how much of the "
            "real total the chart's outer edge is hiding."
        ),
        doc=(
            "Fires when an area chart resolves to an unstacked mode (`stack: "
            "none`), paints two or more series (from a `color:` column or a "
            "wide `y: [a, b, c]` measure list), and no pair of those series "
            "visibly trades places anywhere on the axis. A pair is compared "
            "at the x values they share, on values coercible to a number, "
            "and only where both sit on one side of the zero baseline: a "
            "series painting above the baseline and one painting below it "
            "never warn about each other, since their bands occupy disjoint "
            "regions rather than nesting. A log y axis is not judged, since "
            "position there is logarithmic and no single ratio converts a "
            "gap at every magnitude. dbt charts already signals stacking "
            "through fill weight: an unstacked area's fill is translucent, a "
            "stacked one's is solid. A series that visibly trades places with "
            "another declares itself an overlap; the reader sees two bands "
            "swap and reads them as independent. Where that never visibly "
            "happens, each band looks nested inside the next at every x, "
            "indistinguishable from a real stack whatever the fill opacity "
            "says. A reader takes the outer edge for the total; it instead "
            "represents an individual series value, and the real total's "
            "magnitude may be several times larger. The render is unchanged "
            "by this warning; "
            "it reports a chart that is very likely either a stacked area "
            "missing its `stack:` key or a line chart drawn with the wrong "
            "mark. Small multiples split by the series column itself give "
            "each series its own panel, so no panel holds a pair to compare "
            "and nothing is reported. A chart with overlay layers is never "
            "judged: a layer can paint the very total the base series' outer "
            "edge only looks like, and judging the base alone would fire on "
            "a chart that already resolves the ambiguity. A series that "
            "repeats the same x value on two or more rows within one panel "
            "abstains the whole panel, since there is no principled way to "
            "pick which of the repeated values the chart actually paints."
        ),
        docs_topic="charts",
    )
)

ERR_TABLE_FORMAT_KIND_MISMATCH = REGISTRY.register(
    ErrorCode(
        code="ERR-TABLE-FORMAT-KIND-MISMATCH",
        domain="render",
        title="a table column's format spec does not match its cell values",
        message_template=(
            "table column format {fmt!r} does not match its cell values. {remedy}"
        ),
        doc=(
            "Fired when a table column's `format:` spec is the wrong kind "
            "for the values it formats: a strftime-style time spec (a "
            "predefined name like `time_short`, or an explicit string "
            "containing a `%`-prefixed directive such as `%B` or `%W`) "
            "applied to a numeric value, or a d3 numeric spec applied to a "
            "date/datetime value. `format:` is kind-agnostic at compile "
            "time, so `dct validate` accepts either mismatch; this is "
            "caught per cell at render time instead, once the actual value "
            "kind is known."
        ),
        docs_topic="charts",
    )
)
