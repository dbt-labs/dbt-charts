"""0.12.0 -> 0.13.0: a horizontal bar's ``axis_y.position: left | right`` becomes
``axis_x.position`` (the ``v0_13_0`` live move), through ``compile()``.
"""

from __future__ import annotations

import pytest

from dbt_charts.core.compile.compiler import compile

BOARD = """\
title: t
queries:
  q:
    type: values
    columns: [cat, v]
    values:
      - [a, 3]
{board_style}charts:
  c:
    type: bar
    query: q
    x: cat
    y: v
    style:
{chart_style}rows:
  - c
"""
OWN = "      orientation: horizontal\n      axis_y:\n        position: right\n"
BOARD_DEFAULT = "style:\n  charts:\n    bar:\n      orientation: horizontal\n"


def _compiled(board_style: str, chart_style: str):
    result = compile(BOARD.format(board_style=board_style, chart_style=chart_style))
    assert result.board is not None, result.errors
    assert not result.errors
    return result


@pytest.mark.parametrize(
    ("board_style", "chart_style"),
    [
        ("", OWN),
        (BOARD_DEFAULT, "      axis_y:\n        position: right\n"),
    ],
    ids=["own-orientation", "board-default"],
)
def test_a_horizontal_bars_axis_y_position_compiles_as_the_category_edge(
    board_style: str, chart_style: str
) -> None:
    result = _compiled(board_style, chart_style)
    style = result.board.charts["c"].style
    assert style.axis_x.position == "right"
    assert style.axis_y is None
    assert any("axis_x.position" in w.message for w in result.warnings)


def test_a_vertical_bars_axis_y_position_is_left_alone() -> None:
    result = _compiled("", "      axis_y:\n        position: right\n")
    style = result.board.charts["c"].style
    assert style.axis_y.position == "right"
    assert style.axis_x is None
