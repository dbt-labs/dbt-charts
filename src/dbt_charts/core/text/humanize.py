"""A column name as display text: ``revenue_usd`` is ``Revenue ($)``."""

from __future__ import annotations

from typing import Literal

from dbt_charts.core.text.format_d3 import board_word

# A unit suffix or abbreviation is a symbol that reads the same in every
# language, or the id of the catalog message the board language spells it with.
_UNIT_SUFFIXES: dict[str, tuple[Literal["symbol", "word"], str]] = {
    "usd": ("symbol", "($)"),
    "eur": ("symbol", "(€)"),
    "gbp": ("symbol", "(£)"),
    "pct": ("symbol", "(%)"),
    "percent": ("symbol", "(%)"),
    "ratio": ("word", "humanize.ratio"),
    "cnt": ("word", "humanize.count"),
    "num": ("symbol", "(#)"),
    "qty": ("word", "humanize.qty"),
    "lbs": ("symbol", "(lb)"),
    "kg": ("symbol", "(kg)"),
    "km": ("symbol", "(km)"),
    "mi": ("symbol", "(mi)"),
    "mph": ("symbol", "(mph)"),
    "secs": ("symbol", "(s)"),
    "seconds": ("symbol", "(s)"),
    "mins": ("symbol", "(min)"),
    "hrs": ("symbol", "(h)"),
}

_ABBREVIATIONS: dict[str, tuple[Literal["symbol", "word"], str]] = {
    "yoy": ("word", "humanize.yoy"),
    "mom": ("word", "humanize.mom"),
    "qoq": ("word", "humanize.qoq"),
    "avg": ("word", "humanize.avg"),
    **{
        token: ("symbol", token.upper())
        for token in (
            [
                "arr",
                "mrr",
                "nrr",
                "ltv",
                "cac",
                "roi",
                "roas",
                "csat",
                "nps",
                "api",
                "id",
                "url",
                "sql",
                "hr",
                "mpg",
                "ip",
            ]
        )
    },
    "enps": ("symbol", "eNPS"),
}


# "dbt" is stylized lowercase always (dbt Labs' own convention).
_FORCE_LOWERCASE: frozenset[str] = frozenset({"dbt"})


def sentence_case(text: str) -> str:
    """Uppercase the first character only; a leading ``dbt`` stays lowercase.

    The rest is left alone so acronyms and proper nouns survive.
    """
    first, sep, rest = text.partition(" ")
    if first.lower() in _FORCE_LOWERCASE:
        return first.lower() + sep + rest
    return text[:1].upper() + text[1:]


def _display(entry: tuple[Literal["symbol", "word"], str]) -> str:
    kind, text = entry
    return board_word(text) if kind == "word" else text


def slug_to_text(slug: str) -> str:
    """Tokenize a slug/ID into human-readable sentence-case text.

    Handles common data column naming conventions:
    - snake_case and kebab-case → space-separated tokens
    - Single-case tokens lowercase; mixed-case tokens (``MTok``) keep their
      spelling
    - Unit suffixes: ``_usd`` → ``($)``, ``_pct`` → ``(%)``, ``_in_lbs`` → ``(lb)``
    - Abbreviations: ``yoy`` → ``YoY``, ``arr`` → ``ARR``

    Only the first letter is capitalized (``sentence_case``): this is the one
    derived-label default every surface shows. A caller's ``font.case`` is
    applied on top of it by ``apply_font_case`` (or ``field_display_text``).

    Examples:
        >>> slug_to_text("revenue_usd")
        'Revenue ($)'
        >>> slug_to_text("yoy_growth_pct")
        'YoY growth (%)'
        >>> slug_to_text("logo_churn_pct")
        'Logo churn (%)'
        >>> slug_to_text("avg_deal_size")
        'Avg deal size'
    """
    if not slug:
        return ""

    tokens = slug.replace("-", "_").replace(" ", "_").split("_")
    tokens = [t for t in tokens if t]

    suffix_label = ""
    if len(tokens) > 1 and tokens[-1].lower() in _UNIT_SUFFIXES:
        suffix_label = _display(_UNIT_SUFFIXES[tokens[-1].lower()])
        tokens = tokens[:-1]
        if tokens[-1].lower() == "in":  # weight_in_lbs reads "weight (lb)"
            tokens = tokens[:-1]

    words = []
    for token in tokens:
        lower = token.lower()
        if lower in _ABBREVIATIONS:
            words.append(_display(_ABBREVIATIONS[lower]))
        elif token != lower and token != token.upper():
            words.append(token)
        else:
            words.append(lower)

    text = " ".join(words)
    if suffix_label:
        text = f"{text} {suffix_label}".strip()
    # A first word spelled with a capital (``iPhone``, ``YoY``) is already as intended.
    first_word = text.split(" ", 1)[0]
    return text if first_word != first_word.lower() else sentence_case(text)
