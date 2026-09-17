"""Bar chart emitter for render-v2."""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
from typing import Any, Literal

from dbt_charts.core.compile.config import get_chart_rendering
from dbt_charts.core.compile.models.chart.resolved.bar import ResolvedBarChart
from dbt_charts.core.compile.models.primitives import OverlapSpec
from dbt_charts.core.compile.models.style.theme.category_colors import (
    category_scale_for,
)
from dbt_charts.core.compile.resolve.chart._chart_rows import (
    ChartDataset,
    restamp,
    restripe,
)
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
from dbt_charts.core.diagnostics.codes_render import (
    ERR_HISTOGRAM_NON_NUMERIC,
    ERR_LABEL_VALUES_NOT_TEMPORAL,
)
from dbt_charts.core.render.chart._types import VLDict
from dbt_charts.core.render.chart.emitters._cartesian import (
    apply_domain_headroom_bounds,
    authored_measure_domain,
    bar_sort_to_vl,
    build_palette_config,
    cartesian_x_scale_domain,
    distinct_series_values,
    multiples_scale_independent,
    nudge_band_scale_off_range_start,
    pin_normalize_axis_format,
    resolve_measure_y_scale,
    resolve_xy_titles,
    series_order_expression,
    spatial_color_scale,
    wide_measures_title,
    x_encoding_is_banded,
)
from dbt_charts.core.render.chart.emitters._channels import (
    apply_color_legend,
    apply_legend_entry_order,
    categorical_color_encoding,
    channel_to_encoding,
    gap_fill_ordinal_time_per_panel,
    infer_vega_type_from_data,
    resolve_legend_entries,
)
from dbt_charts.core.render.chart.emitters._endpoint_rail import (
    resolve_endpoint_rail_span,
)
from dbt_charts.core.render.chart.emitters._label_overlap import resolve_axis_x_overlap
from dbt_charts.core.render.chart.emitters._layers import emit_bar_layer
from dbt_charts.core.render.chart.emitters._overlay import (
    overlay_uses_band_step,
    overlay_x_domain_values,
    render_cartesian_overlay,
)
from dbt_charts.core.render.chart.emitters._tooltip import field_cardinality
from dbt_charts.core.render.chart.emitters._wide import (
    FoldedMeasures,
    fold_wide_measures,
)
from dbt_charts.core.render.chart.spec import ChartSpec, RenderBox
from dbt_charts.core.render.chart.time_unit_detect import normalize_labeled_temporal
from dbt_charts.core.render.chart.type_inference import (
    apply_x_tick_cadence,
    build_cartesian_x_encoding,
    gate_label_format,
    resolve_cartesian_x_type,
    temporal_edge_labels_flushed,
    y_zero_scale,
    zero_anchor_pinned_floor,
)
from dbt_charts.core.render.chart.validation import (
    validate_color_series,
    validate_preaggregated_data_per_panel,
    validate_raw_rows_for_histogram,
)
from dbt_charts.core.render.chart.vl_field_maps import (
    axis_to_vl,
    bake_tick_ladder,
    bar_corner_props,
    bar_mark_radius,
    bar_mark_to_vl,
    compose_axis_label_expr,
    effective_bar_size,
    emit_resolved_scale_vl,
    measure_axis_to_vl,
)
from dbt_charts.core.render.utils import normalize_data_types
from dbt_charts.core.text.case import format_display_text
from dbt_charts.core.utils import (
    DEFAULT_VL_LABEL_LIMIT,
    cap_padding_to_label_limit,
    layered_endpoint_rail_fires,
    layered_endpoint_rail_shape,
    measured_label_padding,
    sorted_series_by_stack_order,
)

# Keeps every band-scale slot on the axis even when its row's own measure is
# null. Vega-Lite otherwise derives a band scale's default domain from the
# marks' invalid-value-filtered dataset, so a null-valued bucket (present in
# the data, filtered from the marks) silently loses its axis slot — bar is
# the only cartesian family this bites, since line/area resolve the same
# bucketed grain to a continuous temporal scale, whose [min, max] domain
# doesn't shrink when one interior row is filtered. "break-paths-show-
# domains" keeps the slot and draws no mark for the invalid row — the native
# VL fix, verified against vl_convert 1.9.0 (VL 6.4.1) to also cover nominal
# categorical axes and to never collide with an authored chart.sort, unlike a
# computed scale.domain pinned from query rows.
_MARK_INVALID_CONFIG: VLDict = {"invalid": "break-paths-show-domains"}


def _is_color_1to1_with_x(
    x_field: str,
    color_field: str,
    dataset: ChartDataset,
) -> bool:
    """True when every x value maps to exactly one color value WITHIN EVERY panel.

    When this holds, emitting xOffset/yOffset allocates N sub-bands per
    category — each occupied by a single bar — making bars 1/N of slot
    width. Suppress the offset channel and render full-width bars instead;
    the color encoding still fires and paints each bar in its category color.

    Folded per panel (``dataset.panels`` — the chart's already panel-shaped,
    x-mutation-safe split; a non-faceted chart is the N=1 case, one panel,
    identical to a pooled read over the whole dataset). Requires ALL panels
    with data to be individually 1:1, not just one: a faceted chart can have
    one panel 1:1 while a sibling panel genuinely maps one x to several
    colors (true grouped bars) — suppressing the offset there would draw
    that panel's grouped bars full-width in the same band, hiding all but
    one. Checking the pooled view has the opposite failure — different
    panels can map the same x to different colors, which pools to M:N and
    would wrongly emit xOffset for a panel that is genuinely 1:1 on its own
    — so the fold must run per panel, but requiring every panel (not just
    one) to agree.

    Takes the already-built ``dataset`` rather than re-deriving panels from
    flat rows: ``chart.x`` can itself be the multiples field, and a caller
    that already ran ``normalize_labeled_temporal`` on it (bar.py's own
    year-shaped-x normalization) would have rewritten exactly the value a
    fresh ``regroup()`` needs unchanged to find the panel — the same hazard
    ``restripe()`` exists to avoid. ``restamp()``s ``x_field``/``color_field``
    back onto each panel's rows first (a no-op for either that isn't a
    partition field) — ``dataset.panels[*].rows`` never carry a partition
    column's values, so either field being the multiples field would
    otherwise make every row's lookup miss and read as 0 distinct values.
    """
    dataset = restamp(restamp(dataset, x_field), color_field)
    checked = False
    for panel in dataset.panels:
        unique_x = set()
        unique_pairs = set()
        for row in panel.rows:
            if x_field in row and color_field in row:
                unique_x.add(row[x_field])
                unique_pairs.add((row[x_field], row[color_field]))
        if not unique_x:
            continue
        checked = True
        if len(unique_x) != len(unique_pairs):
            return False
    return checked


# Grouped-bar overlap keyword → fraction of bar width.
# Negative = gap between bars; 0 = touching; positive = overlap.
_OVERLAP_NONE: float = -0.1
_OVERLAP_FLUSH: float = 0.0
_OVERLAP_PARTIAL: float = 0.25
_OVERLAP_FULL: float = 1.0
_OVERLAP_KEYWORDS: dict[str, float] = {
    "none": _OVERLAP_NONE,
    "flush": _OVERLAP_FLUSH,
    "partial": _OVERLAP_PARTIAL,
    "full": _OVERLAP_FULL,
}

# Default tick values for 100%-normalized stacked bars — quartile grid.
_NORMALIZE_QUARTILE_TICKS: tuple[float, ...] = (0, 0.25, 0.5, 0.75, 1.0)


def _count_series(chart: ResolvedBarChart, data: list[VLDict]) -> int:
    """Distinct color-field values in data — the grouped-bar series count."""
    color_ch = chart.resolved_channels.get("color")
    if color_ch is None or not color_ch.data_field:
        return 1
    return field_cardinality(color_ch.data_field, data) or 1


