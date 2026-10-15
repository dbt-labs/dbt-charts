"""Local result cache backend.

Stage: EXECUTE (Cache)
Purpose: Replace-on-write DuckDB cache for `dct render` and `dct serve`. One
outcome row per cache key, the result rows stored on it as a single payload —
no history, no generations.

Payload encoding: zlib-compressed JSON of ``{"types": {...}, "rows": [...]}``.
JSON holds what a query result holds — nested values, columns of mixed type,
rows that do not share a key set — and keeps each row's key order. It has no
date, datetime or Decimal, so ``types`` names each column whose every value is
one of those, and the read restores them; that is the per-column type tag the
``QueryResultCache.get`` contract asks a lossy store to carry.
"""

from __future__ import annotations

import json
import logging
import zlib
from collections.abc import Callable
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from pathlib import Path  # noqa: TID251 — DuckDB cache file (DCT_CACHE_PATH)
from typing import Any

from dbt_charts.core.execute._duckdb_cache_base import _DuckDBResultCacheBase
from dbt_charts.core.execute.cache_backend import (
    CacheHit,
    CacheOutcome,
    CacheRows,
    TruncatedReason,
)

logger = logging.getLogger(__name__)

_RESTORE: dict[str, Callable[[str], date | Decimal]] = {
    "date": date.fromisoformat,
    "datetime": datetime.fromisoformat,
    "decimal": Decimal,
}


# datetime is a date subclass, so it is tested first.
_TAGGED_TYPES: tuple[tuple[type, str], ...] = (
    (datetime, "datetime"),
    (date, "date"),
    (Decimal, "decimal"),
)


def _column_types(rows: CacheRows) -> dict[str, str]:
    """Tag each column whose every non-None value is one restorable type."""
    seen: dict[str, set[str | None]] = {}
    for row in rows:
        for column, value in row.items():
            if value is not None:
                seen.setdefault(column, set()).add(
                    next(
                        (tag for cls, tag in _TAGGED_TYPES if isinstance(value, cls)),
                        None,
                    )
                )
    return {
        column: tag
        for column, tags in seen.items()
        if len(tags) == 1 and (tag := next(iter(tags))) is not None
    }


def _json_default(
    value: Any,  # type-state: explicit_any — json.dumps hands its default hook whatever it could not serialize
) -> str | float:
    """Serialize a value JSON has no type for.

    Reached for a tagged column's dates, and for values nothing restores: one
    nested in a list or dict, or in a column of mixed type, which the cache
    contract passes through in the store's own form. A Decimal there stays a
    number.
    """
    if isinstance(value, (date, datetime)):
        return value.isoformat()
    if isinstance(value, Decimal):
        return float(value)
    return str(value)


def _encode(rows: CacheRows) -> bytes:
    types = _column_types(rows)
    # float cannot hold every warehouse NUMERIC, so a Decimal column is stored
    # as text; the default hook would otherwise turn it into a lossy number.
    exact = {column for column, tag in types.items() if tag == "decimal"}
    if exact:
        rows = [
            {
                column: str(value) if column in exact and value is not None else value
                for column, value in row.items()
            }
            for row in rows
        ]
    document = json.dumps({"types": types, "rows": rows}, default=_json_default)
    return zlib.compress(document.encode(), level=1)


def _decode(payload: bytes) -> CacheRows:
    document = json.loads(zlib.decompress(payload))
    rows: CacheRows = document["rows"]
    for column, tag in document["types"].items():
        restore = _RESTORE[tag]
        for row in rows:
            if row.get(column) is not None:
                row[column] = restore(row[column])
    return rows


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
                rows = _decode(slots.payload)
            except (zlib.error, ValueError, KeyError, TypeError, ArithmeticError):
                logger.warning(
                    "query-result cache: undecodable entry for %s; re-running",
                    query_hash,
                )
            else:
                return CacheHit(
                    rows=rows,
                    written_at=written_at,
                    truncated_reason=slots.truncated_reason,
                )
        return slots.error

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

        A success replaces the rows and clears any error; an error records
        itself and does not touch the rows. Because the two writes are disjoint
        there is nothing to reconcile: a worker whose query timed out cannot
        destroy rows another worker just computed.
        """
        del (
            source_name
        )  # recorded by Cloud's backend; the local cache has no use for it
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
            )
        logger.debug("Cached %d rows for %s", len(outcome), query_hash)
