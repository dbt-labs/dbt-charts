"""Vega-Lite field name maps and helper for snake_case → camelCase mapping."""

from __future__ import annotations

import json
from typing import Any, Literal

from pydantic import BaseModel

from dbt_charts.core.compile.models.style.resolved._base import (
    ResolvedAxisStyle,
    ResolvedRulerAxis,
    ResolvedScaleStyle,
    ResolvedTickLabel,
)
from dbt_charts.core.compile.models.style.theme.marks import BarMarkStyle
from dbt_charts.core.compile.resolve.chart.tick_values import numeric_domain_bounds
from dbt_charts.core.render.chart._types import VLDict
from dbt_charts.core.render.chart.emitters._measured_label_padding import (
    estimated_quantitative_tick_labels,
    numeric_values,
    quantitative_tick_labels,
)
from dbt_charts.core.render.numeral_expr import numeral_vega_expr
from dbt_charts.core.text.numeral_scale import (
    SUB_UNIT_SCIENTIFIC_FLOOR,
    SuffixMode,
    with_symbol,
)
from dbt_charts.core.text.predefined_formats import PREDEFINED_NATIVE_NAMES
from dbt_charts.core.utils import (
    DEFAULT_VL_LABEL_LIMIT,
    Rows,
    cap_padding_to_label_limit,
    coerce_numeric_cell,
    measured_label_padding,
)

# ``type``/log.base/pow.exponent/symlog.constant emit log/pow/symlog scale
# config. Must not be omitted — a missed field silently resolves to None and
# drops scale config from the emitted spec.
_SCALE_CONTINUOUS_FIELD_MAP: dict[str, str] = {
    "zero": "zero",
    "type": "type",
    # domain and nice are encoding-level only (see SCALE_ENCODING_FIELD_MAP).
}
# Universal top-level scale fields (not in any sub-group).
_SCALE_UNIVERSAL_FIELD_MAP: dict[str, str] = {
    "round": "round",
    "clamp": "clamp",
}
# Encoding-level-only scale fields: nice, padding shorthand. domain is read
# explicitly from scale.continuous.domain below (nested, not a flat field).
# Applied to encoding.x.scale / encoding.y.scale only (not VL config).
SCALE_ENCODING_FIELD_MAP: dict[str, str] = {
    "nice": "nice",
    # Unified shorthand. VL dispatches based on scale type (bandPaddingOuter,
    # pointPadding, continuousPadding). Encoding-level only.
    "padding": "padding",
}
# x-channel-only scale fields.
_SCALE_X_ONLY_FIELD_MAP: dict[str, str] = {
    "x_reverse": "xReverse",
}


def emit_resolved_scale_vl(
    scale: ResolvedScaleStyle | None,
    *,
    include_encoding_fields: bool = True,
    include_x_only: bool = False,
) -> dict[str, Any]:
    """Emit a VL scale dict from a ResolvedScaleStyle (non-None fields only).

    Args:
        scale: Resolved scale to emit. Returns {} when None.
        include_encoding_fields: When True (default), includes domain/nice/padding
            (encoding-level-only fields). Set False for VL config-level emit.
        include_x_only: When True, includes xReverse (x-channel only).
    """
    if scale is None:
        return {}
    out: dict[str, Any] = {}
    out.update(map_fields(scale, _SCALE_UNIVERSAL_FIELD_MAP))
    if scale.continuous is not None:
        out.update(map_fields(scale.continuous, _SCALE_CONTINUOUS_FIELD_MAP))
        if scale.continuous.log is not None and scale.continuous.log.base is not None:
            out["base"] = scale.continuous.log.base
        if (
            scale.continuous.pow is not None
            and scale.continuous.pow.exponent is not None
        ):
            out["exponent"] = scale.continuous.pow.exponent
        if (
            scale.continuous.symlog is not None
            and scale.continuous.symlog.constant is not None
        ):
            out["constant"] = scale.continuous.symlog.constant
    if include_encoding_fields:
        out.update(map_fields(scale, SCALE_ENCODING_FIELD_MAP))
        if scale.continuous is not None:
            domain = scale.continuous.domain
            if domain is not None:
                out["domain"] = list(domain)
    if include_x_only:
        if scale.x_reverse is not None:
            out["xReverse"] = scale.x_reverse
    return out


def _n(obj: Any, *path: str) -> Any:
    """Get nested attribute, returning None if any level is None or missing."""
    for attr in path:
        if obj is None:
            return None
        obj = getattr(obj, attr, None)
    return obj


def _dasharray_to_vl(dasharray: str) -> list[float]:
    """Convert SVG stroke-dasharray string (e.g. '4 2') to VL strokeDash number array."""
    return [float(x) for x in dasharray.split()]


def _stroke_to_vl(stroke: Any) -> dict[str, Any]:
    """Map a StrokeStyle → VL stroke properties (non-None fields only).

    Single canonical primitive shared by all per-mark mappers and halo
    path builders so new StrokeStyle fields automatically flow everywhere.
    """
    d: dict[str, Any] = {}
    if (v := _n(stroke, "color")) is not None:
        d["stroke"] = v
    if (v := _n(stroke, "width")) is not None:
        d["strokeWidth"] = v
    if (v := _n(stroke, "cap")) is not None:
        d["strokeCap"] = v
    if (v := _n(stroke, "join")) is not None:
        d["strokeJoin"] = v
    if (v := _n(stroke, "dasharray")) is not None:
        d["strokeDash"] = _dasharray_to_vl(v)
    return d


