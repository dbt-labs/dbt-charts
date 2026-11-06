"""Integration tests for SQL dashboards with DuckDB.

Tests SQL query execution via DuckDB adapter.
"""

from collections.abc import Callable
from pathlib import Path

from dbt_charts.cli.filesystem_project import FilesystemProject
from dbt_charts.core.compile import compile
from dbt_charts.core.compile.config import ProjectSourcesConfig
from dbt_charts.core.compile.models.source import DuckDBSourceConfig
from dbt_charts.core.execute.env import build_exec_env
from dbt_charts.core.render import render

from .._backend_runner import DuckDBRunner
from .._run import Run

_MEMORY_DUCKDB_SOURCES = ProjectSourcesConfig(
    sources={"test_profile": {"type": "duckdb", "path": ":memory:"}}
)


class TestSqlDashboard:
    """Tests for SQL dashboards."""

    def test_sql_query_execution(
        self, tmp_path, local_project: Callable[..., FilesystemProject]
    ):
        """Test SQL query execution with DuckDB."""
        yaml_content = """
title: SQL Dashboard
queries:
  test_query:
    type: sql
    sql: SELECT 1 as value, 'test' as name UNION ALL SELECT 2, 'test2'
    source: test_profile
charts:
  chart1:
    query: test_query
    type: table
rows:
  - chart1
"""
        result = compile(yaml_content, project_sources=_MEMORY_DUCKDB_SOURCES)
        assert result.success
        assert result.board is not None

        # Create executor with SQL adapter
        executor = Run(
            result.board,
            query_registry=result.query_registry,
            env=build_exec_env(local_project(tmp_path)),
        )

        # Execute query directly
        data = executor.execute_query("test_query", use_cache=False)
        assert data is not None
        assert len(data) == 2
        assert data[0]["value"] == 1
        assert data[0]["name"] == "test"

    def test_sql_with_jinja_variables(
        self, local_project: Callable[..., FilesystemProject]
    ):
        """Test SQL query with Jinja variable substitution."""
        yaml_content = """
title: SQL with Variables
variables:
  limit_value:
    input: number
    default: 5
queries:
  test_query:
    type: sql
    sql: SELECT * FROM (SELECT 1 as id UNION ALL SELECT 2 UNION ALL SELECT 3 UNION ALL SELECT 4 UNION ALL SELECT 5) LIMIT {{ limit_value }}
    source: test_profile
charts:
  chart1:
    query: test_query
    type: table
rows:
  - chart1
"""
        result = compile(yaml_content, project_sources=_MEMORY_DUCKDB_SOURCES)
        assert result.success
        assert result.board is not None

        executor = Run(
            result.board,
            env=build_exec_env(local_project(Path.cwd())),
            query_registry=result.query_registry,
        )

        # Execute with default variable
        data = executor.execute_query("test_query", use_cache=False)
        assert len(data) == 5

        # Execute with custom variable
        data_custom = executor.execute_query(
            "test_query", use_cache=False, variables={"limit_value": 2}
        )
        assert len(data_custom) == 2

    def test_sql_dashboard_renders(
        self, local_project: Callable[..., FilesystemProject]
    ):
        """Test SQL dashboard renders correctly."""
        yaml_content = """
title: SQL Dashboard
queries:
  sales_data:
    type: sql
    sql: |
      SELECT
        'Jan' as month,
        1000 as revenue
      UNION ALL
      SELECT 'Feb', 1200
      UNION ALL
      SELECT 'Mar', 1500
    source: test_profile
charts:
  revenue_chart:
    query: sales_data
    type: bar
    x: month
    y: revenue
rows:
  - revenue_chart
"""
        result = compile(yaml_content, project_sources=_MEMORY_DUCKDB_SOURCES)
        assert result.success
        assert result.board is not None

        executor = Run(
            result.board,
            env=build_exec_env(local_project(Path.cwd())),
            query_registry=result.query_registry,
        )

        svg_output = render(
            result.board, executor.ctx, executor.ledger, format="svg"
        ).output
        assert isinstance(svg_output, str)
        assert "<svg" in svg_output

        html_output = render(
            result.board, executor.ctx, executor.ledger, format="html"
        ).output
        assert isinstance(html_output, str)
        assert "<!DOCTYPE html>" in html_output

    def test_sql_adapter_connection_string(self):
        """Test DuckDB adapter with custom connection string."""
        adapter = DuckDBRunner(
            source_config=DuckDBSourceConfig(type="duckdb"),
        )

        compile_result = compile(
            """
queries:
  test:
    type: sql
    sql: SELECT 1 as value
    source: test_profile
charts:
  chart1:
    query: test
    type: table
rows:
  - chart1
"""
        )
        assert compile_result.success, f"Compile failed: {compile_result.errors}"
        assert compile_result.board is not None
        query = compile_result.board.queries["test"]

        result = adapter.execute(query)
        assert len(result.data) == 1
        assert result.data[0]["value"] == 1

    def test_sql_query_with_limit(
        self, local_project: Callable[..., FilesystemProject]
    ):
        """Test SQL query with a LIMIT in the SQL (limit: is not a sql-query field)."""
        yaml_content = """
title: SQL with Limit
queries:
  test_query:
    type: sql
    sql: SELECT * FROM (SELECT 1 as id UNION ALL SELECT 2 UNION ALL SELECT 3 UNION ALL SELECT 4 UNION ALL SELECT 5) LIMIT 3
    source: test_profile
charts:
  chart1:
    query: test_query
    type: table
rows:
  - chart1
"""
        result = compile(yaml_content, project_sources=_MEMORY_DUCKDB_SOURCES)
        assert result.success
        assert result.board is not None

        executor = Run(
            result.board,
            env=build_exec_env(local_project(Path.cwd())),
            query_registry=result.query_registry,
        )

        data = executor.execute_query("test_query", use_cache=False)
        assert len(data) == 3
