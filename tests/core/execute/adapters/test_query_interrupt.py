"""`interrupt()` cancels in-flight queries and refuses new ones (serve shutdown)."""

from __future__ import annotations

import threading
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock, patch

import pytest

from dbt_charts.cli.filesystem_project import FilesystemProject
from dbt_charts.core.compile.models.query.normalized import SqlQuery
from dbt_charts.core.compile.models.source import DuckDBSourceConfig
from dbt_charts.core.diagnostics.codes_execute import ERR_QUERY_INTERRUPTED
from dbt_charts.core.diagnostics.execution import QueryError
from dbt_charts.core.execute.adapters.adapter_registry import AdapterRegistry
from dbt_charts.core.execute.adapters.base import QueryResult
from dbt_charts.core.execute.adapters.dbt_adapter import DbtAdapter
from dbt_charts.core.execute.adapters.duckdb_adapter import DuckDBAdapter
from dbt_charts.core.execute.adapters.sql_adapter import _LIVENESS_SQL, SqlAdapter

_SLOW_SQL = (
    "SELECT count(*) AS n FROM range(100000000000) t(i) "
    "WHERE md5(i::varchar) LIKE 'abcdef%'"
)
_USER_SQL = "SELECT 42 AS answer"
_BUILD_ADAPTER = "dbt_charts.core.execute.adapters.dbt_adapter_factory.build_adapter"
_POSTGRES_SOURCE: dict[str, Any] = {
    "type": "postgres",
    "host": "h",
    "dbname": "db",
    "user": "u",
    "password": "p",
}


def _query(sql: str) -> SqlQuery:
    return SqlQuery(sql=sql, source="db")


def _in_thread(fn: Callable[[], Any]) -> tuple[threading.Thread, list[Any]]:
    out: list[Any] = []
    thread = threading.Thread(target=lambda: out.append(fn()), daemon=True)
    thread.start()
    return thread, out


def _dbt_adapter_mock() -> MagicMock:
    adapter = MagicMock()
    adapter.connection_named.return_value.__enter__.return_value = None
    adapter.connection_named.return_value.__exit__.return_value = False
    return adapter


class TestDuckDBInterrupt:
    def test_interrupt_stops_a_running_query(self) -> None:
        adapter = DuckDBAdapter(source_config=DuckDBSourceConfig(type="duckdb"))
        try:
            thread, out = _in_thread(lambda: adapter._execute(_query(_SLOW_SQL)))
            time.sleep(0.5)  # let the query reach DuckDB
            adapter.interrupt()
            thread.join(10)
            assert not thread.is_alive(), "query kept running after interrupt"
            result: QueryResult = out[0]
            assert result.error is not None
            assert result.error.code is ERR_QUERY_INTERRUPTED
        finally:
            adapter.close()

    def test_interrupted_adapter_refuses_new_queries(self) -> None:
        adapter = DuckDBAdapter(source_config=DuckDBSourceConfig(type="duckdb"))
        try:
            adapter.interrupt()
            error = adapter._execute(_query("SELECT 1 AS n")).error
            assert error is not None
            assert error.code is ERR_QUERY_INTERRUPTED
        finally:
            adapter.close()


@pytest.mark.usefixtures("typed_via_execute")
class TestSqlAdapterInterrupt:
    def test_interrupt_cancels_the_running_warehouse_query(
        self, tmp_path: Path, local_project: Callable[..., FilesystemProject]
    ) -> None:
        sa = SqlAdapter(
            project=local_project(tmp_path),
            dbt_project_path=None,
            profile_type="postgres",
        )
        started, release = threading.Event(), threading.Event()
        built = _dbt_adapter_mock()

        def _blocking_execute(sql: str, *_a: Any, **_k: Any) -> Any:
            if sql == _LIVENESS_SQL:
                raise RuntimeError("session terminated")  # the cancel killed it
            if sql != _USER_SQL:
                return (None, MagicMock())  # connection setup
            started.set()
            release.wait(10)
            raise RuntimeError("canceled by the warehouse")

        built.execute.side_effect = _blocking_execute
        built.connections.get_thread_connection.return_value.state = "open"
        with patch(_BUILD_ADAPTER, return_value=built) as build:
            pool = sa._get_source_pool(_POSTGRES_SOURCE)

            def _caller() -> Exception | None:
                try:
                    pool.execute(_USER_SQL, None)
                except QueryError as exc:
                    return exc
                return None

            thread, out = _in_thread(_caller)
            assert started.wait(5)
            sa.interrupt()
            built.cancel_open_connections.assert_called_once()
            release.set()
            thread.join(10)
            assert not thread.is_alive()
            assert out[0].code is ERR_QUERY_INTERRUPTED
            # Postgres' cancel kills the session; the pool must not reconnect
            # and run the query again.
            assert build.call_count == 1

            with pytest.raises(QueryError) as refused:
                pool.execute(_USER_SQL, None)
            assert refused.value.code is ERR_QUERY_INTERRUPTED
            with pytest.raises(QueryError) as refused_new:
                sa._get_source_pool({**_POSTGRES_SOURCE, "host": "other"})
            assert refused_new.value.code is ERR_QUERY_INTERRUPTED
            sa.close()


