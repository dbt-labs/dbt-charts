"""A chart's title typography follows its card width, not its shrunk plot width."""

from __future__ import annotations

import re

from ._svg_render import render_board_to_svg

_BOARD = """
title: Title tier
theme: clarity
queries:
  q:
    type: values
    rows:
      - {product: Widgets, revenue: 410000, units: 1200, avg: 340}
      - {product: Gadgets, revenue: 380000, units: 900, avg: 420}
      - {product: Gizmos, revenue: 250000, units: 700, avg: 360}
charts:
  attached:
    query: q
    type: bar
    title: Attached Title
    x: product
    y: revenue
    support_table:
      - source: revenue
        format: currency_whole
        label: Revenue
      - source: units
        format: ",d"
        label: Units
      - source: avg
        format: currency_whole
        label: Avg/order
  plain:
    query: q
    type: bar
    title: Plain Title
    x: product
    y: revenue
rows:
  - cols:
      - attached
      - plain
"""


def _title_font(svg: str, title: str) -> tuple[str, str]:
    match = re.search(
        r'<text[^>]*font-family="([^"]*)"[^>]*font-size="([^"]*)"[^>]*>' + title, svg
    )
    assert match, title
    return match.group(1), match.group(2)


def test_support_table_chart_title_matches_its_sibling_card() -> None:
    svg = render_board_to_svg(_BOARD)

    assert _title_font(svg, "Attached Title") == _title_font(svg, "Plain Title")
