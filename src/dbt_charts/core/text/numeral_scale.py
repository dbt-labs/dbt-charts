"""Shared-scale resolver: does a set of numbers compact, and at what magnitude.

Pure — no font, no I/O, no compile/render import. Every label formats itself
alone today, so nothing in the engine can answer "does this group of values
compact, and if so at what scale".

A set compacts only when it must: when some SI tier at or above thousands
divides its step (whole multiples, plus a half-step), and when the extreme
value written out in plain digits reaches six integer digits. A ruler
therefore writes its ticks out in full up to the hundred-thousands.

Two entry points over one core, because the inputs genuinely differ. A ladder
is ordered and regular and chosen by the engine itself
(``core.numeric.nice_tick_values``); a column is arbitrary data.
Ruler magnitude comes from the tick step and never from a majority vote,
because voting over ticks we selected ourselves would be circular.
"""

from __future__ import annotations

import dataclasses
import math
from collections import Counter
from collections.abc import Iterable
from dataclasses import dataclass
from enum import Enum

from d3_format import FormatSpec, parse as _d3_parse
from dbt_charts.core.text.format_d3 import _D3_TO_ANALYTIC, _D3_TO_NARRATIVE, Notation

# d3's SI prefix letter at each tier — the same letters _D3_TO_ANALYTIC and
# _D3_TO_NARRATIVE key on, so the suffix strings are read from there rather
# than restated as a new literal table. Below thousands a value is already
# short enough to write out in full, so it never needs a magnitude suffix.
# Capped at trillions deliberately: a tick ladder runs from zero and stays
# within about one tier of its top by construction, so a ruler/ledger/strip
# magnitude never needs to reach peta and above — unlike the vocabulary maps
# this reads from, which do cover the full SI range for a value that stands
# alone (see format_d3's above-trillions narrative fallback).
_D3_PREFIX_BY_EXPONENT: dict[int, str] = {3: "k", 6: "M", 9: "G", 12: "T"}

# SI tiers a shared scale may land on — the same set _D3_PREFIX_BY_EXPONENT
# is keyed on, so the two cannot diverge.
_TIERS: tuple[int, ...] = tuple(_D3_PREFIX_BY_EXPONENT)

# A set compacts only when its extreme value, written out in plain digits,
# reaches this many integer digits. Chosen over 5 because 10,000-90,000 read
# instantly at six characters, and the Economist corpus survey found a
# magnitude suffix reaching a tick twice in 61 pages.
_COMPACTION_DIGIT_THRESHOLD = 6

# Once the magnitude is chosen, the extreme value rendered at that scale
# reaching this many integer digits means the ladder/column has outgrown its
# tier, so the suffix must repeat rather than anchor once.
_MODE_TRIGGER_DIGITS = 4

# Decimal places a step is rounded to before its own depth is read off, and
# therefore the deepest fixed-point precision this module can derive. A step
# finer than one unit of that grid has no fixed-point spec at all — see
# `_fixed_point_reaches`.
_MAX_STEP_PRECISION = 10


def suffix_at_register(exponent: int, register: Notation) -> str:
    """The magnitude suffix text for ``exponent``, at an explicit register.

    ``SharedScale.suffix_string`` calls this at its own mode-derived register;
    a horizontal ruler forces narrative regardless of mode (it forms no
    column, so the analytic/narrative choice that normally follows mode
    doesn't apply — see the "ruler" section of the numeral design doc), which
    needs the suffix text at a register the scale's own mode didn't select.
    """
    table = _D3_TO_ANALYTIC if register == "analytic" else _D3_TO_NARRATIVE
    return table[_D3_PREFIX_BY_EXPONENT[exponent]]


class SuffixMode(Enum):
    """Which of the two compacting modes a shared scale uses.

    ANCHOR declares the magnitude once (the top tick, or the ledger's first
    row) and leaves every other member bare. REPEAT declares it on every
    non-zero member, because the set has outgrown the tier the magnitude
    names.
    """

    ANCHOR = "anchor"
    REPEAT = "repeat"


