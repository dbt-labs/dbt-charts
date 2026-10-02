"""Regression: an affixed support_table percent entry must still fail fast on
0-100-shaped data. ``StripNumerals._compose_affixed_text``
(support_table_attachment.py) formats through ``format_d3``, which composes the affix
around the SIGNED value but does not run ``_check_percent_range``; it must check the
range itself, or a forgotten ``÷100`` in SQL feeding a percent spec with an affix
paints a 100x-wrong number instead of raising.
"""

from __future__ import annotations

from pathlib import Path

from dbt_charts.cli.filesystem_project import FilesystemProject
from dbt_charts.core.compile import compile as compile_board
from dbt_charts.core.diagnostics.codes_render import ERR_PERCENT_RANGE
from dbt_charts.core.execute import Executor
from dbt_charts.core.execute.adapters import build_adapter_registry
from dbt_charts.core.render import render

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


def test_affixed_percent_support_table_entry_rejects_0_100_shaped_value() -> None:
    compiled = compile_board(_BOARD)
    assert compiled.success, compiled.errors
    assert compiled.board is not None
    executor = Executor(
        compiled.board,
        adapter_registry=build_adapter_registry(FilesystemProject(Path.cwd())),
        query_registry=compiled.query_registry,
    )
    result = render(compiled.board, executor, format="svg", controls=False)
    assert result.output is None
    assert result.board_error is not None
    assert result.board_error.code == ERR_PERCENT_RANGE.code
