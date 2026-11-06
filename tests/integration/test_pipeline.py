"""Integration tests for full compile → execute → render pipeline.

These tests verify the complete pipeline works end-to-end.
"""

from collections.abc import Callable
from pathlib import Path

from dbt_charts.cli.filesystem_project import FilesystemProject
from dbt_charts.core.compile import compile
from dbt_charts.core.execute.env import build_exec_env
from dbt_charts.core.render import render

from .._run import Run


class TestFullPipeline:
    """Tests for complete pipeline execution."""

    def test_csv_dashboard_pipeline(
        self, local_project: Callable[..., FilesystemProject]
    ):
        """Test full pipeline with inline values query."""
        yaml_content = """
title: Sales Dashboard
queries:
  sales:
    type: values
    rows:
      - {month: Jan, revenue: 100}
      - {month: Feb, revenue: 150}
charts:
  revenue_chart:
    query: sales
    type: bar
    x: month
    y: revenue
rows:
  - revenue_chart
"""
        # Compile
        result = compile(yaml_content)
        assert result.success
        assert result.board is not None

        # Execute
        executor = Run(
            result.board,
            env=build_exec_env(local_project(Path.cwd())),
            query_registry=result.query_registry,
        )

        # Render
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
        # HTML format now wraps SVG output
        assert "<svg" in html_output

    def test_pipeline_with_variables(
        self, local_project: Callable[..., FilesystemProject]
    ):
        """Test pipeline with variable substitution."""
        yaml_content = """
title: Filtered Dashboard
variables:
  year:
    input: number
    default: 2023
queries:
  sales:
    type: values
    rows:
      - {month: Jan, revenue: 100}
      - {month: Feb, revenue: 150}
charts:
  chart1:
    query: sales
    type: bar
    x: month
    y: revenue
    title: Sales for {{ year }}
rows:
  - chart1
"""
        result = compile(yaml_content)
        assert result.success
        assert result.board is not None

        executor = Run(
            result.board,
            env=build_exec_env(local_project(Path.cwd())),
            query_registry=result.query_registry,
        )

        # Render with default variables
        svg_output = render(
            result.board, executor.ctx, executor.ledger, format="svg"
        ).output
        assert isinstance(svg_output, str)
        assert "2023" in svg_output

        # Render with custom variables
        svg_output_custom = render(
            result.board,
            executor.ctx,
            executor.ledger,
            format="svg",
            variables={"year": 2024},
        ).output
        assert isinstance(svg_output_custom, str)
        assert "2024" in svg_output_custom

    def test_pipeline_with_nested_boards(
        self, local_project: Callable[..., FilesystemProject]
    ):
        """Test pipeline with nested boards."""
        yaml_content = """
title: Main Dashboard
queries:
  sales:
    type: values
    rows:
      - {month: Jan, revenue: 100}
      - {month: Feb, revenue: 150}
charts:
  chart1:
    query: sales
    type: bar
    x: month
    y: revenue
rows:
  - title: Nested Board
    rows:
      - chart1
"""
        result = compile(yaml_content)
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
        assert "Nested Board" in svg_output
