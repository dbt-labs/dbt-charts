"""Axis style cascade — the 13-layer merge from theme defaults down to
chart-local overrides, and the ResolvedAxisStyle builder it feeds.

The chart-local cascade walk (``resolved_axis_style`` / ``_merge_axis_cascade``)
and the ``AxisXStyle``/``AxisYStyle`` → ``ResolvedAxisStyle`` builder
(``build_resolved_axis``) it calls at the end of the walk live in this one
module.
"""

from __future__ import annotations

import dataclasses
from types import EllipsisType
from typing import Any, Literal, TypeVar, overload

from pydantic import BaseModel

from d3_format import format as _d3_format
from dbt_charts.core.compile.format import resolve_format
from dbt_charts.core.compile.merge import merge_onto_base
from dbt_charts.core.compile.models.primitives import FormatConfig
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
    ResolvedRulerAxis,
    ResolvedTickLabel,
)
from dbt_charts.core.compile.models.style.theme import (
    AxisLabelOverlapConfig,
    AxisMirrorStyle,
    AxisXStyle,
    AxisYStyle,
    DimensionLabelStyle,
    DimensionTicksStyle,
)
from dbt_charts.core.compile.resolve.style.board import resolve_cascaded_font
from dbt_charts.core.compile.resolve.style.scale import (
    _build_ruler,
    build_resolved_scale,
)
from dbt_charts.core.font_measure import compose_decimal_units
from dbt_charts.core.text.format_d3 import is_d3_si_spec
from dbt_charts.core.text.numeral_scale import (
    build_decimal_pad_table,
    non_compacting_tick_format,
    shared_scale_for_ladder,
    sub_unit_digit_format,
    sub_unit_scientific_format,
)
from dbt_charts.core.text.predefined_formats import ALL_PREDEFINED_NAMES

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
    "format",
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

# One board-override slot: (patch class, raw model_dump value re-validated below).
_BoardSlot = tuple[type[BaseModel], Any]  # type-state: explicit_any — board-dump value


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
) -> ResolvedAxisLabelOverlapConfig | None:
    """Convert authored AxisLabelOverlapConfig to the resolved dataclass.

    Returns None when the cascade did not populate overlap (resolver will skip).
    Raises if the cascade yielded a partial struct with any None fields — the
    theme must supply both bools via _base.yaml so this never fires in normal
    operation; it fires only when overlap is present but incomplete, which signals
    a broken theme.
    """
    if overlap is None:
        return None
    if overlap.tilt is None or overlap.skip is None:
        raise ValueError(
            f"axis.labels.overlap has None fields after cascade: {overlap!r}; "
            "theme must supply both bools (tilt, skip) via _base.yaml"
        )
    return ResolvedAxisLabelOverlapConfig(tilt=overlap.tilt, skip=overlap.skip)


