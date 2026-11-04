"""A query's source is resolved once per execution and the result is passed down."""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock

import pytest

from dbt_charts.cli.filesystem_project import FilesystemProject
from dbt_charts.core.column_types import literal_schema
from dbt_charts.core.compile import compile as compile_board
from dbt_charts.core.compile.models.source import DuckDBSourceConfig, SourceConfig
from dbt_charts.core.diagnostics import ERR_SOURCE_NOT_NAMED
from dbt_charts.core.diagnostics.base import DbtChartsError
from dbt_charts.core.diagnostics.execution import ExecutionError, QueryError
from dbt_charts.core.execute import Executor
from dbt_charts.core.execute.adapters.adapter_registry import AdapterRegistry
from dbt_charts.core.execute.adapters.base import BaseAdapter, QueryParams, QueryResult
from dbt_charts.core.execute.source_resolver import SourceResolver
from dbt_charts.core.execute.trivial_local_cache import TrivialDuckDBCache


class _RowsAdapter(BaseAdapter):
    supported_types = {"sql"}

    def _execute(
        self,
        query: Any,
        variables: Any = None,
        params: QueryParams = None,
        source_config: SourceConfig | None = None,
    ) -> QueryResult:
        rows = [{"ts": 1, "val": 10}]
        return QueryResult(
            data=rows, columns=["ts", "val"], schema=literal_schema(rows, ["ts", "val"])
        )


def _registry(
    local_project: Callable[..., FilesystemProject],
) -> tuple[AdapterRegistry, MagicMock]:
    resolver = MagicMock(spec=SourceResolver)
    resolver.resolve.return_value = DuckDBSourceConfig(type="duckdb", path=":memory:")
    registry = AdapterRegistry(project=local_project(Path()), resolver=resolver)
    registry.register(_RowsAdapter())
    return registry, resolver


_BOARD = """\
title: T
queries:
  plain:
    sql: SELECT 1 AS ts, 10 AS val
    source: db
  other:
    sql: SELECT 2 AS ts, 20 AS val
    source: db
  third:
    sql: SELECT 3 AS ts, 30 AS val
    source: db
charts:
  c:
    query: plain
    type: table
rows:
  - c
"""


def test_each_query_resolves_its_source_once(
    local_project: Callable[..., FilesystemProject], tmp_path: Path
) -> None:
    registry, resolver = _registry(local_project)
    compiled = compile_board(_BOARD)
    executor = Executor(
        compiled.board,
        adapter_registry=registry,
        query_registry=compiled.query_registry,
        result_cache=TrivialDuckDBCache(db_path=tmp_path / "c.duckdb"),
    )

    for name in ("plain", "other", "third"):
        resolver.resolve.reset_mock()
        executor.execute_query(name)
        assert resolver.resolve.call_count == 1, name


def test_incremental_tail_reuses_the_resolved_source(
    local_project: Callable[..., FilesystemProject], tmp_path: Path
) -> None:
    compiled = compile_board(_BOARD.replace("title: T", "title: T\nincremental: ts"))
    cache = TrivialDuckDBCache(db_path=tmp_path / "c.duckdb")

    def run() -> MagicMock:
        registry, resolver = _registry(local_project)
        Executor(
            compiled.board,
            adapter_registry=registry,
            query_registry=compiled.query_registry,
            result_cache=cache,
        ).execute_query("plain")
        return resolver

    run()
    warm = run()
    assert warm.resolve.call_count == 1


def test_unresolvable_source_keeps_the_resolver_error_fields(
    local_project: Callable[..., FilesystemProject], tmp_path: Path
) -> None:
    registry, resolver = _registry(local_project)
    resolver.resolve.side_effect = DbtChartsError.from_code(
        ERR_SOURCE_NOT_NAMED, available=["db"]
    )
    compiled = compile_board(_BOARD)
    executor = Executor(
        compiled.board,
        adapter_registry=registry,
        query_registry=compiled.query_registry,
        result_cache=TrivialDuckDBCache(db_path=tmp_path / "c.duckdb"),
    )

    with pytest.raises(QueryError) as caught:
        executor.execute_query("plain")

    assert caught.value.code == ERR_SOURCE_NOT_NAMED
    assert caught.value.fields["available"] == ["db"]
    assert caught.value.fields["query_name"] == "plain"


