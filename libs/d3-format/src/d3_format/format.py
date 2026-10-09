"""d3-format Python implementation.

Implements the d3-format spec with byte-for-byte parity to d3.js.
Key deviations from Python's standard number formatting:
- Negative sign is U+2212 MINUS SIGN, not U+002D HYPHEN-MINUS.
- Rounding uses round-half-up (JavaScript Math.round), not banker's rounding.
- SI type uses significant-digit precision (not decimal-place precision).
- `d` type rounds to nearest integer using round-half-up.
- `n` type is locale-aware `g` with grouping.

``_apply_spec`` follows the shape of d3's own ``format(value)`` closure --
prefix/suffix assembly, the integer/fraction split, grouping, then padding --
because the order of those steps is observable: zero-fill is grouped along with
the digits, and a currency symbol displaces the percent sign.
"""

from __future__ import annotations

import math
from collections.abc import Callable
from dataclasses import dataclass
from decimal import ROUND_HALF_UP, Decimal, localcontext
from typing import TypedDict, overload

from d3_format.spec import FormatSpec, parse

# U+2212 MINUS SIGN — d3 uses this, not hyphen-minus.
_MINUS = "−"
# U+221E INFINITY SYMBOL — d3's d/,d use this for infinite values.
_INF_SYMBOL = "∞"

# SI prefixes: index 8 = '' (10^0), 9 = 'k' (10^3), etc.
_SI_PREFIXES = [
    "y",
    "z",
    "a",
    "f",
    "p",
    "n",
    "µ",
    "m",
    "",
    "k",
    "M",
    "G",
    "T",
    "P",
    "E",
    "Z",
    "Y",
]
_SI_PREFIX_OFFSET = 8  # index of the '' (10^0) entry

# Type letters that select a formatter. Every other letter the grammar admits
# falls back to ".12~g" (see _resolve_type); "n" is rewritten before this is
# consulted, so it is deliberately absent.
_KNOWN_TYPES = frozenset("%bcdefgoprsxX")

# Types whose precision counts significant digits (clamped to [1, 21]) rather
# than digits after the decimal point (clamped to [0, 20]).
_SIGNIFICANT_PRECISION_TYPES = frozenset("gprs")

# Types whose body may carry a fraction or exponent that must be held out of
# thousands-grouping and zero-padding (d3's `maybeSuffix`).
_SPLIT_SUFFIX_TYPES = frozenset("defgprs%")

# Types the "#" alternate form marks with a radix prefix.
_ALTERNATE_FORM_TYPES = frozenset("boxX")

# Radix types → (base, uppercase output).
_RADIX_TYPES: dict[str, tuple[int, bool]] = {
    "b": (2, False),
    "o": (8, False),
    "x": (16, False),
    "X": (16, True),
}

_RADIX_CONVERTERS: dict[int, Callable[[int], str]] = {2: bin, 8: oct, 16: hex}


def _round_half_up(x: float, decimals: int) -> float:
    """Round x to `decimals` decimal places using round-half-up (like JS Math.round).

    Python's built-in round() uses banker's rounding; d3/JS always rounds 0.5 up.
    We use Decimal for exact midpoint detection.
    """
    if not math.isfinite(x) or math.isnan(x):
        return x
    quant = Decimal(
        "1e-" + str(decimals)
    )  # works for any int, including 0 and negative
    # Use Decimal(x) — the exact IEEE-754 binary value — so rounding matches d3/JS.
    # Decimal(repr(x)) would round "2.55" up to "2.6", but d3 rounds it down to "2.5"
    # because the binary value is 2.5499..., not 2.55.
    d = Decimal(x)
    with localcontext() as ctx:
        # quantize raises InvalidOperation when the result needs more digits
        # than the context allows, which the default 28 does not for a large
        # magnitude (1e30 as an integer) or a deep precision (.20f).
        ctx.prec = max(ctx.prec, d.adjusted() + decimals + 2)
        return float(d.quantize(quant, rounding=ROUND_HALF_UP))


