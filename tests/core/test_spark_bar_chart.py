"""Tests for spark bar chart rendering.

Tests the spark_bar chart type which renders compact horizontal bars
for profiler column cards and value distribution displays.
"""

import json
import re
from typing import TYPE_CHECKING
from xml.etree import ElementTree

import pytest

from dbt_charts.core.compile.models.style.resolved import ResolvedStyle
from dbt_charts.core.compile.resolve.style.board import resolve_style_and_context

if TYPE_CHECKING:
    from dbt_charts.core.compile.models.style.context import ChartStyleContext
from dbt_charts.core.compile.config import get_theme_style
from dbt_charts.core.compile.resolve import resolve
from dbt_charts.core.render.chart.vega_lite import render_chart
from dbt_charts.core.render.placeholder import generate_placeholder_data

from ._engine_render import render_spark_bar_svg

_BOARD_STYLE, _BOARD_CTX = resolve_style_and_context(get_theme_style())


def _rects(svg: str) -> list[dict[str, str]]:
    root = ElementTree.fromstring(svg)
    return [element.attrib for element in root.findall("{*}rect")]


class TestSparkBarBasic:
    """Tests for basic spark bar chart rendering."""

    def test_spark_bar_basic(self, make_chart) -> None:
        """Simple spark bar chart renders correctly."""
        data = [
            {"value": "Category A", "frequency": 100},
            {"value": "Category B", "frequency": 75},
            {"value": "Category C", "frequency": 50},
        ]
        chart = resolve(
            make_chart("spark_bar", x="frequency", y="value"),
            data,
            chart_style_context=_BOARD_CTX,
        )

        svg = render_spark_bar_svg(chart, data, board_style=_BOARD_STYLE)

        assert svg.startswith("<svg")
        assert 'xmlns="http://www.w3.org/2000/svg"' in svg
        # Should have bars (rect elements)
        assert "<rect" in svg
        # Should have labels
        assert "Category A" in svg
        assert "Category B" in svg
        assert "Category C" in svg

    def test_spark_bar_with_title(self, make_chart) -> None:
        """Spark bar chart with title renders title."""
        data = [
            {"label": "A", "count": 100},
            {"label": "B", "count": 50},
        ]
        chart = resolve(
            make_chart("spark_bar", x="count", y="label", title="Value Distribution"),
            data,
            chart_style_context=_BOARD_CTX,
        )

        svg = render_spark_bar_svg(chart, data, board_style=_BOARD_STYLE)

        assert "Value Distribution" in svg
        # At the default 200px width (narrow tier), the title uses sans (Inter).
        assert "Inter Variable" in svg

    def test_spark_bar_with_subtitle(self, make_chart) -> None:
        """Spark bar chart subtitle renders in the body font."""
        data = [
            {"label": "A", "count": 100},
            {"label": "B", "count": 50},
        ]
        chart = resolve(
            make_chart(
                "spark_bar",
                x="count",
                y="label",
                title="Value Distribution",
                subtitle="Current quarter only",
            ),
            data,
            chart_style_context=_BOARD_CTX,
        )

        svg = render_spark_bar_svg(chart, data, board_style=_BOARD_STYLE)

        assert "Current quarter only" in svg
        assert "Inter Variable" in svg

    def test_spark_bar_truncates_long_labels(self, make_chart) -> None:
        """Long labels are truncated with ellipsis."""
        data = [
            {
                "value": "This is a very long category name that should be truncated",
                "frequency": 100,
            },
        ]
        chart = resolve(
            make_chart("spark_bar", x="frequency", y="value"),
            data,
            chart_style_context=_BOARD_CTX,
        )

        svg = render_spark_bar_svg(chart, data, width=200, board_style=_BOARD_STYLE)

        # Label should be truncated
        assert "..." in svg
        # Full label should not appear
        assert "This is a very long category name that should be truncated" not in svg


