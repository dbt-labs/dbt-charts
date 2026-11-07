"""Run stage: execute the lookup's misses and report one outcome per query.

Stage: EXECUTE (run)
Purpose: Take the ``Miss``es a lookup could not answer and return
    ``Mapping[name, Outcome]`` with ``Outcome = Rows | Failed``. How one miss
    executes is the caller's ``execute``; this module owns what surrounds it:
    the worker pool, the ``{{ queries.X.cache }}`` dependency waves, and the
    failure boundary, so no error is raised past ``run``.

    It also owns the observed call a miss makes: every route (a warehouse
    backend, a file source, ``values``, ``schema``) runs through ``run_route``,
    and ``run_query`` runs one ad-hoc query the same way.

Entry Points:
    - run(misses, execute, settle, max_workers) -> dict[str, Outcome]
    - run_route(env, route, query, ...) -> QueryResult
    - run_query(env, query, ...) -> QueryResult
    - execute_resolved(env, resolved, query, source_config) -> QueryResult
    - settle(ctx, ledger, miss, outcome) -> None
    - execute_miss(ctx, ledger, miss) -> Outcome
    - run_misses(ctx, ledger, found, max_workers) -> dict[str, Outcome]
"""

import contextvars
import logging
import re
import time
from collections.abc import Callable, Mapping, Sequence
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from datetime import UTC, datetime
from graphlib import TopologicalSorter
from typing import TYPE_CHECKING, Any, Literal

from dbt_charts.core.attribution import attribute
from dbt_charts.core.column_types import ColumnType, declared_literal_cells
from dbt_charts.core.compile.format import missing_format_columns
from dbt_charts.core.compile.models.board.normalized import Board, VariableValues
from dbt_charts.core.compile.models.primitives import Regional
from dbt_charts.core.compile.models.query.normalized import (
    AnyQuery,
    SchemaQuery,
    SqlQuery,
    ValuesQuery,
    is_schema_query,
    is_sql_query,
    is_values_query,
)
from dbt_charts.core.compile.models.source import (
    AttributedSourceConfig,
    CsvSourceConfig,
    JsonSourceConfig,
    ParquetSourceConfig,
    ResolvedSourceConfig,
    is_file_source,
)
from dbt_charts.core.compile.sql_guard import sqlglot_dialect
from dbt_charts.core.compile.template.daterange import (
    BoardClock,
    board_clock,
    board_clock_in_scope,
)
from dbt_charts.core.diagnostics.base import DbtChartsError
from dbt_charts.core.diagnostics.codes_execute import (
    ERR_QUERY_DURATION_EXCEEDED,
    ERR_QUERY_FORMAT_COLUMN_MISSING,
)
from dbt_charts.core.diagnostics.execution import ExecutionError, QueryError
from dbt_charts.core.dialects import get_dialect
from dbt_charts.core.execute.backends.dbt_adapter_factory import ConnectionSetupFailed
from dbt_charts.core.execute.cache_backend import (
    CacheRows,
    ResultRows,
    TruncationInfo,
)
from dbt_charts.core.execute.compose import (
    ResolvedQuery,
    compose,
    compose_sql,
    file_source_dialect_refusal,
)
from dbt_charts.core.execute.incremental import (
    incremental_safety_reason,
    run_incremental_tail,
)
from dbt_charts.core.execute.lookup import (
    Ledger,
    Lookup,
    Miss,
    cache_failure,
    cache_outcome,
    limit_rows,
    memo_key,
)
from dbt_charts.core.execute.observability import WarehouseObserver, notify_observers
from dbt_charts.core.execute.plan import (
    CACHE_REF_JINJA_RE,
    ExecContext,
    PlannedQuery,
    get_query,
    require_cache_refs,
    resolve_query_references,
)
from dbt_charts.core.execute.plan_sources import (
    resolve_query_source,
    source_not_named_error,
)
from dbt_charts.core.execute.query_result import (
    QueryDurationExceeded,
    QueryParams,
    QueryResult,
    ResolvedRelation,
    adapter_unavailable,
    apply_row_limit_truncation,
    classify_warehouse_error,
    connection_failure,
    handle_adapter_error,
    plain_error,
    resolve_effective_row_limit,
)
from dbt_charts.core.execute.result import rows_to_query_result
from dbt_charts.core.execute.schema_adapter import schema_rows
from dbt_charts.core.execute.source_resolver import AD_HOC_QUERY_NAME, DbtContext
from dbt_charts.core.execute.sql_translate import dialect_differs, dialect_unsupported

