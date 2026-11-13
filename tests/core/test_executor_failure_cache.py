"""Tests for Executor failure caching and force_refresh integration.

Verifies that Executor.execute_query:
- Caches failures to DuckDB when result_cache is configured
- Replays a cached failure as a QueryError carrying its original code
- Respects force_refresh=True to clear and re-run
"""

from datetime import UTC
from unittest.mock import Mock

import pytest

from dbt_charts.core.compile import compile
from dbt_charts.core.diagnostics import (
    ERR_BINDER_GROUP_BY,
    ERR_INTERNAL,
)
from dbt_charts.core.diagnostics.base import DbtChartsError
from dbt_charts.core.diagnostics.codes_execute import ERR_DBT_REF_UNKNOWN_NODE
from dbt_charts.core.execute import QueryError
from dbt_charts.core.execute.cache_backend import CachedQueryFailure
from dbt_charts.core.execute.duckdb_cache import compute_cache_key
from dbt_charts.core.execute.lookup import Ledger, memo_key
from dbt_charts.core.execute.plan import plan
from dbt_charts.core.execute.query_result import QueryResult
from dbt_charts.core.execute.trivial_local_cache import TrivialDuckDBCache
from dbt_charts.core.text.time_zones import UTC as BOARD_UTC

from .._results import rows_result, rows_schema
from .._run import Run
from .._scripted_env import ScriptedBackend, scripted_env

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


def _make_executor(
    routes: Mock, result_cache=None, adapter_result=None, adapter_side_effect=None
):
    """Build a run whose routes answer from *adapter_result* or *adapter_side_effect*."""
    result = compile(BOARD_YAML)
    if adapter_side_effect:
        routes.side_effect = adapter_side_effect
    elif adapter_result is not None:
        routes.return_value = adapter_result
    else:
        data = [{"value": 1}]
        routes.return_value = QueryResult(data=data, schema=rows_schema(data))

    return Run(
        result.board,
        env=scripted_env(ScriptedBackend()),
        result_cache=result_cache,
    )


class TestQueryErrorCachedViaIsSuccessFalsePath:
    """QueryError from the is_success=False adapter path must still be cached.

    Regression for: the DbtChartsError guard was too broad — QueryError IS-A DbtChartsError,
    so DuckDB failures returned as QueryResult(is_success=False) were skipped by the guard
    and never cached, making the failure cache a no-op for the playground's only adapter.
    """

    def test_query_error_from_failed_adapter_result_is_cached(self, routes: Mock):
        failed_result = _failed_result(QueryError("column 'x' not found"))

        cache = TrivialDuckDBCache(failure_ttl_seconds=900)
        try:
            executor = _make_executor(
                routes, result_cache=cache, adapter_result=failed_result
            )

            with pytest.raises(QueryError):
                executor.execute_query("good_query")

            # The next render must replay from cache, not hit the adapter again
            executor.ledger = Ledger()
            with pytest.raises(QueryError) as exc_info:
                executor.execute_query("good_query")
            assert isinstance(exc_info.value.__cause__, CachedQueryFailure)
        finally:
            cache.close()


def _failed_result(error: QueryError) -> QueryResult:
    return QueryResult(data=[], error=error)


