"""Scatter chart emitter for render-v2."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from dbt_charts.core.compile.models.chart.resolved.scatter import ResolvedScatterChart
from dbt_charts.core.compile.models.style.resolved import ResolvedAxisStyle
from dbt_charts.core.compile.models.style.theme.category_colors import (
    category_scale_for,
)
from dbt_charts.core.compile.resolve.chart._chart_rows import ChartDataset
from dbt_charts.core.compile.resolve.chart._wide_fields import (
    raw_wide_series_names,
    wide_measure_labels_for,
    wide_series_names,
)
from dbt_charts.core.render.chart._types import VLDict
from dbt_charts.core.render.chart.emitters._cartesian import (
    XYTitles,
    apply_domain_headroom_bounds,
    build_palette_config,
    canonicalize_cartesian_x_data,
    dimension_sort_to_vl,
    distinct_series_values,
    multiples_scale_independent,
    resolve_cartesian_x,
    resolve_xy_titles,
    spatial_color_scale,
    wide_measures_title,
)
from dbt_charts.core.render.chart.emitters._channels import (
    apply_color_legend,
    apply_legend_entry_order,
    categorical_color_encoding,
    channel_to_encoding,
    field_encoding,
    infer_vega_type_from_data,
)
from dbt_charts.core.render.chart.emitters._layers import emit_scatter_layer
from dbt_charts.core.render.chart.emitters._overlay import overlay_x_domain_values
from dbt_charts.core.render.chart.emitters._wide import (
    FoldedMeasures,
    fold_wide_measures,
)
from dbt_charts.core.render.chart.spec import ChartSpec, RenderBox
from dbt_charts.core.render.chart.type_inference import (
    _utc_time_label_expr,
    apply_x_tick_cadence,
    gate_label_format,
)
from dbt_charts.core.render.chart.vl_field_maps import (
    axis_to_vl,
    bake_tick_ladder,
    compose_axis_label_expr,
    emit_resolved_scale_vl,
    measure_axis_to_vl,
)
from dbt_charts.core.render.utils import normalize_data_types
from dbt_charts.core.text.case import format_display_text
from dbt_charts.core.text.format_d3 import is_time_format


def _wide_scatter_fold(
    chart: ResolvedScatterChart,
    data: list[dict[str, Any]],  # type-state: explicit_any — raw query result rows
) -> FoldedMeasures:
    """Fold list-valued measures (``y: [a, b, ...]``) into one VL fold for scatter.

    Scatter has no accumulation order (no stack, no line continuity to sort
    a last value against) -- display and fold order are both the fold's own
    RAW-sorted identity, the same convention bar/line's own no-stack/no-x
    branches fall back to ("matching VL's own default alphabetical domain
    inference"). Scatter's own authored ``color:`` path reaches the same
    order by omission (no explicit domain/scale pinned unless
    ``legend.values``/``category_colors`` is authored), so this isn't a new
    rule -- see ``ScatterEmitter.emit``'s plain color-series branch below.
    """
    assert chart.wide_measures
    measures = list(chart.wide_measures)
    dimension = chart.color
    wide_labels = wide_measure_labels_for(chart.wide_measures)
    series = wide_series_names(measures, dimension, data, wide_labels)
    raw_series = raw_wide_series_names(measures, dimension, data)
    return fold_wide_measures(
        measures,
        dimension,
        data,
        chart.palette,
        chart.legend,
        display_order=series,
        wide_measure_labels=wide_labels,
        fold_order=raw_series,
    )


def _emit_wide_scatter(
    chart: ResolvedScatterChart,
    data: list[dict[str, Any]],  # type-state: explicit_any — raw query result rows
    ay: ResolvedAxisStyle,
    xy: XYTitles,
    x_enc: VLDict | None,
) -> tuple[VLDict, VLDict, list[VLDict], list[VLDict]]:
    """The folded y/color/tooltip encoding + fold transforms for a wide
    (``y: [a, b]``) scatter -- everything ``ScatterEmitter.emit()`` needs
    beyond the x encoding it already built (shared with the plain path, so
    not rebuilt here).

    Returns ``(y_enc, color_enc, tooltip, transforms)``.
    """
    wide = _wide_scatter_fold(chart, data)
    y_axis = measure_axis_to_vl(ay, data, chart.wide_measures)
    y_axis = compose_axis_label_expr(y_axis, ay.ruler, ay)
    bake_tick_ladder(y_axis, ay)
    y_scale = emit_resolved_scale_vl(ay.scale)
    if "domain" not in y_scale:
        # Exact headroom-applied bounds baked at resolve(); an authored
        # `domain` (mapped above) always wins outright.
        y_scale = apply_domain_headroom_bounds(y_scale, ay.domain_max, ay.domain_min)
    y_enc: VLDict = {
        "field": wide.value_field,
        "type": "quantitative",
        "title": xy.y_title,
    }
    if y_axis:
        y_enc["axis"] = y_axis
    if y_scale:
        y_enc["scale"] = y_scale
    fmt = chart.style.tooltip_format
    if fmt:
        y_enc["format"] = fmt

    # wide.color's own encoding-level "title" is deliberately null -- any
    # non-null value here leaks into the legend's own swatch heading, since
    # the legend has no title of its own to fall back to instead. VL's
    # default per-mark tooltip falls back to a channel's raw field name when
    # its title is null, so without an explicit tooltip the hover card would
    # show the synthetic WIDE_VALUE_FIELD/WIDE_LABEL_FIELD names verbatim.
    # Build one explicitly, titled the same way bar/area/line's own
    # structured tooltip labels these two rows (measure axis title, "Series").
    tooltip: list[VLDict] = []
    if x_enc is not None:
        x_tooltip: VLDict = {
            "field": x_enc["field"],
            "type": x_enc["type"],
            "title": xy.x_title,
        }
        if "timeUnit" in x_enc:
            x_tooltip["timeUnit"] = x_enc["timeUnit"]
        tooltip.append(x_tooltip)
    value_tooltip: VLDict = {
        "field": wide.value_field,
        "type": "quantitative",
        "title": xy.y_title,
    }
    if fmt:
        value_tooltip["format"] = fmt
    tooltip.append(value_tooltip)
    tooltip.append({"field": wide.label_field, "type": "nominal", "title": "Series"})

    return y_enc, wide.color, tooltip, wide.transforms


@dataclass
class ScatterEmitter:
    def emit(
        self,
        chart: ResolvedScatterChart,
        box: RenderBox,
        dataset: ChartDataset,
        datasets: dict[str | None, list[dict[str, Any]]] | None = None,
    ) -> ChartSpec:
        data = dataset.all_rows()
        ax = chart.style.axis_x
        ay = chart.style.axis_y
        xy = resolve_xy_titles(
            chart.x,
            None if chart.wide_measures else chart.y,
            chart.x_label,
            (
                chart.y_label or wide_measures_title(chart.wide_measures)
                if chart.wide_measures
                else chart.y_label
            ),
            ax,
            ay,
            box,
            chart.id,
        )
        encoding: dict[str, Any] = {}
        transforms: list[VLDict] = []

        x_transformed = False
        if chart.x:
            # Decided from the RAW rows, before canonicalization ever runs:
            # canonicalize_cartesian_x_data's normalize_labeled_temporal
            # applies an is_year_shaped heuristic meant for bar/line/area's
            # dimension x (year-shaped integers ARE years there); scatter's
            # canonical x is a continuous measure, where that same heuristic
            # would misread a numeric column in the 1900-2100 band as a date
            # axis. The common scatter case: two continuous measures. Never
            # route this through resolve_axis_x_overlap's own directive —
            # its quantitative fast path unconditionally asserts
            # labelOverlap: false (never hide a label), which disables
            # Vega-Lite's own adaptive thinning of a crowded numeric axis.
            # That directive is fine for bar/line/area/heatmap, whose
            # typical x is temporal/categorical, but scatter's canonical
            # x is exactly the case it never gets tested against — a
            # dense quantitative axis reads as overlapping digits with it
            # forced on. Keep Vega-Lite's own default here, as before.
            if infer_vega_type_from_data(data, chart.x) == "quantitative":
                x_type = "quantitative"
                x_axis = axis_to_vl(ax)
                if ax.labels.angle is None:
                    x_axis["labelAngle"] = 0.0
                # Authored tick cadence still applies: this branch skips
                # build_cartesian_x_encoding, so it reaches the shared emitter
                # itself or ticks.count/step resolve and then vanish.
                apply_x_tick_cadence(x_axis, ax, chart.x, x_type)
                x_time_unit = None
            else:
                # Labeled bucket strings ("Q1 2024") and non-date-only temporal
                # values (datetime.datetime) reach resolve_cartesian_x's
                # axis.values/labelExpr/timeUnit unparseable by Vega's JS Date
                # otherwise — bar/line/area canonicalize their x rows before the
                # same call; scatter has no gap-fill scaffold of its own, so it
                # needs the same two-step precondition directly.
                data, x_transformed = canonicalize_cartesian_x_data(
                    data, chart.x, ax.time_unit
                )
                # Overlay layers may carry x buckets the base series doesn't
                # (a forward goal ramp against actuals). Vega-Lite unions the
                # sub-layer domains, so the axis must be built against that
                # union — both its tick values and its crowding measurement
                # — not against the base's own rows. Mirrors bar.py's
                # identical computation ahead of its own x encoding.
                # base_x_authored_temporal=False: scatter canonicalizes x
                # unconditionally (see render_cartesian_overlay call below),
                # never consulting gap_fill_ordinal_time's escape hatch.
                x_domain = (
                    overlay_x_domain_values(
                        chart.layers,
                        data,
                        chart.x,
                        ax,
                        False,
                        datasets,
                        chart.query_name,
                    )
                    if chart.layers
                    else None
                )
                # resolve_cartesian_x is the shared bar/line/area resolver
                # (overlap → axis_to_vl → build_cartesian_x_encoding);
                # mark_type "scatter" joins line/area's always-continuous-
                # temporal rule for a bucketed-calendar grain (a scatter
                # point needs its real position, not a band-snapped one),
                # which is also what gets it bar/line's Jan/2024 label
                # vocabulary instead of falling through to Vega-Lite's own
                # default temporal formatter.
                x_res = resolve_cartesian_x(
                    chart.x,
                    data,
                    ax,
                    chart.style.label_usable_ratio,
                    box.width,
                    chart.id,
                    "scatter",
                    domain_values=x_domain,
                    panel_fields=tuple(axis.field for axis in dataset.axes),
                )
                x_type, x_axis, x_time_unit = x_res.vl_type, x_res.axis, x_res.time_unit
            x_scale = emit_resolved_scale_vl(ax.scale, include_x_only=True)
            x_enc: dict[str, Any] = {
                "field": chart.x,
                "type": x_type,
                "title": xy.x_title,
            }
            if x_time_unit:
                x_enc["timeUnit"] = x_time_unit
            if x_axis:
                x_enc["axis"] = x_axis
            if x_scale:
                x_enc["scale"] = x_scale
            encoding["x"] = x_enc

        if chart.wide_measures:
            y_enc, color_enc, tooltip, transforms = _emit_wide_scatter(
                chart, data, ay, xy, encoding.get("x")
            )
            encoding["y"] = y_enc
            encoding["color"] = color_enc
            encoding["tooltip"] = tooltip
        elif chart.y:
            y_type = infer_vega_type_from_data(data, chart.y)
            y_axis = measure_axis_to_vl(ay, data, (chart.y,))
            y_axis = compose_axis_label_expr(y_axis, ay.ruler, ay)
            # A d3 axis format applied to categorical tick labels makes Vega
            # coerce every category string to NaN, and one over dates paints
            # the literal spec — gate_label_format raises for both rather
            # than silently dropping the authored format. A nominal/ordinal y
            # with a genuine time spec still routes through a UTC labelExpr
            # (mirrors the x-axis path in build_cartesian_x_encoding) instead
            # of raising: is_time_format exempts it inside the gate too, but
            # only this branch knows to compose the label expression.
            y_fmt = y_axis.get("format")
            if (
                y_type in ("nominal", "ordinal")
                and isinstance(y_fmt, str)
                and is_time_format(y_fmt)
            ):
                if "labelExpr" not in y_axis:
                    y_axis["labelExpr"] = _utc_time_label_expr(y_fmt)
                # Vega validates "format" independently of labelExpr — a time
                # spec left there (not a valid d3 NUMBER format) raises
                # "invalid format" at render, even though labelExpr is what
                # actually paints the tick. Must go, unlike the elif branch's
                # legal-numeric-category case below, where the format is a
                # real number spec and stays.
                del y_axis["format"]
            elif isinstance(y_fmt, str):
                # A band-scale y (dot plot) has axis_x as its measure sibling,
                # the opposite of the gate's own axis_x-authored default — a
                # temporal y has no such sibling to point at, so it falls
                # through to the gate's own time-spec remedy instead.
                gate_label_format(
                    y_fmt,
                    chart.y,
                    data,
                    y_type,
                    setting="axis_y.labels.format",
                    remedy=(
                        "A categorical y (dot plot) has no measure ticks to "
                        "format — the measure is axis_x here. Author the "
                        "number format on style.axis_x.labels.format, or "
                        "remove it."
                    )
                    if y_type in ("nominal", "ordinal")
                    else None,
                )
            # orient is baked at resolve time (ay.position is concrete, never "auto")

            bake_tick_ladder(y_axis, ay)

            y_scale = emit_resolved_scale_vl(ay.scale)
            if y_type == "quantitative" and "domain" not in y_scale:
                # Exact headroom-applied bounds baked at resolve(); an authored
                # `domain` (mapped above) always wins outright.
                y_scale = apply_domain_headroom_bounds(
                    y_scale, ay.domain_max, ay.domain_min
                )
            y_enc = {
                "field": chart.y,
                "type": y_type,
                "title": xy.y_title,
            }
            if y_axis:
                y_enc["axis"] = y_axis
            if y_scale:
                y_enc["scale"] = y_scale
            # A categorical y (dot plot) honors an authored chart.sort to order
            # its band domain — mirrors the bar categorical-axis path. A numeric y
            # is a continuous scale where sort does not apply.
            if y_type in ("nominal", "ordinal"):
                y_sort = dimension_sort_to_vl(chart.sort)
                if y_sort is not None:
                    y_enc["sort"] = y_sort

            # Tooltip format goes on the measure encoding only: a numeric format on a
            # categorical y coerces its category strings to NaN in the tooltip.
            fmt = chart.style.tooltip_format
            if fmt and y_type == "quantitative":
                y_enc["format"] = fmt

            encoding["y"] = y_enc

        if not chart.wide_measures:
            # The wide branch above already set encoding["color"] from its
            # own fold-aware wide.color -- resolved_channels["color"] there
            # is the same synthetic WIDE_LABEL_FIELD channel, whose humanized
            # legend text and palette order only fold_wide_measures knows how
            # to build (see _wide_scatter_fold's docstring).
            color_ch = chart.resolved_channels.get("color")
            if color_ch is not None:
                color_title = (
                    format_display_text(
                        color_ch.data_field,
                        from_slug=True,
                        font=chart.legend.title.font,
                    )
                    if color_ch.data_field
                    else None
                )
                enc = channel_to_encoding(color_ch, data, title=color_title)
                apply_color_legend(enc, chart.legend)
                # A bound field still owes every value its board slot's
                # color — VL's own alphabetical default range would
                # otherwise paint this chart from its own local position,
                # not the board's (mirrors bar/line's grouped-series path).
                if categorical_color_encoding(color_ch, enc.get("type")):
                    series = distinct_series_values(data, color_ch.data_field)
                    # Only fires when authored -- see the identical
                    # reasoning on the heatmap site: an unconditional pin
                    # here would emit a str()-cast `values` list with no
                    # explicit `scale.domain` to back it (only the
                    # board-wide `category_colors` branch below sets one),
                    # risking a type-mismatched swatch for a non-string
                    # color column.
                    if chart.legend.values is not None:
                        apply_legend_entry_order(
                            enc,
                            series,
                            authored=chart.legend.values,
                        )
                    # Ordinal columns carry their own inherent order --
                    # the paint scale below must never touch it.
                    if enc.get("type") == "nominal" and chart.palette:
                        scale = category_scale_for(
                            chart.category_colors, color_ch.data_field
                        )
                        if scale is not None and series:
                            enc["scale"] = spatial_color_scale(
                                series, chart.palette, series, scale
                            )
                encoding["color"] = enc

        if chart.size:
            size_enc = field_encoding(chart.size, "quantitative")
            size_enc["title"] = format_display_text(
                chart.size, from_slug=True, font=chart.legend.title.font
            )
            # drop_values: chart.legend.values, when authored, is a
            # categorical reorder for the COLOR legend -- this is a
            # quantitative gradient tick ladder, which never resolves
            # against nominal/ordinal entries.
            apply_color_legend(size_enc, chart.legend, drop_values=True)
            encoding["size"] = size_enc

        if chart.shape:
            encoding["shape"] = field_encoding(chart.shape, "nominal")

        # mark_props: base point shape + tooltip + optional single-series fill.
        # When a color encoding is present it drives fill; suppress the default.
        # When no color encoding, use the single_series_fill baked at resolve time.
        mark_props = emit_scatter_layer(
            chart.style.point_mark,
            "color" in encoding,
            chart.style.single_series_fill,
        )

        config: dict[str, Any] = {**build_palette_config(chart.palette)}

        spec = ChartSpec(
            mark="point",
            encoding=encoding,
            layers=[],
            config=config,
            mark_props=mark_props,
            transforms=transforms,
        )
        # When x canonicalization fired, stamp transformed rows onto the spec
        # so the session does not overwrite with raw query data.
        if x_transformed:
            spec.data = normalize_data_types(data)

        if chart.layers:
            from dbt_charts.core.render.chart.emitters._overlay import (
                render_cartesian_overlay,
            )

            spec = render_cartesian_overlay(
                spec,
                chart.layers,
                data,
                chart_id=chart.id,
                axis_x=chart.style.axis_x,
                axis_y=chart.style.axis_y,
                base_measure_title_suppressed=(
                    bool(chart.y_label) and chart.style.axis_y.title.visible is False
                ),
                base_orientation="vertical",
                # Scatter's own base (above) canonicalizes x unconditionally,
                # regardless of an authored axis_x.type: temporal — it never
                # consults gap_fill_ordinal_time's escape hatch the way
                # bar/line/area do, so an own-query overlay layer here must
                # never skip either.
                base_x_authored_temporal=False,
                tooltip_format=chart.style.tooltip_format,
                canvas=chart.canvas,
                single_series_fill=chart.style.single_series_fill,
                legend=chart.legend,
                config=config,
                # The endpoint-label rail never applies to scatter as a base
                # chart (EndpointLabelFeature.applies_to() only covers
                # line/area/bar) — a scatter base has no rail to be
                # load-bearing for.
                layered_rail_may_fire=False,
                base_query_name=chart.query_name,
                base_mark_type="scatter",
                base_label=xy.y_plain,
                datasets=datasets,
                base_stack_normalize=False,
                base_stack_center=False,
                multiples_scale_independent=multiples_scale_independent(chart),
            )

        return spec
