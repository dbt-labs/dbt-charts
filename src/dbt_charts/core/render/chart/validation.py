"""Chart data validation helpers.

Keeps data-shape policy separate from mechanical spec assembly.
"""

from __future__ import annotations

import datetime as dt
from collections import Counter
from decimal import Decimal
from typing import Any

from dbt_charts.core.compile.models.chart.resolved import (
    ResolvedAreaChart,
    ResolvedBarChart,
    ResolvedLineChart,
    effective_color_field,
)
from dbt_charts.core.compile.resolve.chart._chart_rows import ChartDataset, PanelRows
from dbt_charts.core.diagnostics.chart_data import ChartDataError
from dbt_charts.core.diagnostics.codes_render import (
    ERR_BAR_DUPLICATE_ROWS,
    ERR_COLOR_NULL_SERIES,
    ERR_HISTOGRAM_PREAGGREGATED,
)
from dbt_charts.core.render.chart.type_inference import infer_vega_type_from_data

# A run this short forming a gapless integer sequence by chance is not
# meaningfully unusual for genuine raw data; below this length the "dense
# integer run" signal in _forms_gapless_integer_run is too weak to act on.
_MIN_RUN_FOR_GROUP_BY_SHAPE = 5


def validate_color_series(
    chart: ResolvedBarChart | ResolvedLineChart | ResolvedAreaChart,
    data: list[dict[str, Any]],
) -> None:
    """Reject NULL values in a *categorical series* ``color`` column.

    A NULL series has no palette slot and no legend entry, but its rows still
    reach Vega-Lite's stack transform — so the segment reserves space, is never
    painted, and is never named. Dropping the rows instead would delete real
    values from a total, so neither half is safe: the grain has to be fixed in
    the query.

    Scoped to ``series`` mode over a categorical scale, because that is the only
    shape with the defect. ``gradient`` and ``conditional`` modes bind the color
    channel to a numeric metric where NULL is routine and harmless — no palette
    slot to miss, no legend entry to omit, and Vega-Lite lowers both to a
    continuous scale or a condition list that a null simply doesn't match. A
    quantitative ``series`` color is continuous for the same reason.
    """
    color_ch = chart.resolved_channels.get("color")
    if color_ch is None or color_ch.mode != "series" or not color_ch.data_field:
        return

    # A wide chart's series field is synthetic (VL's fold adds it); the column
    # that can carry a null series is the authored dimension it crosses with.
    # That dimension is categorical by construction whatever its dtype — every
    # value becomes a discrete composite series — so the type gate below,
    # which lets a quantitative series color through as continuous, does not
    # apply to it.
    color_field = chart.color if chart.wide_measures else color_ch.data_field
    if color_field is None:
        return
    if not chart.wide_measures and infer_vega_type_from_data(data, color_field) not in {
        "nominal",
        "ordinal",
    }:
        return

    null_rows = sum(1 for row in data if row.get(color_field) is None)
    if not null_rows:
        return

    raise ChartDataError.from_code(
        ERR_COLOR_NULL_SERIES,
        chart_type=chart.chart_type.title(),
        chart_id=chart.id,
        color_field=color_field,
        null_rows=null_rows,
    )


def _format_duplicate_key(fields: list[str], key: tuple[Any, ...]) -> str:
    parts = [
        f"{field}={value.isoformat() if isinstance(value, (dt.date, dt.datetime)) else value!r}"
        for field, value in zip(fields, key, strict=False)
    ]
    return ", ".join(parts)


def _plot_key_fields(
    chart: ResolvedBarChart | ResolvedLineChart | ResolvedAreaChart,
    data: list[dict[str, Any]],
) -> list[str]:
    chart_type = chart.chart_type
    # A wide chart's series field is the fold's synthetic label, absent from
    # pre-fold rows; its plot key is the authored dimension (chart.color).
    color = chart.color if chart.wide_measures else effective_color_field(chart)

    if chart_type == "bar":
        x_field = chart.x
        # wide_measures holds the original measure names before fold normalization;
        # chart.y is WIDE_VALUE_FIELD (a synthetic field) when wide_measures is set.
        y_fields: list[str] = (
            list(chart.wide_measures)
            if chart.wide_measures
            else ([chart.y] if isinstance(chart.y, str) else [])
        )
        if not x_field or not y_fields:
            return []

        x_type = infer_vega_type_from_data(data, x_field)
        y_types = {infer_vega_type_from_data(data, field) for field in y_fields}
        if x_type in {"nominal", "ordinal", "temporal"} and y_types == {"quantitative"}:
            fields = [x_field]
        elif (
            len(y_fields) == 1
            and next(iter(y_types)) in {"nominal", "ordinal"}
            and x_type == "quantitative"
        ):
            fields = [y_fields[0]]
        else:
            return []

        if color:
            fields.append(color)
        return fields

    if chart_type in {"line", "area"}:
        x_field = chart.x
        y_fields_la: list[str] = (
            list(chart.wide_measures)
            if chart.wide_measures
            else ([chart.y] if isinstance(chart.y, str) else [])
        )
        if not x_field or not y_fields_la:
            return []
        if any(
            infer_vega_type_from_data(data, field) != "quantitative"
            for field in y_fields_la
        ):
            return []

        fields = [x_field]
        if color:
            fields.append(color)
        return fields

    if chart_type in {"rect", "square", "heatmap"}:
        x_field = chart.x
        y_field = chart.y if isinstance(chart.y, str) else None
        if x_field and y_field:
            return [x_field, y_field]

    return []


