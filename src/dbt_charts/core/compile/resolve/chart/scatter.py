"""Scatter chart resolver."""

from __future__ import annotations

from dbt_charts.core.compile.errors import CompilationError
from dbt_charts.core.compile.merge import merge_onto_base
from dbt_charts.core.compile.models.chart.normalized import (
    ScatterChart,
)
from dbt_charts.core.compile.models.chart.resolved import (
    ResolvedScatterChart,
)
from dbt_charts.core.compile.models.style.context import ChartStyleContext
from dbt_charts.core.compile.models.style.resolved import (
    ResolvedScatterStyle,
)
from dbt_charts.core.compile.resolve.chart._axes import (
    _NO_RAIL_ENDPOINT_LABELS,
    _authored_legend,
    _bake_ay_position_right,
    cartesian_series_naming,
    estimate_cartesian_plot_height,
)
from dbt_charts.core.compile.resolve.chart._channels import (
    _classify_to_channel_type,
)
from dbt_charts.core.compile.resolve.chart._chart_rows import (
    ChartDataset,
    LayerDatasets,
)
from dbt_charts.core.compile.resolve.chart._domain import (
    _authored_axis_y_ticks_count,
    _bake_y_zero,
    _CartesianTickResolution,
    _first_non_numeric_y,
    _reject_non_positive_log_scale_data,
    _resolve_cartesian_ticks,
    _shared_y_values,
    _zero_anchor_floats,
    resolve_y_zero,
)
from dbt_charts.core.compile.resolve.chart._kwargs import (
    AutomaticLinkCandidate,
    ChartTextVariables,
    _base_kwargs,
    _cartesian_kwargs,
    _title_font,
)
from dbt_charts.core.compile.resolve.chart._layers import (
    _check_layers_y_domain,
    _resolve_layer_list,
)
from dbt_charts.core.compile.resolve.chart._marks import (
    _label_format_fallback,
    _measure_tooltip_format,
)
from dbt_charts.core.compile.resolve.chart._palette import (
    _effective_palette,
    _effective_requested_alias_palette,
    _effective_single_series_fill,
)
from dbt_charts.core.compile.resolve.chart._plan import (
    build_cartesian_axes,
    plan_cartesian,
    quantitative_channel_values,
)
from dbt_charts.core.compile.resolve.chart._wide_fields import (
    bake_wide_measures_kwargs,
    resolve_wide_measure_channels,
)
from dbt_charts.core.compile.resolve.style.chart_context import (
    build_chart_style_context,
)
from dbt_charts.core.diagnostics.codes_compile import (
    ERR_SCATTER_MULTI_Y_NOT_NUMERIC,
)
from dbt_charts.core.text.format_d3 import is_d3_si_spec

__all__ = [
    "_resolve_scatter",
]


