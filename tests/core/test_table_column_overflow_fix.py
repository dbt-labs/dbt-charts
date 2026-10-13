"""Regression: actual_content_width must never exceed available_width.

Before the fix, sacred columns (numeric/date) refused to shrink below their
content_floor, so a 12-column table in a 600px tile could produce
actual_content_width ≈ 840px — overflowing the tile boundary.

After the fix, all auto-columns receive an equal share of available_width.
The table always fits the tile.
"""

from __future__ import annotations

import re

from dbt_charts.core.compile.config import (
    get_theme_style,
)
from dbt_charts.core.compile.resolve import resolve
from dbt_charts.core.compile.resolve.style.board import (
    resolve_chart_style_context,
    resolve_style,
)

_BOARD_STYLE = resolve_chart_style_context(get_theme_style())


def _make_12col_data() -> list[dict]:
    return [
        {
            "name": "Acme Corp",
            "region": "West",
            "stage": "Closed Won",
            "owner": "Alice",
            "amount": 125000,
            "quota": 100000,
            "attainment": 1.25,
            "close_date": "2024-03-15",
            "renewal_date": "2025-03-15",
            "score": 9.5,
            "rank": 1,
            "ytd": 987654,
        },
        {
            "name": "Globex",
            "region": "East",
            "stage": "Negotiation",
            "owner": "Bob",
            "amount": 8500,
            "quota": 50000,
            "attainment": 0.17,
            "close_date": "2024-06-30",
            "renewal_date": "2025-06-30",
            "score": 4.2,
            "rank": 12,
            "ytd": 12345,
        },
    ]


_COLUMNS = [
    "name",
    "region",
    "stage",
    "owner",
    "amount",
    "quota",
    "attainment",
    "close_date",
    "renewal_date",
    "score",
    "rank",
    "ytd",
]


class TestColumnsNeverExceedAvailableWidth:
    """All auto-columns fit inside available_width regardless of column type."""

    def test_svg_width_matches_tile_width(self):
        """SVG outer rect must not exceed available_width for many-column table."""
        from dbt_charts.core.compile.models.chart.normalized import TableChart
        from dbt_charts.core.compile.models.query.normalized import SqlQuery

        from ._engine_render import render_table_svg

        chart = TableChart(
            id="overflow_test",
            query=SqlQuery(sql="SELECT 1", source="test"),
            query_name="q",
            type="table",
        )
        data = _make_12col_data()
        available_width = 600
        chart = resolve(chart, data, chart_style_context=_BOARD_STYLE)
        svg = render_table_svg(
            chart,
            data,
            width=available_width,
            board_style=resolve_style(get_theme_style()),
        )

        svg_width_m = re.search(r'<svg[^>]*\bwidth="([^"]+)"', svg)
        assert svg_width_m is not None, "Could not find SVG width attribute"
        actual_width = float(svg_width_m.group(1))
        assert actual_width <= available_width + 0.5, (
            f"SVG width {actual_width:.1f}px exceeds tile width {available_width}px — "
            f"table must fit within tile boundaries regardless of column count."
        )
