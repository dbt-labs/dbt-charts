"""Endpoint-label rail: which layout it uses, and how much horizontal span it claims.

The rail's own width is fully known before the x-axis label-crowding decision
runs (series names and font are baked at resolve time), but it used to be
computed only later, in ``features/endpoint_labels.py``'s feature pass —
after the crowding decision had already picked a tilt angle against the
card's full slot width. This module gives the emitters a way to ask "how much
of my width will the rail eat" *before* that decision, so
``resolve_axis_x_overlap`` can be fed the plot's real width instead of the
slot's.

``features/endpoint_labels.py`` imports ``measure_label_pane_width`` from
here rather than keeping its own copy, so there is exactly one place that
turns a list of series names into a pane width.
"""

from __future__ import annotations

from typing import Any, Literal

from dbt_charts.core.compile.config import get_chart_rendering
from dbt_charts.core.compile.models.chart.resolved import ResolvedChart
from dbt_charts.core.compile.models.chart.resolved._base import _BaseResolvedChartFields
from dbt_charts.core.compile.models.chart.resolved.area import ResolvedAreaChart
from dbt_charts.core.compile.models.chart.resolved.bar import ResolvedBarChart
from dbt_charts.core.compile.models.chart.resolved.line import ResolvedLineChart
from dbt_charts.core.compile.resolve.chart._wide_fields import (
    wide_measure_labels_for,
    wide_series_names,
)
from dbt_charts.core.font_measure import get_weighted_font_measurer
from dbt_charts.core.text.case import field_title
from dbt_charts.core.utils import (
    layered_endpoint_rail_fires,
    layered_endpoint_rail_shape,
)

# Horizontal gap between the last measured character and the pane edge.
_LABEL_GAP_PX = 4.0

EndpointRailLayout = Literal["right_pane", "top_rail"]


def endpoint_rail_layout(chart: ResolvedChart) -> EndpointRailLayout | None:
    """Which endpoint-label layout ``chart`` renders with, or ``None``.

    Single source of truth for whether the endpoint-label rail fires and
    which shape it takes — ``EndpointLabelFeature.applies_to`` (the render
    feature pass) and ``resolve_endpoint_rail_span`` (the pre-crowding
    estimate) must agree, or the axis crowding decision sizes against a rail
    that never renders, or vice versa.

    ``"right_pane"`` (hconcat, eats plot WIDTH) covers line/area and vertical
    bar with a series color, plus the layered single-series case.
    ``"top_rail"`` (vconcat, eats plot HEIGHT only) is horizontal stacked
    bar's own rail — irrelevant to x-axis label crowding.
    """
    if not isinstance(chart, (ResolvedLineChart, ResolvedAreaChart, ResolvedBarChart)):
        return None
    if not chart.style.endpoint_labels.visible:
        return None
    color_channel = chart.resolved_channels.get("color")
    has_series_color = color_channel is not None and color_channel.mode == "series"
    if isinstance(chart, ResolvedBarChart) and chart.orientation == "horizontal":
        if has_series_color and chart.stack not in (None, "none"):
            return "top_rail"
        return None
    if has_series_color:
        return "right_pane"
    if color_channel is not None:
        return None
    if layered_endpoint_rail_fires(
        layered_endpoint_rail_shape(chart.x, chart.y),
        [layer.color is None for layer in chart.layers],
    ):
        return "right_pane"
    return None


