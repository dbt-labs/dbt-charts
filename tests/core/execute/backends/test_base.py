"""Backends: the routes map, and the driver backend a driver-kind source runs on."""

from __future__ import annotations

from collections.abc import Callable, Generator
from pathlib import Path
from unittest.mock import patch

import nanoarrow as na
import pyarrow as pa
import pytest

from dbt_charts.cli.filesystem_project import FilesystemProject
from dbt_charts.core.compile.models.query.normalized import SqlQuery
from dbt_charts.core.compile.models.source import (
    LakeComputeTokenSource,
    TrinoSourceConfig,
)
from dbt_charts.core.diagnostics.codes_execute import (
    ERR_ADAPTER_UNAVAILABLE,
    ERR_BINDER_UNKNOWN_RELATION,
)
from dbt_charts.core.diagnostics.execution import QueryError
from dbt_charts.core.dialects import SQLDialect
from dbt_charts.core.execute.backends.base import Backend, BackendRegistry
from dbt_charts.core.execute.backends.driver import DriverBackend
from dbt_charts.core.execute.compose import ResolvedQuery
from dbt_charts.core.execute.env import ExecEnv, close_env, make_exec_env
from dbt_charts.core.execute.query_result import (
    QueryResult,
    RowFetchLimit,
    WarehouseStatementError,
    resolve_effective_row_limit,
)
from dbt_charts.core.execute.run import execute_resolved, run_query
from dbt_charts.core.execute.sql_literals import INLINE_PLACEHOLDERS

from ...._fake_driver import FakeDriverConnection, fake_lakecompute_driver

_OPEN = "dbt_charts.core.execute.drivers.lakecompute.open_lakecompute"

_LAKE = LakeComputeTokenSource(type="lakecompute", token="t")
_TRINO = TrinoSourceConfig(
    type="trino", host="h", user="u", database="hive", schema="s"
)
_TABLE = pa.table({"n": [1, 2]})


class _StubBackend:
    param_style: SQLDialect = INLINE_PLACEHOLDERS

    def execute(self, source: object, query: ResolvedQuery) -> na.Array:
        return na.Array(_TABLE)

    def interrupt(self) -> None:
        self.interrupted = True

    def close(self) -> None:
        self.closed = True


def _p(index: int) -> str:
    return INLINE_PLACEHOLDERS.param(index)


def _resolved(
    sql: str = "SELECT 1", params: tuple[object, ...] = (), setup: tuple[str, ...] = ()
) -> ResolvedQuery:
    return ResolvedQuery(
        sql=sql,
        params=params,
        setup_sql=setup,
        limit=resolve_effective_row_limit(100),
    )


def test_for_source_returns_the_backend_mapped_to_the_source_kind() -> None:
    lake, trino = _StubBackend(), _StubBackend()
    registry = BackendRegistry({"lakecompute": lake, "trino": trino})
    assert registry.for_source(_LAKE) is lake
    assert registry.for_source(_TRINO) is trino


def test_an_unmapped_source_kind_names_the_kind() -> None:
    registry = BackendRegistry({"trino": _StubBackend()})
    with pytest.raises(QueryError, match="no backend for source kind 'lakecompute'"):
        registry.for_source(_LAKE)


def test_close_and_interrupt_reach_a_shared_backend_once() -> None:
    calls: list[str] = []

    class Counting(_StubBackend):
        def close(self) -> None:
            calls.append("close")

        def interrupt(self) -> None:
            calls.append("interrupt")

    shared: Backend = Counting()
    registry = BackendRegistry({"lakecompute": shared, "trino": shared})
    registry.interrupt()
    registry.close()
    assert calls == ["interrupt", "close"]


def test_the_driver_backend_inlines_params_and_runs_setup_sql_in_one_session() -> None:
    backend = DriverBackend(max_workers=1)
    try:
        with fake_lakecompute_driver(_TABLE) as (connection, opener):
            table = backend.execute(
                _LAKE,
                _resolved(
                    f"SELECT n FROM t WHERE n > {_p(1)} AND s = {_p(2)}",
                    params=(1, "o'k"),
                    setup=("CREATE TEMP TABLE t AS SELECT 1 AS n",),
                ),
            )
    finally:
        backend.close()
    assert len(table) == 2
    assert opener.call_count == 1
    assert connection.session_statements == ["CREATE TEMP TABLE t AS SELECT 1 AS n"]
    assert connection.statements == ["SELECT n FROM t WHERE n > 1 AND s = 'o''k'"]


class _Failing(FakeDriverConnection):
    def execute(self, sql: str, limit: RowFetchLimit) -> na.Array:
        raise WarehouseStatementError("42S02", "no such table")


