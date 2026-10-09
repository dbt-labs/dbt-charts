"""Trivial local result cache backend — M3 Phase 2.

Stage: EXECUTE (Cache)
Purpose: Replace-on-write DuckDB cache for `dct serve`. Strips all
history/incremental/PK machinery from the full DuckDBCache.

Schema decision: keep the on-disk cache file, drop the generations. One outcome
row per cache key, plus one result-data table per key holding its rows. No
set_seq/run_seq columns.

Stale-file handling: a cache file written by an older schema carries columns we
no longer write (_snapshot_set_seq/_snapshot_run_seq from the generational
DuckDBCache; _run_timestamp from before the write timestamp moved onto the
outcome row). On open, we detect these by checking result-table column sets and
drop/recreate. It's a disposable cache; data loss is correct behavior.
"""

from __future__ import annotations

import contextlib
import logging
from datetime import datetime, timedelta, timezone
from pathlib import Path  # noqa: TID251 — DuckDB cache file (DCT_CACHE_PATH)
from typing import Any

from dbt_charts.core.execute._duckdb_cache_base import (
    _cache_safe_value,
    _DuckDBResultCacheBase,
    _infer_type,
    _q,
    _restore_decimal_columns,
    _result_table_name,
    _rows_from_result,
    _uniform_decimal_columns,
)
from dbt_charts.core.execute.cache_backend import (
    CacheHit,
    CacheOutcome,
    CacheRows,
    TruncatedReason,
)

logger = logging.getLogger(__name__)


# Columns from superseded result-table schemas that make a table unusable.
_STALE_COLS = {"_snapshot_set_seq", "_snapshot_run_seq", "_run_timestamp"}


