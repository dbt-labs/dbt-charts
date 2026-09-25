"""SQL query adapter for executing raw SQL queries via dbt adapters.

Stage: EXECUTE
Purpose: Execute SQL queries against warehouses via dbt adapters
    (Postgres, Snowflake, BigQuery, Databricks, etc.).

SQLite execution is handled by SqliteAdapter (see sqlite_adapter.py). DuckDB
execution is handled exclusively by DuckDBAdapter (see duckdb_adapter.py);
this adapter never claims a duckdb source.

Security: queries inline params after Jinja rendering — values are validated
dashboard YAML variables, not raw user input from HTTP.
"""

from __future__ import annotations

import hashlib
import json
import logging
import threading
from concurrent.futures import ThreadPoolExecutor
from contextvars import copy_context
from pathlib import Path  # noqa: TID251 — holds the dbt project dir
from typing import TYPE_CHECKING, Any

from pydantic import BaseModel, ConfigDict

if TYPE_CHECKING:
    from dbt_charts.core.compile.models.source import ResolvedSourceConfig

from dbt_charts.core.compile.models.board.normalized import VariableValues
from dbt_charts.core.compile.models.query.normalized import (
    AnyQuery,
    SqlQuery,
    is_sql_query,
)
from dbt_charts.core.compile.sql_guard import validate_select_only, validate_setup_sql
from dbt_charts.core.compile.template.parameterized import render_parameterized
from dbt_charts.core.diagnostics.base import DbtChartsError
from dbt_charts.core.diagnostics.codes_execute import ERR_QUERY_DURATION_EXCEEDED
from dbt_charts.core.diagnostics.execution import QueryError
from dbt_charts.core.dialects import SQLDialect, get_dialect
from dbt_charts.core.execute.adapters.base import (
    BaseAdapter,
    QueryParams,
    QueryResult,
    ResolvedRelation,
    RowFetchLimit,
    apply_row_limit_truncation,
    classify_warehouse_error,
    connection_failure,
    handle_adapter_error,
    plain_error,
    resolve_effective_row_limit,
    resolve_setup_sql,
)
from dbt_charts.core.execute.adapters.dbt_adapter_factory import ConnectionSetupFailed
from dbt_charts.core.execute.adapters.dbt_utils import DbtRefResolver
from dbt_charts.core.execute.sql_literals import (
    INLINE_PLACEHOLDERS,
    inline_params_for_dialect,
)
from dbt_charts.core.project import Project

logger = logging.getLogger(__name__)


def bigquery_default_dataset(source_config: dict[str, Any]) -> str | None:
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


def _source_config_hash(source_config: dict[str, Any]) -> str:
    """Stable hash of source_config for adapter cache keys.

    Uses sorted JSON serialization so key order doesn't affect the hash.

    ``attribution`` is excluded: it is cost metadata that rides along to the
    warehouse, not connection identity. Hashing it would give every distinct set of
    labels its own pool, so a per-dashboard label would cost a fresh connection
    handshake per dashboard.
    """
    identity = {k: v for k, v in source_config.items() if k != "attribution"}
    serialized = json.dumps(identity, sort_keys=True, default=str)
    return hashlib.sha256(serialized.encode()).hexdigest()[:16]


# Cheapest statement every warehouse we build answers, used to ask a connection
# whether it is still there.
_LIVENESS_SQL = "SELECT 1"


class PreparedSql(BaseModel):
    """A compiled query rendered down to the exact string that goes on the wire."""

    model_config = ConfigDict(frozen=True, arbitrary_types_allowed=True)

    sql: str
    setup_sql: str | None
    dialect_name: str
    resolved_relations: list[ResolvedRelation] = []
    # Passed to the dbt-adapter's execute(..., limit=...) kwarg so the driver
    # bounds the fetch itself (cursor.fetchmany) — the SQL text is untouched.
    row_fetch_limit: RowFetchLimit


