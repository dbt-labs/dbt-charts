"""A chart whose legend is hidden under a side legend edge names no series.

Run through the real render pipeline: the fact is decided where the chart
resolves, against that chart's own cascaded style.
"""

from __future__ import annotations

import json
from typing import Any

import pytest

from dbt_charts.core.compile import compile as compile_board
from dbt_charts.core.compile.models.chart.resolved import ResolvedLineChart
from dbt_charts.core.compile.resolve import resolve
from dbt_charts.core.diagnostics import WARN_LEGEND_HIDDEN_UNDER_EDGE

from ._svg_render import render_board_svg_result

_ROWS: list[dict[str, Any]] = [
    {"m": m, "s": s, "v": v + i, "w": 2 * (v + i)}
    for i, m in enumerate(["2024-01-01", "2024-02-01", "2024-03-01"])
    for s, v in (("a", 1), ("b", 3))
]

_HEAD = f"""
queries:
  q:
    type: values
    rows: {json.dumps(_ROWS)}
"""
_LINE = "    type: line\n    query: q\n    x: m\n    y: v\n    color: s\n"
_MULTI_Y = "    type: line\n    query: q\n    x: m\n    y: [v, w]\n"
_GROUPED = "    type: bar\n    query: q\n    x: m\n    y: v\n    color: s\n"
_MULTIPLES = _LINE + "    multiples:\n      rows: s\n"
_SINGLE = "    type: line\n    query: q\n    x: m\n    y: v\n"
_AREA = "    type: area\n    query: q\n    x: m\n    y: v\n    color: s\n"
_PER_SERIES_TABLE = (
    _LINE + "    support_table:\n      entries:\n        - per_series: v\n"
)


def _legend(*, visible: bool | None = None, edge: str | None = None) -> str:
    lines = ["legend:"]
    if visible is not None:
        lines.append(f"  visible: {str(visible).lower()}")
    if edge is not None:
        lines += ["  position:", f"    edge: {edge}"]
    return "\n".join(lines)


def _indent(text: str, spaces: int) -> str:
    return "\n".join(" " * spaces + line for line in text.splitlines()) + "\n"


def _charts_style(block: str, *, family: str | None = None) -> str:
    inner = _indent(block, 4 if family is None else 6)
    if family is None:
        return f"style:\n  charts:\n{inner}"
    return f"style:\n  charts:\n    {family}:\n{inner}"


def _chart_style(block: str) -> str:
    return "    style:\n" + _indent(block, 6)


def _warns(board: str) -> list[str]:
    rendered = render_board_svg_result(board)
    return [
        w.chart or ""
        for w in rendered.warnings
        if w.code == WARN_LEGEND_HIDDEN_UNDER_EDGE.code
    ]


def _board(
    chart: str = _LINE,
    *,
    style: str = "",
    chart_style: str = "",
    layout: str = "rows:\n  - c\n",
) -> str:
    return f"{style}{_HEAD}charts:\n  c:\n{chart}{chart_style}{layout}"


_FAMILY_HIDES_UNDER_BOARD_EDGE = (
    "style:\n  charts:\n    legend:\n      position:\n        edge: top\n"
    "    line:\n      legend:\n        visible: false\n"
)
_HIDDEN = _chart_style(_legend(visible=False))
_TOP = _charts_style(_legend(edge="top"))