def _grouped_bar_paint_order(chart: ResolvedBarChart, series: list[str]) -> list[str]:
    """A grouped bar's paint-scale/xOffset order, from ``chart.legend.values``.

    Read independently of legend visibility: whether ``scale.domain`` and
    xOffset follow an authored reorder is a paint-order question, not a
    legend-display one, so a hidden legend (``apply_legend_entry_order``
    no-ops and returns ``None`` for a suppressed legend) must not suppress
    this reorder the way relying on that return value would. Only a FULL
    authored reorder (same set, different order) counts -- a curated
    SUBSET is a legend-only filter, never a domain reorder, and falls back
    to ``series``.
    """
    if chart.legend.values is None:
        return series
    resolved, _ = resolve_legend_entries(chart.legend.values, series)
    # apply_legend_entry_order writes list(dict.fromkeys(resolved)) to
    # legend["values"] -- dedupe here too, or a repeated (or fold-repeated,
    # e.g. "tools" re-matching "Tools") authored entry pads `resolved`
    # past `series`' length and a genuine full reorder falls back to
    # alphabetical instead.
    resolved = list(dict.fromkeys(resolved))
    return resolved if sorted(resolved) == sorted(series) else series


def _resolve_overlap_fraction(
    overlap: OverlapSpec,
    n_series: int,
) -> float:
    """Resolve an authored overlap value to a fraction of bar width.

    Keywords map via ``_OVERLAP_KEYWORDS``; None/'auto' are series-adaptive —
    partial only at exactly 2 series, none otherwise. A number passes through.
    """
    if overlap is None or overlap == "auto":
        return _OVERLAP_PARTIAL if n_series == 2 else _OVERLAP_NONE
    if isinstance(overlap, str):
        return _OVERLAP_KEYWORDS[overlap]
    return float(overlap)


def apply_grouped_bar_spacing(
    chart: ResolvedBarChart,
    spec: ChartSpec,
    data: list[VLDict],
    n_series: int = -1,
) -> None:
    """Apply grouped-bar group gaps and within-group bar spacing on a flat spec.

    Must run on the flat (pre-``chart.layers``-wrap) spec: the categorical axis
    scale and the xOffset/yOffset encoding only sit at the spec's top level
    before ``render_cartesian_overlay`` wraps it into a layered spec, and the
    ``mark.width``/``mark.height`` band shorthand set below must be in place
    before ``_fix_bar_band_width`` converts it to a bandwidth() expression.

    ``overlap`` fraction: negative = gap, 0 = touching, positive = overlap.
    * Gap/touching (≤0): emit ``mark.{width|height}: {band: 1+f}`` (responsive).
    * Full (≥1): drop the offset channel so series coincide.
    * Positive overlap (0 < f < 1): restricted to exactly 2 series.
      - Vertical: ``width: {band: 1+f}`` — band > 1 overflows in Vega.
      - Horizontal: ``size: {expr: "(1+f) * bandwidth('yOffset')"}`` — the
        ``band`` shorthand clamps height at 1 so it can't overlap, but the
        ``bandwidth(scaleName)`` expression form isn't clamped and is
        resolved by Vega at its own layout time, so it needs no panel
        dimension and no chrome estimate.
    """
    if chart.stack != "none":
        return
    color_ch = chart.resolved_channels.get("color")
    if color_ch is None or color_ch.mode != "series":
        return

    is_horizontal = chart.orientation == "horizontal"
    offset_ch = "yOffset" if is_horizontal else "xOffset"
    encoding = spec.encoding
    if offset_ch not in encoding:
        return

    cat_ch = "y" if is_horizontal else "x"
    mark_props = spec.mark_props
    if cat_ch in encoding and not x_encoding_is_banded(encoding[cat_ch]):
        # A continuous categorical channel (quantitative, or a temporal x
        # promoted past max_ordinal_buckets) has no band scale for the offset
        # channel to sub-divide, so every spacing control below is inapplicable:
        # paddingInner/paddingOuter are band-scale-only, and `overlap` is
        # defined as a fraction of band width. Leave mark.width/height alone so
        # the mark's own literal pixel width (bar.size, set by bar_mark_to_vl —
        # or, when unauthored, continuous_bar_size_prop's gap/min_size/max_size
        # ladder) governs — the same native mechanism a non-grouped
        # continuous-x bar already renders with.
        #
        # An AUTHORED overlap must not evaporate here, though: silently
        # dropping it would hand back a spec that ignores the setting with no
        # signal. Only the renderer default falls through — both spellings of
        # it: None ("not authored") and the explicit "auto" that
        # BarChartStyle.overlap documents as its equivalent. Authoring the
        # documented no-op must render exactly as omitting it does, which is
        # what _resolve_overlap_fraction already does for the banded case.
        # Written against the generic bandedness predicate rather than
        # hardcoding "vertical": today `_emit_horizontal` always types its
        # categorical channel "nominal", so this never fires on a horizontal
        # bar — but that is a property of the emitter, not an invariant worth
        # baking in a second time here.
        if chart.style.overlap is not None and chart.style.overlap != "auto":
            raise ChartDataError(
                f"Bar chart '{chart.id}': style.overlap is not supported on a "
                f"continuous {cat_ch}-axis — overlap is a fraction of the "
                "categorical band width, and a quantitative (or wide temporal) "
                f"{cat_ch} has no band to divide. Group the {cat_ch} into "
                "categories or buckets, or drop the overlap setting.",
                chart_id=chart.id,
            )
        return

    # Outer categorical axis band-scale padding — the gap between groups and the
    # margin at both plot edges. The shorthand 'padding' overrides paddingOuter
    # in Vega, so it must be removed.
    if cat_ch in encoding:
        cat_scale: dict[str, Any] = encoding[cat_ch].setdefault("scale", {})
        bar_cfg = get_chart_rendering().bar
        cat_scale.pop("padding", None)
        cat_scale["paddingInner"] = bar_cfg.grouped_bar_padding_inner
        cat_scale["paddingOuter"] = bar_cfg.grouped_bar_padding_outer
    if n_series < 0:
        n_series = _count_series(chart, data)
    fraction = _resolve_overlap_fraction(chart.style.overlap, n_series)

    if fraction >= _OVERLAP_FULL:
        encoding.pop(offset_ch, None)
        return

    encoding[offset_ch].setdefault("scale", {})["paddingInner"] = 0
    size_dim = "height" if is_horizontal else "width"

    if fraction <= 0:
        mark_props[size_dim] = {"band": max(0.05, 1.0 + fraction)}
        return

    # Positive overlap: only supported for exactly 2 series.
    if n_series != 2:
        raise ChartDataError(
            f"Bar chart '{chart.id}': positive overlap is only supported for "
            f"exactly 2 series (got {n_series}) — 3+ series buries the middle one. "
            "Use 'none'/'flush', or rely on the 'auto' default.",
            chart_id=chart.id,
        )

    if not is_horizontal:
        # Vertical: width band > 1 overflows → exact, responsive overlap.
        mark_props["width"] = {"band": 1.0 + fraction}
        return

    # Horizontal: band clamps, so size comes from Vega's own resolved
    # per-series offset-scale width. paddingInner=0 on the offset scale
    # (set above) makes bandwidth('yOffset') == total_step / n_series, and
    # fraction < 1 here keeps (1+fraction)*bandwidth under 2*bandwidth ==
    # total_step — the group footprint can't spill into a neighboring
    # category by construction, no clamp needed.
    mark_props["size"] = {"expr": f"{1.0 + fraction} * bandwidth('yOffset')"}
    # Drop the band-relative height (from bar_mark_to_vl / theme band_width) so
    # the explicit pixel size is the sole height control — band and size conflict.
    mark_props.pop("height", None)