class _QueryDurationExceeded(Exception):
    """A query was aborted by the source's server-side statement timeout.

    Raised instead of a generic driver exception so the caller can build a
    clear error without retrying — the connection is healthy (the warehouse
    cancelled the statement, not the session), so a retry would just burn
    the same timeout window twice.
    """

    def __init__(self, seconds: int) -> None:
        self.seconds = seconds
        super().__init__(f"query exceeded max_query_duration_seconds={seconds}s")


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
      on the ``SqlAdapter`` inside an ``AdapterRegistry`` that serve persists in
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
        self, source_config: dict[str, Any], max_workers: int, timeout_seconds: int
    ) -> None:
        self._source_config = source_config
        self._dialect = get_dialect(source_config["type"])
        # The cap in force, after a profile that caps itself harder narrows the
        # ceiling. Everything downstream reads this one number — the statement
        # sent at connect, the credentials below, and the seconds a
        # _QueryDurationExceeded reports — so a timeout error cannot name a
        # limit that is not the one the warehouse enforced.
        self._timeout_seconds = self._dialect.resolve_timeout_seconds(
            source_config, timeout_seconds
        )
        # Precomputed once per pool (dialect + cap are both fixed per source):
        # the timeout SQL to run once at connection setup, or None when the
        # dialect has no server-side SQL mechanism (DuckDB returns None;
        # BigQuery's cap is a job-level setting applied in _ensure_connected,
        # ClickHouse's a credential applied just below).
        self._timeout_sql = self._dialect.statement_timeout_sql(self._timeout_seconds)
        # The config every worker thread hands build_adapter: `source_config`
        # itself unless the dialect's cap is credential-shaped and it put the
        # cap inside. Precomputed for the same reason _timeout_sql is — both
        # inputs are frozen per pool.
        self._connect_config = self._dialect.connect_credentials(
            source_config, self._timeout_seconds
        )
        self._tls: threading.local = threading.local()
        # Track every per-thread adapter so close() can release them all.
        self._adapters: list[Any] = []
        self._adapters_lock = threading.Lock()
        # Persistent worker threads: connections built here survive across renders
        # (render-level ThreadPoolExecutors are ephemeral and would reconnect cold).
        self._pool = ThreadPoolExecutor(
            max_workers=max_workers, thread_name_prefix="dct-srcpool"
        )

    def execute(
        self, inlined_sql: str, setup_sql: str | None, limit: int | None = None
    ) -> Any:  # type-state: explicit_any — dbt-adapters' agate.Table is untyped here
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

        ``limit`` is forwarded to dbt-adapters' own ``execute(..., limit=...)``
        kwarg, which bounds the fetch via ``cursor.fetchmany(limit)`` — the SQL
        text sent to the warehouse is never touched.
        """
        return self._pool.submit(
            copy_context().run,
            self._execute_on_worker,
            inlined_sql,
            setup_sql,
            limit,
        ).result()

    def _execute_on_worker(
        self, inlined_sql: str, setup_sql: str | None, limit: int | None
    ) -> Any:  # type-state: explicit_any — dbt-adapters' agate.Table is untyped here
        adapter = self._ensure_connected()
        dirty = self._tls.session_dirty
        try:
            # Inside the try: a connection that died while idle fails here rather
            # than on the query, and that is still a dead connection.
            if dirty:
                adapter = self._clean_session(adapter)
            return self._run(adapter, inlined_sql, setup_sql, limit)
        except _QueryDurationExceeded:
            # The warehouse cancelled the statement, not the connection —
            # retrying would just burn the same timeout window again.
            raise
        except Exception:  # noqa: BLE001 — retried below only if the session died
            if self._connection_alive(adapter):
                # The warehouse received the query and rejected it. Re-sending it
                # fails identically, and the reconnect throws away a warm
                # connection to do it.
                raise
            self._drop_connection()
            adapter = self._ensure_connected()
            return self._run(adapter, inlined_sql, setup_sql, limit)

    def _connection_alive(self, adapter: Any) -> bool:
        """Whether the warehouse still answers on this connection.

        Asked of the connection rather than inferred from the error text: every
        driver spells a dropped session differently, and a wrong guess either
        retries a query the warehouse already rejected or surfaces an idle-timeout
        drop (OAuth expiry, Databricks warehouse spin-down) as a query error.
        """
        try:
            adapter.execute(_LIVENESS_SQL, auto_begin=False, fetch=True)
        except Exception:  # noqa: BLE001 — no answer is the answer
            return False
        return True

    def _clean_session(self, adapter: Any) -> Any:
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
        adapter: Any,  # type-state: explicit_any — dbt adapter instance is untyped here
        inlined_sql: str,
        setup_sql: str | None,
        limit: int | None,
    ) -> Any:  # type-state: explicit_any — dbt-adapters' agate.Table is untyped here
        # Single owner of the flag, and set before the send: a setup_sql that fails
        # partway still leaves whatever it created on the connection.
        self._tls.session_dirty = bool(setup_sql)
        sql = (
            setup_sql.rstrip().rstrip(";") + ";\n" + inlined_sql
            if setup_sql
            else inlined_sql
        )
        # Some dbt-adapters cursors (dbt-spark's Hive/ODBC wrappers) implement
        # fetchall but not fetchmany — passing limit= there raises AttributeError
        # inside get_result_from_cursor. Omit it for those dialects and fall back
        # to the post-fetch slice in apply_row_limit_truncation.
        driver_limit = limit if self._dialect.cursor_supports_driver_limit else None
        try:
            _, table = adapter.execute(
                sql, auto_begin=False, fetch=True, limit=driver_limit
            )
        except Exception as exc:  # noqa: BLE001 — classify statement timeout before retry
            if self._dialect.is_statement_timeout_error(exc):
                raise _QueryDurationExceeded(self._timeout_seconds) from exc
            raise
        return table

    def _ensure_connected(self) -> Any:
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
        """
        adapter = getattr(self._tls, "adapter", None)
        if adapter is not None:
            return adapter

        from dbt_charts.core.execute.adapters.dbt_adapter_factory import build_adapter

        try:
            # This warehouse pool only handles network warehouses; DuckDB and
            # SQLite are owned by their own adapters. Every query entering this
            # adapter is SELECT-only.
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
            # a dialect with neither (databricks, spark, mysql, athena,
            # clickhouse, sqlserver — redshift has its own
            # statement_timeout_sql override) never touches the handle here,
            # so its connect failure leaks past this classification entirely.
            handle = adapter.connections.get_thread_connection().handle

            # Must follow ctx.__enter__() so get_thread_connection() returns the live
            # handle — same structural reason the DuckDB SET search_path follows
            # conn.execute.
            if self._source_config.get("type") == "bigquery":
                from google.cloud import bigquery

                handle.default_query_job_config = bigquery.QueryJobConfig(
                    default_dataset=bigquery_default_dataset(self._source_config),
                    # BigQuery has no session-level statement_timeout SQL — the cap
                    # is a job-level setting instead of the dialect SQL mechanism
                    # every other network warehouse uses.
                    job_timeout_ms=self._timeout_seconds * 1000,
                )

            # For dialects that have a session-level timeout SQL (Postgres, Snowflake,
            # Redshift), send it once at connection setup. Session-scoped SET commands
            # persist for the life of the connection, so one send per connection is
            # sufficient and correct — no per-query prepend needed.
            if self._timeout_sql is not None:
                adapter.execute(self._timeout_sql, auto_begin=False, fetch=False)
        except Exception as e:  # noqa: BLE001 — reclassified by caller, not swallowed
            raise ConnectionSetupFailed(e) from e

        self._tls.adapter = adapter
        self._tls.ctx = ctx
        self._tls.session_dirty = False
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
        adapter = getattr(self._tls, "adapter", None)
        self._tls.adapter = None
        self._tls.ctx = None
        # The session state went with the connection.
        self._tls.session_dirty = False
        if adapter is None:
            return
        with self._adapters_lock:
            if adapter in self._adapters:
                self._adapters.remove(adapter)
        _cleanup_adapter(adapter)

    def close(self) -> None:
        """Stop the worker threads, then close every adapter they built."""
        self._pool.shutdown(wait=True)
        with self._adapters_lock:
            adapters, self._adapters = self._adapters, []
        for adapter in adapters:
            _cleanup_adapter(adapter)


