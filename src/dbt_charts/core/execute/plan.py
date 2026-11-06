"""Plan stage: what a render's queries are, before any of them is looked up or run.

Stage: EXECUTE (plan)
Purpose: Turn query names plus caller variables into ``PlannedQuery``s: the
    merged variables, the lazily resolved source, the cached upstreams a query composes
    over and the persistent-cache key. Lookup reads the store and run executes under the
    key a plan carries, so the two cannot disagree on it.

    A ``{{ queries.X.cache }}`` query is planned together with its upstreams, so
    one lookup and one run orders them; nothing reads an upstream by running it
    mid-query.

Entry Points:
    - make_context(board, env, ...) -> ExecContext
    - plan(ctx, names, variables, use_cache) -> list[PlannedQuery]
    - cache_key(ctx, name, query, variables, resolve_source) -> CacheKey
"""

import hashlib
import json
import re
from collections.abc import Callable, Iterable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from functools import cache, partial
from pathlib import (
    Path,  # noqa: TID251 — names the DuckDB/SQLite files on disk
)
from typing import TYPE_CHECKING, Any

import yaml
from pydantic import ValidationError

from dbt_charts.cli.filesystem_project import (
    FilesystemProject,  # tach-ignore(core->cli: host-type guard; needs a non-cli signal on Project — deferred)
)
from dbt_charts.core.board_listings import resolve_listings
from dbt_charts.core.compile.models.board.normalized import Board, VariableValues
from dbt_charts.core.compile.models.query.normalized import (
    AnyQuery,
    SqlQuery,
    is_sql_query,
)
from dbt_charts.core.compile.models.source import (
    FILE_SOURCE_TYPES,
    CsvSourceConfig,
    DuckDBSourceConfig,
    JsonSourceConfig,
    ParquetSourceConfig,
    ResolvedSourceConfig,
    SQLiteSourceConfig,
    declared_metricflow,
    parse_source_config,
)
from dbt_charts.core.compile.sql_guard import sqlglot_dialect
from dbt_charts.core.compile.template.daterange import BoardClock, board_clock
from dbt_charts.core.compile.template.listings import ListingCall, TextListings
from dbt_charts.core.compile.template.parameterized import (
    DateRangeFilter,
    render_parameterized_with_queries,
)
from dbt_charts.core.compile.template.variables import (
    coerce_variable_values,
    parse_variable_json_strings,
    resolve_daterange_presets,
)
from dbt_charts.core.dbt_ref_check import named_source_target_path
from dbt_charts.core.diagnostics.base import DbtChartsError
from dbt_charts.core.diagnostics.codes_execute import (
    ERR_ADAPTER_RELATIVE_PATH_NO_DATA_DIR,
    ERR_ADAPTER_UNAVAILABLE,
    ERR_FILE_SOURCE_NOT_FOUND,
)
from dbt_charts.core.diagnostics.execution import ExecutionError, QueryError
from dbt_charts.core.dialects import DIALECTS
from dbt_charts.core.execute.backends.base import BackendRegistry
from dbt_charts.core.execute.dbt_jinja import has_dbt_jinja
from dbt_charts.core.execute.dbt_utils import source_target_path
from dbt_charts.core.execute.duckdb_cache import compute_cache_key
from dbt_charts.core.execute.metricflow import bundle_files, bundle_fingerprint
from dbt_charts.core.execute.plan_sources import (
    effective_source,
    resolve_query_source,
)
from dbt_charts.core.execute.query_result import handle_adapter_error
from dbt_charts.core.execute.sql_literals import (
    INLINE_PLACEHOLDERS,
    inline_params_for_dialect,
)
from dbt_charts.core.project import Project, is_glob

if TYPE_CHECKING:
    from dbt_charts.core.dbt_manifest import RawTableFormats
    from dbt_charts.core.execute.cache_backend import QueryResultCache
    from dbt_charts.core.execute.env import ExecEnv

CacheKey = tuple[str, str, str]

# The source resolution of one query, taken lazily so a memo or store hit never
# resolves (a resolver may refuse a source, and that refusal is the query's
# failure at execution, not a cache probe's).
SourceThunk = Callable[[], ResolvedSourceConfig | None]

