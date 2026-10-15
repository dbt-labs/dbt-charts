"""Tests for TrivialDuckDBCache outcome caching and force_refresh.

All operations use the Protocol API: put / get / clear. One outcome row per
(source_hash, query_hash, variables_hash) key holds either the result rows or
the error, so a re-run's outcome replaces the prior one.
"""

import time
from datetime import UTC, datetime, timedelta

import pytest

from dbt_charts.core.execute.cache_backend import CachedQueryFailure, CacheHit
from dbt_charts.core.execute.duckdb_cache import (
    compute_query_hash,
    compute_source_hash,
    compute_variables_hash,
)
from dbt_charts.core.execute.trivial_local_cache import TrivialDuckDBCache


@pytest.fixture
def cache():
    """In-memory TrivialDuckDBCache for testing."""
    c = TrivialDuckDBCache()
    yield c
    c.close()


SQL = "SELECT * FROM nonexistent_table"
SOURCE = "test_source"
VARS: dict = {}
SOURCE_HASH = compute_source_hash(SOURCE)
QUERY_HASH = compute_query_hash(SQL)
VARS_HASH = compute_variables_hash(VARS)


def put_error(
    cache: TrivialDuckDBCache,
    exception: Exception,
    query_hash: str = QUERY_HASH,
    variables_hash: str = VARS_HASH,
) -> None:
    """Store *exception* as the outcome for a key (test-local convenience)."""
    cache.put(
        SOURCE_HASH,
        query_hash,
        variables_hash,
        exception,
        board_slug="test_board",
        query_name="broken_query",
    )


def put_rows(
    cache: TrivialDuckDBCache,
    rows: list[dict],
    query_hash: str = QUERY_HASH,
    variables_hash: str = VARS_HASH,
) -> None:
    """Store *rows* as the outcome for a key (test-local convenience)."""
    cache.put(
        SOURCE_HASH,
        query_hash,
        variables_hash,
        rows,
        board_slug="test_board",
        query_name="broken_query",
    )


class TestCacheFailureRoundtrip:
    """Failing query → get returns CachedQueryFailure, not the original exception."""

    def test_cache_failure_and_retrieve(self, cache: TrivialDuckDBCache):
        put_error(cache, RuntimeError("table not found: nonexistent_table"))

        failure = cache.get(SOURCE_HASH, QUERY_HASH, VARS_HASH)
        assert isinstance(failure, CachedQueryFailure)
        assert failure.error_class == "RuntimeError"
        assert "nonexistent_table" in failure.error_message
        assert failure.failed_at is not None

    def test_no_outcome_returns_none(self, cache: TrivialDuckDBCache):
        assert (
            cache.get(SOURCE_HASH, compute_query_hash("never_ran"), VARS_HASH) is None
        )


class TestCacheFailureTTLExpiry:
    """After the retry window, the error is gone (the query should re-run)."""

    def test_expired_failure_returns_none(self):
        # 1-second retry window so expiry is testable quickly.
        cache = TrivialDuckDBCache(failure_ttl_seconds=1)
        try:
            put_error(cache, ValueError("bad column"))
            assert cache.get(SOURCE_HASH, QUERY_HASH, VARS_HASH) is not None

            time.sleep(1.1)

            assert cache.get(SOURCE_HASH, QUERY_HASH, VARS_HASH) is None
        finally:
            cache.close()

    def test_zero_ttl_never_caches(self):
        cache = TrivialDuckDBCache(failure_ttl_seconds=0)
        try:
            put_error(cache, ValueError("bad column"))
            assert cache.get(SOURCE_HASH, QUERY_HASH, VARS_HASH) is None
        finally:
            cache.close()