def axis_to_vl(
    axis: Any,
    *,
    label_overlap: Literal["allow", "parity"] | None = None,
    label_angle: float | None = None,
) -> dict[str, Any]:
    """Map any axis style object → VL axis config dict (non-None fields only).

    Works for any concrete axis style (all fields emitted) and any axis-variant
    patch (skip None). Single canonical mapper for all axis variants — theme
    global/channel/type-conditional and chart-local patches.

    ``label_overlap`` is the render-local VL directive from resolve_axis_x_overlap;
    it is not stored on the resolved model (VL vocabulary stays out of Resolved*).
    """
    if axis is None:
        return {}
    d: dict[str, Any] = {}

    # Label font (nested via labels.font)
    if (v := _n(axis, "labels", "font", "color")) is not None:
        d["labelColor"] = v
    if (v := _n(axis, "labels", "font", "family")) is not None:
        d["labelFont"] = v
    if (v := _n(axis, "labels", "font", "size")) is not None:
        d["labelFontSize"] = v
    if (v := _n(axis, "labels", "font", "weight")) is not None:
        d["labelFontWeight"] = v
    if (v := _n(axis, "labels", "padding")) is not None:
        d["labelPadding"] = v
    if (v := _n(axis, "labels", "max_width")) is not None:
        d["labelLimit"] = v
    if label_angle is not None:
        d["labelAngle"] = label_angle
    elif (v := _n(axis, "labels", "angle")) is not None:
        d["labelAngle"] = v
    if (v := _n(axis, "labels", "align")) is not None:
        d["labelAlign"] = v
    # label_overlap is the render-local VL directive from resolve_axis_x_overlap.
    # "allow" → false (no reduction; labels may overlap).
    # "parity" → pass through as-is (VL drops every other label).
    # None → omit (VL applies its per-scale adaptive default).
    if label_overlap == "allow":
        d["labelOverlap"] = False
    elif label_overlap == "parity":
        d["labelOverlap"] = label_overlap
    if (v := _n(axis, "labels", "min_gap")) is not None:
        d["labelSeparation"] = v
    if (v := _n(axis, "labels", "visible")) is not None:
        d["labels"] = v
    if (v := _n(axis, "labels", "expr")) is not None:
        d["labelExpr"] = v
    if (v := _n(axis, "labels", "bound")) is not None:
        d["labelBound"] = v
    if (v := _n(axis, "labels", "flush")) is not None:
        d["labelFlush"] = v
    if (v := _n(axis, "labels", "offset")) is not None:
        d["labelOffset"] = v
    # `format` always comes from label.format -- never from tick_label.format
    # (ResolvedAxisStyle's non-compacting sibling of `ruler`). Vega's own
    # auto-generated axis description (its SVG ARIA accessibility label)
    # also reads `format`, applying it to the scale's domain min/max, values
    # that are NOT ticks and that a tick-derived precision was never sized
    # for. tick_label.format is composed into `labelExpr` instead, by
    # `inject_axis_numeral_expr` (called by each emitter after this
    # function) -- see that function's docstring.
    if (v := _n(axis, "labels", "format")) is not None:
        d["format"] = v

    # Title: null suppresses the visual axis label (title still appears in tooltips
    # via encoding.title). Emitting null is the correct VL mechanism — it removes
    # the title from layout entirely, unlike titleFontSize:0 which hides visually
    # but still occupies space and fires a11y events.
    if _n(axis, "title", "visible") is False:
        d["title"] = None
    else:
        if (v := _n(axis, "title", "font", "color")) is not None:
            d["titleColor"] = v
        if (v := _n(axis, "title", "font", "family")) is not None:
            d["titleFont"] = v
        if (v := _n(axis, "title", "font", "size")) is not None:
            d["titleFontSize"] = v
        if (v := _n(axis, "title", "font", "weight")) is not None:
            d["titleFontWeight"] = v
        if (v := _n(axis, "title", "padding")) is not None:
            d["titlePadding"] = v
        if (v := _n(axis, "title", "angle")) is not None:
            d["titleAngle"] = v
        if (v := _n(axis, "title", "align")) is not None:
            d["titleAlign"] = v
        # Vega-Lite has no default titleLimit (unlike labelLimit's 180), so an
        # authored cap is the only thing that bounds a title VL would otherwise
        # draw at full length.
        if (v := _n(axis, "title", "max_width")) is not None:
            d["titleLimit"] = v

    # Grid
    if (v := _n(axis, "grid", "visible")) is not None:
        d["grid"] = v
    if (v := _n(axis, "grid", "opacity")) is not None:
        d["gridOpacity"] = v
    if (v := _n(axis, "grid", "width")) is not None:
        d["gridWidth"] = v
    if (v := _n(axis, "grid", "color")) is not None:
        d["gridColor"] = v
    if (v := _n(axis, "grid", "dash")) is not None:
        d["gridDash"] = v

    # Domain (dbt charts' `line` field — VL's axis.domain* properties)
    if (v := _n(axis, "line", "visible")) is not None:
        d["domain"] = v
    if (v := _n(axis, "line", "width")) is not None:
        d["domainWidth"] = v
    if (v := _n(axis, "line", "color")) is not None:
        d["domainColor"] = v

    # Ticks
    if (v := _n(axis, "ticks", "visible")) is not None:
        d["ticks"] = v
    if (v := _n(axis, "ticks", "color")) is not None:
        d["tickColor"] = v
    if (v := _n(axis, "ticks", "length")) is not None:
        d["tickSize"] = v
    if (v := _n(axis, "ticks", "width")) is not None:
        d["tickWidth"] = v
    if (v := _n(axis, "ticks", "offset")) is not None:
        d["offset"] = v

    # Flat fields
    if (v := _n(axis, "position")) is not None:
        d["orient"] = v
    if (v := _n(axis, "band_position")) is not None:
        d["bandPosition"] = v
    if (v := _n(axis, "scale", "values")) is not None:
        d["values"] = v

    return d


def bake_tick_ladder(ay_vl: VLDict, tick_values: tuple[float, ...]) -> None:
    """Bake the resolved measure-axis tick ladder into VL ``axis.values``.

    ``tick_values`` (``ResolvedAxisStyle.tick_values``) is the domain-spanning
    nice-tick ladder computed at resolve() — it must reach every cartesian
    measure channel (vertical/horizontal bar, single/multi-metric line, area,
    and scatter) so two charts pinned to the same authored domain read on one
    ruler regardless of orientation or emit path. A no-op when the author
    already supplied an explicit ``axis.values`` list (``axis_to_vl`` already
    wrote it) or when no ladder was baked (``ticks.count`` unset).
    """
    if tick_values and "values" not in ay_vl:
        ay_vl["values"] = list(tick_values)


