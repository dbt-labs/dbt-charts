"""``email`` / ``url`` cannot be redefined and are rejected in Vega slots."""

from __future__ import annotations

from dbt_charts.core.compile.compiler import compile as compile_board

from .._text_format_board import ROWS, text_format_board


def test_style_formats_cannot_redefine_a_text_name() -> None:
    board = text_format_board(ROWS, style="style:\n  formats:\n    email: '.2f'\n")
    result = compile_board(board)
    assert not result.success
    assert result.errors[0].code == "ERR-FORMAT-TEXT-REDEFINED"


def test_text_name_in_a_vega_slot_is_rejected() -> None:
    chart = (
        "    type: bar\n    x: name\n    y: n\n    style:\n"
        "      axis_y:\n        labels:\n          format: email\n"
    )
    rows = "      - {name: a, n: 1}\n"
    result = compile_board(text_format_board(rows, chart, formats="      n: integer\n"))
    assert not result.success
    assert result.errors[0].code == "ERR-FORMAT-TEXT-IN-VEGA-SLOT"
