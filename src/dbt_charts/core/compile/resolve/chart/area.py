"""Area chart resolver and encoding validation."""

from __future__ import annotations

from typing import Literal

from dbt_charts.core.compile.errors import CompilationError
from dbt_charts.core.compile.merge import merge_onto_base
from dbt_charts.core.compile.models.chart.normalized import (
    AreaChart,
)
from dbt_charts.core.compile.models.chart.resolved import (
    ResolvedAreaChart,
)
from dbt_charts.core.compile.models.style.context import ChartStyleContext
from dbt_charts.core.compile.models.style.resolved import ResolvedAreaStyle
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
    _bake_normalize_domain,
    _bake_y_zero,
    _CartesianTickResolution,
    _numeric_y_values,
    _reject_non_positive_log_scale_data,
    _resolve_cartesian_ticks,
    _resolve_stacked_bar_ticks,
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
    _build_resolved_area_line,
    _build_resolved_area_mark,
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
    density_adaptive_stroke,
)
from dbt_charts.core.compile.resolve.chart.enrich import (
    classify_column_type,
    first_non_null_samples,
)
from dbt_charts.core.compile.resolve.style.chart_context import (
    build_chart_style_context,
)
from dbt_charts.core.diagnostics.codes_compile import (
    ERR_AREA_ENCODING_SWAPPED,
    ERR_AREA_LOG_SCALE_INDEPENDENT_MULTIPLES,
    ERR_AREA_STACKED_LOG_SCALE_NOT_SUPPORTED,
    ERR_AREA_STACKED_MARK_STYLE_CLEARED,
    ERR_AREA_STACKED_STROKE_INCOMPLETE,
)
from dbt_charts.core.text.format_d3 import is_d3_si_spec
from dbt_charts.core.utils import (
    layered_endpoint_rail_fires,
    layered_endpoint_rail_shape,
    numeric_column_values,
)

__all__ = [
    "_resolve_area",
    "_validate_area_encoding",
]


