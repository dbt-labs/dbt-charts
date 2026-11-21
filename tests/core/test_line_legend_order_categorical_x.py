"""The legend lists series in the order of their color slots (largest total
first), not by their value at the axis's last category or alphabetically."""

import re

from ._svg_render import render_board_to_svg

# Series A has the larger total (110 against 90) but ends lowest; B ends highest.
_BOARD = """
title: T
queries:
  q:
    type: values
    rows:
      - {x: Before, s: A, v: 90}
      - {x: Before, s: B, v: 10}
      - {x: After, s: A, v: 20}
      - {x: After, s: B, v: 80}
charts:
  c:
    query: q
    type: line
    x: x
    y: v
    color: s
    style:
      legend:
        position:
          edge: top
rows:
  - c
"""


def test_legend_orders_series_by_color_slot() -> None:
    svg = render_board_to_svg(_BOARD)

    legend = re.findall(r'role-legend-label"[^>]*>(?:<[^>]+>)*([AB])<', svg)
    assert legend == ["A", "B"]
