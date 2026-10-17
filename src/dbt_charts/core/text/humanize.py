"""A column name as display text: ``revenue_usd`` is ``revenue ($)``."""

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
                "ip",
            ]
        )
    },
    "enps": ("symbol", "eNPS"),
}


def _display(entry: tuple[Literal["symbol", "word"], str]) -> str:
    kind, text = entry
    return board_word(text) if kind == "word" else text


def slug_to_text(slug: str) -> str:
    """Tokenize a slug/ID into human-readable text — WITHOUT letter-case transforms.

    Handles common data column naming conventions:
    - snake_case and kebab-case → space-separated tokens
    - Single-case tokens lowercase; mixed-case tokens (``MTok``) keep their
      spelling
    - Unit suffixes: ``_usd`` → ``($)``, ``_pct`` → ``(%)``
    - Abbreviations: ``yoy`` → ``YoY``, ``arr`` → ``ARR``

    Letter-case (title/sentence/upper/lower) is NOT applied here — the caller
    supplies a ``font.case`` value and passes the result through
    ``apply_font_case`` (or ``field_display_text``).

    Examples:
        >>> slug_to_text("revenue_usd")
        'revenue ($)'
        >>> slug_to_text("yoy_growth_pct")
        'YoY growth (%)'
        >>> slug_to_text("logo_churn_pct")
        'logo churn (%)'
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
    return text
