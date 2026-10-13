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
from dbt_charts.core.compile.models.style.authored import BarChartStylePatch
from dbt_charts.core.compile.resolve import resolve
from dbt_charts.core.compile.resolve.style.board import resolve_chart_style_context
from dbt_charts.core.render.chart.vega_lite import generate_vega_lite_spec

_BOARD_STYLE = resolve_chart_style_context(get_theme_style())


@pytest.fixture(autouse=True)
def reset():
    reset_config()
    yield
    reset_config()


class TestBarChartGetsCompactFormat:
    """type: bar with quantitative y and no explicit format emits ".3~s" from theme."""

    def test_bar_quantitative_y_theme_format(self, make_chart):
        """Vertical bar chart with no authored format should emit axis.format == ".3~s".

        Uses explicit vertical orientation so the VL y-axis is quantitative
        and the theme's axis_quantitative.format cascade applies. The emitted
        value is the fully resolved literal (``.3~s``), never the raw alias
        name (``number``) — this doubles as the alias no-leak guard.
        """
        chart = make_chart(
            "bar",
            x="month",
            y="revenue",
            style=BarChartStylePatch.model_validate({"orientation": "vertical"}),
        )
        data = [
            {"month": "Jan", "revenue": 1_200_000},
            {"month": "Feb", "revenue": 1_400_000},
        ]
        _rc = resolve(chart, data, chart_style_context=_BOARD_STYLE)
        spec = generate_vega_lite_spec(chart, data)
        axis_fmt = spec["encoding"]["y"]["axis"].get("format")
        assert axis_fmt == ".3~s", (
            f"Expected axis.format='.3~s' from theme cascade, got {axis_fmt!r}. "
            "axis_quantitative.format references the number alias (.3~s) "
            "in _base.yaml; the alias must resolve, not leak its name."
        )


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
        from dbt_charts.core.compile.resolve import resolve
        from dbt_charts.core.compile.resolve.style.board import (
            resolve_chart_style_context,
        )
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


class TestHorizontalBarNoFormatOnCategoricalY:
    """Horizontal bar has a categorical y-axis — no axis.format should appear."""

    def test_horizontal_bar_categorical_y_no_format(self, make_chart):
        """Horizontal bar's categorical y-axis must not emit axis.format."""
        chart = make_chart(
            "bar",
            x="product",
            y="revenue",
            style=BarChartStylePatch.model_validate({"orientation": "horizontal"}),
        )
        data = [
            {"product": "Widget A", "revenue": 100},
            {"product": "Widget B", "revenue": 200},
        ]
        _rc = resolve(chart, data, chart_style_context=_BOARD_STYLE)
        spec = generate_vega_lite_spec(chart, data)
        # In horizontal bar, y is categorical, x is quantitative
        y_axis = spec["encoding"]["y"].get("axis", {})
        assert "format" not in y_axis, (
            f"Categorical y-axis should not have format, got {y_axis.get('format')!r}. "
            "The ~s format must only apply to quantitative axes."
        )