class TestSparkBarSignedValues:
    """Signed magnitude rows use one shared midpoint."""

    def test_reversed_mixed_sign_rows_render_from_shared_midpoint(
        self, make_chart
    ) -> None:
        data = [
            {"value": "Loss", "frequency": -20},
            {"value": "Gain", "frequency": 40},
        ]
        chart = resolve(
            make_chart("spark_bar", x="frequency", y="value"),
            data,
            chart_style_context=_BOARD_CTX,
        )

        rects = _rects(
            render_spark_bar_svg(chart, data, width=300, board_style=_BOARD_STYLE)
        )

        assert len(rects) == 4
        negative_track, negative_fill, positive_track, positive_fill = rects
        midpoint = float(negative_track["x"]) + float(negative_track["width"]) / 2
        assert float(negative_fill["width"]) > 0
        assert float(negative_fill["x"]) + float(
            negative_fill["width"]
        ) == pytest.approx(midpoint)
        assert float(positive_fill["x"]) == pytest.approx(midpoint)
        assert negative_track["x"] == positive_track["x"]
        assert negative_track["width"] == positive_track["width"]

    def test_all_negative_rows_render_left_of_shared_midpoint(self, make_chart) -> None:
        data = [
            {"value": "Small loss", "frequency": -20},
            {"value": "Large loss", "frequency": -40},
        ]
        chart = resolve(
            make_chart("spark_bar", x="frequency", y="value"),
            data,
            chart_style_context=_BOARD_CTX,
        )

        rects = _rects(
            render_spark_bar_svg(chart, data, width=300, board_style=_BOARD_STYLE)
        )

        assert len(rects) == 4
        first_track, first_fill, second_track, second_fill = rects
        midpoint = float(first_track["x"]) + float(first_track["width"]) / 2
        for fill in (first_fill, second_fill):
            assert float(fill["width"]) > 0
            assert float(fill["x"]) + float(fill["width"]) == pytest.approx(midpoint)
        assert first_track["x"] == second_track["x"]
        assert first_track["width"] == second_track["width"]

    def test_all_nonnegative_rows_keep_left_edge_geometry(self, make_chart) -> None:
        data = [
            {"value": "Small gain", "frequency": 20},
            {"value": "Large gain", "frequency": 40},
        ]
        chart = resolve(
            make_chart("spark_bar", x="frequency", y="value"),
            data,
            chart_style_context=_BOARD_CTX,
        )

        rects = _rects(
            render_spark_bar_svg(chart, data, width=300, board_style=_BOARD_STYLE)
        )

        assert len(rects) == 4
        first_track, first_fill, second_track, second_fill = rects
        assert float(first_fill["x"]) == pytest.approx(float(first_track["x"]))
        assert float(second_fill["x"]) == pytest.approx(float(second_track["x"]))
        assert float(second_fill["width"]) == pytest.approx(
            float(second_track["width"])
        )

    def test_positive_finite_row_renders_when_another_value_is_infinite(
        self, make_chart
    ) -> None:
        data = [
            {"value": "Finite", "frequency": 40},
            {"value": "Infinite", "frequency": float("inf")},
        ]
        chart = resolve(
            make_chart("spark_bar", x="frequency", y="value"),
            data,
            chart_style_context=_BOARD_CTX,
        )

        rects = _rects(
            render_spark_bar_svg(chart, data, width=300, board_style=_BOARD_STYLE)
        )

        assert len(rects) == 3
        finite_track, finite_fill, infinite_track = rects
        assert float(finite_fill["x"]) == pytest.approx(float(finite_track["x"]))
        assert float(finite_fill["width"]) == pytest.approx(
            float(finite_track["width"])
        )
        assert infinite_track["y"] != finite_track["y"]


