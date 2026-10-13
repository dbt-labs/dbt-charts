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

import json
import math
from collections.abc import Callable
from enum import StrEnum
from typing import Any, NamedTuple

from d3_format import format as _d3_format

# Same glyph constants as format_d3 -- declared here to break the circular
# import (format_d3 imports PREDEFINED_NUMBER_NAMES from this module).
_NULL_DISPLAY = "—"  # em-dash — the house null sentinel
_MINUS = "−"  # minus sign − replacing ASCII '-'


def _with_minus(s: str) -> str:
    return s.replace("-", _MINUS)


class PredefinedNumberFormat(StrEnum):
    """Engine-owned number format names.

    Each member triggers house rules (notation registers, round-aware trim,
    or a native formatter that bypasses d3 entirely). Authors write the member
    name in format: fields; the engine owns what it produces.

    Members percent_number / percent_number_delta / percentage_points_delta
    bypass d3 because the value IS the whole-number percent or point delta
    (d3's % type multiplies by 100, which is wrong for these).
    """

    currency = "currency"
    currency_whole = "currency_whole"
    currency_full = "currency_full"
    currency_compact = "currency_compact"
    percent = "percent"
    percent_whole = "percent_whole"
    percent_delta = "percent_delta"
    integer = "integer"
    delta = "delta"
    number = "number"
    number_full = "number_full"
    compact = "compact"
    year = "year"
    percent_number = "percent_number"
    percent_number_delta = "percent_number_delta"
    percentage_points_delta = "percentage_points_delta"
    id = "id"
    duration = "duration"
    bytes = "bytes"
    latitude = "latitude"
    longitude = "longitude"
    rating = "rating"


class PredefinedTimeFormat(StrEnum):
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

    date_short = "date_short"
    time_short = "time_short"
    date_year = "date_year"
    date_quarter = "date_quarter"
    date_month = "date_month"
    date_week = "date_week"


# The date grain each ``date_<grain>`` name labels, keyed by the calendar unit
# ``detect_time_unit`` returns. An engine ``date_<grain>`` carries no strftime
# spec: it means the engine's own labels for that grain (a year context row,
# fiscal quarter numbering), which no single spec expresses. Only a board's
# redefinition is a spec.
GRAIN_FORMAT_NAMES: dict[str, str] = {
    "year": PredefinedTimeFormat.date_year,
    "yearquarter": PredefinedTimeFormat.date_quarter,
    "yearmonth": PredefinedTimeFormat.date_month,
    "yearweek": PredefinedTimeFormat.date_week,
}
GRAIN_NAMES: frozenset[str] = frozenset(GRAIN_FORMAT_NAMES.values())


class PredefinedSpec(NamedTuple):
    """An engine d3 spec and the unit glued after the digits (``°``)."""

    d3: str
    suffix: str = ""
    # Names a kind of value (an id, a rating, a coordinate) rather than a look,
    # so another name may share its spec without the two being interchangeable.
    kind: bool = False


class PredefinedTextFormat(StrEnum):
    """Engine-owned text format names.

    The value prints unchanged; the name tells a surface that takes links
    (table cell, KPI value) what the text is. Context-free: an axis or tooltip
    prints the text. Has no spec, so ``style.formats`` cannot redefine it.
    """

    email = "email"
    url = "url"


# Engine-owned d3 specs for each predefined number format member.
# Members with a PREDEFINED_NATIVE entry bypass this table entirely.
#
# The SI members carry an explicit `.3` because d3's `s` type defaults to six
# significant digits and `~` only trims trailing zeros -- a bare `~s` renders
# 50752.9 as `50.7529K`, six significant figures under a name that promises
# brevity. Three, not two: `.2~s` rounds 999 up to a false `1K`. The `compact`
# members are the deliberate two-figure exception: "1.2M" and "45K" for a
# headline where the third digit is noise.
PREDEFINED_SPECS: dict[str, PredefinedSpec] = {
    PredefinedNumberFormat.currency: PredefinedSpec("$.3~s"),
    PredefinedNumberFormat.currency_whole: PredefinedSpec("$,.0f"),
    PredefinedNumberFormat.currency_full: PredefinedSpec("$,.2f"),
    PredefinedNumberFormat.currency_compact: PredefinedSpec("$.2~s"),
    PredefinedNumberFormat.percent: PredefinedSpec(".1%"),
    PredefinedNumberFormat.percent_whole: PredefinedSpec(".0%"),
    PredefinedNumberFormat.percent_delta: PredefinedSpec("+.1%"),
    PredefinedNumberFormat.integer: PredefinedSpec(",.0f"),
    PredefinedNumberFormat.delta: PredefinedSpec("+,d"),
    PredefinedNumberFormat.number: PredefinedSpec(".3~s"),
    PredefinedNumberFormat.number_full: PredefinedSpec(",.2f"),
    PredefinedNumberFormat.compact: PredefinedSpec(".2~s"),
    PredefinedNumberFormat.year: PredefinedSpec("d"),
    PredefinedNumberFormat.id: PredefinedSpec("d", kind=True),
    # `~` so a whole-degree axis tick reads `−122°`, not `−122.0000°`.
    PredefinedNumberFormat.latitude: PredefinedSpec(".4~f", "°", kind=True),
    PredefinedNumberFormat.longitude: PredefinedSpec(".4~f", "°", kind=True),
    PredefinedNumberFormat.rating: PredefinedSpec(".1f", kind=True),
}