def _js_number_to_string(v: float) -> str:
    """Replicate JavaScript's ``String(number)``.

    Python's ``repr`` yields the same shortest-round-trip digits but lays them
    out with different thresholds (``1e-06`` where JS writes ``0.000001``,
    ``255.0`` where JS writes ``255``), so the digits are re-rendered per
    ECMA-262 Number::toString: exponential only outside 10^-6 .. 10^21.
    """
    if math.isnan(v):
        return "NaN"
    if math.isinf(v):
        return "Infinity" if v > 0 else "-Infinity"
    if v == 0:
        return "0"  # JS String(-0) is "0"
    sign = "-" if v < 0 else ""
    parts = Decimal(repr(abs(v))).normalize().as_tuple()
    digits = "".join(str(d) for d in parts.digits)
    k = len(digits)
    # ECMA writes the value as 0.<digits> x 10^n.
    n = int(parts.exponent) + k
    if k <= n <= 21:
        return sign + digits + "0" * (n - k)
    if 0 < n <= 21:
        return sign + digits[:n] + "." + digits[n:]
    if -6 < n <= 0:
        return sign + "0." + "0" * -n + digits
    mantissa = digits if k == 1 else digits[0] + "." + digits[1:]
    exp = n - 1
    return f"{sign}{mantissa}e{'+' if exp >= 0 else '-'}{abs(exp)}"


def _fmt_fixed(abs_val: float, decimals: int) -> str:
    """Format abs_val to `decimals` decimal places with round-half-up."""
    rounded = _round_half_up(abs_val, decimals)
    return f"{rounded:.{decimals}f}"


def _to_radix(abs_val: float, base: int, upper: bool) -> str:
    """Replicate JS ``Math.round(x).toString(base)`` for base 2, 8 or 16."""
    digits = _RADIX_CONVERTERS[base](int(_round_half_up(abs_val, 0)))[2:]
    return digits.upper() if upper else digits


@dataclass(frozen=True)
class FormatLocale:
    """d3's ``formatLocale`` definition. ``grouping=None`` disables grouping."""

    decimal: str = "."
    thousands: str = ","
    grouping: tuple[int, ...] | None = (3,)
    currency: tuple[str, str] = ("$", "")
    numerals: tuple[str, ...] | None = None
    percent: str = "%"
    minus: str = _MINUS
    nan: str = "NaN"

    def format(self, spec_str: str) -> Callable[[float | int | Decimal], str]:
        """d3's ``locale.format(spec)``."""
        spec = parse(spec_str)
        _resolve_type(spec)
        precision = _resolve_precision(spec)
        return lambda v: _apply_spec(spec, precision, v, self)


class LocaleDefinition(TypedDict, total=False):
    """A d3 locale definition, as d3-format's locale JSON files spell it."""

    decimal: str
    thousands: str
    grouping: list[int]
    currency: list[str]
    numerals: list[str]
    percent: str
    minus: str
    nan: str


def format_locale(definition: LocaleDefinition) -> FormatLocale:
    """Build a locale from a d3 locale definition (e.g. d3-format's fr-FR.json).

    Like d3, a definition missing ``grouping`` or ``thousands`` does not group.
    """
    grouping = definition.get("grouping")
    thousands = definition.get("thousands")
    currency = definition.get("currency", ["", ""])
    numerals = definition.get("numerals")
    return FormatLocale(
        decimal=definition.get("decimal", "."),
        thousands=thousands or "",
        grouping=None if grouping is None or thousands is None else tuple(grouping),
        currency=(currency[0], currency[1]),
        numerals=None if numerals is None else tuple(numerals),
        percent=definition.get("percent", "%"),
        minus=definition.get("minus", _MINUS),
        nan=definition.get("nan", "NaN"),
    )


EN_US = FormatLocale()


def _group(value: str, width: float, locale: FormatLocale) -> str:
    """Replicate d3's formatGroup.

    ``width`` caps the grouped length; d3 passes infinity except when zero-fill
    padding is grouped along with the digits, where it bounds the total.
    """
    grouping = locale.grouping
    if grouping is None:
        return value
    i = len(value)
    parts: list[str] = []
    j = 0
    g = grouping[0]
    length = 0
    while i > 0 and g > 0:
        if length + g + 1 > width:
            g = max(1, int(width - length))
        i -= g
        parts.append(value[max(0, i) : i + g])
        length += g + 1
        if length > width:
            break
        j = (j + 1) % len(grouping)
        g = grouping[j]
    parts.reverse()
    return locale.thousands.join(parts)


