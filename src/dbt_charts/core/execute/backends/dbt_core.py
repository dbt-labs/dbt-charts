"""The dbt-adapters backend: pooled dbt warehouse connections behind ``Backend``.

Stage: EXECUTE
Purpose: Execute SQL queries against warehouses via dbt adapters
    (Postgres, Snowflake, BigQuery, Databricks, etc.).

SQLite and DuckDB are not warehouses of this backend: they run in-process.

Security: queries inline params after Jinja rendering — values are validated
dashboard YAML variables, not raw user input from HTTP.
"""

from __future__ import annotations

import logging
import threading
from concurrent.futures import ThreadPoolExecutor
from contextvars import copy_context
from typing import TYPE_CHECKING, Any

from dbt_charts.core.column_types import TypedRows
from dbt_charts.core.compile.config import (
    resolve_max_query_duration_seconds,
    resolve_max_workers,
)
from dbt_charts.core.diagnostics.base import DbtChartsError
from dbt_charts.core.diagnostics.codes_execute import ERR_QUERY_INTERRUPTED
from dbt_charts.core.diagnostics.execution import QueryError
from dbt_charts.core.dialects import SQLDialect, get_dialect
from dbt_charts.core.execute.backends.base import LIVENESS_SQL, source_config_hash
from dbt_charts.core.execute.backends.dbt_adapter_factory import (
    AdapterNotInstalledError,
    ConnectionSetupFailed,
    cancel_open_queries,
)
from dbt_charts.core.execute.backends.typed_fetch import fetch_typed
from dbt_charts.core.execute.query_result import QueryDurationExceeded
from dbt_charts.core.execute.sql_literals import INLINE_PLACEHOLDERS

if TYPE_CHECKING:
    from dbt_charts.core.compile.models.source import ResolvedSourceConfig
    from dbt_charts.core.execute.compose import ResolvedQuery

logger = logging.getLogger(__name__)

_DbtAdapter = Any  # type-state: explicit_any — untyped dbt adapter instance
_RawSource = dict[str, Any]  # type-state: explicit_any — raw dumped source config


def bigquery_default_dataset(
    source_config: _RawSource,
) -> str | None:
    """The `project.dataset` BigQuery should resolve unqualified table names against.

    None means "don't set one" — BigQuery then requires fully qualified names. That
    is the only honest answer when the source does not name both halves: the client's
    own `project` is dbt's *execution* project (where queries bill), which defaults to
    the data project but is independently settable, so borrowing it would silently
    resolve relations against a different project than dbt does.
    """
    project = source_config.get("project")
    dataset = source_config.get("dataset")
    if not project or not dataset:
        return None
    return f"{project}.{dataset}"