FileSourceConfig = CsvSourceConfig | JsonSourceConfig | ParquetSourceConfig
KeyedSourceConfig = FileSourceConfig | DuckDBSourceConfig | SQLiteSourceConfig
KEYED_SOURCE_TYPES = (*FILE_SOURCE_TYPES, "duckdb", "sqlite")

# Matches {{ queries.NAME.cache }} in authored SQL. The \s* around the dot
# tolerates whitespace variants like {{ queries.A . cache }} for consistency
# with the dep-graph regex.
CACHE_REF_JINJA_RE: re.Pattern[str] = re.compile(
    r"\{\{\s*queries\.(\w+)\s*\.\s*cache\s*\}\}"
)


@dataclass
class PlanMemo:
    """Per-render memos. A source's version token is read once, so the key a query
    writes matches the one a later query in the same render reads even if a file's
    mtime changes mid-render."""

    source_versions: dict[str, str] = field(default_factory=dict)
    semantic_versions: dict[str, str] = field(default_factory=dict)
    manifest_versions: dict[str | None, str] = field(default_factory=dict)
    # Sizing and paint resolve the same text, so each listing is read once.
    text_listings: dict[tuple[str, frozenset[ListingCall]], TextListings] = field(
        default_factory=dict
    )
    range_filters: dict[
        tuple[str, str], tuple[str, str | None, list[DateRangeFilter]] | None
    ] = field(default_factory=dict)


@dataclass(frozen=True)
class ExecContext:
    """Everything a render's stages read. ``memo`` is the only mutable part, and
    ``dataclasses.replace`` shares it, so swapping the env keeps the memos."""

    board: Board
    env: "ExecEnv"
    query_registry: dict[str, AnyQuery]
    result_cache: "QueryResultCache | None"
    # Default *persistent-store* participation. False is no-store: nothing is
    # read from or written to ``result_cache``. The per-render memo is
    # unaffected: resolve and render must see identical rows.
    use_cache: bool
    # This render's only time source: pin it and the render's bytes stop
    # depending on when it ran.
    clock: Callable[[], datetime]
    rendered_at: datetime
    # The board's file-identity slug, recorded on every cache entry this render
    # writes. None for a board with no file.
    board_slug: str | None
    memo: PlanMemo = field(default_factory=PlanMemo)

    @property
    def all_queries(self) -> dict[str, AnyQuery]:
        return {**self.board.queries, **self.query_registry}


def utc_now() -> datetime:
    """The real clock, in UTC: ``make_context``'s default time source."""
    return datetime.now(UTC)


def make_context(
    board: Board,
    env: "ExecEnv",
    query_registry: dict[str, AnyQuery] | None = None,
    use_cache: bool = True,
    result_cache: "QueryResultCache | None" = None,
    clock: Callable[[], datetime] = utc_now,
    board_slug: str | None = None,
) -> ExecContext:
    """The context one render's stages share.

    Args:
        board: Compiled board.
        env: Build at the entry point with
            ``build_exec_env(FilesystemProject(root), ...)``; nothing
            invents one from cwd.
        query_registry: Queries of other files the board references. None: the
            board's own queries are the whole registry.
        use_cache: Default *persistent-store* participation. False is no-store:
            nothing is read from or written to ``result_cache``. The per-render
            memo is unaffected: resolve and render must see identical rows.
        result_cache: Persistent result cache (Suite context).
        clock: This render's only time source. Read once here for
            ``rendered_at`` and again as each query's rows come back; pin it and
            the render's bytes stop depending on when it ran.
        board_slug: The board's file-identity slug, recorded on every cache entry
            this render writes. None for a board with no file, whose entries carry
            the compiled id instead.
    """
    return ExecContext(
        board=board,
        env=env,
        query_registry=query_registry
        or {},  # type-state: silent_fallback — None: the board's own queries are the whole registry
        result_cache=result_cache,
        use_cache=use_cache,
        clock=clock,
        rendered_at=clock(),
        board_slug=board_slug,
    )


