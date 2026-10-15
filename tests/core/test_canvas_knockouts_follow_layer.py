"""Knockout strokes and fills follow the effective canvas, not the base theme's.

``theme.background`` self-tokens (bar borders, slice and stacked-area strokes,
point fills, geoshape strokes) must resolve against the canvas a project layer
or board declares, so a dark layer carries no light rings.
"""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest

from dbt_charts.core.compile import compile as compile_board
from dbt_charts.core.compile.compiler import compile_file

DARK = "#1a1a19"

_ROWS = "rows:\n  - text: hello\n"

_KNOCKOUT_LEAVES = [
    "marks.bar.border.color",
    "marks.slice.stroke.color",
    "marks.area.stacked.stroke.color",
    "marks.point.fill",
    "marks.geoshape.stroke.color",
    "table.spark.bar.label.fill",
]


def _leaf(root: Any, dotted: str) -> Any:
    for part in dotted.split("."):
        root = getattr(root, part)
    return root


def _assert_leaves(board: Any, expected: str) -> None:
    charts = board.chart_style_context.pre_style.charts
    for path in _KNOCKOUT_LEAVES:
        assert _leaf(charts, path) == expected, path


def _assert_leaves_follow_canvas(board: Any) -> None:
    assert board.resolved_style.background == DARK
    _assert_leaves(board, DARK)


def test_board_background_override_drives_knockouts() -> None:
    result = compile_board(f"extends: clarity\nstyle:\n  background: '{DARK}'\n{_ROWS}")
    assert result.success, result.errors
    _assert_leaves_follow_canvas(result.board)


def test_project_layer_canvas_drives_knockouts(in_memory_project: Callable) -> None:
    project = in_memory_project(
        Path("/tmp/test-dark-layer"),
        {
            "charts/_dark.yml": f"extends: clarity\nstyle:\n  background: '{DARK}'\n",
            "charts/board.yml": f"extends: ./_dark.yml\n{_ROWS}",
        },
    )
    result = compile_file(project.path("charts/board.yml").read_board())
    assert result.success, result.errors
    _assert_leaves_follow_canvas(result.board)


def test_authored_charts_leaf_and_category_color_resolve_to_hex() -> None:
    result = compile_board(
        "extends: clarity\n"
        f"style:\n  background: '{DARK}'\n"
        "  charts:\n"
        "    axis:\n      grid:\n        color: theme.background\n"
        "    category_colors:\n"
        "      region:\n        values:\n          A: theme.background\n"
        f"{_ROWS}"
    )
    assert result.success, result.errors
    ctx = result.board.chart_style_context
    assert ctx.axis.grid.color == DARK
    assert ctx.category_color_pins["region"].values["A"] == DARK


def test_keep_self_tokens_defaults_off() -> None:
    from dbt_charts.core.compile.config import get_theme_style
    from dbt_charts.core.compile.resolve.style.palette import UnknownColorError
    from dbt_charts.core.compile.resolve.style.tokens import _resolve_color_tokens

    theme = get_theme_style("clarity")
    with pytest.raises(UnknownColorError):
        _resolve_color_tokens(
            theme.charts.marks.bar.border.model_copy(
                update={"color": "theme.background"}
            ),
            theme.palettes,
            theme.roles,
        )


_DARK_ROOT = f"extends: clarity\nstyle:\n  background: '{DARK}'\n"


def test_unpainted_nested_board_inside_dark_root_reads_the_root_canvas() -> None:
    result = compile_board(
        f"{_DARK_ROOT}rows:\n  - title: Child\n    style:\n      font:\n"
        "        family: Arial\n    text: hi\n"
    )
    assert result.success, result.errors
    child = result.board.layout.items[0].board
    assert child.resolved_style.background == "transparent"
    _assert_leaves(child, DARK)


def test_painted_nested_board_inside_dark_root_reads_its_own_composite() -> None:
    result = compile_board(
        f"{_DARK_ROOT}rows:\n  - title: Child\n    style:\n"
        "      background: 'rgba(255, 255, 255, 0.1)'\n    text: hi\n"
    )
    assert result.success, result.errors
    child = result.board.layout.items[0].board
    canvas = child.chart_style_context.board_canvas
    assert canvas != DARK
    _assert_leaves(child, canvas)


def test_cards_that_differ_from_the_page_set_the_knockout() -> None:
    cream_page_white_cards = compile_board(
        "extends: clarity\nstyle:\n  background: '#f6f1e7'\n"
        f"  charts:\n    background: white\n{_ROWS}"
    )
    assert cream_page_white_cards.success, cream_page_white_cards.errors
    _assert_leaves(cream_page_white_cards.board, "#FFFFFF")

    light_page_dark_cards = compile_board(
        f"extends: clarity\nstyle:\n  charts:\n    background: '{DARK}'\n{_ROWS}"
    )
    assert light_page_dark_cards.success, light_page_dark_cards.errors
    _assert_leaves(light_page_dark_cards.board, DARK)


def test_self_token_in_a_keyed_collection_resolves() -> None:
    result = compile_board(
        f"{_DARK_ROOT}  charts:\n    table:\n      columns:\n        rev:\n"
        f"          background: theme.background\n{_ROWS}"
    )
    assert result.success, result.errors
    columns = result.board.chart_style_context.table.columns
    assert columns["rev"].background == DARK
