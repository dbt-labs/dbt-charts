"""Base and helpers for the DuckDB-backed QueryResultCache.

Stage: EXECUTE (Cache)
Purpose: Hold the connection lifecycle, SQL helpers, and the outcome store
shared by the DuckDB-backed cache. TrivialDuckDBCache (replace-on-write) is the
sole subclass; it adds get/put and result-table management (_ensure_result_table,
_insert_rows, _drop_stale_result_tables) on top of this base.

The `_query_outcomes` table is authoritative: one row per cache key, carrying
**two independent slots** — the last successful run (`row_count`, `written_at_utc`,
with the payload in the per-key `_r_<hash>` table) and the last error
(`error_*`, `error_written_at_utc`). Either may be empty; both may be full. A
success write clears the error slot in the same statement, which is what makes
the merge fix the coexistence bugs the two-table design had. Nothing stores an
expiry — freshness is derived on read (see `cache_backend`).

`_r_<hash>` exists only for a rows slot with at least one row, so a zero-row
success is a bare outcome row, told apart from a vanished payload by
`row_count`.

Public helpers (_q, _result_table_name, _cache_safe_value, _infer_type) live
here and are imported by trivial_local_cache.py.

Dependencies:
    - duckdb (optional at module load; required at construction)
"""

from __future__ import annotations

import hashlib
import json
import logging
import threading
import traceback as tb_mod
from dataclasses import dataclass
from datetime import UTC, date, datetime
from decimal import Decimal
from pathlib import Path  # noqa: TID251 — DuckDB cache file (DCT_CACHE_PATH)
from typing import TYPE_CHECKING, Any, ClassVar

if TYPE_CHECKING:
    import duckdb

    HAS_DUCKDB = True
else:
    try:
        import duckdb

        HAS_DUCKDB = True
    except ImportError:
        duckdb = None
        HAS_DUCKDB = False

from dbt_charts._install_hint import install_hint
from dbt_charts.core.execute.cache_backend import (
    CachedQueryFailure,
    CacheRows,
    TruncatedReason,
)

logger = logging.getLogger(__name__)

# How long to keep a combined hash prefix for table names
_HASH_PREFIX = 12

# Exported: these helpers are defined here and imported by trivial_local_cache.py.
__all__ = [
    "_DuckDBResultCacheBase",
    "_Slots",
    "_cache_safe_value",
    "_infer_type",
    "_q",
    "_restore_decimal_columns",
    "_result_table_name",
    "_rows_from_result",
    "_uniform_decimal_columns",
]


# ─────────────────────────────────────────────────────────────────────────────
# SQL helper functions (imported by trivial_local_cache.py)
# ─────────────────────────────────────────────────────────────────────────────


def _q(identifier: str) -> str:
    """Quote a SQL identifier (already assumed safe)."""
    escaped = identifier.replace('"', '""')
    return f'"{escaped}"'


def _result_table_name(source_hash: str, query_hash: str, variables_hash: str) -> str:
    """Generate the table name for a cached result.

    Format: _r_{combined_prefix} where combined is a short hash of all three key parts.
    Short enough to stay under DuckDB's identifier limits.
    """
    combined = hashlib.sha256(
        f"{source_hash}:{query_hash}:{variables_hash}".encode()
    ).hexdigest()[:_HASH_PREFIX]
    return f"_r_{combined}"


def _cache_safe_value(value: Any) -> Any:
    """Coerce a Python value to something DuckDB's parameterized INSERT accepts.

    Decimals become float: DuckDB's DECIMAL is HUGEINT-backed and capped at
    precision 38, but BigQuery NUMERIC can return precision > 38 (e.g. NUMERIC(47,38)
    on fan-out-deduplicated SUM aggregates). float's 15 significant digits are
    plenty for dashboard rendering, but is lossy — a uniformly-Decimal column
    (every value in the column is exactly a Decimal) is stored as VARCHAR
    text instead, exact, via ``_uniform_decimal_columns``/callers of this
    function; this float coercion only ever applies to a Decimal value in a
    genuinely mixed-type column, where exactness is not promised (see
    ``QueryResultCache.get``'s type-fidelity contract). list/dict become
    JSON text for the JSON column.
    """
    if isinstance(value, Decimal):
        return float(value)
    if isinstance(value, (list, dict)):
        return json.dumps(value)
    return value


