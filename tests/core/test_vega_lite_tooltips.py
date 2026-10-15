"""Tests for Vega-Lite chart tooltip generation.

Split from test_vega_lite.py — tooltip-specific tests.
"""

import re

from dbt_charts.core.compile.config import get_theme_style
from dbt_charts.core.compile.resolve.style.board import resolve_style
from dbt_charts.core.render.chart.vega_lite import generate_vega_lite_spec

from ._engine_render import render_pie_svg

_BOARD_STYLE = resolve_style(get_theme_style())


def _arc_labels(svg: str) -> list[str]:
    return re.findall(r'aria-label="([^"]+)"[^>]*aria-roledescription="arc mark"', svg)


def _mark(spec: dict) -> dict:
    """Get the primary mark from a single-mark spec or layer[0] of a layered one."""
    m = spec.get("mark")
    if isinstance(m, dict) and m:
        return m
    layers = spec.get("layer", [])
    return layers[0].get("mark", {}) if layers else {}


class TestChartTooltips:
    """Tests for chart tooltip generation."""

    def test_bar_chart_has_tooltip_enabled(self, make_chart):
        chart = make_chart("bar", x="month", y="revenue")
        data = [{"month": "Jan", "revenue": 100}]
        spec = generate_vega_lite_spec(chart, data)
        mark = _mark(spec)
        assert mark["type"] == "bar"
        assert mark["tooltip"] is True

    def test_bar_chart_has_tooltip_encoding(self, make_chart):
        chart = make_chart("bar", x="month", y="revenue")
        data = [{"month": "Jan", "revenue": 100}]
        spec = generate_vega_lite_spec(chart, data)
        enc = spec["encoding"]
        xy_fields = {enc.get("x", {}).get("field"), enc.get("y", {}).get("field")}
        assert "month" in xy_fields
        assert "revenue" in xy_fields

    def test_chart_tooltip_includes_color_field(self, make_chart):
        chart = make_chart("bar", x="month", y="revenue", color="region")
        data = [{"month": "Jan", "revenue": 100, "region": "North"}]
        spec = generate_vega_lite_spec(chart, data)
        assert spec["encoding"]["color"]["field"] == "region"

    def test_tooltip_has_human_readable_titles(self, make_chart):
        chart = make_chart("bar", x="order_date", y="total_revenue")
        data = [{"order_date": "2024-01-01", "total_revenue": 1000}]
        spec = generate_vega_lite_spec(chart, data)
        enc = spec["encoding"]
        xy_titles = {enc.get("x", {}).get("title"), enc.get("y", {}).get("title")}
        # Default axis/tooltip titles are tokenized but preserve the column's
        # own casing (lowercase) — an authored x_label/y_label is what gets
        # title case.
        assert "order date" in xy_titles
        assert "total revenue" in xy_titles

    def test_tooltip_quantitative_fields_have_format(self, make_chart):
        chart = make_chart("bar", x="category", y="value")
        data = [{"category": "A", "value": 1234.56}]
        spec = generate_vega_lite_spec(chart, data)
        enc = spec["encoding"]
        measure = next(
            enc[ch]
            for ch in ("x", "y")
            if enc.get(ch, {}).get("type") == "quantitative"
        )
        # Assert a format string is present; don't pin the theme-default literal.
        fmt = measure.get("format")
        assert fmt and isinstance(fmt, str)

    def test_line_chart_has_tooltips(self, make_chart):
        """Foreground line layer shows tooltips; halo layer must not.

        Tooltip *content* lives on the outer spec's ``description`` channel as
        a value/expr def (set by StructuredTooltipFeature), not a per-layer
        ``encoding.tooltip`` array — see emitters/_tooltip.py.
        """
        chart = make_chart("line", x="date", y="value")
        data = [{"date": "2024-01-01", "value": 10}]
        spec = generate_vega_lite_spec(chart, data)
        layers = spec["layer"]
        # A single point is always past the density-auto-on trigger
        # (bake_point_companions), so layers are [halo line, halo point,
        # foreground line, foreground point, hover overlay] -- the
        # foreground line (the one that shows tooltips) is index 2.
        fg = layers[2]
        assert fg["mark"]["type"] == "line"
        assert fg["mark"]["tooltip"] is True
        assert "expr" in spec["encoding"]["description"]["value"]
        # Halo layer is purely visual; tooltips would double up.
        assert layers[0]["mark"]["tooltip"] is False

    def test_scatter_chart_has_tooltips(self, make_chart):
        chart = make_chart("scatter", x="x_value", y="y_value", size="magnitude")
        data = [{"x_value": 1, "y_value": 4, "magnitude": 10}]
        spec = generate_vega_lite_spec(chart, data)
        assert isinstance(spec["mark"], dict)
        assert spec["mark"]["tooltip"] is True
        assert spec["encoding"]["size"]["field"] == "magnitude"
        assert spec["encoding"]["size"]["title"] == "Magnitude"

    def test_area_chart_has_tooltips(self, make_chart):
        """Tooltip *content* lives on the outer spec's ``description`` channel as
        a value/expr def (set by StructuredTooltipFeature), not a per-layer
        ``encoding.tooltip`` array — see emitters/_tooltip.py.
        """
        chart = make_chart("area", x="date", y="value")
        data = [{"date": "2024-01-01", "value": 10}]
        spec = generate_vega_lite_spec(chart, data)
        # Area chart emits [halo, fg, rule] layers; find the fg area layer and its layer dict.
        layers = spec.get("layer", [])
        fg_layer = next(
            (
                lyr
                for lyr in reversed(layers)
                if isinstance(lyr.get("mark"), dict)
                and lyr["mark"].get("type") == "area"
                and lyr["mark"].get("tooltip") is True
            ),
            None,
        )
        assert fg_layer is not None, (
            "Expected a foreground area layer with tooltip=True"
        )
        fg_mark = fg_layer["mark"]
        assert fg_mark.get("type") == "area"
        assert fg_mark.get("tooltip") is True
        assert "expr" in spec["encoding"]["description"]["value"]


