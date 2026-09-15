"""Shared cartesian encoding primitives for render-v2 emitters.

These helpers extract verbatim-identical logic from the line and area emitters.
Each primitive is genuinely shared (2+ call sites), accepts explicit args, and
never takes board_style — preserving the no-board_style emitter invariant.

Convention: every primitive here is a pure builder — it returns a value and
never mutates an argument in place. Exception: ``nest_zero_rule`` mutates
``entry`` in place (appends to ``entry.layers`` when already ``mark="layered"``,
and always pins ``nice: false`` on its own scale dict when a headroom bound is
present) — every call site already treats its return value as authoritative,
so the mutation is harmless, but it is a real exception to this convention.
"""

from __future__ import annotations

import json
from collections.abc import Callable
from typing import Any, Literal, NamedTuple

from dbt_charts.core.compile.config import get_chart_rendering
from dbt_charts.core.compile.models.chart.authored._annotations import ChartSort
from dbt_charts.core.compile.models.chart.authored._base import MultiplesConfig
from dbt_charts.core.compile.models.chart.resolved import (
    PartitionAxis,
    ResolvedChart,
)
from dbt_charts.core.compile.models.chart.resolved._base import (
    _CartesianResolvedChartFields,
)
from dbt_charts.core.compile.models.chart.resolved._layer import (
    LayeredResolvedChart,
)
from dbt_charts.core.compile.models.chart.resolved.area import ResolvedAreaChart
from dbt_charts.core.compile.models.chart.resolved.bar import ResolvedBarChart
from dbt_charts.core.compile.models.chart.resolved.heatmap import ResolvedHeatmapChart
from dbt_charts.core.compile.models.chart.resolved.line import ResolvedLineChart
from dbt_charts.core.compile.models.primitives import ResolvedFontStyle
from dbt_charts.core.compile.models.style.resolved._base import (
    ResolvedAxisStyle,
    ResolvedScaleStyle,
)
from dbt_charts.core.compile.models.style.theme.category_colors import (
    CategoryColorScale,
    color_at,
)
from dbt_charts.core.compile.resolve.chart._chart_rows import regroup
from dbt_charts.core.compile.resolve.chart._wide_fields import wide_measure_labels_for
from dbt_charts.core.compile.resolve.chart.tick_values import (
    numeric_domain_bounds,
)
from dbt_charts.core.diagnostics.chart_data import ChartDataError
from dbt_charts.core.diagnostics.codes_render import (
    ERR_SCALE_DOMAIN_REQUIRES_CONTINUOUS_X,
)
from dbt_charts.core.font_measure import get_font_measurer
from dbt_charts.core.render.chart._types import VLDict
from dbt_charts.core.render.chart.artifacts import ChartRenderData
from dbt_charts.core.render.chart.axis_label_collision import (
    AxisLabelCollision,
    record_axis_label_collision,
)
from dbt_charts.core.render.chart.emitters._label_overlap import resolve_axis_x_overlap
from dbt_charts.core.render.chart.spec import ChartSpec, RenderBox
from dbt_charts.core.render.chart.text_truncation import record_text_truncation
from dbt_charts.core.render.chart.time_unit_detect import (
    BUCKETED_CALENDAR_UNITS,
    canonicalize_and_sort_ordinal_x,
    detect_time_unit,
    normalize_labeled_temporal,
    vl_time_unit,
)
from dbt_charts.core.render.chart.type_inference import (
    build_cartesian_x_encoding,
    infer_vega_type_from_data,
    is_zero_anchored,
    resolve_cartesian_x_type,
    temporal_edge_labels_flushed,
    y_zero_scale,
    zero_anchor_pinned_floor,
)
from dbt_charts.core.render.chart.vl_field_maps import (
    axis_to_vl,
    bake_tick_ladder,
    emit_resolved_scale_vl,
)
from dbt_charts.core.render.chart.x_domain import vl_sort_op
from dbt_charts.core.render.utils import normalize_scalar_for_json
from dbt_charts.core.text.case import default_axis_title
from dbt_charts.core.text.predefined_formats import (
    PREDEFINED_SPECS,
    PredefinedNumberFormat,
)
from dbt_charts.core.utils import (
    DEFAULT_VL_LABEL_LIMIT,
    Rows,
    bar_sort_op,
    numeric_column_values,
    x_domain_order,
)
from mdsvg.fonts import (
    measure_text_precise,
    truncate_text_precise,
    wrap_text_precise,
)

# dbt charts ChartSort.order ("asc"/"desc") → Vega-Lite sort.order. VL silently
# falls back to ascending on the raw dbt charts form, so translate at emit (mirrors
# V1 profile._SORT_ORDER_VL).
_SORT_ORDER_VL = {"asc": "ascending", "desc": "descending"}

# VL scale types on which an explicit [low, high] domain is a valid continuous
# range override. Every other cartesian-x type (ordinal/nominal band scales)
# reads a 2-element domain as exactly two category values instead.
_CONTINUOUS_VL_TYPES = frozenset({"temporal", "quantitative"})

# The complement: cartesian-x types that resolve to a discrete band scale.
_BAND_X_TYPES = frozenset({"ordinal", "nominal"})


def x_encoding_is_banded(x_enc: VLDict) -> bool:
    """True when a cartesian position channel's emitted encoding sits on a
    discrete band/point scale — nominal/ordinal, or a bucketed-calendar
    temporal (``timeUnit`` present).

    False for a genuinely continuous position: quantitative, or a temporal
    channel wide enough to have been promoted past ``max_ordinal_buckets``
    (raw temporal, no ``timeUnit``). A discrete scale is what an ``xOffset``/
    ``yOffset`` sub-scale needs to divide bars within — on a continuous
    position there is no band step for it to divide, so any offset channel
    riding along is inert for positioning and must not be trusted to size a
    mark either.
    """
    return x_enc.get("type") in _BAND_X_TYPES or "timeUnit" in x_enc


# Smallest outer padding that lifts a band scale's first tick off the exact
# range start. Expressed as a fraction of the step, so at any realistic plot
# width it moves a band edge by well under a thousandth of a pixel (measured:
# 0.0001px at 480px across 7/13/31 bands) — below the SVG coordinate precision
# we emit, and orders of magnitude below anything visible.
_FIRST_BAND_TICK_EPSILON = 1e-6


def nudge_band_scale_off_range_start(
    x_scale: VLDict, vl_type: str, ax_vl: VLDict, band_doubled: bool
) -> None:
    """Keep Vega from culling a band scale's FIRST axis label.

    Vega drops the label on the first band at isolated plot widths whenever
    the axis carries an explicit ``values`` list and the scale's range starts
    at exactly zero — present at one width, gone half a pixel later, present
    again half a pixel after that. Verified against vl_convert 1.9.0 across
    band counts and thousands of half-pixel widths: it is always the first
    band and never any other, it fires for any ``values`` list containing that
    band (a full-domain list and a thinned cadence list alike), and it is
    unaffected by ``labelOverlap``, ``labelBound``, ``labelFlush``,
    ``labelLimit``, ``labelSeparation``, or the label's own text. Omitting
    ``values`` avoids it, but ``values`` carries the label cadence ladder and
    cannot be dropped. It is a rounding fault at the range start, so an
    epsilon of outer padding is enough to clear it.

    ``band_doubled`` suppresses the nudge for the band-aware ``step`` curve
    (``step_band.py``), whose geometry depends on band *k*'s right edge and
    band *k+1*'s left edge being the same float: it doubles each row onto both
    edges and lets ``step-after`` put the riser on the shared boundary. Outer
    padding separates those two coordinates, so the pairing breaks and risers
    land on the wrong edge — measured as 41.56px plateau shifts on
    ``quick-guide_5``. The epsilon is tiny in displacement but an exact
    equality is load-bearing there, so "too small to see" is not the relevant
    question. A band-step chart therefore keeps the cull; its plateau geometry
    is the larger visual contract.

    Deliberately NOT ``align``: pinning ``align: 0`` also clears the cull, and
    is inert only when the scale has no slack to distribute. Band scales here
    do sometimes carry slack, and against it ``align: 0`` shoves every band to
    one side — it moved a chart's whole plot area 47px (0.75 of a band). This
    nudge's effect is bounded by ``_FIRST_BAND_TICK_EPSILON`` regardless of
    how much slack exists.

    Only fires where dbt charts asked for flush-to-edge bands
    (``padding: 0``); an authored outer padding is already off the range start
    and is left exactly as authored. Scatter is out of scope either way — it
    rebuilds its own scale from ``x_res`` rather than carrying this one, so
    the mutation never reaches its spec.
    """
    if band_doubled or vl_type not in _BAND_X_TYPES or "values" not in ax_vl:
        return
    if x_scale.get("padding") != 0:
        return
    x_scale["paddingOuter"] = _FIRST_BAND_TICK_EPSILON


def cartesian_x_scale_domain(
    scale: ResolvedScaleStyle | None, vl_type: str, chart_id: str
) -> VLDict:
    """Return the VL x-scale ``domain`` entry for an authored cartesian x scale.

    Empty dict when no domain is authored. Raises ``ERR-SCALE-DOMAIN-
    REQUIRES-CONTINUOUS-X`` when a domain is authored but the x-axis resolved
    to an ordinal/nominal (band) type: Vega-Lite reads a 2-element domain on
    a band scale as exactly two categories, collapsing every mark onto the
    first one, rather than extending a continuous range.
    """
    _sc_cont = scale.continuous if scale is not None else None
    if _sc_cont is None or _sc_cont.domain is None:
        return {}
    if vl_type not in _CONTINUOUS_VL_TYPES:
        raise ChartDataError.from_code(
            ERR_SCALE_DOMAIN_REQUIRES_CONTINUOUS_X,
            chart_id=chart_id,
            vl_type=vl_type,
        )
    return {"domain": list(_sc_cont.domain)}