def _validate_area_encoding(
    normalized: AreaChart,
    dataset: ChartDataset,
    x_channel_type: str,
    resolved_stack: Literal["none", "zero", "normalize", "center"],
) -> None:
    """Reject categorical y and numeric-x stacked encodings before orchestration."""
    data = dataset.all_rows()
    # Area semantics fix x=dimension, y=value — unlike bar there is no
    # style.orientation knob to rotate an area chart, so an author who swaps
    # x/y to "rotate" the chart gets no error, just a scrambled encoding: the
    # axis cascade below hardcodes y to "quantitative" regardless of the real
    # column type. Two failure modes, both refused here:
    #  1. y is plain-categorical (e.g. a string label) — mirrors bar's
    #     categorical-y guard exactly, using classify_column_type (not the
    #     stricter _classify_to_channel_type above) so numeric-looking
    #     strings from untyped sources still count as numeric.
    #  2. x is a native numeric measure (_classify_to_channel_type ==
    #     "quantitative") *and* mostly unique per row (its distinct-value
    #     ratio is high) while color splits the data into multiple series,
    #     with stack "center"/"normalize" active. A properly-authored
    #     dimension repeats once per color series at each position (a year
    #     column, even stored as a plain int, has few distinct values
    #     relative to row count); the swapped-in value column is a
    #     continuous measure that rarely repeats, so Vega-Lite can't share
    #     x positions across series — the stack collapses to a sliver
    #     (center) or the normalize math runs on the wrong axis (year values
    #     printed as "201800%"). Checking cardinality (not just native type)
    #     avoids flagging a legitimate int-typed year dimension, which is
    #     also "quantitative" by raw type but repeats across color groups.
    #     A y column that also happens to be numeric-looking (e.g. a bare
    #     year) evades check 1, so this catches it independently.
    # Both skip columns with zero samples in `data` (a data-binding question
    # for a different check). Two gaps accepted for this stopgap (tracked
    # with the real rotated-area orientation feature): a numeric-looking-y
    # swap with no stacking (stack "none"/"zero"), and the same swap with no
    # `color` at all (single-series area) — check 2 requires `color` because
    # cardinality-vs-row-count is only a meaningful signal once there are
    # multiple series sharing x positions. The > 0.5 ratio threshold is
    # deliberately loose: a fully-populated N-series stack yields ratio 1/N,
    # so N=2 sits exactly at the boundary (not flagged) and only trips on
    # N=2 with missing cells — a narrow, accepted false-positive corner for
    # a heuristic guard.
    if isinstance(normalized.y, str):
        area_y_fields: list[str] = [normalized.y]
    elif isinstance(normalized.y, list):
        area_y_fields = normalized.y
    else:
        area_y_fields = []
    area_x_is_measure_with_stack = False
    if (
        x_channel_type == "quantitative"
        and resolved_stack in ("center", "normalize")
        and normalized.x is not None
        and normalized.color is not None
    ):
        # Checked per panel, not over the pooled whole-dataset rows: a
        # faceted chart repeats the same x values once per panel, which
        # shrinks the pooled distinct-value ratio and can hide a swapped
        # encoding that every individual panel actually exhibits (each
        # panel is what Vega-Lite actually stacks in one facet cell — a
        # ratio computed from the union is a false-negative risk, not a
        # meaningful signal). Any single panel tripping the ratio is
        # enough; no fold/reduce needed since this is a boolean verdict,
        # not a scalar to aggregate.
        for panel in dataset.panels:
            # Cardinality ratio is stable well before reading every row —
            # cap the scan instead of materializing the full column on
            # every qualifying panel (bar's guard likewise caps at the
            # default 20; this one needs more rows to estimate a ratio,
            # not the whole result set).
            panel_x_samples = first_non_null_samples(
                normalized.x, panel.rows, limit=500
            )
            panel_color_samples = first_non_null_samples(
                normalized.color, panel.rows, limit=500
            )
            panel_x_ratio = (
                len(set(panel_x_samples)) / len(panel_x_samples)
                if panel_x_samples
                else 0.0
            )
            panel_distinct_colors = len(set(panel_color_samples))
            if panel_distinct_colors > 1 and panel_x_ratio > 0.5:
                area_x_is_measure_with_stack = True
                break
    for area_y_field in area_y_fields:
        area_y_samples = first_non_null_samples(area_y_field, data)
        if not area_y_samples:
            continue
        area_y_not_numeric = (
            classify_column_type(area_y_field, area_y_samples) != "numeric"
        )
        if not (area_y_not_numeric or area_x_is_measure_with_stack):
            continue
        area_y_hint = None
        if x_channel_type == "quantitative":
            area_y_hint = (
                f"Column {normalized.x!r} looks numeric — did you mean to swap x and y?"
            )
        if area_y_not_numeric:
            area_reason = f"y column {area_y_field!r} is not numeric."
        else:
            area_reason = (
                f"x column {normalized.x!r} is numeric, and "
                f"`style.stack: {resolved_stack}` needs x to be a repeatable "
                "dimension, not a continuous value."
            )
        raise CompilationError.from_code(
            ERR_AREA_ENCODING_SWAPPED,
            chart_id=normalized.id,
            reason=area_reason,
            hint=area_y_hint,
        )


