"""Query name collection helpers.

Stage: EXECUTE
Purpose: Walk compiled board trees to enumerate the query names needed before rendering.

Entry Points:
    - collect_all_query_names(board) -> set[str]
    - collect_layout_chart_query_names(board) -> set[str]

These are used by the parallel pre-execution path (renderer.py) to know which
queries to fire before rendering begins.
"""

from collections.abc import Sequence
from typing import Any

from dbt_charts.core.compile.models.board.normalized import Board
from dbt_charts.core.compile.models.board.resolved import ResolvedBoard
from dbt_charts.core.compile.models.variable.authored import (
    SingleRowBoolProbe,
    Variable,
)
from dbt_charts.core.compile.template.jinja import text_query_references


def _collect_var_queries(var: Variable, names: set[str]) -> None:
    """Add all query names referenced by a variable (options + enabled probe)."""
    q = var.get_option_query()
    if q:
        names.add(q)
    if isinstance(var.enabled, SingleRowBoolProbe):
        names.add(var.enabled.query)


def collect_layout_chart_query_names(board: Board | ResolvedBoard) -> set[str]:
    """Walk the layout tree and return query names directly used by charts.

    Shared layout-walk utility used by the parallel pre-execution path
    in ``renderer.py`` (which does not expand transitive deps, since
    ``{{ queries.X }}`` is Jinja-inlined).
    """
    names: set[str] = set()

    def _walk(items: Sequence[Any]) -> None:
        for item in items:
            if item.chart and item.chart.query_name:
                names.add(item.chart.query_name)
            if item.board and item.board.layout:
                _walk(item.board.layout.items)

    _walk(board.layout.items)

    return names


def collect_all_query_names(board: Board) -> set[str]:
    """Walk the compiled board and return ALL named queries needed before rendering.

    Covers chart queries, layered-chart layer queries (layout-reachable only),
    variable option queries (``options.query`` and top-level ``query``),
    variable ``enabled`` SingleRowBoolProbe queries, layout-item ``visible``
    SingleRowBoolProbe queries, and queries read by board ``title:``/``text:``
    and by chart display text. Descends into nested boards.

    All names returned are keys in ``board.queries``. Callers pass the result
    directly to ``execute_queries_parallel``.
    """
    names: set[str] = set(text_query_references([board.title, board.text]))

    def _walk(items: list[Any], variables: dict[str, Variable]) -> None:
        for var in variables.values():
            _collect_var_queries(var, names)
        for item in items:
            if item.chart:
                if item.chart.query_name:
                    names.add(item.chart.query_name)
                names.update(item.chart.text_queries.values())
                if getattr(item.chart, "layers", None):
                    for layer in item.chart.layers:
                        if layer.query:
                            names.add(layer.query)
            if isinstance(item.visible, SingleRowBoolProbe):
                names.add(item.visible.query)
            if item.board:
                names.update(text_query_references([item.board.title, item.board.text]))
                if item.board.layout:
                    _walk(item.board.layout.items, item.board.variables)

    _walk(board.layout.items, board.variables)

    return names