def measure_axis_to_vl(
    axis: Any,
    data: list[VLDict],
    y_fields: tuple[str, ...],
    *,
    category_labels: tuple[str, ...] | None = None,
) -> dict[str, Any]:
    """``axis_to_vl`` for a measure axis that keeps its left/right orient in the
    final spec — i.e. every cartesian y-axis except horizontal bar's measure
    axis (which renders on VL's x channel and strips ``orient`` afterward, so
    it must call ``axis_to_vl`` directly instead of this wrapper). Every NEW
    emitter that keeps a left/right measure-axis orient must route through
    this wrapper, not raw ``axis_to_vl`` — the guard is per-call-site, so a
    forgotten wrapper silently reintroduces the invasion below.

    An authored (or resolve-mapped, from ``label.align: inward``) own-side
    ``label.align`` flips the label's growth direction back toward the plot.
    Vega-Lite's ``autosize: fit`` estimate doesn't reserve gutter for that
    direction on its own, but the same vl-convert probe that found the bug
    also found ``labelPadding`` widens the reserved gutter 1:1 regardless of
    align direction — so when dbt charts can determine the tick *content*, it
    computes the real label width and sets ``labelPadding`` from it instead of
    falling back. Two sources of exact content: ``category_labels`` (literal
    per-row values for a nominal/ordinal axis — e.g. heatmap row labels; no
    format uncertainty, mirrors bar.py's categorical own-side gate) when the
    caller supplies it, or ``axis.tick_values``/``data``+``y_fields``'s domain
    plus a concrete ``axis.format`` for a quantitative axis (see
    ``_measured_label_padding.py``'s module docstring for the baked-vs-estimated
    split). Any own-side align dbt charts can't safely measure from (neither
    source available, an authored ``label.expr``, or a ``label.font.case`` of
    ``upper``/``lower`` — VL renders ``labelExpr`` in preference to ``format``,
    and `inject_axis_label_case` (run by callers after this function returns)
    injects one for upper/lower case, so a d3-format-derived measurement taken
    here would silently mismeasure what actually renders) falls back to the
    away-side default via ``_fallback_reversed_label_align`` instead of
    reserving an unmeasured gutter.
    """
    d = axis_to_vl(axis)
    orient = d.get("orient")
    align = d.get("labelAlign")
    labels: list[str] = []
    if (
        orient in ("left", "right")
        and align == orient
        and "labelExpr" not in d
        and axis.labels.font.case not in ("upper", "lower")
    ):
        if category_labels:
            labels = list(category_labels)
        else:
            # tick_label (set only when ruler is None, per
            # ResolvedAxisStyle's mutual-exclusion invariant) is what this
            # axis's ticks actually paint, via inject_axis_numeral_expr's
            # labelExpr composition (axis_to_vl leaves `format` alone --
            # see its docstring) -- measuring against the shared
            # label.format instead would size the gutter for the wrong
            # (SI-compacted) string.
            if axis.tick_label is not None:
                measure_format = axis.tick_label.format
            elif axis.labels.format is not None:
                measure_format = axis.labels.format
            else:
                measure_format = None
            # A PREDEFINED_NATIVE format name (e.g. "percent_number") bypasses
            # d3 entirely -- it paints via a Python lambda, not a d3-format
            # spec. quantitative_tick_labels/estimated_quantitative_tick_labels
            # call d3_format() directly, which raises D3FormatError on a bare
            # name like this. Native formats are only valid in Python-painted
            # slots (KPI, table) per predefined_formats.py -- an axis reaching
            # one here means compile-time validation didn't catch an invalid
            # authoring, not that this code should guess how to measure it.
            if measure_format is not None and measure_format in PREDEFINED_NATIVE_NAMES:
                measure_format = None
            if measure_format is not None:
                if axis.tick_values:
                    labels = quantitative_tick_labels(
                        tuple(axis.tick_values),
                        measure_format,
                        ruler=axis.ruler,
                        tick_label=axis.tick_label,
                    )
                else:
                    _sc = axis.scale
                    _sc_cont = _sc.continuous if _sc is not None else None
                    domain = _sc_cont.domain if _sc_cont is not None else None
                    bounds = numeric_domain_bounds(domain)
                    # When the axis has an explicit domain (e.g. normalize-stack
                    # bakes [0.0, 1.0]), measure from the domain ONLY — raw data
                    # values outside that range can't produce ticks there.
                    if bounds is not None:
                        values = list(bounds)
                    else:
                        values = numeric_values(data, y_fields)
                    # tick_label.si_format/.scientific_format (set only for a
                    # ladder-less axis) mirror the exact per-tick guard
                    # inject_axis_numeral_expr composes into the real
                    # labelExpr -- see estimated_quantitative_tick_labels's
                    # own docstring for why measuring measure_format alone
                    # would OVER-measure every candidate >= 1 (the axis
                    # paints "1.5M"; measure_format alone reads "1500000").
                    labels = estimated_quantitative_tick_labels(
                        values,
                        measure_format,
                        si_format=axis.tick_label.si_format
                        if axis.tick_label is not None
                        else None,
                        scientific_format=axis.tick_label.scientific_format
                        if axis.tick_label is not None
                        else None,
                    )
    if labels:
        padding = measured_label_padding(
            labels, axis.labels.font.family, axis.labels.font.size
        )
        label_limit = (
            axis.labels.max_width
            if axis.labels.max_width is not None
            else DEFAULT_VL_LABEL_LIMIT
        )
        d["labelPadding"] = cap_padding_to_label_limit(padding, label_limit)
    else:
        _fallback_reversed_label_align(d)
    return d


def _fallback_reversed_label_align(ax_vl: dict[str, Any]) -> None:
    """Drop ``labelAlign`` when it grows y-axis labels back across the axis
    line and dbt charts can't safely compute a ``labelPadding`` that keeps it
    clear of the plot (see ``measure_axis_to_vl``).

    An own-side align without baked tick content (ordinal axes, or an
    unformatted quantitative axis) has no exact-string guarantee to measure
    from — rather than reserving an unmeasured (and possibly wrong) gutter,
    fall back to Vega-Lite's own away-from-plot default by omitting
    ``labelAlign`` entirely. Emptiness/labelExpr/font.case are render-time
    facts resolve can't see when it maps ``inward``/``outward`` to a concrete
    side, so this is the safety net, not an authoring error. ``"center"``
    invades at half strength regardless of measurability (the anchor point
    centers each label on the axis line, so half of every label crosses into
    the plot — no single-sided padding value fixes a bidirectional gutter),
    so it always falls back here too, never reaching the measuring branch.
    """
    orient = ax_vl.get("orient")
    align = ax_vl.get("labelAlign")
    if orient not in ("left", "right") or align not in (orient, "center"):
        return
    del ax_vl["labelAlign"]


def _decimal_pad_vega_expr(missing_len_e: str, pad_table: tuple[str, ...]) -> str:
    """Vega ternary chain selecting pad_table[missing_len_e] without a loop variable.

    Builds from the highest index downward so the innermost (smallest)
    indices get the tightest equality test -- a 3-entry table (precision=1)
    becomes the 2-way ternary Vega evaluates cheapest.
    """
    expr = json.dumps(pad_table[-1])
    for i in range(len(pad_table) - 2, -1, -1):
        expr = f"({missing_len_e} === {i} ? {json.dumps(pad_table[i])} : {expr})"
    return expr


def _decimal_pad_missing_len_expr(formatted_e: str, precision: int) -> str:
    """Vega expression for ``decimal_pad_for``'s own index formula
    (``precision - frac if frac else precision + 1``), computed from the
    TRIMMED formatted string's own fractional DIGIT count -- the ONE
    definition of "missing depth" ``decimal_pad_for`` (``core/text/
    numeral_scale.py``) already uses to build the very ``pad_table`` this
    selects from, via the shared ``fractional_digit_count``.

    Replaced a length-diff formula (``length(format(v, spec_without_~)) -
    length(formatted_e)``) that only agreed with ``decimal_pad_for``'s index
    space for a fixed-point (``"f"``-type) spec, where dropping ``~`` *is*
    the trimming. Two real divergences broke that shortcut for other specs:
    a significant-figures (``"s"``-type) spec's integer part alone can
    already fill the significant-figure budget with no fractional part at
    all (``format(128, '.3s')`` is ``"128"`` whether or not ``~`` is
    present, so the length diff is 0 even though the value is a whole
    number needing the DEEPEST pad -- ``decimal_pad_for`` maps "no
    fractional digits" to ``precision + 1``, not 0), and a magnitude suffix
    letter (``"M"``) lands in the untrimmed string's own fractional-part
    slice, corrupting a plain length count; separately, a pad table capped
    below its spec's own declared precision (``decimal_pad_table_for``'s
    ``max_precision``) disagreed with a length-diff computed from the
    UNCAPPED spec. Reading fractional digits straight off the TRIMMED
    string sidesteps all three: it needs no untrimmed reference and no
    knowledge of the table's own cap, so it agrees with
    ``decimal_pad_for`` by construction for every spec type and cap.
    """
    frac_e = f"replace({formatted_e}, /^[^.]*\\.?/, '')"
    frac_digits_e = f"length(replace({frac_e}, /[^0-9]/g, ''))"
    return f"({frac_digits_e} === 0 ? {precision + 1} : {precision} - {frac_digits_e})"