class TestSparkBarDimensions:
    """Tests for spark bar chart dimensions."""

    def test_spark_bar_custom_width(self, make_chart) -> None:
        """Custom width is applied."""
        data = [{"value": "A", "frequency": 100}]
        chart = resolve(
            make_chart("spark_bar", x="frequency", y="value"),
            data,
            chart_style_context=_BOARD_CTX,
        )

        svg = render_spark_bar_svg(chart, data, width=300, board_style=_BOARD_STYLE)

        assert 'width="300.0"' in svg

    def test_spark_bar_auto_height(self, make_chart) -> None:
        """Height auto-calculates based on number of bars."""
        data_3 = [
            {"value": "A", "frequency": 100},
            {"value": "B", "frequency": 75},
            {"value": "C", "frequency": 50},
        ]
        data_5 = data_3 + [
            {"value": "D", "frequency": 25},
            {"value": "E", "frequency": 10},
        ]
        chart = resolve(
            make_chart("spark_bar", x="frequency", y="value"),
            data_3,
            chart_style_context=_BOARD_CTX,
        )

        svg_3 = render_spark_bar_svg(chart, data_3, board_style=_BOARD_STYLE)
        svg_5 = render_spark_bar_svg(chart, data_5, board_style=_BOARD_STYLE)

        # Extract heights from SVG (may be float e.g. "62.0")
        height_3 = float(re.search(r'height="([\d.]+)"', svg_3).group(1))
        height_5 = float(re.search(r'height="([\d.]+)"', svg_5).group(1))

        assert height_5 > height_3


class TestSparkBarMaxBars:
    """Tests for max_bars limiting behavior."""

    def test_spark_bar_max_bars_limit(self, make_chart) -> None:
        """Data is limited to max_bars (default 10)."""
        # Create 15 data points
        data = [{"value": f"Cat {i}", "frequency": 100 - i * 5} for i in range(15)]
        chart = resolve(
            make_chart("spark_bar", x="frequency", y="value"),
            data,
            chart_style_context=_BOARD_CTX,
        )

        svg = render_spark_bar_svg(chart, data, board_style=_BOARD_STYLE)

        # Should show "more" indicator
        assert "+ 5 more" in svg
        # Should not have all categories
        assert "Cat 14" not in svg


class TestSparkBarRenderChart:
    """Tests for render_chart with spark_bar type."""

    def test_render_chart_spark_bar_svg(self, make_chart) -> None:
        """render_chart routes spark_bar to correct renderer."""
        chart = make_chart("spark_bar", x="frequency", y="value")
        data = [
            {"value": "A", "frequency": 100},
            {"value": "B", "frequency": 50},
        ]

        svg = render_chart(chart, _BOARD_STYLE, _BOARD_CTX, data, format="svg")

        assert svg.startswith("<svg")
        assert "<rect" in svg

    def test_render_chart_spark_bar_json(self, make_chart) -> None:
        """render_chart returns JSON format correctly."""
        from dbt_charts.core.compile.config import (
            get_theme_style,
        )

        chart = make_chart("spark_bar", x="frequency", y="value")
        data = [
            {"value": "A", "frequency": 100},
        ]

        rs, ctx = resolve_style_and_context(get_theme_style())
        json_str = render_chart(chart, rs, ctx, data, format="json")

        result = json.loads(json_str)
        assert result["type"] == "spark_bar"
        assert result["data"] == data


class TestSparkBarPlaceholder:
    """Tests for spark bar placeholder data generation."""

    def test_placeholder_data_spark_bar(self) -> None:
        """Placeholder data is generated for spark_bar charts."""
        data = generate_placeholder_data("spark_bar")

        assert len(data) > 0
        # Should have value and frequency fields
        first_row = data[0]
        assert any(isinstance(v, str) for v in first_row.values()), (
            "Should have string category"
        )
        assert any(isinstance(v, (int, float)) for v in first_row.values()), (
            "Should have numeric frequency"
        )


class TestSparkBarEmpty:
    """Tests for empty/edge case handling."""

    def test_spark_bar_empty_data(self, make_chart) -> None:
        """Empty data renders without error."""
        chart = resolve(
            make_chart("spark_bar", x="frequency", y="value"),
            [],
            chart_style_context=_BOARD_CTX,
        )

        svg = render_spark_bar_svg(chart, [], board_style=_BOARD_STYLE)

        assert svg.startswith("<svg")


