"""Axis style cascade — the ordered merge from ``charts.axis`` down to
chart-local overrides, and the ResolvedAxisStyle builder it feeds.

The chart-local cascade walk (``resolved_axis_style`` / ``_merge_axis_cascade``)
and the ``AxisXStyle``/``AxisYStyle`` → ``ResolvedAxisStyle`` builder
(``build_resolved_axis``) it calls at the end of the walk live in this one
module.
"""

from __future__ import annotations

import dataclasses
from collections.abc import Sequence
from types import EllipsisType
from typing import Literal, TypeVar, overload

from pydantic import BaseModel

from d3_format import format as _d3_format
from dbt_charts.core.compile.format import (
    check_anchored_sign,
    column_format,
    grain_format_specs,
    resolve_format_parts,
    tick_min_step_for_format,
)
from dbt_charts.core.compile.merge import merge_onto_base
from dbt_charts.core.compile.models.primitives import (
    AuthoredFormat,
    FormatTable,
    ResolvedFormat,
)
from dbt_charts.core.compile.models.style.authored import (
    AxisXStylePatch,
    AxisYStylePatch,
    BandAxisStylePatch,
    BaseAxisStylePatch,
    QuantitativeAxisStylePatch,
)
from dbt_charts.core.compile.models.style.context import ChartStyleContext
from dbt_charts.core.compile.models.style.resolved._base import (
    ResolvedAxisElementStyle,
    ResolvedAxisGridStyle,
    ResolvedAxisGridThresholdStyle,
    ResolvedAxisLabelOverlapConfig,
    ResolvedAxisLineStyle,
    ResolvedAxisStyle,
    ResolvedAxisTicksStyle,
    ResolvedMirrorAxis,
    ResolvedRulerAxis,
    ResolvedTickLabel,
)
from dbt_charts.core.compile.models.style.theme import (
    AxisLabelOverlapConfig,
    AxisMirrorStyle,
    AxisXStyle,
    AxisYStyle,
    BaseScaleStyle,
    DimensionLabelStyle,
    DimensionTicksStyle,
    XScaleStyle,
)
from dbt_charts.core.compile.resolve.style.board import resolve_cascaded_font
from dbt_charts.core.compile.resolve.style.inherit_resolver import settled, unset_autos
from dbt_charts.core.compile.resolve.style.scale import (
    _build_ruler,
    build_resolved_scale,
    ladder_anchor_at_start,
)
from dbt_charts.core.constants.axis import AUTO_TICK_TARGET
from dbt_charts.core.font_measure import compose_decimal_units
from dbt_charts.core.text.format_d3 import (
    is_d3_fixed_decimal_spec,
    is_d3_number_spec,
    is_d3_si_spec,
)
from dbt_charts.core.text.numeral_scale import (
    build_decimal_pad_table,
    non_compacting_tick_format,
    shared_scale_for_ladder,
    sub_unit_digit_format,
    sub_unit_scientific_format,
    with_symbol,
)
from dbt_charts.core.text.predefined_formats import (
    PREDEFINED_SUB_UNIT_FALLBACK,
)

# Fields on AxisLabelStyle (base) that are optional VL passthroughs.
# "overlap" is handled explicitly (AxisLabelOverlapConfig → ResolvedAxisLabelOverlapConfig).
# "align" is handled explicitly too (_resolve_own_side_align) — its authored
# type carries inward/outward, which must be mapped against the axis's final
# edge before it can land on the narrower resolved Literal["left","right"].
_LABEL_PASSTHROUGH = (
    "max_width",
    "angle",
    "min_gap",
    "visible",
    "expr",
    "bound",
    "flush",
    "offset",
)


# Additional passthrough fields only on DimensionLabelStyle (AxisXStyle.labels).
# Absent from AxisLabelStyle (used by AxisYStyle.labels and axis_quantitative/band).
_ELEM_X_LABEL_PASSTHROUGH = (
    "time_unit",
    "tilt_increments",
    "values",
    "clock",
)


# AxisTitleStyle is deliberately thin (font/padding/angle/align/visible only)
# — a title is one short static string, not a per-tick label stream, so it
# has none of the label-overlap/VL-passthrough machinery above.
_TITLE_PASSTHROUGH = ("padding", "angle", "visible")

AUTO_AXIS_FORMAT = "auto"


def decided_axis_format(channel_type: str) -> str | None:
    """What an axis's ``labels.format: auto`` means: the bounded SI ``number`` on a
    quantitative axis (9.18, 21.5M, never 9.18039), no format on any other, whose
    labels name themselves."""
    return "number" if channel_type == "quantitative" else None


def _resolve_own_side_align(
    align: Literal["left", "right", "center", "inward", "outward"] | None,
    edge: Literal["left", "right"] | None,
) -> Literal["left", "right", "center"] | None:
    """Map an authored align directive to a concrete side against a known edge.

    ``left``/``right``/``center`` pass through unchanged (absolute,
    edge-independent). ``inward`` resolves to the edge itself (own-side,
    toward the plot); ``outward`` resolves to the opposite side (away from
    the plot). With no left/right edge (``edge=None`` — a bottom/top axis,
    or the edge isn't known yet) inward/outward have no side to resolve
    against and collapse to ``None`` (the default alignment) — an
    inapplicable-but-reasonable authoring, not an error.
    """
    if align == "left" or align == "right" or align == "center" or align is None:
        return align
    if edge is None:
        return None
    if align == "inward":
        return edge
    return "left" if edge == "right" else "right"


def _effective_align(
    resolved_align: Literal["left", "right", "center"] | None,
    edge: Literal["left", "right"] | None,
) -> Literal["left", "right", "center"] | None:
    """Which side an axis's digits/text effectively align toward, folding
    in VL's own per-orient smart default when no align was resolved.

    ``resolved_align`` is ``_resolve_own_side_align``'s own output
    (inward/outward already mapped to a concrete side against ``edge``);
    None here means "no explicit align", which is VL's own away-from-plot
    default -- start-anchored (``"left"``) on a right-edge axis,
    end-anchored (``"right"``) on a left-edge one.
    """
    if resolved_align is not None:
        return resolved_align
    if edge == "right":
        return "left"
    if edge == "left":
        return "right"
    return None


