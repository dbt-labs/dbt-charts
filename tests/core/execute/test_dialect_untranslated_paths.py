"""Paths that never translate must refuse a query whose `dialect:` differs.

Running SQL written for another warehouse untouched would be a wrong result
that looks right, so each of these raises ERR-SQL-DIALECT-UNSUPPORTED.
"""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path

import pytest

from dbt_charts.cli.filesystem_project import FilesystemProject
from dbt_charts.core.compile.compiler import compile
from dbt_charts.core.compile.config import ProjectSourcesConfig
from dbt_charts.core.compile.models.query.normalized import SqlQuery
from dbt_charts.core.diagnostics.codes_execute import ERR_SQL_DIALECT_UNSUPPORTED
from dbt_charts.core.diagnostics.execution import QueryError
from dbt_charts.core.execute.adapters.adapter_registry import build_adapter_registry
from dbt_charts.core.execute.executor import Executor

_BOARD = """\
queries:
  base:
    sql: select 1 as id
    source: db
  tailed:
    sql: select 1 as id
    source: db
    dialect: snowflake
    incremental: id
charts:
  c1:
    type: table
    query: tailed
rows:
  - c1
"""


def _executor(local_project: Callable[..., FilesystemProject]) -> Executor:
    result = compile(_BOARD)
    assert result.board is not None, result.errors
    project = local_project(Path.cwd())
    vars(project)["sources"] = ProjectSourcesConfig(
        sources={"db": {"type": "duckdb", "path": ":memory:"}}
    )
    return Executor(result.board, build_adapter_registry(project), use_cache=False)


def test_incremental_query_refuses_a_differing_dialect(
    local_project: Callable[..., FilesystemProject],
) -> None:
    with pytest.raises(QueryError) as exc:
        _executor(local_project).execute_query("tailed")
    assert exc.value.code is ERR_SQL_DIALECT_UNSUPPORTED


def test_incremental_query_with_no_resolved_source_is_refused(
    local_project: Callable[..., FilesystemProject], monkeypatch: pytest.MonkeyPatch
) -> None:
    executor = _executor(local_project)
    monkeypatch.setattr(
        executor.adapter_registry, "resolve_query_source", lambda *a, **k: None
    )
    with pytest.raises(QueryError) as exc:
        executor.execute_query("tailed")
    assert exc.value.code is ERR_SQL_DIALECT_UNSUPPORTED


def test_file_source_refuses_a_differing_dialect(
    tmp_path: Path, local_project: Callable[..., FilesystemProject]
) -> None:
    (tmp_path / "data.csv").write_text("a\n1\n")
    (tmp_path / "dbt_charts.yml").write_text(
        "sources:\n  files:\n    type: csv\n    files:\n      data: data.csv\n"
    )
    registry = build_adapter_registry(local_project(tmp_path))
    query = SqlQuery(sql="select a from data", source="files", dialect="bigquery")

    result = registry.execute(query)

    assert result.error is not None
    assert result.error.code is ERR_SQL_DIALECT_UNSUPPORTED
