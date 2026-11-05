"""Cache lookup: which of a render's queries already have an answer.

Stage: EXECUTE (lookup)
Purpose: Read the per-render memo, the error memo and the persistent result cache
    for a set of keyed queries and return plain data: ``Lookup(hits, misses,
    failures)``. Nothing here executes a query; the
    caller applies the hits and runs the misses.

    A render's mutable state is one ``Ledger``: the memo, the error memo, the
    data ages and the truncations. ``lookup_and_record`` and ``run.settle`` are
    its only writers.

Entry Points:
    - lookup(planned, ...) -> Lookup
    - lookup_and_record(ctx, ledger, planned, ...) -> Lookup
    - limit_rows(result, query_name, seed_reason) -> (ResultRows, TruncationInfo | None)
"""

import json
import logging
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from dbt_charts.core.compile import config
from dbt_charts.core.compile.models.board.normalized import VariableValues
from dbt_charts.core.compile.models.cache import CachePolicy
from dbt_charts.core.compile.models.query.normalized import AnyQuery, is_sql_query
from dbt_charts.core.diagnostics.base import DbtChartsError
from dbt_charts.core.diagnostics.codes_execute import (
    ERR_QUERY_INTERRUPTED,
    ERR_WAREHOUSE_AUTH,
    ERR_WAREHOUSE_CONNECTION,
)
from dbt_charts.core.diagnostics.execution import ExecutionError, QueryError
from dbt_charts.core.diagnostics.registry import REGISTRY, ErrorCode
from dbt_charts.core.execute.adapters.base import ResolvedRelation
from dbt_charts.core.execute.cache_backend import (
    BrowsableResultCache,
    CachedQueryFailure,
    CacheRows,
    QueryResultCache,
    ResultRows,
    TruncatedReason,
    TruncationInfo,
)
from dbt_charts.core.execute.duckdb_cache import compute_relevant_variables
from dbt_charts.core.execute.plan import (
    CacheKey,
    ExecContext,
    PlannedQuery,
    source_version,
)

_logger = logging.getLogger(__name__)

MemoKey = tuple[str, str, str, str]
# (query name, data_as_of, policy, served by the persistent cache).
DataAge = tuple[str, datetime, CachePolicy, bool]


@dataclass
class Ledger:
    """Everything one render accumulates about its queries.

    The memo is keyed by content *plus* the asking query's resolved policy
    (``memo_key``): its slots are warmed from the persistent store, so a
    content-only key would let one query's policy answer a sibling's read.
    """

    memo: dict[MemoKey, ResultRows] = field(default_factory=dict)
    errors: dict[str, Exception] = field(default_factory=dict)
    provenance: dict[str, list[ResolvedRelation]] = field(default_factory=dict)
    # Query name -> why its result was cut by execution.max_rows or
    # max_result_bytes this render; read by the query-result-truncated warning.
    truncations: dict[str, TruncationInfo] = field(default_factory=dict)
    # One record per query completion. Fresh executions carry the instant the
    # rows came back; cache hits the entry's written_at. All records drive the
    # snapshot expires_at; the cache-hit ones drive the "data as of" stamp.
    ages: list[DataAge] = field(default_factory=list)
    warehouse_hit_ats: list[datetime] = field(default_factory=list)


def cache_hit_ats(ledger: Ledger) -> list[datetime]:
    """Write-timestamps of every persistent-cache hit this render served.

    Empty when every query ran fresh. The board-chrome freshness stamp reads
    this to disclose data age; the selection policy lives with the stamp in
    ``render/boards.py``.
    """
    return [at for _name, at, _policy, is_hit in ledger.ages if is_hit]


def warehouse_cache_hit_ats(ledger: Ledger) -> list[datetime]:
    """The ``cache_hit_ats`` whose cache key folds no content version, so their
    data may have changed since the write."""
    return list(ledger.warehouse_hit_ats)


def data_age(ledger: Ledger, query_name: str) -> tuple[datetime, bool] | None:
    """When *query_name*'s rows were produced, and whether the persistent cache
    served them, as of its latest completion in this render; None when it has
    not completed."""
    for name, data_as_of, _policy, is_hit in reversed(ledger.ages):
        if name == query_name:
            return data_as_of, is_hit
    return None


