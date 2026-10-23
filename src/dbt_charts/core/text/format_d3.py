"""Pure D3-format number formatting, with dbt charts' SI notation extension.

Delegates spec parsing/formatting to ``libs/d3-format/`` and post-processes
SI (``s``-type) specs into dbt charts' ``analytic``/``narrative`` notations
when the caller explicitly passes ``notation``. Callers that want house
notation (predefined format members) pass ``notation="analytic"`` or
``notation="narrative"``; callers on the native-d3 path (inline specs,
user aliases) omit it so no post-process runs.

No dependency on the theme/format-alias cascade (that lives in
``dbt_charts.core.compile.format`` — see ``compile.format_value.format_value``
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

import json
import re
from collections.abc import Callable, Generator
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass
from datetime import UTC, date, datetime
from decimal import Decimal
from typing import Any, Literal, TypedDict, cast, get_args

import vl_convert
from importlib_resources import files

from d3_format import (
    EN_US,
    FormatLocale,
    LocaleDefinition,
    format_locale,
    parse as _d3_parse,
)
from d3_format.errors import D3FormatError
from dbt_charts import _engine
from dbt_charts.core.text.magnitude_words import MagnitudeWords, magnitude_words
from dbt_charts.core.text.predefined_formats import (
    PREDEFINED_NATIVE,
    PREDEFINED_NUMBER_NAMES,
)

# The house notation vocabulary — analytic (SI-prefix, " K"/" M") or narrative
# (prose-style, "k"/"mn"). Declared once here; every other module that needs
# it (FormatConfig, the Vega expression emitter, the shared-scale resolver)
# imports this name rather than restating the same two-value Literal.
Notation = Literal["analytic", "narrative"]

# Where a leading sign sits against an authored prefix: "−€500" or "EUR −500".
SignPlacement = Literal["before_prefix", "after_prefix"]


def default_sign_placement(prefix: str | None) -> SignPlacement:
    """A spaced prefix is a word or code ("EUR −500"); a glued one is a symbol
    ("−€500")."""
    spaced = prefix is not None and prefix[-1:].isspace()
    return "after_prefix" if spaced else "before_prefix"


# The d3-format locales Vega bundles.
NumberLocaleName = Literal[
    "ar-AE",
    "ar-BH",
    "ar-DJ",
    "ar-DZ",
    "ar-EG",
    "ar-EH",
    "ar-ER",
    "ar-IL",
    "ar-IQ",
    "ar-JO",
    "ar-KM",
    "ar-KW",
    "ar-LB",
    "ar-LY",
    "ar-MA",
    "ar-MR",
    "ar-OM",
    "ar-PS",
    "ar-QA",
    "ar-SA",
    "ar-SD",
    "ar-SO",
    "ar-SS",
    "ar-SY",
    "ar-TD",
    "ar-TN",
    "ar-YE",
    "ca-ES",
    "cs-CZ",
    "da-DK",
    "de-CH",
    "de-DE",
    "en-CA",
    "en-GB",
    "en-IE",
    "en-IN",
    "en-US",
    "es-BO",
    "es-ES",
    "es-MX",
    "fi-FI",
    "fr-CA",
    "fr-FR",
    "he-IL",
    "hu-HU",
    "it-IT",
    "ja-JP",
    "ko-KR",
    "mk-MK",
    "nl-NL",
    "pl-PL",
    "pt-BR",
    "pt-PT",
    "ru-RU",
    "sl-SI",
    "sv-SE",
    "uk-UA",
    "zh-CN",
]


def number_locale_definition(name: NumberLocaleName) -> LocaleDefinition:
    """d3's locale definition for ``name``, as Vega's bundled d3-format ships it."""
    return cast(
        "LocaleDefinition",
        vl_convert.get_format_locale(name),
    )  # type-state: cast — vl_convert's stub types d3's locale JSON as dict[str, Any]


# The d3-time-format locales Vega bundles.
TimeLocaleName = Literal[
    "ar-EG",
    "ar-SY",
    "ca-ES",
    "cs-CZ",
    "da-DK",
    "de-CH",
    "de-DE",
    "en-CA",
    "en-GB",
    "en-US",
    "es-ES",
    "es-MX",
    "fa-IR",
    "fi-FI",
    "fr-CA",
    "fr-FR",
    "he-IL",
    "hr-HR",
    "hu-HU",
    "it-IT",
    "ja-JP",
    "ko-KR",
    "mk-MK",
    "nb-NO",
    "nl-BE",
    "nl-NL",
    "pl-PL",
    "pt-BR",
    "ru-RU",
    "sv-SE",
    "tr-TR",
    "uk-UA",
    "vi-VN",
    "zh-CN",
    "zh-TW",
]


class TimeLocaleDefinition(TypedDict):
    """d3's ``timeFormatLocale`` definition, as Vega's ``config.locale.time``."""

    dateTime: str
    date: str
    time: str
    periods: list[str]
    days: list[str]
    shortDays: list[str]
    months: list[str]
    shortMonths: list[str]


WeekdayName = Literal[
    "sunday", "monday", "tuesday", "wednesday", "thursday", "friday", "saturday"
]
WEEKDAYS: tuple[WeekdayName, ...] = get_args(WeekdayName)

CLDR_FIRST_DAY: dict[str, str] = json.loads(
    files("dbt_charts.core.text")
    .joinpath("data", "cldr_first_day.json")
    .read_text(encoding="utf-8")
)["firstDay"]


def time_locale_definition(name: TimeLocaleName) -> TimeLocaleDefinition:
    """d3's time locale definition for ``name``, as Vega's bundled d3-time-format
    ships it."""
    return cast(
        "TimeLocaleDefinition",
        vl_convert.get_time_format_locale(name),
    )  # type-state: cast — vl_convert's stub types d3's locale JSON as dict[str, Any]


def time_locale_week_start(name: TimeLocaleName) -> int:
    """The index (0 Sunday) of the first weekday CLDR gives ``name``'s region."""
    return [day[:3] for day in WEEKDAYS].index(CLDR_FIRST_DAY[name[3:]])