class TestSuccessTTLExpiry:
    """Rows expire on the reader's resolved policy ttl, derived on read."""

    def test_rows_expire_on_the_readers_ttl(self, cache: TrivialDuckDBCache):
        put_rows(cache, [{"col": 1}])
        assert (
            cache.get(SOURCE_HASH, QUERY_HASH, VARS_HASH, ttl=timedelta(seconds=-1))
            is None
        )

    def test_forever_ttl_never_expires(self, cache: TrivialDuckDBCache):
        put_rows(cache, [{"col": 1}])
        hit = cache.get(SOURCE_HASH, QUERY_HASH, VARS_HASH)
        assert isinstance(hit, CacheHit)
        assert hit.rows == [{"col": 1}]


class TestReaderPolicyBound:
    """Rows expire on the *reader's* ttl — nothing about expiry is stored.

    The cache key is content-addressed (source + SQL + variables) and
    deliberately excludes the cache policy, so two queries with identical SQL
    and different `cache:` blocks share one entry. If only the writer's ttl
    bounded the entry, whichever ran last would silently govern the other's
    authored freshness contract.
    """

    def test_short_ttl_reader_misses_a_long_ttl_writers_entry(
        self, cache: TrivialDuckDBCache
    ):
        # A `ttl: forever` writer stamps an entry that never expires on its own.
        put_rows(cache, [{"col": 1}])
        assert isinstance(cache.get(SOURCE_HASH, QUERY_HASH, VARS_HASH), CacheHit)

        # A reader whose own policy is already elapsed must not be served it.
        assert (
            cache.get(SOURCE_HASH, QUERY_HASH, VARS_HASH, ttl=timedelta(seconds=-1))
            is None
        )

    def test_reader_within_its_ttl_is_served(self, cache: TrivialDuckDBCache):
        put_rows(cache, [{"col": 1}])
        hit = cache.get(SOURCE_HASH, QUERY_HASH, VARS_HASH, ttl=timedelta(hours=1))
        assert isinstance(hit, CacheHit)
        assert hit.rows == [{"col": 1}]

    def test_reader_ttl_does_not_shorten_the_error_retry_clock(
        self, cache: TrivialDuckDBCache
    ):
        """An error's deadline is a system constant; the reader has no say."""
        put_error(cache, RuntimeError("blip"))
        outcome = cache.get(
            SOURCE_HASH, QUERY_HASH, VARS_HASH, ttl=timedelta(seconds=-1)
        )
        assert isinstance(outcome, CachedQueryFailure)