def _apply_decimal_pad(formatted_e: str, pad_table: tuple[str, ...]) -> str:
    """Append a decimal pad to an already-built Vega format expression, or
    return it unchanged when there's no pad table.

    ``formatted_e`` may itself be a ternary combining an anchor-tick spec
    (currency symbol embedded via ``with_symbol``) with the plain spec --
    ``_decimal_pad_missing_len_expr`` reads digits off whichever branch the
    ternary evaluates to at runtime, so a currency symbol (which never
    lands after the decimal point) can't change which pad_table entry gets
    selected.
    """
    if not pad_table:
        return formatted_e
    precision = len(pad_table) - 2
    missing_len_e = _decimal_pad_missing_len_expr(formatted_e, precision)
    pad_e = _decimal_pad_vega_expr(missing_len_e, pad_table)
    return f"({formatted_e} + {pad_e})"


def inject_axis_numeral_expr(
    ax_vl: dict[str, Any],
    ruler: ResolvedRulerAxis | None,
    tick_label: ResolvedTickLabel | None = None,
) -> dict[str, Any]:
    """Compose the ladder's digit producer -- the innermost labelExpr layer.

    ``inject_axis_label_case`` and ``inject_axis_label_values_filter`` (below)
    transform ``datum.label``, a string VL already formatted from a bare d3
    spec, so they wrap whatever text is already in ``labelExpr``. This
    function reads ``datum.value``, the raw tick number, and *produces* the
    label text -- the quantitative sibling of the temporal smart-cadence step
    in ``type_inference.py``, which is the same kind of producer for a
    temporal axis. It is therefore the innermost layer: call it first, and
    let case/label.values wrap around whatever it emits, exactly as they
    already wrap the temporal cadence expression. Not a fourth ad-hoc
    wrapper -- a second producer with the same stated order.

    Takes ``ruler`` directly rather than a whole ``ResolvedAxisStyle`` --
    the single baked "does this axis ship the ruler composition" decision
    (``ResolvedAxisStyle.ruler`` for the primary edge,
    ``ResolvedAxisStyle.mirror_ruler`` for the mirror ghost -- both baked
    the same way, at resolve, so this function never needs to know which
    edge it is composing for). It already folds in every gate that used to
    be re-checked here (does the ladder compact, is the format SI-shaped,
    is ``label.expr`` unauthored) and the column-forming override (a
    horizontal ruler is baked to REPEAT mode and narrative register,
    ``reserve=False``, and ``prefix_repeats=True``, at resolve -- render
    never re-declares orientation). ``mode`` and ``prefix_repeats`` are
    independent facts: ``mode`` also follows REPEAT on a column-forming axis
    whose ladder's own magnitude calls for a narrative suffix register, but
    ``prefix_repeats`` stays keyed on column_forming alone -- a currency
    prefix has no such magnitude-driven register to it.

    Falls back to ``tick_label`` -- ``ruler``'s non-compacting sibling --
    when ``ruler`` is None. The two are mutually exclusive by construction
    (``ResolvedAxisStyle.__post_init__``), so at most one branch ever fires.
    Unlike ``ruler``, ``tick_label`` has no per-edge mirror variant (see
    ``ResolvedAxisStyle.mirror_ruler``'s docstring): callers always pass the
    primary axis's own resolved ``tick_label``, never a mirror-specific one.
    This composition is deliberately NOT done by writing ``tick_label.format``
    onto VL's axis ``format`` in ``axis_to_vl``: Vega's own auto-generated
    axis description (its SVG ARIA accessibility label) also reads
    ``format``, applying it to the scale's domain min/max -- values that are
    not ticks, and that the tick-derived precision was never sized for
    (verified empirically against `vl_convert`: overriding `format` rounds a
    domain endpoint like 43.2 to "43" in the description even though every
    visible tick paints correctly; a `labelExpr` leaves the description
    alone). Composing it here, at the same point and by the same mechanism as
    the ruler branch, keeps ``format`` untouched either way and lets
    ``measure_axis_to_vl`` (this module) and the mirror ghost
    (``features/mirror_axis.py``) read the resolved ``tick_label`` field
    directly -- rather than re-deriving "what will labelExpr paint" from a VL
    dict -- before handing the plain format string down to
    ``_measured_label_padding.py``'s gutter-measurement primitives.

    ``tick_label.prefix`` / ``tick_label.anchor_at_start`` parallel the
    structure of ``ruler.prefix`` / ``ruler.anchor_at_start`` for the
    non-compacting case: when the format has a currency symbol and
    ``anchor_at_start`` is a bool (column-forming, baked at resolve), the
    prefix appears on the anchor tick only. When ``anchor_at_start`` is
    ``None`` instead (non-column-forming), the prefix repeats on every tick
    -- mirrors ``ruler``'s ``prefix_repeats``, not its ``mode`` (see above:
    the two decisions are independent). Either way a zero-valued
    tick never carries the symbol (mirrors ``ruler``'s ``suffix_present``): a
    "$0" baseline is no more informative than a bare "0". Integer place-value
    alignment is automatic under text-anchor=end; fractional/decimal tail
    alignment is not -- when ``tick_label.decimal_pad_table`` is set, a pad
    string is appended to each trimmed-decimal value so all labels have
    equal rendered advance.

    The anchor semantics differ between the two: ``ruler.anchor_at_start``
    is magnitude-based (the tick furthest from zero, regardless of sign,
    since a shared "K"/"M"/"B" suffix scale depends on the magnitude
    extreme); ``tick_label.anchor_at_start`` is sign-aware (largest positive
    tick when any exist, else most-negative -- a "$" prefix has no scale
    dependency the way a shared suffix does).

    ``tick_label.si_format`` is a third, mutually exclusive shape from the
    above: set only for a ladder-LESS axis (``build_resolved_axis`` had no
    ``tick_values`` to derive a step from), where ``tick_label.format`` alone
    would otherwise flatten every tick to plain digits and trade away the
    house's k/M compaction above 1. Handled first, before the
    prefix/anchor/pad machinery above (which assumes a real ladder's fixed
    spec) -- see the branch below. Never set for a log-scale axis, even
    though it also reaches ``build_resolved_axis`` with empty ``tick_values``
    (its own unrelated reason -- see ``axis_cascade.py``): composing ANY
    ``labelExpr`` here, regardless of content, defeats Vega's own
    ``labelOverlap`` thinning of a log axis's dense minor-tick ladder, since
    that thinning only runs when the axis paints from a plain ``format``
    string.

    No-ops (returns ``ax_vl`` unchanged) when both ``ruler`` and
    ``tick_label`` are None, or when ``labelExpr`` is already set
    (belt-and-braces: resolve's authored-``label.expr`` check already
    implies this).
    """
    if "labelExpr" in ax_vl:
        return ax_vl
    if ruler is None:
        if tick_label is None:
            return ax_vl
        if tick_label.si_format is not None:
            # No ladder means no step to derive one fixed spec from (the
            # branch below's mechanism) -- Vega's own auto-picked ticks can
            # land anywhere the domain allows. Gate on each tick's own
            # magnitude instead of a baked position (`datum.index`, the
            # anchor mechanisms below use): a tick at or above 1 keeps this
            # axis's own SI format untouched, so k/M compaction is
            # unaffected; below 1, where nothing chose SI to begin with,
            # `tick_label.format` (`sub_unit_digit_format`'s significant
            # digits) applies -- except far enough below 1 that even that
            # collapses into a wall of leading zeros, where
            # `tick_label.scientific_format` (`sub_unit_scientific_format`,
            # gated at `SUB_UNIT_SCIENTIFIC_FLOOR`) applies instead, mirroring
            # `non_compacting_tick_format`'s own fixed-point/scientific split
            # for a real ladder. `tick_label.scientific_format` is never None
            # here -- `ResolvedTickLabel.__post_init__` requires it whenever
            # `si_format` is set, which this branch already checked -- so
            # there is no unguarded arm to fall back to. Neither arm has a
            # prefix/decimal-pad device of its own -- see
            # `sub_unit_digit_format`'s own docstring for why.
            si_e = f"format(datum.value,{json.dumps(tick_label.si_format)})"
            plain_e = f"format(datum.value,{json.dumps(tick_label.format)})"
            # `datum.value !== 0` first: zero is exactly representable at any
            # precision and reads as a plain "0" -- the fixed-point register
            # handles it correctly on its own; scientific would print "0e+0"
            # instead.
            scientific_e = (
                f"format(datum.value,{json.dumps(tick_label.scientific_format)})"
            )
            sub_one_e = (
                f"(datum.value !== 0 && abs(datum.value) < "
                f"{json.dumps(SUB_UNIT_SCIENTIFIC_FLOOR)} "
                f"? {scientific_e} : {plain_e})"
            )
            return {
                **ax_vl,
                "labelExpr": f"(abs(datum.value) < 1 ? {sub_one_e} : {si_e})",
            }
        trimmed_e = f"format(datum.value,{json.dumps(tick_label.format)})"
        text_e = _apply_decimal_pad(trimmed_e, tick_label.decimal_pad_table)
        if tick_label.prefix:
            # Use with_symbol so d3-format's sign-before-symbol ordering
            # applies: format("$,.0f")(-500) -> "-$500", not "$-500". Integer
            # place-value is automatic under text-anchor=end; decimal padding
            # is handled by _apply_decimal_pad below (missing-length math is
            # symbol-invariant, so the plain tick_label.format spec is what's
            # passed for that, not anchor_spec).
            anchor_spec = with_symbol(tick_label.format, tick_label.prefix)
            anchor_e = _apply_decimal_pad(
                f"format(datum.value,{json.dumps(anchor_spec)})",
                tick_label.decimal_pad_table,
            )
            if tick_label.anchor_at_start is None:
                prefix_present = "datum.value !== 0"
            else:
                vega_anchor_idx = 0 if tick_label.anchor_at_start else 1
                prefix_present = (
                    f"datum.value !== 0 && datum.index === {vega_anchor_idx}"
                )
            return {
                **ax_vl,
                "labelExpr": f"({prefix_present} ? {anchor_e} : {text_e})",
            }
        return {**ax_vl, "labelExpr": text_e}

    # ruler.prefix / ruler.digit_spec are split from axis.format once, at
    # resolve (ruler_digit_format, core/text/numeral_scale.py) -- the digit
    # portion never carries an SI type (the magnitude is already divided out
    # below, and feeding a scaled value back through d3's own 's' type would
    # let it pick a *second* suffix on top of ours). quantitative_tick_labels
    # (_measured_label_padding.py) reads the same two baked strings, so the
    # axis paints and the gutter measures identically by construction.
    prefix, digit_spec = ruler.prefix, ruler.digit_spec
    value_expr = f"(datum.value/{10.0**ruler.exponent!r})"
    # digit_spec never carries an SI type, so no notation ever applies here --
    # numeral_vega_expr is reused only for its format(...) wrapping.
    digits_expr = numeral_vega_expr(value_expr, digit_spec)
    suffix_text = ruler.suffix_string

    # datum.index is Vega's own normalized 0-to-1 position over whichever
    # ticks it actually draws -- ruler.anchor_at_start picks WHICH end (0 or
    # 1) carries the magnitude, decided once at resolve from the ladder's
    # own shape (see ResolvedAxisStyle.ruler's docstring for why a position
    # is used here rather than the tick's raw value: nice_tick_values's
    # ladder routinely includes an entry Vega never actually draws, and a
    # value comparison against a dropped entry never matches any real tick).
    anchor_index = 0 if ruler.anchor_at_start else 1
    anchor_test = f"datum.index === {anchor_index}"
    if ruler.mode is SuffixMode.ANCHOR:
        suffix_present = f"datum.value !== 0 && {anchor_test}"
    else:
        suffix_present = "datum.value !== 0"

    # For currency prefix: use with_symbol on the qualifying tick(s) so
    # d3-format's own sign-before-symbol ordering applies (format("$,.1~f")(-500)
    # -> "-$500", not "$" + format(",.1~f")(-500) -> "$-500"). Gated on
    # ruler.prefix_repeats, not ruler.mode/suffix_present -- see this
    # function's docstring. ruler.reservation is baked at
    # resolve (compose_suffix_reservation, core/font_measure.py) from the
    # same suffix_text this function reads -- quantitative_tick_labels
    # (_measured_label_padding.py) reads the identical baked string, so
    # paint and the gutter measurement can never disagree. Decimal padding
    # (fractional-tail alignment) is handled by ruler.decimal_pad_table via
    # _apply_decimal_pad below -- the same table quantitative_tick_labels
    # reads, so the two are always in sync. Its missing-length math is
    # computed from the plain digit_spec (never anchor_digit_spec), which is
    # correct for both branches of the ternary (a currency symbol adds equal
    # length to both sides of the length-diff).
    if prefix:
        anchor_digit_spec = with_symbol(digit_spec, prefix)
        anchor_digits_expr = numeral_vega_expr(value_expr, anchor_digit_spec)
        prefix_present = (
            "datum.value !== 0"
            if ruler.prefix_repeats
            else f"datum.value !== 0 && {anchor_test}"
        )
        digit_expr_combined = (
            f"({prefix_present} ? {anchor_digits_expr} : {digits_expr})"
        )
    else:
        digit_expr_combined = digits_expr

    parts: list[str] = []
    parts.append(_apply_decimal_pad(digit_expr_combined, ruler.decimal_pad_table))
    padding_expr = json.dumps(ruler.reservation) if ruler.reserve else "''"
    parts.append(f"({suffix_present} ? {json.dumps(suffix_text)} : {padding_expr})")

    return {**ax_vl, "labelExpr": " + ".join(parts)}