@dataclass(frozen=True)
class SharedScale:
    """The magnitude chosen for a set: exponent, mode, and the affixes it implies.

    ``None`` (not a ``compacts: bool`` field) means a set does not compact —
    when it doesn't there is no magnitude, mode, register, or suffix to carry,
    so a boolean-plus-four-``None``s representation would make an invalid
    state representable.
    """

    exponent: int
    mode: SuffixMode

    @property
    def register(self) -> Notation:
        """The register this scale speaks: analytic for ANCHOR, narrative for REPEAT.

        Derived from mode, not stored — storing it would create a second
        source of truth for one decision (register follows frequency: a
        suffix stated once is a set declaration and reads analytic; a suffix
        on every member is value-attached and reads narrative).
        """
        return "analytic" if self.mode is SuffixMode.ANCHOR else "narrative"

    @property
    def suffix_string(self) -> str:
        """The one suffix string this scale ever emits, at its register."""
        return suffix_at_register(self.exponent, self.register)

    def suffix(self, value: float, is_anchor: bool) -> str:
        """The magnitude suffix a given member of the set should carry.

        Zero never carries a magnitude suffix, in either mode, at any scale —
        ``0k`` is meaningless, and zero is zero at any scale. In ANCHOR mode
        only the anchor member carries it; every other member is bare. In
        REPEAT mode every non-zero member carries it.
        """
        if value == 0:
            return ""
        if self.mode is SuffixMode.ANCHOR and not is_anchor:
            return ""
        return self.suffix_string


def _integer_digit_count(value: float) -> int:
    """Digits before the decimal point in value's plain, written-out form.

    Truncates rather than rounds: 999.5 has 3 written integer digits, not 4 —
    rounding up would count a decimal fraction as an extra whole digit.
    """
    return len(str(int(math.floor(abs(value)))))


def _decimal_exponent(value: float) -> int:
    """Digits before the decimal point in value's plain form, allowing
    negative results for ``|value| < 1``.

    Unlike ``_integer_digit_count`` (which floors at 1 below 1.0 -- correct
    for a ladder/column's magnitude-*extreme*, always >= 1 by construction),
    this must distinguish 0.01 from 0.0001: both would otherwise report the
    same "1" digit count, which is what let ``column_digit_format`` collapse
    unrelated sub-tier values (rows below the column's shared magnitude) to
    the same displayed string. Equivalent to ``_integer_digit_count`` for
    every value >= 1 (verified: identical for all non-zero magnitudes across
    a stress sweep including every power of ten up to 1e14).
    """
    if value == 0:
        return 1
    return math.floor(math.log10(abs(value))) + 1


def _mode_for_scaled_extreme(scaled_extreme: float) -> SuffixMode:
    if _integer_digit_count(scaled_extreme) >= _MODE_TRIGGER_DIGITS:
        return SuffixMode.REPEAT
    return SuffixMode.ANCHOR


def _largest_tier_dividing(step: float) -> int | None:
    """Largest SI tier (at or above thousands) that step divides into evenly.

    A fractional-ratio exception (a step at exactly half a tier resolving to
    that tier) was tried and reverted: it fired on any tier, not only the one
    it was written for, so a step like 500,000 got bumped from thousands
    (ratio 500, a whole multiple) up to millions (ratio 0.5) -- spelling
    "0.5M" instead of "500k". Whole-multiple-only avoids that; a step with no
    whole-multiple tier at or above thousands doesn't compact at all.

    ``nice_tick_values`` rounds to 10 decimal places, so the closeness check
    tolerates float noise rather than demanding an exact ratio.
    """
    for tier in sorted(_TIERS, reverse=True):
        ratio = step / (10.0**tier)
        if ratio >= 1 and math.isclose(ratio, round(ratio), rel_tol=1e-9, abs_tol=1e-9):
            return tier
    return None