@dataclass(frozen=True)
class PlannedQuery:
    name: str
    query: AnyQuery
    # Board defaults merged under the caller's values.
    variables: dict[str, Any]  # type-state: explicit_any — variable values
    source: SourceThunk
    # The plan of each X of a ``{{ queries.X.cache }}`` query, by name: what run
    # reads from the memo, so a wave has already run them. Empty for any other.
    upstreams: dict[str, "PlannedQuery"]
    cache_key: CacheKey
    # False for a call-level no-store or a `cache: false` policy.
    use_store: bool
    # Whether a stored incremental hit may be refreshed by a tail. False for any
    # query reading a file source, whatever its route.
    tailable: bool


def merge_board_variables(
    board: Board, variables: VariableValues, clock: BoardClock
) -> VariableValues:
    """Merge a board's declared variables with caller overrides, coerced to type,
    each daterange preset dated on the board's ``clock``.

    Declared names first (so an undeclared-but-defaulted variable still binds),
    then board defaults, then the caller's values. Coercion is the runtime
    boundary: string inputs become their declared semantic type (date/number/
    bool) so the SQL layer binds typed literals. Execution and the
    ``--warehouse`` check share this so both see identical SQL.
    """
    variable_registry = (
        board.variable_registry  # type-state: silent_fallback — None: the board declares no variables
        or {}
    )
    all_variables: VariableValues = dict.fromkeys(variable_registry)
    all_variables.update(board.variable_defaults)
    merged = {**all_variables, **parse_variable_json_strings(variables)}
    return resolve_daterange_presets(
        coerce_variable_values(merged, variable_registry), variable_registry, clock
    )


def _query_name_for(query: AnyQuery, registry: dict[str, AnyQuery]) -> str | None:
    """Find the registry name for a query object, or None if not found."""
    for name, q in registry.items():
        if q is query:
            return name
    return None


def resolve_query_references(
    query: AnyQuery,
    *,
    all_queries: dict[str, AnyQuery],
    query_name: str | None = None,
) -> AnyQuery:
    """Resolve ``{{ queries.* }}`` references in SQL as text, leaving variables untouched.

    Resolves nested references in topological dependency order so that
    multi-level query composition (e.g. style -> calc -> base) works, and
    propagates ``setup_sql`` from referenced queries so their preambles run
    before the dependent query. Execution and the ``--warehouse`` check share
    this: a check that resolved only one hop would report a syntax error on the
    unrendered ``{{ queries.X }}`` of SQL that renders fine.

    Variables are NOT substituted here. They stay author-written so the
    parameterized render on the way to the warehouse is the single render over
    them — substituting here and rendering again would corrupt authored literals
    that look like Jinja templates.

    Args:
        query: Query to resolve.
        all_queries: Every query the reference could name — board and registry.
        query_name: Name of this query (for dependency-ordered lookup).
    """
    # Only SQL queries can have query references
    if not is_sql_query(query):
        return query

    # Collect setup_sql from the full dependency chain
    from dbt_charts.core.execute.setup_sql import collect_setup_sql

    setup_stmts = collect_setup_sql(
        # We need a name to look up, but we may be called with an unnamed query.
        # Pass the query's own name by searching all_queries; fallback to just
        # using the query's own setup_sql.
        _query_name_for(query, all_queries)
        or "__anonymous__",  # type-state: silent_fallback — an unnamed query still needs a key in the setup_sql walk
        {**all_queries, "__anonymous__": query},
    )
    merged_setup = "\n".join(setup_stmts) if setup_stmts else None

    # Only inline {{ queries.X }} refs expand here; {{ queries.X.cache }}
    # refs stay in place because the composer runs them over X's cached rows.
    if "{{ queries." not in query.sql:
        # No inline {{ queries.X }} refs to resolve, but may still need
        # propagated setup_sql.
        if merged_setup != query.setup_sql:
            return query.model_copy(update={"setup_sql": merged_setup})
        return query

    from dbt_charts.core.compile.template.jinja import expand_query_refs

    # References only. Variables stay author-written so the parameterized
    # render on the way to the warehouse is the single render over them.
    resolved_sql = expand_query_refs(
        query_name
        or "",  # type-state: silent_fallback — an unnamed query has no registry entry to expand
        all_queries,
    )

    updates: dict[str, Any] = {}  # type-state: explicit_any — model_copy field updates
    updates["sql"] = resolved_sql
    if merged_setup:
        updates["setup_sql"] = merged_setup
    return query.model_copy(update=updates)


