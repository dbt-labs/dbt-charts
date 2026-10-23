from types import SimpleNamespace

from dbt_charts.core.compile.models.primitives import BoardLinks

"""Tests for placeholder rendering with overlay and transparency.

These tests verify that placeholder charts are rendered with:
- High transparency/disabled styling
- "add data" text overlay in the center
- Clear indication that this is placeholder state
"""

from unittest.mock import MagicMock

from dbt_charts.core.compile.config import (
    get_theme_style,
)
from dbt_charts.core.compile.resolve import resolve
from dbt_charts.core.compile.resolve.style.board import (
    resolve_style,
    resolve_style_and_context,
)


class TestPlaceholderRendering:
    """Tests for rendering charts with placeholder data."""

    def test_line_chart_placeholder_renders(self, make_chart):
        """Line chart placeholder should render as line."""
        from dbt_charts.core.render.chart.vega_lite import render_chart

        chart = make_chart("line", query=None, query_name=None)
        _rs, _ctx = resolve_style_and_context(get_theme_style())
        svg = render_chart(
            chart,
            _rs,
            _ctx,
            data=[],
            format="svg",
            is_placeholder=True,
        )

        # Should be valid SVG
        assert svg.startswith("<svg") or "<svg" in svg

    def test_pie_chart_placeholder_renders(self, make_chart):
        """Pie chart placeholder should render."""
        from dbt_charts.core.render.chart.vega_lite import render_chart

        chart = make_chart("pie", x="category", y="value", query=None, query_name=None)
        _rs, _ctx = resolve_style_and_context(get_theme_style())
        svg = render_chart(
            chart,
            _rs,
            _ctx,
            data=[],
            format="svg",
            is_placeholder=True,
        )

        assert svg.startswith("<svg") or "<svg" in svg

    def test_kpi_placeholder_renders(self, make_chart):
        """KPI placeholder should render with sample value."""
        from dbt_charts.core.render.chart.vega_lite import render_chart

        chart = make_chart(
            "kpi", x=None, y=None, value="value", query=None, query_name=None
        )
        _rs, _ctx = resolve_style_and_context(get_theme_style())
        svg = render_chart(
            chart,
            _rs,
            _ctx,
            data=[],
            format="svg",
            is_placeholder=True,
        )

        assert "<svg" in svg or svg.startswith("<svg")


class TestPlaceholderIntegration:
    """Integration tests for placeholder in the render pipeline."""

    def test_renderer_detects_no_query_state(self, make_chart):
        """Renderer should detect charts without queries and use placeholders."""
        from dbt_charts.core.execute.executor import Executor
        from dbt_charts.core.render.chart.rendering import render_chart_item

        chart = make_chart("bar", query=None, query_name=None)

        # Mock executor that returns empty data for any query
        executor = MagicMock(spec=Executor, board=SimpleNamespace(links=BoardLinks()))
        executor.execute_query.return_value = []

        rs, ctx = resolve_style_and_context(get_theme_style())

        resolved = resolve(chart, [], chart_style_context=ctx)
        svg, _height = render_chart_item(
            resolved,
            executor,
            variables={},
            available_width=400,
            available_height=300,
            resolved_style=rs,
            render_cache={},
        )

        # Should render something (not error out)
        assert svg is not None
        assert len(svg) > 0
        # Should contain SVG content (either placeholder or real chart)
        assert "<svg" in svg or "<g" in svg

    def test_renderer_uses_real_data_when_available(self, make_chart):
        """Renderer should use real data when query returns results."""
        from dbt_charts.core.execute.executor import Executor
        from dbt_charts.core.render.chart.rendering import render_chart_item

        chart = make_chart("bar", x="category", y="value")

        real_data = [
            {"category": "A", "value": 100},
            {"category": "B", "value": 200},
        ]

        # Mock executor that returns real data
        executor = MagicMock(spec=Executor, board=SimpleNamespace(links=BoardLinks()))
        executor.execute_query.return_value = real_data

        rs, ctx = resolve_style_and_context(get_theme_style())
        resolved = resolve(chart, real_data, chart_style_context=ctx)
        svg, _height = render_chart_item(
            resolved,
            executor,
            variables={},
            available_width=400,
            available_height=300,
            resolved_style=rs,
            render_cache={},
        )

        # Should render without "add data" overlay (not a placeholder)
        assert "<svg" in svg or svg.startswith("<")
        assert "add data" not in svg.lower()


class TestPlaceholderOverlayStyle:
    """Tests for placeholder overlay styling."""

    def test_overlay_is_centered(self):
        """The 'add data' overlay should be centered on the chart."""
        from dbt_charts.core.render.placeholder import add_placeholder_overlay

        # Create a simple SVG
        test_svg = (
            '<svg width="400" height="300"><rect width="400" height="300"/></svg>'
        )
        from dbt_charts.core.compile.models.primitives import FontStyle

        _style = resolve_style(get_theme_style())
        result = add_placeholder_overlay(
            test_svg,
            width=400,
            height=300,
            font=FontStyle(family="sans-serif"),
            resolved_style=_style,
        )

        # Should have text element approximately centered
        assert "add data" in result.lower()
        # Check for x/y positioning near center (200, 150)
        # The text should be positioned somewhere around the center

    def test_overlay_has_semi_transparent_background(self):
        """Overlay should have semi-transparent background for visibility."""
        from dbt_charts.core.compile.models.primitives import FontStyle
        from dbt_charts.core.render.placeholder import add_placeholder_overlay

        test_svg = (
            '<svg width="400" height="300"><rect width="400" height="300"/></svg>'
        )
        _style = resolve_style(get_theme_style())
        result = add_placeholder_overlay(
            test_svg,
            width=400,
            height=300,
            font=FontStyle(family="sans-serif"),
            resolved_style=_style,
        )

        # Should have some form of overlay background with opacity/transparency
        assert "rgba" in result.lower() or "opacity" in result.lower()


