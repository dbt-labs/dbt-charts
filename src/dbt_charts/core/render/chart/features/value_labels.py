"""Value-label feature: injects a VL text mark layer for per-datum numeric labels.

Fires when ``style.marks.<mark>.labels.visible is True``.
Families covered: bar, line, area (via line_mark), scatter.

Architecture decision: a single ``ChartFeature`` handles all families rather
than per-family helpers inside emitters — the label layer logic is identical
across families (same mark dict, same text encoding structure) so unifying it
avoids duplication and keeps emitters free of label concerns.
"""

from __future__ import annotations

import json as _json
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any, ClassVar, overload

from dbt_charts.core.compile.config import get_chart_rendering
from dbt_charts.core.compile.models.chart.resolved import ResolvedChart
from dbt_charts.core.compile.models.chart.resolved._layer import (
    LayeredResolvedChart,
    ResolvedAreaLayer,
    ResolvedBarLayer,
    ResolvedLayer,
    ResolvedLineLayer,
    ResolvedScatterLayer,
)
from dbt_charts.core.compile.models.chart.resolved.area import ResolvedAreaChart
from dbt_charts.core.compile.models.chart.resolved.bar import ResolvedBarChart
from dbt_charts.core.compile.models.chart.resolved.line import ResolvedLineChart
from dbt_charts.core.compile.models.chart.resolved.scatter import ResolvedScatterChart
from dbt_charts.core.compile.models.style.theme import (
    BarLabelsStyle,
    BarTotalLabelStyle,
    MarkLabelsStyle,
    PointLabelsStyle,
    font_weight_as_css,
)
from dbt_charts.core.diagnostics.chart_data import ChartDataError
from dbt_charts.core.diagnostics.codes_render import (
    ERR_LABELS_FIELD_NOT_FOUND,
    ERR_STACKED_MIDDLE_ALIGNED_LABELS,
)
from dbt_charts.core.render.chart._types import VLDict
from dbt_charts.core.render.chart.feature import chart_rows
from dbt_charts.core.render.chart.spec import ChartSpec, RenderBox
from dbt_charts.core.render.chart.step_band import STEP_BAND_EDGE_FIELD, is_band_step
from dbt_charts.core.render.chart.x_domain import rendered_x_domain
from dbt_charts.core.render.numeral_expr import numeral_vega_expr
from dbt_charts.core.render.utils import DomainValue, ordered_distinct_values
from dbt_charts.core.utils import Rows, is_vega_numeric_value

# ---------------------------------------------------------------------------
# Position → VL mark dict maps (mirrors v1 oracle exactly)
# ---------------------------------------------------------------------------

BAR_POS_MAP_HORIZONTAL: dict[str, dict[str, Any]] = {
    "above": {"type": "text", "align": "left", "dx": 4},
    "top": {"type": "text", "align": "right", "dx": -4},
    "middle": {"type": "text", "baseline": "middle", "align": "center"},
    "middle_aligned": {"type": "text", "baseline": "middle", "align": "center"},
    "bottom": {"type": "text", "align": "left", "dx": 4},
}

BAR_POS_MAP_VERTICAL: dict[str, dict[str, Any]] = {
    "above": {"type": "text", "baseline": "bottom", "dy": -4},
    "top": {"type": "text", "baseline": "top", "dy": 4},
    "middle": {"type": "text", "baseline": "middle"},
    "middle_aligned": {"type": "text", "baseline": "middle"},
    "bottom": {"type": "text", "baseline": "bottom", "dy": -4},
}

_POINT_POS_MAP: dict[str, dict[str, Any]] = {
    "top": {"type": "text", "baseline": "bottom", "dy": -4},
    "bottom": {"type": "text", "baseline": "top", "dy": 4},
    "left": {"type": "text", "align": "right", "dx": -4},
    "right": {"type": "text", "align": "left", "dx": 4},
    "middle": {"type": "text", "baseline": "middle", "align": "center"},
}

# Same as _POINT_POS_MAP — line labels use the same positional grammar.
_LINE_POS_MAP: dict[str, dict[str, Any]] = {
    "top": {"type": "text", "baseline": "bottom", "dy": -4},
    "bottom": {"type": "text", "baseline": "top", "dy": 4},
    "left": {"type": "text", "align": "right", "dx": -4},
    "right": {"type": "text", "align": "left", "dx": 4},
    "middle": {"type": "text", "baseline": "middle"},
}

# Declaring ANY xOffset on a band scale drops Vega-Lite's implicit band-center
# placement: the compiled Vega loses the `band: 0.5` it otherwise emits, so the
# mark's x becomes the band's LEADING edge plus the offset (confirmed against
# vl-convert's own compiled output). Every offset below is therefore measured
# from the leading edge, not from the center.
_BAND_CENTER_OFFSET = "bandwidth('x') / 2"

# The two positions that run along the categorical axis, and the xOffset that
# anchors each one to the band edge it names. Only these move on a band-width
# mark: band width is horizontal, so top/bottom are unaffected, and middle is
# already right — the band center IS the mark's center. The position map's own
# signed dx then applies the same house clearance it applies off a datum.
_BAND_EDGE_ANCHORS: dict[str, str] = {
    "left": "0",
    "right": "bandwidth('x')",
}

# Where a band-edge caption goes when its own band is the domain's edge one and
# the preferred side has no room. Not the opposite edge, which would park the
# caption over the NEIGHBORING category's band — on a reference mark that
# reads as labeling the wrong period. `top` keeps it in its own band.
_BAND_EDGE_FALLBACK = "top"


# ---------------------------------------------------------------------------
# Band geometry
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class BandLabelAnchor:
    """Band geometry a value label needs to sit beside a band-width mark.

    A ``curve: step`` line on a categorical x draws a plateau spanning the
    whole band (``step_band.py``), but its label sublayer draws from the
    layer's own one row per category — so a horizontal position anchored off
    the datum lands the caption inside the mark it is meant to sit beside.

    Built once per label slot by the caller, which has already decided the
    band verdict with ``is_band_step``, and handed down — the same "one
    verdict, decided once" shape ``label_is_text`` uses. ``None`` at a call
    site means "not a band mark", the only state this has to model.
    """

    x_field: str
    # The x value whose caption has no room on that side: the domain's leading
    # band, and its trailing one. None when no caption lands on that band —
    # never a real value, since ``ordered_distinct_values`` drops nulls — so
    # the common target-overlay case (one tick, mid-domain) is None on both
    # sides and splits into nothing.
    leading_edge: DomainValue | None
    trailing_edge: DomainValue | None
    # The label sublayer inherits the band-DOUBLED rows the plateau is drawn
    # from (the base-chart case, where the band transform lands on the shared
    # top-level encoding). It then needs a per-band dedupe, and an explicit
    # xOffset to override the inherited edge-column one.
    rows_are_doubled: bool


def band_label_anchor(
    x_field: str,
    y_field: str,
    domain: list[DomainValue],
    rows: Rows,
    rows_are_doubled: bool,
) -> BandLabelAnchor:
    """Resolve the band geometry for one label slot on a band-width mark.

    ``domain`` is the x-scale's own value order; ``rows`` the rows the label
    layer draws from. A row whose measure is null paints no caption (VL drops
    the mark), so it can't claim a domain edge either. Both sides go through
    ``ordered_distinct_values``, so a ``datetime.date`` x compares in the same
    normalized key space the domain and the emitted filter literal use.
    """
    labeled = set(
        ordered_distinct_values(
            [row for row in rows if row.get(y_field) is not None], x_field
        )
    )

    if not domain:
        return BandLabelAnchor(x_field, None, None, rows_are_doubled)
    return BandLabelAnchor(
        x_field,
        domain[0] if domain[0] in labeled else None,
        domain[-1] if domain[-1] in labeled else None,
        rows_are_doubled,
    )