if TYPE_CHECKING:
    from dbt_charts.core.execute.env import ExecEnv

_logger = logging.getLogger(__name__)

# The sentinel `{{ queries.X.cache }}` renders to in a composing query's SQL.
_CACHE_REF_RE: re.Pattern[str] = re.compile(r"__dct_cache_ref__(\w+)__")

# Names the threads of the per-render query pool. A host that opens a
# thread-local resource per query (e.g. a per-thread DB connection) can key its
# cleanup on this prefix, so it reaps only pool workers and never the
# request/task thread that runs the synchronous cache-hit pre-pass.
RENDER_POOL_THREAD_PREFIX = "dct-render"


@dataclass(frozen=True)
class Rows:
    """A miss that ran and produced rows."""

    result: ResultRows
    # None when the rows extend a stored set whose age the lookup already recorded.
    data_as_of: datetime | None
    relations: Sequence[ResolvedRelation] = ()
    # Set when the ceilings (or the adapter) cut the rows.
    truncation: TruncationInfo | None = None


@dataclass(frozen=True)
class Failed:
    """A miss that raised. ``record`` is the failure as the store should keep it
    and the data ages count it: set for the query's own failures, None for a
    failure of the machinery around the query (an incremental tail, a cache-ref
    upstream), which is neither stored nor counted."""

    error: Exception
    record: Exception | None


Outcome = Rows | Failed


def settle(ctx: ExecContext, ledger: Ledger, miss: Miss, outcome: Outcome) -> None:
    """The one place an outcome reaches the memo, the persistent store and the
    data ages. Nothing in the execute path writes any of them.

    The memo is unconditional (the render's identity guarantee); the store is
    written only under the plan's ``use_store``: a disabled policy or a
    call-level ``use_cache=False`` writes nothing (no-store, not
    store-and-ignore).
    """
    name, query = miss.name, miss.query
    variables, use_store = miss.planned.variables, miss.planned.use_store
    if isinstance(outcome, Rows):
        ledger.memo[memo_key(query, miss.cache_key)] = outcome.result
        if outcome.relations:
            ledger.provenance[name] = list(outcome.relations)
        if outcome.truncation is not None:
            ledger.truncations[name] = outcome.truncation
        if use_store:
            truncation = ledger.truncations.get(name)
            cache_outcome(
                ctx,
                name,
                query,
                outcome.result,
                variables,
                miss.cache_key,
                truncated_reason=truncation.reason if truncation else None,
            )
        if outcome.data_as_of is not None:
            ledger.ages.append((name, outcome.data_as_of, query.cache, False))
    else:
        # A composing query in a later wave reads its upstream's failure here.
        ledger.errors[name] = outcome.error
        if outcome.record is not None:
            if use_store:
                cache_failure(
                    ctx, name, query, variables, miss.cache_key, outcome.record
                )
            ledger.ages.append((name, ctx.clock(), query.cache, False))