class _SourcePool:
    """Per-source-config pool of warm dbt connections on persistent worker threads.

    The pool owns a long-lived ``ThreadPoolExecutor``; every ``execute()`` runs on
    one of its worker threads.  Each worker builds its OWN dbt adapter (hence its
    own dbt ConnectionManager and connection state) on first use and keeps a named
    connection open — so the connect+SSL-handshake cost (~1.8 s on Databricks) is
    paid once per worker, not once per query.

    Two properties matter, and both come from running on this persistent pool:

    - **Concurrency.** Independent adapters mean no shared per-adapter state
      (connection manager, credentials manager, relations cache) serializing
      execution.  A single shared adapter collapsed workers=8 to workers=1
      (measured flat ~2.1 s on Databricks).
    - **Cross-render warmth.** The render pipeline builds a fresh
      ``ThreadPoolExecutor`` per render, so per-thread connections keyed on the
      render threads would be rebuilt cold every page.  Dispatching onto this
      pool's *own* persistent threads keeps connections warm across renders, so
      the first visit to a new filter reuses open warehouse sessions instead of
      reconnecting.  This is why the pool must outlive a single request — it lives
      on the ``DbtBackend`` inside an ``ExecEnv`` that serve persists in
      ``app.state``.

    Pool adapters are built with ``register_macros=False``: raw SQL execution
    needs no dbt macro context, so the macro resolver and context generator are
    never wired onto these adapters.

    A worker holds its connection across renders, so a transaction left open on it
    is open until the pool closes — pinning an MVCC snapshot and every lock the
    reads took.  ``auto_begin=False`` is only half of preventing that: it stops
    *dbt* from opening one, while psycopg2 opens its own on the first statement of a
    non-autocommit connection.  ``build_adapter`` closes the other half by connecting
    Postgres in autocommit mode.  Pure SELECTs need no transaction either way;
    ``validate_select_only`` already guarantees that is all we send.

    The same holding is why the pool owns the session's cleanliness: temp objects a
    ``setup_sql`` creates are scoped to the connection, not the query, so without a
    reset the next render of that board collides with its own leftovers and an
    unrelated board can read them.  ``_clean_session`` runs the dialect's reset
    before the next query on a connection that ran one — or drops the connection
    where the dialect has no reset statement.
    """

    def __init__(
        self,
        source_config: _RawSource,
        max_workers: int,
        timeout_seconds: int,
    ) -> None:
        self._source_config = source_config
        self._dialect = get_dialect(source_config["type"])
        # The cap in force, after a profile that caps itself harder narrows the
        # ceiling. Everything downstream reads this one number — the statement
        # sent at connect, the credentials below, and the seconds a
        # QueryDurationExceeded reports — so a timeout error cannot name a
        # limit that is not the one the warehouse enforced.
        self._timeout_seconds = self._dialect.resolve_timeout_seconds(
            source_config, timeout_seconds
        )
        # Precomputed once per pool (dialect + cap are both fixed per source):
        # the timeout SQL to run once at connection setup, or None when the
        # dialect has no server-side SQL mechanism (DuckDB returns None;
        # BigQuery's cap is a job-level setting applied in _ensure_connected,
        # ClickHouse's, Redshift's and SQL Server's are credentials applied just below).
        self._timeout_sql = self._dialect.statement_timeout_sql(self._timeout_seconds)
        # The config every worker thread hands build_adapter: `source_config`
        # itself unless the dialect's cap is credential-shaped and it put the
        # cap inside. Precomputed for the same reason _timeout_sql is — both
        # inputs are frozen per pool.
        self._connect_config = self._dialect.connect_credentials(
            source_config, self._timeout_seconds
        )
        self._tls: threading.local = threading.local()
        self._interrupted = False
        # Track every per-thread adapter so close() can release them all.
        self._adapters: list[_DbtAdapter] = []
        self._adapters_lock = threading.Lock()
        # Persistent worker threads: connections built here survive across renders
        # (render-level ThreadPoolExecutors are ephemeral and would reconnect cold).
        self._pool = ThreadPoolExecutor(
            max_workers=max_workers, thread_name_prefix="dct-srcpool"
        )

    def execute(
        self,
        inlined_sql: str,
        setup_sql: str | None,
        time_zone: str,
        limit: int | None = None,
    ) -> TypedRows:
        """Run SQL on a persistent worker thread, reconnecting once on failure.

        The caller may be any (ephemeral) render thread; the actual warehouse
        round-trip runs on this pool's persistent threads so the connection stays
        warm across renders.  Reconnect handles idle-timeout drops (OAuth expiry,
        TCP keepalive failure, Databricks SQL-warehouse spin-down) without
        surfacing a transient error.

        The caller's context is copied onto the worker: ``ThreadPoolExecutor`` does
        not propagate ``ContextVar`` values, so without this the query attribution
        established around the render is invisible where the query is actually sent
        and every job goes out unattributed.

        ``limit`` bounds the driver's own fetch (``cursor.fetchmany(limit)``) —
        the SQL text sent to the warehouse is never touched.
        """
        if self._interrupted:
            raise QueryError.from_code(ERR_QUERY_INTERRUPTED)
        try:
            return self._pool.submit(
                copy_context().run,
                self._execute_on_worker,
                inlined_sql,
                setup_sql,
                time_zone,
                limit,
            ).result()
        except Exception as exc:  # noqa: BLE001 — re-raised unless interrupted
            # The cancel surfaces as a driver error, a CancelledError for a
            # dropped queued query, or a refused submit; all mean the same.
            if self._interrupted:
                raise QueryError.from_code(ERR_QUERY_INTERRUPTED) from exc
            raise

    def _execute_on_worker(
        self,
        inlined_sql: str,
        setup_sql: str | None,
        time_zone: str,
        limit: int | None,
    ) -> TypedRows:
        adapter = self._replace_if_released(self._ensure_connected())
        dirty = self._tls.session_dirty
        try:
            # Inside the try: a connection that died while idle fails here rather
            # than on the query, and that is still a dead connection.
            if dirty:
                adapter = self._clean_session(adapter)
            return self._run(adapter, inlined_sql, setup_sql, time_zone, limit)
        except QueryDurationExceeded:
            # The warehouse cancelled the statement, not the connection —
            # retrying would just burn the same timeout window again.
            raise
        except Exception:  # noqa: BLE001 — retried below only if the session died
            if self._interrupted:
                # Postgres cancels by killing the session: a reconnect would run
                # the query again.
                raise
            adapter = self._replace_if_released(adapter)
            if self._connection_alive(adapter):
                # The warehouse received the query and rejected it. Re-sending it
                # fails identically, and the reconnect throws away a warm
                # connection to do it.
                raise
            self._drop_connection()
            adapter = self._ensure_connected()
            return self._run(adapter, inlined_sql, setup_sql, time_zone, limit)

    def _replace_if_released(
        self,
        adapter: _DbtAdapter,
    ) -> _DbtAdapter:
        """The adapter to run on: a fresh one if the driver released its connection.

        dbt-sqlserver releases the connection on any error, so this must run
        before :meth:`_connection_alive` probes it. The rebuild goes through
        ``_ensure_connected`` and gets its per-connection setup.
        """
        from dbt.adapters.contracts.connection import ConnectionState
        from dbt.adapters.exceptions import InvalidConnectionError

        try:
            state = adapter.connections.get_thread_connection().state
        except InvalidConnectionError:
            # dbt drops the thread's connection when closing it failed.
            state = None
        if state == ConnectionState.OPEN:
            return adapter
        self._drop_connection()
        return self._ensure_connected()

    def _connection_alive(
        self,
        adapter: _DbtAdapter,
    ) -> bool:
        """Whether the warehouse still answers on this connection.

        Asked of the connection rather than inferred from the error text: every
        driver spells a dropped session differently, and a wrong guess either
        retries a query the warehouse already rejected or surfaces an idle-timeout
        drop (OAuth expiry, Databricks warehouse spin-down) as a query error.
        """
        try:
            adapter.execute(LIVENESS_SQL, auto_begin=False, fetch=True)
        except Exception:  # noqa: BLE001 — no answer is the answer
            return False
        return True

    def _clean_session(
        self,
        adapter: _DbtAdapter,
    ) -> _DbtAdapter:
        """Clear session-scoped objects a previous ``setup_sql`` left on the connection.

        Returns the adapter to run on — a fresh one when the dialect has no reset
        statement, since the connection is then the only thing holding that state.
        """
        reset = self._dialect.session_reset_statements()
        if not reset:
            # Worth seeing: on a dialect with no reset statement this is a full
            # reconnect per query for as long as the board uses setup_sql, which on
            # a slow-connecting warehouse is the pool's whole value.
            logger.debug(
                "Dropping %s connection to clear session state (dialect has no "
                "reset statement)",
                self._dialect.name,
            )
            self._drop_connection()
            return self._ensure_connected()
        for statement in reset:
            adapter.execute(statement, auto_begin=False, fetch=False)
        return adapter

    def _run(
        self,
        adapter: _DbtAdapter,
        inlined_sql: str,
        setup_sql: str | None,
        time_zone: str,
        limit: int | None,
    ) -> TypedRows:
        self._apply_time_zone(adapter, time_zone)
        # Single owner of the flag, and set before the send: a setup_sql that fails
        # partway still leaves whatever it created on the connection.
        self._tls.session_dirty = bool(setup_sql)
        if setup_sql:
            # It may change the session zone.
            self._tls.session_zone = None
        sql = (
            setup_sql.rstrip().rstrip(";") + ";\n" + inlined_sql
            if setup_sql
            else inlined_sql
        )
        # dbt-spark's Hive/ODBC cursors implement fetchall but not fetchmany.
        # Omit the limit for those dialects and fall back to the post-fetch
        # slice in apply_row_limit_truncation.
        driver_limit = limit if self._dialect.cursor_supports_driver_limit else None
        try:
            return fetch_typed(
                adapter, self._dialect.name, sql, driver_limit, time_zone
            )
        except Exception as exc:  # noqa: BLE001 — classify statement timeout before retry
            if self._dialect.is_statement_timeout_error(exc):
                raise QueryDurationExceeded(self._timeout_seconds) from exc
            raise

    def _apply_time_zone(self, adapter: _DbtAdapter, time_zone: str) -> None:
        """Run this connection's session in *time_zone*, sending only a change.

        The connection is pooled across boards, so the zone it last ran in is
        tracked per worker thread, with the client handle it was set on: dbt-bigquery
        swaps the handle on a retry. ClickHouse takes the zone per
        query instead (see ``fetch_typed``).
        """
        handle = adapter.connections.get_thread_connection().handle
        if self._tls.session_zone == (handle, time_zone):
            return
        if self._source_config.get("type") == "bigquery":
            from google.cloud.bigquery import ConnectionProperty

            if handle.default_query_job_config is None:
                handle.default_query_job_config = self._bigquery_job_config()
            handle.default_query_job_config.connection_properties = [
                ConnectionProperty("time_zone", time_zone)
            ]
        elif statement := self._dialect.session_time_zone_sql(time_zone):
            adapter.execute(statement, auto_begin=False, fetch=False)
        self._tls.session_zone = (handle, time_zone)

    def _bigquery_job_config(
        self,
    ) -> Any:  # type-state: explicit_any — optional google import
        from google.cloud import bigquery

        return bigquery.QueryJobConfig(
            default_dataset=bigquery_default_dataset(self._source_config),
            # BigQuery has no session-level statement_timeout SQL — the cap
            # is a job-level setting instead of the dialect SQL mechanism
            # every other network warehouse uses.
            job_timeout_ms=self._timeout_seconds * 1000,
        )

    def _ensure_connected(
        self,
    ) -> _DbtAdapter:
        """Return this thread's adapter, building + connecting it on first use.

        Failures here raise ConnectionSetupFailed (bad credentials, unreachable
        host): they happen before any SQL reaches the warehouse, so the caller
        routes them to connection_failure (typed ERR-WAREHOUSE-CONNECTION)
        rather than classify_warehouse_error — distinct from _run() raising,
        which means the warehouse received and rejected a query. Labeling a
        credentials failure a "warehouse rejected the query" outcome asserts a
        cause that is false. The lazy connection handle is forced open for
        every dialect (not just bigquery/postgres/snowflake) so a connect
        failure on any dialect is caught here rather than leaking into _run().

        A missing adapter package is the exception: AdapterNotInstalledError
        propagates unwrapped and the caller reports its install command.
        """
        adapter = getattr(  # type-state: silent_fallback — unset until this thread first connects
            self._tls, "adapter", None
        )
        if adapter is not None:
            return adapter

        from dbt_charts.core.execute.backends.dbt_adapter_factory import build_adapter

        try:
            # This warehouse pool only handles network warehouses; DuckDB and
            # SQLite run in-process. Every query entering this backend is
            # SELECT-only.
            adapter = build_adapter(
                self._connect_config,
                read_only=False,
                register_macros=False,
            )
            ctx = adapter.connection_named(f"dbt_charts_pool_{threading.get_ident()}")
            ctx.__enter__()

            # Force the LazyHandle open here, for every dialect: a connect
            # failure (bad credentials, unreachable host) must raise inside
            # this try so the caller wraps it as ConnectionSetupFailed
            # instead of surfacing later from _run() as a false "warehouse
            # rejected the query" outcome. Postgres/Snowflake already force it
            # via the statement_timeout_sql send below; BigQuery needs the
            # handle itself to attach default_query_job_config. Without this,
            # a dialect with neither (databricks, spark, athena,
            # clickhouse, sqlserver — redshift has its own
            # statement_timeout_sql override) never touches the handle here,
            # so its connect failure leaks past this classification entirely.
            handle = adapter.connections.get_thread_connection().handle

            # Must follow ctx.__enter__() so get_thread_connection() returns the live
            # handle — same structural reason the DuckDB SET search_path follows
            # conn.execute.
            if self._source_config.get("type") == "bigquery":
                handle.default_query_job_config = self._bigquery_job_config()

            # For dialects that have a session-level timeout SQL (Postgres, Snowflake,
            # Redshift), send it once at connection setup. Session-scoped SET commands
            # persist for the life of the connection, so one send per connection is
            # sufficient and correct — no per-query prepend needed.
            if self._timeout_sql is not None:
                adapter.execute(self._timeout_sql, auto_begin=False, fetch=False)
        except AdapterNotInstalledError:
            # A missing package is a setup error with its own install command,
            # not a connection failure to bucket as "check your credentials".
            raise
        except Exception as e:  # noqa: BLE001 — reclassified by caller, not swallowed
            raise ConnectionSetupFailed(e) from e

        self._tls.adapter = adapter
        self._tls.ctx = ctx
        self._tls.session_dirty = False
        self._tls.session_zone = None
        with self._adapters_lock:
            self._adapters.append(adapter)
        return adapter

    def _drop_connection(self) -> None:
        """Close this thread's dead adapter so _ensure_connected rebuilds it.

        Closing via cleanup_all() releases the warehouse session before the
        adapter is discarded — without this the dropped session leaks (the old
        clear_thread_connection() path removed the entry from cleanup_all()'s
        reach without ever closing it).
        """
        adapter = getattr(  # type-state: silent_fallback — unset until this thread first connects
            self._tls, "adapter", None
        )
        self._tls.adapter = None
        self._tls.ctx = None
        # The session state went with the connection.
        self._tls.session_dirty = False
        self._tls.session_zone = None
        if adapter is None:
            return
        with self._adapters_lock:
            if adapter in self._adapters:
                self._adapters.remove(adapter)
        _cleanup_adapter(adapter)

    def interrupt(self) -> None:
        """Cancel running queries, drop queued ones, and refuse new ones."""
        self._interrupted = True
        self._pool.shutdown(wait=False, cancel_futures=True)
        with self._adapters_lock:
            adapters = list(self._adapters)
        for adapter in adapters:
            cancel_open_queries(adapter)

    def close(self) -> None:
        """Stop the worker threads, then close every adapter they built."""
        self._pool.shutdown(wait=True)
        with self._adapters_lock:
            adapters, self._adapters = self._adapters, []
        for adapter in adapters:
            _cleanup_adapter(adapter)


