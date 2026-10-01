"""Pure D3-format number formatting, with dbt charts' SI notation extension.

Delegates spec parsing/formatting to ``libs/d3-format/`` and post-processes
SI (``s``-type) specs into dbt charts' ``analytic``/``narrative`` notations
when the caller explicitly passes ``notation``. Callers that want house
notation (predefined format members) pass ``notation="analytic"`` or
``notation="narrative"``; callers on the native-d3 path (inline specs,
user aliases) omit it so no post-process runs.

No dependency on the theme/format-alias cascade (that lives in
``dbt_charts.core.compile.format`` — see ``render.format_utils.format_value``
for the cascade-aware caller). Lives in ``core`` so compile-time callers
(e.g. axis-tick label formatting) can format a raw value without depending
on render.

Also owns ``is_time_format`` (detects a d3-time-format directive so a
strftime-style spec isn't mistaken for a broken d3-format spec). The
predefined-format vocabulary (PREDEFINED_NUMBER_NAMES, PREDEFINED_NATIVE, …)
lives in ``predefined_formats.py`` in this package.

NULL_DISPLAY is declared here so callers throughout the tree can import from a
single stable location. The house glyph helpers (_with_minus, MINUS) live in
predefined_formats.py where the native formatters that need them are defined.
"""

from __future__ import annotations

import re
from collections.abc import Callable
from datetime import date, datetime, timezone
from decimal import Decimal
from typing import Any, Literal

from d3_format import format as _d3_format, parse as _d3_parse
from d3_format.errors import D3FormatError
from dbt_charts.core.text.predefined_formats import PREDEFINED_NUMBER_NAMES

# The house notation vocabulary — analytic (SI-prefix, " K"/" M") or narrative
# (prose-style, "k"/"mn"). Declared once here; every other module that needs
# it (FormatConfig, the Vega expression emitter, the shared-scale resolver)
# imports this name rather than restating the same two-value Literal.
Notation = Literal["analytic", "narrative"]

# Rendered wherever a value is absent — never a per-surface literal. A wrong
# result that merely looks right (a stray "-" mistaken for a value) is worse
# than an obviously-blank cell, so every surface renders the same character.
NULL_DISPLAY = "—"

# d3-time-format directive pattern: % followed by optional padding modifier and a letter.
# %%  is a literal-percent escape — callers must strip it before matching.
_TIME_FORMAT_RE = re.compile(r"%[-_0]?[A-Za-z]")


def is_time_format(fmt: str) -> bool:
    """Return True when fmt contains a d3-time-format directive (``%<letter>``).

    d3-format (number formatting) and d3-time-format (date formatting) are
    different grammars sharing the same `format:` authoring surface. A
    strftime-style spec like ``"%b %Y"`` is invalid d3-format syntax, so
    callers use this to route it to time-format handling instead of raising
    d3-format's number-spec parse error. ``%%`` is a literal-percent escape
    stripped before matching so ``"%%Y"`` does not trigger detection.
    """
    return bool(_TIME_FORMAT_RE.search(fmt.replace("%%", "")))


# Spellings Python's float() accepts and JS's unary + does not, so the two
# disagree on whether a tick paints as a number: digit separators, and the
# non-finite words (JS reads "nan"/"inf" as NaN and paints exactly that).
_NOT_JS_NUMERIC_RE = re.compile(r"_|^[+-]?(nan|inf(inity)?)$", re.IGNORECASE)


def reads_as_number(value: Any) -> bool:  # type-state: explicit_any — a raw query cell
    """Whether d3 can read this tick value as a number, i.e. JS ``+value``.

    Neither of the two numeric predicates this repo already has answers this
    question, which is why it is a third one:

    - ``coerce_numeric_cell`` is the shared *null* rule ("no color, no domain
      contribution") and excludes ``bool``, a contract this question does not
      share — d3 reads ``+true`` as ``1`` and paints ``0``/``1`` over a boolean
      dimension rather than NaN.
    - ``is_vega_numeric_value`` (``core/utils.py``) is the "what VL type is
      this column" rule and deliberately rejects numeric *strings*. d3
      coerces those, so rejecting them here would refuse a column that
      formats perfectly.

    Do not consolidate this into either of them: each rejection above is a
    board that renders today. Lives here (not ``type_inference.py``, the
    render-layer module that originated it) so a leaf ``core/text/`` caller
    — e.g. ``category_label_text`` — can reuse it without a render import.
    """
    if isinstance(value, (int, float, Decimal)):
        return True
    if isinstance(value, str):
        text = value.strip()
        if not text:
            return True  # JS reads +"" and +" " as 0
        if _NOT_JS_NUMERIC_RE.search(text):
            return False
        try:
            float(text)
        except ValueError:
            return False
        return True
    return False