def chart_sort_to_vl(sort: ChartSort | None) -> VLDict | None:
    """Map an authored ``ChartSort`` to a field-based VL sort dict.

    Returns ``None`` when no sort is authored — callers spread that straight
    into an encoding's ``sort`` (VL treats ``None`` as "no explicit order").
    """
    if sort is None:
        return None
    return {"field": sort.by, "order": _SORT_ORDER_VL[sort.order]}


def dimension_sort_to_vl(sort: ChartSort | None) -> VLDict | None:
    """``chart_sort_to_vl`` for a dimension axis, with VL's aggregate pinned.

    A dimension axis orders its categories by the sort column's own value, so
    the aggregate over a category's rows is ``min`` — never ``sum``, which is a
    stacking total rather than the category's value. Pinned rather than left to
    VL's inference: that default varies with the composed spec (mark, stack,
    sub-layer split), and ``render/chart/x_domain.py`` has to reproduce the
    rendered order exactly.
    """
    vl_sort = chart_sort_to_vl(sort)
    return None if vl_sort is None else {**vl_sort, "op": "min"}


def bar_sort_to_vl(
    sort: ChartSort | None, measure_field: str | None, stacked: bool
) -> VLDict | None:
    """``chart_sort_to_vl`` for a bar's categorical axis, with the aggregate pinned.

    Bar takes ``bar_sort_op``'s verdict rather than ``dimension_sort_to_vl``'s
    flat ``min`` because a stacked bar sorted by its own measure IS sorting by
    the stacked total. Pinned for the same reason a dimension axis pins: VL's
    own inference moves with the composed spec (``sum`` under a stacking mark,
    ``min`` under ``stack: null``), and ``render/chart/x_domain.py`` has to
    reproduce the rendered order exactly.
    """
    vl_sort = chart_sort_to_vl(sort)
    if vl_sort is None:
        return None
    return {**vl_sort, "op": bar_sort_op(vl_sort["field"], measure_field, stacked)}


# The families that build a dimension x through these helpers — narrower than
# ``ResolvedChart`` (which the facet-narrowing predicate takes) but concrete,
# where ``_CartesianResolvedChartFields`` is the shared field protocol.
ResolvedCartesianChart = (
    ResolvedAreaChart | ResolvedLineChart | ResolvedHeatmapChart | ResolvedBarChart
)

_CATEGORICAL_X_TYPES = ("nominal", "ordinal")


def pin_sorted_x_domain(
    x_enc: VLDict, data: ChartRenderData, chart: ResolvedCartesianChart
) -> None:
    """Pin the authored sort's category order as an explicit ``scale.domain``,
    in place. No-op on an unsorted or continuous x.

    Vega-Lite applies a field sort AFTER the transforms an emitter puts on the
    sub-layers: a stacked area's ``impute`` injects a 0 for every series a
    category is missing, and that injected value becomes the category's
    aggregate. On a ragged grid — a series starting mid-period, the ordinary
    case — VL therefore orders by numbers that are not in the query, and no
    aggregate over the raw rows reproduces it.

    Every read-back consumer trusts ``x_domain.rendered_x_domain`` to say what
    VL drew (the endpoint rail, band value labels, the overlay reconciler that
    pins the union). Stating the domain removes the inference from the loop:
    an explicit domain outranks ``sort`` in Vega-Lite, so VL draws the order
    ``x_domain_order`` computed, and the prediction is the drawing.

    A small-multiples x resolved per panel is the exception: narrowing trims
    each panel's scale to its own rows, and one explicit domain applies to
    every independent scale, so pinning would paint back the empty category
    slots narrowing exists to remove.
    """
    sort = x_enc.get("sort")
    if not isinstance(sort, dict) or x_enc.get("type") not in _CATEGORICAL_X_TYPES:
        return
    field = x_enc.get("field")
    if not isinstance(field, str):
        return
    if chart.multiples is not None and "x" in _domain_subset_narrowing_candidates(
        chart, chart.multiples, data
    ):
        return
    domain = x_domain_order(
        data,
        field,
        sort["field"],
        sort["order"] == "descending",
        op=vl_sort_op(sort),
    )
    if domain:
        # Domain values land in the spec without passing through
        # `normalize_data_types`, so a raw date/Decimal would reach
        # vl_convert's JSON serialization unconverted (`render/utils.py`).
        x_enc.setdefault("scale", {})["domain"] = [
            normalize_scalar_for_json(value) for value in domain
        ]


def build_palette_config(palette: tuple[str, ...] | None) -> VLDict:
    """Build config.range.category from a resolved palette.

    Returns an empty dict when palette is absent — the emitter can safely
    spread this into the ChartSpec config without special-casing.
    """
    if palette:
        return {"range": {"category": list(palette)}}
    return {}


def canonicalize_cartesian_x_data(
    data: ChartRenderData,
    x_field: str,
    authored_time_unit: str | None,
    *,
    skip_bucket_collapse: bool = False,
) -> tuple[ChartRenderData, bool]:
    """Normalize labeled-temporal x values and canonicalize date-like ones.

    ``build_cartesian_x_encoding`` emits ``axis.values``/``labelExpr``/
    ``timeUnit`` against the raw domain value, so the caller owns making that
    value JS-``Date``-parseable first — bar/line/area establish this via
    ``normalize_labeled_temporal`` (rewrites a labeled bucket string like
    "Q1 2024" to its ISO date) plus, for line/area, ``canonicalize_and_sort_
    ordinal_x`` (collapses a ``datetime.datetime``/non-date-only ISO value to
    date-only ISO — see ``_ordinal_bucket_key``'s docstring for the exact
    hazard: an ISO datetime string left as-is never matches its band-scale
    peers). Heatmap and scatter have no gap-fill scaffold of their own, so
    this is the minimal precondition pair for them.

    ``authored_time_unit`` is the axis's own authored ``time_unit`` FOR
    ``x_field`` — pass ``None`` when ``x_field`` isn't the axis's own field
    (e.g. an overlay layer's independently-authored ``x`` column), so the
    bucket grain is auto-detected from that field's own rows instead of a
    grain authored for a different column collapsing it wholesale.

    ``skip_bucket_collapse`` mirrors ``gap_fill_ordinal_time``'s own
    ``resolve_authored_x_type(ax) == "temporal"`` early return (see
    ``_channels.py``): an authored continuous-temporal x axis never bucket-
    collapses at all, so a value sharing that axis's scale must skip it too,
    or the two land on different JS-``Date``-parseable string forms (a raw
    naive ``datetime`` vs. a date-only ISO string).

    Gated on the same authored-or-detected bucketed calendar grain
    (``BUCKETED_CALENDAR_UNITS``) that ``resolve_cartesian_x_type`` resolves
    for ``build_cartesian_x_encoding`` — detecting alone (the pre-fix gate)
    disagreed with that authored-or-detected resolution: an authored
    ``axis_x.time_unit`` on a non-midnight ``datetime`` reached
    ``build_cartesian_x_encoding``'s ``axis.values`` (stringified via
    ``.isoformat()``) uncanonicalized here, while the row itself stringified
    via ``str()`` — the two never matched, so a nominal/ordinal band scale
    painted zero labels. A plain numeric or nominal x — heatmap's hour-of-day
    grid, a genuinely categorical scatter x — never resolves "temporal" and
    so never reaches this gate. A *sub-daily* temporal x (any nonzero
    hour/minute/second) with no authored bucketing resolves "temporal" but
    ``detect_time_unit`` returns ``None`` for it (its own explicit sub-daily
    fallthrough) — that value is a continuous timestamp, not a date, and
    truncating it to ``.date().isoformat()`` would silently collapse distinct
    rows onto the same day.

    Returns ``(data, transformed)``; when True the caller must stamp the
    returned rows onto ``ChartSpec.data`` (mirrors ``line.py``'s
    ``_normalize_line_data``) so the render session does not overwrite the
    spec with the original, uncanonicalized rows.
    """
    if not data or not x_field:
        return data, False
    normalized = normalize_labeled_temporal(data, x_field)
    transformed = normalized is not data
    data = normalized
    if (
        not skip_bucket_collapse
        and infer_vega_type_from_data(data, x_field) == "temporal"
    ):
        time_unit: str | None
        if authored_time_unit is not None:
            time_unit = authored_time_unit
        else:
            x_values = [row.get(x_field) for row in data if x_field in row]
            time_unit = detect_time_unit(x_values)
        if time_unit in BUCKETED_CALENDAR_UNITS:
            data = canonicalize_and_sort_ordinal_x(data, x_field)
            transformed = True
    return data, transformed


class CartesianXResolution(NamedTuple):
    """Resolved x encoding type, axis VL dict, x scale, and optional VL timeUnit."""

    vl_type: str
    axis: VLDict
    scale: VLDict
    time_unit: str | None = None  # utc-prefixed timeUnit for temporal escape-hatch
    # AxisLabelLayout.label_block_height for the angle resolved below — how
    # much vertical room the labels take at that tilt. None on the caller's
    # own no-x-field fallback, where no axis was resolved to measure.
    label_block_height: float | None = None


