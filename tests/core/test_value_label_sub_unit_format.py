"""House-register value labels paint sub-1 values in digits, not SI milli.

The theme's ``number`` axis format resolves to an SI spec; a value label
painting it straight through d3 turns 0.67 into "670m".
"""

import pytest

from ._svg_render import render_board_to_svg


def _board(family: str, mark: str, values: str, style: str = "") -> str:
    return f"""
title: sub-unit labels
queries:
  q:
    columns: [channel, v, w]
    values: {values}
charts:
  lbl:
    type: {family}
    query: q
    x: channel
    y: v
    style:{style}
      marks:
        {mark}:
          labels:
            visible: true
rows:
  - cols: [lbl]
"""


_SUB_UNIT = "[[A, 0.67, 0.42], [B, 0.18, 0.31]]"


@pytest.mark.parametrize(
    ("family", "mark"),
    [("bar", "bar"), ("line", "line"), ("area", "line"), ("scatter", "point")],
)
def test_inherited_number_format_paints_sub_unit_as_digits(
    family: str, mark: str
) -> None:
    svg = render_board_to_svg(_board(family, mark, _SUB_UNIT))
    assert "670m" not in svg
    assert "180m" not in svg
    assert ">0.67<" in svg
    assert ">0.18<" in svg


def test_explicit_number_label_format_paints_sub_unit_as_digits() -> None:
    board = _board("bar", "bar", _SUB_UNIT).replace(
        "visible: true", "visible: true\n            format: number"
    )
    svg = render_board_to_svg(board)
    assert ">0.67<" in svg
    assert "670m" not in svg


def test_layer_number_label_paints_sub_unit_as_digits() -> None:
    board = _board("bar", "bar", _SUB_UNIT).replace(
        "    style:",
        """    layers:
      - type: line
        y: w
        style:
          marks:
            line:
              labels:
                visible: true
                format: number
    style:""",
    )
    svg = render_board_to_svg(board)
    assert ">0.42<" in svg
    assert "420m" not in svg


def test_values_of_one_and_above_keep_house_si() -> None:
    svg = render_board_to_svg(_board("bar", "bar", "[[A, 1234, 0], [B, 0.5, 0]]"))
    assert ">1.23k<" in svg
    assert ">0.5<" in svg