class TestCachedFailureKeepsItsCode:
    """A failure replayed from the persistent cache carries the code it was raised with.

    Regression for: the cached replay surfaced as ERR-INTERNAL, so a second
    `dct render` of a board with a bad GROUP BY reported a bug instead of
    ERR-BINDER-GROUP-BY. (Unknown-column failures are no longer cached at all.)
    """

    def test_replayed_failure_has_the_original_code(self, routes: Mock):
        cache = TrivialDuckDBCache(failure_ttl_seconds=900)
        try:
            error = QueryError.from_code(ERR_BINDER_GROUP_BY, detail="no col")
            fresh = _make_executor(
                routes, result_cache=cache, adapter_result=_failed_result(error)
            )
            with pytest.raises(QueryError) as first:
                fresh.execute_query("good_query")

            replay = _make_executor(routes, result_cache=cache)
            with pytest.raises(QueryError) as second:
                replay.execute_query("good_query")

            routes.assert_called_once()  # the fresh run's call; the replay made none
            assert second.value.code is ERR_BINDER_GROUP_BY
            assert str(second.value) == str(first.value)
            assert second.value.fields["query_name"] == "good_query"
        finally:
            cache.close()

    def test_replayed_failure_keeps_the_fields_its_hint_needs(self, routes: Mock):
        cache = TrivialDuckDBCache(failure_ttl_seconds=900)
        try:
            error = QueryError.from_code(
                ERR_DBT_REF_UNKNOWN_NODE,
                ref_name="ordrs",
                available=["orders", "customers"],
                why="",
            )
            fresh = _make_executor(
                routes, result_cache=cache, adapter_result=_failed_result(error)
            )
            with pytest.raises(QueryError) as first:
                fresh.execute_query("good_query")

            replay = _make_executor(routes, result_cache=cache)
            with pytest.raises(QueryError) as second:
                replay.execute_query("good_query")

            fresh_hint = first.value.to_diagnostic().hint
            assert fresh_hint
            assert second.value.to_diagnostic().hint == fresh_hint
            assert second.value.fields == first.value.fields
        finally:
            cache.close()

    def test_untyped_failure_replays_as_internal_like_a_fresh_one(self, routes: Mock):
        cache = TrivialDuckDBCache(failure_ttl_seconds=900)
        try:
            fresh = _make_executor(
                routes, result_cache=cache, adapter_side_effect=RuntimeError("boom")
            )
            with pytest.raises(QueryError) as first:
                fresh.execute_query("good_query")

            replay = _make_executor(routes, result_cache=cache)
            with pytest.raises(QueryError) as second:
                replay.execute_query("good_query")

            assert routes.call_count == 1  # the replay came from the cache
            assert first.value.code is ERR_INTERNAL
            assert second.value.code is ERR_INTERNAL
        finally:
            cache.close()

    def test_unregistered_code_is_a_miss_and_reruns(self, routes: Mock):
        """A code this version does not know (renamed or retired since the
        write) cannot be replayed faithfully, so the query runs again."""
        cache = TrivialDuckDBCache(failure_ttl_seconds=900)
        try:
            error = QueryError.from_code(ERR_BINDER_GROUP_BY, detail="x")
            fresh = _make_executor(
                routes, result_cache=cache, adapter_result=_failed_result(error)
            )
            with pytest.raises(QueryError):
                fresh.execute_query("good_query")
            with cache._connection() as conn:
                conn.execute("UPDATE _query_outcomes SET error_code = 'ERR-GONE'")

            replay = _make_executor(routes, result_cache=cache)
            assert replay.execute_query("good_query") == [{"value": 1}]
            assert routes.call_count == 2  # the fresh run's call, then the rerun
        finally:
            cache.close()


class TestDbtChartsErrorNotCached:
    """DbtChartsError (policy/compile errors) must never be stored in the failure cache.

    Regression for: DbtChartsError cached as CachedQueryFailure loses the structured
    error message (dataclass __init__ doesn't call Exception.__init__(msg)), so
    str(cached_failure) == '' and the human-readable text is silently discarded on replay.
    """

    def test_dbt_charts_error_not_written_to_failure_cache(self, routes: Mock):
        cache = TrivialDuckDBCache(failure_ttl_seconds=900)
        try:
            result = compile(BOARD_YAML)
            policy_error = DbtChartsError("inline source not allowed")

            call_count = 0

            def raise_policy(*args, **kwargs):
                nonlocal call_count
                call_count += 1
                raise policy_error

            routes.side_effect = raise_policy
            executor = Run(
                result.board,
                env=scripted_env(ScriptedBackend()),
                result_cache=cache,
            )

            # First call raises QueryError (wraps DbtChartsError)
            with pytest.raises(QueryError):
                executor.execute_query("good_query")
            assert call_count == 1

            # Confirm nothing was stored as this key's outcome
            q = result.board.queries["good_query"]
            source_hash, query_hash, variables_hash = compute_cache_key(
                q, None, result.board.sources, time_zone=BOARD_UTC
            )
            assert cache.get(source_hash, query_hash, variables_hash) is None

            # The next render must also hit the adapter (not replay a cached failure)
            executor.ledger = Ledger()
            with pytest.raises(QueryError):
                executor.execute_query("good_query")
            assert call_count == 2
        finally:
            cache.close()


class TestCachedQueryFailureMessage:
    """CachedQueryFailure must preserve error_message in str() and args."""

    def test_str_returns_error_message(self):
        from datetime import datetime

        exc = CachedQueryFailure(
            error_class="RuntimeError",
            error_message="table not found",
            error_code=None,
            error_fields={},
            traceback="",
            failed_at=datetime.now(UTC),
        )
        assert str(exc) == "table not found"
        assert "table not found" in repr(exc)


