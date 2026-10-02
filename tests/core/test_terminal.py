"""Tests for terminal rendering functionality."""

import logging

import pytest

from dbt_charts.core.diagnostics.chart_data import ChartDataError
from dbt_charts.core.render.terminal_charts import (
    _terminal_y_field,
    render_chart_terminal,
    render_kpi_terminal,
    render_table_terminal,
)


def test_render_kpi_terminal(make_chart):
    """Test KPI rendering to terminal."""
    chart = make_chart("kpi", value="revenue", label="Total Revenue")

    data = [{"revenue": 1234567.89}]

    output = render_kpi_terminal(chart, data, "Total Revenue", formats=None)
    # Terminal must consume the finalized title argument; deriving a fallback
    # here would reopen the normalized-to-render boundary.
    assert "Total Revenue" in output
    assert "1234567" in output or "1,234,567" in output


def test_render_kpi_terminal_honors_authored_format_prefix_and_spec(make_chart):
    """An authored style.value.format (spec + prefix) must reach terminal output, as it
    does in the SVG renderer.
    """
    chart = make_chart(
        "kpi",
        value="revenue",
        label="Revenue",
        style={"value": {"format": {"spec": ",.0f", "prefix": "EUR "}}},
    )

    output = render_kpi_terminal(chart, [{"revenue": 154500}], "Revenue", formats=None)

    assert "EUR 154,500" in output
    assert "154,500.00" not in output


def test_render_kpi_terminal_spec_less_affix_matches_svg_finalized_format(
    make_chart,
):
    """A spec-less FormatConfig ({prefix: "EUR "} alone, no spec authored) on a large
    KPI value paints like the SVG renderer.
    """
    from dbt_charts.core.compile.format import finalize_kpi_value_format
    from dbt_charts.core.compile.models.primitives import FormatConfig
    from dbt_charts.core.render.format_utils import format_value

    value_format = FormatConfig(prefix="EUR ")
    value = -41500

    chart = make_chart(
        "kpi",
        value="revenue",
        label="Revenue",
        style={"value": {"format": {"prefix": "EUR "}}},
    )
    output = render_kpi_terminal(chart, [{"revenue": value}], "Revenue", formats=None)

    finalized = finalize_kpi_value_format(value_format, float(value))
    expected = format_value(value, finalized, None)

    assert expected in output
    assert "42k" in expected  # sanity: the SI compaction SVG also applies
    assert "41500" not in output  # ungrouped digits must never reappear


def test_render_kpi_terminal_money_prefix_below_one_falls_back(make_chart):
    """A spec-less money prefix ({prefix: "£"} alone) on a sub-$1 KPI value
    must print exact digits ("£0.67"), never d3's SI milli reading glued onto
    the number ("£670m", 670 million pounds for 67 pence) -- the same
    sub-unit-floor rule format_kpi_parts/format_value apply everywhere else,
    reached through `finalize_kpi_value_format` (too small to compact, so it
    stays spec-less) exactly like the large-value case above reaches the SI
    branch. `render/chart/kpi.py`'s SVG painter runs the identical
    `finalize_kpi_value_format` + `format_kpi_parts` pipeline on this same
    chart.format (only the tuple-vs-string return shape differs from
    terminal's `format_value`), so asserting both here pins SVG and terminal
    to the same digits.
    """
    from dbt_charts.core.compile.format import finalize_kpi_value_format
    from dbt_charts.core.compile.models.primitives import FormatConfig
    from dbt_charts.core.render.format_utils import format_kpi_parts, format_value

    value_format = FormatConfig(prefix="£")
    value = 0.67

    chart = make_chart(
        "kpi",
        value="revenue",
        label="Revenue",
        style={"value": {"format": {"prefix": "£"}}},
    )
    output = render_kpi_terminal(chart, [{"revenue": value}], "Revenue", formats=None)

    finalized = finalize_kpi_value_format(value_format, value)
    terminal_expected = format_value(value, finalized, None)
    svg_prefix, svg_number, svg_suffix = format_kpi_parts(value, finalized)
    svg_expected = svg_prefix + svg_number + svg_suffix

    assert terminal_expected == "£0.67"
    assert svg_expected == "£0.67"
    assert terminal_expected in output
    assert "670m" not in output


