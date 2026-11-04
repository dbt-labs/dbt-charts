"""Registry that executes a board's queries.

Stage: EXECUTE
Purpose: Resolve a query's source and run it: SQL on the backend its source
    kind routes to, ``values`` and ``schema`` queries on their own adapters.
"""

from __future__ import annotations

import threading
import time
from collections.abc import Callable, Mapping
from datetime import UTC, datetime
from pathlib import (
    Path,  # noqa: TID251 — names the dbt project directory and DuckDB/SQLite files on disk
)
from typing import TYPE_CHECKING, Any, Final

from dbt_charts.cli.filesystem_project import (
    FilesystemProject,  # tach-ignore(core->cli: host-type guard; needs a non-cli signal on Project — deferred)
)
from dbt_charts.core.attribution import attribute
from dbt_charts.core.compile.config import ProjectSourcesConfig
from dbt_charts.core.compile.models.board.normalized import VariableValues
from dbt_charts.core.compile.models.primitives import Regional
from dbt_charts.core.compile.models.query.normalized import (
    AnyQuery,
    SchemaQuery,
    SqlQuery,
    is_sql_query,
    is_values_query,
)
from dbt_charts.core.compile.models.source import (
    FILE_SOURCE_TYPES,
    AttributedSourceConfig,
    CsvSourceConfig,
    DbtProfileSourceConfig,
    DuckDBSourceConfig,
    JsonSourceConfig,
    ParquetSourceConfig,
    SemanticSourceConfig,
    SQLiteSourceConfig,
    is_file_source,
)
from dbt_charts.core.compile.sql_guard import sqlglot_dialect
from dbt_charts.core.compile.template.daterange import (
    BoardClock,
    board_clock,
    board_clock_in_scope,
)
from dbt_charts.core.compile.template.parameterized import DateRangeFilter
from dbt_charts.core.diagnostics.base import DbtChartsError

# ERR_SOURCE_NOT_FOUND is compile-owned (dropping the domain segment
# collided the compile- and execute-side codes; compile's message_template
# won).
from dbt_charts.core.diagnostics.codes_compile import ERR_SOURCE_NOT_FOUND
from dbt_charts.core.diagnostics.codes_execute import (
    ERR_ADAPTER_RELATIVE_PATH_NO_DATA_DIR,
    ERR_ADAPTER_UNAVAILABLE,
    ERR_QUERY_DURATION_EXCEEDED,
    ERR_SOURCE_NOT_FOUND_EMPTY,
    ERR_SOURCE_NOT_NAMED,
)
from dbt_charts.core.diagnostics.execution import QueryError
from dbt_charts.core.dialects import DIALECTS, SQLDialect, get_dialect
from dbt_charts.core.execute.adapters.base import (
    QueryDurationExceeded,
    QueryParams,
    QueryResult,
    adapter_unavailable,
    apply_row_limit_truncation,
    classify_warehouse_error,
    connection_failure,
    handle_adapter_error,
    plain_error,
    resolve_effective_row_limit,
)
from dbt_charts.core.execute.adapters.dbt_adapter_factory import (
    ConnectionSetupFailed,
)
from dbt_charts.core.execute.adapters.dbt_utils import (
    DbtRefResolver,
)
from dbt_charts.core.execute.adapters.schema_adapter import SchemaAdapter
from dbt_charts.core.execute.adapters.values_adapter import ValuesAdapter
from dbt_charts.core.execute.backends.base import Backend, BackendRegistry
from dbt_charts.core.execute.backends.duckdb_local import default_duckdb_source
from dbt_charts.core.execute.compose import (
    compose,
)
from dbt_charts.core.execute.dbt_jinja import has_dbt_jinja
from dbt_charts.core.execute.drivers import DRIVER_SOURCE_TYPES
from dbt_charts.core.execute.metricflow import SemanticLayer
from dbt_charts.core.execute.observability import WarehouseObserver, notify_observers
from dbt_charts.core.execute.result import rows_to_query_result
from dbt_charts.core.execute.source_registry import SourceRegistry
from dbt_charts.core.execute.source_resolver import (
    AD_HOC_QUERY_NAME,
    DbtContext,
    DefaultSourceResolver,
)
from dbt_charts.core.execute.sql_literals import (
    INLINE_PLACEHOLDERS,
    inline_params_for_dialect,
)
from dbt_charts.core.execute.sql_translate import (
    dialect_differs,
    dialect_unsupported,
    engine_dialect,
)
from dbt_charts.core.project import Project
from dbt_charts.core.project_data_files import unknown_source_advice

if TYPE_CHECKING:
    from dbt_charts.core.compile.models.board.normalized import Board
    from dbt_charts.core.compile.models.source import ResolvedSourceConfig
    from dbt_charts.core.execute.compose import ResolvedQuery
    from dbt_charts.core.execute.file_source_materializer import (
        FileSourceMaterializer,
    )
    from dbt_charts.core.execute.source_resolver import SourceResolver

# Empty source config used as the default when the caller has no project sources to
# pass.  Module-level Final avoids per-call construction; SourceRegistry never mutates
# its project_sources argument (only self._synthetic is modified by register()).
_EMPTY_PROJECT_SOURCES: Final[ProjectSourcesConfig] = ProjectSourcesConfig(sources={})

# Approved local-dev surfaces that need non-exclusive DuckDB locks AND SQL
# read_csv/read_json_auto (see m3-duckdb-concurrency-and-file-sources initiative).
# Grep this symbol before adding allow_external_access_in_readonly=True elsewhere.
# A DuckDB source that authors attach/extensions/secrets gets external access
# only while those fields are applied; DuckDBBackend._connect then locks the
# connection to the attach paths and secret scopes.
LOCAL_AUTHORING_REGISTRY_KWARGS: dict[str, Any] = {
    "read_only": True,
    "allow_external_access_in_readonly": True,
    "duckdb_config": {"enable_external_access": True},
}


