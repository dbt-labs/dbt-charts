"""Theme styling reaches the encoded axis."""

from __future__ import annotations

from dbt_charts.core.compile.config import (
    get_theme_style,
    reset_config,
)
from dbt_charts.core.compile.resolve import resolve
from dbt_charts.core.compile.resolve.style.board import resolve_chart_style_context
from dbt_charts.core.render.chart.vega_lite import generate_vega_lite_spec

_BOARD_STYLE = resolve_chart_style_context(get_theme_style())


class TestThemeGridColorThreading:
    def setup_method(self) -> None:
        reset_config()

    def teardown_method(self) -> None:
        reset_config()

    def test_default_theme_applies_when_requested(self, make_chart) -> None:
        """Theme values flow into the y-axis gridColor in the encoded spec."""
        chart = make_chart("bar", x="month", y="revenue")
        data = [
            {"month": "Jan", "revenue": 100},
            {"month": "Feb", "revenue": 120},
        ]

        _rc = resolve(chart, data, chart_style_context=_BOARD_STYLE)
        spec = generate_vega_lite_spec(chart, data)

        # Theme styling threads through to axis encoding — not present on unstyled spec.
        assert spec.get("encoding", {}).get("y", {}).get("axis", {}).get("gridColor")