def resolve_cartesian_x(
    x_field: str,
    data: list[dict[str, Any]],
    ax: ResolvedAxisStyle,
    label_usable_ratio: float,
    chart_width: float,
    chart_id: str,
    mark_type: str,
    curve: str | None = None,
    band_doubled: bool = False,
    domain_values: list[Any] | None = None,  # type-state: explicit_any — raw x values
    reserved_width: float = 0.0,
    *,
    panel_fields: tuple[str, ...],
) -> CartesianXResolution:
    """Resolve x encoding type, axis VL dict, and x scale for a cartesian x field.

    Mirrors the identical block in the line and area emitters: overlap
    resolution → axis_to_vl → build_cartesian_x_encoding, plus extracting
    scale.padding/domain from the authored axis style. Caller guards
    ``x_field is not None`` before calling. ``chart_id`` names the chart in
    the error raised by ``cartesian_x_scale_domain`` when an authored domain
    doesn't fit the resolved scale type. ``mark_type`` ("line"/"area") and
    ``curve`` (the authored line/area curve style) are forwarded to
    ``build_cartesian_x_encoding`` for the bucketed-grain type split.
    ``band_doubled`` reports whether any overlay LAYER band-doubles this same
    scale; the base's own ``curve`` cannot see that, and either side is enough
    to make adjacent band edges load-bearing. ``domain_values`` is the x
    scale's full band domain when overlay layers extend it past this axis's
    own rows (``overlay_x_domain_values``) — forwarded to both the crowding
    measurement and the tick-value/cadence build, mirroring bar's own overlay
    handling (``bar.py``'s ``_emit_vertical``).

    ``reserved_width`` is horizontal chrome the caller already knows about
    and this function does not — chiefly the endpoint-label rail
    (``emitters/_endpoint_rail.py``'s ``resolve_endpoint_rail_span``), which
    is sized from the same series names/font the feature pass will draw it
    with, before the crowding decision below ever runs. Subtracted from
    ``chart_width`` before the label-fit walk measures against it, so
    ``label_usable_ratio`` is applied to the chart's real plot width instead
    of its full outer slot.

    ``panel_fields`` is the chart's small-multiples partition columns,
    forwarded verbatim to ``resolve_cartesian_x_type`` and
    ``build_cartesian_x_encoding`` so both the ordinal/temporal verdict and
    the scaffold-budget gate measure per panel, not pooled across panels
    (see ``resolve_cartesian_x_type``'s docstring). Keyword-only with no
    default: line/area/scatter now consult the same gate bar does, so
    a caller that forgot small multiples would silently pool the span and
    flip a chart with individually-dense panels to continuous — the exact
    failure ``ordinal_scaffold_within_budget`` exists to prevent.
    """
    from dbt_charts.core.render.chart.step_band import BAND_STEP_CURVE

    vl_type, _, resolved_x_time_unit = resolve_cartesian_x_type(
        data, x_field, ax, mark_type, curve == BAND_STEP_CURVE, panel_fields
    )
    label_layout = resolve_axis_x_overlap(
        ax,
        x_field,
        data,
        label_usable_ratio,
        bucket_aligned_temporal=curve == "step",
        edge_labels_flushed=temporal_edge_labels_flushed(vl_type, ax),
        chart_width=chart_width - reserved_width,
        domain_values=domain_values,
        resolved_time_unit=resolved_x_time_unit,
    )
    if label_layout.collision_label_count is not None:
        record_axis_label_collision(
            chart_id,
            AxisLabelCollision(
                field=x_field, label_count=label_layout.collision_label_count
            ),
        )
    ax_vl_raw = axis_to_vl(
        ax,
        label_overlap=label_layout.label_overlap,
        label_angle=label_layout.angle,
    )
    x_scale: VLDict = {}
    if ax.scale is not None and ax.scale.padding is not None:
        x_scale["padding"] = ax.scale.padding
    vl_type, ax_vl, detected_tu = build_cartesian_x_encoding(
        data,
        x_field,
        ax,
        ax_vl_raw,
        mark_type,
        curve,
        format_time_unit=label_layout.format_time_unit,
        visibility_time_unit=label_layout.visibility_time_unit,
        label_anchor_index=label_layout.anchor_index,
        domain_values=domain_values,
        outer_chart_width=chart_width,
        plot_width=chart_width - reserved_width,
        panel_fields=panel_fields,
    )
    x_scale.update(cartesian_x_scale_domain(ax.scale, vl_type, chart_id))
    nudge_band_scale_off_range_start(
        x_scale, vl_type, ax_vl, band_doubled or curve == BAND_STEP_CURVE
    )
    # Temporal path: emit utc-prefixed timeUnit when a bucketed unit was detected
    # (either authored or auto-detected). Skip "none" (continuous temporal) and
    # time-part units like monthofyear that vl_time_unit handles via _TIME_UNIT_TO_VL.
    enc_time_unit: str | None = None
    if vl_type == "temporal" and detected_tu is not None and detected_tu != "none":
        enc_time_unit = vl_time_unit(detected_tu)
    return CartesianXResolution(
        vl_type, ax_vl, x_scale, enc_time_unit, label_layout.label_block_height
    )


# An axis title Vega-Lite will render: a plain string, or one entry per line
# when dbt charts pre-wrapped it to fit the axis (VL renders a list as lines but
# never computes the breaks itself).
AxisTitle = str | list[str] | None


class XYTitles(NamedTuple):
    """Resolved x/y axis titles for a cartesian chart.

    ``x_title`` / ``y_title`` are what Vega-Lite renders: a ``list[str]`` when
    the title had to be wrapped to fit its axis (VL draws one line per entry).
    ``x_plain`` / ``y_plain`` are those same titles before wrapping — the value
    to use anywhere the title is *data* rather than layout, such as the
    base-series legend label a layered chart builds its color scale domain
    from. Wrapping is a display concern and must not leak into a datum.

    Both channels carry a plain form because these titles are in VL channel
    space, not authored space: the legend label a layered chart needs is the
    MEASURE title, and a horizontal bar draws its measure on ``x``.
    """

    x_title: AxisTitle
    y_title: AxisTitle
    x_plain: str | None
    y_plain: str | None


def axis_title_budget(extent: float, panels: int = 1) -> int:
    """Pixels an axis title may occupy along the axis it labels.

    ``extent`` is the chart's size in the title's own direction: height for a
    left/right axis (whose title renders rotated), width for top/bottom. The
    layout chrome outside the plot is subtracted so the title competes for the
    space it can actually have.

    ``panels`` is how many facet panels that direction was divided across, so
    each panel owes its share of a chrome the whole chart pays once. Charging a
    110px panel of a 770px card the full 72px leaves it 38px it never lost.
    """
    return max(int(extent - _LAYOUT_CHROME_PX / panels), 1)


def wrap_axis_title(
    text: str, extent: float, font: ResolvedFontStyle, panels: int = 1
) -> tuple[str | list[str], bool]:
    """Wrap an axis title to at most two lines that fit ``extent``.

    Vega-Lite renders ``axis.title`` as a list of lines but never computes the
    breaks itself, and it caps nothing by default — an over-long title is drawn
    at full length and ``autosize: fit`` shrinks the plot to make room, to the
    point of collapsing it entirely. Wrapping here is what bounds it.

    Returns ``(title, truncated)`` where ``title`` is the wrapped value passed
    to Vega-Lite (a single string or a list of lines) and ``truncated`` is
    ``True`` when the ellipsis fired — i.e. authored text was cut. A single
    string is returned when the text already fits, so charts that render
    correctly today are unaffected. That string is whitespace-normalized by
    the wrapper (runs of spaces collapse), not byte-identical to the input.
    """
    budget = axis_title_budget(extent, panels)
    measurer = get_font_measurer(font.family)
    # mdsvg's wrapper splits an over-wide token before wrapping — right for a
    # URL or an identifier in prose, wrong for an axis title, where a broken
    # word is simply wrong output. Ellipsize such a word here so the wrapper
    # never meets one it has to break. This also has to be its own truncation
    # signal: a split word still fits in ``max_lines``, so line count alone
    # reports nothing.
    authored_words = text.split()
    words = [
        word
        if measure_text_precise(word, font.size, measurer) <= budget
        else truncate_text_precise(word, budget, font.size, measurer, ellipsis=True)
        for word in authored_words
    ]
    lines, truncated = wrap_text_precise(
        " ".join(words),
        budget,
        font.size,
        measurer,
        max_lines=2,
        ellipsis=True,
    )
    # wrap_text_precise strips and normalizes whitespace, so a blank label
    # (``x_label: " "`` — how an author blanks an axis title without deleting
    # the key) wraps to no lines at all. Hand that text straight back: a blank
    # title stays blank, as it rendered before any wrapping existed.
    if not lines:
        return text, False
    return (lines if len(lines) > 1 else lines[0]), truncated or words != authored_words


def wide_measures_title(measures: tuple[str, ...]) -> str:
    """Y-axis title for a folded multi-measure (wide ``y: [...]``) chart.

    Joins each measure's humanized label
    (``_wide_fields.wide_measure_labels_for``) -- the same
    derivation a single ``y:`` field's title already uses via
    ``default_axis_title``, generalized to the list case, since the
    measure names ARE real authored fields (unlike the synthetic
    fold-key/color field, which has no name to derive a legend title
    from). An authored ``y_label`` always overrides this -- callers pass
    it through ``resolve_xy_titles``'s ``y_label`` param, not this
    function.
    """
    wide_measure_labels = wide_measure_labels_for(measures)
    return ", ".join(wide_measure_labels[m] for m in measures)


