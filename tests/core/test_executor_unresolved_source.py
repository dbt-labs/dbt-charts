"""Regression test: unresolved string sources must surface as a clear error
through Executor.execute_query, not silently fall back to DuckDB.

Repro: Cloud's chart-preview path wraps the chart in a board whose `sources:`
block only contains an in-memory DuckDB. If the chart references a different
named source (e.g. `my_postgres`), pre-fix behavior was to run the SQL on
DuckDB anyway, surfacing as a misleading parser error (notably for backtick-
quoted BigQuery identifiers).
"""

from collections.abc import Callable
from pathlib import Path

import pytest

from dbt_charts.cli.filesystem_project import FilesystemProject
from dbt_charts.core.compile import compile
from dbt_charts.core.execute import QueryError
from dbt_charts.core.execute.env import build_exec_env

from .._run import Run

PREVIEW_BOARD_YAML = """
title: Chart Preview
queries:
  bq_query:
    sql: "SELECT * FROM `proj.ds.tbl`"
    source: my_bigquery
charts:
  c:
    query: bq_query
    type: kpi
    value: x
rows:
  - c
"""


def test_unresolved_source_raises_query_error_not_duckdb_parse_error(
    local_project: Callable[..., FilesystemProject],
) -> None:
    result = compile(PREVIEW_BOARD_YAML)
    assert result.board is not None
    executor = Run(
        result.board,
        env=build_exec_env(local_project(Path.cwd())),
        query_registry=result.query_registry,
    )

    with pytest.raises(QueryError) as exc_info:
        executor.execute_query("bq_query")

    msg = str(exc_info.value)
    assert "my_bigquery" in msg
    assert "not found" in msg.lower()
    lowered = msg.lower()
    assert "parser" not in lowered
    assert "syntax" not in lowered