# d3's SI letters for thousand through trillion; past trillions both registers
# print d3's own letter, glued like the analytic words (`1.2T`, `1.2P`).
_SI_WORD_LETTERS = ("k", "M", "G", "T")
_SI_PAST_TRILLIONS = {s: s for s in ("P", "E", "Z", "Y")}
SI_LETTERS: tuple[str, ...] = (*_SI_WORD_LETTERS, *_SI_PAST_TRILLIONS)


@dataclass(frozen=True, kw_only=True)
class HouseLocale(FormatLocale):
    """A d3 locale plus the words its magnitudes print in each register."""

    analytic: MagnitudeWords
    narrative: MagnitudeWords

    def magnitudes(self, register: Notation) -> dict[str, str]:
        match register:
            case "analytic":
                words = self.analytic
            case "narrative":
                words = self.narrative
        return {**dict(zip(_SI_WORD_LETTERS, words, strict=True)), **_SI_PAST_TRILLIONS}


def house_locale(
    definition: LocaleDefinition, analytic: MagnitudeWords, narrative: MagnitudeWords
) -> HouseLocale:
    return HouseLocale(
        **vars(format_locale(definition)), analytic=analytic, narrative=narrative
    )


HOUSE_EN_US = HouseLocale(
    **vars(EN_US),
    analytic=magnitude_words("en-US")[0],
    narrative=magnitude_words("en-US")[1],
)

EN_US_TIME = time_locale_definition("en-US")

_NUMBER_LOCALE: ContextVar[HouseLocale] = ContextVar(
    "dct_number_locale", default=HOUSE_EN_US
)
_TIME_LOCALE: ContextVar[TimeLocaleDefinition] = ContextVar(
    "dct_time_locale", default=EN_US_TIME
)
_WEEK_START: ContextVar[int] = ContextVar("dct_week_start", default=0)
_LANGUAGE: ContextVar[str] = ContextVar("dct_language", default="en")