def linked_dbt_path(project: Project) -> Path | None:
    """The project's dbt project directory, or None when it has no dbt project.

    dbt_project.yml is valid at project.dbt_root (see resolve_dbt_project_dir for
    how that's resolved) -- meaningful only for a real filesystem project (dbt's
    own profiles/target resolution assumes a directory).
    """
    return (
        project.dbt_root
        if isinstance(project, FilesystemProject)
        and project.dbt_project.exists("dbt_project.yml")
        else None
    )


def warehouse_routes(max_workers: int | None) -> dict[str, Backend]:
    """The routes of the warehouse kinds: Arrow drivers share one backend, dbt
    adapters another."""
    from dbt_charts.core.execute.adapters.dbt_adapter_factory import DBT_SOURCE_TYPES
    from dbt_charts.core.execute.backends.dbt_core import DbtBackend
    from dbt_charts.core.execute.backends.driver import DriverBackend

    return {
        **dict.fromkeys(DRIVER_SOURCE_TYPES, DriverBackend(max_workers)),
        **dict.fromkeys(DBT_SOURCE_TYPES, DbtBackend(max_workers)),
    }


def build_adapter_registry(
    project: Project,
    *,
    read_only: bool = True,
    profile_type: str = "duckdb",
    target: str | None = None,
    duckdb_config: dict[str, Any] | None = None,
    resolver: SourceResolver | None = None,
    allow_external_access_in_readonly: bool = False,
    max_workers: int | None = None,
    observers: list[WarehouseObserver] | None = None,
    file_materializer: FileSourceMaterializer | None = None,
    file_materializer_factory: Callable[[], FileSourceMaterializer] | None = None,
    extra_routes: Mapping[str, Backend] | None = None,
) -> AdapterRegistry:
    """Build an AdapterRegistry with standard adapters.

    This is the single canonical way to create an adapter registry.
    All entry points should use this instead of hand-wiring adapters.

    Args:
        project: The dbt charts project supplying source config. When it is a
            ``FilesystemProject``, DuckDBBackend/SqliteBackend also root
            relative paths against ``project.root``; any other host (Cloud's
            git-blob store, an in-memory test double) gets ``data_dir=None``
            — fine as long as no source references a relative file path.
            (Cloud actually builds its registry via
            ``build_cloud_adapter_registry``, not this function, but the
            fallback keeps this function itself host-agnostic.)
        read_only: Whether SQL adapters should be read-only (default True).
        profile_type: SQL dialect (default "duckdb").
        target: dbt target for every ``dbt_profile`` source that names none
            (``--target``). None: ``$DBT_TARGET``, then the profile's default.
        duckdb_config: Optional DuckDB config dict passed to the default
            connection. When read_only=True (the default) and
            allow_external_access_in_readonly=False, enable_external_access is
            hard-forced to False for caller SQL regardless of what this dict
            contains (see LOCAL_AUTHORING_REGISTRY_KWARGS above). To
            combine read_only=True with external file access (read_csv, httpfs,
            etc.) on every connection, set both allow_external_access_in_readonly=True
            AND duckdb_config={"enable_external_access": True}. The default path
            (read_only=False) also allows external access without the flag.
        resolver: Source resolver instance. Defaults to DefaultSourceResolver when
            None. Pass an AllowlistedSourceResolver for deployed-mode enforcement.
        allow_external_access_in_readonly: Security opt-in; passed through to
            DuckDBBackend. Default False
            preserves existing behavior. Approved callsites passing True are listed
            on LOCAL_AUTHORING_REGISTRY_KWARGS in this module (local `dct
            playground`, MCP, the embedded preview, evals).
        max_workers: Width of the dbt backend's per-source connection pool (warm
            warehouse worker threads). None falls back to the execution-config
            default. Serve passes its resolved max_workers so the persisted
            registry's pool matches the render-time parallelism.
        file_materializer: Pre-built materializer for CSV/JSON/Parquet file
            sources (a host's shared one, e.g. Cloud's).
        file_materializer_factory: Lazy builder for the file-source
            materializer, built on first use. When neither argument is given,
            the registry lazily builds the local in-process materializer, so a
            fully-cached render never opens one.
        extra_routes: Host-supplied backends by source kind, replacing the
            default route of any kind they name. The registry owns them:
            ``close()`` closes each.
    """
    from dbt_charts.core.execute.backends.duckdb_local import DuckDBBackend
    from dbt_charts.core.execute.backends.sqlite import SqliteBackend
    from dbt_charts.core.execute.file_source_materializer import (
        resolve_local_file_materializer_factory,
    )

    # Relative DuckDB/SQLite paths root against the project directory — only a
    # FilesystemProject has one. Any other host (Cloud's git-blob store, an
    # in-memory test double) passes data_dir=None; the backends tolerate that
    # (no relative-path resolution needed until a source actually references one).
    data_dir = project.root if isinstance(project, FilesystemProject) else None
    resolved_dbt_path = linked_dbt_path(project)

    registry = AdapterRegistry(
        project=project,
        project_sources=project.sources,
        resolver=resolver,
        observers=observers,
        file_materializer=file_materializer,
        file_materializer_factory=(
            file_materializer_factory
            if file_materializer_factory is not None
            else resolve_local_file_materializer_factory(project, file_materializer)
        ),
        backends=BackendRegistry(
            {
                **warehouse_routes(max_workers),
                "duckdb": DuckDBBackend(
                    data_dir=data_dir,
                    read_only=read_only,
                    duckdb_config=duckdb_config,
                    allow_external_access_in_readonly=allow_external_access_in_readonly,
                ),
                "sqlite": SqliteBackend(data_dir=data_dir),
                **(extra_routes if extra_routes is not None else {}),
            }
        ),
        profile_type=profile_type,
        dbt_context=(
            DbtContext(dbt_project_path=resolved_dbt_path.resolve(), target=target)
            if resolved_dbt_path is not None
            else None
        ),
    )

    # When the execute path opts into external file access under read-only,
    # cold schema introspection must open DuckDB with the same config.
    if read_only and allow_external_access_in_readonly:
        registry.schema_introspection_duckdb_config = duckdb_config

    return registry


