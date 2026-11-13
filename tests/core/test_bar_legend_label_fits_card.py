"""A top legend entry wider than its card on its own shortens and keeps the
full name as hover text, instead of painting past the card's edge. Entries
that fit one per row wrap there instead and are never cut."""

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
  - width: 180
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


def test_long_legend_entries_that_fit_one_per_row_wrap_there_uncut() -> None:
    svg = render_board_to_svg(_BOARD.replace("width: 180", "width: 300"))

    shown = re.findall(r"role-legend-label[^>]*><text[^>]*>([^<]*)<", svg)
    assert sorted(shown) == sorted(_NAMES)