def query_data_ages(ledger: Ledger) -> list[tuple[str, datetime, CachePolicy]]:
    """Per-query (name, data_as_of, policy) for every query that completed.

    Consumed by the snapshot writer to compute ``expires_at`` anchored on the
    actual data age rather than the render timestamp.
    """
    return [(name, at, policy) for name, at, policy, _ in ledger.ages]


def limit_rows(
    result: ResultRows,
    query_name: str,
    seed_reason: TruncatedReason | None = None,
) -> tuple[ResultRows, TruncationInfo | None]:
    """The one chokepoint rows pass through before the memo or the store sees
    them: the "never cache an oversized result" guarantee lives here.

    ``seed_reason`` carries a truncation signal already detected upstream:
    - ``"max_rows"``: adapter-return or cache-ref fetch already detected row
      truncation (``QueryResult.truncated_reason`` or the post-fetchmany slice).
    - ``"max_result_bytes"``: warm-cache path propagates a byte-truncation
      signal from a prior write so the warning still fires on a cache hit
      where the data is already under the current byte ceiling.
    - ``None``: no upstream signal; both row- and byte-checks run from scratch.
    The row-count check is defense-in-depth for adapter paths (fetch already
    bounded); it is the primary check on the warm-cache path, where a lowered
    ceiling could otherwise serve more rows than the new limit allows.

    Byte size is checked by accumulating each row's serialized size
    incrementally and cutting the moment the running total crosses
    max_result_bytes — never by serializing the whole result to measure it,
    which would defeat the point on an oversized result.

    Returns the kept rows and, when anything was cut or seeded, the truncation.
    """
    reason: TruncatedReason | None = seed_reason
    rows = result.rows
    max_rows = config.resolve_max_rows()
    if len(rows) > max_rows:
        rows = rows[:max_rows]
        if reason is None:
            reason = "max_rows"

    max_bytes = config.resolve_max_result_bytes()
    kept: CacheRows = []
    running_bytes = 0
    for row in rows:
        row_bytes = len(json.dumps(row, default=str))
        if running_bytes + row_bytes > max_bytes:
            reason = "max_result_bytes"
            break
        kept.append(row)
        running_bytes += row_bytes

    truncation = (
        None
        if reason is None
        else TruncationInfo(
            query_name=query_name, kept_row_count=len(kept), reason=reason
        )
    )
    return ResultRows(kept, result.schema), truncation


def memo_key(query: AnyQuery, cache_key: CacheKey) -> MemoKey:
    """Scope a content cache key to the asking query's resolved cache policy.

    ``compute_cache_key`` is content-addressed on purpose: identical SQL against
    one source is a single *stored* entry, so siblings share the warehouse work.
    The in-memory memo cannot be keyed that way. It is warmed from that stored
    entry, and freshness is a property of the query doing the asking — so one
    slot shared across policies lets whichever query rendered first decide what
    its siblings are served, with no ttl check and no `enabled` check in the way.

    Appending the policy gives each distinct policy its own slot. Identical
    policies still share one, which is where the memoization win lives. The
    *persistent* key is untouched — folding policy in there would cold-start the
    stored entry on every ttl edit.
    """
    if not query.cache.enabled:
        # Disabled queries never read the store, so this slot only ever holds
        # rows the current render executed. That is what makes `cache: false` a
        # real no-store without a special case, while still memoizing across a
        # query's own consumers.
        return (*cache_key, "off")
    ttl = query.cache.ttl_timedelta
    return (*cache_key, "forever" if ttl is None else str(ttl.total_seconds()))


@dataclass(frozen=True)
class CachedRows:
    """Rows served without executing: from the memo, or from the store."""

    result: ResultRows
    # When the store wrote them; None for rows this render's memo already held.
    written_at: datetime | None
    # Set when the ceilings cut the stored rows, or the write recorded a cut.
    truncation: TruncationInfo | None = None


