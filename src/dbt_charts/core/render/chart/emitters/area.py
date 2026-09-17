"""Area chart emitter for render-v2."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from dbt_charts.core.compile.models.chart.resolved._channel import ResolvedStyleChannel
from dbt_charts.core.compile.models.chart.resolved.area import ResolvedAreaChart
from dbt_charts.core.compile.models.style.resolved._marks import ResolvedAreaMarkStyle
from dbt_charts.core.compile.models.style.resolved.area import ResolvedAreaStyle
from dbt_charts.core.compile.models.style.theme.category_colors import (
    category_scale_for,
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
    pin_normalize_axis_format,
    pin_sorted_domain,
    resolve_cartesian_x,
    resolve_xy_titles,
    series_order_expression,
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
from dbt_charts.core.render.chart.emitters._layers import (
    emit_area_layer,
    sparse_band_transforms,
)
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
from dbt_charts.core.render.chart.validation import (
    validate_color_series,
    validate_preaggregated_data_per_panel,
)
from dbt_charts.core.render.chart.vl_field_maps import (
    compose_axis_label_expr,
    measure_axis_to_vl,
)
from dbt_charts.core.render.utils import normalize_data_types
from dbt_charts.core.utils import (
    layered_endpoint_rail_fires,
    layered_endpoint_rail_shape,
    sorted_series_by_stack_order,
)

_DF_SERIES_ORDER_KEY = "__df_series_order"


def _normalize_area_data(
    chart: ResolvedAreaChart,
    dataset: ChartDataset,
    data: list[dict[str, Any]],
) -> tuple[list[dict[str, Any]], bool, bool]:
    """Apply labeled-temporal normalization and ordinal-time gap-fill.

    Returns (data, transformed, x_authored_temporal). ``transformed`` is
    True when either step changed the rows — the caller stamps those rows
    onto the spec so the session does not overwrite them with raw query
    data. This must fire for labeled-temporal normalization alone (e.g.
    "Q1 2020" rewritten to an ISO bucket) even when gap-fill itself is
    skipped, or the session re-stamps unparsed labels onto a temporal
    encoding. ``x_authored_temporal`` is gap_fill_ordinal_time's own
    verdict, threaded to the caller's render_cartesian_overlay call instead
    of re-derived there.

    ``dataset`` is ``data`` (``dataset.all_rows()``) still in panel-shaped
    form — passed to ``gap_fill_ordinal_time_per_panel`` via ``restripe()``
    when normalization mutated ``data``, since ``chart.x`` can itself be the
    multiples field and re-deriving panels from the mutated value would
    fail to match the pre-mutation baked axis.
    """
    transformed = False
    mutated_dataset = dataset
    if chart.x:
        # Rewrite labeled/year-shaped x to ISO first (same as line.py/bar.py) so
        # build_cartesian_x_encoding sees date-shaped values and the mark-type
        # split routes area → temporal, not a quantitative fractional-year axis.
        normalized = normalize_labeled_temporal(data, chart.x)
        if normalized is not data:
            data = normalized
            transformed = True
            mutated_dataset = restripe(dataset, data)
    densified, x_authored_temporal = gap_fill_ordinal_time_per_panel(
        chart.style.axis_x,
        chart.x,
        chart.color,
        mutated_dataset,
        "area",
        chart.style.area_mark.curve == BAND_STEP_CURVE,
        True,
    )
    if densified is not None:
        data = densified
        transformed = True
    return data, transformed, x_authored_temporal


def _area_spatial_order(
    chart: ResolvedAreaChart, data: list[dict[str, Any]], series_field: str
) -> tuple[list[str], list[str]]:
    """Resolve area's distinct series and spatial legend order in one pass.

    Returns ``(series, order)``. Stacked: ``order`` is top-of-stack first,
    reversing the chart-global descending-total baseline order. Unstacked
    (overlap): stable order by each series' most-recent non-null value.
    ``order`` is empty when there's no series, or no x/y anchor to order by
    — the caller treats that as "nothing to reorder."
    """
    series = distinct_series_values(data, series_field)
    if not series:
        return series, []
    if chart.stack not in (None, "none"):
        y_field = chart.y if isinstance(chart.y, str) else WIDE_VALUE_FIELD
        return series, list(
            reversed(
                sorted_series_by_stack_order(
                    series, data, series_field, None, y_field=y_field
                )
            )
        )
    if chart.x and isinstance(chart.y, str):
        return series, sorted_series_by_last_value(
            series, data, chart.x, chart.y, series_field
        )
    return series, []


def _apply_area_color_encoding(
    chart: ResolvedAreaChart,
    data: list[dict[str, Any]],
    top_encoding: VLDict,
) -> ResolvedStyleChannel | None:
    """Apply color encoding to top_encoding in-place.

    Returns the resolved color channel (or None) for use in sub-layer assembly.
    """
    color_ch = chart.resolved_channels.get("color")
    if color_ch is not None:
        enc = channel_to_encoding(color_ch, data)
        apply_color_legend(enc, chart.legend)
        enc_type = enc.get("type")
        if categorical_color_encoding(color_ch, enc_type) and enc_type == "nominal":
            series, order = _area_spatial_order(chart, data, color_ch.data_field)
            # `order` is empty only when there's no x/y anchor to
            # reorder by (see _area_spatial_order's own docstring) --
            # `series` (plain alphabetical) is still the correct domain
            # to resolve `legend.values` against even then, just not
            # the display-order refinement. `chart.palette` can
            # legally be authored empty; it governs the scale's range
            # only.
            resolve_order = order or series
            if resolve_order:
                if order and chart.palette:
                    palette_order = (
                        list(reversed(order))
                        if chart.stack not in (None, "none")
                        else series
                    )
                    # A bound scale colors by value, so the palette
                    # order it is handed no longer decides anything --
                    # the stacked reversal above still governs the
                    # unbound case.
                    enc["scale"] = spatial_color_scale(
                        palette_order,
                        chart.palette,
                        order,
                        category_scale_for(chart.category_colors, color_ch.data_field),
                    )
                # `chart.palette` can legally be authored empty; it
                # governs the scale's range only, so resolution still
                # runs. But with nothing authored and no palette to
                # paint a scale from, there is nothing worth pinning
                # (matches line.py's identical guard).
                if chart.legend.values is not None or chart.palette:
                    apply_legend_entry_order(
                        enc,
                        resolve_order,
                        authored=chart.legend.values,
                    )
        elif (
            categorical_color_encoding(color_ch, enc_type)
            and enc_type == "ordinal"
            and chart.legend.values is not None
        ):
            # An ordinal column carries its own inherent order --
            # Vega's native stack/paint order, not the descending-total
            # or last-value spatial order above -- so the paint scale
            # must never touch it. Resolution still has to run so an
            # authored `legend.values` entry doesn't ship as a phantom
            # swatch with no diagnostic.
            apply_legend_entry_order(
                enc,
                distinct_series_values(data, color_ch.data_field),
                authored=chart.legend.values,
            )
        top_encoding["color"] = enc
    return color_ch


def _apply_area_step_band(
    chart: ResolvedAreaChart,
    data: list[dict[str, Any]],
    top_encoding: VLDict,
    area_mark: ResolvedAreaMarkStyle,
    x_type: str | None,
) -> list[dict[str, Any]] | None:
    """Apply band-aware step row doubling when curve is step on a band x-axis."""
    if is_band_step(area_mark.curve, x_type):
        # Area always renders as a continuous silhouette.
        return apply_step_band(data, top_encoding, chart_id=chart.id, connect=True)
    return None


def _build_area_top_encoding(
    chart: ResolvedAreaChart,
    data: list[dict[str, Any]],
    style: ResolvedAreaStyle,
    box: RenderBox,
    x_domain: list[Any] | None,  # type-state: explicit_any — raw x values
    panel_fields: tuple[str, ...],
) -> tuple[VLDict, ResolvedStyleChannel | None, str | None, str | None, float | None]:
    """Build the VL encoding dict, color channel, resolved x VL type
    (None when the chart has no x channel at all), plain y label, and the
    x-label block height at the tilt just resolved, for an area chart.

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
            "area",
            style.area_mark.curve,
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
    # Area always needs explicit stack to override VL's implicit stacking default.
    y_enc["stack"] = chart.stack if chart.stack not in (None, "none") else None
    if chart.stack == "normalize":
        pin_normalize_axis_format(ay_vl, ay)
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
    color_ch = _apply_area_color_encoding(chart, data, top_encoding)
    x_type = vl_type if chart.x else None
    return top_encoding, color_ch, x_type, titles.y_plain, x_res.label_block_height


