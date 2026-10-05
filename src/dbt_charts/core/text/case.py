"""Letter-case transform dispatcher for dbt charts typography.

Applies at render time — after Jinja resolution but before the string reaches
an SVG <text> node or a Vega-Lite spec string field.

Supported values (matching FontStyle.case):
  none       — no-op; string emitted as authored / as normalized by slug_to_text.
  upper      — str.upper() (Unicode-aware).
  lower      — str.lower() (Unicode-aware).
  sentence   — first character uppercased, remainder unchanged.
               Preserves acronyms and proper nouns that the author
               already capitalized.  Does NOT lowercase the rest of the
               string — that would corrupt ARR, iOS, etc.
  title      — Chicago/Gruber algorithm via the `titlecase` library, with
               a callback that extends the small-words list (per, with,
               from, into, onto, upon, out, off, down, up) and a
               standalone-all-caps guard so single acronym slugs
               (``ARR``, ``YOY``, ``USD``, ``KPI``) survive the
               library's whole-line ``all_caps`` detection. Lowercases
               stopwords (a, an, and, as, at, but, by, en, for, if, in,
               of, on, or, the, to, v[.], vs[.], via — plus the
               extended set above). Always capitalizes first and last
               words. Preserves any token with internal capitals
               (iPhone, YoY).
  slug       — Machine identifier form: spaces/hyphens → underscore, lower.
               "Order Status" → "order_status".
  camel      — camelCase: first word lower, subsequent words capitalized.
               "order status" → "orderStatus".
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Literal

from titlecase import titlecase as _titlecase  # pyright: ignore[reportMissingTypeStubs]

from dbt_charts.core.utils import slug_to_text

if TYPE_CHECKING:
    from dbt_charts.core.compile.models.primitives import ResolvedFontStyle

CaseValue = Literal["none", "sentence", "title", "upper", "lower", "slug", "camel"]

# Editorial-style small words the NYT default list misses. Without this set
# the titlecase library's default would capitalize ``per``, ``with``,
# ``from``, etc. as ordinary words — wrong for editorial title
# case ("Revenue Per Region" should be "Revenue per Region").
_EXTRA_SMALL_WORDS: frozenset[str] = frozenset(
    {"per", "with", "from", "into", "onto", "upon", "out", "off", "down", "up"}
)


# Tokens that must never be uppercased by the library's all-consonant
# acronym heuristic. "dbt" is stylized lowercase always (dbt Labs' own
# convention) — never an acronym, but its three consonants otherwise
# trip the same rule that promotes "mrr"/"sql".
_FORCE_LOWERCASE: frozenset[str] = frozenset({"dbt"})


def _acronym_callback(word: str, **_kwargs: object) -> str | None:
    """Callback for the `titlecase` library — preserves embedded all-caps
    acronyms the library's built-in detection doesn't cover, and forces
    known non-acronym tokens (``_FORCE_LOWERCASE``) to stay lowercase.

    The library has a special-case for all-consonant tokens of length > 2
    (uppercases them — that's how ``MRR``, ``SQL`` survive). It misses
    acronyms with vowels (``ARR``, ``YOY``, ``USD``, ``KPI``, ``CEO``,
    ``ETF``) and any all-caps token of length ≤ 2 (``HR``, ``IT``,
    ``OS``). This callback returns the original casing for any all-caps
    token of length > 1; ``None`` defers to the library default.

    Extended small-words (``per``, ``with``, ``from``, etc.) are handled
    by a POST-process in ``apply_case`` rather than this callback,
    because callback return values are marked Immutable and bypass the
    library's first/last-word capitalization pass (so returning
    ``"per"`` for a first-word ``Per`` would wrongly leave it
    lowercase).
    """
    if word.lower() in _FORCE_LOWERCASE:
        return word.lower()
    if word.isupper() and len(word) > 1 and word.isalpha():
        return word
    return None


def _lowercase_extra_small_words(text: str) -> str:
    """Post-process ``text`` (already in title case) to lowercase the
    editorial-style small words in ``_EXTRA_SMALL_WORDS`` when they are
    NOT the first or last whitespace-separated token. Mirrors the
    titlecase library's ``small_first_last=True`` contract for the
    extended set.

    Skips tokens that are already all-caps and alphabetic: an author
    who wrote ``"Scale UP Now"`` (or whose data carries ``UP``/``OUT``
    as an acronym) gets that intent preserved by the callback at the
    library stage; the post-process must not clobber it.
    """
    words = text.split(" ")
    if len(words) <= 2:
        # All positions are first-or-last — nothing to lowercase.
        return text
    for i in range(1, len(words) - 1):
        w = words[i]
        if w.lower() in _EXTRA_SMALL_WORDS and not (w.isupper() and w.isalpha()):
            words[i] = w.lower()
    return " ".join(words)


def apply_case(text: str, case: CaseValue) -> str:
    """Apply a letter-case transform to *text* and return the result.

    Args:
        text: The string to transform.  May be empty.
        case: One of 'none', 'upper', 'lower', 'sentence', 'title', 'slug', 'camel'.

    Returns:
        The transformed string.

    Raises:
        ValueError: If *case* is not a recognized value.
    """
    if case == "none":
        return text
    if case == "upper":
        return text.upper()
    if case == "lower":
        return text.lower()
    if case == "sentence":
        # Cautious sentence case: uppercase only the first character, leave
        # the rest unchanged.  Preserves acronyms/proper nouns.
        return text[:1].upper() + text[1:] if text else text
    if case == "title":
        # The titlecase library detects a whole-string ``all_caps`` shape
        # at line scope (titlecase/__init__.py:102) and lowercases it
        # BEFORE per-word logic runs, so a callback alone can't preserve
        # a standalone acronym slug (``ARR`` → ``Arr``). Guard that case
        # explicitly (#172).
        if text and text.isupper() and " " not in text and text.isalpha():
            return text
        # Library handles built-in small words + first/last capitalization.
        # Callback covers embedded-acronym preservation (#172). Post-process
        # extends the small-words list with the editorial set the NYT
        # default list misses (#7d).
        return _lowercase_extra_small_words(
            _titlecase(text, callback=_acronym_callback)
        )
    if case == "slug":
        # Machine form: spaces/hyphens → underscore, all lowercase.
        return text.replace(" ", "_").replace("-", "_").lower()
    if case == "camel":
        # camelCase: lower the first word, capitalize subsequent words.
        words = text.replace("-", " ").replace("_", " ").split()
        if not words:
            return text
        return words[0].lower() + "".join(w.capitalize() for w in words[1:])
    raise ValueError(
        f"Unknown case: {case!r}. Expected one of 'none', 'upper', "
        "'lower', 'sentence', 'title', 'slug', 'camel'."
    )


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


def format_display_text(
    text: str,
    *,
    from_slug: bool,
    font: ResolvedFontStyle,
) -> str:
    """Two-step render pipeline: optional slug tokenization then font case.

    Args:
        text: The raw input string — either an authored string or a slug.
        from_slug: True when *text* is a field slug / identifier that needs
            ``slug_to_text`` tokenization first (underscore→space, unit/abbreviation
            expansion).  False when *text* is already human-readable authored text.
        font: The resolved font for this slot; font.case drives the case transform.

    Returns:
        The display-ready string.
    """
    normalized = slug_to_text(text) if from_slug else text
    return apply_font_case(normalized, font)


def default_axis_title(field: str) -> str:
    """Default title for an axis whose title an author never set.

    Tokenizes the bound column name (``order_month`` → ``order month``) but
    skips the font's case transform — a default axis title should read like
    the column it encodes, not like a title-cased headline. That's what makes
    it distinct from ``format_display_text``, which every OTHER slug-derived
    label (chart title, legend title, series/tooltip labels) still goes
    through: an author who wants title case on an axis writes ``x_label``/
    ``y_label`` explicitly.

    Args:
        field: The bound column name (never an authored label — callers pass
            an authored ``x_label``/``y_label`` straight through instead of
            calling this).

    Returns:
        The tokenized, case-preserved default title text.
    """
    return slug_to_text(field)


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
