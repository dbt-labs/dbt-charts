"""Vega-Lite field name maps and helper for snake_case → camelCase mapping."""

from __future__ import annotations

import json
import re
from typing import Any, Literal

from dbt_charts.core.compile.models.primitives import (
    ResolvedFormat,
    painted_sign_placement,
)
from dbt_charts.core.compile.models.style.resolved._base import (
    ResolvedLegendPosition,
)
from dbt_charts.core.render.numeral_expr import (
    numeral_vega_expr,
    vl_format_key,
)
from dbt_charts.core.text.format_d3 import (
    _LEADING_SIGN_CHARS,
    SignPlacement,
    active_format_locale,
    is_d3_si_spec,
)
from dbt_charts.core.text.numeral_scale import (
    has_symbol,
    without_symbol,
)
from dbt_charts.core.text.predefined_formats import (
    VEGA_ARMS,
)

# ``type``/log.base/pow.exponent/symlog.constant emit log/pow/symlog scale
# config. Must not be omitted — a missed field silently resolves to None and
# drops scale config from the emitted spec.
# Universal top-level scale fields (not in any sub-group).
# Encoding-level-only scale fields: nice, padding shorthand. domain is read
# explicitly from scale.continuous.domain below (nested, not a flat field).
# Applied to encoding.x.scale / encoding.y.scale only (not VL config).
# x-channel-only scale fields.


def _n(obj: Any, *path: str) -> Any:
    """Get nested attribute, returning None if any level is None or missing."""
    for attr in path:
        if obj is None:
            return None
        obj = getattr(obj, attr, None)
    return obj


def _affixed_digits_expr(
    value_expr: str,
    spec: str,
    fmt: ResolvedFormat,
    decimal_pad_table: tuple[str, ...] = (),
    *,
    anchored: bool,
    trailing_suffix: bool = True,
) -> str:
    """Vega expr mirroring ``format_d3``'s ``affix_signed`` order, for an affix
    d3's spec cannot embed: ``spec`` paints the digits, ``fmt`` supplies the
    affix, notation and sign placement, ``anchored`` whether the affix paints
    on one value only (``painted_sign_placement``).

    Formats the signed value so d3's sign decision survives; only a leading
    sign is relocated against the prefix.
    """
    formatted_e = _apply_decimal_pad(
        numeral_vega_expr(value_expr, spec, fmt.notation), decimal_pad_table
    )
    return _affix_signed_expr(
        formatted_e,
        fmt.prefix,
        fmt.suffix if trailing_suffix else "",
        painted_sign_placement(fmt, anchored),
    )


def _affix_signed_expr(
    text_e: str, prefix: str, suffix: str, sign_placement: SignPlacement
) -> str:
    """Vega twin of ``affix_signed``: ``text_e`` wrapped in the affix, its
    leading sign placed against ``prefix``."""
    has_sign_e = f"test(/^[{_LEADING_SIGN_CHARS}]/, {text_e})"
    sign_e = f"({has_sign_e} ? substring({text_e}, 0, 1) : '')"
    body_e = f"({has_sign_e} ? substring({text_e}, 1) : {text_e})"
    parts = [sign_e]
    if prefix:
        if sign_placement == "after_prefix":
            parts.insert(0, json.dumps(prefix))
        else:
            parts.append(json.dumps(prefix))
    parts.append(body_e)
    if suffix:
        parts.append(json.dumps(suffix))
    return " + ".join(parts)


def compose_axis_format(
    d: dict[str, Any],  # type-state: explicit_any — VL JSON dict boundary
    fmt: ResolvedFormat,
    quantitative: bool,
) -> None:
    """Set VL's native ``format``, or compose a ``labelExpr`` when the format
    carries an affix, a currency symbol or an SI notation override a d3 spec
    cannot hold. A zero tick stays bare, without the affix or the spec's own
    symbol, as on the ladder paths; the symbol only on a ``quantitative``
    scale, since a category's label is its row text.

    An authored ``labels.expr`` (already in ``d``) wins; ``format`` is still set
    because Vega's axis description reads it.
    """
    native = fmt.spec in VEGA_ARMS
    symbol = quantitative and has_symbol(fmt.spec)
    if "labelExpr" in d:
        d.update(vl_format_key(fmt.spec))
    elif fmt.prefix or fmt.suffix:
        affixed = _affixed_digits_expr("datum.value", fmt.spec, fmt, anchored=False)
        bare = numeral_vega_expr("datum.value", without_symbol(fmt.spec))
        d["labelExpr"] = f"(datum.value === 0 ? {bare} : {affixed})"
    elif native or symbol or (fmt.notation is not None and is_d3_si_spec(fmt.spec)):
        painted = numeral_vega_expr("datum.value", fmt.spec, fmt.notation)
        if symbol:
            bare = numeral_vega_expr("datum.value", without_symbol(fmt.spec))
            painted = f"(datum.value === 0 ? {bare} : {painted})"
            d["format"] = fmt.spec
        d["labelExpr"] = painted
    else:
        d["format"] = fmt.spec


def axis_to_vl(
    axis: Any,
    *,
    label_overlap: Literal["allow", "parity", "greedy"] | None = None,
    label_angle: float | None = None,
) -> dict[str, Any]:
    """Map any axis style object → VL axis config dict (non-None fields only).

    Works for any concrete axis style (all fields emitted) and any axis-variant
    patch (skip None). Single canonical mapper for all axis variants — theme
    global/channel/type-conditional and chart-local patches.

    ``label_overlap`` is the render-local VL directive from resolve_axis_x_overlap/
    resolve_axis_y_overlap;
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
    # label_overlap is the render-local VL directive from resolve_axis_{x,y}_overlap.
    # "allow" → false (no reduction; labels may overlap).
    # "parity"/"greedy" → pass through as-is (VL drops every other label /
    # drops whichever labels collide).
    # None → omit (VL applies its per-scale adaptive default).
    if label_overlap == "allow":
        d["labelOverlap"] = False
    elif label_overlap in ("parity", "greedy"):
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
    if (fmt := _n(axis, "labels", "format")) is not None:
        # A ladder composes the affix into its own labelExpr.
        if _n(axis, "ruler") is not None or _n(axis, "tick_label") is not None:
            d.update(vl_format_key(fmt.spec))
        else:
            compose_axis_format(d, fmt, _n(axis, "is_quantitative") is True)

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
    decimal = re.escape(active_format_locale().decimal)
    frac_e = f"replace({formatted_e}, /^[^{decimal}]*{decimal}?/, '')"
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


def legend_orient(position: ResolvedLegendPosition) -> str:
    """Map a resolved legend placement to a Vega-Lite ``legend.orient``.

    Vega-Lite's cardinals reserve a strip start-aligned to the plot; its corners
    float over the marks. A centered or end-aligned legend starts from the cardinal
    or corner on its edge and is slid along that edge afterwards (``render/
    converters/legend_align.py``), since Vega-Lite has no such orient. Resolve rejects
    every unsupported combination (``validate_cartesian_position``), so reaching
    one here is a bug.
    """
    edge, align = position.edge, position.align
    if not position.overlay:
        return edge
    if edge in ("top", "bottom"):
        assert align in ("start", "center", "end"), position
        return f"{edge}-{'right' if align == 'end' else 'left'}"
    assert align == "center", position
    return f"top-{edge}"


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
        d["orient"] = legend_orient(v)
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