@contextmanager
def regional_scope(
    number: HouseLocale,
    time: TimeLocaleDefinition,
    week_start: int,
    language: str = "en",
) -> Generator[None]:
    number_token = _NUMBER_LOCALE.set(number)
    time_token = _TIME_LOCALE.set(time)
    week_token = _WEEK_START.set(week_start)
    language_token = _LANGUAGE.set(language)
    try:
        yield
    finally:
        _LANGUAGE.reset(language_token)
        _WEEK_START.reset(week_token)
        _TIME_LOCALE.reset(time_token)
        _NUMBER_LOCALE.reset(number_token)


@contextmanager
def language_scope(language: str) -> Generator[None]:
    """Open only the word-language scope: for words printed with no board's
    number and time locales behind them (authoring warnings, the controls
    runtime's English slice)."""
    token = _LANGUAGE.set(language)
    try:
        yield
    finally:
        _LANGUAGE.reset(token)


def active_format_locale() -> HouseLocale:
    return _NUMBER_LOCALE.get()


def active_time_locale() -> TimeLocaleDefinition:
    return _TIME_LOCALE.get()


def active_week_start() -> int:
    return _WEEK_START.get()


def active_language() -> str:
    return _LANGUAGE.get()


def board_word(message_id: str, count: int | None = None, **args: str) -> str:
    """Message `message_id` in the board's language (`regional.language`);
    `count` picks a plural form and fills `{n}`, `args` fill other placeholders."""
    return _engine.message(_LANGUAGE.get(), message_id, count, args)


def locale_definition(locale: FormatLocale) -> LocaleDefinition:
    """``locale`` as the d3 definition Vega's ``config.locale.number`` takes."""
    definition: LocaleDefinition = {
        "decimal": locale.decimal,
        "currency": list(locale.currency),
        "percent": locale.percent,
        "minus": locale.minus,
        "nan": locale.nan,
    }
    if locale.grouping is not None:
        definition["thousands"] = locale.thousands
        definition["grouping"] = list(locale.grouping)
    if locale.numerals is not None:
        definition["numerals"] = list(locale.numerals)
    return definition


def currency_front(locale: FormatLocale) -> str:
    """The locale's currency ahead of the digits; a word keeps a gap (``kr 12``)."""
    before, after = locale.currency
    if before.strip():
        return before
    symbol = after.strip()
    return symbol + ("\u00a0" if symbol[-1:].isalpha() else "")


def currency_suffix(spec: str) -> str:
    """The text the locale's currency places after the digits of ``spec``."""
    return active_format_locale().currency[1] if "$" in spec else ""


def composed_currency(symbol: str, repeats: bool) -> tuple[str, str]:
    """``(lead, trail)`` a ``$`` composes outside d3 around digits ending in a
    magnitude suffix: empty where the locale's currency leads (d3 places it),
    else trailing where it repeats (``500k €``) and leading where declared once."""
    locale = active_format_locale()
    if symbol != "$" or locale.currency[0].strip():
        return "", ""
    return ("", locale.currency[1]) if repeats else (currency_front(locale), "")


def d3_format(spec: str) -> Callable[[float | int | Decimal], str]:
    """d3 ``format(spec)`` under the active number locale."""
    return active_format_locale().format(spec)


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

# Matches a % directive sequence: optional padding modifier + letter.
_DIRECTIVE_LETTER_RE = re.compile(r"%[-_0]?(.)")


def find_unsupported_directive(fmt: str) -> str | None:
    """Return the first %-directive letter in fmt not in
    ``PYTHON_STRFTIME_DIRECTIVES``, or None when every directive is covered.

    Python's strftime silently passes an unknown directive through
    (``%Q`` -> the literal text ``Q``) rather than raising, so a spec with an
    unimplemented directive must be rejected explicitly. ``%%`` is a
    literal-percent escape and never counts as a directive.
    """
    for match in _DIRECTIVE_LETTER_RE.finditer(fmt.replace("%%", "")):
        letter = match.group(1)
        if letter not in PYTHON_STRFTIME_DIRECTIVES:
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


_EPOCH_UTC = datetime(1970, 1, 1, tzinfo=UTC)


def _epoch_millis(d: date | datetime) -> str:
    # A naive datetime (or a plain date) is the same "already UTC" convention
    # the engine's table follows for its own strftime callers -- Vega's
    # utcFormat has no concept of a naive instant, so ours must pick the one
    # reading that makes measurement match paint.
    dt = d if isinstance(d, datetime) else datetime(d.year, d.month, d.day)
    dt = dt.replace(tzinfo=UTC) if dt.tzinfo is None else dt.astimezone(UTC)
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