def _render_align(
    label_align: Literal["left", "right", "center"] | None,
    edge: Literal["left", "right"] | None,
    fallback_fires: bool,
) -> Literal["left", "right", "center"] | None:
    """The align that actually reaches paint for ``edge``, after render's
    own-side/center safety net has had its say -- not just
    ``_effective_align``'s resolve-time fold.

    ``measure_axis_to_vl``'s ``_fallback_reversed_label_align`` deletes an
    own-side or ``"center"`` ``labelAlign`` outright when dbt charts can't
    safely measure a ``labelPadding`` for it (chiefly: ``label.font.case``
    is ``upper``/``lower``, which makes VL render an injected ``labelExpr``
    a d3-format measurement can't see -- or ``"center"``, which invades
    regardless of measurability). That decision runs exactly once, against
    the PRIMARY's own resolved align and edge, and its outcome is what both
    the primary paints AND what the mirror ghost inherits verbatim
    (``dict(primary_axis)``, ``MirrorAxisFeature`` -- it never re-runs the
    fallback for its own edge). ``fallback_fires`` is that one precomputed
    outcome; this function folds it into ``_effective_align`` for whichever
    edge is asking:

    - Fired: the align is gone by the time either axis paints, so BOTH the
      primary and the ghost fall through to VL's own per-orient smart
      default -- independently, since each axis object carries its own
      orient and VL computes each one's default at its own paint time. This
      is exactly ``_effective_align(None, edge)``, evaluated once per edge.
    - Not fired: ``label_align`` survives untouched and is what both the
      primary emits AND the ghost inherits as a literal string -- so this
      is exactly ``_effective_align(label_align, edge)``, which already
      returns a non-None ``label_align`` verbatim regardless of ``edge``
      (an away-side align surviving on the primary can still be own-side,
      unguarded, once copied onto the ghost's opposite edge -- that read is
      correct: nothing re-evaluates it there either).

    The mechanism ``_build_ruler`` bakes (digit field vs. reservation) has
    to be chosen from THIS, not ``_effective_align`` alone, or resolve
    bakes a mechanism for an anchoring render never actually paints.
    """
    return _effective_align(None if fallback_fires else label_align, edge)


def _label_align_fallback_fires(
    label_align: Literal["left", "right", "center"] | None,
    edge: Literal["left", "right"] | None,
    case: Literal["none", "sentence", "title", "upper", "lower", "slug", "camel"],
) -> bool:
    """Predicts ``measure_axis_to_vl``'s ``_fallback_reversed_label_align``
    outcome from resolve-time facts alone, for a ruler-bearing axis.

    That function deletes ``labelAlign`` when it is own-side (matches
    ``edge``) or ``"center"``, and dbt charts can't safely measure a
    ``labelPadding`` for it. A ruler-bearing axis (``_build_ruler``'s own
    precondition: SI-shaped ``format``, non-empty ``tick_values``, no
    authored ``label.expr``) always has exact tick content to measure from
    -- the one thing that can still block measurement is
    ``label.font.case`` being ``upper``/``lower`` (VL prefers the injected,
    un-measurable ``labelExpr`` over ``format`` then). ``"center"`` invades
    regardless of case, per that function's own docstring. Axes this
    predicate doesn't apply to (``edge`` not left/right -- an X-axis, or a
    horizontal bar's measure axis, which never reaches
    ``measure_axis_to_vl`` at all) always return False; ``_effective_align``
    never returns ``"center"`` and never equals ``edge`` for an unauthored
    align, so an unauthored align (``label_align is None``) also always
    returns False without a separate check.
    """
    if edge not in ("left", "right"):
        return False
    effective = _effective_align(label_align, edge)
    return effective == "center" or (effective == edge and case in ("upper", "lower"))


def _resolve_overlap_config(
    overlap: AxisLabelOverlapConfig | None,
) -> ResolvedAxisLabelOverlapConfig:
    overlap = settled(overlap)
    return ResolvedAxisLabelOverlapConfig(
        tilt=settled(overlap.tilt), skip=settled(overlap.skip)
    )


def _anchors_affix(fmt: ResolvedFormat) -> bool:
    return fmt.repeat == "anchor" and bool(fmt.prefix or fmt.suffix)


def target_tick_count(count: int | Literal["auto"] | None) -> int | None:
    """The tick count an axis aims for; ``auto`` is the engine's own target."""
    return AUTO_TICK_TARGET if count == "auto" else count


def ladderless_tick_label(
    fmt: ResolvedFormat, measure_values: Sequence[float]
) -> ResolvedTickLabel:
    """The tick label of an SI axis with no ladder: sub-1 values paint plain
    digits, the rest keep the SI spec. An anchored affix sits on the last tick
    when any value is positive, else the first (bottom), as
    ``ladder_anchor_at_start``."""
    anchor_at_start = bool(measure_values) and max(measure_values) <= 0
    return ResolvedTickLabel(
        format=sub_unit_digit_format(fmt.spec),
        anchor_at_start=anchor_at_start if _anchors_affix(fmt) else None,
        si_format=fmt.spec,
        scientific_format=sub_unit_scientific_format(fmt.spec),
    )


def affix_tick_label(
    fmt: ResolvedFormat, tick_values: tuple[float, ...]
) -> ResolvedTickLabel | None:
    """A ``repeat: anchor`` affix on an axis no ruler or house tick label
    paints; ``None`` keeps ``compose_axis_format``'s affix on every tick."""
    if not _anchors_affix(fmt):
        return None
    return ResolvedTickLabel(
        format=fmt.spec, anchor_at_start=ladder_anchor_at_start(tick_values)
    )


def _scale_names_ticks_or_is_log(scale: BaseScaleStyle | XScaleStyle | None) -> bool:
    """An authored ``scale.values`` ladder or a log scale owns its own ticks."""
    if scale is None:
        return False
    continuous = scale.continuous
    return scale.values is not None or (
        continuous is not None and continuous.type == "log"
    )