def _prepend_filter(layer: VLDict, expr: str) -> None:
    """Add a filter ahead of a built layer's own transforms.

    Ahead, not appended: a house-register ``calculate`` reads the measure
    field, and there is no reason to format rows the filter drops.

    A ``transform`` array on a VL sublayer is not free: ``features/
    zero_value_label.py`` records a reproduced vl-convert defect where one
    makes the shared categorical axis discard its ``sort`` and fall back to
    alphabetical. Band anchoring survives it because the order is not carried
    by ``sort`` alone: the overlay path pins an already-sorted domain on every
    layered categorical x (``_reconcile_x_domain``, ``emitters/_overlay.py``),
    and an unlayered sorted line/area pins its own (``pin_sorted_domain``,
    ``emitters/_cartesian.py``) — a discarded ``sort`` cannot undo an explicit
    domain (``test_value_labels_keep_a_sorted_x_axis``). The bar segment-label
    path hoists its transforms out instead (``_hoist_sort_field_calculate``
    below). A future position that needs a filter here must re-check that.

    Not ``.get("transform", [])`` — the type-state gate
    (``scripts/type_state_counter.py``) counts a 2-arg
    ``.get(k, default)`` as a silent fallback, and the key is a pre-validated
    member of a dict this module built, not user input. Same below in
    ``text_layer_spec``.
    """
    existing: list[VLDict] = []
    if "transform" in layer:  # noqa: SIM401
        existing = layer["transform"]
    layer["transform"] = [{"filter": expr}, *existing]


def _datum_ref(field: str) -> str:
    return f"datum[{_json.dumps(field)}]"


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def apply_label_font(
    mark_dict: dict[str, Any], labels: BarLabelsStyle | PointLabelsStyle
) -> None:
    """Merge labels.font props into mark_dict in place (mirrors v1 oracle)."""
    if labels.font is None:
        return
    if labels.font.family is not None:
        mark_dict["font"] = labels.font.family
    if labels.font.weight is not None:
        mark_dict["fontWeight"] = font_weight_as_css(labels.font.weight)
    if labels.font.style is not None:
        mark_dict["fontStyle"] = labels.font.style
    if labels.font.color is not None:
        mark_dict["color"] = labels.font.color


def label_size_encoding(
    labels: BarLabelsStyle | PointLabelsStyle,
) -> dict[str, Any] | None:
    if labels.font is not None and labels.font.size is not None:
        return {"value": labels.font.size}
    return None


def labels_draw_text(labels: MarkLabelsStyle, rows: Rows) -> bool:
    """Whether this label slot draws text rather than a formatted number.

    True only when ``labels.field`` names a non-numeric column. An unset
    ``field`` labels the chart's own measure, which is numeric by
    construction, so it never takes the text path.

    Decided once per label slot, by the caller, and handed down — the same
    "one verdict, decided once" shape a table column's alignment uses, so no
    downstream branch re-asks per row.

    Scans the WHOLE column, deliberately not via
    ``infer_vega_type_from_data``, which samples the first ten rows and skips
    nulls. A caption column is sparse by nature — one populated row on the
    month in progress, null everywhere else — so on any board with more than
    ten categories the sample is all-null, leaves ``all_numeric`` untouched,
    and classifies quantitative. That is the exact ``NaN`` this function
    exists to prevent, reappearing on the natural single-query form of the
    board that motivated it.

    A column with no non-null value anywhere stays on the number path — that
    falls out of ``all(())`` being True, not from a guard: there is nothing to
    classify from, and nothing renders either way.

    ``all`` rather than ``any`` is a deliberate choice about which way a MIXED
    column degrades, and both directions are lossy because such a column is a
    data error this does not police. Under ``all``, one stray string turns the
    whole column to text and its numbers lose their format; under ``any``, the
    string cell alone paints ``NaN``. Unformatted-but-correct numbers beat a
    ``NaN``, so ``all`` it is.
    """
    if labels.field is None:
        return False
    values = [
        row[labels.field]
        for row in rows
        if labels.field in row and row[labels.field] is not None
    ]
    return not all(is_vega_numeric_value(v) for v in values)


def _house_register_text_encoding(
    labels: MarkLabelsStyle | BarTotalLabelStyle,
    label_field: str,
    calc_field: str,
    is_house: bool,
    label_is_text: bool,
) -> tuple[VLDict, list[VLDict]]:
    """Build the ``text`` channel encoding for a value label.

    ``label_is_text`` short-circuits every formatting route below: a text
    column (a caption like "Pace" sourced via ``labels.field``) is drawn
    verbatim as nominal text. Number-formatting a string is what painted
    ``NaN`` — Vega coerces the string to a number to apply the format, and all
    three routes did it their own way.

    Otherwise branches on ``is_house`` alone -- the SI-shape check already happened
    upstream, in ``resolve_label_format`` (``compile/format.py``), which only
    ever sets ``is_house`` True for an SI-shaped alias.

    When ``is_house`` is True, the narrative register fires (``1.2mn``) via a
    ``calculate`` transform -- Vega, not Python, paints datum-driven text, so
    format_d3's post-process never runs on values Vega formats itself from a
    bare d3 spec.

    When ``is_house`` is False (a literal d3 spec, or any non-SI format —
    currency, percent, ... — regardless of provenance), the format is handed
    verbatim to Vega (``text.format``), producing raw d3 output (``1.2M``).

    Returns ``(text_encoding, transforms)`` -- ``transforms`` is empty unless
    the narrative branch fires, in which case it holds the one ``calculate``
    transform the caller must splice into its transform list.
    """
    if label_is_text:
        return {"field": label_field, "type": "nominal"}, []
    if labels.format is None:
        return {"field": label_field, "type": "quantitative"}, []
    if not is_house:
        return (
            {"field": label_field, "type": "quantitative", "format": labels.format},
            [],
        )
    expr = numeral_vega_expr(f"datum[{label_field!r}]", labels.format, "narrative")
    return (
        {"field": calc_field, "type": "nominal"},
        [{"calculate": expr, "as": calc_field}],
    )


def _null_safe_position(expr: str, y_field: str) -> str:
    """Guard a calculated label position so a null measure yields null.

    Vega expressions are JS: ``null / 2`` is ``0``, and a dataset-wide constant
    midpoint is valid for every row regardless of that row's value. Either way
    an interior label position draws text for a row that has no value, printing
    the literal string ``null`` — in knockout white, on the zero baseline.

    Returning null instead lets Vega-Lite drop the mark, which is exactly what
    it already does for ``above``/``top``, where the measure field is encoded
    directly. Guarding on the measure field alone (never the label field) is
    what keeps the interior positions at parity with that native behavior.

    An exact ``0`` is a value, not a gap: ``isValid(0)`` is true, so a genuine
    zero keeps its label here and ``ZeroValueLabelFeature`` keeps its own.
    """
    return f"isValid(datum[{y_field!r}]) ? {expr} : null"


