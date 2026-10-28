"""A callout written inline in rows/cols compiles like any inline chart."""

from __future__ import annotations

from dbt_charts.core.compile.compiler import compile as compile_board

from ._svg_render import render_board_to_svg

BOARD = """
title: T
rows:
  - type: callout
    title: Heads up
    message: Static message.
"""


def test_inline_callout_in_rows_compiles() -> None:
    result = compile_board(BOARD)
    assert result.success, result.errors
    board = result.board
    assert board is not None
    chart = board.layout.items[0].chart
    assert chart is not None
    assert chart.type == "callout"


def test_inline_callout_renders_its_message() -> None:
    assert "Static message." in render_board_to_svg(BOARD)