def _waves(misses: Sequence[Miss]) -> list[list[Miss]]:
    """Layer *misses* so a ``{{ queries.X.cache }}`` upstream runs and settles
    before its consumer. Upstreams that are not misses (served by lookup) gate
    nothing."""
    by_name = {miss.name: miss for miss in misses}
    graph: dict[str, set[str]] = {
        miss.name: (
            {
                ref
                for ref in CACHE_REF_JINJA_RE.findall(miss.query.sql)
                if ref in by_name
            }
            if is_sql_query(miss.query)
            else set()
        )
        for miss in misses
    }
    sorter = TopologicalSorter(graph)
    sorter.prepare()
    waves: list[list[Miss]] = []
    while sorter.is_active():
        ready = sorter.get_ready()
        waves.append([by_name[name] for name in ready])
        sorter.done(*ready)
    return waves


def run(
    misses: Sequence[Miss],
    execute: Callable[[Miss], Outcome],
    settle: Callable[[Miss, Outcome], None],
    max_workers: int | None = None,
) -> dict[str, Outcome]:
    """Execute *misses*; every one lands in the result as ``Rows`` or ``Failed``.

    ``execute`` runs one miss; whatever it raises becomes an uncacheable
    ``Failed``. ``settle`` is called once per miss, on the thread that ran it,
    as soon as its outcome is known, so a later wave (and the ledger's memo)
    sees an upstream's rows. With ``max_workers=None`` everything runs on the
    calling thread; otherwise each wave fans across a pool of that width.
    """
    outcomes: dict[str, Outcome] = {}

    def run_one(miss: Miss) -> None:
        try:
            outcome = execute(miss)
        except Exception as exc:  # noqa: BLE001 — a backend may raise anything
            outcome = Failed(exc, None)
        outcomes[miss.name] = outcome
        try:
            settle(miss, outcome)
        except Exception as exc:  # noqa: BLE001 — a store write may raise anything
            # The rows are already in hand and memoized; the store only saves a
            # later render the query, so its failure must not fail this one.
            _logger.warning("could not store query %r: %s", miss.name, exc)

    if not misses:
        return outcomes
    if max_workers is None:
        for wave in _waves(misses):
            for miss in wave:
                run_one(miss)
        return outcomes
    with ThreadPoolExecutor(
        max_workers=max_workers, thread_name_prefix=RENDER_POOL_THREAD_PREFIX
    ) as pool:
        for wave in _waves(misses):
            # `ContextVar` does not cross `submit` on its own — a copy per task,
            # so `attribute()`'s scope reaches every worker without the workers
            # stepping on each other's Context via concurrent `.run()`.
            futures = [
                pool.submit(contextvars.copy_context().run, run_one, miss)
                for miss in wave
            ]
            for future in futures:
                future.result()
    return outcomes


def check_formats(ctx: ExecContext, name: str, result: ResultRows) -> None:
    """Raise when the query's ``formats:`` names a column the rows lack."""
    if result.rows:
        for column in missing_format_columns(
            get_query(ctx, name).formats, result.rows[0]
        ):
            raise QueryError.from_code(
                ERR_QUERY_FORMAT_COLUMN_MISSING,
                query=name,
                column=column,
                columns=", ".join(sorted(result.rows[0])),
            )


def format_checked(ctx: ExecContext, name: str, outcome: Outcome) -> Outcome:
    if isinstance(outcome, Failed):
        return outcome
    try:
        check_formats(ctx, name, outcome.result)
    except QueryError as exc:
        return Failed(exc, None)
    return outcome


def require_translated_path(
    query: AnyQuery,
    source_config: ResolvedSourceConfig | None,
    query_name: str,
) -> None:
    """Refuse an incremental `dialect:` query that differs from the warehouse."""
    if not (
        is_sql_query(query)
        and query.dialect is not None
        and isinstance(query.incremental, str)
    ):
        return
    try:
        if source_config is not None and not dialect_differs(
            query.dialect, source_config
        ):
            return
        err = dialect_unsupported(
            query.dialect,
            "the source's warehouse",
            "incremental refresh wraps the SQL in warehouse SQL, so write it "
            "in the source's dialect",
        )
    except ExecutionError as exc:
        err = exc
    raise QueryError(str(err), query_name, code=err.code)