def _emit_histogram(
    chart: ResolvedBarChart,
    data: list[dict[str, Any]],
    box: RenderBox,
    *,
    config: dict[str, Any],
) -> ChartSpec:
    """Emit a histogram spec: binned quantitative x, aggregate-count y, color-only grouping.

    Mirrors V1 _map_histogram exactly. Does NOT call validate_preaggregated_data —
    histograms consume raw rows (VL handles binning + aggregation) — but does
    call validate_raw_rows_for_histogram, the mirror-image guard that rejects
    the opposite mistake: data that's already one row per bucket.
    """
    # Validate before building: missing or non-numeric x crashes vl_convert.
    if chart.x is None:
        raise ChartDataError.from_code(
            ERR_HISTOGRAM_NON_NUMERIC,
            chart_id=chart.id,
            field="<none>",
            vl_type="missing",
        )
    x_vl_type = infer_vega_type_from_data(data, chart.x)
    if x_vl_type != "quantitative":
        raise ChartDataError.from_code(
            ERR_HISTOGRAM_NON_NUMERIC,
            chart_id=chart.id,
            field=chart.x,
            vl_type=x_vl_type,
        )
    validate_raw_rows_for_histogram(chart, data)

    # Histogram bars are always positive (counts) → top-anchored corner rounding.
    # bar_mark_to_vl no longer carries the corner radius (sign-aware rounding
    # moved to bar_corner_props); apply it here as _emit_vertical does.
    radius = bar_mark_radius(chart.style.mark)
    corner = (
        bar_corner_props(radius, "vertical", True, False)
        if radius is not None
        else None
    ) or {}
    color_ch = chart.resolved_channels.get("color")
    mark_props: dict[str, Any] = {
        # A histogram's binned x IS a band scale for width purposes — VL's
        # {"band": f} shorthand resolves correctly against the bin's own
        # x/x2 span (measured empirically; unlike a plain continuous x, this
        # case is not degraded).
        **bar_mark_to_vl(chart.style.mark, "vertical", True),
        **corner,
        "tooltip": True,
    }
    if color_ch is None:
        mark_props["fill"] = chart.style.single_series_fill

    # Histogram x is always quantitative/continuous: pin angle=0 and set the
    # allow directive so no label dropping occurs (VL manages bin tick layout).
    ax = chart.style.axis_x
    x_enc: dict[str, Any] = {
        "field": chart.x,
        "type": "quantitative",
        "bin": True,
        "title": resolve_xy_titles(
            chart.x, None, chart.x_label, None, ax, ax, box, chart.id
        ).x_title,
        "axis": axis_to_vl(ax, label_overlap="allow", label_angle=0.0),
    }
    # A histogram's x IS the quantitative (binned) axis, so authored tick
    # cadence applies here. This path builds its own axis dict rather than
    # going through build_cartesian_x_encoding, so it reaches the shared
    # emitter directly — otherwise ticks.count/step resolve and then vanish.
    apply_x_tick_cadence(x_enc["axis"], ax, chart.x, "quantitative")

    y_enc: dict[str, Any] = {
        "aggregate": "count",
        "type": "quantitative",
        "title": chart.y_label or "Count",
        # Count aggregate: bar heights are computed by VL client-side, so
        # there is no data column to estimate a domain from — () keeps the
        # own-side-align guard's away-side fallback in effect for this axis.
        # Every measure-axis emitter routes its axis dict through
        # compose_axis_label_expr, this one included, so a future histogram
        # change that does start baking tick_values picks up the ruler (and
        # case/values-filter wrapping) for free instead of silently missing
        # the convention.
        "axis": compose_axis_label_expr(
            measure_axis_to_vl(chart.style.axis_y, data, ()),
            chart.style.axis_y.ruler,
            chart.style.axis_y,
        ),
    }

    encoding: dict[str, Any] = {"x": x_enc, "y": y_enc}
    transforms: list[VLDict] = []

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
        if categorical_color_encoding(color_ch, enc_type) and enc_type == "nominal":
            series = distinct_series_values(data, color_field)
            counts = Counter(
                str(row[color_field])
                for row in data
                if row.get(color_field) is not None
            )
            baseline_order = sorted(series, key=lambda s: (-counts[s], s))
            display_order = list(reversed(baseline_order))
            # See the vertical stacked branch below: `chart.palette`
            # can legally be authored empty, so resolution runs
            # unconditionally -- it governs `scale`'s range only.
            apply_legend_entry_order(
                enc,
                display_order,
                authored=chart.legend.values,
            )
            if chart.palette:
                enc["scale"] = spatial_color_scale(
                    baseline_order, chart.palette, display_order
                )
            transforms.append(
                {
                    "calculate": series_order_expression(color_field, baseline_order),
                    "as": _DF_SERIES_ORDER_KEY,
                }
            )
            encoding["order"] = {
                "field": _DF_SERIES_ORDER_KEY,
                "sort": "ascending",
            }
        elif (
            categorical_color_encoding(color_ch, enc_type)
            and enc_type == "ordinal"
            and chart.legend.values is not None
        ):
            # An ordinal column carries its own inherent order --
            # Vega sorts the domain natively, so none of the paint
            # scale, counts-based baseline order or mark-order
            # transform above may touch it. Resolution still has to
            # run so an authored `legend.values` entry doesn't ship
            # as a phantom swatch with no diagnostic.
            apply_legend_entry_order(
                enc,
                distinct_series_values(data, color_field),
                authored=chart.legend.values,
            )
        encoding["color"] = enc

    return ChartSpec(
        mark="bar",
        mark_props=mark_props,
        encoding=encoding,
        config=config,
        transforms=transforms,
    )


@dataclass
class BarEmitter:
    def emit(
        self,
        chart: ResolvedBarChart,
        box: RenderBox,
        dataset: ChartDataset,
        datasets: dict[str | None, list[dict[str, Any]]] | None = None,
    ) -> ChartSpec:
        data = dataset.all_rows()
        config: dict[str, Any] = {
            **build_palette_config(chart.palette),
            "mark": _MARK_INVALID_CONFIG,
        }

        if chart.chart_type == "histogram":
            return _emit_histogram(chart, data, box, config=config)

        # Year-shaped x (INTEGER/VARCHAR) normalizes to ISO "YYYY-01-01" so the
        # bar x-axis routes through the same bucketed-year machinery as a true
        # DATE column, same as line.py's _normalize_line_data. Fires here,
        # before the vertical/horizontal split below — safe today because
        # chart.x is the category field on both orientations (horizontal bars
        # swap it onto the y channel, they never repurpose it as a measure).
        # `mutated_dataset` re-slices by position (restripe), not by
        # re-matching partition-column values (regroup) — chart.x can itself
        # be the multiples field, and this mutation would otherwise rewrite
        # exactly the value regroup() needs unchanged to find the panel.
        mutated_dataset = dataset
        if chart.x:
            normalized = normalize_labeled_temporal(data, chart.x)
            if normalized is not data:
                data = normalized
                mutated_dataset = restripe(dataset, data)

        # Validate before emitting — raises ChartDataError for duplicate keys.
        # ResolvedBarChart is structurally compatible with the V1 ResolvedChart
        # for bar-chart validation (chart_type, x, y, color — no shape needed).
        validate_preaggregated_data_per_panel(chart, mutated_dataset)
        validate_color_series(chart, data)

        is_horiz = chart.orientation == "horizontal"
        # Gap-fill bucketed ordinal-time x-axis data, same as line/area — before
        # the wide/long-form split, same as _normalize_line_data/_normalize_area_data:
        # a wide chart's raw rows are one-per-bucket(-per-dimension) with the
        # measures as columns, exactly the shape complete_ordinal_time_series
        # expects: chart.color is the authored dimension (or None), so the fill
        # cross-joins buckets × dimension values as it would for an authored
        # color: chart, and the measures ride along — no unfold needed.
        # When gap-fill fires, stamp transformed rows onto spec.data so the
        # session does not overwrite with the original raw data (mirrors line.py).
        # resolves_cartesian_x=False for horizontal: _emit_horizontal renders
        # chart.x as a plain nominal category axis (never through
        # resolve_cartesian_x_type), so it always needs the full ordinal
        # scaffold regardless of what the vertical-path density gate would say.
        # base_x_authored_temporal is gap_fill_ordinal_time's own verdict —
        # threaded to the overlay call below instead of re-derived there.
        filled, base_x_authored_temporal = gap_fill_ordinal_time_per_panel(
            chart.style.axis_x,
            chart.x,
            chart.color,
            mutated_dataset,
            "bar",
            False,
            not is_horiz,
        )
        gap_fired = filled is not None
        if filled is not None:
            data = filled

        if chart.wide_measures:
            if chart.x is None:
                raise ChartDataError(
                    f"Bar chart '{chart.id}': multi-metric (y: [...]) charts require an x field."
                )
            spec = _emit_wide_bar(chart, box, data, config, mutated_dataset)
            if gap_fired:
                spec.data = normalize_data_types(data)
            apply_grouped_bar_spacing(
                chart,
                spec,
                data,
                n_series=len(
                    wide_series_names(
                        chart.wide_measures,
                        chart.color,
                        data,
                        wide_measure_labels_for(chart.wide_measures),
                    )
                ),
            )
            return spec

        tooltip_format = chart.style.tooltip_format
        single_series_color = chart.style.single_series_fill

        if is_horiz:
            spec = _emit_horizontal(
                chart,
                data,
                tooltip_format,
                single_series_color,
                config,
                box,
                mutated_dataset,
            )
        else:
            # Overlay layers may carry x buckets the base series doesn't (a
            # forward goal ramp against actuals). Vega-Lite unions the
            # sub-layer domains, so the axis must be built against that union
            # — both its tick values and its crowding measurement — not
            # against the base's own rows. Computed here, before the base
            # spec's x encoding exists, and only on the vertical path: a
            # horizontal bar renders its categories on y, never consults this,
            # and computing it anyway would canonicalize every layer's rows
            # for nothing — and raise on a layer column this axis never reads.
            x_domain = (
                overlay_x_domain_values(
                    chart.layers,
                    data,
                    chart.x,
                    chart.style.axis_x,
                    base_x_authored_temporal,
                    datasets,
                    chart.query_name,
                )
                if chart.layers
                else None
            )
            spec = _emit_vertical(
                chart,
                data,
                tooltip_format,
                single_series_color,
                config,
                box,
                mutated_dataset,
                x_domain=x_domain,
                # A step-band overlay shares this scale and needs adjacent
                # band edges to stay the same float (see
                # nudge_band_scale_off_range_start).
                band_doubled=overlay_uses_band_step(chart.layers),
            )

        if gap_fired:
            spec.data = normalize_data_types(data)

        apply_grouped_bar_spacing(chart, spec, data)

        if chart.layers:
            base_label = spec.base_series_label
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
                # Same narrowing the whole emitter already runs on
                # `chart.orientation` (`is_horiz` above): the resolved field is
                # Optional, and every read of it here treats anything but
                # "horizontal" as the vertical layout.
                base_orientation="horizontal" if is_horiz else "vertical",
                base_x_authored_temporal=base_x_authored_temporal,
                tooltip_format=chart.style.tooltip_format,
                background=chart.background,
                single_series_fill=chart.style.single_series_fill,
                legend=chart.legend,
                config=config,
                layered_rail_may_fire=(
                    not is_horiz
                    and chart.style.endpoint_labels.visible
                    and layered_endpoint_rail_fires(
                        layered_endpoint_rail_shape(chart.x, chart.y),
                        [layer.color is None for layer in chart.layers],
                    )
                ),
                base_query_name=chart.query_name,
                base_mark_type="bar",
                base_label=base_label,
                datasets=datasets,
                base_stack_normalize=chart.stack == "normalize",
                # A bar's own stack: "center" is a diverging stack (0 is
                # still the meaningful anchor), not the area-only
                # streamgraph case base_stack_center guards against.
                base_stack_center=False,
                multiples_scale_independent=multiples_scale_independent(chart),
            )

        return spec


