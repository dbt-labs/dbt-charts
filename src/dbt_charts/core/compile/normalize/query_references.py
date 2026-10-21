"""Which named queries a compiled board reads, and where it defines them."""

from collections.abc import Iterator

from dbt_charts.core.compile.models.board.normalized import Board
from dbt_charts.core.compile.models.chart.normalized import (
    AreaChart,
    BarChart,
    LineChart,
    ScatterChart,
)
from dbt_charts.core.compile.models.query.normalized import SqlQuery
from dbt_charts.core.compile.models.variable.authored import SingleRowBoolProbe
from dbt_charts.core.compile.template.jinja import text_query_references


def boards_in(board: Board) -> Iterator[Board]:
    """The board and every board nested in its layout."""
    yield board
    for item in board.layout.items:
        if item.board is not None:
            yield from boards_in(item.board)


def defined_query_names(board: Board) -> set[str]:
    """Names the tree defines, minus the compiler's own ``_``-prefixed ones."""
    return {
        name
        for scope in boards_in(board)
        for name in scope.queries
        if not name.startswith("_")
    }


def referenced_query_names(board: Board) -> set[str]:
    """Names read by a chart, layer, variable, layout probe, text or another query.

    Every defined chart counts, placed or not: an unplaced chart has its own
    warning, and focusing a board narrows only the layout.
    """
    names: set[str] = set()
    for scope in boards_in(board):
        names |= text_query_references([scope.title, scope.text])
        for chart in scope.charts.values():
            if chart.query_name:
                names.add(chart.query_name)
            names.update(chart.text_queries.values())
            if isinstance(chart, BarChart | LineChart | AreaChart | ScatterChart):
                names.update(
                    layer.query.removeprefix("queries.")
                    for layer in chart.layers
                    if layer.query
                )
        for var in scope.variables.values():
            if option_query := var.get_option_query():
                names.add(option_query.removeprefix("queries."))
            if isinstance(var.enabled, SingleRowBoolProbe):
                names.add(var.enabled.query.removeprefix("queries."))
        for item in scope.layout.items:
            if isinstance(item.visible, SingleRowBoolProbe):
                names.add(item.visible.query.removeprefix("queries."))
        for query in scope.queries.values():
            if isinstance(query, SqlQuery):
                names |= text_query_references([query.sql, query.setup_sql])
    return names