def build_resolved_axis(
    axis: AxisXStyle | AxisYStyle,
    *,
    band_position: float | None = None,
    edge: Literal["left", "right"] | None = None,
    tick_values: tuple[float, ...] = (),
    domain_max: float | EllipsisType = ...,
    domain_min: float | EllipsisType = ...,
    column_forming: bool = True,
    format: ResolvedFormat | None,
    is_quantitative: bool = False,
    quantitative_for_alignment: bool | None = None,
    zero_anchored: bool = False,
    chart_id: str,
    y_gridline_caps_bottom: bool | None = None,
    formats: FormatTable | None,
    floor_tick_step: bool = False,
    measure_values: Sequence[float] = (),
) -> ResolvedAxisStyle:
    """Build ResolvedAxisStyle from a channel-typed AxisXStyle or AxisYStyle.

    Accepts AxisXStyle or AxisYStyle (after resolved_axis_style's merge chain).
    band_position is passed explicitly since BandAxisStyle is structurally absent
    from AxisXStyle/AxisYStyle — the caller extracts it from BandAxisStylePatch.

    ``edge`` is the axis's final left/right placement, when known (None for a
    bottom/top axis, or when the caller hasn't determined the edge yet — see
    ``resolved_axis_style``'s docstring). It resolves ``labels.align``/
    ``title.align``'s inward/outward directives to concrete left/right (or
    None) via ``_resolve_own_side_align`` — the only place that mapping
    happens, so the render layer only ever sees left/right/None.

    ``column_forming`` says whether this axis's ticks stack into a column in
    the rendered chart — true for every vertical ruler (the default: every
    cartesian y-axis except a horizontal bar's measure axis, which renders on
    VL's x channel and passes ``column_forming=False``). It is folded into
    the baked ``ruler`` and ``tick_label`` decisions below (and nowhere else
    — render never re-declares it) and gates the tabular-font guarantee: only a
    column-forming axis reserves the suffix field (a padding string composed
    from measured space characters, ``font_measure.compose_suffix_reservation``),
    so only one is required to resolve a tabular label font.

    ``format`` is the axis's baked label format, carried whole onto
    ``labels.format``. ``format.is_house`` is the one house-vs-literal test;
    ``None`` (the board-level probe) is never house.

    ``floor_tick_step`` opts the measure axis (``AxisYStyle``) into deriving
    ``ticks.step`` from its fixed-decimal format when no ladder was baked
    (``tick_values`` empty): Vega-Lite picks the ticks and the step reaches it
    as ``tickMinStep``. Off unless the cartesian plan says the axis's format is
    the one that paints.

    ``is_quantitative`` is a channel-type fact: is this axis's field numeric
    rather than categorical. It is baked straight onto
    ``ResolvedAxisStyle.is_quantitative`` below and, absent an explicit
    ``quantitative_for_alignment``, also feeds the digit-alignment gate
    (``_force_right`` below). A caller with a genuinely different axis for
    each question — a horizontal bar's measure axis is always quantitative
    by channel type but does not render as the column-forming axis the
    alignment gate cares about — passes both explicitly instead of relying
    on the shared default.

    ``quantitative_for_alignment`` overrides ``is_quantitative`` for the
    digit-alignment gate only, when the two questions diverge (see above).
    ``None`` (the default) reuses ``is_quantitative`` itself — the two facts
    coincide for every axis except a horizontal bar's measure axis, so only
    that caller needs to pass this explicitly.

    The ``ruler`` decision folds three resolve-time facts into one: does the
    tick ladder compact (``shared_scale_for_ladder``,
    ``core/text/numeral_scale.py``), is the format SI-shaped (an author who
    chose a plain format opted out of magnitude abbreviation), and is
    ``label.expr`` unauthored (the documented full opt-out). All three used
    to be re-checked independently by paint, by the gutter measurement, and
    by the mirror ghost, and disagreed. Now every consumer reads
    ``ResolvedAxisStyle.ruler`` alone.

    ``tick_label.format`` is ``ruler``'s non-compacting sibling — see its
    field docstring on ``ResolvedAxisStyle``. It is a plain d3 fixed-point
    spec, not a Vega expression: a non-compacting ladder makes no per-tick
    decision (no suffix, no anchor position), so unlike ``ruler`` it needs
    no expression, only a format string. That string is baked here and read
    only by this axis's own tick-label emission — it is never written to
    ``label.format`` (shared with value labels, tooltips, and the
    currency-warning detector) or ``label.expr`` (a sentinel several other
    consumers read as "the author opted out").

    A ladder-LESS axis (``tick_values`` empty — an unset
    ``ticks.count``, or ``multiples.scale: independent``) has no step
    for ``tick_label.format`` to derive a precision from, so it bakes
    ``tick_label.si_format``/``.scientific_format`` alongside it instead: the
    axis's own SI spec, untouched, and the scientific register for a tick too
    small for significant digits to reach cleanly. ``inject_axis_numeral_expr``
    reads the three as a per-tick guard rather than one fixed spec — see its
    own docstring for the full mechanism, including why a log-scale axis
    (which reaches this same empty-``tick_values`` branch for an unrelated
    reason) is excluded below rather than folded into it.

    ``y_gridline_caps_bottom`` resolves an ``axis_x.ticks.visible: "auto"``
    (the base theme's default) into a concrete bool: True when this axis's
    own gridlines are hidden (nothing else marks position) or the caller
    says the y-axis's lowest gridline lands on the plot's bottom edge;
    False when the caller says it doesn't (gridlines already reach the
    labels). Only ``build_cartesian_axes`` — the one place both axes of a
    cartesian pair are known — computes and passes a real bool; every other
    caller (the support_table strip's solo ``axis_x`` resolution, or a
    theme-defaults probe with no board to pair it against) leaves this None,
    which keeps the tick showing — the same answer this axis always had
    before ``"auto"`` existed, for a context that structurally can't ask the
    real geometric question.

    ``formats`` resolves an authored ``axis_y.mirror.format``.

    ``measure_values`` are the finite values the axis plots, read where no
    ladder says which end an anchored affix belongs on; empty when unknown.
    """

    # threshold lives on every axis's grid.threshold now — a threshold rule is
    # a property of a quantitative axis, on either channel, not of the
    # measure (y) axis specifically. Built unconditionally; it is simply
    # inert wherever the axis never ticks at the threshold.
    _threshold = settled(axis.grid.threshold)
    grid_threshold = ResolvedAxisGridThresholdStyle(
        color=settled(_threshold.color),
        width=settled(_threshold.width),
        visible=settled(_threshold.visible),
    )

    # label passthrough: x-axis adds tilt_increments/values/time_unit/clock from DimensionLabelStyle.
    label_kwargs = {f: getattr(axis.labels, f) for f in _LABEL_PASSTHROUGH}
    if isinstance(axis.labels, DimensionLabelStyle):
        label_kwargs.update(
            {f: getattr(axis.labels, f) for f in _ELEM_X_LABEL_PASSTHROUGH}
        )

    # Right-edge quantitative axes decimal-align: a house-rule format alias
    # (e.g. "percent", "currency") forces label.align = "right" over whatever
    # the author set, and a literal d3 number spec gets it unless the author
    # set an align (an explicit override).  Left-edge axes already get "right" from
    # VL's own default.  Bottom/top axes (edge=None, e.g. horizontal bar
    # measure) are excluded.
    # Guard: forced end-anchoring triggers the trailing-reservation device
    # (_build_ruler's reserve path) which requires a tabular font.  Boards
    # with a non-tabular axis label font keep the old start-anchored behavior
    # to avoid ERR-AXIS-COLUMN-REQUIRES-TABULAR-FONT on previously-working
    # configs.  non-column-forming axes (horizontal bar measure) are exempt:
    # their reserve is always False regardless of start_anchored.
    _resolved_font = resolve_cascaded_font(axis.labels.font)
    format_is_house = format is not None and format.is_house
    _quantitative_for_alignment = (
        is_quantitative
        if quantitative_for_alignment is None
        else quantitative_for_alignment
    )
    # A literal spec must be d3 number grammar: a strftime or unknown spec on a
    # numeric axis cannot be measured for the gutter, so it stays unaligned.
    _literal_number_spec = format is not None and is_d3_number_spec(format.spec)
    _force_right = (
        _quantitative_for_alignment
        and (format_is_house or (axis.labels.align is None and _literal_number_spec))
        and edge == "right"
        and (not column_forming or _resolved_font.tabular_figures)
    )
    # _resolve_own_side_align("right", ...) always returns "right" (absolute values
    # pass through unchanged); typed as Literal["left","right","center"] | None
    # so pyright accepts it without a local `| None` annotation, which the counter's
    # `optional` category would still count (report-only, not gate-blocking, but
    # avoided here for a clean return type).
    resolved_label_align = (
        _resolve_own_side_align("right", edge)
        if _force_right
        else _resolve_own_side_align(axis.labels.align, edge)
    )

    # Which mechanism aligns this axis's digits is a function of the
    # resolved *anchoring* (text-anchor:start vs :end), never of which
    # physical edge the axis happens to sit on: start-anchored digits grow
    # away from the tick, where the trailing reservation cannot help them
    # (padding the far side never moves the digits' shared edge), so they
    # take the measured digit field instead; end-anchored digits already
    # share a right edge on their own, where the reservation aligns the
    # *suffix* field on top of that. "center" gets neither device (no
    # single-sided padding aligns a centered label, the same exclusion
    # `_fallback_reversed_label_align` applies for gutter measurement).
    #
    # The anchoring fed into `start_anchored` must be the one that actually
    # reaches paint, not `_effective_align`'s resolve-time fold alone --
    # render's own safety net (`_fallback_reversed_label_align`) can still
    # delete an own-side/center `labelAlign` afterward. `_render_align`
    # folds that predicted outcome in; see its own docstring.
    fallback_fires = _label_align_fallback_fires(
        resolved_label_align, edge, _resolved_font.case
    )
    start_anchored = _render_align(resolved_label_align, edge, fallback_fires) == "left"
    # One SI-shape verdict, computed here and handed to both _build_ruler calls
    # and the tick_label branch below, so none of them re-derives it. The axis's
    # resolved format when SI-shaped, else None. A raw d3 spec that was only
    # right-aligned by _force_right (no house alias, no authored align) renders
    # literal d3 per tick -- every suffix stacks and the digits line up -- so
    # it never reaches the anchored-magnitude ruler.
    _literal_forced_right = _force_right and not format_is_house
    _si_format = (
        format.spec
        if format is not None
        and is_d3_si_spec(format.spec)
        and not _literal_forced_right
        else None
    )
    # A decimal-carrying fixed-point house format (percent, currency_full, ...)
    # never compacts, so the ruler never reaches it; the tick_label branch
    # below derives its tick precision from the ladder step instead of
    # painting the preset's data precision (10.0%). A raw d3 spec stays
    # literal.
    _fixed_decimal_format = (
        format.spec
        if format_is_house
        and format is not None
        and is_d3_fixed_decimal_spec(format.spec)
        else None
    )
    # Vega draws only the rungs inside the baked domain (the top one is often
    # dropped), so the anchor end is chosen among those.
    drawn_ticks = tuple(
        t
        for t in tick_values
        if (domain_min is ... or t >= domain_min)
        and (domain_max is ... or t <= domain_max)
    )
    anchor_at_start = ladder_anchor_at_start(drawn_ticks)
    # An unbaked top edge leaves the drawn rungs unknown; the ruler anchors on
    # the magnitude extreme instead.
    ruler_anchor_at_start = (
        anchor_at_start
        if domain_max is not ...
        else bool(drawn_ticks) and drawn_ticks.index(max(drawn_ticks, key=abs)) == 0
    )
    ruler = _build_ruler(
        tick_values=tick_values,
        si_format=_si_format,
        label_expr=axis.labels.expr,
        column_forming=column_forming,
        start_anchored=start_anchored,
        font_family=_resolved_font.family,
        font_tabular=_resolved_font.tabular_figures,
        chart_id=chart_id,
        anchor_at_start=ruler_anchor_at_start,
        notation=format.notation if format is not None else None,
        repeat=format.repeat if format is not None else None,
    )

    # tick_label is ruler's non-compacting sibling: an SI ladder that does not
    # compact, or a decimal-carrying fixed-point house format, is rewritten by
    # `non_compacting_tick_format` unless the format is not house or the ladder
    # has no step to derive. `ruler is None` alone is not enough: other causes
    # are re-checked here.
    #
    # There is no magnitude floor on the ladder: nothing chooses SI below
    # thousands (`shared_scale_for_ladder` declines), so a sub-1 ladder left
    # out of this branch would paint from the theme's placeholder spec, whose
    # d3 sub-unit prefixes read as the house magnitude suffixes -- 0.3 as
    # "300m", milli misread as million.
    raw_scale = shared_scale_for_ladder(list(tick_values)) if tick_values else None
    step_format = (
        _fixed_decimal_format
        if _fixed_decimal_format is not None
        else _si_format
        if raw_scale is None
        else None
    )
    tick_label: ResolvedTickLabel | None = None
    if (
        step_format is not None
        and format_is_house
        and len(tick_values) >= 2
        # `nice_tick_values` rounds each rung to 10 places, so a near-degenerate
        # span (1.0 to 1.0000000001) can hand back a ladder whose adjacent rungs
        # are equal: two ticks, no step. Same exit as a ladder too short to
        # carry one.
        and tick_values[0] != tick_values[1]
        and axis.labels.expr is None
    ):
        step = abs(tick_values[1] - tick_values[0])
        # Prefix-split still applies when there's a currency symbol.
        # Integer place-value alignment is automatic under text-anchor=end;
        # fractional/decimal tail alignment is not -- decimal_pad_table
        # (baked below when column_forming) compensates for that.
        # A None precision is the scientific register: no fixed decimal
        # position, so no pad table.
        prefix, digit_spec, precision = non_compacting_tick_format(step_format, step)
        anchor_at_start_plain = None
        assert format is not None  # format_is_house implies a format
        # An authored affix anchors like currency's native "$"; None is the
        # repeat signal, the default where no column forms.
        anchored = (
            column_forming if format.repeat is None else format.repeat == "anchor"
        )
        if (prefix or format.prefix or format.suffix) and anchored:
            anchor_at_start_plain = anchor_at_start
        if (
            precision is not None
            and column_forming
            and not start_anchored
            and _resolved_font.tabular_figures
        ):
            # Only pad when ticks have mixed fractional depth; uniform depth
            # means every label already aligns (no fix needed).
            # start_anchored skipped: text-anchor:start ignores trailing pads.
            frac_depths = {
                len(_d3_format(digit_spec, v).partition(".")[2]) for v in tick_values
            }
            if len(frac_depths) > 1:
                digit_unit, dot_unit = compose_decimal_units(_resolved_font.family)
                tick_label_pad_table: tuple[str, ...] = build_decimal_pad_table(
                    precision, digit_unit, dot_unit
                )
            else:
                tick_label_pad_table = ()
        else:
            tick_label_pad_table = ()
        tick_label = ResolvedTickLabel(
            format=digit_spec,
            prefix=prefix,
            anchor_at_start=anchor_at_start_plain,
            decimal_pad_table=tick_label_pad_table,
        )
    elif (
        not tick_values
        and format_is_house
        and _si_format is not None
        and axis.labels.expr is None
        # Excludes a log-scale axis, which also reaches here with empty
        # tick_values for its own, unrelated reason -- see the docstring
        # above and `inject_axis_numeral_expr`'s own for why.
        and not (
            axis.scale is not None
            and axis.scale.continuous is not None
            and axis.scale.continuous.type == "log"
        )
    ):
        # See the docstring above for the mechanism; `inject_axis_numeral_expr`
        # is the authoritative account of how the three specs below compose.
        assert format is not None  # _si_format implies a format
        tick_label = ladderless_tick_label(format, measure_values)
    elif ruler is None and format is not None and axis.labels.expr is None:
        tick_label = affix_tick_label(format, drawn_ticks)

    # The mirrored edge anchors independently of the primary (labels.align
    # resolves per edge), so it gets its own _build_ruler call. An authored
    # mirror.format/.expr replaces the ghost's paint: no ruler then.
    mirror: ResolvedMirrorAxis | None = None
    if isinstance(axis, AxisYStyle) and axis.mirror:
        authored_mirror = (
            axis.mirror if isinstance(axis.mirror, AxisMirrorStyle) else None
        )
        mirror_format: ResolvedFormat | None = None
        mirror_ruler: ResolvedRulerAxis | None = None
        mirror_tick_label: ResolvedTickLabel | None = None
        if authored_mirror is not None and authored_mirror.format is not None:
            # One shared scale: the mirror paints the primary's digits.
            mirror_format = column_format("number", formats, authored_mirror.format)
            if not tick_values and mirror_format.raw in PREDEFINED_SUB_UNIT_FALLBACK:
                mirror_tick_label = ladderless_tick_label(mirror_format, measure_values)
            elif (
                mirror_format.raw in PREDEFINED_SUB_UNIT_FALLBACK
                and raw_scale is None
                and len(tick_values) >= 2
                and tick_values[0] != tick_values[1]
            ):
                mirror_symbol, mirror_digits, _ = non_compacting_tick_format(
                    mirror_format.spec, abs(tick_values[1] - tick_values[0])
                )
                mirror_format = resolve_format_parts(
                    mirror_format,
                    formats,
                    spec=with_symbol(mirror_digits, mirror_symbol),
                )
            else:
                mirror_format = column_format(
                    "number", formats, authored_mirror.format, values=tick_values
                )
            if mirror_tick_label is None:
                mirror_tick_label = affix_tick_label(mirror_format, drawn_ticks)
            if (
                mirror_tick_label is not None
                and mirror_tick_label.anchor_at_start is not None
            ):
                check_anchored_sign(
                    mirror_format, f"charts.{chart_id}.style.axis_y.mirror.format"
                )
        elif (
            (authored_mirror is None or authored_mirror.expr is None)
            and ruler is not None
            and edge in ("left", "right")
        ):
            opposite_edge: Literal["left", "right"] = (
                "left" if edge == "right" else "right"
            )
            # MirrorAxisFeature copies the primary's `labelAlign` onto the ghost.
            mirror_ruler = _build_ruler(
                tick_values=tick_values,
                si_format=_si_format,
                label_expr=axis.labels.expr,
                column_forming=column_forming,
                start_anchored=_render_align(
                    resolved_label_align, opposite_edge, fallback_fires
                )
                == "left",
                font_family=_resolved_font.family,
                font_tabular=_resolved_font.tabular_figures,
                chart_id=chart_id,
                anchor_at_start=ruler_anchor_at_start,
                notation=format.notation if format is not None else None,
                repeat=format.repeat if format is not None else None,
            )
        mirror = ResolvedMirrorAxis(
            format=mirror_format,
            expr=authored_mirror.expr if authored_mirror is not None else None,
            ruler=mirror_ruler,
            tick_label=mirror_tick_label,
        )

    # Where we bake no ladder (tick_values empty) Vega-Lite picks the ticks
    # itself; the measure axis's fixed-decimal format floors its step via a
    # resolved tickMinStep. Never set alongside a baked ladder. axis_y never
    # carries an authored step (ERR_TICKS_INTERVAL_MEASURE_AXIS).
    tick_step: int | float | None = axis.ticks.step
    if (
        tick_step is None
        and isinstance(axis, AxisYStyle)
        and floor_tick_step
        and is_quantitative
        and not tick_values
        and format is not None
        and not _scale_names_ticks_or_is_log(axis.scale)
    ):
        tick_step = tick_min_step_for_format(format.spec)

    ticks_visible = axis.ticks.visible
    # "auto" is only declared on axis_x's own tick slot (DimensionTicksStyle).
    if ticks_visible == "auto":
        if axis.grid.visible is not True:
            ticks_visible = True  # no gridlines at all -- nothing else marks position
        elif y_gridline_caps_bottom is None:
            ticks_visible = True  # no cross-axis geometry available -- keep the tick
        else:
            ticks_visible = y_gridline_caps_bottom

    axis_key = "axis_y" if isinstance(axis, AxisYStyle) else "axis_x"
    anchors_affix = (ruler is not None and not ruler.prefix_repeats) or (
        tick_label is not None and tick_label.anchor_at_start is not None
    )
    if format is not None and anchors_affix:
        check_anchored_sign(format, f"charts.{chart_id}.style.{axis_key}.labels.format")
    label = ResolvedAxisElementStyle(
        font=_resolved_font,
        padding=settled(axis.labels.padding),
        align=resolved_label_align,
        overlap=_resolve_overlap_config(axis.labels.overlap),
        format=format,
        **label_kwargs,
    )
    return ResolvedAxisStyle(
        grid=ResolvedAxisGridStyle(
            visible=settled(axis.grid.visible),
            opacity=settled(axis.grid.opacity),
            width=settled(axis.grid.width),
            color=settled(axis.grid.color),
            dash=axis.grid.dash,
            threshold=grid_threshold,
        ),
        line=ResolvedAxisLineStyle(
            visible=settled(axis.line.visible),
            width=settled(axis.line.width),
            color=settled(axis.line.color),
        ),
        ticks=ResolvedAxisTicksStyle(
            visible=settled(ticks_visible),
            color=settled(axis.ticks.color),
            width=axis.ticks.width,
            length=axis.ticks.length,
            offset=axis.ticks.offset,
            count=target_tick_count(axis.ticks.count),
            step=tick_step,
            time_unit=(
                axis.ticks.time_unit
                if isinstance(axis.ticks, DimensionTicksStyle)
                else None
            ),
        ),
        labels=label,
        title=ResolvedAxisElementStyle(
            font=resolve_cascaded_font(axis.title.font),
            align=_resolve_own_side_align(axis.title.align, edge),
            **{f: getattr(axis.title, f) for f in _TITLE_PASSTHROUGH},
        ),
        position=axis.position,
        mirror=mirror,
        band_position=band_position,
        scale=build_resolved_scale(axis.scale),
        fill=axis.fill if isinstance(axis, AxisXStyle) else None,
        # "auto" is authoring sugar for "not authored — auto-detect"; collapse
        # it to None here so render's "authored_time_unit is None" branch
        # decides whether to detect. A resolved value is either a real
        # detected/authored grain or the "no bucketing" sentinel "none" —
        # never the literal string "auto". This is not the only place "auto"
        # is read, though: compile/resolve/chart/bar.py computes bar orientation
        # from the pre-collapse merged axis's time_unit, so "auto" (and
        # "none") still steer orientation there as if they were a real
        # grain — a separate, pre-existing gap this change does not close.
        time_unit=(
            (None if axis.time_unit == "auto" else axis.time_unit)
            if isinstance(axis, AxisXStyle)
            else None
        ),
        type=axis.type if isinstance(axis, AxisXStyle) else None,
        # fiscal_year_start_month is x-only (year/yearquarter bucket
        # anchoring); non-x axes get the neutral calendar-convention default
        # (1 = January) since the resolved field stays a required int.
        fiscal_year_start_month=(
            axis.fiscal_year_start_month if isinstance(axis, AxisXStyle) else 1
        ),
        tick_values=tick_values,
        drawn_tick_values=drawn_ticks,
        ruler=ruler,
        tick_label=tick_label,
        domain_max=None if domain_max is ... else domain_max,
        domain_min=None if domain_min is ... else domain_min,
        is_quantitative=is_quantitative,
        zero_anchored=zero_anchored,
        grain_formats=grain_format_specs(formats),
    )


