"""Tests for TrivialDuckDBCache — M3 Phase 2 stripped local cache.

Covers:
- replace-on-write: re-put same key overwrites, no row growth
- opening over a stale seq-schema cache file drops+rebuilds without error
"""

from pathlib import Path

from dbt_charts.core.execute._duckdb_cache_base import _result_table_name
from dbt_charts.core.execute.cache_backend import CachedQueryFailure, CacheHit
from dbt_charts.core.execute.duckdb_cache import (
    compute_query_hash,
    compute_source_hash,
    compute_variables_hash,
)
from dbt_charts.core.execute.trivial_local_cache import TrivialDuckDBCache

_SH = compute_source_hash("test_source")
_QH = compute_query_hash("SELECT 1")
_VH = compute_variables_hash({})


def _put(cache: TrivialDuckDBCache, data: list[dict]) -> None:
    cache.put(_SH, _QH, _VH, data, board_slug="board_a", query_name="q1")


class TestReplaceOnWrite:
    """put() replaces prior rows — no row accumulation."""

    def test_initial_put_returns_rows(self) -> None:
        cache = TrivialDuckDBCache()
        try:
            _put(cache, [{"v": 1}])
            hit = cache.get(_SH, _QH, _VH)
            assert hit is not None
            assert hit.rows == [{"v": 1}]
        finally:
            cache.close()

    def test_re_put_same_key_overwrites(self) -> None:
        """Second put must not append — row count must stay at the new result set size."""
        cache = TrivialDuckDBCache()
        try:
            _put(cache, [{"v": 1}])
            _put(cache, [{"v": 2}])
            hit = cache.get(_SH, _QH, _VH)
            assert hit is not None
            assert len(hit.rows) == 1
            assert hit.rows[0]["v"] == 2
        finally:
            cache.close()

    def test_multi_row_result_replaced_not_grown(self) -> None:
        """put([3 rows]) then put([2 rows]) must yield exactly 2 rows."""
        cache = TrivialDuckDBCache()
        try:
            _put(cache, [{"v": 1}, {"v": 2}, {"v": 3}])
            _put(cache, [{"v": 10}, {"v": 20}])
            hit = cache.get(_SH, _QH, _VH)
            assert hit is not None
            assert len(hit.rows) == 2
            assert {r["v"] for r in hit.rows} == {10, 20}
        finally:
            cache.close()

    def test_miss_returns_none(self) -> None:
        cache = TrivialDuckDBCache()
        try:
            assert cache.get(_SH, _QH, _VH) is None
        finally:
            cache.close()

    def test_dropped_result_table_is_a_miss_not_an_empty_hit(self) -> None:
        """A row-bearing entry whose result table vanished must re-run.

        The outcome row records how many rows it wrote, which is what tells a
        genuine zero-row success apart from a payload dropped out from under a
        live entry (an externally-cleared cache file, say). Without the count
        the latter would read back as an empty hit and silently serve no data.
        """
        cache = TrivialDuckDBCache()
        try:
            _put(cache, [{"v": 1}])
            cache.conn.execute(f'DROP TABLE "{_result_table_name(_SH, _QH, _VH)}"')
            assert cache.get(_SH, _QH, _VH) is None
        finally:
            cache.close()

    def test_empty_put_is_a_zero_row_outcome(self) -> None:
        """put([]) records a real zero-row success, not a miss.

        The outcome row is what replaces a prior error for this key, so an
        empty result must write one — the two-store cache wrote nothing here
        and left the stale failure standing.
        """
        cache = TrivialDuckDBCache()
        try:
            _put(cache, [])
            hit = cache.get(_SH, _QH, _VH)
            assert isinstance(hit, CacheHit)
            assert hit.rows == []
        finally:
            cache.close()


class TestTruncatedReasonPersists:
    """put()'s truncated_reason travels with the rows and comes back on get() —
    the truncation fact must be a property of the cached data, not of how a
    render fetched it, so a warm hit still knows a result was truncated."""

    def test_truncated_reason_round_trips(self) -> None:
        cache = TrivialDuckDBCache()
        try:
            cache.put(
                _SH,
                _QH,
                _VH,
                [{"v": 1}],
                board_slug="board_a",
                query_name="q1",
                truncated_reason="max_rows",
            )
            hit = cache.get(_SH, _QH, _VH)
            assert isinstance(hit, CacheHit)
            assert hit.truncated_reason == "max_rows"
        finally:
            cache.close()

    def test_untruncated_result_has_no_reason(self) -> None:
        cache = TrivialDuckDBCache()
        try:
            _put(cache, [{"v": 1}])
            hit = cache.get(_SH, _QH, _VH)
            assert isinstance(hit, CacheHit)
            assert hit.truncated_reason is None
        finally:
            cache.close()

    def test_re_put_without_reason_clears_a_prior_truncation(self) -> None:
        """A re-run that comes back under the ceiling must clear the stale
        truncated_reason from the previous (truncated) run — the reason is
        replaced wholesale on every rows write, never merged."""
        cache = TrivialDuckDBCache()
        try:
            cache.put(
                _SH,
                _QH,
                _VH,
                [{"v": 1}],
                board_slug="board_a",
                query_name="q1",
                truncated_reason="max_rows",
            )
            cache.put(
                _SH,
                _QH,
                _VH,
                [{"v": 2}],
                board_slug="board_a",
                query_name="q1",
                truncated_reason=None,
            )
            hit = cache.get(_SH, _QH, _VH)
            assert isinstance(hit, CacheHit)
            assert hit.truncated_reason is None
        finally:
            cache.close()