def get_query(ctx: ExecContext, query_name: str) -> AnyQuery:
    """Look up a query by name: ``board.queries`` first, then the registry.

    Raises:
        ExecutionError: If query not found
    """
    if query_name in ctx.board.queries:
        return ctx.board.queries[query_name]
    if query_name in ctx.query_registry:
        return ctx.query_registry[query_name]
    raise ExecutionError(f"Query '{query_name}' not found")


def prepare(
    ctx: ExecContext, query_name: str, variables: VariableValues | None
) -> tuple[str, AnyQuery, dict[str, Any]]:  # type-state: explicit_any — variable values
    """Strip the ``queries.`` prefix, merge variables with board defaults, and
    look up the query: the one (query, variables) pair every stage derives, and
    so the one cache key.

    Raises:
        ExecutionError: If the query name is not found.
    """
    query_name = query_name.removeprefix("queries.")
    merged = merge_board_variables(
        ctx.board,
        variables or {},  # type-state: silent_fallback — None: no caller overrides
        ctx.board.clock(ctx.rendered_at),
    )
    return query_name, get_query(ctx, query_name), merged


def plan(
    ctx: ExecContext,
    names: Iterable[str],
    variables: VariableValues | None = None,
    use_cache: bool | None = None,
) -> list[PlannedQuery]:
    """Plan *names* (may carry the ``queries.`` prefix) under *variables*.

    ``use_cache`` overrides the context's store participation for this call; a
    query whose own policy is disabled never uses the store either way. A
    ``{{ queries.X.cache }}`` query carries its upstreams' plans.

    Raises:
        ExecutionError: A query is not found, or a file source's version cannot
            be read.
    """
    planned: dict[str, PlannedQuery] = {}
    with board_clock(ctx.board.clock(ctx.rendered_at)):
        for raw_name in names:
            name, query, merged = prepare(ctx, raw_name, variables)
            source = cache(partial(resolve_source, ctx, query, name))
            key = cache_key(ctx, name, query, merged, source)
            refs = (
                dict.fromkeys(CACHE_REF_JINJA_RE.findall(query.sql))
                if is_sql_query(query)
                else ()
            )
            planned[name] = PlannedQuery(
                name,
                query,
                merged,
                source,
                {up.name: up for up in plan(ctx, refs, variables, use_cache)},
                key,
                (ctx.use_cache if use_cache is None else use_cache)
                and query.cache.enabled,
                not isinstance(keyed_source(ctx, query, source), FileSourceConfig),
            )
    return list(planned.values())


def require_cache_refs(ctx: ExecContext, query_name: str) -> None:
    """Refuse a ``{{ queries.X.cache }}`` query the context cannot compose.

    Raises:
        ExecutionError: No result cache is attached, or it cannot serve refs.
    """
    if not ctx.result_cache:
        raise ExecutionError(
            f"Query '{query_name}' uses {{{{ queries.X.cache }}}} composition but "
            "no cache backend is configured. Attach a result cache (TrivialDuckDBCache "
            "or PostgresResultCache) to enable cross-source cache-ref queries."
        )
    if not ctx.result_cache.supports_cache_refs():
        raise ExecutionError(
            f"Query '{query_name}' uses {{{{ queries.X.cache }}}} composition but "
            "the attached cache backend does not support cache-ref execution."
        )


def resolve_source(
    ctx: ExecContext, query: AnyQuery, query_name: str
) -> ResolvedSourceConfig | None:
    """The one resolution of a query's source; everything downstream is handed it.

    Resolved on the SQL as it executes, ``{{ queries.X }}`` expanded: a source
    the author left unnamed is chosen by what the composed SQL contains.

    Raises:
        QueryError: The source cannot be resolved.
    """
    try:
        env = ctx.env
        return resolve_query_source(
            resolve_query_references(
                query, all_queries=ctx.all_queries, query_name=query_name
            ),
            sources=env.sources,
            resolver=env.resolver,
            dbt_context=env.dbt_context,
            board=ctx.board,
            query_name=query_name,
        )
    except DbtChartsError as exc:
        error = handle_adapter_error("Source resolution", exc).error
        assert error is not None
        error.fields.setdefault("query_name", query_name)
        raise error from exc


