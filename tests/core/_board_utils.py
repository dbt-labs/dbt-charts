"""Test helpers for constructing minimal Board objects.

Board.layout, Board.resolved_style, and Board.level are required fields. Tests
that need a Board container but don't exercise rendering use make_test_board()
instead of bare Board(id=...) construction to avoid repeating the required
fields at every call site.
"""

from __future__ import annotations

from functools import cache
from typing import Any

from dbt_charts.core.compile.config import get_theme_style
from dbt_charts.core.compile.models.board.normalized import Board, Layout
from dbt_charts.core.compile.models.board.resolved import ResolvedBoard
from dbt_charts.core.compile.models.chart.normalized import Chart
from dbt_charts.core.compile.models.chart.resolved import ResolvedChart
from dbt_charts.core.compile.models.style.context import ChartStyleContext
from dbt_charts.core.compile.models.style.resolved import ResolvedStyle


@cache
def _default_resolved_style() -> ResolvedStyle:
    """Return the default-theme ResolvedStyle, cached per process."""
    from dbt_charts.core.compile.resolve.style.board import resolve_style

    return resolve_style(get_theme_style())


@cache
def _default_chart_style_context() -> ChartStyleContext:
    """Return the default-theme ChartStyleContext, cached per process."""
    from dbt_charts.core.compile.resolve.style.board import resolve_chart_style_context

    return resolve_chart_style_context(get_theme_style())


def make_test_board(**kwargs: Any) -> Board:
    """Construct a minimal Board for unit tests that don't exercise rendering.

    Fills in the required layout, resolved_style, and chart_style_context
    fields with sensible defaults so callers only need to specify the fields
    their test cares about.
    """
    defaults: dict[str, Any] = {
        "id": "test",
        "layout": Layout(type="rows"),
        "resolved_style": _default_resolved_style(),
        "chart_style_context": _default_chart_style_context(),
        "level": 0,
    }
    return Board(**(defaults | kwargs))


@cache
def _default_resolved_board() -> ResolvedBoard:
    """Return a minimal ResolvedBoard, cached per process."""
    from dbt_charts.core.render.board_resolve import (
        build_resolved_board_static as resolve_board,
    )

    board = make_test_board()
    return resolve_board(board)


def make_test_resolved_chart(
    chart: Chart, data: list[dict] | None = None, width: float | None = None
) -> ResolvedChart:
    """Wrap a Chart in a ResolvedChart for warning-detector unit tests."""
    from dbt_charts.core.compile.resolve import resolve

    return resolve(
        chart,
        data or [],
        chart_style_context=_default_chart_style_context(),
        width=width,
    )


def make_test_resolved_board(**kwargs: Any) -> ResolvedBoard:
    """Construct a minimal ResolvedBoard for unit tests.

    Returns a resolved board based on the default theme, with optional field
    overrides applied via dataclasses.replace(). Useful for tests that need
    to pass a ResolvedBoard to WarningContext or similar production APIs.
    """
    import dataclasses

    base = _default_resolved_board()
    if not kwargs:
        return base
    return dataclasses.replace(base, **kwargs)


def apply_static_layout(board: Board) -> Board:
    """Run the static sizing pass on a board (no executor, no data).

    Wires calculate_layout_height + calculate_layout_items against the global
    theme (get_theme_style()). Use only for tests that need item.width/height
    populated without a full data-aware pipeline.

    Note: the root container box (width, margin) comes from get_theme_style(),
    not board.resolved_style — tests that override board width/margin on the board
    will see the container box diverge from the production path. Title math is
    excluded; that belongs to calculate_data_aware_layout.
    """
    from dbt_charts.core.compile.sizing import get_board_gap
    from dbt_charts.core.render.prose import plan_board_prose
    from dbt_charts.core.render.sizing import (
        calculate_layout_height,
        calculate_layout_items,
    )

    _theme = get_theme_style()
    container_width = float(_theme.frame.max_width)
    content_width = max(container_width - 2 * float(_theme.frame.margin), 0.0)
    min_height = float(_theme.frame.min_height)
    card_gap = float(_theme.frame.card_gap) if board.card_gap else 0.0
    gap = get_board_gap(board)
    variable_values = board.variable_defaults
    plan_board_prose(board, variable_values, content_width)
    container_height = calculate_layout_height(
        board.layout,
        card_gap,
        gap,
        min_height,
        available_width=content_width,
        variable_values=variable_values,
        resolved_style=board.resolved_style,
    )
    board.layout.width = container_width
    board.layout.height = container_height
    board.layout.content_width = content_width
    board.layout.content_height = container_height
    calculate_layout_items(
        board.layout,
        content_width,
        container_height,
        card_gap,
        gap,
        variable_values,
        resolved_style=board.resolved_style,
    )
    return board
