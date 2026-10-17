"""Tests for placeholder data generation.

These tests verify that charts without queries display appropriate
placeholder/dummy data to help users visualize the chart before
connecting real data.
"""

from dbt_charts.core.compile.config import (
    get_default_theme_name,
    get_theme_style,
)
from dbt_charts.core.compile.resolve.style.board import resolve_style
from dbt_charts.core.render.placeholder import generate_placeholder_data


class TestGeneratePlaceholderData:
    """Tests for generate_placeholder_data function."""

    def test_bar_chart_placeholder(self):
        """Bar charts should get categorical + numeric placeholder data."""
        data = generate_placeholder_data("bar")
        assert len(data) >= 3
        assert all("category" in row for row in data)
        assert all("value" in row for row in data)
        # Should be categorical data
        assert isinstance(data[0]["category"], str)
        assert isinstance(data[0]["value"], (int, float))

    def test_line_chart_placeholder(self):
        """Line charts should get time series-like placeholder data."""
        data = generate_placeholder_data("line")
        assert len(data) >= 5
        # Should have x (time-like) and y (numeric) fields
        first_row = data[0]
        assert "x" in first_row or "date" in first_row or "month" in first_row
        assert "value" in first_row or "y" in first_row

    def test_area_chart_placeholder(self):
        """Area charts should get time series-like placeholder data."""
        data = generate_placeholder_data("area")
        assert len(data) >= 5
        # Similar to line chart
        first_row = data[0]
        assert "x" in first_row or "date" in first_row or "month" in first_row

    def test_scatter_chart_placeholder(self):
        """Scatter plots should get two numeric columns."""
        data = generate_placeholder_data("scatter")
        assert len(data) >= 5
        first_row = data[0]
        # Should have x and y numeric values
        assert "x" in first_row
        assert "y" in first_row
        assert isinstance(first_row["x"], (int, float))
        assert isinstance(first_row["y"], (int, float))

    def test_circle_chart_placeholder(self):
        """Circle charts (like scatter) should get two numeric columns."""
        data = generate_placeholder_data("circle")
        assert len(data) >= 5
        first_row = data[0]
        assert "x" in first_row
        assert "y" in first_row

    def test_pie_chart_placeholder(self):
        """Pie charts should get categorical + numeric placeholder data."""
        data = generate_placeholder_data("pie")
        assert len(data) >= 3
        first_row = data[0]
        assert "category" in first_row
        assert "value" in first_row

    def test_heatmap_placeholder(self):
        """Heatmap should get x, y, and value columns."""
        data = generate_placeholder_data("heatmap")
        assert len(data) >= 9  # At least 3x3 grid
        first_row = data[0]
        assert "x" in first_row
        assert "y" in first_row
        assert "value" in first_row

    def test_table_placeholder(self):
        """Tables should get multi-column placeholder data."""
        data = generate_placeholder_data("table")
        assert len(data) >= 3
        first_row = data[0]
        # Should have multiple columns like a typical table
        assert len(first_row) >= 2

    def test_kpi_placeholder(self):
        """KPI charts should get a single value."""
        data = generate_placeholder_data("kpi")
        assert len(data) >= 1
        first_row = data[0]
        assert "value" in first_row
        assert isinstance(first_row["value"], (int, float))

    def test_histogram_placeholder(self):
        """Histogram should get numeric values for binning."""
        data = generate_placeholder_data("histogram")
        assert len(data) >= 20  # Need enough data points for binning
        first_row = data[0]
        assert "value" in first_row
        assert isinstance(first_row["value"], (int, float))

    def test_boxplot_placeholder(self):
        """Boxplot should get numeric values with categories."""
        data = generate_placeholder_data("boxplot")
        assert len(data) >= 15
        first_row = data[0]
        assert "category" in first_row
        assert "value" in first_row

    def test_unknown_chart_type_falls_back_to_bar(self):
        """Unknown chart types should fall back to bar chart data."""
        data = generate_placeholder_data("unknown_type")
        assert len(data) >= 3
        assert "category" in data[0]
        assert "value" in data[0]

    def test_none_chart_type_falls_back_to_bar(self):
        """None chart type should fall back to bar chart data."""
        # Using type: ignore because we're testing edge case with invalid input
        data = generate_placeholder_data(None)  # type: ignore[arg-type]
        assert len(data) >= 3
        assert "category" in data[0]
        assert "value" in data[0]

    def test_empty_string_chart_type_falls_back_to_bar(self):
        """Empty string chart type should fall back to bar chart data."""
        data = generate_placeholder_data("")
        assert len(data) >= 3
        assert "category" in data[0]
        assert "value" in data[0]

    def test_whitespace_only_chart_type_falls_back_to_bar(self):
        """Whitespace-only chart type should fall back to bar chart data."""
        data = generate_placeholder_data("   ")
        assert len(data) >= 3
        assert "category" in data[0]
        assert "value" in data[0]

    def test_case_insensitive_chart_type(self):
        """Chart type matching should be case-insensitive."""
        # Test uppercase
        data_upper = generate_placeholder_data("BAR")
        assert len(data_upper) >= 3
        assert "category" in data_upper[0]

        # Test mixed case
        data_mixed = generate_placeholder_data("LiNe")
        assert len(data_mixed) >= 5
        first_row = data_mixed[0]
        assert "month" in first_row or "x" in first_row

    def test_placeholder_data_is_reproducible(self):
        """Placeholder data should be consistent (not random each call)."""
        data1 = generate_placeholder_data("bar")
        data2 = generate_placeholder_data("bar")
        assert data1 == data2

    def test_chart_config_influences_placeholder(self, make_chart):
        """Placeholder should use chart's x/y field names if provided."""
        chart = make_chart("bar", x="month", y="revenue")
        data = generate_placeholder_data("bar", chart=chart)
        # Should use the field names from chart config
        assert len(data) >= 3
        first_row = data[0]
        assert "month" in first_row
        assert "revenue" in first_row


class TestPlaceholderOverlayText:
    """Tests for placeholder overlay text."""

    def test_overlay_text_is_user_friendly(self):
        """Overlay text should be user-friendly."""
        # The theme leaves it `auto`; resolving the style words it.
        theme = get_theme_style(get_default_theme_name())
        assert theme.placeholder.overlay.text == "auto"
        assert "data" in resolve_style(theme).placeholder.overlay.text.lower()
