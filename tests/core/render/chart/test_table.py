"""Table text paints the family of the named font its theme slot binds."""

from __future__ import annotations

import re

import pytest

from ..._svg_render import render_board_to_svg

_BOARD = """
style:
  fonts:
    {name}:
      family: Zork
queries:
  q1:
    type: values
    rows:
      - {{a: x, b: 1}}
charts:
  t1:
    query: q1
    type: table
rows:
  - t1
"""


@pytest.mark.parametrize("name", ["label", "body", "caption"])
def test_header_follows_label_only(name: str) -> None:
    svg = render_board_to_svg(_BOARD.format(name=name))
    header = re.search(r'<text[^>]*font-weight="600"[^>]*>\s*A\b', svg)
    assert header is not None
    assert ("Zork" in header.group(0)) == (name == "label")


@pytest.mark.parametrize("name", ["label", "body", "caption"])
def test_cells_follow_label_only(name: str) -> None:
    svg = render_board_to_svg(_BOARD.format(name=name))
    cell = re.search(r'<text[^>]*data-col="0"[^>]*>', svg)
    assert cell is not None
    assert ("Zork" in cell.group(0)) == (name == "label")


def test_header_keeps_its_family_when_the_fit_cascade_steps_down() -> None:
    columns = [f"c{i}" for i in range(3)]
    row = ", ".join(f"{c}: 1234567890123456" for c in columns)
    board = f"""
queries:
  q1:
    type: values
    rows:
      - {{{row}}}
charts:
  t1:
    query: q1
    type: table
    style:
      font:
        size: 14
      header:
        font:
          family: Zork
      preferred_width: 200
      column_layout:
        default_width: 40
rows:
  - t1
"""
    svg = render_board_to_svg(board)
    header = re.search(r'<text[^>]*font-weight="600"[^>]*>\s*C0\b', svg)
    assert header is not None
    assert "Zork" in header.group(0)
    cell = re.search(r'<text[^>]*data-col="0"[^>]*>', svg)
    assert cell is not None
    assert 'font-size="11"' in cell.group(0)