def _cleanup_adapter(adapter: Any) -> None:
    """Best-effort close of a dbt adapter's open connections."""
    try:
        adapter.connections.cleanup_all()
    except Exception:  # noqa: BLE001 — best-effort; log and continue
        logger.debug("Error in source pool cleanup", exc_info=True)


class SqlAdapter(BaseAdapter):
    """Adapter for executing raw SQL queries via dbt adapters.

    Handles warehouses (Postgres, Snowflake, BigQuery, Databricks, etc.).
    SQLite queries are handled by SqliteAdapter; DuckDB queries are handled
    exclusively by DuckDBAdapter — this adapter never claims either.

    Supported query types: sql

    Example:
        >>> from dbt_charts.cli.filesystem_project import FilesystemProject
        >>> adapter = SqlAdapter(project=FilesystemProject(Path("/path/to/project")), dbt_project_path=None)
        >>> query = SqlQuery(sql="SELECT * FROM users WHERE id = {{ user_id }}", source="my_postgres")
        >>> result = adapter.execute(query, {"user_id": 1})
    """

    def __init__(
        self,
        *,
        project: Project,
        dbt_project_path: str | None,
        profile_type: str,
        max_workers: int | None = None,
    ):
        """Initialize SQL adapter.

        Args:
            project: The dbt charts project (for resolving relative file paths).
            dbt_project_path: Path to dbt project, or None if no dbt project.
            profile_type: Database type for dialect selection (e.g., 'postgres').
            max_workers: Width of the per-source connection pool.
        """
        self.dbt_project_path = Path(dbt_project_path) if dbt_project_path else None
        self.project = project
        self.profile_type = profile_type
        self._source_pools: dict[str, _SourcePool] = {}
        self._source_pools_lock = threading.Lock()
        self._max_workers = max_workers
        self._dbt_refs = DbtRefResolver(project)

    @property
    def supported_types(self) -> set[str]:
        """Return supported query types."""
        return {"sql"}

    def _can_execute(
        self, query: AnyQuery, source_config: ResolvedSourceConfig | None
    ) -> bool:
        """Claim SQL against a resolved warehouse source (postgres, snowflake,
        bigquery, …). sqlite is owned exclusively by SqliteAdapter; duckdb is
        owned exclusively by DuckDBAdapter."""
        if not (is_sql_query(query) and source_config is not None):
            return False
        return source_config.type not in ("sqlite", "duckdb")

    def param_render_dialect(self, source_config: ResolvedSourceConfig) -> SQLDialect:
        """Pre-rendered SQL for this adapter uses the internal inline style.

        Everything this adapter receives is flattened to literals — params are
        never bound through a driver — so the warehouse's own placeholder
        syntax would collide with authored literals shaped like placeholders
        ('$1,000+', a ? in prose). The source does not affect the choice.
        """
        return INLINE_PLACEHOLDERS

    def _execute(
        self,
        query: AnyQuery,
        variables: VariableValues | None = None,
        params: QueryParams = None,
        source_config: ResolvedSourceConfig | None = None,
    ) -> QueryResult:
        """Execute a SQL query via dbt adapters.

        Routes to _execute_via_dbt_adapter for all warehouse dialects
        (postgres, snowflake, bigquery, databricks, etc.). SQLite is owned
        exclusively by SqliteAdapter; DuckDB is owned exclusively by
        DuckDBAdapter.

        Args:
            query: AnyQuery object (SqlQuery expected)
            variables: Variable values for Jinja resolution
            params: Optional pre-computed parameter values.
            source_config: Typed source config from SourceResolver.

        Returns:
            QueryResult with data or error
        """
        if not is_sql_query(query):
            return plain_error(f"Expected SQL query, got {query.query_type}")

        prepared = self.prepare_sql(
            query, variables=variables, params=params, source_config=source_config
        )
        if isinstance(prepared, QueryResult):
            return prepared

        # Resolver-provided source_config is the single source of truth. When
        # absent, the adapter falls through to its own default connection using
        # the configured profile_type. dbt_profile sources are expanded to their
        # concrete warehouse type by DefaultSourceResolver before routing, so
        # _execute never receives a DbtProfileSourceConfig.
        raw_config: dict[str, Any] | None = (
            source_config.model_dump(by_alias=True)
            if source_config is not None
            else None
        )

        result = self._execute_via_dbt_adapter(prepared, query, raw_config)

        if prepared.resolved_relations:
            result.resolved_relations = prepared.resolved_relations

        return result

    def prepare_sql(
        self,
        query: SqlQuery,
        *,
        variables: VariableValues | None = None,
        params: QueryParams = None,
        source_config: ResolvedSourceConfig | None = None,
    ) -> PreparedSql | QueryResult:
        """Do everything client-side that turns a compiled query into wire SQL.

        dbt refs, ``setup_sql`` Jinja, variable rendering, param inlining, the
        pushed-down limit, and the mutating-SQL guards — the whole client half of
        ``_execute``, stopping before the warehouse is contacted. Callers that
        need the exact string execution would send (the BigQuery dry run) get it
        from here rather than re-deriving it and drifting.

        Returns a ``QueryResult`` instead when preparation fails, so the caller
        propagates one error shape whichever half produced it.
        """
        try:
            sql, resolved_relations = self._dbt_refs.resolve(query.sql)
        except DbtChartsError as e:
            return handle_adapter_error("dbt ref resolution", e)

        dialect_name = (
            source_config.type if source_config is not None else self.profile_type
        )

        resolved_setup_sql: str | None = None
        if query.setup_sql:
            resolved = resolve_setup_sql(query.setup_sql, variables)
            if isinstance(resolved, QueryResult):
                return resolved
            resolved_setup_sql = resolved

        # Both branches carry the same placeholder style — the internal
        # collision-free one this adapter declares via param_render_dialect:
        # caller-supplied params arrive with SQL the composition point rendered
        # in that declared style, and SQL rendered here uses it directly. The
        # warehouse's own syntax is never used: it can occur in authored string
        # literals ($1 in a money string, ? in prose) and would be corrupted by
        # the inline step.
        placeholder_style: SQLDialect = INLINE_PLACEHOLDERS
        if params is not None:
            resolved_sql, resolved_params = sql, list(params)
        else:
            try:
                parameterized = render_parameterized(
                    sql,
                    variables=variables or {},
                    dialect=placeholder_style,
                    strict=not (is_sql_query(query) and query.lenient_variables),
                    warehouse=get_dialect(dialect_name),
                )
            except (ValueError, KeyError, TypeError) as e:
                return handle_adapter_error("SQL parameterization", e)
            resolved_sql, resolved_params = parameterized.sql, parameterized.params

        try:
            # The dbt adapter.execute() accepts only plain SQL — no bound params.
            # Inline params as SQL literals before passing the SQL string.
            # Placeholders are matched in the style they were rendered in
            # (placeholder_style = INLINE_PLACEHOLDERS — the internal collision-free
            # style); escaping follows the warehouse that will parse the literals.
            inlined_sql = inline_params_for_dialect(
                resolved_sql,
                list(resolved_params) if resolved_params else [],
                placeholder_style,
                escaping=get_dialect(dialect_name),
            )

            # Validate setup_sql + main SQL separately (different allowlists)
            # — fail-closed if either piece contains mutating SQL. validate_setup_sql
            # also runs in the DuckDB setup path; running it here too is the correct
            # re-check for the combined-script branch since the strings hit a
            # different driver.
            if resolved_setup_sql:
                validate_setup_sql(resolved_setup_sql, dialect=dialect_name)
            validate_select_only(inlined_sql, dialect=dialect_name)
        except Exception as e:  # noqa: BLE001 — client-side prep boundary
            # All client-side prep — the warehouse was never contacted, so this
            # must not be classified as a warehouse rejection. DbtChartsError
            # codes (MutatingSqlError, UnparseableSqlError from the guards) are
            # preserved by handle_adapter_error's DbtChartsError branch.
            return handle_adapter_error(f"{dialect_name} query setup", e)

        # The author's own query.limit if set and within the execution.max_rows
        # ceiling, else the ceiling itself (over-fetched by one row so the
        # executor can detect truncation). Bounds the driver's own fetch
        # (dbt-adapters' execute(..., limit=...) -> cursor.fetchmany()) — the
        # SQL text sent to the warehouse is never rewritten, so every dialect
        # and statement shape (DESCRIBE/SHOW/EXPLAIN, an author's own inline
        # LIMIT/TOP) behaves exactly as it would without this ceiling.
        row_fetch_limit = resolve_effective_row_limit(query.limit)

        return PreparedSql(
            sql=inlined_sql,
            setup_sql=resolved_setup_sql,
            dialect_name=dialect_name,
            resolved_relations=resolved_relations,
            row_fetch_limit=row_fetch_limit,
        )

    def _get_source_pool(self, source_config: dict[str, Any]) -> _SourcePool:
        """Return the per-source pool, creating it once per distinct source.

        The pool builds its dbt adapters lazily, one per persistent worker thread
        on first execute() — nothing is built here. Pool width follows the same
        resolution as the render-time pool (explicit max_workers → DCT_MAX_WORKERS
        → execution-config default) so the two never disagree.
        """
        key = _source_config_hash(source_config)
        if key not in self._source_pools:
            with self._source_pools_lock:
                if key not in self._source_pools:
                    from dbt_charts.core.compile.config import (
                        resolve_max_query_duration_seconds,
                        resolve_max_workers,
                    )

                    max_workers = resolve_max_workers(self._max_workers)
                    timeout_seconds = resolve_max_query_duration_seconds(source_config)
                    self._source_pools[key] = _SourcePool(
                        source_config, max_workers, timeout_seconds
                    )
        return self._source_pools[key]

    def _execute_via_dbt_adapter(
        self,
        prepared: PreparedSql,
        query: SqlQuery,
        source_config: dict[str, Any] | None,
    ) -> QueryResult:
        """Send already-prepared SQL to the dbt adapter for the resolved dialect.

        ``prepared`` arrives from :meth:`prepare_sql` with params inlined and
        the mutating-SQL guards already run — this half only opens the
        connection, bounds the fetch via ``prepared.row_fetch_limit``, and
        reads the result back.

        setup_sql (if any) runs inside the same connection_named("dbt_charts_query")
        block as the main query so temp functions/tables are visible to the query.
        BigQuery CREATE TEMP FUNCTION is session-scoped — two connection_named calls
        produce two distinct connections and the temp function would not be visible.
        """
        dialect_name = prepared.dialect_name
        if source_config is None:
            return plain_error(
                f"No source config found for dialect '{dialect_name}'. "
                f"Provide an inline source: block or a named source in dbt_charts.yml."
            )

        try:
            # Use the per-source connection pool: one adapter per source, one
            # thread-local connection per worker.  Avoids the OAuth handshake +
            # SSL session setup (~1.5–1.8 s on Databricks) on every query.
            # Building the pool object itself does no I/O — it builds dbt
            # adapters lazily inside pool.execute() below.
            pool = self._get_source_pool(source_config)
        except Exception as e:  # noqa: BLE001
            # Pool construction is client-side — the warehouse was never
            # contacted, so this must not be classified as a warehouse rejection.
            return handle_adapter_error(f"{dialect_name} query setup", e)

        try:
            table = pool.execute(
                prepared.sql,
                prepared.setup_sql,
                limit=prepared.row_fetch_limit.fetch_limit,
            )
        except _QueryDurationExceeded as e:
            source_label = query.source or dialect_name
            return QueryResult(
                data=[],
                error=QueryError(
                    ERR_QUERY_DURATION_EXCEEDED.message_template.format(
                        seconds=e.seconds, source=source_label
                    ),
                    code=ERR_QUERY_DURATION_EXCEEDED,
                ),
            )
        except ConnectionSetupFailed as e:
            # Building/connecting the worker's dbt adapter failed (bad
            # credentials, unreachable host) — the warehouse never saw the
            # query, so this is not a warehouse rejection either.
            return connection_failure(dialect_name, e.cause)
        except Exception as e:  # noqa: BLE001
            return classify_warehouse_error(
                f"{dialect_name} SQL execution", e, dialect_name
            )

        # Result materialization is client-side (no further warehouse round
        # trip) — left unguarded so a defect here (e.g. an unexpected row
        # shape) surfaces as a crash, not a mislabeled "warehouse rejected
        # the query".
        columns = [col.lower() for col in table.column_names]
        rows = list(table.rows)

        # The driver already bounded the fetch to row_fetch_limit.fetch_limit
        # rows (dbt-adapters' execute(..., limit=...) -> cursor.fetchmany()).
        # This slices the ceiling's one-extra-row over-fetch back down and
        # flags truncation only when the ceiling, not the author's own
        # query.limit, was the binding value.
        rows, truncated_reason = apply_row_limit_truncation(
            rows, prepared.row_fetch_limit
        )

        data = [dict(zip(columns, row, strict=False)) for row in rows]
        return QueryResult(
            data=data, columns=columns, truncated_reason=truncated_reason
        )

    def close(self) -> None:
        """Close all source pools."""
        with self._source_pools_lock:
            pools = list(self._source_pools.values())
            self._source_pools.clear()
        for pool in pools:
            pool.close()
