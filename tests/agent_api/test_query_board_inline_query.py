"""A chart's inline query is named by the Python board (`_inline_query_<chart>`) and keyed by
position in the plan: running or previewing it by that name works and never crashes."""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path

import pytest

from dbt_charts.agent_api.query import lookup_board_query_sql, query_board
from dbt_charts.cli.filesystem_project import FilesystemProject
from dbt_charts.core.execute.env import ExecEnv, build_exec_env

_BOARD = """\
title: inline
source: mem
charts:
  c:
    type: kpi
    value: n
    query:
      sql: SELECT 5 AS n
"""


@pytest.fixture
def project(
    tmp_path: Path, local_project: Callable[..., FilesystemProject]
) -> FilesystemProject:
    (tmp_path / "dbt_charts.yml").write_text(
        "sources:\n  mem:\n    type: duckdb\n    path: ':memory:'\n"
    )
    (tmp_path / "inline.yml").write_text(_BOARD)
    return local_project(tmp_path)


def test_query_board_runs_an_inline_query_by_its_python_name(
    project: FilesystemProject,
) -> None:
    env: ExecEnv = build_exec_env(project, read_only=False)
    path = Path(project.root) / "inline.yml"
    result = query_board("_inline_query_c", path, project, env=env)
    assert result.success, result.errors
    assert result.data == [{"n": 5}]


def test_lookup_board_query_sql_previews_an_inline_query_by_its_python_name(
    project: FilesystemProject,
) -> None:
    path = Path(project.root) / "inline.yml"
    result = lookup_board_query_sql("_inline_query_c", path, project=project)
    assert result.success, result.errors
    assert result.sql is not None and "SELECT 5" in result.sql


def test_an_unknown_query_name_is_a_registered_not_found(
    project: FilesystemProject,
) -> None:
    env: ExecEnv = build_exec_env(project, read_only=False)
    path = Path(project.root) / "inline.yml"
    result = query_board("nope", path, project, env=env)
    assert not result.success
    assert result.error_code == "ERR-BOARD-QUERY-NOT-FOUND"
