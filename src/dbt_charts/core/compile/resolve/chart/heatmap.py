"""Heatmap chart resolver."""

from __future__ import annotations

from dbt_charts.core.compile.merge import merge_onto_base
from dbt_charts.core.compile.models.chart.normalized import (
    HeatmapChart,
)
from dbt_charts.core.compile.models.chart.resolved import (
    ResolvedHeatmapChart,
)
from dbt_charts.core.compile.models.primitives import (
    ScaleTargetConfig,
    bake_scale_target_stops,
)
from dbt_charts.core.compile.models.style.context import ChartStyleContext
from dbt_charts.core.compile.models.style.resolved import ResolvedHeatmapStyle
from dbt_charts.core.compile.resolve.chart._axes import (
    _NO_RAIL_ENDPOINT_LABELS,
    _authored_legend,
    _bake_ay_position_left,
    cartesian_series_naming,
    estimate_cartesian_plot_height,
)
from dbt_charts.core.compile.resolve.chart._channels import _column_numeric_values
from dbt_charts.core.compile.resolve.chart._chart_rows import ChartDataset
from dbt_charts.core.compile.resolve.chart._domain import _CartesianTickResolution
from dbt_charts.core.compile.resolve.chart._kwargs import (
    AutomaticLinkCandidate,
    ChartTextVariables,
    _base_kwargs,
    _cartesian_kwargs,
    _title_font,
)
from dbt_charts.core.compile.resolve.chart._palette import (
    _effective_palette,
    _effective_requested_alias_palette,
)
from dbt_charts.core.compile.resolve.chart._plan import (
    build_cartesian_axes,
    plan_cartesian,
)
from dbt_charts.core.compile.resolve.style.chart_context import (
    build_chart_style_context,
)

__all__ = [
    "_resolve_heatmap",
]


def _resolve_heatmap(
    normalized: HeatmapChart,
    dataset: ChartDataset,
    chart_style_context: ChartStyleContext,
    width: float,
    automatic_link_candidate: AutomaticLinkCandidate,
    variables: ChartTextVariables,
) -> ResolvedHeatmapChart:
    data = dataset.all_rows()
    chart_local_style_context = build_chart_style_context(
        chart_style_context, normalized
    )
    plan = plan_cartesian(
        normalized,
        data,
        chart_style_context,
        "heatmap",
        "nominal",
        "nominal",
        normalized.multiples,
        normalized.y,
        has_quantitative_axis=False,
    )
    primary = plan.primary
    heatmap = merge_onto_base(chart_style_context.heatmap, primary)
    channels = plan.channels
    ax_merged, ay_merged = plan.ax_merged, plan.ay_merged
    # Heatmap's color channel is always a gradient (a continuous scale over
    # cell values), never a row of discrete series entries -- a gradient
    # legend is a different shape that does not wrap into a horizontal row,
    # so the top-legend fit rule does not apply here.
    naming = cartesian_series_naming(
        normalized,
        channels,
        _authored_legend(primary),
        merge_onto_base(chart_style_context.legend, heatmap.legend),
        width,
        _NO_RAIL_ENDPOINT_LABELS,
        endpoint_label_has_layers=False,
        has_layers=False,
        rail_eligible_for_suppression=False,
        suppress_wide_measure_series=False,
        multiples_wide_measure_series=False,
        top_legend_series=None,
        plot_height_estimate=estimate_cartesian_plot_height(normalized, heatmap, width),
        # top_legend_series=None above means the row-fit check this feeds
        # never runs for this family -- 0.0 is inert, not a real estimate.
        left_axis_reserve_px=0.0,
        # top_legend_series=None above means rung 2's floor check never runs
        # for this family either -- these three are inert, not real facts.
        card_padding_px=0.0,
        subtitle_present=False,
        axis_title_costs_height=False,
    )
    ay_merged = _bake_ay_position_left(ay_merged)
    # Heatmap's column axis (axis_x) is always a bottom-orient nominal band —
    # no left/right edge to resolve inward/outward align against.
    # Neither axis carries tick_values on a heatmap (both channels are
    # nominal), so the non-compacting bake can't fire on either --
    # format_authored=True is inert here, not a real provenance read.
    # A heatmap's tooltip formats its color channel, not y (nominal) --
    # emitters/heatmap.py sets encoding.color.format = tooltip_format for
    # every quantitative color encoding, and structured_tooltip.py's
    # heatmap builder does the same off resolved_channels["color"].
    color_channel = channels.get("color")
    color_field = (
        color_channel.data_field
        if color_channel is not None and color_channel.data_field
        else None
    )
    tooltip_format_values = _column_numeric_values(data, color_field)
    _ay, style_tail = build_cartesian_axes(
        normalized.id,
        chart_style_context,
        ax_merged,
        ay_merged,
        ax_band_position=plan.ax_band_position,
        ay_band_position=plan.ay_band_position,
        ax_edge=None,
        ay_format_authored=True,
        ay_format_is_alias=False,
        ticks=_CartesianTickResolution((), None, None),
        column_forming=True,
        measure_tooltip_format=None,
        tooltip_format_values=tooltip_format_values,
        # Both of a heatmap's channels are nominal -- see plan_cartesian's
        # own "nominal", "nominal" call above.
        ax_is_quantitative=False,
        ay_is_quantitative=False,
        ay_floors_tick_step=False,
        # Inert: ticks is always the empty _CartesianTickResolution above, so
        # _y_gridline_caps_bottom returns before this bool is ever read.
        zero_anchor=False,
        endpoint_rail_may_discard_domain=False,
    )
    _tf = _title_font(normalized, chart_local_style_context, width)
    return ResolvedHeatmapChart(
        **_base_kwargs(
            normalized,
            chart_style_context,
            channels,
            heatmap.legend,
            _effective_palette(chart_style_context, primary),
            requested_alias_palette=_effective_requested_alias_palette(
                chart_style_context, primary
            ),
            automatic_link_candidate=automatic_link_candidate,
            layout_padding=heatmap.padding,
            suppress_legend=naming.suppress_legend,
            top_legend=naming.top_legend,
            force_legend_visible=naming.force_legend_visible,
            legend_position_overridden_by_width=naming.legend_position_overridden_by_width,
        ),
        **_cartesian_kwargs(
            normalized,
            chart_local_style_context,
            variables,
            data,
            "heatmap",
            panel_axes=dataset.axes,
        ),
        chart_type="heatmap",
        style=ResolvedHeatmapStyle(
            color_gradient=(
                bake_scale_target_stops(
                    ScaleTargetConfig.model_validate(
                        heatmap.color.gradient.model_dump(exclude_none=True)
                    )
                )
                if heatmap.color is not None and heatmap.color.gradient is not None
                else None
            ),
            rect_mark=heatmap.marks.rect,
            label_usable_ratio=chart_style_context.label_usable_ratio,
            title_font=_tf,
            **style_tail,
        ),
    )
