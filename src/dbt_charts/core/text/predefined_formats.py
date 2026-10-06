"""Engine-owned predefined format vocabulary.

Every format the engine treats as a semantic name -- with house rules (notation
registers, round-aware trim, native formatters) -- lives here. Format values
from three sources:

| source          | behavior                                                  |
|-----------------|-----------------------------------------------------------|
| enum member     | house rules: engine spec + notation register + trim       |
| style.formats   | native d3: literal spec, no post-processing               |
| inline d3 str   | native d3: literal spec, no post-processing               |

House glyphs (MINUS U+2212, NULL_DISPLAY em-dash) stay universal for ALL
format sources. They are chart typography, not format semantics -- a user alias
should not resurrect ASCII hyphen-minus in one cell of a table whose other
cells use the proper glyph.

A style.formats key equal to an ALL_PREDEFINED_NAMES member redefines that name
for the board: the table is consulted first, and the redefined name is a native
d3 spec (no house rules) of the same kind as the name it replaces.

NULL_DISPLAY and MINUS are intentionally re-declared here rather than imported
from format_d3 to avoid a circular import (format_d3 imports PREDEFINED_NUMBER_NAMES
from this module).
"""

from __future__ import annotations

from collections.abc import Callable
from enum import Enum
from typing import Any

# Same glyph constants as format_d3 -- declared here to break the circular
# import (format_d3 imports PREDEFINED_NUMBER_NAMES from this module).
_NULL_DISPLAY = "—"  # em-dash — the house null sentinel
_MINUS = "−"  # minus sign − replacing ASCII '-'


def _with_minus(s: str) -> str:
    return s.replace("-", _MINUS)


class PredefinedNumberFormat(str, Enum):
    """Engine-owned number format names.

    Each member triggers house rules (notation registers, round-aware trim,
    or a native formatter that bypasses d3 entirely). Authors write the member
    name in format: fields; the engine owns what it produces.

    Members percent_number / percent_number_delta / percentage_points_delta
    bypass d3 because the value IS the whole-number percent or point delta
    (d3's % type multiplies by 100, which is wrong for these).

    __str__ returns the string value (not "ClassName.member") so members pass
    cleanly through code paths that call str() on format specs. StrEnum does the
    same but only exists in Python 3.11+; this shim covers 3.10.
    """

    def __str__(self) -> str:
        return str.__str__(self)

    currency = "currency"
    currency_whole = "currency_whole"
    currency_full = "currency_full"
    percent = "percent"
    percent_whole = "percent_whole"
    percent_delta = "percent_delta"
    integer = "integer"
    delta = "delta"
    number = "number"
    number_full = "number_full"
    year = "year"
    percent_number = "percent_number"
    percent_number_delta = "percent_number_delta"
    percentage_points_delta = "percentage_points_delta"


class PredefinedTimeFormat(str, Enum):
    """Engine-owned time format names.

    date_short is the theme-mandated default for temporal table cells. It is
    referenced by name from the theme's axis_quantitative.labels.format and
    read directly by table rendering. Absence raises at compile.

    time_short is the house alias for a bare 24-hour clock reading
    ("%H:%M") — the sub-day sibling of date_short. Also the format the
    time-notation engine default emits for a continuous temporal axis
    authored with ``clock: 24`` (see
    ``time_unit_detect.default_subday_label_expr_for``).
    """

    def __str__(self) -> str:
        return str.__str__(self)

    date_short = "date_short"
    time_short = "time_short"


