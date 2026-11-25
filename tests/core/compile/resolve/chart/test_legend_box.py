"""The legend's padding is decided per chart at resolve: ``auto`` is the legend's own
placement, and the fill follows the chart's own background unless the legend sets one."""

from __future__ import annotations

from typing import Any

import pytest

from dbt_charts.core.compile.config import get_theme_style
from dbt_charts.core.compile.models.style.resolved import ResolvedLegendStyle
from dbt_charts.core.compile.resolve import resolve
from dbt_charts.core.compile.resolve.style.board import resolve_chart_style_context

_ROWS = [
    {"month": "Jan", "value": 1, "region": "east"},
    {"month": "Jan", "value": 2, "region": "west"},
]


def _resolved(
    make_chart: Any, style: dict[str, Any]
) -> tuple[ResolvedLegendStyle, str]:
    context = resolve_chart_style_context(get_theme_style())
    chart = make_chart("bar", x="month", y="value", color="region", style=style)
    resolved = resolve(chart, _ROWS, chart_style_context=context)
    return resolved.legend, resolved.background


def _legend(**leaves: Any) -> dict[str, Any]:
    return {"key": {"visible": True, "type": "legend", "legend": leaves}}


@pytest.mark.parametrize(
    ("legend", "padding"),
    [
        ({"position": {"edge": "top", "overlay": True}}, 5.0),
        ({"position": {"edge": "right", "overlay": False}}, 0.0),
        ({"position": {"edge": "top", "overlay": True}, "padding": 2}, 2.0),
        ({"position": {"edge": "right", "overlay": False}, "padding": 8}, 8.0),
        ({"position": {"edge": "right", "overlay": False}, "padding": "auto"}, 0.0),
    ],
)
def test_auto_padding_is_five_when_the_legend_floats_and_a_number_applies_to_every_legend(
    make_chart: Any, legend: dict[str, Any], padding: float
) -> None:
    resolved, _ = _resolved(make_chart, _legend(**legend))
    assert resolved.padding == padding


def test_the_fill_is_the_charts_own_background_unless_the_legend_sets_one(
    make_chart: Any,
) -> None:
    own = "#3366cc"
    resolved, background = _resolved(make_chart, {**_legend(), "background": own})
    assert background == own
    assert resolved.background == own
    authored, _ = _resolved(
        make_chart, {**_legend(background="#abcdef"), "background": own}
    )
    assert authored.background == "#abcdef"
