"""A ``style.layout.<type>.gap`` authored on a board spaces the items of every
nested board beneath it, styled or not.
"""

from __future__ import annotations

import pytest

from ._svg_render import authored_boxes, render_board_to_svg


def _row_y(svg: str, authored_path: str) -> float:
    return authored_boxes(svg, "dbt-box-outer")[authored_path][1]


def _entry_board(gap: float) -> str:
    return f"""\
cols:
  - style:
      layout:
        rows:
          gap: {gap}
    rows:
      - text: Row one
      - text: Row two
"""


def _grandchild_board(entry_gap: float) -> str:
    return f"""\
cols:
  - style:
      layout:
        rows:
          gap: {entry_gap}
    rows:
      - text: Row A
      - rows:
          - text: Inner one
          - text: Inner two
"""


def _root_gap_board(root_gap: float, nested_style: str) -> str:
    return f"""\
style:
  layout:
    rows:
      gap: {root_gap}
rows:
  - title: Section
{nested_style}    rows:
      - text: Row one
      - text: Row two
"""


@pytest.mark.parametrize("nested_style", ["", "    style:\n      muted: '#888888'\n"])
def test_root_layout_gap_spaces_a_nested_board(nested_style: str) -> None:
    small = render_board_to_svg(_root_gap_board(4, nested_style))
    large = render_board_to_svg(_root_gap_board(160, nested_style))
    path = ("rows.0.rows.1", "rows.0.rows.0")
    small_delta = _row_y(small, path[0]) - _row_y(small, path[1])
    large_delta = _row_y(large, path[0]) - _row_y(large, path[1])
    assert large_delta - small_delta == pytest.approx(160 - 4)


def test_nested_layout_gap_inherits_to_a_deeper_board() -> None:
    small = render_board_to_svg(_grandchild_board(4))
    large = render_board_to_svg(_grandchild_board(160))
    path = ("cols.0.rows.1.rows.1", "cols.0.rows.1.rows.0")
    small_inner = _row_y(small, path[0]) - _row_y(small, path[1])
    large_inner = _row_y(large, path[0]) - _row_y(large, path[1])
    assert large_inner - small_inner == pytest.approx(160 - 4)