# Every strftime directive letter portable_strftime actually paints (native
# strftime letters, plus the twelve CRT-unsupported ones _DIRECTIVE_COMPUTERS
# substitutes in Python first). Padding modifiers (-, _, 0) between % and the
# letter are accepted but don't change which letters are valid. `E`/`O` are
# POSIX locale modifiers, not directives in their own right -- neither
# belongs here.
PYTHON_STRFTIME_DIRECTIVES: frozenset[str] = frozenset(
    "aAbBcCdDefFgGhHIjklLmMnpPqQrRsSTuUVwWxXyYzZ"
)

# Directive letters d3-time-format implements. Vega paints a time_format,
# axis-label, or tooltip spec by sending it straight to d3-time-format, never
# through portable_strftime, so these are the letters a Vega-painted slot can
# legitimately author.
D3_TIME_FORMAT_DIRECTIVES: frozenset[str] = frozenset("aAbBcdefgGHIjLmMpqQsSuUVwWxXyYZ")

# Matches a % directive sequence: optional padding modifier + letter.
_DIRECTIVE_LETTER_RE = re.compile(r"%[-_0]?(.)")


def find_unsupported_directive(
    fmt: str, valid_directives: frozenset[str]
) -> str | None:
    """Return the first %-directive letter in fmt not in valid_directives,
    or None when every directive is covered.

    Python's strftime silently passes an unknown directive through
    (``%Q`` -> the literal text ``Q``) rather than raising, so a spec with an
    unimplemented directive must be rejected explicitly. ``%%`` is a
    literal-percent escape and never counts as a directive. Callers pass the
    directive set for the engine that will actually paint the spec: compile
    time checks against the union of what Vega (d3-time-format) and Python
    (portable_strftime) can paint; the render-time backstop in
    table_support.py checks only Python's set, since that is what it paints.
    """
    for match in _DIRECTIVE_LETTER_RE.finditer(fmt.replace("%%", "")):
        letter = match.group(1)
        if letter not in valid_directives:
            return letter
    return None


# Single-field numeric directives where the -/_/0 padding modifier is actually
# defined. Composite (%T, %X, %D, %r, ...) and text (%Z, %n, ...) directives
# render more than one padded value or none at all, so stripping padding from
# their combined output would mangle it (e.g. "00:00:00" -> ":00:00" for
# %-T) — those must reach strftime unmodified.
_PADDED_NUMERIC_DIRECTIVES = frozenset("CdeGHIjklLmMqQSuUVwWyY")


def _century(d: date) -> str:
    return f"{d.year // 100:02d}"


def _hour_24(d: date) -> int:
    # date has no time-of-day at all; midnight is the only POSIX-correct
    # default (matches what every host libc already does with a date-only
    # struct_time).
    return d.hour if isinstance(d, datetime) else 0


def _day_space_padded(d: date) -> str:
    return f"{d.day:2d}"


def _hour_24_space_padded(d: date) -> str:
    return f"{_hour_24(d):2d}"


def _hour_12_space_padded(d: date) -> str:
    hour_of_12 = _hour_24(d) % 12
    return f"{12 if hour_of_12 == 0 else hour_of_12:2d}"


def _iso_year(d: date) -> str:
    return f"{d.isocalendar().year:04d}"


def _iso_week(d: date) -> str:
    return f"{d.isocalendar().week:02d}"


def _iso_weekday(d: date) -> str:
    return str(d.isocalendar().weekday)


def _day_of_year(d: date) -> str:
    return f"{d.timetuple().tm_yday:03d}"


def _week_monday_first(d: date) -> str:
    # POSIX %W: Monday-first week number, with a real week 00 for any days
    # before the year's first Monday (distinct from ISO %V, which has no
    # week 00 — the last days of December instead roll into next year's
    # week 01, or the first days of January roll back into the previous
    # year's week 52/53).
    jan1 = date(d.year, 1, 1)
    days_since_jan1 = d.toordinal() - jan1.toordinal()
    days_to_first_monday = (7 - jan1.weekday()) % 7
    if days_since_jan1 < days_to_first_monday:
        return "00"
    return f"{(days_since_jan1 - days_to_first_monday) // 7 + 1:02d}"