def _apply_trim(body: str) -> str:
    """Remove trailing zeros (and trailing decimal point) from a formatted number."""
    if "e" in body:
        mantissa, exp_part = body.split("e", 1)
        if "." in mantissa:
            mantissa = mantissa.rstrip("0").rstrip(".")
        return mantissa + "e" + exp_part
    if "." in body:
        return body.rstrip("0").rstrip(".")
    return body


def _format_decimal_parts(x: float, precision: int) -> tuple[str, int] | None:
    """Replicate d3's formatDecimalParts(x, p).

    x must be positive and finite (non-zero).
    Returns (coefficient_digits, exponent) where:
    - coefficient_digits: the p significant-figure string (may be longer if carry)
    - exponent: position of the leading digit (i.e. value ≈ 0.coefficient × 10^(exp+1))

    e.g. formatDecimalParts(1.23, 3) → ("123", 0)
         formatDecimalParts(1234.5, 3) → ("123", 3) (rounded to 3 sig figs)

    Uses Decimal(x) — the exact IEEE-754 binary value — with ROUND_HALF_UP so that
    rounding matches JavaScript's toExponential (d3 rounds the binary, not repr).
    """
    if not math.isfinite(x) or x == 0:
        return None
    p = max(1, precision)
    d = Decimal(x)  # exact binary value — matches JS's toExponential rounding
    adj = d.adjusted()  # exponent of leading digit = floor(log10(|d|))
    # Scale so rounding to nearest integer gives p significant figures.
    scale_exp = p - 1 - adj
    scaled = d * (Decimal(10) ** scale_exp)
    rounded_int = scaled.quantize(Decimal("1"), rounding=ROUND_HALF_UP)
    coefficient = str(abs(int(rounded_int)))
    # If rounding caused a carry (e.g. 9.5 → 10 at p=1), exp_out increases.
    exp_out = adj + (len(coefficient) - p)
    # d3's toExponential(p-1) always returns exactly p significant-digit chars.
    # When a carry adds a digit (e.g. '100' from p=2), truncate to match d3's shape.
    if len(coefficient) > p:
        coefficient = coefficient[:p]
    return coefficient, exp_out


