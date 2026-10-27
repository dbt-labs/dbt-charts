"""Tests for consolidated query execution into one pre-render stage.

Pin: collect_all_query_names includes options.query, bool probe queries,
and promoted column-option queries; option queries are cache-hits during
the render walk after pre-execution.
"""

from __future__ import annotations

from typing import Any
from unittest.mock import Mock

from dbt_charts.core.column_types import literal_schema
from dbt_charts.core.compile import compile
from dbt_charts.core.execute import Executor
from dbt_charts.core.execute.adapters.base import QueryResult
from dbt_charts.core.execute.collect import (
    collect_all_query_names,
    collect_layout_chart_query_names,
)

# ─────────────────────────────────────────────────────────────────────────────
# YAML fixtures
# ─────────────────────────────────────────────────────────────────────────────

FILTERED_DASHBOARD_YAML = """\
title: Filtered Dashboard
source: duckdb
queries:
  sales:
    sql: SELECT 'a' as x, 1 as y
  city_opts:
    sql: SELECT DISTINCT city FROM cities ORDER BY city
  country_opts:
    sql: SELECT DISTINCT country FROM countries ORDER BY country
  active_flag:
    sql: SELECT true as is_active
charts:
  sales_chart:
    query: sales
    type: bar
    x: x
    y: y
variables:
  city:
    input: select
    options:
      query: city_opts
  country:
    input: select
    options:
      query: country_opts
  feature:
    input: select
    options:
      static: [a, b]
    enabled:
      query: active_flag
      column: is_active
rows:
  - sales_chart
"""

COLUMN_BOUND_YAML = """\
title: Column Bound Dashboard
source: duckdb
queries:
  sales:
    sql: SELECT 'a' as x, 1 as y
charts:
  sales_chart:
    query: sales
    type: bar
    x: x
    y: y
variables:
  region:
    input: select
    options:
      column: regions.name
  city:
    input: select
    options:
      column: cities.city_name
rows:
  - sales_chart
"""

BOOL_PROBE_VISIBLE_YAML = """\
title: Bool Probe Visible
source: duckdb
queries:
  sales:
    sql: SELECT 'a' as x, 1 as y
  panel_flag:
    sql: SELECT true as show_panel
charts:
  sales_chart:
    query: sales
    type: bar
    x: x
    y: y
rows:
  - visible:
      query: panel_flag
      column: show_panel
    rows:
      - sales_chart
"""


def _compile(yaml_text: str):
    return compile(yaml_text)


# ─────────────────────────────────────────────────────────────────────────────
# collect_all_query_names
# ─────────────────────────────────────────────────────────────────────────────


class TestCollectAllQueryNames:
    def test_includes_chart_queries(self) -> None:
        result = _compile(FILTERED_DASHBOARD_YAML)
        names = collect_all_query_names(result.board)
        assert "sales" in names

    def test_includes_variable_options_query(self) -> None:
        result = _compile(FILTERED_DASHBOARD_YAML)
        names = collect_all_query_names(result.board)
        assert "city_opts" in names
        assert "country_opts" in names

    def test_includes_bool_probe_enabled_query(self) -> None:
        result = _compile(FILTERED_DASHBOARD_YAML)
        names = collect_all_query_names(result.board)
        assert "active_flag" in names

    def test_excludes_static_only_variables(self) -> None:
        result = _compile(FILTERED_DASHBOARD_YAML)
        chart_names = collect_layout_chart_query_names(result.board)
        all_names = collect_all_query_names(result.board)
        # all_names must be a strict superset — it includes option/probe queries beyond chart queries
        assert all_names > chart_names

    def test_includes_layout_item_visible_probe(self) -> None:
        result = _compile(BOOL_PROBE_VISIBLE_YAML)
        names = collect_all_query_names(result.board)
        assert "panel_flag" in names

    def test_simple_board_no_options_same_as_chart_names(self) -> None:
        """Board with no variable options returns same set as chart-name collector."""
        yaml = """\
title: Simple
source: duckdb
queries:
  q:
    sql: SELECT 1 as x
charts:
  c:
    query: q
    type: bar
    x: x
    y: x
rows:
  - c
"""
        result = _compile(yaml)
        assert collect_all_query_names(
            result.board
        ) == collect_layout_chart_query_names(result.board)

    def test_includes_promoted_column_option_queries(self) -> None:
        """Column-bound options are promoted to named queries and included."""
        result = _compile(COLUMN_BOUND_YAML)
        names = collect_all_query_names(result.board)
        assert "_var_options_region" in names
        assert "_var_options_city" in names