def _cleanup_adapter(
    adapter: _DbtAdapter,
) -> None:
    """Best-effort close of a dbt adapter's open connections."""
    try:
        adapter.connections.cleanup_all()
    except Exception:  # noqa: BLE001 — best-effort; log and continue
        logger.debug("Error in source pool cleanup", exc_info=True)


class DbtBackend:
    """Runs warehouse sources on dbt adapters.

    One pool of warm connections per distinct source, built on first use. The
    dbt adapter's ``execute()`` takes only plain SQL, so the bindings are
    inlined as literals, escaped for the warehouse that parses them; the
    mutating-SQL guards already ran in ``compose``.

    ``setup_sql`` runs in the same call as the main query: BigQuery's
    ``CREATE TEMP FUNCTION`` is session-scoped, and two calls would be two
    connections.
    """

    param_style: SQLDialect = INLINE_PLACEHOLDERS

    def __init__(self, max_workers: int | None) -> None:
        self._max_workers = max_workers
        self._source_pools: dict[str, _SourcePool] = {}
        self._source_pools_lock = threading.Lock()
        self._interrupted = False

    def execute(self, source: ResolvedSourceConfig, query: ResolvedQuery) -> TypedRows:
        # Lowercased: warehouses disagree on the case of unquoted names.
        typed = self._get_source_pool(source.model_dump(by_alias=True)).execute(
            query.inline(source.type),
            ";\n".join(s.rstrip().rstrip(";") for s in query.setup_sql) or None,
            query.time_zone,
            limit=query.limit.fetch_limit,
        )
        return TypedRows(
            [col.lower() for col in typed.columns], typed.rows, typed.types
        )

    def _get_source_pool(
        self,
        source_config: _RawSource,
    ) -> _SourcePool:
        """Return the per-source pool, creating it once per distinct source.

        The pool builds its dbt adapters lazily, one per persistent worker thread
        on first execute() — nothing is built here. Pool width follows the same
        resolution as the render-time pool (explicit max_workers → DCT_MAX_WORKERS
        → execution-config default) so the two never disagree.
        """
        key = source_config_hash(source_config)
        with self._source_pools_lock:
            if self._interrupted:
                raise QueryError.from_code(ERR_QUERY_INTERRUPTED)
            if key not in self._source_pools:
                try:
                    self._source_pools[key] = _SourcePool(
                        source_config,
                        resolve_max_workers(self._max_workers),
                        resolve_max_query_duration_seconds(source_config),
                    )
                except ValueError as exc:
                    # Client-side refusal (bad cap, unsupported backend): the
                    # warehouse was never contacted, so it is not a rejection.
                    raise DbtChartsError(str(exc)) from exc
            return self._source_pools[key]

    def interrupt(self) -> None:
        with self._source_pools_lock:
            self._interrupted = True
            pools = list(self._source_pools.values())
        for pool in pools:
            pool.interrupt()

    def close(self) -> None:
        with self._source_pools_lock:
            pools = list(self._source_pools.values())
            self._source_pools.clear()
        for pool in pools:
            pool.close()