_DF_SERIES_ORDER_KEY = "__df_series_order"


def _emit_wide_bar(
    chart: ResolvedBarChart,
    box: RenderBox,
    data: list[dict[str, Any]],
    config: dict[str, Any],
    dataset: ChartDataset,
) -> ChartSpec:
    """Emit list-valued measures through one VL fold/unit specification."""
    assert chart.wide_measures
    measures = list(chart.wide_measures)
    dimension = chart.color
    wide_labels = wide_measure_labels_for(chart.wide_measures)
    series = wide_series_names(measures, dimension, data, wide_labels)
    # `fold_order` (below) must always stay RAW -- it feeds
    # `_measure_paint_order`/`_label_expression`, which build Vega
    # expressions comparing against the fold's own RAW key values, never
    # the humanized display text.
    raw_series = raw_wide_series_names(measures, dimension, data)
    if chart.stack not in (None, "none"):
        # Same order computation bar's authored-color stacked path uses
        # (see the `elif color_ch.mode == "series" ...` branch below), fed a
        # long-form view of the wide data — a wide bar's series order must
        # match what an authored color: field of the same data would produce.
        # `folded`'s own WIDE_LABEL_FIELD is RAW (unfold_wide_rows never
        # humanizes), so the stack-order grouping has to run against the
        # matching raw identity, not `series` (already humanized) -- humanize
        # the RESULT afterward, per entry.
        folded = unfold_wide_rows(data, measures, dimension)
        raw_baseline_order = sorted_series_by_stack_order(
            raw_series,
            folded,
            WIDE_LABEL_FIELD,
            chart.style.stack_order,
            y_field=WIDE_VALUE_FIELD,
        )
        baseline_order = [
            humanize_wide_series_name(name, dimension, wide_labels)
            for name in raw_baseline_order
        ]
        display_order = (
            list(reversed(baseline_order))
            if chart.orientation != "horizontal"
            else baseline_order
        )
        raw_fold_order = measures
    else:
        # Grouped (no stack): bar's authored-color path never pins an
        # explicit order for this shape either — it leaves VL's own default
        # (alphabetical) domain inference in place. Matching that default
        # explicitly here, rather than reproducing VL's implicit behavior
        # by omission, keeps the two paths tied to one documented contract
        # instead of an undocumented VL default either could silently drift
        # from.
        baseline_order = None
        display_order = series
        raw_fold_order = raw_series
    wide = fold_wide_measures(
        measures,
        chart.color,
        data,
        chart.palette,
        chart.legend,
        display_order=display_order,
        baseline_order=baseline_order,
        wide_measure_labels=wide_labels,
        # Stacked: baseline_order already governs stack position via the
        # explicit WIDE_ORDER_FIELD channel below, so the fold's own row
        # order is visually inert -- keep it as authored. Grouped: no such
        # channel exists, so the fold order IS the paint order.
        fold_order=raw_fold_order,
    )
    if chart.orientation == "horizontal":
        return _emit_horizontal(
            chart,
            data,
            chart.style.tooltip_format,
            chart.style.single_series_fill,
            config,
            box,
            dataset,
            wide,
        )
    return _emit_vertical(
        chart,
        data,
        chart.style.tooltip_format,
        chart.style.single_series_fill,
        config,
        box,
        dataset,
        wide,
    )