def _array_literal_elements(expr: str) -> list[str] | None:
    """Split a JS array-literal expression string into its top-level elements.

    Returns ``None`` when ``expr`` isn't a ``[...]`` array literal, so callers
    can fall back to treating it as a single scalar expression. Depth-aware
    over ``()[]{}`` and quoted strings so a comma inside a nested call (e.g.
    ``utcFormat(datum.value, '%b %-d')``) never splits the array early — the
    sub-day clock vocabulary's two-row labelExpr is exactly this shape.
    """
    if not (expr.startswith("[") and expr.endswith("]")):
        return None
    inner = expr[1:-1]
    elements: list[str] = []
    depth = 0
    quote: str | None = None
    start = 0
    i = 0
    while i < len(inner):
        c = inner[i]
        if quote:
            if c == "\\":
                i += 1
            elif c == quote:
                quote = None
        elif c in "'\"":
            quote = c
        elif c in "([{":
            depth += 1
        elif c in ")]}":
            depth -= 1
        elif c == "," and depth == 0:
            elements.append(inner[start:i].strip())
            start = i + 1
        i += 1
    elements.append(inner[start:].strip())
    return elements


def inject_axis_label_case(ax_vl: dict[str, Any], axis: Any) -> dict[str, Any]:
    """Wrap existing labelExpr with a Vega case function for axis.label.font.case.

    Only 'upper' and 'lower' can be expressed as Vega label expressions; other
    CaseValues ('title', 'sentence', 'slug', 'camel') require Python-side string
    manipulation and are silent no-ops here. The inner expression defaults to
    ``datum.label`` when no labelExpr is present, so case injection on a plain
    axis emits ``upper(datum.label)``; on a temporal axis it wraps the smart
    cadence expression that was already set.

    A two-row array-literal labelExpr (the sub-day clock vocabulary's date
    context row) is wrapped element-by-element, never as a single
    ``upper([a, b])`` call around the whole array: Vega's ``upper`` maps to
    JS's ``String#toUpperCase``, which coerces an array argument via
    ``Array#toString`` first — silently joining both rows into one
    comma-separated string before the case function ever runs.

    MUST be called AFTER build_cartesian_x_encoding for x-axes so the temporal
    smart-cadence labelExpr is already set before wrapping.
    """
    case = _n(axis, "labels", "font", "case")
    if case not in ("upper", "lower"):
        return ax_vl
    inner = ax_vl.get("labelExpr", "datum.label")
    elements = _array_literal_elements(inner)
    if elements is not None:
        wrapped = ", ".join(f"{case}({element})" for element in elements)
        return {**ax_vl, "labelExpr": f"[{wrapped}]"}
    return {**ax_vl, "labelExpr": f"{case}({inner})"}