def resolve_xy_titles(
    x_field: str | None,
    y_field: str | None,
    x_label: str | None,
    y_label: str | None,
    ax: ResolvedAxisStyle,
    ay: ResolvedAxisStyle,
    box: RenderBox,
    chart_id: str,
    x_authored_field: Literal["x_label", "y_label"] = "x_label",
    y_authored_field: Literal["x_label", "y_label"] = "y_label",
) -> XYTitles:
    """Resolve x/y axis titles: explicit authored label, else title-cased field slug.

    Titles are in Vega-Lite channel space, not authored space: a horizontal bar
    passes its authored ``y_label`` as ``x_label`` here, because that is the
    channel it renders on. Each title is then wrapped against the extent of the
    channel it lands on — width for x, height for the rotated y.

    ``x_authored_field`` and ``y_authored_field`` name which authored YAML field
    maps to each VL channel.  For vertical bars and lines, the defaults ("x_label"
    and "y_label") are correct.  Horizontal bars pass ``x_authored_field="y_label"``
    because the authored y_label renders on the VL x channel after the flip.

    When a title has to be truncated, the truncation is recorded into the active
    ``axis_title_truncation`` ContextVar sink using the authored field name so
    the ``WARN_AXIS_TITLE_TRUNCATED`` detector can emit a path that points at the
    right YAML line.  Pass ``chart_id=""`` to skip recording (e.g. in tests that
    only care about the wrapped value, not the warning).

    Handles None x/y gracefully. ``y_field`` must already be narrowed to
    ``str | None`` (callers that receive ``str | list[str]`` must narrow
    before calling). Tooltip *content* is a separate concern — built by
    ``features/structured_tooltip.py`` from the chart-axes LUT, not here.
    """
    x_text: str | None = x_label or (default_axis_title(x_field) if x_field else None)
    y_text: str | None = y_label or (default_axis_title(y_field) if y_field else None)
    x_title: AxisTitle
    y_title: AxisTitle
    if x_text:
        x_title, x_truncated = wrap_axis_title(
            x_text, box.width, ax.title.font, box.panel_cols
        )
        if x_truncated and chart_id:
            record_text_truncation(chart_id, "axis_title", x_text, x_authored_field)
    else:
        x_title = x_text
    if y_text:
        y_title, y_truncated = wrap_axis_title(
            y_text, box.height, ay.title.font, box.panel_rows
        )
        if y_truncated and chart_id:
            record_text_truncation(chart_id, "axis_title", y_text, y_authored_field)
    else:
        y_title = y_text
    return XYTitles(x_title, y_title, x_text, y_text)


def build_x_enc(
    x_field: str,
    vl_type: str,
    x_title: AxisTitle,
    ax_vl: VLDict,
    x_scale: VLDict,
    time_unit: str | None = None,
    *,
    sort: VLDict | None,
) -> VLDict:
    """Build the VL x encoding dict for a cartesian time/nominal x field.

    ``sort`` is ``dimension_sort_to_vl(chart.sort)`` — ``None`` when
    unauthored, which disables VL's default alphabetical ordering so the
    query's row order is preserved. An authored sort outranks the chronological
    row order ``canonicalize_and_sort_ordinal_x`` imposes on a bucketed-time x:
    that order is the default for an unauthored axis, not an override of one,
    and VL reads a discrete scale's domain off this key rather than off row
    order. Scale is omitted when empty. ``time_unit`` is the utc-prefixed VL
    timeUnit for temporal escape-hatch (e.g. "utcyearmonth") — omitted when
    None.
    """
    enc: VLDict = {
        "field": x_field,
        "type": vl_type,
        "title": x_title,
        "axis": ax_vl,
        "sort": sort,
    }
    if time_unit:
        enc["timeUnit"] = time_unit
    if x_scale:
        enc["scale"] = x_scale
    return enc


# Title block + view padding outside the plot along one axis. Used to size
# horizontal bars, and to budget how much of an axis its own title may occupy.
# Deliberately a little larger than the chrome actually measures (~66px), so
# the budget it leaves is under the real space rather than over it.
_LAYOUT_CHROME_PX = 72.0


# A raw query-result cell value (str/int/float/date/None) — dynamic by
# construction, not a laundered type: the concrete type depends on the
# board's own query.
_CellValue = Any  # type-state: explicit_any — raw query result cell value
# Facet panel key (one cell value per active facet dimension) -> distinct
# values of one field seen within that panel.
_PanelDomains = dict[tuple[_CellValue, ...], set[_CellValue]]


def _panel_domains(
    field: str,
    panel_axes: tuple[PartitionAxis, ...],
    data: list[dict[str, Any]],  # type-state: explicit_any — raw query result rows
) -> _PanelDomains:
    """``field``'s distinct values, grouped by resolve's own baked
    small-multiples partition (``panel_axes``) — replays the one partition
    resolve already computed (``regroup()``,
    ``compile/resolve/chart/_chart_rows.py``) rather than re-deriving panel
    membership from raw facet-field values a second time. Two authorities
    computing the same fact drift; this reads the one resolve already baked.

    Empty when the chart isn't faceted (``panel_axes == ()``) — nothing to
    group by. ``field`` may itself be one of the partition columns (the
    degenerate same-field-as-facet shape, e.g. ``y: series`` faceted by
    ``columns: series``) — ``regroup()`` strips those off each row and
    holds the single value on ``Panel.key`` instead (see ``Panel``'s own
    docstring in ``_chart_rows.py``), so that case is read off the key,
    not scanned for in rows that no longer carry it.
    """
    if not panel_axes:
        return {}
    axis_position = next(
        (i for i, axis in enumerate(panel_axes) if axis.field == field), None
    )
    panel_domains: _PanelDomains = {}
    for panel in regroup(panel_axes, data).panels:
        values = (
            {panel.key[axis_position]}
            if axis_position is not None
            else {
                row[field]
                for row in panel.rows
                if field in row and row[field] is not None
            }
        )
        if values:
            panel_domains[panel.key] = values
    return panel_domains


def widest_panel_distinct_count(
    field: str,
    panel_axes: tuple[PartitionAxis, ...],
    data: list[dict[str, Any]],  # type-state: explicit_any — raw query result rows
) -> int:
    """The most distinct ``field`` values any single small-multiples panel
    holds, replaying resolve's baked partition (``_panel_domains``).

    ``0`` when the chart isn't faceted or no panel carries any value for
    ``field`` — callers deciding a per-panel budget fall back to the
    whole-dataset count in that case (there is nothing narrower to use).
    Shared by every reader of the "narrowed panels hold at most this many
    values" fact — the render-side height floor
    (``effective_horizontal_bar_category_count`` below) and the warning
    detectors that estimate a per-band/per-slot pixel width from a
    narrowed axis (``render/warnings/bar_band_width_too_narrow.py``,
    ``render/warnings/value_labels_crowd_width.py``) — so there is one
    place computing it, not three copies that can drift.
    """
    panel_domains = _panel_domains(field, panel_axes, data)
    if not panel_domains:
        return 0
    return max(len(values) for values in panel_domains.values())


def _panel_domain_is_proper_subset(
    field: str,
    panel_axes: tuple[PartitionAxis, ...],
    data: list[dict[str, Any]],  # type-state: explicit_any — raw query result rows
) -> bool:
    """True when at least one facet panel's own rows carry a proper subset of
    ``field``'s values across the whole dataset.

    Data-driven, not name-driven: narrowing a position channel's scale to a
    panel's own domain is only worth doing — and only correct to read — when
    that panel actually holds fewer values than the chart's full domain.
    Whether ``field`` happens to be one of the facet fields is irrelevant; a
    panel keyed by one field can just as easily be sparse against a
    completely different field's domain (a region panel containing two of
    five products). A field with <= 1 distinct value overall has no "narrower"
    state to reach (avoids a spurious True forced by "1 value is 'less than'
    1 value" edge cases with malformed input).
    """
    panel_domains = _panel_domains(field, panel_axes, data)
    if not panel_domains:
        return False
    domain = {value for values in panel_domains.values() for value in values}
    if len(domain) <= 1:
        return False
    return any(len(values) < len(domain) for values in panel_domains.values())


def _domain_subset_narrowing_candidates(
    chart: ResolvedChart,
    multiples: MultiplesConfig,
    data: list[dict[str, Any]],  # type-state: explicit_any — raw query result rows
) -> frozenset[Literal["x", "y"]]:
    """VL position channels ("x"/"y") worth resolving independently per facet
    panel, because at least one panel's own rows carry a proper subset of
    that channel's field's domain (``_panel_domain_is_proper_subset``) —
    whatever the field is called, not only when it happens to equal one of
    the facet fields. A panel that DOES contain the full domain gains
    nothing from narrowing, so it is left alone; VL's facet default (a
    scale shared and unioned across every panel) is already correct there.

    Width-oblivious: this is the domain-and-mirror layer only. It never
    checks whether the columns/grid-facet "y" case is actually affordable —
    ``facet_bound_position_channels()`` (below) adds that on top, and this
    function is also what ``facet_extra_axis_width_px()`` calls to decide
    whether measuring is worth doing at all, before affordability can even
    be evaluated (affordability needs the measurement's own result). Calling
    the public, affordability-aware predicate from inside the measurement
    that predicate itself depends on would recurse; this split breaks that.

    VL already draws an axis for the row-varying channel (VL "y") once per
    row panel, and for the column-varying channel (VL "x") once per column
    panel — narrowing costs no extra space there. Under a `columns` (or
    grid) facet, VL otherwise draws the "y" axis only once, shared down the
    left edge; forcing it independent paints a whole extra axis inside every
    column panel. `facet_panel_width()` budgets that extra width itself
    (see `facet_extra_axis_width_px()` below) when affordable.

    Symmetrically, "x" narrowed under a `rows` (or grid) facet forces an
    extra axis into every row panel — that costs panel *height*, and no
    counterpart to `facet_panel_width()` budgets a chart's height (there is
    no `facet_panel_height()`). So VL "x" still does NOT narrow when
    `multiples.rows is not None`: the one exclusion this function keeps is
    deliberate, not a leftover — removing it would reintroduce the same
    unbudgeted-axis defect this function exists to prevent, just on the
    other axis. Widening the height budget to close that gap is a distinct,
    separately-scoped fix.

    VL "y" also never narrows when the resolved y-axis mirrors to both
    edges (``chart.style.axis_y.mirror`` — a bool or an ``AxisMirrorStyle``,
    both truthy). `MirrorAxisFeature` appends the ghost edge as a layer on
    the unit spec; once "y"'s scale is independent, VL stops sharing that
    unit spec across the panel row and instantiates it — ghost layer
    included — once per panel, not once for the whole facet. Charging that
    per panel does not fix it either: measured on a real board, it forces
    panels to shrink past zero rather than merely under-reserve. Refusing
    to narrow a mirrored "y" is the only option that cannot overflow, so it
    is the deliberate, permanent behavior here — not a narrower budget to
    grow into later. "x" has no mirror field to check (`AxisYStyle.mirror`
    is the only shipped mirror flag), so this guard is "y"-only by
    construction.

    `color`/`theta`/`size` are never included: only positional band/axis
    space narrows, not the shared color identity.

    Never narrows for a chart carrying an authored ``layers:`` overlay
    (bar/line/area/scatter — see ``LayeredResolvedChart``). The overlay
    assembly moves a base-only channel off the top-level VL encoding onto
    ``spec.layers[0]`` (the base is always layers[0] — see
    ``render_cartesian_overlay``'s "paint order contract" docstring in
    ``_overlay.py``), which the two consumers of this predicate read
    differently: ``FacetFeature`` sees a ChartSpec and could in principle
    follow that relocation, but ``effective_horizontal_bar_category_count``
    (below) runs BEFORE any ChartSpec exists — it only ever has the resolved
    chart and raw data, with nothing to relocate to. Narrowing here for a
    layered chart would need both consumers to agree on where a moved
    channel's type lives, and only one of them has anywhere to look. Kept
    out of scope deliberately, not narrowed accidentally: excluding it here,
    once, means every consumer of this predicate agrees by construction
    instead of each needing its own guard. Tracked as follow-on work:
    graph-library/facet-narrowing-does-not-cover-a-layered-chart.
    """
    if isinstance(chart, LayeredResolvedChart) and chart.layers:
        return frozenset()
    # multiples is cartesian-only, so every real caller already holds a
    # cartesian chart here — narrows chart.x/.y/.style below to the
    # cartesian-shaped subset of ResolvedChart's own discriminated union.
    assert isinstance(chart, _CartesianResolvedChartFields)
    is_flipped = (
        isinstance(chart, ResolvedBarChart) and chart.orientation == "horizontal"
    )
    x_channel: Literal["x", "y"] = "y" if is_flipped else "x"
    y_channel: Literal["x", "y"] = "x" if is_flipped else "y"
    mirrored = bool(chart.style.axis_y.mirror)

    channels: set[Literal["x", "y"]] = set()
    if (
        isinstance(chart.x, str)
        and not (x_channel == "x" and multiples.rows is not None)
        and not (x_channel == "y" and mirrored)
        and _panel_domain_is_proper_subset(chart.x, chart.panel_axes, data)
    ):
        channels.add(x_channel)
    if (
        isinstance(chart.y, str)
        and not (y_channel == "x" and multiples.rows is not None)
        and not (y_channel == "y" and mirrored)
        and _panel_domain_is_proper_subset(chart.y, chart.panel_axes, data)
    ):
        channels.add(y_channel)
    return frozenset(channels)