def cache_key(
    ctx: ExecContext,
    query_name: str,
    query: AnyQuery,
    variables: VariableValues | None,
    resolve_source_thunk: SourceThunk,
) -> CacheKey:
    """Cache key over the SQL as it executes, ``{{ queries.* }}`` expanded.

    Keying the authored text would let an edit to an upstream query leave
    every downstream entry serving old rows. A ``{{ queries.X.cache }}``
    reference is not inlined — the composing query runs over X's cached rows —
    so X's own key is folded in instead, recursively. Reference expansion
    rejects cycles first, which is what keeps that recursion finite.
    """
    all_queries = ctx.all_queries
    expanded = resolve_query_references(
        query, all_queries=all_queries, query_name=query_name
    )
    ref_names = (
        dict.fromkeys(CACHE_REF_JINJA_RE.findall(expanded.sql))
        if is_sql_query(expanded)
        else ()
    )
    upstream_keys = []
    for ref_name in ref_names:
        upstream = cache_ref_upstream(query_name, ref_name, all_queries)
        upstream_keys.append(
            cache_key(
                ctx,
                ref_name,
                upstream,
                variables,
                partial(resolve_source, ctx, upstream, ref_name),
            )
        )
    return compute_cache_key(
        expanded,
        variables,
        ctx.board.sources,
        source_version=source_version(ctx, query, resolve_source_thunk)
        + manifest_version(ctx, expanded, resolve_source_thunk)
        + semantic_version(ctx, query)
        + "".join(f"|{ref_key}" for ref_key in upstream_keys),
    )


def cache_ref_upstream(
    query_name: str, ref_name: str, all_queries: dict[str, AnyQuery]
) -> AnyQuery:
    """The query a ``{{ queries.X.cache }}`` reference names, if usable as one.

    Raises:
        ExecutionError: If it does not exist or has ``cache: false``.
    """
    upstream = all_queries.get(ref_name)
    if upstream is None:
        raise ExecutionError(
            f"Query '{query_name}' references {{{{ queries.{ref_name}.cache }}}} "
            f"but query '{ref_name}' does not exist in this board or registry."
        )
    if not upstream.cache.enabled:
        raise ExecutionError(
            f"Query '{query_name}' references {{{{ queries.{ref_name}.cache }}}} "
            f"but query '{ref_name}' has cache: false and cannot be "
            "used as a cache-ref upstream."
        )
    return upstream


def keyed_source(
    ctx: ExecContext, query: AnyQuery, resolve_source_thunk: SourceThunk
) -> KeyedSourceConfig | None:
    """The file-backed source a query reads, or None for any other.

    A name declared by both the board and the project is the resolver's call
    (a default resolver takes the board's entry, an allowlisting one the
    project's), so only then, and only if one is file-backed, is the source
    resolved: the key then versions the file the query runs on. A name declared
    once reads that declaration, never resolving, so a source the resolver
    refuses is the query's failure at execution, not a cache probe's.
    """
    if not is_sql_query(query) or not query.source:
        return None
    board_raw = ctx.board.sources.get(query.source)
    project_raw = ctx.env.sources.get(query.source)
    config: ResolvedSourceConfig | None
    if board_raw and project_raw:
        if not any(
            raw.get("type") in KEYED_SOURCE_TYPES for raw in (board_raw, project_raw)
        ):
            return None
        try:
            config = resolve_source_thunk()
        except Exception:  # noqa: BLE001 — a resolver may raise anything (SSRF guard, DNS)
            return None
    else:
        raw = board_raw or project_raw
        if raw is None or raw.get("type") not in KEYED_SOURCE_TYPES:
            return None
        try:
            config = parse_source_config(raw)
        except ValueError:
            return None
    if isinstance(config, KeyedSourceConfig):
        return config
    return None


