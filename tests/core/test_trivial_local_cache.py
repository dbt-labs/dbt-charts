"""Tests for TrivialDuckDBCache: replace-on-write, truncation, failures."""

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