@pytest.mark.parametrize(
    ("value_format", "value", "painted"),
    [
        ({"spec": ",.0f", "prefix": "EUR "}, 154500, "EUR 154,500"),
        ({"spec": ",.0f", "suffix": " EUR"}, -41500, "\u221241,500 EUR"),
        (".3~s", 1234567, "1.23M"),
        ("currency", 0.45, "$0.45"),
    ],
)
def test_render_kpi_terminal_paints_the_same_string_as_the_text_render(
    make_chart, value_format, value, painted
):
    """--format terminal and --format text (board_to_dict's KPI text) format the
    headline through the same flat-string path.
    """
    from dbt_charts.core.compile.config import get_theme_style
    from dbt_charts.core.compile.resolve import resolve
    from dbt_charts.core.compile.resolve.style.board import resolve_chart_style_context
    from dbt_charts.core.render.board_to_dict import _kpi_text_parts

    chart = make_chart(
        "kpi",
        value="revenue",
        label="Revenue",
        style={"value": {"format": value_format}},
    )
    data = [{"revenue": value}]
    resolved = resolve(
        chart,
        data,
        chart_style_context=resolve_chart_style_context(get_theme_style()),
    )

    assert _kpi_text_parts(resolved, data[0], None)["value"] == painted
    assert painted in render_kpi_terminal(chart, data, "Revenue", formats=None)


def test_render_kpi_terminal_survives_a_style_block_without_value(make_chart):
    """A KPI authoring a style block that never touches `value:` (e.g. only `align`)
    must not crash.
    """
    chart = make_chart(
        "kpi",
        value="revenue",
        label="Revenue",
        style={"align": "center"},
    )

    output = render_kpi_terminal(chart, [{"revenue": 154500}], "Revenue", formats=None)

    assert "154,500" in output
    assert "Error" not in output


def test_render_kpi_terminal_resolves_a_style_formats_alias(make_chart):
    """style.value.format authored as a bare style.formats alias name (not
    a literal spec) must resolve through the board's alias map, not fall
    through to the inline-d3 path and hand the alias name straight to d3,
    which would turn the KPI into an inline error line.
    """
    chart = make_chart(
        "kpi",
        value="revenue",
        label="Revenue",
        style={"value": {"format": "eur"}},
    )

    output = render_kpi_terminal(
        chart, [{"revenue": 154500}], "Revenue", formats={"eur": ",.0f"}
    )

    assert "154,500" in output
    assert "Error" not in output


def test_render_kpi_terminal_raises_on_missing_column(make_chart):
    """value: is always a column reference — missing column raises ChartDataError."""
    chart = make_chart("kpi", value="revenue", label="Rev")
    data = [{"total": 100}]
    with pytest.raises(ChartDataError, match="revenue.*not found|not found.*revenue"):
        render_kpi_terminal(chart, data, "Rev", formats=None)


def test_render_kpi_terminal_honors_empty_final_display_title(make_chart):
    chart = make_chart("kpi", value="revenue", label="{{ region }}")

    output = render_kpi_terminal(
        chart,
        [{"revenue": 100}],
        "",
        formats=None,
    )

    assert "{{ region }}" not in output


def test_render_table_terminal(make_chart):
    """Test table rendering to terminal."""
    chart = make_chart("table", title="Test Table")

    data = [
        {"name": "Alice", "age": 30, "score": 95.5},
        {"name": "Bob", "age": 25, "score": 87.0},
    ]

    output = render_table_terminal(chart, data, "Test Table")
    assert "Test Table" in output or "table" in output.lower()
    # Should contain column names or data
    assert "Alice" in output or "Bob" in output or "name" in output.lower()