def extra_axis_is_affordable(
    unnarrowed_panel_width: float, extra_axis_px: float
) -> bool:
    """True when reserving ``extra_axis_px`` for the extra per-column-panel
    axis still leaves at least ``chart_rendering.facet.min_panel_px`` of
    panel width — the SAME floor ``WARN_FACET_PANEL_WIDTH_BELOW_MINIMUM``
    (``render/warnings/facet_panel_width_below_minimum.py``) already reads,
    not a separate constant. Narrowing a position channel is an improvement
    only when the result is still legible; an honest budget that consumes
    the whole card is a signal to skip the reservation, not a reason to
    paint five zero-width panels. ``extra_axis_px <= 0.0`` is always
    affordable — there is nothing to reserve.
    """
    if extra_axis_px <= 0.0:
        return True
    min_panel_px = get_chart_rendering().facet.min_panel_px
    return unnarrowed_panel_width - extra_axis_px >= min_panel_px


def facet_bound_position_channels(
    chart: ResolvedChart,
    multiples: MultiplesConfig,
    data: list[dict[str, Any]],  # type-state: explicit_any — raw query result rows
    unnarrowed_panel_width: float | None,
) -> frozenset[Literal["x", "y"]]:
    """``_domain_subset_narrowing_candidates()`` plus one more gate: the
    columns/grid-facet "y" case additionally narrows only when
    ``extra_axis_is_affordable()`` says the reservation still leaves a
    legible panel.

    ``unnarrowed_panel_width`` is the per-column-panel width the facet would
    use with NO extra axis reserved (``facet_panel_width(..., extra_axis_px
    =0.0)`` — the same number the caller's own width budget is computed
    against, so both agree on the same verdict from the same inputs).
    ``None`` skips the affordability check entirely rather than treating it
    as "always affordable" or "never affordable" — legitimate for a caller
    with no panel geometry to check (a non-faceted chart, where "y" can
    never reach the columns-facet branch below regardless) or a caller
    that never computed real facet geometry in the first place.

    Only the width-budgeted "y" case is gated here — "x" is either free
    (rows-only/no-facet) or already excluded outright by
    ``_domain_subset_narrowing_candidates()`` (rows/grid facet, no height
    budget exists to check against), so it never reaches this gate.
    """
    channels = set(_domain_subset_narrowing_candidates(chart, multiples, data))
    if (
        "y" in channels
        and multiples.columns is not None
        and unnarrowed_panel_width is not None
    ):
        extra = facet_extra_axis_width_px(chart, multiples, data)
        if not extra_axis_is_affordable(unnarrowed_panel_width, extra):
            channels.discard("y")
    return frozenset(channels)


def _emits_discrete_y(
    chart: _CartesianResolvedChartFields,
    y_field: str,
    data: list[dict[str, Any]],  # type-state: explicit_any — raw query result rows
) -> bool:
    """Whether VL "y" will actually be emitted nominal/ordinal for ``chart``,
    re-derived from the resolved chart and raw data rather than the emitted
    spec — ``facet_extra_axis_width_px()`` (below) runs before any spec
    exists (see ``vega_lite.py``'s ``_render_vl_artifact``), so it cannot
    read ``spec.encoding["y"]["type"]`` the way
    ``_discrete_facet_channels()`` (``render/chart/features/facet.py``)
    does post-emission.

    NOT a single data-inference call: how the emitted "y" type is decided
    varies by chart family, and getting that wrong in either direction is a
    real bug — treating every family as data-inferred would budget width for
    a chart whose real "y" is hardcoded quantitative and never narrows, or
    skip budgeting for a chart whose "y" is hardcoded discrete regardless of
    what the data looks like. Per family, from source:

    - Heatmap (single measure — the only shape reaching here, see the
      ``isinstance(y_field, str)`` guard at the call site: a wide/list
      ``chart.y`` folds into a layered spec whose per-measure "y" encodings
      never land on the top-level encoding at all): hardcoded nominal
      unconditionally — ``emitters/heatmap.py``'s ``y_type = "nominal"``
      ("a grid dimension, never a numeric domain"). Always discrete.
    - Horizontal bar: hardcoded nominal unconditionally —
      ``emitters/bar.py``'s ``_emit_horizontal`` (the ``cat_field`` "y"
      encoding), which never consults the data or
      ``resolve_cartesian_x_type()`` for this axis. Always discrete
      (VL "y" carries ``chart.x``, the category, after the flip).
    - Vertical bar / line / area: hardcoded quantitative unconditionally —
      ``emitters/bar.py``'s ``_emit_vertical`` and
      ``emitters/_cartesian.py``'s ``build_cartesian_y_encoding`` (line,
      area — ``"type": "quantitative"``). Safe only because resolve
      refuses a non-numeric ``y`` outright before render ever runs. Never
      discrete.
    - Scatter (single-series): the one family actually inferred from data —
      ``emitters/scatter.py``'s ``y_type = infer_vega_type_from_data(data,
      chart.y)``. Scatter has no numeric-``y`` validation and no fixed axis
      semantics (a scatter "y" can legitimately be nominal, e.g. a dot
      plot), so this is the only row where re-reading the data is the
      correct source of truth, not merely convenient.
    - Scatter (wide, ``chart.wide_measures``): never reaches this function at
      all. The caller (``facet_extra_axis_width_px``, below) gates on
      ``"y" in _domain_subset_narrowing_candidates(...)``, which itself
      requires ``_panel_domain_is_proper_subset(chart.y, ...)`` — and for a
      wide chart ``chart.y`` is the synthetic ``WIDE_VALUE_FIELD``, absent
      from every raw row, so every panel domain reads empty and "y" is
      never a narrowing candidate. A folded ``y: [a, b]`` is always
      quantitative regardless (resolved at ``compile/resolve/chart/
      scatter.py``); it simply never needs to ask this function.
    """
    if isinstance(chart, ResolvedHeatmapChart):
        return True
    if isinstance(chart, ResolvedBarChart):
        return chart.orientation == "horizontal"
    if isinstance(chart, (ResolvedLineChart, ResolvedAreaChart)):
        return False
    return infer_vega_type_from_data(data, y_field) in {"nominal", "ordinal"}