def source_version(
    ctx: ExecContext, query: AnyQuery, resolve_source_thunk: SourceThunk
) -> str:
    """Content version of the files a query's source reads.

    The single content-version home threaded into every ``compute_cache_key``
    call for this query: the combined ``Project.file_version`` of a file
    source's files, or the database file version of a DuckDB/SQLite source.
    Returns ``""`` for warehouse sources (nothing to fold — their config
    identity is the whole key). Memoized per source name for the render's
    lifetime so a file edited mid-render cannot make a later read key differ
    from the earlier write key.
    """
    config = keyed_source(ctx, query, resolve_source_thunk)
    if not isinstance(config, FileSourceConfig):
        return database_file_version(ctx, query, config)
    file_source = config
    # keyed_source returns non-None only for a SqlQuery naming a source.
    assert is_sql_query(query) and query.source is not None
    source_name = query.source
    cached = ctx.memo.source_versions.get(source_name)
    if cached is not None:
        return cached
    project = ctx.env.project
    # Pre-hash each file's token to a fixed hex digest before joining, so the
    # combination is independent of any delimiter appearing inside a token
    # (the Project.file_version contract does not restrict the token alphabet).
    # Glob patterns must be expanded first — file_version on a literal glob
    # path would fail with FileNotFoundError on a local project.
    relpaths: list[str] = []
    for path_or_glob in file_source.files.values():
        if is_glob(path_or_glob):
            relpaths.extend(sorted(p.relpath for p in project.files.glob(path_or_glob)))
        else:
            relpaths.append(path_or_glob)
    parts = []
    for relpath in relpaths:
        try:
            token = project.file_version(relpath)
        except OSError as exc:
            # OSError, not FileNotFoundError: stat() raises
            # NotADirectoryError when a path component traverses through
            # an existing file (only absolute paths are rejected at
            # compile time — this shape compiles clean) and
            # PermissionError on an unreadable parent. Both must degrade
            # the same as a missing leaf, or they escape lookup's
            # ExecutionError guard and reproduce the exact 500 this code
            # exists to prevent. exc.strerror carries the specific OS
            # reason so a permission error doesn't read as "not found".
            raise QueryError.from_code(
                ERR_FILE_SOURCE_NOT_FOUND,
                source_name=source_name,
                relpath=relpath,
                detail=exc.strerror or str(exc),
            ) from None
        parts.append(hashlib.sha256(token.encode()).hexdigest())
    parts.sort()
    version = hashlib.sha256("|".join(parts).encode()).hexdigest()[:16]
    ctx.memo.source_versions[source_name] = version
    return version


def database_file_stat_version(
    backends: BackendRegistry,
    project: Project,
    source_config: DuckDBSourceConfig | SQLiteSourceConfig,
) -> str:
    """Content-version token of a DuckDB/SQLite source's database file.

    ``mtime_ns:size`` of the file the backend opens, plus that of its
    write-ahead log when one exists: a write not yet checkpointed lands only
    there. ``""`` (unversioned) for DuckDB ``:memory:``, for a DuckDB
    connection that can read beyond its file, and when no backend here opens
    the source: a host that cannot open the file never probes the disk for
    it. Raises OSError when the file cannot be stat'ed.
    """
    from dbt_charts.core.execute.backends.duckdb_local import DuckDBBackend  # noqa: PLC0415 — duckdb_local imports the adapters package, which imports this module

    if source_config.path == ":memory:":
        return ""
    if not backends.serves(source_config):
        return ""
    backend = backends.for_source(source_config)
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
        if not isinstance(project, FilesystemProject):
            raise DbtChartsError.from_code(
                ERR_ADAPTER_RELATIVE_PATH_NO_DATA_DIR,
                adapter="DuckDB" if is_duckdb else "SQLite",
                path=source_config.path,
            )
        db_path = project.root / db_path
    wal_path = db_path.with_name(db_path.name + (".wal" if is_duckdb else "-wal"))
    paths = [db_path, wal_path] if wal_path.exists() else [db_path]
    stats = [path.stat() for path in paths]
    return "|".join(f"{st.st_mtime_ns}:{st.st_size}" for st in stats)