# Predefined SI members never print an SI prefix below 1: d3's milli ("m")
# collides case-only with the house million grammar, so $0.67 as "$670m" or
# 0.2 as "200m" reads as a magnitude. From SUB_UNIT_FLOOR up to 1, the member
# falls back to this plain-digit sibling.
PREDEFINED_SUB_UNIT_FALLBACK: dict[str, str] = {
    PredefinedNumberFormat.currency.value: PredefinedNumberFormat.currency_full.value,
    PredefinedNumberFormat.number.value: PredefinedNumberFormat.number_full.value,
    PredefinedNumberFormat.currency_compact.value: PredefinedNumberFormat.currency_full.value,
    PredefinedNumberFormat.compact.value: PredefinedNumberFormat.number_full.value,
}

# Money-affix detection for the slot vote: deliberately small and explicit.
# Currency symbols glued directly onto a value, and the major ISO 4217 codes a
# FormatConfig prefix/suffix commonly spells out (` USD` is that field's own
# documented example). When money cannot be positively identified the answer is
# "not money": a plain quantity must never be truncated to two decimals.
_MONEY_SYMBOLS = {"$", "£", "€", "¥", "₹", "₩", "₽"}
_MONEY_CODES = {"USD", "GBP", "EUR", "JPY", "CAD", "AUD", "CHF", "CNY", "INR"}


def _affix_is_money(affix: str | None) -> bool:
    stripped = affix.strip() if affix else ""
    if not stripped:
        return False
    return any(symbol in stripped for symbol in _MONEY_SYMBOLS) or (
        stripped.upper() in _MONEY_CODES
    )


def is_money_format(spec: str, prefix: str | None, suffix: str | None) -> bool:
    """A spec carrying a literal ``$``, or an affix naming a currency."""
    return "$" in spec or _affix_is_money(prefix) or _affix_is_money(suffix)


# The smallest amount the two-decimal sibling renders as a nonzero string.
# Below it the member paints significant digits (scientific past
# SUB_UNIT_SCIENTIFIC_FLOOR) instead, so a CPC, per-token or FX column does not
# read as a column of zeros.
SUB_UNIT_FLOOR = 0.005


# Engine-owned d3 time specs for each predefined time format member.
PREDEFINED_TIME_SPECS: dict[str, str] = {
    PredefinedTimeFormat.date_short: "%-d %b %Y",
    PredefinedTimeFormat.time_short: "%H:%M",
}


class NativeFormat(NamedTuple):
    """A format that bypasses d3. ``python`` paints Python slots (KPI, table);
    ``vega``, when present, is the matching Vega expression over a value
    expression, which makes the name valid in Vega-painted slots too."""

    python: Callable[[Any], tuple[str, str]]  # type-state: explicit_any — a raw cell
    vega: Callable[[str], str] | None = None


_DURATION_UNITS = ((86400, "d"), (3600, "h"), (60, "m"), (1, "s"))
# Each unit with the one below it, which a value shows beside it.
_DURATION_PAIRS = tuple(zip(_DURATION_UNITS, _DURATION_UNITS[1:], strict=False))


def _duration(v: Any) -> tuple[str, str]:  # type-state: explicit_any — a raw cell
    """Two largest units of a span in seconds, a zero lower unit dropped
    (``1h 23m``, ``45s``); the fraction rounds half up, as the Vega arm does."""
    if v is None or not math.isfinite(float(v)):
        return _NULL_DISPLAY, ""
    seconds = math.floor(abs(float(v)) + 0.5)
    sign = _MINUS if v < 0 and seconds else ""
    for (size, unit), (lower_size, lower_unit) in _DURATION_PAIRS:
        if seconds >= size:
            lower = seconds % size // lower_size
            tail = f" {lower}{lower_unit}" if lower else ""
            return f"{sign}{seconds // size}{unit}{tail}", ""
    return f"{sign}{seconds}s", ""


