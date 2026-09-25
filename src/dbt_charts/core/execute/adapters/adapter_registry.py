"""Registry for query adapters.

Stage: EXECUTE
Purpose: Manage and select appropriate adapters for query execution.

The AdapterRegistry maintains a list of available adapters and uses
type-based routing via the unified query interface.
"""

from __future__ import annotations

import threading
import time
from collections.abc import Callable
from typing import TYPE_CHECKING, Any, Final

from dbt_charts.cli.filesystem_project import (
    FilesystemProject,  # tach-ignore(core->cli: host-type guard; needs a non-cli signal on Project — deferred)
)
from dbt_charts.core.attribution import attribute
from dbt_charts.core.compile.config import ProjectSourcesConfig
from dbt_charts.core.compile.models.board.normalized import VariableValues
from dbt_charts.core.compile.models.query.normalized import (
    AnyQuery,
    SchemaQuery,
    SqlQuery,
    is_sql_query,
)
from dbt_charts.core.compile.models.source import (
    FILE_SOURCE_TYPES,
    AttributedSourceConfig,
    CsvSourceConfig,
    DuckDBSourceConfig,
    JsonSourceConfig,
    ParquetSourceConfig,
    is_file_source,
)
from dbt_charts.core.diagnostics.base import DbtChartsError

# ERR_SOURCE_NOT_FOUND is compile-owned (dropping the domain segment
# collided the compile- and execute-side codes; compile's message_template
# won).
from dbt_charts.core.diagnostics.codes_compile import ERR_SOURCE_NOT_FOUND
from dbt_charts.core.diagnostics.codes_execute import (
    ERR_NO_DEFAULT_SOURCE,
    ERR_SOURCE_NOT_FOUND_EMPTY,
)
from dbt_charts.core.dialects import SQLDialect, get_dialect
from dbt_charts.core.execute.adapters.base import (
    BaseAdapter,
    QueryParams,
    QueryResult,
    apply_row_limit_truncation,
    handle_adapter_error,
    plain_error,
    resolve_effective_row_limit,
)
from dbt_charts.core.execute.adapters.dbt_utils import DbtRefResolver
from dbt_charts.core.execute.observability import WarehouseObserver, notify_observers
from dbt_charts.core.execute.source_registry import SourceRegistry
from dbt_charts.core.execute.source_resolver import (
    AD_HOC_QUERY_NAME,
    DbtContext,
    DefaultSourceResolver,
)
from dbt_charts.core.execute.sql_literals import INLINE_PLACEHOLDERS
from dbt_charts.core.project import Project

if TYPE_CHECKING:
    from dbt_charts.core.compile.models.board.normalized import Board
    from dbt_charts.core.compile.models.source import ResolvedSourceConfig
    from dbt_charts.core.execute.adapters.sql_adapter import PreparedSql
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
LOCAL_AUTHORING_REGISTRY_KWARGS: dict[str, Any] = {
    "read_only": True,
    "allow_external_access_in_readonly": True,
    "duckdb_config": {"enable_external_access": True},
}


