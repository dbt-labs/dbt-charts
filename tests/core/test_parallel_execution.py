"""Tests for parallel query execution."""

import threading
import time
from collections.abc import Callable
from pathlib import Path
from unittest.mock import patch

from dbt_charts.cli.filesystem_project import FilesystemProject
from dbt_charts.core.column_types import literal_schema
from dbt_charts.core.compile import compile
from dbt_charts.core.compile.config import ProjectSourcesConfig
from dbt_charts.core.execute.env import build_exec_env
from dbt_charts.core.execute.lookup import Miss
from dbt_charts.core.execute.plan import plan
from dbt_charts.core.execute.query_result import QueryResult
from dbt_charts.core.execute.run import Failed
from dbt_charts.core.execute.trivial_local_cache import TrivialDuckDBCache
from dbt_charts.core.render import render

from .._lookup import planned_query, sql_entry
from .._results import rows_result
from .._run import Run
from .._scripted_env import ScriptedBackend, scripted_env


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


def _run_parallel(
    executor: Run, names: set[str], max_workers: int = 8
) -> dict[str, Exception | None]:
    outcomes = executor.run_misses(executor.lookup(names), max_workers)
    return {
        name: outcome.error if isinstance(outcome, Failed) else None
        for name, outcome in outcomes.items()
    }


def _slow_adapter_execute(delay: float = 0.1):
    """Create a mock adapter execute that simulates slow queries.

    Records thread IDs and timestamps to verify concurrent execution.
    """
    call_log: list[dict] = []

    def execute(_source, query):
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

        board = compile("title: Empty\n").board
        mock_registry_backend = ScriptedBackend()
        mock_registry = scripted_env(mock_registry_backend)
        executor = Run(board, env=mock_registry)

        errors = _run_parallel(executor, set())
        assert errors == {}
        assert mock_registry_backend.execute.call_count == 0


