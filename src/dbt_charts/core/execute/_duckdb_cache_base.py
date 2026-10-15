"""Connection lifecycle and outcome store for the DuckDB-backed QueryResultCache.

Stage: EXECUTE (Cache)
Purpose: Hold the connection lifecycle and the `_query_outcomes` store.
TrivialDuckDBCache (replace-on-write) is the sole subclass; it adds get/put and
the payload encoding on top of this base.

`_query_outcomes` is the only table: one row per cache key, carrying **two
independent slots** — the last successful run (`written_at_utc`, `payload`) and
the last error (`error_*`, `error_written_at_utc`). Either may be empty; both
may be full. A success write clears the error slot in the same statement, so a
stale failure cannot outlive the success that superseded it. Nothing stores an
expiry — freshness is derived on read (see `cache_backend`).

A result is one `payload` value on its key's row, never a table of its own:
DuckDB loads the whole catalog on connect, so a table per key makes opening the
file cost in proportion to the number of cached queries.

A file-backed cache connects for one read or write and disconnects. DuckDB
allows a single read-write process per file, so a connection held for the life
of the process would lock every other `dct` process out of a shared cache.

Dependencies:
    - duckdb (optional at module load; required at construction)
"""

from __future__ import annotations

import logging
import threading
import time
import traceback as tb_mod
from collections.abc import Generator
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path  # noqa: TID251 — DuckDB cache file (DCT_CACHE_PATH)
from typing import TYPE_CHECKING, ClassVar

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
from dbt_charts.core.diagnostics.codes_execute import ERR_CACHE_FILE_UNAVAILABLE
from dbt_charts.core.diagnostics.execution import ExecutionError
from dbt_charts.core.execute.cache_backend import (
    CachedQueryFailure,
    TruncatedReason,
)

logger = logging.getLogger(__name__)

# Writes collect in the write-ahead log and are checkpointed into the file once
# the log passes this size, not on every disconnect. A checkpoint cannot extend
# a block an earlier one wrote, so checkpointing each single-row write costs a
# whole 256 KB block per cached query. The log is replayed on every connect, so
# it is kept small enough that a read stays in the low milliseconds. DuckDB's
# own `checkpoint_threshold` cannot do this: it compares an estimate that a
# one-row write already exceeds at any threshold this small.
_CHECKPOINT_WAL_BYTES = 64_000

_OUTCOME_COLUMNS = {
    "source_hash": "TEXT NOT NULL",
    "query_hash": "TEXT NOT NULL",
    "variables_hash": "TEXT NOT NULL",
    "board_slug": "TEXT NOT NULL",
    "query_name": "TEXT NOT NULL",
    "written_at_utc": "TIMESTAMP",
    "truncated_reason": "TEXT",
    "payload": "BLOB",
    "error_class": "TEXT",
    "error_message": "TEXT",
    "traceback": "TEXT",
    "error_written_at_utc": "TIMESTAMP",
}


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


@dataclass(frozen=True)
class _Slots:
    """Both slots of one cache key, as read in a single lookup.

    ``rows_written_at`` and ``payload`` are None together, when nothing has
    succeeded for this key yet; ``error`` is None when nothing has failed, or
    the last failure is past its retry window. Both empty is indistinguishable
    from an absent row, which is what ``EMPTY`` represents.
    """

    rows_written_at: datetime | None
    payload: bytes | None
    error: CachedQueryFailure | None
    truncated_reason: TruncatedReason | None

    EMPTY: ClassVar[_Slots]


_Slots.EMPTY = _Slots(
    rows_written_at=None, payload=None, error=None, truncated_reason=None
)