def execute_miss(ctx: ExecContext, ledger: Ledger, miss: Miss) -> Outcome:
    """Execute one miss: cache-ref composition, or a warehouse run (an
    incremental tail when the store held prior rows), else a failure. Writes
    no cache; ``settle`` does."""
    planned = miss.planned
    query_name, original_query = planned.name, planned.query
    variables, resolve_source = planned.variables, planned.source

    def failed(error: Exception) -> Failed:
        if isinstance(error, QueryError):
            return Failed(error, error)
        wrapped = QueryError(
            str(error),
            query_name,
            code=error.code if isinstance(error, DbtChartsError) else None,
        )
        wrapped.__cause__ = error
        return Failed(wrapped, error)

    # Cache-ref composition ({{ queries.X.cache }}) is detected on the
    # *authored* SQL, before reference resolution. The composing query runs
    # over the upstream rows in an isolated in-process DuckDB — never
    # string-interpolated onto the source adapter or an application-database
    # connection. It is incompatible with an incremental tail: the whole
    # composing query reruns over the cached upstream rows, so no watermark
    # predicate can be appended.
    if planned.upstreams:
        assert is_sql_query(original_query)
        composed, cache_ref_reason = _execute_cache_ref_query(
            ctx,
            ledger,
            original_query,
            planned.upstreams,
            variables,
            query_name,
        )
        limited, truncation = limit_rows(composed, query_name, cache_ref_reason)
        return Rows(limited, ctx.clock(), truncation=truncation)

    query = resolve_query_references(
        original_query, all_queries=ctx.all_queries, query_name=query_name
    )
    try:
        source_config = resolve_source()
        require_translated_path(query, source_config, query_name)
    except Exception as e:  # noqa: BLE001 — a resolver may raise anything (SSRF guard, DNS)
        return failed(e)

    # The route follows the source the resolver chose, not the plan's reading
    # of the declared sources: the two disagree when a board and the project
    # declare one name as different kinds.
    route = route_for(query, source_config)
    tail = miss.tail
    if tail is not None and tail.watermark is not None:
        # The lookup built a tail only for an SQL query;
        # resolve_query_references preserves the subtype.
        assert is_sql_query(query)
        # The dialect the adapter will actually execute against — the safety
        # predicate and the tail SQL must agree with it, or a
        # dialect-specific LIMIT spelling (T-SQL TOP) slips the safety guard
        # and a non-ANSI identifier quote gets silently misread.
        dialect = None if source_config is None else sqlglot_dialect(source_config.type)
        reason = incremental_safety_reason(query.sql, dialect)
        if reason is None:
            limited, truncation = run_incremental_tail(
                query_name,
                query,
                tail,
                dialect,
                lambda tail_query: run_route(
                    ctx.env,
                    route,
                    tail_query,
                    variables,
                    None,
                    ctx.board,
                    query_name,
                    source_config,
                ),
            )
            return Rows(limited, None, truncation=truncation)
        _logger.debug(
            "%s: SQL contains %s — ineligible for incremental tail; full refresh",
            query_name,
            reason,
        )

    try:
        result = run_route(
            ctx.env,
            route,
            query,
            variables,
            None,
            ctx.board,
            query_name,
            source_config,
        )
    except Exception as e:  # noqa: BLE001 — an adapter may raise anything
        return failed(e)
    if not result.is_success:
        assert result.error is not None
        result.error.fields.setdefault("query_name", query_name)
        return Failed(result.error, result.error)

    data_as_of = ctx.clock()
    limited, truncation = limit_rows(
        ResultRows(result.data, result.schema), query_name, result.truncated_reason
    )
    return Rows(limited, data_as_of, result.resolved_relations, truncation)


