"""Pluggable cache-backend Protocol for query result storage.

Stage: EXECUTE (Cache)
Purpose: Define the abstract surface that any query-result cache backend must
implement. The current implementations are TrivialDuckDBCache (local) and
PostgresResultCache (Cloud). Future alternatives (Redis, S3, in-memory) can
plug in by satisfying this Protocol.

Cache keys are always (source_hash, query_hash, variables_hash):
  - source_hash  — stable identity of the data source (connection string, project, etc.)
  - query_hash   — hash of the SQL/query after static resolution
  - variables_hash — hash of the variables that materially affect the result

board_slug and query_name are METADATA, not key components. They are passed
as keyword-only arguments to put for snapshot history.

One entry per key holds **two slots** — the last successful run's rows and the
last error. Either may be empty. That is the two-table shape, moved onto one
row, and the move is what fixes the bugs: a success write clears the error slot
*in the same statement*, so a stale failure can no longer outlive the success
that superseded it, and there is no cross-store bookkeeping to leak. The two
writes touch disjoint columns, so concurrent renders cannot clobber each other
and nothing needs locking.

Read order is rows-first: a caller still inside its own ttl is never handed a
failure for data it is entitled to, even when a sibling query over the same SQL
failed a moment ago. Only when the rows are too old *for this caller* does the
error slot answer.

Freshness is **derived on read, never stored**. The entry records only when it
was written; each caller decides whether that is fresh enough:

- **Rows** are live while no older than the *reader's* resolved
  ``CachePolicy.ttl_timedelta``, passed to ``get``.
- An **error** is live while inside its backend's retry window — a constant,
  not a reader policy, because a transient warehouse blip must be re-tried in
  minutes no matter how long the query is willing to serve stale rows. Each
  backend still spells that window itself (``failure_ttl_seconds`` here,
  ``_FAILURE_TTL_SECONDS`` in Cloud), unchanged and un-unified by this merge;
  routing both through engine config is open work, not something this claims.

Storing an expiry on the row would be wrong, not merely redundant: the key is
content-addressed (source + SQL + variables) and **deliberately excludes the
cache policy**, so two queries with identical SQL and different ``cache:``
blocks share one entry. A stored expiry would let whichever ran last silently
govern the other's authored freshness contract. The row cannot own an expiry
because the row is not owned by one policy.

Backend selection: TrivialDuckDBCache (local) and PostgresResultCache (Cloud)
implement this today. A future DCT_CACHE_BACKEND env-var hook can route to
other alternatives. This Protocol is also the seam for a future
`pip install dbt-charts[duckdb]` extras-packaging effort.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any, Literal, Protocol, TypeAlias, get_args, runtime_checkable

# Shared with executor.TruncationInfo.reason — the set of causes a cached
# rows entry can carry. Defined here (not imported from executor) because
# this module must not depend on execute/executor.py's higher layer.
TruncatedReason: TypeAlias = Literal["max_rows", "max_result_bytes"]

# Canonical type for a query-result rowset: a list of column-name → value dicts.
# Values are dynamically typed (SQL/JSON), so the leaf is Any by nature. Defined
# once here and reused by the cache backends and the composition engine.
CacheRows: TypeAlias = list[dict[str, Any]]


def _require_aware(value: datetime, field: str) -> None:
    """Reject a naive timestamp crossing the cache-backend seam.

    ``QueryResultCache`` is a plugin boundary — DuckDB locally, Postgres on
    Cloud, and whatever a host implements next — so this is validating untrusted
    input at the boundary, not re-checking our own normalized data. A naive
    value here is a backend bug that surfaces far away and quietly: elapsed-time
    comparisons go wrong by the machine's UTC offset, and ``min()`` across a
    render's hits raises a TypeError with no hint which backend produced it.
    """
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError(
            f"{field} must be timezone-aware (UTC); got naive {value!r}. "
            "Cache backends store and return aware UTC timestamps — see the "
            "module docstring."
        )


@dataclass(frozen=True)
class CacheHit:
    """Rows plus write-timestamp for one rows-bearing ``get()`` — a single
    round trip. Backends fetch both from the same underlying row(s); a
    second query just to learn the write timestamp would double DB traffic
    per cache hit (a real cost on Cloud's Postgres-backed cache).
    """

    rows: CacheRows
    # Timezone-aware UTC, from every backend. Both compare it against a bound
    # and callers take min() across a render's hits — one naive value in the set
    # makes that a TypeError, and one mislabeled value makes it silently wrong.
    written_at: datetime
    # None when the rows were not truncated at write time. The truncation
    # fact travels with the cached data rather than being re-derived from how
    # this render fetched it — a warm hit must surface the same
    # WARN-QUERY-RESULT-TRUNCATED warning a fresh execution would.
    truncated_reason: TruncatedReason | None = None

    def __post_init__(self) -> None:
        _require_aware(self.written_at, "CacheHit.written_at")
        if self.truncated_reason is not None and self.truncated_reason not in get_args(
            TruncatedReason
        ):
            raise ValueError(
                f"CacheHit.truncated_reason must be one of "
                f"{get_args(TruncatedReason)} or None, got {self.truncated_reason!r}"
            )


@dataclass(frozen=True)
class CachedQueryFailure(Exception):
    """A query failure retrieved from the cache.

    Raised instead of re-running a query that already failed within the retry
    window. Consumers can distinguish cached failures from fresh failures by
    catching this type.
    """

    error_class: str
    error_message: str
    # "" when unavailable — traceback extraction can fail for C-level
    # exceptions, and that is what both backends already store.
    traceback: str
    # Timezone-aware UTC, same contract as CacheHit.written_at — the retry
    # window is measured against it, so a local-clock value would make the
    # elapsed comparison wrong by the machine's UTC offset.
    failed_at: datetime

    def __post_init__(self) -> None:
        # dataclass __init__ does not call Exception.__init__, so str(self) == ''.
        # Calling it here restores the message for str(), repr(), and logging.
        Exception.__init__(self, self.error_message)
        _require_aware(self.failed_at, "CachedQueryFailure.failed_at")


# What one ``get()`` can find: the rows a run produced, or the error it raised.
# A union rather than one record with nullable rows/error fields — the two are
# mutually exclusive by construction, so the invalid "both" and "neither"
# states are not representable.
CacheOutcome: TypeAlias = CacheHit | CachedQueryFailure


@runtime_checkable
class QueryResultCache(Protocol):
    """Pluggable backend for persistent query-result caching.

    All operations are keyed by (source_hash, query_hash, variables_hash).
    board_slug and query_name are metadata passed on writes; they are NOT key
    components. This keeps the key free of app-layer noise so two boards with
    identical queries against identical sources share one cache entry.

    Implementations may use any storage: DuckDB, Redis, S3, in-memory, etc.
    Current implementations: TrivialDuckDBCache (local), PostgresResultCache (Cloud).
    """

    def get(
        self,
        source_hash: str,
        query_hash: str,
        variables_hash: str,
        *,
        ttl: timedelta | None = None,
    ) -> CacheOutcome | None:
        """Return this key's live outcome — a ``CacheHit`` (rows + write
        timestamp) or a ``CachedQueryFailure`` — or None when there is none
        or it has expired.

        One lookup, because there is one entry: a rows miss is never followed
        by a second probe for an error.

        *ttl* is the **reader's** resolved ``CachePolicy.ttl_timedelta`` and
        bounds a rows outcome only: the entry must be no older than *ttl*.
        ``None`` means the reader imposes no age bound — ``ttl: forever``, or a
        content-addressed lookup with no time dimension. It is ignored for an
        error outcome, whose retry window is a backend constant the reader has
        no say in.

        Passing the reader's ttl here is load-bearing, not optional: the cache
        key excludes the cache policy, so two queries with identical SQL and
        different ``cache:`` blocks share one entry. A caller that omits *ttl*
        gets whatever the other query last wrote, however old.

        The write timestamp travels with the hit (see ``CacheHit``) so a caller
        that also wants "data as of <time>" (the executor, for board-chrome)
        never needs a second round trip.

        **Type fidelity is part of the contract, scoped to uniformly-typed
        columns.** ``int``/``bool``/``str``/``float`` always stay themselves.
        A column whose every non-None value across the whole rowset shares
        one of ``date``, ``datetime`` (naive stays naive, aware stays aware),
        or ``Decimal`` must round-trip exactly — not a stringified or
        float-coerced stand-in — even through a backend whose storage is
        lossy on its own (e.g. JSON, which has no date/datetime/Decimal
        type, or DuckDB's fixed-precision DECIMAL): such a backend must carry
        enough metadata alongside the rows to restore the original type on
        read. ``PostgresResultCache`` and ``TrivialDuckDBCache`` both store
        rows as JSON with a per-column type tag beside them. A column that
        mixes types — including
        mixing ``date`` and ``datetime`` in the same column — is NOT
        promised exact fidelity: its values pass through in whatever form
        the backend's own storage naturally holds (e.g. an ISO string),
        never guessed back into a type from their shape. Restoration
        failure on a value a backend's own tag claims to be able to restore
        (a corrupted entry, or code-version skew) must be treated as a
        miss — ``get()`` never raises and never returns partially-restored
        rows.

        **Column order is part of the contract too.** Each row mapping must
        come back with its keys in the order ``put()`` stored them: a query
        result's column order is part of its shape, and a table's rendered
        columns are read straight off ``rows[0].keys()``. A backend whose
        storage does not preserve mapping key order — Postgres ``jsonb`` sorts
        object keys, and most key-value stores promise nothing — must either
        store in a form that does, or record the column list alongside the rows
        and rebuild each row from it on read, the same way the type map above is
        carried. ``TrivialDuckDBCache`` stores each row as a JSON object, which
        keeps its key order, so rows that do not share a key set come back with
        their own keys.

        Callers of ``get()`` (the executor's incremental-tail watermark
        computation, in particular) trust this and do not guess a value's
        real type from its shape; a backend that violates it — including a
        mixed-typed key column, whose values are not promised to be
        comparable — produces a type disagreement the executor's merge step
        raises on, rather than a silently wrong result.
        """
        ...

    def put(
        self,
        source_hash: str,
        query_hash: str,
        variables_hash: str,
        outcome: CacheRows | Exception,
        *,
        board_slug: str,
        query_name: str,
        source_name: str = "",
        truncated_reason: TruncatedReason | None = None,
    ) -> None:
        """Record *outcome* in its own slot, leaving the other one alone.

        Result rows replace the rows slot and clear the error slot; an error
        records itself and does not touch the rows. No expiry is stored — see
        the module docstring — and no ttl is needed here, because a write never
        has to judge whether the *other* slot is still wanted. Only a reader
        knows that, and only for itself.

        board_slug and query_name are recorded as metadata, not key components.
        truncated_reason travels with the rows so a later warm ``get()`` can
        reconstruct the same truncation warning a fresh execution would —
        ignored when *outcome* is an Exception.

        """
        ...

    def clear(self, source_hash: str, query_hash: str, variables_hash: str) -> None:
        """Remove the entry for the given key."""
        ...

    def close(self) -> None:
        """Release any held resources (connections, file handles, etc.)."""
        ...

    # ── Cache-ref composition extension ──────────────────────────────────────
    # {{ queries.X.cache }} composition runs in an isolated in-process DuckDB
    # (dbt_charts.core.execute.cache_composition), fed the upstream rows directly
    # by the executor — it does NOT run on the backend's own/application
    # connection. The backend only advertises the capability below.

    def supports_cache_refs(self) -> bool:
        """Return True if this backend can execute {{ queries.X.cache }} composition.

        DuckDB-backed and Postgres-backed caches return True; an in-memory
        dict-based cache (or any future backend that cannot cache the upstream
        rows) returns False.
        """
        ...
