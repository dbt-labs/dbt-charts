"""Run stage: execute the lookup's misses and report one outcome per query.

Stage: EXECUTE (run)
Purpose: Take the ``Miss``es a lookup could not answer and return
    ``Mapping[name, Outcome]`` with ``Outcome = Rows | Failed``. How one miss
    executes is the caller's ``execute``; this module owns what surrounds it:
    the worker pool, the ``{{ queries.X.cache }}`` dependency waves, and the
    failure boundary, so no error is raised past ``run``.

Entry Points:
    - run(misses, execute, settle, max_workers) -> dict[str, Outcome]
    - settle(ctx, ledger, miss, outcome) -> None
    - execute_miss(ctx, ledger, miss) -> Outcome
    - run_misses(ctx, ledger, found, max_workers) -> dict[str, Outcome]
    - read_query(ctx, ledger, planned, force_refresh) -> ResultRows
"""

import contextvars
import logging
import re
from collections.abc import Callable, Sequence
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from datetime import datetime
from graphlib import TopologicalSorter
from typing import Any, Literal

from dbt_charts.core.column_types import ColumnType
from dbt_charts.core.compile.format import missing_format_columns
from dbt_charts.core.compile.models.query.normalized import AnyQuery, is_sql_query
from dbt_charts.core.compile.models.source import ResolvedSourceConfig
from dbt_charts.core.compile.sql_guard import sqlglot_dialect
from dbt_charts.core.compile.template.daterange import board_clock
from dbt_charts.core.diagnostics.base import DbtChartsError
from dbt_charts.core.diagnostics.codes_execute import ERR_QUERY_FORMAT_COLUMN_MISSING
from dbt_charts.core.diagnostics.execution import ExecutionError, QueryError
from dbt_charts.core.execute.adapters.base import (
    ResolvedRelation,
    apply_row_limit_truncation,
    resolve_effective_row_limit,
)
from dbt_charts.core.execute.cache_backend import (
    CacheRows,
    ResultRows,
    TruncationInfo,
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
    lookup_and_record,
    memo_key,
)
from dbt_charts.core.execute.plan import (
    CACHE_REF_JINJA_RE,
    CacheRefComposition,
    ExecContext,
    PlannedQuery,
    cache_ref_upstream,
    get_query,
    plan,
    resolve_query_references,
)
from dbt_charts.core.execute.sql_translate import dialect_differs, dialect_unsupported

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
    elif outcome.record is not None:
        if use_store:
            cache_failure(ctx, name, query, variables, miss.cache_key, outcome.record)
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
    as soon as its outcome is known, so a later wave (and the executor's memo)
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
    if isinstance(planned.route, CacheRefComposition):
        assert is_sql_query(original_query)
        composed, cache_ref_reason = _execute_cache_ref_query(
            ctx, ledger, original_query, variables, query_name
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
                ctx, query_name, query, variables, tail, dialect, source_config
            )
            return Rows(limited, None, truncation=truncation)
        _logger.debug(
            "%s: SQL contains %s — ineligible for incremental tail; full refresh",
            query_name,
            reason,
        )

    try:
        result = ctx.adapter_registry.execute(
            query,
            variables,
            board=ctx.board,
            query_name=query_name,
            source_config=source_config,
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
    ctx: ExecContext, ledger: Ledger, found: Lookup, max_workers: int
) -> dict[str, Outcome]:
    """Run what *found* could not answer, in a pool.

    Every miss runs, and every failure is replayed: both land in the error
    memo, so a later read raises the stored error instead of re-executing. A
    failure never blocks its siblings. This is the error memo's only writer.

    Returns:
        One outcome per missed or failed query: ``Rows``, or ``Failed`` for a
        replayed failure, a failed run or a ``formats:`` column the rows lack.
    """
    results: dict[str, Outcome] = {
        name: Failed(failure.error, None) for name, failure in found.failures.items()
    }
    for name, outcome in run_and_settle(ctx, ledger, found.misses, max_workers).items():
        results[name] = format_checked(ctx, name, outcome)
    for name, outcome in results.items():
        if isinstance(outcome, Failed):
            _logger.debug(
                "Parallel execution of query '%s' failed: %s", name, outcome.error
            )
            ledger.errors[name] = outcome.error
    return results


def read_query(
    ctx: ExecContext,
    ledger: Ledger,
    planned: PlannedQuery,
    *,
    force_refresh: bool = False,
) -> ResultRows:
    """Rows for one planned query: the memo or store if they hold it, else run it.

    Raises the query's error when it failed, or a replayed failure holds it.
    """
    found = lookup_and_record(
        ctx,
        ledger,
        [planned],
        resume_incremental=True,
        force_refresh=force_refresh,
    )
    if planned.name in found.hits:
        return found.hits[planned.name].result
    if planned.name in found.failures:
        raise found.failures[planned.name].error
    outcome = run_and_settle(ctx, ledger, found.misses, None)[planned.name]
    if isinstance(outcome, Failed):
        raise outcome.error
    return outcome.result


def _execute_cache_ref_query(
    ctx: ExecContext,
    ledger: Ledger,
    query: AnyQuery,
    variables: dict[str, Any],  # type-state: explicit_any — variable values
    query_name: str,
) -> tuple[ResultRows, Literal["max_rows"] | None]:
    """Execute a ``{{ queries.X.cache }}`` composing query in an isolated engine.

    For each referenced upstream query NAME:
      1. Validate it exists and is cacheable.
      2. Read its rows. A wave runs upstreams first, so they are already in the
         ledger's memo; a one-shot read finds them in the store or runs them.
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
        variables: Merged variable values.
        query_name: Name of the composing query (for error messages).

    Returns:
        The isolated composition engine's rows with their declared schema, and "max_rows" when the
        composed result came back over the ceiling (rows already sliced to
        it) — mirroring how adapters signal via QueryResult.truncated_reason.

    Raises:
        ExecutionError: If no cache backend, cache doesn't support refs,
            an upstream query is missing, or an upstream has
            cache: false.
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
        cache_ref_upstream(query_name, ref_name, all_queries)
        [upstream_plan] = plan(ctx, [ref_name], variables)
        upstream = read_query(ctx, ledger, upstream_plan)
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
    )
    rows, truncated_reason = apply_row_limit_truncation(rows, row_fetch_limit)
    return ResultRows(rows, schema), truncated_reason