def shared_scale_for_ladder(ticks: list[float]) -> SharedScale | None:
    """Resolve the shared scale for a tick ladder, or None if it doesn't compact.

    The magnitude comes from the tick step, never from a majority vote over
    the ticks — we chose them ourselves, so voting would be circular. A set
    compacts only when some SI tier at or above thousands divides the step
    evenly (the magnitude even exists) *and* the extreme tick, written out in
    plain digits, reaches the compaction digit threshold.
    """
    if len(ticks) < 2:
        return None

    step = abs(ticks[1] - ticks[0])
    tier = _largest_tier_dividing(step)
    if tier is None:
        return None

    extreme = max(abs(t) for t in ticks)
    if _integer_digit_count(extreme) < _COMPACTION_DIGIT_THRESHOLD:
        return None

    return SharedScale(
        exponent=tier, mode=_mode_for_scaled_extreme(extreme / (10.0**tier))
    )


def _natural_tier(value: float) -> int | None:
    """The SI tier value's own magnitude falls into, or None below thousands."""
    magnitude = abs(value)
    for tier in sorted(_TIERS, reverse=True):
        if magnitude >= 10.0**tier:
            return tier
    return None


def tier_distance(value: float, majority_exponent: int) -> int | None:
    """How many SI tiers below ``majority_exponent`` ``value``'s own natural
    tier sits.

    ``None`` when ``value`` has no natural tier at all (below the smallest
    SI tier) -- further below than any finite distance expresses. ``0``
    means ``value``'s own tier *is* the majority tier; ``1`` means one tier
    below (e.g. a thousands-range value inside a millions-majority column).
    Never negative: a value voted into a column's majority-tier tally can
    only sit at or below the tier the vote produced.

    Exists so a column-wide digit_spec's precision can be bounded: deriving
    precision from an arbitrarily-far-below-tier value (``column_digit_format``'s
    ``finest_scaled`` argument) has no natural ceiling on its own -- a value
    a majority tier's straggler is one step below (e.g. thousands inside a
    millions column) legitimately needs a few extra decimals to stay
    accurate; a value with no tier at all needs so many that every other row
    in the column inherits far more precision than the format's own
    significant-figure count ever asked for. See
    ``compile/resolve/chart/_table.py``'s shared-scale bake, which refuses
    the whole column rather than let one outlier dictate everyone else's
    precision.
    """
    value_tier = _natural_tier(value)
    if value_tier is None:
        return None
    tiers_ascending = sorted(_TIERS)
    return tiers_ascending.index(majority_exponent) - tiers_ascending.index(value_tier)


def _printed_si_suffix(formatted: str) -> str:
    """The trailing SI magnitude-suffix letters a d3 ``s``-type spec printed
    (``k``, ``M``, ``m``, the micro sign, ...), or ``""`` when the value
    printed with no suffix at all.

    Reads the text a spec actually produced, not a raw-value classification
    over the SI tier (``_natural_tier``): d3's own notation also emits
    SUB-unit prefixes (``m``, the micro sign, ...) for a value below 1, and
    rounds a boundary value up across a tier edge at the format's own
    significant-figure count (``999.96`` at three figures prints ``"1k"``,
    not ``"1000"``) -- a classifier over the raw, unrounded value alone
    misses both, so it can call two cells "the same unit" when one printed
    with a different suffix, or none at all.

    Strips a trailing accounting-sign close-paren first (``"($4.5M)"``) so a
    negative cell's suffix still compares equal to a positive sibling's --
    the paren itself carries no unit information, and leaving it in would
    make whether a column happens to contain a negative value decide whether
    it decimal-aligns at all.
    """
    text = formatted[:-1] if formatted.endswith(")") else formatted
    end = len(text)
    while end > 0 and text[end - 1].isalpha():
        end -= 1
    return text[end:]