P = TypeVar("P", bound=BaseModel)


@dataclasses.dataclass(frozen=True)
class AxisOverrides:
    """Chart-local axis override patches: the last three layers of the cascade.

    Used by the resolve path to pass chart-local axis patches explicitly
    to resolved_axis_style without requiring a per-chart ChartStyleContext.
    v1 continues to read these from effective.axis_overrides_* as before.
    """

    global_: BaseAxisStylePatch | None = None
    x: AxisXStylePatch | None = None
    y: AxisYStylePatch | None = None
    quantitative: QuantitativeAxisStylePatch | None = None
    band: BandAxisStylePatch | None = None


def _full_model_as_overlay(model: BaseModel, patch_cls: type[P]) -> P:
    """Re-type a fully-populated theme model as a ``*Patch`` overlay.

    ``merge_onto_base``'s presence contract is ``model_fields_set``-based: a
    field the patch explicitly sets to ``None`` clears the base, it does not
    inherit. ``effective.axis_x``/``axis_y``/``axis_quantitative``/``axis_band`` are full
    theme models (every field populated by ``model_validate`` at cascade
    time, so every field is "set") where an unauthored field is simply
    ``None`` and must still inherit from the layer below — the old contract
    these axis-cascade steps were written against. Round-tripping through
    ``exclude_none=True`` recovers that: only genuinely non-None fields (at
    every nesting level) survive into the dump, so the rebuilt Patch's
    ``model_fields_set`` reflects "authored", not "constructed".
    """
    return patch_cls.model_validate(model.model_dump(exclude_none=True))


