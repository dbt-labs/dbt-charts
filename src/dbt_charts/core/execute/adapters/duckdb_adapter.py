"""DuckDB adapter for direct DuckDB query execution.

Stage: EXECUTE
Purpose: Execute SQL queries via the raw duckdb driver (parameterized, fast).

All DuckDB execution — file-based or :memory: — goes through this adapter,
driven by a typed DuckDBSourceConfig.  Non-DuckDB warehouses use SqlAdapter
(dbt-adapters path).

Security: DuckDB uses parameterized queries ($1, $2 placeholders). The
read-only flag and enable_external_access config are enforced here.
"""

from __future__ import annotations

import re
import threading
from pathlib import Path  # noqa: TID251 — resolves the DuckDB database file on disk
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    import duckdb

    from dbt_charts.core.compile.models.source import ResolvedSourceConfig
    from dbt_charts.core.project import Project

from dbt_charts.core.compile.models.board.normalized import VariableValues
from dbt_charts.core.compile.models.query.normalized import (
    AnyQuery,
    is_sql_query,
)
from dbt_charts.core.compile.models.source import DuckDBSourceConfig
from dbt_charts.core.compile.sql_guard import validate_select_only, validate_setup_sql
from dbt_charts.core.compile.template.parameterized import render_parameterized
from dbt_charts.core.diagnostics.base import DbtChartsError
from dbt_charts.core.diagnostics.codes_execute import (
    ERR_ADAPTER_RELATIVE_PATH_NO_DATA_DIR,
    ERR_BINDER_TYPE_MISMATCH,
    ERR_BINDER_UNKNOWN_COLUMN,
    ERR_WAREHOUSE_RUNTIME,
)
from dbt_charts.core.diagnostics.execution import QueryError
from dbt_charts.core.dialects import get_dialect
from dbt_charts.core.execute.adapters.base import (
    BaseAdapter,
    QueryParams,
    QueryResult,
    apply_row_limit_truncation,
    connection_failure,
    handle_adapter_error,
    plain_error,
    resolve_effective_row_limit,
    resolve_setup_sql,
)
from dbt_charts.core.execute.adapters.dbt_utils import DbtRefResolver
from dbt_charts.core.execute.duckdb_config import normalize_duckdb_config

# Relative read_csv()/read_parquet() paths resolve against data_dir via a
# per-connection `SET file_search_path` applied at connection creation. This lock
# serializes execute/fetchall because the default connection (`self._connection`)
# is shared across threads; removing it requires thread-local connections and a
# close()-vs-in-flight-query contract (see task
# parallelize-duckdb-execution-remove-the-execute-lock).
_DUCKDB_EXECUTE_LOCK = threading.RLock()


def _classify_duckdb_error(
    exc: Exception, operation: str = "DuckDB SQL execution"
) -> QueryResult:
    """Map a DuckDB exception to a typed ERR-* QueryResult.

    DuckDB's exception hierarchy:
    - BinderException (ProgrammingError) — unknown column, ambiguous ref
    - CatalogException (ProgrammingError) — unknown table / schema
    - TypeMismatchException (DataError) — operator type mismatch
    Everything else → ERR-WAREHOUSE-RUNTIME (warehouse rejected the query
    but no fine-grained category applies).

    The classified `QueryError` is built via `from_code` so its message is
    exactly the registered template, not a hand-rolled
    "DuckDB SQL execution failed: {detail}" prefix.

    validate_select_only/validate_setup_sql run inside the same try this
    classifies, so a DbtChartsError (MutatingSqlError, UnparseableSqlError)
    can reach here too — delegate those to handle_adapter_error first so
    they keep their own registered code (and, for UnparseableSqlError, its
    sql_line/sql_start_col/sql_end_col fields) instead of falling through to
    the generic warehouse-runtime classification below. Mirrors
    classify_warehouse_error's identical delegation for the dbt-adapter path.
    """
    if isinstance(exc, DbtChartsError):
        return handle_adapter_error(operation, exc)

    import duckdb

    detail = str(exc)
    if isinstance(exc, (duckdb.BinderException, duckdb.CatalogException)):
        code = ERR_BINDER_UNKNOWN_COLUMN
    elif isinstance(exc, duckdb.TypeMismatchException):
        code = ERR_BINDER_TYPE_MISMATCH
    else:
        code = ERR_WAREHOUSE_RUNTIME
    return QueryResult(data=[], error=QueryError.from_code(code, detail=detail))