@dataclass(frozen=True)
class Tail:
    """The prior rows an incremental query refreshes, and where to resume."""

    prior_rows: CacheRows
    # None when the prior rows are empty: there is nothing to resume from.
    watermark: Any | None  # type-state: explicit_any — MAX watermark; dialect-typed
    written_at: datetime
    truncation: TruncationInfo | None = None


@dataclass(frozen=True)
class Miss:
    """A query that must run. ``tail`` is set when the store held prior rows an
    incremental refresh can extend."""

    planned: PlannedQuery
    tail: Tail | None = None

    @property
    def name(self) -> str:
        return self.planned.name

    @property
    def query(self) -> AnyQuery:
        return self.planned.query

    @property
    def cache_key(self) -> CacheKey:
        return self.planned.cache_key


@dataclass(frozen=True)
class Failure:
    """A query whose recorded failure is replayed instead of re-running it."""

    error: Exception
    # When the store recorded the failure; None for this render's error memo.
    failed_at: datetime | None = None


@dataclass(frozen=True)
class Lookup:
    """Every planned query lands in exactly one of the three."""

    hits: Mapping[str, CachedRows]
    misses: tuple[Miss, ...]
    failures: Mapping[str, Failure]


def _compute_watermark(
    rows: CacheRows, key_col: str, query_name: str
) -> Any | None:  # type-state: explicit_any — MAX watermark; source-dialect-typed
    """Return MAX(key_col) over rows, or None when rows are empty.

    The watermark's type is whatever the cache handed back for *rows* — never
    guessed from string shape here. ``QueryResultCache.get()`` is contractually
    required to return each column with the same Python type its ``put()``
    received; guessing from a string's shape misclassifies a VARCHAR watermark
    column holding ISO-date-shaped values as a DATE.

    Raises QueryError if key_col is absent from any row or if the column
    values are not orderable — a misspelled incremental watermark column is a
    hard error, not a silent fallback to full refresh.
    """
    if not rows:
        return None
    try:
        return max(row[key_col] for row in rows)
    except KeyError as exc:
        raise QueryError(
            f"incremental watermark column {key_col!r} not found in cached rows for query {query_name!r}",
            query_name,
        ) from exc
    except TypeError as exc:
        raise QueryError(
            f"incremental watermark column {key_col!r} values are not orderable for query {query_name!r}",
            query_name,
        ) from exc


def _replayed_query_error(failure: CachedQueryFailure, query_name: str) -> QueryError:
    """Rebuild the QueryError a fresh run raised from its cached record."""
    code = None
    if failure.error_code is not None:
        registered = REGISTRY.get(failure.error_code)
        # Only a failure whose code is a registered error is replayed.
        assert isinstance(registered, ErrorCode)
        code = registered
    # The stored message is already the final text, so query_name goes in
    # fields only — through QueryError's constructor it would be appended again.
    error = QueryError(failure.error_message, code=code)
    error.fields = {**failure.error_fields}
    error.fields.setdefault("query_name", query_name)
    error.__cause__ = failure
    return error


def _memoized_error(stored: Exception, query_name: str) -> QueryError:
    if isinstance(stored, QueryError):
        return stored
    error = QueryError(
        str(stored),
        query_name,
        code=stored.code if isinstance(stored, DbtChartsError) else None,
    )
    error.__cause__ = stored
    return error


