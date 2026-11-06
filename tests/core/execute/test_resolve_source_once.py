"""A query's source is resolved once per execution and the result is passed down."""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path
from unittest.mock import MagicMock

import nanoarrow as na
import pyarrow as pa
import pytest

from dbt_charts.cli.filesystem_project import FilesystemProject
from dbt_charts.core.compile import compile as compile_board
from dbt_charts.core.compile.models.source import (
    DuckDBSourceConfig,
    ResolvedSourceConfig,
)
from dbt_charts.core.diagnostics import ERR_SOURCE_NOT_NAMED
from dbt_charts.core.diagnostics.base import DbtChartsError
from dbt_charts.core.diagnostics.execution import ExecutionError, QueryError
from dbt_charts.core.dialects import SQLDialect
from dbt_charts.core.execute.backends.base import BackendRegistry
from dbt_charts.core.execute.compose import ResolvedQuery
from dbt_charts.core.execute.env import ExecEnv, make_exec_env
from dbt_charts.core.execute.plan import plan
from dbt_charts.core.execute.source_resolver import SourceResolver
from dbt_charts.core.execute.sql_literals import INLINE_PLACEHOLDERS
from dbt_charts.core.execute.trivial_local_cache import TrivialDuckDBCache

from ..._run import Run


class _RowsBackend:
    param_style: SQLDialect = INLINE_PLACEHOLDERS

    def execute(self, source: ResolvedSourceConfig, query: ResolvedQuery) -> na.Array:
        return na.Array(pa.table({"ts": [1], "val": [10]}))

    def interrupt(self) -> None:
        pass

    def close(self) -> None:
        pass


def _registry(
    local_project: Callable[..., FilesystemProject],
) -> tuple[ExecEnv, MagicMock]:
    resolver = MagicMock(spec=SourceResolver)
    resolver.resolve.return_value = DuckDBSourceConfig(type="duckdb", path=":memory:")
    registry = make_exec_env(
        project=local_project(Path()),
        resolver=resolver,
        backends=BackendRegistry({"duckdb": _RowsBackend()}),
    )
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
    executor = Run(
        compiled.board,
        env=registry,
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
        Run(
            compiled.board,
            env=registry,
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
    executor = Run(
        compiled.board,
        env=registry,
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
    executor = Run(
        compiled.board,
        env=registry,
        query_registry=compiled.query_registry,
        result_cache=cache,
    )

    with pytest.raises(QueryError):
        executor.execute_query("plain")

    assert [name for name, *_ in executor.ledger.ages] == ["plain"]
    assert cache.get(*plan(executor.ctx, ["plain"])[0].cache_key, ttl=None) is not None


def test_a_cached_query_is_served_without_resolving(
    local_project: Callable[..., FilesystemProject], tmp_path: Path
) -> None:
    compiled = compile_board(_BOARD)
    cache = TrivialDuckDBCache(db_path=tmp_path / "c.duckdb")

    def executor_for(registry: ExecEnv) -> Run:
        return Run(
            compiled.board,
            env=registry,
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
    executor = Run(
        compiled.board,
        env=registry,
        query_registry=compiled.query_registry,
        result_cache=cache,
    )

    key = plan(executor.ctx, ["plain"])[0].cache_key
    with pytest.raises(QueryError):
        executor.execute_query("plain")

    assert [name for name, *_ in executor.ledger.ages] == ["plain"]
    assert "plain" not in executor.lookup(["plain"]).hits
    assert key == plan(executor.ctx, ["plain"])[0].cache_key


def test_an_unreadable_manifest_does_not_escape_the_cache_key(
    local_project: Callable[..., FilesystemProject], tmp_path: Path
) -> None:
    registry, _ = _registry(local_project)
    registry.dbt_refs.manifest_version = MagicMock(  # type: ignore[method-assign] — stub the manifest read
        side_effect=ExecutionError("manifest unreadable")
    )
    compiled = compile_board(_JINJA_BOARD)
    executor = Run(
        compiled.board,
        env=registry,
        query_registry=compiled.query_registry,
        result_cache=TrivialDuckDBCache(db_path=tmp_path / "c.duckdb"),
    )

    assert (
        plan(executor.ctx, ["plain"])[0].cache_key
        == plan(executor.ctx, ["plain"])[0].cache_key
    )
    assert "plain" not in executor.lookup(["plain"]).hits