def facet_extra_axis_width_px(
    chart: ResolvedChart,
    multiples: MultiplesConfig,
    data: list[dict[str, Any]],  # type-state: explicit_any — raw query result rows
) -> float:
    """The width ONE extra per-column-panel axis instance would cost, were
    the "y"-bound channel narrowed under a columns/grid facet — see
    ``facet_bound_position_channels``'s docstring. ``0.0`` when no channel
    could ever force that (no columns facet, "y" isn't a raw domain-subset
    candidate, or the family never emits "y" discrete).

    A pure measurement, not a narrow/don't-narrow verdict: this says how
    much the reservation WOULD cost, not whether it is affordable. Callers
    that need the final "is this actually happening" answer use
    ``facet_bound_position_channels()``, which measures via this function
    and then applies ``extra_axis_is_affordable()`` on top. Kept separate
    because affordability needs this function's own result as an input —
    folding the check in here would make it call itself.

    Sized by measurement, not a flat guess: a fixed per-axis constant can't
    tell a 1-character label from a 30-character one apart, and a widest
    label sets the real width VL will draw regardless of how many labels
    share it. ``_LAYOUT_CHROME_PX`` (this module) already budgets the
    title/tick/padding chrome an axis needs beyond its label text — the
    same constant `min_height_for_horizontal_bar_categories` reuses for the
    same purpose on the other axis — so only the label text itself is
    measured here, against the field's FULL domain (every panel's width is
    the same VL-side property, so the budget must cover the widest label
    any panel could paint, not just the panel being computed for), clamped
    to the axis's own label limit (``axis_y.labels.max_width``, else VL's
    own 180px default) the same way VL itself would clip an over-long label.
    """
    if multiples.columns is None:
        return 0.0
    # multiples is cartesian-only, so every real caller already holds a
    # cartesian chart here — narrows chart.x/.y/.style below, same as
    # facet_bound_position_channels above.
    assert isinstance(chart, _CartesianResolvedChartFields)
    is_flipped = (
        isinstance(chart, ResolvedBarChart) and chart.orientation == "horizontal"
    )
    y_field = chart.x if is_flipped else chart.y
    if not isinstance(y_field, str):
        return 0.0
    if "y" not in _domain_subset_narrowing_candidates(chart, multiples, data):
        return 0.0
    if not _emits_discrete_y(chart, y_field, data):
        return 0.0
    # The category axis a horizontal bar narrows is styled through axis_x,
    # not axis_y — _emit_horizontal builds VL "y" from chart.style.axis_x
    # (bar.py's own "categorical cascade" comment; ERR text at bar.py:1066
    # says the same: "A horizontal bar's axis_x is its categorical axis").
    # Same flip that picked y_field above must pick the style, or this
    # measures the WRONG axis's font/labelLimit whenever the two diverge.
    labels = chart.style.axis_x.labels if is_flipped else chart.style.axis_y.labels
    font = labels.font
    assert font.size is not None, (
        "axis_y.labels.font.size unset — theme cascade must populate it"
    )
    domain = {
        str(row[y_field]) for row in data if y_field in row and row[y_field] is not None
    }
    if not domain:
        return 0.0
    measurer = get_font_measurer(font.family)
    label_limit = (
        labels.max_width if labels.max_width is not None else DEFAULT_VL_LABEL_LIMIT
    )
    widest = min(max(measurer.measure(v, font.size) for v in domain), label_limit)
    return _LAYOUT_CHROME_PX + widest


def count_horizontal_bar_categories(
    category_field: str | None,
    data: list[dict[str, Any]],
) -> int:
    """Distinct category values on the authored ``x`` field (VL y after flip)."""
    if not category_field or not data:
        return 0
    return len(
        {
            str(row[category_field])
            for row in data
            if category_field in row and row[category_field] is not None
        }
    )


def min_height_for_horizontal_bar_categories(
    n_categories: int,
    axis_x: ResolvedAxisStyle,
    bar_size: float | None,
) -> float:
    """Minimum chart height so every categorical band can show a flat y label.

    axis_x and bar_size are the caller's already-resolved
    ``resolved_chart.style.axis_x`` / ``effective_bar_size(.style.mark)`` —
    the fully chart-local-cascaded values (theme chart-type patch and any
    chart-local override both baked in at resolve time). Both stay
    ``| None``-typed here only because the underlying theme model shares its
    type with the resolved usage; the cascade guarantees them concrete by
    this point.
    """
    if n_categories <= 0:
        return 0.0
    label = axis_x.labels
    font_size = label.font.size
    min_gap = label.min_gap
    padding = label.padding
    assert font_size is not None, (
        "axis_x.labels.font.size unset — theme cascade must populate it"
    )
    assert min_gap is not None, (
        "axis_x.labels.min_gap unset — theme cascade must populate it"
    )
    assert padding is not None, (
        "axis_x.labels.padding unset — theme cascade must populate it"
    )
    assert bar_size is not None, (
        "marks.bar.size and marks.bar.max_size both unset — "
        "theme cascade must populate at least one"
    )
    label_line = font_size + 2 * padding
    band_step = max(bar_size, label_line) + min_gap
    return _LAYOUT_CHROME_PX + n_categories * band_step


def effective_horizontal_bar_category_count(
    chart: ResolvedBarChart,
    data: list[dict[str, Any]],  # type-state: explicit_any — raw query result rows
    unnarrowed_panel_width: float | None,
) -> int:
    """Distinct category count the height floor must budget for.

    Ordinarily the whole dataset's union (`count_horizontal_bar_categories`)
    — the facet operator resolves the category axis as shared by default, so
    every panel paints every category regardless of which rows landed in it.
    That premise breaks exactly where `facet_bound_position_channels` narrows
    the category channel: each panel then only paints its own subset, so the
    floor only needs the WIDEST panel's own count — not the whole-dataset
    count `count_horizontal_bar_categories` would return, which would over-
    budget height for every panel holding fewer. Not a flat 1: a panel can
    hold any proper subset of the domain, not only the single-value case a
    name-matched rows facet used to guarantee by construction.

    `unnarrowed_panel_width` threads straight through to
    `facet_bound_position_channels` — a horizontal bar's category rides VL
    "y" (after the orientation flip), the same channel the columns/grid
    width budget gates on affordability, so this must reach the identical
    narrow/don't-narrow verdict: declining to narrow for width reasons means
    every panel still paints the full category set, and the height floor
    must budget for that, not the narrower per-panel count.
    """
    n = count_horizontal_bar_categories(chart.x, data)
    if (
        chart.x
        and chart.multiples is not None
        and "y"
        in facet_bound_position_channels(
            chart, chart.multiples, data, unnarrowed_panel_width
        )
    ):
        widest = widest_panel_distinct_count(chart.x, chart.panel_axes, data)
        if widest:
            return widest
    return n


def apply_domain_headroom_bounds(
    scale: VLDict,
    domain_max: float | None,
    domain_min: float | None,
) -> VLDict:
    """Bake resolve()-time headroom bounds onto a VL scale dict.

    Both bounds are exact (never nice-rounded). ``domain_max`` is the
    headroom-applied top; ``domain_min`` is the symmetric span-relative bottom
    on a zoomed axis, or the headroom-expanded data floor on an all-negative
    zero-anchored one. None on either means VL auto-fits that edge. Callers must have already confirmed no authored domain is set.
    """
    if domain_max is None and domain_min is None:
        return scale
    result = dict(scale)
    if domain_max is not None:
        result["domainMax"] = domain_max
    if domain_min is not None:
        result["domainMin"] = domain_min
    return result


def multiples_scale_independent(chart: _CartesianResolvedChartFields) -> bool:
    """True when ``chart.multiples`` pins every panel to its own y-scale.

    The single spelling of "does this chart-wide zero-rule verdict need to
    be skipped because it can't hold for every panel", computed once here
    so ``BaselineFeature`` and each cartesian emitter's dual-axis rule
    injection cannot drift apart on the same check.
    """
    return chart.multiples is not None and chart.multiples.scale == "independent"


def authored_measure_domain(ay: ResolvedAxisStyle) -> tuple[float, float] | None:
    """The authored numeric ``scale.continuous.domain`` on *ay*, or None.

    The single spelling of "did the author pin this axis's domain", so the
    consumers below — the VL scale emitter and the baseline gate — cannot
    disagree about what counts as authored.
    """
    cont = ay.scale.continuous if ay.scale is not None else None
    return numeric_domain_bounds(cont.domain if cont is not None else None)


def effective_measure_domain(
    axis: ResolvedAxisStyle, data_extent: tuple[float, float] | None
) -> tuple[float, float] | None:
    """The ``(lo, hi)`` this measure axis actually renders.

    On a y-axis this mirrors ``resolve_measure_y_scale``'s branches in the
    same order, so the gate that reads this and the emitter that builds the
    y scale cannot disagree about where the axis starts and ends:

    1. **Authored domain** wins outright, and suppresses everything below it by
       design — resolve leaves the baked bounds None whenever one is set.
    2. **Baked ``domain_min`` / ``domain_max``** — the headroom-expanded edges,
       taken per-edge, because None means "auto-fit THIS edge" and the two are
       decided separately (a zero-anchored axis bakes only the top).
    3. **The zero-anchor floor**, for a low edge resolve left unbaked:
       ``ladder[0]`` else ``0.0``, byte for byte what ``y_zero_scale`` pins as
       ``domainMin`` on such an axis. The ladder is filtered through
       ``zero_anchor_pinned_floor``, the emitter's own decision, so this
       cannot drift from what is really written; an authored ``scale.values``
       list may not source a domain bound, and when no rung supplies a floor
       the axis reports where ``zero: true`` auto-fits instead.
    4. **The data extent**, which is what Vega-Lite auto-fits from.

    On an unauthored x-axis, branches 2 and 3 never fire: resolve has no
    ``resolve_measure_x_scale`` and its smart-zero heuristic only ever runs
    against the y column, so ``domain_min``/``domain_max``/the zero anchor
    stay unbaked for x. The function therefore reduces there to "authored
    domain, else the data extent" — exactly what Vega-Lite auto-fits an
    unpinned x-axis to. That reduction holds only because of what resolve
    does NOT bake for x today; an x-side headroom bake added later would
    start populating those fields and invalidate it.

    **Every value here is one the emitter actually pins, or the extent VL fits
    to — never a prediction of where VL's ``nice`` will land.** An earlier
    revision read the whole tick ladder as if it were the domain, and it does
    not hold: on a ``headroom: 0.05`` percent axis the engine pins
    ``domainMax 0.9785`` while the ladder's top rung is ``1.0``, so the rung
    sits outside the rendered domain and VL clips it. Reading it made this
    function non-monotonic in headroom — a *tighter* fit reported more range —
    and let a parity rule paint on a chart whose domain excluded it, where the
    rule's own datum then stretched the domain to cover the mark that should
    never have been drawn. A tick list is not a domain; keep this function
    reading only pinned facts.

    A consequence worth naming: a ``headroom: 0`` axis whose ladder rounds out
    past ``value`` still reports False here, because nothing pins that edge.
    Making that case work needs the domain pinned at resolve — which is what
    the one-ended/bounded-domain task's ``include`` field is for.

    ``data_extent`` is None when the measure column carries no numeric rows
    under the field the caller asked about — including a synthetic folded field
    that is absent from unfolded rows, where resolve may still have baked real
    bounds. Returns None when nothing pins the axis at all.
    """
    authored = authored_measure_domain(axis)
    if authored is not None:
        return min(authored), max(authored)

    if data_extent is None:
        return None
    data_lo, data_hi = data_extent
    hi = axis.domain_max if axis.domain_max is not None else data_hi
    if axis.domain_min is not None:
        lo = axis.domain_min
    elif is_zero_anchored(axis.scale):
        # Exactly what the emitter pins as domainMin for this axis — read from
        # the same decision, never re-derived here. An inline copy of it is
        # what let this function keep answering 0.0 after the emitter stopped
        # pinning that, reporting an inverted range on an all-negative axis.
        pinned = zero_anchor_pinned_floor(axis)
        # No rung, so the emitter pins nothing and `zero: true` leaves the
        # floor to Vega-Lite's own fit: 0 for data that stays above it, the
        # data floor once anything goes below. Not `pinned or 0.0` — a real
        # 0.0 rung is a pin, not an absence.
        lo = pinned if pinned is not None else min(0.0, data_lo)
    else:
        lo = data_lo
    return lo, hi