_BAND_CHANNEL_TYPES = ("ordinal", "nominal", "band")


def _family_axis_slot(
    chart_style_context: ChartStyleContext, chart_type: str, slot: str
) -> BaseModel | None:
    """The chart family's own ``slot`` patch, None when it sets none. A family
    carries only the slots it can paint: chart_type is "" when no family applies,
    and heatmap has no ``axis_quantitative``."""
    family = getattr(
        chart_style_context, chart_type, None
    )  # type-state: silent_fallback — see docstring
    return getattr(family, slot, None)  # type-state: silent_fallback — see docstring


def _without_band_position(patch: BaseModel | None) -> BaseModel | None:
    """``patch`` minus ``band_position``, which only ``BandAxisStylePatch`` carries
    and no merged axis has a field for (it is returned beside the axis instead)."""
    if not isinstance(patch, BandAxisStylePatch):
        return patch
    return BandAxisStylePatch.model_validate(
        patch.model_dump(exclude={"band_position"}, exclude_unset=True)
    )


def _axis_layers(
    chart_style_context: ChartStyleContext,
    axis_name: Literal["axis_x", "axis_y"],
    channel_type: str,
    chart_type: str,
    column_layer_format: AuthoredFormat | None,
    local: AxisOverrides,
    temporal_band: bool,
) -> list[BaseModel | None]:
    """The cascade's layers above ``charts.axis``, lowest first.

    Specificity decides, whichever file set a path: the channel slot beats the
    general one, the type slot beats the channel slot, a chart family beats the
    whole ``charts`` tier, and the chart's own style beats both. The axis
    column's declared format sits between the family and the chart.

    ``temporal_band`` marks a band-typed axis whose column is really a date (a
    heatmap's nominal month axis): it skips ``charts.axis_band`` so time stays on
    the muted ``charts.axis`` voice; a family's or chart's own ``axis_band`` still
    styles it.
    """
    channel_patch_cls = AxisXStylePatch if axis_name == "axis_x" else AxisYStylePatch
    channel_axis = getattr(chart_style_context, axis_name)
    type_slot = (
        "axis_quantitative"
        if channel_type == "quantitative"
        else "axis_band"
        if channel_type in _BAND_CHANNEL_TYPES
        else None
    )

    def family_slot(slot: str | None) -> BaseModel | None:
        if slot is None:
            return None
        return _without_band_position(
            _family_axis_slot(chart_style_context, chart_type, slot)
        )

    local_type = (
        local.quantitative
        if type_slot == "axis_quantitative"
        else _without_band_position(local.band)
    )
    return [
        _full_model_as_overlay(channel_axis, channel_patch_cls),
        _full_model_as_overlay(
            chart_style_context.axis_quantitative, QuantitativeAxisStylePatch
        )
        if type_slot == "axis_quantitative"
        else _without_band_position(
            _full_model_as_overlay(chart_style_context.axis_band, BandAxisStylePatch)
        )
        if type_slot == "axis_band" and not temporal_band
        else None,
        family_slot("axis"),
        family_slot(axis_name),
        family_slot(type_slot),
        None
        if column_layer_format is None
        else BaseAxisStylePatch.model_validate(
            {"labels": {"format": column_layer_format}}
        ),
        local.global_,
        local.x if axis_name == "axis_x" else local.y,
        local_type if type_slot is not None else None,
    ]