def column_shares_one_printed_unit(cells: Iterable[tuple[float, str]]) -> bool:
    """True when every non-zero, finite value's ACTUAL printed SI suffix
    agrees -- the question a no-shared-tier decimal-pad bake must answer.

    Distinguishes the two ways ``shared_scale_for_column`` can refuse a
    shared tier: a real column-wide majority-tier bake can refuse either
    because there's a genuine magnitude mix (some values >= the smallest
    tier, some below, or split across different real tiers -- no shared
    decimal position exists at all, e.g. 12100/900) or merely because the
    extreme value is too small to clear the compaction-digit threshold even
    though every value shares one real tier (e.g. 15000/5000/3500, all
    thousands, extreme below the 6-digit floor). Only the second case has a
    shared decimal position to pad to -- a bare "900" and a "12.1k" cell
    share no place value, so padding them together is meaningless, not just
    imprecise.

    ``cells`` pairs each value with its OWN already-formatted text (the same
    string the caller measures for fractional depth) so this reads the
    actual printed suffix rather than reformatting or reclassifying the raw
    value -- deliberately not built from ``_natural_tier``, which only knows
    tiers at or above thousands and has no rounding awareness, so it silently
    disagrees with what a "s"-type spec really prints for a sub-1 value (a
    milli-prefixed suffix) or a value that rounds up across a tier edge.

    Every caller of this predicate must gate on it before building a
    no-shared-tier decimal-pad table -- ``_table.py``'s
    ``_unscaled_decimal_pad_table``, ``support_table_attachment.py``'s
    ``_unscaled_decimal_pad`` -- not merely on ``shared_scale_for_column``
    returning ``None``. That includes a caller whose own shared-scale bake
    is structurally disabled (``_table.py``'s ``allow_shared_scale=False``
    pie/donut legend path never even calls ``shared_scale_for_column``), so
    a homogeneous single-real-tier column can reach the fallback there too,
    not only the "everyone's too small" case.

    Filters non-finite values same as ``shared_scale_for_column``'s own
    voter list (``_magnitude_numerals``'s ``voters``) -- an ``inf``/``nan``
    cell has no unit of its own to disagree with the rest of the column
    about, and must not silently veto every other cell's pad.

    Vacuously True for an empty, all-zero, or all-non-finite set, matching
    ``shared_scale_for_column``'s own exclusion of zeros from its tally --
    there is no value with a unit to disagree about.
    """
    suffixes = {
        _printed_si_suffix(text)
        for value, text in cells
        if value != 0 and math.isfinite(value)
    }
    return len(suffixes) <= 1


def shared_scale_for_column(values: list[float]) -> SharedScale | None:
    """Resolve the shared scale for an arbitrary column, or None if it doesn't compact.

    The magnitude is the majority tier across every non-zero value in the
    set — unlike a ladder, a column's values are given, so a majority vote is
    the right mechanism. Every non-zero value casts a ballot: its natural
    tier, or ``None`` when it sits below the smallest SI tier — so a set that
    is mostly sub-thousand values cannot be outvoted by one large outlier.
    ``None`` wins any tie it is part of, not only an outright majority — an
    even split still means the set has no clear shared magnitude, so it
    shows exact digits rather than scaling a value that was tied against
    "no tier". Among tiers tied for the majority with no ``None`` in the tie,
    the higher one wins, which also makes the result independent of row
    order. Compacts only when the extreme value, written out in plain
    digits, reaches the compaction digit threshold.
    """
    tally = Counter(_natural_tier(v) for v in values if v != 0)
    if not tally:
        return None

    top_count = max(tally.values())
    tied_tiers = [tier for tier, count in tally.items() if count == top_count]
    if None in tied_tiers:
        return None
    majority_tier = max(tier for tier in tied_tiers if tier is not None)

    extreme = max(abs(v) for v in values)
    if _integer_digit_count(extreme) < _COMPACTION_DIGIT_THRESHOLD:
        return None

    return SharedScale(
        exponent=majority_tier,
        mode=_mode_for_scaled_extreme(extreme / (10.0**majority_tier)),
    )