def inject_axis_label_values_filter(ax_vl: dict[str, Any], axis: Any) -> dict[str, Any]:
    """Wrap labelExpr with a membership filter for authored ``labels.values``.

    Blanks any tick whose date isn't in ``axis.labels.values``, decoupling
    label density from tick/grid density: tick/grid values are left
    dense (auto-filled or explicitly authored via ``axis.scale.values``); only
    the listed dates keep their label text. The inner expression defaults to
    ``datum.label`` when no labelExpr is present yet, so authoring only
    ``labels.values`` on an otherwise-plain temporal axis still respects
    whatever ``format`` VL already applied to ``datum.label``.

    MUST be called AFTER build_cartesian_x_encoding / inject_axis_label_case
    so it wraps whatever label text (smart cadence, custom format, case) those
    steps already produced — labels.values always wins on top.
    """
    label_values = _n(axis, "labels", "values")
    if label_values is None:
        return ax_vl
    from dbt_charts.core.render.chart.time_unit_detect import label_values_filter_expr

    inner = ax_vl.get("labelExpr", "datum.label")
    return {**ax_vl, "labelExpr": label_values_filter_expr(label_values, inner)}


def compose_axis_label_expr(
    ax_vl: dict[str, Any], ruler: ResolvedRulerAxis | None, axis: ResolvedAxisStyle
) -> dict[str, Any]:
    """The one place that chains the three ``labelExpr`` layers, in the
    order ``inject_axis_numeral_expr``'s own docstring establishes: the
    ruler-driven numeral producer runs innermost, then case, then the
    ``labels.values`` membership filter (a documented no-op on ``axis_y`` --
    the field lives only on ``DimensionLabelStyle``, the x-axis label style;
    ``axis_y``'s label style has no ``values`` field at all).

    Every caller that needs a quantitative axis's full ``labelExpr`` --
    a primary axis's own build (line.py's y-axis) and the mirror ghost's
    rebuild (``MirrorAxisFeature``, when its own resolved anchoring differs
    from the inherited copy's) -- calls this one function instead of
    chaining the three independently. Chaining them per call site lets an
    edge composed by one site diverge from another the moment a wrapper is
    added. One function, with the order encoded once, means the next
    wrapper reaches every caller without any of them having to remember to
    add it.
    """
    ax_vl = inject_axis_numeral_expr(ax_vl, ruler, axis.tick_label)
    ax_vl = inject_axis_label_case(ax_vl, axis)
    ax_vl = inject_axis_label_values_filter(ax_vl, axis)
    return ax_vl


def legend_to_vl(legend: Any) -> dict[str, Any] | None:
    """Map any legend style object → VL legend config dict (non-None fields only).

    Returns None when visible=False (VL legend: null disables the legend entirely).
    Works for LegendStyle and LegendStylePatch.

    dbt charts → VL name renames applied here:
      label.max_width  → labelLimit
      label.padding    → labelOffset  (gap between symbol and label text)
    """
    if legend is None:
        return {}
    if _n(legend, "visible") is False:
        return None  # VL: encoding.color.legend = null

    d: dict[str, Any] = {}
    if (v := _n(legend, "position")) is not None:
        d["orient"] = v
    if (v := _n(legend, "direction")) is not None:
        d["direction"] = v
    if (v := _n(legend, "columns")) is not None and v > 0:
        d["columns"] = v
    if (v := _n(legend, "symbol_limit")) is not None:
        d["symbolLimit"] = v
    if (v := _n(legend, "values")) is not None:
        d["values"] = list(v)
    if (v := _n(legend, "label", "font", "color")) is not None:
        d["labelColor"] = v
    if (v := _n(legend, "label", "font", "family")) is not None:
        d["labelFont"] = v
    if (v := _n(legend, "label", "font", "size")) is not None:
        d["labelFontSize"] = v
    if (v := _n(legend, "label", "font", "weight")) is not None:
        d["labelFontWeight"] = v
    if (v := _n(legend, "label", "max_width")) is not None:
        d["labelLimit"] = v
    if (v := _n(legend, "label", "padding")) is not None:
        d["labelOffset"] = v
    # Title: null suppresses the legend title (the text otherwise flows from
    # encoding.<channel>.title, which we leave intact for aria-labels / tooltips).
    # Emitting null removes the title from layout entirely — unlike titleFontSize:0,
    # which collapses the glyphs but still occupies space. Mirrors axis_to_vl.
    if _n(legend, "title", "visible") is False:
        d["title"] = None
    else:
        if (v := _n(legend, "title", "font", "color")) is not None:
            d["titleColor"] = v
        if (v := _n(legend, "title", "font", "family")) is not None:
            d["titleFont"] = v
        if (v := _n(legend, "title", "font", "size")) is not None:
            d["titleFontSize"] = v
        if (v := _n(legend, "title", "font", "weight")) is not None:
            d["titleFontWeight"] = v
    # Author overrides: constant symbolType wins over mark-derived Vega expressions.
    if (v := _n(legend, "symbol_shape")) is not None:
        d["symbolType"] = v
    if _n(legend, "symbol_fill") is False:
        d["symbolFillColor"] = "transparent"
    return d