def test_render_chart_terminal_bar(make_chart):
    """Test bar chart rendering to terminal."""
    chart = make_chart("bar", x="month", y="revenue", title="Revenue by Month")

    data = [
        {"month": "Jan", "revenue": 1000},
        {"month": "Feb", "revenue": 1500},
        {"month": "Mar", "revenue": 1200},
    ]

    # This will use fallback if plotext is not available
    output = render_chart_terminal(
        chart, data, "Revenue by Month", width=80, height=20, formats=None
    )
    assert output  # Should produce some output
    assert "Revenue by Month" in output or "chart" in output.lower()


def test_render_chart_terminal_uses_final_display_title(make_chart):
    chart = make_chart("bar", x="month", y="revenue", title="{{ region }} revenue")
    data = [{"month": "Jan", "revenue": 1000}]

    output = render_chart_terminal(
        chart,
        data,
        "West revenue",
        width=80,
        height=20,
        formats=None,
    )

    assert "West revenue" in output
    assert "{{ region }}" not in output


def test_render_chart_terminal_line(make_chart):
    """Test line chart rendering to terminal."""
    chart = make_chart("line", x="date", y="value", title="Value Over Time")

    data = [
        {"date": "2024-01-01", "value": 10},
        {"date": "2024-01-02", "value": 15},
        {"date": "2024-01-03", "value": 12},
    ]

    output = render_chart_terminal(
        chart, data, "Value Over Time", width=80, height=20, formats=None
    )
    assert output  # Should produce some output


def test_render_chart_terminal_empty_data(make_chart):
    """Test chart rendering with empty data."""
    chart = make_chart("bar", x="x", y="y")

    data = []

    output = render_chart_terminal(chart, data, "", formats=None)
    assert output  # Should handle empty data gracefully


def test_terminal_y_field_warns_when_dropping_extra_series(make_chart, caplog):
    """Terminal rendering should warn when a multi-series chart is reduced to one y."""
    chart = make_chart("line", x="month", y=["revenue", "cost", "profit"])

    with caplog.at_level(logging.WARNING):
        y_field = _terminal_y_field(chart)

    assert y_field == "revenue"
    assert "only supports one y series" in caplog.text
    assert "dropping 2 additional series" in caplog.text


def test_render_chart_item_terminal_isolates_a_bare_dbt_charts_error(make_chart):
    """A DbtChartsError from the terminal chart pipeline degrades to an inline line.

    Pre-existing and unrelated to resolve-time isolation: the terminal branch
    of ``render()`` sits outside every try/except, so an exception escaping
    here propagated all the way out as a crash. ExecutionError/RenderError were
    already softened one level further out; a *bare* DbtChartsError was not,
    which is why that is the family this pins.
    """
    from unittest.mock import MagicMock

    from dbt_charts.core.diagnostics import ERR_INTERNAL
    from dbt_charts.core.diagnostics.base import DbtChartsError
    from dbt_charts.core.execute.executor import Executor
    from dbt_charts.core.render.terminal import render_chart_item_terminal

    chart = make_chart("bar", x="month", y="revenue")
    executor = MagicMock(spec=Executor)
    executor.execute_chart.side_effect = DbtChartsError.from_code(
        ERR_INTERNAL, message="terminal pipeline blew up"
    )

    output = render_chart_item_terminal(chart, executor, {}, 80, 20, formats=None)

    assert output.startswith(f"[Error rendering {chart.id}:")
    assert "terminal pipeline blew up" in output


def test_render_chart_item_terminal_still_isolates_bug_class_errors(make_chart):
    """The four bug-class types stay caught — this widened, it did not replace."""
    from unittest.mock import MagicMock

    from dbt_charts.core.execute.executor import Executor
    from dbt_charts.core.render.terminal import render_chart_item_terminal

    chart = make_chart("bar", x="month", y="revenue")
    executor = MagicMock(spec=Executor)
    executor.execute_chart.side_effect = ValueError("bad value")

    output = render_chart_item_terminal(chart, executor, {}, 80, 20, formats=None)

    assert output.startswith(f"[Error rendering {chart.id}:")