def run_and_settle(
    ctx: ExecContext,
    ledger: Ledger,
    misses: Sequence[Miss],
    max_workers: int | None,
) -> dict[str, Outcome]:
    """Run *misses* and settle each outcome. A miss carries the plan that keyed
    it, so its source is resolved once and its key is the one lookup read."""

    def execute(miss: Miss) -> Outcome:
        with board_clock(ctx.board.clock(ctx.rendered_at)):
            return execute_miss(ctx, ledger, miss)

    return run(
        misses,
        execute,
        lambda miss, outcome: settle(ctx, ledger, miss, outcome),
        max_workers,
    )


def run_misses(
    ctx: ExecContext, ledger: Ledger, found: Lookup, max_workers: int | None
) -> dict[str, Outcome]:
    """Run what *found* could not answer, in a pool (on the calling thread when
    *max_workers* is None).

    Every miss runs, and every failure is replayed: both land in the error
    memo, so a later read raises the stored error instead of re-executing. A
    failure never blocks its siblings.

    Returns:
        One outcome per missed or failed query: ``Rows``, or ``Failed`` for a
        replayed failure, a failed run or a ``formats:`` column the rows lack.
    """
    replayed = {
        name: Failed(failure.error, None) for name, failure in found.failures.items()
    }
    ledger.errors.update({name: failed.error for name, failed in replayed.items()})
    results: dict[str, Outcome] = {**replayed}
    for name, outcome in run_and_settle(ctx, ledger, found.misses, max_workers).items():
        results[name] = format_checked(ctx, name, outcome)
    for name, outcome in results.items():
        if isinstance(outcome, Failed):
            _logger.debug(
                "Parallel execution of query '%s' failed: %s", name, outcome.error
            )
            ledger.errors[name] = outcome.error
    return results


def _settled_rows(ledger: Ledger, upstream: PlannedQuery) -> ResultRows:
    """The rows an upstream's wave left in the memo; raises its error instead."""
    rows = ledger.memo.get(memo_key(upstream.query, upstream.cache_key))
    if rows is not None:
        return rows
    if upstream.name not in ledger.errors:
        raise ExecutionError(
            f"Upstream query '{upstream.name}' was not run before its composing query"
        )
    raise ledger.errors[upstream.name]


