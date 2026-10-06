from __future__ import annotations

import re

from ._svg_render import render_board_to_svg

_BOARD = """
queries:
  q:
    type: values
    rows:
      - {category: A, value: 70}
      - {category: B, value: 30}
charts:
  c:
    query: q
    type: donut
    color: category
    theta: value
    total:
      visible: true
      label: Total
rows:
  - c
"""


def test_donut_center_total_has_no_per_datum_label() -> None:
    svg = render_board_to_svg(_BOARD)

    total_texts = re.findall(r"<text\b([^>]*)>(?:Total|100)</text>", svg)
    assert len(total_texts) == 2, total_texts
    assert not [attrs for attrs in total_texts if "aria-label" in attrs], total_texts