def _suppress_label_layer_axes(spec: ChartSpec) -> None:
    """Stamp ``"axis": None`` on every text sublayer's VL y channel.

    Dual-axis only — called when the emitted spec carries
    ``resolve.scale.y = "independent"`` (set by ``emitters/_overlay.py``).
    Under that resolution every sublayer's un-suppressed y channel draws its
    OWN axis — titled with the raw field name, since nobody authors a label
    for a synthetic label-sublayer channel — so both the base chart's and
    each overlay layer's label sublayers (already appended by the overlay
    emitter) must opt out. Suppressing the axis leaves the channel's scale,
    and so the label's position, untouched.

    Exactly the channel the resolve certifies, whatever its encoding type:
    the overlay emitter never resolves x independent, so x is always a
    SHARED scale — and an explicit null on a shared scale corrupts
    Vega-Lite's axis merge (verified against vl-convert: the merged axis
    loses its ticks or fails to parse), which is also why the suppression is
    gated on the emitted resolve rather than baked into the builders.

    The stamp is type-blind because it does not need to know: a horizontal
    base cannot reach this function at all, since a layer pinning
    ``axis_y.position`` there is refused by ``ERR-LAYER-AXIS-POSITION-
    ORIENTATION`` and nothing else resolves y independent.
    """
    for sub in spec.layers:
        if sub.mark != "text":
            continue
        enc = sub.encoding.get("y")
        if isinstance(enc, dict) and "field" in enc:
            enc["axis"] = None


# Positions drawn over the bar's fill, so the label needs the inside ink color.
_INSIDE_BAR_POSITIONS = frozenset({"top", "middle", "bottom", "middle_aligned"})
# Of those, the ones the bar's own extent can actually crop. middle_aligned is
# drawn over the bar but anchors to a height shared across every bar, so a short
# bar's label floats free of it — there is nothing to fit inside.
_FIT_TESTED_POSITIONS = _INSIDE_BAR_POSITIONS - {"middle_aligned"}


def _build_bar_text_layer(
    labels: BarLabelsStyle,
    y_field: str,
    is_horizontal: bool,
    is_stacked: bool,
    background: str,
    is_house: bool,
    label_is_text: bool,
    category_field: str | None,
    stack_offset: str | None,
    stack_sort: list[VLDict] | None,
) -> VLDict:
    """Build a single VL text-layer dict for a bar mark.

    ``category_field``, ``stack_offset`` and ``stack_sort`` are needed only to
    center a label inside its own stacked segment. All three describe the stack
    the BAR layer emitted — read off its encoding, never re-derived — because
    the label has to land on the geometry Vega-Lite actually lays out.
    """
    pos_map = BAR_POS_MAP_HORIZONTAL if is_horizontal else BAR_POS_MAP_VERTICAL
    measure_channel = "x" if is_horizontal else "y"

    effective_position: str = (
        labels.position if labels.position is not None else "above"
    )
    if is_stacked and effective_position == "above":
        effective_position = "top"

    # middle_aligned aligns every bar's label to ONE shared height — a
    # whole-bar idea with no per-segment reading. middle is the stacked answer.
    if is_stacked and effective_position == "middle_aligned":
        raise ChartDataError.from_code(ERR_STACKED_MIDDLE_ALIGNED_LABELS)

    mark_dict = dict(pos_map[effective_position])
    if labels.dx is not None:
        mark_dict["dx"] = labels.dx
    if labels.dy is not None:
        mark_dict["dy"] = labels.dy
    apply_label_font(mark_dict, labels)

    # Inside-bar positions need the background color for legibility.
    if effective_position in _INSIDE_BAR_POSITIONS and "color" not in mark_dict:
        mark_dict["color"] = background

    label_field = labels.field if labels.field is not None else y_field
    text_enc, house_transforms = _house_register_text_encoding(
        labels,
        label_field,
        "__value_label_text",
        is_house=is_house,
        label_is_text=label_is_text,
    )

    layer_enc: dict[str, Any] = {"text": text_enc}
    size = label_size_encoding(labels)
    if size is not None:
        layer_enc["size"] = size

    # A label's color is only a static mark prop, and Vega-Lite lets any
    # inherited encoding.color beat one: a text sublayer picks up the outer
    # color channel and paints each label in its own bar's fill — invisible
    # inside the bar, and a silent override of the authored ink when an
    # authored color channel supplies that fill. Pin it as this layer's
    # own encoding value so nothing upstream can claim it. A stacked label
    # always sits in a segment and so always needs one; elsewhere no color
    # means no claim, and the label goes on inheriting the series color.
    label_color = mark_dict.pop("color", background if is_stacked else None)
    if label_color is not None:
        layer_enc["color"] = {"value": label_color}

    if effective_position == "bottom":
        bottom_field = "__bar_bottom"
        return {
            "mark": mark_dict,
            "transform": [
                *house_transforms,
                {"calculate": _null_safe_position("0", y_field), "as": bottom_field},
            ],
            "encoding": {
                **layer_enc,
                measure_channel: {"field": bottom_field, "type": "quantitative"},
            },
        }
    if effective_position == "middle_aligned":
        mid_field = "__value_label_mid_aligned"
        transforms = [
            *house_transforms,
            {"joinaggregate": [{"op": "mean", "field": y_field, "as": "__mean_val"}]},
            {
                "calculate": _null_safe_position("datum.__mean_val / 2", y_field),
                "as": mid_field,
            },
        ]
        return {
            "mark": mark_dict,
            "transform": transforms,
            "encoding": {
                **layer_enc,
                measure_channel: {"field": mid_field, "type": "quantitative"},
            },
        }
    if effective_position == "middle" and is_stacked:
        # Each segment's own band center. The bounds come from a stack
        # transform rather than arithmetic on the row's value, because a
        # segment's position depends on every segment below it.
        start_field, end_field = "__value_label_seg_start", "__value_label_seg_end"
        stack_transform: VLDict = {
            "stack": y_field,
            # The bar's own offset, not a hardcoded "zero": `normalize` lays the
            # bars out on a pinned [0, 1] domain and `center` straddles the
            # baseline, so stacking the labels from zero puts them off-axis or
            # inside the neighboring segment.
            "offset": stack_offset,
            "as": [start_field, end_field],
        }
        if category_field is not None:
            # No category is not a special case, just one implicit stack: an
            # ungrouped stack transform accumulates every row into one column,
            # which is exactly the geometry such a chart draws.
            stack_transform["groupby"] = [category_field]
        if stack_sort is not None:
            stack_transform["sort"] = stack_sort
        mid_field = "__value_label_mid"
        return {
            "mark": mark_dict,
            "transform": [
                *house_transforms,
                stack_transform,
                {
                    "calculate": _null_safe_position(
                        f"(datum.{start_field} + datum.{end_field}) / 2", y_field
                    ),
                    "as": mid_field,
                },
            ],
            "encoding": {
                **layer_enc,
                measure_channel: {
                    "field": mid_field,
                    "type": "quantitative",
                    # Not cosmetic: the midpoint is already an absolute
                    # position, and a sublayer sharing the outer measure
                    # channel inherits its stack: "zero" and would re-stack it.
                    "stack": None,
                },
            },
        }
    if effective_position == "middle":
        mid_field = "__value_label_mid"
        transforms = [
            *house_transforms,
            {
                "calculate": _null_safe_position(f"datum[{y_field!r}] / 2", y_field),
                "as": mid_field,
            },
        ]
        return {
            "mark": mark_dict,
            "transform": transforms,
            "encoding": {
                **layer_enc,
                measure_channel: {"field": mid_field, "type": "quantitative"},
            },
        }
    if is_stacked:
        # Text marks don't inherit VL bar stacking — inject explicit stacked position.
        stacked_result: VLDict = {
            "mark": mark_dict,
            "encoding": {
                **layer_enc,
                measure_channel: {
                    "field": y_field,
                    "type": "quantitative",
                    "stack": "zero",
                },
            },
        }
        if house_transforms:
            stacked_result["transform"] = house_transforms
        return stacked_result
    layer_enc[measure_channel] = {"field": y_field, "type": "quantitative"}
    result: VLDict = {"mark": mark_dict, "encoding": layer_enc}
    if house_transforms:
        result["transform"] = house_transforms
    return result


