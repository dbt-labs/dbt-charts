"""End-to-end: WARN-TREND-ON-BARS surfaces from render() once per bar trend."""

from __future__ import annotations

from unittest.mock import Mock

from dbt_charts.core.compile import compile
from dbt_charts.core.execute import Executor
from dbt_charts.core.render import render

_CODE = "WARN-TREND-ON-BARS"

_ROWS = [
    {"month": 1, "revenue": 5.0},
    {"month": 2, "revenue": 6.0},
    {"month": 3, "revenue": 5.5},
]


def _yaml(chart_type: str) -> str:
    return f"""
title: Probe
charts:
  c:
    query: q
    type: {chart_type}
    x: month
    y: revenue
    annotations:
      - type: trend
        fit: linear()
queries:
  q:
    sql: SELECT * FROM t
    source: test_source
rows:
  - c
"""


def _warnings(chart_type: str) -> list[str]:
    result = compile(_yaml(chart_type))
    assert result.success and result.board is not None, result.errors
    ok = Mock()
    ok.is_success = True
    ok.data = _ROWS
    ok.resolved_relations = None
    ok.truncated_reason = None
    mock_registry = Mock()
    mock_registry.execute.return_value = ok
    executor = Executor(
        result.board,
        adapter_registry=mock_registry,
        query_registry=result.query_registry,
    )
    rendered = render(result.board, executor, format="svg")
    assert rendered.board_error is None and rendered.output
    return [w.chart for w in rendered.warnings if w.code == _CODE]


def test_a_trend_on_bars_warns_once() -> None:
    assert _warnings("bar") == ["c"]


def test_a_trend_on_a_line_is_silent() -> None:
    assert _warnings("line") == []