def database_file_version(
    ctx: ExecContext,
    query: AnyQuery,
    config: KeyedSourceConfig | None,
) -> str:
    """``database_file_stat_version`` of a query on a DuckDB/SQLite
    source, memoized per source name like file-source versions. ``""`` for
    any other source, and for one whose config does not parse: execution
    raises that error itself, so nothing is cached under this key. *config*
    comes from ``keyed_source``, which resolves only a name both board and
    project declare.
    """
    # An incremental query finds its prior rows under an unchanged key and
    # fetches only the tail (append-only, as on a warehouse); a file version
    # would turn every append into a full re-query.
    if (
        not is_sql_query(query)
        or query.source is None
        or isinstance(query.incremental, str)
    ):
        return ""
    versions = ctx.memo.source_versions
    cached = versions.get(query.source)
    if cached is not None:
        return cached
    if not isinstance(config, DuckDBSourceConfig | SQLiteSourceConfig):
        versions[query.source] = ""
        return ""
    try:
        version = database_file_stat_version(ctx.env.backends, ctx.env.project, config)
    except OSError as exc:
        # A QueryError, so lookup reports a failure rather than raising.
        raise QueryError.from_code(
            ERR_FILE_SOURCE_NOT_FOUND,
            source_name=query.source,
            relpath=str(exc.filename),
            detail=exc.strerror or str(exc),
        ) from None
    except DbtChartsError as exc:
        raise QueryError(str(exc), code=exc.code) from None
    versions[query.source] = version
    return version


def semantic_version(ctx: ExecContext, query: AnyQuery) -> str:
    """Fingerprint of the MetricFlow bundle the query's source declares, so a
    changed bundle never replays rows, or an error, compiled from the old one.
    ``""`` for a source without one. Memoized per source name like
    ``source_version``, for the same read-key/write-key consistency.

    Reads the declared ``metricflow:`` rather than resolving the source, for
    the reason ``database_file_version`` gives, and hashes the files without
    loading the bundle: a declaration that does not parse, a bundle that
    fails to load, or a path that escapes the project fails at execution
    with its own error, so nothing is cached under this key.
    """
    if not is_sql_query(query) or query.source is None:
        return ""
    cached = ctx.memo.semantic_versions.get(query.source)
    if cached is not None:
        return cached
    raw = ctx.board.sources.get(
        query.source
    ) or ctx.env.sources.runtime_config().sources.get(query.source)
    try:
        bundle = declared_metricflow(raw) if raw is not None else None
    except ValidationError:
        bundle = None
    version = ""
    if bundle is not None:
        try:
            files = bundle_files(ctx.env.project, bundle.path)
        except ValueError:
            files = None
        if files is not None:
            version = f"|metricflow:{bundle_fingerprint(files)}"
            if bundle.required:
                version += ":required"
    ctx.memo.semantic_versions[query.source] = version
    return version


def manifest_version(
    ctx: ExecContext, query: AnyQuery, resolve_source_thunk: SourceThunk
) -> str:
    """Version of the dbt manifest this query's ``ref()`` / ``source()``
    resolve against, read from the resolver that resolves them; folded into
    the cache key beside ``source_version``. ``""`` for a query without dbt
    Jinja, or whose source cannot be resolved. Memoized per ``target_path``.
    """
    if not is_sql_query(query) or not (
        has_dbt_jinja(query.sql)
        or (query.setup_sql is not None and has_dbt_jinja(query.setup_sql))
    ):
        return ""
    versions = ctx.memo.manifest_versions
    try:
        target_path = source_target_path(resolve_source_thunk())
        if target_path not in versions:
            versions[target_path] = ctx.env.dbt_refs.manifest_version(target_path)
    except Exception:  # noqa: BLE001 — a resolver may raise anything (SSRF guard, DNS)
        # Execution re-raises the specific error as this query's failure; a
        # cache-key probe must not raise it.
        return ""
    return versions[target_path]


