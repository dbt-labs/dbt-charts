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
from dbt_charts.core.compile.resolve.style.markdown_style import (
    prose_measure,
    title_min_content_width,
)
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
        resolved.title,
        resolved.level,
        resolved.style,
        {},
        None,
        root=True,
    )


def _title_height(resolved: ResolvedBoard, width: float) -> float:
    return get_title_height(
        resolved.title,
        width,
        {},
        level=resolved.level,
        resolved_style=resolved.style,
        base_dir=None,
        root=True,
    )


def test_a_title_stays_on_one_line_on_a_board_authored_wide_enough(
    local_project,
) -> None:
    wide = "style:\n  frame:\n    width: 900\n  footer:\n    visible: false\n"
    wide += "  timestamp:\n    visible: false\n"
    resolved = _resolved(_TITLE + wide + _QUERY + _TABLE + _ROWS, local_project)
    chrome = 2 * resolved.page_padding + 2 * resolved.card_padding
    inner = resolved.width - chrome
    assert inner >= _single_line(resolved)
    assert _title_height(resolved, inner) == _title_height(resolved, 10_000)


def test_no_title_no_floor(local_project) -> None:
    titled = _resolved(_TITLE + _QUIET + _QUERY + _TABLE + _ROWS, local_project)
    bare = _resolved(_QUIET + _QUERY + _TABLE + _ROWS, local_project)
    assert bare.width < titled.width - 100


def test_title_longer_than_max_width_is_capped_and_wraps_at_words(
    local_project,
) -> None:
    words = ["supercalifragilistic"] * 12
    yaml = f"title: {' '.join(words)}\n" + _QUIET + _QUERY + _TABLE + _ROWS
    resolved = _resolved(yaml, local_project)
    chrome = 2 * resolved.page_padding + 2 * resolved.card_padding
    assert resolved.width == pytest.approx(prose_measure(resolved.style) + chrome)
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
    style = resolved.style
    width = footer_text_width(style.footer, str(style.font.family))
    if timestamp:
        sample = "Data as of 12:00 UTC on 28 Dec 2026"
        width += style.timestamp.gap
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
    assert "1–10 / 30" in _svg(yaml, local_project)


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


_LONG_TITLE = "A board title of sixty-two characters, long enough to wrap!!"


def test_long_title_wraps_at_the_reading_width_with_no_charts(local_project) -> None:
    yaml = f"title: {_LONG_TITLE}\n" + _QUIET + "text: A short paragraph.\n"
    resolved = _resolved(yaml, local_project)
    chrome = 2 * resolved.page_padding + 2 * resolved.card_padding
    inner = resolved.width - chrome
    assert resolved.width <= prose_measure(resolved.style) + chrome
    assert _single_line(resolved) > inner
    assert _title_height(resolved, inner) > _title_height(resolved, 10_000)


def test_long_title_and_prose_share_the_prose_measure(local_project) -> None:
    body = " ".join(["Pipeline coverage held steady across every region."] * 12)
    yaml = f"title: {_LONG_TITLE}\n" + _QUIET + f"text: {body}\n"
    resolved = _resolved(yaml, local_project)
    chrome = 2 * resolved.page_padding + 2 * resolved.card_padding
    assert resolved.width - chrome == pytest.approx(prose_measure(resolved.style))


_TWENTY_LINES = "\n\n".join(f"  Line {n} of prose." for n in range(20))


def _columns(svg: str) -> int:
    return len(
        {0.0} | {float(m) for m in re.findall(r"translate\((\d[\d.]*), 0\)", svg)}
    )


def test_a_twenty_line_yaml_text_block_still_flows_into_columns(local_project) -> None:
    wide = "style:\n  frame:\n    width: 1200\n"
    yaml = wide + _QUIET.replace("style:\n", "", 1) + f"text: |\n{_TWENTY_LINES}\n"
    assert _columns(_svg(yaml, local_project)) >= 2


def test_a_title_word_wider_than_the_measure_widens_the_board(local_project) -> None:
    word = "Supercalifragilisticexpialidocious" * 2
    yaml = f"title: {word} and so on\n" + _QUIET + "text: x\n"
    resolved = _resolved(yaml, local_project)
    chrome = 2 * resolved.page_padding + 2 * resolved.card_padding
    assert prose_measure(resolved.style) < resolved.width - chrome
    assert resolved.width - chrome >= title_min_content_width(
        word,
        1,
        resolved.style,
        {},
        None,
        root=True,
    )
