"""StructuredTooltipFeature — chart-axes LUT-driven aria-label content.

Runs after emission for line/area/bar/pie/donut/scatter/heatmap and sets
``ChartSpec.tooltip_description``, the per-datum expression string
``assemble_final_vl`` wires to VL's native ``description`` channel as a
``{"value": {"expr": ...}}`` def. See ``emitters/_tooltip.py`` for why
``description`` (not an appended ``encoding.tooltip`` array) is the single
mechanism used across every family, and why it's wired as a value/expr def
rather than a field bound through a ``calculate`` transform.

Charts with an authored ``chart.layers`` overlay (combo/dual-axis) DO get
structured content, one bubble per hovered x -- the base's own parts (and
group total, when commensurable) first, then each overlay reference last, as
the thing the parts are being compared against. The base's own role content
is computed exactly as the non-combo case below, but wired onto the BASE's OWN
sub-spec (``spec.layers[0]``, or the entry it wraps; see
``layer_encoding_owner``), not the outer/shared encoding. A shared
top-level description is unreliable across sibling layers (e.g. one
gap-filled, another not; see ``_apply_structured_tooltip``'s private-data
guard), so each layer gets its own description set directly.
Each overlay layer's OWN content is built independently in
``emitters/_overlay.py::render_cartesian_overlay`` (mirrors this module's
role-building, reusing the same LUT/builder primitives) and wired the same
way. ``chart_interactivity.js``'s existing x-unified grouping
(``collectMatchingMarks``) then folds base + overlay marks sharing one x into
one bubble with no JS changes.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, replace
from typing import Any

from dbt_charts.core.compile.models.chart.resolved import ResolvedChart
from dbt_charts.core.compile.models.chart.resolved._layer import (
    ResolvedBarLayer,
    ResolvedLayer,
)
from dbt_charts.core.compile.models.chart.resolved.area import ResolvedAreaChart
from dbt_charts.core.compile.models.chart.resolved.bar import ResolvedBarChart
from dbt_charts.core.compile.models.chart.resolved.heatmap import ResolvedHeatmapChart
from dbt_charts.core.compile.models.chart.resolved.line import ResolvedLineChart
from dbt_charts.core.compile.models.chart.resolved.pie import ResolvedPieChart
from dbt_charts.core.compile.models.chart.resolved.scatter import ResolvedScatterChart
from dbt_charts.core.compile.resolve.chart._wide_fields import (
    WIDE_LABEL_FIELD,
    WIDE_VALUE_FIELD,
)
from dbt_charts.core.render.chart._types import VLDict
from dbt_charts.core.render.chart.artifacts import ChartRenderData
from dbt_charts.core.render.chart.emitters._cartesian import (
    layer_encoding_owner,
    series_order_expression,
    wide_measures_title,
)
from dbt_charts.core.render.chart.emitters._tooltip import (
    SPAN_DATE_FORMAT,
    SWATCH,
    TooltipField,
    build_structured_tooltip_expr,
    header_tooltip_field,
    series_row_promoted,
    span_tooltip_rows,
)
from dbt_charts.core.render.chart.emitters.pie import PIE_PCT_FIELD, PIE_TOTAL_FIELD
from dbt_charts.core.render.chart.feature import chart_rows
from dbt_charts.core.render.chart.spec import ChartSpec, RenderBox
from dbt_charts.core.render.chart.time_unit_detect import normalize_labeled_temporal
from dbt_charts.core.render.chart.type_inference import infer_vega_type_from_data
from dbt_charts.core.text.case import default_axis_title, format_display_text

_SeriesCartesianChart = ResolvedBarChart | ResolvedLineChart | ResolvedAreaChart
_NormalizableChart = ResolvedBarChart | ResolvedAreaChart

_CHART_TYPE_LUT_KEY: dict[type[_SeriesCartesianChart], str] = {
    ResolvedBarChart: "bar",
    ResolvedLineChart: "line",
    ResolvedAreaChart: "area",
}

# Percent-of-total tooltip rows always use a whole-percent d3-format — this is
# a fixed tooltip convention, not a themeable presentation value.
_PERCENT_TOOLTIP_FORMAT = ".0%"

# Fields added by _stack_group_total to a stacked bar/area's own data
# pipeline (server-side joinaggregate + calculate — VL's stack:normalize/zero
# only affects the plotted position, it exposes no datum field for either
# number).
_GROUP_TOTAL_FIELD = "__dct_group_total"
_GROUP_PCT_FIELD = "__dct_group_pct"


def _value_tooltip_field(
    field: str, title: str, data: ChartRenderData, fmt: str
) -> TooltipField:
    """One dependent/peer VALUE row honoring the datum's inferred VL type.

    Quantitative applies the theme's ``tooltip_format``; temporal/nominal
    pass the raw value through. Builds scatter's y row (`_scatter_roles`),
    which can be a measure, a date, or a category (dot-plot).
    """
    field_type = infer_vega_type_from_data(data, field)
    if field_type == "quantitative":
        return TooltipField(field, title, kind="quantitative", format=fmt)
    if field_type == "temporal":
        return TooltipField(field, title, kind="temporal")
    return TooltipField(field, title, kind="nominal")


# Calculate-transform field carrying each row's precomputed display-rank --
# read from `scale.domain`, the paint order each emitter already computed,
# never re-derived. An authored `legend.values` reorders the rendered
# legend only, not `scale.domain`, so it does not affect this rank --
# except on a grouped bar, the one documented case where a full authored
# reorder also reorders `scale.domain`.
_TOOLTIP_ORDER_FIELD = "__dct_tooltip_order"


def _series_order_role(spec: ChartSpec, color_field: str) -> tuple[TooltipField, ...]:
    """Bake the color scale's already-reordered domain into a per-datum rank.

    Stamps a ``calculate`` transform + returns an invisible ROLE_ORDER
    tooltip entry, so ``chart_interactivity.js`` can sort the x-unified
    bubble by rank even when no legend renders (an endpoint-labeled line,
    the common default, has no ``.role-legend-label`` DOM for the runtime's
    other order-reading path). Absent (``()``) when the color scale carries
    no explicit domain -- a numeric/boolean color field -- leaving nothing to
    rank by.

    A domain that happens to equal its own sorted order still earns a rank.
    Skipping those looks tempting and is wrong: the runtime's only fallback is
    legend DOM order, and the charts most likely to sort alphabetically by
    coincidence are the endpoint-labeled ones that render no legend at all.
    """
    color_enc = spec.encoding.get("color") if spec.encoding else None
    scale = color_enc.get("scale") if isinstance(color_enc, dict) else None
    domain = scale.get("domain") if isinstance(scale, dict) else None
    if not isinstance(domain, list) or not domain:
        return ()
    expr = series_order_expression(color_field, domain)
    spec.transforms = [
        *spec.transforms,
        {"calculate": expr, "as": _TOOLTIP_ORDER_FIELD},
    ]
    return (TooltipField(_TOOLTIP_ORDER_FIELD, ""),)


def _cartesian_roles(
    chart: _SeriesCartesianChart, data: ChartRenderData, base_spec: ChartSpec
) -> tuple[
    tuple[TooltipField, ...],
    tuple[TooltipField, ...],
    list[TooltipField],
    tuple[TooltipField, ...],
]:
    """Header (chart.x), series (color, iff promoted), values (chart.y), order (rank).

    Header/series/order are 0-or-1-element tuples (absent vs. present) — see
    ``build_structured_tooltip_expr``.
    """
    assert isinstance(chart.y, str)  # narrowed by applies_to
    style = chart.style

    header: tuple[TooltipField, ...] = ()
    if chart.x is not None:
        x_title = chart.x_label or default_axis_title(chart.x)
        # The base emitter (bar/line/area) already ran this canonicalization
        # on its OWN copy of chart.x before deciding the x encoding's type and
        # grain -- a year-shaped integer column becomes an ISO date, which VL
        # then encodes as temporal and coerces every runtime reference to
        # (our own description expr's datum lookup included) to epoch-ms.
        # StructuredTooltipFeature reads a FRESH, unmutated copy of the rows
        # (see the module docstring), so the header must run the same
        # canonicalization or its kind decision diverges from what the field
        # actually evaluates to at render time.
        #
        # This CAN raise on mixed label families. It doesn't here because
        # `applies_to` excludes histogram -- the one bar branch that returns
        # ahead of its own normalize call. Line and area normalize
        # unconditionally, so the emitter made this identical call on these
        # identical values first and any raise already escaped before features
        # run. Drop that histogram gate and this line becomes a crash inside a
        # tooltip feature.
        header_data = normalize_labeled_temporal(data, chart.x)
        header = (header_tooltip_field(chart.x, x_title, header_data),)

    # Wide charts (y: [m1, m2, ...]) are folded client-side by VL: at render
    # time datum[WIDE_VALUE_FIELD] = the measure value and
    # datum[WIDE_LABEL_FIELD] = the measure name. Use a human title for the
    # value row and force the series row rather than probing pre-fold rows.
    if chart.wide_measures:
        y_title = chart.y_label or wide_measures_title(chart.wide_measures)
        values: list[TooltipField] = [
            TooltipField(
                WIDE_VALUE_FIELD,
                y_title,
                kind="quantitative",
                format=style.tooltip_format,
            )
        ]
        series: tuple[TooltipField, ...] = (TooltipField(WIDE_LABEL_FIELD, "Series"),)
        order = _series_order_role(base_spec, WIDE_LABEL_FIELD)
        return header, series, values, order

    y_title = chart.y_label or default_axis_title(chart.y)
    values = [
        TooltipField(chart.y, y_title, kind="quantitative", format=style.tooltip_format)
    ]

    series = ()
    order = ()
    color_ch = chart.resolved_channels.get("color")
    if (
        color_ch is not None
        and color_ch.mode == "series"
        and color_ch.data_field
        and series_row_promoted(color_ch.data_field, data)
    ):
        series_title = format_display_text(
            color_ch.data_field, from_slug=True, font=style.axis_x.title.font
        )
        series = (TooltipField(color_ch.data_field, series_title),)
        order = _series_order_role(base_spec, color_ch.data_field)

    return header, series, values, order


def _layer_name(layer: ResolvedLayer) -> str:
    """The name a layer goes by in the bubble: the overlay label cascade."""
    assert layer.y is not None
    return layer.label if layer.label is not None else default_axis_title(layer.y)


def _shares_rows(layer: ResolvedLayer, chart: ResolvedBarChart) -> bool:
    return layer.y is not None and layer.query_name in (None, chart.query_name)


def _span_values(
    chart: ResolvedBarChart, data: ChartRenderData, end: TooltipField
) -> tuple[list[TooltipField], list[VLDict]]:
    """Value rows, and the transforms they read, for a bar with y_start.

    Layers plotting from the same rows join one block with the bar, since
    together they describe one category: each layer's value under its own
    name first, then the bar's ends and their difference, never repeating an
    end a layer already names (a dumbbell: 2019, 2024, change; a ranged dot:
    value, low, high, range). A bar layer with its own y_start nested inside
    the bar reads outer and inner ends in the order finance tools use for a
    candlestick: open, high, low, close, then the inner change.
    """
    assert chart.y is not None and chart.y_start is not None
    if chart.measure_type == "temporal":
        end = TooltipField(chart.y, end.title, kind="temporal", format=SPAN_DATE_FORMAT)
    start = replace(end, field=chart.y_start, title=default_axis_title(chart.y_start))
    layers = [layer for layer in chart.layers if _shares_rows(layer, chart)]
    inner = next(
        (
            layer
            for layer in layers
            if isinstance(layer, ResolvedBarLayer) and layer.y_start is not None
        ),
        None,
    )
    named = [
        replace(end, field=layer.y, title=_layer_name(layer))
        for layer in layers
        if layer is not inner and layer.y is not None
    ]
    if inner is not None:
        assert inner.y is not None and inner.y_start is not None
        inner_end = replace(end, field=inner.y, title=default_axis_title(inner.y))
        inner_start = replace(
            end, field=inner.y_start, title=default_axis_title(inner.y_start)
        )
        change, transforms = span_tooltip_rows(
            inner_end, inner_start, frozenset({inner.y, inner.y_start}), data
        )
        return [*named, inner_start, end, start, inner_end, *change], transforms
    rows, transforms = span_tooltip_rows(
        end, start, frozenset(tf.field for tf in named), data
    )
    return [*named, *rows], transforms


def _with_swatches(
    base_spec: ChartSpec, values: list[TooltipField], named: frozenset[str]
) -> list[TooltipField]:
    """Key each row of a combined block to the mark it describes.

    Each layer's rows take that layer's paint; the bar's own rows (its ends
    and their difference) take the bar's. Both come off the shared color scale
    the overlay built. A block with no layer rows (a candlestick's nested
    bars) keeps none: its rows all describe one mark. Nor does a block with a
    layer split into colors of its own: no one swatch describes that layer.
    """
    enc = base_spec.encoding.get("color")
    if not named or not isinstance(enc, dict) or "datum" not in enc:
        return values
    scale = enc["scale"]
    paint = dict(zip(scale["domain"], scale["range"], strict=True))
    if not named <= paint.keys():
        return values
    bar_paint = paint[enc["datum"]]
    return [
        replace(tf, swatch=paint[tf.title] if tf.title in named else bar_paint)
        for tf in values
    ]


_LAYER_SERIES_FIELD = "__dct_layer_series"


def _layer_series(
    chart: ResolvedBarChart, data: ChartRenderData, base_spec: ChartSpec
) -> tuple[tuple[TooltipField, ...], list[VLDict]]:
    """A series row borrowed from a layer's color split, for a block whose bar
    has none (an ink-outlined candlestick: only the body says up or down).

    Its swatch is the layer's paint for that row's value, carried in the label
    (SWATCH-bracketed): the hovered mark may be the uncolored bar.
    """
    enc = base_spec.encoding.get("color")
    if not isinstance(enc, dict) or "scale" not in enc:
        return (), []
    paint = dict(zip(enc["scale"]["domain"], enc["scale"]["range"], strict=True))
    for layer in chart.layers:
        if layer.color is None or not series_row_promoted(layer.color, data):
            continue
        ref = f"datum[{json.dumps(layer.color)}]"
        swatch = " : ".join(
            f"{ref} == {json.dumps(value)} ? {json.dumps(SWATCH + paint[value] + SWATCH)}"
            for value in dict.fromkeys(row[layer.color] for row in data)
            if value in paint
        )
        if not swatch:
            continue
        title = format_display_text(
            layer.color, from_slug=True, font=chart.style.axis_x.title.font
        )
        return (
            (TooltipField(_LAYER_SERIES_FIELD, title),),
            [{"calculate": f"({swatch} : '') + {ref}", "as": _LAYER_SERIES_FIELD}],
        )
    return (), []


def _stamp_descriptions(
    spec: ChartSpec, description: str, transforms: list[VLDict]
) -> None:
    """Give every data mark in a layered spec the same description, so a hover
    on any of them opens the one block (the runtime folds identical marks).

    The block's fields are computed on the shared outer data; a mark carrying
    its own copy of the rows (a gap-filled base) computes them itself.
    """
    for layer in spec.layers:
        if layer.tooltip_description is not None:
            layer.tooltip_description = description
            if layer.data is not None:
                layer.transforms = [*layer.transforms, *transforms]
        _stamp_descriptions(layer, description, transforms)


def _has_negative_value(data: ChartRenderData, field: str) -> bool:
    """True when any row's ``field`` value is negative.

    Guards the normalized-stack %+total fallback: percent-of-total and the
    group sum are both ill-defined once a segment can subtract from the
    group rather than only add to it (a negative "adjustment" segment can
    make the total smaller than some individual positive segment, so its
    "share" would exceed 100% or flip sign) -- raw-only is the honest
    fallback, never a confidently-wrong percentage.
    """
    return any(
        isinstance(value, (int, float)) and not isinstance(value, bool) and value < 0
        for value in (row.get(field) for row in data)
    )


def _stack_group_total(
    spec: ChartSpec,
    chart: _NormalizableChart,
    values: list[TooltipField],
    with_percent: bool,
) -> tuple[list[TooltipField], tuple[TooltipField, ...]]:
    """Group-total footer for a stacked bar/area, +percent when normalized.

    Total legal <=> commensurable and additive: both a 100%-normalized stack
    (``chart.stack == "normalize"``) and an absolute stack (``"zero"``) sum
    same-unit segments, so both get the group-total footer via the same
    server-side joinaggregate, grouped by the chart's independent field
    (``chart.x`` stays independent regardless of orientation — see the LUT
    module docstring). Only the normalized case also gets the percent-of-total
    row (raw segments alone don't imply a meaningful share without the
    normalization); an absolute stack keeps its plain raw value row plus the
    new Total footer. Grouped ("none") bars are out of scope here.
    """
    assert isinstance(chart.x, str)  # narrowed by applies_to (header requires chart.x)
    y_field = chart.y
    assert isinstance(y_field, str)  # narrowed by applies_to
    spec.transforms = [
        *spec.transforms,
        {
            "joinaggregate": [
                {"op": "sum", "field": y_field, "as": _GROUP_TOTAL_FIELD}
            ],
            "groupby": [chart.x],
        },
    ]
    total_field = TooltipField(
        _GROUP_TOTAL_FIELD,
        "Total",
        kind="quantitative",
        format=chart.style.tooltip_format,
    )
    if not with_percent:
        return values, (total_field,)
    spec.transforms = [
        *spec.transforms,
        {
            "calculate": (
                f"datum[{json.dumps(y_field)}] / datum[{json.dumps(_GROUP_TOTAL_FIELD)}]"
            ),
            "as": _GROUP_PCT_FIELD,
        },
    ]
    pct_field = TooltipField(
        _GROUP_PCT_FIELD, "Share", kind="quantitative", format=_PERCENT_TOOLTIP_FORMAT
    )
    return [pct_field, *values], (total_field,)


def _pie_roles(
    chart: ResolvedPieChart,
) -> tuple[tuple[TooltipField, ...], list[TooltipField], tuple[TooltipField, ...]]:
    """Header (chart.identity_field), values (share % then raw), total (grand
    total) for a pie/donut.

    ``__dbt_pct``/``__dbt_total`` are already baked into every row by
    ``PieEmitter``'s ``_augment_pie_data`` (the same fields drive the donut
    center-total display) — no new transform needed here, unlike the
    cartesian normalized-stack case.
    """
    font = chart.legend.title.font
    header: tuple[TooltipField, ...] = ()
    if chart.identity_field is not None:
        header = (
            TooltipField(
                chart.identity_field,
                format_display_text(chart.identity_field, from_slug=True, font=font),
            ),
        )
    value_title = format_display_text(chart.theta, from_slug=True, font=font)
    fmt = chart.style.tooltip_format
    # The share % leads on a pie; the raw slice count and the grand total are
    # context, so both render muted (low-contrast). See MUTED in _tooltip.py.
    values = [
        TooltipField(
            PIE_PCT_FIELD, "Share", kind="quantitative", format=_PERCENT_TOOLTIP_FORMAT
        ),
        TooltipField(
            chart.theta, value_title, kind="quantitative", format=fmt, muted=True
        ),
    ]
    total = (
        TooltipField(
            PIE_TOTAL_FIELD, "Total", kind="quantitative", format=fmt, muted=True
        ),
    )
    return header, values, total


def _dot_plot_roles(
    chart: ResolvedScatterChart, data: ChartRenderData, spec: ChartSpec
) -> (
    tuple[
        tuple[TooltipField, ...],
        tuple[TooltipField, ...],
        list[TooltipField],
        tuple[TooltipField, ...],
    ]
    | None
):
    """Header (the category), series (the color), value, order for a dot plot.

    A scatter with one category axis, one measure axis and a color split is a
    dot plot: each category's dots are one row of the chart, so a hover reads
    them together, the way a grouped bar's does. The category heads the
    bubble, each series follows under its color in the color scale's order
    (a time series in time order), and the measure is the value. None for any
    other scatter shape.
    """
    assert isinstance(chart.x, str) and isinstance(chart.y, str)
    color_ch = chart.resolved_channels.get("color")
    if color_ch is None or not color_ch.data_field:
        return None
    kinds = {axis: infer_vega_type_from_data(data, axis) for axis in (chart.x, chart.y)}
    categories = [axis for axis, kind in kinds.items() if kind == "nominal"]
    measures = [axis for axis, kind in kinds.items() if kind == "quantitative"]
    if len(categories) != 1 or len(measures) != 1:
        return None
    (category,), (measure,) = categories, measures
    style = chart.style
    title = chart.x_label if measure == chart.x else chart.y_label
    header = (TooltipField(category, default_axis_title(category)),)
    series_title = format_display_text(
        color_ch.data_field, from_slug=True, font=style.axis_x.title.font
    )
    series = (TooltipField(color_ch.data_field, series_title),)
    value = TooltipField(
        measure,
        title if title is not None else default_axis_title(measure),
        kind="quantitative",
        format=style.tooltip_format,
    )
    return header, series, [value], _series_order_role(spec, color_ch.data_field)


def _scatter_roles(
    chart: ResolvedScatterChart, data: ChartRenderData
) -> tuple[str, tuple[TooltipField, ...], list[TooltipField]]:
    """LUT key + header + values (y then x, both peer rows) for scatter.

    Locked design's screen-informed exception: y (dependent) leads, x
    (independent) follows -- neither axis alone identifies a scatter point.
    Plain scatter (no color) carries no header at all. A bound color
    channel promotes its value to a swatched header-like row (mirrors pie's
    header_is_swatched -- the header IS the series here), so the LUT key
    switches to "scatter_colored". Per the locked table this promotion is
    "color if present" -- no cardinality gate, unlike cartesian's series row.

    "Connected" scatter (an order/time field leading as the header) is a
    third locked variant, but scatter has no authored order/time channel to
    read one from -- deferred rather than inventing authored surface.
    """
    assert isinstance(chart.x, str)  # narrowed by applies_to
    assert isinstance(chart.y, str)  # narrowed by applies_to
    style = chart.style
    fmt = style.tooltip_format
    x_title = chart.x_label or default_axis_title(chart.x)
    y_title = chart.y_label or default_axis_title(chart.y)
    # Mirrors the scatter emitter's x gate, decided on the same raw rows: a
    # quantitative x stays a measure (canonicalizing it would misread a
    # 1900-2100 numeric column as years); anything else the emitter runs
    # through normalize_labeled_temporal before encoding, so Vega coerces a
    # labeled bucket ("Q1 2024") to epoch-ms. The row must format that same
    # canonical value with its grain, as the cartesian header does. The call
    # cannot raise here: the emitter made it on these identical values first,
    # under the identical gate, and `applies_to` excludes layers and multiples.
    if infer_vega_type_from_data(data, chart.x) == "quantitative":
        x_row = TooltipField(chart.x, x_title, kind="quantitative", format=fmt)
    else:
        x_data = normalize_labeled_temporal(data, chart.x)
        x_row = header_tooltip_field(chart.x, x_title, x_data)
    values = [_value_tooltip_field(chart.y, y_title, data, fmt), x_row]

    color_ch = chart.resolved_channels.get("color")
    if color_ch is not None and color_ch.data_field:
        series_title = format_display_text(
            color_ch.data_field, from_slug=True, font=style.axis_x.title.font
        )
        header = (TooltipField(color_ch.data_field, series_title),)
        return "scatter_colored", header, values
    return "scatter", (), values


def _heatmap_roles(
    chart: ResolvedHeatmapChart, data: ChartRenderData
) -> tuple[tuple[TooltipField, ...], list[TooltipField]]:
    """Header (compound [x, y] identity pair), values (the color measure).

    Heatmap's two independents (x, y) TOGETHER identify the cell -- neither
    alone does -- so both header as a pair; the color-encoded value is the
    sole dependent row. No series row (color IS the dependent, not an
    identity dimension) and no total (a grid of cells has no group to sum).
    """
    assert isinstance(chart.x, str)  # narrowed by applies_to
    assert isinstance(chart.y, str)  # narrowed by applies_to
    style = chart.style
    x_title = chart.x_label or default_axis_title(chart.x)
    y_title = chart.y_label or default_axis_title(chart.y)
    header = (
        header_tooltip_field(chart.x, x_title, data),
        header_tooltip_field(chart.y, y_title, data),
    )
    color_ch = chart.resolved_channels.get("color")
    assert color_ch is not None and color_ch.data_field  # heatmap's value channel
    value_title = format_display_text(
        color_ch.data_field, from_slug=True, font=chart.legend.title.font
    )
    values = [
        TooltipField(
            color_ch.data_field,
            value_title,
            kind="quantitative",
            format=style.tooltip_format,
        )
    ]
    return header, values


@dataclass
class StructuredTooltipFeature:
    """Sets ChartSpec.tooltip_description for structured-tooltip chart families."""

    def applies_to(self, chart: ResolvedChart) -> bool:
        # Faceted (small-multiples) charts render every panel inside ONE
        # .dbt-chart. The JS x-unified grouping scopes to .dbt-chart and its
        # dedup key omits the facet field, so a structured (grouping-eligible)
        # description would collect same-(header, series) marks from sibling
        # panels and show another panel's values on hover. `chart.multiples is
        # None` gates faceted charts back to the per-mark tooltip.
        # (`multiples` lives on the cartesian families only, hence the
        # per-branch check rather than a top-level one.) `chart.layers`
        # (combo) is NOT excluded — see the module docstring; the base's role
        # content is still computed below, just wired onto its own sub-spec.
        if isinstance(chart, (ResolvedBarChart, ResolvedLineChart, ResolvedAreaChart)):
            return (
                chart.chart_type != "histogram"
                and isinstance(chart.y, str)
                and chart.multiples is None
            )
        if isinstance(chart, ResolvedScatterChart):
            # A wide (multi-y) scatter's chart.y is the synthetic
            # WIDE_VALUE_FIELD fold column, absent from raw rows -- unlike
            # bar/line/area's own wide branch above, _scatter_roles below has
            # no fold-aware variant, so a wide scatter falls back to the
            # plain per-mark VL tooltip instead of reading a field that isn't
            # there.
            return (
                isinstance(chart.x, str)
                and isinstance(chart.y, str)
                and not chart.wide_measures
                and not chart.layers
                and chart.multiples is None
            )
        if isinstance(chart, ResolvedHeatmapChart):
            color_ch = chart.resolved_channels.get("color")
            return (
                isinstance(chart.x, str)
                and isinstance(chart.y, str)
                and color_ch is not None
                and bool(color_ch.data_field)
                and chart.multiples is None
            )
        return isinstance(chart, ResolvedPieChart)

    def apply(
        self,
        spec: ChartSpec,
        chart: ResolvedChart,
        box: RenderBox,
        datasets: dict[str | None, list[dict[str, Any]]],
    ) -> ChartSpec:
        if isinstance(chart, (ResolvedBarChart, ResolvedLineChart, ResolvedAreaChart)):
            # applies_to() gates every branch here on `chart.multiples is
            # None` (structured tooltips and multiples are mutually
            # exclusive — the JS dedup key omits the facet field), so
            # .all_rows() is always the whole, single-panel dataset.
            assert isinstance(chart.y, str)  # narrowed by applies_to
            data = chart_rows(chart, datasets).all_rows()
            # Combo (chart.layers set): the base's own role content is wired
            # onto its OWN sub-spec (spec.layers[0]), never the outer/shared
            # encoding -- see the module docstring. render_cartesian_overlay
            # always puts the base spec first in spec.layers, but a dual-axis
            # base's own zero rule may have wrapped it in an extra
            # `mark="layered"` level with no encoding of its own -- unwrap to
            # the real owner.
            base_spec = layer_encoding_owner(spec.layers[0]) if chart.layers else spec
            header, series, values, order = _cartesian_roles(chart, data, base_spec)
            plain_values = values
            is_span = isinstance(chart, ResolvedBarChart) and chart.y_start is not None
            if is_span:
                assert isinstance(chart, ResolvedBarChart)
                values, span_transforms = _span_values(chart, data, values[0])
                if chart.layers and all(
                    _shares_rows(lay, chart) for lay in chart.layers
                ):
                    named = frozenset(
                        _layer_name(lay)
                        for lay in chart.layers
                        if not (
                            isinstance(lay, ResolvedBarLayer)
                            and lay.y_start is not None
                        )
                    )
                    values = _with_swatches(base_spec, values, named)
                    if not series:
                        series, series_transforms = _layer_series(
                            chart, data, base_spec
                        )
                        span_transforms = [*span_transforms, *series_transforms]
                    spec.transforms = [*spec.transforms, *span_transforms]
                    base_spec.tooltip_description = build_structured_tooltip_expr(
                        "bar", header, series, values
                    )
                    _stamp_descriptions(
                        spec, base_spec.tooltip_description, span_transforms
                    )
                    return spec
                base_spec.transforms = [*base_spec.transforms, *span_transforms]
            if chart.layers and not series:
                # A single-series base has no series row today (no color
                # channel to promote) -- but chart_interactivity.js's
                # collectMatchingMarks dedups/sorts x-unified rows by
                # (header, series); with no series row the base's own match
                # would collide with the overlay's (also series-less)
                # dedup key and one would be silently dropped. Synthesize a
                # literal identity row from the base's own y-title (the same
                # title `_cartesian_roles` already gave the value row) so the
                # two dedup keys (and legend-order ranks) stay distinct.
                # Combo-only: never fires for a non-combo single-series chart.
                base_label = values[0].title
                series = (TooltipField(base_label, base_label, literal=True),)
            total: tuple[TooltipField, ...] = ()
            # For wide charts, check all authored measure columns; WIDE_VALUE_FIELD
            # does not exist in pre-fold rows so _has_negative_value would miss it.
            _wide_negative = (
                any(_has_negative_value(data, f) for f in chart.wide_measures)
                if isinstance(chart, (ResolvedBarChart, ResolvedAreaChart))
                and chart.wide_measures
                else _has_negative_value(data, chart.y)
            )
            if (
                isinstance(chart, (ResolvedBarChart, ResolvedAreaChart))
                and chart.stack in ("normalize", "zero")
                and not _wide_negative
            ):
                # A combo's x-unified bubble is a single shared grid across
                # every matched row (base segments + each overlay's own
                # single-value row) -- chart_interactivity.js decides once,
                # for the WHOLE bubble, whether a percent column exists, from
                # whether any matched row carries 2 values. An overlay row
                # only ever carries 1 (its own value, no percent), so if the
                # base's normalized stack added its own percent row, the grid
                # would misinterpret the overlay's lone value as the percent
                # column and leave its own value column blank. Combo suppresses
                # the percent row entirely (keeps the raw value + Total footer)
                # rather than mixing a 2-value and a 1-value row shape in one
                # grid -- the overlay reference was never part of the percent
                # calculation anyway.
                values, total = _stack_group_total(
                    base_spec,
                    chart,
                    values,
                    chart.stack == "normalize" and not chart.layers,
                )
            lut_key = _CHART_TYPE_LUT_KEY[type(chart)]
            description = build_structured_tooltip_expr(
                lut_key, header, series, values, total, order
            )
            if (
                is_span
                and isinstance(chart, ResolvedBarChart)
                and chart.measure_type == "quantitative"
            ):
                # A row that starts at zero is a plain bar (a waterfall's
                # totals): its value, not a start, an end and a difference.
                plain = build_structured_tooltip_expr(
                    lut_key, header, series, plain_values, total, order
                )
                description = f"(datum[{json.dumps(chart.y_start)}] == 0 ? {plain} : {description})"
            base_spec.tooltip_description = description
        elif isinstance(chart, ResolvedScatterChart):
            data = chart_rows(chart, datasets).all_rows()
            dot_plot = _dot_plot_roles(chart, data, spec)
            if dot_plot is not None:
                header, series, values, order = dot_plot
                spec.tooltip_description = build_structured_tooltip_expr(
                    "dot_plot", header, series, values, order=order
                )
                return spec
            lut_key, header, values = _scatter_roles(chart, data)
            spec.tooltip_description = build_structured_tooltip_expr(
                lut_key, header, (), values
            )
        elif isinstance(chart, ResolvedHeatmapChart):
            data = chart_rows(chart, datasets).all_rows()
            header, values = _heatmap_roles(chart, data)
            spec.tooltip_description = build_structured_tooltip_expr(
                "heatmap", header, (), values
            )
        elif isinstance(chart, ResolvedPieChart):
            header, values, total = _pie_roles(chart)
            spec.tooltip_description = build_structured_tooltip_expr(
                "pie", header, (), values, total
            )
        return spec
