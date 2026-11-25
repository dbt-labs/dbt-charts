"""Heatmap chart resolver."""

from __future__ import annotations

from dataclasses import replace
from typing import Literal

from dbt_charts.core.compile.format import reject_native
from dbt_charts.core.compile.models.chart.normalized import (
    HeatmapChart,
)
from dbt_charts.core.compile.models.chart.resolved import (
    ResolvedHeatmapChart,
)
from dbt_charts.core.compile.models.primitives import (
    ScaleTargetConfig,
    bake_scale_target_stops,
    resolved_as,
)
from dbt_charts.core.compile.models.style.context import ChartStyleContext
from dbt_charts.core.compile.models.style.resolved import ResolvedHeatmapStyle
from dbt_charts.core.compile.models.style.resolved._marks import ResolvedRectLabels
from dbt_charts.core.compile.models.style.resolved.heatmap import ResolvedRectMark
from dbt_charts.core.compile.models.style.theme.marks import labels_drawn
from dbt_charts.core.compile.resolve.chart._axes import (
    _bake_ay_position_left,
    cartesian_series_naming,
    color_legend_entries,
    estimate_cartesian_plot_height,
    legend_wrap_columns,
    no_rail,
)
from dbt_charts.core.compile.resolve.chart._channels import (
    _classify_to_channel_type,
    _column_numeric_values,
)
from dbt_charts.core.compile.resolve.chart._chart_rows import ChartDataset, ChartRows
from dbt_charts.core.compile.resolve.chart._conditional import (
    resolve_mark_conditional,
)
from dbt_charts.core.compile.resolve.chart._domain import _CartesianTickResolution
from dbt_charts.core.compile.resolve.chart._kwargs import (
    AutomaticLinkCandidate,
    _base_kwargs,
    _cartesian_kwargs,
    _color_column_format,
    authored_measure_format,
    board_family_style,
)
from dbt_charts.core.compile.resolve.chart._marks import (
    _label_format_fallback,
    datum_format,
)
from dbt_charts.core.compile.resolve.chart._palette import (
    _effective_palette,
    _effective_single_series_fill,
    _effective_warned_palette,
    _primary_mark_paint,
    _unchanneled_fill,
)
from dbt_charts.core.compile.resolve.chart._pattern import resolve_pattern
from dbt_charts.core.compile.resolve.chart._plan import (
    build_cartesian_axes,
    plan_cartesian,
)
from dbt_charts.core.compile.resolve.chart._wide_fields import (
    validate_wide_measure_labels,
    wide_measure_labels_for,
)
from dbt_charts.core.compile.resolve.style.chart_context import (
    build_chart_style_context,
)
from dbt_charts.core.compile.resolve.style.inherit_resolver import merge_unset
from dbt_charts.core.compile.resolve.style.legend_position import (
    cascaded_key,
)
from dbt_charts.core.compile.resolve.style.typography import resolve_title_font
from dbt_charts.core.compile.template.display_text import ChartDisplayText
from dbt_charts.core.utils import vega_infers_quantitative

__all__ = [
    "_resolve_heatmap",
]


def _is_temporal(column: str | list[str] | None, data: ChartRows) -> bool:
    """Whether a heatmap axis column holds dates. A wide ``y`` list names measures,
    never a date axis."""
    return (
        isinstance(column, str)
        and _classify_to_channel_type(column, data, is_dimension=True) == "temporal"
    )


