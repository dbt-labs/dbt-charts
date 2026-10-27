"""Regression tests for the DuckDB success-cache short-circuit in Executor.execute_query.

Verifies that:
- A hit in the DuckDB success cache short-circuits adapter execution entirely.
- The in-memory cache is warmed on a DuckDB hit (second call skips DuckDB too).
- force_refresh=True bypasses the DuckDB success cache and calls the adapter.
"""

from unittest.mock import Mock

from dbt_charts.core.compile import compile
from dbt_charts.core.execute import Executor
from dbt_charts.core.execute.duckdb_cache import (
    compute_cache_key,
    compute_query_hash,
    compute_source_hash,
    compute_variables_hash,
)
from dbt_charts.core.execute.trivial_local_cache import TrivialDuckDBCache

BOARD_YAML = """
title: Test
queries:
  revenue:
    sql: SELECT amount FROM orders
    source: test_profile
charts:
  c:
    query: revenue
    type: kpi
    value: amount
rows:
  - c
"""

CACHED_ROWS = [{"amount": 999}]


def _compiled():
    return compile(BOARD_YAML)


def _make_executor(result_cache: TrivialDuckDBCache) -> tuple[Executor, Mock]:
    """Return (executor, mock_registry) with an adapter that raises if called."""
    result = _compiled()
    mock_registry = Mock()
    mock_registry.execute.side_effect = AssertionError(
        "adapter.execute() must not be called — should have hit result cache",
    )
    executor = Executor(
        result.board,
        adapter_registry=mock_registry,
        query_registry=result.query_registry,
        result_cache=result_cache,
    )
    return executor, mock_registry


def _board_slug() -> str:
    """Return the board slug the Executor._get_board_slug() will compute."""
    result = _compiled()
    board = result.board
    return board.title or board.id or "default"


def _seed_cache(cache: TrivialDuckDBCache) -> None:
    """Seed the DuckDB cache with CACHED_ROWS for the 'revenue' query.

    Uses compute_cache_key — the single source of truth — so the seeded key
    always matches what execute_query will look up.
    """
    result = _compiled()
    query = result.board.queries["revenue"]
    board = result.board

    source_hash, query_hash, variables_hash = compute_cache_key(
        query, board_sources=board.sources
    )

    cache.put(
        source_hash,
        query_hash,
        variables_hash,
        CACHED_ROWS,
        board_slug=_board_slug(),
        query_name="revenue",
    )


class TestDuckDBSuccessCacheShortCircuit:
    """execute_query returns cached rows without calling the adapter."""

    def test_hit_returns_cached_rows_without_adapter_call(self):
        """Canonical regression test: seeded DuckDB cache → adapter never called."""
        cache = TrivialDuckDBCache()
        try:
            _seed_cache(cache)
            executor, mock_registry = _make_executor(cache)

            rows = executor.execute_query("revenue")

            assert rows == CACHED_ROWS
            assert mock_registry.execute.call_count == 0
        finally:
            cache.close()

    def test_in_memory_warmed_on_duckdb_hit(self):
        """DuckDB is hit once; second call within same process is served from memory."""
        cache = TrivialDuckDBCache()
        try:
            _seed_cache(cache)
            executor, mock_registry = _make_executor(cache)

            # Patch get() to count real DuckDB reads
            original_get = cache.get
            duckdb_read_count = []

            def counting_get(*args, **kwargs):
                result = original_get(*args, **kwargs)
                if result is not None:
                    duckdb_read_count.append(1)
                return result

            cache.get = counting_get  # type: ignore[method-assign]

            rows1 = executor.execute_query("revenue")
            rows2 = executor.execute_query("revenue")

            assert rows1 == CACHED_ROWS
            assert rows2 == CACHED_ROWS
            # DuckDB should have been read exactly once; second served from in-memory
            assert len(duckdb_read_count) == 1
            assert mock_registry.execute.call_count == 0
        finally:
            cache.close()

    def test_force_refresh_bypasses_duckdb_and_calls_adapter(self):
        """force_refresh=True clears cache and hits the adapter, not DuckDB."""
        cache = TrivialDuckDBCache()
        try:
            _seed_cache(cache)
            result = _compiled()
            mock_registry = Mock()
            fresh_rows = [{"amount": 1}]
            ok = Mock()
            ok.is_success = True
            ok.data = fresh_rows
            ok.resolved_relations = None
            ok.truncated_reason = None
            mock_registry.execute.return_value = ok

            executor = Executor(
                result.board,
                adapter_registry=mock_registry,
                query_registry=result.query_registry,
                result_cache=cache,
            )

            rows = executor.execute_query("revenue", force_refresh=True)

            assert rows == fresh_rows
            assert mock_registry.execute.call_count == 1
        finally:
            cache.close()

    def test_no_result_cache_configured_hits_adapter(self):
        """When no result cache is configured, adapter is always called."""
        result = _compiled()
        mock_registry = Mock()
        ok = Mock()
        ok.is_success = True
        ok.data = [{"amount": 42}]
        ok.resolved_relations = None
        ok.truncated_reason = None
        mock_registry.execute.return_value = ok

        executor = Executor(
            result.board,
            adapter_registry=mock_registry,
            query_registry=result.query_registry,
            result_cache=None,
        )

        rows = executor.execute_query("revenue", use_cache=False)
        assert rows == [{"amount": 42}]
        assert mock_registry.execute.call_count == 1

    def test_stale_cache_entry_with_different_query_hash_misses(self):
        """A cache entry with a different query_hash is not returned (stale SQL guard).

        Simulates the scenario where board.yaml is edited to change the SQL after
        data was cached.  The short-circuit must return a miss so the adapter
        runs and produces fresh data.
        """
        cache = TrivialDuckDBCache()
        try:
            result = _compiled()

            # Seed cache with a DIFFERENT query_hash (as if SQL had changed)
            board = result.board
            stale_hash = compute_query_hash("SELECT old_column FROM old_table")
            source_hash = compute_source_hash(
                board.queries["revenue"].source, board_sources=board.sources
            )
            variables_hash = compute_variables_hash({})
            cache.put(
                source_hash,
                stale_hash,
                variables_hash,
                [{"amount": 0}],  # stale rows
                board_slug=_board_slug(),
                query_name="revenue",
            )

            # Adapter returns fresh data
            fresh_rows = [{"amount": 999}]
            mock_registry = Mock()
            ok = Mock()
            ok.is_success = True
            ok.data = fresh_rows
            ok.resolved_relations = None
            ok.truncated_reason = None
            mock_registry.execute.return_value = ok

            executor = Executor(
                result.board,
                adapter_registry=mock_registry,
                query_registry=result.query_registry,
                result_cache=cache,
            )

            rows = executor.execute_query("revenue")

            # Stale cache entry must be ignored; fresh data from adapter
            assert rows == fresh_rows
            assert mock_registry.execute.call_count == 1
        finally:
            cache.close()