# Engine-owned d3 specs for each predefined number format member.
# Members with a PREDEFINED_NATIVE entry bypass this table entirely.
#
# The SI members carry an explicit `.3` because d3's `s` type defaults to six
# significant digits and `~` only trims trailing zeros -- a bare `~s` renders
# 50752.9 as `50.7529 K`, six significant figures under a name that promises
# brevity. Three, not two: `.2~s` rounds 999 up to a false `1 K`.
PREDEFINED_SPECS: dict[str, str] = {
    PredefinedNumberFormat.currency: "$.3~s",
    PredefinedNumberFormat.currency_whole: "$,.0f",
    PredefinedNumberFormat.currency_full: "$,.2f",
    PredefinedNumberFormat.percent: ".1%",
    PredefinedNumberFormat.percent_whole: ".0%",
    PredefinedNumberFormat.percent_delta: "+.1%",
    PredefinedNumberFormat.integer: ",.0f",
    PredefinedNumberFormat.delta: "+,d",
    PredefinedNumberFormat.number: ".3~s",
    PredefinedNumberFormat.number_full: ",.2f",
    PredefinedNumberFormat.year: "d",
}

# Predefined members whose SI spec is wrong below si_sub_unit_floor: money
# has no sub-cent unit to name, so a value under $1 misreads -- $0.67 as
# "$670m" (d3's SI milli prefix), colliding case-only with the house million
# grammar and reading as 670 million dollars. Below the floor, the member
# falls back to this plain-digit sibling instead of its SI spec.
PREDEFINED_SUB_UNIT_FALLBACK: dict[str, str] = {
    PredefinedNumberFormat.currency.value: PredefinedNumberFormat.currency_full.value,
    PredefinedNumberFormat.number.value: PredefinedNumberFormat.number_full.value,
}


# Money-affix detection for si_sub_unit_floor: deliberately small and
# explicit, kept next to the predicate that uses it. Currency symbols glued
# directly onto a value, and the major ISO 4217 codes a FormatConfig
# prefix/suffix commonly spells out (` USD` is that field's own documented
# example).
_MONEY_SYMBOLS = {"$", "£", "€", "¥", "₹", "₩", "₽"}
_MONEY_CODES = {"USD", "GBP", "EUR", "JPY", "CAD", "AUD", "CHF", "CNY", "INR"}


def _affix_is_money(affix: str | None) -> bool:
    stripped = affix.strip() if affix else ""
    if not stripped:
        return False
    return any(symbol in stripped for symbol in _MONEY_SYMBOLS) or (
        stripped.upper() in _MONEY_CODES
    )


# The smallest amount `$,.2f` renders as a nonzero string. Money below this is
# left on SI: the two-decimal fallback would paint it "$0.00".
MONEY_SUB_UNIT_FLOOR = 0.005


def si_sub_unit_floor(
    spec: str, prefix: str | None = None, suffix: str | None = None
) -> float:
    """The lower edge of the sub-$1 band in which an adaptive member avoids SI.

    Used at the call site as ``floor < abs(value) < 1.0`` -- a fixed 1.0
    ceiling, not a general adaptive band. Fixing "$13" for $12.99 needs a
    per-set vote over the whole slot (axis, column, ...) and is tracked
    separately (closed PR #7854); this predicate is the narrower per-value
    fix for amounts under a dollar, where SI is not imprecise but wrong.

    ``MONEY_SUB_UNIT_FLOOR`` (0.005) for money, which has no sub-cent unit to
    name: d3's SI ``m`` (milli) is not in the house suffix vocabulary
    (``_D3_TO_ANALYTIC`` keys are k/M/G/T/P/E/Z/Y), so it stays glued inside
    the number as "$670m" rather than lifting into the suffix lane.

    The floor is 0.005 rather than 0.0 because the fallback it swaps to is
    ``$,.2f``, which cannot represent less than half a cent: below that it
    renders "$0.00" for every row of a CPC, per-token or FX column, which is
    indistinguishable from the genuine zeros this same band is careful to
    leave as "$0". Confining the swap to the range the fallback can actually
    represent keeps sub-cent money on SI, where it is at least readable.

    ``1.0`` for a plain quantity, whose fraction the Python-painted KPI and
    table surfaces still print as an SI sub-unit -- ``0.671`` as "671m",
    d3's milli. An axis no longer does: a non-compacting tick ladder writes
    its digits out at any magnitude (``non_compacting_tick_format``,
    ``numeral_scale``), so read this arm as those surfaces' own position, not
    a shared one.

    Money is a resolved spec carrying a literal ``$``, or a prefix/suffix
    that contains a known currency symbol or spells out a major ISO 4217
    code (``_MONEY_SYMBOLS`` / ``_MONEY_CODES`` above). This is a heuristic
    over a small, explicit set -- it cannot enumerate the world's
    currencies, and it does not try to. Money detection here has two ways
    to be wrong, and only one of them is survivable: calling a real
    currency "not money" just keeps today's SI rendering (a value like
    ``0.002 kg`` printing an odd-looking ``2m kg`` is a cosmetic miss, and
    that same miscall on money is the acceptable "$13 from $12.99" case
    already tracked separately). Calling a plain quantity "money" runs it
    through the exact-two-decimal fallback and truncates it -- ``0.002 kg``
    would render as ``0.00 kg``, discarding a real value. So whenever this
    predicate cannot positively identify money, it must decline the floor
    and fall through to SI -- never the other way around.
    """
    is_money = "$" in spec or _affix_is_money(prefix) or _affix_is_money(suffix)
    return MONEY_SUB_UNIT_FLOOR if is_money else 1.0