def lookup(
    planned: Sequence[PlannedQuery],
    *,
    memo: Mapping[MemoKey, ResultRows],
    errors: Mapping[str, Exception],
    result_cache: QueryResultCache | None,
    force_refresh: bool = False,
    resume_incremental: bool,
) -> Lookup:
    """Partition *planned* queries into hits, misses and replayed failures.

    Order per query: the per-render memo (the render's identity guarantee — it
    serves regardless of cache settings), then the error memo, then one probe of
    the persistent store, gated on ``PlannedQuery.use_store`` and the query's own policy.
    The store read passes the reader's ttl, because the key excludes the policy;
    both halves of the policy are enforced here, the only place the store is
    read. A stored hit passes through ``limit_rows`` so a ceiling lowered since
    the write still applies.

    ``force_refresh`` reads nothing: every query is a plain miss.

    ``resume_incremental`` decides what a stored incremental query is: True makes
    it a miss carrying its ``Tail`` (the caller is about to run the query), False
    a plain hit served as stored (a pre-pass that only wants what needs no
    execution, and must not run a tail).

    Raises nothing for a bad cache entry or watermark: those are the query's
    failure, reported in ``failures``.
    """
    hits: dict[str, CachedRows] = {}
    misses: list[Miss] = []
    failures: dict[str, Failure] = {}
    for query_plan in planned:
        name, query, cache_key = query_plan.name, query_plan.query, query_plan.cache_key
        if force_refresh:
            misses.append(Miss(query_plan))
            continue
        # The memo precedes the error memo: the parallel pass writes rows into the
        # memo before its persistent write-through, which can raise and land an
        # error in ``errors`` beside valid rows. Errors reflect a real failure only
        # when the memo has no rows.
        memoized = memo.get(memo_key(query, cache_key))
        if memoized is not None:
            hits[name] = CachedRows(memoized, None)
            continue
        if name in errors:
            failures[name] = Failure(_memoized_error(errors[name], name))
            continue
        if not (query_plan.use_store and query.cache.enabled and result_cache):
            misses.append(Miss(query_plan))
            continue
        outcome = result_cache.get(*cache_key, ttl=query.cache.ttl_timedelta)
        if isinstance(outcome, CachedQueryFailure):
            # Written under a code this version no longer registers: it cannot
            # be replayed as it was raised, so the query re-runs.
            if outcome.error_code is not None and outcome.error_code not in (
                REGISTRY.codes(level="error")
            ):
                misses.append(Miss(query_plan))
            else:
                failures[name] = Failure(
                    _replayed_query_error(outcome, name), outcome.failed_at
                )
            continue
        if outcome is None:
            misses.append(Miss(query_plan))
            continue
        # A ceiling lowered since the write must not serve the old, larger set;
        # the write's own truncation signal rides along as the seed.
        enforced, truncation = limit_rows(
            ResultRows(outcome.rows, outcome.schema), name, outcome.truncated_reason
        )
        if (
            is_sql_query(query)
            # isinstance, not truthiness: the cache-key fold uses the same gate.
            and isinstance(query.incremental, str)
            and resume_incremental
            and query_plan.tailable
        ):
            try:
                watermark = _compute_watermark(enforced.rows, query.incremental, name)
            except QueryError as exc:
                failures[name] = Failure(exc)
                continue
            tail = Tail(enforced.rows, watermark, outcome.written_at, truncation)
            misses.append(Miss(query_plan, tail))
            continue
        hits[name] = CachedRows(enforced, outcome.written_at, truncation)
    return Lookup(hits, tuple(misses), failures)


def lookup_and_record(
    ctx: ExecContext,
    ledger: Ledger,
    planned: Sequence[PlannedQuery],
    *,
    resume_incremental: bool,
    force_refresh: bool = False,
) -> Lookup:
    """Run ``lookup`` over *ledger* and the context's store, recording what it served.

    A store hit warms the memo under the asking query's policy-scoped slot,
    names the board's use of the entry, and records the hit's data age (it
    powers both the snapshot ``expires_at`` and the "data as of" stamp). A
    replayed store failure records its age too. An incremental miss's prior
    rows were served from the store as well, so they record theirs.
    """
    found = lookup(
        planned,
        memo=ledger.memo,
        errors=ledger.errors,
        result_cache=ctx.result_cache,
        force_refresh=force_refresh,
        resume_incremental=resume_incremental,
    )
    by_name = {query_plan.name: query_plan for query_plan in planned}
    for name, hit in found.hits.items():
        if hit.written_at is not None:
            query_plan = by_name[name]
            ledger.memo[memo_key(query_plan.query, query_plan.cache_key)] = hit.result
            _record_store_hit(ctx, ledger, query_plan, hit.written_at)
            if hit.truncation is not None:
                ledger.truncations[name] = hit.truncation
    for miss in found.misses:
        if miss.tail is not None:
            _record_store_hit(ctx, ledger, by_name[miss.name], miss.tail.written_at)
            if miss.tail.truncation is not None:
                ledger.truncations[miss.name] = miss.tail.truncation
    for name, failure in found.failures.items():
        if failure.failed_at is not None:
            ledger.ages.append(
                (name, failure.failed_at, by_name[name].query.cache, False)
            )
    return found