def _execute_cache_ref_query(
    ctx: ExecContext,
    ledger: Ledger,
    query: AnyQuery,
    upstreams: Mapping[str, PlannedQuery],
    variables: dict[str, Any],  # type-state: explicit_any — variable values
    query_name: str,
) -> tuple[ResultRows, Literal["max_rows"] | None]:
    """Execute a ``{{ queries.X.cache }}`` composing query in an isolated engine.

    For each referenced upstream query NAME:
      1. Validate it exists and is cacheable.
      2. Read its rows. The plan carries every upstream and a wave runs them
         first, so their rows (or their failure) are already in the ledger.
    Then render the composing SQL — inline ``{{ queries.X }}`` refs resolved,
    runtime variables **parameterized** (not string-interpolated) — and run
    it over the upstream rows in a fresh in-process DuckDB whose namespace
    contains ONLY those rows (no application tables, no other tenant's cache,
    nothing on a shared/application connection). The composing SQL's own
    fetch is bounded the same way the four adapters bound theirs — a
    driver-level ``fetchmany()``, not a rewrite of the composing SQL — so a
    join across two upstream tables each near the row ceiling can't blow up
    memory during composition, before any post-fetch check runs.

    Args:
        query: The composing SqlQuery, authored SQL (``{{ queries.X.cache }}``
            and ``{{ variable }}`` templates still present).
        upstreams: The plan of each referenced X.
        variables: Merged variable values.
        query_name: Name of the composing query (for error messages).

    Returns:
        The isolated composition engine's rows with their declared schema, and "max_rows" when the
        composed result came back over the ceiling (rows already sliced to
        it) — mirroring how adapters signal via QueryResult.truncated_reason.

    Raises:
        ExecutionError: No result cache is attached, or it cannot serve refs.
    """
    require_cache_refs(ctx, query_name)
    assert is_sql_query(query)
    sql = query.sql

    all_queries = ctx.all_queries
    ref_names = list(
        dict.fromkeys(CACHE_REF_JINJA_RE.findall(sql))
    )  # preserve order, dedupe

    # name → upstream rows, registered as isolated-DuckDB tables below; the
    # declared schema beside them, so an upstream with no row still gets a
    # table of the right columns.
    named_rows: dict[str, CacheRows] = {}
    schemas: dict[str, dict[str, ColumnType]] = {}

    for ref_name in ref_names:
        upstream = _settled_rows(ledger, upstreams[ref_name])
        check_formats(ctx, ref_name, upstream)
        named_rows[ref_name] = upstream.rows
        schemas[ref_name] = upstream.schema

    # Render inline {{ queries.X }} refs and parameterize {{ variable }}
    # values ($1/$2/…). {{ queries.X.cache }} renders to its sentinel via
    # the query namespace; we then swap each sentinel for the bare upstream
    # name so it resolves against the registered rows (matching the inline
    # `(sql) AS name` shape).
    from dbt_charts.core.compile.template.parameterized import (
        render_parameterized_with_queries,
    )
    from dbt_charts.core.execute.cache_composition import compose_over_named_rows

    rendered = render_parameterized_with_queries(
        sql, variables, queries=all_queries, profile_type="duckdb"
    )
    composed_sql = _CACHE_REF_RE.sub(lambda m: m.group(1), rendered.sql)

    # Bounds the composition engine's own fetch (fetchmany()) — composed_sql
    # runs unmodified, so an author's own inline LIMIT in the composing SQL
    # behaves exactly as it would without this ceiling.
    row_fetch_limit = resolve_effective_row_limit(query.limit)
    rows, schema = compose_over_named_rows(
        composed_sql,
        named_rows,
        rendered.params,
        limit=row_fetch_limit.fetch_limit,
        schemas=schemas,
        time_zone=ctx.board.regional.time_zone,
    )
    rows, truncated_reason = apply_row_limit_truncation(rows, row_fetch_limit)
    return ResultRows(rows, schema), truncated_reason


class Unresolved:
    """Type of ``UNRESOLVED``: ``None`` is a valid resolved source, so it cannot
    also mean "not resolved yet"."""


UNRESOLVED = Unresolved()


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


def run_query(
    env: "ExecEnv",
    query: AnyQuery,
    variables: VariableValues | None = None,
    params: QueryParams = None,
    *,
    board: Board | None = None,
    dbt_context: DbtContext | None = None,
    query_name: str = AD_HOC_QUERY_NAME,
    source_config: ResolvedSourceConfig | None | Unresolved = UNRESOLVED,
) -> QueryResult:
    """Run one query that no render planned: resolve its source, pick its route
    and run it, observed.

    A render scopes the board clock a preset literal in the SQL dates on;
    called outside one, this scopes the board's clock, or for an ad-hoc query
    the UTC date with the default theme's fiscal year and week.

    Args:
        query: The query.
        variables: Variable values for query resolution.
        params: Pre-computed bindings, used as they are instead of rendering the
            SQL's variables again.
        board: Compiled board; supplies board-level sources and the
            ``{{ queries.X }}`` registry.
        dbt_context: Explicit dbt context override. None: the linked dbt project.
        query_name: Author-facing name of the query, for the source resolver's
            ERR-SOURCE-NOT-FOUND / ERR-SOURCE-INLINE-FORBIDDEN messages. A
            synthetic query (bulk profiling, ad-hoc SQL) has none to give; the
            default documents that explicitly.
        source_config: The query's already-resolved source (None is a
            legitimate resolution: a source-less non-SQL query). UNRESOLVED
            resolves it here.
    """
    if isinstance(source_config, Unresolved):
        try:
            source_config = resolve_query_source(
                query,
                sources=env.sources,
                resolver=env.resolver,
                dbt_context=(
                    dbt_context if dbt_context is not None else env.dbt_context
                ),
                board=board,
                query_name=query_name,
            )
        except DbtChartsError as exc:
            # handle_adapter_error keeps the resolver's typed identity, so
            # ERR-SOURCE-NOT-FOUND et al. don't flatten to ERR-INTERNAL.
            return handle_adapter_error("Source resolution", exc)
    route = route_for(query, source_config)
    if board_clock_in_scope():
        return run_route(
            env, route, query, variables, params, board, query_name, source_config
        )
    now = datetime.now(UTC)
    with board_clock(board.clock(now) if board else ad_hoc_board_clock(now)):
        return run_route(
            env, route, query, variables, params, board, query_name, source_config
        )


