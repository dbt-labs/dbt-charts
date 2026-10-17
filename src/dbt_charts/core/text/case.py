"""Letter-case transform dispatcher for dbt charts typography.

Applies at render time — after Jinja resolution but before the string reaches
an SVG <text> node or a Vega-Lite spec string field.

Supported values (matching FontStyle.case):
  none       — no-op; string emitted as authored / as normalized by slug_to_text.
  upper      — str.upper() (Unicode-aware).
  lower      — str.lower() (Unicode-aware).
  sentence   — first character uppercased, remainder unchanged, except a
               leading token that is exactly `dbt`, which stays lowercase.
               Preserves acronyms and proper nouns that the author
               already capitalized.  Does NOT lowercase the rest of the
               string — that would corrupt ARR, iOS, etc.
  title      — the board language's title rule (`case.title_style` in its word
               catalog, applied by the engine): English keeps its small words
               lowercase mid-title, other cased languages capitalize the first
               word only, and CJK languages leave the text as authored.
  slug       — Machine identifier form: spaces/hyphens → underscore, lower.
               "Order Status" → "order_status".
  camel      — camelCase: first word lower, subsequent words capitalized.
               "order status" → "orderStatus".
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import TYPE_CHECKING, Literal

from dbt_charts import _engine
from dbt_charts.core.text.format_d3 import active_language
from dbt_charts.core.text.humanize import slug_to_text

if TYPE_CHECKING:
    from dbt_charts.core.compile.models.primitives import ResolvedFontStyle

CaseValue = Literal["none", "sentence", "title", "upper", "lower", "slug", "camel"]

# "dbt" is stylized lowercase always (dbt Labs' own convention).
_FORCE_LOWERCASE: frozenset[str] = frozenset({"dbt"})


def apply_case(text: str, case: CaseValue) -> str:
    """Apply a letter-case transform to *text* and return the result.

    Args:
        text: The string to transform.  May be empty.
        case: One of 'none', 'upper', 'lower', 'sentence', 'title', 'slug', 'camel'.

    Returns:
        The transformed string.
    """
    if case == "none":
        return text
    if case == "upper":
        return text.upper()
    if case == "lower":
        return text.lower()
    if case == "sentence":
        # Cautious sentence case: uppercase only the first character, leave
        # the rest unchanged (preserves acronyms/proper nouns), except a
        # leading forced-lowercase token such as "dbt".
        first, sep, rest = text.partition(" ")
        if first.lower() in _FORCE_LOWERCASE:
            return first.lower() + sep + rest
        return text[:1].upper() + text[1:]
    if case == "title":
        return _engine.apply_case(active_language(), text, "title")
    if case == "slug":
        # Machine form: spaces/hyphens → underscore, all lowercase.
        return text.replace(" ", "_").replace("-", "_").lower()
    if case == "camel":
        # camelCase: lower the first word, capitalize subsequent words.
        words = text.replace("-", " ").replace("_", " ").split()
        if not words:
            return text
        return words[0].lower() + "".join(w.capitalize() for w in words[1:])


def apply_case_to_runs(runs: list[str], case: CaseValue) -> list[str]:
    """Apply *case* to one string that inline formatting split into *runs*.

    The runs are cased as one string, so a word split by emphasis is one word
    and the title's first and last words are the string's, then cut back at
    the runs' edges. A transform that changes the string's length (``ß``
    upper-cased, ``İ`` lower-cased) moves those edges; then each run is cased
    on its own instead.
    """
    cased = apply_case("".join(runs), case)
    if len(cased) != sum(len(run) for run in runs):
        return [apply_case(run, case) for run in runs]
    pieces: list[str] = []
    start = 0
    for run in runs:
        pieces.append(cased[start : start + len(run)])
        start += len(run)
    return pieces


def apply_font_case(text: str, font: ResolvedFontStyle) -> str:
    """Apply the letter-case transform specified by font.case.

    Args:
        text: The string to transform.
        font: A fully-resolved font; font.case drives the transform.

    Returns:
        The transformed string.
    """
    return apply_case(text, font.case)


def field_title(field: str, field_labels: Mapping[str, str]) -> str:
    """Text a surface paints for a field an author gave no surface-level label.

    The chart's authored ``field_labels`` entry, verbatim; else the tokenized
    column name (``order_month`` → ``order month``) with no case transform.
    """
    label = field_labels.get(field)
    return label if label is not None else slug_to_text(field)


def field_display_text(
    field: str, field_labels: Mapping[str, str], font: ResolvedFontStyle
) -> str:
    """``field_title`` for a slot whose derived text takes its font's case
    (legend titles, tooltip series rows). An authored label stays verbatim."""
    label = field_labels.get(field)
    return label if label is not None else apply_font_case(slug_to_text(field), font)


def inferred_display_name(
    name: str,
    *,
    font: ResolvedFontStyle | None = None,
    case: CaseValue | None = None,
) -> str:
    """Return the display text inferred from an object key.

    This is the single shared path for comparing or rendering inferred labels
    from YAML object names. Callers that have resolved typography should pass
    ``font``; compile-time callers can pass the known case transform.
    """
    if font is not None and case is not None:
        raise ValueError("Pass either font or case, not both.")

    normalized = slug_to_text(name)
    if font is not None:
        return apply_font_case(normalized, font)
    return apply_case(normalized, case or "none")
