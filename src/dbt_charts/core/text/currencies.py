"""The currencies ``regional.currency`` accepts, and how each one prints.

Data only: ``with_symbol`` swaps an entry's symbol into a locale's currency
template. Adding a currency is one row here and one in ``CurrencyCode``.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

CurrencyCode = Literal[
    "USD",
    "EUR",
    "GBP",
    "JPY",
    "CAD",
    "AUD",
    "CHF",
    "SEK",
    "NOK",
    "DKK",
    "INR",
    "CNY",
    "BRL",
    "MXN",
]

_NBSP = "\u00a0"


@dataclass(frozen=True)
class Currency:
    """How one ISO 4217 code prints: the minor unit and the symbol."""

    digits: int
    symbol: str


def with_symbol(template: tuple[str, str], symbol: str) -> tuple[str, str]:
    """A d3 locale's ``currency`` template with ``symbol`` in place of its own:
    the template's side and gap, and a gap a word symbol needs that the
    template's own symbol did not (``CHF 12``, not ``CHF12``)."""
    before, after = template
    if before.strip():
        gap = before[len(before.rstrip()) :]
        return symbol + (gap or (_NBSP if symbol[-1].isalpha() else "")), ""
    if after.strip():
        gap = after[: len(after) - len(after.lstrip())]
        return "", (gap or (_NBSP if symbol[0].isalpha() else "")) + symbol
    raise ValueError("the locale's currency template has no symbol to replace")


CURRENCIES: dict[CurrencyCode, Currency] = {
    "USD": Currency(2, "$"),
    "EUR": Currency(2, "€"),
    "GBP": Currency(2, "£"),
    "JPY": Currency(0, "¥"),
    "CAD": Currency(2, "CA$"),
    "AUD": Currency(2, "A$"),
    "CHF": Currency(2, "CHF"),
    "SEK": Currency(2, "kr"),
    "NOK": Currency(2, "kr"),
    "DKK": Currency(2, "kr"),
    "INR": Currency(2, "₹"),
    "CNY": Currency(2, "CN¥"),
    "BRL": Currency(2, "R$"),
    "MXN": Currency(2, "MX$"),
}