def _emit_vertical(
    chart: ResolvedBarChart,
    data: list[dict[str, Any]],
    tooltip_format: str,
    single_series_color: str,
    config: dict[str, Any],
    box: RenderBox,
    dataset: ChartDataset,
    wide: FoldedMeasures | Literal[False] = False,
    x_domain: list[Any] | None = None,
    band_doubled: bool = False,
) -> ChartSpec:
    """Emit a full vertical bar spec matching the oracle."""
    bar_mark = chart.style.mark
    label_usable_ratio = chart.style.label_usable_ratio
    ax = chart.style.axis_x  # categorical cascade
    ay = chart.style.axis_y  # measure cascade
    cat_field = chart.x

    # Resolve color channel once — used for fill/orient/encoding decisions below.
    color_ch = chart.resolved_channels.get("color") if wide is False else None

    # VL y = measure axis; radius computed before any early return so emit_bar_layer
    # receives it whether or not encoding is populated.
    measure_field = (
        wide.value_field
        if wide is not False
        else chart.y
        if isinstance(chart.y, str)
        else None
    )
    radius = bar_mark_radius(bar_mark)

    # No x field: emit a bar mark with tooltip only (no axis encoding).
    # Matches V1 behavior where map_x_encoding returns None for absent x.
    if cat_field is None:
        spec = emit_bar_layer(
            bar_mark,
            "vertical",
            wide is not False or color_ch is not None,
            single_series_color,
            radius,
            {},
            data,
            measure_field,
            config,
            [],
            True,
            None,
        )
        return spec

    # Categorical axis (VL x): resolve overlap first, then axis_to_vl
    panel_fields = tuple(axis.field for axis in dataset.axes)
    emitted_x_vl_type, _, emitted_x_time_unit = resolve_cartesian_x_type(
        data, cat_field, ax, "bar", False, panel_fields
    )
    reserved_width = resolve_endpoint_rail_span(chart, data, box.width)
    label_layout = resolve_axis_x_overlap(
        ax,
        cat_field,
        data,
        label_usable_ratio,
        is_horizontal_bar=False,
        edge_labels_flushed=temporal_edge_labels_flushed(emitted_x_vl_type, ax),
        chart_width=box.width - reserved_width,
        domain_values=x_domain,
        resolved_time_unit=emitted_x_time_unit,
    )
    ax_vl_raw = axis_to_vl(
        ax,
        label_overlap=label_layout.label_overlap,
        label_angle=label_layout.angle,
    )
    x_vl_type, ax_vl, detected_tu = build_cartesian_x_encoding(
        data,
        cat_field,
        ax,
        ax_vl_raw,
        "bar",
        format_time_unit=label_layout.format_time_unit,
        visibility_time_unit=label_layout.visibility_time_unit,
        label_anchor_index=label_layout.anchor_index,
        domain_values=x_domain,
        outer_chart_width=box.width,
        plot_width=box.width - reserved_width,
        panel_fields=panel_fields,
    )

    # Titles resolve together so each is wrapped against the extent of the
    # channel it actually renders on (width for x, the rotated height for y).
    # A wide chart's measure_field is a synthetic fold field (no name to
    # derive from) — fall back to the joined, humanized measure names instead,
    # same as an authored y_label always would.
    y_label_effective = chart.y_label or (
        wide_measures_title(chart.wide_measures) if wide is not False else None
    )
    titles = resolve_xy_titles(
        cat_field,
        measure_field if wide is False else None,
        chart.x_label,
        y_label_effective,
        ax,
        ay,
        box,
        chart.id,
    )
    x_title, y_title = titles.x_title, titles.y_title

    x_scale: dict[str, Any] = {}
    if ax.scale is not None and ax.scale.padding is not None:
        x_scale["padding"] = ax.scale.padding
    x_scale["paddingInner"] = bar_mark.padding
    x_scale.update(cartesian_x_scale_domain(ax.scale, x_vl_type, chart.id))
    nudge_band_scale_off_range_start(x_scale, x_vl_type, ax_vl, band_doubled)
    if x_vl_type == "quantitative" or (
        x_vl_type == "temporal" and (detected_tu is None or detected_tu == "none")
    ):
        # Vega-Lite centers each fixed-width bar on its data value; on a
        # continuous scale — quantitative, or a temporal x with no timeUnit
        # banding (a bar banded to its bucket sits inside the bucket's own
        # span and needs no reservation) —
        # "padding" dispatches to VL's own continuousPadding
        # (pixels on each side of the domain), which keeps the min/max bars
        # fully on-plot without an explicit domain (so VL's own `nice`
        # rounding still applies). axis_x.scale.padding defaults to 0
        # theme-wide for the categorical/band gutter case (_base.yaml
        # axis_x.scale.padding), which doesn't know about the bar's own pixel
        # footprint on a quantitative scale — take the larger of whatever's
        # already resolved and the half-bar-width the mark geometrically
        # needs to stay on-plot, so an author's own larger padding still
        # wins. An authored bar_mark.size gives the exact half-width; an
        # unauthored (computed-default) bar reserves against max_size, the
        # ceiling continuous_bar_size_prop's clamp can never exceed.
        bar_width_estimate = effective_bar_size(bar_mark)
        assert bar_width_estimate is not None, (
            "marks.bar.size and marks.bar.max_size both unset — "
            "theme cascade must populate at least one"
        )
        # An absent "padding" key means no gutter was resolved at all, so the
        # half-bar reservation stands alone; when one was resolved, the larger
        # of the two wins so an author's own wider padding still applies.
        half_bar = bar_width_estimate / 2
        resolved_padding = x_scale.get("padding")
        x_scale["padding"] = (
            half_bar if resolved_padding is None else max(resolved_padding, half_bar)
        )

    x_enc: dict[str, Any] = {
        "field": cat_field,
        "type": x_vl_type,
        "title": x_title,
        "axis": ax_vl,
        "scale": x_scale,
        # Honor an authored chart.sort by mapping it to a field-based VL sort on
        # the categorical (x) axis — mirrors V1 _apply_chart_sort. Without a sort
        # the nominal scale falls back to alphabetical domain order.
        "sort": bar_sort_to_vl(
            chart.sort, measure_field, chart.stack not in (None, "none")
        ),
    }
    # Temporal bar: emit timeUnit so VL bucketing stays UTC-aligned.
    if x_vl_type == "temporal" and detected_tu is not None and detected_tu != "none":
        from dbt_charts.core.render.chart.time_unit_detect import vl_time_unit

        x_enc["timeUnit"] = vl_time_unit(detected_tu)

    # Computed once, ahead of the stack-mode branch below, since both the
    # "normalize" and the plain stack path need it (the plain path uses it
    # to gate the auto-stack silencing; emit_bar_layer needs it regardless
    # of stack mode).
    x_is_banded = x_encoding_is_banded(x_enc)

    # Endpoint labels fire on a vertical bar only when the author opts in AND a
    # series color channel exists; they then take the right rail (so the y-axis
    # moves left) and replace the color legend. A series-colored bar WITHOUT
    # Measure axis (VL y): orient is baked at resolve time (ay.position is concrete).
    _measure_y_fields = (measure_field,) if measure_field else ()
    ay_vl = measure_axis_to_vl(ay, data, _measure_y_fields)
    ay_vl = compose_axis_label_expr(ay_vl, ay.ruler, ay)

    y_enc: dict[str, Any] = {
        "field": measure_field,
        "type": "quantitative",
        "title": y_title,
        "axis": ay_vl,
        "format": tooltip_format,
    }

    # tick_values are baked at resolve(); emitter reads them to build VL scale/axis.
    y_scale: dict[str, Any] = {}
    if chart.stack == "normalize":
        y_enc["stack"] = chart.stack
        # resolve() bakes ay.scale.continuous.domain to [0, 1] (or the
        # author's own pinned domain) for every normalize stack — route
        # through the shared measure-scale builder like every other
        # measure-channel emitter, so the domain is explicit on the VL spec
        # (a shared scale with an overlay layer's raw values, e.g. an
        # invisible padding layer, can't pull the rendered range away from
        # the percent axis) without re-deriving or overriding what resolve()
        # already decided.
        y_scale.update(resolve_measure_y_scale(ay))
        # Default quartile tick grid for 100%-normalize bars unless the author
        # supplied explicit axis.values (already in ay_vl via axis_to_vl).
        if "values" not in ay_vl:
            ay_vl["values"] = list(_NORMALIZE_QUARTILE_TICKS)
        pin_normalize_axis_format(ay_vl, ay)
    else:
        is_stacked = bool(measure_field) and chart.stack not in (None, "none")
        _ay_cont = ay.scale.continuous if ay.scale is not None else None
        ay_scale_zero = _ay_cont.zero if _ay_cont is not None else None
        bar_zero = not is_stacked and ay_scale_zero is not False
        authored_domain = authored_measure_domain(ay)
        y_ticks: list[float] | None = list(ay.tick_values) if ay.tick_values else None
        bake_tick_ladder(ay_vl, ay.tick_values)

        if authored_domain is not None:
            y_scale = {"domain": list(authored_domain)}
            if ay.scale is not None:
                y_scale.update(emit_resolved_scale_vl(ay.scale))
                # An authored domain already IS the exact bounds — "zero" is a
                # domain-inference hint, meaningless once the domain is pinned.
                y_scale.pop("zero", None)
        elif bar_zero:
            # Bar anchors on its own verdict (bar_zero), not on
            # is_zero_anchored, so this cannot route through y_zero_scale —
            # but the floor decision is the same one, and comes from the same
            # place. `nice: False` stands in when no rung supplies a floor:
            # the pin is what suppressed VL's default nice-rounding.
            # y_ticks[-1] still feeds the stacked domainMax below, so only the
            # floor is taken from the filtered ladder.
            pinned_floor = zero_anchor_pinned_floor(ay)
            y_scale = (
                {"domainMin": pinned_floor, "zero": True}
                if pinned_floor is not None
                else {"nice": False, "zero": True}
            )
            if ay.scale is not None:
                extra_scale = emit_resolved_scale_vl(ay.scale)
                extra_scale.pop("zero", None)
                y_scale.update(extra_scale)
        else:
            # Stacked vertical bar: bar_zero is False for a stack, so the
            # anchor verdict falls back to the axis's own is_zero_anchored.
            y_scale = y_zero_scale(ay)

        if measure_field and chart.stack not in (None, "none"):
            y_enc["stack"] = chart.stack
            # domainMax: baked stacked total (headroom already applied at
            # resolve) so the axis spans the full stacked height. Falls back
            # to nice-tick top when baked value absent.
            if authored_domain is None:
                if chart.stacked_domain_max is not None:
                    y_scale = {**y_scale, "domainMax": chart.stacked_domain_max}
                elif y_ticks:
                    y_scale = {**y_scale, "domainMax": y_ticks[-1]}
        elif authored_domain is None:
            # Non-stacked (single-series or grouped): headroom-applied bounds
            # from resolve() — matches build_cartesian_y_encoding in _cartesian.py.
            y_scale = apply_domain_headroom_bounds(
                y_scale, ay.domain_max, ay.domain_min
            )

        if chart.stack == "none" and not x_is_banded:
            # VL auto-stacks a bar mark whenever a discrete channel (color)
            # accompanies the quantitative measure, even with an xOffset
            # present and no aggregate — but only actually collapses
            # anything when x itself isn't already banded: xOffset already
            # disambiguates each x/color pair on a nominal/ordinal (or
            # bucketed-calendar temporal) x, so VL's auto-stack groups are
            # already size 1 there and the key would be a no-op. On a
            # genuinely continuous x, silence the default explicitly, or
            # grouped bars render against a domain sized for single values
            # while VL quietly sums them, clipping every bar to zero height.
            y_enc["stack"] = None

    if y_scale:
        y_enc["scale"] = y_scale

    encoding: dict[str, Any] = {"x": x_enc, "y": y_enc}

    transforms: list[dict[str, Any]] = (
        list(wide.transforms) if wide is not False else []
    )
    color_enc_type: str = "nominal"

    if wide is not False:
        encoding["color"] = wide.color
        if chart.stack == "none":
            encoding["xOffset"] = {"field": wide.label_field, "type": "nominal"}
        elif wide.order:
            encoding["order"] = wide.order
    elif color_ch is not None:
        color_field = color_ch.data_field
        color_title = (
            format_display_text(
                color_field, from_slug=True, font=chart.legend.title.font
            )
            if color_field
            else None
        )
        enc = channel_to_encoding(color_ch, data, title=color_title)
        color_enc_type = enc.get(
            "type", "nominal"
        )  # type-state: silent_fallback — literal-mode enc has no "type" key
        apply_color_legend(enc, chart.legend)
        encoding["color"] = enc
        if chart.stack == "none":
            # grouped columns use xOffset; skip when color is 1:1 with x —
            # that would create N solo sub-bands (razor-thin bars). xOffset
            # is emitted regardless of x's VL type (nominal/ordinal/
            # temporal/quantitative): it correctly groups a band-eligible
            # (nominal/ordinal, or bucketed-calendar temporal) x, and is
            # inert on a genuinely continuous x once y.stack: null (below)
            # is in place — omitting it there would silently un-group a
            # >max_ordinal_buckets bucketed-time x (type_inference.py
            # promotes that to temporal), which used to group correctly.
            if color_field and not _is_color_1to1_with_x(
                cat_field, color_field, dataset
            ):
                # xOffset always needs a discrete (band) scale to split each
                # x category into sub-bars — a quantitative or temporal
                # color type would give it a continuous scale instead, and
                # bandwidth('xOffset') then evaluates to 0
                # (ERR-CHART-PAINTED-NO-MARKS). The color paint scale can
                # legitimately be continuous; the offset scale never can.
                offset_enc_type = (
                    color_enc_type
                    if color_enc_type in ("nominal", "ordinal")
                    else "nominal"
                )
                encoding["xOffset"] = {
                    "field": color_field,
                    "type": offset_enc_type,
                    "title": color_title,
                }
            # Grouped bars have no display-order to pin (no stack, no
            # reorder) but a bound field still owes every value its board
            # slot's color — VL's own alphabetical default range would
            # otherwise paint this chart from its own local position, not
            # the board's.
            if categorical_color_encoding(color_ch, color_enc_type):
                series = distinct_series_values(data, color_field)
                # A grouped bar's legend already follows `scale.domain`
                # with no explicit pin, so only an AUTHORED list needs
                # resolving here.
                if chart.legend.values is not None:
                    apply_legend_entry_order(
                        enc,
                        series,
                        authored=chart.legend.values,
                    )
                # Ordinal columns carry their own inherent order (Vega
                # sorts the domain natively) -- the paint scale below
                # must never reorder or recolor one, so it stays
                # nominal-only.
                if color_enc_type == "nominal" and chart.palette:
                    scale = category_scale_for(chart.category_colors, color_field)
                    if scale is not None and series:
                        # xOffset shares this field's scale, so a full
                        # authored reorder must reorder scale.domain
                        # too, read independently of legend visibility
                        # (see _grouped_bar_paint_order).
                        order = _grouped_bar_paint_order(chart, series)
                        encoding["color"]["scale"] = spatial_color_scale(
                            series, chart.palette, order, scale
                        )
        elif (
            categorical_color_encoding(color_ch, color_enc_type)
            and color_enc_type == "nominal"
            and measure_field
            and chart.stack not in (None, "none")
        ):
            series = distinct_series_values(data, color_field)
            if series:
                # Baseline-first order (index 0 = bottom of stack).
                order = sorted_series_by_stack_order(
                    series,
                    data,
                    color_field,
                    chart.style.stack_order,
                    y_field=measure_field,
                )
                # `chart.palette` can legally be authored empty; it
                # governs the scale's range only, never the legend's
                # entry set, so resolution runs unconditionally. The
                # mark order below needs no palette either.
                display_order = list(reversed(order))
                apply_legend_entry_order(
                    encoding["color"],
                    display_order,
                    authored=chart.legend.values,
                )
                if chart.palette:
                    palette_order = (
                        series
                        if _is_color_1to1_with_x(cat_field, color_field, dataset)
                        else order
                    )
                    encoding["color"]["scale"] = spatial_color_scale(
                        palette_order,
                        chart.palette,
                        display_order,
                        category_scale_for(chart.category_colors, color_field),
                    )
                expr = series_order_expression(color_field, order)
                transforms.append({"calculate": expr, "as": _DF_SERIES_ORDER_KEY})
                encoding["order"] = {
                    "field": _DF_SERIES_ORDER_KEY,
                    "sort": "ascending",
                }
        elif (
            categorical_color_encoding(color_ch, color_enc_type)
            and color_enc_type == "ordinal"
            and measure_field
            and chart.stack not in (None, "none")
            and chart.legend.values is not None
        ):
            # See _emit_histogram's ordinal branch above: an ordinal
            # column's own inherent order must never be touched by
            # the stack-order computation, paint scale, or mark-order
            # transform -- only resolution runs.
            apply_legend_entry_order(
                encoding["color"],
                distinct_series_values(data, color_field),
                authored=chart.legend.values,
            )

    spec = emit_bar_layer(
        bar_mark,
        "vertical",
        wide is not False or color_ch is not None,
        single_series_color,
        radius,
        encoding,
        data,
        measure_field,
        config,
        transforms,
        x_is_banded,
        cat_field,
    )
    spec.base_series_label = titles.y_plain
    spec.x_label_block_height = label_layout.label_block_height
    return spec


