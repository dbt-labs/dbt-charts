"""A DuckDB/SQLite database file's content version is part of the cache key."""

from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import Any

import duckdb
import pytest
import yaml

from dbt_charts.cli.filesystem_project import FilesystemProject
from dbt_charts.core.compile import compile as compile_board
from dbt_charts.core.execute.adapters.adapter_registry import (
    LOCAL_AUTHORING_REGISTRY_KWARGS,
    build_adapter_registry,
)
from dbt_charts.core.execute.executor import Executor
from dbt_charts.core.execute.trivial_local_cache import TrivialDuckDBCache


def _write_rows(source_type: str, db_path: Path, amounts: list[int]) -> None:
    db_path.parent.mkdir(parents=True, exist_ok=True)
    if source_type == "duckdb":
        with duckdb.connect(str(db_path)) as con:
            con.execute("CREATE OR REPLACE TABLE sales (amount INTEGER)")
            for amount in amounts:
                con.execute("INSERT INTO sales VALUES (?)", [amount])
        return
    with sqlite3.connect(db_path) as con:
        con.execute("DROP TABLE IF EXISTS sales")
        con.execute("CREATE TABLE sales (amount INTEGER)")
        con.executemany("INSERT INTO sales VALUES (?)", [(a,) for a in amounts])
    con.close()


def _executor(
    project: FilesystemProject,
    cache: TrivialDuckDBCache,
    registry_kwargs: dict[str, Any] | None = None,
) -> Executor:
    board = compile_board(
        yaml.dump(
            {
                "queries": {
                    "q": {"sql": "SELECT amount FROM sales ORDER BY 1", "source": "db"}
                },
                "charts": {"c": {"query": "q", "type": "table"}},
            }
        )
    ).board
    assert board is not None
    return Executor(
        board,
        adapter_registry=build_adapter_registry(project, **(registry_kwargs or {})),
        query_registry={"q": board.queries["q"]},
        result_cache=cache,
    )


def _render(project: FilesystemProject, cache: TrivialDuckDBCache) -> list[Any]:
    return [row["amount"] for row in _executor(project, cache).execute_query("q")]


def _declare(tmp_path: Path, source_type: str, filename: str) -> FilesystemProject:
    (tmp_path / "dbt_charts.yml").write_text(
        yaml.dump(
            {"sources": {"db": {"type": source_type, "path": f"data/{filename}"}}}
        )
    )
    return FilesystemProject(tmp_path)


@pytest.mark.parametrize(
    ("source_type", "filename"), [("duckdb", "shop.duckdb"), ("sqlite", "shop.db")]
)
def test_editing_database_file_busts_cache(
    tmp_path: Path, source_type: str, filename: str
) -> None:
    db_path = tmp_path / "data" / filename
    _write_rows(source_type, db_path, [100])
    project = _declare(tmp_path, source_type, filename)
    cache = TrivialDuckDBCache()

    assert _render(project, cache) == [100]
    _write_rows(source_type, db_path, [100, 999])

    assert _render(project, cache) == [100, 999]


def test_missing_database_file_is_a_cache_miss_not_a_crash(tmp_path: Path) -> None:
    project = _declare(tmp_path, "sqlite", "missing.db")

    assert _executor(project, TrivialDuckDBCache()).is_cached("q") is False


@pytest.mark.parametrize(
    ("source_type", "filename"), [("duckdb", "shop.duckdb"), ("sqlite", "shop.db")]
)
def test_unchanged_database_file_hits_and_is_not_a_warehouse_hit(
    tmp_path: Path, source_type: str, filename: str
) -> None:
    _write_rows(source_type, tmp_path / "data" / filename, [100])
    project = _declare(tmp_path, source_type, filename)
    cache = TrivialDuckDBCache()
    _render(project, cache)

    executor = _executor(project, cache)
    assert executor.is_cached("q") is True
    assert executor.cache_hit_ats
    assert executor.warehouse_cache_hit_ats == []


def test_duckdb_with_external_access_stays_a_warehouse_hit(tmp_path: Path) -> None:
    # SQL on this connection can read files beyond the database, so the
    # database file's version cannot vouch for the result.
    _write_rows("duckdb", tmp_path / "data" / "shop.duckdb", [100])
    project = _declare(tmp_path, "duckdb", "shop.duckdb")
    cache = TrivialDuckDBCache()
    _executor(project, cache, LOCAL_AUTHORING_REGISTRY_KWARGS).execute_query("q")

    executor = _executor(project, cache, LOCAL_AUTHORING_REGISTRY_KWARGS)
    executor.execute_query("q")
    assert executor.warehouse_cache_hit_ats == executor.cache_hit_ats != []


def test_duckdb_source_with_attach_stays_unversioned(tmp_path: Path) -> None:
    from dbt_charts.core.compile.models.source import DuckDBSourceConfig

    (tmp_path / "shop.duckdb").touch()
    registry = build_adapter_registry(FilesystemProject(tmp_path))
    plain = DuckDBSourceConfig(type="duckdb", path="shop.duckdb")
    attached = DuckDBSourceConfig.model_validate(
        {
            "type": "duckdb",
            "path": "shop.duckdb",
            "attach": [{"path": "other.duckdb", "alias": "other"}],
        }
    )

    assert registry.database_file_version(plain) != ""
    assert registry.database_file_version(attached) == ""


def test_cache_key_never_resolves_a_warehouse_source() -> None:
    # A host's resolver may refuse a warehouse source with its own exception
    # (an SSRF guard); that refusal belongs to execution, not the cache probe.
    from unittest.mock import Mock

    board = compile_board(
        yaml.dump(
            {
                "queries": {"q": {"sql": "SELECT 1 AS v", "source": "wh"}},
                "charts": {"c": {"query": "q", "type": "table"}},
            }
        )
    ).board
    assert board is not None
    registry = Mock()
    registry.project_sources.sources = {"wh": {"type": "postgres", "host": "h"}}
    registry.project_file_sources.return_value = {}
    registry.resolve_query_source.side_effect = RuntimeError("blocked host")
    executor = Executor(
        board,
        adapter_registry=registry,
        query_registry={"q": board.queries["q"]},
        result_cache=TrivialDuckDBCache(),
    )

    assert executor.is_cached("q") is False
    registry.resolve_query_source.assert_not_called()


def test_host_without_a_sqlite_adapter_never_stats_the_path(tmp_path: Path) -> None:
    # A host that cannot open the file must not probe the disk for it.
    from dbt_charts.core.compile.models.source import SQLiteSourceConfig
    from dbt_charts.core.execute.adapters.adapter_registry import AdapterRegistry

    registry = AdapterRegistry(FilesystemProject(tmp_path))
    source = SQLiteSourceConfig(type="sqlite", path=str(tmp_path / "missing.db"))

    assert registry.database_file_version(source) == ""