def _observed_run(
    tmp_path: Path,
    local_project: Callable[..., FilesystemProject],
    connection: FakeDriverConnection,
) -> tuple[QueryResult, list[tuple[str, str]]]:
    seen: list[tuple[str, str]] = []
    registry = make_exec_env(
        project=local_project(tmp_path),
        backends=BackendRegistry({"lakecompute": DriverBackend(max_workers=1)}),
        observers=[lambda kind, status, _seconds: seen.append((kind, status))],
    )
    query = SqlQuery(sql="SELECT n FROM t", source="lake")
    try:
        with patch(_OPEN, return_value=connection):
            result = execute_resolved(
                registry, _resolved("SELECT n FROM t"), query, source_config=_LAKE
            )
    finally:
        close_env(registry)
    return result, seen


def test_a_driver_source_runs_on_its_backend_and_the_observers_see_it(
    tmp_path: Path, local_project: Callable[..., FilesystemProject]
) -> None:
    result, seen = _observed_run(tmp_path, local_project, FakeDriverConnection(_TABLE))
    assert result.error is None
    assert result.data == [{"n": 1}, {"n": 2}]
    assert seen == [("lakecompute", "success")]


def test_a_failed_driver_statement_is_a_classified_error_the_observers_see(
    tmp_path: Path, local_project: Callable[..., FilesystemProject]
) -> None:
    result, seen = _observed_run(tmp_path, local_project, _Failing(_TABLE))
    assert result.error is not None
    assert result.error.code is ERR_BINDER_UNKNOWN_RELATION
    assert seen == [("lakecompute", "error")]


@pytest.fixture
def _dbt_path_is_forbidden() -> Generator[None]:
    with patch(
        "dbt_charts.core.execute.backends.dbt_adapter_factory.build_adapter",
        side_effect=AssertionError("a driver source must never build a dbt adapter"),
    ):
        yield


def _executing_registry(
    tmp_path: Path,
    local_project: Callable[..., FilesystemProject],
    seen: list[str],
) -> ExecEnv:
    return make_exec_env(
        project=local_project(tmp_path),
        backends=BackendRegistry({"lakecompute": DriverBackend(max_workers=1)}),
        observers=[lambda _kind, status, _seconds: seen.append(status)],
    )


@pytest.mark.usefixtures("_dbt_path_is_forbidden")
def test_execute_runs_a_driver_query_on_its_backend(
    tmp_path: Path, local_project: Callable[..., FilesystemProject]
) -> None:
    seen: list[str] = []
    registry = _executing_registry(tmp_path, local_project, seen)
    try:
        with patch(_OPEN, return_value=FakeDriverConnection(_TABLE)):
            result = run_query(
                registry,
                SqlQuery(sql="SELECT n FROM t", source="lake"),
                source_config=_LAKE,
            )
    finally:
        close_env(registry)
    assert result.error is None
    assert result.data == [{"n": 1}, {"n": 2}]
    assert seen == ["success"]


def test_a_composition_refusal_on_a_driver_source_reaches_the_observers(
    tmp_path: Path, local_project: Callable[..., FilesystemProject]
) -> None:
    seen: list[str] = []
    registry = _executing_registry(tmp_path, local_project, seen)
    try:
        result = run_query(
            registry, SqlQuery(sql="DROP TABLE t", source="lake"), source_config=_LAKE
        )
    finally:
        close_env(registry)
    assert result.error is not None
    assert seen == ["error"]


def test_an_unrouted_source_kind_is_unavailable_and_reaches_the_observers(
    tmp_path: Path, local_project: Callable[..., FilesystemProject]
) -> None:
    seen: list[str] = []
    registry = make_exec_env(
        project=local_project(tmp_path),
        backends=BackendRegistry({}),
        observers=[lambda _kind, status, _seconds: seen.append(status)],
    )
    result = run_query(
        registry, SqlQuery(sql="SELECT 1", source="lake"), source_config=_LAKE
    )
    assert result.error is not None
    assert result.error.code is ERR_ADAPTER_UNAVAILABLE
    assert "'lakecompute'" in str(result.error)
    assert seen == ["error"]


def test_a_driver_source_with_no_route_is_refused_by_name(
    tmp_path: Path, local_project: Callable[..., FilesystemProject]
) -> None:
    registry = make_exec_env(
        project=local_project(tmp_path), backends=BackendRegistry({})
    )
    query = SqlQuery(sql="SELECT 1", source="lake")
    result = execute_resolved(registry, _resolved(), query, source_config=_LAKE)
    assert result.error is not None
    assert result.error.code is ERR_ADAPTER_UNAVAILABLE
    assert "no backend for source kind 'lakecompute'" in str(result.error)
