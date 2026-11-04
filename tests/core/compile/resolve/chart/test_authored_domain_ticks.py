"""An authored y domain bounds the baked tick ladder: no rung outside it,
and both authored ends are ticked."""

from __future__ import annotations

from typing import Any

import pytest

from d3_format import format as d3_format
from dbt_charts.core.compile.config import get_theme_style, reset_config
from dbt_charts.core.compile.models.chart.normalized import BarChart, LineChart
from dbt_charts.core.compile.resolve import resolve
from dbt_charts.core.compile.resolve.style.board import resolve_style_and_context


@pytest.fixture(autouse=True)
def reset() -> Any:
    reset_config()
    yield
    reset_config()


@pytest.mark.parametrize(
    ("domain", "expected"),
    [
        ([20, 1], (1.0, 5.0, 10.0, 15.0, 20.0)),
        ([1, 20], (1.0, 5.0, 10.0, 15.0, 20.0)),
        ([5, 1], (1.0, 2.0, 3.0, 4.0, 5.0)),
        # An end finer than the ladder step's magnitude would print rounded
        # ("11" for 10.5) or break SI compaction, so it is left unticked.
        ([0, 10.5], (0.0, 5.0, 10.0)),
        ([1, 20000000], (5e6, 1e7, 1.5e7, 2e7)),
        ([0, 1234567], (0.0, 5e5, 1e6)),
        # An added end drops an interior rung sitting under half a step away.
        ([3, 20], (3.0, 10.0, 15.0, 20.0)),
    ],
)
def test_authored_domain_ticks_stay_inside_and_include_both_ends(
    domain: list[int], expected: tuple[float, ...]
) -> None:
    _, ctx = resolve_style_and_context(get_theme_style("clarity"))
    chart = LineChart.model_validate(
        {
            "id": "t",
            "type": "line",
            "x": "x",
            "y": "val",
            "style": {"axis_y": {"scale": {"continuous": {"domain": domain}}}},
        }
    )
    rows = [{"x": i, "val": 1 + i} for i in range(1, 20)]
    ticks = resolve(chart, rows, chart_style_context=ctx).style.axis_y.tick_values
    assert tuple(sorted(ticks)) == expected, ticks


def test_uneven_authored_tick_list_keeps_the_precision_of_its_finest_gap() -> None:
    """The format step is the smallest gap painted: ``[0, 0.5, 1, 2, 3, 4]``
    must not label the 0.5 tick "1" (twice)."""
    _, ctx = resolve_style_and_context(get_theme_style("clarity"))
    chart = LineChart.model_validate(
        {
            "id": "t",
            "type": "line",
            "x": "x",
            "y": "val",
            "style": {"axis_y": {"scale": {"values": [0, 0.5, 1, 2, 3, 4]}}},
        }
    )
    rows = [{"x": i, "val": i / 5} for i in range(1, 20)]
    axis = resolve(chart, rows, chart_style_context=ctx).style.axis_y
    assert axis.tick_label is not None
    labels = [d3_format(axis.tick_label.format)(t) for t in axis.tick_values]
    assert len(set(labels)) == len(labels), labels
    assert "0.5" in labels, labels


@pytest.mark.parametrize(
    ("domain", "expected"),
    [
        ([20, 1], (1.0, 5.0, 10.0, 15.0, 20.0)),
        ([0, 10.5], (0.0, 5.0, 10.0)),
    ],
)
def test_stacked_bar_authored_domain_ticks_stay_inside(
    domain: list[int], expected: tuple[float, ...]
) -> None:
    _, ctx = resolve_style_and_context(get_theme_style("clarity"))
    chart = BarChart.model_validate(
        {
            "id": "t",
            "type": "bar",
            "x": "x",
            "y": "val",
            "stack": "zero",
            "style": {"axis_y": {"scale": {"continuous": {"domain": domain}}}},
        }
    )
    rows = [{"x": i, "val": 1 + i} for i in range(1, 20)]
    ticks = resolve(chart, rows, chart_style_context=ctx).style.axis_y.tick_values
    assert tuple(sorted(ticks)) == expected, ticks