class TestSlotsAreIndependent:
    """Rows and error are two slots on one row; a write touches only its own.

    This is the two-table behavior preserved. A failure never destroys rows —
    which matters because the key is shared: the query that failed is not
    necessarily the query that will read next, and it does not get to decide
    what that reader is entitled to.
    """

    def test_error_does_not_destroy_rows(self, cache: TrivialDuckDBCache):
        put_rows(cache, [{"col": 1}])
        put_error(cache, RuntimeError("timed out"))

        # A reader still inside its ttl gets its rows, not the failure.
        hit = cache.get(SOURCE_HASH, QUERY_HASH, VARS_HASH, ttl=timedelta(hours=1))
        assert isinstance(hit, CacheHit)
        assert hit.rows == [{"col": 1}]

    def test_a_reader_past_its_ttl_falls_through_to_the_error(
        self, cache: TrivialDuckDBCache
    ):
        put_rows(cache, [{"col": 1}])
        put_error(cache, RuntimeError("timed out"))

        outcome = cache.get(
            SOURCE_HASH, QUERY_HASH, VARS_HASH, ttl=timedelta(seconds=-1)
        )
        assert isinstance(outcome, CachedQueryFailure)
        assert "timed out" in outcome.error_message

    def test_long_ttl_sibling_survives_a_short_ttl_siblings_failure(
        self, cache: TrivialDuckDBCache
    ):
        """The regression this design exists to prevent.

        A 1h query missing, re-running and failing must not break a 24h query
        that is still inside its own window over the same key.
        """
        put_rows(cache, [{"col": 1}])
        # The impatient sibling has aged out and just failed.
        put_error(cache, RuntimeError("transient blip"))

        patient = cache.get(SOURCE_HASH, QUERY_HASH, VARS_HASH, ttl=timedelta(hours=24))
        assert isinstance(patient, CacheHit), (
            "a sibling's failure must not break a reader inside its own ttl"
        )
        assert patient.rows == [{"col": 1}]

    def test_success_clears_the_error_slot(self, cache: TrivialDuckDBCache):
        """The bug the two-table design could not fix atomically.

        A stale failure must not outlive the success that superseded it.
        """
        put_error(cache, RuntimeError("was broken"))
        put_rows(cache, [{"col": 1}])

        # Even a reader that cannot use the rows must not see the old error.
        assert (
            cache.get(SOURCE_HASH, QUERY_HASH, VARS_HASH, ttl=timedelta(seconds=-1))
            is None
        )

    def test_zero_row_success_replaces_prior_rows(self, cache: TrivialDuckDBCache):
        """A run that now returns nothing must not serve the last run's rows.

        The rows live in a side table, so the zero-row write has to drop it —
        the outcome row alone cannot make them unreachable.
        """
        put_rows(cache, [{"col": 1}, {"col": 2}])
        put_rows(cache, [])

        hit = cache.get(SOURCE_HASH, QUERY_HASH, VARS_HASH)
        assert isinstance(hit, CacheHit)
        assert hit.rows == []

    def test_zero_row_success_clears_the_error_slot(self, cache: TrivialDuckDBCache):
        """A zero-row run is a success and must retire the error like any other."""
        put_error(cache, RuntimeError("was broken"))
        put_rows(cache, [])

        hit = cache.get(SOURCE_HASH, QUERY_HASH, VARS_HASH)
        assert isinstance(hit, CacheHit)
        assert hit.rows == []
        assert (
            cache.get(SOURCE_HASH, QUERY_HASH, VARS_HASH, ttl=timedelta(seconds=-1))
            is None
        )


class TestForceRefreshClearsOutcome:
    """clear() wipes the outcome row so the query re-runs."""

    def test_clear_removes_failure(self, cache: TrivialDuckDBCache):
        put_error(cache, RuntimeError("table gone"))
        assert cache.get(SOURCE_HASH, QUERY_HASH, VARS_HASH) is not None

        cache.clear(SOURCE_HASH, QUERY_HASH, VARS_HASH)

        assert cache.get(SOURCE_HASH, QUERY_HASH, VARS_HASH) is None

    def test_clear_removes_success(self, cache: TrivialDuckDBCache):
        put_rows(cache, [{"col": 1}])
        assert cache.get(SOURCE_HASH, QUERY_HASH, VARS_HASH) is not None

        cache.clear(SOURCE_HASH, QUERY_HASH, VARS_HASH)

        assert cache.get(SOURCE_HASH, QUERY_HASH, VARS_HASH) is None


class TestCacheFailureDoesNotPoisonOtherQueries:
    """Different sql/vars hash is independent — one key's error is not another's."""

    def test_different_query_unaffected(self, cache: TrivialDuckDBCache):
        put_error(cache, RuntimeError("this one fails"), compute_query_hash("SELECT 1"))

        assert cache.get(SOURCE_HASH, compute_query_hash("SELECT 2"), VARS_HASH) is None

    def test_different_vars_unaffected(self, cache: TrivialDuckDBCache):
        vars_hash_a = compute_variables_hash({"region": "west"})
        vars_hash_b = compute_variables_hash({"region": "east"})

        put_error(cache, RuntimeError("this one fails"), variables_hash=vars_hash_a)

        assert cache.get(SOURCE_HASH, QUERY_HASH, vars_hash_b) is None
        assert cache.get(SOURCE_HASH, QUERY_HASH, vars_hash_a) is not None


