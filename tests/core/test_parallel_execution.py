"""Tests for parallel query execution."""

import threading
import time
from collections.abc import Callable
from pathlib import Path
from unittest.mock import Mock, patch

from dbt_charts.cli.filesystem_project import FilesystemProject
from dbt_charts.core.column_types import literal_schema
from dbt_charts.core.compile import compile
from dbt_charts.core.compile.config import ProjectSourcesConfig
from dbt_charts.core.execute import Executor
from dbt_charts.core.execute.adapters import build_adapter_registry
from dbt_charts.core.execute.adapters.base import QueryResult
from dbt_charts.core.execute.duckdb_cache import compute_cache_key
from dbt_charts.core.execute.trivial_local_cache import TrivialDuckDBCache
from dbt_charts.core.render import render

from ._board_utils import make_test_board


def _project_with_duckdb_source(
    local_project: Callable[..., FilesystemProject],
) -> FilesystemProject:
    """A Project whose sources registry resolves the 'duckdb' name to an
    in-memory DuckDB connection (D-09: boards can no longer define sources
    inline, so SIMPLE_BOARD_YAML/DEPENDENT_BOARD_YAML's `source: duckdb` is
    resolved here instead of board-locally)."""
    project = local_project(Path.cwd())
    project.__dict__["sources"] = ProjectSourcesConfig(
        sources={"duckdb": {"type": "duckdb", "path": ":memory:"}}
    )
    return project


def _slow_adapter_execute(delay: float = 0.1):
    """Create a mock adapter execute that simulates slow queries.

    Records thread IDs and timestamps to verify concurrent execution.
    """
    call_log: list[dict] = []

    def execute(query, variables=None, params=None, **kwargs):
        entry = {
            "sql": query.sql,
            "thread": threading.current_thread().ident,
            "start": time.monotonic(),
        }
        time.sleep(delay)
        entry["end"] = time.monotonic()
        call_log.append(entry)
        return QueryResult(
            data=[{"x": 1, "y": 2}], schema=literal_schema([{"x": 1, "y": 2}])
        )

    return execute, call_log


# ── Minimal YAML fixtures ───────────────────────────────────────────

SIMPLE_BOARD_YAML = """\
title: Test Dashboard
source: duckdb
queries:
  sales:
    sql: SELECT 'sales' as x, 2 as y
  users:
    sql: SELECT 'users' as x, 2 as y
  orders:
    sql: SELECT 'orders' as x, 2 as y
charts:
  sales_chart:
    query: sales
    type: bar
    x: x
    y: y
  users_chart:
    query: users
    type: bar
    x: x
    y: y
  orders_chart:
    query: orders
    type: bar
    x: x
    y: y
cols:
  - sales_chart
  - users_chart
  - orders_chart
"""

DEPENDENT_BOARD_YAML = """\
title: Test Dashboard
source: duckdb
queries:
  base_orders:
    sql: SELECT 1 as x, 2 as amount, '2024-06-01' as date
  high_value:
    sql: SELECT * FROM {{ queries.base_orders }} WHERE amount > 1000
  recent:
    sql: SELECT * FROM {{ queries.base_orders }} WHERE date >= '2024-01-01'
charts:
  high_value_chart:
    query: high_value
    type: bar
    x: x
    y: amount
  recent_chart:
    query: recent
    type: bar
    x: x
    y: amount
rows:
  - high_value_chart
  - recent_chart
"""

# ── Tests ────────────────────────────────────────────────────────────


class TestCollectChartQueries:
    """Test collecting query names from the layout tree."""

    def test_collects_all_chart_queries(self):
        from dbt_charts.core.execute.collect import collect_layout_chart_query_names

        result = compile(SIMPLE_BOARD_YAML)
        assert result.success, result.errors
        names = collect_layout_chart_query_names(result.board)
        assert names == {"sales", "users", "orders"}

    def test_collects_only_direct_chart_queries(self):
        """Transitive {{ queries.X }} deps are NOT collected — they're inlined."""
        from dbt_charts.core.execute.collect import collect_layout_chart_query_names

        result = compile(DEPENDENT_BOARD_YAML)
        assert result.success, result.errors
        names = collect_layout_chart_query_names(result.board)
        # Only chart-direct queries, NOT base_orders (which is a dep only)
        assert names == {"high_value", "recent"}
        assert "base_orders" not in names

    def test_empty_board_returns_empty(self):
        from dbt_charts.core.execute.collect import collect_layout_chart_query_names

        board = make_test_board(
            id="empty",
            title="Empty",
            charts={},
            queries={},
            sources={},
            variable_defaults={},
        )
        names = collect_layout_chart_query_names(board)
        assert names == set()


