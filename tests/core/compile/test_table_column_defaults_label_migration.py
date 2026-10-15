"""0.9.0 -> 0.10.0: ``column_defaults.label`` is removed."""

from __future__ import annotations

import pytest

from dbt_charts.core.compile import compile
from dbt_charts.core.compile.models.chart.normalized import TableChart

_BOARD = """title: T
queries:
  q1: {{sql: "select 1 as a, 2 as b", source: test}}
{board_style}charts:
  c1:
    type: table
    query: q1
{chart_style}rows: [c1]
"""

_CHART_STYLE = """    style:
      column_defaults:
        label: Shared
        align: center
      columns:
        b:
          label: Own
"""
_BOARD_STYLE = """style:
  charts:
    table:
      column_defaults:
        label: Shared
        align: center
"""


@pytest.mark.parametrize(
    ("board_style", "chart_style"),
    [("", _CHART_STYLE), (_BOARD_STYLE, "")],
    ids=["chart", "board"],
)
def test_column_defaults_label_is_dropped_with_a_note(
    board_style: str, chart_style: str
) -> None:
    result = compile(_BOARD.format(board_style=board_style, chart_style=chart_style))
    assert result.success, [f"{e.code}: {e.message}" for e in result.errors]
    assert result.board is not None
    chart = result.board.charts["c1"]
    assert isinstance(chart, TableChart)
    assert any("Set label on each column" in w.message for w in result.warnings)
    assert "Shared" not in result.board.model_dump_json()
    assert "center" in result.board.model_dump_json()
