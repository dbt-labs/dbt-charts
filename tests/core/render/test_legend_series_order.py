"""A legend lists series in the order of their color slots, through the real render.

Sizes make slot order (beta 61, gamma 60, alpha 40) differ from alphabetical and
from last-value order (alpha, beta, gamma), so neither can pass for it. A stacked
chart's legend follows its stack instead (top of stack first), and a wide `y:`
chart's follows `y:`.
"""

import re
from pathlib import Path

import pytest

from dbt_charts.core.compile import compile
from dbt_charts.core.execute.env import build_exec_env
from dbt_charts.core.render.renderer import render

from ..._run import Run

LONG = """
queries:
  long:
    type: values
    rows:
      - {month: "2026-01-01", series: beta, n: 30}
      - {month: "2026-01-01", series: gamma, n: 50}
      - {month: "2026-02-01", series: alpha, n: 40}
      - {month: "2026-02-01", series: beta, n: 31}
      - {month: "2026-02-01", series: gamma, n: 10}
  wide:
    type: values
    rows:
      - {month: "2026-01-01", gamma: 50, alpha: 5, beta: 30}
      - {month: "2026-02-01", gamma: 10, alpha: 40, beta: 31}
charts:
  c:
    query: %s
    type: %s
    x: month
    %s
    style:
      legend:
        position:
          edge: right
      %s
rows:
  - c
"""
CASES = {
    "line": ("long", "line", "y: n\n    color: series", ""),
    "bar_grouped": ("long", "bar", "y: n\n    color: series", "stack: none"),
    "bar_stacked": ("long", "bar", "y: n\n    color: series", "stack: zero"),
    "area_stacked": ("long", "area", "y: n\n    color: series", "stack: zero"),
    "area_unstacked": ("long", "area", "y: n\n    color: series", "stack: none"),
    "scatter": ("long", "scatter", "y: n\n    color: series", ""),
    "wide_line": ("wide", "line", "y: [gamma, alpha, beta]", ""),
    "wide_bar": ("wide", "bar", "y: [gamma, alpha, beta]", "stack: none"),
}
_EXPECTED = {
    "line": ["beta", "gamma", "alpha"],
    "bar_grouped": ["beta", "gamma", "alpha"],
    "area_unstacked": ["beta", "gamma", "alpha"],
    # A scatter is sized by its average y: alpha 40, beta 30.5, gamma 30.
    "scatter": ["alpha", "beta", "gamma"],
    "bar_stacked": ["alpha", "gamma", "beta"],
    "area_stacked": ["alpha", "gamma", "beta"],
    "wide_line": ["Gamma", "Alpha", "Beta"],
    # Totals: beta 61, gamma 60, alpha 45.
    "wide_bar": ["Beta", "Gamma", "Alpha"],
}


@pytest.mark.parametrize("name", list(CASES))
def test_a_legend_lists_series_in_slot_order(name, local_project):
    q, t, y, st = CASES[name]
    r = compile(LONG % (q, t, y, st))
    assert r.success, r.errors
    ex = Run(r.board, env=build_exec_env(local_project(Path.cwd())))
    svg = render(r.board, ex.ctx, ex.ledger, format="svg").output
    assert (
        re.findall(r'role-legend-label"[^>]*><text[^>]*>([^<]+)</text>', svg)
        == _EXPECTED[name]
    )


_TWO_CHARTS = """
queries:
  mine:
    type: values
    rows:
      - {month: Jan, series: gamma, n: 10}
      - {month: Jan, series: beta, n: 30}
      - {month: Jan, series: alpha, n: 20}
  theirs:
    type: values
    rows:
      - {month: Jan, series: gamma, n: 1000}
      - {month: Jan, series: beta, n: 1}
      - {month: Jan, series: alpha, n: 1}
charts:
  grouped:
    type: bar
    query: mine
    x: month
    y: n
    color: series
    style:
      stack: none
      legend:
        position:
          edge: right
  other:
    type: bar
    query: theirs
    x: month
    y: n
    color: series
    style:
      stack: none
rows:
  - grouped
  - other
"""


def test_a_chart_orders_its_series_by_its_own_data_while_the_board_colors_them(
    local_project,
):
    """The board ranks gamma first (it dominates the other chart), the grouped bar's
    own data ranks beta, alpha, gamma: its legend and its bars (top to bottom, as the bars
    run horizontally on a categorical x) follow the latter."""
    r = compile(_TWO_CHARTS)
    assert r.success, r.errors
    ex = Run(r.board, env=build_exec_env(local_project(Path.cwd())))
    svg = render(r.board, ex.ctx, ex.ledger, format="svg").output
    chart = next(
        part
        for part in re.split(r"(?=data-chart-id=)", svg)
        if part.startswith('data-chart-id="grouped"')
    )
    legend = re.findall(r'role-legend-label"[^>]*><text[^>]*>([^<]+)</text>', svg)
    bars = sorted(
        (float(y), name)
        for y, name in re.findall(
            r'<path d="M[\d.]+,([\d.]+)[^"]*"[^>]*aria-label="[^"]*⁢(\w+);', chart
        )
    )
    first_chart_bars = [name for _, name in bars]
    # The other chart's own data ranks gamma first.
    assert legend == ["beta", "alpha", "gamma", "gamma", "alpha", "beta"]
    legend = legend[:3]
    assert sorted(first_chart_bars) == ["alpha", "beta", "gamma"]
    assert first_chart_bars == legend
