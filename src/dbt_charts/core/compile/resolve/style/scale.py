"""Resolved scale construction and ruler (digit-ladder compaction) math."""

from __future__ import annotations

from d3_format import format as _d3_format
from dbt_charts.core.compile.errors import CompilationError
from dbt_charts.core.compile.models.primitives import AffixRepeat
from dbt_charts.core.compile.models.style.resolved._base import (
    ResolvedRulerAxis,
    ResolvedScaleContinuousStyle,
    ResolvedScaleLogStyle,
    ResolvedScalePowStyle,
    ResolvedScaleStyle,
    ResolvedScaleSymlogStyle,
)
from dbt_charts.core.compile.models.style.theme import BaseScaleStyle, XScaleStyle
from dbt_charts.core.diagnostics.codes_compile import (
    ERR_AXIS_COLUMN_REQUIRES_TABULAR_FONT,
)
from dbt_charts.core.font_measure import (
    compose_decimal_units,
    compose_suffix_reservation,
)
from dbt_charts.core.text.format_d3 import Notation
from dbt_charts.core.text.numeral_scale import (
    SuffixMode,
    build_decimal_pad_table,
    ruler_digit_format,
    shared_scale_for_ladder,
    suffix_at_register,
)


def build_resolved_scale(
    scale: BaseScaleStyle | XScaleStyle | None,
) -> ResolvedScaleStyle | None:
    """Convert authored BaseScaleStyle/XScaleStyle to ResolvedScaleStyle.

    Returns None when no scale was authored (axis or chart has no scale override).
    continuous is None when not authored. x_reverse is populated only from
    XScaleStyle; None on all other inputs.
    """
    if scale is None:
        return None
    return ResolvedScaleStyle(
        round=scale.round,
        clamp=scale.clamp,
        nice=scale.nice,
        padding=scale.padding,
        headroom=scale.headroom,
        values=scale.values,
        continuous=(
            ResolvedScaleContinuousStyle(
                zero=None if scale.continuous.zero == "auto" else scale.continuous.zero,
                type=scale.continuous.type,
                domain=scale.continuous.domain,
                log=(
                    ResolvedScaleLogStyle(base=scale.continuous.log.base)
                    if scale.continuous.log is not None
                    else None
                ),
                pow=(
                    ResolvedScalePowStyle(exponent=scale.continuous.pow.exponent)
                    if scale.continuous.pow is not None
                    else None
                ),
                symlog=(
                    ResolvedScaleSymlogStyle(constant=scale.continuous.symlog.constant)
                    if scale.continuous.symlog is not None
                    else None
                ),
            )
            if scale.continuous is not None
            else None
        ),
        x_reverse=scale.x_reverse if isinstance(scale, XScaleStyle) else None,
    )


def _build_ruler(
    *,
    tick_values: tuple[float, ...],
    si_format: str | None,
    label_expr: str | None,
    column_forming: bool,
    start_anchored: bool,
    font_family: str,
    font_tabular: bool,
    chart_id: str,
    notation: Notation | None = None,
    repeat: AffixRepeat | None = None,
) -> ResolvedRulerAxis | None:
    """Bake the ruler decision for one edge of one axis: does the tick
    ladder compact, and if so does it ship the trailing suffix reservation
    that aligns digits (an end-anchored label only). Integer place-value
    alignment is automatic under text-anchor=end; fractional/decimal tail
    alignment is not -- decimal_pad_table (baked below) compensates for that.

    A start-anchored axis (``_effective_align(...) == "left"``) gets no
    ruler at all: it falls back to plain VL formatting, unaligned.
    ``build_resolved_axis`` forces ``align: "right"`` for a format-alias axis
    only when it is quantitative, on the right edge, and its label font has
    tabular figures -- so the branch is reached by a raw/literal SI spec, and
    also by an alias axis that misses one of those conditions (a non-tabular
    label font, e.g.) or carries an explicitly authored start-anchoring align.

    ``build_resolved_axis`` calls this once for the primary edge and, when
    ``style.axis_y.mirror`` is set, once more for the mirrored edge: same
    ladder (``tick_values``/``si_format``), same font, but the mirrored
    edge's own resolved anchoring is typically (not always -- an authored
    ``labels.align`` can make either edge either anchoring) the opposite of
    the primary's.

    ``si_format`` is the axis's resolved label format when it is SI-shaped,
    else ``None`` -- the caller runs ``is_d3_si_spec`` once and hands the
    verdict down, so this function does not re-derive it (both call sites
    share the one decision).

    ``notation`` is the authored register override; it replaces the
    mode-derived one and is baked into ``register``. ``repeat`` likewise
    overrides the column-forming default of ``prefix_repeats``.
    """
    raw_scale = shared_scale_for_ladder(list(tick_values)) if tick_values else None
    if (
        raw_scale is None
        or si_format is None
        or label_expr is not None
        or (column_forming and start_anchored)
    ):
        return None

    effective_mode = raw_scale.mode if column_forming else SuffixMode.REPEAT
    prefix, digit_spec = ruler_digit_format(si_format)
    # Which END of the ascending ladder is the magnitude-extreme -- see
    # ResolvedAxisStyle.ruler's docstring for why this is a position
    # (baked once here) rather than the tick's raw value.
    anchor_at_start = tick_values.index(max(tick_values, key=abs)) == 0
    register: Notation = notation or (
        "analytic" if effective_mode is SuffixMode.ANCHOR else "narrative"
    )

    reserve = column_forming
    if reserve and not font_tabular:
        raise CompilationError.from_code(
            ERR_AXIS_COLUMN_REQUIRES_TABULAR_FONT,
            chart_id=chart_id,
            family=font_family,
        )
    reservation = (
        compose_suffix_reservation(
            suffix_at_register(raw_scale.exponent, register), font_family
        )
        if reserve
        else ""
    )
    if reserve:
        magnitude = 10**raw_scale.exponent
        # Only pad when ticks actually have mixed decimal depth: gate on the
        # length of the *fractional* part (after the "."), not total length.
        # Total length varies with integer digit count (0 vs 20 are different
        # lengths but the same fractional depth -- both already aligned).
        frac_depths = {
            len(_d3_format(digit_spec, v / magnitude).partition(".")[2])
            for v in tick_values
        }
        if len(frac_depths) > 1:
            digit_unit, dot_unit = compose_decimal_units(font_family)
            # Ruler digit_spec is always precision=1: table has 3 entries (0..2).
            decimal_pad_table: tuple[str, ...] = build_decimal_pad_table(
                1, digit_unit, dot_unit
            )
        else:
            decimal_pad_table = ()
    else:
        decimal_pad_table = ()
    return ResolvedRulerAxis(
        exponent=raw_scale.exponent,
        mode=effective_mode,
        reserve=reserve,
        prefix_repeats=not column_forming if repeat is None else repeat == "every",
        prefix=prefix,
        digit_spec=digit_spec,
        anchor_at_start=anchor_at_start,
        reservation=reservation,
        register=register,
        decimal_pad_table=decimal_pad_table,
    )


__all__ = ["build_resolved_scale", "_build_ruler"]
