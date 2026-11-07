"""The Arrow-driver backend: pooled driver sessions behind ``Backend``.

Stage: EXECUTE
"""

from __future__ import annotations

import logging
import threading
from concurrent.futures import ThreadPoolExecutor
from contextvars import copy_context
from typing import TYPE_CHECKING, Any

from dbt_charts.core.compile.config import (
    resolve_max_query_duration_seconds,
    resolve_max_workers,
)
from dbt_charts.core.diagnostics.base import DbtChartsError
from dbt_charts.core.diagnostics.codes_execute import ERR_QUERY_INTERRUPTED
from dbt_charts.core.diagnostics.execution import QueryError
from dbt_charts.core.dialects import SQLDialect, get_dialect
from dbt_charts.core.execute.backends.base import LIVENESS_SQL, source_config_hash
from dbt_charts.core.execute.backends.dbt_adapter_factory import ConnectionSetupFailed
from dbt_charts.core.execute.drivers.base import DriverConnection, open_driver
from dbt_charts.core.execute.query_result import (
    QueryDurationExceeded,
    RowFetchLimit,
    resolve_effective_row_limit,
)
from dbt_charts.core.execute.sql_literals import INLINE_PLACEHOLDERS

if TYPE_CHECKING:
    import nanoarrow as na

    from dbt_charts.core.compile.models.source import ResolvedSourceConfig
    from dbt_charts.core.execute.compose import ResolvedQuery

logger = logging.getLogger(__name__)


class DriverBackend:
    """Runs lake compute and Trino sources on their Arrow drivers.

    One pool of warm sessions per distinct source, built on first use.
    """

    param_style: SQLDialect = INLINE_PLACEHOLDERS

    def __init__(self, max_workers: int | None) -> None:
        self._max_workers = max_workers
        self._pools: dict[str, _DriverPool] = {}
        self._lock = threading.Lock()
        self._interrupted = False

    def execute(self, source: ResolvedSourceConfig, query: ResolvedQuery) -> na.Array:
        return self._pool_for(source).execute(
            query.inline(source.type), query.setup_sql, query.time_zone, query.limit
        )

    def _pool_for(self, source: ResolvedSourceConfig) -> _DriverPool:
        raw_config = source.model_dump(by_alias=True)
        key = source_config_hash(raw_config)
        with self._lock:
            if self._interrupted:
                raise QueryError.from_code(ERR_QUERY_INTERRUPTED)
            if key not in self._pools:
                self._pools[key] = _DriverPool(
                    raw_config,
                    source,
                    resolve_max_workers(self._max_workers),
                    resolve_max_query_duration_seconds(raw_config),
                )
            return self._pools[key]

    def interrupt(self) -> None:
        with self._lock:
            self._interrupted = True
            pools = list(self._pools.values())
        for pool in pools:
            pool.interrupt()

    def close(self) -> None:
        with self._lock:
            pools = list(self._pools.values())
            self._pools.clear()
        for pool in pools:
            pool.close()