def _emit_multi_metric_area(
    chart: ResolvedAreaChart,
    data: list[dict[str, Any]],
    box: RenderBox,
    panel_fields: tuple[str, ...],
) -> ChartSpec:
    """Emit a folded unit spec for a multi-metric (y: [a, b, ...]) area chart."""
    assert chart.wide_measures
    style = chart.style
    area_mark = style.area_mark
    ax, ay = style.axis_x, style.axis_y
    x_res = (
        resolve_cartesian_x(
            chart.x,
            data,
            ax,
            style.label_usable_ratio,
            box.width,
            chart.id,
            "area",
            reserved_width=resolve_endpoint_rail_span(chart, data, box.width),
            panel_fields=panel_fields,
        )
        if chart.x
        else CartesianXResolution("nominal", {}, {})
    )
    if is_band_step(area_mark.curve, x_res.vl_type if chart.x else None):
        raise ChartDataError(
            f"Area chart '{chart.id}': band-aware step curve is not supported "
            "for multi-metric (y: [...]) charts -- use a single y field or "
            "choose a different curve style."
        )
    measures = list(chart.wide_measures)
    dimension = chart.color
    wide_labels = wide_measure_labels_for(chart.wide_measures)
    series = wide_series_names(measures, dimension, data, wide_labels)
    # Same order computation area's authored-color path uses (_area_spatial_order,
    # above), fed a long-form view of the wide data. `folded`'s own
    # WIDE_LABEL_FIELD is RAW (unfold_wide_rows never humanizes), so any
    # order computed by grouping/matching against it -- `sorted_series_by_
    # stack_order`, `_area_spatial_order` -- is RAW too; humanize the
    # RESULT afterward, per entry, rather than feeding it the already-
    # humanized `series` (which would never match `folded`'s rows and
    # silently degrade to an alphabetical fallback).
    folded = unfold_wide_rows(data, measures, dimension)
    raw_series = raw_wide_series_names(measures, dimension, data)
    is_stacked = chart.stack not in (None, "none")
    if is_stacked:
        raw_baseline_order = sorted_series_by_stack_order(
            raw_series,
            folded,
            WIDE_LABEL_FIELD,
            None,
            y_field=WIDE_VALUE_FIELD,
        )
        baseline_order = [
            humanize_wide_series_name(name, dimension, wide_labels)
            for name in raw_baseline_order
        ]
        display_order = list(reversed(baseline_order))
        raw_fold_order = measures
    else:
        _series, raw_order = _area_spatial_order(chart, folded, WIDE_LABEL_FIELD)
        display_order = (
            [
                humanize_wide_series_name(name, dimension, wide_labels)
                for name in raw_order
            ]
            if raw_order
            else series
        )
        # `raw_order` (like `_series`) only ever names measures with at
        # least one non-null row in THIS render -- `folded` already
        # dropped an all-null measure entirely (unfold_wide_rows). `series`
        # (wide_series_names, the authored measures x observed dimension
        # values) is the real, complete domain `fold_wide_measures`
        # resolves `legend.values` against, so an entry missing from
        # `display_order` (a measure -- or measure x dimension composite --
        # a live board's data happens to go empty for) must still be
        # appended, or an unrelated data change turns a correct `values:`
        # entry into a spurious WARN, and `spatial_color_scale`'s
        # `color_of[s] for s in order` raises a KeyError for any entry
        # `series` (which feeds the palette) has but `display_order`
        # doesn't.
        missing = [s for s in series if s not in display_order]
        if missing:
            display_order = display_order + missing
        baseline_order = None
        raw_fold_order = raw_order if raw_order else raw_series
    wide = fold_wide_measures(
        measures,
        chart.color,
        data,
        chart.palette,
        chart.legend,
        display_order=display_order,
        baseline_order=baseline_order,
        wide_measure_labels=wide_labels,
        # Explicit order governs stacked accumulation; unstacked/overlap uses
        # fold sequence as front-to-back paint order. Stays RAW -- feeds
        # _measure_paint_order/_label_expression, which build Vega
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
    y_enc["stack"] = chart.stack if chart.stack not in (None, "none") else None
    if chart.stack == "normalize":
        pin_normalize_axis_format(ay_vl, ay)
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
    if wide.order:
        top_encoding["order"] = wide.order
    stacked = chart.stack not in (None, "none")
    facet_fields = [f for f in (wide.color.get("field"), wide.order.get("field")) if f]
    band_transforms = (
        sparse_band_transforms(chart.x, wide.value_field, facet_fields)
        if stacked and chart.x and facet_fields
        else []
    )
    sub_layers = emit_area_layer(
        style.area_mark,
        style.line_mark,
        style.point_mark,
        chart.background,
        style.single_series_fill,
        True,
        wide.color,
        [],
        stacked,
        band_transforms,
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
class AreaEmitter:
    def emit(
        self,
        chart: ResolvedAreaChart,
        box: RenderBox,
        dataset: ChartDataset,
        datasets: dict[str | None, list[dict[str, Any]]] | None = None,
    ) -> ChartSpec:
        data = dataset.all_rows()
        validate_preaggregated_data_per_panel(chart, dataset)
        validate_color_series(chart, data)
        # Normalize labeled temporal strings and fill ordinal-time gaps before
        # any path — multi-metric needs this as much as single-series.
        data, transformed, base_x_authored_temporal = _normalize_area_data(
            chart, dataset, data
        )
        panel_fields = tuple(axis.field for axis in dataset.axes)
        if chart.wide_measures:
            # A folded (wide-measures) chart returns before chart.layers ever
            # applies (below) — no union to compute here.
            spec = _emit_multi_metric_area(chart, data, box, panel_fields)
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
        top_encoding, color_ch, x_type, y_plain, x_label_block = (
            _build_area_top_encoding(chart, data, style, box, x_domain, panel_fields)
        )
        step_band_data = _apply_area_step_band(
            chart, data, top_encoding, style.area_mark, x_type
        )
        is_stacked = chart.stack not in (None, "none")
        # No per-layer tooltip array: structured tooltip content (base's own,
        # and -- when chart.layers is set -- the base's role content wired
        # onto this same spec) is set after emission by StructuredTooltipFeature
        # via VL's description channel. See features/structured_tooltip.py.
        measure_field = chart.y if isinstance(chart.y, str) else None
        raw_series_field = (
            top_encoding["color"].get("field") if color_ch is not None else None
        )
        series_field = raw_series_field if isinstance(raw_series_field, str) else None
        _series, display_order = (
            _area_spatial_order(chart, data, series_field)
            if is_stacked and series_field
            else ([], [])
        )
        baseline_order = list(reversed(display_order))
        order_transforms: list[VLDict] = []
        facet_fields: list[str] = []
        if series_field:
            facet_fields.append(series_field)
        if baseline_order and series_field:
            top_encoding["order"] = {
                "field": _DF_SERIES_ORDER_KEY,
                "sort": "ascending",
            }
            order_transforms.append(
                {
                    "calculate": series_order_expression(series_field, baseline_order),
                    "as": _DF_SERIES_ORDER_KEY,
                }
            )
            facet_fields.append(_DF_SERIES_ORDER_KEY)
        band_transforms = (
            sparse_band_transforms(chart.x, measure_field, facet_fields)
            if is_stacked and chart.x and measure_field and series_field
            else []
        )
        sub_layers = emit_area_layer(
            style.area_mark,
            style.line_mark,
            style.point_mark,
            chart.background,
            style.single_series_fill,
            color_ch is not None,
            top_encoding["color"] if color_ch is not None else {},
            [],
            is_stacked,
            band_transforms,
            band_step=step_band_data is not None,
            pin_child_colors=color_ch is not None or bool(chart.layers),
            inherit_parent_color=bool(chart.layers),
        )
        spec = ChartSpec(
            mark="layered",
            encoding=top_encoding,
            layers=sub_layers,
            config=build_palette_config(chart.palette),
            data=step_band_data,
            transforms=order_transforms,
            x_label_block_height=x_label_block,
        )
        # Stamp transformed rows so the session does not overwrite with raw
        # query data. Preserve step-band-expanded rows when gap-fill also fired.
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
                base_mark_type="area",
                base_label=base_label,
                datasets=datasets,
                base_stack_normalize=chart.stack == "normalize",
                base_stack_center=chart.stack == "center",
                multiples_scale_independent=multiples_scale_independent(chart),
            )

        return spec
