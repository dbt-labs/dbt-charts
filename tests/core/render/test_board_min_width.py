"""A board with no authored width never hugs narrower than the chrome it carries.

The table hug may shrink a board far below 800, so each element that needs a
minimum (title on one line, footer row, prose words, paginator row) lifts the
floor only when it is present; an authored ``frame.width`` still wins.
"""

from __future__ import annotations

import re
from collections.abc import Callable
from pathlib import Path

import pytest

from dbt_charts.core.compile import compile, focus_on_chart
from dbt_charts.core.compile.models.board.normalized import Board
from dbt_charts.core.compile.models.board.resolved import ResolvedBoard
from dbt_charts.core.compile.sizing import board_container_width, footer_text_width
from dbt_charts.core.execute.adapters import build_adapter_registry
from dbt_charts.core.execute.executor import Executor
from dbt_charts.core.font_measure import get_font_measurer
from dbt_charts.core.project import Project
from dbt_charts.core.render import render
from dbt_charts.core.render.board_resolve import (
    build_resolved_board,
    build_resolved_board_static,
)
from dbt_charts.core.render.sizing import get_title_height

_QUERY = """
queries:
  q:
    type: values
    rows:
      - {a: x, b: 1}
      - {a: y, b: 2}
"""
_TABLE = "charts:\n  t:\n    query: q\n    type: table\n"
_ROWS = "rows:\n  - t\n"
_TITLE = "title: A rather long board title that outgrows its tiny table\n"
_QUIET = "style:\n  footer:\n    visible: false\n  timestamp:\n    visible: false\n"
_TITLE_TEXT = "A rather long board title that outgrows its tiny table"


def _executor(board: Board, project: Project) -> Executor:
    return Executor(
        board,
        adapter_registry=build_adapter_registry(project),
        query_registry=None,
    )


def _compiled(yaml: str) -> Board:
    result = compile(yaml)
    assert result.success and result.board is not None, result.errors
    return result.board


def _resolved(yaml: str, local_project: Callable[..., Project]) -> ResolvedBoard:
    board = _compiled(yaml)
    resolved, _ = build_resolved_board(
        board, _executor(board, local_project(Path.cwd())), {}, render_first=False
    )
    return resolved


def _svg(yaml: str, local_project: Callable[..., Project]) -> str:
    board = _compiled(yaml)
    out = render(board, _executor(board, local_project(Path.cwd())), format="svg")
    assert out.board_error is None, out.board_error
    return out.output


def _single_line(resolved: ResolvedBoard) -> float:
    from dbt_charts.core.compile.resolve.style.markdown_style import (
        title_max_content_width,
    )

    return title_max_content_width(
        resolved.title, resolved.level, resolved.style, {}, None
    )


def test_title_sets_floor_and_stays_on_one_line(local_project) -> None:
    resolved = _resolved(_TITLE + _QUIET + _QUERY + _TABLE + _ROWS, local_project)
    chrome = 2 * resolved.page_padding + 2 * resolved.card_padding
    assert resolved.width >= _single_line(resolved) + chrome
    assert resolved.width < _single_line(resolved) + chrome + 12
    inner = resolved.width - chrome
    one_line = get_title_height(
        resolved.title,
        10_000,
        {},
        level=resolved.level,
        resolved_style=resolved.style,
        base_dir=None,
    )
    assert (
        get_title_height(
            resolved.title,
            inner,
            {},
            level=resolved.level,
            resolved_style=resolved.style,
            base_dir=None,
        )
        == one_line
    )


def test_no_title_no_floor(local_project) -> None:
    titled = _resolved(_TITLE + _QUIET + _QUERY + _TABLE + _ROWS, local_project)
    bare = _resolved(_QUIET + _QUERY + _TABLE + _ROWS, local_project)
    assert bare.width < titled.width - 100