@dataclass(frozen=True)
class FileSource:
    """A csv/json/parquet source, read through the file materializer."""


@dataclass(frozen=True)
class Values:
    """A ``values`` query: its rows are in the query."""


@dataclass(frozen=True)
class Schema:
    """A ``schema`` query: answered in process from the source's metadata."""


@dataclass(frozen=True)
class Warehouse:
    """Everything else: the source's own backend runs the SQL."""


Route = FileSource | Values | Schema | Warehouse


def route_for(query: AnyQuery, source_config: ResolvedSourceConfig | None) -> Route:
    """The route of a query that composes no cached upstream, by its resolved source."""
    if is_values_query(query):
        return Values()
    if is_schema_query(query):
        return Schema()
    if source_config is not None and is_file_source(source_config):
        return FileSource()
    return Warehouse()


def run_route(
    env: "ExecEnv",
    route: Route,
    query: AnyQuery,
    variables: VariableValues | None,
    params: QueryParams,
    board: Board | None,
    query_name: str,
    source_config: ResolvedSourceConfig | None,
) -> QueryResult:
    """Run a query down its route, in the attribution scope of its board, query
    and source."""
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
        match route:
            case Values():
                assert isinstance(query, ValuesQuery)
                return _observed(env.observers, None, lambda: run_values(query))
            case Schema():
                assert isinstance(query, SchemaQuery)
                if source_config is not None and is_file_source(source_config):
                    # Schema introspection of a file source (the /data explorer,
                    # or an authored `type: schema` query) is not supported yet.
                    return plain_error(
                        f"Source {query.source!r} is a {source_config.type} file "
                        "source. Schema introspection (the /data explorer, or "
                        "an authored `type: schema` query) is not yet "
                        "supported for file sources."
                    )
                return _observed(env.observers, None, lambda: schema_rows(env, query))
            case FileSource() | Warehouse():
                assert isinstance(query, SqlQuery)
                if source_config is None:
                    return handle_adapter_error(
                        "Source resolution", source_not_named_error(env.sources)
                    )
                if isinstance(route, FileSource):
                    return _run_file_source(
                        env, query, variables, params, board, source_config
                    )
                return execute_on_backend(
                    env, query, source_config, variables, params, board, None
                )


def _observed(
    observers: list[WarehouseObserver],
    warehouse_type: str | None,
    call: Callable[[], QueryResult],
) -> QueryResult:
    """*call*, reported to the observers with its status and duration."""
    status = "error"
    start = time.perf_counter()
    try:
        result = call()
        status = "success" if result.error is None else "error"
        return result
    finally:
        notify_observers(observers, warehouse_type, status, time.perf_counter() - start)


def run_values(query: ValuesQuery) -> QueryResult:
    schema = query.declared_schema()
    return QueryResult(
        data=query.apply_limit(declared_literal_cells(query.rows, schema)),
        columns=list(schema),
        schema=schema,
    )