class TestStaleSchemaHandling:
    """Opening over a stale cache file with seq columns must drop/rebuild silently.

    These tests seed the stale table at the REAL table name for the test key
    (_result_table_name(_SH, _QH, _VH)) so that disabling the drop logic causes
    the test to fail: without the drop, _ensure_result_table sees the existing
    table, keeps it, and then get() returns the wrong seq-column rows (or errors
    when the column set differs).
    """

    def test_opens_over_stale_seq_schema_without_error(self, tmp_path: Path) -> None:
        """Stale table at the real key name is dropped; subsequent put/get work."""
        import duckdb

        db_path = tmp_path / "cache.duckdb"
        # The real table name for (_SH, _QH, _VH) — stale data must live here
        # to verify the drop actually runs for the key we will later put/get.
        real_tbl = _result_table_name(_SH, _QH, _VH)

        conn = duckdb.connect(str(db_path))
        conn.execute(
            f"""
            CREATE TABLE "{real_tbl}" (
                _snapshot_set_seq INTEGER,
                _snapshot_run_seq INTEGER,
                _run_timestamp TIMESTAMP,
                _source_hash VARCHAR,
                _variables JSON,
                v INTEGER
            )
            """
        )
        conn.execute(
            f"INSERT INTO \"{real_tbl}\" VALUES (1, 1, NOW(), 'h', '{{}}', 99)"
        )
        conn.close()

        # TrivialDuckDBCache must drop the stale table on open; then put/get work.
        cache = TrivialDuckDBCache(db_path=db_path)
        try:
            _put(cache, [{"v": 7}])
            hit = cache.get(_SH, _QH, _VH)
            # Must return only the freshly-put row, not the stale seq-column row.
            assert hit is not None
            assert hit.rows == [{"v": 7}]
        finally:
            cache.close()

    def test_opens_over_pre_rename_face_slug_schema_without_error(
        self, tmp_path: Path
    ) -> None:
        """A cache file whose _query_outcomes still has face_slug is dropped and re-warmed.

        Seeds the outcome table exactly as pre-rename released code wrote it, so
        disabling the face_slug drop guard makes the first put() raise
        duckdb.BinderException (INSERT names board_slug).
        """
        import duckdb

        db_path = tmp_path / "cache.duckdb"
        conn = duckdb.connect(str(db_path))
        conn.execute(
            """
            CREATE TABLE _query_outcomes (
                source_hash TEXT NOT NULL,
                query_hash TEXT NOT NULL,
                variables_hash TEXT NOT NULL,
                face_slug TEXT NOT NULL,
                query_name TEXT NOT NULL,
                row_count BIGINT,
                written_at_utc TIMESTAMP,
                error_class TEXT,
                error_message TEXT,
                traceback TEXT,
                error_written_at_utc TIMESTAMP,
                PRIMARY KEY (source_hash, query_hash, variables_hash)
            )
            """
        )
        conn.execute(
            "INSERT INTO _query_outcomes VALUES "
            "('h', 'q', 'v', 'old-face', 'sales', 1, NOW(), "
            "NULL, NULL, NULL, NULL)"
        )
        conn.close()

        cache = TrivialDuckDBCache(db_path=db_path)
        try:
            _put(cache, [{"v": 7}])
            hit = cache.get(_SH, _QH, _VH)
            assert hit is not None
            assert hit.rows == [{"v": 7}]
            cols = [
                row[0]
                for row in cache.conn.execute(
                    "SELECT column_name FROM information_schema.columns "
                    "WHERE table_name = '_query_outcomes'"
                ).fetchall()
            ]
            assert "board_slug" in cols and "face_slug" not in cols
        finally:
            cache.close()

    def test_opens_over_pre_truncated_reason_schema_without_error(
        self, tmp_path: Path
    ) -> None:
        """A cache file whose _query_outcomes predates truncated_reason is
        dropped and re-warmed.

        Seeds the outcome table exactly as pre-truncated_reason released code
        wrote it (post-rename: board_slug present, no truncated_reason
        column), so disabling the guard makes the first put() raise
        duckdb.BinderException (INSERT names truncated_reason).
        """
        import duckdb

        db_path = tmp_path / "cache.duckdb"
        conn = duckdb.connect(str(db_path))
        conn.execute(
            """
            CREATE TABLE _query_outcomes (
                source_hash TEXT NOT NULL,
                query_hash TEXT NOT NULL,
                variables_hash TEXT NOT NULL,
                board_slug TEXT NOT NULL,
                query_name TEXT NOT NULL,
                row_count BIGINT,
                written_at_utc TIMESTAMP,
                error_class TEXT,
                error_message TEXT,
                traceback TEXT,
                error_written_at_utc TIMESTAMP,
                PRIMARY KEY (source_hash, query_hash, variables_hash)
            )
            """
        )
        conn.execute(
            "INSERT INTO _query_outcomes VALUES "
            "('h', 'q', 'v', 'a-board', 'sales', 1, NOW(), "
            "NULL, NULL, NULL, NULL)"
        )
        conn.close()

        cache = TrivialDuckDBCache(db_path=db_path)
        try:
            _put(cache, [{"v": 7}])
            hit = cache.get(_SH, _QH, _VH)
            assert hit is not None
            assert hit.rows == [{"v": 7}]
            cols = [
                row[0]
                for row in cache.conn.execute(
                    "SELECT column_name FROM information_schema.columns "
                    "WHERE table_name = '_query_outcomes'"
                ).fetchall()
            ]
            assert "truncated_reason" in cols
        finally:
            cache.close()

    def test_stale_file_is_usable_after_open(self, tmp_path: Path) -> None:
        """After dropping the stale table, fresh puts/gets return only new rows."""
        import duckdb

        db_path = tmp_path / "cache.duckdb"
        real_tbl = _result_table_name(_SH, _QH, _VH)

        conn = duckdb.connect(str(db_path))
        conn.execute(
            f"""
            CREATE TABLE "{real_tbl}" (
                _snapshot_set_seq INTEGER,
                _snapshot_run_seq INTEGER,
                _run_timestamp TIMESTAMP,
                _source_hash VARCHAR,
                _variables JSON,
                result VARCHAR
            )
            """
        )
        conn.execute(
            f"INSERT INTO \"{real_tbl}\" VALUES (1, 1, NOW(), 'h', '{{}}', 'stale')"
        )
        conn.close()

        cache = TrivialDuckDBCache(db_path=db_path)
        try:
            _put(cache, [{"result": "ok"}])
            hit = cache.get(_SH, _QH, _VH)
            assert hit is not None
            assert len(hit.rows) == 1
            assert hit.rows[0]["result"] == "ok"
        finally:
            cache.close()