class TestFailureUpsert:
    """Caching an error for the same key overwrites the previous one."""

    def test_upsert_overwrites(self, cache: TrivialDuckDBCache):
        put_error(cache, RuntimeError("first error"))
        put_error(cache, ValueError("second error"))

        failure = cache.get(SOURCE_HASH, QUERY_HASH, VARS_HASH)
        assert isinstance(failure, CachedQueryFailure)
        assert failure.error_class == "ValueError"
        assert "second error" in failure.error_message


class TestConcurrentSlotWrites:
    """The two writes touch disjoint columns, so neither can lose the race.

    This is what replaced the lock: with a single outcome slot, a worker whose
    query timed out could overwrite rows another worker had just computed, and
    closing that needed a read-then-write guard under a row lock. Two slots
    remove the race instead of guarding it — so this asserts the property
    directly rather than trusting the reasoning.
    """

    def test_interleaved_success_and_error_keep_both_slots(self):
        import threading

        cache = TrivialDuckDBCache()
        try:
            barrier = threading.Barrier(2)

            def write_rows() -> None:
                barrier.wait()
                for _ in range(50):
                    put_rows(cache, [{"col": 1}])

            def write_error() -> None:
                barrier.wait()
                for _ in range(50):
                    put_error(cache, RuntimeError("blip"))

            threads = [
                threading.Thread(target=write_rows),
                threading.Thread(target=write_error),
            ]
            for t in threads:
                t.start()
            for t in threads:
                t.join()

            # Whatever the interleaving, a reader inside its ttl still gets
            # rows: no error write ever destroyed them.
            put_rows(cache, [{"col": 1}])
            hit = cache.get(SOURCE_HASH, QUERY_HASH, VARS_HASH, ttl=timedelta(hours=1))
            assert isinstance(hit, CacheHit)
            assert hit.rows == [{"col": 1}]
        finally:
            cache.close()


class TestCurrentFileSurvivesReopen:
    def test_a_current_file_is_preserved_across_reopen(self, tmp_path) -> None:
        """The drop must be version-triggered, not unconditional.

        `_query_outcomes` is recreated on every open, so a blanket DROP would
        wipe the cache each launch and quietly defeat persistence entirely.
        """
        db = tmp_path / "current.duckdb"
        first = TrivialDuckDBCache(db_path=db)
        try:
            put_rows(first, [{"v": 1}])
        finally:
            first.close()

        second = TrivialDuckDBCache(db_path=db)
        try:
            hit = second.get(SOURCE_HASH, QUERY_HASH, VARS_HASH)
            assert isinstance(hit, CacheHit), "a current-schema file must survive"
            assert hit.rows == [{"v": 1}]
        finally:
            second.close()