def _resolve_area(
    normalized: AreaChart,
    dataset: ChartDataset,
    chart_style_context: ChartStyleContext,
    width: float,
    datasets: LayerDatasets,
    automatic_link_candidate: AutomaticLinkCandidate,
    variables: ChartTextVariables,
) -> ResolvedAreaChart:
    data = dataset.all_rows()
    multiples_scale = (
        normalized.multiples.scale if normalized.multiples is not None else "shared"
    )
    x_ch_type = _classify_to_channel_type(normalized.x, data, is_dimension=True)
    # Checked before plan_cartesian's axis bake so a doubly-invalid chart
    # reports this encoding error, not the axis bake's. When chart.stack is
    # authored, resolved_stack doesn't need the context and the validator
    # runs first; when it's unset, resolved_stack needs .area.stack off the
    # context that's about to be built, so the validator runs after.
    if normalized.stack is not None:
        resolved_stack = normalized.stack
        _validate_area_encoding(normalized, dataset, x_ch_type, resolved_stack)
        chart_local_style_context = build_chart_style_context(
            chart_style_context, normalized
        )
    else:
        chart_local_style_context = build_chart_style_context(
            chart_style_context, normalized
        )
        resolved_stack = chart_local_style_context.area.stack
        _validate_area_encoding(normalized, dataset, x_ch_type, resolved_stack)
    plan = plan_cartesian(
        normalized,
        data,
        chart_style_context,
        "area",
        x_ch_type,
        "quantitative",
        normalized.multiples,
        normalized.y,
        has_quantitative_axis=True,
    )
    area = chart_local_style_context.area
    primary = plan.primary
    channels = plan.channels
    channels, wide_measure_series = resolve_wide_measure_channels(
        normalized, channels, "area", has_layers=bool(normalized.layers)
    )
    author_asked_for_rail = _author_asked_for_endpoint_labels(normalized.style)
    endpoint_label_has_layers = layered_endpoint_rail_fires(
        layered_endpoint_rail_shape(normalized.x, normalized.y),
        [layer.color is None for layer in normalized.layers],
    )
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
        merge_onto_base(chart_style_context.legend, area.legend),
        width,
        _endpoint_labels_off_for_multiples(
            _endpoint_labels_off_for_layers(
                area.endpoint_labels, normalized, author_asked_for_rail
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
        plot_height_estimate=estimate_cartesian_plot_height(normalized, area, width),
        # Line and area never flip the dimension field onto Vega-Lite's
        # y-channel the way a horizontal bar does, so this reserve is never
        # dimension-driven for them. Their own measure axis usually keeps
        # the family default (right) too, but _bake_ay_orient can still
        # flip it left when the endpoint-label rail fires -- a real
        # quantitative left axis this reserve does not currently account
        # for (known gap, not fixed here; see
        # estimate_left_axis_reserve_px's own docstring).
        left_axis_reserve_px=estimate_left_axis_reserve_px(
            None, merge_onto_base(chart_style_context.legend, area.legend)
        ),
        card_padding_px=chart_style_context.card_padding,
        subtitle_present=bool(normalized.subtitle),
        # Area never flips its dimension field onto Vega-Lite's y-channel
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
    y_field_area = normalized.y if isinstance(normalized.y, str) else None
    _area_floats = _zero_anchor_floats(normalized.y, data)
    ay_merged = _bake_ay_orient(
        ay_merged,
        channels,
        endpoint_labels.visible,
        wide_measure_series=wide_measure_series,
        has_layers=endpoint_label_has_layers,
    )
    _ay_cont_area = ay_merged.scale.continuous if ay_merged.scale is not None else None
    authored_y_domain = _ay_cont_area.domain if _ay_cont_area is not None else None
    _ay_pre_zero_bake = ay_merged
    # A streamgraph's y=0 is the silhouette's visual centerline, not a
    # baseline, so a zero anchor is not a weaker opinion there — it is a
    # meaningless one. VL floats each center-stacked column up by
    # (max_total - this_total) / 2, so a baked domainMin of 0 happens to clip
    # nothing; leaving the bake in would make that accident load-bearing.
    # An all-negative axis can only anchor at 0 where something downstream can
    # establish its floor. The shared-scale, non-stacked ladder bakes one
    # (`negative_floor` in `_resolve_cartesian_ticks`); a stack's floor is its
    # per-category negative total, which nothing derives, and `independent`
    # deliberately bakes no chart-wide bound at all. Without a floor, render
    # falls back to `zero_anchor_floor`'s literal 0.0 and pins it as the
    # BOTTOM of a negative domain, collapsing every mark onto one pixel row.
    # Nothing is lost by leaving these unbaked: Vega-Lite already anchors a
    # stack at 0, and each independent panel auto-fits its own.
    all_negative_area = bool(_area_floats) and max(_area_floats) < 0
    negative_anchor_has_no_floor = all_negative_area and (
        resolved_stack not in (None, "none") or multiples_scale == "independent"
    )
    if (
        resolved_stack != "center"
        and not negative_anchor_has_no_floor
        and (y_field_area or isinstance(normalized.y, list))
    ):
        ay_merged = _bake_y_zero(ay_merged, _area_floats, "area")
    _log_y_fields = (
        [y_field_area]
        if y_field_area
        else (normalized.y if isinstance(normalized.y, list) else [])
    )
    # The layer ambiguity check must see the pre-workaround domain state: it is
    # about an authored chart-level domain, not the synthetic area-log domain.
    _reject_non_positive_log_scale_data(normalized.id, ay_merged, _log_y_fields, data)
    _ay_cont_log = ay_merged.scale.continuous if ay_merged.scale is not None else None
    if (
        _ay_cont_log is not None
        and _ay_cont_log.type == "log"
        and resolved_stack
        not in (
            None,
            "none",
        )
    ):
        raise CompilationError.from_code(
            ERR_AREA_STACKED_LOG_SCALE_NOT_SUPPORTED,
            chart_id=normalized.id,
            stack=resolved_stack,
        )
    if (
        _log_y_fields
        and _ay_cont_log is not None
        and _ay_cont_log.type == "log"
        and _ay_cont_log.domain is None
        and multiples_scale == "independent"
    ):
        # The domain baked just below (when this guard doesn't fire) is
        # computed once from every panel's data pooled together
        # (dataset.all_rows() via `data`) — it has to be, since a single
        # log-area domain is what avoids Vega-Lite's degenerate log-area
        # rendering (see the comment on that bake). Suppressing the bake
        # under `scale: independent` would silently reintroduce that
        # degeneration; baking it anyway would silently apply one shared
        # domain to every panel, contradicting `scale: independent`'s
        # promise. Refuse rather than emit a spec whose `resolve` and
        # `encoding` contradict each other. An explicitly AUTHORED domain
        # (the `_ay_cont_log.domain is None` guard above) is unaffected —
        # that is the author's own explicit choice, the same as any other
        # authored domain overriding `scale: independent` elsewhere.
        raise CompilationError.from_code(
            ERR_AREA_LOG_SCALE_INDEPENDENT_MULTIPLES,
            chart_id=normalized.id,
        )
    if (
        _log_y_fields
        and _ay_cont_log is not None
        and _ay_cont_log.type == "log"
        and _ay_cont_log.domain is None
    ):
        # Vega-Lite's automatic domain inference degenerates for an area mark
        # on a log scale with no explicit domain (verified: renders as a flat
        # line with zero visible tick labels — an explicit domain avoids
        # whatever internal baseline computation area's fill triggers; a line
        # mark on the identical scale has no such issue and is left to VL's
        # inference). Bake the data extent directly rather than letting VL
        # infer it. Multi-metric (y: [a, b]) spans every metric's own
        # extent — the same degeneration hits every layer's area mark, and
        # they all share this one axis_y scale.
        _log_y_floats = [
            v for field in _log_y_fields for v in numeric_column_values(data, field)
        ]
        if _log_y_floats and ay_merged.scale is not None:
            _new_cont = _ay_cont_log.model_copy(
                update={"domain": [min(_log_y_floats), max(_log_y_floats)]}
            )
            ay_merged = ay_merged.model_copy(
                update={
                    "scale": ay_merged.scale.model_copy(
                        update={"continuous": _new_cont}
                    )
                }
            )
    if resolved_stack == "center":
        # A streamgraph's y=0 is the silhouette's visual centerline, not a
        # floor (mirrors the _bake_y_zero skip above) -- the published fact
        # must say so too, not report an anchor decision that never ran.
        zero_anchored_area = False
    elif _ay_cont_log is not None and _ay_cont_log.type == "log":
        # A log scale carries no zero anchor -- log(0) is undefined, so no
        # anchor decision is meaningful (mirrors _bake_y_zero's own log skip).
        zero_anchored_area = False
    elif _area_floats:
        _az = resolve_y_zero(
            _ay_pre_zero_bake,
            min(_area_floats),
            max(_area_floats),
            "area",
        )
        zero_anchored_area = not negative_anchor_has_no_floor and (
            _az is True or (_az is None and min(_area_floats) >= 0.0)
        )
    else:
        zero_anchored_area = True
    is_stacked = resolved_stack not in (None, "none")
    x_field_area = normalized.x if isinstance(normalized.x, str) else None
    if resolved_stack == "normalize":
        # normalize-stack domain is always [0,1].
        area_ticks = _CartesianTickResolution((), None)
        ay_merged = _bake_normalize_domain(ay_merged)
    elif is_stacked and x_field_area and _log_y_fields:
        # Stacked area ladders against the cumulative column total, not the raw
        # per-series range. Without this a chart whose columns sum to 154 labels
        # an axis that stops at 70, leaving two thirds of the plot unlabeled —
        # the marks were always right, only the ladder was wrong.
        #
        # Mirrors bar's stacked path; _resolve_stacked_bar_ticks is data-shape
        # generic despite the name. `center` (streamgraph) takes the same ladder
        # because Vega-Lite floats each column up by
        # (max_total - this_total) / 2, so the tallest column sits flush at
        # [0, max_total] and no column exceeds that ceiling — the same
        # zero-anchored domain a plain zero-stack renders on.
        area_ticks = _CartesianTickResolution(
            _resolve_stacked_bar_ticks(
                ay_merged, dataset, _log_y_fields, x_field_area, multiples_scale
            ),
            None,
        )
    else:
        area_y_values = (
            _shared_y_values(data, y_field_area, normalized, datasets)
            if y_field_area
            else _numeric_y_values(data, tuple(_log_y_fields))
        )
        area_ticks = _resolve_cartesian_ticks(
            normalized.id,
            ay_merged,
            area_y_values,
            zero_anchor=zero_anchored_area,
            authored_ticks_count=_authored_axis_y_ticks_count(normalized.style),
            scale=multiples_scale,
        )
    # domain_max/min stay None for stacked area: Vega-Lite owns the stacked
    # domain, and baking a nice-rounded ladder rung as domainMax would clip
    # marks above it. Headroom on area remains single-series-only.
    area_domain_max = area_ticks.domain_max if not is_stacked else None
    area_domain_min = area_ticks.domain_min if not is_stacked else None
    # Area's x is always a bottom-orient temporal/ordinal axis — no left/right edge.
    # No tick_values on the categorical axis -- the non-compacting bake
    # can't fire regardless, but format_authored is required, not defaulted
    # (see build_resolved_axis's docstring).
    tooltip_format_values = quantitative_channel_values(data, normalized.y)
    ay, area_style_tail = build_cartesian_axes(
        normalized.id,
        chart_style_context,
        ax_merged,
        ay_merged,
        ax_band_position=plan.ax_band_position,
        ay_band_position=plan.ay_band_position,
        ax_edge=None,
        ay_format_authored=plan.ay_format_authored,
        ay_format_is_alias=plan.ay_format_is_alias,
        ticks=_CartesianTickResolution(
            area_ticks.ticks, area_domain_max, area_domain_min
        ),
        column_forming=True,
        measure_tooltip_format=_measure_tooltip_format(
            normalized, primary, chart_style_context, values=tooltip_format_values
        ),
        tooltip_format_values=tooltip_format_values,
        # Area semantics fix x=dimension, y=value -- see _validate_area_encoding.
        ax_is_quantitative=x_ch_type == "quantitative",
        ay_is_quantitative=True,
        zero_anchor=zero_anchored_area,
        # A non-stacked, multi-series (color or wide) area with
        # endpoint_labels.visible renders its label pane as a second,
        # unscaled view sharing the y-scale -- Vega-Lite's shared-scale
        # merge then silently drops this axis's baked domain_min (see
        # _y_domain_floor's docstring). A real stack (normalize/center)
        # re-pins the domain explicitly at render time (endpoint_labels.py's
        # own is_stacked branch), so only the non-stacked shape is at risk.
        # A layered single-series area reaches the same rail through a
        # different door -- render's gate fires on colorless layers too.
        endpoint_rail_may_discard_domain=(
            endpoint_labels.visible
            and not is_stacked
            and (
                normalized.color is not None
                or wide_measure_series
                or endpoint_label_has_layers
            )
        ),
    )
    area_mark_merged = area.marks.area
    line_mark_merged = area.marks.line
    # Stacked / streamgraph charts (any stack mode other than "none") swap the
    # overlap translucent-halo recipe for the stacked recipe declared on
    # ``area_mark.stacked`` — solid fill (area_mark.opacity) + a full-perimeter
    # background-color stroke, no halo (line_mark.stroke/halo_multiplier — the
    # perimeter stroke is drawn via the same fg-line mechanism as the overlap
    # recipe's top-edge stroke, so it overrides the LINE mark, not the area
    # mark). The chart's stack mode is known at compile time, so the selection
    # is baked here rather than branched at render; the emitter still consults
    # ``chart.stack`` to choose *composition* (single perimeter-stroked layer
    # vs. halo+top-line). A single unstacked series takes the whole recipe
    # too: translucency signals overlap and there is nothing to overlap with,
    # and a trend line on a solid fill reads as a band taller than its value.
    # ``chart.stack`` stays "none" for it, so the emitter keeps the
    # halo+top-line composition: the recipe's ``halo_multiplier: 0`` is what
    # suppresses that path's halo, and its top-edge line draws the separator.
    # Band count is the question, not y's spelling: a one-element wide list
    # draws one band, so it takes the same fill weight as the scalar y.
    is_single_series = not (
        normalized.color is not None
        or bool(normalized.layers)
        or (isinstance(normalized.y, list) and len(normalized.y) > 1)
    )
    on_stacked_recipe = is_stacked or is_single_series
    stacked_mark = area_mark_merged.stacked
    if on_stacked_recipe:
        if stacked_mark is None:
            raise CompilationError.from_code(
                ERR_AREA_STACKED_MARK_STYLE_CLEARED,
                chart_id=normalized.id,
            )
        if stacked_mark.opacity is not None:
            area_mark_merged = area_mark_merged.model_copy(
                update={"opacity": stacked_mark.opacity}
            )
        line_overrides = {
            field: value
            for field, value in (
                ("stroke", stacked_mark.stroke),
                ("halo_multiplier", stacked_mark.halo_multiplier),
            )
            if value is not None
        }
        if line_overrides:
            # This REPLACES marks.line.stroke rather than merging into it, so
            # any geometry the stacked recipe omits is simply gone — the
            # emitter then writes strokeCap/strokeJoin as None, Vega drops the
            # attributes, and the separator falls back to SVG butt/miter,
            # spiking each vertex. Scoped to this branch on purpose: a
            # multi-series overlap keeps marks.line.stroke intact, where an
            # authored `cap: null` is a legal state (ResolvedStrokeStyle
            # documents None as "use the VL default").
            if stacked_mark.stroke is not None and (
                stacked_mark.stroke.cap is None or stacked_mark.stroke.join is None
            ):
                raise CompilationError.from_code(
                    ERR_AREA_STACKED_STROKE_INCOMPLETE,
                    chart_id=normalized.id,
                )
            line_mark_merged = line_mark_merged.model_copy(update=line_overrides)
    axis_is_house = (
        ay.labels.format is not None
        and is_d3_si_spec(ay.labels.format)
        and (not plan.ay_format_authored or plan.ay_format_is_alias)
    )
    _primary_area_marks = (
        primary.marks if primary is not None and primary.marks is not None else None
    )
    resolved_area_line_labels, area_label_is_house = _label_format_fallback(
        line_mark_merged.labels,
        ay.labels.format,
        axis_is_house,
        chart_style_context.formats,
    )
    line_mark_merged = line_mark_merged.model_copy(
        update={"labels": resolved_area_line_labels}
    )
    # Density-adaptive stroke for area's top-edge line.  For stacked charts the
    # effective stroke is on the stacked perimeter (already merged above into
    # line_mark_merged); for overlap it's the top-edge line.  Peek authored width
    # from the raw patch BEFORE cascade so "unset" stays distinguishable.
    _area_line_stroke_authored = (
        _primary_area_marks is not None
        and _primary_area_marks.line is not None
        and _primary_area_marks.line.stroke is not None
        and _primary_area_marks.line.stroke.width is not None
    )
    # Charts on the stacked recipe -- stacked and single-series alike -- route
    # their edge stroke through marks.area.stacked.stroke; if the author set it
    # there, that is an author pin too.
    if not _area_line_stroke_authored and on_stacked_recipe:
        _stacked_patch = (
            _primary_area_marks.area.stacked
            if _primary_area_marks is not None and _primary_area_marks.area is not None
            else None
        )
        _area_line_stroke_authored = (
            _stacked_patch is not None
            and _stacked_patch.stroke is not None
            and _stacked_patch.stroke.width is not None
        )
    _area_adaptive_stroke = 0.0
    if not _area_line_stroke_authored and normalized.x is not None:
        _area_adaptive_stroke = density_adaptive_stroke(
            channels,
            dataset,
            normalized.x,
            width,
            normalized.multiples,
            bool(ay.mirror),
        )
    # On the stacked recipe the edge is a SEPARATOR, not a trend line. The
    # thick-at-sparse half of the formula is a line-presence rule that does not
    # fit a separator (the fills carry the weight), so cap the BASE edge at the
    # theme fallback — never thicker — while still letting it thin with density
    # for crisp boundaries. Single-series takes the same cap for a sharper
    # reason: its edge is background-colored, so an uncapped width would knock
    # visible pixels off the top of the band and understate every value. With
    # the default theme the adaptive floor exceeds the separator width, so the
    # cap always wins and the edge is the recipe's width.
    # Overlay layers get the uncapped value: a line/area layer is its own trend
    # mark, not a separator.
    _area_baked_stroke = _area_adaptive_stroke
    if (
        _area_baked_stroke > 0
        and on_stacked_recipe
        and line_mark_merged.stroke is not None
        and line_mark_merged.stroke.width is not None
    ):
        _area_baked_stroke = min(_area_baked_stroke, line_mark_merged.stroke.width)
    line_mark_merged = bake_line_stroke(line_mark_merged, _area_baked_stroke)
    # Area charts have no point-companion block: area.marks.point size is not
    # coupled to the top-edge stroke thickness (unlike line where point rings
    # visually track the line width).
    resolved_area_mark = _build_resolved_area_mark(area_mark_merged)
    resolved_line_mark = _build_resolved_area_line(line_mark_merged)
    _tf = _title_font(normalized, chart_local_style_context, width)
    bkw = _base_kwargs(
        normalized,
        chart_style_context,
        channels,
        area.legend,
        _effective_palette(chart_style_context, primary),
        requested_alias_palette=_effective_requested_alias_palette(
            chart_style_context, primary
        ),
        automatic_link_candidate=automatic_link_candidate,
        layout_padding=area.padding,
        suppress_legend=naming.suppress_legend,
        top_legend=naming.top_legend,
        force_legend_visible=naming.force_legend_visible,
        legend_position_overridden_by_width=naming.legend_position_overridden_by_width,
    )
    resolved_layers = _resolve_layer_list(
        normalized.layers,
        chart_style_context,
        "area",
        area,
        normalized.query_name,
        _area_adaptive_stroke,
        0.0,
    )
    if authored_y_domain is not None:
        _check_layers_y_domain(normalized.id, resolved_layers, authored_y_domain)
    _ck = _cartesian_kwargs(
        normalized,
        chart_local_style_context,
        variables,
        data,
        "area",
        panel_axes=dataset.axes,
    )
    _ck, wide_measures = bake_wide_measures_kwargs(normalized.y, _ck)
    return ResolvedAreaChart(
        **bkw,
        **_ck,
        wide_measures=wide_measures,
        chart_type="area",
        stack=resolved_stack,
        style=ResolvedAreaStyle(
            series_label=_resolved_series_label(
                chart_style_context,
                primary,
                width,
                chart_local_style_context.ink_canvas,
            ),
            area_mark=resolved_area_mark,
            line_mark=resolved_line_mark,
            point_mark=area.marks.point,
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
            label_is_house=area_label_is_house,
            **area_style_tail,
        ),
        layers=resolved_layers,
    )
