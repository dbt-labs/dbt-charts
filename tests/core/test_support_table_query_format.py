"""A support_table row reading a column prints with that column's query format."""

from __future__ import annotations

from dbt_charts.core.compile.config import get_theme_style
from dbt_charts.core.compile.format import resolve_format_parts
from dbt_charts.core.compile.models.chart.authored import (
    ChartSupportTable,
    ChartSupportTableAggregate,
    ChartSupportTableEntry,
    ChartSupportTablePerSeries,
    ChartSupportTableSource,
)
from dbt_charts.core.compile.models.chart.normalized import BarChart, LineChart
from dbt_charts.core.compile.models.primitives import ResolvedFormat
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


def _integer_spec() -> str:
    _, ctx = resolve_style_and_context(get_theme_style(), None)
    return resolve_format_parts("integer", ctx.formats).spec


def _resolved_entries(
    entries: list[ChartSupportTableEntry],
) -> list[ResolvedFormat | None]:
    data = [
        {"month": "2025-01", "series": "p", "n": 1200.0, "share": 0.123},
        {"month": "2025-01", "series": "q", "n": 800.0, "share": 0.123},
        {"month": "2025-02", "series": "p", "n": 1500.0, "share": 0.2567},
        {"month": "2025-02", "series": "q", "n": 300.0, "share": 0.2567},
    ]
    chart = BarChart(
        id="c",
        source_path="charts.c",
        query=SqlQuery(
            sql="SELECT 1",
            source="t",
            formats={"n": "integer", "share": "percent_whole"},
        ),
        query_name="q",
        type="bar",
        x="month",
        y="n",
        color="series",
        support_table=ChartSupportTable(entries=entries),
    )
    _, ctx = resolve_style_and_context(get_theme_style(), None)
    resolved = resolve(chart, data, chart_style_context=ctx, width=420.0)
    return [entry.format for entry in resolved.support_table.entries]


def test_aggregate_entry_follows_the_query_format() -> None:
    total, share = _resolved_entries(
        [
            ChartSupportTableAggregate(aggregate="sum", source="n"),
            ChartSupportTableAggregate(aggregate="max", source="share"),
        ]
    )
    assert total is not None
    assert total.spec == _integer_spec()
    assert share is not None
    assert "%" in share.spec


def test_count_aggregate_ignores_the_source_column_format() -> None:
    """A count reads rows, not the column's unit."""
    (count,) = _resolved_entries(
        [ChartSupportTableAggregate(aggregate="count", source="share")]
    )
    assert count is None


def test_per_series_entry_follows_the_query_format() -> None:
    (per_series,) = _resolved_entries([ChartSupportTablePerSeries(per_series="n")])
    assert per_series is not None
    assert per_series.spec == _integer_spec()
