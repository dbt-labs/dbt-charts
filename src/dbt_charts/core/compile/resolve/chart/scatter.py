"""Scatter chart resolver."""

from __future__ import annotations

from dataclasses import replace
from datetime import datetime

from dbt_charts.core.compile.errors import CompilationError
from dbt_charts.core.compile.format import column_format, declared_column_format
from dbt_charts.core.compile.models.chart.normalized import (
    ScatterChart,
)
from dbt_charts.core.compile.models.chart.resolved import (
    ResolvedScatterChart,
)
from dbt_charts.core.compile.models.primitives import resolved_as
from dbt_charts.core.compile.models.style.context import ChartStyleContext
from dbt_charts.core.compile.models.style.resolved import (
    ResolvedPointLabels,
    ResolvedPointMarkStyle,
    ResolvedScatterStyle,
)
from dbt_charts.core.compile.resolve.chart._annotations import (
    NO_ANNOTATIONS,
    annotation_chart,
    point_radius,
    resolve_annotations,
    unfilled_ring,
)
from dbt_charts.core.compile.resolve.chart._axes import (
    _NO_RAIL_ENDPOINT_LABELS,
    _bake_ay_position_right,
    _cascaded_legend,
    cartesian_color_domain_values,
    cartesian_series_naming,
    estimate_cartesian_plot_height,
    estimate_left_axis_reserve_px,
)
from dbt_charts.core.compile.resolve.chart._channels import (
    _classify_to_channel_type,
)
from dbt_charts.core.compile.resolve.chart._chart_rows import (
    ChartDataset,
    LayerDatasets,
)
from dbt_charts.core.compile.resolve.chart._domain import (
    _bake_x_zero,
    _bake_y_zero,
    _CartesianTickResolution,
    _check_bar_layer_spans,
    _first_non_numeric_y,
    _reject_non_positive_log_layer_data,
    _reject_non_positive_log_scale_data,
    _reject_non_positive_log_x_data,
    _resolve_cartesian_ticks,
    _shared_y_values,
    _zero_anchor_floats,
    resolve_y_zero,
)
from dbt_charts.core.compile.resolve.chart._kwargs import (
    AutomaticLinkCandidate,
    _base_kwargs,
    _cartesian_kwargs,
)
from dbt_charts.core.compile.resolve.chart._layers import (
    _check_layers_y_domain,
    _resolve_layer_inks,
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
from dbt_charts.core.compile.resolve.chart._tooltip import (
    reject_tooltip_beside_date_y,
    resolve_tooltip,
)
from dbt_charts.core.compile.resolve.chart._wide_fields import (
    bake_wide_measures_kwargs,
    resolve_wide_measure_channels,
)
from dbt_charts.core.compile.resolve.style.chart_context import (
    build_chart_style_context,
)
from dbt_charts.core.compile.resolve.style.inherit_resolver import merge_unset
from dbt_charts.core.compile.resolve.style.legend_position import merge_legend
from dbt_charts.core.compile.resolve.style.typography import resolve_title_font
from dbt_charts.core.compile.template.display_text import ChartDisplayText
from dbt_charts.core.diagnostics.codes_compile import (
    ERR_ANNOTATION_INVALID,
    ERR_SCATTER_MULTI_Y_NOT_NUMERIC,
)

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
    rendered_at: datetime | None,
    display_text: ChartDisplayText = None,
) -> ResolvedScatterChart:
    data = dataset.all_rows()
    reject_tooltip_beside_date_y(normalized, data)
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
    _check_bar_layer_spans(normalized, data, datasets, None)
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
        temporal_band_axes=frozenset(),
    )
    primary = plan.primary
    scatter = merge_unset(chart_style_context.scatter, primary)
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
        *_cascaded_legend(chart_style_context, scatter.legend),
        merge_legend(chart_style_context.legend, scatter.legend),
        width,
        _NO_RAIL_ENDPOINT_LABELS,
        endpoint_label_has_layers=False,
        has_layers=bool(normalized.layers),
        rail_eligible_for_suppression=False,
        suppress_wide_measure_series=wide_measure_series,
        multiples_wide_measure_series=wide_measure_series,
        top_legend_series=None,
        legend_entries=cartesian_color_domain_values(dataset, normalized.color),
        plot_height_estimate=estimate_cartesian_plot_height(normalized, scatter, width),
        left_axis_reserve_px=estimate_left_axis_reserve_px(
            None, merge_legend(chart_style_context.legend, scatter.legend)
        ),
        # top_legend_series=None above means rung 2's floor check never runs
        # for this family -- these three are inert, not real facts.
        card_padding_px=0.0,
        subtitle_height_px=0.0,
        axis_title_height_px=0.0,
    )
    ay_merged = plan.axes.y.style
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
    _reject_non_positive_log_layer_data(normalized, ay_merged, data, datasets)
    _reject_non_positive_log_x_data(
        normalized.id, plan.axes.x.style, normalized.x, data
    )
    # Scatter's tooltip_format tracks an explicit chart-authored measure format
    # (axis_y labels.format or the y column's declared format) — falls back to
    # the board default tooltip.format when the author didn't override it, same
    # as bar/line/area.
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
    measure_tooltip_format = _measure_tooltip_format(
        normalized,
        primary,
        chart_style_context,
        y_ch_type,
        values=tooltip_format_values,
    )
    single_series_fill = _effective_single_series_fill(
        chart_style_context,
        primary,
        rhythm_slot=normalized.rhythm_slot,
        has_layers=bool(normalized.layers),
    )
    inks = _resolve_layer_inks(
        normalized,
        chart_style_context,
        scatter,
        primary,
        channels,
        data,
        datasets,
        single_series_fill,
        measure_quantitative=y_ch_type == "quantitative",
    )
    annotations = NO_ANNOTATIONS
    if rendered_at is not None and normalized.annotations:
        if y_ch_type == "temporal":
            raise CompilationError.from_code(
                ERR_ANNOTATION_INVALID,
                chart_id=normalized.id,
                index=0,
                reason="annotations on a scatter need a numeric or category y",
            )
        annotations = resolve_annotations(
            normalized.annotations,
            annotation_chart(
                normalized,
                x_ch_type,
                y_ch_type,
                False,
                single_series_fill,
                False,
                inks,
            ),
            dataset,
            datasets,
            chart_local_style_context,
            rendered_at,
        )
        y_continuous = ay_merged.scale.continuous if ay_merged.scale else None
        if (
            y_continuous is not None
            and y_continuous.type == "log"
            and (refused := annotations.index_of_y(lambda value: value <= 0))
            is not None
        ):
            raise CompilationError.from_code(
                ERR_ANNOTATION_INVALID,
                chart_id=normalized.id,
                index=refused,
                reason="annotation values on a log y axis must be positive",
            )
    # _zero_anchor_floats spans every wide measure's own values, or the
    # single y field's -- the one shared extent the zero-anchor decision and
    # the tick ladder both read from (mirrors line.py's multi-metric bake).
    _zero_floats = _zero_anchor_floats(normalized.y, data) + list(annotations.y_values)
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
                + list(annotations.y_values)
                if y_field_scatter
                else _zero_floats
            ),
            zero_anchor=_scatter_anchored,
            scale=multiples_scale,
            is_quantitative=y_ch_type == "quantitative",
        )
    else:
        scatter_ticks = _CartesianTickResolution((), None)
        _scatter_anchored = False
    # Scatter's x (even a categorical "dot plot" x) is always bottom-orient —
    # only y ever places on a left/right edge.
    # No tick_values on the categorical axis -- the non-compacting bake
    # can't fire regardless, but the format is required, not defaulted
    # (see build_resolved_axis's docstring).
    ay, style_tail = build_cartesian_axes(
        normalized.id,
        "scatter",
        chart_style_context,
        replace(
            plan.axes.x,
            style=_bake_x_zero(
                plan.axes.x.style,
                plan.axes.x.format,
                x_ch_type == "quantitative",
                quantitative_channel_values(data, normalized.x),
            ),
        ),
        replace(plan.axes.y, style=ay_merged),
        ax_edge=None,
        ticks=scatter_ticks,
        column_forming=True,
        measure_tooltip_format=measure_tooltip_format,
        tooltip_format_values=tooltip_format_values,
        ax_is_quantitative=x_ch_type == "quantitative",
        ay_is_quantitative=y_ch_type == "quantitative",
        ay_floors_tick_step=True,
        zero_anchor=_scatter_anchored,
        # Scatter has no endpoint-label rail (naming.py never routes it
        # through EndpointLabelFeature), so its baked domain_min is never at
        # risk of the shared-scale composition _y_domain_floor guards against.
        endpoint_rail_may_discard_domain=False,
    )
    axis_house_default = plan.axes.y.is_house
    resolved_scatter_labels, scatter_label_is_house = _label_format_fallback(
        scatter.marks.point.labels,
        ResolvedPointLabels,
        ay.labels.format,
        axis_house_default,
        chart_style_context.formats,
        tooltip_format_values,
    )
    scatter_point_mark = resolved_as(
        ResolvedPointMarkStyle, scatter.marks.point, labels=resolved_scatter_labels
    )
    resolved_layers = _resolve_layer_list(
        normalized.layers,
        chart_style_context,
        "scatter",
        scatter,
        normalized.query_name,
        0.0,
        0.0,
        data,
        datasets,
        normalized.id,
        normalized.query.formats if normalized.query else None,
        inks,
        normalized.layer_query_formats,
    )
    if authored_y_domain is not None:
        _check_layers_y_domain(normalized.id, resolved_layers, authored_y_domain)
    _tf = resolve_title_font(chart_local_style_context, width)
    _ck = _cartesian_kwargs(
        normalized,
        chart_local_style_context,
        display_text,
        data,
        "scatter",
        panel_axes=dataset.axes,
        stacked=False,
    )
    _ck, wide_measures = bake_wide_measures_kwargs(normalized.y, _ck)
    x_values = quantitative_channel_values(data, normalized.x)
    x_query_formats = normalized.query.formats if normalized.query else None
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
            wrap_legend=naming.wrap_legend,
            legend_position_overridden_by_width=naming.legend_position_overridden_by_width,
        ),
        **_ck,
        tooltip=resolve_tooltip(normalized, data, chart_style_context.formats),
        wide_measures=wide_measures,
        chart_type="scatter",
        x_tooltip_format=(
            column_format(
                "number",
                chart_style_context.formats,
                query_formats=x_query_formats,
                columns=normalized.x,
                values=x_values,
            )
            if x_values
            and declared_column_format(
                x_query_formats, normalized.x, chart_style_context.formats, time=False
            )
            is not None
            else None
        ),
        size=normalized.size,
        shape=normalized.shape,
        style=ResolvedScatterStyle(
            point_mark=scatter_point_mark,
            single_series_fill=single_series_fill,
            title_font=_tf,
            label_is_house=scatter_label_is_house,
            label_usable_ratio=chart_style_context.label_usable_ratio,
            **style_tail,
        ),
        layers=resolved_layers,
        annotations=annotations.render(
            ay,
            style_tail["axis_x"],
            resolved_layers,
            point_radius(
                scatter_point_mark.size,
                ring=unfilled_ring(scatter_point_mark),
                size_channel=None
                if normalized.size is None
                else [row.get(normalized.size) for row in data],
            ),
        ),
        annotation_gaps=annotations.gaps,
    )