def _record_store_hit(
    ctx: ExecContext, ledger: Ledger, planned: PlannedQuery, written_at: datetime
) -> None:
    if isinstance(ctx.result_cache, BrowsableResultCache):
        ctx.result_cache.note_use(
            *planned.cache_key, cache_board_slug(ctx), planned.name
        )
    ledger.ages.append((planned.name, written_at, planned.query.cache, True))
    if not source_version(ctx, planned.query):
        ledger.warehouse_hit_ats.append(written_at)


def cache_board_slug(ctx: ExecContext) -> str:
    """The board identity a cache entry records: its file slug, or, for a board
    with no file (a preview from YAML text), its compiled id behind a ``/`` that
    no file slug starts with, so a draft titled like a stored board never lists
    or serves as that board."""
    return ctx.board_slug if ctx.board_slug is not None else f"/{ctx.board.id}"


def cache_outcome(
    ctx: ExecContext,
    query_name: str,
    query: AnyQuery,
    outcome: ResultRows | Exception,
    variables: VariableValues | None,
    cache_key: CacheKey,
    truncated_reason: TruncatedReason | None = None,
) -> None:
    """Write this query's outcome — its rows or its error — to the cache.

    One entry per key, so the write is the whole reconciliation: a success
    (zero-row included) replaces a stale error, and an error replaces rows
    the reader can no longer use, with no "clear the other store"
    bookkeeping. Nothing about expiry is stored — freshness is derived on
    read (see the cache_backend module docstring).

    An error never displaces live rows — the backends enforce that, not
    this caller. Within one execute_query the error write is only reached
    after the read missed, but renders run concurrently over a shared key,
    so a worker whose query timed out could otherwise wipe rows another
    worker had just computed successfully.

    ``truncated_reason`` persists the truncation fact alongside the rows it
    describes, so a later warm read (``lookup``) can reconstruct the same
    warning a fresh execution would, instead of losing it on a cache hit.
    """
    if not ctx.result_cache:
        return

    source_hash, query_hash, variables_hash = cache_key
    ctx.result_cache.put(
        source_hash,
        query_hash,
        variables_hash,
        outcome,
        board_slug=cache_board_slug(ctx),
        query_name=query_name,
        source_name=(
            query.source
            if is_sql_query(query) and isinstance(query.source, str)
            else ""
        ),
        truncated_reason=truncated_reason,
        variables=compute_relevant_variables(query, variables),
    )


def cache_failure(
    ctx: ExecContext,
    query_name: str,
    query: AnyQuery,
    variables: VariableValues | None,
    cache_key: CacheKey,
    exception: Exception,
) -> None:
    """Cache a query failure (if a backend is configured). Never raises.

    DbtChartsError (policy / compile errors) are not cached: they are fast
    to re-derive, always deterministic, and caching them would discard the
    structured error code and message that callers check.

    A cache write that fails must not replace the real query error the
    caller is about to raise — hence the swallow, which the rows path
    (whose failures the parallel prefetch records) deliberately lacks.
    """
    if isinstance(exception, DbtChartsError) and not isinstance(
        exception, ExecutionError
    ):
        return
    # Says nothing about the query: it was stopped, not answered.
    # Credential and connection failures are fixed outside the board, so
    # the next render must run the query again rather than replay them.
    if isinstance(exception, DbtChartsError) and (
        exception.code
        in (ERR_QUERY_INTERRUPTED, ERR_WAREHOUSE_AUTH, ERR_WAREHOUSE_CONNECTION)
    ):
        return
    try:
        cache_outcome(ctx, query_name, query, exception, variables, cache_key)
    except Exception:  # noqa: BLE001 — a failure-cache write may raise anything; it must not mask the query's own error
        _logger.debug(
            "Failed to cache query failure for '%s'", query_name, exc_info=True
        )