def _resolve_heatmap(
    normalized: HeatmapChart,
    dataset: ChartDataset,
    chart_style_context: ChartStyleContext,
    width: float,
    automatic_link_candidate: AutomaticLinkCandidate,
    display_text: ChartDisplayText = None,
) -> ResolvedHeatmapChart:
    validate_wide_measure_labels(normalized)
    data = dataset.all_rows()
    if normalized.pattern is not None and _is_temporal(normalized.pattern, data):
        raise ValueError(
            f"chart.pattern ({normalized.pattern!r}) names a date column, which has "
            "no pattern scale: pattern a discrete or numeric column."
        )
    # A pattern with no `color:` is the cells' value: the same column, drawn in one color.
    authored_color = normalized.color
    if normalized.pattern is not None and authored_color is None:
        normalized = normalized.model_copy(update={"color": normalized.pattern})
    chart_local_style_context = build_chart_style_context(
        chart_style_context, normalized
    )
    temporal_axes: set[Literal["axis_x", "axis_y"]] = set()
    if _is_temporal(normalized.x, data):
        temporal_axes.add("axis_x")
    if _is_temporal(normalized.y, data):
        temporal_axes.add("axis_y")
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
        temporal_band_axes=frozenset(temporal_axes),
        x_type_defaulted=False,
        y_type_defaulted=False,
        horizontal=False,
    )
    primary = plan.primary
    heatmap = merge_unset(board_family_style(chart_style_context, normalized), primary)
    rect_labels = heatmap.marks.rect.labels
    if (
        labels_drawn(rect_labels.visible)
        and rect_labels.format is not None
        and _is_temporal(normalized.color, data)
    ):
        raise ValueError(
            f"style.marks.rect.labels.format does not apply to the date color "
            f"{normalized.color!r}: its labels print as the legend spells its dates."
        )
    heatmap_conditional = resolve_mark_conditional(heatmap.conditional, data)
    channels = plan.channels
    ay_merged = plan.axes.y.style
    # Heatmap's color channel is always a gradient (a continuous scale over
    # cell values), never a row of discrete series entries -- a gradient
    # legend is a different shape that does not wrap into a horizontal row,
    # so the top-legend fit rule does not apply here.
    naming = cartesian_series_naming(
        normalized,
        channels,
        cascaded_key(chart_local_style_context.key, direct=False),
        chart_local_style_context.legend,
        width,
        no_rail,
        endpoint_label_has_layers=False,
        has_layers=False,
        rail_eligible_for_suppression=False,
        suppress_wide_measure_series=False,
        multiples_wide_measure_series=False,
        top_legend_series=None,
        legend_entries=(),
        plot_height_estimate=estimate_cartesian_plot_height(normalized, heatmap, width),
        # top_legend_series=None above means the row-fit check this feeds
        # never runs for this family -- 0.0 is inert, not a real estimate.
        left_axis_reserve_px=0.0,
        # top_legend_series=None above means rung 2's floor check never runs
        # for this family either -- these three are inert, not real facts.
        card_padding_px=0.0,
        subtitle_height_px=0.0,
        axis_title_height_px=0.0,
    )
    ay_merged = _bake_ay_position_left(ay_merged)
    # Heatmap's column axis (axis_x) is always a bottom-orient nominal band —
    # no left/right edge to resolve inward/outward align against.
    # Neither axis carries tick_values on a heatmap (both channels are
    # nominal), so the non-compacting bake can't fire on either --
    # ay_format_raw is passed None explicitly below, which is inert here,
    # not a real provenance read.
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
        "heatmap",
        chart_style_context,
        plan.axes.x.as_literal(),
        replace(plan.axes.y.as_literal(), style=ay_merged),
        ax_edge=None,
        ticks=_CartesianTickResolution((), None, None),
        column_forming=True,
        measure_tooltip_format=_color_column_format(
            normalized, color_field, chart_style_context.formats, data
        ),
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
    reject_native(style_tail["tooltip_format"], "style.tooltip.format")
    _tf = resolve_title_font(chart_local_style_context, width)
    tinted_field = (
        normalized.color
        if heatmap.tints
        and normalized.pattern is None
        and normalized.color is not None
        and vega_infers_quantitative(data, normalized.color)
        else normalized.pattern
    )
    pattern = resolve_pattern(
        tinted_field,
        None if tinted_field != normalized.pattern else authored_color,
        data,
        chart_style_context,
        heatmap.pattern,
        _effective_single_series_fill(
            chart_style_context,
            primary,
            ("marks", "rect", "color"),
            rhythm_slot=normalized.rhythm_slot,
        ),
        primary,
    )
    strip_key_format, strip_key_is_house = (
        datum_format(
            None,
            chart_style_context.formats,
            normalized.query.formats if normalized.query else None,
            normalized.color,
            data,
        )
        if heatmap.strip_key and normalized.color
        else (None, False)
    )
    return ResolvedHeatmapChart(
        pattern=pattern,
        **_base_kwargs(
            normalized,
            chart_style_context,
            channels,
            chart_local_style_context,
            _effective_palette(chart_style_context, primary),
            warned_palette=_effective_warned_palette(chart_style_context, primary),
            automatic_link_candidate=automatic_link_candidate,
            layout_padding=heatmap.padding,
            wrap_columns=legend_wrap_columns(
                # A list `y` with no color field draws one legend entry per measure.
                tuple(
                    wide_measure_labels_for(
                        tuple(normalized.y), normalized.field_labels
                    ).values()
                )
                if isinstance(normalized.y, list) and not normalized.color
                else color_legend_entries(channels, data),
                width,
                chart_local_style_context.legend,
            ),
            suppress_legend=naming.suppress_legend,
            top_legend=naming.top_legend,
            legend_position_overridden_by_width=naming.legend_position_overridden_by_width,
        ),
        **_cartesian_kwargs(
            normalized,
            chart_local_style_context,
            display_text,
            data,
            "heatmap",
            panel_axes=dataset.axes,
            measure_format=authored_measure_format(normalized.style),
            stacked=False,
        ),
        chart_type="heatmap",
        style=ResolvedHeatmapStyle(
            tints=bool(heatmap.tints),
            label_divides=bool(heatmap.label_divides),
            strip_key=bool(heatmap.strip_key),
            strip_key_format=strip_key_format,
            strip_key_is_house=strip_key_is_house,
            # An empty palette is a legal heatmap with a color channel; only a
            # colorless heatmap reads the fill.
            single_series_fill=(
                _unchanneled_fill(
                    chart_style_context, primary, ("marks", "rect", "color")
                )
                if _effective_palette(chart_style_context, primary)
                or _primary_mark_paint(primary, ("marks", "rect", "color"))
                else None
            ),
            color_gradient=(
                bake_scale_target_stops(
                    ScaleTargetConfig.model_validate(
                        heatmap.color.gradient.model_dump(exclude_none=True)
                    )
                )
                if heatmap.color is not None and heatmap.color.gradient is not None
                else None
            ),
            rect_mark=resolved_as(
                ResolvedRectMark,
                heatmap.marks.rect,
                labels=_label_format_fallback(
                    heatmap.marks.rect.labels,
                    ResolvedRectLabels,
                    style_tail["tooltip_format"],
                    False,
                    chart_style_context.formats,
                    tooltip_format_values,
                )[0],
            ),
            conditional=heatmap_conditional,
            label_usable_ratio=chart_style_context.label_usable_ratio,
            title_font=_tf,
            **style_tail,
        ),
    )