def _bar_channel_encoding(spec: ChartSpec, channel: str) -> VLDict | None:
    """A channel's emitted encoding, wherever the emitter actually left it.

    A bar chart with authored ``layers:`` is restructured by
    ``render_cartesian_overlay`` before the feature pipeline runs: the outer
    spec keeps only the category and order channels, and the measure
    encoding — carrying the stack offset and the scale this feature reads —
    moves onto the bar sublayer. Reading the outer spec alone finds no offset
    and no span there at all, which is how the same wrong-offset defect
    reappeared on the layered shape after being fixed on the plain one.

    Recurses because a mixed-sign split nests another layered spec inside.
    """
    outer = spec.encoding.get(channel)
    if isinstance(outer, dict):
        return outer
    for layer in spec.layers:
        if layer.mark == "layered":
            nested = _bar_channel_encoding(layer, channel)
            if nested is not None:
                return nested
        elif layer.mark == "bar":
            enc = layer.encoding.get(channel)
            if isinstance(enc, dict):
                return enc
    return None


def _emitted_stack(
    spec: ChartSpec, measure_channel: str
) -> tuple[str | None, list[VLDict] | None]:
    """The stack offset and sort the bar layer actually emitted.

    Read off the emitted encoding rather than re-derived from the resolved
    chart. The label layer has to line up with the geometry Vega-Lite will lay
    out, and the emitter is the only thing that knows what it put there —
    deriving it independently is exactly how a label layer drifts from its bars.

    Absent an ``order`` channel the stack does NOT fall back to row order: VL
    sorts it by the color field, descending (confirmed against the compiled
    Vega). The emitter only declares ``order`` for a nominal series color, so
    a quantitative or gradient color column lands here — mirroring VL's own
    default is what keeps those labels on their own segments.
    """
    measure_enc = _bar_channel_encoding(spec, measure_channel)
    offset = measure_enc.get("stack") if measure_enc is not None else None
    order_enc = _bar_channel_encoding(spec, "order")
    if isinstance(order_enc, dict) and "field" in order_enc:
        sort_order = order_enc.get(
            "sort",
            # type-state: silent_fallback — VL's order channel defaults to
            # ascending when `sort` is omitted; mirroring that documented
            # default is the correct read, not a guess at a missing value.
            "ascending",
        )
        return offset, [{"field": order_enc["field"], "order": sort_order}]
    color_enc = _bar_channel_encoding(spec, "color")
    if color_enc is not None and "field" in color_enc:
        return offset, [{"field": color_enc["field"], "order": "descending"}]
    return offset, None


def _hoist_sort_field_calculate(
    spec: ChartSpec, stack_sort: list[VLDict] | None
) -> None:
    """Make the stack sort's field exist on the outer spec, if a sublayer owns it.

    A mixed-sign bar with a corner radius splits into two sign-filtered
    sublayers (``emitters/_layers.py``), which carry the stack-order calculate
    while the outer spec keeps only the encoding. The label layer's stack
    transform is hoisted onto that outer spec, where the field would be
    undefined — and VL sorting by an undefined field is stable, so it degrades
    silently to row order instead of failing.
    """
    if not stack_sort:
        return
    field = stack_sort[0]["field"]
    if any(transform.get("as") == field for transform in spec.transforms):
        return
    for layer in spec.layers:
        for transform in layer.transforms:
            if transform.get("as") == field:
                spec.transforms.append(dict(transform))
                return


def _measure_span(spec: ChartSpec, measure_channel: str) -> float | None:
    """The measure axis span the chart will actually render with, or None.

    Read off the emitted scale for the same reason as ``_emitted_stack``: the
    resolved chart's baked ``stacked_domain_max`` is not always what the
    emitter pins — an authored ``domain`` overrides it — and measuring against
    a span the chart is not using pushes the fit test the unsafe way, hiding
    labels that had room.

    Returns None for any non-linear scale. ``symlog`` and ``pow`` map value to
    pixel non-linearly, so no linear span can bound a segment's height: a
    symlog bar at 3% of the domain paints at half the plot. Bars reject ``log``
    outright and that rejection recommends ``symlog``, so this is a shape
    authors are actively pointed at.
    """
    measure_enc = _bar_channel_encoding(spec, measure_channel)
    if measure_enc is None:
        return None
    scale = measure_enc.get("scale")
    if not isinstance(scale, dict):
        return None
    scale_type = scale.get("type")
    if scale_type is not None and scale_type != "linear":
        return None
    domain = scale.get("domain")
    if isinstance(domain, list) and len(domain) == 2:
        low, high = domain
        if not isinstance(low, (int, float)) or not isinstance(high, (int, float)):
            # A temporal domain is copied through verbatim as ISO strings.
            # That is not a numeric span and must not be coerced into one.
            return None
        return float(high) - float(low)
    domain_max = scale.get("domainMax")
    if not isinstance(domain_max, (int, float)):
        return None
    domain_min = scale.get(
        "domainMin",
        # type-state: silent_fallback — an absent domainMin on a zero-anchored
        # quantitative scale IS zero in Vega-Lite; mirroring its documented
        # default is the correct read of the emitted scale, not a guess.
        0.0,
    )
    if not isinstance(domain_min, (int, float)):
        return None
    return float(domain_max) - float(domain_min)


def _fit_measures_the_label(effective_position: str, is_stacked: bool) -> bool:
    """Whether the label actually sits inside the extent the fit test measures.

    ``above`` draws past the bar end, in open space. ``middle_aligned`` anchors
    to a height shared across every bar, so a short bar's label floats free of
    its own extent. Stacked ``bottom`` pins every segment's label to the zero
    baseline — ``_build_bar_text_layer`` returns before the stacked branches —
    so a segment's height says nothing about where its label lands; that
    placement is its own pre-existing bug and the fit test declines to reason
    on top of it rather than compound it.
    """
    if effective_position not in _FIT_TESTED_POSITIONS:
        return False
    return not (is_stacked and effective_position == "bottom")


