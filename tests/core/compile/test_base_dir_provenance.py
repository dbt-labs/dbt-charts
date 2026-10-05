"""`Board.base_dir` records the directory of the file its content was written in.

Provenance only: no resolution reads it yet, and it is never serialized.
"""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path

from pydantic import TypeAdapter

from dbt_charts.core.compile import compile
from dbt_charts.core.compile.models.board.normalized import Board
from dbt_charts.core.project import Project


def test_board_records_where_it_was_written(
    in_memory_project: Callable[..., Project],
) -> None:
    kpi = "type: kpi\n    value: n\n    query:\n      sql: SELECT 1 AS n\n      source: db"
    project = in_memory_project(
        Path("/p"),
        {
            "dbt_charts.yml": "",
            "charts/lib/kpis.yml": f"charts:\n  c:\n    {kpi}\n",
            "charts/board.yml": (
                f"charts:\n  c: lib/kpis.charts.c\n  local:\n    {kpi}\n"
                "rows: [c, local]\n"
            ),
        },
    )
    path = project.path("charts/board.yml")
    result = compile(path.read_text(), base_dir=path.parent)
    assert result.success, result.errors
    board = result.board
    assert board.base_dir == "charts"
    assert "base_dir" not in repr(TypeAdapter(Board).dump_python(board, mode="json"))
