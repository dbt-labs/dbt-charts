"""The filter helpers spell SQL for the warehouse on each adapter path that renders.

`SqlAdapter` renders in an internal placeholder style that no engine parses,
so the warehouse has to be handed to the helpers separately — on the registry's
composition path and on the adapter's own render. `DbtAdapter` renders through
its own template pass. Each is pinned here with a date variable through
`filter()`: the cast only appears if the warehouse reached the helper, and the
internal style raises if it did not.
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import date
from pathlib import Path
from typing import Any

import pytest

from dbt_charts.cli.filesystem_project import FilesystemProject
from dbt_charts.core.column_types import literal_schema
from dbt_charts.core.compile.compiler import compile
from dbt_charts.core.compile.config import ProjectSourcesConfig
from dbt_charts.core.compile.models.query.normalized import SqlQuery
from dbt_charts.core.compile.models.source import PostgresSourceConfig
from dbt_charts.core.execute.adapters.adapter_registry import build_adapter_registry
from dbt_charts.core.execute.adapters.base import QueryResult
from dbt_charts.core.execute.adapters.dbt_adapter import DbtAdapter
from dbt_charts.core.execute.adapters.sql_adapter import PreparedSql, SqlAdapter
from dbt_charts.core.execute.executor import Executor

PG_SOURCE = {
    "type": "postgres",
    "host": "h",
    "dbname": "db",
    "user": "u",
    "password": "p",
}

BOARD = "\n".join(
    [
        "source: db",
        "variables:",
        "  d:",
        "    input: date",
        "    default: '2024-01-15'",
        "queries:",
        "  base:",
        "    sql: SELECT 1 AS id, TIMESTAMP '2024-01-15 13:00:00' AS ts",
        "    source: db",
        "  composed:",
        "    sql: |",
        "      SELECT * FROM {{ queries.base }} WHERE {{ filter('ts', d, '>=') }}",
        "    source: db",
        "charts:",
        "  dummy:",
        "    type: bar",
        "    x: id",
        "    y: id",
        "    query: composed",
    ]
)

EXPECTED = "CAST(ts AS DATE) >= DATE '2024-01-15'"


@pytest.fixture
def wire_sql(monkeypatch: pytest.MonkeyPatch) -> dict[str, str]:
    """Intercept at the warehouse boundary and record what would be sent."""
    seen: dict[str, str] = {}

    def _fake(
        self: SqlAdapter,
        prepared: PreparedSql,
        query: SqlQuery,
        source_config: dict[str, Any] | None,
    ) -> QueryResult:
        seen["sql"] = prepared.sql
        return QueryResult(data=[], columns=[], schema=literal_schema([], []))

    monkeypatch.setattr(SqlAdapter, "_execute_via_dbt_adapter", _fake)
    return seen


def test_registry_composition_path(
    wire_sql: dict[str, str], local_project: Callable[..., FilesystemProject]
) -> None:
    """Executor → registry composition → SqlAdapter with pre-rendered params."""
    result = compile(BOARD)
    assert result.success, [str(e) for e in result.errors]
    assert result.board is not None
    project = local_project(Path.cwd())
    vars(project)["sources"] = ProjectSourcesConfig(sources={"db": PG_SOURCE})
    executor = Executor(result.board, build_adapter_registry(project), use_cache=False)

    executor.execute_query("composed", {})

    assert EXPECTED in wire_sql["sql"]


def test_adapter_render_path(
    wire_sql: dict[str, str], local_project: Callable[..., FilesystemProject]
) -> None:
    """SqlAdapter rendering for itself, over values the executor already coerced."""
    adapter = SqlAdapter(
        project=local_project(Path.cwd()),
        dbt_project_path=None,
        profile_type="postgres",
    )

    adapter._execute(
        SqlQuery(sql="SELECT * FROM t WHERE {{ filter('ts', d, '>=') }}", source="db"),
        {"d": date(2024, 1, 15)},
        source_config=PostgresSourceConfig(**PG_SOURCE),
    )

    assert EXPECTED in wire_sql["sql"]


def test_dbt_adapter_render_path(
    tmp_path: Path, local_project: Callable[..., FilesystemProject]
) -> None:
    """DbtAdapter's own template pass, for the warehouse its target names."""
    adapter = DbtAdapter(
        project=local_project(tmp_path), dbt_project_path=tmp_path, target_name="dev"
    )
    adapter._dialect = "sqlite"

    sql = adapter._resolve_dbt_sql(
        "SELECT * FROM t WHERE {{ filter('ts', d, '>=') }}", {"d": date(2024, 1, 15)}
    )

    assert "DATE(ts) >= DATE '2024-01-15'" in sql