def decimal_reservation_pad(
    missing_len: int,
    has_dot: bool,
    digit_unit: str,
    dot_unit: str,
) -> str:
    """The trailing padding that makes a trimmed decimal value as wide as its
    untrimmed sibling, so a column of mixed whole/fractional values aligns under
    text-anchor:end.

    ``missing_len`` is ``len(full_text) - len(trimmed_text)`` where both use
    the same d3 format spec -- one with ``~`` (trim) and one without. Trim
    only strips from the tail, so the length diff is exact: no char-by-char
    scanning needed.

    ``has_dot`` is ``"." in trimmed_text``: True when the decimal point survived
    trimming (some but not all decimal digits were stripped). When it is False
    the dot itself was also stripped and must be represented by ``dot_unit``
    in the padding.

    ``digit_unit`` and ``dot_unit`` must be pre-composed via
    ``compose_decimal_units`` (measured, not assumed Unicode literals).

    Returns ``""`` when ``missing_len`` is zero (nothing was trimmed).
    """
    if missing_len < 0:
        raise ValueError(
            f"decimal_reservation_pad: missing_len must be >= 0, got {missing_len!r}"
        )
    if missing_len == 0:
        return ""
    # How many decimal digit positions were trimmed (the dot is one of the
    # missing chars when has_dot is False).
    digit_pad_count = missing_len - (0 if has_dot else 1)
    return digit_unit * digit_pad_count + ("" if has_dot else dot_unit)


def build_decimal_pad_table(
    precision: int,
    digit_unit: str,
    dot_unit: str,
) -> tuple[str, ...]:
    """Pre-built pad table for a fixed-point format with the given precision.

    Returns a tuple of length ``precision + 2`` where index ``i`` is the
    trailing pad for ``missing_len == i`` (0..precision+1).

    - Index 0: nothing trimmed -> ``""``
    - Indices 1..precision: ``i`` fractional digits trimmed, dot survived
    - Index precision+1: all fractional digits *and* the dot trimmed (whole number)

    ``digit_unit`` and ``dot_unit`` must come from ``compose_decimal_units``.
    """
    return tuple(
        decimal_reservation_pad(i, i < precision + 1, digit_unit, dot_unit)
        for i in range(precision + 2)
    )


def fractional_digit_count(num_str: str) -> int:
    """Digit-only count of ``num_str``'s fractional part (after the first ".").

    The ONE definition of "how many fractional digits does this formatted
    string show" every pad-table builder and selector shares --
    ``decimal_pad_for`` below, and (mirrored) every pad-table builder in
    ``render/chart/support_table_attachment.py``. Only digit characters
    count, deliberately: a trailing non-digit character after the dot --
    accounting-sign format's closing ")" (``"(2.5)"`` -> "5)"), or a
    significant-figures spec's magnitude suffix letter (``"4.00M"`` -> "00M")
    -- would otherwise corrupt the depth measurement. Counting only digits
    is what lets this same function serve a plain fixed-point spec and a
    significant-figures spec identically, regardless of *why* a value
    trimmed short.
    """
    return sum(1 for c in num_str.partition(".")[2] if c.isdigit())


def decimal_pad_for(pad_table: tuple[str, ...], num_str: str) -> str:
    """Select the trailing pad for ``num_str`` from a pre-built ``pad_table``.

    Returns the pad string to append to ``num_str`` before measurement or
    paint.  ``pad_table`` must be non-empty (the caller already guards on
    ``if pad_table``); raises ``IndexError`` when the formatted value has more
    fractional digits than the table was built for.

    ``precision = len(pad_table) - 2`` is implicit: the table always has
    ``precision + 2`` entries (indices 0..precision+1).
    """
    precision = len(pad_table) - 2
    frac = fractional_digit_count(num_str)
    if frac > precision:
        raise IndexError(
            f"pad_table precision {precision} < formatted value fractional depth {frac}"
        )
    return pad_table[precision - frac if frac else precision + 1]


def _digit_spec(parsed: FormatSpec, precision: int) -> str:
    """Build a fixed-point, trim-enabled, comma-grouped, symbol-stripped d3
    spec at ``precision`` from an already-parsed spec.

    Shared by ``ruler_digit_format``, ``column_digit_format``, and
    ``non_compacting_tick_format``'s fixed-point path — the three differ only in
    how they arrive at ``precision``, not in the spec shape they build from it.
    """
    return str(
        dataclasses.replace(
            parsed,
            type="f",
            precision=precision,
            trim=True,
            comma=True,
            symbol="",
            width=None,
            zero=False,
        )
    )


