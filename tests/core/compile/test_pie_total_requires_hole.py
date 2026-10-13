"""`total:` is drawn at the pie's center, so a solid pie (no hole) must reject it
rather than print the sum on top of the wedges.
"""

from __future__ import annotations

import pytest

from dbt_charts.core.compile.config import get_theme_style
from dbt_charts.core.compile.errors import CompilationError
from dbt_charts.core.compile.models.chart.authored import ChartTotal
from dbt_charts.core.compile.models.chart.normalized import PieChart
from dbt_charts.core.compile.models.query.normalized import SqlQuery
from dbt_charts.core.compile.models.style.authored import PieChartStylePatch
from dbt_charts.core.compile.resolve import resolve
from dbt_charts.core.compile.resolve.style.board import resolve_chart_style_context

_DATA = [{"cat": "A", "val": 40.0}, {"cat": "B", "val": 60.0}]


def _resolve_pie(total: ChartTotal, inner_radius: float | None) -> None:
    style = (
        PieChartStylePatch.model_validate({"inner_radius": inner_radius})
        if inner_radius is not None
        else None
    )
    chart = PieChart(
        id="pie1",
        type="pie",
        theta="val",
        color="cat",
        query=SqlQuery(sql="SELECT 1", source="t"),
        query_name="q",
        variable_dependencies=set(),
        total=total,
        style=style,
    )
    resolve(chart, _DATA, resolve_chart_style_context(get_theme_style()))


def test_total_on_solid_pie_rejected() -> None:
    with pytest.raises(CompilationError) as exc_info:
        _resolve_pie(ChartTotal(label="Total"), inner_radius=None)
    assert exc_info.value.code is not None
    assert exc_info.value.code.code == "ERR-PIE-TOTAL-WITHOUT-HOLE"
    assert "style.inner_radius" in str(exc_info.value)


def test_total_on_donut_compiles() -> None:
    _resolve_pie(ChartTotal(label="Total"), inner_radius=0.6)


def test_hidden_total_on_solid_pie_compiles() -> None:
    _resolve_pie(ChartTotal(visible=False), inner_radius=None)
