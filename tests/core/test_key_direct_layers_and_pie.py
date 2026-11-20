"""`style.key`: a layer opts out of the key, and a pie's key is its wedge labels, its table or nothing."""

from __future__ import annotations

import json
import re
from typing import Any

import pytest

from ._svg_render import render_board_svg_result

_ROWS: list[dict[str, Any]] = [
    {"m": m, "v": 1 + i, "t": 2 + i}
    for i, m in enumerate(["2024-01-01", "2024-02-01", "2024-03-01"])
]


def _layered(layer_key: bool) -> str:
    return f"""
queries:
  q:
    type: values
    rows: {json.dumps(_ROWS)}
charts:
  c:
    type: line
    query: q
    x: m
    y: v
    style:
      key:
        type: direct
    layers:
      - type: line
        y: t
        label: Target label
        key: {str(layer_key).lower()}
rows:
  - c
"""


def test_a_layer_declining_the_key_is_left_off_the_direct_rail() -> None:
    named = render_board_svg_result(_layered(True)).output
    unnamed = render_board_svg_result(_layered(False)).output

    rail_label = re.compile(r"<text[^>]*>Target label</text>")
    assert rail_label.search(named)
    assert not rail_label.search(unnamed)


def _pie_texts(
    kind: str, key: str, rows: list[dict[str, Any]], style: str = ""
) -> list[str]:
    board = f"""
queries:
  q:
    type: values
    rows: {json.dumps(rows)}
charts:
  c:
    type: {kind}
    query: q
    theta: v
    color: n
    style:
{style}      key:
        {key}
rows:
  - c
"""
    svg = render_board_svg_result(board).output
    return [
        re.sub("<[^>]*>", "", text)
        for text in re.findall(r"<text[^>]*>(.*?)</text>", svg, re.S)
    ]


_EVEN_ROWS = [
    {"n": "Apples", "v": 40},
    {"n": "Pears", "v": 30},
    {"n": "Plums", "v": 30},
]


@pytest.mark.parametrize("kind", ["pie", "donut"])
def test_a_pie_key_is_wedge_labels_a_full_table_or_nothing(kind: str) -> None:
    """`direct` labels the wedges (the name rides in the label, not a table
    cell); `legend` is the table alone; a hidden key names nothing."""
    direct = _pie_texts(kind, "type: direct", _EVEN_ROWS)
    legend = _pie_texts(kind, "type: legend", _EVEN_ROWS)
    hidden = _pie_texts(kind, "visible: false", _EVEN_ROWS)

    assert any("40% Apples" in text for text in direct)
    assert "Apples" not in direct
    assert "Apples" in legend
    assert not any("40% Apples" in text for text in legend)
    assert not any("Apples" in text for text in hidden)


def test_a_hidden_donut_key_never_falls_back_to_the_table() -> None:
    """A total too wide for the hole sends an `auto` donut to the table; a hidden key is the wheel."""
    rows = [{"n": r["n"], "v": r["v"] * 100_000_000} for r in _EVEN_ROWS]
    style = "      inner_radius: 0.15\n"

    auto = _pie_texts("donut", "type: auto", rows, style)
    hidden = _pie_texts("donut", "visible: false", rows, style)

    assert "Apples" in auto
    assert not any("Apples" in text for text in hidden)


def test_a_direct_pie_key_resolves_to_the_overflow_table() -> None:
    """Auto sends a pie no slice of which clears the label floor to the full
    table; `direct` keeps the wedge labels and lists the unlabeled slices
    under the hybrid overflow heading."""
    rows = [{"n": f"S{i:02d}", "v": 1} for i in range(14)]

    auto = _pie_texts("pie", "type: auto", rows)
    direct = _pie_texts("pie", "type: direct", rows)

    assert "Too small to label" not in auto
    assert "Too small to label" in direct
