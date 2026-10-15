"""The x-axis scale a cartesian chart's rows resolve to, read off the resolved
chart and its query rows rather than an emitted spec.

``emitted_x_axis`` mirrors each family's preprocessing (labeled-temporal
normalization, per-panel gap fill) and then asks ``resolve_cartesian_x_type``,
the same function the emitters call, so a detector sees the verdict the
chart actually draws with.
"""

from __future__ import annotations

from dataclasses import dataclass

from dbt_charts.core.compile.models.chart.resolved import (
    ResolvedAreaChart,
    ResolvedBarChart,
    ResolvedHeatmapChart,
    ResolvedLineChart,
    ResolvedScatterChart,
)
from dbt_charts.core.compile.models.chart.resolved._base import (
    _CartesianResolvedChartFields,
)
from dbt_charts.core.compile.resolve.chart._chart_rows import regroup, restripe
from dbt_charts.core.render.chart.emitters._channels import (
    gap_fill_ordinal_time_per_panel,
)
from dbt_charts.core.render.chart.step_band import BAND_STEP_CURVE
from dbt_charts.core.render.chart.time_unit_detect import normalize_labeled_temporal
from dbt_charts.core.render.chart.type_inference import (
    DetectedTimeUnit,
    resolve_cartesian_x_type,
)
from dbt_charts.core.utils import Rows


@dataclass(frozen=True)
class EmittedXAxis:
    vl_type: str
    time_unit: DetectedTimeUnit
    # The rows after the family's own preprocessing, which is what the axis
    # is resolved over.
    rows: Rows


def emitted_x_axis(
    chart: ResolvedBarChart | ResolvedLineChart | ResolvedAreaChart,
    chart_rows: Rows,
) -> EmittedXAxis:
    """Resolve a bar, line or area chart's x axis the way its emitter does.

    The caller guarantees ``chart.x`` is set and excludes histograms, which
    bin a quantitative x. A wide (multi-metric) line or area resolves x
    without its curve.

    Raises:
        ValueError: mixed labeled-temporal formats, or a column too sparse in
            parseable dates to classify.
        ChartDataError: rows that don't regroup or gap-fill.
    """
    assert chart.x is not None
    if isinstance(chart, ResolvedBarChart):
        mark_type = "bar"
        is_band_step = False
        resolves_cartesian_x = chart.orientation != "horizontal"
    elif isinstance(chart, ResolvedLineChart):
        mark_type = "line"
        is_band_step = (
            not chart.wide_measures and chart.style.line_mark.curve == BAND_STEP_CURVE
        )
        resolves_cartesian_x = True
    else:
        mark_type = "area"
        is_band_step = (
            not chart.wide_measures and chart.style.area_mark.curve == BAND_STEP_CURVE
        )
        resolves_cartesian_x = True

    normalized = normalize_labeled_temporal(chart_rows, chart.x)
    dataset = regroup(chart.panel_axes, chart_rows)
    mutated_dataset = (
        restripe(dataset, normalized) if normalized is not chart_rows else dataset
    )
    filled, _ = gap_fill_ordinal_time_per_panel(
        chart.style.axis_x,
        chart.x,
        chart.color,
        mutated_dataset,
        mark_type,
        is_band_step,
        resolves_cartesian_x,
        chart.x_date_range,
    )
    pipeline_rows = filled if filled is not None else normalized
    vl_type, _, time_unit = resolve_cartesian_x_type(
        pipeline_rows,
        chart.x,
        chart.style.axis_x,
        mark_type,
        is_band_step,
        tuple(axis.field for axis in chart.panel_axes),
    )
    return EmittedXAxis(vl_type, time_unit, pipeline_rows)


def x_scale_type(chart: _CartesianResolvedChartFields, chart_rows: Rows) -> str | None:
    """The scale type ("nominal", "ordinal", "temporal", "quantitative") the
    chart's x channel draws with, or None when x is not its category axis (a
    horizontal bar's x is the measure; a histogram bins it).

    Raises:
        ValueError, ChartDataError: as ``emitted_x_axis``.
    """
    if isinstance(chart, ResolvedHeatmapChart):
        return "nominal"
    if isinstance(chart, ResolvedScatterChart):
        assert chart.x is not None
        vl_type, _, _ = resolve_cartesian_x_type(
            chart_rows,
            chart.x,
            chart.style.axis_x,
            "scatter",
            False,
            tuple(axis.field for axis in chart.panel_axes),
        )
        return vl_type
    if isinstance(chart, ResolvedBarChart) and (
        chart.orientation == "horizontal" or chart.chart_type == "histogram"
    ):
        return None
    assert isinstance(chart, ResolvedBarChart | ResolvedLineChart | ResolvedAreaChart)
    return emitted_x_axis(chart, chart_rows).vl_type