class TestExecuteQueriesParallel:
    """Test parallel query execution."""

    def test_independent_queries_run_concurrently(self):
        """Verify independent queries actually run in parallel via thread IDs."""
        from dbt_charts.core.execute.parallel import execute_queries_parallel

        result = compile(SIMPLE_BOARD_YAML)
        assert result.success, result.errors
        mock_execute, call_log = _slow_adapter_execute(delay=0.1)

        mock_registry = Mock()
        mock_registry.execute.side_effect = mock_execute

        executor = Executor(result.board, adapter_registry=mock_registry)

        start = time.monotonic()
        execute_queries_parallel(executor, {"sales", "users", "orders"})
        elapsed = time.monotonic() - start

        assert len(call_log) == 3

        # Verify different threads were used (the real invariant)
        threads = {entry["thread"] for entry in call_log}
        assert len(threads) > 1, "All queries ran on the same thread"

        # Loose timing check: parallel should be significantly faster than serial
        # 3 × 0.1s serial = 0.3s. Parallel must beat that. Use 0.25s to leave margin.
        assert elapsed < 0.25, f"Queries not running in parallel: {elapsed:.2f}s"

    def test_results_cached_after_parallel_execution(self):
        """After parallel execution, all execute_chart() calls should be cache hits."""
        from dbt_charts.core.execute.parallel import execute_queries_parallel

        result = compile(SIMPLE_BOARD_YAML)
        assert result.success, result.errors

        mock_registry = Mock()
        mock_registry.execute.return_value = QueryResult(
            data=[{"x": 1, "y": 2}], schema=literal_schema([{"x": 1, "y": 2}])
        )

        executor = Executor(result.board, adapter_registry=mock_registry)

        execute_queries_parallel(executor, {"sales", "users", "orders"})

        # Now execute_chart should be cache hits (no new adapter calls)
        call_count_before = mock_registry.execute.call_count
        for chart in result.board.charts.values():
            executor.execute_chart(chart)
        call_count_after = mock_registry.execute.call_count

        assert call_count_after == call_count_before, (
            "execute_chart made new adapter calls after parallel execution — cache not working"
        )

    def test_error_in_one_query_doesnt_block_others(self):
        """If one query errors, others should still complete."""
        from dbt_charts.core.execute.parallel import execute_queries_parallel

        result = compile(SIMPLE_BOARD_YAML)
        assert result.success, result.errors

        def flaky_execute(query, variables=None, params=None, **kwargs):
            if "sales" in query.sql.lower():
                raise RuntimeError("Connection refused")
            return QueryResult(
                data=[{"x": 1, "y": 2}], schema=literal_schema([{"x": 1, "y": 2}])
            )

        mock_registry = Mock()
        mock_registry.execute.side_effect = flaky_execute

        executor = Executor(result.board, adapter_registry=mock_registry)

        errors = execute_queries_parallel(executor, {"sales", "users", "orders"})

        # sales should have an error
        assert errors["sales"] is not None
        # users and orders should succeed
        assert errors["users"] is None
        assert errors["orders"] is None

    def test_empty_query_set(self):
        """Empty query set should be a no-op."""
        board = make_test_board(
            id="empty",
            title="Empty",
            charts={},
            queries={},
            sources={},
            variable_defaults={},
        )
        mock_registry = Mock()
        executor = Executor(board, adapter_registry=mock_registry)

        from dbt_charts.core.execute.parallel import execute_queries_parallel

        errors = execute_queries_parallel(executor, set())
        assert errors == {}
        assert mock_registry.execute.call_count == 0


