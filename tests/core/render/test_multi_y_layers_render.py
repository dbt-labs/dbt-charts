"""Layers over a `y: [a, b]` base: each layer row draws once, labels key the legend."""

from __future__ import annotations

import re
from collections.abc import Callable
from pathlib import Path

import pytest

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


_BANDS = """
title: t
queries:
  curve:
    type: values
    columns: [amount, total, part_a]
    values:
      - [{x0}, 0, 0]
      - [{x1}, 150, 90]
      - [{x2}, 220, 120]
      - [{x3}, 260, 140]
  bands:
    type: values
    columns: [band, amount, band_top]
    values:
{bands}
charts:
  c:
    query: curve
    type: {base}
    x: amount
    y: [total, part_a]
    layers:
      - type: area
        query: bands
        y: band_top
        color: band
rows:
  - c
"""


def _bands(base: str, xs: list[str], *spans: tuple[str, int, int]) -> str:
    rows = "".join(
        f"      - [{band}, {xs[i]}, 280]\n" for band, lo, hi in spans for i in (lo, hi)
    )
    return _BANDS.format(
        base=base,
        bands=rows.rstrip("\n"),
        **{f"x{i}": x for i, x in enumerate(xs)},
    )


def _gridline_ys(svg: str) -> list[str]:
    return [
        y1
        for y1, y2 in re.findall(r'<line [^>]*y1="([^"]*)"[^>]*y2="([^"]*)"', svg)
        if y1 == y2
    ]


@pytest.mark.parametrize(
    ("base", "xs"),
    [("line", ["0", "100", "200", "300"]), ("bar", ["a", "b", "c", "d"])],
)
def test_area_layer_bands_sharing_an_edge_fit_the_plot_like_one_band(
    tmp_path: Path, local_project: Callable[..., Project], base: str, xs: list[str]
) -> None:
    """Area-layer rows sharing an x fill from zero, so they claim no extra height."""
    shared = _svg(
        tmp_path,
        local_project,
        _bands(base, xs, ("Low", 0, 1), ("Mid", 1, 2), ("High", 2, 3)),
    )
    alone = _svg(tmp_path, local_project, _bands(base, xs, ("High", 2, 3)))
    assert _gridline_ys(alone)
    assert _gridline_ys(shared) == _gridline_ys(alone)
