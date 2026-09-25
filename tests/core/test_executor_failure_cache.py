"""Tests for Executor failure caching and force_refresh integration.

Verifies that Executor.execute_query:
- Caches failures to DuckDB when result_cache is configured
- Returns CachedQueryFailure on repeat calls within TTL
- Respects force_refresh=True to clear and re-run
"""

from unittest.mock import Mock

import pytest

from dbt_charts.core.compile import compile
from dbt_charts.core.diagnostics.base import DbtChartsError
from dbt_charts.core.execute import Executor, QueryError
from dbt_charts.core.execute.cache_backend import CachedQueryFailure
from dbt_charts.core.execute.duckdb_cache import compute_cache_key
from dbt_charts.core.execute.executor import _memo_key
from dbt_charts.core.execute.trivial_local_cache import TrivialDuckDBCache

BOARD_YAML = """
title: Test
queries:
  good_query:
    sql: SELECT 1 as value
    source: test_profile
  bad_query:
    sql: SELECT * FROM nonexistent
    source: test_profile
charts:
  c:
    query: good_query
    type: kpi
    value: value
rows:
  - c
"""


def _make_executor(result_cache=None, adapter_result=None, adapter_side_effect=None):
    """Build an Executor with a mock adapter registry."""
    result = compile(BOARD_YAML)
    mock_registry = Mock()
    if adapter_side_effect:
        mock_registry.execute.side_effect = adapter_side_effect
    elif adapter_result is not None:
        mock_registry.execute.return_value = adapter_result
    else:
        ok = Mock()
        ok.is_success = True
        ok.data = [{"value": 1}]
        ok.column_descriptions = None
        ok.resolved_relations = None
        ok.truncated_reason = None
        mock_registry.execute.return_value = ok

    return Executor(
        result.board,
        adapter_registry=mock_registry,
        query_registry=result.query_registry,
        result_cache=result_cache,
    )


class TestQueryErrorCachedViaIsSuccessFalsePath:
    """QueryError from the is_success=False adapter path must still be cached.

    Regression for: the DbtChartsError guard was too broad — QueryError IS-A DbtChartsError,
    so DuckDB failures returned as QueryResult(is_success=False) were skipped by the guard
    and never cached, making the failure cache a no-op for the playground's only adapter.
    """

    def test_query_error_from_failed_adapter_result_is_cached(self):
        failed_result = Mock()
        failed_result.is_success = False
        failed_result.error = QueryError("column 'x' not found")
        failed_result.column_descriptions = None
        failed_result.resolved_relations = None
        failed_result.truncated_reason = None

        cache = TrivialDuckDBCache(failure_ttl_seconds=900)
        try:
            executor = _make_executor(result_cache=cache, adapter_result=failed_result)

            with pytest.raises(QueryError):
                executor.execute_query("good_query")

            # Second call must replay from cache, not hit the adapter again
            with pytest.raises(CachedQueryFailure):
                executor.execute_query("good_query")
        finally:
            cache.close()


class TestDbtChartsErrorNotCached:
    """DbtChartsError (policy/compile errors) must never be stored in the failure cache.

    Regression for: DbtChartsError cached as CachedQueryFailure loses the structured
    error message (dataclass __init__ doesn't call Exception.__init__(msg)), so
    str(cached_failure) == '' and the human-readable text is silently discarded on replay.
    """

    def test_dbt_charts_error_not_written_to_failure_cache(self):
        cache = TrivialDuckDBCache(failure_ttl_seconds=900)
        try:
            result = compile(BOARD_YAML)
            policy_error = DbtChartsError("inline source not allowed")

            call_count = 0

            def raise_policy(*args, **kwargs):
                nonlocal call_count
                call_count += 1
                raise policy_error

            executor = Executor(
                result.board,
                adapter_registry=Mock(execute=Mock(side_effect=raise_policy)),
                query_registry=result.query_registry,
                result_cache=cache,
            )

            # First call raises QueryError (wraps DbtChartsError)
            with pytest.raises(QueryError):
                executor.execute_query("good_query")
            assert call_count == 1

            # Confirm nothing was stored as this key's outcome
            q = result.board.queries["good_query"]
            source_hash, query_hash, variables_hash = compute_cache_key(
                q, None, result.board.sources
            )
            assert cache.get(source_hash, query_hash, variables_hash) is None

            # Second call must also hit the adapter (not replay a cached failure)
            with pytest.raises(QueryError):
                executor.execute_query("good_query")
            assert call_count == 2
        finally:
            cache.close()


class TestCachedQueryFailureMessage:
    """CachedQueryFailure must preserve error_message in str() and args."""

    def test_str_returns_error_message(self):
        from datetime import datetime, timezone

        exc = CachedQueryFailure(
            error_class="RuntimeError",
            error_message="table not found",
            traceback="",
            failed_at=datetime.now(timezone.utc),
        )
        assert str(exc) == "table not found"
        assert "table not found" in repr(exc)


class TestExecutorCachesFailureToDuckDB:
    """When result_cache is set and a query fails, the failure is cached."""

    def test_failure_cached_and_returned_on_second_call(self):
        cache = TrivialDuckDBCache(failure_ttl_seconds=900)
        try:
            call_count = 0

            def fail_once(*args, **kwargs):
                nonlocal call_count
                call_count += 1
                raise RuntimeError("table not found")

            executor = _make_executor(
                result_cache=cache,
                adapter_side_effect=fail_once,
            )

            # First call: fresh failure
            with pytest.raises(QueryError):
                executor.execute_query("good_query")

            assert call_count == 1

            # Second call: should raise CachedQueryFailure without hitting adapter
            with pytest.raises(CachedQueryFailure) as exc_info:
                executor.execute_query("good_query")

            assert exc_info.value.error_class == "RuntimeError"
            assert "table not found" in exc_info.value.error_message
            # Adapter was only called once (first time)
            assert call_count == 1
        finally:
            cache.close()