def _resolve_scatter(
    normalized: ScatterChart,
    dataset: ChartDataset,
    chart_style_context: ChartStyleContext,
    width: float,
    datasets: LayerDatasets,
    automatic_link_candidate: AutomaticLinkCandidate,
    variables: ChartTextVariables,
) -> ResolvedScatterChart:
    data = dataset.all_rows()
    multiples_scale = (
        normalized.multiples.scale if normalized.multiples is not None else "shared"
    )
    # A wide y: [a, b] folds every measure onto one shared WIDE_VALUE_FIELD
    # axis, always quantitative -- unlike a single y:, which may legitimately
    # be categorical (the dot-plot recipe). A non-numeric measure in the list
    # would silently bake a NaN axis with zero marks; refuse instead, mirroring
    # bar's/line's own _first_non_numeric_y guard (they hardcode y to
    # quantitative unconditionally, so they check every y field, not just the
    # list case).
    if isinstance(normalized.y, list):
        _scatter_bad_y = _first_non_numeric_y(normalized.y, data)
        if _scatter_bad_y is not None:
            raise CompilationError.from_code(
                ERR_SCATTER_MULTI_Y_NOT_NUMERIC,
                chart_id=normalized.id,
                y_field=_scatter_bad_y,
            )
    # Scatter axes are typically both quantitative, but a categorical x or y
    # (dot plot) must bake as nominal — otherwise the numeric label font/format
    # is applied to category strings (Vega coerces them to NaN). A wide y:
    # [a, b] folds onto the synthetic WIDE_VALUE_FIELD, which is always
    # numeric — the dot-plot recipe (a real column classified nominal/ordinal)
    # only ever applies to a single authored y column.
    y_field_scatter = normalized.y if isinstance(normalized.y, str) else None
    x_ch_type = _classify_to_channel_type(normalized.x, data, is_dimension=True)
    y_ch_type = (
        "quantitative"
        if isinstance(normalized.y, list)
        else _classify_to_channel_type(y_field_scatter, data, is_dimension=False)
    )
    chart_local_style_context = build_chart_style_context(
        chart_style_context, normalized
    )
    plan = plan_cartesian(
        normalized,
        data,
        chart_style_context,
        "scatter",
        x_ch_type,
        y_ch_type,
        normalized.multiples,
        normalized.y,
        has_quantitative_axis=True,
    )
    primary = plan.primary
    scatter = merge_onto_base(chart_style_context.scatter, primary)
    channels = plan.channels
    channels, wide_measure_series = resolve_wide_measure_channels(
        normalized, channels, "scatter", has_layers=bool(normalized.layers)
    )
    # Scatter (and bubble -- a scatter with a size channel, not a separate
    # family) never routes to a top legend: its legend swatch is a circle
    # matching its own point marks in shape and comparable in size (measured
    # 10.0px swatch vs. 7.75px marks), so a top legend would read as a
    # duplicate scatter of marks rather than a key. The right-hand vertical
    # legend is the deliberate default, not the top-legend fit rule's
    # unmeasured case.
    naming = cartesian_series_naming(
        normalized,
        channels,
        _authored_legend(primary),
        merge_onto_base(chart_style_context.legend, scatter.legend),
        width,
        _NO_RAIL_ENDPOINT_LABELS,
        endpoint_label_has_layers=False,
        has_layers=bool(normalized.layers),
        rail_eligible_for_suppression=False,
        suppress_wide_measure_series=wide_measure_series,
        multiples_wide_measure_series=wide_measure_series,
        top_legend_series=None,
        plot_height_estimate=estimate_cartesian_plot_height(normalized, scatter, width),
        # top_legend_series=None above means the row-fit check this feeds
        # never runs for this family -- 0.0 is inert, not a real estimate.
        left_axis_reserve_px=0.0,
        # top_legend_series=None above means rung 2's floor check never runs
        # for this family either -- these three are inert, not real facts.
        card_padding_px=0.0,
        subtitle_present=False,
        axis_title_costs_height=False,
    )
    ax_merged, ay_merged = plan.ax_merged, plan.ay_merged
    ay_merged = _bake_ay_position_right(ay_merged)
    _ay_cont_scatter = (
        ay_merged.scale.continuous if ay_merged.scale is not None else None
    )
    authored_y_domain = (
        _ay_cont_scatter.domain if _ay_cont_scatter is not None else None
    )
    _ay_pre_zero_bake = ay_merged
    _reject_non_positive_log_scale_data(
        normalized.id,
        ay_merged,
        (
            [y_field_scatter]
            if y_field_scatter
            else (list(normalized.y) if isinstance(normalized.y, list) else [])
        ),
        data,
    )
    # _zero_anchor_floats spans every wide measure's own values, or the
    # single y field's -- the one shared extent the zero-anchor decision and
    # the tick ladder both read from (mirrors line.py's multi-metric bake).
    _zero_floats = _zero_anchor_floats(normalized.y, data)
    if y_field_scatter or wide_measure_series:
        if _zero_floats:
            if y_ch_type == "quantitative":
                ay_merged = _bake_y_zero(ay_merged, _zero_floats, "scatter")
            _sz = resolve_y_zero(
                _ay_pre_zero_bake, min(_zero_floats), max(_zero_floats), "scatter"
            )
            _scatter_anchored = _sz is True or (
                _sz is None and min(_zero_floats) >= 0.0
            )
        else:
            # No numeric y values -- a nominal or temporal y (the rotated
            # dot-plot recipe puts the category on y), or every row null.
            # Nothing was anchored, so this field must say so rather than
            # claim an anchor decision that never ran; it also feeds the
            # shared-scale tick resolution below, so it moves the baked
            # ladder AND the domain bounds, not just what the field reports:
            # an unanchored axis fits the data instead of pinning a floor
            # at 0.
            _scatter_anchored = False
        scatter_ticks = _resolve_cartesian_ticks(
            normalized.id,
            ay_merged,
            (
                _shared_y_values(data, y_field_scatter, normalized, datasets)
                if y_field_scatter
                else _zero_floats
            ),
            zero_anchor=_scatter_anchored,
            authored_ticks_count=_authored_axis_y_ticks_count(normalized.style),
            scale=multiples_scale,
        )
    else:
        scatter_ticks = _CartesianTickResolution((), None)
        _scatter_anchored = False
    # Scatter's x (even a categorical "dot plot" x) is always bottom-orient —
    # only y ever places on a left/right edge.
    # No tick_values on the categorical axis -- the non-compacting bake
    # can't fire regardless, but format_authored is required, not defaulted
    # (see build_resolved_axis's docstring).
    # Scatter's tooltip_format tracks an explicit chart-authored measure format
    # (style.number_format / chart.format) — falls back to the board default
    # tooltip.format when the author didn't override it, same as bar/line/area.
    # emitters/scatter.py paints tooltip_format on the y encoding
    # unconditionally, but x only ever gets it through the structured
    # tooltip's plain (non-faceted, non-layered) scatter branch
    # (_scatter_roles's own applies-to gate: `not chart.layers and
    # chart.multiples is None`) -- a faceted or layered scatter never paints
    # x with this format at all, so x must not vote there, or a sub-$1 x
    # column drags a correctly-SI'd y column to two decimals with nothing
    # painting the x side to justify it. A nominal y (dot plot) or nominal x
    # naturally contributes no values regardless -- the sub-$1 floor never
    # fires on a non-numeric column.
    tooltip_format_values = quantitative_channel_values(data, normalized.y)
    if (
        x_ch_type == "quantitative"
        and normalized.multiples is None
        and not normalized.layers
    ):
        tooltip_format_values = [
            *tooltip_format_values,
            *quantitative_channel_values(data, normalized.x),
        ]
    ay, style_tail = build_cartesian_axes(
        normalized.id,
        chart_style_context,
        ax_merged,
        ay_merged,
        ax_band_position=plan.ax_band_position,
        ay_band_position=plan.ay_band_position,
        ax_edge=None,
        ay_format_authored=plan.ay_format_authored,
        ay_format_is_alias=plan.ay_format_is_alias,
        ay_format_raw=plan.ay_format_raw,
        ticks=scatter_ticks,
        column_forming=True,
        measure_tooltip_format=_measure_tooltip_format(
            normalized,
            primary,
            chart_style_context,
            y_ch_type,
            values=tooltip_format_values,
        ),
        tooltip_format_values=tooltip_format_values,
        ax_is_quantitative=x_ch_type == "quantitative",
        ay_is_quantitative=y_ch_type == "quantitative",
        zero_anchor=_scatter_anchored,
        # Scatter has no endpoint-label rail (naming.py never routes it
        # through EndpointLabelFeature), so its baked domain_min is never at
        # risk of the shared-scale composition _y_domain_floor guards against.
        endpoint_rail_may_discard_domain=False,
    )
    axis_is_house = (
        ay.labels.format is not None
        and is_d3_si_spec(ay.labels.format)
        and (not plan.ay_format_authored or plan.ay_format_is_alias)
    )
    resolved_scatter_labels, scatter_label_is_house = _label_format_fallback(
        scatter.marks.point.labels,
        ay.labels.format,
        axis_is_house,
        chart_style_context.formats,
    )
    scatter_point_mark = scatter.marks.point.model_copy(
        update={"labels": resolved_scatter_labels}
    )
    resolved_layers = _resolve_layer_list(
        normalized.layers,
        chart_style_context,
        "scatter",
        scatter,
        normalized.query_name,
        0.0,
        0.0,
    )
    if authored_y_domain is not None:
        _check_layers_y_domain(normalized.id, resolved_layers, authored_y_domain)
    _tf = _title_font(normalized, chart_local_style_context, width)
    _ck = _cartesian_kwargs(
        normalized,
        chart_local_style_context,
        variables,
        data,
        "scatter",
        panel_axes=dataset.axes,
    )
    _ck, wide_measures = bake_wide_measures_kwargs(normalized.y, _ck)
    return ResolvedScatterChart(
        **_base_kwargs(
            normalized,
            chart_style_context,
            channels,
            scatter.legend,
            _effective_palette(chart_style_context, primary),
            requested_alias_palette=_effective_requested_alias_palette(
                chart_style_context, primary
            ),
            automatic_link_candidate=automatic_link_candidate,
            layout_padding=scatter.padding,
            suppress_legend=naming.suppress_legend,
            top_legend=naming.top_legend,
            force_legend_visible=naming.force_legend_visible,
            legend_position_overridden_by_width=naming.legend_position_overridden_by_width,
        ),
        **_ck,
        wide_measures=wide_measures,
        chart_type="scatter",
        size=normalized.size,
        shape=normalized.shape,
        style=ResolvedScatterStyle(
            point_mark=scatter_point_mark,
            single_series_fill=_effective_single_series_fill(
                chart_style_context,
                primary,
                rhythm_slot=normalized.rhythm_slot,
                has_layers=bool(normalized.layers),
            ),
            title_font=_tf,
            label_is_house=scatter_label_is_house,
            label_usable_ratio=chart_style_context.label_usable_ratio,
            **style_tail,
        ),
        layers=resolved_layers,
    )