class TestExecutorCacheThreadSafety:
    """Test that the executor cache is safe for concurrent access."""

    def test_concurrent_cache_writes(self):
        """Multiple threads writing to cache simultaneously shouldn't corrupt it."""
        result = compile(SIMPLE_BOARD_YAML)
        assert result.success, result.errors
        mock_registry = Mock()
        mock_registry.execute.return_value = QueryResult(
            data=[{"x": 1, "y": 2}], schema=literal_schema([{"x": 1, "y": 2}])
        )
        executor = Executor(result.board, adapter_registry=mock_registry)

        errors = []

        def write_cache(key: str):
            try:
                for _ in range(100):
                    executor._cache[key] = [{"x": 1}]
                    _ = executor._cache.get(key)
            except Exception as e:  # noqa: BLE001
                errors.append(e)

        threads = [
            threading.Thread(target=write_cache, args=(f"key_{i}",)) for i in range(10)
        ]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert not errors, f"Cache operations raised errors: {errors}"


class TestDuckDBIntegration:
    """Integration test: parallel execution with the real DuckDB adapter."""

    def test_parallel_queries_with_real_duckdb(
        self, local_project: Callable[..., FilesystemProject]
    ):
        """Execute multiple queries concurrently against real DuckDB.

        Verifies thread safety: the DuckDB adapter's _DUCKDB_EXECUTE_LOCK
        serializes DuckDB access, so concurrent submit is safe.
        """
        from dbt_charts.core.execute.collect import collect_layout_chart_query_names
        from dbt_charts.core.execute.parallel import execute_queries_parallel

        result = compile(SIMPLE_BOARD_YAML)
        assert result.success, result.errors

        executor = Executor(
            result.board,
            adapter_registry=build_adapter_registry(
                _project_with_duckdb_source(local_project)
            ),
        )  # real adapter registry

        query_names = collect_layout_chart_query_names(result.board)
        errors = execute_queries_parallel(executor, query_names)

        # All queries should succeed
        for name, err in errors.items():
            assert err is None, f"Query '{name}' failed: {err}"

        # All results should be cached
        for chart in result.board.charts.values():
            data = executor.execute_chart(chart)
            assert len(data) > 0, f"No data for chart '{chart.id}'"

    def test_render_with_duckdb_cache_caches_parallel_query_results(
        self, tmp_path, local_project: Callable[..., FilesystemProject]
    ):
        """Render pre-executes independent chart queries and writes them to DuckDB."""
        result = compile(SIMPLE_BOARD_YAML)
        assert result.success, result.errors

        cache = TrivialDuckDBCache(db_path=tmp_path / "query_cache.duckdb")
        try:
            executor = Executor(
                result.board,
                adapter_registry=build_adapter_registry(
                    _project_with_duckdb_source(local_project)
                ),
                result_cache=cache,
            )
            rendered = render(result.board, executor, format="html")
            assert rendered.output

            for query_name in ("sales", "users", "orders"):
                query = result.board.queries[query_name]
                source_hash, query_hash, variables_hash = compute_cache_key(
                    query, board_sources=result.board.sources
                )
                hit = cache.get(source_hash, query_hash, variables_hash)
                assert hit is not None, f"{query_name} was not cached"
                assert hit.rows[0]["x"] == query_name
        finally:
            cache.close()


class TestStoredPreExecutionErrors:
    """Pre-execution errors are stored and raised during render without re-executing."""

    def test_stored_error_raised_without_reexecution(self):
        """execute_chart raises the stored pre-execution error and does NOT
        re-execute the query against the adapter."""
        import pytest

        from dbt_charts.core.diagnostics.execution import QueryError

        result = compile(SIMPLE_BOARD_YAML)
        assert result.success, result.errors

        call_count = 0

        def counting_execute(query, variables=None, params=None, **kwargs):
            nonlocal call_count
            if "'sales'" in query.sql:
                call_count += 1
                raise RuntimeError("Connection refused")
            return QueryResult(
                data=[{"x": 1, "y": 2}], schema=literal_schema([{"x": 1, "y": 2}])
            )

        mock_registry = Mock()
        mock_registry.execute.side_effect = counting_execute

        executor = Executor(result.board, adapter_registry=mock_registry)

        from dbt_charts.core.execute.collect import collect_layout_chart_query_names
        from dbt_charts.core.execute.parallel import execute_queries_parallel

        # Pre-execution — sales fails once
        query_names = collect_layout_chart_query_names(result.board)
        errors = execute_queries_parallel(executor, query_names)
        assert errors["sales"] is not None
        assert call_count == 1

        # Phase 2: execute_chart raises stored error, adapter NOT called again
        with pytest.raises(QueryError, match="Connection refused"):
            executor.execute_chart("sales_chart")
        assert call_count == 1, (
            "Adapter was called again — render should use stored error"
        )