class _DriverPool:
    """Per-source pool of warm driver connections on persistent worker threads.

    Each worker opens its OWN connection on first use and keeps it across
    renders, so the connect (and the auth behind it) is paid once per worker.

    The per-query cap is the driver's own option, sent at connect through
    ``open_driver``'s ``timeout_seconds`` argument, so nothing here sends a
    timeout statement. A connection that ran a ``setup_sql`` is reset before the
    next query, or dropped where the dialect has no reset statement.
    """

    def __init__(
        self,
        source_config: dict[str, Any],  # type-state: explicit_any — raw dumped source
        driver_config: ResolvedSourceConfig,
        max_workers: int,
        timeout_seconds: int,
    ) -> None:
        self._driver_config = driver_config
        self._dialect = get_dialect(source_config["type"])
        self._timeout_seconds = self._dialect.resolve_timeout_seconds(
            source_config, timeout_seconds
        )
        self._tls: threading.local = threading.local()
        self._connections: list[DriverConnection] = []
        self._connections_lock = threading.Lock()
        self._interrupted = False
        self._pool = ThreadPoolExecutor(
            max_workers=max_workers, thread_name_prefix="dct-driverpool"
        )

    def execute(
        self,
        sql: str,
        setup_sql: tuple[str, ...],
        time_zone: str,
        limit: RowFetchLimit,
    ) -> na.Array:
        """Run SQL on a persistent worker thread, reconnecting once if the session died.

        The caller's context is copied onto the worker: ``ThreadPoolExecutor``
        does not propagate ``ContextVar`` values.
        """
        if self._interrupted:
            raise QueryError.from_code(ERR_QUERY_INTERRUPTED)
        try:
            return self._pool.submit(
                copy_context().run,
                self._execute_on_worker,
                sql,
                setup_sql,
                time_zone,
                limit,
            ).result()
        except Exception as exc:  # noqa: BLE001 — re-raised unless interrupted
            # A cancelled statement, a dropped queued query and a refused submit
            # all mean the same thing.
            if self._interrupted:
                raise QueryError.from_code(ERR_QUERY_INTERRUPTED) from exc
            raise

    def _execute_on_worker(
        self,
        sql: str,
        setup_sql: tuple[str, ...],
        time_zone: str,
        limit: RowFetchLimit,
    ) -> na.Array:
        connection = self._ensure_connected(time_zone)
        try:
            if self._tls.session_dirty:
                connection = self._clean_session(connection, time_zone)
            return self._run(connection, sql, setup_sql, time_zone, limit)
        except QueryDurationExceeded:
            raise
        except Exception:  # noqa: BLE001 — retried below only if the session died
            if self._interrupted:
                raise
            if self._connection_alive(connection):
                # The warehouse received the query and rejected it. Re-sending it
                # fails identically, and the reconnect throws away a warm session.
                raise
            self._drop_connection()
            return self._run(
                self._ensure_connected(time_zone), sql, setup_sql, time_zone, limit
            )

    def _run(
        self,
        connection: DriverConnection,
        sql: str,
        setup_sql: tuple[str, ...],
        time_zone: str,
        limit: RowFetchLimit,
    ) -> na.Array:
        self._apply_time_zone(connection, time_zone)
        # Set before the send: a setup_sql that fails partway still leaves
        # whatever it created on the connection.
        self._tls.session_dirty = bool(setup_sql)
        if setup_sql and not self._dialect.time_zone_per_request:
            # It may change the session zone.
            self._tls.session_zone = None
        try:
            for statement in setup_sql:
                connection.session_sql(statement)
            return connection.execute(sql, limit)
        except Exception as exc:  # noqa: BLE001 — classify statement timeout before retry
            if self._dialect.is_statement_timeout_error(exc):
                raise QueryDurationExceeded(self._timeout_seconds) from exc
            raise

    def _apply_time_zone(self, connection: DriverConnection, time_zone: str) -> None:
        """Run this connection's session in *time_zone*, sending only a change.

        A dialect that takes the zone per request fixed it when the connection
        opened (``_ensure_connected``).
        """
        if self._dialect.time_zone_per_request or self._tls.session_zone == time_zone:
            return
        if statement := self._dialect.session_time_zone_sql(time_zone):
            connection.session_sql(statement)
        self._tls.session_zone = time_zone

    def _ensure_connected(self, time_zone: str) -> DriverConnection:
        """Return this thread's connection, opening it on first use.

        Where the zone is fixed at connect, a connection opened in another zone
        is closed and replaced.

        A failure here raises ConnectionSetupFailed: it happened before any SQL
        reached the warehouse, so the caller reports a connection failure rather
        than a rejected query. A missing extra propagates as itself and carries
        its install command.
        """
        connection: DriverConnection | None = getattr(
            self._tls,
            "connection",
            None,  # type-state: silent_fallback — unset until this thread first connects
        )
        if connection is not None:
            if not (
                self._dialect.time_zone_per_request
                and self._tls.session_zone != time_zone
            ):
                return connection
            self._drop_connection()
        try:
            connection = open_driver(
                self._driver_config, self._timeout_seconds, time_zone
            )
        except DbtChartsError:
            # Coded verdicts (a missing extra, a driver download that failed its
            # checksum) are not connection failures and keep their own code.
            raise
        except Exception as e:  # noqa: BLE001 — reclassified by caller, not swallowed
            raise ConnectionSetupFailed(e) from e
        self._tls.connection = connection
        self._tls.session_dirty = False
        self._tls.session_zone = (
            time_zone if self._dialect.time_zone_per_request else None
        )
        with self._connections_lock:
            self._connections.append(connection)
        return connection

    def _connection_alive(self, connection: DriverConnection) -> bool:
        """Whether the warehouse still answers on this connection.

        Asked of the connection rather than inferred from the error text.
        """
        try:
            connection.execute(LIVENESS_SQL, resolve_effective_row_limit(1))
        except Exception:  # noqa: BLE001 — no answer is the answer
            return False
        return True

    def _clean_session(
        self, connection: DriverConnection, time_zone: str
    ) -> DriverConnection:
        """Clear session objects a previous ``setup_sql`` left on the connection."""
        reset = self._dialect.session_reset_statements()
        if not reset:
            self._drop_connection()
            return self._ensure_connected(time_zone)
        for statement in reset:
            connection.session_sql(statement)
        return connection

    def _drop_connection(self) -> None:
        connection: DriverConnection | None = getattr(
            self._tls,
            "connection",
            None,  # type-state: silent_fallback — unset until this thread first connects
        )
        self._tls.connection = None
        self._tls.session_dirty = False
        self._tls.session_zone = None
        if connection is None:
            return
        with self._connections_lock:
            if connection in self._connections:
                self._connections.remove(connection)
        _close_connection(connection)

    def interrupt(self) -> None:
        """Cancel running statements, drop queued ones, and refuse new ones."""
        self._interrupted = True
        self._pool.shutdown(wait=False, cancel_futures=True)
        with self._connections_lock:
            connections = list(self._connections)
        for connection in connections:
            connection.cancel()

    def close(self) -> None:
        """Stop the worker threads, then close every connection they opened."""
        self._pool.shutdown(wait=True)
        with self._connections_lock:
            connections, self._connections = self._connections, []
        for connection in connections:
            _close_connection(connection)


def _close_connection(connection: DriverConnection) -> None:
    """Best-effort close of a driver connection."""
    try:
        connection.close()
    except Exception:  # noqa: BLE001 — best-effort; log and continue
        logger.debug("Error closing driver connection", exc_info=True)