def full_rule_at(
    value: float,
    axis: str,
    measure_field: str,
    color: str,
    width: float,
) -> ChartSpec:
    """Return a full VL ``rule`` sub-spec spanning the full plot width/height.

    ``axis`` controls orientation:
    - ``"x"``: rule at x=value spanning full y height (horizontal bar zero line)
    - ``"y"``: rule at y=value spanning full x width (vertical zero line)
    """
    datum_channel = axis  # "x" or "y" — the channel carrying the datum value
    datum_enc: VLDict = {"datum": value, "type": "quantitative"}
    if datum_channel == "x":
        encoding: VLDict = {
            "x": datum_enc,
            "y": {"value": 0},
            "y2": {"value": "height"},
            "color": {"value": color},
            "yOffset": {"value": 0},
        }
    else:
        encoding = {
            "y": datum_enc,
            "x": {"value": 0},
            "x2": {"value": "width"},
            "color": {"value": color},
            "xOffset": {"value": 0},
        }
    return ChartSpec(
        mark="rule",
        mark_props={
            "color": color,
            "strokeWidth": width,
            "opacity": 1,
            "tooltip": False,
        },
        encoding=encoding,
        data=[{measure_field: value}],
    )


def values_straddle_zero(rows: Rows, field: str) -> bool:
    """True when ``field``'s numeric values in ``rows`` straddle (or touch) 0."""
    values = numeric_column_values(rows, field)
    if not values:
        return False
    return min(values) <= 0 <= max(values)


def non_bar_zero_rule_should_fire(
    is_scatter: bool,
    zero_setting: bool | None,
    *,
    zero_anchored: bool,
    authored_domain: tuple[float, float] | None,
    zero_in_domain: Callable[[], bool],
) -> bool:
    """Whether a line/area/scatter measure axis's own zero rule fires.

    The one implementation of ``BaselineFeature._insert_zero_rule``'s real
    non-bar verdict: fires unless the axis explicitly turned zero-anchoring
    off (``zero_setting is False``), in which case ``zero_in_domain`` (0
    lies within the values that share this scale) decides. A scatter axis
    with no authored domain additionally falls back to the axis's own baked
    zero-anchor verdict, since resolve's own heuristic can abstain and leave
    the axis data-fitted even though nothing pinned ``zero: false``.
    ``zero_in_domain`` is a callable, not a bool, so a caller whose check is
    expensive (unioning every sibling layer's rows) only pays for it on the
    branches that actually consult it. Bar has no such nuance: it always
    fires and is decided by the caller before reaching this function.
    """
    if zero_setting is False:
        return zero_in_domain()
    should_fire = True
    if is_scatter and authored_domain is None:
        should_fire = zero_anchored or zero_in_domain()
    return should_fire


def build_zero_rule_if_applicable(
    measure_field: str,
    axis: str,
    *,
    log_scale: bool,
    authored_domain: tuple[float, float] | None,
    domain_min: float | None,
    domain_max: float | None,
    grid_visible: bool,
    zero_color: str,
    zero_width: float,
    should_fire: bool,
) -> ChartSpec | None:
    """Build a zero-baseline ``rule`` ChartSpec for one measure scale, or None.

    The single implementation of the guards every zero-rule caller must
    honor, regardless of chart family or whether the scale is shared or
    independently resolved: a log-typed scale can't carry a literal ``0``
    datum (breaks VL's entire axis rendering), a domain excluding 0 has no
    legal position for the rule, and ``grid_visible=False`` suppresses it
    outright. ``should_fire`` is the caller's own straddle/always-fire
    verdict; see ``non_bar_zero_rule_should_fire`` for the line/area/scatter
    verdict most callers combine with their own mark-family always-fire
    shortcut.

    ``domain_min`` / ``domain_max`` are resolve's baked, headroom-expanded
    edges, and exclude 0 the same way an authored domain does. The rule's own
    datum pulls 0 into the scale only while Vega-Lite is still free to
    auto-fit that edge; a pin wins over it, and the rule then paints off the
    plot rect — where ``autosize: pad`` grows the SVG to contain the stray
    mark, and ``autosize: fit`` translates the whole plot group off the
    canvas instead. Pass ``None`` for an edge nothing pins.
    """
    if log_scale:
        return None
    if authored_domain is not None and not (
        min(authored_domain) <= 0.0 <= max(authored_domain)
    ):
        return None
    if (domain_max is not None and domain_max < 0.0) or (
        domain_min is not None and domain_min > 0.0
    ):
        return None
    if not grid_visible:
        return None
    if not should_fire:
        return None
    return full_rule_at(
        0,
        axis=axis,
        measure_field=measure_field,
        color=zero_color,
        width=zero_width,
    )


def nest_zero_rule(
    entry: ChartSpec, rule: ChartSpec, measure_channel: str
) -> ChartSpec:
    """Nest ``rule`` inside ``entry``'s own scale (an independent-y dual-axis layer).

    An outer ``resolve.scale.y: independent`` governs only the DIRECT
    children of that outer ``layer[]`` array (see ``render_cartesian_overlay``'s
    docstring on dual-axis placement); a rule appended as a new top-level
    sibling there would carry no field of its own and manufacture a
    degenerate ``[0, 0]`` scale. Wrapping ``entry`` and ``rule`` together in
    one more ``layer[]`` level puts them in the same composite, where
    Vega-Lite unions their y encodings onto one shared scale by default.

    That union changes the compiled Vega scale's default ``nice`` from unset
    to ``true`` (measured via ``vl_convert.vegalite_to_vega``: an un-nested
    entry's compiled scale carries no ``nice`` key at all; the identical
    entry once nested does) -- which then rounds the domain PAST an explicit
    ``domainMax``/``domainMin`` headroom pin at render time, defeating
    ``apply_domain_headroom_bounds``'s "exact, never nice-rounded" contract.
    Pinning ``nice: false`` on ``measure_channel``'s scale here keeps the pin
    exact after nesting. The guard is ``domainMax``/``domainMin`` presence, not
    an explicit headroom author choice: ``y_zero_scale`` writes ``domainMin``
    on every zero-anchored axis, so this fires on essentially every
    zero-anchored dual-axis bar/area base; an authored or data-fit scale with
    neither key is the only case left unaffected.
    """
    channel_enc = entry.encoding.get(measure_channel)
    scale = channel_enc.get("scale") if isinstance(channel_enc, dict) else None
    if isinstance(scale, dict) and ("domainMax" in scale or "domainMin" in scale):
        scale["nice"] = False
    if entry.mark == "layered":
        entry.layers.append(rule)
        return entry
    return ChartSpec(mark="layered", layers=[entry, rule])


def layer_encoding_owner(entry: ChartSpec) -> ChartSpec:
    """The sub-entry that actually carries ``entry``'s own encoding.

    ``nest_zero_rule`` may wrap a bare-mark entry in a fresh ``mark="layered"``
    ChartSpec with no encoding of its own (nesting a zero rule inside the
    wrapped entry's scale). A caller reading "this layer's own encoding" (to
    patch a legend, stamp a tooltip-order transform, etc.) must unwrap to the
    real owner instead of the empty wrapper.
    """
    if entry.mark == "layered" and not entry.encoding and entry.layers:
        return entry.layers[0]
    return entry


def resolve_measure_y_scale(ay: ResolvedAxisStyle) -> VLDict:
    """Build the VL y-scale dict for a resolved measure axis.

    Authored domain replaces the data-driven zero/domainMin computation, but
    still carries type/base/exponent/constant (etc.) via emit_resolved_scale_vl
    — a domain and a scale type are not mutually exclusive (e.g. log needs
    both). Every measure-channel emitter (vertical bar, horizontal bar,
    single- and multi-metric line, area, scatter) must route through this one
    branch: a zero-anchored "zero: true" flag left standing next to an
    authored domain lets VL's domainMin companion silently override the
    authored lower edge with the tick ladder's own rounded-down bottom rung.
    """
    authored_domain = authored_measure_domain(ay)
    if authored_domain is not None:
        y_scale: VLDict = {"domain": list(authored_domain)}
        if ay.scale is not None:
            y_scale.update(emit_resolved_scale_vl(ay.scale))
            # An authored domain already IS the exact bounds — "zero" is a
            # domain-inference hint (VL's "extend the domain to include 0"),
            # meaningless once the domain is pinned explicitly, and its
            # domainMin companion would leak a rounded-down tick bound below
            # the authored lower edge.
            y_scale.pop("zero", None)
        return y_scale
    y_scale = y_zero_scale(ay)
    return apply_domain_headroom_bounds(y_scale, ay.domain_max, ay.domain_min)


