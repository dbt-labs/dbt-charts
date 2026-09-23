"""Line chart resolver."""

from __future__ import annotations

from dbt_charts.core.compile.errors import CompilationError
from dbt_charts.core.compile.merge import merge_onto_base
from dbt_charts.core.compile.models.chart.normalized import (
    LineChart,
)
from dbt_charts.core.compile.models.chart.resolved import (
    ResolvedLineChart,
)
from dbt_charts.core.compile.models.style.context import ChartStyleContext
from dbt_charts.core.compile.models.style.resolved import ResolvedLineStyle
from dbt_charts.core.compile.resolve.chart._axes import (
    _author_asked_for_endpoint_labels,
    _authored_legend,
    _bake_ay_orient,
    _endpoint_labels_off_for_layers,
    _endpoint_labels_off_for_multiples,
    _reject_dual_axis_layered_endpoint_labels,
    cartesian_color_domain_values,
    cartesian_series_naming,
    cartesian_top_legend_entries,
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
    _authored_axis_y_ticks_count,
    _bake_y_zero,
    _bake_zero_flag,
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
    _build_resolved_line_mark,
    _label_format_fallback,
    _measure_tooltip_format,
)
from dbt_charts.core.compile.resolve.chart._palette import (
    _effective_palette,
    _effective_requested_alias_palette,
    _effective_single_series_fill,
    _resolved_series_label,
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
from dbt_charts.core.compile.resolve.chart.adaptive_stroke import (
    bake_line_stroke,
    bake_point_companions,
    resolve_px_per_point,
    stroke_from_px_per_point,
)
from dbt_charts.core.compile.resolve.chart.tick_values import numeric_domain_bounds
from dbt_charts.core.compile.resolve.style.chart_context import (
    build_chart_style_context,
)
from dbt_charts.core.diagnostics.codes_compile import (
    ERR_LINE_Y_NOT_NUMERIC,
)
from dbt_charts.core.text.format_d3 import is_d3_si_spec
from dbt_charts.core.utils import (
    layered_endpoint_rail_fires,
    layered_endpoint_rail_shape,
)

__all__ = [
    "_resolve_line",
]


def _resolve_line(
    normalized: LineChart,
    dataset: ChartDataset,
    chart_style_context: ChartStyleContext,
    width: float,
    datasets: LayerDatasets,
    automatic_link_candidate: AutomaticLinkCandidate,
    variables: ChartTextVariables,
) -> ResolvedLineChart:
    data = dataset.all_rows()
    multiples_scale = (
        normalized.multiples.scale if normalized.multiples is not None else "shared"
    )
    x_ch_type = _classify_to_channel_type(normalized.x, data, is_dimension=True)
    # Line semantics fix x=dimension, y=value — the axis cascade hardcodes y
    # to "quantitative" regardless of the real column type. A categorical y
    # silently bakes a NaN axis with zero marks; refuse instead. Checked
    # before plan_cartesian so a doubly-invalid chart reports this error, not
    # the axis bake's.
    _line_bad_y = _first_non_numeric_y(normalized.y, data)
    if _line_bad_y is not None:
        _line_y_hint = None
        if x_ch_type == "quantitative":
            _line_y_hint = (
                f"Column {normalized.x!r} looks numeric — did you mean to swap x and y?"
            )
        raise CompilationError.from_code(
            ERR_LINE_Y_NOT_NUMERIC,
            chart_id=normalized.id,
            y_field=_line_bad_y,
            hint=_line_y_hint,
        )
    chart_local_style_context = build_chart_style_context(
        chart_style_context, normalized
    )
    plan = plan_cartesian(
        normalized,
        data,
        chart_style_context,
        "line",
        x_ch_type,
        "quantitative",
        normalized.multiples,
        normalized.y,
        has_quantitative_axis=True,
    )
    line = chart_local_style_context.line
    primary = plan.primary
    channels = plan.channels
    channels, wide_measure_series = resolve_wide_measure_channels(
        normalized, channels, "line", has_layers=bool(normalized.layers)
    )
    author_asked_for_rail = _author_asked_for_endpoint_labels(normalized.style)
    endpoint_label_has_layers = layered_endpoint_rail_fires(
        layered_endpoint_rail_shape(normalized.x, normalized.y),
        [layer.color is None for layer in normalized.layers],
    )
    # Line qualifies for the top-legend fit rule -- once it has an overlay
    # layer to name -- but rarely reaches it in practice: a multi-series line
    # usually carries its own endpoint-label rail instead (see
    # cartesian_series_naming's `not suppress_legend` gate).
    color_domain_values = cartesian_color_domain_values(dataset, normalized.color)
    top_legend_series = (
        cartesian_top_legend_entries(
            normalized.y if isinstance(normalized.y, str) else None,
            normalized.y_label,
            normalized.layers,
            color_domain_values=color_domain_values,
            has_color=normalized.color is not None,
        )
        if normalized.layers
        else None
    )
    naming = cartesian_series_naming(
        normalized,
        channels,
        _authored_legend(primary),
        merge_onto_base(chart_style_context.legend, line.legend),
        width,
        _endpoint_labels_off_for_multiples(
            _endpoint_labels_off_for_layers(
                line.endpoint_labels, normalized, author_asked_for_rail
            ),
            normalized,
            author_asked_for_rail,
        ),
        endpoint_label_has_layers=endpoint_label_has_layers,
        has_layers=bool(normalized.layers),
        rail_eligible_for_suppression=True,
        suppress_wide_measure_series=wide_measure_series,
        multiples_wide_measure_series=wide_measure_series,
        top_legend_series=top_legend_series,
        plot_height_estimate=estimate_cartesian_plot_height(normalized, line, width),
        # Line and area never flip the dimension field onto Vega-Lite's
        # y-channel the way a horizontal bar does, so this reserve is never
        # dimension-driven for them. Their own measure axis usually keeps
        # the family default (right) too, but _bake_ay_orient can still
        # flip it left when the endpoint-label rail fires -- a real
        # quantitative left axis this reserve does not currently account
        # for (known gap, not fixed here; see
        # estimate_left_axis_reserve_px's own docstring).
        left_axis_reserve_px=estimate_left_axis_reserve_px(
            None, merge_onto_base(chart_style_context.legend, line.legend)
        ),
        card_padding_px=chart_style_context.card_padding,
        subtitle_present=bool(normalized.subtitle),
        # Line never flips its dimension field onto Vega-Lite's y-channel
        # the way a horizontal bar does -- the x-axis is always the
        # horizontal rail.
        axis_title_costs_height=plan.ax_merged.title.visible is not False,
    )
    endpoint_labels = naming.endpoint_labels
    _reject_dual_axis_layered_endpoint_labels(
        normalized.id,
        normalized.layers,
        endpoint_labels.visible and endpoint_label_has_layers,
    )
    ax_merged, ay_merged = plan.ax_merged, plan.ay_merged
    y_field_line = normalized.y if isinstance(normalized.y, str) else None
    # The zero-anchor decision (ladder domain) and the emitted scale's "zero"
    # flag must be derived from the same extent, or the ladder gets baked for
    # a domain the scale never adopts. _zero_floats is that one shared extent
    # for both single- and multi-metric line.
    _zero_floats = _zero_anchor_floats(normalized.y, data)
    ay_merged = _bake_ay_orient(
        ay_merged,
        channels,
        endpoint_labels.visible,
        wide_measure_series=wide_measure_series,
        has_layers=endpoint_label_has_layers,
    )
    _ay_cont_line = ay_merged.scale.continuous if ay_merged.scale is not None else None
    authored_y_domain = _ay_cont_line.domain if _ay_cont_line is not None else None
    _ay_pre_zero_bake = ay_merged
    if y_field_line or isinstance(normalized.y, list):
        ay_merged = _bake_y_zero(ay_merged, _zero_floats, "line")
    _reject_non_positive_log_scale_data(
        normalized.id,
        ay_merged,
        (
            [y_field_line]
            if y_field_line
            else (list(normalized.y) if isinstance(normalized.y, list) else [])
        ),
        data,
    )
    if _zero_floats:
        _lz = resolve_y_zero(
            _ay_pre_zero_bake,
            min(_zero_floats),
            max(_zero_floats),
            "line",
        )
        # Anchored: explicit True, or smart-zero abstained for all-positive data
        # (close-to-zero heuristic keeps floor at 0). Crossing-zero stays
        # span-relative — the heuristic returns None there, but the chart has no
        # zero to anchor to, so the sign of data_min is the deciding factor.
        # All-negative data no longer reaches None past the ratio threshold: the
        # heuristic mirrors its branches and returns an explicit False, which is
        # what keeps a far-from-zero negative line fitted.
        zero_anchored_line = _lz is True or (_lz is None and min(_zero_floats) >= 0.0)
        if (
            _lz is None
            and isinstance(normalized.y, list)
            and ay_merged.ticks.count is not None
            and numeric_domain_bounds(authored_y_domain) is None
        ):
            # _bake_y_zero above only bakes a definitive True/False; an
            # abstention still resolves to a zero-anchor decision for the
            # tick ladder below — bake that SAME decision onto the scale, or
            # a ladder baked for [0, max] renders against a scale VL
            # auto-fits to the real data extent, silently dropping every
            # rung below it. Scoped to multi-metric with a real ladder:
            # single-metric line reaches the same domain via the baseline
            # rule's ``datum: 0`` layer instead, and with no ladder there's
            # nothing to protect. An authored domain is excluded too —
            # resolve_measure_y_scale (emit-side) strips "zero" once a
            # domain is pinned, and zero_anchor_domain_floor excludes an
            # authored tick ladder from ever sourcing a domainMin, for the
            # same reason: neither may silently override an authored bound.
            _post_bake_cont = (
                ay_merged.scale.continuous if ay_merged.scale is not None else None
            )
            if _post_bake_cont is None or _post_bake_cont.type != "log":
                ay_merged = _bake_zero_flag(ay_merged, zero_anchored_line)
    else:
        zero_anchored_line = True
    # Tick resolution needs every value drawn against the shared measure
    # scale — the single-y path already collects layer values via
    # _shared_y_values; multi-metric has no layers, so span its own y fields
    # directly (mirrors the "y_floats spans every series" contract
    # _resolve_cartesian_ticks documents).
    line_y_values = (
        _shared_y_values(data, y_field_line, normalized, datasets)
        if y_field_line
        else _zero_floats
    )
    line_ticks = _resolve_cartesian_ticks(
        normalized.id,
        ay_merged,
        line_y_values,
        zero_anchor=zero_anchored_line,
        authored_ticks_count=_authored_axis_y_ticks_count(normalized.style),
        scale=multiples_scale,
    )
    # Line's x is always a bottom-orient temporal/ordinal axis — no left/right edge.
    # No tick_values on the categorical axis -- the non-compacting bake
    # can't fire regardless, but format_authored is required, not defaulted
    # (see build_resolved_axis's docstring).
    tooltip_format_values = quantitative_channel_values(data, normalized.y)
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
        ticks=line_ticks,
        column_forming=True,
        measure_tooltip_format=_measure_tooltip_format(
            normalized, primary, chart_style_context, values=tooltip_format_values
        ),
        tooltip_format_values=tooltip_format_values,
        # Line semantics fix x=dimension, y=value -- see the same comment
        # above near the categorical-y guard.
        ax_is_quantitative=x_ch_type == "quantitative",
        ay_is_quantitative=True,
        zero_anchor=zero_anchored_line,
        # A multi-series (color or wide) line with endpoint_labels.visible
        # renders its label pane as a second, unscaled view sharing the
        # y-scale -- Vega-Lite's shared-scale merge then silently drops this
        # axis's baked domain_min (see _y_domain_floor's docstring). Line has
        # no stack concept, so every color/wide multi-series line with the
        # rail on hits this, unconditionally. A layered single-series line
        # reaches the same rail through a different door -- render's own gate
        # (endpoint_rail_layout) fires on colorless layers too -- so it is
        # tested with the same predicate render uses, not re-derived.
        endpoint_rail_may_discard_domain=(
            endpoint_labels.visible
            and (
                normalized.color is not None
                or wide_measure_series
                or endpoint_label_has_layers
            )
        ),
    )
    axis_is_house = (
        ay.labels.format is not None
        and is_d3_si_spec(ay.labels.format)
        and (not plan.ay_format_authored or plan.ay_format_is_alias)
    )
    _primary_marks = primary.marks if primary is not None else None
    _primary_line = _primary_marks.line if _primary_marks is not None else None
    resolved_line_labels, line_label_is_house = _label_format_fallback(
        line.marks.line.labels,
        ay.labels.format,
        axis_is_house,
        chart_style_context.formats,
    )
    line_mark_with_labels = line.marks.line.model_copy(
        update={"labels": resolved_line_labels}
    )
    # Density-adaptive stroke: bake BEFORE building the resolved mark so the
    # resolver paints the final width; render reads it verbatim.
    # Peek the authored stroke before cascade to distinguish "unset" from "set
    # to the fallback value".  A zero width is the "no stroke" sentinel — never
    # overwrite it.  The faceted per-cell width uses facet_panel_width() so
    # the baked stroke matches what each panel actually renders into.
    _line_stroke_authored = (
        _primary_line is not None
        and _primary_line.stroke is not None
        and _primary_line.stroke.width is not None
    )
    # The point-companion density trigger below reads px-per-point regardless
    # of whether the line stroke itself was pinned — an authored stroke width
    # and the point-density trigger are unrelated knobs; gating this on
    # _line_stroke_authored too left points permanently off whenever an
    # author pinned marks.line.stroke.width, at any density.
    _px_per_point = 0.0
    if normalized.x is not None:
        _px_per_point = resolve_px_per_point(
            channels,
            dataset,
            normalized.x,
            width,
            normalized.multiples,
            bool(ay.mirror),
        )
    _adaptive_stroke = (
        stroke_from_px_per_point(_px_per_point) if not _line_stroke_authored else 0.0
    )
    line_mark_with_labels = bake_line_stroke(line_mark_with_labels, _adaptive_stroke)
    resolved_line_mark = _build_resolved_line_mark(line_mark_with_labels)
    resolved_point_labels, point_label_is_house = _label_format_fallback(
        line.marks.point.labels,
        ay.labels.format,
        axis_is_house,
        chart_style_context.formats,
    )
    line_point_mark = line.marks.point.model_copy(
        update={"labels": resolved_point_labels}
    )
    # Point companions track the effective line stroke — the derivation lives
    # in bake_point_companions, next to bake_line_stroke. Gated only on there
    # being a real stroke to track (the zero-sentinel path leaves
    # resolved_line_mark.stroke.width at 0.0, which must not zero
    # already-visible points); independent of whether that stroke came from
    # the adaptive bake or an author pin. The density trigger gates the size
    # half only, inside the helper.
    if resolved_line_mark.stroke.width > 0:
        line_point_mark = bake_point_companions(
            line_point_mark,
            resolved_line_mark.stroke.width,
            _px_per_point,
            # Cascaded value, not chart-tier-only: board/theme-tier size
            # authoring must be seen too (see PointMarkStyle.size — the
            # global default is unset precisely so any tier setting it reads
            # as authored here).
            size_authored=line_point_mark.size is not None,
            # Cascaded for the same reason as size — the global theme slot
            # is unset, so any tier that set a ring reads as authored here.
            ring_authored=line_point_mark.stroke_width is not None,
        )
    _tf = _title_font(normalized, chart_local_style_context, width)
    bkw = _base_kwargs(
        normalized,
        chart_style_context,
        channels,
        line.legend,
        _effective_palette(chart_style_context, primary),
        requested_alias_palette=_effective_requested_alias_palette(
            chart_style_context, primary
        ),
        automatic_link_candidate=automatic_link_candidate,
        layout_padding=line.padding,
        suppress_legend=naming.suppress_legend,
        top_legend=naming.top_legend,
        force_legend_visible=naming.force_legend_visible,
        legend_position_overridden_by_width=naming.legend_position_overridden_by_width,
    )
    resolved_layers = _resolve_layer_list(
        normalized.layers,
        chart_style_context,
        "line",
        line,
        normalized.query_name,
        _adaptive_stroke,
        _px_per_point,
    )
    if authored_y_domain is not None:
        _check_layers_y_domain(normalized.id, resolved_layers, authored_y_domain)
    _ck = _cartesian_kwargs(
        normalized,
        chart_local_style_context,
        variables,
        data,
        "line",
        panel_axes=dataset.axes,
    )
    _ck, wide_measures = bake_wide_measures_kwargs(normalized.y, _ck)
    return ResolvedLineChart(
        **bkw,
        **_ck,
        wide_measures=wide_measures,
        chart_type="line",
        style=ResolvedLineStyle(
            series_label=_resolved_series_label(
                chart_style_context,
                primary,
                width,
                chart_local_style_context.ink_canvas,
            ),
            line_mark=resolved_line_mark,
            point_mark=line_point_mark,
            endpoint_labels=endpoint_labels,
            single_series_fill=_effective_single_series_fill(
                chart_style_context,
                primary,
                rhythm_slot=normalized.rhythm_slot,
                has_layers=bool(normalized.layers),
            ),
            label_usable_ratio=chart_style_context.label_usable_ratio,
            dashes=chart_style_context.dashes,
            title_font=_tf,
            line_label_is_house=line_label_is_house,
            point_label_is_house=point_label_is_house,
            **style_tail,
        ),
        layers=resolved_layers,
    )