def ruler_digit_format(format_spec: str) -> tuple[str, str]:
    """Split an axis's SI format spec into (currency prefix, digit-only spec).

    Shared by the ruler's resolve-time bake (``build_resolved_axis``, which
    calls this once per axis and stores the result on ``ResolvedRulerAxis``)
    and by ``quantitative_tick_labels``'s ``ruler is None`` fallback — one
    computation, read everywhere, so the digit spec an axis paints and the
    one its gutter is measured against can never drift apart.

    The digit spec always types as fixed-point (``f``), never SI (``s``) —
    the caller has already divided the value by the shared magnitude, so
    feeding the scaled result back through d3's own ``s`` type would let it
    pick a *second* suffix on top of the caller's own (a repeat-mode scaled
    value of 1000 would render "1k", not "1,000"). One decimal place is
    always enough: ``nice_tick_values``'s tier-divisibility rule (above)
    permits only a whole or a half-tier step, so a scaled tick is never more
    than one decimal deep. The currency symbol is split out here, not
    formatted in — it anchors on a single tick, which is a per-tick decision
    neither this function nor its caller's spec-parsing step can make alone.
    """
    parsed = _d3_parse(format_spec)
    digit_spec = _digit_spec(parsed, 1)
    return parsed.symbol, digit_spec


def column_digit_format(format_spec: str, finest_scaled: float) -> tuple[str, str]:
    """Split a table column's SI format spec into (currency prefix, digit-only spec).

    Mirrors ``ruler_digit_format``'s split, but the decimal precision is not
    hardcoded to one place. ``ruler_digit_format`` can hardcode precision=1
    because ``nice_tick_values``'s tier-divisibility rule guarantees a scaled
    *tick* is never more than one decimal deep. A table column's raw row
    values carry no such guarantee: within one shared SI tier, a scaled value
    legitimately ranges from 1 to just under 1000 (tier=M covers
    1,000,000-999,999,999, so value/1e6 ranges 1.0-999.999), and a fixed
    1-decimal spec would under- or over-represent precision depending on how
    many integer digits a given row's scaled value has.

    ``finest_scaled`` must be the column's *smallest* non-zero scaled value
    (fewest integer digits), not its largest. That row needs the *most*
    decimals to hit the format's significant-figure count, so pinning
    precision to it guarantees no row in the column undershoots its own sig
    figs -- the same "decide once" pattern ``anchor_at_start`` uses for axis
    rulers, applied to the opposite extreme: rows with *more* integer digits
    then show more precision than their own significant-figure count
    strictly needs (harmless extra precision, never wrong), and the
    resulting alignment gap is exactly what ``decimal_pad_table``
    compensates for, as it already does for axis rulers. Passing the
    largest value instead silently truncates every smaller row (e.g. a
    column's 3.46 rounding to a bare "3").
    """
    parsed = _d3_parse(format_spec)
    sig_figs = parsed.precision if parsed.precision is not None else 6
    decimal_precision = max(0, sig_figs - _decimal_exponent(finest_scaled))
    digit_spec = _digit_spec(parsed, decimal_precision)
    return parsed.symbol, digit_spec


