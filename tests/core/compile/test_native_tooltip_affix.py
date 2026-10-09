"""A chart with no structured tooltip paints a measure format's affix on its hover
card: a faceted ``multiples:`` chart, a wide (multi-measure) scatter, a layered
scatter, and a histogram (``StructuredTooltipFeature.applies_to`` excludes them).
"""

from __future__ import annotations

import html
import re
from typing import Any

import pytest
import yaml

from .._svg_render import render_board_to_svg

_ROWS = [
    {"month": "Jan", "revenue": 1200.0, "cost": 500.0, "region": "east"},
    {"month": "Feb", "revenue": 2400.0, "cost": 600.0, "region": "west"},
]
_AFFIX = {"spec": ",.0f", "prefix": "EUR "}


def _cards(chart: dict[str, Any], style: dict[str, Any] | None = None) -> list[str]:
    board = {
        "title": "t",
        "queries": {"q": {"type": "values", "rows": _ROWS}},
        "charts": {"c": {"query": "q", **chart}},
        "rows": ["c"],
    }
    if style is not None:
        board["style"] = style
    svg = render_board_to_svg(yaml.safe_dump(board))
    labels = [html.unescape(x) for x in re.findall(r'aria-label="([^"]*)"', svg)]
    return [x for x in labels if ":" in x and "axis" not in x.lower()]


@pytest.mark.parametrize(
    "chart",
    [
        pytest.param(
            {"type": "bar", "x": "month", "y": "revenue"}
            | {"multiples": {"columns": "region"}},
            id="faceted_bar",
        ),
        pytest.param(
            {"type": "scatter", "x": "month", "y": ["revenue", "cost"]},
            id="wide_scatter",
        ),
        pytest.param(
            {"type": "scatter", "x": "cost", "y": "revenue"}
            | {"layers": [{"type": "line", "y": "cost"}]},
            id="layered_scatter",
        ),
    ],
)
def test_an_unstructured_hover_card_paints_the_affix(chart: dict[str, Any]) -> None:
    cards = _cards(chart | {"style": {"number_format": _AFFIX}})
    assert any("EUR 1,200" in card for card in cards), cards


def test_a_histogram_count_card_paints_the_affix() -> None:
    rows = {"spec": ",.0f", "suffix": " rows"}
    cards = _cards(
        {"type": "histogram", "x": "revenue", "style": {"number_format": rows}}
    )
    assert any("Count: 1 rows" in card for card in cards), cards


def test_a_board_tooltip_alias_reaches_a_faceted_card() -> None:
    cards = _cards(
        {"type": "bar", "x": "month", "y": "revenue"}
        | {"multiples": {"columns": "region"}},
        style={"formats": {"eur": _AFFIX}, "charts": {"tooltip": {"format": "eur"}}},
    )
    assert any("EUR 1,200" in card for card in cards), cards


def test_a_money_name_reaches_a_faceted_card() -> None:
    cards = _cards(
        {"type": "bar", "x": "month", "y": "revenue"}
        | {"multiples": {"columns": "region"}}
        | {"style": {"number_format": "currency_whole"}}
    )
    assert any("$1,200" in card for card in cards), cards


def test_an_explicit_x_format_keeps_the_scatter_x_plain() -> None:
    cards = _cards(
        {"type": "scatter", "x": "cost", "y": "revenue"}
        | {"layers": [{"type": "line", "y": "cost"}]}
        | {
            "style": {
                "number_format": _AFFIX,
                "axis_x": {"labels": {"format": ",.0f"}},
            }
        }
    )
    assert any(card.startswith("cost: 500;") for card in cards), cards
    assert any("cost: EUR 500" in card for card in cards), cards