class TestSparkBarNonNumericValue:
    """spark_bar's x is the magnitude and y is the label — the reverse of every
    other cartesian family. Authoring the cartesian order (x: <text>, y: <number>)
    must raise, not silently render an all-zero-width chart."""

    def test_non_numeric_x_raises_chart_data_error(self, make_chart) -> None:
        """A text x field (the cartesian-order mistake) raises ChartDataError
        instead of rendering every bar at zero width."""
        from dbt_charts.core.diagnostics.chart_data import ChartDataError
        from dbt_charts.core.diagnostics.codes_render import (
            ERR_SPARK_BAR_VALUE_NOT_NUMERIC,
        )

        data = [
            {"product": "Widget", "revenue": 100},
            {"product": "Gadget", "revenue": 50},
        ]
        chart = resolve(
            make_chart("spark_bar", x="product", y="revenue"),
            data,
            chart_style_context=_BOARD_CTX,
        )

        with pytest.raises(ChartDataError) as exc_info:
            render_spark_bar_svg(chart, data, board_style=_BOARD_STYLE)

        assert exc_info.value.code is ERR_SPARK_BAR_VALUE_NOT_NUMERIC
        assert "product" in str(exc_info.value)
        assert "test_spark_bar" in str(exc_info.value)

    def test_numeric_x_with_null_rows_still_renders(self, make_chart) -> None:
        """A value column mixing real numbers with NULLs is sparse real-world
        data, not a data-shape error — it must still render."""
        data = [
            {"value": "A", "count": 100},
            {"value": "B", "count": None},
            {"value": "C", "count": 50},
        ]
        chart = resolve(
            make_chart("spark_bar", x="count", y="value"),
            data,
            chart_style_context=_BOARD_CTX,
        )

        svg = render_spark_bar_svg(chart, data, board_style=_BOARD_STYLE)

        assert svg.startswith("<svg")
        assert "<rect" in svg

    def test_mixed_numeric_and_text_x_raises_chart_data_error(self, make_chart) -> None:
        """A value column that is numeric for some rows and text for others is
        a genuine data-shape bug (not sparse NULLs) — it must raise
        ChartDataError naming the offending value, not crash inside
        ``float()`` as a bare, unclassified ValueError."""
        from dbt_charts.core.diagnostics.chart_data import ChartDataError
        from dbt_charts.core.diagnostics.codes_render import (
            ERR_SPARK_BAR_VALUE_NOT_NUMERIC,
        )

        data = [
            {"value": "A", "count": 100},
            {"value": "B", "count": "N/A"},
        ]
        chart = resolve(
            make_chart("spark_bar", x="count", y="value"),
            data,
            chart_style_context=_BOARD_CTX,
        )

        with pytest.raises(ChartDataError) as exc_info:
            render_spark_bar_svg(chart, data, board_style=_BOARD_STYLE)

        assert exc_info.value.code is ERR_SPARK_BAR_VALUE_NOT_NUMERIC
        assert "count" in str(exc_info.value)
        assert "N/A" in str(exc_info.value)

    def test_typoed_x_column_reports_not_found_not_a_swap(self, make_chart) -> None:
        """A misspelled x column must say the column is absent and list what the
        query returned — NOT tell the author to swap x and y.

        A missing key makes every row.get() return None, which by value alone
        is indistinguishable from an all-NULL column. Without the key check
        this fell through to the swap hint, confidently prescribing a
        reordering of already-correct YAML for what is really a typo."""
        from dbt_charts.core.diagnostics.chart_data import ChartDataError
        from dbt_charts.core.diagnostics.codes_render import (
            ERR_SPARK_BAR_VALUE_FIELD_NOT_FOUND,
        )

        data = [
            {"category": "A", "revenue": 100},
            {"category": "B", "revenue": 50},
        ]
        chart = resolve(
            make_chart("spark_bar", x="revenu", y="category"),
            data,
            chart_style_context=_BOARD_CTX,
        )

        with pytest.raises(ChartDataError) as exc_info:
            render_spark_bar_svg(chart, data, board_style=_BOARD_STYLE)

        assert exc_info.value.code is ERR_SPARK_BAR_VALUE_FIELD_NOT_FOUND
        message = str(exc_info.value)
        assert "revenu" in message
        # Names what the query actually returned, so the typo is obvious.
        assert "category" in message and "revenue" in message
        # Must NOT prescribe the swap — that is the wrong fix for a typo.
        assert "swap" not in message.lower()

    def test_present_but_all_null_x_reports_non_numeric_not_missing(
        self, make_chart
    ) -> None:
        """The mirror of the typo case: a column that IS present but holds only
        NULLs is a real magnitude-channel problem, so it keeps the swap hint
        rather than claiming the column is absent."""
        from dbt_charts.core.diagnostics.chart_data import ChartDataError
        from dbt_charts.core.diagnostics.codes_render import (
            ERR_SPARK_BAR_VALUE_NOT_NUMERIC,
        )

        data = [
            {"category": "A", "revenue": None},
            {"category": "B", "revenue": None},
        ]
        chart = resolve(
            make_chart("spark_bar", x="revenue", y="category"),
            data,
            chart_style_context=_BOARD_CTX,
        )

        with pytest.raises(ChartDataError) as exc_info:
            render_spark_bar_svg(chart, data, board_style=_BOARD_STYLE)

        assert exc_info.value.code is ERR_SPARK_BAR_VALUE_NOT_NUMERIC
        assert "revenue" in str(exc_info.value)

    def test_unset_x_with_no_numeric_column_raises_chart_data_error(
        self, make_chart
    ) -> None:
        """No authored x and no numeric column anywhere in the data leaves
        auto-detection with x_field=None — there is no legitimate spark_bar
        with no magnitude field, so this must raise, not silently paint a
        zero-value bar per row."""
        from dbt_charts.core.diagnostics.chart_data import ChartDataError
        from dbt_charts.core.diagnostics.codes_render import (
            ERR_SPARK_BAR_VALUE_NOT_NUMERIC,
        )

        data = [
            {"product": "Widget", "region": "US"},
            {"product": "Gadget", "region": "EU"},
        ]
        chart = resolve(
            make_chart("spark_bar", x=None, y=None),
            data,
            chart_style_context=_BOARD_CTX,
        )

        with pytest.raises(ChartDataError) as exc_info:
            render_spark_bar_svg(chart, data, board_style=_BOARD_STYLE)

        assert exc_info.value.code is ERR_SPARK_BAR_VALUE_NOT_NUMERIC


