"""A date-range filter on the column behind x pins the x axis to the range.

Drives real duckdb queries through resolve and the emitters. The bound is proven
from the query's own lineage; every unproven shape must leave the axis as it was.
"""

from __future__ import annotations

from datetime import date
from pathlib import Path
from typing import Any

import duckdb

from dbt_charts.cli.filesystem_project import FilesystemProject
from dbt_charts.core.compile import compile as compile_yaml
from dbt_charts.core.compile.config import (
    ProjectSourcesConfig,
)
from dbt_charts.core.execute.env import build_exec_env

from ...._run import Run

# Mondays: the first and last of March 2026 (Sun the 1st, Tue the 31st) both sit
# in weeks with no order, and the week of the 16th has none either.
_ORDERS = ["2026-03-02", "2026-03-09", "2026-03-23"]
_MARCH = [date(2026, 3, 1), date(2026, 3, 31)]


def _yaml(
    chart_type: str = "bar",
    *,
    select: str = "date_trunc('week', created_at) AS week",
    group_by: str = "1",
    where: str = "{{ filter_date_range('created_at', date_range) }}",
    x: str = "week",
    table: str = "orders",
    style: str = "",
) -> str:
    return f"""
variables:
  date_range:
    input: daterange
queries:
  q:
    type: sql
    source: mem
    sql: |
      SELECT {select}, SUM(n) AS total
      FROM {table}
      WHERE {where}
      GROUP BY {group_by}
      ORDER BY 1
charts:
  c:
    query: q
    type: {chart_type}
    x: {x}
    y: total
{style}
rows:
  - c
"""


def _setup(yaml: str, tmp_path: Path) -> tuple[Any, Run]:
    warehouse = tmp_path / "warehouse.duckdb"
    values = ",".join(
        f"(DATE '{day}', DATE '2026-01-01', {i + 1})" for i, day in enumerate(_ORDERS)
    )
    with duckdb.connect(str(warehouse)) as con:
        con.execute(
            f"CREATE OR REPLACE TABLE orders AS SELECT * FROM "
            f"(VALUES {values}) AS t(created_at, signup_date, n)"
        )
        con.execute(
            "CREATE OR REPLACE TABLE events AS SELECT * FROM (VALUES "
            "(TIMESTAMP '2026-03-05 10:00:00', 1), "
            "(TIMESTAMP '2026-03-31 18:00:00', 2)) AS t(ts, n)"
        )
    # The plan compiles against the project's sources: declare the warehouse there.
    (tmp_path / "dbt_charts.yml").write_text(
        f'sources:\n  mem:\n    type: duckdb\n    path: "{warehouse}"\n'
    )
    result = compile_yaml(
        yaml,
        project_sources=ProjectSourcesConfig(
            sources={"mem": {"type": "duckdb", "path": str(warehouse)}}
        ),
    )
    assert result.success and result.board is not None, result.errors
    executor = Run(
        result.board,
        env=build_exec_env(FilesystemProject(tmp_path)),
    )
    return result.board, executor


def test_query_rows_are_untouched(tmp_path: Path) -> None:
    board, executor = _setup(_yaml("bar"), tmp_path)
    rows = executor.execute_query("q", {"date_range": _MARCH})
    assert [r["week"] for r in rows] == [
        date(2026, 3, 2),
        date(2026, 3, 9),
        date(2026, 3, 23),
    ]