def build_adapter_registry(
    project: Project,
    *,
    read_only: bool = True,
    allowed_types: set[str] | None = None,
    profile_type: str = "duckdb",
    target: str = "dev",
    duckdb_config: dict[str, Any] | None = None,
    resolver: SourceResolver | None = None,
    allow_external_access_in_readonly: bool = False,
    max_workers: int | None = None,
    observers: list[WarehouseObserver] | None = None,
    file_materializer: FileSourceMaterializer | None = None,
    file_materializer_factory: Callable[[], FileSourceMaterializer] | None = None,
) -> AdapterRegistry:
    """Build an AdapterRegistry with standard adapters.

    This is the single canonical way to create an adapter registry.
    All entry points should use this instead of hand-wiring adapters.

    Args:
        project: The dbt charts project supplying source config. When it is a
            ``FilesystemProject``, DuckDBAdapter/SqliteAdapter also root
            relative paths against ``project.root``; any other host (Cloud's
            git-blob store, an in-memory test double) gets ``data_dir=None``
            — fine as long as no source references a relative file path.
            (Cloud actually builds its registry via
            ``build_cloud_adapter_registry``, not this function, but the
            fallback keeps this function itself host-agnostic.)
        read_only: Whether SQL adapters should be read-only (default True).
        allowed_types: If set, only register adapters whose supported_types
            intersect with this set. None means register all.
        profile_type: SQL dialect (default "duckdb").
        target: dbt target name (default: "dev").
        duckdb_config: Optional DuckDB config dict passed to the default
            connection. When read_only=True (the default) and
            allow_external_access_in_readonly=False, enable_external_access is
            hard-forced to False regardless of what this dict contains. To combine
            read_only=True with external file access (read_csv, httpfs, etc.), set
            both allow_external_access_in_readonly=True AND
            duckdb_config={"enable_external_access": True}. The default path
            (read_only=False) also allows external access without the flag.
        resolver: Source resolver instance. Defaults to DefaultSourceResolver when
            None. Pass an AllowlistedSourceResolver for deployed-mode enforcement.
        allow_external_access_in_readonly: Security opt-in; passed through to
            SqlAdapter. See SqlAdapter docstring for full semantics. Default False
            preserves existing behavior. Approved callsites passing True are listed
            on LOCAL_AUTHORING_REGISTRY_KWARGS in this module (local `dct
            playground`, MCP, the embedded preview, evals).
        max_workers: Width of the SqlAdapter per-source connection pool (warm
            warehouse worker threads). None falls back to the execution-config
            default. Serve passes its resolved max_workers so the persisted
            registry's pool matches the render-time parallelism.
        file_materializer: Pre-built materializer for CSV/JSON/Parquet file
            sources. A plain pass-through — this function invents no local
            default when it is None; the caller (ProjectSession, a host's own
            composition root) decides via
            ``file_source_materializer.resolve_local_file_materializer_factory``.
        file_materializer_factory: Lazy builder for the file-source
            materializer, used instead of ``file_materializer`` when it should
            be built only on first use. Exactly one of the two is normally
            set; passing neither leaves file-source queries refused by
            ``AdapterRegistry.execute``.
    """
    from dbt_charts.core.execute.adapters.dbt_adapter import DbtAdapter
    from dbt_charts.core.execute.adapters.duckdb_adapter import DuckDBAdapter
    from dbt_charts.core.execute.adapters.http_adapter import HttpAdapter
    from dbt_charts.core.execute.adapters.sql_adapter import SqlAdapter
    from dbt_charts.core.execute.adapters.sqlite_adapter import SqliteAdapter
    from dbt_charts.core.execute.adapters.values_adapter import ValuesAdapter

    registry = AdapterRegistry(
        project=project,
        project_sources=project.sources,
        resolver=resolver,
        observers=observers,
        file_materializer=file_materializer,
        file_materializer_factory=file_materializer_factory,
    )

    def _should_register(types: set[str]) -> bool:
        return allowed_types is None or bool(types & allowed_types)

    # Relative DuckDB/SQLite paths root against the project directory — only a
    # FilesystemProject has one. Any other host (Cloud's git-blob store, an
    # in-memory test double) passes data_dir=None; DuckDBAdapter/SqliteAdapter
    # already tolerate that (no relative-path resolution needed until a source
    # actually references one).
    data_dir = project.root if isinstance(project, FilesystemProject) else None

    # dbt_project.yml is valid at project.dbt_root (see resolve_dbt_project_dir
    # for how that's resolved) -- meaningful only for a real filesystem project
    # (dbt's own profiles/target resolution assumes a directory).
    resolved_dbt_path = (
        project.dbt_root
        if isinstance(project, FilesystemProject)
        and project.dbt_project.exists("dbt_project.yml")
        else None
    )

    # DbtAdapter first (handles SQL queries with dbt features, takes priority)
    if resolved_dbt_path is not None and _should_register({"sql"}):
        registry.register(
            DbtAdapter(
                project=project,
                dbt_project_path=resolved_dbt_path,
                target_name=target,
            )
        )

    # DuckDBAdapter second (handles all DuckDB execution; must precede SqlAdapter).
    # Its own source_config is fixed to :memory: — DuckDB remains a valid *named*
    # source type (dbt_charts.yml `sources: {name: {type: duckdb, ...}}`), but there
    # is no build-time default connection for a sourceless query to fall back to.
    if _should_register({"sql"}):
        registry.register(
            DuckDBAdapter(
                source_config=DuckDBSourceConfig(type="duckdb", path=":memory:"),
                data_dir=data_dir,
                read_only=read_only,
                duckdb_config=duckdb_config,
                allow_external_access_in_readonly=allow_external_access_in_readonly,
                dbt_project_path=str(resolved_dbt_path) if resolved_dbt_path else None,
                project=project,
            )
        )

    # SqlAdapter (dbt-warehouse path: postgres, snowflake, bigquery, etc.)
    if _should_register({"sql"}):
        registry.register(
            SqlAdapter(
                project=project,
                dbt_project_path=str(resolved_dbt_path) if resolved_dbt_path else None,
                profile_type=profile_type,
                max_workers=max_workers,
            )
        )

    # SqliteAdapter (filesystem-fd; local projects only)
    if _should_register({"sql"}):
        registry.register(SqliteAdapter(data_dir=data_dir, project=project))

    # Lazy adapters — zero cost until actually called
    if _should_register({"http"}):
        registry.register(HttpAdapter())

    if _should_register({"values"}):
        registry.register(ValuesAdapter())

    if _should_register({"schema"}):
        from dbt_charts.core.execute.adapters.schema_adapter import (
            SchemaAdapter,
        )

        registry.register(SchemaAdapter(registry))

    # When the execute path opts into external file access under read-only,
    # cold schema introspection must open DuckDB with the same config.
    if read_only and allow_external_access_in_readonly:
        registry.schema_introspection_duckdb_config = duckdb_config

    return registry