class TestSparkBarFieldAutoDetection:
    """Tests for automatic field detection."""

    def test_spark_bar_auto_detect_fields(self, make_chart) -> None:
        """Fields are auto-detected when not specified.

        x=None, y=None here (not make_chart's "x_field"/"y_field" defaults) —
        those literal placeholders aren't columns in this data and previously
        masked this test never exercising real auto-detection at all.
        """
        data = [
            {"category": "A", "count": 100},
            {"category": "B", "count": 50},
        ]
        chart = resolve(
            make_chart("spark_bar", x=None, y=None),
            data,
            chart_style_context=_BOARD_CTX,
        )

        svg = render_spark_bar_svg(chart, data, board_style=_BOARD_STYLE)

        assert svg.startswith("<svg")
        # Should still render bars
        assert "<rect" in svg


class TestSparkBarCountFormatting:
    """Tests for count/frequency formatting."""

    def test_spark_bar_integer_counts(self, make_chart) -> None:
        """Integer counts are displayed without decimal."""
        data = [{"value": "A", "frequency": 1000}]
        chart = resolve(
            make_chart("spark_bar", x="frequency", y="value"),
            data,
            chart_style_context=_BOARD_CTX,
        )

        svg = render_spark_bar_svg(chart, data, board_style=_BOARD_STYLE)

        # Should format with thousands separator
        assert "1,000" in svg

    def test_spark_bar_float_counts(self, make_chart) -> None:
        """Float counts are displayed with one decimal."""
        data = [{"value": "A", "frequency": 75.5}]
        chart = resolve(
            make_chart("spark_bar", x="frequency", y="value"),
            data,
            chart_style_context=_BOARD_CTX,
        )

        svg = render_spark_bar_svg(chart, data, board_style=_BOARD_STYLE)

        assert "75.5" in svg


def _dark_board_style_and_ctx() -> "tuple[ResolvedStyle, ChartStyleContext]":
    from dbt_charts.core.compile.config import get_theme_style

    return resolve_style_and_context(get_theme_style("neon"))


