"""Regression: an affixed support_table percent entry must still fail fast on
0-100-shaped data. the engine's affixed support-table numerals formats through ``format_d3``, which composes the affix
around the SIGNED value but does not run ``_check_percent_range``; it must check the
range itself, or a forgotten ``÷100`` in SQL feeding a percent spec with an affix
paints a 100x-wrong number instead of raising.
"""

from __future__ import annotations

from pathlib import Path

from dbt_charts.cli.filesystem_project import FilesystemProject
from dbt_charts.core.compile import compile as compile_board
from dbt_charts.core.diagnostics.codes_render import ERR_PERCENT_RANGE
from dbt_charts.core.execute.env import build_exec_env
from dbt_charts.core.render import render

from ...._run import Run

_BOARD = """
title: T
queries:
  q:
    type: values
    rows:
      - {channel: A, share: 55}
      - {channel: B, share: 60}
charts:
  revenue:
    type: bar
    query: q
    x: channel
    y: share
    support_table:
      entries:
        - source: share
          label: Share
          format:
            spec: ".1%"
            suffix: " YoY"
rows:
  - revenue
"""


def _render_errors(board: str) -> list[str]:
    compiled = compile_board(board)
    assert compiled.success, compiled.errors
    assert compiled.board is not None
    executor = Run(
        compiled.board,
        env=build_exec_env(FilesystemProject(Path.cwd())),
    )
    result = render(
        compiled.board, executor.ctx, executor.ledger, format="svg", controls=False
    )
    return [e.code for e in result.chart_errors]


def test_affixed_percent_support_table_entry_rejects_0_100_shaped_value() -> None:
    assert _render_errors(_BOARD) == [ERR_PERCENT_RANGE.code]


def test_large_negative_ratio_renders_in_table_and_kpi() -> None:
    board = """
title: T
queries:
  q:
    type: values
    rows:
      - {k: A, margin: -20.94}
      - {k: B, margin: 0.18}
  one:
    type: values
    rows:
      - {margin: -20.94}
charts:
  t:
    type: table
    query: q
    style:
      columns:
        margin:
          format: percent
  kpi:
    type: kpi
    query: one
    value: margin
    style:
      value:
        format: percent
rows:
  - t
  - kpi
"""
    assert _render_errors(board) == []
