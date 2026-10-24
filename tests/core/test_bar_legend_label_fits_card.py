"""A top legend wider than its card shortens its entries and keeps the full
name as hover text, instead of painting past the card's edge."""

import re

from ._svg_render import render_board_to_svg

_NAMES = ("Alpha with a long descriptive label", "Bravo with a long descriptive label")

_ALPHA, _BRAVO = _NAMES
_BOARD = f"""
title: T
queries:
  q:
    type: values
    rows:
      - {{m: A, s: "{_ALPHA}", v: 10}}
      - {{m: A, s: "{_BRAVO}", v: 20}}
      - {{m: B, s: "{_ALPHA}", v: 15}}
      - {{m: B, s: "{_BRAVO}", v: 25}}
charts:
  c:
    query: q
    type: bar
    x: m
    y: v
    color: s
cols:
  - width: 300
    rows:
      - c
"""


def test_long_legend_entries_are_cut_and_reveal_the_full_name_on_hover() -> None:
    svg = render_board_to_svg(_BOARD)

    cut = re.findall(
        r"role-legend-label[^>]*><text[^>]*>([^<]*…)<title>([^<]*)</title>", svg
    )
    assert [full for _, full in cut] == list(_NAMES)
    assert all(full.startswith(shown.rstrip("…").rstrip()) for shown, full in cut)