class AdapterRegistry:
    """Registry for managing and selecting query adapters.

    Adapters declare the query types they support (`supported_types`) and, via
    `can_execute(query, source_config)`, whether they own a query against its
    resolved source. The registry routes on both.

    Priority order: the first registered adapter whose
    `can_execute(query, source_config)` is True wins, so registration order
    breaks ties between eligible adapters (e.g. DbtAdapter is registered before
    DuckDBAdapter, so it claims source-less dbt-jinja SQL that both accept).

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
        """
        self._adapters: list[BaseAdapter] = []
        self._observers: list[WarehouseObserver] = list(observers) if observers else []
        self._type_index: dict[str, list[BaseAdapter]] = {}
        self._project = project
        self._dbt_refs = DbtRefResolver(project)
        self._sources = SourceRegistry(project_sources=project_sources)
        self._resolver: SourceResolver = (
            resolver if resolver is not None else DefaultSourceResolver()
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

    def register(self, adapter: BaseAdapter) -> None:
        """Register an adapter.

        Adapters are indexed by their supported types for fast lookup.

        Args:
            adapter: Adapter instance to register
        """
        self._adapters.append(adapter)

        # Update type index
        for query_type in adapter.supported_types:
            if query_type not in self._type_index:
                self._type_index[query_type] = []
            self._type_index[query_type].append(adapter)

    def close(self) -> None:
        """Close adapters that hold external resources (e.g. DuckDB file handles).

        Also closes the file-source materializer the registry itself built
        via ``file_materializer_factory``, if any — its cache backend may
        hold a DuckDB handle. An *injected* materializer (``file_materializer``
        at construction) is never closed here: the registry doesn't own it,
        the caller that built and handed it in does (Cloud shares one
        materializer across the registry and the session).
        """
        for adapter in self._adapters:
            close = getattr(adapter, "close", None)
            if close is not None:
                close()
        if self._built_file_materializer is not None:
            self._built_file_materializer.close()

    def _get_file_materializer(self) -> FileSourceMaterializer | None:
        """Return the registry's file-source materializer, building it lazily.

        Mirrors ``Executor._get_file_materializer``: an injected materializer
        is returned directly; a factory-backed one is built at most once,
        under a lock, so concurrent callers share a single instance. Returns
        None when neither was supplied — the registry has no file-source
        support wired.
        """
        if self._file_materializer is not None:
            return self._file_materializer
        if self._file_materializer_factory is None:
            return None
        with self._materializer_lock:
            if self._built_file_materializer is None:
                self._built_file_materializer = self._file_materializer_factory()
        return self._built_file_materializer

    def get_adapters_for_type(self, query_type: str) -> list[BaseAdapter]:
        """Get all adapters that support a given query type.

        Args:
            query_type: Query type string (e.g., "sql", "csv")

        Returns:
            List of adapters supporting this type (in registration order)
        """
        return self._type_index.get(query_type, [])

    def get_adapter(
        self, query: AnyQuery, source_config: ResolvedSourceConfig | None
    ) -> BaseAdapter | None:
        """Get the adapter that owns this query against its resolved source.

        Routes on query_type (fast path via the type index) then on the resolved
        source type via each adapter's ``can_execute``. First registered match
        wins, so registration order breaks ties between adapters eligible for the
        same query (e.g. DbtAdapter before DuckDBAdapter for source-less dbt-jinja
        SQL). Returns None when nothing claims it — a registry that omits an
        adapter (Cloud omits DuckDB/SQLite) closes that source's seam here.

        Args:
            query: AnyQuery object
            source_config: Typed source config resolved by SourceResolver, or None.

        Returns:
            Adapter instance or None if no adapter can execute the query
        """
        # Fast path: lookup by type
        candidates = self.get_adapters_for_type(query.query_type)

        for adapter in candidates:
            if adapter.can_execute(query, source_config):
                return adapter

        # Fallback: check all adapters (for adapters with custom can_execute)
        for adapter in self._adapters:
            if adapter not in candidates and adapter.can_execute(query, source_config):
                return adapter

        return None

    def register_source(self, name: str, config: dict[str, Any]) -> None:
        """Register a synthetic source. Idempotent: first registration wins."""
        self._sources.register(name, config)

    def _derive_dbt_context(self) -> DbtContext | None:
        from dbt_charts.core.execute.adapters.dbt_adapter import DbtAdapter

        for adapter in self._adapters:
            if isinstance(adapter, DbtAdapter):
                return DbtContext(dbt_project_path=adapter.dbt_project_path)
        return None

    def execute(
        self,
        query: AnyQuery,
        variables: VariableValues | None = None,
        params: QueryParams = None,
        *,
        board: Board | None = None,
        dbt_context: DbtContext | None = None,
        query_name: str = AD_HOC_QUERY_NAME,
    ) -> QueryResult:
        """Execute a query using the appropriate adapter.

        Args:
            query: AnyQuery object
            variables: Variable values for query resolution
            params: Optional pre-computed parameter values for parameterized execution.
                When provided, the adapter should use these params directly instead
                of re-processing variables. This is used by batch execution where
                parameterization happens before adapter execution.
            board: Compiled board; used to extract board-level sources for resolution.
            dbt_context: Explicit dbt context override. When None, derived from
                registered adapters.
            query_name: Author-facing name of the query, threaded to the source
                resolver for ERR-SOURCE-NOT-FOUND / ERR-SOURCE-INLINE-FORBIDDEN
                messages. Callers executing a synthetic query (schema
                introspection, bulk profiling, ad-hoc SQL) have none to give;
                the default documents that explicitly.

        Returns:
            QueryResult with data or error
        """
        try:
            source_config = self.resolve_query_source(
                query, board=board, dbt_context=dbt_context, query_name=query_name
            )
        except DbtChartsError as exc:
            # handle_adapter_error keeps the resolver's typed identity, so
            # ERR-SOURCE-NOT-FOUND et al. don't flatten to ERR-INTERNAL.
            return handle_adapter_error("Source resolution", exc)

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

            materializer = self._get_file_materializer()
            if materializer is None:
                return plain_error(
                    f"Source {query.source!r} is a {source_config.type} "
                    "file source, but no file-source materializer is "
                    "configured for this session. Pass one via "
                    "AdapterRegistry(file_materializer=...) or "
                    "ProjectSession(file_materializer=...) to run ad-hoc "
                    "queries against file sources."
                )

            assert isinstance(
                source_config, CsvSourceConfig | JsonSourceConfig | ParquetSourceConfig
            )
            # Same composition every adapter gets: {{ queries.X }} refs expand
            # and variables render before the materializer ever sees the SQL —
            # dispatching from any point above this would hand it an
            # unresolved template (materialize_and_run's own render step
            # substitutes plain variables but does not expand query refs).
            # "duckdb" is the placeholder style materialize_and_run's own
            # internal render would have used — composition here replaces
            # that render, not layers a second one on top of it.
            composed = self._compose_query_refs(
                query,
                variables,
                params,
                board=board,
                source_config=source_config,
                render_dialect=get_dialect("duckdb"),
            )
            if isinstance(composed, QueryResult):
                return composed
            query, params = composed
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

        # Single-pass routing on (query_type, resolved source type). source_config
        # is already resolved above, so each adapter's can_execute claims only the
        # source it owns — no concrete-class introspection here.
        adapter = self.get_adapter(query, source_config)

        if not adapter:
            if self._type_index.get(query.query_type):
                # Distinguished so the author does not debug a phantom missing
                # query type when it is the source that has no claimant.
                detail = (
                    f"this query's {source_config.type!r} source"
                    if source_config is not None
                    else "this query"
                )
                return plain_error(
                    f"No adapter for query type {query.query_type!r} "
                    f"supports {detail} in this deployment."
                )
            return plain_error(
                f"No adapter registered for query type {query.query_type!r} "
                "in this deployment."
            )

        # Composition: expand {{ queries.X }} references and parameterize
        # variables before dispatch. Placeholders are rendered in the style the
        # target adapter declares via param_render_dialect.
        composed = self._compose_query_refs(
            query,
            variables,
            params,
            board=board,
            source_config=source_config,
            render_dialect=(
                adapter.param_render_dialect(source_config)
                if source_config is not None
                else INLINE_PLACEHOLDERS
            ),
        )
        if isinstance(composed, QueryResult):
            return composed
        query, params = composed

        status = "error"
        start = time.perf_counter()
        try:
            # The board and query that caused this warehouse call. Established here
            # rather than authored, so it cannot drift when a board or query is
            # renamed. The surface is set further out, by whichever root opened the
            # session.
            scope = {"query": query_name}
            if board is not None:
                scope["board"] = board.id
            if is_sql_query(query) and query.target:
                scope["target"] = query.target
            # The source's own attribution rides this per-call scope, not the pooled
            # adapter: pools are shared by connection identity, so two sources on one
            # warehouse would otherwise both get whichever one built the pool.
            authored = (
                source_config.attribution
                if isinstance(source_config, AttributedSourceConfig)
                else {}
            )
            with attribute(scope, authored):
                result = adapter.execute(
                    query, variables, params, source_config=source_config
                )
            status = "success" if result.error is None else "error"
            return result
        finally:
            notify_observers(
                self._observers,
                getattr(adapter, "profile_type", None),
                status,
                time.perf_counter() - start,
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

        Called only after ``_compose_query_refs`` — ``query.sql`` already has
        any ``{{ queries.X }}`` reference expanded, and *params* is set
        whenever composition actually ran (a board with its own queries);
        otherwise *params* is None and the materializer renders *variables*
        itself, same as the render path.

        Applies the same ``query.limit`` / ``execution.max_rows`` contract
        ``SqlAdapter``/``DuckDBAdapter`` honor, but not the same mechanism:
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
        # No bindings is a valid state, not a caller bug — mirrors Executor's
        # own Step 4c call.
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
                    rows = materializer.materialize_and_run(
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
            return QueryResult(data=rows, truncated_reason=truncated_reason)
        finally:
            notify_observers(
                self._observers, source_config.type, status, time.perf_counter() - start
            )

    def _compose_query_refs(
        self,
        query: AnyQuery,
        variables: VariableValues | None,
        params: QueryParams,
        *,
        board: Board | None,
        source_config: ResolvedSourceConfig | None,
        render_dialect: SQLDialect,
    ) -> tuple[AnyQuery, QueryParams] | QueryResult:
        """Resolve dbt refs, expand ``{{ queries.X }}``, and parameterize variables.

        Composition lives here — one place every adapter passes through — so no
        individual adapter can omit it. dbt ref()/source() resolution runs
        first, since the variable-Jinja render below is StrictUndefined and
        doesn't know ``ref``. The adapter then receives pre-computed params and
        skips its own render step; its own ``DbtRefResolver.resolve()`` call is
        a no-op on *this* query's own SQL (already ref-free), but still does
        real work when a ``{{ queries.X }}`` sub-query's raw, unresolved SQL
        gets inlined here and reaches the adapter unresolved.
        ``render_dialect`` is the placeholder style the adapter declared via
        ``param_render_dialect``.
        """
        if not (
            params is None
            and is_sql_query(query)
            and board is not None
            and board.queries
            and source_config is not None
        ):
            return query, params

        from dbt_charts.core.compile.template.parameterized import (
            render_parameterized_with_queries,
        )

        try:
            resolved_sql, _resolved_relations = self._dbt_refs.resolve(query.sql)
        except DbtChartsError as exc:
            return handle_adapter_error("dbt ref resolution", exc)

        try:
            # Adapters that bind through a driver render in the warehouse's own
            # style, so that is the warehouse. The inline style is parsed by no
            # engine; there the source's type names the warehouse — a
            # `dbt_profile` source has already been expanded to its concrete
            # target type by the resolver.
            warehouse = (
                get_dialect(source_config.type)
                if render_dialect is INLINE_PLACEHOLDERS
                else render_dialect
            )
            rendered = render_parameterized_with_queries(
                resolved_sql,
                variables if variables is not None else {},
                queries=board.queries,
                dialect=render_dialect,
                strict=not query.lenient_variables,
                warehouse=warehouse,
            )
        except (ValueError, KeyError, TypeError) as exc:
            return plain_error(str(exc))
        return query.model_copy(update={"sql": rendered.sql}), list(rendered.params)

    def prepare_sql(
        self,
        query: AnyQuery,
        *,
        board: Board,
        variables: VariableValues,
        source_config: ResolvedSourceConfig,
    ) -> PreparedSql | QueryResult:
        """The exact SQL ``execute`` would put on the wire, without sending it.

        Same ``{{ queries.X }}`` composition and adapter-side rendering — it
        stops at the connection. This is what a caller needs when the warehouse
        itself offers a way to inspect a query it never runs, such as a BigQuery
        dry run. The source is resolved by the caller (via
        :meth:`resolve_query_source`), which has to know it anyway to decide
        that a dry run is the right mechanism.
        """
        composed = self._compose_query_refs(
            query,
            variables,
            None,
            board=board,
            source_config=source_config,
            render_dialect=INLINE_PLACEHOLDERS,
        )
        if isinstance(composed, QueryResult):
            return composed
        query, params = composed
        if not is_sql_query(query):
            return plain_error(f"Expected SQL query, got {query.query_type}")

        from dbt_charts.core.execute.adapters.sql_adapter import SqlAdapter

        # Narrowed to the adapter that owns wire SQL: DuckDB and SQLite drive
        # their own in-process client, so there is no prepared string to hand
        # back for them, and no caller that wants one.
        adapter = self.get_adapter(query, source_config)
        if not isinstance(adapter, SqlAdapter):
            return plain_error(
                f"{type(adapter).__name__} builds no wire SQL — "
                f"prepare_sql applies only to dbt-adapter sources."
            )
        return adapter.prepare_sql(
            query, variables=variables, params=params, source_config=source_config
        )

    @property
    def adapters(self) -> list[BaseAdapter]:
        """Get all registered adapters."""
        return list(self._adapters)

    @property
    def supported_types(self) -> set[str]:
        """Get all query types supported by registered adapters.

        Returns:
            Set of query type strings
        """
        return set(self._type_index.keys())

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

    def _no_default_source_error(self) -> DbtChartsError:
        """Build (not raise) ERR-NO-DEFAULT-SOURCE — the single message
        for "source is None and there is no default to fall back to". Shared
        by ``resolve_source_config(None)`` (raises it) and the sourceless-SQL
        guard in ``execute()`` (returns it as a QueryResult error) so there is
        exactly one place that builds this error.
        """
        return DbtChartsError.from_code(
            ERR_NO_DEFAULT_SOURCE,
            available=[s["name"] for s in self.list_sql_sources()],
        )

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

        Returns None for a source-less non-SQL query (inline values, http,
        schema list-all): those have no connection and must not be gated on one.

        Raises:
            DbtChartsError: ERR-NO-DEFAULT-SOURCE when SQL names no source
                (there is no build-time default connection to fall back to), or
                whatever the resolver raises for an unresolvable name.
        """
        authored = getattr(query, "source", None)
        if authored is None:
            if not is_sql_query(query):
                return None
            raise self._no_default_source_error()
        return self._resolver.resolve(
            authored=authored,
            board_sources=board.sources if board is not None else {},
            project_sources=self._sources.runtime_config(),
            dbt_context=(
                dbt_context if dbt_context is not None else self._derive_dbt_context()
            ),
            query_name=query_name,
        )

    def resolve_source_config(self, source: str | None = None) -> dict[str, Any]:
        """Resolve a full source_config dict for use with InspectConnection.

        Returns the warehouse-specific dict (type + credentials).

        Args:
            source: Named source profile. Every query must name a configured
                source — there is no default-DuckDB fallback for None.

        Returns:
            source_config dict with at least a 'type' key.

        Raises:
            DbtChartsError: ERR-NO-DEFAULT-SOURCE if source is None;
                ERR-SOURCE-NOT-FOUND if the named source is missing but
                others are configured; ERR-SOURCE-NOT-FOUND-EMPTY if no
                sources are configured.
        """
        if source is None:
            raise self._no_default_source_error()

        config = self._sources.get(source)
        if config and config.get("type"):
            return config

        available = [item["name"] for item in self.list_sql_sources()]
        if available:
            raise DbtChartsError.from_code(
                ERR_SOURCE_NOT_FOUND,
                query_name=AD_HOC_QUERY_NAME,
                source=source,
                available=available,
            )
        raise DbtChartsError.from_code(ERR_SOURCE_NOT_FOUND_EMPTY, source=source)