def execute_on_backend(
    env: "ExecEnv",
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

    def call() -> QueryResult:
        nonlocal resolved
        try:
            backend = env.backends.for_source(source_config)
        except DbtChartsError as exc:
            return handle_adapter_error(f"{dialect_name} query setup", exc)
        if resolved is None:
            composed = compose_sql(
                query,
                variables,
                params,
                board,
                source_config,
                backend.param_style,
                refs=env.dbt_refs,
                project=env.project,
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
        return rows_to_query_result(
            table, resolved.limit, list(resolved.resolved_relations) or None
        )

    return _observed(env.observers, dialect_name, call)


def execute_resolved(
    env: "ExecEnv",
    resolved: ResolvedQuery,
    query: AnyQuery,
    *,
    source_config: ResolvedSourceConfig,
) -> QueryResult:
    """Send a query from ``compose_query`` — possibly rewritten — to the warehouse.

    ``query`` only labels a duration error. Nothing is rendered or guarded again, so the
    caller owns what it substituted into ``resolved.sql``.
    """
    if not is_sql_query(query):
        return plain_error(f"Expected SQL query, got {query.query_type}")
    return execute_on_backend(env, query, source_config, None, None, None, resolved)


def _run_file_source(
    env: "ExecEnv",
    query: SqlQuery,
    variables: VariableValues | None,
    params: QueryParams,
    board: Board | None,
    source_config: ResolvedSourceConfig,
) -> QueryResult:
    """Run a SQL query on a csv/json/parquet source through the materializer.

    Applies the same ``query.limit`` / ``execution.max_rows`` contract
    the backends honor, but not the same mechanism: they bound the driver cursor
    itself, so an over-the-ceiling result is never fully fetched.
    ``materialize_and_run`` has no cursor to bound — it returns every row the
    materialized cache holds, and this slices to ``row_fetch_limit`` afterward.
    ``truncated_reason`` is set only when the ceiling (not the author's own
    limit) was the binding value.
    """
    assert isinstance(
        source_config, CsvSourceConfig | JsonSourceConfig | ParquetSourceConfig
    )
    source_name = query.source
    assert source_name is not None  # a file source is reached through its name
    if query.dialect is not None and dialect_differs(query.dialect, source_config):
        return file_source_dialect_refusal(query.dialect)
    materializer = env.materializer.get()
    if materializer is None:
        return adapter_unavailable(
            f"Source {query.source!r} is a {source_config.type} "
            "file source, and this session has no file-source "
            "materializer."
        )
    # Same composition every backend gets: {{ queries.X }} refs expand and
    # variables render before the materializer ever sees the SQL. "duckdb" is
    # the placeholder style materialize_and_run's own render would have used.
    try:
        composed = compose(
            query,
            variables=variables,
            params=params,
            board=board,
            source_config=source_config,
            dialect_name="duckdb",
            render_dialect=get_dialect("duckdb"),
            refs=env.dbt_refs,
            semantic=None,
        )
    except DbtChartsError as exc:
        return handle_adapter_error("File-source execution", exc)
    if isinstance(composed, QueryResult):
        return composed
    row_fetch_limit = resolve_effective_row_limit(query.limit)
    # No bindings is a valid state, not a caller bug.
    bound_variables = variables or {}  # type-state: silent_fallback — see above

    def call() -> QueryResult:
        try:
            rows, schema = materializer.materialize_and_run(
                source_config,
                composed.sql,
                bound_variables,
                source_name,
                composed.time_zone,
                params=list(composed.params),
                strict=not query.lenient_variables,
            )
        except (DbtChartsError, RuntimeError, OSError, ValueError) as exc:
            return handle_adapter_error("File-source execution", exc)
        rows, truncated_reason = apply_row_limit_truncation(rows, row_fetch_limit)
        return QueryResult(
            data=rows,
            columns=list(schema),
            schema=schema,
            truncated_reason=truncated_reason,
        )

    return _observed(env.observers, source_config.type, call)
