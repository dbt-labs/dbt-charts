"""A ``sort.by`` column absent from the query rows must fail, not render unsorted."""

from __future__ import annotations

import pytest

from dbt_charts.core.compile.models.chart.authored import ChartSort
from dbt_charts.core.compile.models.chart.normalized import BarChart, LineChart
from dbt_charts.core.compile.models.query.normalized import SqlQuery
from dbt_charts.core.diagnostics.chart_data import ChartDataError

from ._engine_render import render_chart_svg

_ROWS = [
    {"title": "A", "genre": "rpg", "sessions": 10, "crash_rate": 0.3},
    {"title": "B", "genre": "fps", "sessions": 20, "crash_rate": 0.1},
]


def _bar(sort_by: str, color: str | None = "genre") -> BarChart:
    return BarChart(
        id="c",
        query=SqlQuery(sql="SELECT 1", source="test"),
        query_name="q",
        type="bar",
        x="title",
        y="sessions",
        color=color,
        sort=ChartSort(by=sort_by, order="desc"),
    )


@pytest.mark.parametrize("color", ["genre", None])
def test_missing_sort_column_raises(color: str | None) -> None:
    with pytest.raises(ChartDataError, match="nonexistent_col") as exc:
        render_chart_svg(_bar("nonexistent_col", color), _ROWS)
    assert exc.value.code.code == "ERR-SORT-FIELD-NOT-FOUND"


def test_sort_column_missing_from_a_later_row_raises() -> None:
    rows = [*_ROWS, {"title": "C", "genre": "rpg", "sessions": 5}]
    with pytest.raises(ChartDataError, match="crash_rate"):
        render_chart_svg(_bar("crash_rate"), rows)


def test_missing_sort_column_raises_on_line() -> None:
    chart = LineChart(
        id="c",
        query=SqlQuery(sql="SELECT 1", source="test"),
        query_name="q",
        type="line",
        x="title",
        y="sessions",
        sort=ChartSort(by="nope", order="asc"),
    )
    with pytest.raises(ChartDataError, match="nope"):
        render_chart_svg(chart, _ROWS)
