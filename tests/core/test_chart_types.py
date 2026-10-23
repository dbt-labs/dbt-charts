"""Tests for chart type mappings and special chart handling.

Tests the dbt_charts.core.render.vega_lite module for generating
Vega-Lite specifications for all supported chart types.
"""

import pytest

from dbt_charts.core.compile.config import (
    get_theme_style,
)
from dbt_charts.core.compile.errors import CompilationError
from dbt_charts.core.compile.models.chart.authored import (
    AUTHORED_CHART_TYPE_TAGS,
    ChartType,
)
from dbt_charts.core.compile.resolve import resolve
from dbt_charts.core.compile.resolve.style.board import (
    resolve_chart_style_context,
    resolve_style,
)

_BOARD_STYLE = resolve_chart_style_context(get_theme_style())


def test_chart_type_parity_with_authored_union() -> None:
    """ChartType membership must exactly equal the AuthoredChart discriminated union tags.

    Enforces the invariant stated in _type.py: every ChartType member has an
    AuthoredChart variant, and every AuthoredChart variant has a ChartType member.
    Adding a ChartType member with no union variant (or vice versa) fails here.
    """
    assert {ct.value for ct in ChartType} == set(AUTHORED_CHART_TYPE_TAGS)


class TestChartTypeMappings:
    """Tests that chart types map to correct Vega-Lite marks."""

    @pytest.mark.parametrize(
        "chart_type",
        [
            # Raw VL mark types — not in AuthoredChart union
            "circle",
            "square",
            "tick",
            "rule",
            "trail",
            "rect",
            "arc",
            # Composite VL marks — not in AuthoredChart union
            "boxplot",
            "errorbar",
            "errorband",
        ],
    )
    def test_non_authorable_types_rejected_at_compile(self, chart_type: str) -> None:
        """Non-authorable VL mark types are rejected at the compile boundary.

        These raw/composite VL marks are not in the AuthoredChart union, so the
        normalizer refuses them before any render code runs — the guarantee lives
        at the compile boundary, not a render-time guard.
        """
        from dbt_charts.core.compile.normalize.charts import normalize_chart

        chart_def = {
            "type": chart_type,
            "query": {"sql": "SELECT 1", "source": "test"},
            "x": "x_field",
            "y": "y_field",
        }
        with pytest.raises(CompilationError):
            normalize_chart("c1", chart_def, {}, sources={})


class TestRemovedTextChartType:
    """Regression: type: text is not a supported chart type at any level."""

    def test_type_text_raises_compilation_error(self):
        from dbt_charts.core.compile.normalize.charts import normalize_chart

        chart_def = {
            "type": "text",
            "query": {"sql": "SELECT 1", "source": "test"},
            "x": "date",
            "y": "value",
        }
        with pytest.raises(CompilationError, match=r"text"):
            normalize_chart("test_text", chart_def, {}, sources={})


class TestTableChart:
    """Tests for table chart SVG generation."""

    def test_table_renders_svg(self, make_chart):
        from ._engine_render import render_table_svg

        chart = make_chart("table", title="Test Table")
        data = [
            {"name": "Alice", "score": 95},
            {"name": "Bob", "score": 87},
            {"name": "Charlie", "score": 92},
        ]
        chart = resolve(chart, data, chart_style_context=_BOARD_STYLE)
        svg = render_table_svg(
            chart,
            data,
            width=600,
            height=300,
            board_style=resolve_style(get_theme_style()),
        )

        assert svg.startswith("<svg")
        assert 'xmlns="http://www.w3.org/2000/svg"' in svg
        assert "Test Table" in svg
        assert "Alice" in svg
        assert "95" in svg

    def test_table_via_render_chart(self, make_chart):
        import json

        from dbt_charts.core.render.chart.vega_lite import render_chart

        chart = make_chart("table", title="Sales Data")
        data = [
            {"product": "Widget", "revenue": 1234.56},
            {"product": "Gadget", "revenue": 5678.90},
        ]

        from dbt_charts.core.compile.resolve.style.board import (
            resolve_chart_style_context,
            resolve_style,
        )

        svg = render_chart(
            chart,
            resolved_style=resolve_style(get_theme_style()),
            chart_style_context=resolve_chart_style_context(get_theme_style()),
            data=data,
            format="svg",
            width=500,
            height=250,
        )
        assert svg.startswith("<svg")
        assert "Sales Data" in svg
        assert "Widget" in svg

        json_output = render_chart(
            chart,
            resolve_style(get_theme_style()),
            resolve_chart_style_context(get_theme_style()),
            data,
            format="json",
            width=500,
            height=250,
        )
        parsed = json.loads(json_output)
        assert parsed["type"] == "table"
        assert parsed["title"] == "Sales Data"
        assert len(parsed["columns"]) == 2
        assert len(parsed["data"]) == 2

    def test_table_with_empty_data(self, make_chart):
        from ._engine_render import render_table_svg

        chart = make_chart("table", title="Empty Table")
        chart = resolve(chart, [], chart_style_context=_BOARD_STYLE)
        svg = render_table_svg(
            chart,
            [],
            width=400,
            height=200,
            board_style=resolve_style(get_theme_style()),
        )
        assert svg.startswith("<svg")
        assert ">\u2014<" in svg

    def test_table_number_formatting(self, make_chart):
        from ._engine_render import render_table_svg

        chart = make_chart("table")
        data = [{"count": 1234567, "amount": 9876.54}]
        chart = resolve(chart, data, chart_style_context=_BOARD_STYLE)
        svg = render_table_svg(
            chart,
            data,
            width=400,
            height=200,
            board_style=resolve_style(get_theme_style()),
        )
        # An integer column prints whole; other numerics with no explicit
        # format use the theme SI default (.3~s): 9876.54 -> "9.88" + "K".
        assert "1,234,567" in svg
        assert ">9.88</tspan>" in svg

    def test_table_truncates_rows(self, make_chart):
        from ._engine_render import render_table_svg

        chart = make_chart("table")
        data = [{"id": i, "value": i * 10} for i in range(50)]
        chart = resolve(chart, data, chart_style_context=_BOARD_STYLE)
        svg = render_table_svg(
            chart,
            data,
            width=400,
            height=200,
            board_style=resolve_style(get_theme_style()),
        )
        # With default pagination enabled, truncated tables show the paginator.
        assert "dbt-paginator" in svg