# Per-type mark mappers are intentionally separate: each chart type exposes a different
# subset of mark properties with different VL field names (e.g. bar uses
# mark.width, line uses interpolate, scatter uses size). A single generic mapper
# would need to union all fields and conditionally skip inapplicable ones — more complex
# with no benefit since callers always know the chart type.
def bar_mark_to_vl(
    bar: BarMarkStyle, orientation: Literal["vertical", "horizontal"], x_is_banded: bool
) -> VLDict:
    """Map BarStyle/Patch → VL extended mark dict (non-None only).

    Does NOT emit corner-radius properties — those are data-dependent and must
    be set by the caller via ``bar_corner_props``. Does NOT compute a
    continuous-scale default width — that's ``continuous_bar_size_prop``'s job
    (it needs the data to compute a step, which this function never receives);
    the caller only reaches for it when ``bar.size`` is unset AND the scale
    isn't banded, since this function already handles the authored case below.

    ``bar.size``, when authored, is a literal fixed pixel width on ANY scale —
    band or continuous — and wins outright over ``band_width``: "authored
    beats computed, always". Otherwise, on a banded scale (``x_is_banded``),
    ``orientation`` controls which dimension receives the band fraction:
    - ``"vertical"``: ``mark.width = {"band": band_width}``  — x categorical band.
    - ``"horizontal"``: ``mark.height = {"band": band_width}``  — y categorical band.

    ``x_is_banded`` gates the ``band_width`` fallback entirely: VL's
    ``{"band": f}`` width shorthand only resolves against a genuine band/bin
    scale (nominal, ordinal, bucketed-calendar temporal, or a histogram's
    binned quantitative x). On a continuous scale with no band to measure, the
    shorthand doesn't error or fall back to ``f`` of anything meaningful —
    measured empirically, it renders every bar at a fixed ~18px, Vega's own
    internal default for an unresolvable band width, regardless of the
    fraction or any other mark property. ``continuousBandSize`` (formerly
    emitted from ``bar.size`` here) is dead code on every scale: Vega-Lite's
    own bar mark never reads it, band or continuous. Deleted rather than kept
    for "compatibility" — nothing consumed it correctly to begin with.
    """
    d: VLDict = {}
    if (v := _n(bar, "opacity")) is not None:
        # BarMarkStyle.opacity is documented as fill opacity, not overall mark
        # opacity: VL's top-level `opacity` multiplies fill AND stroke alike
        # (confirmed empirically -- the rendered path carries a single SVG
        # `opacity` attribute covering both), which would hide an authored
        # `border` right along with the fill on an opacity:0 bar. `fillOpacity`
        # is the native VL property that isolates the two, leaving the stroke
        # at its own (default 1) opacity -- exactly what an outline-only bar
        # (opacity:0 fill + a visible border) needs.
        d["fillOpacity"] = v
    if (v := _n(bar, "border", "color")) is not None:
        d["stroke"] = v
    if (v := _n(bar, "border", "width")) is not None:
        d["strokeWidth"] = v
    size = _n(bar, "size")
    if size is not None:
        if orientation == "horizontal":
            d["height"] = size
        else:
            d["width"] = size
    elif x_is_banded and (v := _n(bar, "band_width")) is not None:
        if orientation == "horizontal":
            d["height"] = {"band": v}
        else:
            d["width"] = {"band": v}
    return d


def continuous_bar_size_prop(
    bar: BarMarkStyle,
    field: str | None,
    data: list[VLDict],
    orientation: Literal["vertical", "horizontal"],
) -> VLDict:
    """Build the VL mark width/height prop for a bar on a continuous, unauthored-size scale.

    Callers reach for this only when ``bar.size`` is None AND the scale isn't
    banded — ``bar_mark_to_vl`` already emits a literal ``bar.size`` on any
    scale, so this function never re-checks it. Computes a live Vega
    expression: the minimum pixel gap between adjacent distinct values of
    ``field`` (never the mean — an uneven cluster can be tight even when the
    average spacing looks fine), minus ``bar.gap``, clamped to
    ``[bar.min_size, bar.max_size]``.

    The gap-in-pixels is read back from the scale itself at render time
    (``scale('x', a + step) - scale('x', a)``, ``step`` computed here from the
    raw data in the field's own units) rather than estimated in Python from
    the chart's pixel width and domain bounds: Vega-Lite's own domain "nice"-
    rounding and continuous padding are internal to the runtime scale
    resolution vl-convert performs, not something this function can predict
    without reimplementing it — asking the live scale for the same two points
    it will actually plot keeps the two in sync by construction, the same
    reasoning ``_fix_bar_band_width`` (``emitters/_overlay.py``) already
    applies to a band scale's ``bandwidth(...)`` expression.

    ``field`` absent, or fewer than 2 distinct numeric values in ``data`` (no
    adjacent pair to measure a gap from) — falls back to a literal
    ``bar.max_size``: there's nothing to collide with, so the ceiling is a
    safe, simple width.

    Numeric coercion goes through ``coerce_numeric_cell`` — the same rule
    ``bar_hover_band.py`` uses — so a warehouse NUMERIC/DECIMAL x column
    (BigQuery, DuckDB) counts as numeric here too, and a non-finite cell
    (NaN, ±Infinity) is excluded rather than sorted alongside real values or
    interpolated into the emitted expression string.

    A continuous TEMPORAL scale takes the same treatment with the values
    coerced to epoch milliseconds (``epoch_ms``) — Vega's ``scale('x', …)``
    reads a number on a time scale as a timestamp, so the min-gap probe works
    identically. Tried only when numeric coercion finds fewer than 2 values:
    a column is one type, so whichever coercion matches ≥2 values is the
    column's.
    """
    from dbt_charts.core.render.chart.time_unit_detect import epoch_ms

    prop_key = "height" if orientation == "horizontal" else "width"
    xs = (
        sorted(
            {
                coerced
                for row in data
                if field in row
                and (coerced := coerce_numeric_cell(row[field])) is not None
            }
        )
        if field is not None
        else []
    )
    if len(xs) < 2 and field is not None:
        xs = sorted(
            {
                ms
                for row in data
                if field in row and (ms := epoch_ms(row[field])) is not None
            }
        )
    if len(xs) < 2:
        assert bar.max_size is not None, (
            "marks.bar.size and marks.bar.max_size both unset — "
            "theme cascade must populate at least one"
        )
        return {prop_key: bar.max_size}
    step = min(b - a for a, b in zip(xs, xs[1:], strict=False))
    scale_name = "y" if orientation == "horizontal" else "x"
    a = xs[0]
    # size is unset (the only way callers reach this function), so gap/min_size/
    # max_size are the fields the theme cascade must have populated — asserted
    # individually (rather than trusting the caller) since an interpolated
    # None would otherwise repr() as the literal text "None" in the expression
    # string below and die with an opaque Vega "Unrecognized signal name" error.
    assert bar.gap is not None, (
        "marks.bar.gap unset — theme cascade must populate it when marks.bar.size is unset"
    )
    assert bar.min_size is not None, (
        "marks.bar.min_size unset — theme cascade must populate it when marks.bar.size is unset"
    )
    assert bar.max_size is not None, (
        "marks.bar.max_size unset — theme cascade must populate it when marks.bar.size is unset"
    )
    expr = (
        f"clamp(abs(scale('{scale_name}', {a + step!r}) - scale('{scale_name}', {a!r})) "
        f"- {bar.gap!r}, {bar.min_size!r}, {bar.max_size!r})"
    )
    return {prop_key: {"expr": expr}}


