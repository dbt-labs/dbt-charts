"""A ``style.layout.<type>.gap`` authored on a nested board takes effect at that
board and does not inherit to boards nested further inside it.
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


def test_nested_entry_layout_gap_moves_its_rows() -> None:
    small = render_board_to_svg(_entry_board(4))
    large = render_board_to_svg(_entry_board(160))
    small_delta = _row_y(small, "cols.0.rows.1") - _row_y(small, "cols.0.rows.0")
    large_delta = _row_y(large, "cols.0.rows.1") - _row_y(large, "cols.0.rows.0")
    assert large_delta - small_delta == pytest.approx(160 - 4)


def test_nested_layout_gap_does_not_inherit_to_a_deeper_board() -> None:
    small = render_board_to_svg(_grandchild_board(4))
    large = render_board_to_svg(_grandchild_board(160))
    path = ("cols.0.rows.1.rows.1", "cols.0.rows.1.rows.0")
    small_inner = _row_y(small, path[0]) - _row_y(small, path[1])
    large_inner = _row_y(large, path[0]) - _row_y(large, path[1])
    assert large_inner == pytest.approx(small_inner)
