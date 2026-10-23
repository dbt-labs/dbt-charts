"""ERR-CHART-PAINTED-NO-MARKS must not depend on how long the series is.

The guard once fired on a 37-point blank chart and stayed silent on an 11-point one
rendered from the same misconfiguration. The row count is not supposed to be an
input: a chart that received rows and painted nothing is broken at any length.

The blank render is the one the engine guards: an authored temporal x over values no
date parses from, which sends every mark of a step line to NaN.
"""

from __future__ import annotations

import calendar
from typing import Any
from unittest.mock import Mock

import pytest

from dbt_charts.core.compile import compile as compile_board
from dbt_charts.core.execute import Executor
from dbt_charts.core.render import render

_BOARD = """
queries:
  q:
    sql: SELECT * FROM t
    source: s
charts:
  c:
    title: probe
    query: q
    type: line
    x: x
    y: y
    style:
      marks:
        line:
          curve: step
      axis_x:
        type: temporal
rows:
  - c
"""


def _month_end(index: int) -> str:
    year, month = 2023 + index // 12, index % 12 + 1
    return f"{year}-{month:02d}-{calendar.monthrange(year, month)[1]}"


def _render_blank(rows: list[dict[str, Any]]) -> list[str]:
    result = compile_board(_BOARD)
    assert result.success and result.board is not None, result.errors
    query_result = Mock(
        is_success=True,
        data=rows,
        column_descriptions=None,
        resolved_relations=None,
        truncated_reason=None,
    )
    adapter_registry = Mock()
    adapter_registry.execute.return_value = query_result
    executor = Executor(
        result.board,
        adapter_registry=adapter_registry,
        query_registry=result.query_registry,
    )
    return [e.code for e in render(result.board, executor, format="svg").chart_errors]


@pytest.mark.parametrize("n_points", [3, 11, 37])
def test_guard_fires_at_every_series_length(n_points: int) -> None:
    rows: list[dict[str, Any]] = [{"x": f"p{i}", "y": 100 + i} for i in range(n_points)]
    assert _render_blank(rows) == ["ERR-CHART-PAINTED-NO-MARKS"]


@pytest.mark.parametrize("n_points", [3, 11, 37])
def test_guard_is_silent_once_the_rows_reach_their_buckets(n_points: int) -> None:
    """The same board over dates that parse: nothing to report."""
    rows: list[dict[str, Any]] = [
        {"x": _month_end(i), "y": 100 + i} for i in range(n_points)
    ]
    assert _render_blank(rows) == []