def _duration_vega(value_expr: str) -> str:
    """``_duration``, term for term."""
    t = f"floor(abs({value_expr}) + 0.5)"
    expr = f"{t} + 's'"
    for (size, unit), (lower_size, lower_unit) in reversed(_DURATION_PAIRS):
        lower = f"floor({t} % {size} / {lower_size})"
        head = f"floor({t} / {size}) + '{unit}'"
        tail = f"({lower} ? ' ' + {lower} + '{lower_unit}' : '')"
        expr = f"({t} >= {size} ? {head} + {tail} : {expr})"
    sign = f"({value_expr} < 0 && {t} >= 1 ? {json.dumps(_MINUS)} : '')"
    return f"{sign} + {expr}"


_SI_PREFIXES = "kMGTPEZY"


def _bytes(v: Any) -> tuple[str, str]:  # type-state: explicit_any — a raw cell
    """Decimal SI (1 kB = 1000 B) to three significant digits, the unit spaced
    from the number at every magnitude: ``950 B``, ``1.53 MB``."""
    if v is None or not math.isfinite(float(v)):
        return _NULL_DISPLAY, ""
    x = float(v)
    # Under one byte d3's SI would reach for milli.
    text = _d3_format(".3~r" if abs(x) < 1 else ".3~s")(x)
    if text[-1] in _SI_PREFIXES:
        return text[:-1], f" {text[-1]}B"
    return text, " B"


def _bytes_vega(value_expr: str) -> str:
    plain = f"format({value_expr}, '.3~r')"
    si = f"format({value_expr}, '.3~s')"
    return f"replace(abs({value_expr}) < 1 ? {plain} : {si}, /([{_SI_PREFIXES}]?)$/, ' $1B')"


def _unit_value(
    spec: str, unit: str
) -> Callable[[Any], tuple[str, str]]:  # type-state: explicit_any — a raw cell
    """A native painting the cell as ``spec`` with ``unit`` glued after it."""

    def paint(v: Any) -> tuple[str, str]:  # type-state: explicit_any — a raw cell
        if v is None:
            return _NULL_DISPLAY, ""
        return _with_minus(format(float(v), spec)), unit

    return paint


# Native formatters: bypass d3 entirely. The value already IS in the caller's
# unit (whole-number percent, point delta, seconds, bytes). Each returns
# (number_str, unit_str) so callers that split prefix/number/suffix continue to
# work unchanged.
PREDEFINED_NATIVE: dict[str, NativeFormat] = {
    PredefinedNumberFormat.percent_number: NativeFormat(_unit_value(".1f", "%")),
    PredefinedNumberFormat.percent_number_delta: NativeFormat(_unit_value("+.1f", "%")),
    PredefinedNumberFormat.percentage_points_delta: NativeFormat(
        _unit_value("+.1f", " pts")
    ),
    PredefinedNumberFormat.duration: NativeFormat(_duration, _duration_vega),
    PredefinedNumberFormat.bytes: NativeFormat(_bytes, _bytes_vega),
}

# Fast membership sets -- use these for string-key checks rather than
# constructing enum members (Pydantic does NOT coerce str to str-Enum when
# | str is present in the union, so runtime detection must use these sets).
PREDEFINED_NUMBER_NAMES: frozenset[str] = frozenset(
    m.value for m in PredefinedNumberFormat
)
PREDEFINED_TIME_NAMES: frozenset[str] = frozenset(m.value for m in PredefinedTimeFormat)
PREDEFINED_TEXT_NAMES: frozenset[str] = frozenset(m.value for m in PredefinedTextFormat)
ALL_PREDEFINED_NAMES: frozenset[str] = (
    PREDEFINED_NUMBER_NAMES | PREDEFINED_TIME_NAMES | PREDEFINED_TEXT_NAMES
)
VEGA_ARMS: dict[str, Callable[[str], str]] = {
    name: native.vega
    for name, native in PREDEFINED_NATIVE.items()
    if native.vega is not None
}
# Natives without a Vega arm are valid only in Python-painted slots (KPI,
# table). Vega-painted slots (axis labels, mark labels, number_format, time_format,
# support_table) must reject these at compile; Vega has no equivalent renderer.
PYTHON_ONLY_NATIVE_NAMES: frozenset[str] = frozenset(PREDEFINED_NATIVE) - frozenset(
    VEGA_ARMS
)

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
# The time analog: a spec, or a grain name meaning the engine's own labels.
_unresolvable_time = PREDEFINED_TIME_NAMES - (
    frozenset(PREDEFINED_TIME_SPECS) | GRAIN_NAMES
)
assert not _unresolvable_time, (
    f"PredefinedTimeFormat members with no resolver: {_unresolvable_time!r}. "
    f"Add to PREDEFINED_TIME_SPECS or GRAIN_FORMAT_NAMES."
)