def pin_normalize_axis_format(ay_vl: VLDict, ay: ResolvedAxisStyle) -> None:
    """Pin the render-local axis dict's percent format for a normalize stack.

    A normalize-stacked axis is always a 0-100% share axis — that is what
    ``stack: normalize`` means, unconditionally, not a default an author's
    own ``axis_y.labels.format`` can opt out of. Vega-Lite's own auto-percent
    inference agrees, but only for a lone series; it silently stops applying
    once the axis merges with a sibling layer's own axis config (any overlay
    layer sharing the y-scale), and the theme's plain SI-number default wins
    instead (``250m`` instead of ``25%``). Pinned explicitly here so the
    percent presentation doesn't depend on that merge behavior. Shared by
    every ``stack: normalize``-capable family (bar, area) so the fix and its
    reasoning live in one place.

    Also clears an ``ay_vl["labelExpr"]`` the ladder-less sub-unit guard
    composed (``inject_axis_numeral_expr``, from ``ay.tick_label.si_format``)
    for the axis's own SI default — a normalize-stacked measure axis is
    always ladder-less and always unauthored-SI by construction (fixed [0, 1]
    domain, no ``ticks.count`` bake), so that guard fires here unconditionally
    too, and Vega prefers ``labelExpr`` over ``format`` -- left in place, the
    percent pin above would be silently ignored. Gated on ``ay.labels.expr``
    (not merely "labelExpr present"): an AUTHORED ``label.expr`` already wins
    over ``format`` today via that same Vega precedence, before this function
    ever runs -- unconditional as the percent format itself is, it does not
    reach past an author's own expression, only past the engine's own
    composition for a presentation this axis will never use.

    Mutates only the render-local ``ay_vl`` dict (the VL axis config for this
    encoding), never ``ay.labels.format`` on the resolved axis style: that
    field is shared with value labels, stack-total labels, and tooltips, and
    those must keep reading the raw column value/format — a value label on a
    normalize-stacked bar shows its own count (``300``), not the axis's
    share of the stack. Reading the author's own format against that raw value
    does lie when the format is a percent and the measure is not already a
    0..1 share (``300`` prints as ``30000%``); that is reported, never
    rewritten, by ``render/warnings/normalize_percent_format_reads_raw_value.py``.
    """
    if ay.labels.expr is None:
        ay_vl.pop("labelExpr", None)
    ay_vl["format"] = PREDEFINED_SPECS[PredefinedNumberFormat.percent_whole]


def build_cartesian_y_encoding(
    y_field: str | list[str] | None,
    ay: ResolvedAxisStyle,
    ay_vl: VLDict,
    y_title: AxisTitle,
    tooltip_format: str,
) -> VLDict:
    """Build the VL y encoding dict from a resolved axis style.

    Tick values from ay.tick_values are baked into ay_vl["values"] when present.

    Callers that need a VL ``stack`` key (area charts) mutate the returned dict.
    """
    bake_tick_ladder(ay_vl, ay.tick_values)
    return {
        "field": y_field,
        "type": "quantitative",
        "title": y_title,
        "axis": ay_vl,
        "scale": resolve_measure_y_scale(ay),
        "format": tooltip_format,
    }


def series_order_expression(color_field: str, order: list[str]) -> str:
    """Build a VL calculate expression mapping each series value to its index in *order*.

    Bakes a precomputed Python order (the SAME list driving the legend/label
    display order) into a mark's own paint order or tooltip rank, so that
    consumer and display order can never drift apart. ``order`` entries are
    always ``str`` (``distinct_series_values`` str-casts the raw column), but
    the color field's actual row values keep their native type (numeric year,
    boolean flag, etc.) — comparing with a bare ``===`` would silently never
    match a non-string field, so both sides are coerced through ``toString``
    first. Example for color_field="status" with order=["new", "solved"]:
    ``toString(datum["status"]) === "new" ? 0 : toString(datum["status"]) === "solved" ? 1 : -1``
    """
    field_ref = f"toString(datum[{json.dumps(color_field)}])"
    parts = [f"{field_ref} === {json.dumps(v)} ? {i}" for i, v in enumerate(order)]
    return " : ".join(parts) + " : -1"


def distinct_series_values(data: list[dict[str, Any]], series_field: str) -> list[str]:
    """Alphabetically sorted distinct non-null values of *series_field*.

    This is the ground-truth series set for palette assignment: Vega-Lite's
    own default color-scale domain, matching ``config.range.category`` slot
    *i* to the *i*-th alphabetically-sorted series.

    Nulls stay filtered here. Admitting them would put the string ``"None"`` in
    the scale domain while the rows themselves hold ``null`` — a phantom legend
    entry for a series that paints nothing, which is worse than omitting it.
    Callers that must not silently lose a series run ``validate_color_series``
    first; families that don't yet (scatter, pie/arc, overlay layers) keep the
    quiet drop until that guard is extended to them.
    """
    return sorted(
        {str(row[series_field]) for row in data if row.get(series_field) is not None}
    )


def spatial_color_scale(
    series: list[str],
    palette: tuple[str, ...],
    order: list[str],
    scale: CategoryColorScale | None = None,
) -> VLDict:
    """Explicit VL color ``{domain, range}`` reordering DISPLAY only.

    ``series`` assigns palette slots; ``order`` independently becomes the
    legend/tooltip/label display sequence. Callers that only reorder display
    pass the alphabetical series domain. Stacked bars pass their baseline-first
    order so stack rank and palette rank stay aligned.

    A board ``scale`` overrides that positional assignment: each value takes
    the slot it owns board-wide, so the same category is the same color on
    every chart. Either way the color follows the value, never its new
    display position.
    """
    if scale is not None:
        color_of = {s: color_at(scale, s, palette) for s in series}
    else:
        color_of = {s: palette[i % len(palette)] for i, s in enumerate(series)}
    return {"domain": list(order), "range": [color_of[s] for s in order]}


def emitted_categorical_color_scale(
    *color_encodings: VLDict | None,
) -> dict[str, str] | None:
    """Return the first explicit categorical color mapping in emitted order."""
    for color_encoding in color_encodings:
        if not isinstance(color_encoding, dict):
            continue
        scale = color_encoding.get("scale")
        if not isinstance(scale, dict):
            continue
        domain = scale.get("domain")
        color_range = scale.get("range")
        if (
            isinstance(domain, list)
            and isinstance(color_range, list)
            and all(isinstance(series, str) for series in domain)
            and all(isinstance(fill, str) for fill in color_range)
        ):
            return dict(zip(domain, color_range, strict=True))
    return None


def companion_color_for_fill(
    fill: str, palette: list[str] | tuple[str, ...], companions: tuple[str, ...]
) -> str:
    """Return the companion ink occupying the emitted fill's palette slot."""
    try:
        index = palette.index(fill)
    except ValueError:
        return fill
    return companions[index] if index < len(companions) else fill


# x is a row's raw x-field value (str/date/int, whatever the query returned);
# only ever compared with >= below, never introspected.
_AnchorXY = dict[str, tuple[Any, float]]  # type-state: explicit_any — see comment above


def last_nonnull_xy_per_series(
    data: list[dict[str, Any]], x_field: str, y_field: str, series_field: str
) -> _AnchorXY:
    """Each series' own most-recent non-null (x, y) pair.

    A trailing null, or a series that ends early, never collapses its
    position — this tracks the last ACTUAL (x, y) per series independently,
    not the value at the single global-last x row. The one walk-back both
    ``last_nonnull_value_per_series`` (line/area rail, value only) and the
    stacked rail's per-series anchor column (``_stacked_midpoints`` in
    ``features/endpoint_labels.py``, which also needs the x) build on.
    """
    latest: _AnchorXY = {}
    for row in data:
        s, x, y = row.get(series_field), row.get(x_field), row.get(y_field)
        if s is None or x is None or y is None:
            continue
        key = str(s)
        if key not in latest or x >= latest[key][0]:
            latest[key] = (x, float(y))
    return latest


def last_nonnull_value_per_series(
    data: list[dict[str, Any]],  # type-state: explicit_any — row values are dynamic
    x_field: str,
    y_field: str,
    series_field: str,
) -> dict[str, float]:
    """Each series' value at its own most-recent non-null (x, y) pair.

    A trailing null, or a series that ends early, never collapses its
    position — this tracks the last ACTUAL value per series independently,
    not the value at the single global-last x row.
    """
    return {
        s: y
        for s, (_x, y) in last_nonnull_xy_per_series(
            data, x_field, y_field, series_field
        ).items()
    }


def sorted_series_by_last_value(
    series: list[str],
    data: list[dict[str, Any]],
    x_field: str,
    y_field: str,
    series_field: str,
) -> list[str]:
    """Stable order for multi-line/overlap-area.

    Descending by each series' most-recent non-null value; ties broken
    alphabetically for determinism. A series with no non-null value anywhere
    carries no ordering signal, so it sorts last (alphabetically among
    itself). This is a single static order computed once at render time —
    never a live re-sort as the hover cursor moves.
    """
    values = last_nonnull_value_per_series(data, x_field, y_field, series_field)
    with_value = sorted(
        (s for s in series if s in values), key=lambda s: (-values[s], s)
    )
    without_value = sorted(s for s in series if s not in values)
    return with_value + without_value


__all__ = [
    "CartesianXResolution",
    "XYTitles",
    "apply_domain_headroom_bounds",
    "build_cartesian_y_encoding",
    "bar_sort_to_vl",
    "build_palette_config",
    "build_x_enc",
    "cartesian_x_scale_domain",
    "chart_sort_to_vl",
    "dimension_sort_to_vl",
    "pin_sorted_x_domain",
    "companion_color_for_fill",
    "emitted_categorical_color_scale",
    "distinct_series_values",
    "resolve_xy_titles",
    "wide_measures_title",
    "count_horizontal_bar_categories",
    "last_nonnull_value_per_series",
    "min_height_for_horizontal_bar_categories",
    "resolve_cartesian_x",
    "resolve_measure_y_scale",
    "series_order_expression",
    "sorted_series_by_last_value",
    "spatial_color_scale",
]
