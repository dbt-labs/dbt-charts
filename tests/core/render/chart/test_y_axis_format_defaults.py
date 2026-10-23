"""Tests for quantitative y-axis format defaults via theme cascade.

The theme default ``axis_quantitative.format`` references the ``number``
alias, which resolves to ``.3~s`` — a bounded (3-sig-fig) compact SI format that
drives number formatting for all quantitative y-axes — including ``type: layered``
— without magic column-name inference. The alias lives in ``_base.yaml``.

These tests verify:
1. ``type: layered`` with bar+bar+line layers and million-range y-values renders ".3~s"
2. ``type: bar`` with a quantitative y and only theme style emits ".3~s"
3. Horizontal bar (categorical y-axis) does NOT get ``axis.format`` on the y-axis
"""

import pytest

from dbt_charts.core.compile.config import (
    get_theme_style,
    reset_config,
)
from dbt_charts.core.compile.resolve import resolve
from dbt_charts.core.compile.resolve.style.board import resolve_chart_style_context


@pytest.fixture(autouse=True)
def reset():
    reset_config()
    yield
    reset_config()


class TestLineChartNumberFormatFlowsThroughResolve:
    """style.number_format on a line chart must reach the rendered y-axis."""

    def test_line_number_format_percent_whole_sets_y_axis_format(self):
        """style.number_format: percent_whole must produce axis.format='.0%' on y.

        Regression: _resolve_line set axis_quantitative_format from chart_style_context
        (always '~s') and did NOT inject number_format into the y-axis override.
        The rendered y-axis always showed ~s tick format regardless of the authored
        number_format.
        """
        from dbt_charts.core.compile.models.chart.normalized import LineChart
        from dbt_charts.core.compile.models.query.normalized import SqlQuery
        from dbt_charts.core.compile.models.style.authored import LineChartStylePatch
        from dbt_charts.core.render.chart.vl_field_maps import axis_to_vl

        chart = LineChart(
            id="nrr",
            type="line",
            query=SqlQuery(sql="SELECT 1", source="test"),
            query_name="q",
            x="month",
            y="val",
            style=LineChartStylePatch.model_validate(
                {"axis_y": {"labels": {"format": "percent_whole"}}}
            ),
        )
        data = [
            {"month": "2025-01-01", "val": 1.05},
            {"month": "2025-02-01", "val": 1.08},
        ]
        from dbt_charts.core.compile.models.chart.resolved import ResolvedLineChart

        board_style = resolve_chart_style_context(get_theme_style())
        resolved = resolve(chart, data, board_style)
        assert isinstance(resolved, ResolvedLineChart)
        ay_vl = axis_to_vl(resolved.style.axis_y)
        fmt = ay_vl.get("format")
        assert fmt == ".0%", (
            f"Expected axis.format='.0%' from axis_y={'labels': {'format': percent_whole}}, "
            f"got {fmt!r}. style.number_format must flow through _resolve_line "
            "into the y-axis format, not just into the resolved style label."
        )