class _DuckDBResultCacheBase:
    """Abstract base for DuckDB-backed result caches.

    Manages: connection lifecycle, outcome store (_query_outcomes).

    Subclasses must implement: get and put.
    """

    def __init__(
        self,
        db_path: Path | None = None,
        failure_ttl_seconds: int = 900,
        lock_timeout_seconds: float = 10.0,
        max_age: timedelta | None = None,
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
        self.lock_timeout_seconds = lock_timeout_seconds
        self.max_age = max_age
        self._lock = threading.RLock()
        # An in-memory database lives only as long as its connection, and no
        # other process can reach it, so that one connection is kept.
        self._memory_conn = None if db_path else duckdb.connect(":memory:")

        # The file holds warehouse query results, so a directory or file created
        # here is readable by its owner alone. One that already exists keeps the
        # mode it has. DuckDB creates the file itself, with the process umask,
        # and will not open an empty placeholder, so the file's mode is set
        # right after the connect that creates it, before any result is written.
        is_new_file = db_path is not None and not db_path.exists()
        if db_path:
            # Auto-provision: the parent directory may not exist yet (e.g. a
            # project's first opt-in `cache: {path: .dct/cache.duckdb}`)
            # — the documented contract is "created if absent", no manual
            # `mkdir` step.
            db_path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)

        with self._connection() as conn:
            if db_path and is_new_file:
                db_path.chmod(0o600)
            self._ensure_schema(conn)
            if max_age is not None:
                self._prune(conn, max_age)

    @contextmanager
    def _connection(self) -> Generator[duckdb.DuckDBPyConnection]:
        """Yield a connection for one read or write, holding ``self._lock``."""
        with self._lock:
            if self._memory_conn is not None:
                yield self._memory_conn
                return
            conn = self._connect_to_file()
            try:
                yield conn
                wal = Path(f"{self.db_path}.wal")
                if wal.exists() and wal.stat().st_size > _CHECKPOINT_WAL_BYTES:
                    conn.execute("CHECKPOINT")
            finally:
                conn.close()

    def _connect_to_file(self) -> duckdb.DuckDBPyConnection:
        """Connect to the cache file, waiting out another process's hold on it.

        Every ``IOException`` is retried rather than only a lock conflict: the
        conflict has no exception type of its own, and its wording differs by
        platform. A file that is unreadable for another reason fails the same
        way once the wait runs out, with DuckDB's own message attached.
        """
        deadline = time.monotonic() + self.lock_timeout_seconds
        delay = 0.005
        while True:
            try:
                conn = duckdb.connect(str(self.db_path))
            except duckdb.IOException as exc:
                if time.monotonic() >= deadline:
                    raise ExecutionError.from_code(
                        ERR_CACHE_FILE_UNAVAILABLE,
                        path=str(self.db_path),
                        seconds=self.lock_timeout_seconds,
                        detail=str(exc).splitlines()[0],
                    ) from exc
                time.sleep(delay)
                delay = min(delay * 2, 0.1)
            else:
                conn.execute("PRAGMA disable_checkpoint_on_shutdown")
                return conn

    def _read_slots(
        self,
        conn: duckdb.DuckDBPyConnection,
        source_hash: str,
        query_hash: str,
        variables_hash: str,
    ) -> _Slots:
        """Read both slots for this key in one lookup.

        A missing row and two empty slots are the same thing to every caller, so
        an absent key comes back as ``_Slots.EMPTY`` rather than None.
        """
        row = conn.execute(
            """
            SELECT written_at_utc, truncated_reason, payload,
                   error_class, error_message, traceback, error_written_at_utc
            FROM _query_outcomes
            WHERE source_hash = ? AND query_hash = ? AND variables_hash = ?
            """,
            [source_hash, query_hash, variables_hash],
        ).fetchone()
        if row is None:
            return _Slots.EMPTY
        (
            written_at_utc,
            truncated_reason,
            payload,
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
            payload=payload,
            error=error,
            truncated_reason=truncated_reason,
        )

    def _write_rows_slot(
        self,
        conn: duckdb.DuckDBPyConnection,
        source_hash: str,
        query_hash: str,
        variables_hash: str,
        payload: bytes,
        board_slug: str,
        query_name: str,
        truncated_reason: TruncatedReason | None,
    ) -> None:
        """Record a successful run, clearing the error slot in the same statement.

        That clearing is the whole point of one row per key: with two tables a
        stale failure could outlive the success that superseded it, and there
        was no way to retire it atomically.

        ``truncated_reason`` replaces wholesale, never merges — a re-run that
        comes back under the ceiling must clear a prior truncation, not keep
        reporting one that no longer applies.
        """
        conn.execute(
            """
            INSERT INTO _query_outcomes
                (source_hash, query_hash, variables_hash, board_slug, query_name,
                 written_at_utc, truncated_reason, payload,
                 error_class, error_message, traceback, error_written_at_utc)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, NULL, NULL, NULL, NULL)
            ON CONFLICT (source_hash, query_hash, variables_hash) DO UPDATE SET
                board_slug = excluded.board_slug,
                query_name = excluded.query_name,
                written_at_utc = excluded.written_at_utc,
                truncated_reason = excluded.truncated_reason,
                payload = excluded.payload,
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
                _utc_wall_clock(),
                truncated_reason,
                payload,
            ],
        )

    def _write_error_slot(
        self,
        conn: duckdb.DuckDBPyConnection,
        source_hash: str,
        query_hash: str,
        variables_hash: str,
        exception: Exception,
        board_slug: str,
        query_name: str,
    ) -> None:
        """Record an error, leaving any rows slot untouched.

        The two writes touch disjoint columns, so a worker whose query failed
        can never destroy rows another worker just computed — no read-then-write
        window.
        """
        conn.execute(
            """
            INSERT INTO _query_outcomes
                (source_hash, query_hash, variables_hash, board_slug, query_name,
                 error_class, error_message, traceback, error_written_at_utc)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
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
        """Remove this key's outcome row, rows and error alike."""
        with self._connection() as conn:
            conn.execute(
                "DELETE FROM _query_outcomes WHERE source_hash = ? AND query_hash = ? AND variables_hash = ?",
                [source_hash, query_hash, variables_hash],
            )

    def close(self) -> None:
        """Close the in-memory connection; a file-backed cache holds nothing open."""
        with self._lock:
            if self._memory_conn is not None:
                self._memory_conn.close()

    # ── Cache-ref composition ({{ queries.X.cache }}) ────────────────────────
    # Composition runs in an isolated in-process DuckDB fed the upstream rows by
    # the executor (dbt_charts.core.execute.cache_composition); this backend only
    # advertises the capability.

    def supports_cache_refs(self) -> bool:
        """DuckDB-backed cache always supports cache-ref composition."""
        return True

    def _prune(self, conn: duckdb.DuckDBPyConnection, max_age: timedelta) -> None:
        """Drop entries older than *max_age* and give their disk space back.

        DuckDB does not reuse the blocks of deleted rows while their neighbors
        live on, so a DELETE would leave the file growing forever. The table is
        rebuilt from its live rows instead. That rewrites every live payload,
        so it must not happen on every open: entries within an eighth of
        *max_age* of the limit go too, which leaves nothing to expire again
        until that much time has passed.
        """
        age = "coalesce(written_at_utc, error_written_at_utc)"
        now = _utc_wall_clock()
        expired = conn.execute(
            f"SELECT count(*) FROM _query_outcomes WHERE {age} < ?", [now - max_age]
        ).fetchone()
        if expired is None or expired[0] == 0:
            return
        # One transaction: a process killed partway must leave either the old
        # table or the rebuilt one, never an empty one.
        conn.execute("BEGIN")
        conn.execute(
            f"CREATE TEMP TABLE _live AS SELECT * FROM _query_outcomes WHERE {age} >= ?",
            [now - max_age * 7 / 8],
        )
        conn.execute("DROP TABLE _query_outcomes")
        self._create_outcomes_table(conn)
        conn.execute("INSERT INTO _query_outcomes SELECT * FROM _live")
        conn.execute("DROP TABLE _live")
        conn.execute("COMMIT")
        conn.execute("CHECKPOINT")
        logger.info("query-result cache: removed %d expired entries", expired[0])

    @staticmethod
    def _create_outcomes_table(conn: duckdb.DuckDBPyConnection) -> None:
        column_defs = ", ".join(
            f"{name} {sql_type}" for name, sql_type in _OUTCOME_COLUMNS.items()
        )
        conn.execute(
            f"CREATE TABLE IF NOT EXISTS _query_outcomes ({column_defs}, "
            "PRIMARY KEY (source_hash, query_hash, variables_hash))"
        )

    def _ensure_schema(self, conn: duckdb.DuckDBPyConnection) -> None:
        """Create the outcome table, first dropping any superseded layout.

        A file whose `_query_outcomes` columns differ from the ones this
        version writes came from another version. CREATE TABLE IF NOT EXISTS
        would keep those columns and the first read or write would fail, so the
        cache's tables are dropped instead — the outcome table, and the
        per-key `_r_<hash>` result tables and `_query_failures` that earlier
        layouts kept beside it. It is a disposable cache: each dropped key
        re-warms on its next request.
        """
        columns = {
            name
            for (name,) in conn.execute(
                "SELECT column_name FROM information_schema.columns "
                "WHERE table_name = '_query_outcomes'"
            ).fetchall()
        }
        if columns != set(_OUTCOME_COLUMNS):
            superseded = conn.execute(
                "SELECT table_name FROM information_schema.tables "
                "WHERE table_type = 'BASE TABLE' AND ("
                "table_name IN ('_query_outcomes', '_query_failures') "
                "OR starts_with(table_name, '_r_'))"
            ).fetchall()
            for (table,) in superseded:
                escaped = table.replace('"', '""')
                conn.execute(f'DROP TABLE "{escaped}"')
            if superseded:
                logger.info(
                    "query-result cache: dropped %d table(s) from a superseded "
                    "layout on open; entries will re-warm",
                    len(superseded),
                )
        self._create_outcomes_table(conn)