def build_resolved_axis(
    axis: AxisXStyle | AxisYStyle,
    *,
    band_position: float | None = None,
    edge: Literal["left", "right"] | None = None,
    tick_values: tuple[float, ...] = (),
    domain_max: float | EllipsisType = ...,
    domain_min: float | EllipsisType = ...,
    column_forming: bool = True,
    format_authored: bool,
    format_is_alias: bool,
    is_quantitative: bool = False,
    quantitative_for_alignment: bool | None = None,
    zero_anchored: bool = False,
    chart_id: str,
    y_gridline_caps_bottom: bool | None = None,
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

    ``format_authored`` says whether ``axis.labels.format`` was set by an
    authored cascade layer (board layers 6-9, chart-format fallback Layer 10,
    or chart-local layers 11-13 in ``_merge_axis_cascade``) rather than by
    the theme default. It governs only ``tick_label.format`` below (a ladder that
    doesn't compact still writes its ticks in full unless the author asked
    for that exact SI format themselves); the compacting ``ruler`` branch
    reads it not at all — an author-written SI format still gets full ruler
    treatment there. Required, not defaulted: this is the one flag deciding
    whether the non-compacting bake fires at all, and a caller that forgets
    to thread it should get a type error, not a silent "no bake". A caller
    with no real ``tick_values`` to offer (the categorical ``ax`` axis, or
    any axis where the branch structurally can't fire) still must pass an
    explicit value — ``True`` reads as "nothing to authorize a rewrite of".

    ``format_is_alias`` says whether the *raw*, pre-resolution string the
    authoring layer set was a key in the format-alias table (e.g.
    ``currency`` resolving to ``"$.3~s"``), rather than a literal d3
    spec the author typed directly. It only ever relaxes
    ``format_authored``'s gate below — an authored alias still qualifies for
    the plain-digit bake, since picking a named alias for its prefix/suffix
    is not the same intent as hand-writing an SI spec to force permanent
    compaction. Required, not defaulted, for the same reason as
    ``format_authored``: a caller that forgets to thread it should get a
    type error, not a silent wrong answer. A caller passing
    ``format_authored=False`` may pass ``False`` here too — the value is
    inert whenever the format was never authored at all.

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

    A ladder-LESS axis (``tick_values`` empty — a theme leaving
    ``ticks.count`` unset, or ``multiples.scale: independent``) has no step
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
    """

    def _require(value: Any, path: str) -> Any:
        if value is None:
            raise ValueError(
                f"{path} must be authored by the theme or cascade from axis"
            )
        return value

    # threshold lives on every axis's grid.threshold now — a threshold rule is
    # a property of a quantitative axis, on either channel, not of the
    # measure (y) axis specifically. Built unconditionally; it is simply
    # inert wherever the axis never ticks at the threshold. The field itself
    # stays Optional on the model (a SkipInheritSlots cascade sentinel — see
    # AxisGridThresholdStyle), so _require it here same as its sub-fields.
    _threshold = _require(axis.grid.threshold, "charts.axis.grid.threshold")
    grid_threshold = ResolvedAxisGridThresholdStyle(
        color=_require(_threshold.color, "charts.axis.grid.threshold.color"),
        width=_require(_threshold.width, "charts.axis.grid.threshold.width"),
        visible=_require(_threshold.visible, "charts.axis.grid.threshold.visible"),
    )

    # label passthrough: x-axis adds tilt_increments/values/time_unit/clock from DimensionLabelStyle.
    label_kwargs = {f: getattr(axis.labels, f) for f in _LABEL_PASSTHROUGH}
    if isinstance(axis.labels, DimensionLabelStyle):
        label_kwargs.update(
            {f: getattr(axis.labels, f) for f in _ELEM_X_LABEL_PASSTHROUGH}
        )

    # House-rule format aliases (e.g. "percent", "currency") force
    # label.align = "right" on right-edge quantitative axes so digits share a
    # right edge regardless of what the author set.  Left-edge axes already
    # get "right" from VL's own default.  Bottom/top axes (edge=None, e.g.
    # horizontal bar measure) are excluded.
    # Guard: forced end-anchoring triggers the trailing-reservation device
    # (_build_ruler's reserve path) which requires a tabular font.  Boards
    # with a non-tabular axis label font keep the old start-anchored behavior
    # to avoid ERR-AXIS-COLUMN-REQUIRES-TABULAR-FONT on previously-working
    # configs.  non-column-forming axes (horizontal bar measure) are exempt:
    # their reserve is always False regardless of start_anchored.
    _resolved_font = resolve_cascaded_font(axis.labels.font, "charts.axis.labels.font")
    _quantitative_for_alignment = (
        is_quantitative
        if quantitative_for_alignment is None
        else quantitative_for_alignment
    )
    _force_right = (
        _quantitative_for_alignment
        and format_is_alias
        and edge == "right"
        and (not column_forming or _resolved_font.tabular_figures)
    )
    # _resolve_own_side_align("right", ...) always returns "right" (absolute values
    # pass through unchanged); typed as Literal["left","right","center"] | None
    # so mypy accepts it without a local `| None` annotation, which the counter's
    # `optional` category would still count (report-only, not gate-blocking, but
    # avoided here for a clean return type).
    resolved_label_align = (
        _resolve_own_side_align("right", edge)
        if _force_right
        else _resolve_own_side_align(axis.labels.align, edge)
    )

    label = ResolvedAxisElementStyle(
        font=_resolved_font,
        padding=_require(axis.labels.padding, "charts.axis.labels.padding"),
        align=resolved_label_align,
        overlap=_resolve_overlap_config(axis.labels.overlap),
        **label_kwargs,
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
    fallback_fires = _label_align_fallback_fires(label.align, edge, label.font.case)
    start_anchored = _render_align(label.align, edge, fallback_fires) == "left"
    # One SI-shape verdict, computed here and handed to both _build_ruler calls
    # and the tick_label branch below, so none of them re-derives it. The axis's
    # resolved format when SI-shaped, else None. This does NOT feed _force_right:
    # the alignment force is scoped to house-rule format *aliases* only -- a raw
    # d3 spec renders as literal d3, not the house register.
    _si_format = (
        axis.labels.format
        if axis.labels.format is not None and is_d3_si_spec(axis.labels.format)
        else None
    )
    ruler = _build_ruler(
        tick_values=tick_values,
        si_format=_si_format,
        label_expr=label.expr,
        column_forming=column_forming,
        start_anchored=start_anchored,
        font_family=label.font.family,
        font_tabular=label.font.tabular_figures,
        chart_id=chart_id,
    )

    # tick_label.format is ruler's non-compacting sibling: a ladder that
    # does NOT compact (raw_scale is None) gets its ticks rewritten by
    # `non_compacting_tick_format`, unless the author wrote the SI format
    # themselves (format_authored) or the ladder has too little to derive
    # a step from. Recomputed here rather than read off `ruler is None`
    # because `ruler` can also be None for reasons (non-SI format,
    # authored label.expr) this branch independently re-checks.
    #
    # There is no magnitude floor on the ladder: nothing chooses SI below
    # thousands (`shared_scale_for_ladder` declines), so a sub-1 ladder left
    # out of this branch would paint from the theme's placeholder spec, whose
    # d3 sub-unit prefixes read as the house magnitude suffixes -- 0.3 as
    # "300m", milli misread as million.
    raw_scale = shared_scale_for_ladder(list(tick_values)) if tick_values else None
    tick_label: ResolvedTickLabel | None = None
    if (
        raw_scale is None
        and (not format_authored or format_is_alias)
        and len(tick_values) >= 2
        # `nice_tick_values` rounds each rung to 10 places, so a near-degenerate
        # span (1.0 to 1.0000000001) can hand back a ladder whose adjacent rungs
        # are equal: two ticks, no step. Same exit as a ladder too short to
        # carry one.
        and tick_values[0] != tick_values[1]
        and _si_format is not None
        and label.expr is None
    ):
        step = abs(tick_values[1] - tick_values[0])
        # Prefix-split still applies when there's a currency symbol.
        # Integer place-value alignment is automatic under text-anchor=end;
        # fractional/decimal tail alignment is not -- decimal_pad_table
        # (baked below when column_forming) compensates for that.
        # _si_format is the same resolved string as axis.labels.format here (the
        # guard is `_si_format is not None`), narrowed to str for the caller.
        # A None precision is the scientific register: no fixed decimal
        # position, so no pad table.
        prefix, digit_spec, precision = non_compacting_tick_format(_si_format, step)
        anchor_at_start_plain = None
        if prefix and column_forming:
            # anchor_at_start_plain stays None below when not column_forming
            # -- inject_axis_numeral_expr reads a None here (with prefix set)
            # as the repeat signal, not "no anchor decided yet". Mirrors
            # _build_ruler's effective_mode override (scale.py).
            #
            # Sign-aware anchor: prefer the largest positive tick so the
            # prefix lands on the most prominent value. A "$" has no scale
            # dependency (unlike a shared "K"/"M" suffix, whose magnitude
            # determines the unit), so anchoring on the magnitude-extreme
            # regardless of sign produces the wrong tick on a zero-crossing
            # ladder where the negative extreme is larger. Fall back to
            # min() for all-negative-zero-topped ladders so the prefix
            # never anchors on the 0 cap, which carries no magnitude.
            positives = [v for v in tick_values if v > 0]
            anchor_value = max(positives) if positives else min(tick_values)
            anchor_at_start_plain = tick_values.index(anchor_value) == 0
        if (
            precision is not None
            and column_forming
            and not start_anchored
            and label.font.tabular_figures
        ):
            # Only pad when ticks have mixed fractional depth; uniform depth
            # means every label already aligns (no fix needed).
            # start_anchored skipped: text-anchor:start ignores trailing pads.
            frac_depths = {
                len(_d3_format(digit_spec, v).partition(".")[2]) for v in tick_values
            }
            if len(frac_depths) > 1:
                digit_unit, dot_unit = compose_decimal_units(label.font.family)
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
        and (not format_authored or format_is_alias)
        and _si_format is not None
        and label.expr is None
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
        tick_label = ResolvedTickLabel(
            format=sub_unit_digit_format(_si_format),
            si_format=_si_format,
            scientific_format=sub_unit_scientific_format(_si_format),
        )

    # style.axis_y.mirror draws the y-scale on both edges (MirrorAxisFeature,
    # render/chart/features/mirror_axis.py). The mirrored edge's own
    # resolved anchoring is not always the primary's opposite (an authored
    # labels.align resolves against each edge independently), so it gets
    # its own _build_ruler call here, at resolve, through the same guard --
    # never re-derived in render, where the guard has already run and
    # cannot reach it. mirror.expr/mirror.format replace the ghost's paint
    # entirely, so there is nothing for a baked ruler to describe; skip
    # rather than bake a decision no consumer reads (and that could
    # spuriously trip the guard for a font this device was never asked to
    # measure).
    mirror_ruler: ResolvedRulerAxis | None = None
    mirror_overrides_label = (
        isinstance(axis, AxisYStyle)
        and isinstance(axis.mirror, AxisMirrorStyle)
        and (axis.mirror.expr is not None or axis.mirror.format is not None)
    )
    if (
        ruler is not None
        and isinstance(axis, AxisYStyle)
        and axis.mirror
        and edge in ("left", "right")
        and not mirror_overrides_label
    ):
        opposite_edge: Literal["left", "right"] = "left" if edge == "right" else "right"
        # NOT a second `_resolve_own_side_align` against `opposite_edge` --
        # `MirrorAxisFeature` never re-resolves the authored align for its
        # own edge; it copies the PRIMARY's already-resolved `labelAlign`
        # verbatim (`dict(primary_axis)`). `label.align` (the primary's own
        # resolved value) fed through `_render_align` for `opposite_edge`
        # is what that verbatim copy actually renders as on the ghost's
        # edge -- re-resolving `inward`/`outward` against `opposite_edge`
        # instead computes a decision render never makes. `fallback_fires`
        # is the same one outcome the primary's own render-accurate
        # anchoring above used: the fallback runs once, against the
        # primary's edge, and both axes inherit or smart-default from that
        # single result -- never re-evaluated per edge.
        mirror_ruler = _build_ruler(
            tick_values=tick_values,
            si_format=_si_format,
            label_expr=label.expr,
            column_forming=column_forming,
            start_anchored=_render_align(label.align, opposite_edge, fallback_fires)
            == "left",
            font_family=label.font.family,
            font_tabular=label.font.tabular_figures,
            chart_id=chart_id,
        )

    ticks_visible = axis.ticks.visible
    # "auto" is only ever declared on DimensionTicksStyle (axis_x's own tick
    # slot) -- the isinstance guard is what lets mypy see that arm at all;
    # AxisYStyle's ticks.visible is bool | None and can never compare equal.
    if isinstance(axis, AxisXStyle) and axis.ticks.visible == "auto":
        if axis.grid.visible is not True:
            ticks_visible = True  # no gridlines at all -- nothing else marks position
        elif y_gridline_caps_bottom is None:
            ticks_visible = True  # no cross-axis geometry available -- keep the tick
        else:
            ticks_visible = y_gridline_caps_bottom

    return ResolvedAxisStyle(
        grid=ResolvedAxisGridStyle(
            visible=_require(axis.grid.visible, "charts.axis.grid.visible"),
            opacity=_require(axis.grid.opacity, "charts.axis.grid.opacity"),
            width=_require(axis.grid.width, "charts.axis.grid.width"),
            color=_require(axis.grid.color, "charts.axis.grid.color"),
            dash=axis.grid.dash,
            threshold=grid_threshold,
        ),
        line=ResolvedAxisLineStyle(
            visible=_require(axis.line.visible, "charts.axis.line.visible"),
            width=_require(axis.line.width, "charts.axis.line.width"),
            color=_require(axis.line.color, "charts.axis.line.color"),
        ),
        ticks=ResolvedAxisTicksStyle(
            visible=_require(ticks_visible, "charts.axis.ticks.visible"),
            color=_require(axis.ticks.color, "charts.axis.ticks.color"),
            width=axis.ticks.width,
            length=axis.ticks.length,
            offset=axis.ticks.offset,
            count=axis.ticks.count,
            step=axis.ticks.step,
            time_unit=(
                axis.ticks.time_unit
                if isinstance(axis.ticks, DimensionTicksStyle)
                else None
            ),
        ),
        labels=label,
        title=ResolvedAxisElementStyle(
            font=resolve_cascaded_font(axis.title.font, "charts.axis.title.font"),
            align=_resolve_own_side_align(axis.title.align, edge),
            **{f: getattr(axis.title, f) for f in _TITLE_PASSTHROUGH},
        ),
        position=axis.position,
        mirror=axis.mirror if isinstance(axis, AxisYStyle) else None,
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
        ruler=ruler,
        mirror_ruler=mirror_ruler,
        tick_label=tick_label,
        domain_max=None if domain_max is ... else domain_max,
        domain_min=None if domain_min is ... else domain_min,
        is_quantitative=is_quantitative,
        zero_anchored=zero_anchored,
    )


P = TypeVar("P", bound=BaseModel)


@dataclasses.dataclass(frozen=True)
class AxisOverrides:
    """Chart-local axis override patches for layers 11-13 of the cascade.

    Used by the resolve path to pass chart-local axis patches explicitly
    to resolved_axis_style without requiring a per-chart ChartStyleContext.
    v1 continues to read these from effective.axis_overrides_* as before.
    """

    global_: BaseAxisStylePatch | None = None
    x: AxisXStylePatch | None = None
    y: AxisYStylePatch | None = None
    quantitative: QuantitativeAxisStylePatch | None = None
    band: BandAxisStylePatch | None = None


def chart_type_axis_patch(
    chart_style_context: ChartStyleContext,
    chart_type: str,
    axis_name: Literal["axis_x", "axis_y"],
) -> AxisXStylePatch | AxisYStylePatch | None:
    """Return the chart-type-specific axis patch (Layer 4 of the cascade).

    Pie charts have no cartesian axes, so this always returns None for them.
    """
    chart_type_style = getattr(chart_style_context, chart_type, None)
    return (
        getattr(chart_type_style, axis_name, None)
        if chart_type_style is not None
        else None
    )


def _full_model_as_overlay(model: BaseModel, patch_cls: type[P]) -> P:
    """Re-type a fully-populated theme model as a ``*Patch`` overlay.

    ``merge_onto_base``'s presence contract is ``model_fields_set``-based: a
    field the patch explicitly sets to ``None`` clears the base, it does not
    inherit. ``effective.axis_x``/``axis_y``/``axis_quantitative`` are full
    theme models (every field populated by ``model_validate`` at cascade
    time, so every field is "set") where an unauthored field is simply
    ``None`` and must still inherit from the layer below — the old contract
    these axis-cascade steps were written against. Round-tripping through
    ``exclude_none=True`` recovers that: only genuinely non-None fields (at
    every nesting level) survive into the dump, so the rebuilt Patch's
    ``model_fields_set`` reflects "authored", not "constructed".
    """
    return patch_cls.model_validate(model.model_dump(exclude_none=True))


def _patch_authors_format(patch: BaseModel) -> bool:
    """True when an axis patch explicitly sets labels.format.

    Generic over all axis-variant patch types in the cascade (board layers
    6-9 and chart-local layers 11-13 — all carry a ``labels`` field). Reads
    through
    ``model_dump()`` rather than ``patch.labels.format`` directly: every
    ``*Patch`` class's TYPE_CHECKING stub declares itself a bare subclass of
    the theme model it patches (``class BaseAxisStylePatch(BaseAxisStyle):
    pass``), so mypy sees ``.labels`` as the theme's always-populated field,
    not the real, genuinely-Optional patch field — a direct ``patch.labels is
    not None`` reads as statically-always-true and mypy rejects it as a
    redundant check. ``model_dump()`` sidesteps the wrong stub instead of
    lying to the type checker via a new ``Any``, a new ``| None``, or an
    ignore-pragma comment — the type-state counter
    (``scripts/type_state_counter.py``) tracks all three; ``Any`` and the
    ignore-pragma block the gate, ``| None`` is report-only. Callers guard the
    ``patch is None`` case themselves.
    """
    labels = patch.model_dump().get("labels")
    return isinstance(labels, dict) and labels.get("format") is not None


def _patch_format_is_alias(patch: BaseModel) -> bool:
    """True when a patch's authored ``labels.format`` raw string is an
    engine-predefined name (house rules apply). Returns False for user
    ``style.formats`` aliases and inline d3 specs (native d3, no notation).

    Callers only call this once ``_patch_authors_format(patch)`` has already
    confirmed the patch sets ``labels.format`` at all — this function assumes
    that and just re-reads the same raw value through the same ``model_dump()``
    detour (see that docstring for why). ``labels.format`` is ``str | None``
    on every one of the five axis-variant patch types (never a ``FormatConfig``
    or dict shape — that's KPI's ``format`` field, a different model), so no
    ``isinstance(..., dict)`` branch or ``None``-coalesce is needed here: the
    precondition already rules both out.
    """
    labels = patch.model_dump().get("labels")
    raw_spec = labels.get("format") if isinstance(labels, dict) else None
    if raw_spec is None:
        return False
    return raw_spec in ALL_PREDEFINED_NAMES


@overload
def _merge_axis_cascade(
    chart_style_context: ChartStyleContext,
    axis_name: Literal["axis_x"],
    channel_type: str,
    chart_type_axis_patch: AxisXStylePatch | AxisYStylePatch | None = None,
    *,
    chart_fallback_format: str | FormatConfig | None = None,
    axis_overrides: AxisOverrides | None = None,
    chart_type: str,
    label_authored: bool,
) -> tuple[AxisXStyle, float | None, bool, bool]: ...


@overload
def _merge_axis_cascade(
    chart_style_context: ChartStyleContext,
    axis_name: Literal["axis_y"],
    channel_type: str,
    chart_type_axis_patch: AxisXStylePatch | AxisYStylePatch | None = None,
    *,
    chart_fallback_format: str | FormatConfig | None = None,
    axis_overrides: AxisOverrides | None = None,
    chart_type: str,
    label_authored: bool,
) -> tuple[AxisYStyle, float | None, bool, bool]: ...


def _merge_axis_cascade(
    chart_style_context: ChartStyleContext,
    axis_name: Literal["axis_x", "axis_y"],
    channel_type: str,
    chart_type_axis_patch: AxisXStylePatch | AxisYStylePatch | None = None,
    *,
    chart_fallback_format: str | FormatConfig | None = None,
    axis_overrides: AxisOverrides | None = None,
    chart_type: str,
    label_authored: bool,
) -> tuple[AxisXStyle | AxisYStyle, float | None, bool, bool]:
    """Walk the 13-layer axis cascade and return the merged, channel-typed axis,
    any band_position found along the way, whether an authored layer set
    ``labels.format``, and whether that authored value's raw string was a
    format-alias key.

    Extracted from ``resolved_axis_style`` so a caller that needs a field off
    the merge (e.g. ``position``/``categorical_orient``/``time_unit`` — all
    plain passthrough values, unaffected by inward/outward align resolution)
    can read it before the final narrow-typed ``build_resolved_axis`` call,
    which needs to know the axis's final left/right edge to resolve
    ``label.align``/``title.align``. See ``resolved_axis_style``'s own
    docstring for the 13-layer order this walks.

    Seeds a channel-typed base (``AxisXStyle``/``AxisYStyle``) from the
    channel-agnostic ``effective.axis`` (``BaseAxisStyle``, a raw passthrough
    — see ``_build_resolved_charts``) so channel-specific fields (fill,
    mirror, tilt_increments, ...) survive the merge chain and reach
    ``build_resolved_axis``. ``fill``/``fiscal_year_start_month`` are
    required on ``AxisXStyle``, so they're seeded directly from the
    theme-level ``axis_x`` value (always populated — required fields backed
    by ``_base.yaml`` defaults) before any merge runs.

    ``band_position`` lives only on ``BandAxisStyle``/``BandAxisStylePatch``
    — structurally absent from ``AxisXStyle``/``AxisYStyle`` so it cannot
    survive the merge chain above. Extracted separately from the band
    override patch and returned alongside the merged axis.

    The returned bool ("format_authored") is True when any board layer
    (global/channel/quantitative/family), the chart-level format fallback
    (``chart_fallback_format``), or a chart-local override (axis_overrides)
    sets ``labels.format`` — never Layers 1-3 (theme defaults) or Layer 4
    (the commingled theme+board chart-type patch passed via
    ``chart_type_axis_patch``). It feeds ``build_resolved_axis``'s
    ``format_authored`` keyword, which governs whether a non-compacting ladder
    may bake ``tick_label.format``.

    The second returned bool ("format_is_alias") says whether the *raw*
    string the authoring layer set (before ``resolve_format()`` runs) is an
    engine-predefined name (member of ``ALL_PREDEFINED_NAMES``) — never
    re-derived from the resolved d3 spec, since a predefined name and a
    hand-typed literal can resolve to the exact same string. It feeds
    ``build_resolved_axis``'s ``format_is_alias`` keyword, which relaxes the
    ``format_authored`` gate for a predefined name (e.g. ``currency``)
    so it still qualifies for the plain-digit bake — only a genuine literal
    spec opts out. A ``style.formats`` alias returns False (native d3, no
    house rules) even though it resolves through the alias map.

    ``label_authored`` (Layer 5) is the caller's own ``bool(chart.x_label)``/
    ``bool(chart.y_label)`` for the axis this call is merging — truthy, not
    ``is not None``, so a blank label forces no title and reserves no space
    for one. Every caller must spell it the same way or the emitted title and
    the space budgeted for it drift apart. When
    True, a synthesized ``{"title": {"visible": True}}`` patch is merged in
    right here — after Layer 4, before Layer 6 — so an authored label
    still overrides the theme's blanket title suppression by default, but
    any later authored layer (board 6-9 or chart-local 11-13) that itself
    sets ``title.visible`` on this
    axis wins over the forced default via the normal
    ``merge_onto_base``/``model_fields_set`` precedence, exactly like every
    other layer below it. This is the *only* place the forcing default is
    injected — see ``resolved_axis_style``'s docstring for why a per-slot
    guard at the call site was the wrong shape.
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

    channel_patch_cls = AxisXStylePatch if axis_name == "axis_x" else AxisYStylePatch
    base = merge_onto_base(
        base,
        _full_model_as_overlay(
            getattr(chart_style_context, axis_name),
            channel_patch_cls,
        ),
    )

    if channel_type == "quantitative":
        base = merge_onto_base(
            base,
            _full_model_as_overlay(
                chart_style_context.axis_quantitative, QuantitativeAxisStylePatch
            ),
        )
    # axis_band has no theme-resolved representation; only chart-local applies.

    # Layer 4: theme+board-commingled chart-type patch.  Applied before the
    # board-only layers so those can still override a theme chart-type default.
    base = merge_onto_base(base, chart_type_axis_patch)

    if label_authored:
        # Layer 5: an authored x_label/y_label defaults this axis's title to
        # visible. Placed after the theme tier and before the board tier, so
        # it beats the theme's blanket title suppression but loses to any
        # explicit title.visible authored at board scope (layers 6-9) or
        # chart-local scope (layers 11-13) — a *default*, not an override.
        base = merge_onto_base(
            base, BaseAxisStylePatch.model_validate({"title": {"visible": True}})
        )

    # Board layers 6-9 — the author's own `style.charts.*`, applied after the
    # whole theme tier so an authored leaf is never overwritten by a theme
    # default. Two scopes walk the same three slots in the same order
    # (global -> channel -> quantitative):
    #
    #   6-8. board scope   (charts.axis / axis_x|axis_y / axis_quantitative)
    #   9.   family scope  (charts.<family>.axis*) — applied second so a
    #        board-authored family key beats a board-authored global one. The
    #        commingled Layer 4 merge already carried the raw value, but it
    #        would otherwise lose to layers 5-7; re-applying the board-only
    #        part here restores family > global without disturbing theme
    #        contributions that are not also board-authored.
    #
    # Read through model_dump(exclude_none=True) to sidestep the TYPE_CHECKING
    # stub (ChartsStylePatch inherits ChartsStyle's non-optional field types,
    # so `_cbo.axis is not None` trips reportUnnecessaryComparison). The dump
    # doubles as the null-leaf guard: an authored `padding:` with no value is
    # stripped, so it cannot clear a theme-required field. Key presence
    # therefore means "authored, with non-null content".
    #
    # axis_band has no board-level authoring surface (extra_forbidden on
    # ChartsStyle), so there is no board-level band slot here.
    format_authored = False
    format_is_alias = False

    # model_dump values are dynamic; each is re-validated into a typed patch below.
    _cbo_data: dict[str, Any]  # type-state: explicit_any — Pydantic model_dump result
    _cbo_data = chart_style_context.charts_board_overrides.model_dump(exclude_none=True)
    _scopes: list[dict[str, Any]]  # type-state: explicit_any — model_dump dicts
    _scopes = [_cbo_data]
    if chart_type and _cbo_data.get(chart_type) is not None:
        _scopes.append(_cbo_data[chart_type])
    for _scope in _scopes:
        _slots: list[_BoardSlot] = [
            (BaseAxisStylePatch, _scope.get("axis")),
            (channel_patch_cls, _scope.get(axis_name)),
        ]
        if channel_type == "quantitative":
            _slots.append((QuantitativeAxisStylePatch, _scope.get("axis_quantitative")))
        for _cls, _raw in _slots:
            if _raw is None:
                continue
            _patch = _cls.model_validate(_raw)
            base = merge_onto_base(base, _patch)
            if _patch_authors_format(_patch):
                format_authored = True
                format_is_alias = _patch_format_is_alias(_patch)

    # Layer 10: chart-level format fallback (chart.format / style.number_format /
    # style.time_format).  Sits after all board layers so chart-authored format
    # beats the board, but before chart-local style.axis_* so those still win.
    if chart_fallback_format is not None:
        format_authored = True
        _fallback_spec = (
            chart_fallback_format.spec
            if isinstance(chart_fallback_format, FormatConfig)
            else chart_fallback_format
        )
        if _fallback_spec is None:
            _fallback_spec = ""
        format_is_alias = _fallback_spec in ALL_PREDEFINED_NAMES
        _resolved_fallback_fmt = resolve_format(
            chart_fallback_format, chart_style_context.formats
        )
        base = merge_onto_base(
            base,
            BaseAxisStylePatch.model_validate(
                {"labels": {"format": _resolved_fallback_fmt}}
            ),
        )

    # format_is_alias tracks only the *most recently authored* layer's raw
    # string, not a blind OR across every layer that ever set the field --
    # mirroring merge_onto_base's last-write-wins semantics for the field
    # itself. If an earlier layer authors an alias and a later layer
    # overrides it with a literal spec, the final labels.format is that
    # literal, so format_is_alias must become False again here too, not stay
    # stuck True from the overridden layer. format_authored, by contrast,
    # accumulates -- any authored layer counts, matching the pre-existing
    # OR-chain.
    band_position: float | None = None
    if axis_overrides is not None:
        # v2 path: chart-local patches passed explicitly.
        base = merge_onto_base(base, axis_overrides.global_)
        if axis_overrides.global_ is not None and _patch_authors_format(
            axis_overrides.global_
        ):
            format_authored = True
            format_is_alias = _patch_format_is_alias(axis_overrides.global_)
        if channel_type == "quantitative":
            base = merge_onto_base(base, axis_overrides.quantitative)
            if axis_overrides.quantitative is not None and _patch_authors_format(
                axis_overrides.quantitative
            ):
                format_authored = True
                format_is_alias = _patch_format_is_alias(axis_overrides.quantitative)
        elif channel_type in ("ordinal", "nominal", "band"):
            band_patch = axis_overrides.band
            if band_patch is not None:
                band_position = band_patch.band_position
            base = merge_onto_base(base, band_patch)
            if band_patch is not None and _patch_authors_format(band_patch):
                format_authored = True
                format_is_alias = _patch_format_is_alias(band_patch)
        _channel_patch = axis_overrides.x if axis_name == "axis_x" else axis_overrides.y
        base = merge_onto_base(base, _channel_patch)
        if _channel_patch is not None and _patch_authors_format(_channel_patch):
            format_authored = True
            format_is_alias = _patch_format_is_alias(_channel_patch)
    else:
        # v1 path: read chart-local patches from effective (set by build_chart_style_context).
        base = merge_onto_base(base, chart_style_context.axis_overrides_global)
        _global_patch = chart_style_context.axis_overrides_global
        if _global_patch is not None and _patch_authors_format(_global_patch):
            format_authored = True
            format_is_alias = _patch_format_is_alias(_global_patch)
        if channel_type == "quantitative":
            base = merge_onto_base(
                base, chart_style_context.axis_overrides_quantitative
            )
            _quant_patch = chart_style_context.axis_overrides_quantitative
            if _quant_patch is not None and _patch_authors_format(_quant_patch):
                format_authored = True
                format_is_alias = _patch_format_is_alias(_quant_patch)
        elif channel_type in ("ordinal", "nominal", "band"):
            band_patch = chart_style_context.axis_overrides_band
            if band_patch is not None:
                band_position = band_patch.band_position
            base = merge_onto_base(base, band_patch)
            if band_patch is not None and _patch_authors_format(band_patch):
                format_authored = True
                format_is_alias = _patch_format_is_alias(band_patch)
        _channel_patch = getattr(chart_style_context, f"axis_overrides_{axis_name[-1]}")
        base = merge_onto_base(base, _channel_patch)
        if _channel_patch is not None and _patch_authors_format(_channel_patch):
            format_authored = True
            format_is_alias = _patch_format_is_alias(_channel_patch)

    # mirror.format is an authored per-edge relabel (AxisMirrorStyle, y-axis
    # only) that never passes through the labels.format cascade above — it
    # is stored as-is on the resolved axis and handed to Vega verbatim by
    # mirror_axis.py, so it must get the same resolve_format treatment
    # (alias lookup + round-aware trim) here, not at the render read site,
    # or the mirrored edge and the primary edge disagree on digits for what
    # is meant to be one shared scale.
    if (
        isinstance(base, AxisYStyle)
        and isinstance(base.mirror, AxisMirrorStyle)
        and base.mirror.format is not None
    ):
        base = base.model_copy(
            update={
                "mirror": base.mirror.model_copy(
                    update={
                        "format": resolve_format(
                            base.mirror.format, chart_style_context.formats
                        )
                    }
                )
            }
        )

    return base, band_position, format_authored, format_is_alias


def resolved_axis_style(
    chart_style_context: ChartStyleContext,
    axis_name: Literal["axis_x", "axis_y"],
    channel_type: str,
    chart_type_axis_patch: AxisXStylePatch | AxisYStylePatch | None = None,
    *,
    chart_fallback_format: str | FormatConfig | None = None,
    axis_overrides: AxisOverrides | None = None,
    chart_type: str,
    label_authored: bool,
) -> ResolvedAxisStyle:
    """Walk the 13-layer axis cascade and return a merged ResolvedAxisStyle.

    Single source of truth for axis state at emit time. The renderer reads
    every axis field (ticks, grid, domain, position, offset, ...) from this
    return value — never from ``effective.axis_x`` / ``axis_y`` directly,
    since those reflect only Layers 1+2 and silently miss type-conditional,
    chart-type, and chart-local overrides.

    Layer order (each overrides the previous):
      1.  theme global                     (effective.axis)
      2.  theme channel-specific           (effective.axis_x / axis_y)
      3.  theme type-conditional           (effective.axis_quantitative / band)
      4.  theme+board chart-type-specific  (chart_type_axis_patch)
      5.  label-forced title default       (label_authored)
      6.  board global                     (charts_board_overrides.axis)
      7.  board channel-specific           (charts_board_overrides.axis_x / axis_y)
      8.  board type-conditional           (charts_board_overrides.axis_quantitative)
      9.  board family re-apply            (charts_board_overrides.<chart_type>.axis*)
      10. chart-level format fallback      (chart_fallback_format)
      11. chart-local global               (effective.axis_overrides_global)
      12. chart-local type-conditional     (effective.axis_overrides_quantitative / _band)
      13. chart-local channel-specific     (effective.axis_overrides_{{x,y}}) — wins

    ``axis_x``/``axis_y``/``axis_quantitative`` are authored-only overlays
    (SkipInheritSlots — apply_inherit never fills their unset leaves from the
    shared ``axis`` global). Merging them explicitly here, in this order, is
    the only place they combine — no independent inheritance between axis
    fields means no risk of an inherited-not-authored value on one slot
    clobbering another slot's own deviation from the shared global.

    ``chart_fallback_format`` (Layer 10) is ``chart.format`` /
    ``style.number_format`` / ``style.time_format`` — the per-chart format
    fallback computed by ``chart_authored_axis_format()``. It sits after all
    board layers so a chart author's own format beats any board-level default,
    while still losing to an explicit ``style.axis_x``/``axis_y``
    ``.labels.format`` (layers 11-13) that the chart itself authors.

    ``label_authored`` (Layer 5) is ``bool(chart.x_label)``/
    ``bool(chart.y_label)`` for the axis being merged — truthy, not
    ``is not None``, so a blank label forces no title — the same
    "synthesized default, beats the theme, loses to authored layers below"
    shape as ``chart_fallback_format`` applies to format. An authored label
    forces the axis title on over the theme's blanket suppression, but any
    later layer that itself sets ``title.visible`` — a board-level
    ``style.charts.axis*.title.visible`` (layers 6-9) or a chart-local
    ``axis_x``/``axis_y``/``axis_quantitative``/``axis_band`` patch (layers
    11-13) — wins over the forced default with no special-casing, because it
    is simply a later layer in the same merge chain. Placed here, after the
    theme tier and before the board tier, so board-level and chart-local
    ``title.visible: false`` both override it — an earlier version of the fix
    (before the board tier existed) placed it after the theme chart-type
    patch and above only chart-local, which would let a board-level
    ``title.visible: false`` lose to it.

    Chart-local layers (11-13) are read from ``effective.axis_overrides_*``
    by default (v1 path, populated by ``build_chart_style_context``).  When
    ``axis_overrides`` is provided (v2 path), those values are used instead —
    allowing the caller to pass chart-local patches without constructing a
    per-chart ChartStyleContext.  v1 callers are byte-identical.

    Returns the axis with ``label.align``/``title.align`` resolved with no
    known left/right edge (``inward``/``outward`` collapse to ``None`` — the
    away-from-plot default). Callers that need own-side align resolved
    against a real edge (e.g. the cartesian resolvers, once orientation/
    position is known) call ``_merge_axis_cascade`` + ``build_resolved_axis``
    directly instead of this wrapper.
    """
    base, band_position, format_authored, format_is_alias = _merge_axis_cascade(
        chart_style_context,
        axis_name,
        channel_type,
        chart_type_axis_patch,
        chart_fallback_format=chart_fallback_format,
        axis_overrides=axis_overrides,
        chart_type=chart_type,
        label_authored=label_authored,
    )
    # No tick_values here -- this cascade never bakes a ruler ladder, so
    # ruler is always None regardless of chart_id (which build_resolved_axis
    # needs only for the tabular-guarantee error message, unreachable on
    # this no-ladder path). No caller of this wrapper has a real chart id to
    # offer, so "" is passed inline rather than threading a dead parameter
    # through resolved_axis_style's public signature. format_authored and
    # format_is_alias are threaded through regardless -- cheap and correct,
    # and not inert: the ladder-less sub-unit bake's own entry condition is
    # empty tick_values, which this wrapper always has. What keeps it from
    # firing on this path is that `labels.format` here is still an
    # unresolved alias name (e.g. "number"), not the literal d3 spec
    # `is_d3_si_spec` requires.
    return build_resolved_axis(
        base,
        band_position=band_position,
        format_authored=format_authored,
        format_is_alias=format_is_alias,
        is_quantitative=channel_type == "quantitative",
        chart_id="",
        # This wrapper resolves one axis in isolation, with no paired y-axis
        # to ask the geometric question against -- explicit, not relying on
        # the parameter default, since this axis's own `ticks` field is never
        # read on this no-ladder path regardless (see the comment above).
        y_gridline_caps_bottom=None,
    )