@pytest.mark.usefixtures("typed_via_execute")
class TestDbtAdapterInterrupt:
    def test_interrupt_cancels_open_connections_and_refuses_new_queries(
        self, tmp_path: Path, local_project: Callable[..., FilesystemProject]
    ) -> None:
        adapter = DbtAdapter(
            project=local_project(tmp_path),
            dbt_project_path=tmp_path,
            target_name="dev",
        )
        built = _dbt_adapter_mock()
        adapter._adapter = built

        adapter.interrupt()

        built.cancel_open_connections.assert_called_once()
        error = adapter._execute(_query("SELECT 1")).error
        assert error is not None
        assert error.code is ERR_QUERY_INTERRUPTED
        built.execute.assert_not_called()

    def test_a_cancelled_query_reports_the_interrupt(
        self, tmp_path: Path, local_project: Callable[..., FilesystemProject]
    ) -> None:
        adapter = DbtAdapter(
            project=local_project(tmp_path),
            dbt_project_path=tmp_path,
            target_name="dev",
        )
        built = _dbt_adapter_mock()
        adapter._adapter = built
        adapter._dialect = "postgres"

        def _cancelled(*_a: Any, **_k: Any) -> Any:
            adapter.interrupt()
            raise RuntimeError("canceling statement due to user request")

        built.execute.side_effect = _cancelled
        with patch(
            "dbt_charts.core.execute.adapters.dbt_adapter.open_connection"
        ) as conn:
            conn.return_value.__enter__.return_value = None
            conn.return_value.__exit__.return_value = False
            error = adapter._execute(_query("SELECT 1")).error

        assert error is not None
        assert error.code is ERR_QUERY_INTERRUPTED

    def test_interrupt_before_first_query_has_nothing_to_cancel(
        self, tmp_path: Path, local_project: Callable[..., FilesystemProject]
    ) -> None:
        adapter = DbtAdapter(
            project=local_project(tmp_path),
            dbt_project_path=tmp_path,
            target_name="dev",
        )
        adapter.interrupt()
        error = adapter._execute(_query("SELECT 1")).error
        assert error is not None
        assert error.code is ERR_QUERY_INTERRUPTED


class TestRegistryInterrupt:
    def test_interrupt_reaches_every_adapter_that_supports_it(
        self, tmp_path: Path, local_project: Callable[..., FilesystemProject]
    ) -> None:
        registry = AdapterRegistry(project=local_project(tmp_path))
        adapters = [MagicMock(supported_types={"sql"}) for _ in range(2)]
        for adapter in adapters:
            registry.register(adapter)

        registry.interrupt()

        for adapter in adapters:
            adapter.interrupt.assert_called_once()


def test_an_interrupted_query_is_not_cached_as_a_failure() -> None:
    from dbt_charts.core.execute.executor import Executor

    executor = MagicMock()
    query = _query("SELECT 1")
    Executor._cache_failure(
        executor, "q", query, None, QueryError.from_code(ERR_QUERY_INTERRUPTED)
    )
    executor._cache_outcome.assert_not_called()

    Executor._cache_failure(executor, "q", query, None, QueryError("rejected"))
    executor._cache_outcome.assert_called_once()
