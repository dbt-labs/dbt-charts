"""A chart whose `color:` names a column the rows lack is an authoring error."""

from __future__ import annotations

import pytest

from dbt_charts.core.compile.config import get_theme_style
from dbt_charts.core.compile.models.chart.normalized import BarChart, PieChart
from dbt_charts.core.compile.models.query.normalized import SqlQuery
from dbt_charts.core.compile.resolve import resolve
from dbt_charts.core.compile.resolve.style.board import resolve_chart_style_context
from dbt_charts.core.diagnostics.base import DbtChartsError

_ROWS: list[dict[str, object]] = [{"cat": "A", "val": 40.0}, {"cat": "B", "val": 60.0}]


def _resolve(chart_type: str, data: list[dict[str, object]]) -> None:
    common = {
        "id": "c1",
        "color": "region",
        "query": SqlQuery(sql="SELECT 1", source="t"),
        "query_name": "q",
        "variable_dependencies": set(),
    }
    chart = (
        PieChart(type="pie", theta="val", **common)
        if chart_type == "pie"
        else BarChart(type="bar", x="cat", y="val", **common)
    )
    resolve(chart, data, resolve_chart_style_context(get_theme_style()))


@pytest.mark.parametrize("chart_type", ["pie", "bar"])
def test_missing_color_column_with_rows_raises_registered_error(
    chart_type: str,
) -> None:
    with pytest.raises(DbtChartsError) as exc_info:
        _resolve(chart_type, _ROWS)
    assert exc_info.value.code is not None
    assert exc_info.value.code.code == "ERR-CHART-COLUMN-NOT-IN-RESULT"
    message = str(exc_info.value)
    assert "c1" in message
    assert "region" in message


def test_missing_color_column_with_zero_rows_still_resolves() -> None:
    _resolve("pie", [])
