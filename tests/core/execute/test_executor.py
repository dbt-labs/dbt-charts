"""Executor cache keys follow the dbt manifest a ref() resolves against."""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

import duckdb
import pytest
import yaml

from dbt_charts.cli.filesystem_project import FilesystemProject
from dbt_charts.core.compile import compile as compile_board
from dbt_charts.core.diagnostics.codes_execute import ERR_DBT_MANIFEST_MISSING
from dbt_charts.core.diagnostics.execution import QueryError
from dbt_charts.core.execute.adapters.adapter_registry import build_adapter_registry
from dbt_charts.core.execute.cache_backend import CachedQueryFailure
from dbt_charts.core.execute.executor import Executor
from dbt_charts.core.execute.trivial_local_cache import TrivialDuckDBCache


def _write_manifest(root: Path, table: str) -> None:
    manifest = root / "target" / "manifest.json"
    manifest.parent.mkdir(exist_ok=True)
    node = {
        "resource_type": "model",
        "name": "orders",
        "schema": "main",
        "alias": table,
        "relation_name": f"main.{table}",
    }
    manifest.write_text(json.dumps({"nodes": {"model.p.orders": node}, "sources": {}}))
    stat = manifest.stat()
    os.utime(manifest, ns=(stat.st_atime_ns, stat.st_mtime_ns + 1_000_000_000))


def _project(tmp_path: Path, *, manifest_table: str | None) -> FilesystemProject:
    db_path = tmp_path / "wh.duckdb"
    con = duckdb.connect(str(db_path))
    con.execute("create table t_a as select 1 as v")
    con.execute("create table t_b as select 2 as v")
    con.close()
    (tmp_path / "dbt_charts.yml").write_text(
        yaml.dump({"sources": {"wh": {"type": "duckdb", "path": str(db_path)}}})
    )
    if manifest_table is not None:
        _write_manifest(tmp_path, manifest_table)
    return FilesystemProject(tmp_path)


def _board(sql: str) -> Any:
    document = {
        "queries": {"q": {"sql": sql, "source": "wh"}},
        "charts": {"c": {"query": "q", "type": "table"}},
    }
    board = compile_board(yaml.dump(document)).board
    assert board is not None
    return board


def _run(
    project: FilesystemProject,
    board: Any,
    cache: TrivialDuckDBCache,
    registry: Any = None,
) -> int:
    executor = Executor(
        board,
        adapter_registry=registry or build_adapter_registry(project),
        query_registry={"q": board.queries["q"]},
        result_cache=cache,
    )
    return executor.execute_query("q")[0]["v"]


def test_ref_query_misses_the_cache_when_the_manifest_changes(tmp_path: Path) -> None:
    project = _project(tmp_path, manifest_table="t_a")
    board = _board("SELECT v FROM {{ ref('orders') }}")
    cache = TrivialDuckDBCache()

    assert _run(project, board, cache) == 1
    assert _run(project, board, cache) == 1

    _write_manifest(tmp_path, "t_b")

    assert _run(project, board, cache) == 2


def test_query_without_dbt_jinja_ignores_the_manifest(tmp_path: Path) -> None:
    project = _project(tmp_path, manifest_table="t_a")
    board = _board("SELECT v FROM t_a")
    cache = TrivialDuckDBCache()
    executor = Executor(
        board,
        adapter_registry=build_adapter_registry(project),
        query_registry={"q": board.queries["q"]},
        result_cache=cache,
    )
    query = board.queries["q"]

    before = executor._manifest_version(query)
    _write_manifest(tmp_path, "t_b")
    executor_after = Executor(
        board,
        adapter_registry=build_adapter_registry(project),
        query_registry={"q": board.queries["q"]},
        result_cache=cache,
    )

    assert before == "" == executor_after._manifest_version(query)
    assert _run(project, board, cache) == 1


def test_missing_manifest_still_raises_the_diagnostic(tmp_path: Path) -> None:
    project = _project(tmp_path, manifest_table=None)
    board = _board("SELECT v FROM {{ ref('orders') }}")

    with pytest.raises(QueryError) as raised:
        _run(project, board, TrivialDuckDBCache())

    assert raised.value.code is ERR_DBT_MANIFEST_MISSING


def test_one_long_lived_registry_follows_a_rewritten_manifest(tmp_path: Path) -> None:
    project = _project(tmp_path, manifest_table="t_a")
    board = _board("SELECT v FROM {{ ref('orders') }}")
    cache = TrivialDuckDBCache()
    registry = build_adapter_registry(project)

    assert _run(project, board, cache, registry) == 1

    _write_manifest(tmp_path, "t_b")

    assert _run(project, board, cache, registry) == 2
    # A restart (fresh registry) reads the same rows the live one cached.
    assert _run(project, board, cache) == 2


def test_deleting_the_manifest_under_a_live_registry_stops_resolution(
    tmp_path: Path,
) -> None:
    project = _project(tmp_path, manifest_table="t_a")
    board = _board("SELECT v FROM {{ ref('orders') }}")
    cache = TrivialDuckDBCache()
    registry = build_adapter_registry(project)
    assert _run(project, board, cache, registry) == 1

    (tmp_path / "target" / "manifest.json").unlink()

    with pytest.raises(QueryError) as raised:
        _run(project, board, cache, registry)
    assert raised.value.code is ERR_DBT_MANIFEST_MISSING
    # A later process gets the same refusal, never the rows cached for the old manifest.
    with pytest.raises((QueryError, CachedQueryFailure), match="no dbt manifest"):
        _run(project, board, cache)