_NAME_DIRECTIVES = frozenset("aAbBpP")

# The d3-time-format directive alphabet portable_strftime renders byte-
# identical to Vega's utcFormat, bare and under every padding modifier --
# enforced, not just asserted: ``test_format_d3_vega_parity.py`` probes real
# Vega (``vl_convert``) for this exact set x every modifier and fails if one
# drifts, so this comment cannot go stale the way an unverified claim did
# before (``%_L``, bare ``%f``/``%g``, all silently wrong here at one point).
# The excluded real d3 directives are %Z (empty on a naive datetime, unlike
# d3's "+0000"), and %s (Python's %s silently uses the host's local timezone
# rather than a true UTC epoch count). %f and %g are real d3 directives whose
# modifier forms Python renders differently. Every remaining letter is not
# d3-time-format grammar at all -- Vega paints the literal letter while
# Python's strftime may compute an unrelated value (or, on Windows, raise).
VEGA_SAFE_TIME_DIRECTIVES = frozenset("aAbBcdeGHIjLmMpqQSuUVwWxXyY")


def time_directive_letters(fmt: str) -> list[str]:
    """The directive letters ``fmt`` uses, ``%%`` escapes and modifiers aside."""
    return [letter for _, letter in _TIME_DIRECTIVE_RE.findall(fmt) if letter]


def unsupported_time_directives(fmt: str) -> list[str]:
    """Directive letters in ``fmt`` outside ``VEGA_SAFE_TIME_DIRECTIVES``,
    sorted and de-duplicated. Empty when every directive Vega would paint
    from ``fmt`` matches what ``portable_strftime`` measures.
    """
    return sorted(
        {
            letter
            for letter in time_directive_letters(fmt)
            if letter not in VEGA_SAFE_TIME_DIRECTIVES
        }
    )


def _apply_padding_modifiers(d: date | datetime, fmt: str) -> str:
    """Resolve `-`/`_`/`0` padding modifiers and the twelve CRT-unsupported
    directives (bare or modified) in ``fmt`` to plain text.

    Name directives (`%a %A %b %B %p %P`, modified or not) become the active
    locale's words. Every other directive (including `%%`) passes through
    untouched, so the result is still a valid strftime spec.
    """

    def _substitute(match: re.Match[str]) -> str:
        if match.group(0) == "%%":
            return "%%"
        modifier, letter = match.group(1), match.group(2)
        if letter in _NAME_DIRECTIVES:
            return _locale_word(d, letter, active_time_locale())
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


_COMPOSITE_DIRECTIVE_RE = re.compile(r"%%|%[-_0]?([cxX])")


def _expand_composites(fmt: str, locale: TimeLocaleDefinition) -> str:
    """Replace ``%c %x %X`` with the locale's patterns, which may nest
    (``%c`` holds ``%X``)."""
    patterns = {"c": locale["dateTime"], "x": locale["date"], "X": locale["time"]}

    def _expand(match: re.Match[str]) -> str:
        letter = match.group(1)
        return match.group(0) if letter is None else patterns[letter]

    while (expanded := _COMPOSITE_DIRECTIVE_RE.sub(_expand, fmt)) != fmt:
        fmt = expanded
    return fmt


def _locale_word(d: date | datetime, letter: str, locale: TimeLocaleDefinition) -> str:
    weekday = (d.weekday() + 1) % 7
    pm = isinstance(d, datetime) and d.hour >= 12
    word = {
        "a": locale["shortDays"][weekday],
        "A": locale["days"][weekday],
        "b": locale["shortMonths"][d.month - 1],
        "B": locale["months"][d.month - 1],
        "p": locale["periods"][pm],
        "P": locale["periods"][pm].lower(),
    }[letter]
    return word.replace("%", "%%")


