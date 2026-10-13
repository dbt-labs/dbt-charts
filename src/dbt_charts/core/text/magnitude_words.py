"""The words a named locale's magnitudes print: thousand, million, billion,
trillion, in the analytic and the narrative register.

Narrative is the abbreviated word (CLDR's short compact form: ``Mio.``,
``mln``). Analytic is the extra-short form: ``k`` and ``M`` where they read,
the language's own billion and trillion where ``B`` would name 10^12. CLDR
groups ja/zh/ko by 10^4, so those rows use the 10^3-tier words.
A word carries its own gap: the non-breaking space, or none.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from dbt_charts.core.text.format_d3 import NumberLocaleName

MagnitudeWords = tuple[str, str, str, str]
"""Thousand, million, billion, trillion."""

_Registers = tuple[MagnitudeWords, MagnitudeWords]
"""Analytic, narrative."""


def _nbsp(*words: str) -> MagnitudeWords:
    thousand, million, billion, trillion = ("\u00a0" + w for w in words)
    return thousand, million, billion, trillion


def _both(words: MagnitudeWords) -> _Registers:
    return words, words


_BY_LANGUAGE: dict[str, _Registers] = {
    "ar": _both(_nbsp("ألف", "مليون", "مليار", "ترليون")),
    "ca": _both(_nbsp("k", "M", "mil\u00a0M", "B")),
    "cs": (_nbsp("k", "M", "mld", "bil"), _nbsp("tis.", "mil.", "mld.", "bil.")),
    "da": (_nbsp("k", "M", "mia", "bio"), _nbsp("t", "mio.", "mia.", "bio.")),
    "de": (_nbsp("k", "M", "Mrd", "Bio"), _nbsp("Tsd.", "Mio.", "Mrd.", "Bio.")),
    "en": (("K", "M", "B", "T"), ("k", "mn", "bn", "trn")),
    "es": _both(_nbsp("mil", "M", "mil\u00a0M", "B")),
    "fi": (_nbsp("k", "M", "mrd", "bilj"), _nbsp("t.", "milj.", "mrd.", "bilj.")),
    "fr": _both(_nbsp("k", "M", "Md", "Bn")),
    "he": _both(_nbsp("K", "M", "B", "T")),
    "hu": _both(_nbsp("E", "M", "Mrd", "B")),
    "it": (_nbsp("k", "M", "Mld", "Bln"), _nbsp("mila", "Mln", "Mld", "Bln")),
    "ja": _both(("千", "百万", "十億", "兆")),
    "ko": _both(("천", "백만", "십억", "조")),
    "mk": _both(_nbsp("илј.", "мил.", "милј.", "бил.")),
    "nl": (
        _nbsp("K", "M", "mld", "bln"),
        ("K", "\u00a0mln.", "\u00a0mld.", "\u00a0bln."),
    ),
    "pl": (_nbsp("k", "M", "mld", "bln"), _nbsp("tys.", "mln", "mld", "bln")),
    "pt": (_nbsp("k", "M", "B", "T"), _nbsp("mil", "mi", "bi", "tri")),
    "ru": (_nbsp("к", "М", "млрд", "трлн"), _nbsp("тыс.", "млн", "млрд", "трлн")),
    "sl": _both(_nbsp("tis.", "mio.", "mrd.", "bil.")),
    "sv": _both(_nbsp("tn", "mn", "md", "bn")),
    "uk": (_nbsp("к", "М", "млрд", "трлн"), _nbsp("тис.", "млн", "млрд", "трлн")),
    "zh": _both(("千", "百万", "十亿", "万亿")),
}

_BY_REGION: dict[str, _Registers] = {
    "es-BO": _both(_nbsp("K", "M", "mil\u00a0M", "B")),
    "es-MX": _both(_nbsp("k", "M", "mil\u00a0M", "B")),
    "fr-CA": _both(_nbsp("k", "M", "G", "T")),
    "pt-PT": _both(_nbsp("mil", "M", "mM", "Bi")),
}


def magnitude_words(name: NumberLocaleName) -> _Registers:
    """``name``'s analytic and narrative words."""
    if name in _BY_REGION:
        return _BY_REGION[name]
    return _BY_LANGUAGE[name.split("-")[0]]