@pytest.mark.parametrize(
    "board",
    [
        _board(style=_TOP, chart_style=_HIDDEN),
        _board(
            style=_charts_style(_legend(edge="top"), family="line"), chart_style=_HIDDEN
        ),
        _board(style=_FAMILY_HIDES_UNDER_BOARD_EDGE),
        _board(chart=_MULTI_Y, style=_TOP, chart_style=_HIDDEN),
        _board(chart=_AREA, style=_TOP, chart_style=_HIDDEN),
        _board(
            chart=_GROUPED,
            style=_TOP,
            chart_style=_chart_style("stack: zero\n" + _legend(visible=False)),
        ),
        _board(
            style=_charts_style(_legend(visible=False)),
            chart_style=_chart_style(_legend(edge="top")),
        ),
        _board(
            style=_charts_style(_legend(visible=False)),
            chart_style=_chart_style(_legend(edge="right")),
        ),
        _board(chart_style=_chart_style(_legend(visible=False, edge="bottom"))),
        _board(
            layout=(
                "rows:\n  - style:\n      charts:\n        legend:\n"
                "          position:\n            edge: top\n    rows:\n      - c\n"
            ),
            chart_style=_HIDDEN,
        ),
        _board(
            layout=(
                "tabs:\n  items:\n    - title: t\n      style:\n        charts:\n"
                "          legend:\n            position:\n              edge: top\n"
                "      rows:\n        - c\n"
            ),
            chart_style=_HIDDEN,
        ),
    ],
    ids=[
        "board-edge",
        "family-edge",
        "family-hides-under-board-edge",
        "multi-y",
        "area",
        "stacked-bar",
        "board-hides-chart-edge-top",
        "board-hides-chart-edge-right",
        "chart-sets-both",
        "nested-board-edge",
        "tab-edge",
    ],
)
def test_a_hidden_legend_under_a_side_names_nothing_and_is_told(board: str) -> None:
    assert _warns(board) == ["c"]


@pytest.mark.parametrize(
    "board",
    [
        _board(
            style=_TOP, chart_style=_chart_style(_legend(visible=False, edge="auto"))
        ),
        _board(
            style=_TOP,
            chart_style=_chart_style(_legend(visible=False, edge="endpoints")),
        ),
        _board(style=_TOP, chart_style=_chart_style(_legend(visible=True))),
        _board(style=_TOP),
        _board(chart_style=_HIDDEN),
        _board(chart=_SINGLE, style=_TOP, chart_style=_HIDDEN),
        _board(chart=_GROUPED, style=_TOP, chart_style=_HIDDEN),
        _board(
            chart=_GROUPED,
            style=_TOP,
            chart_style=_chart_style(_legend(visible=False, edge="auto")),
        ),
        _board(chart=_MULTIPLES, style=_TOP, chart_style=_HIDDEN),
        _board(style=_TOP + "width: 300\n", chart_style=_HIDDEN),
        _board(chart=_PER_SERIES_TABLE, style=_TOP, chart_style=_HIDDEN),
    ],
    ids=[
        "chart-edge-auto",
        "chart-edge-endpoints",
        "legend-shown",
        "legend-not-hidden",
        "no-edge",
        "single-series",
        "grouped-bar",
        "grouped-bar-edge-auto",
        "multiples",
        "tiny",
        "per-series-support-table",
    ],
)
def test_nothing_is_unnamed_so_nothing_is_said(board: str) -> None:
    assert _warns(board) == []


def _named(chart_style: str) -> tuple[bool, bool]:
    """(endpoint labels, legend visible) as the chart resolves."""
    result = compile_board(_board(chart_style=chart_style))
    assert result.success and result.board is not None, result.errors
    [item] = result.board.layout.items
    assert item.chart is not None
    chart = resolve(
        item.chart,
        _ROWS,
        chart_style_context=result.board.chart_style_context,
        width=900.0,
    )
    assert isinstance(chart, ResolvedLineChart)
    return chart.style.endpoint_labels.visible, chart.legend.visible


@pytest.mark.parametrize(
    "placement",
    [
        "legend:\n  position:\n    align: start",
        "legend:\n  position:\n    overlay: true",
    ],
    ids=["align", "overlay"],
)
def test_a_placed_legend_with_no_edge_rules_the_rail_out(placement: str) -> None:
    assert _named(_chart_style(placement)) == (False, True)
    assert _named(_chart_style(placement + "\n  visible: false")) == (True, False)