@overload
def _merge_axis_cascade(
    chart_style_context: ChartStyleContext,
    axis_name: Literal["axis_x"],
    channel_type: str,
    *,
    column_layer_format: AuthoredFormat | None = None,
    axis_overrides: AxisOverrides | None = None,
    chart_type: str,
    labeled: bool,
    temporal_band: bool = False,
) -> tuple[AxisXStyle, float | None]: ...


@overload
def _merge_axis_cascade(
    chart_style_context: ChartStyleContext,
    axis_name: Literal["axis_y"],
    channel_type: str,
    *,
    column_layer_format: AuthoredFormat | None = None,
    axis_overrides: AxisOverrides | None = None,
    chart_type: str,
    labeled: bool,
    temporal_band: bool = False,
) -> tuple[AxisYStyle, float | None]: ...


def _merge_axis_cascade(
    chart_style_context: ChartStyleContext,
    axis_name: Literal["axis_x", "axis_y"],
    channel_type: str,
    *,
    column_layer_format: AuthoredFormat | None = None,
    axis_overrides: AxisOverrides | None = None,
    chart_type: str,
    labeled: bool,
    temporal_band: bool = False,
) -> tuple[AxisXStyle | AxisYStyle, float | None]:
    """Walk the axis cascade and return the merged, channel-typed axis, plus any
    band_position the chart's own band patch sets.

    ``base.labels.format`` on the returned axis is the *raw*, pre-resolution
    format (a predefined name like ``"number"``, a ``style.formats`` alias key,
    or a literal d3 spec), whichever layer wrote it last. ``auto`` is the one
    value decided here, once, from ``channel_type``. See
    ``ResolvedAxisStyle.format_raw`` for what that raw winner decides.

    Seeds a channel-typed base (``AxisXStyle``/``AxisYStyle``) from the
    channel-agnostic ``charts.axis`` (``BaseAxisStyle``, a raw passthrough)
    so channel-specific fields (fill, mirror, tilt_increments, ...) survive the
    merge and reach ``build_resolved_axis``. ``fill``/``fiscal_year_start_month``
    are required on ``AxisXStyle``, so they are seeded from the theme-level
    ``axis_x`` (always populated) before any merge runs.

    ``band_position`` lives only on ``BandAxisStyle``/``BandAxisStylePatch``,
    structurally absent from ``AxisXStyle``/``AxisYStyle``, so it cannot
    survive the merge. It is read off the chart-local band patch and returned
    alongside the merged axis.

    ``labeled`` is the caller's own ``bool(chart.x_label)``/``bool(chart.y_label)``
    for the axis this call is merging: truthy, not ``is not None``, so a blank
    label shows no title and reserves no space for one. Every caller must spell
    it the same way or the emitted title and the space budgeted for it drift
    apart. It resolves a cascaded ``title.visible: auto`` to a bool once,
    after the last layer; a concrete ``true``/``false`` from any tier is never
    touched.

    ``temporal_band`` is ``_axis_layers``'s flag for a band-typed axis whose
    column is a date.

    Chart-local patches come from ``axis_overrides`` when given, else from the
    context's ``axis_overrides_*``.
    """
    axis_dict = chart_style_context.axis.model_dump()
    if axis_name == "axis_x":
        axis_dict["fill"] = chart_style_context.axis_x.fill
        axis_dict["fiscal_year_start_month"] = (
            chart_style_context.axis_x.fiscal_year_start_month
        )
        base: AxisXStyle | AxisYStyle = AxisXStyle.model_validate(axis_dict)
    else:
        base = AxisYStyle.model_validate(axis_dict)

    local = axis_overrides or AxisOverrides(
        global_=chart_style_context.axis_overrides_global,
        x=chart_style_context.axis_overrides_x,
        y=chart_style_context.axis_overrides_y,
        quantitative=chart_style_context.axis_overrides_quantitative,
        band=chart_style_context.axis_overrides_band,
    )
    for layer in _axis_layers(
        chart_style_context,
        axis_name,
        channel_type,
        chart_type,
        column_layer_format,
        local,
        temporal_band,
    ):
        base = merge_onto_base(base, layer)

    if base.title.visible == "auto":
        base = merge_onto_base(
            base, BaseAxisStylePatch.model_validate({"title": {"visible": labeled}})
        )

    if base.labels.format == AUTO_AXIS_FORMAT:
        base = merge_onto_base(
            base,
            BaseAxisStylePatch.model_validate(
                {"labels": {"format": decided_axis_format(channel_type)}}
            ),
        )

    band_position = None
    if channel_type in _BAND_CHANNEL_TYPES:
        family_band = _family_axis_slot(chart_style_context, chart_type, "axis_band")
        charts_band = (
            None
            if temporal_band
            else _full_model_as_overlay(
                chart_style_context.axis_band, BandAxisStylePatch
            )
        )
        for band_patch in (local.band, family_band, charts_band):
            if isinstance(band_patch, BandAxisStylePatch) and (
                band_patch.band_position is not None
            ):
                band_position = band_patch.band_position
                break
    return unset_autos(base), band_position