class TrivialDuckDBCache(_DuckDBResultCacheBase):
    """Replace-on-write DuckDB cache for local `dct serve`.

    Implements QueryResultCache. Backed by a DuckDB file so results survive
    `dct serve` restarts (warm cache).

    put() always replaces the existing outcome for a key. There is no history,
    no generations, no set_seq/run_seq, no primary-key incremental path.

    Attributes:
        db_path: Path to DuckDB file (or None for :memory:)
        failure_ttl_seconds: Retry window for a cached error; 0 disables error
            caching, so a failing query is re-tried on every render.
    """

    def __init__(
        self,
        db_path: Path | None = None,
        failure_ttl_seconds: int = 900,
    ) -> None:
        super().__init__(
            db_path=db_path,
            failure_ttl_seconds=failure_ttl_seconds,
            _class_name="TrivialDuckDBCache",
        )
        self._drop_stale_result_tables()
        self._ensure_schema()

    # ─────────────────────────────────────────────────────────────────────
    # QueryResultCache Protocol implementation
    # ─────────────────────────────────────────────────────────────────────

    def get(
        self,
        source_hash: str,
        query_hash: str,
        variables_hash: str,
        *,
        ttl: timedelta | None = None,
    ) -> CacheOutcome | None:
        """Return this key's live outcome — rows or the cached error — else None.

        Read order is the two-table read order, preserved: **rows first**, and
        only if this reader can no longer use them does the error slot come
        into play. So a caller inside its own ttl is never handed a failure for
        data it is still entitled to, even when a sibling query over the same
        SQL failed a moment ago.

        `row_count` tells a zero-row success (a real hit, with no result table
        to keep) from a payload that went missing under a row-bearing slot (a
        miss — the caller re-runs and replaces it).
        """
        with self._lock:
            slots = self._read_slots(source_hash, query_hash, variables_hash)
            written_at = slots.rows_written_at
            if written_at is not None and (
                ttl is None or datetime.now(timezone.utc) - written_at <= ttl
            ):
                if slots.row_count == 0:
                    return CacheHit(
                        rows=[],
                        written_at=written_at,
                        truncated_reason=slots.truncated_reason,
                    )
                tbl = _result_table_name(source_hash, query_hash, variables_hash)
                if self._table_exists(tbl):
                    rows = _rows_from_result(
                        self.conn.execute(f"SELECT * FROM {_q(tbl)}")
                    )
                    rows = _restore_decimal_columns(rows, slots.decimal_columns)
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
        there is nothing to reconcile and nothing to lock: a worker whose query
        timed out cannot destroy rows another worker just computed, and a
        success cannot silently strand a stale failure the way two tables could.

        """
        del (
            source_name
        )  # recorded by Cloud's backend; the local cache has no use for it
        tbl = _result_table_name(source_hash, query_hash, variables_hash)

        with self._lock:
            if isinstance(outcome, Exception):
                self._write_error_slot(
                    source_hash,
                    query_hash,
                    variables_hash,
                    outcome,
                    board_slug,
                    query_name,
                )
                return
            decimal_columns = _uniform_decimal_columns(outcome)
            if outcome:
                self._ensure_result_table(tbl, outcome, decimal_columns)
                self.conn.execute(f"DELETE FROM {_q(tbl)}")
                self._insert_rows(tbl, outcome, decimal_columns)
            else:
                # A zero-row success keeps no result table — the outcome row
                # alone records it, and dropping any prior table is what stops
                # the previous run's rows from being served as this one's.
                self._drop_result_table(tbl)
            self._write_rows_slot(
                source_hash,
                query_hash,
                variables_hash,
                len(outcome),
                board_slug,
                query_name,
                truncated_reason,
                decimal_columns,
            )
        logger.debug("Cached %d rows to %s", len(outcome), tbl)

    # ─────────────────────────────────────────────────────────────────────
    # Private helpers
    # ─────────────────────────────────────────────────────────────────────

    def _drop_stale_result_tables(self) -> None:
        """Drop result tables that carry a superseded column schema.

        A pre-existing cache file written by the generational DuckDBCache has
        _snapshot_set_seq/_snapshot_run_seq columns on every result table; one
        written before the outcome merge has a per-row _run_timestamp. The
        current schema writes neither, and CREATE TABLE IF NOT EXISTS would
        leave the mismatched columns in place. Drop them — it's a disposable
        cache, and it re-warms naturally on the next request.
        """
        import duckdb

        try:
            tables = self.conn.execute(
                """
                SELECT table_name FROM information_schema.tables
                WHERE table_type = 'BASE TABLE'
                  AND table_name LIKE '_r_%'
                """
            ).fetchall()
        except duckdb.CatalogException:
            return

        dropped = 0
        for (tbl,) in tables:
            cols = self._column_names(tbl)
            if cols & _STALE_COLS:
                with contextlib.suppress(Exception):
                    self.conn.execute(f"DROP TABLE IF EXISTS {_q(tbl)}")
                    dropped += 1

        if dropped:
            logger.info(
                "trivial cache: dropped %d stale seq-column table(s) on open", dropped
            )

    def _ensure_result_table(
        self,
        table_name: str,
        data: CacheRows,
        decimal_columns: frozenset[str] = frozenset(),
    ) -> None:
        if self._table_exists(table_name):
            return
        # Scan all rows for the first non-None value per column so union_by_name
        # padding (which fills row 0 with None for absent columns) doesn't collapse
        # numeric columns to VARCHAR. A uniformly-Decimal column (decimal_columns)
        # forces VARCHAR explicitly instead — see _uniform_decimal_columns for why.
        cols = [
            f'"{col_name}" '
            + (
                "VARCHAR"
                if col_name in decimal_columns
                else _infer_type(
                    next(
                        (r[col_name] for r in data if r.get(col_name) is not None), None
                    )
                )
            )
            for col_name in data[0]
        ]
        self.conn.execute(f"CREATE TABLE {_q(table_name)} ({', '.join(cols)})")

    def _insert_rows(
        self,
        table_name: str,
        data: CacheRows,
        decimal_columns: frozenset[str] = frozenset(),
    ) -> None:
        # Bulk-load via a registered Arrow table + one INSERT … SELECT, not a
        # per-row executemany: DuckDB's executemany binds row by row (~47s for
        # 100k rows), while an Arrow scan is a single columnar append (~100ms).
        # Values are still coerced (Decimal→float outside decimal_columns,
        # list/dict→JSON text) so the typed result table from
        # _ensure_result_table — including its JSON columns, which
        # _rows_from_result decodes on read — is unchanged. A uniformly-Decimal
        # column instead gets str(value) — exact — matching the VARCHAR type
        # _ensure_result_table gave it.
        import pyarrow as pa

        keys = list(data[0])
        coerced = [
            {
                k: (
                    str(row[k])
                    if k in decimal_columns and row.get(k) is not None
                    else _cache_safe_value(row.get(k))
                )
                for k in keys
            }
            for row in data
        ]
        try:
            arrow = pa.Table.from_pylist(coerced)
        except (pa.ArrowInvalid, pa.ArrowTypeError, pa.ArrowNotImplementedError):
            # A genuinely heterogeneous column Arrow can't hold in one array —
            # e.g. an HTTP/JSON source returning str in one row and int in another
            # for the same column. Fall back to the per-row bind, where DuckDB
            # casts each cell to the column type, exactly as the cache did before
            # the bulk path. Same rows and values, just slower — not a wrong-data
            # fallback; these inputs are rare and small in practice.
            self._insert_rows_row_by_row(table_name, keys, coerced)
            return
        self.conn.register("_dct_bulk_ingest", arrow)
        try:
            self.conn.execute(
                f"INSERT INTO {_q(table_name)} SELECT * FROM _dct_bulk_ingest"
            )
        finally:
            self.conn.unregister("_dct_bulk_ingest")

    def _insert_rows_row_by_row(
        self, table_name: str, keys: list[str], coerced: list[dict[str, Any]]
    ) -> None:
        placeholders = ", ".join("?" * len(keys))
        param_rows = [[row[k] for k in keys] for row in coerced]
        self.conn.executemany(
            f"INSERT INTO {_q(table_name)} VALUES ({placeholders})", param_rows
        )
