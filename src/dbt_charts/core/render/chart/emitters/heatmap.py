"""Heatmap chart emitter for render-v2."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Literal

from dbt_charts.core.compile.models.chart.resolved.heatmap import ResolvedHeatmapChart
from dbt_charts.core.compile.models.style.theme.category_colors import (
    category_scale_for,
)
from dbt_charts.core.compile.resolve.chart._chart_rows import ChartDataset
from dbt_charts.core.diagnostics.chart_data import ChartDataError
from dbt_charts.core.diagnostics.codes_render import (
    ERR_LABEL_FORMAT_AXIS_MISMATCH,
)
from dbt_charts.core.render.chart._types import VLDict
from dbt_charts.core.render.chart.artifacts import ChartRenderData
from dbt_charts.core.render.chart.emitters._cartesian import (
    canonicalize_cartesian_x_data,
    dimension_sort_to_vl,
    distinct_series_values,
    pin_sorted_domain,
    resolve_xy_titles,
    spatial_color_scale,
)
from dbt_charts.core.render.chart.emitters._channels import (
    apply_color_legend,
    apply_gradient_legend_endpoint_labels,
    apply_legend_entry_order,
    categorical_color_encoding,
    channel_to_encoding,
    gradient_scale_to_vl,
)
from dbt_charts.core.render.chart.emitters._label_overlap import resolve_axis_x_overlap
from dbt_charts.core.render.chart.spec import ChartSpec, RenderBox
from dbt_charts.core.render.chart.type_inference import (
    HEATMAP_FORMAT_REMEDY,
    apply_x_tick_cadence,
    build_cartesian_x_encoding,
    gate_label_format,
)
from dbt_charts.core.render.chart.vl_field_maps import (
    axis_to_vl,
    measure_axis_to_vl,
    rect_mark_to_vl,
)
from dbt_charts.core.render.utils import normalize_data_types, slug_to_text
from dbt_charts.core.text.case import format_display_text


def _write_dimension_sort(
    enc: VLDict,
    chart: ResolvedHeatmapChart,
    data: ChartRenderData,
    axis: Literal["x", "y"],
) -> None:
    """Write a heatmap dimension encoding's ``sort``, in place.

    Unauthored: an explicit ``"sort": None`` keeps Vega-Lite's domain in query
    row order — the same default ``bar``/``line``/``area`` pin, rather than
    the alphabetical order VL falls back to for a nominal field with no
    ``sort`` key at all. Authored: maps ``chart.sort`` to a field-based VL
    sort and pins the resulting category order as an explicit domain.

    A heatmap's authored ``sort`` has no way to name "the x axis" or "the y
    axis" specifically, so the same sort reaches every dimension identically.
    """
    vl_sort = dimension_sort_to_vl(chart.sort)
    if vl_sort is None:
        enc["sort"] = None
        return
    enc["sort"] = vl_sort
    pin_sorted_domain(enc, data, chart, axis=axis)


@dataclass
class HeatmapEmitter:
    def emit(
        self,
        chart: ResolvedHeatmapChart,
        box: RenderBox,
        dataset: ChartDataset,
    ) -> ChartSpec:
        data = dataset.all_rows()
        ax = chart.style.axis_x
        ay = chart.style.axis_y
        color_ch = chart.resolved_channels.get("color")
        config: dict[str, Any] = {}
        if chart.palette:
            config["range"] = {"category": list(chart.palette)}
        # Colorless single-measure rect fill ("solid fill from theme" — no color
        # channel authored, no multi-measure color-by-datum): bake the chart's own
        # effective palette[0] here so it agrees with range.category by
        # construction — no post-assembly re-patch. A color channel or multi-measure
        # layering always supplies its own encoding.color, which VL prefers over
        # this config default anyway.
        if chart.palette and color_ch is None and not isinstance(chart.y, list):
            config["rect"] = {"fill": chart.palette[0]}

        # Multi-measure: list[str] y → layered spec, one rect layer per measure.
        # Heatmap has no orientation knob (ResolvedHeatmapChart inherits _CartesianResolvedChartFields
        # but HeatmapChart authored model has no orientation field) — axes are always fixed:
        # x=categorical, y=categorical, color=quantitative value.
        if isinstance(chart.y, list):
            top_encoding: dict[str, Any] = {}
            if chart.x:
                top_encoding["x"] = {
                    "field": chart.x,
                    "type": "nominal",
                }
                _write_dimension_sort(top_encoding["x"], chart, data, axis="x")
                # No axis dict on this branch, so nothing can merge — but the
                # band still draws category labels, and the single-measure
                # path below gates the same authored fields. Validate-only,
                # so a bare ticks.step raises here too instead of vanishing.
                apply_x_tick_cadence(None, ax, chart.x, "nominal")
                gate_label_format(
                    ax.labels.format,
                    chart.x,
                    data,
                    "nominal",
                    setting="axis_x.labels.format",
                    remedy=HEATMAP_FORMAT_REMEDY,
                )
            # y takes no gate_label_format call: that gate asks whether the
            # ticks can carry the format, and here nothing can — this branch
            # builds per-measure layer encodings with no `axis` dict and puts
            # no y on top_encoding, so an authored axis_y format never reaches
            # Vega on any shape. Refuse whenever one is authored, rather than
            # weighing evidence that cannot change the answer; a numeric-tick
            # exemption here would re-open the silent drop for exactly the
            # commonest wide shape (y: ["2023", "2024"], whose names read as
            # numbers).
            if ay.labels.format is not None:
                raise ChartDataError.from_code(
                    ERR_LABEL_FORMAT_AXIS_MISMATCH,
                    setting="axis_y.labels.format",
                    field=chart.y,
                    fmt=ay.labels.format,
                    remedy=(
                        "A multi-measure heatmap draws no y axis of its own — "
                        "each measure becomes a color-coded layer — so no "
                        "label format can reach it. " + HEATMAP_FORMAT_REMEDY
                    ),
                )
            layers: list[ChartSpec] = []
            for y_field in chart.y:
                layer_enc: dict[str, Any] = {
                    "y": {
                        "field": y_field,
                        "type": "nominal",
                    },
                    "color": {"datum": slug_to_text(y_field)},
                }
                # Layers share one y-position scale (no `resolve.scale.y` is
                # ever set), so every layer needs the same sort. Where two
                # layers pin conflicting orders for the same values, VL draws
                # layer 0's order.
                _write_dimension_sort(layer_enc["y"], chart, data, axis="y")
                layers.append(ChartSpec(mark="rect", encoding=layer_enc))
            return ChartSpec(
                mark="layered", encoding=top_encoding, layers=layers, config=config
            )

        encoding: dict[str, Any] = {}
        # chart.y is narrowed to str | None here: the list[str] case returned above.
        xy = resolve_xy_titles(
            chart.x, chart.y, chart.x_label, chart.y_label, ax, ay, box, chart.id
        )

        x_transformed = False
        if chart.x:
            # Labeled bucket strings ("Q1 2024") and non-date-only temporal
            # values (datetime.datetime) reach build_cartesian_x_encoding's
            # axis.values/labelExpr unparseable by Vega's JS Date otherwise —
            # bar/line/area canonicalize their x rows before this same call;
            # heatmap has no gap-fill scaffold of its own, so it needs the
            # same two-step precondition directly. No-ops for non-date data.
            data, x_transformed = canonicalize_cartesian_x_data(
                data, chart.x, ax.time_unit
            )
            # Walk the strategy list; directive is render-local (not on the model).
            # Returns (axis, None) when overlap is None (cascade not populated).
            label_layout = resolve_axis_x_overlap(
                ax,
                chart.x,
                data,
                chart.style.label_usable_ratio,
                edge_labels_flushed=False,
                chart_width=box.width,
            )
            x_axis_base = axis_to_vl(
                ax,
                label_overlap=label_layout.label_overlap,
                label_angle=label_layout.angle,
            )
            # Heatmap x is a grid dimension: always a nominal band scale, even for
            # numeric fields (e.g. hour_of_day 0..23) or date-like ones. Promoting
            # it to ordinal/temporal collapses each rect cell to zero width
            # (matches oracle _map_rect) — resolve_cartesian_x_type's own
            # mark_type == "heatmap" branch pins the type while still getting
            # bar's bucketed-calendar labelExpr/tick-thinning for date-like x
            # data (Jan/2024 vocabulary, not a raw ISO string doing double duty
            # as sort key and label).
            x_type, x_axis, _detected_tu = build_cartesian_x_encoding(
                data,
                chart.x,
                ax,
                x_axis_base,
                "heatmap",
                format_time_unit=label_layout.format_time_unit,
                visibility_time_unit=label_layout.visibility_time_unit,
                label_anchor_index=label_layout.anchor_index,
                panel_fields=(),  # heatmap returns above the gate
            )
            x_enc: dict[str, Any] = {
                "field": chart.x,
                "type": x_type,
                "title": xy.x_title,
            }
            _write_dimension_sort(x_enc, chart, data, axis="x")
            if x_axis:
                x_enc["axis"] = x_axis
            encoding["x"] = x_enc

        # chart.y is narrowed to str here: the list[str] case returned above.
        if chart.y:
            # Heatmap y is a grid dimension: always nominal (see x_type above)
            # — never a numeric domain to estimate a gutter from, so ().
            y_type = "nominal"
            # A heatmap has no measure axis, so a currency preset here used
            # to paint $NaN row labels with no diagnostic — same gate x
            # already had, now also on y.
            gate_label_format(
                ay.labels.format,
                chart.y,
                data,
                y_type,
                setting="axis_y.labels.format",
                remedy=HEATMAP_FORMAT_REMEDY,
            )
            # Row labels are the literal per-row values (no d3 format applies
            # to a nominal field), so an own-side align (resolved from
            # axis_y.labels.align: inward, or an authored left/right) is
            # measured directly against them — same exact-content case
            # bar.py's categorical own-side gate uses. Only worth the
            # O(rows) dedup pass when align is actually set — otherwise
            # measure_axis_to_vl's own-side gate can never fire and the
            # labels would go unused.
            y_category_labels = (
                tuple(
                    dict.fromkeys(
                        str(row[chart.y])
                        for row in data
                        if row.get(chart.y) is not None
                    )
                )
                if ay.labels.align is not None
                else ()
            )
            y_axis = measure_axis_to_vl(ay, data, (), category_labels=y_category_labels)
            y_enc: dict[str, Any] = {
                "field": chart.y,
                "type": y_type,
                "title": xy.y_title,
            }
            _write_dimension_sort(y_enc, chart, data, axis="y")
            if y_axis:
                y_enc["axis"] = y_axis
            encoding["y"] = y_enc

        if color_ch is not None:
            enc = channel_to_encoding(color_ch, data)
            # Add title for aria-label quality.
            if enc.get("field"):
                enc["title"] = format_display_text(
                    enc["field"],
                    from_slug=True,
                    font=chart.legend.title.font,
                )
            # Heatmap's color channel is the whole measure, so it needs a key
            # like every other family — wire the resolved legend style through
            # like bar/line/scatter/pie do. VL renders its native continuous
            # gradient legend for a quantitative field with no extra code.
            apply_color_legend(enc, chart.legend)
            # A genuinely categorical color field (string values, e.g. a
            # heatmap using `color:` to name which category occupies each
            # cell rather than a quantitative measure) is board-bound like
            # any other chart's color channel — repro: a heatmap ordered
            # `category DESC` plus a bar, both `color: category`, used to
            # come out with SWAPPED colors between the two charts because
            # this branch never called `category_scale_for`. Gated on
            # `enc["type"] in ("nominal", "ordinal")`, mutually
            # exclusive with the quantitative gradient branch below.
            if categorical_color_encoding(color_ch, enc.get("type")):
                series = distinct_series_values(data, enc["field"])
                # Only fires when authored: with no `chart.palette` /
                # `category_scale_for` scale below, `enc["scale"]` is
                # never set, so Vega infers the domain itself from the
                # RAW row values -- an unconditional pin here would emit
                # a str()-cast `values` list that misses that inferred
                # domain whenever the color column isn't already a
                # string (e.g. numeric categories), labeling a swatch
                # with ink that isn't its own.
                if chart.legend.values is not None:
                    apply_legend_entry_order(
                        enc,
                        series,
                        authored=chart.legend.values,
                    )
                # Ordinal columns carry their own inherent order --
                # the paint scale below must never touch it.
                if enc.get("type") == "nominal" and chart.palette:
                    scale = category_scale_for(chart.category_colors, enc["field"])
                    if scale is not None and series:
                        enc["scale"] = spatial_color_scale(
                            series, chart.palette, series, scale
                        )
            # color_gradient is the cascade-complete gradient (theme/board
            # defaults merged with any chart-local style.color.gradient
            # override) — strictly more complete than a "gradient" mode
            # channel's own ch.scale, which is built from the chart-local
            # gradient dict alone and can be missing a domain the cascade
            # supplies (e.g. board-level min/max). Always prefer it over
            # whatever channel_to_encoding already set.
            # Gated on `enc["type"] == "quantitative"`, not just
            # `_cg is not None`: a color column VL is rendering
            # ordinal/nominal (a "series"-mode channel over non-numeric
            # data, e.g.) must get neither a data-derived domain nor
            # endpoint labels — a numeric domain baked onto a scale VL
            # treats as nominal leaves every rect with no matching fill
            # (Vega can't match string categories against a numeric
            # domain). `_numeric_extent` and `infer_vega_type_from_data`
            # now share one numeric-value rule (`is_vega_numeric_value`),
            # so they can't disagree on whether a *value* is numeric —
            # but `infer_vega_type_from_data` only samples the first 10
            # rows while the domain scan below covers every row, so this
            # explicit type check is real defense-in-depth against that
            # residual sampling gap, not just belt-and-suspenders.
            _cg = chart.style.color_gradient
            if _cg is not None and enc.get("type") == "quantitative":
                enc["scale"] = gradient_scale_to_vl(_cg, data, enc.get("field"))
                if enc.get("field"):
                    apply_gradient_legend_endpoint_labels(enc, _cg, data, enc["field"])
            # Tooltip format on color encoding for quantitative heatmap values.
            if enc.get("type") == "quantitative":
                fmt = chart.style.tooltip_format
                if fmt:
                    enc.setdefault("format", fmt)
            encoding["color"] = enc
        # mark_props: rect mark props + tooltip (no fill — color encoding owns it).
        mark_props: dict[str, Any] = {"tooltip": True}
        mark_props.update(rect_mark_to_vl(chart.style.rect_mark))

        spec = ChartSpec(
            mark="rect",
            encoding=encoding,
            config=config,
            mark_props=mark_props,
        )
        # When x canonicalization fired, stamp transformed rows onto the spec
        # so the session does not overwrite with raw query data.
        if x_transformed:
            spec.data = normalize_data_types(data)
        return spec