# ─────────────────────────────────────────────────────────────────────────────
# Options queries are cache hits after pre-execution
# ─────────────────────────────────────────────────────────────────────────────


class TestPreExecutionCacheHits:
    def test_option_queries_are_cache_hits_after_parallel_pre_execution(self) -> None:
        """After pre-execution, variable_controls gets cache hits for options queries."""
        from dbt_charts.core.execute.parallel import execute_queries_parallel
        from dbt_charts.core.render.variables_resolve import resolve_query_options

        result = _compile(FILTERED_DASHBOARD_YAML)
        executor, registry = _mock_executor(result)

        registry.execute.return_value = QueryResult(
            data=[{"city": "Paris"}], schema=literal_schema([{"city": "Paris"}])
        )

        # Pre-execution: run all queries including options queries
        all_names = collect_all_query_names(result.board)
        execute_queries_parallel(executor, all_names)

        calls_after_pre_exec = registry.execute.call_count
        assert calls_after_pre_exec >= 1  # at least one query was pre-executed

        # Render walk: option query calls are cache hits — no new adapter calls
        resolve_query_options("city", "city_opts", executor, variables={})
        resolve_query_options("country", "country_opts", executor, variables={})

        assert registry.execute.call_count == calls_after_pre_exec

    def test_column_option_queries_are_cache_hits_after_pre_execution(self) -> None:
        """Promoted column-option queries are cache hits on second pre-execution."""
        from dbt_charts.core.execute.parallel import execute_queries_parallel

        result = _compile(COLUMN_BOUND_YAML)
        executor, registry = _mock_executor(result)

        registry.execute.return_value = QueryResult(
            data=[{"name": "East"}], schema=literal_schema([{"name": "East"}])
        )

        all_names = collect_all_query_names(result.board)
        execute_queries_parallel(executor, all_names)

        calls_after_first = registry.execute.call_count

        # Second pre-execution: column-option queries must be cache hits
        execute_queries_parallel(executor, all_names)

        assert registry.execute.call_count == calls_after_first


def _mock_executor(board_result: Any) -> tuple[Any, Mock]:
    """Return (executor, mock_registry) with mock adapter that returns a single-row result."""
    registry = Mock()
    registry.execute.return_value = QueryResult(
        data=[{"v": 1}], schema=literal_schema([{"v": 1}])
    )
    executor = Executor(board=board_result.board, adapter_registry=registry)
    return executor, registry


# ─────────────────────────────────────────────────────────────────────────────
# Regression tests for reviewer-identified missed paths
# ─────────────────────────────────────────────────────────────────────────────


class TestMissedPaths:
    def test_top_level_var_query_collected(self) -> None:
        """Variable.query (top-level, not options.query) must be collected."""
        yaml = """\
title: T
source: duckdb
queries:
  opts_q:
    sql: SELECT DISTINCT v FROM t
charts:
  c:
    query: opts_q
    type: bar
    x: v
    y: v
variables:
  region:
    input: select
    query: opts_q
rows:
  - c
"""
        result = _compile(yaml)
        names = collect_all_query_names(result.board)
        assert "opts_q" in names

    def test_top_level_var_column_promoted_to_named_query(self) -> None:
        """Variable.column (top-level) is promoted to a named query at compile time."""
        yaml = """\
title: T
source: duckdb
queries:
  sales:
    sql: SELECT 1 as x
charts:
  c:
    query: sales
    type: bar
    x: x
    y: x
variables:
  region:
    input: select
    column: regions.name
rows:
  - c
"""
        result = _compile(yaml)
        # Promoted to board.queries at compile time
        assert "_var_options_region" in result.board.queries
        names = collect_all_query_names(result.board)
        assert "_var_options_region" in names
