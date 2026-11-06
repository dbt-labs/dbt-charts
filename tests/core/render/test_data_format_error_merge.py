from types import SimpleNamespace

from dbt_charts.core.compile.models.primitives import BoardLinks

"""Regression: a chart whose query failed is reported once, not once per pass.

`render()` draws every format, so the draw and the data-format walk both read the
failed query's collected error. The merge keeps one diagnostic per chart.
"""

from unittest.mock import MagicMock, patch

from dbt_charts.core.diagnostics.execution import ExecutionError
from dbt_charts.core.execute.query_results import QueryResults
from dbt_charts.core.render import (
    board_resolve as _board_resolve_mod,
)
from dbt_charts.core.render.board_resolve import build_resolved_board_static

from ..._lookup import render_via
from ..._results import rows_result
from .._svg_render import PINNED_RENDER_TIME
from .test_partial_success import _compile_two_chart_board

_GOOD = [{"value": 42}]


def _render(mock):
    board = _compile_two_chart_board()
    mock.cache_hit_ats = []
    mock.rendered_at = PINNED_RENDER_TIME
    with (
        patch.object(
            _board_resolve_mod,
            "build_resolved_board",
            return_value=(build_resolved_board_static(board), {}),
        ),
    ):
        return render_via(mock, board, format="json")


def test_failure_in_both_passes_is_reported_once():
    """The same broken chart fails in draw and walk alike — one diagnostic."""
    mock = MagicMock(spec=QueryResults, board=SimpleNamespace(links=BoardLinks()))

    def _query(query_name, variables=None):
        if query_name == "q_good":
            return _GOOD
        raise ExecutionError("boom")

    mock.execute_query_result.side_effect = lambda query_name, variables=None: (
        rows_result(_query(query_name, variables))
    )
    result = _render(mock)

    assert [d.fields.get("chart_id") for d in result.chart_errors] == ["bad"]
