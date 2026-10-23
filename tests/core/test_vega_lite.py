from types import SimpleNamespace

from dbt_charts.core.compile.models.primitives import BoardLinks

"""Tests for Vega-Lite chart generation.

Tests the dbt_charts.core.render.vega_lite module for generating
Vega-Lite specifications from chart definitions and data.

Tooltip-specific tests: see test_vega_lite_tooltips.py
Axis/format tests: see test_vega_lite_axes.py
Arc/pie tests: see test_vega_lite_arc.py
Table/error handling tests: see test_vega_lite_table.py
"""

import json
from unittest.mock import MagicMock

import pytest
from pydantic import TypeAdapter as _TypeAdapter

from dbt_charts.core.compile.config import get_theme_style
from dbt_charts.core.compile.models.chart.normalized import Chart
from dbt_charts.core.compile.models.primitives import FormatTable
from dbt_charts.core.compile.resolve.style.board import (
    resolve_chart_style_context,
    resolve_style,
)

_CHART_ADAPTER: _TypeAdapter[Chart] = _TypeAdapter(Chart)
from dbt_charts.core.compile.resolve import resolve
from dbt_charts.core.render.chart.vega_lite import render_chart
from dbt_charts.core.render.utils import slug_to_text

from ._engine_render import render_chart_svg

_BOARD_CONTEXT = resolve_chart_style_context(get_theme_style())


class TestSlugToText:
    """Tests for slug_to_text helper (tokenization only — no casing)."""

    def test_snake_case(self):
        """Test snake_case: words lowercased (no title casing)."""
        assert slug_to_text("revenue_by_month") == "Revenue by month"

    def test_kebab_case(self):
        """Test kebab-case: words lowercased."""
        assert slug_to_text("revenue-by-month") == "Revenue by month"

    def test_single_word(self):
        """Test single word: lowercase, no casing."""
        assert slug_to_text("revenue") == "Revenue"

    def test_empty_string(self):
        """Test empty string."""
        assert slug_to_text("") == ""

    def test_unit_suffix_usd(self):
        assert slug_to_text("revenue_usd") == "Revenue ($)"

    def test_unit_suffix_pct(self):
        assert slug_to_text("yoy_growth_pct") == "YoY growth (%)"

    def test_count_suffix_cnt(self):
        assert slug_to_text("customer_cnt") == "Customer (Count)"

    def test_count_suffix_num(self):
        assert slug_to_text("employee_num") == "Employee (#)"

    def test_count_suffix_qty(self):
        assert slug_to_text("order_qty") == "Order (Qty)"

    def test_abbreviation_arr(self):
        assert slug_to_text("arr") == "ARR"

    def test_abbreviation_in_middle(self):
        assert slug_to_text("avg_deal_size") == "Avg deal size"

    def test_mixed_abbreviation_and_suffix(self):
        assert slug_to_text("arr_usd") == "ARR ($)"

    def test_single_token_suffix_not_stripped(self):
        """Single-token inputs are not treated as suffixes."""
        assert slug_to_text("usd") == "Usd"

    def test_spaces_in_input(self):
        assert slug_to_text("contribution margin") == "Contribution margin"

    def test_chart_id_with_suffix(self):
        """Field slugs get suffix treatment but not title casing."""
        assert slug_to_text("revenue_usd") == "Revenue ($)"
        assert slug_to_text("customer_cnt") == "Customer (Count)"
        assert slug_to_text("growth_pct") == "Growth (%)"


class TestSingleChartPropertyValidation:
    """Tests for single-chart mark and encoding property validation."""

    def test_bar_chart_errors_on_duplicate_categories(self, make_chart):
        from dbt_charts.core.diagnostics.chart_data import ChartDataError

        chart = make_chart("bar", x="product", y="revenue")
        data = [
            {"product": "Widget A", "revenue": 100},
            {"product": "Widget A", "revenue": 150},
        ]

        with pytest.raises(
            ChartDataError, match="requires pre-aggregated data with at most one row"
        ):
            render_chart_svg(chart, data)

    def test_bar_chart_errors_on_duplicate_category_series(self, make_chart):
        from dbt_charts.core.diagnostics.chart_data import ChartDataError

        chart = make_chart("bar", x="product", y="revenue", color="region")
        data = [
            {"product": "Widget A", "region": "North", "revenue": 100},
            {"product": "Widget A", "region": "North", "revenue": 150},
        ]

        with pytest.raises(
            ChartDataError, match="one row per plotted key \\(product, region\\)"
        ):
            render_chart_svg(chart, data)