def _uniform_decimal_columns(rows: CacheRows) -> frozenset[str]:
    """Columns whose every non-None value is exactly ``Decimal``.

    DuckDB's fixed-precision DECIMAL can't hold every warehouse NUMERIC
    (see ``_cache_safe_value``), and float coercion is lossy — so a
    uniformly-Decimal column is stored as VARCHAR text (``str(value)``,
    exact) instead, a sidecar type recorded in ``_query_outcomes.
    decimal_columns`` and restored by ``_restore_decimal_columns`` on read.
    A column that mixes Decimal with any other type is not uniform and
    keeps the existing (lossy) float coercion — exactness is only promised
    where every value in the column shares one type, same as the temporal
    contract in ``PostgresResultCache``.
    """
    if not rows:
        return frozenset()
    decimal_cols = set()
    for col in rows[0]:
        values = [r[col] for r in rows if r.get(col) is not None]
        if values and all(type(v) is Decimal for v in values):
            decimal_cols.add(col)
    return frozenset(decimal_cols)


def _restore_decimal_columns(
    rows: CacheRows, decimal_columns: frozenset[str]
) -> CacheRows:
    """Restore each *decimal_columns* entry's stored VARCHAR text to Decimal.

    A None value (nullable column, NULL row) is left alone.
    """
    if not decimal_columns:
        return rows
    restored = []
    for row in rows:
        new_row = dict(row)
        for col in decimal_columns:
            value = new_row.get(col)
            if value is not None:
                new_row[col] = Decimal(value)
        restored.append(new_row)
    return restored


def _infer_type(value: Any) -> str:
    if value is None:
        return "VARCHAR"
    if isinstance(value, bool):
        return "BOOLEAN"
    if isinstance(value, int):
        return "BIGINT"
    if isinstance(value, (float, Decimal)):
        return "DOUBLE"
    if isinstance(value, datetime):
        # A naive datetime maps to DuckDB's naive TIMESTAMP; a tz-aware one
        # (BigQuery TIMESTAMP, Postgres timestamptz, Snowflake TIMESTAMP_TZ)
        # must map to TIMESTAMP WITH TIME ZONE or the round-trip silently
        # drops tzinfo — the naive TIMESTAMP column has nowhere to put it.
        return "TIMESTAMP" if value.tzinfo is None else "TIMESTAMP WITH TIME ZONE"
    # datetime is itself a date subclass, so this must come after the
    # datetime check — a bare date left unhandled here fell through to
    # VARCHAR, and a DATE watermark round-tripped through the cache as a
    # plain string.
    if isinstance(value, date):
        return "DATE"
    if isinstance(value, (list, dict)):
        return "JSON"
    return "VARCHAR"


def _utc_wall_clock() -> datetime:
    """The current UTC wall clock, naive — what the TIMESTAMP columns store.

    Binding an *aware* datetime to DuckDB's ``TIMESTAMP`` (which is
    ``WITHOUT TIME ZONE``) makes the driver cast it, and that cast resolves
    against the connection's ``TimeZone`` setting — with ICU present that is the
    machine's zone, so the column would hold the *local* wall clock. Verified:
    under ``TZ=Asia/Tokyo`` an aware 06:38Z reads back as 15:38.

    Dropping the tzinfo here removes the cast, so the stored value is a UTC wall
    clock by construction rather than by driver behavior — which is what lets
    ``_read_slots`` re-attach UTC as a pure relabel. Without this, every reading
    would be off by the machine's offset and, east of Greenwich, entries would
    read as future-dated and never expire.
    """
    return datetime.now(UTC).replace(tzinfo=None)


def _rows_from_result(
    result: duckdb.DuckDBPyConnection, limit: int | None = None
) -> CacheRows:
    """Reconstruct row dicts from an executed DuckDB result, decoding JSON columns.

    list/dict columns are stored as JSON text on write (``_cache_safe_value``),
    and DuckDB hands a JSON-typed column back as a Python ``str``. Decode those
    columns here so the round-trip is symmetric — callers (and the renderer)
    expect the original ``list``/``dict``, not the serialized string.

    ``limit`` bounds the fetch itself via ``fetchmany()`` instead of pulling
    every row over and slicing after — None (the default) fetches everything,
    unaffected for every caller that doesn't pass one.
    """
    description = result.description
    columns = [d[0] for d in description]
    json_cols = {i for i, d in enumerate(description) if str(d[1]) == "JSON"}
    rows = result.fetchmany(limit) if limit else result.fetchall()
    if not json_cols:
        return [dict(zip(columns, row, strict=False)) for row in rows]
    return [
        dict(
            zip(
                columns,
                [
                    json.loads(v) if i in json_cols and v is not None else v
                    for i, v in enumerate(row)
                ],
                strict=False,
            )
        )
        for row in rows
    ]


