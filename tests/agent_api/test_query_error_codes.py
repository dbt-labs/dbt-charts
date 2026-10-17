"""Every way the SQL tool can fail names the registered code that classified it."""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path, PurePosixPath

import pytest

from dbt_charts.agent_api.query import execute_query, query_board
from dbt_charts.cli.filesystem_project import FilesystemProject
from dbt_charts.core.diagnostics.registry import REGISTRY
from dbt_charts.core.execute.adapters import AdapterRegistry, build_adapter_registry
from dbt_charts.core.project import Project

BOARD = """
source: db
variables:
  region:
    input: text
queries:
  ok:
    sql: select 1 as a
  needs_region:
    sql: "select '{{ region }}' as r"
  missing_table:
    sql: select * from no_such_table
charts:
  c:
    query: ok
    type: table
rows:
  - c
"""


@pytest.fixture
def project(
    tmp_path: Path, local_project: Callable[..., FilesystemProject]
) -> FilesystemProject:
    (tmp_path / "dbt_charts.yml").write_text(
        "sources:\n"
        "  db:\n    type: duckdb\n    path: ':memory:'\n"
        "  unreachable:\n    type: duckdb\n    path: /no/such/dir/x.duckdb\n"
    )
    (tmp_path / "orders.yml").write_text(BOARD)
    return local_project(tmp_path)


@pytest.fixture
def registry(project: FilesystemProject) -> AdapterRegistry:
    return build_adapter_registry(project, read_only=False)


@pytest.mark.parametrize(
    ("sql", "source", "code"),
    [
        ("select * from no_such_table", "db", "ERR-BINDER-UNKNOWN-RELATION"),
        ("drop table x", "db", "ERR-MUTATING-SQL"),
        ("select ((", "db", "ERR-UNPARSEABLE-SQL"),
        ("select 1", "nope", "ERR-SOURCE-NOT-FOUND"),
        ("select 1", None, "ERR-SOURCE-NOT-NAMED"),
        ("select '{{ unbound }}'", "db", "ERR-JINJA-ERROR"),
        ("select 1", "unreachable", "ERR-WAREHOUSE-CONNECTION"),
    ],
)
def test_sql_form_failure_carries_the_classifying_code(
    registry: AdapterRegistry, sql: str, source: str | None, code: str
) -> None:
    result = execute_query(sql, source=source, adapter_registry=registry)

    assert not result.success
    assert result.error_code == code
    assert REGISTRY.get(code).code == code


def test_sql_form_success_has_no_error_code(registry: AdapterRegistry) -> None:
    result = execute_query("select 1 as a", source="db", adapter_registry=registry)

    assert result.success
    assert result.error_code is None
    assert "error_code" not in result.model_dump(mode="json", exclude_none=True)


def test_deployment_without_the_adapter_is_not_internal(
    project: FilesystemProject,
) -> None:
    bare = AdapterRegistry(project=project, project_sources=project.sources)

    result = execute_query("select 1", source="db", adapter_registry=bare)

    assert result.error_code == "ERR-ADAPTER-UNAVAILABLE"


@pytest.mark.parametrize(
    ("path", "name", "code"),
    [
        ("missing.yml", "ok", "ERR-FILE-NOT-FOUND"),
        ("../../../outside.yml", "ok", "ERR-PATH-OUTSIDE-PROJECT"),
        ("orders.yml", "zzz", "ERR-BOARD-QUERY-NOT-FOUND"),
        ("orders.yml", "needs_region", "ERR-VARIABLE-UNBOUND"),
        ("orders.yml", "missing_table", "ERR-BINDER-UNKNOWN-RELATION"),
    ],
)
def test_board_form_failure_carries_the_classifying_code(
    project: FilesystemProject,
    registry: AdapterRegistry,
    path: str,
    name: str,
    code: str,
) -> None:
    result = query_board(name, Path(path), project, adapter_registry=registry)

    assert not result.success
    assert result.error_code == code
    assert REGISTRY.get(code).code == code


def test_board_that_does_not_compile_reports_the_compile_error_code(
    tmp_path: Path,
    project: FilesystemProject,
    registry: AdapterRegistry,
) -> None:
    (tmp_path / "broken.yml").write_text("queries:\n  q:\n    sql: select 1\nrows: [")

    result = query_board("q", Path("broken.yml"), project, adapter_registry=registry)

    assert not result.success
    assert result.error_code is not None
    assert REGISTRY.get(result.error_code).code == result.error_code
    assert result.error_code != "ERR-INTERNAL"
    assert result.path == PurePosixPath("broken.yml")


def test_board_form_success_has_no_error_code(
    project: FilesystemProject, registry: AdapterRegistry
) -> None:
    result = query_board("ok", Path("orders.yml"), project, adapter_registry=registry)

    assert result.success
    assert result.error_code is None


def test_an_absolute_board_path_on_a_hosted_project_is_the_callers_argument(
    tmp_path: Path,
    registry: AdapterRegistry,
    in_memory_project: Callable[..., Project],
) -> None:
    hosted = in_memory_project(tmp_path, {"charts/orders.yml": "title: Orders\n"})

    result = query_board(
        "ok", tmp_path / "charts" / "orders.yml", hosted, adapter_registry=registry
    )

    assert result.error_code == "ERR-TOOL-ARGUMENTS-INVALID"
    assert "charts/orders.yml" in result.errors[0]
