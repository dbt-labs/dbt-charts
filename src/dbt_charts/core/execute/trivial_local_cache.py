"""Local result cache backend.

Stage: EXECUTE (Cache)
Purpose: Replace-on-write DuckDB cache for `dct render` and `dct serve`. One
outcome row per cache key, the result rows stored on it as a single payload —
no history, no generations.

Payload encoding: zlib-compressed JSON of ``{"schema": [...], "rows": [...]}``
(``result_codec``). JSON holds what a query result holds, nested values and
rows that do not share a key set included, and keeps each row's key order; the
declared schema restores the types it has no name for.
"""

from __future__ import annotations

import json
import logging
import zlib
from datetime import UTC, datetime, timedelta
from pathlib import Path  # noqa: TID251 — DuckDB cache file (DCT_CACHE_PATH)

from dbt_charts.core.execute._duckdb_cache_base import _DuckDBResultCacheBase
from dbt_charts.core.execute.cache_backend import (
    CacheEntry,
    CacheHit,
    CacheOutcome,
    CacheVariables,
    ResultRows,
    TruncatedReason,
)
from dbt_charts.core.execute.cache_keys import stored_query_hash
from dbt_charts.core.execute.result_codec import decode_result, encode_result

logger = logging.getLogger(__name__)


def _encode(result: ResultRows) -> bytes:
    document = json.dumps(encode_result(result))
    return zlib.compress(document.encode(), level=1)


def _decode(payload: bytes) -> ResultRows:
    return decode_result(json.loads(zlib.decompress(payload)))


class TrivialDuckDBCache(_DuckDBResultCacheBase):
    """Replace-on-write DuckDB cache for local `dct render` and `dct serve`.

    Implements QueryResultCache. Backed by a DuckDB file so results survive
    from one invocation to the next, and several processes can share one file.

    put() always replaces the existing outcome for a key. There is no history,
    no generations, no primary-key incremental path.

    Attributes:
        db_path: Path to DuckDB file (or None for :memory:)
        failure_ttl_seconds: Retry window for a cached error; 0 disables error
            caching, so a failing query is re-tried on every render.
        lock_timeout_seconds: How long one read or write keeps retrying a
            file it cannot open before raising ERR-CACHE-FILE-UNAVAILABLE.
        max_age: Entries older than this were removed when the cache was
            opened; None when nothing is ever removed.
    """

    def __init__(
        self,
        db_path: Path | None = None,
        failure_ttl_seconds: int = 900,
        lock_timeout_seconds: float = 10.0,
        max_age: timedelta | None = None,
    ) -> None:
        """Open the cache, deleting entries older than *max_age* when given.

        *max_age* bounds how long warehouse data sits on disk in a file nobody
        chose a location for; None keeps every entry until it is overwritten.
        """
        super().__init__(
            db_path=db_path,
            failure_ttl_seconds=failure_ttl_seconds,
            lock_timeout_seconds=lock_timeout_seconds,
            max_age=max_age,
            _class_name="TrivialDuckDBCache",
        )

    def get(
        self,
        source_hash: str,
        query_hash: str,
        variables_hash: str,
        *,
        ttl: timedelta | None = None,
    ) -> CacheOutcome | None:
        """Return this key's live outcome — rows or the cached error — else None.

        Read order is **rows first**, and only if this reader can no longer use
        them does the error slot come into play. So a caller inside its own ttl
        is never handed a failure for data it is still entitled to, even when a
        sibling query over the same SQL failed a moment ago.

        A payload that cannot be decoded — written by another version, or
        damaged — is a miss: the caller re-runs the query and replaces it.
        """
        with self._connection() as conn:
            slots = self._read_slots(conn, source_hash, query_hash, variables_hash)
        written_at = slots.rows_written_at
        if (
            written_at is not None
            and slots.payload is not None
            and (ttl is None or datetime.now(UTC) - written_at <= ttl)
        ):
            try:
                result = _decode(slots.payload)
            except (zlib.error, ValueError, KeyError, TypeError, ArithmeticError):
                logger.warning(
                    "query-result cache: undecodable entry for %s; re-running",
                    query_hash,
                )
            else:
                return CacheHit(
                    rows=result.rows,
                    schema=result.schema,
                    written_at=written_at,
                    truncated_reason=slots.truncated_reason,
                )
        return slots.error

    def listed_key(self, key: tuple[str, str, str]) -> tuple[str, str, str]:
        source_hash, query_hash, variables_hash = key
        return source_hash, stored_query_hash(query_hash), variables_hash

    def read(self, key: tuple[str, str, str]) -> CacheHit | None:
        """The rows under a listed entry's key, as ``entries()`` reports it.

        The key is the stored form, so no re-hashing: a listing already saw
        the row it wants. An undecodable payload is None, as in ``get()``.
        """
        with self._connection() as conn:
            row = conn.execute(
                "SELECT written_at_utc, payload, truncated_reason "
                "FROM _query_outcomes WHERE source_hash = ? AND query_hash = ? "
                "AND variables_hash = ? AND payload IS NOT NULL",
                list(key),
            ).fetchone()
        if row is None:
            return None
        written_at, payload, truncated_reason = row
        try:
            result = _decode(payload)
        except (zlib.error, ValueError, KeyError, TypeError, ArithmeticError):
            logger.warning("query-result cache: undecodable entry for %s", key[1])
            return None
        return CacheHit(
            rows=result.rows,
            schema=result.schema,
            written_at=written_at.replace(tzinfo=UTC),
            truncated_reason=truncated_reason,
        )

    def put(
        self,
        source_hash: str,
        query_hash: str,
        variables_hash: str,
        outcome: ResultRows | Exception,
        *,
        board_slug: str,
        query_name: str,
        source_name: str = "",
        truncated_reason: TruncatedReason | None = None,
        variables: CacheVariables | None = None,
    ) -> None:
        """Record *outcome* in its own slot, leaving the other one alone.

        A success replaces the rows and clears any error; an error records
        itself and does not touch the rows. Because the two writes are disjoint
        there is nothing to reconcile: a worker whose query timed out cannot
        destroy rows another worker just computed.
        """
        del (
            source_name
        )  # recorded by Cloud's backend; the local cache has no use for it
        recorded = variables or {}  # type-state: silent_fallback — None: no variables
        if isinstance(outcome, Exception):
            with self._connection() as conn:
                self._write_error_slot(
                    conn,
                    source_hash,
                    query_hash,
                    variables_hash,
                    outcome,
                    board_slug,
                    query_name,
                    recorded,
                )
            return
        payload = _encode(outcome)
        with self._connection() as conn:
            self._write_rows_slot(
                conn,
                source_hash,
                query_hash,
                variables_hash,
                payload,
                board_slug,
                query_name,
                truncated_reason,
                len(outcome.rows),
                list(outcome.schema),
                recorded,
            )
        logger.debug("Cached %d rows for %s", len(outcome.rows), query_hash)

    def entries(self) -> list[CacheEntry]:
        with self._connection() as conn:
            return self._read_entries(conn)