def _quarter(d: date) -> str:
    return str((d.month - 1) // 3 + 1)


def _milliseconds(d: date | datetime) -> str:
    ms = d.microsecond // 1000 if isinstance(d, datetime) else 0
    return f"{ms:03d}"


_EPOCH_UTC = datetime(1970, 1, 1, tzinfo=timezone.utc)


def _epoch_millis(d: date | datetime) -> str:
    # A naive datetime (or a plain date) is the same "already UTC" convention
    # table_support.py documents for its own strftime callers -- Vega's
    # utcFormat has no concept of a naive instant, so ours must pick the one
    # reading that makes measurement match paint.
    dt = d if isinstance(d, datetime) else datetime(d.year, d.month, d.day)
    dt = (
        dt.replace(tzinfo=timezone.utc)
        if dt.tzinfo is None
        else dt.astimezone(timezone.utc)
    )
    delta = dt - _EPOCH_UTC
    return str(
        (delta.days * 86_400 + delta.seconds) * 1000 + delta.microseconds // 1000
    )


# %G/%V/%u each call date.isocalendar() independently rather than sharing one
# cached call, but since isocalendar() is a pure function of the date, that
# can't introduce drift between them.
_DIRECTIVE_COMPUTERS: dict[str, Callable[[date | datetime], str]] = {
    "C": _century,
    "e": _day_space_padded,
    "G": _iso_year,
    "j": _day_of_year,
    "k": _hour_24_space_padded,
    "l": _hour_12_space_padded,
    "L": _milliseconds,
    "q": _quarter,
    "Q": _epoch_millis,
    "u": _iso_weekday,
    "V": _iso_week,
    "W": _week_monday_first,
}

# Matches, in priority order: a literal-percent escape (checked first so an
# escape immediately followed by a modifier char, e.g. "%%-d", isn't misread
# as a modified directive starting at the second `%`); or any directive with
# an optional padding modifier (% + optional -/_/0 + a letter). Whether the
# letter needs Python-side handling (a CRT-unsupported directive, bare or
# modified) is decided in `_substitute`, not by the regex.
_TIME_DIRECTIVE_RE = re.compile(r"%%|%([-_0])?([A-Za-z])")

# The d3-time-format directive alphabet portable_strftime renders byte-
# identical to Vega's utcFormat, bare and under every padding modifier --
# enforced, not just asserted: ``test_format_d3_vega_parity.py`` probes real
# Vega (``vl_convert``) for this exact set x every modifier and fails if one
# drifts, so this comment cannot go stale the way an unverified claim did
# before (``%_L``, bare ``%f``/``%g``, all silently wrong here at one point).
# The excluded real d3 directives are %c/%x/%X (locale composites: Python
# renders them through the host's C-locale strftime, not d3's en-US
# "%x, %X"), %Z (empty on a naive datetime, unlike d3's "+0000"), and %s
# (Python's %s silently uses the host's local timezone rather than a true
# UTC epoch count). %f and %g are real d3 directives whose modifier forms
# Python renders differently. Every remaining letter
# is not d3-time-format grammar at all -- Vega paints the literal letter
# while Python's strftime may compute an unrelated value (or, on Windows,
# raise).
VEGA_SAFE_TIME_DIRECTIVES = frozenset("aAbBdeGHIjLmMpqQSuUVwWyY")


def unsupported_time_directives(fmt: str) -> list[str]:
    """Directive letters in ``fmt`` outside ``VEGA_SAFE_TIME_DIRECTIVES``,
    sorted and de-duplicated. Empty when every directive Vega would paint
    from ``fmt`` matches what ``portable_strftime`` measures.
    """
    return sorted(
        {
            letter
            for _, letter in _TIME_DIRECTIVE_RE.findall(fmt)
            if letter and letter not in VEGA_SAFE_TIME_DIRECTIVES
        }
    )


def _apply_padding_modifiers(d: date | datetime, fmt: str) -> str:
    """Resolve `-`/`_`/`0` padding modifiers and the twelve CRT-unsupported
    directives (bare or modified) in ``fmt`` to plain text.

    Every other directive (including `%%` and modified non-numeric
    directives) passes through untouched, so the result is still a valid
    strftime spec.
    """

    def _substitute(match: re.Match[str]) -> str:
        if match.group(0) == "%%":
            return "%%"
        modifier, letter = match.group(1), match.group(2)
        if modifier is None:
            computed = _DIRECTIVE_COMPUTERS.get(letter)
            return computed(d) if computed else match.group(0)
        if letter not in _PADDED_NUMERIC_DIRECTIVES:
            return match.group(0)
        # %e/%k/%l are space-padded by default (not zero-padded like %d/%H),
        # so the unpadded value needs both whitespace and zeros stripped.
        padded = (
            _DIRECTIVE_COMPUTERS[letter](d)
            if letter in _DIRECTIVE_COMPUTERS
            else d.strftime(f"%{letter}")
        )
        value = padded.strip().lstrip("0") or "0"
        if modifier == "-":
            return value
        if modifier == "_":
            return value.rjust(len(padded))
        return value.rjust(len(padded), "0")

    return _TIME_DIRECTIVE_RE.sub(_substitute, fmt)


def portable_strftime(d: date | datetime, fmt: str) -> str:
    """strftime a date/datetime, resolving Windows-CRT-unsupported directives
    in Python first.

    d3-time-format specs (``%-d %b %Y``) borrow the GNU/BSD no-pad modifier,
    and six of them (``%e %G %j %u %V %W``) are themselves glibc/BSD
    extensions — both are directives glibc and macOS's libc implement but
    Windows' CRT does not, so ``datetime.strftime`` raises
    ``ValueError: Invalid format string`` there. `%L`/`%Q`/`%q` are d3-only
    directives Windows' CRT (and glibc/macOS) never implement at all.
    Substituting the affected directives with their computed value before
    delegating the rest of the spec to strftime sidesteps the host libc
    entirely, so the result is identical on every platform.
    """
    return d.strftime(_apply_padding_modifiers(d, fmt))


# d3 SI prefix → dbt charts analytic suffix mapping
_D3_TO_ANALYTIC: dict[str, str] = {
    "k": " K",
    "M": " M",
    "G": " B",
    "T": " T",
    "P": " P",
    "E": " E",
    "Z": " Z",
    "Y": " Y",
}

# d3 SI prefix → dbt charts narrative suffix mapping. No accepted narrative
# form exists above trillions (English financial writing stops at "trn"), so
# peta/exa/zetta/yotta borrow the analytic suffix rather than leave d3's
# bare, unmapped letter ("1P") to reach a shipped artifact — read from
# _D3_TO_ANALYTIC rather than restated, so the two tables cannot drift.
_D3_TO_NARRATIVE: dict[str, str] = {
    "k": "k",
    "M": "mn",
    "G": "bn",
    "T": "trn",
    "P": _D3_TO_ANALYTIC["P"],
    "E": _D3_TO_ANALYTIC["E"],
    "Z": _D3_TO_ANALYTIC["Z"],
    "Y": _D3_TO_ANALYTIC["Y"],
}


def format_d3(
    value: int | float | None,
    format_spec: str,
    prefix: str = "",
    suffix: str = "",
    notation: Notation | None = None,
) -> str:
    """Format a value using D3-style format specification.

    Delegates to ``libs/d3-format/`` for spec parsing and formatting.
    Applies dbt charts-specific analytic/narrative notation post-processing for
    SI (``s``-type) specs after the lib produces d3-standard output.

    Args:
        value: Numeric value to format
        format_spec: D3 format string (e.g., "$,.2f", ".1~%", "~s")
        prefix: Custom prefix to prepend
        suffix: Custom suffix to append
        notation: SI-prefix notation family ("analytic" or "narrative")

    Returns:
        Formatted string
    """
    if value is None:
        return f"{prefix}{NULL_DISPLAY}{suffix}"

    if not format_spec:
        return f"{prefix}{value}{suffix}"

    formatted = _d3_format(format_spec)(float(value))

    # dbt charts-specific SI notation post-processing — only when the caller
    # explicitly requests a notation register. Callers on the predefined-format
    # path pass notation="analytic" or "narrative"; callers on the native-d3
    # path (inline specs, user aliases) omit it so no post-process runs.
    if notation is not None and _is_si_spec(format_spec):
        if notation == "analytic":
            formatted = _apply_analytic_notation(formatted)
        elif notation == "narrative":
            formatted = _apply_narrative_notation(formatted)
        else:
            raise ValueError(f"Unknown notation: {notation!r}")

    return f"{prefix}{formatted}{suffix}"


def _is_si_spec(spec: str) -> bool:
    """True when spec's type character is 's' (SI prefix)."""
    return _d3_parse(spec).type == "s"


def is_d3_si_spec(spec: str) -> bool:
    """True when spec is SI-shaped d3-format grammar. Never raises on a
    compile-accepted non-d3 spec.

    Public, unlike ``_is_si_spec`` above: this is the cross-module-safe
    predicate a caller outside this module reaches for, and its whole job
    is to answer that question without parsing.

    A predefined format member name (``percent_number``, ``number``, …) or a
    strftime directive (``%b %Y``) shares the ``format:`` authoring surface
    with real d3-format specs -- ``validate.formats._validate_spec`` whitelists
    both explicitly -- but neither is d3 grammar, and ``_is_si_spec`` calls
    ``_d3_parse`` unguarded. Both are definitionally not SI-shaped, so this
    answers "no" for them without parsing.

    Anything else that ``_d3_parse`` rejects is likewise not an SI-shaped d3
    spec -- which is the whole question -- so it answers False rather than
    raising. The two compile-accepted inputs that reach here and would make
    ``_d3_parse`` raise are an unresolved ``style.formats`` alias name and a
    predefined *date* name (``date_short``); both answer False. Total by design:
    ``build_resolved_axis`` computes this verdict once, up front, for an axis
    whose authored format may be any of these grammars, and a raise there would
    take a valid board down. Validation is not this function's job -- a
    genuinely malformed spec (``%%Y``) is rejected earlier at compile() by
    ``validate/formats.py`` with ``ERR-FORMAT-INVALID``, where the author gets a
    diagnostic, not a traceback. The cost is a false negative: an unresolvable
    alias whose target is SI-shaped answers False, a missed classification, not
    a wrong render (the resolve path works from resolved specs).
    """
    if spec in PREDEFINED_NUMBER_NAMES or is_time_format(spec):
        return False
    try:
        return _is_si_spec(spec)
    except D3FormatError:
        return False


def is_d3_fixed_decimal_spec(spec: str) -> bool:
    """True when spec is fixed-point d3 grammar ('f' or '%') that prints at
    least one decimal place. Never raises on a compile-accepted non-d3 spec.

    ``is_d3_si_spec``'s sibling, with the same totality contract for the same
    reason. A zero-precision spec (``integer``, ``percent_whole``) has no
    decimals to over-print, so it answers False.
    """
    if spec in PREDEFINED_NUMBER_NAMES or is_time_format(spec):
        return False
    try:
        parsed = _d3_parse(spec)
    except D3FormatError:
        return False
    return parsed.type in ("f", "%") and bool(parsed.precision)


def round_aware_spec(format_spec: str) -> str:
    """Inject d3's trim (``~``) flag into an SI spec that doesn't already have it.

    ``.2s`` on an exact multiple of its SI base (1,000,000) renders ``1.0M``
    — a trailing zero that carries no precision, false-precision noise on a
    round number. d3's own trim flag already does exactly what's wanted here
    (drop insignificant trailing zeros, keep every real digit: ``1.25M`` stays
    ``1.25M``), so this borrows it rather than re-implementing rounding.
    A no-op for a non-SI spec, or one that already sets ``~``.
    """
    parsed = _d3_parse(format_spec)
    if parsed.type != "s" or parsed.trim:
        return format_spec
    parsed.trim = True
    return str(parsed)


def _apply_analytic_notation(d3_str: str) -> str:
    """Convert d3 SI string (e.g. '1.5M') to analytic notation (e.g. '1.5 M')."""
    for d3_sfx, analytic_sfx in _D3_TO_ANALYTIC.items():
        if d3_str.endswith(d3_sfx):
            return d3_str[: -len(d3_sfx)] + analytic_sfx
    return d3_str


def _apply_narrative_notation(d3_str: str) -> str:
    """Convert d3 SI string (e.g. '1.5G') to narrative notation (e.g. '1.5bn')."""
    for d3_sfx, narrative_sfx in _D3_TO_NARRATIVE.items():
        if d3_str.endswith(d3_sfx):
            return d3_str[: -len(d3_sfx)] + narrative_sfx
    return d3_str