def _fit_hide_test(
    chart: ResolvedBarChart,
    y_field: str,
    is_horizontal: bool,
    effective_position: str,
    is_stacked: bool,
    measure_span: float | None,
    stack_offset: str | None,
) -> str | None:
    """A VL expression true for labels their bar segment cannot hold, or None.

    Applied as a ``condition`` on the text channel — never as a layer
    ``filter``. A ``transform`` array on a label sublayer makes vl-convert
    discard the shared categorical axis's ``sort`` and fall back to
    alphabetical order, so a sorted bar chart would silently lose its sort the
    moment one segment came up short. ``zero_value_label.py``'s module
    docstring records the same trap being hit and backed out.

    Only labels drawn *inside* the bar can be clipped by it — ``above`` sits in
    open space past the bar end, as does the stack total. Horizontal bars fit
    against the label's width, which depends on the string Vega-Lite composes
    at render time from its own format; that is a different measurement, so
    this stands down rather than half-answer it.

    Only ``middle`` is measured on a downward bar: ``top`` and ``bottom`` draw
    outside a negative mark, so its extent crops nothing.

    An exact ``0`` is exempt. A zero bar has no extent to fit anything into,
    but it is not a short segment: a 0-height rect is invisible, so an
    unlabeled zero reads as missing data rather than as a real zero. That is
    the ambiguity ``ZeroValueLabelFeature`` exists to resolve on the
    ``labels.visible is not True`` path; on this path the value label is the
    only thing saying the row is there at all, so blanking it is worse than
    letting it sit on the baseline.

    The segment is measured against the PLOT RECTANGLE it is drawn in, not
    against the slot. The slot also holds the title, subtitle, legend, axis
    labels and padding, so it is the larger box — and since the threshold
    grows as the height shrinks, measuring against the slot yields the smaller
    threshold and under-counts what needs hiding.

    Two invariants make that measurable here:

    - **Only the height is deferred to render time; the span stays
      Python-side.** ``domain('y')`` cannot be used: Vega-Lite hoists a concat
      child's scales to the root scope and renames them ``concat_<i>_y``, so a
      literal ``y`` names a scale that does not exist and the expression reads
      NaN on every wrapped chart. ``_measure_span`` reads the span off the
      scale the emitter pinned.
    - **The height signal must name THIS view's plot rect.** A bare ``height``
      resolves against the compiled root view — every child scope reads the
      root signal. That is correct for a bare chart, and for the endpoint-label
      ``hconcat``, because ``_wrap_hconcat_label_pane`` emits
      ``resolve.scale.y: "shared"``, and a shared measure scale is what makes
      vl-convert hoist a root ``height``. That resolve is what to preserve:
      panes of equal height happen to hoist without it, but the panes are not
      always equal (a ``support_table`` chart with no authored height leaves the
      label pane's unset), so the shared scale is the guarantee that holds. A
      facet root sets no ``height`` at all, so faceted charts must name
      ``child_height`` instead; see ``plot_height`` below.

    ``vconcat`` is the one wrap this cannot express, and it is unreachable:
    ``top_rail`` is gated on horizontal orientation in ``endpoint_labels.py``,
    and horizontal bars stand down above.

    There is no Python-side early-exit, because the threshold is a render-time
    value Python cannot evaluate — and the slot-derived value the old guard
    used was itself the under-count. A chart with room carries a condition
    that never fires and renders identically.

    The measurement is exact for a zero-anchored domain. On a domain that does
    not include zero the bar's visible extent is shorter than ``v`` implies, so
    the test errs toward keeping labels — the pre-existing direction.
    """
    if is_horizontal or not _fit_measures_the_label(effective_position, is_stacked):
        return None
    if stack_offset == "normalize":
        # Bars are laid out as each segment's SHARE of its stack, on a pinned
        # [0, 1] axis, while the rows still carry raw values. Comparing the two
        # would measure against the wrong units entirely.
        return None
    font_size = chart.style.label_font_size
    if measure_span is None or measure_span <= 0:
        return None
    required_px = font_size * get_chart_rendering().bar.label_fit_line_height_multiplier
    field = f"datum[{y_field!r}]"
    # A bare `height` resolves against the COMPILED ROOT view, not whatever
    # scope the mark ends up in. A facet root never sets it — Vega-Lite emits
    # `child_width`/`child_height` instead — so a literal `height` there reads
    # 0, the test goes universally true, and every inside label blanks.
    # `chart.multiples is not None` is the same predicate `FacetFeature`
    # gates on, so the two cannot drift apart.
    plot_height = "child_height" if chart.multiples is not None else "height"

    def extent_px(measure: str) -> str:
        # `v * plot_height / span` IS the segment's pixel extent, so this reads
        # directly as "the segment is shorter than one line of label needs".
        return f"{measure} * {plot_height} / {measure_span:.6g} < {required_px:.6g}"

    if effective_position == "middle":
        return f"{field} != 0 && {extent_px(f'abs({field})')}"
    # `top` anchors to the bar's end and `bottom` to the zero line. On a
    # downward bar both put the text OUTSIDE the mark — below its lowest point
    # and above the baseline respectively — where the bar cannot crop it. The
    # `> 0` gate already excludes those, so the extent needs no abs().
    return f"{field} > 0 && {extent_px(field)}"


def _build_bar_total_label_layer(
    total_label: BarTotalLabelStyle,
    y_field: str,
    x_field: str,
    is_horizontal: bool,
    is_house: bool,
) -> dict[str, Any]:
    """Build a VL text-layer dict showing the per-category stack total.

    Uses a joinaggregate sum transform (computed at VL render time, not in the
    query layer) — a deliberate, scoped exception to the render-layer's
    "no chart-layer aggregation" rule, kept local to this one case. Do not
    generalize this pattern or rewrite it as a query-side sum.

    Called only when is_stacked and total_label.visible are both True.
    The label sits above/right of the whole stack (outside the bar fill), so
    its default position mirrors the "above" mark from the standard position maps.
    """
    measure_channel = "x" if is_horizontal else "y"
    category_channel = "y" if is_horizontal else "x"

    # Reuse the "above" entry from the existing position maps — same placement as a
    # non-stacked bar label positioned above the bar end.
    pos_map = BAR_POS_MAP_HORIZONTAL if is_horizontal else BAR_POS_MAP_VERTICAL
    mark_dict = dict(pos_map["above"])

    if total_label.dx is not None:
        mark_dict["dx"] = total_label.dx
    if total_label.dy is not None:
        mark_dict["dy"] = total_label.dy
    if total_label.font.family is not None:
        mark_dict["font"] = total_label.font.family
    if total_label.font.size is not None:
        mark_dict["fontSize"] = total_label.font.size
    if total_label.font.weight is not None:
        mark_dict["fontWeight"] = font_weight_as_css(total_label.font.weight)
    if total_label.font.style is not None:
        mark_dict["fontStyle"] = total_label.font.style

    text_enc, house_transforms = _house_register_text_encoding(
        total_label,
        "__stack_total",
        "__stack_total_text",
        is_house=is_house,
        # The stack total is a synthetic summed field, never an authored column,
        # so it has no `labels.field` and is numeric by construction.
        label_is_text=False,
    )

    encoding = {
        "text": text_enc,
        # stack: None is required, not cosmetic: the outer chart's measure
        # channel carries stack: "zero" for the segment bars. A sublayer that
        # shares that channel but omits "stack" does not default to unstacked —
        # Vega-Lite inherits the outer stacking and tries to stack the
        # already-summed, per-row-duplicated __stack_total field across the
        # color groups, corrupting the scale domain and collapsing the whole
        # chart (bars included) to zero height.
        measure_channel: {
            "field": "__stack_total",
            "type": "quantitative",
            "stack": None,
        },
        category_channel: {"field": x_field, "type": "nominal"},
        # Pin color via encoding (not mark.color): the outer chart carries a nominal
        # color:{field:segment} encoding that overrides mark-level color in VL.
        # encoding.color.value = constant wins over an inherited field encoding,
        # so each __stack_total row renders in the same foreground text color instead
        # of the N different segment fill colors.
        # total_label.font is guaranteed non-None (InheritSlot from charts.font).
        "color": {"value": total_label.font.color},
    }

    return {
        "mark": mark_dict,
        "transform": [
            {
                "joinaggregate": [
                    {"op": "sum", "field": y_field, "as": "__stack_total"}
                ],
                "groupby": [x_field],
            },
            # __stack_total must exist before a narrative-register calculate
            # can reference it — see _house_register_text_encoding's docstring.
            *house_transforms,
        ],
        "encoding": encoding,
    }