def _precision_for_step(step: float) -> int:
    """Decimal places ``step`` needs written out in full.

    ``nice_tick_values`` only ever emits steps of the form
    ``{1, 2, 5} * 10**k``, so a step based at zero has an exact decimal depth —
    there is no repeating-fraction case to guard against. Rounds to
    ``_MAX_STEP_PRECISION`` places first so that step's own trailing
    binary-float noise doesn't inflate the count. A step read off a ladder
    based far from zero keeps its residue (0.01 near 1e7 is 0.00999999977…, ten
    places deep), and the count — and the labels — follow it.

    Raises on a step finer than that grid: the count would come back too small,
    and the fixed-point spec built from it prints neighboring ticks as the same
    string (all of them ``"0"`` at the bottom end). Callers choosing a ladder's
    spec ask ``_fixed_point_reaches`` first rather than catching this.
    """
    if not _fixed_point_reaches(step):
        raise ValueError(
            f"step {step!r} is not expressible in {_MAX_STEP_PRECISION} decimal "
            "places; a fixed-point spec derived from it collapses neighboring "
            "ticks to one string"
        )
    text = f"{round(step, _MAX_STEP_PRECISION):.{_MAX_STEP_PRECISION}f}".rstrip("0")
    return len(text.split(".")[1]) if "." in text else 0


def _fixed_point_reaches(step: float) -> bool:
    """Whether a fixed-point spec can express ``step`` at all.

    One unit of the finest grid ``_MAX_STEP_PRECISION`` can write is the floor.
    A step under it either rounds away to nothing (1e-11, whose ticks would all
    print "0") or rounds onto a coarser step than its own (5e-11 onto 1e-10),
    whose spec prints two neighboring ticks identically. A zero step — two
    equal rungs — is below it too, and is likewise no step to derive from.

    Deliberately absolute, not a relative comparison against the rounding's own
    error: on a ladder based in the millions a 0.01 step is really
    0.00999999977…, and reading that residue relatively calls an ordinary
    money axis unreachable and sends it to scientific.
    """
    return abs(step) >= 10.0**-_MAX_STEP_PRECISION


def with_symbol(digit_spec: str, symbol: str) -> str:
    """Reinsert a currency symbol into a digit-only d3 spec.

    Inverts the symbol-removal done by ``ruler_digit_format`` /
    ``non_compacting_tick_format`` so d3-format's own sign-before-symbol
    placement applies:
    ``format("$,.0f")(-500)`` → ``"-$500"``, not ``"$" + format(",.0f")(-500)``
    → ``"$-500"``.  Call only on a tick that carries the symbol (the anchor
    tick, or every non-zero tick in repeat mode); a tick that doesn't is
    already correct without it.
    """
    return str(dataclasses.replace(_d3_parse(digit_spec), symbol=symbol))


def non_compacting_tick_format(
    format_spec: str, step: float
) -> tuple[str, str, int | None]:
    """The spec a ladder that does NOT compact paints with: (currency prefix,
    digit-only spec, fixed-point precision).

    Returns ``(prefix, digit_spec, precision)`` where ``prefix`` is the currency
    symbol (e.g. ``"$"``) or an empty string when the format has no symbol, and
    ``digit_spec`` is the symbol-stripped d3 spec. ``precision`` is the number
    of decimal places a fixed-point spec uses (the step-derived value encoded
    in the spec, returned so callers need not import the private
    ``_precision_for_step``), and ``None`` for the scientific register below,
    which has no fixed decimal position and therefore no decimal pad table to
    build.

    Every non-compacting ladder gets a spec from here, at any magnitude. Two
    registers, split where fixed point stops working rather than where the
    numbers get small:

    - Fixed point wherever ``_fixed_point_reaches`` the step, sub-unit ladders
      included -- 0.3 writes out as ``0.3``.
    - Scientific below that, where a fixed-point spec collapses neighboring
      ticks to one string. It keeps the format's own significant-figure count,
      so a pico ladder reads ``1e-11`` / ``2e-11``.

    Mirrors ``ruler_digit_format``'s split so the same anchor-only prefix
    convention applies to non-compacting ladders: there IS a meaningful
    anchor tick (the magnitude-extreme), and the currency symbol disambiguates
    nothing when repeated on every tick -- $8,000 / 6,000 / 4,000 ... reads
    the same as $8,000 / $6,000 / $4,000 ..., while the anchor-only form
    is the established dbt charts convention for both modes.

    Unlike ``ruler_digit_format`` (precision always 1, for an already-scaled
    value), precision here comes from ``step`` -- a non-compacting ladder writes
    its raw, unscaled values, so a half-step (0.5, 1.5, ...) must not round away.
    """
    parsed = _d3_parse(format_spec)
    if not _fixed_point_reaches(step):
        scientific = dataclasses.replace(
            parsed, type="e", trim=True, comma=False, symbol="", width=None, zero=False
        )
        return parsed.symbol, str(scientific), None
    precision = _precision_for_step(step)
    return parsed.symbol, _digit_spec(parsed, precision), precision


