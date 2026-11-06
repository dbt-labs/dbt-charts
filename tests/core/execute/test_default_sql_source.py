"""SQL whose authored source defers to the dbt project resolves to a concrete
default source, chosen once by what the SQL contains."""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path

import duckdb
import pytest
import yaml

from dbt_charts.cli.filesystem_project import FilesystemProject
from dbt_charts.core.compile.models.query.normalized import SqlQuery
from dbt_charts.core.compile.models.source import (
    DbtTargetSourceConfig,
    DuckDBSourceConfig,
)
from dbt_charts.core.diagnostics.base import DbtChartsError
from dbt_charts.core.diagnostics.codes_execute import (
    ERR_DBT_PROJECT_NO_LOCAL_WAREHOUSE,
)
from dbt_charts.core.execute.env import ExecEnv, close_env, make_exec_env
from dbt_charts.core.execute.plan import resolve_source
from dbt_charts.core.execute.plan_sources import resolve_query_source
from dbt_charts.core.execute.run import run_query
from dbt_charts.core.execute.source_resolver import DbtContext


@pytest.fixture(autouse=True)
def isolated_profile_search(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    for var in ("DBT_PROFILES_DIR", "DBT_PROFILE", "DBT_TARGET"):
        monkeypatch.delenv(var, raising=False)


def _registry(root: Path, local_project: Callable[..., FilesystemProject]) -> ExecEnv:
    """A dbt project that declares no ``sources:``, so a source name resolves to
    nothing and defers to the project."""
    root.mkdir()
    (root / "dbt_project.yml").write_text("name: shop\nprofile: shop_profile\n")
    (root / "profiles.yml").write_text(
        yaml.safe_dump(
            {
                "shop_profile": {
                    "target": "dev",
                    "outputs": {
                        "dev": {
                            "type": "duckdb",
                            "path": str(root / "warehouse.duckdb"),
                            "threads": 1,
                        }
                    },
                }
            }
        )
    )
    return make_exec_env(
        project=local_project(root),
        dbt_context=DbtContext(dbt_project_path=root, target=None),
    )


def test_dbt_jinja_resolves_to_the_dbt_target(
    tmp_path: Path, local_project: Callable[..., FilesystemProject]
) -> None:
    registry = _registry(tmp_path / "shop", local_project)

    resolved = resolve_query_source(
        SqlQuery(sql="SELECT * FROM {{ ref('orders') }}", source="unknown"),
        sources=registry.sources,
        resolver=registry.resolver,
        dbt_context=registry.dbt_context,
    )

    assert isinstance(resolved, DbtTargetSourceConfig)
    assert resolved.type == "duckdb"


def test_plain_sql_resolves_to_the_local_duckdb_warehouse(
    tmp_path: Path, local_project: Callable[..., FilesystemProject]
) -> None:
    root = tmp_path / "shop"
    registry = _registry(root, local_project)
    (root / "data").mkdir()
    warehouse = root / "data" / "dev.duckdb"
    duckdb.connect(str(warehouse)).close()

    resolved = resolve_query_source(
        SqlQuery(sql="SELECT 1", source="unknown"),
        sources=registry.sources,
        resolver=registry.resolver,
        dbt_context=registry.dbt_context,
    )

    assert resolved == DuckDBSourceConfig(type="duckdb", path=str(warehouse))


def test_plain_sql_with_no_local_warehouse_is_an_error(
    tmp_path: Path, local_project: Callable[..., FilesystemProject]
) -> None:
    registry = _registry(tmp_path / "shop", local_project)

    with pytest.raises(DbtChartsError) as raised:
        resolve_query_source(
            SqlQuery(sql="SELECT 1", source="unknown"),
            sources=registry.sources,
            resolver=registry.resolver,
            dbt_context=registry.dbt_context,
        )

    assert raised.value.code is ERR_DBT_PROJECT_NO_LOCAL_WAREHOUSE


def test_the_executor_chooses_the_source_on_the_expanded_sql(
    tmp_path: Path, local_project: Callable[..., FilesystemProject]
) -> None:
    """A query with no Jinja of its own that composes one using ``ref()`` runs on
    the dbt target; choosing on the authored text would send it to local DuckDB."""
    from dbt_charts.core.compile import compile as compile_board

    from ..._run import Run

    registry = _registry(tmp_path / "shop", local_project)
    compiled = compile_board(
        yaml.dump(
            {
                "source": "unknown",
                "queries": {
                    "base": "select * from {{ ref('orders') }}",
                    "agg": "select * from ({{ queries.base }}) t",
                },
                "charts": {"c": {"query": "agg", "type": "table"}},
            }
        )
    )
    assert compiled.board is not None, compiled.errors
    executor = Run(
        compiled.board,
        env=registry,
        query_registry=compiled.query_registry,
    )

    resolved = resolve_source(executor.ctx, compiled.query_registry["agg"], "agg")

    assert isinstance(resolved, DbtTargetSourceConfig)
    assert resolved.type == "duckdb"


def test_a_dialect_query_is_translated_to_the_source_it_lands_on(
    tmp_path: Path, local_project: Callable[..., FilesystemProject]
) -> None:
    """BigQuery-written SQL on a deferring source runs on the local DuckDB default."""
    from dbt_charts.core.execute.backends.base import BackendRegistry
    from dbt_charts.core.execute.backends.duckdb_local import DuckDBBackend

    root = tmp_path / "shop"
    root.mkdir()
    (root / "data").mkdir()
    duckdb.connect(str(root / "data" / "dev.duckdb")).close()
    registry = make_exec_env(
        project=local_project(root),
        dbt_context=DbtContext(dbt_project_path=root, target=None),
        backends=BackendRegistry({"duckdb": DuckDBBackend(data_dir=root)}),
    )

    result = run_query(
        registry,
        SqlQuery(
            sql="select date_trunc(date '2024-05-17', month) as m",
            source="unknown",
            dialect="bigquery",
        ),
    )
    close_env(registry)

    assert result.error is None, result.error
    assert [str(row["m"])[:10] for row in result.data] == ["2024-05-01"]
