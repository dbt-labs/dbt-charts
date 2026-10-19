from types import SimpleNamespace

from dbt_charts.core.compile.models.primitives import BoardLinks

"""Regression: a chart that fails only in the data-format walk still reports.

`render()` draws every format, so a chart normally fails twice over — once
through the draw's `execute_query`, once through the walk's `execute_chart`.
These pin the two ends of the merge: no double-report when both fail, and no
silent drop when only the walk does (which would hand a caller status "ok"
for a payload carrying an inline `_error`).
"""

from collections.abc import Callable
from typing import Any
from unittest.mock import MagicMock, patch

from dbt_charts.core.compile.models.board.normalized import Layout, LayoutItem
from dbt_charts.core.diagnostics import ERR_FILE_NOT_FOUND
from dbt_charts.core.diagnostics.execution import ExecutionError, QueryError
from dbt_charts.core.execute.executor import Executor
from dbt_charts.core.render import (
    board_resolve as _board_resolve_mod,
    render,
    renderer as _renderer_mod,
)
from dbt_charts.core.render.board_resolve import build_resolved_board_static

from .._board_utils import make_test_board
from .._svg_render import PINNED_RENDER_TIME
from .test_partial_success import _compile_two_chart_board

_GOOD = [{"value": 42}]


def _render(mock):
    board = _compile_two_chart_board()
    mock._query_errors = {}
    mock.cache_hit_ats = []
    mock.rendered_at = PINNED_RENDER_TIME
    mock.is_cached.return_value = False
    with (
        patch.object(_renderer_mod, "execute_queries_parallel"),
        patch.object(
            _board_resolve_mod,
            "build_resolved_board",
            return_value=(build_resolved_board_static(board), {}),
        ),
    ):
        return render(board, executor=mock, format="json")


def test_walk_only_failure_reaches_chart_errors():
    """The draw succeeds for every chart; only the walk fails one."""
    mock = MagicMock(spec=Executor, board=SimpleNamespace(links=BoardLinks()))
    mock.execute_query.side_effect = lambda query_name, variables=None: _GOOD

    def _chart(chart, variables):
        if chart.query_name == "q_good":
            return _GOOD
        raise ExecutionError("walk-only failure")

    mock.execute_chart.side_effect = _chart
    result = _render(mock)

    assert [d.fields.get("chart_id") for d in result.chart_errors] == ["bad"]
    assert "_error" in (result.output or "")


def test_failure_in_both_passes_is_reported_once():
    """The same broken chart fails in draw and walk alike — one diagnostic."""
    mock = MagicMock(spec=Executor, board=SimpleNamespace(links=BoardLinks()))

    def _query(query_name, variables=None):
        if query_name == "q_good":
            return _GOOD
        raise ExecutionError("boom")

    def _chart(chart, variables):
        if chart.query_name == "q_good":
            return _GOOD
        raise ExecutionError("boom")

    mock.execute_query.side_effect = _query
    mock.execute_chart.side_effect = _chart
    result = _render(mock)

    assert [d.fields.get("chart_id") for d in result.chart_errors] == ["bad"]


def _nested_duplicate_id_board(make_chart: Callable[..., Any]):
    """Two nested boards, each with a *different* chart sharing the id 'revenue'.

    Ids are unique within a board, not across a nested-board tree (see
    data_format.py's duplicate-id guard) — so this collision is legitimate
    input, not a shape the compiler would reject.
    """
    chart_a = make_chart("kpi", id="revenue", query_name="q_a", value="value")
    chart_b = make_chart("kpi", id="revenue", query_name="q_b", value="value")
    nested_a = make_test_board(
        title="A",
        layout=Layout(
            type="rows",
            items=[LayoutItem(type="chart", chart=chart_a, width=300, height=150)],
            width=300,
            height=150,
        ),
    )
    nested_b = make_test_board(
        title="B",
        layout=Layout(
            type="rows",
            items=[LayoutItem(type="chart", chart=chart_b, width=300, height=150)],
            width=300,
            height=150,
        ),
    )
    return make_test_board(
        title="Root",
        layout=Layout(
            type="rows",
            items=[
                LayoutItem(type="board", board=nested_a, width=300, height=150),
                LayoutItem(type="board", board=nested_b, width=300, height=150),
            ],
            width=600,
            height=150,
        ),
    )


def test_same_id_different_chart_walk_only_failure_is_not_suppressed(
    make_chart: Callable[..., Any],
) -> None:
    """Two nested boards' charts share the id 'revenue' but are different
    charts. One fails only in the draw (a distinctly-coded error); the other
    fails only in the walk (a different code). Keying the merge on bare
    chart_id would drop the second as a false "already reported" duplicate —
    it must key on (chart_id, code) instead.
    """
    board = _nested_duplicate_id_board(make_chart)
    mock = MagicMock(spec=Executor, board=SimpleNamespace(links=BoardLinks()))
    mock._query_errors = {}
    mock.cache_hit_ats = []
    mock.rendered_at = PINNED_RENDER_TIME
    mock.is_cached.return_value = False

    draw_exc = QueryError("file missing", "q_a")
    draw_exc.code = ERR_FILE_NOT_FOUND

    def _query(query_name, variables=None):
        if query_name == "q_a":
            raise draw_exc
        return _GOOD

    def _chart(chart, variables):
        if chart.query_name == "q_a":
            return _GOOD
        raise ExecutionError("walk-only failure")

    mock.execute_query.side_effect = _query
    mock.execute_chart.side_effect = _chart

    with (
        patch.object(_renderer_mod, "execute_queries_parallel"),
        patch.object(
            _board_resolve_mod,
            "build_resolved_board",
            return_value=(build_resolved_board_static(board), {}),
        ),
    ):
        result = render(board, executor=mock, format="json")

    codes = {(d.fields.get("chart_id"), d.code) for d in result.chart_errors}
    assert ("revenue", ERR_FILE_NOT_FOUND.code) in codes
    assert ("revenue", "ERR-INTERNAL") in codes
