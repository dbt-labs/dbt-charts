"""A support_table row reading a column prints with that column's query format."""

from __future__ import annotations

from dbt_charts.core.compile.config import get_theme_style
from dbt_charts.core.compile.models.chart.authored import (
    ChartSupportTable,
    ChartSupportTableSource,
)
from dbt_charts.core.compile.models.chart.normalized import LineChart
from dbt_charts.core.compile.models.query.normalized import SqlQuery
from dbt_charts.core.compile.resolve import resolve
from dbt_charts.core.compile.resolve.style.board import resolve_style_and_context


def test_source_entry_follows_the_query_format() -> None:
    data = [
        {"month": "2025-01", "value": 1.0, "cost": 1500.0},
        {"month": "2025-02", "value": 2.0, "cost": 2500.0},
    ]
    chart = LineChart(
        id="c",
        source_path="charts.c",
        query=SqlQuery(sql="SELECT 1", source="t", formats={"cost": "currency"}),
        query_name="q",
        type="line",
        x="month",
        y="value",
        support_table=ChartSupportTable(
            entries=[ChartSupportTableSource(source="cost")]
        ),
    )
    _, ctx = resolve_style_and_context(get_theme_style(), None)
    resolved = resolve(chart, data, chart_style_context=ctx, width=420.0)
    fmt = resolved.support_table.entries[0].format
    assert fmt is not None
    assert "$" in fmt.spec