def _build_point_text_layer(
    labels: PointLabelsStyle,
    y_field: str,
    is_house: bool,
    label_is_text: bool,
    measure_channel: str,
) -> dict[str, Any]:
    """Build a VL text-layer dict for a point/scatter mark."""
    pos_map = _POINT_POS_MAP
    effective_position: str = labels.position if labels.position is not None else "top"
    mark_dict = dict(pos_map[effective_position])
    if labels.dx is not None:
        mark_dict["dx"] = labels.dx
    if labels.dy is not None:
        mark_dict["dy"] = labels.dy
    apply_label_font(mark_dict, labels)

    label_field = labels.field if labels.field is not None else y_field
    text_enc, house_transforms = _house_register_text_encoding(
        labels,
        label_field,
        "__value_label_text",
        is_house=is_house,
        label_is_text=label_is_text,
    )

    layer_enc: dict[str, Any] = {
        "text": text_enc,
        measure_channel: {"field": y_field, "type": "quantitative"},
    }
    size = label_size_encoding(labels)
    if size is not None:
        layer_enc["size"] = size
    result: VLDict = {"mark": mark_dict, "encoding": layer_enc}
    if house_transforms:
        result["transform"] = house_transforms
    return result


def _build_line_text_layer(
    labels: PointLabelsStyle,
    y_field: str,
    is_house: bool,
    label_is_text: bool,
    effective_position: str,
    measure_channel: str,
) -> dict[str, Any]:
    """Build a VL text-layer dict for a line mark at ``effective_position``."""
    pos_map = _LINE_POS_MAP
    mark_dict = dict(pos_map[effective_position])
    if labels.dx is not None:
        mark_dict["dx"] = labels.dx
    if labels.dy is not None:
        mark_dict["dy"] = labels.dy
    apply_label_font(mark_dict, labels)

    label_field = labels.field if labels.field is not None else y_field
    text_enc, house_transforms = _house_register_text_encoding(
        labels,
        label_field,
        "__value_label_text",
        is_house=is_house,
        label_is_text=label_is_text,
    )

    layer_enc: dict[str, Any] = {
        "text": text_enc,
        measure_channel: {"field": y_field, "type": "quantitative"},
    }
    size = label_size_encoding(labels)
    if size is not None:
        layer_enc["size"] = size
    result: VLDict = {"mark": mark_dict, "encoding": layer_enc}
    if house_transforms:
        result["transform"] = house_transforms
    return result


def build_line_text_layers(
    labels: PointLabelsStyle,
    y_field: str,
    is_house: bool,
    label_is_text: bool,
    band: BandLabelAnchor | None,
    measure_channel: str,
) -> list[VLDict]:
    """The VL text layers for a line/area mark's value labels.

    One layer off the datum, as every non-band mark gets. On a band-width mark
    the two horizontal positions instead resolve against the band edge, and a
    second layer splits off when the caption's own band is the domain's edge
    one — that row alone falls back to ``top``.

    An authored ``dx`` still composes as a nudge on top of whichever position
    resolves, exactly as it does off a datum.
    """
    position: str = labels.position if labels.position is not None else "top"

    def layer_at(at_position: str) -> VLDict:
        built = _build_line_text_layer(
            labels, y_field, is_house, label_is_text, at_position, measure_channel
        )
        if band is not None and band.rows_are_doubled:
            _prepend_filter(built, f"{_datum_ref(STEP_BAND_EDGE_FIELD)} === 0")
            # The inherited edge-column xOffset would otherwise place the one
            # surviving row at the plateau's leading end, not the band center.
            built["encoding"]["xOffset"] = {"value": {"expr": _BAND_CENTER_OFFSET}}
        return built

    anchored = layer_at(position)
    if band is None or position not in _BAND_EDGE_ANCHORS:
        return [anchored]
    anchored["encoding"]["xOffset"] = {"value": {"expr": _BAND_EDGE_ANCHORS[position]}}
    edge_value = band.leading_edge if position == "left" else band.trailing_edge
    if edge_value is None:
        return [anchored]
    edge = _json.dumps(edge_value)
    fallback = layer_at(_BAND_EDGE_FALLBACK)
    # An authored dx/dy was a nudge on the horizontal anchor the author asked
    # for, so neither survives the switch to a vertical position: dx would
    # shove this one caption off its own band, and dy is worse — it OVERWRITES
    # `top`'s own -4 clearance and drops the caption back into the plateau
    # this whole change exists to clear. Restore the house clearance rather
    # than dropping the key, which would leave the caption on the datum.
    fallback["mark"].pop("dx", None)
    fallback["mark"]["dy"] = _LINE_POS_MAP[_BAND_EDGE_FALLBACK]["dy"]
    _prepend_filter(anchored, f"{_datum_ref(band.x_field)} !== {edge}")
    _prepend_filter(fallback, f"{_datum_ref(band.x_field)} === {edge}")
    return [anchored, fallback]


def text_layer_spec(text_layer: VLDict) -> ChartSpec:
    """Turn a built text-layer dict into the ChartSpec every caller appends.

    ``encoding`` is always present on the builders' return dict; ``transform``
    only when the position needs its own calculate (bottom/middle/
    middle_aligned), ``is_house`` is True (a house-register calculate), or a
    band anchor filtered the rows.
    """
    transforms: list[VLDict] = []
    if "transform" in text_layer:  # noqa: SIM401
        transforms = text_layer["transform"]
    return ChartSpec(
        mark="text",
        mark_props={k: v for k, v in text_layer["mark"].items() if k != "type"},
        encoding=dict(text_layer["encoding"]),
        transforms=list(transforms),
        value_label=True,
    )


@overload
def _layer_label_slots(layer: ResolvedBarLayer) -> tuple[BarLabelsStyle]: ...
@overload
def _layer_label_slots(
    layer: ResolvedLineLayer,
) -> tuple[PointLabelsStyle, PointLabelsStyle]: ...
@overload
def _layer_label_slots(layer: ResolvedAreaLayer) -> tuple[PointLabelsStyle]: ...
@overload
def _layer_label_slots(layer: ResolvedScatterLayer) -> tuple[PointLabelsStyle]: ...
def _layer_label_slots(layer: ResolvedLayer) -> tuple[MarkLabelsStyle, ...]:
    """The label-carrying mark-style slots for one overlay layer.

    Mirrors ``_build_layer_label_specs``'s per-family dispatch
    (``emitters/_overlay.py``): a bar layer's ``bar_mark.labels``, a line
    layer's ``line_mark.labels``/``point_mark.labels`` alias pair, an area
    layer's ``line_mark.labels``, else (scatter) ``point_mark.labels``. The
    overloads let each concrete-layer call site (``_build_layer_label_specs``)
    keep the narrower ``BarLabelsStyle``/``PointLabelsStyle`` types its
    builders require; a call on the ``ResolvedLayer`` union (validation's own
    per-layer loop) falls back to the shared ``MarkLabelsStyle`` base, which
    is all ``.field`` access needs.
    """
    if isinstance(layer, ResolvedBarLayer):
        return (layer.bar_mark.labels,)
    if isinstance(layer, ResolvedLineLayer):
        return (layer.line_mark.labels, layer.point_mark.labels)
    if isinstance(layer, ResolvedAreaLayer):
        return (layer.line_mark.labels,)
    return (layer.point_mark.labels,)  # ResolvedScatterLayer