def _file_source_dialect_refusal(dialect: str) -> QueryResult:
    return handle_adapter_error(
        "File-source execution",
        dialect_unsupported(
            dialect,
            "duckdb",
            "queries on a file source are not translated, so write the SQL in duckdb",
        ),
    )


def ad_hoc_board_clock(instant: datetime) -> BoardClock:
    """The clock of a query that belongs to no board: UTC, with the default
    theme's fiscal year and the default locale's week."""
    regional = Regional()
    return BoardClock(
        regional.today(instant),
        regional.fiscal_year_start_month,
        regional.week_start,
        regional.time_zone,
    )


class Unresolved:
    """Type of ``UNRESOLVED``: ``None`` is a valid resolved source, so it cannot
    also mean "not resolved yet"."""


UNRESOLVED = Unresolved()


class AdapterRegistry:
    """Executes queries: SQL on the backend its source kind routes to, the
    non-SQL query types on their own adapters.

    SQL never picks its route: ``resolve_query_source`` names a concrete source,
    and ``BackendRegistry`` maps that source's kind to a backend. ``values`` and
    ``schema`` queries have no source and dispatch on their query type.

    Example:
        >>> from dbt_charts.core.execute.adapters import build_adapter_registry
        >>> from dbt_charts.cli.filesystem_project import FilesystemProject
        >>> registry = build_adapter_registry(FilesystemProject(Path(".")))
        >>> result = registry.execute(SqlQuery(sql="SELECT 1", source="my_db"))
    """

    def __init__(
        self,
        project: Project,
        project_sources: ProjectSourcesConfig = _EMPTY_PROJECT_SOURCES,
        resolver: SourceResolver | None = None,
        observers: list[WarehouseObserver] | None = None,
        file_materializer: FileSourceMaterializer | None = None,
        file_materializer_factory: Callable[[], FileSourceMaterializer] | None = None,
        backends: BackendRegistry | None = None,
        profile_type: str | None = None,
        dbt_context: DbtContext | None = None,
    ) -> None:
        """Initialize adapter registry.

        Use ``build_adapter_registry()`` to get a fully configured registry.

        Args:
            project: The dbt charts project; file reads go through its seam methods.
            project_sources: Pre-loaded source configuration. Caller is responsible
                for loading this (e.g. via ``load_project_sources``).
            resolver: Source resolver instance. Defaults to DefaultSourceResolver.
            observers: Per-registry observer callables. Called after each query with
                (warehouse_type, status, duration_seconds). No module global state.
            file_materializer: Pre-built materializer for CSV/JSON/Parquet file
                sources (mirrors ``Executor``'s same-named parameter). When
                set, a file-source ``SqlQuery`` is routed through it instead of
                being refused.
            file_materializer_factory: Lazy builder for the file-source
                materializer, built at most once on first use (see
                ``_get_file_materializer``). Exactly one of
                ``file_materializer`` / ``file_materializer_factory`` is
                normally set; both ``None`` leaves file-source queries
                refused.
            backends: The routes of the sources that run on a ``Backend``
                (warehouses). None routes none, so such a source is refused by
                kind.
            profile_type: The warehouse dialect the host reports for this
                registry when no dbt project names one (see
                ``detect_database_type_from_registry``).
            dbt_context: The linked dbt project (its path and target), None when
                the host has none. Sources resolve against it; it routes nothing.
        """
        self._dbt_context = dbt_context
        self._values = ValuesAdapter()
        self._schema = SchemaAdapter(self)
        self._observers: list[WarehouseObserver] = list(observers) if observers else []
        self._project = project
        self._backends = backends if backends is not None else BackendRegistry({})
        self.profile_type = profile_type
        self._dbt_refs = DbtRefResolver(project)
        self._sources = SourceRegistry(project_sources=project_sources)
        self._resolver: SourceResolver = (
            resolver if resolver is not None else DefaultSourceResolver(project)
        )
        # DuckDB config that cold schema introspection must mirror so its
        # connection matches the execute connection. DuckDB rejects a second
        # connection to the same file opened with a different config.
        self.schema_introspection_duckdb_config: dict[str, Any] | None = None
        self._file_materializer = file_materializer
        self._file_materializer_factory = file_materializer_factory
        # The factory-built materializer, tracked separately from an injected
        # one: the registry built this itself, so it — and only it — is
        # closed by close(). An injected materializer (Cloud's, shared with
        # the session that built it) is closed by whoever owns it, not us.
        self._built_file_materializer: FileSourceMaterializer | None = None
        # Guards lazy build of the shared materializer (concurrent callers).
        self._materializer_lock = threading.Lock()

    @property
    def project(self) -> Project:
        """Return the registry's project."""
        return self._project

    @property
    def dbt_refs(self) -> DbtRefResolver:
        return self._dbt_refs

    @property
    def dbt_context(self) -> DbtContext | None:
        """The linked dbt project, or None when there is no dbt project."""
        return self._dbt_context

    def interrupt(self) -> None:
        """Cancel in-flight queries and refuse new ones; one-way, for shutdown."""
        self._backends.interrupt()

    def close(self) -> None:
        """Close the backends, which hold the connections.

        Also closes the file-source materializer the registry itself built
        via ``file_materializer_factory``, if any — its cache backend may
        hold a DuckDB handle. An *injected* materializer (``file_materializer``
        at construction) is never closed here: the registry doesn't own it,
        the caller that built and handed it in does (Cloud shares one
        materializer across the registry and the session).
        """
        self._backends.close()
        if self._built_file_materializer is not None:
            self._built_file_materializer.close()

    def _get_file_materializer(self) -> FileSourceMaterializer | None:
        """Return the registry's file-source materializer, building it lazily.

        The one owner of the file-source materializer, shared by every
        executor and ad-hoc query that runs through this registry. An injected
        materializer is returned directly; a factory-backed one is built at
        most once, under a lock, so concurrent render workers share a single
        instance. Returns None when neither was supplied — the registry has
        no file-source support wired.
        """
        if self._file_materializer is not None:
            return self._file_materializer
        if self._file_materializer_factory is None:
            return None
        with self._materializer_lock:
            if self._built_file_materializer is None:
                self._built_file_materializer = self._file_materializer_factory()
        return self._built_file_materializer

    def register_source(self, name: str, config: dict[str, Any]) -> None:
        """Register a synthetic source. Idempotent: first registration wins."""
        self._sources.register(name, config)

    @property
    def project_sources(self) -> ProjectSourcesConfig:
        """The sources execution resolves against: the project's, plus any
        registered at runtime. Compile against this, not ``project.sources``,
        so a runtime-registered source is known to the compiler too."""
        return self._sources.runtime_config()

    def effective_source(self, source: str | None) -> str | None:
        """*source*, or the project's implicit dbt source when it names none.

        The one rule for SQL that names no source; None when there is neither.
        """
        return source if source is not None else self.project_sources.implicit_source

    def execute(
        self,
        query: AnyQuery,
        variables: VariableValues | None = None,
        params: QueryParams = None,
        *,
        board: Board | None = None,
        dbt_context: DbtContext | None = None,
        query_name: str = AD_HOC_QUERY_NAME,
        source_config: ResolvedSourceConfig | None | Unresolved = UNRESOLVED,
    ) -> QueryResult:
        """Execute a query using the appropriate adapter.

        A render scopes the board clock a preset literal in the SQL dates on;
        called outside one, this scopes the board's clock, or for an ad-hoc
        query the UTC date with the default theme's fiscal year and week.

        Args:
            query: AnyQuery object
            variables: Variable values for query resolution
            params: Optional pre-computed parameter values for parameterized execution.
                When provided, the adapter should use these params directly instead
                of re-processing variables. This is used by batch execution where
                parameterization happens before adapter execution.
            board: Compiled board; used to extract board-level sources for resolution.
            dbt_context: Explicit dbt context override. When None, the linked
                dbt project.
            query_name: Author-facing name of the query, threaded to the source
                resolver for ERR-SOURCE-NOT-FOUND / ERR-SOURCE-INLINE-FORBIDDEN
                messages. Callers executing a synthetic query (schema
                introspection, bulk profiling, ad-hoc SQL) have none to give;
                the default documents that explicitly.
            source_config: The query's already-resolved source (None is a
                legitimate resolution: a source-less non-SQL query). A caller
                that resolved it passes it so the source is resolved once;
                UNRESOLVED resolves it here.

        Returns:
            QueryResult with data or error
        """
        if isinstance(source_config, Unresolved):
            try:
                source_config = self.resolve_query_source(
                    query, board=board, dbt_context=dbt_context, query_name=query_name
                )
            except DbtChartsError as exc:
                # handle_adapter_error keeps the resolver's typed identity, so
                # ERR-SOURCE-NOT-FOUND et al. don't flatten to ERR-INTERNAL.
                return handle_adapter_error("Source resolution", exc)
        if board_clock_in_scope():
            return self._execute(
                query, variables, params, board, source_config, query_name
            )
        now = datetime.now(UTC)
        with board_clock(board.clock(now) if board else ad_hoc_board_clock(now)):
            return self._execute(
                query, variables, params, board, source_config, query_name
            )

    def _execute(
        self,
        query: AnyQuery,
        variables: VariableValues | None,
        params: QueryParams,
        board: Board | None,
        source_config: ResolvedSourceConfig | None,
        query_name: str,
    ) -> QueryResult:
        # SqlQuery and SchemaQuery are the two query types that can carry a
        # source (resolve_query_source returns None for anything else) — the
        # isinstance below narrows to read `.source`, not a second condition.
        # SchemaQuery stays refused unconditionally: schema introspection for
        # file sources (the /data explorer, or an authored `type: schema`
        # query) is out of scope here, deferred to a follow-up task. A
        # SqlQuery with a materializer configured dispatches to it below, after
        # composition (see _execute_file_source); with none configured it
        # refuses the same way SchemaQuery always does.
        if source_config is not None and is_file_source(source_config):
            assert isinstance(query, SqlQuery | SchemaQuery)
            if isinstance(query, SchemaQuery):
                return plain_error(
                    f"Source {query.source!r} is a {source_config.type} file "
                    "source. Schema introspection (the /data explorer, or "
                    "an authored `type: schema` query) is not yet "
                    "supported for file sources."
                )

            if query.dialect is not None and dialect_differs(
                query.dialect, source_config
            ):
                return _file_source_dialect_refusal(query.dialect)
            materializer = self._get_file_materializer()
            if materializer is None:
                return adapter_unavailable(
                    f"Source {query.source!r} is a {source_config.type} "
                    "file source, and this session has no file-source "
                    "materializer."
                )

            assert isinstance(
                source_config, CsvSourceConfig | JsonSourceConfig | ParquetSourceConfig
            )
            # Same composition every adapter gets: {{ queries.X }} refs expand
            # and variables render before the materializer ever sees the SQL —
            # dispatching from any point above this would hand it an
            # unresolved template. "duckdb" is the placeholder style
            # materialize_and_run's own render would have used.
            try:
                file_resolved = compose(
                    query,
                    variables=variables,
                    params=params,
                    board=board,
                    source_config=source_config,
                    dialect_name="duckdb",
                    render_dialect=get_dialect("duckdb"),
                    refs=self._dbt_refs,
                    semantic=None,
                )
            except DbtChartsError as exc:
                return handle_adapter_error("File-source execution", exc)
            if isinstance(file_resolved, QueryResult):
                return file_resolved
            query = query.model_copy(update={"sql": file_resolved.sql})
            params = list(file_resolved.params)
            assert isinstance(query, SqlQuery) and query.source is not None
            return self._execute_file_source(
                materializer,
                query,
                variables,
                params,
                source_config,
                board=board,
                query_name=query_name,
            )

        # The board and query that caused this warehouse call. Established here
        # rather than authored, so it cannot drift when a board or query is
        # renamed. The surface is set further out, by whichever root opened the
        # session.
        scope = {"query": query_name}
        if board is not None:
            scope["board"] = board.id
        # The source's own attribution rides this per-call scope, not the pooled
        # adapter: pools are shared by connection identity, so two sources on one
        # warehouse would otherwise both get whichever one built the pool.
        authored = (
            source_config.attribution
            if isinstance(source_config, AttributedSourceConfig)
            else {}
        )

        with attribute(scope, authored):
            if is_sql_query(query):
                if source_config is None:
                    return handle_adapter_error(
                        "Source resolution", self._source_not_named_error()
                    )
                return self._execute_on_backend(
                    query, source_config, variables, params, board, None
                )
            return self._execute_non_sql(query, variables, params)

    def _execute_non_sql(
        self, query: AnyQuery, variables: VariableValues | None, params: QueryParams
    ) -> QueryResult:
        """Run a ``values`` or ``schema`` query, observed like a backend call."""
        adapter = self._values if is_values_query(query) else self._schema
        status = "error"
        start = time.perf_counter()
        try:
            result = adapter.execute(query, variables, params)
            status = "success" if result.error is None else "error"
            return result
        finally:
            notify_observers(self._observers, None, status, time.perf_counter() - start)

    def _compose(
        self,
        query: SqlQuery,
        variables: VariableValues | None,
        params: QueryParams,
        board: Board | None,
        source_config: ResolvedSourceConfig,
        render_dialect: SQLDialect,
    ) -> ResolvedQuery | QueryResult:
        try:
            semantic = self.semantic_layer(source_config)
        except DbtChartsError as exc:
            return handle_adapter_error("MetricFlow", exc)
        return compose(
            query,
            variables=variables,
            params=params,
            board=board,
            source_config=source_config,
            dialect_name=source_config.type,
            render_dialect=render_dialect,
            refs=self._dbt_refs,
            semantic=semantic,
        )

    def _execute_on_backend(
        self,
        query: SqlQuery,
        source_config: ResolvedSourceConfig,
        variables: VariableValues | None,
        params: QueryParams,
        board: Board | None,
        resolved: ResolvedQuery | None,
    ) -> QueryResult:
        """Run a SQL query on its source's backend, observed from lookup to rows.

        Composes the query unless the caller already did (*resolved*). Every
        failure becomes the query's error result, a backend's classified by
        :func:`classify_warehouse_error`.
        """
        dialect_name = source_config.type
        status = "error"
        start = time.perf_counter()
        try:
            try:
                backend = self._backends.for_source(source_config)
            except DbtChartsError as exc:
                return handle_adapter_error(f"{dialect_name} query setup", exc)
            if resolved is None:
                composed = self._compose(
                    query,
                    variables,
                    params,
                    board,
                    source_config,
                    backend.param_style,
                )
                if isinstance(composed, QueryResult):
                    return composed
                resolved = composed
            try:
                table = backend.execute(source_config, resolved)
            except QueryDurationExceeded as exc:
                return QueryResult(
                    data=[],
                    error=QueryError(
                        ERR_QUERY_DURATION_EXCEEDED.message_template.format(
                            seconds=exc.seconds, source=query.source or dialect_name
                        ),
                        code=ERR_QUERY_DURATION_EXCEEDED,
                    ),
                )
            except QueryError as exc:
                return QueryResult(data=[], error=exc)
            except DbtChartsError as exc:
                return handle_adapter_error(f"{dialect_name} query setup", exc)
            except ConnectionSetupFailed as exc:
                return connection_failure(dialect_name, exc.cause)
            except Exception as exc:  # noqa: BLE001 — the warehouse's verdict on the statement
                return classify_warehouse_error(
                    f"{dialect_name} SQL execution", exc, dialect_name
                )
            result = rows_to_query_result(
                table,
                resolved.limit,
                list(resolved.resolved_relations) or None,
            )
            status = "success"
            return result
        finally:
            notify_observers(
                self._observers, dialect_name, status, time.perf_counter() - start
            )

    def _execute_file_source(
        self,
        materializer: FileSourceMaterializer,
        query: SqlQuery,
        variables: VariableValues | None,
        params: QueryParams,
        source_config: CsvSourceConfig | JsonSourceConfig | ParquetSourceConfig,
        board: Board | None,
        query_name: str,
    ) -> QueryResult:
        """Run a composed file-source SqlQuery through the materializer.

        Called only after :func:`compose` — ``query.sql`` already has
        any ``{{ queries.X }}`` reference expanded, and *params* holds the
        bindings composition rendered.

        Applies the same ``query.limit`` / ``execution.max_rows`` contract
        ``DbtBackend`` and the other backends honor, but not the same mechanism:
        those bound the driver cursor itself, so an over-the-ceiling result
        is never fully fetched. ``materialize_and_run`` has no cursor to
        bound — it returns every row the materialized cache holds, and this
        method slices to ``row_fetch_limit`` afterward. ``truncated_reason``
        is set only when the ceiling (not the author's own limit) was the
        binding value.
        """
        assert query.source is not None  # narrowed by the caller
        row_fetch_limit = resolve_effective_row_limit(query.limit)
        status = "error"
        start = time.perf_counter()
        # No bindings is a valid state, not a caller bug.
        bound_variables = variables or {}  # type-state: silent_fallback — see above
        try:
            scope = {"query": query_name}
            if board is not None:
                scope["board"] = board.id
            authored = (
                source_config.attribution
                if isinstance(source_config, AttributedSourceConfig)
                else {}
            )
            with attribute(scope, authored):
                try:
                    rows, schema = materializer.materialize_and_run(
                        source_config,
                        query.sql,
                        bound_variables,
                        query.source,
                        params=params,
                        strict=not query.lenient_variables,
                    )
                except (DbtChartsError, RuntimeError, OSError, ValueError) as exc:
                    return handle_adapter_error("File-source execution", exc)
            rows, truncated_reason = apply_row_limit_truncation(rows, row_fetch_limit)
            status = "success"
            return QueryResult(
                data=rows,
                columns=list(schema),
                schema=schema,
                truncated_reason=truncated_reason,
            )
        finally:
            notify_observers(
                self._observers, source_config.type, status, time.perf_counter() - start
            )

    def translate_query(
        self,
        query: SqlQuery,
        *,
        board: Board,
        variables: VariableValues,
        source_config: ResolvedSourceConfig,
    ) -> SqlQuery | QueryResult:
        """The query as its source's engine would receive it, values inlined.

        For a caller that must put text of its own around the SQL, like a
        ``EXPLAIN`` prefix, and so cannot let ``execute`` translate afterwards.
        The query must carry a ``dialect:``; the result carries none. One
        already in its source's dialect comes back unchanged. A translation
        failure comes back as the error ``execute`` would have returned.
        """
        assert query.dialect is not None
        try:
            differs = dialect_differs(query.dialect, source_config)
        except DbtChartsError as exc:
            return handle_adapter_error("Dialect translation", exc)
        if not differs:
            return query
        if is_file_source(source_config):
            return _file_source_dialect_refusal(query.dialect)
        try:
            semantic = self.semantic_layer(source_config)
        except DbtChartsError as exc:
            return handle_adapter_error("MetricFlow", exc)
        resolved = compose(
            query,
            variables=variables,
            params=None,
            board=board,
            source_config=source_config,
            dialect_name=source_config.type,
            render_dialect=INLINE_PLACEHOLDERS,
            refs=self._dbt_refs,
            semantic=semantic,
        )
        if isinstance(resolved, QueryResult):
            return resolved
        sql = resolved.inline(engine_dialect(query.dialect, source_config))
        return query.model_copy(update={"sql": sql, "dialect": None})

    def semantic_layer(
        self, source_config: ResolvedSourceConfig
    ) -> (
        SemanticLayer | None
    ):  # type-state: optional — None is a source that declares no bundle
        """The source's MetricFlow bundle, loaded; None when it declares none.

        Reloaded when the bundle's files change, reused otherwise.

        Raises:
            ExecutionError: the library is not installed, or the bundle is
                invalid.
        """
        if (
            not isinstance(source_config, SemanticSourceConfig)
            or source_config.metricflow is None
        ):
            return None
        return SemanticLayer.load(self._project, source_config.metricflow)

    def compose_query(
        self,
        query: AnyQuery,
        *,
        board: Board,
        variables: VariableValues,
        source_config: ResolvedSourceConfig,
    ) -> ResolvedQuery | QueryResult:
        """The query ``execute`` would send, composed but not sent.

        Same composition ``execute`` runs, stopping at the connection, with
        params in the inline style. This is what a caller needs when the
        warehouse itself offers a way to inspect a query it never runs, such
        as a BigQuery dry run. The source is resolved by the caller (via
        :meth:`resolve_query_source`), which has to know it anyway to decide
        that a dry run is the right mechanism.
        """
        if not is_sql_query(query):
            return plain_error(f"Expected SQL query, got {query.query_type}")

        try:
            semantic = self.semantic_layer(source_config)
        except DbtChartsError as exc:
            return handle_adapter_error("MetricFlow", exc)
        return compose(
            query,
            variables=variables,
            params=None,
            board=board,
            source_config=source_config,
            dialect_name=source_config.type,
            render_dialect=INLINE_PLACEHOLDERS,
            refs=self._dbt_refs,
            semantic=semantic,
        )

    def render_with_range_filters(
        self, query: AnyQuery, variables: VariableValues, *, board: Board
    ) -> tuple[str, str | None, list[DateRangeFilter]] | None:
        """The SQL execution renders for a query, plus its ``filter_date_range()`` calls.

        Returns ``(sql, sqlglot dialect, filters)``, or None when there is no SQL to
        parse here: a non-SQL query, or a source whose type is no SQL dialect.

        Raises:
            DbtChartsError: the source does not resolve, dbt refs that don't
                resolve, or a template that doesn't render.
        """
        from dbt_charts.core.compile.template.parameterized import (
            render_parameterized_with_queries,
        )

        if not is_sql_query(query):
            return None
        sql_type = query.dialect or self._declared_warehouse_type(query, board)
        if sql_type is None:
            # An undeclared source: it defers to the dbt project, and
            # ``resolve_query_source`` owns that rule.
            resolved = self.resolve_query_source(query, board=board)
            assert resolved is not None  # SQL always resolves to a source
            sql_type = resolved.type
        if sql_type not in DIALECTS:
            return None
        warehouse = DIALECTS[sql_type]
        resolved_sql, _ = self._dbt_refs.resolve(query.sql)
        filters: list[DateRangeFilter] = []
        rendered = render_parameterized_with_queries(
            resolved_sql,
            variables,
            queries=board.queries,
            dialect=INLINE_PLACEHOLDERS,
            strict=not query.lenient_variables,
            warehouse=warehouse,
            range_filters=filters,
        )
        sql = inline_params_for_dialect(
            rendered.sql, rendered.params, INLINE_PLACEHOLDERS, escaping=warehouse
        )
        return sql, sqlglot_dialect(sql_type), filters

    def _declared_warehouse_type(self, query: SqlQuery, board: Board) -> str | None:
        """The warehouse type the source a SQL query runs against declares.

        A source-less query runs against the project's implicit dbt source. A
        ``dbt_profile`` source names its warehouse only in profiles.yml, which is
        read (never connected to); a file source has no dialect to name. None when
        the source is not declared, so a range proof never resolves a declared
        source (a remote host's DNS lookup, a policy guard).
        """
        source = self.effective_source(query.source)
        if source is None:
            return None
        declared = board.sources.get(source) or self.project_sources.sources.get(source)
        if declared is None:
            return None
        if declared.get("type") != "dbt_profile":
            return declared.get("type")
        expanded = self._resolver.resolve(
            declared, board.sources, self.project_sources, self._dbt_context
        )
        return None if expanded is None else expanded.type

    def execute_resolved(
        self,
        resolved: ResolvedQuery,
        query: AnyQuery,
        *,
        source_config: ResolvedSourceConfig,
    ) -> QueryResult:
        """Send a query from :meth:`compose_query` — possibly rewritten — to the warehouse.

        ``query`` only labels a duration error. Nothing is rendered or guarded again, so the
        caller owns what it substituted into ``resolved.sql``.
        """
        if not is_sql_query(query):
            return plain_error(f"Expected SQL query, got {query.query_type}")
        return self._execute_on_backend(
            query, source_config, None, None, None, resolved
        )

    def project_file_sources(
        self,
    ) -> dict[str, CsvSourceConfig | JsonSourceConfig | ParquetSourceConfig]:
        """Return parsed configs for project-level csv/json/parquet sources."""
        from dbt_charts.core.compile.models.source import (
            CsvSourceConfig,
            JsonSourceConfig,
            ParquetSourceConfig,
            parse_source_config,
        )

        result: dict[str, CsvSourceConfig | JsonSourceConfig | ParquetSourceConfig] = {}
        for name, raw in self._sources.all().items():
            if raw.get("type") not in FILE_SOURCE_TYPES:
                continue
            try:
                cfg = parse_source_config(raw)
            except ValueError:
                continue
            if isinstance(
                cfg, (CsvSourceConfig, JsonSourceConfig, ParquetSourceConfig)
            ):
                result[name] = cfg
        return result

    def list_sql_sources(self) -> list[dict[str, Any]]:
        """Return configured SQL sources from the project source registry."""
        sources: dict[str, dict[str, Any]] = {}
        for name, config in self._sources.all().items():
            source_type = str(config.get("type", "unknown"))
            source_info: dict[str, Any] = {"name": name, "type": source_type}
            db_path = config.get("path")
            if db_path:
                source_info["path"] = str(db_path)
            if source_type == "duckdb" and str(db_path) == ":memory:":
                source_info["in_memory"] = True
            sources[name] = source_info
        return [sources[name] for name in sorted(sources)]

    def _source_not_named_error(self) -> DbtChartsError:
        """Build (not raise) ERR-SOURCE-NOT-NAMED — the single message
        for "source is None and there is no default to fall back to". Shared
        by ``resolve_source_config(None)`` (raises it) and the sourceless-SQL
        guard in ``execute()`` (returns it as a QueryResult error) so there is
        exactly one place that builds this error.
        """
        return DbtChartsError.from_code(
            ERR_SOURCE_NOT_NAMED,
            available=[s["name"] for s in self.list_sql_sources()],
        )

    def dbt_manifest_version(self, target_path: str | None) -> str:
        """Version of the dbt manifest ``ref()`` / ``source()`` resolve against
        for *target_path*; ``""`` when none exists."""
        return self._dbt_refs.manifest_version(target_path)

    def database_file_version(
        self, source_config: DuckDBSourceConfig | SQLiteSourceConfig
    ) -> str:
        """Content-version token of a DuckDB/SQLite source's database file.

        ``mtime_ns:size`` of the file the adapter opens, plus that of its
        write-ahead log when one exists: a write not yet checkpointed lands only
        there. ``""`` (unversioned) for DuckDB ``:memory:``, for a DuckDB
        connection that can read beyond its file, and when no adapter here opens
        the source: a host that cannot open the file never probes the disk for
        it. Raises OSError when the file cannot be stat'ed.
        """
        from dbt_charts.core.execute.backends.duckdb_local import DuckDBBackend

        if source_config.path == ":memory:":
            return ""
        if not self._backends.serves(source_config):
            return ""
        backend = self._backends.for_source(source_config)
        if isinstance(source_config, DuckDBSourceConfig):
            if not isinstance(backend, DuckDBBackend):
                raise DbtChartsError.from_code(
                    ERR_ADAPTER_UNAVAILABLE,
                    reason=(
                        f"the {source_config.type!r} route is a "
                        f"{type(backend).__name__}, which cannot say whether the "
                        "database file is its only input."
                    ),
                )
            if not backend.reads_only_its_file(source_config):
                return ""
        is_duckdb = source_config.type == "duckdb"
        db_path = Path(source_config.path)
        if not db_path.is_absolute():
            if not isinstance(self._project, FilesystemProject):
                raise DbtChartsError.from_code(
                    ERR_ADAPTER_RELATIVE_PATH_NO_DATA_DIR,
                    adapter="DuckDB" if is_duckdb else "SQLite",
                    path=source_config.path,
                )
            db_path = self._project.root / db_path
        wal_path = db_path.with_name(db_path.name + (".wal" if is_duckdb else "-wal"))
        paths = [db_path, wal_path] if wal_path.exists() else [db_path]
        stats = [path.stat() for path in paths]
        return "|".join(f"{st.st_mtime_ns}:{st.st_size}" for st in stats)

    def resolve_query_source(
        self,
        query: AnyQuery,
        *,
        board: Board | None = None,
        dbt_context: DbtContext | None = None,
        query_name: str = AD_HOC_QUERY_NAME,
    ) -> ResolvedSourceConfig | None:
        """Resolve the source a query will actually execute against.

        ``resolve_source_config`` consults project ``sources:`` only. A source
        declared board-level, or reachable only through a dbt profile, is
        invisible to it — so anything that needs to know where a query will run
        (execution, the ``--warehouse`` check) must come through here instead.

        Returns None for a source-less non-SQL query (inline values,
        schema list-all): those have no connection and must not be gated on one.

        SQL that names no source runs against the project's implicit dbt source
        when it has one (``implicit_dbt_source``).

        SQL whose source name resolves to nothing, in a project with a dbt
        project and no ``sources:``, runs on the default source
        (:meth:`_default_sql_source`): never None.

        Raises:
            DbtChartsError: ERR-SOURCE-NOT-NAMED when SQL names no source and
                there is no implicit one to fall back to, or
                whatever the resolver raises for an unresolvable name.
        """
        project_sources = self.project_sources
        authored = getattr(query, "source", None)
        if authored is None:
            if not is_sql_query(query):
                return None
            authored = self.effective_source(None)
            if authored is None:
                raise self._source_not_named_error()
        board_sources = board.sources if board is not None else {}
        dbt_context = dbt_context if dbt_context is not None else self._dbt_context
        resolved = self._resolver.resolve(
            authored=authored,
            board_sources=board_sources,
            project_sources=project_sources,
            dbt_context=dbt_context,
            query_name=query_name,
        )
        if resolved is None and is_sql_query(query):
            return self._default_sql_source(
                query, board_sources, dbt_context, query_name
            )
        return resolved

    def _default_sql_source(
        self,
        query: SqlQuery,
        board_sources: dict[str, dict[str, Any]],  # type-state: explicit_any — raw
        dbt_context: DbtContext | None,
        query_name: str,
    ) -> ResolvedSourceConfig:
        """The source SQL runs on when its authored one defers to the dbt project.

        The one rule: SQL using dbt Jinja (``ref()``/``source()``) runs on the dbt
        target, resolved like a ``dbt_profile`` source; any other SQL runs on the
        project's local DuckDB warehouse. Nothing after resolution re-decides it.

        Raises:
            DbtChartsError: a ``dbt_profile`` / local warehouse resolution
                failure.
        """
        assert dbt_context is not None, (
            "a resolver deferred a SQL query's source with no dbt project linked"
        )
        if has_dbt_jinja(query.sql):
            resolved = self._resolver.resolve(
                authored=DbtProfileSourceConfig(type="dbt_profile"),
                board_sources=board_sources,
                project_sources=self.project_sources,
                dbt_context=dbt_context,
                query_name=query_name,
            )
            assert resolved is not None  # a source config always resolves
            return resolved
        return default_duckdb_source(dbt_context.dbt_project_path)

    def resolve_source_config(self, source: str | None = None) -> dict[str, Any]:
        """Resolve a full source_config dict for use with InspectConnection.

        Returns the warehouse-specific dict (type + credentials).

        Args:
            source: Named source profile. Every query must name a configured
                source; None falls back only to the project's implicit dbt
                source (``effective_source``).

        Returns:
            source_config dict with at least a 'type' key.

        Raises:
            DbtChartsError: ERR-SOURCE-NOT-NAMED if source is None and there
                is no implicit source;
                ERR-SOURCE-NOT-FOUND if the named source is missing but
                others are configured; ERR-SOURCE-NOT-FOUND-EMPTY if no
                sources are configured.
        """
        source = self.effective_source(source)
        if source is None:
            raise self._source_not_named_error()

        config = self._sources.get(source)
        if config and config.get("type"):
            return config

        available = [item["name"] for item in self.list_sql_sources()]
        advice = unknown_source_advice(self._project, source)
        if available:
            raise DbtChartsError.from_code(
                ERR_SOURCE_NOT_FOUND,
                query_name=AD_HOC_QUERY_NAME,
                source=source,
                available=available,
                advice=advice,
            )
        raise DbtChartsError.from_code(
            ERR_SOURCE_NOT_FOUND_EMPTY, source=source, advice=advice
        )