class TestInMemoryErrorShortCircuitsResultCache:
    """A query known-failed in memory must not pay a result-cache round-trip.

    On an all-errored dashboard the serial layout-sizing pass re-runs each
    failing query's ``execute_query`` per chart. The in-memory ``_query_errors``
    memo (populated by the parallel pre-execution pass) already knows the query
    failed, so the persistent outcome probe (``result_cache.get``) — a
    guaranteed miss and, in Cloud, a full Postgres round-trip — must be
    skipped. This pins that the in-memory error check runs before any
    ``result_cache`` read.
    """

    def test_stored_error_raises_without_touching_result_cache(self) -> None:
        result_cache = Mock()
        result_cache.get.return_value = None
        executor = _make_executor(result_cache=result_cache)

        stored = QueryError("Source 'analytics_bq' not found", "good_query")
        executor._query_errors["good_query"] = stored

        with pytest.raises(QueryError):
            executor.execute_query("good_query")

        # Known-failed in memory: the persistent outcome probe is skipped.
        result_cache.get.assert_not_called()

    def test_in_memory_cache_wins_over_query_errors(self) -> None:
        """In-memory _cache result must be returned even when _query_errors has an entry.

        Regression for: parallel pre-execution writes to _cache before
        attempting _cache_outcome. If the persistent write-through raises
        (e.g. Postgres FK constraint in the Cloud snapshots backend), the
        exception lands in _query_errors — but _cache already holds the valid
        rows from the successful query. Checking _cache before _query_errors
        ensures those rows are returned rather than raising a spurious error.
        """
        result_cache = Mock()
        result_cache.get.return_value = None
        executor = _make_executor(result_cache=result_cache)

        # Simulate: query succeeded, result is in _cache…
        query = executor.board.queries["good_query"]
        cache_key = compute_cache_key(query, None, executor.board.sources)
        valid_rows = [{"name": "acme-marker-001"}]
        # The memo is keyed by content *plus* the asking query's policy, so seed
        # it through the production key builder — a raw content key would land in
        # a slot execute_query never reads.
        executor._cache[_memo_key(query, cache_key)] = valid_rows

        # …but the persistent write-through failed and stored an error.
        executor._query_errors["good_query"] = RuntimeError("FK constraint violation")

        # _cache result must win.
        result = executor.execute_query("good_query")
        assert result == valid_rows


class TestExecutorForceRefresh:
    """force_refresh=True clears cache and re-runs the query."""

    def test_force_refresh_reruns_after_failure(self):
        cache = TrivialDuckDBCache(failure_ttl_seconds=900)
        try:
            calls = []

            def adapter_fn(*args, **kwargs):
                calls.append(1)
                if len(calls) == 1:
                    raise RuntimeError("transient error")
                ok = Mock()
                ok.is_success = True
                ok.data = [{"value": 42}]
                ok.column_descriptions = None
                ok.resolved_relations = None
                ok.truncated_reason = None
                return ok

            executor = _make_executor(
                result_cache=cache,
                adapter_side_effect=adapter_fn,
            )

            # First call fails
            with pytest.raises(QueryError):
                executor.execute_query("good_query")

            # force_refresh=True should clear the failure and retry
            data = executor.execute_query("good_query", force_refresh=True)
            assert data == [{"value": 42}]
            assert len(calls) == 2
        finally:
            cache.close()

    def test_force_refresh_reruns_after_success(self):
        cache = TrivialDuckDBCache(failure_ttl_seconds=900)
        try:
            calls = []

            def adapter_fn(*args, **kwargs):
                calls.append(1)
                ok = Mock()
                ok.is_success = True
                ok.data = [{"value": len(calls)}]
                ok.column_descriptions = None
                ok.resolved_relations = None
                ok.truncated_reason = None
                return ok

            executor = _make_executor(
                result_cache=cache,
                adapter_side_effect=adapter_fn,
            )

            # First call succeeds
            data1 = executor.execute_query("good_query")
            assert data1 == [{"value": 1}]

            # Normal call: should return cached
            data2 = executor.execute_query("good_query")
            assert data2 == [{"value": 1}]
            assert len(calls) == 1  # adapter only called once

            # force_refresh: should re-run
            data3 = executor.execute_query("good_query", force_refresh=True)
            assert data3 == [{"value": 2}]
            assert len(calls) == 2
        finally:
            cache.close()


class TestQueryErrorCodePreservation:
    """When adapter.execute() raises DbtChartsError, QueryError must preserve .code.

    Regression for: executor line 499 called QueryError(str(e), query_name) without
    code=..., so DbtChartsError codes (e.g. ERR-DBT-MANIFEST-UNREADABLE) became
    ERR-INTERNAL after wrapping.
    """

    def test_adapter_execution_error_code_preserved_in_query_error(self):
        from dbt_charts.core.diagnostics.codes_execute import (
            ERR_DBT_MANIFEST_UNREADABLE,
        )
        from dbt_charts.core.diagnostics.execution import ExecutionError

        err = ExecutionError.from_code(
            ERR_DBT_MANIFEST_UNREADABLE,
            relpath="target/manifest.json",
            detail="Expecting value: line 1 column 1 (char 0)",
        )
        executor = _make_executor(adapter_side_effect=err)

        with pytest.raises(QueryError) as exc_info:
            executor.execute_query("good_query", use_cache=False)

        assert exc_info.value.code is ERR_DBT_MANIFEST_UNREADABLE
