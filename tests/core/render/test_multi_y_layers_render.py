"""Layers over a `y: [a, b]` base: each layer row draws once, labels key the legend."""

from __future__ import annotations

import re
from collections.abc import Callable
from pathlib import Path

from dbt_charts.core.compile import compile
from dbt_charts.core.execute.env import build_exec_env
from dbt_charts.core.project import Project
from dbt_charts.core.render import render

from ..._run import Run

_BOARD = """
title: t
queries:
  q:
    type: values
    columns: [month, revenue, cost, churn]
    values:
      - [Jan, 10, 4, 3]
      - [Feb, 12, 5, 2]
charts:
  c:
    query: q
    type: {base}
    x: month
    y: [revenue, cost]
    layers:
      - type: {layer}
        y: {layer_y}
rows:
  - c
"""


def _svg(tmp_path: Path, local_project: Callable[..., Project], board: str) -> str:
    result = compile(board)
    assert result.board is not None, result.errors
    run = Run(result.board, env=build_exec_env(local_project(tmp_path)))
    output = render(result.board, run.ctx, run.ledger, format="svg").output
    assert isinstance(output, str)
    return output


def test_a_bar_layer_on_the_base_query_draws_each_row_once(
    tmp_path: Path, local_project: Callable[..., Project]
) -> None:
    svg = _svg(
        tmp_path,
        local_project,
        _BOARD.format(base="line", layer="bar", layer_y="churn"),
    )
    assert re.findall(r"Churn: (\d)", svg) == ["3", "2"]


def test_a_scatter_layer_on_the_base_query_draws_each_row_once(
    tmp_path: Path, local_project: Callable[..., Project]
) -> None:
    svg = _svg(
        tmp_path,
        local_project,
        _BOARD.format(base="bar", layer="scatter", layer_y="churn"),
    )
    assert re.findall(r"Churn: (\d)", svg) == ["3", "2"]


def test_a_layer_labeled_like_a_folded_series_is_refused(
    tmp_path: Path, local_project: Callable[..., Project]
) -> None:
    svg = _svg(
        tmp_path,
        local_project,
        _BOARD.format(base="bar", layer="line", layer_y="revenue"),
    )
    assert "ERR-LAYER-LABEL-COLLISION" in svg


def test_a_base_query_layer_over_bar_multiples_is_refused(
    tmp_path: Path, local_project: Callable[..., Project]
) -> None:
    board = _BOARD.format(base="bar", layer="scatter", layer_y="churn").replace(
        "    layers:", "    multiples:\n      columns: month\n    layers:"
    )
    svg = _svg(tmp_path, local_project, board)
    assert "multiples over a y list with a layer on the base query" in svg