class TestSparkBarDarkTheme:
    """Tests for spark bar dark theme rendering."""

    def test_dark_theme_text_color(self, make_chart) -> None:
        """Dark theme uses light text color via board_style."""
        data = [
            {"value": "Alpha", "count": 80},
            {"value": "Beta", "count": 40},
        ]
        dark_rs, dark_ctx = _dark_board_style_and_ctx()
        chart = resolve(
            make_chart("spark_bar", x="count", y="value"),
            data,
            chart_style_context=dark_ctx,
        )

        svg = render_spark_bar_svg(chart, data, board_style=dark_rs)

        assert svg.startswith("<svg")
        # Dark theme colors should come from theme-derived chart style context
        assert dark_ctx.spark_bar.font.color in svg
        assert dark_ctx.spark_bar.bar.background in svg

    def test_dark_theme_preserves_bars(self, make_chart) -> None:
        """Dark theme still renders bars and labels."""
        data = [
            {"label": "X", "count": 100},
            {"label": "Y", "count": 50},
        ]
        dark_rs, dark_ctx = _dark_board_style_and_ctx()
        chart = resolve(
            make_chart("spark_bar", x="count", y="label"),
            data,
            chart_style_context=dark_ctx,
        )

        svg = render_spark_bar_svg(chart, data, board_style=dark_rs)

        assert "<rect" in svg
        assert "X" in svg
        assert "Y" in svg


class TestSparkBarProfilerStates:
    """Tests for profiler-specific data states: normal, skewed, missing-heavy."""

    def test_normal_distribution(self, make_chart) -> None:
        """Normal distribution renders balanced bars."""
        data = [
            {"value": "A", "frequency": 100},
            {"value": "B", "frequency": 90},
            {"value": "C", "frequency": 80},
            {"value": "D", "frequency": 70},
            {"value": "E", "frequency": 60},
        ]
        chart = resolve(
            make_chart("spark_bar", x="frequency", y="value"),
            data,
            chart_style_context=_BOARD_CTX,
        )

        svg = render_spark_bar_svg(chart, data, board_style=_BOARD_STYLE)

        assert svg.startswith("<svg")
        # All labels present
        for label in ("A", "B", "C", "D", "E"):
            assert label in svg
        # All bars rendered (bg rect + fill rect per bar = 10 rects)
        rect_count = svg.count("<rect")
        assert rect_count == 10  # 5 bg + 5 fill

    def test_skewed_distribution(self, make_chart) -> None:
        """Skewed distribution: one dominant value, rest tiny."""
        data = [
            {"value": "dominant", "frequency": 10000},
            {"value": "rare_a", "frequency": 5},
            {"value": "rare_b", "frequency": 2},
            {"value": "rare_c", "frequency": 1},
        ]
        chart = resolve(
            make_chart("spark_bar", x="frequency", y="value"),
            data,
            chart_style_context=_BOARD_CTX,
        )

        svg = render_spark_bar_svg(chart, data, board_style=_BOARD_STYLE)

        assert svg.startswith("<svg")
        assert "dominant" in svg
        assert "10,000" in svg
        # Small bars should still render (even if very narrow)
        assert "rare_a" in svg
        assert "rare_c" in svg

    def test_missing_heavy_data(self, make_chart) -> None:
        """Missing-heavy: column with high null percentage shows in spark bar."""
        data = [
            {"value": "(null)", "count": 950},
            {"value": "valid_a", "count": 30},
            {"value": "valid_b", "count": 20},
        ]
        chart = resolve(
            make_chart("spark_bar", x="count", y="value"),
            data,
            chart_style_context=_BOARD_CTX,
        )

        svg = render_spark_bar_svg(chart, data, board_style=_BOARD_STYLE)

        assert svg.startswith("<svg")
        assert "(null)" in svg
        assert "950" in svg
        assert "valid_a" in svg

    def test_single_category(self, make_chart) -> None:
        """Single category renders one bar correctly."""
        data = [{"value": "only_value", "count": 500}]
        chart = resolve(
            make_chart("spark_bar", x="count", y="value"),
            data,
            chart_style_context=_BOARD_CTX,
        )

        svg = render_spark_bar_svg(chart, data, board_style=_BOARD_STYLE)

        assert svg.startswith("<svg")
        assert "only_value" in svg
        assert "500" in svg

    def test_settings_override_no_labels(self, make_chart) -> None:
        """Style can hide labels for ultra-compact mode."""
        data = [
            {"value": "A", "count": 100},
            {"value": "B", "count": 50},
        ]
        chart = resolve(
            make_chart(
                "spark_bar",
                x="count",
                y="value",
                style={
                    "spark_bar": {
                        "label": {"visible": False},
                        "count": {"visible": False},
                    }
                },
            ),
            data,
            chart_style_context=_BOARD_CTX,
        )

        svg = render_spark_bar_svg(chart, data, board_style=_BOARD_STYLE)

        assert svg.startswith("<svg")
        # Labels should not appear as text elements (only in data)
        assert ">A<" not in svg
        assert ">B<" not in svg

    def test_settings_override_bar_color(self, make_chart) -> None:
        """Custom bar color via style."""
        data = [{"value": "A", "count": 100}]
        chart = resolve(
            make_chart(
                "spark_bar",
                x="count",
                y="value",
                style={"spark_bar": {"bar": {"color": "#ef4444"}}},
            ),
            data,
            chart_style_context=_BOARD_CTX,
        )

        svg = render_spark_bar_svg(chart, data, board_style=_BOARD_STYLE)

        assert "#ef4444" in svg