def effective_bar_size(bar: BarMarkStyle) -> float | None:
    """The pixel bar thickness a caller should budget layout space against.

    ``bar.size`` when authored; otherwise ``bar.max_size``, the ceiling a
    computed continuous-scale width can never exceed — a safe upper-bound
    estimate for callers (horizontal-bar min-height, quantitative-x scale
    padding) that need a single number before the real per-bar width is
    known. Still ``| None`` in the rare case a chart-local override clears
    both — callers that require a concrete value assert on the result, the
    same cascade-completeness contract every other theme-populated field here
    carries.
    """
    return bar.size if bar.size is not None else bar.max_size


def bar_data_signs(data: list[dict[str, Any]], field: str) -> tuple[bool, bool]:
    """Return (has_positive, has_negative) for numeric values of field in data.

    Values of zero are treated as non-negative (they don't need negative-bar rounding).
    """
    values = [row[field] for row in data if isinstance(row.get(field), (int, float))]
    return any(v > 0 for v in values), any(v < 0 for v in values)


def bar_span_signs(data: Rows, end_field: str, start_field: str) -> tuple[bool, bool]:
    """Return (has_rise, has_fall) for spans running start_field → end_field.

    A zero-length span counts as a rise, as a zero value does in bar_data_signs.
    """
    pairs = [
        (
            coerce_numeric_cell(row.get(end_field)),
            coerce_numeric_cell(row.get(start_field)),
        )
        for row in data
    ]
    diffs = [e - s for e, s in pairs if e is not None and s is not None]
    return any(d >= 0 for d in diffs), any(d < 0 for d in diffs)


def bar_mark_radius(bar: BarMarkStyle) -> float | None:
    """Return the configured border radius for a bar mark, or None if unset."""
    return _n(bar, "border", "radius")


def bar_corner_props(
    radius: float, orientation: str, has_pos: bool, has_neg: bool
) -> dict[str, float] | None:
    """Return the VL corner-radius mark properties for a bar, given data signs.

    ``cornerRadiusEnd`` in Vega uses a clip-path anchored to the bar's local
    y=0 baseline, not the value tip.  For negative bars this rounds the wrong
    end, creating a visible gap at the zero-axis rule.  The correct properties
    depend on what signs are present:

    - all positive (or no data) → ``{"cornerRadiusEnd": radius}``
    - all negative              → explicit tip corners (bottom for vertical,
                                  left for horizontal)
    - mixed                     → ``None``; caller must split into two filtered
                                  layers and use this function per layer

    Obtain ``radius`` from ``bar_mark_radius(bar_style)``; if that returns
    ``None`` there is nothing to do and this function need not be called.
    """
    if not has_neg:
        return {"cornerRadiusEnd": radius}
    if not has_pos:
        if orientation == "horizontal":
            return {"cornerRadiusTopLeft": radius, "cornerRadiusBottomLeft": radius}
        return {"cornerRadiusBottomLeft": radius, "cornerRadiusBottomRight": radius}
    return None  # mixed: two-layer split required


def _emit_point_mark(point: Any) -> dict[str, Any]:
    """Build a VL point mark config dict from a MarkPointStyle/Patch (non-None fields only)."""
    d: dict[str, Any] = {}
    if (v := _n(point, "size")) is not None:
        d["size"] = v
    if (v := _n(point, "color")) is not None:
        d["color"] = v
    if (v := _n(point, "shape")) is not None:
        d["shape"] = v
    if (v := _n(point, "opacity")) is not None:
        d["opacity"] = v
    filled = _n(point, "filled")
    if filled is not None:
        d["filled"] = filled
    # fill only applies when filled=false; encoding.color owns the fill channel otherwise.
    if filled is False and (v := _n(point, "fill")) is not None:
        d["fill"] = v
    # Flat ``stroke_width`` (not a nested ``stroke`` block) — point marks
    # only consume strokeWidth; stroke color comes from the color encoding
    # and cap/join/dasharray don't apply to ring geometry.
    if (v := _n(point, "stroke_width")) is not None:
        d["strokeWidth"] = v
    return d


def line_mark_to_vl(line: Any, point: Any = None) -> dict[str, Any]:
    """Map LineMarkStyle → VL extended mark dict (non-None only).

    ``point`` is an optional PointMarkStyle; when provided and point.size > 0,
    emits a VL point overlay on the line.
    """
    if line is None:
        return {}
    d: dict[str, Any] = _stroke_to_vl(_n(line, "stroke"))
    if (v := _n(line, "curve")) is not None:
        d["interpolate"] = v
    if point is not None:
        pt_size = _n(point, "size")
        if pt_size is not None and pt_size > 0:
            d["point"] = _emit_point_mark(point)
    return d


def area_mark_to_vl(area: Any) -> dict[str, Any]:
    """Map AreaStyle/Patch → VL extended mark dict (non-None only)."""
    if area is None:
        return {}
    d: dict[str, Any] = {}
    if (v := _n(area, "opacity")) is not None:
        d["opacity"] = v
    if (v := _n(area, "curve")) is not None:
        d["interpolate"] = v
    d.update(_stroke_to_vl(_n(area, "stroke")))
    return d


def rect_mark_to_vl(rect: Any) -> dict[str, Any]:
    """Map RectMarkStyle/Patch → VL extended mark dict (non-None only)."""
    if rect is None:
        return {}
    d: dict[str, Any] = {}
    if (v := _n(rect, "opacity")) is not None:
        d["opacity"] = v
    d.update(_stroke_to_vl(_n(rect, "stroke")))
    return d


def scatter_mark_to_vl(point: Any) -> dict[str, Any]:
    """Map PointMarkStyle → VL extended mark dict (non-None only)."""
    if point is None:
        return {}
    return _emit_point_mark(point)


def slice_mark_to_vl(arc: Any) -> dict[str, Any]:
    """Map SliceStyle/Patch → VL extended mark dict (non-None only)."""
    if arc is None:
        return {}
    d: dict[str, Any] = {}
    if (v := _n(arc, "opacity")) is not None:
        d["opacity"] = v
    if (v := _n(arc, "gap")) is not None:
        d["padAngle"] = v
    if (v := _n(arc, "corner_radius")) is not None:
        d["cornerRadius"] = v
    d.update(_stroke_to_vl(_n(arc, "stroke")))
    return d


def map_fields(source: BaseModel, field_map: dict[str, str]) -> dict[str, object]:
    """Extract non-None fields from source, mapping snake_case to camelCase."""
    result: dict[str, object] = {}
    for src_name, dst_name in field_map.items():
        value = getattr(source, src_name, None)
        if value is not None:
            result[dst_name] = value
    return result
