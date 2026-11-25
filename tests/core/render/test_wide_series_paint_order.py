"""A `y: [a, b, c]` chart paints its series in `y:` order, whatever their names.

A bar ranks its series by `series_order`, so it asks for `data` to follow `y:`.

Asserted on the painted ink per series in the real render, not on an
intermediate domain list.
"""

from __future__ import annotations

import re
from collections.abc import Callable
from pathlib import Path

import pytest

from dbt_charts.cli.filesystem_project import FilesystemProject
from dbt_charts.core.compile import compile
from dbt_charts.core.execute.env import build_exec_env
from dbt_charts.core.render.renderer import render

from ..._run import Run

_BOARD = """
queries:
  wide:
    type: values
    rows:
      - {{month: "2026-01-01", gamma: 50, alpha: 5, beta: 30}}
      - {{month: "2026-02-01", gamma: 52, alpha: 6, beta: 31}}
charts:
  c:
    type: {family}
    query: wide
    x: month
    y: [{order}]{style}
rows:
  - c
"""
# Direct labels name their series, beside the plot or in it; otherwise a legend
# swatch precedes its label.
_DIRECT_LABEL = re.compile(r'<text [^>]*data-dbt-series="\w+"[^>]*>')
_SERIES = re.compile(r'data-dbt-series="(\w+)"')
_FILL = re.compile(r' fill="(#[0-9a-f]{6})"')
_LEGEND_SWATCH = re.compile(
    r'role-legend-symbol"[^>]*>.*?fill="(#[0-9a-f]{6})".*?</g>'
    r'<g class="mark-text role-legend-label"[^>]*><text[^>]*>(\w+)</text>'
)


@pytest.fixture
def series_ink(
    local_project: Callable[..., FilesystemProject],
) -> Callable[[str, str], dict[str, str]]:
    def _ink(family: str, order: str) -> dict[str, str]:
        style = "\n    style:\n      series_order: data" if family == "bar" else ""
        result = compile(_BOARD.format(family=family, order=order, style=style))
        assert result.success and result.board is not None, result.errors
        executor = Run(result.board, env=build_exec_env(local_project(Path.cwd())))
        svg = render(result.board, executor.ctx, executor.ledger, format="svg").output
        assert isinstance(svg, str)
        painted = {
            _SERIES.findall(tag)[0].lower(): _FILL.findall(tag)[0]
            for tag in _DIRECT_LABEL.findall(svg)
        }
        painted.update({name.lower(): ink for ink, name in _LEGEND_SWATCH.findall(svg)})
        return painted

    return _ink


@pytest.mark.parametrize("family", ["line", "bar", "area", "scatter"])
def test_a_wide_chart_paints_its_series_in_y_order(
    family: str, series_ink: Callable[[str, str], dict[str, str]]
) -> None:
    first = series_ink(family, "gamma, alpha, beta")
    second = series_ink(family, "alpha, beta, gamma")
    assert len(set(first.values())) == 3, first
    # The series in the first slot, then the second, then the third.
    assert [first["gamma"], first["alpha"], first["beta"]] == [
        second["alpha"],
        second["beta"],
        second["gamma"],
    ]