def sub_unit_digit_format(format_spec: str) -> str:
    """The spec a ladder-less axis's sub-1 ticks paint with -- ``non_compacting_
    tick_format``'s sibling for the one case it cannot reach (see its
    docstring): with no tick ladder, there is no step to derive a fixed-point
    precision from, only the axis's own SI spec.

    Swaps the SI ('s') type for 'r' (rounds to significant digits, no
    magnitude suffix) at the same precision, keeping every other spec detail
    (symbol, comma, sign) the author's own. ``~r`` and ``~s`` agree on every
    digit printed for a value below 1 -- the only difference is the suffix
    ``s`` would otherwise add (d3's own milli/micro/nano/pico prefixes, which
    read as the house's magnitude suffixes above thousands). Always trims,
    same as ``_digit_spec``, so a value that needs fewer than the full
    precision doesn't pad trailing zeros.

    Unlike ``non_compacting_tick_format``/``ruler_digit_format``, the symbol
    is left in the spec rather than split into a separate prefix: those two
    exist to anchor a currency symbol on one tick in a column of many
    (``anchor_at_start``), but a sub-1 value carries no such shared-scale
    decision to anchor -- each tick already stands on its own, so it can
    just carry its own symbol.

    Has its own scientific-register sibling, ``sub_unit_scientific_format``,
    for a tick too small for this register to reach without an excessive run
    of leading zeros -- see that function and ``SUB_UNIT_SCIENTIFIC_FLOOR``.
    """
    parsed = _d3_parse(format_spec)
    precision = parsed.precision if parsed.precision is not None else 6
    return str(dataclasses.replace(parsed, type="r", precision=precision, trim=True))


# The magnitude below which `sub_unit_digit_format`'s significant-digit
# register needs `sub_unit_scientific_format`'s exponential one instead.
# Unlike `_fixed_point_reaches`'s own boundary (`non_compacting_tick_format`'s
# fixed-point/scientific split for a real ladder step), the `r` type does not
# collapse neighboring ticks by magnitude the way a fixed-point spec does --
# it stays precise at any depth, just at the cost of an excessive run of
# leading zeros. This reuses `_MAX_STEP_PRECISION` anyway, for parity with
# that split rather than a second, independently-chosen legibility floor: one
# number governing "how many leading zeros is too many" across both
# registers.
SUB_UNIT_SCIENTIFIC_FLOOR: float = 10.0**-_MAX_STEP_PRECISION


def sub_unit_scientific_format(format_spec: str) -> str:
    """The scientific-register sibling of ``sub_unit_digit_format``, for a
    ladder-less axis's tick too small for significant digits to reach
    without an excessive run of leading zeros -- mirrors
    ``non_compacting_tick_format``'s own fixed-point/scientific split
    (``_fixed_point_reaches``), applied to a tick's own magnitude directly
    since there is no ladder step here to check it against instead.
    ``inject_axis_numeral_expr`` selects between the two registers at
    ``SUB_UNIT_SCIENTIFIC_FLOOR``.

    Drops comma grouping (meaningless in exponential notation -- d3 itself
    ignores it for the ``e`` type) but otherwise keeps the same spec details
    ``sub_unit_digit_format`` does, including the currency symbol, for the
    same reason: each tick paints alone, with no anchor tick to split it
    onto.
    """
    parsed = _d3_parse(format_spec)
    precision = parsed.precision if parsed.precision is not None else 6
    return str(
        dataclasses.replace(
            parsed, type="e", precision=precision, trim=True, comma=False
        )
    )
