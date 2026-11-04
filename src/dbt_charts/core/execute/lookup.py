"""Cache lookup: which of a render's queries already have an answer.

Stage: EXECUTE (lookup)
Purpose: Read the per-render memo, the error memo and the persistent result cache
    for a set of keyed queries and return plain data: ``Lookup(hits, misses,
    failures)``. Nothing here executes a query; the
    caller applies the hits and runs the misses.

Entry Points:
    - lookup(probes, ...) -> Lookup
"""

from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from dbt_charts.core.compile.models.query.normalized import AnyQuery, is_sql_query
from dbt_charts.core.diagnostics.base import DbtChartsError
from dbt_charts.core.diagnostics.execution import QueryError
from dbt_charts.core.diagnostics.registry import REGISTRY, ErrorCode
from dbt_charts.core.execute.cache_backend import (
    CachedQueryFailure,
    CacheRows,
    QueryResultCache,
    ResultRows,
    TruncatedReason,
)

CacheKey = tuple[str, str, str]
MemoKey = tuple[str, str, str, str]
LimitEnforcer = Callable[[ResultRows, str, TruncatedReason | None], ResultRows]


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
class Probe:
    """One query to look up, already keyed by the caller."""

    name: str
    query: AnyQuery
    cache_key: CacheKey
    # False for a call-level no-store or a `cache: false` policy.
    use_store: bool
    # Whether a stored hit may be refreshed by an incremental tail. Evaluated only
    # on an incremental SQL store hit, since answering needs a source lookup.
    tailable: Callable[[], bool]


@dataclass(frozen=True)
class CachedRows:
    """Rows served without executing: from the memo, or from the store."""

    result: ResultRows
    # When the store wrote them; None for rows this render's memo already held.
    written_at: datetime | None


@dataclass(frozen=True)
class Tail:
    """The prior rows an incremental query refreshes, and where to resume."""

    prior_rows: CacheRows
    # None when the prior rows are empty: there is nothing to resume from.
    watermark: Any | None  # type-state: explicit_any — MAX watermark; dialect-typed
    written_at: datetime


@dataclass(frozen=True)
class Miss:
    """A query that must run. ``tail`` is set when the store held prior rows an
    incremental refresh can extend."""

    name: str
    query: AnyQuery
    cache_key: CacheKey
    tail: Tail | None = None


@dataclass(frozen=True)
class Failure:
    """A query whose recorded failure is replayed instead of re-running it."""

    error: Exception
    # When the store recorded the failure; None for this render's error memo.
    failed_at: datetime | None = None


@dataclass(frozen=True)
class Lookup:
    """Every probed query lands in exactly one of the three."""

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
    probes: Sequence[Probe],
    *,
    memo: Mapping[MemoKey, ResultRows],
    errors: Mapping[str, Exception],
    result_cache: QueryResultCache | None,
    enforce_limits: LimitEnforcer,
    force_refresh: bool = False,
    resume_incremental: bool,
) -> Lookup:
    """Partition *probes* into hits, misses and replayed failures.

    Order per query: the per-render memo (the render's identity guarantee — it
    serves regardless of cache settings), then the error memo, then one probe of
    the persistent store, gated on ``Probe.use_store`` and the query's own policy.
    The store read passes the reader's ttl, because the key excludes the policy;
    both halves of the policy are enforced here, the only place the store is
    read. A stored hit passes through *enforce_limits* so a ceiling lowered since
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
    for probe in probes:
        name, query, cache_key = probe.name, probe.query, probe.cache_key
        if force_refresh:
            misses.append(Miss(name, query, cache_key))
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
        if not (probe.use_store and query.cache.enabled and result_cache):
            misses.append(Miss(name, query, cache_key))
            continue
        outcome = result_cache.get(*cache_key, ttl=query.cache.ttl_timedelta)
        if isinstance(outcome, CachedQueryFailure):
            # Written under a code this version no longer registers: it cannot
            # be replayed as it was raised, so the query re-runs.
            if outcome.error_code is not None and outcome.error_code not in (
                REGISTRY.codes(level="error")
            ):
                misses.append(Miss(name, query, cache_key))
            else:
                failures[name] = Failure(
                    _replayed_query_error(outcome, name), outcome.failed_at
                )
            continue
        if outcome is None:
            misses.append(Miss(name, query, cache_key))
            continue
        # A ceiling lowered since the write must not serve the old, larger set;
        # the write's own truncation signal rides along as the seed.
        enforced = enforce_limits(
            ResultRows(outcome.rows, outcome.schema), name, outcome.truncated_reason
        )
        if (
            is_sql_query(query)
            # isinstance, not truthiness: the cache-key fold uses the same gate.
            and isinstance(query.incremental, str)
            and resume_incremental
            and probe.tailable()
        ):
            try:
                watermark = _compute_watermark(enforced.rows, query.incremental, name)
            except QueryError as exc:
                failures[name] = Failure(exc)
                continue
            tail = Tail(enforced.rows, watermark, outcome.written_at)
            misses.append(Miss(name, query, cache_key, tail))
            continue
        hits[name] = CachedRows(enforced, outcome.written_at)
    return Lookup(hits, tuple(misses), failures)