class TestSpecialChartTypeTooltips:
    """Tests for tooltips on special chart types."""

    def test_histogram_has_tooltips(self, make_chart):
        chart = make_chart("histogram", x="value", y=None)
        data = [{"value": 10}, {"value": 20}, {"value": 15}]
        spec = generate_vega_lite_spec(chart, data)
        assert isinstance(spec["mark"], dict)
        assert spec["mark"]["tooltip"] is True
        # x channel carries title (bin range entry in aria-label); y is the
        # auto-aggregated count.
        assert spec["encoding"]["x"]["field"] == "value"
        assert spec["encoding"]["x"]["title"] == "value"

    def test_pie_chart_has_tooltips(self, make_chart):
        chart = make_chart("pie", x="category", y="value")
        data = [{"category": "A", "value": 30}, {"category": "B", "value": 70}]
        labels = _arc_labels(render_pie_svg(chart, data))
        assert len(labels) == 2
        assert "A" in labels[0] and "30" in labels[0], labels
        assert "B" in labels[1] and "70" in labels[1], labels

    def test_pie_with_inner_radius_has_tooltips(self, make_chart):
        """Pie chart with inner_radius (donut appearance) has tooltips."""
        chart = make_chart(
            "pie", x="category", y="value", style={"pie": {"inner_radius": 0.5}}
        )
        data = [{"category": "A", "value": 30}, {"category": "B", "value": 70}]
        assert len(_arc_labels(render_pie_svg(chart, data))) == 2

    def test_heatmap_has_tooltips(self, make_chart):
        chart = make_chart("heatmap", x="day", y="hour", color="count")
        data = [{"day": "Mon", "hour": "9am", "count": 10}]
        spec = generate_vega_lite_spec(chart, data)
        assert isinstance(spec["mark"], dict)
        assert spec["mark"]["tooltip"] is True
        # color carries title + format so aria-label renders e.g. "Count: 10.00".
        color_enc = spec["encoding"]["color"]
        assert color_enc["field"] == "count"
        assert color_enc["title"] == "Count"
        # Assert a format string is present; don't pin the theme-default literal.
        assert color_enc.get("format") and isinstance(color_enc["format"], str)


class TestEmptyDataTooltips:
    """Tests for tooltips with edge cases."""

    def test_empty_data_no_tooltip_error(self, make_chart):
        chart = make_chart("bar")
        spec = generate_vega_lite_spec(chart, [])
        # Bar chart: tooltip lives on the bar mark (layer[0] when zero rule is added)
        assert _mark(spec)["tooltip"] is True

    def test_no_encoding_fields_minimal_tooltip(self, make_chart):
        chart = make_chart("bar", x=None, y=None)
        data = [{"a": 1, "b": 2}]
        spec = generate_vega_lite_spec(chart, data)
        # x/y get auto-enriched from data → bar mark + zero-rule layer.
        assert _mark(spec)["tooltip"] is True