def measure_label_pane_width(
    series_names: list[str],
    font_family: str,
    font_size: float,
    font_weight: str | float,
    chart_width: float,
) -> tuple[float, list[str]]:
    """Return the label pane width and the names the cap will cut.

    Natural width is the widest series name plus the edge gap. A name wider
    than ``max_width_fraction`` of the chart's own width would make the rail
    wider than the canvas it hangs off — the concat overshoot correction then
    has no width left to give and refuses the render — so the pane is capped
    and Vega ellipsizes at the pane's mark limit, which is the cap itself.

    Measured at ``font_weight`` — the same weight ``_series_label_font_props``
    paints the label with (``series_label.font_weight``) — because a bold
    run advances wider than the unweighted default and undercounting it
    would let the rail collide with the axis it displaces.

    The cut names come back rather than being recorded here: only the caller
    knows whether the rail it is building actually honors this width (the
    vconcat top rail does not), so the caller owns the warning.
    """
    if not series_names:
        return _LABEL_GAP_PX, []
    measurer = get_weighted_font_measurer(font_family, font_weight)
    widths = {name: measurer.measure(name, font_size) for name in series_names}
    cap = chart_width * get_chart_rendering().endpoint_labels.max_width_fraction
    # Vega cuts at the mark limit, which is the cap — not the cap less the gap.
    return min(max(widths.values()) + _LABEL_GAP_PX, cap), [
        name for name, w in widths.items() if w > cap
    ]


def _estimated_series_names(
    chart: _BaseResolvedChartFields,
    data: list[dict[str, Any]],  # type-state: explicit_any — raw query result rows
) -> list[str]:
    """Upper-bound series names the rail will show, without ``datasets``.

    Mirrors ``EndpointLabelFeature.apply()``'s own name derivation, minus the
    position-dependent filtering that method also does (dropping a layer/
    series whose every value is null) — a genuinely all-null series is rare,
    and including its name here only ever widens the pre-crowding rail
    estimate, which only ever makes the crowding decision *more* cautious,
    never risks an overlap. Same "safe over-estimate, not a re-derivation of
    Vega's exact choice" shape as ``_measured_label_padding.py``'s unbaked
    tick-label estimate.
    """
    color_ch = chart.resolved_channels.get("color")
    has_series_color = color_ch is not None and color_ch.mode == "series"
    if (
        isinstance(chart, (ResolvedBarChart, ResolvedAreaChart, ResolvedLineChart))
        and chart.wide_measures
    ):
        return wide_series_names(
            chart.wide_measures,
            chart.color,
            data,
            wide_measure_labels_for(chart.wide_measures, chart.field_labels),
        )
    if has_series_color:
        assert color_ch is not None and color_ch.data_field
        series_field = color_ch.data_field
        return sorted(
            {
                str(row[series_field])
                for row in data
                if row.get(series_field) is not None
            }
        )
    # Layered single-series rail: applies_to()'s gate already confirmed every
    # layer is colorless, so every layer with a y field names one entry.
    assert isinstance(chart, (ResolvedLineChart, ResolvedAreaChart, ResolvedBarChart))
    y_field = chart.y
    names = [
        chart.y_label or field_title(y_field, chart.field_labels)
        if isinstance(y_field, str)
        else ""
    ]
    for layer in chart.layers:
        if layer.y is None:
            continue
        names.append(layer.label or field_title(layer.y, chart.field_labels))
    return [name for name in names if name]


def resolve_endpoint_rail_span(
    chart: ResolvedChart,
    data: list[dict[str, Any]],  # type-state: explicit_any — raw query result rows
    chart_width: float,
) -> float:
    """Pixel span the endpoint-label rail claims from ``chart_width``.

    0.0 when the rail doesn't fire, or fires as a ``"top_rail"`` (height
    only, never eats x-axis room). Otherwise the pane's own measured width
    plus the hconcat spacing between it and the plot — the same two figures
    ``features/endpoint_labels.py`` bakes into the final spec, computed here
    from chart config alone so the x-axis crowding decision can subtract it
    before it ever picks a cadence or tilt angle.
    """
    if endpoint_rail_layout(chart) != "right_pane":
        return 0.0
    assert isinstance(chart, (ResolvedLineChart, ResolvedAreaChart, ResolvedBarChart))
    sl = chart.style.series_label
    names = _estimated_series_names(chart, data)
    pane_width, _truncated = measure_label_pane_width(
        names, sl.font_family, sl.font_size, sl.font_weight, chart_width
    )
    return pane_width + chart.style.endpoint_labels.label_offset
