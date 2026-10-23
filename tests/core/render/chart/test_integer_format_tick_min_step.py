"""Regression: an integer (or other fixed-decimal) style.number_format on a
small-magnitude measure axis must not let a computed tick ladder paint
duplicate labels.

Repro: values 0..2 on style.number_format: integer nice-round to ticks
0, 0.5, 1, 1.5, 2 -- each rounds to a whole number under the integer format,
so the axis reads 0, 1, 1, 2, 2 with a gridline sitting on no real label.

nice_tick_values (core/numeric.py) floors its own step at the format's fixed
decimal count (compile.format.tick_min_step_for_format) wherever resolve bakes
a ladder. Where it bakes none (unauthored ticks.count such as stark, or a
stacked bar with a negative row), the format reaches Vega-Lite as a resolved
tickMinStep instead -- never beside baked values.
"""

from __future__ import annotations

import pytest
from pydantic import TypeAdapter

from dbt_charts.core.compile.config import get_theme_style, reset_config
from dbt_charts.core.compile.models.chart.normalized import Chart
from dbt_charts.core.compile.models.query.normalized import SqlQuery
from dbt_charts.core.compile.resolve import resolve
from dbt_charts.core.compile.resolve.style.board import (
    resolve_chart_style_context,
)

_BOARD_STYLE = resolve_chart_style_context(get_theme_style())


@pytest.mark.parametrize("chart_type", ["bar", "area"])
def test_normalize_stack_resolves_no_tick_step(chart_type: str) -> None:
    # The axis paints percent regardless of the authored format, so the
    # resolved model must not publish a step derived from that format.
    reset_config()
    chart = TypeAdapter(Chart).validate_python(
        {
            "id": "t",
            "type": chart_type,
            "x": "x",
            "y": "y",
            "color": "series",
            "query": SqlQuery(sql="SELECT 1", source="src"),
            "query_name": "q",
            "style": {
                "stack": "normalize",
                "axis_y": {"labels": {"format": "integer"}},
            },
        }
    )
    rows = [
        {"x": "a", "series": "s1", "y": 3.0},
        {"x": "a", "series": "s2", "y": 1.0},
    ]
    resolved = resolve(chart, rows, chart_style_context=_BOARD_STYLE)
    reset_config()
    assert resolved.style.axis_y.ticks.step is None
