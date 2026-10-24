"""The legend lists series by their value at the axis's last category, not at
the alphabetically greatest one."""

import re

from ._svg_render import render_board_to_svg

# "Before" is the axis's first category, "After" its last; alphabetically it is
# the other way round. Series B ends highest, A lowest.
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
      endpoint_labels:
        visible: false
rows:
  - c
"""


def test_legend_orders_series_by_the_last_category_on_the_axis() -> None:
    svg = render_board_to_svg(_BOARD)

    legend = re.findall(r'role-legend-label"[^>]*>(?:<[^>]+>)*([AB])<', svg)
    assert legend == ["B", "A"]
