"""The boards and queries a validation pass walks."""

from __future__ import annotations

from collections.abc import Iterator

from dbt_charts.core.compile.models.board.normalized import Board
from dbt_charts.core.compile.models.query.normalized import Query


def boards(board: Board) -> Iterator[Board]:
    """*board* and every board nested in its layout."""
    yield board
    for item in board.layout.items:
        if item.board is not None:
            yield from boards(item.board)


def board_queries(board: Board) -> Iterator[Query]:
    """The queries one board owns: its named queries, then its charts' inline
    queries (a callout has none)."""
    yield from board.queries.values()
    for chart in board.charts.values():
        if chart.type != "callout" and chart.query_is_inline and chart.query:
            yield chart.query