class TestExecutorCacheThreadSafety:
    """Test that the executor cache is safe for concurrent access."""

    def test_concurrent_cache_writes(self):
        """Multiple threads writing to cache simultaneously shouldn't corrupt it."""
        result = compile(SIMPLE_BOARD_YAML)
        assert result.success, result.errors
        mock_registry_backend = ScriptedBackend(
            QueryResult(
                data=[{"x": 1, "y": 2}], schema=literal_schema([{"x": 1, "y": 2}])
            )
        )
        mock_registry = scripted_env(mock_registry_backend)
        executor = Run(result.board, env=mock_registry)

        errors = []

        def write_cache(key: str):
            try:
                for _ in range(100):
                    executor.ledger.memo[key] = [{"x": 1}]
                    _ = executor.ledger.memo.get(key)
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

        result = compile(SIMPLE_BOARD_YAML)
        assert result.success, result.errors

        executor = Run(
            result.board,
            env=build_exec_env(_project_with_duckdb_source(local_project)),
        )  # real exec env

        query_names = collect_layout_chart_query_names(result.board)
        errors = _run_parallel(executor, query_names)

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
            executor = Run(
                result.board,
                env=build_exec_env(_project_with_duckdb_source(local_project)),
                result_cache=cache,
            )
            rendered = render(
                result.board, executor.ctx, executor.ledger, format="html"
            )
            assert rendered.output

            for query_name in ("sales", "users", "orders"):
                [planned] = plan(executor.ctx, [query_name])
                hit = cache.get(*planned.cache_key)
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

        def counting_execute(_source, query):
            nonlocal call_count
            if "'sales'" in query.sql:
                call_count += 1
                raise RuntimeError("Connection refused")
            return QueryResult(
                data=[{"x": 1, "y": 2}], schema=literal_schema([{"x": 1, "y": 2}])
            )

        mock_registry_backend = ScriptedBackend(side_effect=counting_execute)
        mock_registry = scripted_env(mock_registry_backend)

        executor = Run(result.board, env=mock_registry)

        from dbt_charts.core.execute.collect import collect_layout_chart_query_names

        # Pre-execution — sales fails once
        query_names = collect_layout_chart_query_names(result.board)
        errors = _run_parallel(executor, query_names)
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
    only the uncached names reach run_misses. Cache hits
    are resolved synchronously on the calling thread.
    """

    def test_cached_queries_excluded_from_parallel_submit(self):
        """Pre-render submits only uncached queries to the pool.

        Setup: three queries on the board; two pre-warmed in the in-memory cache.
        Assert: run_misses receives only the one cache miss.
        The two hits are resolved synchronously before the pool submit.
        """
        import importlib

        renderer_mod = importlib.import_module("dbt_charts.core.render.renderer")
        yaml_format_mod = importlib.import_module("dbt_charts.core.render.yaml_format")

        result = compile(SIMPLE_BOARD_YAML)
        assert result.success, result.errors

        mock_registry_backend = ScriptedBackend(
            QueryResult(
                data=[{"x": 1, "y": 2}], schema=literal_schema([{"x": 1, "y": 2}])
            )
        )
        mock_registry = scripted_env(mock_registry_backend)
        executor = Run(result.board, env=mock_registry)

        # Pre-warm cache for sales and users — these are cache hits.
        executor.execute_query("sales")
        executor.execute_query("users")
        # orders is NOT cached — it is the miss that must go to the pool.

        submitted_names: list[set] = []

        def capturing_parallel(_ctx, _ledger, found, max_workers):
            submitted_names.append({miss.name for miss in found.misses})
            return {}

        with (
            patch("dbt_charts.core.render.renderer.run_misses", capturing_parallel),
            patch.object(
                yaml_format_mod, "render_board_yaml", return_value="yaml: true\n"
            ),
        ):
            renderer_mod.render(
                result.board, executor.ctx, executor.ledger, format="yaml"
            )

        assert len(submitted_names) == 1, "run_misses not called exactly once"
        # Only the uncached query should be in the pool submit set.
        assert submitted_names[0] == {"orders"}, (
            f"Expected only {{'orders'}} in parallel submit; got {submitted_names[0]}"
        )

    def test_all_cached_means_empty_pool_submit(self):
        """When every parallel query is cached, run_misses gets no misses.

        This test asserts that the renderer hands run_misses no misses.
        """
        import importlib

        renderer_mod = importlib.import_module("dbt_charts.core.render.renderer")
        yaml_format_mod = importlib.import_module("dbt_charts.core.render.yaml_format")

        result = compile(SIMPLE_BOARD_YAML)
        assert result.success, result.errors

        mock_registry_backend = ScriptedBackend(
            QueryResult(
                data=[{"x": 1, "y": 2}], schema=literal_schema([{"x": 1, "y": 2}])
            )
        )
        mock_registry = scripted_env(mock_registry_backend)
        executor = Run(result.board, env=mock_registry)

        # Pre-warm all three queries.
        executor.execute_query("sales")
        executor.execute_query("users")
        executor.execute_query("orders")

        submitted_names: list[set] = []

        def capturing_parallel(_ctx, _ledger, found, max_workers):
            submitted_names.append({miss.name for miss in found.misses})
            return {}

        with (
            patch("dbt_charts.core.render.renderer.run_misses", capturing_parallel),
            patch.object(
                yaml_format_mod, "render_board_yaml", return_value="yaml: true\n"
            ),
        ):
            renderer_mod.render(
                result.board, executor.ctx, executor.ledger, format="yaml"
            )

        assert len(submitted_names) == 1
        assert submitted_names[0] == set(), (
            f"Expected empty parallel submit when all cached; got {submitted_names[0]}"
        )


class TestLookupHits:
    """Direct contract for Executor.lookup — the success-cache peek used by
    the pre-render partition. True only for a warmed success cache."""

    def _executor(self) -> Run:
        result = compile(SIMPLE_BOARD_YAML)
        assert result.success, result.errors
        mock_registry_backend = ScriptedBackend(
            QueryResult(
                data=[{"x": 1, "y": 2}], schema=literal_schema([{"x": 1, "y": 2}])
            )
        )
        mock_registry = scripted_env(mock_registry_backend)
        return Run(result.board, env=mock_registry)

    def test_false_before_execution(self):
        executor = self._executor()
        assert "sales" not in executor.lookup(["sales"]).hits

    def test_true_after_execution(self):
        executor = self._executor()
        executor.execute_query("sales")
        assert "sales" in executor.lookup(["sales"]).hits

    def test_strips_queries_prefix(self):
        executor = self._executor()
        executor.execute_query("sales")
        assert "sales" in executor.lookup(["queries.sales"]).hits

    def test_unknown_query_is_not_cached(self):
        """An unknown name returns False, not a raise — the partition treats it
        as a miss and execute_query surfaces the error on the real call."""
        executor = self._executor()
        assert "does_not_exist" not in executor.lookup(["does_not_exist"]).hits

    def test_memo_hit_reports_true_even_with_use_cache_off(self):
        """`use_cache=False` disables the persistent store, not the memo —
        lookup must keep predicting execute_query, which serves memo rows."""
        result = compile(SIMPLE_BOARD_YAML)
        assert result.success, result.errors
        mock_registry_backend = ScriptedBackend(
            QueryResult(
                data=[{"x": 1, "y": 2}], schema=literal_schema([{"x": 1, "y": 2}])
            )
        )
        mock_registry = scripted_env(mock_registry_backend)
        executor = Run(result.board, env=mock_registry, use_cache=False)
        assert "sales" not in executor.lookup(["sales"]).hits
        executor.execute_query("sales")
        assert "sales" in executor.lookup(["sales"]).hits


def test_run_propagates_attribution_to_every_worker() -> None:
    """A fan-out render must label EVERY query with the calling actor.

    `attribute()`'s ContextVar does not cross `ThreadPoolExecutor.submit` on
    its own — a regression test through the real `run` + a real thread pool
    with `max_workers > 1`. A dropped propagation would silently omit the actor
    label on every query that fans out.
    """
    from dbt_charts.core.attribution import attribute, current_attribution
    from dbt_charts.core.execute.run import Outcome, Rows, run

    seen: dict[str, str | None] = {}

    def execute(miss: Miss) -> Outcome:
        seen[miss.name] = current_attribution().get("dbt_charts_actor")
        return Rows(rows_result([]), None)

    with attribute({"actor": "tenant-a"}, {}):
        run([_miss("a"), _miss("b"), _miss("c")], execute, lambda m, o: None, 3)

    assert seen == {"a": "tenant-a", "b": "tenant-a", "c": "tenant-a"}


def _miss(name: str) -> Miss:
    return Miss(
        planned_query(name, sql_entry("SELECT 1", source="db"), ("s", name, "v"))
    )
