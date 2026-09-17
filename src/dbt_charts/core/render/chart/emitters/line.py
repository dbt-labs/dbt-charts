"""Line chart emitter for render-v2."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from dbt_charts.core.compile.models.chart.resolved.line import ResolvedLineChart
from dbt_charts.core.compile.models.style.resolved._marks import ResolvedLineMarkStyle
from dbt_charts.core.compile.models.style.resolved.line import ResolvedLineStyle
from dbt_charts.core.compile.models.style.theme.category_colors import (
    category_scale_for,
    color_at,
)
from dbt_charts.core.compile.resolve.chart._chart_rows import ChartDataset, restripe
from dbt_charts.core.compile.resolve.chart._wide_fields import (
    WIDE_LABEL_FIELD,
    WIDE_VALUE_FIELD,
    humanize_wide_series_name,
    raw_wide_series_names,
    unfold_wide_rows,
    wide_measure_labels_for,
    wide_series_names,
)
from dbt_charts.core.diagnostics.chart_data import ChartDataError
from dbt_charts.core.render.chart._types import VLDict
from dbt_charts.core.render.chart.emitters._cartesian import (
    CartesianXResolution,
    build_cartesian_y_encoding,
    build_palette_config,
    build_x_enc,
    dimension_sort_to_vl,
    distinct_series_values,
    multiples_scale_independent,
    pin_sorted_domain,
    resolve_cartesian_x,
    resolve_xy_titles,
    sorted_series_by_last_value,
    spatial_color_scale,
    wide_measures_title,
)
from dbt_charts.core.render.chart.emitters._channels import (
    apply_color_legend,
    apply_legend_entry_order,
    categorical_color_encoding,
    channel_to_encoding,
    gap_fill_ordinal_time_per_panel,
)
from dbt_charts.core.render.chart.emitters._endpoint_rail import (
    resolve_endpoint_rail_span,
)
from dbt_charts.core.render.chart.emitters._layers import emit_line_layer
from dbt_charts.core.render.chart.emitters._overlay import (
    overlay_uses_band_step,
    overlay_x_domain_values,
    render_cartesian_overlay,
)
from dbt_charts.core.render.chart.emitters._wide import (
    fold_wide_measures,
)
from dbt_charts.core.render.chart.spec import ChartSpec, RenderBox
from dbt_charts.core.render.chart.step_band import (
    BAND_STEP_CURVE,
    apply_step_band,
    is_band_step,
)
from dbt_charts.core.render.chart.time_unit_detect import normalize_labeled_temporal
from dbt_charts.core.render.chart.type_inference import infer_vega_type_from_data
from dbt_charts.core.render.chart.validation import (
    validate_color_series,
    validate_preaggregated_data_per_panel,
)
from dbt_charts.core.render.chart.vl_field_maps import (
    compose_axis_label_expr,
    measure_axis_to_vl,
)
from dbt_charts.core.render.utils import normalize_data_types
from dbt_charts.core.text.case import format_display_text
from dbt_charts.core.utils import (
    layered_endpoint_rail_fires,
    layered_endpoint_rail_shape,
)


def _normalize_line_data(
    chart: ResolvedLineChart,
    dataset: ChartDataset,
    data: list[dict[str, Any]],
) -> tuple[list[dict[str, Any]], bool, bool]:
    """Apply labeled-temporal normalization and ordinal-time gap-fill.

    Returns (data, transformed, x_authored_temporal). ``transformed`` True
    means the caller must stamp the returned rows onto the spec to prevent
    the session from overwriting with raw data — this must fire for
    labeled-temporal normalization alone (e.g. "Q1 2020" rewritten to an ISO
    bucket) even when gap-fill itself is skipped, or the session re-stamps
    unparsed labels onto a temporal encoding. ``x_authored_temporal`` is
    gap_fill_ordinal_time's own verdict, threaded to the caller's
    render_cartesian_overlay call instead of re-derived there.

    ``dataset`` is ``data`` (``dataset.all_rows()``) still in panel-shaped
    form — passed to ``gap_fill_ordinal_time_per_panel`` via ``restripe()``
    when normalization mutated ``data``, since ``chart.x`` can itself be the
    multiples field and re-deriving panels from the mutated value would
    fail to match the pre-mutation baked axis.
    """
    transformed = False
    mutated_dataset = dataset
    if chart.x:
        normalized = normalize_labeled_temporal(data, chart.x)
        if normalized is not data:
            data = normalized
            transformed = True
            mutated_dataset = restripe(dataset, data)
    bucketed, x_authored_temporal = gap_fill_ordinal_time_per_panel(
        chart.style.axis_x,
        chart.x,
        chart.color,
        mutated_dataset,
        "line",
        chart.style.line_mark.curve == BAND_STEP_CURVE,
        True,
    )
    if bucketed is not None:
        data = bucketed
        transformed = True
    return data, transformed, x_authored_temporal


def _apply_line_color_encoding(
    chart: ResolvedLineChart,
    data: list[dict[str, Any]],
    style: ResolvedLineStyle,
    top_encoding: VLDict,
) -> bool:
    """Apply color and strokeDash encodings to top_encoding in-place.

    Returns has_color_encoding so sub-layer assembly can suppress the static
    stroke for multi-series lines (parent encoding.color drives color instead).
    """
    color_ch = chart.resolved_channels.get("color")
    has_color_encoding = False
    # This chart's board scale for its own color field, when one exists —
    # None for an unbound field, in which case spatial_color_scale below
    # falls back to its own alphabetical-by-palette-position default.
    # Computed once and reused by both the plain (last-value order) branch
    # below and the dashes branch further down: the two domain-locking
    # mechanisms aren't meant to compose, but they DO share the same board
    # slot for a given value, so this is deliberately not re-derived per
    # branch.
    scale = None
    if color_ch is not None:
        color_field = color_ch.data_field
        color_title = (
            format_display_text(
                color_field, from_slug=True, font=chart.legend.title.font
            )
            if color_field
            else None
        )
        enc = channel_to_encoding(color_ch, data, title=color_title)
        apply_color_legend(enc, chart.legend)
        enc_type = enc.get("type")
        is_bindable_series = (
            categorical_color_encoding(color_ch, enc_type) and enc_type == "nominal"
        )
        if is_bindable_series:
            scale = category_scale_for(chart.category_colors, color_field)
        # Stable last-value order when possible -- skipped when
        # style.dashes is set, since that path pins color's domain to a
        # DIFFERENT explicit order (data-insertion) to merge the
        # color+strokeDash legends; the two domain-locking mechanisms
        # aren't meant to compose (resolution still runs there, against
        # `dash_domain`, further down). Without `chart.x`/a scalar
        # `chart.y` the last-value order can't be computed either, so
        # `series` itself (plain alphabetical) is the fallback order --
        # still the correct domain to resolve an AUTHORED
        # `legend.values` against, just not the display-order
        # refinement.
        if is_bindable_series and not style.dashes:
            series = distinct_series_values(data, color_field)
            if series:
                order = (
                    sorted_series_by_last_value(
                        series, data, chart.x, chart.y, color_field
                    )
                    if chart.x and isinstance(chart.y, str)
                    else series
                )
                # `chart.palette` can legally be authored empty; it
                # governs the scale's range only, so resolution still
                # runs. But with nothing authored and no palette to
                # paint a scale from, there is nothing worth pinning.
                if chart.palette:
                    enc["scale"] = spatial_color_scale(
                        series, chart.palette, order, scale
                    )
                if chart.legend.values is not None or chart.palette:
                    apply_legend_entry_order(
                        enc,
                        order,
                        authored=chart.legend.values,
                    )
        elif (
            categorical_color_encoding(color_ch, enc_type)
            and enc_type == "ordinal"
            and not style.dashes
            and chart.legend.values is not None
        ):
            # An ordinal column carries its own inherent order --
            # Vega sorts it natively, so the last-value order, paint
            # scale and board-slot lookup above must never touch it.
            # Resolution still has to run so an authored
            # `legend.values` entry doesn't ship as a phantom swatch.
            apply_legend_entry_order(
                enc,
                distinct_series_values(data, color_field),
                authored=chart.legend.values,
            )
        top_encoding["color"] = enc
        has_color_encoding = True
    if (
        style.dashes
        and color_ch is not None
        and color_ch.mode == "series"
        and has_color_encoding
    ):
        dash_field = color_ch.data_field
        # A caller (e.g. endpoint labels) may have already suppressed color's
        # legend — strokeDash must follow suit, or VL renders it as an
        # independent, un-suppressed legend of its own (color.legend: null
        # does not apply to a sibling encoding channel). An absent "legend" key
        # is safe to treat as "visible" here only because apply_color_legend
        # (_channels.py) always sets the key explicitly — None when suppressed,
        # a populated dict otherwise — so an absent key never happens for a
        # resolved color encoding built by this function.
        color_legend_suppressed = top_encoding["color"].get("legend") is None
        if not color_legend_suppressed:
            # symbolType="stroke" renders line samples in the color legend so
            # dash patterns are visible (not dashed circle outlines). symbolSize sets
            # the sample length long enough for a full dash cycle to read — a short
            # swatch clips the pattern to a solid stub (mirrors V1's dash legend).
            color_legend = top_encoding["color"].setdefault("legend", {})
            if isinstance(color_legend, dict):
                color_legend["symbolType"] = "stroke"
                color_legend["symbolSize"] = 2000  # long enough for a full dash cycle
        # Pin both scales to the same explicit domain (distinct series values in
        # data order). Without it the baseline zero rule's synthetic rows — which
        # carry no series field — add an "undefined" member to the inferred
        # strokeDash domain (a spurious legend entry), and the color/strokeDash
        # scales differ enough that VL emits two legends instead of one merged
        # dash legend. Identical domains collapse them to a single legend whose
        # symbols carry the per-series dash pattern.
        dash_domain = _distinct_in_order(data, dash_field) if dash_field else []
        # Only fires when authored AND the color field is genuinely
        # categorical (matching pie/heatmap/scatter/geo/bar's own gates):
        # an unconditional pin here would emit a str()-cast `values` list
        # while `color_scale["domain"]`/`strokeDash.scale.domain` below
        # keep the RAW (bool/int/date) values -- a type-mismatched legend
        # that renders every entry as NaN through vl_convert, on a board
        # that authored nothing.
        if (
            dash_domain
            and chart.legend.values is not None
            and top_encoding["color"].get("type") in ("nominal", "ordinal")
        ):
            # resolve_legend_entries folds tokens as strings; dash_field's
            # raw values (int/date/etc.) never reach it -- str() them for
            # resolution only. infer_vega_type_from_data samples only 10
            # rows while _distinct_in_order (dash_domain) scans all of
            # them, so the nominal/ordinal gate above does not guarantee
            # every dash_domain entry is already a str -- map the
            # resolved (string) entries back to their raw dash_domain
            # element afterward, so `legend["values"]` and
            # `color_scale["domain"]` below agree on type, not just text.
            str_to_raw = {str(value): value for value in dash_domain}
            apply_legend_entry_order(
                top_encoding["color"],
                [str(value) for value in dash_domain],
                authored=chart.legend.values,
            )
            color_legend = top_encoding["color"].get("legend")
            if isinstance(color_legend, dict) and isinstance(
                color_legend.get("values"), list
            ):
                # Every entry in color_legend["values"] here is either
                # a matched (resolved) domain entry or a member of the
                # str-cast dash_domain fallback order -- both are, by
                # construction, keys of str_to_raw. A direct lookup
                # raises loudly if that invariant is ever wrong, rather
                # than silently keeping a str the domain never carries.
                color_legend["values"] = [str_to_raw[v] for v in color_legend["values"]]
        color_scale = top_encoding["color"].setdefault("scale", {})
        if isinstance(color_scale, dict) and dash_domain:
            color_scale["domain"] = dash_domain
            # The two domain-locking mechanisms don't compose (see the
            # comment above), but a board scale still owes this chart's
            # values their slot's color — without an explicit `range` here,
            # VL painted `dash_domain` positionally over the config-level
            # palette, which desynced from a plain (non-dashed) sibling
            # chart bound to the same field. `chart.palette` can legally
            # be authored empty; color_at raises on an empty palette.
            if scale is not None and chart.palette:
                color_scale["range"] = [
                    color_at(scale, v, chart.palette) for v in dash_domain
                ]
        # strokeDash must share color's title for VL to merge the two legends.
        color_title = top_encoding["color"].get("title")
        strokedash_scale: dict[str, Any] = {"range": style.dashes}
        if dash_domain:
            strokedash_scale["domain"] = dash_domain
        top_encoding["strokeDash"] = {
            "field": dash_field,
            "type": infer_vega_type_from_data(data, dash_field),
            "scale": strokedash_scale,
            "title": color_title,
        }
        if color_legend_suppressed:
            top_encoding["strokeDash"]["legend"] = None
    return has_color_encoding


def _distinct_in_order(data: list[dict[str, Any]], field: str) -> list[Any]:
    """Distinct non-null values of ``field`` in first-seen row order."""
    seen: list[Any] = []
    for row in data:
        value = row.get(field)
        if value is not None and value not in seen:
            seen.append(value)
    return seen


def _apply_line_step_band(
    chart: ResolvedLineChart,
    data: list[dict[str, Any]],
    top_encoding: VLDict,
    line_mark: ResolvedLineMarkStyle,
    x_type: str | None,
) -> list[dict[str, Any]] | None:
    """Apply band-aware step row doubling when curve is step on a band x-axis."""
    if is_band_step(line_mark.curve, x_type):
        return apply_step_band(
            data,
            top_encoding,
            chart_id=chart.id,
            connect=line_mark.connect is not False,
        )
    return None


def _build_line_top_encoding(
    chart: ResolvedLineChart,
    data: list[dict[str, Any]],
    style: ResolvedLineStyle,
    box: RenderBox,
    x_domain: list[Any] | None,  # type-state: explicit_any — raw x values
    panel_fields: tuple[str, ...],
) -> tuple[VLDict, bool, str | None, str | None, float | None]:
    """Build the VL encoding dict, has_color_enc flag, resolved x VL type
    (None when the chart has no x channel at all), plain y label, and the
    x-label block height at the tilt just resolved, for a line chart.

    Tooltip *content* is a separate concern, built after emission by
    ``features/structured_tooltip.py`` from the chart-axes LUT.
    """
    ax, ay = style.axis_x, style.axis_y
    x_res = (
        resolve_cartesian_x(
            chart.x,
            data,
            ax,
            style.label_usable_ratio,
            box.width,
            chart.id,
            "line",
            style.line_mark.curve,
            overlay_uses_band_step(chart.layers),
            x_domain,
            reserved_width=resolve_endpoint_rail_span(chart, data, box.width),
            panel_fields=panel_fields,
        )
        if chart.x
        else CartesianXResolution("nominal", {}, {})
    )
    vl_type, ax_vl, x_scale = x_res.vl_type, x_res.axis, x_res.scale
    titles = resolve_xy_titles(
        chart.x,
        chart.y if isinstance(chart.y, str) else None,
        chart.x_label,
        chart.y_label,
        ax,
        ay,
        box,
        chart.id,
    )
    x_title, y_title = titles.x_title, titles.y_title
    y_field = chart.y if isinstance(chart.y, str) else None
    ay_vl = measure_axis_to_vl(ay, data, (y_field,) if y_field else ())
    ay_vl = compose_axis_label_expr(ay_vl, ay.ruler, ay)
    y_enc = build_cartesian_y_encoding(
        chart.y, ay, ay_vl, y_title, style.tooltip_format
    )
    top_encoding: VLDict = {}
    if chart.x:
        top_encoding["x"] = build_x_enc(
            chart.x,
            vl_type,
            x_title,
            ax_vl,
            x_scale,
            x_res.time_unit,
            sort=dimension_sort_to_vl(chart.sort),
        )
        pin_sorted_domain(top_encoding["x"], data, chart, axis="x")
    if chart.y:
        top_encoding["y"] = y_enc
    has_color_enc = _apply_line_color_encoding(chart, data, style, top_encoding)
    x_type = vl_type if chart.x else None
    return top_encoding, has_color_enc, x_type, titles.y_plain, x_res.label_block_height


def _emit_folded_line(
    chart: ResolvedLineChart,
    data: list[dict[str, Any]],
    box: RenderBox,
    panel_fields: tuple[str, ...],
) -> ChartSpec:
    """Emit a fold-based unit spec for a multi-metric (y: [a, b, ...]) line chart.

    Uses VL fold transform so all measures share one mark spec with a color
    encoding -- legend visibility, endpoint labels, and palette all wire up
    through the ordinary series-color path.
    """
    assert chart.wide_measures
    style = chart.style
    line_mark = style.line_mark
    ax, ay = style.axis_x, style.axis_y
    x_res = (
        resolve_cartesian_x(
            chart.x,
            data,
            ax,
            style.label_usable_ratio,
            box.width,
            chart.id,
            "line",
            reserved_width=resolve_endpoint_rail_span(chart, data, box.width),
            panel_fields=panel_fields,
        )
        if chart.x
        else CartesianXResolution("nominal", {}, {})
    )
    if is_band_step(line_mark.curve, x_res.vl_type if chart.x else None):
        raise ChartDataError(
            f"Line chart '{chart.id}': band-aware step curve is not supported "
            "for multi-metric (y: [...]) charts -- use a single y field or "
            "choose a different curve style."
        )
    measures = list(chart.wide_measures)
    dimension = chart.color
    wide_labels = wide_measure_labels_for(chart.wide_measures)
    series = wide_series_names(measures, dimension, data, wide_labels)
    # Same order computation line's authored-color path uses
    # (sorted_series_by_last_value in _apply_line_color_encoding, above), fed
    # a long-form view of the wide data — a wide line's series order must
    # match what an authored color: field of the same data would produce.
    # `folded`'s own WIDE_LABEL_FIELD is RAW (unfold_wide_rows never
    # humanizes), so last-value ordering has to run against the matching
    # raw identity, not `series` (already humanized) -- humanize the
    # RESULT afterward, per entry.
    raw_series = raw_wide_series_names(measures, dimension, data)
    display_order = series
    raw_fold_order = raw_series
    if chart.x:
        folded = unfold_wide_rows(data, measures, dimension)
        raw_display_order = sorted_series_by_last_value(
            raw_series, folded, chart.x, WIDE_VALUE_FIELD, WIDE_LABEL_FIELD
        )
        display_order = [
            humanize_wide_series_name(name, dimension, wide_labels)
            for name in raw_display_order
        ]
        raw_fold_order = raw_display_order
    wide = fold_wide_measures(
        measures,
        chart.color,
        data,
        chart.palette,
        chart.legend,
        display_order=display_order,
        wide_measure_labels=wide_labels,
        # Line has no stack/order-channel mechanism, so the fold order is
        # always the paint order -- same as display_order, but stays RAW:
        # feeds _measure_paint_order/_label_expression, which build Vega
        # expressions comparing against the fold's own RAW key values.
        fold_order=raw_fold_order,
    )
    ay_vl = measure_axis_to_vl(ay, data, chart.wide_measures)
    ay_vl = compose_axis_label_expr(ay_vl, ay.ruler, ay)
    # Wide/folded y has no single measure field to title from — fall back to
    # the joined, humanized measure names, same as an authored y_label always
    # would (an authored y_label still wins outright).
    y_label_effective = chart.y_label or wide_measures_title(chart.wide_measures)
    y_title = resolve_xy_titles(
        None, None, None, y_label_effective, ax, ay, box, chart.id
    ).y_title
    y_enc = build_cartesian_y_encoding(
        wide.value_field, ay, ay_vl, y_title, style.tooltip_format
    )
    top_encoding: VLDict = {}
    if chart.x:
        x_title = resolve_xy_titles(
            chart.x, None, chart.x_label, None, ax, ay, box, chart.id
        ).x_title
        top_encoding["x"] = build_x_enc(
            chart.x,
            x_res.vl_type,
            x_title,
            x_res.axis,
            x_res.scale,
            x_res.time_unit,
            sort=dimension_sort_to_vl(chart.sort),
        )
        pin_sorted_domain(top_encoding["x"], data, chart, axis="x")
    top_encoding["y"] = y_enc
    top_encoding["color"] = wide.color
    sub_layers = emit_line_layer(
        style.line_mark,
        style.point_mark,
        chart.background,
        style.single_series_fill,
        True,
        wide.color,
        [],
        pin_child_colors=True,
    )
    return ChartSpec(
        mark="layered",
        encoding=top_encoding,
        layers=sub_layers,
        config=build_palette_config(chart.palette),
        transforms=wide.transforms,
        x_label_block_height=x_res.label_block_height,
    )


@dataclass
class LineEmitter:
    def emit(
        self,
        chart: ResolvedLineChart,
        box: RenderBox,
        dataset: ChartDataset,
        datasets: dict[str | None, list[dict[str, Any]]] | None = None,
    ) -> ChartSpec:
        data = dataset.all_rows()
        validate_preaggregated_data_per_panel(chart, dataset)
        validate_color_series(chart, data)
        # Normalize labeled temporal strings and fill ordinal-time gaps before
        # any path — multi-metric needs this as much as single-series.
        data, transformed, base_x_authored_temporal = _normalize_line_data(
            chart, dataset, data
        )
        panel_fields = tuple(axis.field for axis in dataset.axes)
        if chart.wide_measures:
            # A folded (wide-measures) chart returns before chart.layers ever
            # applies (below) — no union to compute here.
            spec = _emit_folded_line(chart, data, box, panel_fields)
            if transformed:
                spec.data = normalize_data_types(data)
            return spec
        style = chart.style
        # Overlay layers may carry x buckets the base series doesn't (a
        # forward goal ramp against actuals). Vega-Lite unions the sub-layer
        # domains, so the axis must be built against that union — both its
        # tick values and its crowding measurement — not against the base's
        # own rows. Mirrors bar.py's identical computation ahead of its own
        # x encoding.
        x_domain = (
            overlay_x_domain_values(
                chart.layers,
                data,
                chart.x,
                style.axis_x,
                base_x_authored_temporal,
                datasets,
                chart.query_name,
            )
            if chart.layers
            else None
        )
        top_encoding, has_color_enc, x_type, y_plain, x_label_block = (
            _build_line_top_encoding(chart, data, style, box, x_domain, panel_fields)
        )
        step_band_data = _apply_line_step_band(
            chart, data, top_encoding, style.line_mark, x_type
        )
        # No per-layer tooltip array: structured tooltip content (base's own,
        # and -- when chart.layers is set -- the base's role content wired
        # onto this same spec) is set after emission by StructuredTooltipFeature
        # via VL's description channel. See features/structured_tooltip.py.
        sub_layers = emit_line_layer(
            style.line_mark,
            style.point_mark,
            chart.background,
            style.single_series_fill,
            has_color_enc,
            top_encoding["color"] if has_color_enc else {},
            [],
            band_step=step_band_data is not None,
            pin_child_colors=has_color_enc or bool(chart.layers),
            inherit_parent_color=bool(chart.layers),
        )
        spec = ChartSpec(
            mark="layered",
            encoding=top_encoding,
            layers=sub_layers,
            config=build_palette_config(chart.palette),
            data=step_band_data,
            x_label_block_height=x_label_block,
        )
        # When bucketing fired, stamp transformed rows onto the spec so the
        # session does not overwrite with raw query data.
        if transformed:
            spec.data = normalize_data_types(
                step_band_data if step_band_data is not None else data
            )

        if chart.layers:
            base_label = y_plain
            spec = render_cartesian_overlay(
                spec,
                chart.layers,
                data,
                chart_id=chart.id,
                axis_x=style.axis_x,
                axis_y=style.axis_y,
                base_measure_title_suppressed=(
                    bool(chart.y_label) and chart.style.axis_y.title.visible is False
                ),
                base_orientation="vertical",
                base_x_authored_temporal=base_x_authored_temporal,
                tooltip_format=style.tooltip_format,
                background=chart.background,
                single_series_fill=style.single_series_fill,
                legend=chart.legend,
                config=build_palette_config(chart.palette),
                layered_rail_may_fire=(
                    style.endpoint_labels.visible
                    and layered_endpoint_rail_fires(
                        layered_endpoint_rail_shape(chart.x, chart.y),
                        [layer.color is None for layer in chart.layers],
                    )
                ),
                base_query_name=chart.query_name,
                base_mark_type="line",
                base_label=base_label,
                datasets=datasets,
                base_stack_normalize=False,
                base_stack_center=False,
                multiples_scale_independent=multiples_scale_independent(chart),
            )

        return spec