def _format_si(abs_val: float, precision: int) -> tuple[str, str]:
    """Format abs_val with SI prefix to `precision` significant digits.

    Replicates d3's formatPrefixAuto(x, p) algorithm exactly:
    1. Round to p significant digits using exponential notation.
    2. Determine SI prefix exponent from the rounded exponent.
    3. Build body string from coefficient positioned relative to prefix.

    Returns (number_body, prefix_char).
    """
    if abs_val == 0.0:
        # d3 calls x.toPrecision(p) for zero (formatDecimalParts returns null).
        # JS toPrecision(p) on 0 = "0." + "0"*(p-1) for p>=2, "0" for p<=1.
        body = ("0." + "0" * (precision - 1)) if precision > 1 else "0"
        return body, ""

    coefficient, exponent = _format_decimal_parts(abs_val, precision)  # type: ignore[misc]

    # Clamp the level (not the product) to [-8, 8] matching d3-format formatPrefixAuto.js.
    # Clamping the product (-24..24) instead of the level (-8..8) would allow exponents
    # beyond ±24 that exceed the _SI_PREFIXES array bounds → IndexError.
    prefix_exp = max(-8, min(8, math.floor(exponent / 3))) * 3
    prefix = _SI_PREFIXES[prefix_exp // 3 + _SI_PREFIX_OFFSET]

    # Position the decimal point in coefficient.
    # i = exponent - prefix_exp + 1 = number of digits before the decimal point.
    i = exponent - prefix_exp + 1
    n = len(coefficient)

    if i == n:
        body = coefficient
    elif i > n:
        body = coefficient + "0" * (i - n)
    elif i > 0:
        body = coefficient[:i] + "." + coefficient[i:]
    else:
        # i <= 0: value is smaller than 1 in the chosen prefix unit.
        # d3: "0." + new Array(1-i).join("0") + formatDecimalParts(x, max(0,p+i-1))[0]
        # new Array(k).join("0") produces k-1 zeros, so (1-i)-1 = -i zeros.
        new_prec = max(1, precision + i - 1)
        d2 = _format_decimal_parts(abs_val, new_prec)
        coeff2 = d2[0] if d2 else "0"
        body = "0." + "0" * (-i) + coeff2

    return body, prefix


def _js_to_precision(abs_val: float, precision: int) -> str:
    """Replicate JavaScript's Number.prototype.toPrecision(p).

    Uses exponential notation when exponent >= precision or exponent < -6.
    Otherwise uses fixed notation. Rounds using round-half-up.

    This is the core of d3's 'g' and 'n' type formatting.
    """
    # d3 treats toPrecision(0) as toPrecision(1) — g/n types use max(1, p).
    p = max(1, precision)
    if abs_val == 0.0:
        # JS: (0).toPrecision(p) = "0." + "0"*(p-1) for p>=2, "0" for p=1
        return ("0." + "0" * (p - 1)) if p > 1 else "0"

    coefficient, exponent = _format_decimal_parts(abs_val, p)  # type: ignore[misc]

    # JS uses e notation if exponent >= p or exponent < -6.
    if exponent >= p or exponent < -6:
        # Exponential form: coefficient[0].coefficient[1:]e+exponent
        if len(coefficient) > 1:
            mantissa = coefficient[0] + "." + coefficient[1:]
        else:
            mantissa = coefficient[0]
        exp_sign = "+" if exponent >= 0 else "-"
        exp_str = str(abs(exponent))
        return f"{mantissa}e{exp_sign}{exp_str}"
    else:
        # Fixed form: position decimal based on exponent.
        i = exponent + 1  # digits before decimal point
        n = len(coefficient)
        if i >= n:
            # All digits before decimal, may need trailing zeros.
            return coefficient + "0" * (i - n)
        elif i > 0:
            return coefficient[:i] + "." + coefficient[i:]
        else:
            # 0 < abs_val < 1: prepend "0." and leading zeros.
            return "0." + "0" * (-i) + coefficient


def _format_rounded(abs_val: float, precision: int) -> str:
    """Replicate d3's formatRounded(x, p), used by the 'r' and 'p' types.

    Always uses fixed notation (no e), rounds to p significant digits.
    """
    if abs_val == 0.0:
        return "0"

    coefficient, exponent = _format_decimal_parts(abs_val, precision)  # type: ignore[misc]

    i = exponent + 1  # digits before decimal
    n = len(coefficient)
    if i <= 0:
        return "0." + "0" * (-i) + coefficient
    elif n > i:
        return coefficient[:i] + "." + coefficient[i:]
    else:
        return coefficient + "0" * (i - n)


def _format_exponential(abs_val: float, precision: int) -> str:
    """Replicate JS Number.prototype.toExponential(p) for the 'e' type."""
    if abs_val == 0.0:
        mantissa = ("0." + "0" * precision) if precision > 0 else "0"
        return mantissa + "e+0"
    coefficient, exponent = _format_decimal_parts(abs_val, precision + 1)  # type: ignore[misc]
    # Pad to precision+1 digits in case trailing zeros were stripped
    coefficient = coefficient.ljust(precision + 1, "0")
    if precision > 0:
        mantissa = coefficient[0] + "." + coefficient[1 : precision + 1]
    else:
        mantissa = coefficient[0]
    return f"{mantissa}e{'+' if exponent >= 0 else '-'}{abs(exponent)}"


def _format_decimal(abs_val: float) -> str:
    """Replicate d3's formatDecimal, used by the 'd' type."""
    if abs_val >= 1e21:
        # JS formatDecimal uses x.toLocaleString("en") for |x| >= 1e21.
        # str(x) gives the shortest round-trip repr; Decimal(str()) expands
        # exponential form to the "clean" integer string matching JS.
        s = str(abs_val)
        return str(int(Decimal(s))) if ("e" in s or "E" in s) else str(int(abs_val))
    return str(int(_round_half_up(abs_val, 0)))


def _format_type(spec: FormatSpec, abs_val: float, precision: int) -> tuple[str, str]:
    """Replicate d3's ``formatTypes[type](abs_val, precision)``.

    Returns (body, si_prefix); si_prefix mirrors d3's prefixExponent side
    channel and is empty for every type but 's'.
    """
    if math.isinf(abs_val):
        # d3 has no infinity branch — each formatter stringifies it. Only 'd'
        # (via toLocaleString) and 'X' (via toUpperCase) diverge from "Infinity".
        if spec.type == "d":
            return _INF_SYMBOL, ""
        return ("INFINITY" if spec.type == "X" else "Infinity"), ""

    t = spec.type
    if t == "f":
        if abs_val >= 1e21:
            return (
                _js_number_to_string(abs_val),
                "",
            )  # JS toFixed falls back to toString
        return _fmt_fixed(abs_val, precision), ""
    if t == "%":
        scaled = abs_val * 100.0
        if scaled >= 1e21:
            return _js_number_to_string(scaled), ""
        return _fmt_fixed(scaled, precision), ""
    if t == "p":
        return _format_rounded(abs_val * 100.0, precision), ""
    if t == "e":
        return _format_exponential(abs_val, precision), ""
    if t == "g":
        return _js_to_precision(abs_val, precision), ""
    if t == "r":
        return _format_rounded(abs_val, precision), ""
    if t == "s":
        return _format_si(abs_val, precision)
    if t == "d":
        return _format_decimal(abs_val), ""
    base, upper = _RADIX_TYPES[t]
    return _to_radix(abs_val, base, upper), ""


def _body_is_zero(body: str) -> bool:
    """True when JS's ``+body`` coercion would be 0 (d3's rounds-to-zero test).

    A body JS coerces to NaN — a radix body like "ff", or "Infinity", or "∞" —
    is not zero, so an unparseable body keeps its sign.
    """
    try:
        return float(body) == 0.0
    except ValueError:
        return False


def _prefix(spec: FormatSpec, locale: FormatLocale) -> str:
    """The leading unit: a currency symbol, or the alternate-form radix marker."""
    if spec.symbol == "$":
        return locale.currency[0]
    if spec.symbol == "#" and spec.type in _ALTERNATE_FORM_TYPES:
        return "0" + spec.type.lower()
    return ""


def _value_suffix(spec: FormatSpec, locale: FormatLocale) -> str:
    """The trailing unit. A currency symbol displaces the percent sign."""
    if spec.symbol == "$":
        return locale.currency[1]
    return locale.percent if spec.type in ("%", "p") else ""


def _pad(
    spec: FormatSpec,
    value_prefix: str,
    value: str,
    value_suffix: str,
    locale: FormatLocale,
) -> str:
    """Group the digits, then pad to width and align — d3's closing steps.

    Grouping appears twice for a reason: without zero-fill d3 groups the digits
    alone, with zero-fill it groups the padding *and* the digits together so the
    separators land inside the padded field.
    """
    if spec.comma and not spec.zero:
        value = _group(value, math.inf, locale)

    length = len(value_prefix) + len(value) + len(value_suffix)
    if spec.width is not None and spec.width > length:
        padding = spec.fill * (spec.width - length)
    else:
        padding = ""

    if spec.comma and spec.zero:
        width = (spec.width - len(value_suffix)) if padding and spec.width else math.inf
        value = _group(padding + value, width, locale)
        padding = ""

    if spec.align == "<":
        out = value_prefix + value + value_suffix + padding
    elif spec.align == "=":
        out = value_prefix + padding + value + value_suffix
    elif spec.align == "^":
        half = len(padding) // 2
        out = padding[:half] + value_prefix + value + value_suffix + padding[half:]
    else:
        out = padding + value_prefix + value + value_suffix
    if locale.numerals is not None:
        numerals = locale.numerals
        out = "".join(numerals[int(c)] if "0" <= c <= "9" else c for c in out)
    return out


def _resolve_type(spec: FormatSpec) -> None:
    """Apply d3's type aliases in place, before any formatting decision.

    ``n`` is ``,g``. The empty type and any letter that selects no formatter are
    alike ``.12~g`` — d3 does not reject an unrecognized type letter, it falls
    back to a general-purpose format, so neither do we.
    """
    if spec.type == "n":
        spec.comma = True
        spec.type = "g"
    elif spec.type not in _KNOWN_TYPES:
        if spec.precision is None:
            spec.precision = 12
        spec.trim = True
        spec.type = "g"


def _resolve_precision(spec: FormatSpec) -> int:
    """d3's precision default and per-type clamp. Call after ``_resolve_type``."""
    if spec.precision is None:
        return 6
    if spec.type in _SIGNIFICANT_PRECISION_TYPES:
        return max(1, min(21, spec.precision))
    return max(0, min(20, spec.precision))


def _apply_spec(
    spec: FormatSpec, precision: int, v: float | int | Decimal, locale: FormatLocale
) -> str:
    """Apply a resolved FormatSpec to a numeric value, mirroring d3's format()."""
    value_prefix = _prefix(spec, locale)
    value_suffix = _value_suffix(spec, locale)
    x = float(v)  # coerce int, Decimal, etc.; preserves -0 sign via IEEE 754

    if spec.type == "c":
        # Character data: the value is stringified verbatim and carries its own
        # ASCII hyphen, so the sign, trim and split steps are all skipped.
        return _pad(
            spec, value_prefix, "", _js_number_to_string(x) + value_suffix, locale
        )

    if math.isnan(x):
        # d3 short-circuits NaN ahead of the type function, which is why 'X'
        # does not uppercase it the way it uppercases "INFINITY".
        is_negative = False
        body, si_prefix = locale.nan, ""
    else:
        is_negative = x < 0 or math.copysign(1.0, x) < 0
        body, si_prefix = _format_type(spec, abs(x), precision)

    if spec.trim:
        body = _apply_trim(body)

    # A negative value that formats to zero loses its sign unless the spec asks
    # for an explicit one.
    if is_negative and spec.sign != "+" and _body_is_zero(body):
        is_negative = False

    if is_negative:
        value_prefix = ("(" if spec.sign == "(" else locale.minus) + value_prefix
    elif spec.sign not in ("-", "("):
        value_prefix = spec.sign + value_prefix

    value_suffix = si_prefix + value_suffix
    if is_negative and spec.sign == "(":
        value_suffix += ")"

    if spec.type in _SPLIT_SUFFIX_TYPES:
        # Hold the fraction/exponent out of grouping and zero-padding.
        for i, char in enumerate(body):
            if not "0" <= char <= "9":
                rest = locale.decimal + body[i + 1 :] if char == "." else body[i:]
                value_suffix = rest + value_suffix
                body = body[:i]
                break

    return _pad(spec, value_prefix, body, value_suffix, locale)


@overload
def format(spec_str: str) -> Callable[[float | int | Decimal], str]: ...  # noqa: A001


@overload
def format(spec_str: str, value: float | int | Decimal) -> str: ...  # noqa: A001


def format(  # noqa: A001
    spec_str: str,
    value: float | int | Decimal | None = None,
) -> Callable[[float | int | Decimal], str] | str:
    """Parse spec and return a formatter, or apply immediately.

    Two calling conventions::

        d3_format(",.2f")(1234.56)   →  "1,234.56"
        d3_format(",.2f", 1234.56)   →  "1,234.56"

    Args:
        spec_str: d3-format spec string.
        value: Optional value to format immediately.

    Returns:
        A ``Callable[[float], str]`` if value is None, else the formatted string.

    Raises:
        D3FormatError: If spec_str does not match the d3-format grammar.
    """
    _fmt = EN_US.format(spec_str)
    if value is None:
        return _fmt
    return _fmt(value)