def _base_band_anchor(
    x_field: str,
    y_field: str,
    curve: str | None,
    spec: ChartSpec,
) -> BandLabelAnchor | None:
    """The band anchor for a BASE chart's own labels, or None off a band mark.

    Everything is read off ``spec`` rather than the chart's query rows,
    because the label sublayer carries no ``.data`` of its own and so renders
    against exactly what the spec carries — which the emitter has already
    canonicalized (a labeled-temporal x arrives as its bucket value, not the
    raw string) and, on a bare line/area chart, doubled to the band edges.
    Anchoring off the pre-emitter rows emits filter literals that match no
    row at all on any x the emitter rewrites.

    ``xOffset`` on the shared encoding is what says those rows are doubled: a
    chart with authored ``layers:`` keeps the band transform on the base's own
    sub-layer and leaves the outer rows undoubled.
    """
    x_enc = spec.encoding.get("x")
    if not isinstance(x_enc, dict) or spec.data is None:
        return None
    if not is_band_step(curve, x_enc.get("type")):
        return None
    return band_label_anchor(
        x_field,
        y_field,
        _band_domain_order(x_enc, spec.data),
        spec.data,
        rows_are_doubled="xOffset" in spec.encoding,
    )


def _band_domain_order(x_enc: VLDict, rows: Rows) -> list[DomainValue]:
    """The band scale's value order as Vega-Lite renders it.

    A pinned ``scale.domain`` wins outright in Vega-Lite, so it is read first —
    which is how a base chart carrying authored ``layers:`` picks up the
    categories its overlays added. Otherwise the rows' own order.
    """
    scale = x_enc.get("scale")
    if isinstance(scale, dict) and isinstance(scale.get("domain"), list):
        return list(scale["domain"])
    return rendered_x_domain(x_enc, rows, [], chart_id=None)


# ---------------------------------------------------------------------------
# ChartFeature
# ---------------------------------------------------------------------------


