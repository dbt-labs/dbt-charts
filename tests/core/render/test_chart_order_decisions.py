"""How a chart orders and the board colors its series, asserted on the painted legend.

Each case reads the real render's legend: its labels in order and the swatch color of
each. A swatch repeats the color of the same series on a control board where it ranks
first, so a color assertion needs no palette literal.
"""

from __future__ import annotations

import re
from collections.abc import Callable
from pathlib import Path

import pytest

from dbt_charts.core.compile import compile
from dbt_charts.core.execute.env import build_exec_env
from dbt_charts.core.render.renderer import render

from ..._run import Run

_SWATCH = re.compile(
    r'role-legend-symbol"[^>]*>.*?(?:fill|stroke)="(#[0-9a-fA-F]{6})".*?</g>'
    r'<g class="mark-text role-legend-label"[^>]*>.*?<text[^>]*>([^<]+)(?:<title>[^<]*</title>)?</text>'
)

Legend = Callable[[str], list[tuple[str, str]]]


@pytest.fixture
def legend(local_project: Callable[..., object]) -> Legend:
    def _legend(text: str) -> list[tuple[str, str]]:
        result = compile(text)
        assert result.success and result.board is not None, result.errors
        run = Run(result.board, env=build_exec_env(local_project(Path.cwd())))
        svg = render(result.board, run.ctx, run.ledger, format="svg").output
        assert isinstance(svg, str)
        return [(label, fill) for fill, label in _SWATCH.findall(svg)]

    return _legend


def _board(rows: str, chart: str) -> str:
    return f"queries:\n  q:\n    type: values\n    rows:\n{rows}charts:\n  c:\n{chart}rows:\n  - c\n"


def _rows(*cells: str) -> str:
    return "".join(f"      - {cell}\n" for cell in cells)


_RANKS = _rows(
    "{year: 2020, entity: Best, rank: 1}",
    "{year: 2021, entity: Best, rank: 1}",
    "{year: 2020, entity: Worst, rank: 6}",
    "{year: 2021, entity: Worst, rank: 6}",
)
_LINE = "    type: line\n    query: q\n    x: year\n    y: rank\n    color: entity\n    style:\n      legend:\n        position:\n          edge: right\n"


def test_a_backwards_axis_ranks_the_smallest_first(legend: Legend) -> None:
    forward = legend(_board(_RANKS, _LINE))
    backward = legend(
        _board(
            _RANKS,
            _LINE
            + "      axis_y:\n        scale:\n          continuous:\n            domain: [6, 1]\n",
        )
    )
    assert [name for name, _ in forward] == ["Worst", "Best"]
    assert [name for name, _ in backward] == ["Best", "Worst"]
    # Best takes the lead color on the backwards axis, as Worst does on the forward one.
    assert backward[0][1] == forward[0][1]


def test_a_scatter_ranks_by_its_average_not_its_point_count(legend: Legend) -> None:
    rows = _rows(
        *[f"{{x: {i}, y: 10, kind: many}}" for i in range(5)],
        "{x: 9, y: 40, kind: few}",
    )
    chart = "    type: scatter\n    query: q\n    x: x\n    y: y\n    color: kind\n    style:\n      legend:\n        position:\n          edge: right\n"
    assert [name for name, _ in legend(_board(rows, chart))] == ["few", "many"]


def test_a_waterfall_lists_its_directions_by_bar_length(legend: Legend) -> None:
    rows = _rows(
        "{step: Start, start: 0, end: 40, direction: Net}",
        "{step: Up, start: 40, end: 140, direction: Gain}",
        "{step: Down, start: 140, end: 120, direction: Loss}",
    )
    chart = "    type: bar\n    query: q\n    x: step\n    y: end\n    y_start: start\n    color: direction\n    style:\n      legend:\n        position:\n          edge: right\n"
    painted = legend(_board(rows, chart))
    assert [name for name, _ in painted] == ["Gain", "Net", "Loss"]
    assert len({fill for _, fill in painted}) == 3


def test_composite_series_rank_by_their_own_totals(legend: Legend) -> None:
    rows = _rows(
        "{week: '2024-01-01', cat: A, avg: 1, big: 100}",
        "{week: '2024-01-01', cat: B, avg: 2, big: 50}",
        "{week: '2024-01-08', cat: A, avg: 1, big: 100}",
        "{week: '2024-01-08', cat: B, avg: 2, big: 50}",
    )
    chart = "    type: line\n    query: q\n    x: week\n    y: [avg, big]\n    color: cat\n    style:\n      legend:\n        position:\n          edge: right\n"
    names = [name for name, _ in legend(_board(rows, chart))]
    assert names == ["A - Big", "B - Big", "B - Avg", "A - Avg"]


def test_a_signed_stack_lists_its_legend_as_the_layers_draw(legend: Legend) -> None:
    rows = _rows(
        "{month: Jan, direction: above, diff: 30}",
        "{month: Jan, direction: below, diff: -16}",
        "{month: Feb, direction: above, diff: 20}",
        "{month: Feb, direction: below, diff: -10}",
    )
    chart = "    type: bar\n    query: q\n    x: month\n    y: diff\n    color: direction\n    style:\n      stack: zero\n      orientation: vertical\n      legend:\n        position:\n          edge: right\n"
    assert [name for name, _ in legend(_board(rows, chart))] == ["above", "below"]


def test_a_negative_series_lists_and_colors_by_magnitude(legend: Legend) -> None:
    rows = _rows(
        "{m: Jan, s: small, v: -10}",
        "{m: Feb, s: small, v: -10}",
        "{m: Jan, s: big, v: -100}",
        "{m: Feb, s: big, v: -100}",
    )
    chart = "    type: line\n    query: q\n    x: m\n    y: v\n    color: s\n    style:\n      legend:\n        position:\n          edge: right\n"
    positive = rows.replace("v: -", "v: ")
    negative_legend = legend(_board(rows, chart))
    assert [name for name, _ in negative_legend] == ["big", "small"]
    # The lead color goes to the same series whichever sign the values carry.
    assert negative_legend == legend(_board(positive, chart))


def test_numbered_composite_series_rank_by_size_not_number(legend: Legend) -> None:
    # One dimension value over numbered measures: every composite is one stem and a number.
    rows = _rows(
        "{week: '2024-01-01', region: West, score_1: 2, score_2: 100}",
        "{week: '2024-01-08', region: West, score_1: 3, score_2: 90}",
    )
    chart = "    type: line\n    query: q\n    x: week\n    y: [score_1, score_2]\n    color: region\n    style:\n      legend:\n        position:\n          edge: right\n"
    names = [name for name, _ in legend(_board(rows, chart))]
    assert names == ["West - Score 2", "West - Score 1"]
