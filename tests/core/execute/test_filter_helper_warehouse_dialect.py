"""The filter helpers spell SQL for the warehouse on each adapter path that renders.

The dbt backend renders in an internal placeholder style that no engine parses,
so the warehouse has to be handed to the helpers separately — on the registry's
composition path. Each is pinned here with a date variable through
`filter()`: the cast only appears if the warehouse reached the helper, and the
internal style raises if it did not.
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import date
from pathlib import Path

import pytest

from dbt_charts.cli.filesystem_project import FilesystemProject
from dbt_charts.core.column_types import TypedRows
from dbt_charts.core.compile.compiler import compile
from dbt_charts.core.compile.config import ProjectSourcesConfig
from dbt_charts.core.compile.models.query.normalized import SqlQuery
from dbt_charts.core.compile.models.source import (
    PostgresSourceConfig,
    ResolvedSourceConfig,
)
from dbt_charts.core.execute.backends.dbt_core import DbtBackend
from dbt_charts.core.execute.compose import ResolvedQuery
from dbt_charts.core.execute.env import build_exec_env

from ..._run import Run
from ..conftest import DbtHarness

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
        self: DbtBackend, source: ResolvedSourceConfig, query: ResolvedQuery
    ) -> TypedRows:
        seen["sql"] = query.inline(source.type)
        return TypedRows([], [], [])

    monkeypatch.setattr(DbtBackend, "execute", _fake)
    return seen


def test_registry_composition_path(
    wire_sql: dict[str, str], local_project: Callable[..., FilesystemProject]
) -> None:
    """Executor → registry composition → backend with pre-rendered params."""
    result = compile(BOARD)
    assert result.success, [str(e) for e in result.errors]
    assert result.board is not None
    project = local_project(Path.cwd())
    vars(project)["sources"] = ProjectSourcesConfig(sources={"db": PG_SOURCE})
    executor = Run(result.board, build_exec_env(project), use_cache=False)

    executor.execute_query("composed", {})

    assert EXPECTED in wire_sql["sql"]


def test_adapter_render_path(
    wire_sql: dict[str, str], local_project: Callable[..., FilesystemProject]
) -> None:
    """The registry composing directly, over values the executor already coerced."""
    adapter = DbtHarness(local_project(Path.cwd()), profile_type="postgres")

    adapter.execute(
        SqlQuery(sql="SELECT * FROM t WHERE {{ filter('ts', d, '>=') }}", source="db"),
        {"d": date(2024, 1, 15)},
        source_config=PostgresSourceConfig(**PG_SOURCE),
    )

    assert EXPECTED in wire_sql["sql"]
