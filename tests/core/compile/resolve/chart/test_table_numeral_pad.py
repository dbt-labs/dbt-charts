"""A table's decimal pad is composed in the family its digits paint in, the
board's numeral family, whatever the table's own text family is."""

from __future__ import annotations

import re

from ...._svg_render import render_board_to_svg

_BOARD = """
{style}
queries:
  q1:
    type: values
    rows:
      - {{month: Jan, share: 0.5}}
      - {{month: Feb, share: 0.25}}
      - {{month: Mar, share: 1}}
charts:
  grid:
    query: q1
    type: table
rows:
  - grid
"""

_SERIF = "'Source Serif 4', serif"
_SERIF_TABLE = f"""style:
  charts:
    table:
      font:
        family: "{_SERIF}"
"""
_SERIF_NUMERAL = f"""style:
  fonts:
    numeral:
      family: "{_SERIF}"
"""


def _pad_after_one(style: str) -> str:
    svg = render_board_to_svg(_BOARD.format(style=style))
    match = re.search(r">1([\u2000-\u200b]+)<", svg)
    assert match is not None
    return match.group(1)


def test_a_serif_table_font_does_not_change_the_numeral_pad() -> None:
    assert _pad_after_one(_SERIF_TABLE) == _pad_after_one("")


def test_the_pad_follows_the_numeral_family() -> None:
    assert _pad_after_one(_SERIF_NUMERAL) != _pad_after_one("")


def test_a_proportional_board_family_still_pads_a_mixed_depth_column() -> None:
    svg = render_board_to_svg(
        _BOARD.format(style="style:\n  font:\n    family: Roboto")
    )
    cell = re.search(
        r'<text\b[^>]*font-family="([^"]*)"[^>]*>(?:(?!</text>).)*?>1[\u2000-\u200b]+<',
        svg,
        re.S,
    )
    assert cell is not None
    assert cell.group(1).startswith("Roboto")