def resolved_axis_style(
    chart_style_context: ChartStyleContext,
    axis_name: Literal["axis_x", "axis_y"],
    channel_type: str,
    *,
    column_layer_format: AuthoredFormat | None = None,
    axis_overrides: AxisOverrides | None = None,
    chart_type: str,
    labeled: bool,
) -> ResolvedAxisStyle:
    """Walk the axis cascade and return a merged ResolvedAxisStyle.

    Single source of truth for axis state at emit time. The renderer reads
    every axis field (ticks, grid, domain, position, offset, ...) from this
    return value, never from ``chart_style_context.axis_x`` / ``axis_y``
    directly, since those carry only the ``charts`` tier and silently miss
    family and chart-local overrides.

    Layer order (each overrides the previous; ``T`` is ``axis_quantitative`` on
    a quantitative channel, ``axis_band`` on a band channel, no slot otherwise):
      1.  ``charts.axis``
      2.  ``charts.axis_x`` / ``axis_y``
      3.  ``charts.axis_quantitative`` / ``charts.axis_band``, whichever is ``T``
      4.  ``charts.<chart_type>.axis``
      5.  ``charts.<chart_type>.axis_x`` / ``axis_y``
      6.  ``charts.<chart_type>.T``
      7.  the axis column's declared format (``column_layer_format``)
      8.  the chart's own ``axis``
      9.  the chart's own ``axis_x`` / ``axis_y``
      10. the chart's own ``T``

    A path's tier is whichever files set it: a board's ``charts.axis_y`` merges
    into the theme's slot and loses to the theme's more specific
    ``charts.bar.axis_y``. After layer 10, a cascaded ``title.visible: auto``
    becomes ``labeled`` and ``labels.format: auto`` becomes the channel's own
    default.

    ``axis_x``/``axis_y``/``axis_quantitative`` are authored-only overlays
    (SkipInheritSlots: apply_inherit never fills their unset leaves from the
    shared ``axis``), so an unset leaf never masquerades as an override of a
    sibling slot's value.

    The chart's own patches (layers 8-10) are read from ``axis_overrides`` when
    given, else from the context's ``axis_overrides_*``, so the caller needs no
    per-chart ChartStyleContext.

    Returns the axis with ``label.align``/``title.align`` resolved with no
    known left/right edge (``inward``/``outward`` collapse to ``None``, the
    away-from-plot default). Callers that need own-side align resolved
    against a real edge (e.g. the cartesian resolvers, once orientation/
    position is known) call ``_merge_axis_cascade`` + ``build_resolved_axis``
    directly instead of this wrapper.
    """
    base, band_position = _merge_axis_cascade(
        chart_style_context,
        axis_name,
        channel_type,
        column_layer_format=column_layer_format,
        axis_overrides=axis_overrides,
        chart_type=chart_type,
        labeled=labeled,
    )
    # No ladder is baked here, so chart_id (only used by the tabular-guarantee
    # error) is "". Only axis geometry is read off this axis.
    return build_resolved_axis(
        base,
        band_position=band_position,
        format=(
            resolve_format_parts(
                base.labels.format,
                chart_style_context.formats,
            )
            if base.labels.format is not None
            else None
        ),
        is_quantitative=channel_type == "quantitative",
        chart_id="",
        # This wrapper resolves one axis in isolation, with no paired y-axis
        # to ask the geometric question against.
        y_gridline_caps_bottom=None,
        formats=chart_style_context.formats,
    )