class DuckDBAdapter(BaseAdapter):
    """Adapter for executing SQL queries directly via the DuckDB driver.

    Handles all DuckDB sources: file-based, :memory:, and named DuckDB sources
    in dbt_charts.yml.  Non-DuckDB warehouses use SqlAdapter.

    Supported query types: sql
    """

    def __init__(
        self,
        *,
        source_config: DuckDBSourceConfig,
        data_dir: Path | None = None,
        read_only: bool = True,
        duckdb_config: dict[str, Any] | None = None,
        allow_external_access_in_readonly: bool = False,
        dbt_project_path: str | None = None,
        project: Project | None = None,
    ):
        """Initialize DuckDB adapter.

        Args:
            source_config: Typed DuckDB connection descriptor. ``source_config.path`` is
                the DuckDB file path or ":memory:" for an in-memory database.
            data_dir: Directory relative DuckDB paths (named-source `path:` values,
                and read_csv()/read_parquet() when external access is on) resolve
                against. None for hosts with no local filesystem to root against
                (e.g. Cloud) — resolving a relative path or enabling external
                access without one raises ValueError.
            read_only: If True (default), open DuckDB connections in read-only mode.
                File-based DuckDB: opened with read_only=True — the driver refuses all writes.
                In-memory DuckDB (:memory:): always opened read-write regardless of this flag
                because there is no other way to populate an in-memory database; this is a
                known exception. enable_external_access=False is still forced for :memory:
                when read_only=True unless allow_external_access_in_readonly=True.

                When read_only=True (and allow_external_access_in_readonly=False),
                enable_external_access=False is forced on the DuckDB config regardless of
                any user-supplied duckdb_config.
            duckdb_config: Optional DuckDB config dict for the default connection.
            allow_external_access_in_readonly: Security opt-in. When True AND
                read_only=True AND duckdb_config contains enable_external_access=True,
                the adapter passes enable_external_access=True through to DuckDB.
                Default False preserves the existing defense: read_only=True always
                implies enable_external_access=False.
            dbt_project_path: Optional path to the dbt project root. When set and
                source_config.path == ":memory:" (no explicit file requested), the
                adapter scans dbt_project_path/data/ for DBT_PROJECT_DB_NAMES and
                connects to the first existing file (falling back to :memory: if none
                found).
            project: Seam the dbt manifest is read through, so `{{ ref() }}` in a
                query against a duckdb source (including a dbt_profile source that
                expanded to one) resolves to its relation. None for a host with no
                project to read — ref()/source() then raises rather than reaching
                the variable renderer as an undefined Jinja global.
        """
        self._data_dir = data_dir
        self.source_config = source_config
        self.read_only = read_only
        self.allow_external_access_in_readonly = allow_external_access_in_readonly
        self._duckdb_config = duckdb_config
        self.dbt_project_path = Path(dbt_project_path) if dbt_project_path else None
        self._dbt_refs = DbtRefResolver(project)
        self._connection: duckdb.DuckDBPyConnection | None = None
        # Thread-local storage for per-source DuckDB connection caches.
        self._tls = threading.local()
        # Track all connections opened across all threads so close() can clean up.
        self._all_conns: list[duckdb.DuckDBPyConnection] = []
        self._all_conns_lock = threading.Lock()

    @property
    def supported_types(self) -> set[str]:
        """Return supported query types."""
        return {"sql"}

    @property
    def profile_type(self) -> str:
        """Warehouse type label used by observability observers."""
        return "duckdb"

    def _can_execute(
        self, query: AnyQuery, source_config: ResolvedSourceConfig | None
    ) -> bool:
        """Claim SQL against a DuckDB source, or a source-less SQL query.

        Source-less SQL (source_config is None) falls to DuckDB as the engine's
        default connection; a resolved duckdb source routes here explicitly.
        """
        return is_sql_query(query) and (
            source_config is None or source_config.type == "duckdb"
        )

    def _execute(
        self,
        query: AnyQuery,
        variables: VariableValues | None = None,
        params: QueryParams = None,
        source_config: ResolvedSourceConfig | None = None,
    ) -> QueryResult:
        """Execute a SQL query via DuckDB.

        Args:
            query: AnyQuery object (SqlQuery expected)
            variables: Variable values for Jinja resolution
            params: Optional pre-computed parameter values.
            source_config: Typed source config from SourceResolver. When None,
                the adapter uses its default connection.

        Returns:
            QueryResult with data or error
        """
        if not is_sql_query(query):
            return plain_error(f"Expected SQL query, got {query.query_type}")

        sql = query.sql
        try:
            sql, resolved_relations = self._dbt_refs.resolve(sql)
        except DbtChartsError as e:
            return handle_adapter_error("dbt ref resolution", e)

        raw_config: dict[str, Any] | None
        if source_config is not None:
            raw_config = source_config.model_dump(by_alias=True)
            dialect_name = source_config.type
        else:
            raw_config = None
            dialect_name = "duckdb"

        # Resolve setup_sql Jinja before executing.
        resolved_setup_sql: str | None = None
        if query.setup_sql:
            resolved = resolve_setup_sql(query.setup_sql, variables)
            if isinstance(resolved, QueryResult):
                return resolved
            resolved_setup_sql = resolved

        if params is not None:
            resolved_sql = sql
            resolved_params = params
        else:
            try:
                parameterized = render_parameterized(
                    sql,
                    variables=variables or {},
                    dialect=get_dialect(dialect_name),
                    strict=not (is_sql_query(query) and query.lenient_variables),
                )
                resolved_sql = parameterized.sql
                resolved_params = parameterized.params
            except (ValueError, KeyError, TypeError) as e:
                return handle_adapter_error("SQL parameterization", e)

        # Read-only file connections are opened per query and closed after, so an
        # idle server does not hold the DB file locked (a held read-only connection
        # still denies a writer its exclusive lock — that is what blocked `refresh`
        # while `serve` was up). :memory: stays pooled — it is opened read-write and
        # destroyed on close, so it cannot be reopened. The one connection spans
        # setup_sql + the main query.
        resolved_path = self._resolved_path(raw_config)
        if self.read_only and resolved_path != ":memory:":
            # Open inside the try so a connect-time lock conflict — the exact
            # refresh-collision this feature is about — is typed instead of
            # escaping untyped. It gets the connection code, not a query one: a
            # file that would not open has not judged anybody's SQL.
            try:
                conn = self._connect(raw_config, resolved_path)
            except Exception as e:  # noqa: BLE001 — classify driver connect errors
                return connection_failure("duckdb", e)
            close_after = True
        else:
            conn = self._get_duckdb_connection_for_query(raw_config)
            close_after = False
        try:
            if resolved_setup_sql:
                err = self._execute_setup_sql_duckdb(resolved_setup_sql, conn)
                if err is not None:
                    return err

            result = self._execute_duckdb(resolved_sql, resolved_params, query, conn)
            if resolved_relations:
                result.resolved_relations = resolved_relations
            return result
        finally:
            if close_after:
                conn.close()

    def _execute_setup_sql_duckdb(
        self,
        resolved_setup_sql: str,
        conn: duckdb.DuckDBPyConnection,
    ) -> QueryResult | None:
        """Execute setup_sql on the query's connection. Returns None on success."""
        try:
            validate_setup_sql(resolved_setup_sql, dialect="duckdb")
            with _DUCKDB_EXECUTE_LOCK:
                conn.execute(resolved_setup_sql)
        except Exception as e:  # noqa: BLE001
            return _classify_duckdb_error(e, "setup_sql")
        return None

    def _execute_duckdb(
        self,
        sql: str,
        params: list[Any],
        query: AnyQuery,
        conn: duckdb.DuckDBPyConnection,
    ) -> QueryResult:
        """Execute SQL query using DuckDB with parameterized execution."""
        # Resolved outside the try/except below: a malformed DCT_MAX_ROWS_CEILING
        # raises ValueError here, which must surface as a config error, not get
        # relabeled "DuckDB execution failed" by the driver-error classifier.
        # Bounds the driver's own fetch (cursor.fetchmany()) — sql is sent to
        # DuckDB unmodified, so every statement shape (DESCRIBE/SHOW, an
        # author's own inline LIMIT) behaves exactly as it would without this
        # ceiling.
        row_fetch_limit = resolve_effective_row_limit(query.limit)
        try:
            validate_select_only(sql, dialect="duckdb")
            with _DUCKDB_EXECUTE_LOCK:
                result = conn.execute(sql, params)
                # Snapshot description INSIDE the lock. conn.execute() returns
                # the connection itself, so result.description reflects whichever
                # query last ran on the (thread-shared) default connection. Read
                # it after the lock releases — as column_descriptions once did —
                # and a concurrent query's execute() overwrites the metadata or
                # closes this result handle ("Invalid Input Error: result closed"
                # / DuckDB bad_weak_ptr). rows is materialized by fetchmany(), so
                # it is safe to consume outside the lock.
                description = result.description
                rows = result.fetchmany(row_fetch_limit.fetch_limit)

            columns = [desc[0] for desc in description] if description else []
            rows, truncated_reason = apply_row_limit_truncation(rows, row_fetch_limit)

            data = [dict(zip(columns, row, strict=False)) for row in rows]
            col_descs = (
                {desc[0]: tuple(desc) for desc in description} if description else None
            )

            return QueryResult(
                data=data,
                columns=columns,
                column_descriptions=col_descs,
                truncated_reason=truncated_reason,
            )

        except Exception as e:  # noqa: BLE001
            return _classify_duckdb_error(e)

    def _get_duckdb_connection_for_query(
        self, source_config: dict[str, Any] | None = None
    ) -> duckdb.DuckDBPyConnection:
        """Get DuckDB connection for a query.

        Uses the typed source_config (resolver-provided) when it points at a
        DuckDB source; otherwise falls through to the adapter's default connection.
        The thread-local connection cache is keyed by source_config['path'] —
        two named DuckDB sources that point at the same path share a connection.
        """
        if source_config and source_config.get("type") == "duckdb":
            schema = source_config.get("schema") or ""
            path = str(source_config.get("path") or ":memory:")
            cache_key = f"{path}\0{schema}" if schema else path
            return self._create_duckdb_connection_from_config(source_config, cache_key)
        return self._get_duckdb_connection()

    def _resolve_duckdb_config(
        self, source_config: dict[str, Any]
    ) -> dict[str, Any] | None:
        """Resolve DuckDB config: source-level overrides adapter-level default."""
        return normalize_duckdb_config(source_config) or self._duckdb_config

    def _resolved_path(self, source_config: dict[str, Any] | None) -> str:
        """Resolve the DuckDB target path for a source (':memory:' or a file path).

        Named duckdb sources resolve a relative path against the project data dir;
        the default source honors the dbt-project data-dir scan (a requested
        ':memory:' can resolve to a real file). Used both to open a connection and
        to decide whether it is file-backed (ephemeral) or in-memory (pooled).
        """
        if source_config and source_config.get("type") == "duckdb":
            db_path = str(source_config.get("path") or ":memory:")
            if db_path != ":memory:" and not Path(db_path).is_absolute():
                if self._data_dir is None:
                    raise DbtChartsError.from_code(
                        ERR_ADAPTER_RELATIVE_PATH_NO_DATA_DIR,
                        adapter="DuckDB",
                        path=db_path,
                    )
                return str((self._data_dir / db_path).resolve())
            return db_path
        path = self.source_config.path
        if path == ":memory:" and self.dbt_project_path:
            from dbt_charts.core.execute.adapters.dbt_utils import DBT_PROJECT_DB_NAMES

            for db_name in DBT_PROJECT_DB_NAMES:
                candidate = self.dbt_project_path / "data" / db_name
                if candidate.exists():
                    return str(candidate)
        return path

    def _connect(
        self, source_config: dict[str, Any] | None, path: str
    ) -> duckdb.DuckDBPyConnection:
        """Open a NEW DuckDB connection at ``path`` for a source (no caching).

        ``path`` is the caller's already-resolved ``_resolved_path(source_config)`` —
        passed in so the ephemeral ``_execute`` path resolves it once (the decision)
        rather than twice. Shared by the pooled getters and the per-query ephemeral
        path so both apply the same security posture and session SETs regardless of
        who owns the connection's lifetime.
        """
        import duckdb

        if source_config is not None and source_config.get("type") == "duckdb":
            if "database" in source_config and "path" not in source_config:
                raise ValueError(
                    "DuckDB source_config uses the unsupported 'database' key. "
                    'Use \'path\' instead (e.g. {"type": "duckdb", "path": "db.duckdb"}).'
                )
            duckdb_config = self._resolve_duckdb_config(source_config) or None
            schema = source_config.get("schema")
        else:
            duckdb_config = self._duckdb_config or None
            schema = None

        conn = duckdb.connect(
            path,
            **self._resolve_duckdb_connect_kwargs(
                path,
                self.read_only,
                duckdb_config,
                self.allow_external_access_in_readonly,
            ),
        )
        # A post-connect SET (or the schema guard) that raises must not orphan the
        # just-opened connection — close it before propagating so the file lock is
        # released.
        try:
            # Session-local: relative read_csv()/read_parquet() paths in queries
            # resolve against the project root instead of the process cwd. Only
            # meaningful when external file access is on (DuckDB rejects relative
            # reads otherwise), so skip it — and its data_path resolution — when off.
            if self._external_access_enabled(
                self.read_only, duckdb_config, self.allow_external_access_in_readonly
            ):
                if self._data_dir is None:
                    raise ValueError(
                        "External file access (read_csv()/read_parquet()) requires "
                        "a data_dir to resolve relative paths against; none was "
                        "configured for this adapter."
                    )
                conn.execute("SET file_search_path = ?", [str(self._data_dir)])
            if schema:
                if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", schema):
                    raise ValueError(
                        f"Invalid schema name {schema!r}: must match "
                        r"^[A-Za-z_][A-Za-z0-9_]*$"
                    )
                conn.execute("SET search_path = ?", [f"{schema},main"])
        except BaseException:
            conn.close()
            raise
        return conn

    def _create_duckdb_connection_from_config(
        self, source_config: dict[str, Any], cache_key: str
    ) -> duckdb.DuckDBPyConnection:
        """Get or create a pooled connection for a named DuckDB source."""
        sources = getattr(self._tls, "sources", None)
        if sources is None:
            sources = self._tls.sources = {}
        if cache_key not in sources:
            conn = self._connect(source_config, self._resolved_path(source_config))
            sources[cache_key] = conn
            with self._all_conns_lock:
                self._all_conns.append(conn)
        return sources[cache_key]

    @staticmethod
    def _external_access_enabled(
        read_only: bool,
        duckdb_config: dict[str, Any] | None,
        allow_external_access_in_readonly: bool = False,
    ) -> bool:
        """Whether relative read_csv()/read_parquet() can resolve on this connection.

        file_search_path only affects relative file reads, which DuckDB rejects
        unless external access is on — so the SET (and its data_dir resolution)
        is pure overhead when this returns False. Read-only forces
        external access off unless the caller opted in
        (allow_external_access_in_readonly) AND set enable_external_access=True;
        otherwise DuckDB's default (on) holds unless the config disables it.
        """
        config = duckdb_config or {}
        if read_only and not (
            allow_external_access_in_readonly
            and config.get("enable_external_access") is True
        ):
            return False
        return config.get("enable_external_access", True) is True

    @staticmethod
    def _resolve_duckdb_connect_kwargs(
        path: str,
        read_only: bool,
        duckdb_config: dict[str, Any] | None,
        allow_external_access_in_readonly: bool = False,
    ) -> dict[str, Any]:
        """Build duckdb.connect() kwargs for the given path and security posture.

        When read_only=True and allow_external_access_in_readonly=False (default):
        - Adds read_only=True for file-based paths (not :memory: — in-memory DuckDB
          must always be opened read-write; this is a known exception).
        - Forces enable_external_access=False in the config, overriding any
          user-supplied duckdb_config value.

        When read_only=True and allow_external_access_in_readonly=True:
        - Same read_only=True driver flag for file-based paths.
        - enable_external_access is NOT forced to False; caller's duckdb_config
          passes through. Approved callsites: LOCAL_AUTHORING_REGISTRY_KWARGS.

        When read_only=False:
        - No read_only driver flag.
        - duckdb_config is passed through as-is.
        """
        kwargs: dict[str, Any] = {}
        if read_only and path != ":memory:":
            kwargs["read_only"] = True
        if duckdb_config is not None or read_only:
            config = dict(duckdb_config or {})
            if not DuckDBAdapter._external_access_enabled(
                read_only, config, allow_external_access_in_readonly
            ):
                config["enable_external_access"] = False
            if config:
                kwargs["config"] = config
        return kwargs

    def _get_duckdb_connection(self) -> duckdb.DuckDBPyConnection:
        """Get or create the pooled default DuckDB connection.

        The dbt-project data-dir scan and session SETs live in ``_connect`` /
        ``_resolved_path`` (shared with the ephemeral path). Reached only for the
        pooled case — read-only file sources open per query via ``_connect``.
        """
        if self._connection is None:
            self._connection = self._connect(None, self._resolved_path(None))
        return self._connection

    def close(self) -> None:
        """Close all DuckDB connections."""
        if self._connection:
            self._connection.close()
            self._connection = None

        with self._all_conns_lock:
            conns, self._all_conns = self._all_conns, []
        for conn in conns:
            conn.close()

        if hasattr(self._tls, "sources"):
            self._tls.sources = {}