class TestGenerateLineChart:
    """Tests for line chart generation."""

    def test_line_chart_errors_on_duplicate_x_without_series(self, make_chart):
        from dbt_charts.core.diagnostics.chart_data import ChartDataError

        chart = make_chart("line", x="date", y="revenue")
        data = [
            {"date": "2024-01-01", "revenue": 10},
            {"date": "2024-01-01", "revenue": 20},
        ]

        with pytest.raises(
            ChartDataError, match="Line chart 'test_line' requires pre-aggregated data"
        ):
            render_chart_svg(chart, data)


class TestGenerateScatterChart:
    """Tests for scatter chart generation."""


class TestGenerateAreaChart:
    """Tests for area chart generation."""


class TestGenerateBarChart:
    """Tests for bar chart generation."""


class TestGenerateTableChart:
    """Tests for table chart generation."""


class TestRenderChart:
    """Tests for render_chart function."""

    def test_render_json_returns_dbt_charts_format(self, make_chart):
        """Test rendering chart to JSON returns dbt charts format, not Vega-Lite."""
        from dbt_charts.core.compile.config import (
            get_theme_style,
        )

        chart = make_chart("bar", x="month", y="revenue")
        data = [{"month": "Jan", "revenue": 100}]

        result = render_chart(
            chart,
            resolve_style(get_theme_style()),
            resolve_chart_style_context(get_theme_style()),
            data,
            format="json",
        )

        parsed = json.loads(result)

        # Should return dbt charts format, NOT Vega-Lite
        assert parsed["type"] == "bar"
        assert parsed["x"] == "month"
        assert parsed["y"] == "revenue"
        assert parsed["data"] == data

        # Should NOT contain Vega-Lite specific fields
        assert "$schema" not in parsed
        assert "mark" not in parsed
        assert "encoding" not in parsed

    def test_render_json_bar_chart_with_all_fields(self, make_chart):
        """Test bar chart JSON includes all relevant fields."""
        from dbt_charts.core.compile.config import (
            get_theme_style,
        )

        chart = make_chart(
            "bar",
            x="month",
            y="revenue",
            color="region",
            title="Revenue by Month",
            x_label="Month",
            y_label="Revenue ($)",
        )
        data = [
            {"month": "Jan", "revenue": 100, "region": "North"},
            {"month": "Feb", "revenue": 200, "region": "South"},
        ]

        result = render_chart(
            chart,
            resolve_style(get_theme_style()),
            resolve_chart_style_context(get_theme_style()),
            data,
            format="json",
            width=800,
            height=300,
        )

        parsed = json.loads(result)

        assert parsed["type"] == "bar"
        assert parsed["title"] == "Revenue by Month"
        assert parsed["x"] == "month"
        assert parsed["y"] == "revenue"
        assert parsed["color"] == "region"
        assert parsed["x_label"] == "Month"
        assert parsed["y_label"] == "Revenue ($)"
        assert parsed["width"] == 800
        assert parsed["height"] == 300
        assert parsed["data"] == data

    def test_render_json_table_chart(self, make_chart):
        """Test table chart JSON includes columns."""
        from dbt_charts.core.compile.config import (
            get_theme_style,
        )

        chart = make_chart("table", title="Sales Data")
        data = [
            {"month": "Jan", "revenue": 100},
            {"month": "Feb", "revenue": 200},
        ]

        result = render_chart(
            chart,
            resolve_style(get_theme_style()),
            resolve_chart_style_context(get_theme_style()),
            data,
            format="json",
        )

        parsed = json.loads(result)

        assert parsed["type"] == "table"
        assert parsed["title"] == "Sales Data"
        assert parsed["columns"] == ["month", "revenue"]
        assert parsed["data"] == data

    def test_render_json_kpi_chart(self, make_chart):
        """Test KPI chart JSON includes value and style.value.format."""
        from dbt_charts.core.compile.config import (
            get_theme_style,
        )

        chart = make_chart(
            "kpi",
            value="total_revenue",
            label="Total Revenue",
            style={"value": {"format": "currency_full"}},
        )
        data = [{"total_revenue": 1000000}]

        result = render_chart(
            chart,
            resolve_style(get_theme_style()),
            resolve_chart_style_context(get_theme_style()),
            data,
            format="json",
        )

        parsed = json.loads(result)

        assert parsed["type"] == "kpi"
        assert parsed["label"] == "Total Revenue"
        assert parsed["value"] == "total_revenue"
        assert parsed["style"]["value"]["format"] == "currency_full"
        assert parsed["data"] == data

    def test_render_json_line_chart(self, make_chart):
        """Test line chart JSON format."""
        from dbt_charts.core.compile.config import (
            get_theme_style,
        )

        chart = make_chart("line", x="date", y="value")
        data = [
            {"date": "2024-01-01", "value": 10},
            {"date": "2024-01-02", "value": 20},
        ]

        result = render_chart(
            chart,
            resolve_style(get_theme_style()),
            resolve_chart_style_context(get_theme_style()),
            data,
            format="json",
        )

        parsed = json.loads(result)

        assert parsed["type"] == "line"
        assert parsed["x"] == "date"
        assert parsed["y"] == "value"
        assert parsed["data"] == data

    def test_render_json_without_dimensions(self, make_chart):
        """Test JSON output omits dimensions when not provided."""
        from dbt_charts.core.compile.config import (
            get_theme_style,
        )

        chart = make_chart("bar", x="x", y="y")
        data = [{"x": 1, "y": 2}]

        result = render_chart(
            chart,
            resolve_style(get_theme_style()),
            resolve_chart_style_context(get_theme_style()),
            data,
            format="json",
        )

        parsed = json.loads(result)

        # Width and height should not be present when not provided
        assert "width" not in parsed
        assert "height" not in parsed

    def test_render_json_with_orientation(self, make_chart):
        """Test JSON output includes style with orientation."""
        from dbt_charts.core.compile.config import (
            get_theme_style,
        )

        chart = make_chart(
            "bar", x="category", y="value", style={"orientation": "horizontal"}
        )
        data = [{"category": "A", "value": 10}]

        result = render_chart(
            chart,
            resolve_style(get_theme_style()),
            resolve_chart_style_context(get_theme_style()),
            data,
            format="json",
        )

        parsed = json.loads(result)

        assert parsed["style"]["orientation"] == "horizontal"

    def test_render_json_with_style(self, make_chart):
        """Test JSON output includes style."""
        from dbt_charts.core.compile.config import (
            get_theme_style,
        )

        chart = make_chart(
            "bar",
            x="category",
            y="value",
            style={"legend": {"visible": False}},
        )
        data = [{"category": "A", "value": 10}]

        result = render_chart(
            chart,
            resolve_style(get_theme_style()),
            resolve_chart_style_context(get_theme_style()),
            data,
            format="json",
        )

        parsed = json.loads(result)

        assert parsed["style"] == {"legend": {"visible": False}}

    def test_render_json_empty_data_table(self, make_chart):
        """Test table chart JSON with empty data includes empty columns array."""
        from dbt_charts.core.compile.config import (
            get_theme_style,
        )

        chart = make_chart("table", title="Empty Sales Data")
        data: list = []

        result = render_chart(
            chart,
            resolve_style(get_theme_style()),
            resolve_chart_style_context(get_theme_style()),
            data,
            format="json",
        )

        parsed = json.loads(result)

        assert parsed["type"] == "table"
        assert parsed["title"] == "Empty Sales Data"
        assert parsed["columns"] == []  # Should always include columns for tables
        assert parsed["data"] == []

    def test_render_json_empty_data_bar_chart(self, make_chart):
        """Test bar chart JSON with empty data."""
        from dbt_charts.core.compile.config import (
            get_theme_style,
        )

        chart = make_chart("bar", x="month", y="revenue")
        data: list = []

        result = render_chart(
            chart,
            resolve_style(get_theme_style()),
            resolve_chart_style_context(get_theme_style()),
            data,
            format="json",
        )

        parsed = json.loads(result)

        assert parsed["type"] == "bar"
        assert parsed["x"] == "month"
        assert parsed["y"] == "revenue"
        assert parsed["data"] == []

    def test_render_json_with_subtitle_and_notes(self, make_chart):
        """Test JSON includes subtitle and notes fields."""
        from dbt_charts.core.compile.config import (
            get_theme_style,
        )

        chart = make_chart(
            "bar",
            x="month",
            y="revenue",
            title="Sales Chart",
            subtitle="North region only",
            notes="Shows monthly sales data",
        )
        data = [{"month": "Jan", "revenue": 100}]

        result = render_chart(
            chart,
            resolve_style(get_theme_style()),
            resolve_chart_style_context(get_theme_style()),
            data,
            format="json",
        )

        parsed = json.loads(result)

        assert parsed["type"] == "bar"
        assert parsed["title"] == "Sales Chart"
        assert parsed["subtitle"] == "North region only"
        assert parsed["notes"] == "Shows monthly sales data"
        assert parsed["x"] == "month"
        assert parsed["y"] == "revenue"
        # Verify internal fields are NOT included
        assert "id" not in parsed
        assert "query" not in parsed
        assert "query_name" not in parsed

    def test_render_chart_item_resolves_jinja_in_subtitle(self, make_chart):
        """Chart rendering should resolve Jinja variables in subtitles."""
        from dbt_charts.core.compile.config import (
            get_theme_style,
        )
        from dbt_charts.core.compile.resolve import resolve
        from dbt_charts.core.compile.template.jinja import resolve_jinja_template
        from dbt_charts.core.execute.executor import Executor
        from dbt_charts.core.render.chart.rendering import render_chart_item

        chart = make_chart(
            "bar",
            x="month",
            y="revenue",
            title="Revenue",
            subtitle="Region: {{ region }}",
        )
        executor = MagicMock(spec=Executor, board=SimpleNamespace(links=BoardLinks()))
        executor.execute_query.return_value = [{"month": "Jan", "revenue": 100}]

        from dbt_charts.core.compile.resolve.style.board import (
            resolve_style_and_context,
        )

        rs, rs_context = resolve_style_and_context(get_theme_style())
        variables = {"region": "West"}
        # Resolve Jinja in text fields before resolve(), mirroring what the sizing
        # pass does via _require_resolved in layout_sizing.py.
        subtitle = chart.subtitle
        if subtitle and "{{" in subtitle:
            subtitle = resolve_jinja_template(subtitle, variables, strict=False)
            chart = chart.model_copy(update={"subtitle": subtitle})
        resolved = resolve(
            chart,
            [{"month": "Jan", "revenue": 100}],
            chart_style_context=rs_context,
        )
        svg, _height = render_chart_item(
            resolved,
            executor,
            variables=variables,
            available_width=400,
            available_height=300,
            resolved_style=rs,
            render_cache={},
        )

        assert "Region: West" in svg

    def test_render_json_with_link(self, make_chart):
        """Test JSON output includes link field."""
        from dbt_charts.core.compile.config import (
            get_theme_style,
        )

        chart = make_chart(
            "bar",
            x="category",
            y="value",
            title="Clickable Chart",
            link="/detail?cat={{ x }}",
        )
        data = [{"category": "A", "value": 100}]

        result = render_chart(
            chart,
            resolve_style(get_theme_style()),
            resolve_chart_style_context(get_theme_style()),
            data,
            format="json",
        )

        parsed = json.loads(result)

        assert parsed["type"] == "bar"
        assert parsed["title"] == "Clickable Chart"
        assert parsed["link"] == "/detail?cat={{ x }}"