class TestCacheHitPartitioning:
    """The pre-render stage must not submit cached queries to the thread pool.

    Pins the scheduling property: when some queries are already cached,
    only the uncached names reach execute_queries_parallel. Cache hits
    are resolved synchronously on the calling thread.
    """

    def test_cached_queries_excluded_from_parallel_submit(self):
        """Pre-render submits only uncached queries to the pool.

        Setup: three queries on the board; two pre-warmed in the in-memory cache.
        Assert: execute_queries_parallel receives only the one cache miss.
        The two hits are resolved synchronously before the pool submit.
        """
        import importlib

        renderer_mod = importlib.import_module("dbt_charts.core.render.renderer")
        yaml_format_mod = importlib.import_module("dbt_charts.core.render.yaml_format")

        result = compile(SIMPLE_BOARD_YAML)
        assert result.success, result.errors

        mock_registry = Mock()
        mock_registry.execute.return_value = QueryResult(
            data=[{"x": 1, "y": 2}], schema=literal_schema([{"x": 1, "y": 2}])
        )
        executor = Executor(result.board, adapter_registry=mock_registry)

        # Pre-warm cache for sales and users — these are cache hits.
        executor.execute_query("sales")
        executor.execute_query("users")
        # orders is NOT cached — it is the miss that must go to the pool.

        submitted_names: list[set] = []

        def capturing_parallel(_exec, names, variables=None, max_workers=8):
            submitted_names.append(set(names))
            return dict.fromkeys(names)

        with (
            patch.object(renderer_mod, "execute_queries_parallel", capturing_parallel),
            patch.object(
                yaml_format_mod, "render_board_yaml", return_value="yaml: true\n"
            ),
        ):
            renderer_mod.render(result.board, executor, format="yaml")

        assert len(submitted_names) == 1, (
            "execute_queries_parallel not called exactly once"
        )
        # Only the uncached query should be in the pool submit set.
        assert submitted_names[0] == {"orders"}, (
            f"Expected only {{'orders'}} in parallel submit; got {submitted_names[0]}"
        )

    def test_all_cached_means_empty_pool_submit(self):
        """When every parallel query is cached, execute_queries_parallel gets an empty set.

        The empty-set fast path in execute_queries_parallel returns {} with no
        thread pool created — verified by the existing test_empty_query_set test.
        This test only asserts that the renderer passes an empty set.
        """
        import importlib

        renderer_mod = importlib.import_module("dbt_charts.core.render.renderer")
        yaml_format_mod = importlib.import_module("dbt_charts.core.render.yaml_format")

        result = compile(SIMPLE_BOARD_YAML)
        assert result.success, result.errors

        mock_registry = Mock()
        mock_registry.execute.return_value = QueryResult(
            data=[{"x": 1, "y": 2}], schema=literal_schema([{"x": 1, "y": 2}])
        )
        executor = Executor(result.board, adapter_registry=mock_registry)

        # Pre-warm all three queries.
        executor.execute_query("sales")
        executor.execute_query("users")
        executor.execute_query("orders")

        submitted_names: list[set] = []

        def capturing_parallel(_exec, names, variables=None, max_workers=8):
            submitted_names.append(set(names))
            return {}

        with (
            patch.object(renderer_mod, "execute_queries_parallel", capturing_parallel),
            patch.object(
                yaml_format_mod, "render_board_yaml", return_value="yaml: true\n"
            ),
        ):
            renderer_mod.render(result.board, executor, format="yaml")

        assert len(submitted_names) == 1
        assert submitted_names[0] == set(), (
            f"Expected empty parallel submit when all cached; got {submitted_names[0]}"
        )