def render_with_range_filters(
    ctx: ExecContext, query_name: str, variables: VariableValues | None
) -> tuple[str, str | None, list[DateRangeFilter]] | None:
    """The SQL a query executes as, and the date ranges its filters recorded.

    None when the query has no ``filter_date_range()`` or can't be rendered
    here; it never fails a render.
    """
    query_name, query, merged = prepare(ctx, query_name, variables)
    memo_key = (query_name, json.dumps(merged, sort_keys=True, default=str))
    memo = ctx.memo.range_filters
    if memo_key not in memo:
        memo[memo_key] = _render_with_range_filters(ctx, query_name, query, merged)
    return memo[memo_key]


def _render_with_range_filters(
    ctx: ExecContext, query_name: str, query: AnyQuery, merged: VariableValues
) -> tuple[str, str | None, list[DateRangeFilter]] | None:
    if not is_sql_query(query) or CACHE_REF_JINJA_RE.search(query.sql):
        return None
    query = resolve_query_references(
        query, all_queries=ctx.all_queries, query_name=query_name
    )
    if not is_sql_query(query) or "filter_date_range" not in query.sql:
        return None
    env = ctx.env
    try:
        sql_type = query.dialect or _declared_warehouse_type(ctx, query)
        if sql_type is None:
            # An undeclared source: it defers to the dbt project, and
            # ``resolve_query_source`` owns that rule.
            resolved = resolve_query_source(
                query,
                sources=env.sources,
                resolver=env.resolver,
                dbt_context=env.dbt_context,
                board=ctx.board,
            )
            assert resolved is not None  # SQL always resolves to a source
            sql_type = resolved.type
        if sql_type not in DIALECTS:
            return None
        warehouse = DIALECTS[sql_type]
        resolved_sql, _ = env.dbt_refs.resolve(query.sql)
        filters: list[DateRangeFilter] = []
        rendered = render_parameterized_with_queries(
            resolved_sql,
            merged,
            queries=ctx.board.queries,
            dialect=INLINE_PLACEHOLDERS,
            strict=not query.lenient_variables,
            warehouse=warehouse,
            range_filters=filters,
        )
        sql = inline_params_for_dialect(
            rendered.sql, rendered.params, INLINE_PLACEHOLDERS, escaping=warehouse
        )
        return sql, sqlglot_dialect(sql_type), filters
    except (DbtChartsError, OSError, ValueError, yaml.YAMLError):
        return None


def _declared_warehouse_type(ctx: ExecContext, query: SqlQuery) -> str | None:
    """The warehouse type the source a SQL query runs against declares.

    A source-less query runs against the project's implicit dbt source. A
    ``dbt_profile`` source names its warehouse only in profiles.yml, which is
    read (never connected to); a file source has no dialect to name. None when
    the source is not declared, so a range proof never resolves a declared
    source (a remote host's DNS lookup, a policy guard).
    """
    env = ctx.env
    project_sources = env.sources.runtime_config()
    source = effective_source(env.sources, query.source)
    if source is None:
        return None
    declared = ctx.board.sources.get(source) or project_sources.sources.get(source)
    if declared is None:
        return None
    if declared.get("type") != "dbt_profile":
        return declared.get("type")
    expanded = env.resolver.resolve(
        declared, ctx.board.sources, project_sources, env.dbt_context
    )
    return None if expanded is None else expanded.type


def base_column_formats(ctx: ExecContext, source: str | None) -> "RawTableFormats":
    """``meta.format`` of the dbt columns behind the query source *source* names,
    from the manifest its ``dbt_profile`` source points at.

    Raises:
        DbtChartsError: The source's manifest cannot be read.
    """
    env = ctx.env
    target_path = named_source_target_path(
        source, ctx.board.sources, env.project.sources.sources
    )
    return env.dbt_refs.column_formats(target_path)


def text_listings(
    ctx: ExecContext, base_dir: str, calls: frozenset[ListingCall]
) -> TextListings:
    key = (base_dir, calls)
    if key not in ctx.memo.text_listings:
        directory = ctx.env.project.directory(base_dir)
        ctx.memo.text_listings[key] = resolve_listings(calls, directory)
    return ctx.memo.text_listings[key]