class TestDataTypeNormalization:
    """Tests for data type normalization."""


class TestChartWithTitle:
    """Tests for chart titles."""


class TestFormatKpiParts:
    """Tests for format_kpi_parts — decomposing formatted KPI values."""

    _FORMATS = FormatTable(
        aliases={"money": "$,.2f", "pct": ".1%", "tilde": ",.2s"}, currency="USD"
    )

    def test_currency_format(self):
        from dbt_charts.core.compile.format_value import format_kpi_parts

        prefix, number, suffix = format_kpi_parts(
            1234567.89, "currency_full", self._FORMATS
        )
        assert prefix == "$"
        assert number == "1,234,567.89"
        assert suffix == ""

    def test_percent_format(self):
        from dbt_charts.core.compile.format_value import format_kpi_parts

        prefix, number, suffix = format_kpi_parts(0.123, "percent", self._FORMATS)
        assert prefix == ""
        assert number == "12.3"
        assert suffix == "%"

    def test_compact_format(self):
        from dbt_charts.core.compile.format_value import format_kpi_parts

        prefix, number, suffix = format_kpi_parts(1500000, "number", self._FORMATS)
        assert prefix == ""
        assert number == "1.5"
        assert suffix == "M"

    def test_format_config_prefix_suffix(self):
        from dbt_charts.core.compile.format_value import format_kpi_parts
        from dbt_charts.core.compile.models.primitives import FormatConfig

        prefix, number, suffix = format_kpi_parts(
            1234, FormatConfig(spec=",.0f", prefix="$", suffix=" USD")
        )
        assert prefix == "$"
        assert number == "1,234"
        assert suffix == "USD"

    def test_no_format(self):
        # KPI path (no default_format): below-threshold exact-digit fallback.
        from dbt_charts.core.compile.format_value import format_kpi_parts

        prefix, number, suffix = format_kpi_parts(42000, None)
        assert prefix == ""
        assert number == "42,000"
        assert suffix == ""

    def test_none_value(self):
        from dbt_charts.core.compile.format_value import format_kpi_parts

        prefix, number, suffix = format_kpi_parts(None, "currency", self._FORMATS)
        assert number == "\u2014"

    def test_percent_number_format(self):
        # percent_number: value is already the whole-number percent (no \u00d7100)
        from dbt_charts.core.compile.format_value import format_kpi_parts, format_value

        prefix, number, suffix = format_kpi_parts(12.8, "percent_number")
        assert prefix == ""
        assert number == "12.8"
        assert suffix == "%"
        # format_value round-trip
        assert format_value(12.8, "percent_number") == "12.8%"
        assert format_value(0.128, "percent_number") == "0.1%"  # NOT "12.8%"

    def test_percent_number_delta_format(self):
        from dbt_charts.core.compile.format_value import format_value

        assert format_value(3.2, "percent_number_delta") == "+3.2%"
        # PREDEFINED_NATIVE bypasses d3_format, so its own lambda is
        # responsible for the house minus glyph (U+2212).
        assert format_value(-1.4, "percent_number_delta") == "−1.4%"

    def test_percent_number_none(self):
        from dbt_charts.core.compile.format_value import format_kpi_parts, format_value

        assert format_value(None, "percent_number") == "\u2014"
        _, number, _ = format_kpi_parts(None, "percent_number")
        assert number == "\u2014"


