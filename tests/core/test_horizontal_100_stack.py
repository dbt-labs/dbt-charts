"""Regression: horizontal 100% stacked bars must pin stack on the measure axis.

When ``style.orientation: horizontal`` is combined with ``style.stack: normalize``,
the orientation routing makes encoding.x the measure axis and encoding.y the
categorical axis. The stack mode must land on the measure encoding (encoding.x
for horizontal, encoding.y for vertical) — putting it on the categorical
encoding leaves VL with no stacking instruction on the measure axis, so:

  - bars don't normalize to [0, 1] and clip past the manually-pinned domain,
    showing as solid full-width rectangles with no visible segments;
  - the auto-percent format VL applies when ``stack: normalize`` sits on the
    quantitative axis never fires, so the measure axis renders raw decimals
    (0.0, 0.1, ...) instead of percentages;
  - the categorical axis carries an unsupported ``stack: normalize`` field
    that causes VL to render every category label as ``NaN%``.

The vertical 100% case must continue to pin stack on encoding.y (no regression).
"""

from __future__ import annotations

import pytest

from dbt_charts.core.compile.models.chart.normalized import (
    AreaChart,
    BarChart,
    LineChart,
)
from dbt_charts.core.compile.models.query.normalized import SqlQuery
from dbt_charts.core.diagnostics.chart_data import ChartDataError

from ._engine_render import render_chart_svg


@pytest.mark.parametrize(
    ("chart_class", "chart_type"),
    [(BarChart, "bar"), (LineChart, "line"), (AreaChart, "area")],
)
def test_wide_form_duplicate_categories_raise(chart_class, chart_type) -> None:
    chart = chart_class(
        id="t",
        query=SqlQuery(sql="SELECT 1", source="t"),
        query_name="q",
        type=chart_type,
        x="month",
        y=["revenue", "expenses"],
    )
    duplicated = [
        {"month": "Jan", "revenue": 10, "expenses": 30},
        {"month": "Jan", "revenue": 20, "expenses": 40},
    ]
    with pytest.raises(ChartDataError, match="duplicate"):
        render_chart_svg(chart, duplicated, width=400, height=300)