def test_render_spark_bar_svg_requires_board_style(make_chart):
    """board_style is keyword-only required — calling without it raises TypeError."""
    import pytest

    data = [{"value": "A", "count": 10}]
    chart = resolve(
        make_chart("spark_bar", x="count", y="value"),
        data,
        chart_style_context=_BOARD_CTX,
    )
    with pytest.raises(TypeError):
        render_spark_bar_svg(chart, data)


def test_spark_bar_subtitle_font_size_none_asserts(make_chart):
    """Assert fires loudly when spark_bar.subtitle.font.size is None (cascade contract)."""
    import dataclasses

    import pytest

    from dbt_charts.core.compile.config import get_theme_style
    from dbt_charts.core.compile.models.primitives import FontStyle
    from dbt_charts.core.compile.models.style.theme import SubtitleStyle
    from dbt_charts.core.compile.resolve.style.board import resolve_chart_style_context
    from dbt_charts.core.diagnostics.chart_data import ChartDataError

    base_ctx = resolve_chart_style_context(get_theme_style())
    sb_override = base_ctx.spark_bar.model_copy(
        update={"subtitle": SubtitleStyle(font=FontStyle(size=None))}
    )
    ctx_custom = dataclasses.replace(base_ctx, spark_bar=sb_override)

    data = [{"label": "A", "count": 100}]
    resolved = resolve(
        make_chart(
            "spark_bar", x="count", y="label", title="Title", subtitle="Subtitle"
        ),
        data,
        chart_style_context=ctx_custom,
    )

    with pytest.raises(ChartDataError, match="spark_bar.subtitle.font.size absent"):
        render_spark_bar_svg(resolved, data, board_style=_BOARD_STYLE)


def test_spark_bar_subtitle_font_size_reads_directly_from_theme(make_chart):
    """subtitle.font.size propagates into SparkBar SVG verbatim — no delta/floor math."""
    import dataclasses

    from dbt_charts.core.compile.config import get_theme_style
    from dbt_charts.core.compile.models.primitives import FontStyle
    from dbt_charts.core.compile.models.style.theme import SubtitleStyle
    from dbt_charts.core.compile.resolve.style.board import resolve_chart_style_context

    distinctive_size = 19.0
    base_ctx = resolve_chart_style_context(get_theme_style())
    spark_bar_override = base_ctx.spark_bar.model_copy(
        update={"subtitle": SubtitleStyle(font=FontStyle(size=distinctive_size))}
    )
    ctx_custom = dataclasses.replace(base_ctx, spark_bar=spark_bar_override)

    data = [{"label": "A", "count": 100}]
    resolved = resolve(
        make_chart(
            "spark_bar",
            x="count",
            y="label",
            title="Distribution",
            subtitle="Test subtitle",
        ),
        data,
        chart_style_context=ctx_custom,
    )

    svg = render_spark_bar_svg(resolved, data, board_style=_BOARD_STYLE)

    assert f'font-size="{distinctive_size}"' in svg, (
        f"Expected subtitle font-size={distinctive_size} verbatim in SparkBar SVG"
    )