class TestExecutorCachesFailureToDuckDB:
    """When result_cache is set and a query fails, the failure is cached."""

    def test_failure_cached_and_returned_on_second_call(self, routes: Mock):
        cache = TrivialDuckDBCache(failure_ttl_seconds=900)
        try:
            call_count = 0

            def fail_once(*args, **kwargs):
                nonlocal call_count
                call_count += 1
                raise RuntimeError("table not found")

            executor = _make_executor(
                routes,
                result_cache=cache,
                adapter_side_effect=fail_once,
            )

            # First call: fresh failure
            with pytest.raises(QueryError):
                executor.execute_query("good_query")

            assert call_count == 1

            # The next render replays the cached failure without hitting adapter
            executor.ledger = Ledger()
            with pytest.raises(QueryError) as exc_info:
                executor.execute_query("good_query")

            cached = exc_info.value.__cause__
            assert isinstance(cached, CachedQueryFailure)
            assert cached.error_class == "RuntimeError"
            assert "table not found" in cached.error_message
            # Adapter was only called once (first time)
            assert call_count == 1
        finally:
            cache.close()


class TestInMemoryErrorShortCircuitsResultCache:
    """A query known-failed in memory must not pay a result-cache round-trip.

    On an all-errored dashboard the serial layout-sizing pass re-runs each
    failing query's ``execute_query`` per chart. The in-memory ``ledger.errors``
    memo (populated by the parallel pre-execution pass) already knows the query
    failed, so the persistent outcome probe (``result_cache.get``) — a
    guaranteed miss and, in Cloud, a full Postgres round-trip — must be
    skipped. This pins that the in-memory error check runs before any
    ``result_cache`` read.
    """

    def test_stored_error_raises_without_touching_result_cache(
        self, routes: Mock
    ) -> None:
        result_cache = Mock()
        result_cache.get.return_value = None
        executor = _make_executor(routes, result_cache=result_cache)

        stored = QueryError("Source 'analytics_bq' not found", "good_query")
        executor.ledger.errors["good_query"] = stored

        with pytest.raises(QueryError):
            executor.execute_query("good_query")

        # Known-failed in memory: the persistent outcome probe is skipped.
        result_cache.get.assert_not_called()

    def test_in_memory_cache_wins_over_query_errors(self, routes: Mock) -> None:
        """The ledger memo's rows win even when ``ledger.errors`` has an entry.

        Regression for: parallel pre-execution writes the memo before
        attempting ``cache_outcome``. If the persistent write-through raises
        (e.g. Postgres FK constraint in the Cloud snapshots backend), the
        exception lands in ``ledger.errors`` — but the memo already holds the valid
        rows from the successful query. Checking the memo before the errors
        ensures those rows are returned rather than raising a spurious error.
        """
        result_cache = Mock()
        result_cache.get.return_value = None
        executor = _make_executor(routes, result_cache=result_cache)

        # Simulate: query succeeded, result is in _cache…
        [planned] = plan(executor.ctx, ["good_query"])
        valid_rows = [{"name": "acme-marker-001"}]
        # The memo is keyed by content *plus* the asking query's policy, so seed
        # it through the production key builder — a raw content key would land in
        # a slot execute_query never reads.
        executor.ledger.memo[memo_key(planned.entry, planned.cache_key)] = rows_result(
            valid_rows
        )

        # …but the persistent write-through failed and stored an error.
        executor.ledger.errors["good_query"] = RuntimeError("FK constraint violation")

        # _cache result must win.
        result = executor.execute_query("good_query")
        assert result == valid_rows


class TestExecutorForceRefresh:
    """force_refresh=True clears cache and re-runs the query."""

    def test_force_refresh_reruns_after_failure(self, routes: Mock):
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
                ok.schema = rows_schema(ok.data)
                ok.resolved_relations = None
                ok.truncated_reason = None
                return ok

            executor = _make_executor(
                routes,
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

    def test_force_refresh_reruns_after_success(self, routes: Mock):
        cache = TrivialDuckDBCache(failure_ttl_seconds=900)
        try:
            calls = []

            def adapter_fn(*args, **kwargs):
                calls.append(1)
                ok = Mock()
                ok.is_success = True
                ok.data = [{"value": len(calls)}]
                ok.schema = rows_schema(ok.data)
                ok.resolved_relations = None
                ok.truncated_reason = None
                return ok

            executor = _make_executor(
                routes,
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

    def test_adapter_execution_error_code_preserved_in_query_error(self, routes: Mock):
        from dbt_charts.core.diagnostics.codes_execute import (
            ERR_DBT_MANIFEST_UNREADABLE,
        )
        from dbt_charts.core.diagnostics.execution import ExecutionError

        err = ExecutionError.from_code(
            ERR_DBT_MANIFEST_UNREADABLE,
            relpath="target/manifest.json",
            detail="Expecting value: line 1 column 1 (char 0)",
        )
        executor = _make_executor(routes, adapter_side_effect=err)

        with pytest.raises(QueryError) as exc_info:
            executor.execute_query("good_query", use_cache=False)

        assert exc_info.value.code is ERR_DBT_MANIFEST_UNREADABLE