# Engine-owned d3 time specs for each predefined time format member.
PREDEFINED_TIME_SPECS: dict[str, str] = {
    PredefinedTimeFormat.date_short: "%-d %b %Y",
    PredefinedTimeFormat.time_short: "%H:%M",
}

# Native formatters: bypass d3 entirely. The value already IS in the caller's
# unit (whole-number percent, point delta). Each returns (number_str, unit_str)
# so callers that split prefix/number/suffix continue to work unchanged.
PREDEFINED_NATIVE: dict[str, Callable[[Any], tuple[str, str]]] = {
    PredefinedNumberFormat.percent_number: lambda v: (
        (_NULL_DISPLAY, "") if v is None else (_with_minus(f"{float(v):.1f}"), "%")
    ),
    PredefinedNumberFormat.percent_number_delta: lambda v: (
        (_NULL_DISPLAY, "") if v is None else (_with_minus(f"{float(v):+.1f}"), "%")
    ),
    PredefinedNumberFormat.percentage_points_delta: lambda v: (
        (_NULL_DISPLAY, "") if v is None else (_with_minus(f"{float(v):+.1f}"), " pts")
    ),
}

# Fast membership sets -- use these for string-key checks rather than
# constructing enum members (Pydantic does NOT coerce str to str-Enum when
# | str is present in the union, so runtime detection must use these sets).
PREDEFINED_NUMBER_NAMES: frozenset[str] = frozenset(
    m.value for m in PredefinedNumberFormat
)
PREDEFINED_TIME_NAMES: frozenset[str] = frozenset(m.value for m in PredefinedTimeFormat)
ALL_PREDEFINED_NAMES: frozenset[str] = PREDEFINED_NUMBER_NAMES | PREDEFINED_TIME_NAMES
# Native members bypass d3 entirely — valid only in Python-painted slots (KPI,
# table). Vega-painted slots (axis labels, mark labels, number_format, time_format,
# support_table) must reject these at compile; Vega has no equivalent renderer.
PREDEFINED_NATIVE_NAMES: frozenset[str] = frozenset(PREDEFINED_NATIVE)

# Every PredefinedNumberFormat member must resolve: either a d3 spec in
# PREDEFINED_SPECS or a native formatter in PREDEFINED_NATIVE. A member without
# an entry passes validate/formats.py (which whitelists ALL_PREDEFINED_NAMES)
# but causes _d3_format("<name>") to raise deep in render — exactly the silent
# failure validate-and-error-fast exists to prevent.
_unresolvable = PREDEFINED_NUMBER_NAMES - (
    frozenset(PREDEFINED_SPECS) | frozenset(PREDEFINED_NATIVE)
)
assert not _unresolvable, (
    f"PredefinedNumberFormat members with no resolver: {_unresolvable!r}. "
    f"Add to PREDEFINED_SPECS or PREDEFINED_NATIVE."
)