def _emit_horizontal(
    chart: ResolvedBarChart,
    data: list[dict[str, Any]],
    tooltip_format: str,
    single_series_color: str,
    config: dict[str, Any],
    box: RenderBox,
    dataset: ChartDataset,
    wide: FoldedMeasures | Literal[False] = False,
) -> ChartSpec:
    """Emit a full horizontal bar spec matching the oracle."""
    bar_mark = chart.style.mark
    label_usable_ratio = chart.style.label_usable_ratio
    ax = chart.style.axis_x  # categorical cascade
    ay = chart.style.axis_y  # measure cascade
    cat_field = chart.x
    if cat_field is None:
        raise ValueError(
            "_emit_horizontal: chart.x must be set; "
            "chart must go through the resolve pipeline before emitting."
        )

    # style.axis_x.labels.values only has meaning against a temporal/ISO-bucketed
    # scale (the labelExpr membership filter build_cartesian_x_encoding wires
    # in for the vertical path). Horizontal bar's categorical axis is always
    # emitted as a flat VL "nominal" y-encoding (see y_enc below) — there is no
    # date-aware label path here for the filter to hook into, so honoring it
    # would be a no-op regardless of whether the values are ISO-shaped. Raise
    # the same coded error the vertical path raises for non-temporal x, rather
    # than silently dropping the authored filter. Unlike the vertical path's
    # guard, this fires unconditionally on ISO-ness (an explicit
    # orientation: horizontal override with genuinely ISO-shaped x data would
    # still be unable to honor labels.values), so the cause/remedy passed here
    # name the orientation, never claim the data itself is invalid.
    if ax.labels.values is not None:
        raise ChartDataError.from_code(
            ERR_LABEL_VALUES_NOT_TEMPORAL,
            field=cat_field,
            cause=(
                "a horizontal bar's categorical axis never honors it, "
                "regardless of date format"
            ),
            remedy="Set orientation: vertical, or remove labels.values.",
        )

    # Same shape, same axis: this axis_x is the categorical one (emitted as
    # the VL y encoding below), so there is nothing for a numeric cadence to
    # merge into — validate-only, with the remedy override apply_x_tick_cadence
    # documents. (ticks.time_unit is a calendar cadence rather than a numeric
    # one; this path has never gated it, and this branch does not change that.)
    apply_x_tick_cadence(
        None,
        ax,
        cat_field,
        "nominal",
        remedy=(
            "A horizontal bar's axis_x is its categorical axis — the measure "
            "is axis_y. Set orientation: vertical, or remove ticks.step."
        ),
    )
    # Same axis, same misreading: the rotation puts the categories on the
    # left edge, so a currency authored here paints $NaN over the labels.
    gate_label_format(
        ax.labels.format,
        cat_field,
        data,
        "nominal",
        setting="axis_x.labels.format",
        remedy=(
            "orientation: horizontal rotates the chart, it does not swap the "
            "channels — axis_x still addresses the categories. Author the "
            "format on style.axis_y.labels.format, the measure axis."
        ),
    )

    # VL x = measure axis.
    color_ch_early = chart.resolved_channels.get("color") if wide is False else None
    measure_field = (
        wide.value_field
        if wide is not False
        else chart.y
        if isinstance(chart.y, str)
        else None
    )
    radius = bar_mark_radius(bar_mark)

    # Measure axis (VL x): strip "orient" entirely — horizontal bar measure axis
    # has no orient property (VL x is always left-to-right).
    ay_vl = axis_to_vl(ay)
    ay_vl.pop("orient", None)
    # ay.ruler is already baked to REPEAT/narrative/no-reservation for this
    # horizontal measure axis (column_forming=False at resolve — see
    # build_resolved_axis's docstring); compose_axis_label_expr reads that
    # decision directly, no render-side orientation flag needed.
    ay_vl = compose_axis_label_expr(ay_vl, ay.ruler, ay)

    # Axes flip on a horizontal bar: the authored y (measure) renders on VL x
    # and the authored x (category) on VL y, so the authored labels are passed
    # in VL channel order and each is wrapped against the extent it lands on.
    # x_authored_field="y_label" tells the truncation recorder that the VL x
    # title came from the authored y_label key (so the squiggle lands correctly).
    y_label_effective = chart.y_label or (
        wide_measures_title(chart.wide_measures) if wide is not False else None
    )
    titles = resolve_xy_titles(
        measure_field if wide is False else None,
        cat_field,
        y_label_effective,
        chart.x_label,
        ay,
        ax,
        box,
        chart.id,
        x_authored_field="y_label",
        y_authored_field="x_label",
    )
    x_title, y_title = titles.x_title, titles.y_title
    # The measure channel here is VL x, not y — bake the same domain-spanning
    # tick ladder the vertical path bakes, or two bars sharing a domain would
    # read on different rulers depending on orientation alone.
    bake_tick_ladder(ay_vl, ay.tick_values)

    # Measure axis scale: anchored at zero by default; respects authored scale.zero.
    # Replicate V1: chart-level band_padding_inner (now baked into ax.scale via
    # _resolve_bar) propagates to the VL x encoding even when x is quantitative.
    # In Vega-Lite, paddingInner on a quantitative scale is a no-op visually, but
    # V1 emits it unconditionally via effective.scale applied to all channels,
    # and the existing tests assert it is present here.
    _ay_cont_h = ay.scale.continuous if ay.scale is not None else None
    _ay_scale_zero_h = _ay_cont_h.zero if _ay_cont_h is not None else None
    x_enc_scale: VLDict = {
        "zero": _ay_scale_zero_h if isinstance(_ay_scale_zero_h, bool) else True
    }
    if ay.scale is not None:
        extra_x_scale = emit_resolved_scale_vl(ay.scale)
        extra_x_scale.pop("zero", None)
        x_enc_scale.update(extra_x_scale)
    x_enc: dict[str, Any] = {
        "field": measure_field,
        "type": "quantitative",
        "title": x_title,
        "axis": ay_vl,
        "scale": x_enc_scale,
        "format": tooltip_format,
    }

    if chart.stack == "normalize":
        # Default quartile tick grid for 100%-normalize bars unless the author
        # supplied explicit axis.values (already in ay_vl via axis_to_vl).
        if "values" not in ay_vl:
            ay_vl["values"] = list(_NORMALIZE_QUARTILE_TICKS)
        pin_normalize_axis_format(ay_vl, ay)
    else:
        # domainMax: use baked stacked total; fall back to nice-tick top.
        authored_x_domain = authored_measure_domain(ay)
        if measure_field and chart.stack not in (None, "none"):
            if authored_x_domain is None:
                if chart.stacked_domain_max is not None:
                    x_enc["scale"]["domainMax"] = chart.stacked_domain_max
                elif ay.tick_values:
                    x_enc["scale"]["domainMax"] = ay.tick_values[-1]
        elif authored_x_domain is None:
            # Non-stacked (single-series or grouped) horizontal bar: the
            # measure axis is x here, but resolve() bakes headroom onto `ay`
            # (the semantic measure axis) regardless of visual orientation.
            x_enc["scale"] = apply_domain_headroom_bounds(
                x_enc["scale"], ay.domain_max, ay.domain_min
            )
            # Zero-anchored measure axis: pin the same floor the vertical
            # branch does, so a rung below the auto-fit domain (e.g. a
            # negative bar) is not silently clipped.
            #
            # Only when resolve baked no floor of its own. An all-negative
            # measure axis bakes ay.domain_min (the headroom-expanded data
            # floor, exact); the ladder's bottom rung is the nice-rounded FAR
            # edge, well below it, and overwriting the bake with that rung
            # widens the domain by ~30% — render re-deciding a domain resolve
            # already resolved. _emit_vertical avoids this by ordering the two
            # the other way round; this branch must check explicitly.
            pinned_floor = zero_anchor_pinned_floor(ay)
            if (
                pinned_floor is not None
                and ay.domain_min is None
                and x_enc["scale"].get("zero") is not False
            ):
                x_enc["scale"]["domainMin"] = pinned_floor
            # No `nice: False` companion here, unlike y_zero_scale and the
            # vertical branch. Those two pinned a literal 0.0 whenever the
            # ladder was empty, which suppressed VL's nice-rounding as a side
            # effect, so dropping that floor has to put `nice: False` back.
            # This branch never pinned a ladder-less floor: its domain has
            # always been nice-rounded, and it never had the degenerate-0.0
            # bug either. Adding the companion here changes shipped geometry
            # rather than preserving it (caught by the visual gate on
            # playground/dundersign-support-operations).

    # Categorical axis (VL y): axis_to_vl(ax) + orient from ay.position
    # (categorical_orient was deleted 2026-08 — position now serves this role
    # too; _resolve.py bakes it "left"-default here via _bake_ay_position_left,
    # matching the deleted field's static default, before computing x_edge).
    # Horizontal bar: categorical labels on VL y; is_horizontal_bar pins allow+angle=0.
    label_layout = resolve_axis_x_overlap(
        ax,
        cat_field,
        data,
        label_usable_ratio,
        is_horizontal_bar=True,
        edge_labels_flushed=False,
        chart_width=box.width,
    )
    ax_vl = axis_to_vl(
        ax,
        label_overlap=label_layout.label_overlap,
        label_angle=label_layout.angle,
    )
    if ay.position is not None:
        ax_vl["orient"] = ay.position
    # Own-side align (ax.labels.align resolved to the axis's own
    # orient side — either authored directly, or resolve-mapped from
    # charts.bar.axis_x.labels.align: inward) flips label growth back toward
    # the plot. Symmetric case to vl_field_maps.py's measure_axis_to_vl guard.
    cat_orient = ax_vl.get("orient")
    # Every row, not a capped sample: an under-measured gutter from a wide
    # label sitting past a sample cutoff would silently under-reserve space
    # and reintroduce the exact clip bug this task exists to fix.
    cat_labels = list(
        dict.fromkeys(
            str(row[cat_field]) for row in data if row.get(cat_field) is not None
        )
    )
    cat_align = ax_vl.get("labelAlign")
    # Measurability: exact per-row category text is always safe to measure
    # UNLESS a labelExpr is authored (VL renders it in preference to the raw
    # value — measuring the row value while a wider expr output actually
    # renders would silently under-reserve the gutter), or there's nothing to
    # measure (zero rows). No font.case exclusion here: horizontal bar's
    # categorical axis never runs inject_axis_label_case (that only wires
    # into build_cartesian_x_encoding's temporal/ordinal x path, which this
    # VL-y categorical axis doesn't go through), so labels.font.case has no
    # rendered effect to mismeasure against.
    if (
        cat_orient in ("left", "right")
        and cat_align == cat_orient
        and "labelExpr" not in ax_vl
        and cat_labels
    ):
        cat_padding = measured_label_padding(
            cat_labels, ax.labels.font.family, ax.labels.font.size
        )
        cat_label_limit = (
            ax.labels.max_width
            if ax.labels.max_width is not None
            else DEFAULT_VL_LABEL_LIMIT
        )
        ax_vl["labelPadding"] = cap_padding_to_label_limit(cat_padding, cat_label_limit)
    elif cat_orient in ("left", "right") and cat_align in (cat_orient, "center"):
        # Own-side align that can't be safely measured (no rows, or a
        # labelExpr override), or "center" (a bidirectional gutter no single
        # padding value fixes, regardless of measurability) — fall back to
        # Vega-Lite's away-from-plot default rather than erroring. Emptiness
        # and labelExpr are render-time facts resolve can't see when it maps
        # inward/outward, so the safety net lives here instead of rejecting
        # a valid render.
        del ax_vl["labelAlign"]

    color_ch = chart.resolved_channels.get("color") if wide is False else None

    y_scale: dict[str, Any] = {}
    if ax.scale is not None and ax.scale.padding is not None:
        y_scale["padding"] = ax.scale.padding
    y_scale["paddingInner"] = bar_mark.padding

    y_enc: dict[str, Any] = {
        "field": cat_field,
        "type": "nominal",
        "title": y_title,
        "axis": ax_vl,
        "scale": y_scale,
    }
    # An authored chart.sort wins on the categorical (y) axis. Otherwise a
    # horizontal bar WITHOUT a color channel defaults to largest-measure-first;
    # a series-colored or wide horizontal bar pins ``sort: null`` so VL keeps
    # the query's first-occurrence domain order (mirrors V1 _apply_chart_sort /
    # _apply_default_horizontal_bar_sort).
    y_sort = bar_sort_to_vl(
        chart.sort, measure_field, chart.stack not in (None, "none")
    )
    if y_sort is not None:
        y_enc["sort"] = y_sort
    elif measure_field and color_ch is None and wide is False:
        y_enc["sort"] = {"field": measure_field, "order": "descending"}
    else:
        y_enc["sort"] = None

    encoding: dict[str, Any] = {"x": x_enc, "y": y_enc}

    # Stacking: emit x.stack for stacked horizontal bars; "none" → grouped (yOffset).
    if chart.stack is not None and chart.stack != "none":
        x_enc["stack"] = chart.stack

    transforms: list[dict[str, Any]] = (
        list(wide.transforms) if wide is not False else []
    )
    color_enc_type_h: str = "nominal"
    if wide is not False:
        encoding["color"] = wide.color
        if chart.stack == "none":
            encoding["yOffset"] = {"field": wide.label_field, "type": "nominal"}
        elif wide.order:
            encoding["order"] = wide.order
    elif color_ch is not None:
        color_field_h = color_ch.data_field
        color_title_h = (
            format_display_text(
                color_field_h, from_slug=True, font=chart.legend.title.font
            )
            if color_field_h
            else None
        )
        enc = channel_to_encoding(color_ch, data, title=color_title_h)
        color_enc_type_h = enc.get(
            "type", "nominal"
        )  # type-state: silent_fallback — literal-mode enc has no "type" key
        apply_color_legend(enc, chart.legend)
        encoding["color"] = enc
        # stack="none" with a color channel → grouped columns (yOffset for horizontal).
        # Skip when color is 1:1 with x — that creates N solo sub-bands (thin bars).
        if chart.stack == "none":
            if color_field_h and not _is_color_1to1_with_x(
                cat_field, color_field_h, dataset
            ):
                # yOffset always needs a discrete (band) scale to split each
                # category into sub-bars — see the vertical branch above for
                # why a quantitative/temporal color type must not leak into it.
                offset_enc_type_h = (
                    color_enc_type_h
                    if color_enc_type_h in ("nominal", "ordinal")
                    else "nominal"
                )
                encoding["yOffset"] = {
                    "field": color_field_h,
                    "type": offset_enc_type_h,
                    "title": color_title_h,
                }
            # See the vertical branch above: a bound field still owes
            # every value its board slot's color, even with no
            # display-order to pin. Only an AUTHORED list needs
            # resolving -- Vega's own legend already follows
            # scale.domain for a grouped bar with no pin.
            if categorical_color_encoding(color_ch, color_enc_type_h):
                series = distinct_series_values(data, color_field_h)
                if chart.legend.values is not None:
                    apply_legend_entry_order(
                        enc,
                        series,
                        authored=chart.legend.values,
                    )
                # See the vertical branch above: an ordinal column's
                # paint-color scale stays nominal-only.
                if color_enc_type_h == "nominal" and chart.palette:
                    scale = category_scale_for(chart.category_colors, color_field_h)
                    if scale is not None and series:
                        # See the vertical branch above: a full
                        # authored reorder has to reorder scale.domain
                        # too, since the yOffset band shares this
                        # field's scale.
                        order = _grouped_bar_paint_order(chart, series)
                        encoding["color"]["scale"] = spatial_color_scale(
                            series, chart.palette, order, scale
                        )
        elif (
            categorical_color_encoding(color_ch, color_enc_type_h)
            and color_enc_type_h == "nominal"
            and measure_field
            and chart.stack not in (None, "none")
        ):
            series = distinct_series_values(data, color_field_h)
            if series:
                # Baseline-first order (index 0 = left edge for horizontal
                # bars) — the "left-first" convention IS baseline-first,
                # no reversal needed.
                order = sorted_series_by_stack_order(
                    series,
                    data,
                    color_field_h,
                    chart.style.stack_order,
                    y_field=measure_field,
                )
                # See the vertical branch above: `chart.palette` can
                # legally be authored empty, so resolution runs
                # unconditionally -- it governs `scale`'s range only.
                apply_legend_entry_order(
                    encoding["color"],
                    order,
                    authored=chart.legend.values,
                )
                if chart.palette:
                    palette_order = (
                        series
                        if _is_color_1to1_with_x(cat_field, color_field_h, dataset)
                        else order
                    )
                    encoding["color"]["scale"] = spatial_color_scale(
                        palette_order,
                        chart.palette,
                        order,
                        category_scale_for(chart.category_colors, color_field_h),
                    )
                expr = series_order_expression(color_field_h, order)
                transforms.append({"calculate": expr, "as": _DF_SERIES_ORDER_KEY})
                encoding["order"] = {
                    "field": _DF_SERIES_ORDER_KEY,
                    "sort": "ascending",
                }
        elif (
            categorical_color_encoding(color_ch, color_enc_type_h)
            and color_enc_type_h == "ordinal"
            and measure_field
            and chart.stack not in (None, "none")
            and chart.legend.values is not None
        ):
            # See the vertical stacked branch above: an ordinal
            # column's own inherent order must never be touched by
            # the stack-order sort, paint scale or mark-order
            # transform -- only resolution runs, so an authored
            # `legend.values` entry doesn't ship as a phantom swatch.
            apply_legend_entry_order(
                encoding["color"],
                distinct_series_values(data, color_field_h),
                authored=chart.legend.values,
            )

    spec = emit_bar_layer(
        bar_mark,
        "horizontal",
        wide is not False or color_ch_early is not None,
        single_series_color,
        radius,
        encoding,
        data,
        measure_field,
        config,
        transforms,
        # Horizontal bar's categorical axis is always emitted nominal (see
        # y_enc above) — there is no continuous-category horizontal shape.
        True,
        cat_field,
    )
    # The base series' legend label is its MEASURE, which this orientation
    # draws on VL x — `y_plain` here is the category title.
    spec.base_series_label = titles.x_plain
    return spec