def validate_preaggregated_data(
    chart: ResolvedBarChart | ResolvedLineChart | ResolvedAreaChart,
    data: PanelRows,
) -> None:
    """Reject raw detail rows when a chart expects one value per plotted key.

    ``data`` is one panel's rows (the N=1 whole dataset when the chart isn't
    faceted) — see ``validate_preaggregated_data_per_panel``. The partition
    column(s) never enter the plot key here: inside a panel they're constant
    by construction (stripped from the rows entirely), so the bare key is
    already correct and a value repeated across *panels* is never seen by a
    single call to raise a phantom duplicate.
    """
    if not data:
        return

    key_fields = _plot_key_fields(chart, data)
    if not key_fields:
        return

    counts = Counter(tuple(row.get(field) for field in key_fields) for row in data)
    duplicate_keys = [key for key, count in counts.items() if count > 1]
    if not duplicate_keys:
        return

    duplicate_preview = "; ".join(
        _format_duplicate_key(key_fields, key) for key in duplicate_keys[:3]
    )
    chart_id = chart.id or "unknown"
    field_list = ", ".join(key_fields)
    raise ChartDataError.from_code(
        ERR_BAR_DUPLICATE_ROWS,
        chart_type=chart.chart_type.title(),
        chart_id=chart_id,
        field_list=field_list,
        duplicate_preview=duplicate_preview,
    )


def validate_preaggregated_data_per_panel(
    chart: ResolvedBarChart | ResolvedLineChart | ResolvedAreaChart,
    dataset: ChartDataset,
) -> None:
    """``validate_preaggregated_data``, run once per panel of ``dataset``.

    ``dataset`` is trusted to already be panel-shaped for this chart — the
    resolve-baked split, or a caller's ``restripe()`` of it after a
    render-time value mutation (e.g. label normalization). This never
    re-derives panel membership itself, which is exactly what a caller
    needs when a mutated column is also a partition field: re-deriving via
    ``regroup()`` would try to match the mutated value against the
    pre-mutation baked axis and fail. A non-faceted chart is the N=1 case —
    one panel holding every row — so this is equivalent to calling
    ``validate_preaggregated_data`` directly.
    """
    for panel in dataset.panels:
        validate_preaggregated_data(chart, panel.rows)


def _forms_gapless_integer_run(values: list[Any]) -> bool:
    """True when every value is integer-valued, none repeats, and together
    they cover every integer between the min and max with no gaps.

    This is exactly the shape ``GROUP BY <integer column>`` produces: one row
    per distinct observed value. It is deliberately *not* "zero duplicate
    values" alone — a continuous measure (the primary histogram use case,
    e.g. price, latency) is essentially always duplicate-free too, so that
    signal can't tell raw from aggregated. A dense, gapless run of whole
    numbers is different: real raw samples have gaps and skew; hitting every
    integer in a range exactly once is the kind of thing an integer bucket
    key produces by construction; a continuous measure practically never
    produces by chance.
    """
    if len(values) < _MIN_RUN_FOR_GROUP_BY_SHAPE:
        return False

    whole_numbers: set[int] = set()
    for value in values:
        if isinstance(value, bool):
            return False
        if isinstance(value, int):
            whole = value
        elif (isinstance(value, float) and value.is_integer()) or (
            isinstance(value, Decimal) and value == value.to_integral_value()
        ):
            whole = int(value)
        else:
            return False
        if whole in whole_numbers:
            return False
        whole_numbers.add(whole)

    return max(whole_numbers) - min(whole_numbers) + 1 == len(whole_numbers)


def validate_raw_rows_for_histogram(
    chart: ResolvedBarChart,
    data: list[dict[str, Any]],
) -> None:
    """Reject pre-aggregated data on a histogram — the mirror image of
    ``validate_preaggregated_data``.

    Histograms hand Vega-Lite raw, ungrouped rows and let VL do its own
    binning + counting; a histogram's ``y`` is always ``aggregate: count``,
    never a data column. A pre-aggregated table — the output of
    ``SELECT x, count(*) FROM t GROUP BY x`` — carries that count (and
    possibly other aggregates) in leftover numeric columns the emitter never
    reads, and ``x`` forms a gapless run of whole numbers (what ``GROUP BY``
    over an integer bucket produces — see ``_forms_gapless_integer_run``).
    Both conditions together are the tell; either alone is common in genuine
    raw data (a raw fact table often carries a spare numeric column — an id,
    a second measure — and a continuous measure is duplicate-free as a
    matter of course). Feeding pre-aggregated data to VL would bin and
    recount the already-counted rows, silently discarding the real count.
    """
    if not data or chart.x is None:
        return
    x_field = chart.x

    color_field = effective_color_field(chart)
    extra_columns = [
        column for column in data[0] if column not in {x_field, color_field}
    ]
    numeric_extra_columns = [
        column
        for column in extra_columns
        if infer_vega_type_from_data(data, column) == "quantitative"
    ]
    if not numeric_extra_columns:
        return

    x_values = [row.get(x_field) for row in data]
    if not _forms_gapless_integer_run(x_values):
        return

    raise ChartDataError.from_code(
        ERR_HISTOGRAM_PREAGGREGATED,
        chart_id=chart.id or "unknown",
        field=x_field,
        count_fields=", ".join(repr(column) for column in numeric_extra_columns),
    )