@pytest.mark.parametrize("failure", [ValueError("blocked host"), OSError("no dns")])
def test_a_resolver_failure_is_that_querys_error_and_is_recorded(
    local_project: Callable[..., FilesystemProject],
    tmp_path: Path,
    failure: Exception,
) -> None:
    registry, resolver = _registry(local_project)
    resolver.resolve.side_effect = failure
    compiled = compile_board(_BOARD)
    cache = TrivialDuckDBCache(db_path=tmp_path / "c.duckdb")
    executor = Executor(
        compiled.board,
        adapter_registry=registry,
        query_registry=compiled.query_registry,
        result_cache=cache,
    )

    with pytest.raises(QueryError):
        executor.execute_query("plain")

    assert [name for name, *_ in executor._query_data_ages] == ["plain"]
    assert cache.get(*executor.cache_key("plain"), ttl=None) is not None


def test_a_cached_query_is_served_without_resolving(
    local_project: Callable[..., FilesystemProject], tmp_path: Path
) -> None:
    compiled = compile_board(_BOARD)
    cache = TrivialDuckDBCache(db_path=tmp_path / "c.duckdb")

    def executor_for(registry: AdapterRegistry) -> Executor:
        return Executor(
            compiled.board,
            adapter_registry=registry,
            query_registry=compiled.query_registry,
            result_cache=cache,
        )

    executor_for(_registry(local_project)[0]).execute_query("plain")
    registry, resolver = _registry(local_project)
    resolver.resolve.side_effect = ValueError("blocked host")

    assert executor_for(registry).execute_query("plain") == [{"ts": 1, "val": 10}]
    resolver.resolve.assert_not_called()


_JINJA_BOARD = _BOARD.replace(
    "SELECT 1 AS ts, 10 AS val", "SELECT * FROM {{ ref('a') }}"
)


@pytest.mark.parametrize(
    "failure", [DbtChartsError("source gone"), ValueError("blocked host")]
)
def test_a_dbt_jinja_query_with_an_unresolvable_source_fails_as_that_query(
    local_project: Callable[..., FilesystemProject],
    tmp_path: Path,
    failure: Exception,
) -> None:
    registry, resolver = _registry(local_project)
    resolver.resolve.side_effect = failure
    compiled = compile_board(_JINJA_BOARD)
    cache = TrivialDuckDBCache(db_path=tmp_path / "c.duckdb")
    executor = Executor(
        compiled.board,
        adapter_registry=registry,
        query_registry=compiled.query_registry,
        result_cache=cache,
    )

    key = executor.cache_key("plain")
    with pytest.raises(QueryError):
        executor.execute_query("plain")

    assert [name for name, *_ in executor._query_data_ages] == ["plain"]
    assert executor.is_cached("plain") is False
    assert key == executor.cache_key("plain")


def test_an_unreadable_manifest_does_not_escape_the_cache_key(
    local_project: Callable[..., FilesystemProject], tmp_path: Path
) -> None:
    registry, _ = _registry(local_project)
    registry.dbt_manifest_version = MagicMock(  # type: ignore[method-assign] — stub the manifest read
        side_effect=ExecutionError("manifest unreadable")
    )
    compiled = compile_board(_JINJA_BOARD)
    executor = Executor(
        compiled.board,
        adapter_registry=registry,
        query_registry=compiled.query_registry,
        result_cache=TrivialDuckDBCache(db_path=tmp_path / "c.duckdb"),
    )

    assert executor.cache_key("plain") == executor.cache_key("plain")
    assert executor.is_cached("plain") is False