class TestFailuresProtocol:
    """An error is stored as this key's outcome, read back by the same get()."""

    def test_put_error_then_get_returns_it(self) -> None:
        cache = TrivialDuckDBCache()
        try:
            cache.put(
                _SH,
                _QH,
                _VH,
                ValueError("boom"),
                board_slug="f",
                query_name="q",
            )
            failure = cache.get(_SH, _QH, _VH)
            assert isinstance(failure, CachedQueryFailure)
            assert failure.error_class == "ValueError"
            assert "boom" in failure.error_message
        finally:
            cache.close()

    def test_clear_removes_the_outcome(self) -> None:
        cache = TrivialDuckDBCache()
        try:
            _put(cache, [{"v": 1}])
            cache.put(_SH, _QH, _VH, ValueError("x"), board_slug="f", query_name="q")
            cache.clear(_SH, _QH, _VH)
            assert cache.get(_SH, _QH, _VH) is None
        finally:
            cache.close()


class TestCacheHitWrittenAt:
    """get() surfaces a cache entry's write timestamp on the returned CacheHit —
    single round trip, no separate entry_written_at() lookup."""

    def test_hit_written_at_is_bounded_by_the_put_call(self) -> None:
        import datetime as dt

        cache = TrivialDuckDBCache()
        try:
            # Aware UTC on both sides: written_at carries a timezone (the
            # backend contract on CacheHit), so bounding it with a naive
            # datetime.now() is a TypeError, not a skew.
            before = dt.datetime.now(dt.UTC)
            _put(cache, [{"v": 1}])
            after = dt.datetime.now(dt.UTC)
            hit = cache.get(_SH, _QH, _VH)
            assert isinstance(hit, CacheHit)
            assert before <= hit.written_at <= after
        finally:
            cache.close()
