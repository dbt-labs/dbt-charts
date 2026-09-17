"""Tests for the per-source connection pool concurrency model.

Pin the structural guarantees that make the consolidated parallel stage actually
parallelize against a remote warehouse:

- same source_config → one ``_SourcePool`` (built lazily, no eager adapter build)
- each worker thread builds its OWN dbt adapter (independent dbt ConnectionManager
  → no per-adapter lock serializing concurrent queries)
- pool execution adapters are built with ``register_macros=False`` so the dbt macro
  resolver and context generator are never wired onto adapters that only run raw SQL
- a per-thread connection is opened once and reused (the connect cost is paid once)
- ``auto_begin=False`` (dbt opens no transaction; the driver-side half of that
  guarantee is pinned in test_dbt_adapter_factory.py)
- session-scoped objects from a ``setup_sql`` do not survive to the next query
- only a dead connection is retried; a query the warehouse rejected is not
- reconnect on a dead session closes the dropped adapter's connections (no leaked
  warehouse session) before rebuilding
- ``close()`` closes every per-thread adapter that was built
"""

from __future__ import annotations

import threading
from collections.abc import Callable
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock, patch

import pytest

from dbt_charts.cli.filesystem_project import FilesystemProject
from dbt_charts.core.compile.config import get_execution_config
from dbt_charts.core.execute.adapters.sql_adapter import (
    SqlAdapter,
    _source_config_hash,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_adapter_mock() -> MagicMock:
    """Return a mock dbt adapter with a re-entrant connection_named ctx manager."""
    adapter = MagicMock()
    ctx = MagicMock()
    ctx.__enter__ = MagicMock(return_value=ctx)
    ctx.__exit__ = MagicMock(return_value=False)
    adapter.connection_named.return_value = ctx

    table_mock = MagicMock()
    table_mock.column_names = ["v"]
    table_mock.rows = [(1,)]
    adapter.execute.return_value = (None, table_mock)
    return adapter


def _make_sql_adapter(
    tmp_path: Path, local_project: Callable[..., FilesystemProject]
) -> SqlAdapter:
    return SqlAdapter(
        project=local_project(tmp_path),
        dbt_project_path=None,
        profile_type="duckdb",
    )


_POSTGRES_SOURCE: dict[str, Any] = {
    "type": "postgres",
    "host": "h",
    "dbname": "db",
    "user": "u",
    "password": "p",
}

_SNOWFLAKE_SOURCE: dict[str, Any] = {
    "type": "snowflake",
    "account": "xy",
    "user": "u",
    "database": "db",
    "warehouse": "wh",
}

_BIGQUERY_SOURCE: dict[str, Any] = {
    "type": "bigquery",
    "project": "my-gcp-project",
    "dataset": "my_dataset",
    "method": "oauth",
    "threads": 1,
}

_DATABRICKS_SOURCE: dict[str, Any] = {
    "type": "databricks",
    "host": "h.cloud.databricks.com",
    "http_path": "/sql/1.0/warehouses/x",
    "token": "t",
}

_BUILD_ADAPTER = "dbt_charts.core.execute.adapters.dbt_adapter_factory.build_adapter"


# ---------------------------------------------------------------------------
# _source_config_hash
# ---------------------------------------------------------------------------


class TestSourceConfigHash:
    def test_same_config_same_hash(self) -> None:
        assert _source_config_hash(_POSTGRES_SOURCE) == _source_config_hash(
            dict(_POSTGRES_SOURCE)
        )

    def test_different_config_different_hash(self) -> None:
        assert _source_config_hash(_POSTGRES_SOURCE) != _source_config_hash(
            _SNOWFLAKE_SOURCE
        )

    def test_key_order_independent(self) -> None:
        a = {"type": "postgres", "host": "h", "dbname": "db"}
        b = {"dbname": "db", "host": "h", "type": "postgres"}
        assert _source_config_hash(a) == _source_config_hash(b)

    def test_attribution_does_not_change_hash(self) -> None:
        """Attribution is cost metadata, not connection identity."""
        labeled = {**_POSTGRES_SOURCE, "attribution": {"team": "analytics"}}
        assert _source_config_hash(labeled) == _source_config_hash(_POSTGRES_SOURCE)

    def test_editing_attribution_does_not_change_hash(self) -> None:
        a = {**_POSTGRES_SOURCE, "attribution": {"team": "analytics"}}
        b = {**_POSTGRES_SOURCE, "attribution": {"team": "platform"}}
        assert _source_config_hash(a) == _source_config_hash(b)


# ---------------------------------------------------------------------------
# Per-source pool: one pool per source, built lazily (no eager adapter build)
# ---------------------------------------------------------------------------


class TestSourcePoolCache:
    def test_same_source_config_returns_same_pool(
        self, tmp_path: Path, local_project: Callable[..., FilesystemProject]
    ) -> None:
        """Two execute calls with the same source build the adapter only once."""
        sa = _make_sql_adapter(tmp_path, local_project)
        with patch(_BUILD_ADAPTER, return_value=_make_adapter_mock()) as mock_build:
            pool = sa._get_source_pool(_POSTGRES_SOURCE)
            pool.execute("SELECT 1", setup_sql=None)
            pool.execute("SELECT 2", setup_sql=None)
        # Same source → same pool → adapter built only once (persistent worker)
        mock_build.assert_called_once()

    def test_get_source_pool_does_not_build_adapter_eagerly(
        self, tmp_path: Path, local_project: Callable[..., FilesystemProject]
    ) -> None:
        """The adapter is built per-thread on first execute(), not at pool creation."""
        sa = _make_sql_adapter(tmp_path, local_project)
        with patch(_BUILD_ADAPTER, return_value=_make_adapter_mock()) as mock_build:
            sa._get_source_pool(_POSTGRES_SOURCE)
        mock_build.assert_not_called()

    def test_different_source_configs_build_separate_pools(
        self, tmp_path: Path, local_project: Callable[..., FilesystemProject]
    ) -> None:
        """Two different sources cause two distinct adapter builds."""
        sa = _make_sql_adapter(tmp_path, local_project)
        with patch(_BUILD_ADAPTER, return_value=_make_adapter_mock()) as mock_build:
            pool_pg = sa._get_source_pool(_POSTGRES_SOURCE)
            pool_sf = sa._get_source_pool(_SNOWFLAKE_SOURCE)
            pool_pg.execute("SELECT 1", setup_sql=None)
            pool_sf.execute("SELECT 1", setup_sql=None)
        # Different sources → different pools → adapter built once per source
        assert mock_build.call_count == 2

    def test_attribution_does_not_split_the_pool(
        self, tmp_path: Path, local_project: Callable[..., FilesystemProject]
    ) -> None:
        """Per-dashboard attribution must not cost a warehouse handshake per dashboard."""
        sa = _make_sql_adapter(tmp_path, local_project)
        pool_a = sa._get_source_pool({**_POSTGRES_SOURCE, "attribution": {"team": "a"}})
        pool_b = sa._get_source_pool({**_POSTGRES_SOURCE, "attribution": {"team": "b"}})
        assert pool_a is pool_b


class TestConnectFailureDetection:
    def test_databricks_connect_failure_raises_connection_setup_failed(
        self, tmp_path: Path, local_project: Callable[..., FilesystemProject]
    ) -> None:
        """databricks has no statement_timeout_sql and isn't bigquery, so
        _ensure_connected() previously never touched the lazy connection
        handle for it — a connect failure (bad token, unreachable host) leaked
        past ConnectionSetupFailed and was only raised later from _run(),
        landing in classify_warehouse_error as a false "warehouse rejected
        the query" outcome. _ensure_connected() must force the handle open
        for every dialect so this is caught here instead.
        """
        from dbt_charts.core.execute.adapters.dbt_adapter_factory import (
            ConnectionSetupFailed,
        )

        class _FailingHandleConnection:
            @property
            def handle(self) -> object:
                raise RuntimeError("Invalid access token")

        def _build(*_a: Any, **_k: Any) -> MagicMock:
            adapter = _make_adapter_mock()
            adapter.connections.get_thread_connection.return_value = (
                _FailingHandleConnection()
            )
            return adapter

        sa = _make_sql_adapter(tmp_path, local_project)
        with patch(_BUILD_ADAPTER, side_effect=_build):
            pool = sa._get_source_pool(_DATABRICKS_SOURCE)
            with pytest.raises(ConnectionSetupFailed, match="Invalid access token"):
                pool.execute("SELECT 1", setup_sql=None)
            pool.close()


# ---------------------------------------------------------------------------
# Concurrency + cross-render warmth: persistent worker threads, independent
# adapters (one connection manager per worker)
# ---------------------------------------------------------------------------


class TestSourcePoolConcurrency:
    def test_concurrent_queries_use_independent_adapters(
        self, tmp_path: Path, local_project: Callable[..., FilesystemProject]
    ) -> None:
        """N concurrent queries → N independent dbt adapters → N connection managers.

        The structural guarantee behind true concurrency: each persistent worker
        runs on its own dbt ConnectionManager, so the per-adapter lock cannot
        serialize queries across workers. A barrier inside execute() forces all N
        to be in flight at once, so N distinct workers (and adapters) are used.
        """
        sa = _make_sql_adapter(tmp_path, local_project)
        built: list[MagicMock] = []
        built_lock = threading.Lock()
        barrier = threading.Barrier(4)

        def _new_adapter(*_a: Any, **_k: Any) -> MagicMock:
            adapter = _make_adapter_mock()

            def _blocking_execute(*_ea: Any, **_ek: Any) -> Any:
                barrier.wait()  # hold the worker until all 4 are concurrent
                table = MagicMock()
                table.column_names = ["v"]
                table.rows = [(1,)]
                return (None, table)

            adapter.execute.side_effect = _blocking_execute
            with built_lock:
                built.append(adapter)
            return adapter

        with patch(_BUILD_ADAPTER, side_effect=_new_adapter):
            pool = sa._get_source_pool(_POSTGRES_SOURCE)
            callers = [
                threading.Thread(target=pool.execute, args=("SELECT 1", None))
                for _ in range(4)
            ]
            for t in callers:
                t.start()
            for t in callers:
                t.join()
            pool.close()

        # one adapter per concurrent worker, each on its own connection
        assert len(built) == 4
        for adapter in built:
            assert adapter.connection_named.call_count == 1

    def test_adapter_persists_across_ephemeral_caller_threads(
        self, tmp_path: Path, local_project: Callable[..., FilesystemProject]
    ) -> None:
        """Warmth across renders: the pool's worker outlives the caller threads.

        Each "render" submits from a fresh (ephemeral) caller thread, mimicking
        the per-render ThreadPoolExecutor. Because work runs on the pool's OWN
        persistent worker, the adapter is built once and reused — not rebuilt cold
        per render. This is the cross-render warmth the persistent pool delivers.
        """
        sa = _make_sql_adapter(tmp_path, local_project)
        with patch(_BUILD_ADAPTER, return_value=_make_adapter_mock()) as mock_build:
            pool = sa._get_source_pool(_POSTGRES_SOURCE)
            for _ in range(3):  # three successive "renders", each a new caller thread
                t = threading.Thread(target=pool.execute, args=("SELECT 1", None))
                t.start()
                t.join()
            pool.close()

        # built once total despite three separate caller threads
        mock_build.assert_called_once()

    def test_execute_runs_on_persistent_pool_thread(
        self, tmp_path: Path, local_project: Callable[..., FilesystemProject]
    ) -> None:
        """execute() dispatches to the pool's worker, not the calling thread."""
        sa = _make_sql_adapter(tmp_path, local_project)
        run_threads: list[str] = []
        mock_adapter = _make_adapter_mock()

        def _record_thread(*_a: Any, **_k: Any) -> Any:
            run_threads.append(threading.current_thread().name)
            table = MagicMock()
            table.column_names = ["v"]
            table.rows = [(1,)]
            return (None, table)

        mock_adapter.execute.side_effect = _record_thread
        with patch(_BUILD_ADAPTER, return_value=mock_adapter):
            pool = sa._get_source_pool(_POSTGRES_SOURCE)
            pool.execute("SELECT 1", setup_sql=None)
            pool.close()

        assert run_threads and run_threads[0].startswith("dct-srcpool")
        assert run_threads[0] != threading.current_thread().name

    def test_pool_adapters_skip_factory_macro_registration(
        self, tmp_path: Path, local_project: Callable[..., FilesystemProject]
    ) -> None:
        """Execution adapters skip macro wiring: raw SQL needs no macro context."""
        sa = _make_sql_adapter(tmp_path, local_project)
        with patch(_BUILD_ADAPTER, return_value=_make_adapter_mock()) as mock_build:
            pool = sa._get_source_pool(_POSTGRES_SOURCE)
            pool.execute("SELECT 1", setup_sql=None)
        mock_build.assert_called_once()
        assert mock_build.call_args.kwargs.get("register_macros") is False

    def test_connection_opened_once_per_worker(
        self, tmp_path: Path, local_project: Callable[..., FilesystemProject]
    ) -> None:
        sa = _make_sql_adapter(tmp_path, local_project)
        mock_adapter = _make_adapter_mock()
        with patch(_BUILD_ADAPTER, return_value=mock_adapter):
            pool = sa._get_source_pool(_POSTGRES_SOURCE)
            pool.execute("SELECT 1", setup_sql=None)
            pool.execute("SELECT 2", setup_sql=None)

        # sequential executes reuse the same warm worker: built + connected once.
        # execute is called 3 times: once for the connection-setup timeout SQL,
        # then once per data query (2).
        assert mock_adapter.connection_named.call_count == 1
        assert mock_adapter.execute.call_count == 3

    def test_pool_width_follows_env_max_workers(
        self,
        tmp_path: Path,
        monkeypatch: Any,
        local_project: Callable[..., FilesystemProject],
    ) -> None:
        """Inner pool width honors DCT_MAX_WORKERS, matching the render-time pool.

        Regression: _SourcePool used to size from config only, capping warehouse
        concurrency below an env-raised max_workers.
        """
        from dbt_charts.core.compile.config import reset_config

        monkeypatch.setenv("DCT_MAX_WORKERS", "11")
        reset_config()
        # adapter max_workers=None → resolve env
        sa = _make_sql_adapter(tmp_path, local_project)
        with patch(_BUILD_ADAPTER, return_value=_make_adapter_mock()):
            pool = sa._get_source_pool(_POSTGRES_SOURCE)
        try:
            assert pool._pool._max_workers == 11
        finally:
            pool.close()
            reset_config()

    def test_session_state_is_reset_before_the_next_query(
        self, tmp_path: Path, local_project: Callable[..., FilesystemProject]
    ) -> None:
        """setup_sql creates session-scoped objects (temp tables/functions/views) that
        outlive the query on a pooled connection: the next render of the same board
        collides with its own leftovers, and a different board can read them."""
        from dbt_charts.core.dialects import get_dialect

        sa = _make_sql_adapter(tmp_path, local_project)
        adapter = _make_adapter_mock()
        with patch(_BUILD_ADAPTER, return_value=adapter):
            pool = sa._get_source_pool(_POSTGRES_SOURCE)
            pool.execute("SELECT 1", "CREATE TEMP TABLE scratch AS SELECT 1")
            adapter.execute.reset_mock()
            pool.execute("SELECT 2", setup_sql=None)

        sent = [call.args[0] for call in adapter.execute.call_args_list]
        assert sent[0] == get_dialect("postgres").session_reset_statements()[0]

    def test_a_clean_session_is_not_reset(
        self, tmp_path: Path, local_project: Callable[..., FilesystemProject]
    ) -> None:
        """Only setup_sql can create session state, so a reset on every query would
        buy a round trip per query for nothing."""
        sa = _make_sql_adapter(tmp_path, local_project)
        adapter = _make_adapter_mock()
        with patch(_BUILD_ADAPTER, return_value=adapter):
            pool = sa._get_source_pool(_POSTGRES_SOURCE)
            pool.execute("SELECT 1", setup_sql=None)
            adapter.execute.reset_mock()
            pool.execute("SELECT 2", setup_sql=None)

        sent = [call.args[0] for call in adapter.execute.call_args_list]
        assert sent == ["SELECT 2"]

    def test_a_dialect_with_no_reset_drops_the_connection_instead(
        self, tmp_path: Path, local_project: Callable[..., FilesystemProject]
    ) -> None:
        """BigQuery has no DISCARD equivalent for its session-scoped temp functions.
        A fresh connection is the only honest way to guarantee a clean session."""
        sa = _make_sql_adapter(tmp_path, local_project)
        adapters: list[MagicMock] = []

        def _build(*_a: Any, **_k: Any) -> MagicMock:
            adapter = _make_adapter_mock()
            adapters.append(adapter)
            return adapter

        with patch(_BUILD_ADAPTER, side_effect=_build):
            pool = sa._get_source_pool(_BIGQUERY_SOURCE)
            pool.execute("SELECT 1", "CREATE TEMP FUNCTION f() AS (1)")
            pool.execute("SELECT 2", setup_sql=None)

        adapters[0].connections.cleanup_all.assert_called_once()
        assert len(adapters) == 2

    def test_a_query_error_on_a_live_connection_is_not_retried(
        self, tmp_path: Path, local_project: Callable[..., FilesystemProject]
    ) -> None:
        """Re-sending a query the warehouse rejected just fails twice, and the
        reconnect throws away a warm connection to do it."""
        from dbt_charts.core.execute.adapters.sql_adapter import _LIVENESS_SQL

        sa = _make_sql_adapter(tmp_path, local_project)
        adapter = _make_adapter_mock()
        sent: list[str] = []

        def _rejects_the_query(sql: str, **kwargs: object) -> tuple | None:
            sent.append(sql)
            if sql == _LIVENESS_SQL or not kwargs.get("fetch"):
                return (None, MagicMock(column_names=["n"], rows=[(1,)]))
            raise RuntimeError('syntax error at or near "SELCT"')

        adapter.execute.side_effect = _rejects_the_query

        with patch(_BUILD_ADAPTER, return_value=adapter):
            pool = sa._get_source_pool(_POSTGRES_SOURCE)
            with pytest.raises(RuntimeError, match="syntax error"):
                pool.execute("SELCT 1", setup_sql=None)

        assert sent.count("SELCT 1") == 1
        adapter.connections.cleanup_all.assert_not_called()

    def test_no_auto_begin_on_execute(
        self, tmp_path: Path, local_project: Callable[..., FilesystemProject]
    ) -> None:
        sa = _make_sql_adapter(tmp_path, local_project)
        mock_adapter = _make_adapter_mock()
        with patch(_BUILD_ADAPTER, return_value=mock_adapter):
            pool = sa._get_source_pool(_POSTGRES_SOURCE)
            pool.execute("SELECT 1", setup_sql=None)

        assert mock_adapter.execute.call_args[1].get("auto_begin") is False

    def test_setup_sql_prepended_to_main_query(
        self, tmp_path: Path, local_project: Callable[..., FilesystemProject]
    ) -> None:
        sa = _make_sql_adapter(tmp_path, local_project)
        mock_adapter = _make_adapter_mock()
        with patch(_BUILD_ADAPTER, return_value=mock_adapter):
            pool = sa._get_source_pool(_POSTGRES_SOURCE)
            pool.execute("SELECT fn()", setup_sql="CREATE TEMP FUNCTION fn() AS 1")

        call_sql = mock_adapter.execute.call_args[0][0]
        assert "CREATE TEMP FUNCTION fn() AS 1" in call_sql
        assert "SELECT fn()" in call_sql
        assert call_sql.index("CREATE TEMP FUNCTION") < call_sql.index("SELECT fn()")


# ---------------------------------------------------------------------------
# Reconnect on dead session — closes the dropped connection (no leak)
# ---------------------------------------------------------------------------


class TestSourcePoolReconnect:
    def test_reconnects_once_on_execute_failure(
        self, tmp_path: Path, local_project: Callable[..., FilesystemProject]
    ) -> None:
        sa = _make_sql_adapter(tmp_path, local_project)
        adapters: list[MagicMock] = []

        def _new_adapter(*_a: Any, **_k: Any) -> MagicMock:
            adapter = _make_adapter_mock()
            adapters.append(adapter)
            return adapter

        first = _make_adapter_mock()

        def _data_query_fails(sql: str, **kwargs: object) -> tuple | None:
            # Connection setup (fetch=False) succeeds; data query raises a dead-
            # connection error that triggers the drop-and-retry-once path.
            if not kwargs.get("fetch"):
                return None
            raise RuntimeError("connection reset by peer")

        first.execute.side_effect = _data_query_fails

        def _build(*_a: Any, **_k: Any) -> MagicMock:
            if not adapters:
                adapters.append(first)
                return first
            return _new_adapter()

        with patch(_BUILD_ADAPTER, side_effect=_build):
            pool = sa._get_source_pool(_POSTGRES_SOURCE)
            result = pool.execute("SELECT 1", setup_sql=None)

        # dead adapter's connections were closed (no leaked session) before retry
        first.connections.cleanup_all.assert_called_once()
        # a fresh adapter was built for the retry
        assert len(adapters) == 2
        assert result is not None

    def test_reconnect_does_not_leak_dropped_adapter_on_close(
        self, tmp_path: Path, local_project: Callable[..., FilesystemProject]
    ) -> None:
        """A dropped (reconnected) adapter must not be double-closed by close()."""
        sa = _make_sql_adapter(tmp_path, local_project)
        adapters: list[MagicMock] = []

        def _build(*_a: Any, **_k: Any) -> MagicMock:
            adapter = _make_adapter_mock()
            if not adapters:
                # First adapter: setup calls succeed, data query fails (dead connection).
                def _data_query_fails(sql: str, **kwargs: object) -> tuple | None:
                    if not kwargs.get("fetch"):
                        return None
                    raise RuntimeError("dead")

                adapter.execute.side_effect = _data_query_fails
            adapters.append(adapter)
            return adapter

        with patch(_BUILD_ADAPTER, side_effect=_build):
            pool = sa._get_source_pool(_POSTGRES_SOURCE)
            pool.execute("SELECT 1", setup_sql=None)
            pool.close()

        # dead adapter closed exactly once (at drop); live adapter closed once (at close)
        assert adapters[0].connections.cleanup_all.call_count == 1
        assert adapters[1].connections.cleanup_all.call_count == 1


# ---------------------------------------------------------------------------
# close() closes every per-thread adapter
# ---------------------------------------------------------------------------


class TestSourcePoolClose:
    def test_close_calls_cleanup_all(
        self, tmp_path: Path, local_project: Callable[..., FilesystemProject]
    ) -> None:
        sa = _make_sql_adapter(tmp_path, local_project)
        mock_adapter = _make_adapter_mock()
        with patch(_BUILD_ADAPTER, return_value=mock_adapter):
            pool = sa._get_source_pool(_POSTGRES_SOURCE)
            pool.execute("SELECT 1", setup_sql=None)
            pool.close()

        mock_adapter.connections.cleanup_all.assert_called_once()

    def test_sql_adapter_close_closes_all_source_pools(
        self, tmp_path: Path, local_project: Callable[..., FilesystemProject]
    ) -> None:
        sa = _make_sql_adapter(tmp_path, local_project)
        mock_adapter = _make_adapter_mock()
        with patch(_BUILD_ADAPTER, return_value=mock_adapter):
            pool = sa._get_source_pool(_POSTGRES_SOURCE)
            pool.execute("SELECT 1", setup_sql=None)

        mock_adapter.connections.cleanup_all.assert_not_called()
        sa.close()
        mock_adapter.connections.cleanup_all.assert_called_once()


# ---------------------------------------------------------------------------
# BigQuery default_dataset — set on the client handle at connection open
# ---------------------------------------------------------------------------


class TestBigQueryDefaultDataset:
    def test_bigquery_connection_sets_default_dataset(
        self, tmp_path: Path, local_project: Callable[..., FilesystemProject]
    ) -> None:
        """Opening a BigQuery pool connection sets default_query_job_config.default_dataset.

        This is the connection-level analog of the DuckDB SET search_path path:
        unqualified table names in inline SQL resolve against the source dataset.
        """
        sa = _make_sql_adapter(tmp_path, local_project)
        mock_adapter = _make_adapter_mock()

        mock_handle = MagicMock()
        mock_adapter.connections.get_thread_connection.return_value.handle = mock_handle

        # Patch QueryJobConfig on the real installed module — sys.modules patching
        # doesn't work for `from google.cloud import bigquery` when the module is
        # already loaded (the namespace package attribute is separate from sys.modules).
        with (
            patch(_BUILD_ADAPTER, return_value=mock_adapter),
            patch("google.cloud.bigquery.QueryJobConfig") as mock_qjc,
        ):
            pool = sa._get_source_pool(_BIGQUERY_SOURCE)
            pool.execute("SELECT 1", setup_sql=None)

        expected_timeout_ms = get_execution_config().max_query_duration_seconds * 1000
        mock_qjc.assert_called_once_with(
            default_dataset="my-gcp-project.my_dataset",
            job_timeout_ms=expected_timeout_ms,
        )
        assert mock_handle.default_query_job_config is mock_qjc.return_value

    def test_bigquery_reconnect_resets_default_dataset(
        self, tmp_path: Path, local_project: Callable[..., FilesystemProject]
    ) -> None:
        """After a dead-connection retry, the replacement handle also gets default_dataset.

        Reconnect path: execute() raises → _drop_connection() nulls _tls.adapter →
        _ensure_connected() runs again on a fresh adapter and must re-enter the
        BigQuery block to set default_query_job_config on the NEW handle.
        """
        sa = _make_sql_adapter(tmp_path, local_project)
        adapters: list[MagicMock] = []

        def _build(*_a: Any, **_k: Any) -> MagicMock:
            adapter = _make_adapter_mock()
            handle = MagicMock()
            adapter.connections.get_thread_connection.return_value.handle = handle
            if not adapters:
                adapter.execute.side_effect = RuntimeError("connection reset")
            adapters.append(adapter)
            return adapter

        with (
            patch(_BUILD_ADAPTER, side_effect=_build),
            patch("google.cloud.bigquery.QueryJobConfig") as mock_qjc,
        ):
            pool = sa._get_source_pool(_BIGQUERY_SOURCE)
            pool.execute("SELECT 1", setup_sql=None)

        # Both the initial connection and the reconnected replacement must have
        # default_query_job_config set — one call per _ensure_connected invocation.
        assert mock_qjc.call_count == 2
        for adapter in adapters:
            handle = adapter.connections.get_thread_connection.return_value.handle
            assert handle.default_query_job_config is mock_qjc.return_value

    def test_uses_authored_project_and_dataset(self) -> None:
        from dbt_charts.core.execute.adapters.sql_adapter import (
            bigquery_default_dataset,
        )

        assert (
            bigquery_default_dataset({"project": "authored-proj", "dataset": "ds"})
            == "authored-proj.ds"
        )

    def test_no_project_means_no_default_rather_than_a_borrowed_one(self) -> None:
        """A dbt_profile target may omit the project (dbt injects the ADC project
        into the credentials). Borrowing the client's project would be wrong: that
        is dbt's *execution* project, which can differ from the data project, so
        unqualified names would resolve somewhere dbt would not look."""
        from dbt_charts.core.execute.adapters.sql_adapter import (
            bigquery_default_dataset,
        )

        assert bigquery_default_dataset({"dataset": "ds"}) is None

    def test_no_dataset_means_no_default(self) -> None:
        from dbt_charts.core.execute.adapters.sql_adapter import (
            bigquery_default_dataset,
        )

        assert bigquery_default_dataset({"project": "p"}) is None

    def test_execution_project_is_never_used_as_the_data_project(self) -> None:
        """execution_project is where queries bill, not where relations live."""
        from dbt_charts.core.execute.adapters.sql_adapter import (
            bigquery_default_dataset,
        )

        assert (
            bigquery_default_dataset(
                {"dataset": "ds", "execution_project": "billing-proj"}
            )
            is None
        )


class TestAttributionReachesTheWorker:
    """`ThreadPoolExecutor` does not propagate `ContextVar`, and the pool's worker
    threads — not the caller — are where the query is actually sent. Without the
    context hop every job would go out unattributed."""

    def test_scope_is_visible_on_the_pool_worker(
        self, tmp_path: Path, local_project: Callable[..., FilesystemProject]
    ) -> None:
        from dbt_charts.core.attribution import attribute, current_attribution

        seen: dict[str, str] = {}
        adapter = _make_adapter_mock()
        adapter.execute.side_effect = lambda *a, **k: (
            seen.update(current_attribution()),
            (None, adapter.execute.return_value[1]),
        )[1]

        sa = _make_sql_adapter(tmp_path, local_project)
        with patch(_BUILD_ADAPTER, return_value=adapter):
            pool = sa._get_source_pool(_POSTGRES_SOURCE)
            with attribute({"board": "revenue"}, {"team": "finance"}):
                pool.execute("SELECT 1", setup_sql=None)

        assert seen["dbt_charts_board"] == "revenue"
        assert seen["team"] == "finance"


# ---------------------------------------------------------------------------
# ClickHouse: the statement cap rides on the connection's settings
# ---------------------------------------------------------------------------

_CLICKHOUSE_SOURCE: dict[str, Any] = {
    "type": "clickhouse",
    "host": "h",
    "user": "u",
    "password": "p",
    "schema": "analytics",
}


class TestClickHouseQueryTimeout:
    def test_max_execution_time_is_set_on_the_connection(
        self, tmp_path: Path, local_project: Callable[..., FilesystemProject]
    ) -> None:
        """The cap is a per-request setting, not a session SET: a pooled HTTP
        session expires idle between renders and would silently drop a SET."""
        sa = _make_sql_adapter(tmp_path, local_project)
        mock_adapter = _make_adapter_mock()

        with patch(_BUILD_ADAPTER, return_value=mock_adapter) as build:
            pool = sa._get_source_pool(_CLICKHOUSE_SOURCE)
            pool.execute("SELECT 1", setup_sql=None)
            pool.close()

        built = build.call_args.args[0]
        assert built["custom_settings"] == {
            "max_execution_time": get_execution_config().max_query_duration_seconds
        }
        # No SET is sent: the only statement the worker ran is the query.
        sent = [c.args[0] for c in mock_adapter.execute.call_args_list]
        assert sent == ["SELECT 1"]

    def test_a_looser_profile_setting_is_capped_at_the_ceiling(
        self, tmp_path: Path, local_project: Callable[..., FilesystemProject]
    ) -> None:
        sa = _make_sql_adapter(tmp_path, local_project)
        source = {
            **_CLICKHOUSE_SOURCE,
            "custom_settings": {"max_execution_time": 9999, "max_threads": 2},
        }

        with patch(_BUILD_ADAPTER, return_value=_make_adapter_mock()) as build:
            pool = sa._get_source_pool(source)
            pool.execute("SELECT 1", setup_sql=None)
            pool.close()

        built = build.call_args.args[0]
        assert built["custom_settings"] == {
            "max_threads": 2,
            "max_execution_time": get_execution_config().max_query_duration_seconds,
        }
        # The source config the pool is keyed on is untouched.
        assert "custom_settings" not in _CLICKHOUSE_SOURCE
        assert source["custom_settings"]["max_execution_time"] == 9999

    def test_a_stricter_profile_setting_is_not_loosened_to_the_ceiling(
        self, tmp_path: Path, local_project: Callable[..., FilesystemProject]
    ) -> None:
        """max_query_duration_seconds is a ceiling, so a profile that caps
        itself harder keeps its own value — the shipped 120s default is not a
        value anyone chose, and writing it over an authored 5s would loosen the
        cap rather than enforce one."""
        sa = _make_sql_adapter(tmp_path, local_project)
        source = {**_CLICKHOUSE_SOURCE, "custom_settings": {"max_execution_time": 5}}

        with patch(_BUILD_ADAPTER, return_value=_make_adapter_mock()) as build:
            pool = sa._get_source_pool(source)
            pool.execute("SELECT 1", setup_sql=None)
            pool.close()

        assert build.call_args.args[0]["custom_settings"] == {"max_execution_time": 5}

    def test_the_timeout_error_reports_the_cap_that_actually_fired(
        self, tmp_path: Path, local_project: Callable[..., FilesystemProject]
    ) -> None:
        """A stricter profile narrows the cap, so the pool must report 5s — not
        the 120s ceiling, whose remediation ("raise the limit") cannot help."""
        from dbt_charts.core.execute.adapters.sql_adapter import (
            _QueryDurationExceeded,
            _SourcePool,
        )

        mock_adapter = _make_adapter_mock()
        mock_adapter.execute.side_effect = RuntimeError(
            "Code: 159. DB::Exception: Timeout exceeded (TIMEOUT_EXCEEDED)"
        )
        source = {**_CLICKHOUSE_SOURCE, "custom_settings": {"max_execution_time": 5}}

        with patch(_BUILD_ADAPTER, return_value=mock_adapter):
            pool = _SourcePool(source, max_workers=1, timeout_seconds=120)
            with pytest.raises(_QueryDurationExceeded) as excinfo:
                pool.execute("SELECT 1", setup_sql=None)
            pool.close()

        assert excinfo.value.seconds == 5

    def test_an_unusable_profile_setting_raises_rather_than_picking_a_side(
        self, tmp_path: Path, local_project: Callable[..., FilesystemProject]
    ) -> None:
        """Neither answer is safe: keeping it runs uncapped, overwriting it caps
        a query the author never asked to cap."""
        sa = _make_sql_adapter(tmp_path, local_project)
        source = {
            **_CLICKHOUSE_SOURCE,
            "custom_settings": {"max_execution_time": "soon"},
        }

        with pytest.raises(ValueError, match="max_execution_time"):
            sa._get_source_pool(source)

    def test_the_cap_follows_the_source_override(
        self, tmp_path: Path, local_project: Callable[..., FilesystemProject]
    ) -> None:
        from dbt_charts.core.execute.adapters.sql_adapter import _SourcePool

        with patch(_BUILD_ADAPTER, return_value=_make_adapter_mock()) as build:
            pool = _SourcePool(_CLICKHOUSE_SOURCE, max_workers=1, timeout_seconds=7)
            pool.execute("SELECT 1", setup_sql=None)
            pool.close()

        assert build.call_args.args[0]["custom_settings"] == {"max_execution_time": 7}

    def test_a_timeout_is_classified_and_not_retried(
        self, tmp_path: Path, local_project: Callable[..., FilesystemProject]
    ) -> None:
        """Error 159 is the warehouse canceling the statement, not the session."""
        from dbt_charts.core.execute.adapters.sql_adapter import (
            _QueryDurationExceeded,
        )

        sa = _make_sql_adapter(tmp_path, local_project)
        mock_adapter = _make_adapter_mock()
        mock_adapter.execute.side_effect = RuntimeError(
            "ClickHouse exception:  Code: 159. DB::Exception: Timeout exceeded: "
            "elapsed 30.0 seconds, maximum: 30. (TIMEOUT_EXCEEDED)"
        )

        with patch(_BUILD_ADAPTER, return_value=mock_adapter) as build:
            pool = sa._get_source_pool(_CLICKHOUSE_SOURCE)
            with pytest.raises(_QueryDurationExceeded):
                pool.execute("SELECT 1", setup_sql=None)
            pool.close()

        assert build.call_count == 1
