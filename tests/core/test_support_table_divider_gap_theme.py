"""The authored support_table divider gap is the painted gap under the divider."""

import re

from ._svg_render import render_board_to_svg

_BOARD = """
title: T
queries:
  q: {type: values, rows: [{cat: A, val: 10}, {cat: B, val: 4}, {cat: C, val: 7}]}
charts:
  c:
    query: q
    type: line
    x: cat
    y: val
    support_table:
      - source: val
rows: [c]
style:
  charts:
    support_table:
      divider:
        visible: true
        color: '#123456'
"""


def _geometry(svg: str) -> str:
    return re.sub(r"clip\d+-[0-9a-f]+-c", "clip", svg)


def test_authored_divider_gap_moves_the_painted_strip() -> None:
    default = _geometry(render_board_to_svg(_BOARD))

    assert _geometry(render_board_to_svg(_BOARD + "        gap: 4\n")) == default
    assert _geometry(render_board_to_svg(_BOARD + "        gap: 14\n")) != default