class TestTimestampsAreAwareUTC:
    """Both arms of the outcome union carry timezone-aware UTC timestamps.

    The two backends have to agree: Cloud returns Django's aware `timezone.now()`,
    so the DuckDB store must not hand back naive values. `min()` across a render's
    hits raises TypeError on a mixed set, and the retry window and the ttl cutoff
    are both measured against these — a value read in the wrong frame is wrong by
    the machine's UTC offset, silently.

    The DuckDB column is a naive TIMESTAMP holding a UTC wall clock, so the read
    path relabels rather than converts. This pins that the write, the comparison
    and the read all sit in the same frame — a `.replace(tzinfo=utc)` bolted onto
    a naive-*local* write would satisfy "is aware" while shifting every reading.
    """

    def test_written_at_is_stored_as_a_utc_wall_clock_whatever_the_session_zone(
        self,
    ) -> None:
        """The stored column must be UTC regardless of DuckDB's TimeZone setting.

        DuckDB's TIMESTAMP is WITHOUT TIME ZONE, so binding an *aware* datetime
        makes the driver cast it against the connection's TimeZone — with ICU
        present that defaults to the machine's zone, and the column would then
        hold the local wall clock while the read relabels it as UTC. East of
        Greenwich that reads as future-dated, `now - written_at` goes negative,
        and every ttl comparison passes unconditionally: nothing ever expires.

        Forcing the session zone here rather than the process TZ is what makes
        this deterministic on any host, CI's UTC included — `time.tzset()` cannot
        move DuckDB's zone once ICU has cached it, so a process-level TZ test can
        silently stop discriminating. Writing a naive UTC wall clock removes the
        cast entirely, which is why this holds for every zone below.
        """
        for zone in ("UTC", "Asia/Tokyo", "America/Phoenix"):
            c = TrivialDuckDBCache()
            try:
                with c._connection() as conn:
                    conn.execute(f"SET TimeZone='{zone}'")
                put_rows(c, [{"v": 1}])
                hit = c.get(SOURCE_HASH, QUERY_HASH, VARS_HASH)
                assert isinstance(hit, CacheHit)
                drift = abs((hit.written_at - datetime.now(UTC)).total_seconds())
                assert drift < 300, (
                    f"TimeZone={zone}: written_at {hit.written_at} is "
                    f"{drift / 3600:.1f}h from the UTC wall clock — an aware bind "
                    "was cast against the session zone instead of stored as UTC"
                )
            finally:
                c.close()

    def test_rows_hit_timestamp_is_aware_utc(self, cache) -> None:
        put_rows(cache, [{"a": 1}])
        hit = cache.get(SOURCE_HASH, QUERY_HASH, VARS_HASH)
        assert isinstance(hit, CacheHit)
        assert hit.written_at.tzinfo is not None
        assert hit.written_at.utcoffset() == timedelta(0)

    def test_failure_timestamp_is_aware_utc(self, cache) -> None:
        put_error(cache, ValueError("boom"))
        outcome = cache.get(SOURCE_HASH, QUERY_HASH, VARS_HASH)
        assert isinstance(outcome, CachedQueryFailure)
        assert outcome.failed_at.tzinfo is not None
        assert outcome.failed_at.utcoffset() == timedelta(0)

    @pytest.mark.usefixtures("non_utc_tz")
    def test_written_at_tracks_real_elapsed_time_not_the_utc_offset(
        self, cache
    ) -> None:
        """The relabel must not shift the reading.

        A freshly written entry is seconds old, never hours — which is what a
        naive-local value relabeled as UTC looks like. Comparing against an
        aware `now` also proves the two frames match, since a mismatch is a
        TypeError rather than a bad number.
        """
        put_rows(cache, [{"a": 1}])
        hit = cache.get(SOURCE_HASH, QUERY_HASH, VARS_HASH)
        assert isinstance(hit, CacheHit)
        age = datetime.now(UTC) - hit.written_at
        assert timedelta(0) <= age < timedelta(minutes=5), (
            f"entry reads as {age} old — a naive-local timestamp relabeled as "
            "UTC lands off by the machine's offset"
        )

    @pytest.mark.usefixtures("non_utc_tz")
    def test_ttl_cutoff_is_measured_in_the_same_frame_as_the_write(self, cache) -> None:
        """A just-written entry must survive a short ttl under a real offset.

        If the write and the cutoff disagreed by the UTC offset, this entry would
        read as hours old and a 1-minute ttl would evict it immediately.
        """
        put_rows(cache, [{"a": 1}])
        assert isinstance(
            cache.get(SOURCE_HASH, QUERY_HASH, VARS_HASH, ttl=timedelta(minutes=1)),
            CacheHit,
        )

    @pytest.mark.usefixtures("non_utc_tz")
    def test_failure_retry_window_is_measured_in_the_same_frame_as_the_write(
        self,
    ) -> None:
        """The error slot's retry window has the same frame requirement.

        Post-unification both arms share the timestamp column, so a local-clock
        error write would expire (or refuse to expire) a cached failure by the
        machine's offset rather than by the retry window.
        """
        c = TrivialDuckDBCache(failure_ttl_seconds=900)
        try:
            put_error(c, ValueError("boom"))
            assert isinstance(
                c.get(SOURCE_HASH, QUERY_HASH, VARS_HASH), CachedQueryFailure
            ), "a just-written failure is inside a 900s retry window"
        finally:
            c.close()