class TestIsCached:
    """Direct contract for Executor.is_cached — the success-cache peek used by
    the pre-render partition. True only for a warmed success cache."""

    def _executor(self) -> Executor:
        result = compile(SIMPLE_BOARD_YAML)
        assert result.success, result.errors
        mock_registry = Mock()
        mock_registry.execute.return_value = QueryResult(
            data=[{"x": 1, "y": 2}], schema=literal_schema([{"x": 1, "y": 2}])
        )
        return Executor(result.board, adapter_registry=mock_registry)

    def test_false_before_execution(self):
        executor = self._executor()
        assert executor.is_cached("sales") is False

    def test_true_after_execution(self):
        executor = self._executor()
        executor.execute_query("sales")
        assert executor.is_cached("sales") is True

    def test_strips_queries_prefix(self):
        executor = self._executor()
        executor.execute_query("sales")
        assert executor.is_cached("queries.sales") is True

    def test_unknown_query_is_not_cached(self):
        """An unknown name returns False, not a raise — the partition treats it
        as a miss and execute_query surfaces the error on the real call."""
        executor = self._executor()
        assert executor.is_cached("does_not_exist") is False

    def test_memo_hit_reports_true_even_with_use_cache_off(self):
        """`use_cache=False` disables the persistent store, not the memo —
        is_cached must keep predicting execute_query, which serves memo rows."""
        result = compile(SIMPLE_BOARD_YAML)
        assert result.success, result.errors
        mock_registry = Mock()
        mock_registry.execute.return_value = QueryResult(
            data=[{"x": 1, "y": 2}], schema=literal_schema([{"x": 1, "y": 2}])
        )
        executor = Executor(
            result.board, adapter_registry=mock_registry, use_cache=False
        )
        assert executor.is_cached("sales") is False
        executor.execute_query("sales")
        assert executor.is_cached("sales") is True


def test_execute_queries_parallel_propagates_attribution_to_every_worker() -> None:
    """A fan-out render must label EVERY query with the calling actor.

    `attribute()`'s ContextVar does not cross `ThreadPoolExecutor.submit` on
    its own (its own docstring warns of exactly this) — this is a regression
    test through the real `execute_queries_parallel` + a real thread pool
    with `max_workers > 1`, not a unit test of the ContextVar in isolation.
    A dropped propagation would silently omit the actor label on every query
    that fans out, which is the multi-query path that matters most.
    """
    from dbt_charts.core.attribution import attribute, current_attribution
    from dbt_charts.core.execute.parallel import execute_queries_parallel

    seen: dict[str, str | None] = {}

    def _record(name: str, variables: object = None) -> list[dict[str, object]]:
        seen[name] = current_attribution().get("dbt_charts_actor")
        return []

    stub = Mock()
    stub.execute_query.side_effect = _record

    with attribute({"actor": "tenant-a"}, {}):
        execute_queries_parallel(stub, {"a", "b", "c"}, max_workers=3)

    assert seen == {"a": "tenant-a", "b": "tenant-a", "c": "tenant-a"}


def test_execute_queries_parallel_names_pool_threads_with_render_prefix() -> None:
    """The pool threads must carry RENDER_POOL_THREAD_PREFIX.

    A host keys per-query thread-local cleanup on this prefix (Cloud reaps the
    result-cache connection only on pool workers), so dropping the stamp would
    silently disable that cleanup with no other test failing. Pin it here.
    """
    from dbt_charts.core.execute.parallel import (
        RENDER_POOL_THREAD_PREFIX,
        execute_queries_parallel,
    )

    seen: dict[str, str] = {}

    def _record(name: str, variables: object = None) -> list[dict[str, object]]:
        seen[name] = threading.current_thread().name
        return []

    stub = Mock()
    stub.execute_query.side_effect = _record

    execute_queries_parallel(stub, {"a", "b"}, max_workers=2)

    assert set(seen) == {"a", "b"}
    assert all(name.startswith(RENDER_POOL_THREAD_PREFIX) for name in seen.values()), (
        f"pool threads not named with {RENDER_POOL_THREAD_PREFIX!r}: {seen}"
    )
