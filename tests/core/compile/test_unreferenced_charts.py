"""Regression: implicit chart layout and orphan-chart cases.

When no explicit layout is present, charts render as implicit rows. Compile
still warns on charts defined but never referenced from an explicit layout.
"""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path

import pytest

from dbt_charts.cli.filesystem_project import FilesystemProject
from dbt_charts.core.compile import compile
from dbt_charts.core.execute.env import build_exec_env
from dbt_charts.core.render.errors import RenderError


def test_charts_without_layout_compile_as_implicit_rows():
    yaml_content = """
source: examples_db
queries:
  q: SELECT 1 AS x, 2 AS y
charts:
  c1:
    query: q
    type: bar
    x: x
    y: y
"""
    result = compile(yaml_content)
    assert result.success
    assert result.warnings == []
    assert result.board.layout.type == "rows"
    assert [item.chart.id for item in result.board.layout.items if item.chart] == ["c1"]


def test_text_and_charts_without_layout_render(
    local_project: Callable[..., FilesystemProject],
):
    yaml_content = """
title: Has Implicit Chart Layout
text: |
  some prose
source: examples_db
queries:
  q: SELECT 1 AS x, 2 AS y
charts:
  c1:
    query: q
    type: bar
    x: x
    y: y
"""
    from dbt_charts.core.render.renderer import render

    from ..._run import Run

    result = compile(yaml_content)
    assert result.success
    executor = Run(
        result.board,
        env=build_exec_env(local_project(Path.cwd())),
    )
    rendered = render(result.board, executor.ctx, executor.ledger)
    assert rendered.output
    assert rendered.board_error is None


def test_multiple_charts_without_layout_keep_authored_order():
    yaml_content = """
source: examples_db
queries:
  q: SELECT 1 AS x, 2 AS y
charts:
  first:
    query: q
    type: bar
    x: x
    y: y
  second:
    query: q
    type: line
    x: x
    y: y
"""
    result = compile(yaml_content)
    assert result.success
    assert result.warnings == []
    assert [item.chart.id for item in result.board.layout.items if item.chart] == [
        "first",
        "second",
    ]


def test_explicit_empty_layout_with_charts_still_render_errors(
    local_project: Callable[..., FilesystemProject],
):
    yaml_content = """
title: Explicit Empty Layout
source: examples_db
queries:
  q: SELECT 1 AS x, 2 AS y
charts:
  c1:
    query: q
    type: bar
    x: x
    y: y
rows: []
"""
    from dbt_charts.core.render.renderer import render

    from ..._run import Run

    result = compile(yaml_content)
    assert result.success
    assert [w.chart for w in result.warnings] == ["c1"]
    executor = Run(
        result.board,
        env=build_exec_env(local_project(Path.cwd())),
    )
    with pytest.raises(RenderError, match="defines charts.*no layout"):
        render(result.board, executor.ctx, executor.ledger)


def test_orphan_chart_in_layout_warns():
    # Two charts defined; only one referenced. Other is an orphan.
    yaml_content = """
source: examples_db
queries:
  q: SELECT 1 AS x, 2 AS y
charts:
  used:
    query: q
    type: bar
    x: x
    y: y
  unused:
    query: q
    type: bar
    x: x
    y: y
rows:
  - used
"""
    result = compile(yaml_content)
    assert result.success
    orphan_warnings = [
        w for w in result.warnings if w.code == "WARN-UNREFERENCED-CHART"
    ]
    assert len(orphan_warnings) == 1
    assert orphan_warnings[0].chart == "unused"


def test_chart_defined_on_parent_referenced_from_nested_board_is_not_orphan():
    # Parent defines `c1`; nested board's layout references it. The orphan
    # walker resolves chart references globally — c1 should not be flagged.
    yaml_content = """
source: examples_db
queries:
  q: SELECT 1 AS x, 2 AS y
charts:
  c1:
    query: q
    type: bar
    x: x
    y: y
rows:
  - rows:
      - c1
"""
    result = compile(yaml_content)
    assert result.success
    assert result.warnings == [], result.warnings


def test_content_only_board_still_allowed():
    yaml_content = """
title: Pure content
text: |
  Just some markdown.
"""
    result = compile(yaml_content)
    assert result.board.layout.items == []
    assert result.warnings == []


def test_charts_referenced_from_rows_is_fine():
    yaml_content = """
source: examples_db
queries:
  q: SELECT 1 AS x, 2 AS y
charts:
  c1:
    query: q
    type: bar
    x: x
    y: y
rows:
  - c1
"""
    result = compile(yaml_content)
    assert result.errors == []
    assert result.warnings == []
    assert len(result.board.layout.items) == 1