def test_title_longer_than_max_width_is_capped_and_wraps_at_words(
    local_project,
) -> None:
    words = ["supercalifragilistic"] * 12
    yaml = f"title: {' '.join(words)}\n" + _QUIET + _QUERY + _TABLE + _ROWS
    assert _resolved(yaml, local_project).width == 1200
    svg = _svg(yaml, local_project)
    pieces = re.findall(r"<tspan[^>]*>([^<]*)</tspan>", svg)
    heard = {
        w for piece in pieces for w in piece.lower().split() if w.startswith("super")
    }
    assert heard == {"supercalifragilistic"}


def test_authored_width_beats_title_floor(local_project) -> None:
    yaml = _TITLE + "style:\n  frame:\n    width: 300\n" + _QUERY + _TABLE + _ROWS
    assert _resolved(yaml, local_project).width == 300


def _footer_row_width(resolved: ResolvedBoard, *, timestamp: bool) -> float:
    from dbt_charts.core.compile.config import get_chart_rendering

    style = resolved.style
    width = footer_text_width(style.footer, str(style.font.family))
    if timestamp:
        sample = "Data as of 12:00 UTC on 28 Dec 2026"
        width += get_chart_rendering().frame.footer_timestamp_gap_px
        width += get_font_measurer(str(style.font.family)).measure(
            sample, float(style.timestamp.font.size)
        )
    return width


def test_footer_and_timestamp_set_floor_only_when_visible(local_project) -> None:
    body = _QUERY + _TABLE + _ROWS
    hidden = _resolved("style:\n  footer:\n    visible: false\n" + body, local_project)
    footer_only = _resolved(
        "style:\n  timestamp:\n    visible: false\n" + body, local_project
    )
    both = _resolved(body, local_project)
    margin = 2 * both.page_padding
    assert hidden.width < footer_only.width + 1  # table may already be wider
    assert both.width > footer_only.width
    assert abs(both.width - (_footer_row_width(both, timestamp=True) + margin)) < 6
    assert footer_only.width >= _footer_row_width(footer_only, timestamp=False) + margin


def test_static_path_applies_the_same_floors() -> None:
    board = _compiled(_TITLE + _QUERY + _TABLE + _ROWS)
    static = build_resolved_board_static(board)
    assert static.width >= _single_line(static) + 2 * static.card_padding


_PROSE = """\
rows:
  - text: |
      | Region | Units |
      |--------|-------|
      | West   | 812   |
"""


def test_markdown_table_header_is_never_split_mid_word(local_project) -> None:
    svg = _svg(_QUIET + _QUERY + _TABLE + _PROSE + "  - t\n", local_project)
    assert ">Region<" in svg and "Regio<" not in svg


def test_paginated_narrow_table_keeps_row_label(local_project) -> None:
    rows = "\n".join(f"      - {{a: x{i}, b: {i}}}" for i in range(30))
    yaml = (
        f"queries:\n  q:\n    type: values\n    rows:\n{rows}\n"
        + "charts:\n  t:\n    query: q\n    type: table\n"
        + "    style:\n      pagination:\n        enabled: true\n        page_rows: 10\n"
        + _QUIET
        + _ROWS
    )
    assert "Rows 1–10 of 30" in _svg(yaml, local_project)


_KPI = "charts:\n  k:\n    query: q\n    type: kpi\n    value: b\n"


@pytest.mark.parametrize(
    "layout",
    ["rows:\n  - k\n", "cols:\n  - k\n"],
    ids=["rows", "cols"],
)
def test_focus_drops_the_board_title_width_floor(layout: str) -> None:
    """Focus renders the chart alone: the source board's title does not widen it."""
    board = _compiled(_TITLE + _QUERY + _KPI + layout)
    margin = 2 * float(board.resolved_style.frame.margin)
    view_width = board_container_width(board, board.variable_defaults)
    assert view_width > 300 + margin  # the title, not the KPI, sets the board
    focused = focus_on_chart(board, "k")
    assert focused.resolved_style.frame.width is None
    assert board_container_width(focused, focused.variable_defaults) < view_width
