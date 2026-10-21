"""A query's `formats:` reaches a bubble map's size tooltip like every other slot."""

from __future__ import annotations

from dbt_charts.core.compile.models.chart.normalized import PointMapChart
from dbt_charts.core.compile.models.query.normalized import SqlQuery

from ._board_utils import make_test_resolved_chart


def test_bubble_map_tooltip_follows_the_query_format() -> None:
    chart = PointMapChart(
        id="c1",
        type="bubble_map",
        query_name="q",
        query=SqlQuery(sql="SELECT 1", source="s", formats={"profit": "currency"}),
        latitude="lat",
        longitude="lng",
        size="profit",
    )
    resolved = make_test_resolved_chart(
        chart, [{"lat": 34.05, "lng": -118.25, "profit": 1500.0}]
    )
    assert "$" in resolved.style.tooltip_format.spec
