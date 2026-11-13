"""Regression tests for Executor cache wiring.

Covers:
- Executor with DuckDB failure cache: adapter not called on cache hit

DCT_CACHE_PATH env-var wiring now lives entirely at the CLI/Typer boundary
(--cache PATH, envvar=DCT_CACHE_PATH); core no longer reads the environment
directly, so there is nothing to test at this layer.

The render_dashboard entry point's own cache wiring (forwarded to Executor
unchanged, never closed by the callee) is covered in
dbt-charts/tests/agent_api/test_dashboards.py::TestRenderDashboardDiagnostics
(test_render_dashboard_threads_result_cache_to_executor,
test_render_dashboard_does_not_close_result_cache) — this module no longer
duplicates that coverage now that compile_and_render is deleted.
"""

from pathlib import Path

import pytest

from dbt_charts.core.compile import compile as df_compile
from dbt_charts.core.execute import QueryError
from dbt_charts.core.execute.cache_backend import CachedQueryFailure
from dbt_charts.core.execute.plan import plan
from dbt_charts.core.execute.trivial_local_cache import TrivialDuckDBCache

from .._run import Run
from .._scripted_env import ScriptedBackend, scripted_env

BOARD_YAML = """\
title: Test
source: memory
queries:
  my_query:
    sql: SELECT 1 as value
charts:
  c:
    query: my_query
    type: kpi
    value: value
rows:
  - c
"""


class TestExecutorFailureCacheBlocksAdapter:
    """Verify that a pre-seeded DuckDB failure cache prevents adapter execution."""

    def test_failure_cache_hit_adapter_not_called(self, tmp_path: Path) -> None:
        """A cached failure for the query means adapter.execute is never called."""
        db = tmp_path / "cache.duckdb"

        # Seed under the planned query's cache key — the single source of truth — so
        # the seeded key always matches what execute_query will look up.
        backend = ScriptedBackend([{"value": 42}])
        env = scripted_env(backend)
        [planned] = plan(Run(df_compile(BOARD_YAML).board, env=env).ctx, ["my_query"])
        source_hash, query_hash, variables_hash = planned.cache_key
        seed = TrivialDuckDBCache(db_path=db, failure_ttl_seconds=9000)
        seed.put(
            source_hash,
            query_hash,
            variables_hash,
            RuntimeError("pre-seeded failure"),
            board_slug="test",
            query_name="my_query",
        )
        seed.close()

        cache = TrivialDuckDBCache(db_path=db, failure_ttl_seconds=9000)
        result = df_compile(BOARD_YAML)
        try:
            executor = Run(
                result.board,
                env=env,
                result_cache=cache,
            )
            with pytest.raises((CachedQueryFailure, QueryError)):
                executor.execute_query("my_query")

            assert backend.execute.call_count == 0
        finally:
            cache.close()
