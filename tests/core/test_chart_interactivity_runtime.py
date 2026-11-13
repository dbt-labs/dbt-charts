"""Tests for where the core-owned chart hover runtime ships."""

from collections.abc import Callable
from pathlib import Path

from dbt_charts.cli.filesystem_project import FilesystemProject
from dbt_charts.core.compile import compile
from dbt_charts.core.execute.env import build_exec_env
from dbt_charts.core.render import render
from dbt_charts.core.render.chart_interactivity import hover_runtime_source

from .._run import Run

_TEST_YAML = """
title: Test Dashboard
queries:
  q1:
    type: values
    rows:
      - {month: Jan, revenue: 100}
      - {month: Feb, revenue: 150}
charts:
  c1:
    query: q1
    type: bar
    x: month
    y: revenue
rows:
  - c1
"""


def _compile_board(local_project: Callable[..., FilesystemProject]):
    result = compile(_TEST_YAML)
    assert result.success
    executor = Run(
        result.board,
        env=build_exec_env(local_project(Path.cwd())),
    )
    return result.board, executor


def test_svg_output_carries_hover_facts_not_the_runtime(
    local_project: Callable[..., FilesystemProject],
):
    board, executor = _compile_board(local_project)

    svg_output = render(board, executor.ctx, executor.ledger, format="svg").output

    assert "<script" not in svg_output
    assert 'data-dbt-tooltip-style="' in svg_output
    assert "window.__dbtChartsChartHoverState" in hover_runtime_source()


def test_html_output_ships_the_hover_runtime(
    local_project: Callable[..., FilesystemProject],
):
    board, executor = _compile_board(local_project)

    html_output = render(board, executor.ctx, executor.ledger, format="html").output

    assert "<!DOCTYPE html>" in html_output
    assert "<svg" in html_output
    assert "window.__dbtChartsChartHoverState" in html_output