def portable_strftime(d: date | datetime, fmt: str) -> str:
    """strftime a date/datetime in the active time locale, resolving
    Windows-CRT-unsupported directives in Python first.

    d3-time-format specs (``%-d %b %Y``) borrow the GNU/BSD no-pad modifier,
    and six of them (``%e %G %j %u %V %W``) are themselves glibc/BSD
    extensions — both are directives glibc and macOS's libc implement but
    Windows' CRT does not, so ``datetime.strftime`` raises
    ``ValueError: Invalid format string`` there. `%L`/`%Q`/`%q` are d3-only
    directives Windows' CRT (and glibc/macOS) never implement at all.
    Substituting the affected directives with their computed value before
    delegating the rest of the spec to strftime sidesteps the host libc
    entirely, so the result is identical on every platform. Day, month and
    period names (and the ``%c %x %X`` composites) come from the board's
    ``regional.locale.time``, never the host's C locale.
    """
    locale = active_time_locale()
    return d.strftime(_apply_padding_modifiers(d, _expand_composites(fmt, locale)))


# Leading sign glyphs: d3's U+2212 plus ASCII "-"/"+". Parenthesized
# negatives are not split: a prefix lands ahead of the paren.
_LEADING_SIGN_CHARS = "−+-"


def split_leading_sign(text: str) -> tuple[str, str]:
    """Split a formatted number's leading sign glyph from its body."""
    if text and text[0] in _LEADING_SIGN_CHARS:
        return text[0], text[1:]
    return "", text


def affix_signed(
    text: str, prefix: str, suffix: str, sign_placement: SignPlacement | None
) -> str:
    """``text`` wrapped in ``prefix``/``suffix``, its leading sign placed per
    ``sign_placement``, which a prefix requires. d3's own ``$`` stays inside
    ``text``, after the sign."""
    if not prefix:
        return f"{text}{suffix}"
    if sign_placement is None:
        raise ValueError(f"prefix {prefix!r} needs a sign_placement")
    sign, body = split_leading_sign(text)
    if sign_placement == "after_prefix":
        return f"{prefix}{sign}{body}{suffix}"
    return f"{sign}{prefix}{body}{suffix}"


def format_d3(
    value: int | float | None,
    format_spec: str,
    prefix: str = "",
    suffix: str = "",
    notation: Notation | None = None,
    sign_placement: SignPlacement | None = None,
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
        sign_placement: where a leading sign sits against ``prefix``;
            required with a prefix

    Returns:
        Formatted string
    """
    if value is None:
        return f"{prefix}{NULL_DISPLAY}{suffix}"

    if not format_spec:
        return affix_signed(str(value), prefix, suffix, sign_placement)

    if format_spec in PREDEFINED_NATIVE:
        digits, unit = PREDEFINED_NATIVE[format_spec].python(value)
        return affix_signed(digits + unit, prefix, suffix, sign_placement)

    formatted = d3_format(format_spec)(float(value))

    # dbt charts-specific SI notation post-processing — only when the caller
    # explicitly requests a notation register. Callers on the predefined-format
    # path pass notation="analytic" or "narrative"; callers on the native-d3
    # path (inline specs, user aliases) omit it so no post-process runs.
    if notation is not None and _is_si_spec(format_spec):
        # The SI letter ends the digits, ahead of a currency placed after them.
        body = formatted.removesuffix(currency_suffix(format_spec))
        trailing = formatted[len(body) :]
        formatted = _apply_notation(body, notation) + trailing

    return affix_signed(formatted, prefix, suffix, sign_placement)


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


def is_d3_number_spec(spec: str) -> bool:
    """True when spec parses as d3-format number grammar. Never raises.

    A strftime directive, an unresolved alias, or a typo answers False, the
    same totality contract as ``is_d3_si_spec``: callers that bake a measured
    number layout from the spec use it to leave non-number formats alone.
    """
    if spec in PREDEFINED_NUMBER_NAMES or is_time_format(spec):
        return False
    try:
        _d3_parse(spec)
    except D3FormatError:
        return False
    return True


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


def _apply_notation(d3_str: str, notation: Notation) -> str:
    """Swap d3's SI letter (``1.5M``) for the active locale's word (``1.5 M``)."""
    for d3_sfx, word in active_format_locale().magnitudes(notation).items():
        if d3_str.endswith(d3_sfx):
            return d3_str[: -len(d3_sfx)] + word
    return d3_str