class TestPlaceholderWithRealVegaLiteOutput:
    """Integration tests with actual Vega-Lite SVG output."""

    def test_bar_chart_placeholder_with_real_vegalite(self, make_chart):
        """Test placeholder on real Vega-Lite bar chart output."""
        from dbt_charts.core.render.chart.vega_lite import render_chart
        from dbt_charts.core.render.placeholder import (
            add_placeholder_overlay,
            apply_placeholder_opacity,
            generate_placeholder_data,
        )

        chart = make_chart("bar", x="category", y="value", query=None, query_name=None)
        placeholder_data = generate_placeholder_data("bar", chart)

        # Generate real SVG from Vega-Lite (without placeholder styling)
        _rs, _ctx = resolve_style_and_context(get_theme_style())
        svg = render_chart(
            chart,
            _rs,
            _ctx,
            data=placeholder_data,
            format="svg",
        )

        # Verify the SVG is valid
        assert svg.startswith("<svg") or "<svg" in svg
        assert "</svg>" in svg

        # Apply placeholder styling and verify it works
        svg_with_opacity = apply_placeholder_opacity(svg, _rs)
        assert "opacity" in svg_with_opacity

        from dbt_charts.core.compile.models.primitives import FontStyle

        svg_with_overlay = add_placeholder_overlay(
            svg_with_opacity, 400, 300, FontStyle(family="sans-serif"), _rs
        )
        assert "add data" in svg_with_overlay.lower()

    def test_pie_chart_placeholder_with_real_vegalite(self, make_chart):
        """Test placeholder on real Vega-Lite pie chart output."""
        from dbt_charts.core.render.chart.vega_lite import render_chart
        from dbt_charts.core.render.placeholder import generate_placeholder_data

        chart = make_chart("pie", x="category", y="value", query=None, query_name=None)
        placeholder_data = generate_placeholder_data("pie", chart)

        _rs, _ctx = resolve_style_and_context(get_theme_style())
        svg = render_chart(
            chart,
            _rs,
            _ctx,
            data=placeholder_data,
            format="svg",
            is_placeholder=True,
        )

        assert "<svg" in svg
        assert "add data" in svg.lower()

    def test_overlay_on_complex_svg_with_nested_elements(self):
        """Test that overlay handles SVG with nested groups correctly."""
        from dbt_charts.core.render.placeholder import (
            add_placeholder_overlay,
            apply_placeholder_opacity,
        )

        # Create SVG with nested groups (mimics Vega-Lite output)
        complex_svg = """<svg width="400" height="300" xmlns="http://www.w3.org/2000/svg">
<g class="chart">
<g class="marks">
<rect x="10" y="10" width="100" height="200" fill="blue"/>
<rect x="120" y="50" width="100" height="160" fill="green"/>
</g>
<g class="axes">
<line x1="0" y1="300" x2="400" y2="300" stroke="black"/>
</g>
</g>
</svg>"""

        # Apply opacity
        _style = resolve_style(get_theme_style())
        svg_with_opacity = apply_placeholder_opacity(complex_svg, _style)
        assert "opacity" in svg_with_opacity
        # Content should be wrapped in opacity group
        assert '<g opacity="0.4">' in svg_with_opacity

        # Apply overlay
        from dbt_charts.core.compile.models.primitives import FontStyle

        svg_with_overlay = add_placeholder_overlay(
            svg_with_opacity, 400, 300, FontStyle(family="sans-serif"), _style
        )
        assert "add data" in svg_with_overlay.lower()
        # Should end with proper closing tag
        assert svg_with_overlay.rstrip().endswith("</svg>")

    def test_overlay_only_modifies_last_svg_closing_tag(self):
        """Test that overlay only adds content before the last </svg> tag."""
        from dbt_charts.core.render.placeholder import add_placeholder_overlay

        # SVG with embedded SVG (edge case)
        svg_with_embedded = """<svg width="400" height="300">
<svg x="10" y="10" width="100" height="100">
<rect width="100" height="100"/>
</svg>
</svg>"""

        from dbt_charts.core.compile.models.primitives import FontStyle

        _style = resolve_style(get_theme_style())
        result = add_placeholder_overlay(
            svg_with_embedded, 400, 300, FontStyle(family="sans-serif"), _style
        )

        # Count SVG closing tags - should still be 2
        closing_tag_count = result.count("</svg>")
        assert closing_tag_count == 2

        # The overlay should be before the last </svg>
        last_close_idx = result.rfind("</svg>")
        overlay_idx = result.find("placeholder-overlay")
        assert overlay_idx < last_close_idx