@dataclass(frozen=True)
class _Slots:
    """Both slots of one cache key, as read in a single lookup.

    ``rows_written_at`` is None when nothing has succeeded for this key yet;
    ``error`` is None when nothing has failed, or the last failure is past its
    retry window. Both empty is indistinguishable from an absent row, which is
    what ``EMPTY`` represents.
    """

    rows_written_at: datetime | None
    row_count: int
    error: CachedQueryFailure | None
    truncated_reason: TruncatedReason | None
    # Columns stored as VARCHAR text (see _uniform_decimal_columns) that
    # get() must convert back to Decimal on read.
    decimal_columns: frozenset[str]

    EMPTY: ClassVar[_Slots]


_Slots.EMPTY = _Slots(
    rows_written_at=None,
    row_count=0,
    error=None,
    truncated_reason=None,
    decimal_columns=frozenset(),
)


# ─────────────────────────────────────────────────────────────────────────────
# Abstract base class
# ─────────────────────────────────────────────────────────────────────────────


class _DuckDBResultCacheBase:
    """Abstract base for DuckDB-backed result caches.

    Manages: connection lifecycle, outcome store (_query_outcomes).

    Subclasses must implement: get, put, and any storage-specific setup
    (_drop_*_tables, _ensure_result_table, _insert_rows).
    """

    def __init__(
        self,
        db_path: Path | None = None,
        failure_ttl_seconds: int = 900,
        *,
        _class_name: str,
    ) -> None:
        if not HAS_DUCKDB:
            raise ImportError(
                f"DuckDB is required to use {_class_name}. "
                f"Install it with: {install_hint('duckdb')}"
            )

        self.db_path = db_path
        self.failure_ttl_seconds = failure_ttl_seconds
        self._lock = threading.RLock()

        if db_path:
            # Auto-provision: the parent directory may not exist yet (e.g. a
            # project's first opt-in `cache: {path: .dct/cache.duckdb}`)
            # — the documented contract is "created if absent", no manual
            # `mkdir` step.
            db_path.parent.mkdir(parents=True, exist_ok=True)
            self.conn = duckdb.connect(str(db_path))
        else:
            self.conn = duckdb.connect(":memory:")

        self.conn.execute("SET enable_object_cache = true")

    def _read_slots(
        self, source_hash: str, query_hash: str, variables_hash: str
    ) -> _Slots:
        """Read both slots for this key in one lookup. Callers hold ``self._lock``.

        A missing row and two empty slots are the same thing to every caller, so
        an absent key comes back as ``_Slots.EMPTY`` rather than None.
        """
        row = self.conn.execute(
            """
            SELECT row_count, written_at_utc, truncated_reason, decimal_columns,
                   error_class, error_message, traceback, error_written_at_utc
            FROM _query_outcomes
            WHERE source_hash = ? AND query_hash = ? AND variables_hash = ?
            """,
            [source_hash, query_hash, variables_hash],
        ).fetchone()
        if row is None:
            return _Slots.EMPTY
        (
            row_count,
            written_at_utc,
            truncated_reason,
            decimal_columns_json,
            err_class,
            err_message,
            traceback,
            err_written_at,
        ) = row
        # DuckDB TIMESTAMP is naive and both slots are written as UTC wall clock
        # (`_utc_wall_clock()`), so re-attaching UTC here is a relabel,
        # not a conversion. Do it before any arithmetic: every comparison below
        # and every timestamp handed to a caller is then aware UTC, matching
        # what Cloud's backend returns. Never `.replace(tzinfo=utc)` a
        # naive-*local* value — that shifts the reading by the machine's offset.
        if written_at_utc is not None:
            written_at_utc = written_at_utc.replace(tzinfo=UTC)
        if err_written_at is not None:
            err_written_at = err_written_at.replace(tzinfo=UTC)
        error = None
        # The error slot expires on the backend's retry window — a transient
        # warehouse blip must be re-tried in minutes however long this query is
        # willing to serve stale rows. 0 disables error caching outright.
        if err_written_at is not None and self.failure_ttl_seconds > 0:
            elapsed = (datetime.now(UTC) - err_written_at).total_seconds()
            if elapsed <= self.failure_ttl_seconds:
                error = CachedQueryFailure(
                    error_class=err_class,
                    error_message=err_message,
                    traceback=traceback,
                    failed_at=err_written_at,
                )
        return _Slots(
            rows_written_at=written_at_utc,
            row_count=row_count or 0,
            error=error,
            truncated_reason=truncated_reason,
            decimal_columns=(
                frozenset(json.loads(decimal_columns_json))
                if decimal_columns_json
                else frozenset()
            ),
        )

    def _write_rows_slot(
        self,
        source_hash: str,
        query_hash: str,
        variables_hash: str,
        row_count: int,
        board_slug: str,
        query_name: str,
        truncated_reason: TruncatedReason | None = None,
        decimal_columns: frozenset[str] = frozenset(),
    ) -> None:
        """Record a successful run, clearing the error slot in the same statement.

        That clearing is the whole point of the merge: with two tables a stale
        failure could outlive the success that superseded it, and there was no
        way to retire it atomically. Callers hold ``self._lock``.

        ``truncated_reason`` replaces wholesale, never merges — a re-run that
        comes back under the ceiling must clear a prior truncation, not keep
        reporting one that no longer applies. ``decimal_columns`` replaces
        wholesale too, same reasoning — see ``_uniform_decimal_columns``.
        """
        self.conn.execute(
            """
            INSERT INTO _query_outcomes
                (source_hash, query_hash, variables_hash, board_slug, query_name,
                 row_count, written_at_utc, truncated_reason, decimal_columns,
                 error_class, error_message, traceback, error_written_at_utc)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, NULL, NULL, NULL, NULL)
            ON CONFLICT (source_hash, query_hash, variables_hash) DO UPDATE SET
                board_slug = excluded.board_slug,
                query_name = excluded.query_name,
                row_count = excluded.row_count,
                written_at_utc = excluded.written_at_utc,
                truncated_reason = excluded.truncated_reason,
                decimal_columns = excluded.decimal_columns,
                error_class = NULL,
                error_message = NULL,
                traceback = NULL,
                error_written_at_utc = NULL
            """,
            [
                source_hash,
                query_hash,
                variables_hash,
                board_slug,
                query_name,
                row_count,
                _utc_wall_clock(),
                truncated_reason,
                json.dumps(sorted(decimal_columns)) if decimal_columns else None,
            ],
        )

    def _write_error_slot(
        self,
        source_hash: str,
        query_hash: str,
        variables_hash: str,
        exception: Exception,
        board_slug: str,
        query_name: str,
    ) -> None:
        """Record an error, leaving any rows slot untouched.

        The two writes touch disjoint columns, so a worker whose query failed
        can never destroy rows another worker just computed — no lock, no
        read-then-write window. Callers hold ``self._lock``.
        """
        self.conn.execute(
            """
            INSERT INTO _query_outcomes
                (source_hash, query_hash, variables_hash, board_slug, query_name,
                 row_count, written_at_utc,
                 error_class, error_message, traceback, error_written_at_utc)
            VALUES (?, ?, ?, ?, ?, NULL, NULL, ?, ?, ?, ?)
            ON CONFLICT (source_hash, query_hash, variables_hash) DO UPDATE SET
                board_slug = excluded.board_slug,
                query_name = excluded.query_name,
                error_class = excluded.error_class,
                error_message = excluded.error_message,
                traceback = excluded.traceback,
                error_written_at_utc = excluded.error_written_at_utc
            """,
            [
                source_hash,
                query_hash,
                variables_hash,
                board_slug,
                query_name,
                type(exception).__name__,
                str(exception),
                "".join(tb_mod.format_exception(exception)),
                _utc_wall_clock(),
            ],
        )

    def clear(self, source_hash: str, query_hash: str, variables_hash: str) -> None:
        """Remove this key's outcome row and any result rows it points at."""
        with self._lock:
            self.conn.execute(
                "DELETE FROM _query_outcomes WHERE source_hash = ? AND query_hash = ? AND variables_hash = ?",
                [source_hash, query_hash, variables_hash],
            )
            self._drop_result_table(
                _result_table_name(source_hash, query_hash, variables_hash)
            )

    def _drop_result_table(self, table_name: str) -> None:
        """Drop a per-key result table if it exists. Callers hold ``self._lock``."""
        if self._table_exists(table_name):
            self.conn.execute(f"DROP TABLE {_q(table_name)}")

    def close(self) -> None:
        with self._lock:
            self.conn.close()

    # ── Cache-ref composition ({{ queries.X.cache }}) ────────────────────────
    # Composition runs in an isolated in-process DuckDB fed the upstream rows by
    # the executor (dbt_charts.core.execute.cache_composition); this backend only
    # advertises the capability.

    def supports_cache_refs(self) -> bool:
        """DuckDB-backed cache always supports cache-ref composition."""
        return True

    def _drop_stale_outcomes_and_payloads(self, reason: str) -> None:
        """Drop _query_outcomes and its orphaned _r_* payload tables.

        Shared by every "on-disk schema predates a column this version
        writes" check in _ensure_schema — CREATE TABLE IF NOT EXISTS would
        otherwise leave the mismatched columns in place. It's a disposable
        cache: every dropped key re-warms naturally on the next request, and
        the payload tables a dropped outcome row pointed at are unreachable
        the moment the row is gone (nothing else indexes them).
        """
        self.conn.execute("DROP TABLE IF EXISTS _query_outcomes")
        orphans = self.conn.execute(
            "SELECT table_name FROM information_schema.tables "
            "WHERE table_type = 'BASE TABLE' AND table_name LIKE '_r_%'"
        ).fetchall()
        for (tbl,) in orphans:
            self.conn.execute(f"DROP TABLE IF EXISTS {_q(tbl)}")
        logger.info(
            "trivial cache: dropped %s _query_outcomes and %d payload "
            "table(s) on open; they will re-warm",
            reason,
            len(orphans),
        )

    def _ensure_schema(self) -> None:
        """Create the outcome table if it doesn't exist.

        Also drops `_query_failures` left by a cache file written before the
        two stores were merged: its rows are re-derivable and the table is
        never read again.
        """
        self.conn.execute("DROP TABLE IF EXISTS _query_failures")
        columns = self._column_names("_query_outcomes")
        # A cache file written before the UTC pin holds naive *local* timestamps
        # under the old `written_at` / `error_written_at` names. The column type
        # and position are unchanged, so CREATE TABLE IF NOT EXISTS would keep
        # them and the read path would relabel local as UTC — east of Greenwich
        # every row then reads younger than it is by the machine's offset and is
        # served past its authored ttl, silently. The `_utc` suffix makes those
        # rows unreadable rather than misreadable: their presence is the version
        # marker, so drop once and re-warm.
        if "written_at" in columns:
            self._drop_stale_outcomes_and_payloads("pre-UTC")
            columns = self._column_names("_query_outcomes")
        # Same pattern for the face->board rename: an on-disk file created
        # before the rename still carries `face_slug`, and CREATE TABLE IF NOT
        # EXISTS would keep it, breaking the first INSERT naming `board_slug`.
        if "face_slug" in columns:
            self._drop_stale_outcomes_and_payloads("pre-rename")
            columns = self._column_names("_query_outcomes")
        # Same pattern for the truncated_reason column added to persist the
        # truncation fact alongside cached rows: an on-disk file from before
        # that change has no such column, and CREATE TABLE IF NOT EXISTS
        # would keep it missing, breaking the first INSERT naming it.
        if columns and "truncated_reason" not in columns:
            self._drop_stale_outcomes_and_payloads("pre-truncated_reason")
            columns = self._column_names("_query_outcomes")
        # Same pattern for decimal_columns, added to record which columns of
        # a rows slot are stored as VARCHAR-text Decimal (see
        # _uniform_decimal_columns) so get() knows to restore them.
        if columns and "decimal_columns" not in columns:
            self._drop_stale_outcomes_and_payloads("pre-decimal_columns")
        self.conn.execute(
            """
            CREATE TABLE IF NOT EXISTS _query_outcomes (
                source_hash TEXT NOT NULL,
                query_hash TEXT NOT NULL,
                variables_hash TEXT NOT NULL,
                board_slug TEXT NOT NULL,
                query_name TEXT NOT NULL,
                row_count BIGINT,
                written_at_utc TIMESTAMP,
                truncated_reason TEXT,
                decimal_columns TEXT,
                error_class TEXT,
                error_message TEXT,
                traceback TEXT,
                error_written_at_utc TIMESTAMP,
                PRIMARY KEY (source_hash, query_hash, variables_hash)
            )
            """
        )

    def _table_exists(self, name: str) -> bool:
        try:
            result = self.conn.execute(
                "SELECT COUNT(*) FROM information_schema.tables WHERE table_name = ?",
                [name],
            ).fetchone()
            return bool(result and result[0] > 0)
        except duckdb.CatalogException:
            return False

    def _column_names(self, table_name: str) -> set[str]:
        try:
            rows = self.conn.execute(
                "SELECT column_name FROM information_schema.columns WHERE table_name = ?",
                [table_name],
            ).fetchall()
            return {r[0] for r in rows}
        except duckdb.CatalogException:
            return set()