@dataclass
class ValueLabelFeature:
    """Appends a VL text mark layer when style.marks.<mark>.labels.visible is True.

    Port of v1 oracle ``_maybe_inject_value_label_layer``.
    Covers: bar, line, area (via line_mark), scatter.
    """

    def applies_to(self, chart: ResolvedChart) -> bool:
        return isinstance(
            chart,
            (
                ResolvedBarChart,
                ResolvedLineChart,
                ResolvedAreaChart,
                ResolvedScatterChart,
            ),
        )

    def _collect_label_fields(
        self,
        chart: ResolvedChart,
        data: Rows,
        datasets: dict[
            str | None,
            list[dict[str, Any]],  # type-state: explicit_any — query rows
        ],
    ) -> list[tuple[str, Rows, str]]:
        """Return (labels.field, its own rows, source description) per slot.

        A base-chart slot pairs with the chart's own ``data`` and the source
        "the chart". An overlay layer's slot pairs with THAT LAYER's own
        rows: ``data`` when the layer shares the base chart's query, else
        ``datasets[layer.query_name]`` — the same divergence rule
        ``render_cartesian_overlay``/``_resolve_layer_rows``
        (``emitters/_overlay.py``) use to decide whether a layer reads its
        own dataset or inherits the base's already-normalized rows. A
        layer's rows must be validated on their own terms: a diverging
        layer's query rarely shares the base's columns, so checking a
        layer's field against the base's rows would pass a field that
        doesn't exist where the layer actually renders (or fail one that
        does).

        ``source`` identifies the offending slot in a raised error: a chart
        can carry N+1 candidate ``labels.field`` sources once overlay layers
        are in play, and "names a column not present" alone leaves the
        author hunting for which one fired.
        """
        slots: list[MarkLabelsStyle]
        if isinstance(chart, ResolvedBarChart):
            slots = [chart.style.mark.labels]
        elif isinstance(chart, ResolvedLineChart):
            slots = [chart.style.line_mark.labels, chart.style.point_mark.labels]
        elif isinstance(chart, ResolvedAreaChart):
            slots = [chart.style.line_mark.labels]
        elif isinstance(chart, ResolvedScatterChart):
            slots = [chart.style.point_mark.labels]
        else:
            slots = []
        pairs: list[tuple[str, Rows, str]] = [
            (s.field, data, "the chart") for s in slots if s.field is not None
        ]
        if not isinstance(chart, LayeredResolvedChart):
            return pairs
        for index, layer in enumerate(chart.layers):
            layer_rows: Rows
            if layer.query_name == chart.query_name:
                layer_rows = data
            else:
                # A missing key is not necessarily a caller bug: two
                # non-production callers pass an incomplete datasets map on
                # purpose — generate_vega_lite_spec() (no per-query datasets
                # concept at all) and renderer.py's board-level
                # warning-detection pass (only threads the base chart's own
                # rows). Neither can supply this layer's own rows, so there
                # is nothing to validate for it — the same tolerant lookup
                # FacetFeature and render_cartesian_overlay use.
                own_rows = datasets.get(layer.query_name)
                layer_rows = own_rows if own_rows is not None else []
            source = f"overlay layer {index} ({layer.type}, query={layer.query_name!r})"
            pairs.extend(
                (s.field, layer_rows, source)
                for s in _layer_label_slots(layer)
                if s.field is not None
            )
        return pairs

    def apply(
        self,
        spec: ChartSpec,
        chart: ResolvedChart,
        box: RenderBox,
        datasets: dict[str | None, list[dict[str, Any]]],
    ) -> ChartSpec:
        data = chart_rows(chart, datasets).all_rows()
        # Validate any labels.field names against the actual columns it will
        # render against so a bad field fails fast (Vega-Lite silently
        # renders empty text otherwise). Each pair carries its own rows —
        # see _collect_label_fields. Guard per-pair on rows being non-empty:
        # no data → nothing to validate against, same as the channel-column
        # gate in compile/resolve/chart/_channels.py:_channels_for.
        for field, rows, source in self._collect_label_fields(chart, data, datasets):
            if not rows:
                continue
            available = set(rows[0])
            if field not in available:
                raise ChartDataError.from_code(
                    ERR_LABELS_FIELD_NOT_FOUND,
                    chart_id=chart.id,
                    field=field,
                    source=source,
                    available=sorted(available),
                )
        # A base chart with overlay `layers:` renders as a mark="layered" wrapper
        # (built by render_cartesian_overlay, which also injects each overlay
        # layer's OWN value-label text layer from that layer's own mark style).
        # The base chart's own labels still dispatch normally below — appending
        # onto spec.layers (the outer layered spec's sublayer list) is correct
        # here too: a sublayer with no explicit `.data` inherits the top-level
        # data BoardRenderSession.emit_chart stamps after features run, which is
        # the base chart's own rows.
        # applies_to() already isinstance-gates on the same five families, so a
        # missing key here means that gate and _HANDLERS have drifted apart —
        # let it raise instead of silently no-oping.
        result = self._HANDLERS[chart.chart_type](self, spec, chart, box, data)
        resolve_scale = result.resolve.get("scale")
        if resolve_scale is not None and resolve_scale.get("y") == "independent":
            _suppress_label_layer_axes(result)
        return result

    def _apply_bar(
        self, spec: ChartSpec, chart: ResolvedBarChart, _box: RenderBox, data: Rows
    ) -> ChartSpec:
        # Wide charts fold y via VL's transform; value-label positions would
        # stack at band center against the synthetic WIDE_VALUE_FIELD.
        if chart.wide_measures:
            return spec
        y = chart.y
        if not isinstance(y, str):
            return spec

        is_horiz = chart.orientation == "horizontal"
        is_stacked = chart.stack not in (None, False, "none")

        self._append_bar_segment_label_layer(spec, chart, y, is_horiz, is_stacked, data)
        self._append_bar_total_label_layer(spec, chart, y, is_horiz, is_stacked)

        return spec

    def _append_bar_segment_label_layer(
        self,
        spec: ChartSpec,
        chart: ResolvedBarChart,
        y: str,
        is_horiz: bool,
        is_stacked: bool,
        data: Rows,
    ) -> None:
        """Append the per-segment value-label text layer, if visible."""
        labels = chart.style.mark.labels
        if labels.visible is not True:
            return
        # Every bit of stack geometry comes off the emitted bar encoding — see
        # _emitted_stack. The label must land on what VL actually lays out.
        measure_channel = "x" if is_horiz else "y"
        stack_offset, stack_sort = _emitted_stack(spec, measure_channel)
        text_layer = _build_bar_text_layer(
            labels,
            y,
            is_horiz,
            is_stacked,
            chart.background,
            is_house=chart.style.label_is_house,
            label_is_text=labels_draw_text(labels, data),
            category_field=chart.x if isinstance(chart.x, str) else None,
            stack_offset=stack_offset,
            stack_sort=stack_sort,
        )
        # Move text-layer transforms to the outer spec's top-level transform
        # list. When a VL sub-layer carries its own transform array, vl-convert
        # discards the outer spec's y.sort and falls back to alphabetical
        # nominal ordering (breaks bar sort for label positions
        # middle/bottom/middle_aligned). The __-prefixed computed fields are
        # harmless on the outer spec and inherited by all layers, so hoisting
        # them preserves y.sort.
        layer_transforms = text_layer.get("transform", [])
        if layer_transforms:
            _hoist_sort_field_calculate(spec, stack_sort)
            spec.transforms.extend(layer_transforms)

        enc = dict(text_layer.get("encoding", {}))
        # For standard (non-midpoint) positions: add the categorical axis
        # explicitly so the text layer carries both x and y.
        # apply_chart_support_table_post_pass moves the outer encoding into a
        # sub-layer; without explicit positional channels the text layer loses
        # its position. Middle/middle_aligned use a synthetic midpoint field in
        # the measure axis and rely on outer-encoding category inheritance, so
        # they are exempt (tests assert the category is NOT explicitly in their
        # encoding).
        category_channel = "y" if is_horiz else "x"
        effective_pos = labels.position if labels.position is not None else "above"
        if is_stacked and effective_pos == "above":
            effective_pos = "top"
        hide_test = _fit_hide_test(
            chart,
            y,
            is_horiz,
            effective_pos,
            is_stacked,
            measure_span=_measure_span(spec, measure_channel),
            stack_offset=stack_offset,
        )
        if hide_test is not None:
            enc["text"] = {
                **enc["text"],
                "condition": {"test": hide_test, "value": ""},
            }
        # Only the un-stacked midpoints inherit the category from the outer
        # encoding; the stacked one carries it explicitly like every other
        # position, so the support-table post-pass can't strip its position.
        inherits_category = not is_stacked and effective_pos in {
            "middle",
            "middle_aligned",
        }
        if (
            category_channel not in enc
            and chart.x is not None
            and not inherits_category
        ):
            enc[category_channel] = {"field": chart.x, "type": "nominal"}

        spec.layers.append(
            ChartSpec(
                mark="text",
                mark_props={k: v for k, v in text_layer["mark"].items() if k != "type"},
                encoding=enc,
                value_label=True,
            )
        )

    def _append_bar_total_label_layer(
        self,
        spec: ChartSpec,
        chart: ResolvedBarChart,
        y: str,
        is_horiz: bool,
        is_stacked: bool,
    ) -> None:
        """Append the stack-total label text layer, if visible.

        Additive and independent of the per-segment `labels` layer. No-op (no
        error) when the chart isn't stacked or has no category field.
        """
        total_label = chart.style.mark.total_label
        if total_label.visible is not True or not is_stacked or chart.x is None:
            return
        total_layer = _build_bar_total_label_layer(
            total_label, y, chart.x, is_horiz, is_house=chart.style.total_label_is_house
        )
        # _build_bar_total_label_layer always includes "transform" and "encoding"
        # — access directly (no .get() fallback needed).
        spec.transforms.extend(total_layer["transform"])
        spec.layers.append(
            ChartSpec(
                mark="text",
                mark_props={
                    k: v for k, v in total_layer["mark"].items() if k != "type"
                },
                encoding=dict(total_layer["encoding"]),
            )
        )

    def _apply_line(
        self, spec: ChartSpec, chart: ResolvedLineChart, _box: RenderBox, data: Rows
    ) -> ChartSpec:
        # marks.point.labels is an alias for marks.line.labels on line charts.
        # The resolver places per-chart point overrides in point_mark.labels;
        # prefer line_mark.labels (explicit), fall back to point_mark.labels (alias).
        line_labels = chart.style.line_mark.labels
        point_labels = chart.style.point_mark.labels
        use_line = line_labels.visible is True
        labels: PointLabelsStyle = line_labels if use_line else point_labels
        if labels.visible is not True:
            return spec
        if chart.wide_measures:
            return spec
        y = chart.y
        if not isinstance(y, str):
            return spec

        is_house = (
            chart.style.line_label_is_house
            if use_line
            else chart.style.point_label_is_house
        )
        for text_layer in build_line_text_layers(
            labels,
            y,
            is_house,
            labels_draw_text(labels, data),
            _base_band_anchor(chart.x, y, chart.style.line_mark.curve, spec)
            if chart.x is not None
            else None,
            measure_channel="y",
        ):
            spec.layers.append(text_layer_spec(text_layer))
        return spec

    def _apply_area(
        self, spec: ChartSpec, chart: ResolvedAreaChart, _box: RenderBox, data: Rows
    ) -> ChartSpec:
        labels = chart.style.line_mark.labels
        if labels.visible is not True:
            return spec
        if chart.wide_measures:
            return spec
        y = chart.y
        if not isinstance(y, str):
            return spec

        for text_layer in build_line_text_layers(
            labels,
            y,
            chart.style.label_is_house,
            labels_draw_text(labels, data),
            _base_band_anchor(chart.x, y, chart.style.area_mark.curve, spec)
            if chart.x is not None
            else None,
            measure_channel="y",
        ):
            spec.layers.append(text_layer_spec(text_layer))
        return spec

    def _apply_scatter(
        self, spec: ChartSpec, chart: ResolvedScatterChart, _box: RenderBox, data: Rows
    ) -> ChartSpec:
        labels = chart.style.point_mark.labels
        if labels.visible is not True:
            return spec
        # No wide_measures early-return, unlike _apply_bar/_apply_line/_apply_area:
        # those guard against value-label positions stacking at one shared band
        # center against the synthetic WIDE_VALUE_FIELD. A scatter point has no
        # band to stack against -- each folded row already carries its own real
        # x/y position, so a label built off WIDE_VALUE_FIELD (chart.y here) is
        # correctly positioned per point, same as the color-coded mark itself.
        y = chart.y
        if not isinstance(y, str):
            return spec

        # Bubble size encoding must not reach the text label layer via inheritance.
        # Move it off the shared outer encoding onto the main (point) layer only.
        if "size" in spec.encoding:
            spec.main_layer_encoding["size"] = spec.encoding.pop("size")

        spec.layers.append(
            text_layer_spec(
                _build_point_text_layer(
                    labels,
                    y,
                    is_house=chart.style.label_is_house,
                    label_is_text=labels_draw_text(labels, data),
                    measure_channel="y",
                )
            )
        )
        return spec

    _HANDLERS: ClassVar[dict[str, Callable[..., ChartSpec]]] = {
        "bar": _apply_bar,
        # histogram: no authored y field → _apply_bar exits immediately (no-op)
        "histogram": _apply_bar,
        "line": _apply_line,
        "area": _apply_area,
        "scatter": _apply_scatter,
    }