class TestRenderKpiSvg:
    """Tests for the custom KPI SVG renderer."""

    @staticmethod
    def _resolve(chart, data):

        return resolve(chart, data, chart_style_context=_BOARD_CONTEXT)

    def test_basic_kpi_svg(self, make_chart):
        from dbt_charts.core.compile.config import (
            get_theme_style,
        )
        from dbt_charts.core.compile.models.style.authored import (
            KpiChartStylePatch,
        )

        from ._engine_render import render_kpi_svg

        data = [{"revenue": 42000}]
        # Override the narrative-compact default so the test asserts on a
        # full-precision number that doesn't depend on KPI's notation register.
        chart = self._resolve(
            make_chart(
                "kpi",
                value="revenue",
                label="Total Revenue",
                style=KpiChartStylePatch.model_validate({"value": {"format": ",.0f"}}),
            ),
            data,
        )
        svg = render_kpi_svg(
            chart,
            data,
            width=300,
            height=120,
            board_style=resolve_style(get_theme_style()),
        )
        assert "<svg" in svg
        assert "Total Revenue" in svg
        assert "42,000" in svg

    def test_negative_kpi_below_compact_threshold_uses_house_minus(self):
        """format_kpi_parts' bare-digit fallback (no authored format, value
        below the SI-compact threshold) is a plain Python f-string — it must
        emit the house minus glyph (U+2212) like every other numeric surface,
        not the ASCII hyphen a raw f-string defaults to."""
        import re

        from dbt_charts.core.compile.models.chart.normalized import KpiChart

        from ._engine_render import render_kpi_svg

        data = [{"delta": -131}]
        chart = self._resolve(
            KpiChart(id="k", type="kpi", value="delta", label="Delta"),
            data,
        )
        svg = render_kpi_svg(
            chart,
            data,
            width=300,
            height=120,
            board_style=resolve_style(get_theme_style()),
        )
        assert "−131" in svg
        assert not re.search(r">-\d", svg)

    def test_kpi_support_line_renders_with_neutral_explainer(self, make_chart):
        from dbt_charts.core.compile.config import (
            get_theme_style,
        )
        from dbt_charts.core.compile.models.chart.authored import (
            KpiSupportConfig,
        )

        from ._engine_render import render_kpi_svg

        data = [{"revenue": 42000, "delta_pct": 0.124}]
        chart = self._resolve(
            make_chart(
                "kpi",
                value="revenue",
                label="Total Revenue",
                support=KpiSupportConfig(
                    value="delta_pct",
                    label="vs LQ",
                    glyph="▲",
                ),
                style={"support": {"tone": "positive", "format": {"spec": "+.1%"}}},
            ),
            data,
        )
        svg = render_kpi_svg(
            chart,
            data,
            width=300,
            height=180,
            board_style=resolve_style(get_theme_style()),
        )

        # Support row carries glyph + value; trailing explainer stays neutral.
        assert "▲" in svg
        assert "12.4%" in svg
        assert "vs LQ" in svg
        # Tone applies semantic green to the support value/glyph.
        # positive.solid resolves to #00884d (D-029: tone palettes via palette role).
        assert "#00884d" in svg
        # Trailing explainer is rendered in the muted/neutral color, NOT the
        # tone color. Pin both halves of the contract: the explainer fill
        # equals the theme's muted slot AND differs from the tone hex.
        import re

        positive_tone = "#00884d"
        muted_hex = resolve_style(get_theme_style()).variables.font.color.lower()
        match = re.search(
            r'<tspan[^>]* fill="(#[0-9A-Fa-f]{6})">[^<]*vs LQ',
            svg,
        )
        assert match, f"explainer 'vs LQ' tspan not found in svg: {svg}"
        explainer_fill = match.group(1).lower()
        assert explainer_fill == muted_hex, (
            f"explainer should use muted slot {muted_hex}, got {explainer_fill}"
        )
        assert explainer_fill != positive_tone.lower(), (
            f"explainer should not carry tone color, got {explainer_fill}"
        )

    def test_kpi_svg_currency_prefix(self, make_chart):
        from dbt_charts.core.compile.config import (
            get_theme_style,
        )
        from dbt_charts.core.compile.models.style.authored import (
            KpiChartStylePatch,
        )

        from ._engine_render import render_kpi_svg

        data = [{"revenue": 5000}]
        chart = self._resolve(
            make_chart(
                "kpi",
                value="revenue",
                label="Revenue",
                style=KpiChartStylePatch.model_validate(
                    {"value": {"format": "currency_full"}}
                ),
            ),
            data,
        )
        svg = render_kpi_svg(
            chart,
            data,
            width=300,
            height=120,
            board_style=resolve_style(get_theme_style()),
        )
        # $ should appear in its own tspan (separate from the number)
        assert "<tspan" in svg
        assert "$" in svg
        assert "5,000.00" in svg

    def test_kpi_svg_percent_suffix(self, make_chart):
        from dbt_charts.core.compile.config import (
            get_theme_style,
        )
        from dbt_charts.core.compile.models.style.authored import (
            KpiChartStylePatch,
        )

        from ._engine_render import render_kpi_svg

        data = [{"rate": 0.85}]
        chart = self._resolve(
            make_chart(
                "kpi",
                value="rate",
                label="Rate",
                style=KpiChartStylePatch.model_validate(
                    {"value": {"format": "percent"}}
                ),
            ),
            data,
        )
        svg = render_kpi_svg(
            chart,
            data,
            width=300,
            height=120,
            board_style=resolve_style(get_theme_style()),
        )
        assert "%" in svg
        assert "85.0" in svg

    def test_kpi_svg_card_background(self, make_chart):
        from dbt_charts.core.compile.config import (
            get_theme_style,
        )
        from dbt_charts.core.compile.models.style.authored import (
            KpiChartStylePatch,
        )

        from ._engine_render import render_kpi_svg

        data = [{"count": 100}]
        chart = self._resolve(
            make_chart(
                "kpi",
                value="count",
                label="Count",
                style=KpiChartStylePatch(background="#eeeeee"),
            ),
            data,
        )
        svg = render_kpi_svg(
            chart,
            data,
            width=300,
            height=120,
            board_style=resolve_style(get_theme_style()),
        )
        # Explicit chart-style.background still draws a rect, but no implicit
        # corner radius — the quantitative-text-object design has no card chrome.
        assert "<rect" in svg
        assert 'fill="#eeeeee"' in svg

    def test_kpi_svg_left_aligns_title_and_value(self, make_chart):
        from dbt_charts.core.compile.config import (
            get_theme_style,
        )

        from ._engine_render import render_kpi_svg

        data = [{"count": 100}]
        chart = self._resolve(make_chart("kpi", value="count", label="Count"), data)
        svg = render_kpi_svg(
            chart,
            data,
            width=300,
            height=120,
            board_style=resolve_style(get_theme_style()),
        )
        assert 'text-anchor="start"' in svg
        assert 'text-anchor="middle"' not in svg

    def test_kpi_text_x_honors_content_padding_left(self, make_chart):
        """Value, label, and support all render at x = kpi.content_padding.left.

        Two contracts in one:

        1. The renderer reads its horizontal inset from the theme's
           ``kpi.content_padding.left`` rather than hardcoding a literal.
           Override that value and the rendered x follows.
        2. All three text rows (value, label, support) share the same x, so
           the text block reads as one left-aligned column. This is what
           lets the KPI line up with page headers / text / chart content
           above and below in board layout, where the surrounding card_padding
           wrapper is applied identically to KPIs and to header content.
        """
        import xml.etree.ElementTree as ET

        from dbt_charts.core.compile.config import (
            get_theme_style,
        )
        from dbt_charts.core.compile.models.chart.authored import KpiSupportConfig

        from ._engine_render import render_kpi_svg

        expected_x = float(get_theme_style().charts.kpi.content_padding.left)

        data = [{"revenue": 42000, "delta_pct": 0.05}]
        chart = self._resolve(
            make_chart(
                "kpi",
                value="revenue",
                label="Total Revenue",
                support=KpiSupportConfig(value="delta_pct", label="vs LQ"),
            ),
            data,
        )
        svg = render_kpi_svg(
            chart,
            data,
            width=300,
            height=120,
            board_style=resolve_style(get_theme_style()),
        )
        root = ET.fromstring(svg)
        ns = {"svg": "http://www.w3.org/2000/svg"}
        text_nodes = root.findall("svg:text", ns)
        assert len(text_nodes) >= 3, "expected value + label + support <text> rows"
        xs = {float(node.attrib["x"]) for node in text_nodes}
        assert xs == {expected_x}, (
            f"KPI <text x=…> values {xs} must all equal "
            f"kpi.content_padding.left ({expected_x}) — the renderer should "
            "honor the theme and all three rows should share one x"
        )
        for tspan in root.findall("svg:text/svg:tspan[@x]", ns):
            assert float(tspan.attrib["x"]) == expected_x, (
                f"KPI label <tspan x={tspan.attrib['x']!r}> must match the "
                f"parent <text> x ({expected_x})"
            )

    def test_kpi_svg_keeps_extra_gap_between_value_and_title(self, make_chart):
        """Value comes first, title second — the engine keeps its 14px minimum gap."""
        import xml.etree.ElementTree as ET

        from ._engine_render import render_kpi_svg

        data = [{"count": 100}]
        chart = self._resolve(make_chart("kpi", value="count", label="Count"), data)
        svg = render_kpi_svg(
            chart,
            data,
            width=300,
            height=140,
            board_style=resolve_style(get_theme_style()),
        )
        root = ET.fromstring(svg)
        ns = {"svg": "http://www.w3.org/2000/svg"}
        ys = [float(node.attrib["y"]) for node in root.findall("svg:text", ns)]
        assert len(ys) >= 2
        # ys[0] is the value baseline, ys[1] is the title baseline.
        assert ys[1] - ys[0] >= 14.0

    def test_kpi_svg_value_font_size_equals_authored_value(self, make_chart):
        import xml.etree.ElementTree as ET

        from dbt_charts.core.compile.config import (
            get_theme_style,
        )

        from ._engine_render import render_kpi_svg

        data = [{"count": 100}]
        chart = self._resolve(make_chart("kpi", value="count", label="Count"), data)
        svg = render_kpi_svg(
            chart,
            data,
            width=352,
            height=48,
            board_style=resolve_style(get_theme_style()),
        )
        root = ET.fromstring(svg)
        ns = {"svg": "http://www.w3.org/2000/svg"}
        # Value is the first text element in the new value/label/support order.
        value_text = root.findall("svg:text", ns)[0]
        value_tspan = value_text.find("svg:tspan", ns)
        assert value_tspan is not None
        # No floor: rendered value font == authored theme value, no clamping applied.
        authored = float(get_theme_style().charts.kpi.value.font.size)
        assert float(value_tspan.attrib["font-size"]) == authored

    def test_kpi_svg_expands_height_when_requested_slot_is_too_small(self, make_chart):
        import xml.etree.ElementTree as ET

        from dbt_charts.core.compile.config import (
            get_theme_style,
        )

        from ._engine_render import render_kpi_svg

        data = [{"count": 100}]
        chart = self._resolve(make_chart("kpi", value="count", label="Count"), data)
        svg = render_kpi_svg(
            chart,
            data,
            width=352,
            height=48,
            board_style=resolve_style(get_theme_style()),
        )
        root = ET.fromstring(svg)
        assert float(root.attrib["height"]) > 48.0

    def test_kpi_svg_errors_on_empty_data(self, make_chart):
        from dbt_charts.core.compile.config import (
            get_theme_style,
        )
        from dbt_charts.core.diagnostics.chart_data import ChartDataError

        from ._engine_render import render_kpi_svg

        # resolve with dummy data, then call with empty data to test the error
        data = [{"revenue": 1}]
        chart = self._resolve(make_chart("kpi", value="revenue"), data)
        with pytest.raises(ChartDataError, match="no data"):
            render_kpi_svg(
                chart,
                [],
                width=300,
                height=120,
                board_style=resolve_style(get_theme_style()),
            )

    def test_kpi_svg_errors_on_multi_row(self, make_chart):
        from dbt_charts.core.compile.config import (
            get_theme_style,
        )
        from dbt_charts.core.diagnostics.chart_data import ChartDataError

        from ._engine_render import render_kpi_svg

        multi_data = [{"revenue": 1}, {"revenue": 2}]
        chart = self._resolve(make_chart("kpi", value="revenue"), multi_data)
        with pytest.raises(ChartDataError, match="expects exactly 1 row"):
            render_kpi_svg(
                chart,
                multi_data,
                width=300,
                height=120,
                board_style=resolve_style(get_theme_style()),
            )

    def test_render_chart_routes_kpi_to_custom_svg(self, make_chart):
        """render_chart with format='svg' should use the custom KPI renderer."""
        from dbt_charts.core.compile.config import (
            get_theme_style,
        )

        chart = make_chart("kpi", value="val", label="My KPI")
        svg = render_chart(
            chart,
            resolved_style=resolve_style(get_theme_style()),
            chart_style_context=resolve_chart_style_context(get_theme_style()),
            data=[{"val": 999}],
            format="svg",
            width=300,
            height=120,
        )
        assert "<svg" in svg
        assert "My KPI" in svg
        assert "999" in svg
        # Should NOT contain Vega-Lite text mark attributes
        assert '"type": "text"' not in svg
