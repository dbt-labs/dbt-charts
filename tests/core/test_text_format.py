"""Tests for text render output format."""

import dataclasses
import json
from datetime import datetime
from unittest.mock import MagicMock

from dbt_charts.core.compile.compiler import CompileResult
from dbt_charts.core.compile.models.board.normalized import Board, Layout, LayoutItem
from dbt_charts.core.compile.models.chart.normalized import Chart
from dbt_charts.core.compile.models.style.context import ChartStyleContext
from dbt_charts.core.execute.executor import Executor
from dbt_charts.core.render.renderer import render

from ._board_utils import _default_chart_style_context, _default_resolved_style


def _make_executor(data: list[dict]) -> MagicMock:
    """Create a mock executor that returns the given data for any chart.

    ``cache_hit_ats`` must be a real (empty) list, not the default MagicMock
    attribute: render() now draws the board for every format (not just svg),
    and the svg footer/timestamp code iterates this attribute directly.

    ``execute_query`` must also return real data, not the default MagicMock
    attribute: render_warnings comes from that same draw pass (it always
    runs, even for a data-bearing format — see renderer.py), and a stubbed
    ``execute_chart`` alone leaves the draw pass reading a MagicMock as if it
    were a zero-row query result, firing a spurious
    WARN-QUERY-RETURNED-ZERO-ROWS into every test's text output.
    """
    executor = MagicMock(spec=Executor)
    executor.execute_chart.return_value = data
    executor.execute_query.return_value = data
    executor.cache_hit_ats = []
    return executor


def _make_compiled_executor(
    compile_result: CompileResult, data: list[dict]
) -> Executor:
    """Build a real Executor for a ``compile()`` result, returning ``data``
    for every query.

    A real ``Executor`` (not a bare ``MagicMock``) is needed here, not
    ``_make_executor`` above: this helper backs tests that assert on a
    board's *authored* style (chart-local and board-level), which only
    exists once ``compile()`` has produced a real ``Board`` to resolve
    against -- a hand-built ``Board`` + mocked ``execute_chart`` never
    exercises the axis cascade's board/chart-local layers.
    """
    from unittest.mock import Mock

    ok = Mock()
    ok.is_success = True
    ok.data = data
    ok.column_descriptions = None
    ok.resolved_relations = None
    ok.truncated_reason = None
    mock_registry = Mock()
    mock_registry.execute.return_value = ok
    return Executor(
        compile_result.board,
        adapter_registry=mock_registry,
        query_registry=compile_result.query_registry,
    )


def _make_board(
    charts: list[Chart],
    title: str = "Test Board",
    chart_style_context: ChartStyleContext | None = None,
) -> Board:
    """Create a Board with chart items in a rows layout."""
    items = [
        LayoutItem(type="chart", chart=chart, width=600, height=300) for chart in charts
    ]
    return Board(
        id="test-board",
        title=title,
        layout=Layout(type="rows", items=items, width=600, height=600),
        resolved_style=_default_resolved_style(),
        chart_style_context=chart_style_context or _default_chart_style_context(),
        level=1,
    )


class TestTextFormat:
    def test_board_title_as_heading(self, make_chart):
        """Board title renders as markdown H1."""
        data = [{"month": "Jan", "revenue": 100}, {"month": "Feb", "revenue": 200}]
        chart = make_chart("bar", x="month", y="revenue", title="Revenue")
        board = _make_board([chart], title="Revenue Dashboard")
        executor = _make_executor(data)

        result = render(board, executor, format="text").output

        assert isinstance(result, str)
        assert "# Revenue Dashboard" in result

    def test_chart_type_in_heading(self, make_chart):
        """Chart title renders as H2 with chart type."""
        data = [{"month": "Jan", "revenue": 100}]
        chart = make_chart("bar", x="month", y="revenue", title="Sales Chart")
        board = _make_board([chart])
        executor = _make_executor(data)

        result = render(board, executor, format="text").output

        assert "## Sales Chart (bar)" in result

    def test_field_mappings(self, make_chart):
        """Field mappings (x, y) are shown."""
        data = [{"month": "Jan", "revenue": 100}]
        chart = make_chart("bar", x="month", y="revenue")
        board = _make_board([chart])
        executor = _make_executor(data)

        result = render(board, executor, format="text").output

        assert "x: month" in result
        assert "y: revenue" in result

    def test_row_count(self, make_chart):
        """Data summary includes row count."""
        data = [{"x": i, "y": i * 10} for i in range(5)]
        chart = make_chart("line", x="x", y="y")
        board = _make_board([chart])
        executor = _make_executor(data)

        result = render(board, executor, format="text").output

        assert "5 rows" in result

    def test_numeric_range(self, make_chart):
        """Numeric columns show min-max range, formatted by the y-axis's own
        (theme-default "number") format, like a KPI or table cell would.
        """
        data = [
            {"month": "Jan", "revenue": 100},
            {"month": "Feb", "revenue": 500},
            {"month": "Mar", "revenue": 1200},
        ]
        chart = make_chart("line", x="month", y="revenue")
        board = _make_board([chart])
        executor = _make_executor(data)

        result = render(board, executor, format="text").output

        assert "revenue: 100–1.2 K" in result

    def test_numeric_x_shows_range(self, make_chart):
        """A numeric x column shows its min-max range, like y does."""
        data = [{"x": 5, "y": 1}, {"x": 50, "y": 2}, {"x": 25, "y": 3}]
        chart = make_chart("scatter", x="x", y="y")
        board = _make_board([chart])
        executor = _make_executor(data)

        result = render(board, executor, format="text").output

        assert "x: 5–50" in result

    def test_categorical_few_values(self, make_chart):
        """String columns with <=5 values show the values."""
        data = [
            {"region": "East", "sales": 100},
            {"region": "West", "sales": 200},
            {"region": "North", "sales": 150},
        ]
        chart = make_chart("bar", x="region", y="sales")
        board = _make_board([chart])
        executor = _make_executor(data)

        result = render(board, executor, format="text").output

        assert "East" in result
        assert "West" in result
        assert "North" in result

    def test_categorical_many_values(self, make_chart):
        """String columns with >5 values show distinct count only."""
        data = [{"city": f"City{i}", "pop": i * 1000} for i in range(8)]
        chart = make_chart("bar", x="city", y="pop")
        board = _make_board([chart])
        executor = _make_executor(data)

        result = render(board, executor, format="text").output

        assert "8 distinct" in result

    def test_kpi_chart(self, make_chart):
        """KPI charts show only the formatted value — no raw-value parenthetical.

        The exact compaction notation is a theme default and not pinned here
        (dbt-charts/AGENTS.md's "don't pin theme/default values in tests");
        this only asserts the raw cell no longer appears alongside it.
        """
        data = [{"revenue": 4200000}]
        chart = make_chart("kpi", value="revenue", x=None, y=None)
        board = _make_board([chart])
        executor = _make_executor(data)

        result = render(board, executor, format="text").output

        assert "kpi" in result.lower()
        assert "4200000" not in result

    def test_titleless_childless_container_heading_dropped(self, make_chart):
        """An empty, titleless layout container contributes no heading at all."""
        empty_row = Board(
            id="row0",
            title="",
            layout=Layout(type="rows", items=[], width=600, height=100),
            resolved_style=_default_resolved_style(),
            chart_style_context=_default_chart_style_context(),
            level=2,
        )
        outer = Board(
            id="outer",
            title="Outer Dashboard",
            layout=Layout(
                type="rows",
                items=[
                    LayoutItem(type="board", board=empty_row, width=600, height=100)
                ],
                width=600,
                height=600,
            ),
            resolved_style=_default_resolved_style(),
            chart_style_context=_default_chart_style_context(),
            level=1,
        )
        executor = _make_executor([{"x": 1}])

        result = render(outer, executor, format="text").output

        assert "row0" not in result
        assert result == "# Outer Dashboard"

    def test_titleless_container_with_only_container_children_heading_dropped(
        self, make_chart
    ):
        """A titleless row whose only children are containers (not direct
        chart/KPI/table items) drops its own heading, but its content
        children still render at their own depth."""
        data = [{"month": "Jan", "revenue": 100}]
        chart = make_chart("bar", x="month", y="revenue")
        col = Board(
            id="row1_col0",
            title="",
            layout=Layout(
                type="rows",
                items=[LayoutItem(type="chart", chart=chart, width=300, height=300)],
                width=300,
                height=300,
            ),
            resolved_style=_default_resolved_style(),
            chart_style_context=_default_chart_style_context(),
            level=3,
        )
        row = Board(
            id="row1",
            title="",
            layout=Layout(
                type="cols",
                items=[LayoutItem(type="board", board=col, width=300, height=300)],
                width=600,
                height=300,
            ),
            resolved_style=_default_resolved_style(),
            chart_style_context=_default_chart_style_context(),
            level=2,
        )
        outer = Board(
            id="outer",
            title="Outer Dashboard",
            layout=Layout(
                type="rows",
                items=[LayoutItem(type="board", board=row, width=600, height=300)],
                width=600,
                height=600,
            ),
            resolved_style=_default_resolved_style(),
            chart_style_context=_default_chart_style_context(),
            level=1,
        )
        executor = _make_executor(data)

        result = render(outer, executor, format="text").output

        assert "\n## row1\n" not in result
        assert "### row1_col0" in result

    def test_nested_board(self, make_chart):
        """Nested boards use deeper heading levels."""
        data = [{"x": 1, "y": 2}]
        chart = make_chart("bar")
        inner_board = Board(
            id="inner",
            title="Inner Section",
            layout=Layout(
                type="rows",
                items=[LayoutItem(type="chart", chart=chart, width=600, height=300)],
                width=600,
                height=300,
            ),
            resolved_style=_default_resolved_style(),
            chart_style_context=_default_chart_style_context(),
            level=2,
        )
        outer_board = Board(
            id="outer",
            title="Outer Dashboard",
            layout=Layout(
                type="rows",
                items=[
                    LayoutItem(type="board", board=inner_board, width=600, height=300)
                ],
                width=600,
                height=600,
            ),
            resolved_style=_default_resolved_style(),
            chart_style_context=_default_chart_style_context(),
            level=1,
        )
        executor = _make_executor(data)

        result = render(outer_board, executor, format="text").output

        assert "# Outer Dashboard" in result
        assert "## Inner Section" in result

    def test_multiple_charts(self, make_chart):
        """Multiple charts in a board all appear."""
        data = [{"x": 1, "y": 2}]
        chart1 = make_chart("bar", id="chart1", title="Chart One")
        chart2 = make_chart("line", id="chart2", title="Chart Two")
        board = _make_board([chart1, chart2])
        executor = _make_executor(data)

        result = render(board, executor, format="text").output

        assert "Chart One" in result
        assert "Chart Two" in result
        assert "(bar)" in result
        assert "(line)" in result

    def test_max_rows_per_query_summary_shows_truncation(self, make_chart):
        """The data summary states the truncation instead of a bare row count."""
        data = [{"month": f"m{i}", "revenue": i} for i in range(7)]
        board = _make_board([make_chart("bar", x="month", y="revenue")])
        executor = _make_executor(data)

        result = render(board, executor, format="text", max_rows_per_query=2).output

        assert "first 1 and last 1 of 7 rows" in result

    def test_kpi_formatted_value_shows_currency(self, make_chart):
        """A currency-formatted KPI shows only the value as it will be drawn."""
        data = [{"revenue": 210.0}]
        chart = make_chart(
            "kpi",
            value="revenue",
            x=None,
            y=None,
            style={"value": {"format": "currency_whole"}},
        )
        board = _make_board([chart])
        executor = _make_executor(data)

        result = render(board, executor, format="text").output

        assert "$210" in result
        assert "210.0" not in result

    def test_kpi_support_line_shows_formatted_value_and_label(self, make_chart):
        """The support block shows its formatted value alongside its label."""
        data = [{"revenue": 210.0, "delta": 0.05}]
        chart = make_chart(
            "kpi",
            value="revenue",
            x=None,
            y=None,
            support={
                "value": "delta",
                "label": "vs last month",
                "format": "percent",
            },
        )
        board = _make_board([chart])
        executor = _make_executor(data)

        result = render(board, executor, format="text").output

        assert "5.0%" in result
        assert "vs last month" in result

    def test_kpi_no_format_unchanged(self, make_chart):
        """A small, unformatted KPI value keeps the plain comma-grouped digits.

        Below the KPI engine's SI-compaction threshold (1000), an unauthored
        format resolves to no format at all, so there is nothing to show
        alongside the raw value.
        """
        data = [{"revenue": 420}]
        chart = make_chart("kpi", value="revenue", x=None, y=None)
        board = _make_board([chart])
        executor = _make_executor(data)

        result = render(board, executor, format="text").output

        assert "420" in result
        assert "(" not in result.split("value:")[1].splitlines()[0]

    def test_cartesian_numeric_range_uses_axis_format(self, make_chart):
        """A bar chart's numeric range is formatted with its axis number_format."""
        data = [
            {"month": "Jan", "revenue": 80},
            {"month": "Feb", "revenue": 120},
        ]
        chart = make_chart(
            "bar",
            x="month",
            y="revenue",
            style={"number_format": "currency_whole"},
        )
        board = _make_board([chart])
        executor = _make_executor(data)

        result = render(board, executor, format="text").output

        assert "$80" in result
        assert "$120" in result

    def test_cartesian_numeric_range_uses_theme_default_format(self, make_chart):
        """No format authored anywhere still formats the range with the
        theme's own axis_quantitative default (a predefined name, "number"),
        the same as a KPI or table cell would.
        """
        data = [
            {"month": "Jan", "revenue": 80_000},
            {"month": "Feb", "revenue": 120_000},
        ]
        chart = make_chart("bar", x="month", y="revenue")
        board = _make_board([chart])
        executor = _make_executor(data)

        result = render(board, executor, format="text").output

        assert "revenue: 80 K–120 K" in result

    def test_cartesian_numeric_range_line_chart_matches_bar_chart(self, make_chart):
        """The range formatter has no axis-ladder awareness, so a line
        chart's range matches a bar chart's for the same data and format.
        """
        data = [
            {"month": "Jan", "revenue": 80},
            {"month": "Feb", "revenue": 120},
        ]
        chart = make_chart(
            "line", x="month", y="revenue", style={"number_format": "currency"}
        )
        board = _make_board([chart])
        executor = _make_executor(data)

        result = render(board, executor, format="text").output

        assert "revenue: $80–$120" in result

    def test_cartesian_numeric_range_sub_1_number_keeps_milli_reading(self, make_chart):
        """A bare sub-1 "number" range is formatted the same way a KPI or
        table cell reads the same value -- ``format_value(0.2, "number")``
        is "200m" (see ``TestSiSubUnitFloor.test_plain_quantity_fraction_
        keeps_milli_reading`` in test_format_utils.py: the non-money
        sub-unit floor is a deliberate, separately-pinned decision this
        function does not override).
        """
        data = [
            {"month": "Jan", "revenue": 0.2},
            {"month": "Feb", "revenue": 0.8},
        ]
        chart = make_chart("bar", x="month", y="revenue")
        board = _make_board([chart])
        executor = _make_executor(data)

        result = render(board, executor, format="text").output

        assert "revenue: 200m–800m" in result

    def test_cartesian_numeric_range_negative_currency_sign_before_symbol(
        self, make_chart
    ):
        """A range crossing zero on a currency axis composes the sign before
        the symbol: "−$40", not "$−40".
        """
        data = [
            {"month": "Jan", "revenue": -40},
            {"month": "Feb", "revenue": 20},
        ]
        chart = make_chart(
            "bar", x="month", y="revenue", style={"number_format": "currency"}
        )
        board = _make_board([chart])
        executor = _make_executor(data)

        result = render(board, executor, format="text").output

        assert "revenue: −$40–$20" in result
        assert "$−40" not in result

    def test_table_columns_show_resolved_labels_and_formats(self, make_chart):
        """Table columns line lists each column's resolved label and format.

        `columns:` is left unauthored (that authors a pivot's column
        dimension) so the flat table shows every query column, in query
        order, with the ``style.columns`` display override merged in.
        """
        data = [
            {"account": "Acme", "industry": "Tech", "revenue": 100000.0},
        ]
        chart = make_chart(
            "table",
            style={
                "columns": {"revenue": {"format": "currency_whole", "label": "Revenue"}}
            },
        )
        board = _make_board([chart])
        executor = _make_executor(data)

        result = render(board, executor, format="text").output

        assert "columns: account, industry, Revenue (currency_whole)" in result

    def test_table_no_column_style_lists_plain_columns(self, make_chart):
        """A table with no style.columns still lists its columns, unadorned."""
        data = [{"account": "Acme", "revenue": 100000.0}]
        chart = make_chart("table")
        board = _make_board([chart])
        executor = _make_executor(data)

        result = render(board, executor, format="text").output

        assert "columns: account, revenue" in result

    def test_table_columns_over_cap_collapse_to_more_tail(self, make_chart):
        """A wide table's columns line caps at 20 names, then a "+N more" tail."""
        data = [{f"col{i}": i for i in range(25)}]
        chart = make_chart("table")
        board = _make_board([chart])
        executor = _make_executor(data)

        result = render(board, executor, format="text").output

        assert "+5 more" in result
        assert "col19" in result
        assert "col20" not in result

    def test_overlay_layer_y_field_appears_in_field_mappings(self, make_chart):
        """A layered chart's overlay `layers[].y` is listed alongside the base y."""
        data = [
            {"month": "Jan", "created": 10, "solved": 4},
            {"month": "Feb", "created": 20, "solved": 8},
        ]
        chart = make_chart(
            "bar",
            x="month",
            y="created",
            layers=[{"type": "line", "y": "solved"}],
        )
        board = _make_board([chart])
        executor = _make_executor(data)

        result = render(board, executor, format="text").output

        assert "y: created" in result
        assert "y: solved" in result

    def test_overlay_layer_y_field_range_appears_in_data_summary(self, make_chart):
        """The overlay layer's y column gets its own min-max range, distinct
        from the base y column's range."""
        data = [
            {"month": "Jan", "created": 10, "solved": 4},
            {"month": "Feb", "created": 20, "solved": 8},
        ]
        chart = make_chart(
            "bar",
            x="month",
            y="created",
            layers=[{"type": "line", "y": "solved"}],
        )
        board = _make_board([chart])
        executor = _make_executor(data)

        result = render(board, executor, format="text").output

        assert "created: 10–20" in result
        assert "solved: 4–8" in result

    def test_table_shows_row_preview(self, make_chart):
        """A table chart shows its first few rows as a compact markdown table."""
        data = [
            {"account": "Acme", "revenue": 100000.0},
            {"account": "Globex", "revenue": 250000.0},
        ]
        chart = make_chart("table")
        board = _make_board([chart])
        executor = _make_executor(data)

        result = render(board, executor, format="text").output

        assert "Acme" in result
        assert "100000" in result
        assert "Globex" in result

    def test_table_row_preview_caps_and_notes_truncation(self, make_chart):
        """A table wider than the preview cap shows a truncation note, not silence."""
        data = [{"account": f"Acct{i}", "revenue": i} for i in range(10)]
        chart = make_chart("table")
        board = _make_board([chart])
        executor = _make_executor(data)

        result = render(board, executor, format="text").output

        assert "Acct0" in result
        assert "Acct9" not in result
        assert "showing" in result.lower()

    def test_table_hidden_column_omitted_from_summary(self, make_chart):
        """A style.columns visible: false column never reaches the text summary."""
        data = [{"account": "Acme", "internal_id": "abc123", "revenue": 100000.0}]
        chart = make_chart(
            "table", style={"columns": {"internal_id": {"visible": False}}}
        )
        board = _make_board([chart])
        executor = _make_executor(data)

        result = render(board, executor, format="text").output

        assert "columns: account, revenue" in result
        assert "internal_id" not in result


class TestTextFormatFormattingCorrectness:
    """Regression coverage for text-render formatting correctness and error isolation."""

    def test_kpi_sub_unit_currency_not_multiplied(self, make_chart):
        """A sub-$1 KPI must not take the SI spec meant for $500m-scale values."""
        data = [{"amount": 0.45}]
        chart = make_chart(
            "kpi",
            value="amount",
            x=None,
            y=None,
            style={"value": {"format": "currency"}},
        )
        board = _make_board([chart])
        executor = _make_executor(data)

        result = render(board, executor, format="text").output

        assert "$0.45" in result
        assert "$450m" not in result

    def test_cartesian_numeric_range_sub_unit_not_multiplied(self, make_chart):
        """A sub-$1 axis range must not take the SI spec ($500m instead of $0.50)."""
        data = [
            {"month": "Jan", "revenue": 0.5},
            {"month": "Feb", "revenue": 0.9},
        ]
        chart = make_chart(
            "bar", x="month", y="revenue", style={"number_format": "currency"}
        )
        board = _make_board([chart])
        executor = _make_executor(data)

        result = render(board, executor, format="text").output

        assert "$0.50" in result
        assert "$0.90" in result
        assert "$500m" not in result
        assert "$900m" not in result

    def test_board_owned_format_alias_applies_to_kpi(self, make_chart):
        """A board's own style.formats alias resolves in text, not just SVG."""
        ctx = dataclasses.replace(
            _default_chart_style_context(), formats={"myfmt": "$,.0f"}
        )
        data = [{"revenue": 4200}]
        chart = make_chart(
            "kpi", value="revenue", x=None, y=None, style={"value": {"format": "myfmt"}}
        )
        board = _make_board([chart], chart_style_context=ctx)
        executor = _make_executor(data)

        result = render(board, executor, format="text").output

        assert "$4,200" in result
        assert "[chart error:" not in result

    def test_cartesian_numeric_range_uses_axis_y_labels_format(self, make_chart):
        """style.axis_y.labels.format also drives the range format, not just number_format."""
        data = [
            {"month": "Jan", "revenue": 80},
            {"month": "Feb", "revenue": 120},
        ]
        chart = make_chart(
            "bar",
            x="month",
            y="revenue",
            style={"axis_y": {"labels": {"format": "currency_whole"}}},
        )
        board = _make_board([chart])
        executor = _make_executor(data)

        result = render(board, executor, format="text").output

        assert "$80" in result
        assert "$120" in result

    def test_cartesian_axis_y_labels_format_wins_over_number_format(self) -> None:
        """style.axis_y.labels.format outranks style.number_format for the text range.

        Mirrors the axis cascade's own precedence (axis_cascade.py's 13-layer
        order: chart-level number_format is Layer 10, chart-local
        axis_y.labels.format is Layer 13 -- the later layer wins). Authors
        both to different currencies on the same chart so only the correct
        winner's formatting can satisfy the assertion.
        """
        from dbt_charts.core.compile import compile as compile_board

        board_yaml = """\
title: Probe
charts:
  c:
    query: q
    type: bar
    x: month
    y: revenue
    style:
      number_format: currency_full
      axis_y:
        labels:
          format: currency_whole
queries:
  q:
    sql: SELECT * FROM t
    source: test_source
rows:
  - c
"""
        result = compile_board(board_yaml)
        assert result.success and result.board is not None, result.errors

        data = [
            {"month": "Jan", "revenue": 80},
            {"month": "Feb", "revenue": 120},
        ]
        executor = _make_compiled_executor(result, data)

        output = render(result.board, executor, format="text").output

        assert "revenue: $80–$120" in output
        assert "$80.00" not in output

    def test_board_level_axis_quantitative_format_reaches_text_range(self) -> None:
        """A board-level style.charts.axis_quantitative.labels.format applies
        even when the chart itself authors no chart-local style at all.
        """
        from dbt_charts.core.compile import compile as compile_board

        board_yaml = """\
title: Probe
style:
  charts:
    axis_quantitative:
      labels:
        format: currency_whole
charts:
  c:
    query: q
    type: bar
    x: month
    y: revenue
queries:
  q:
    sql: SELECT * FROM t
    source: test_source
rows:
  - c
"""
        result = compile_board(board_yaml)
        assert result.success and result.board is not None, result.errors

        data = [
            {"month": "Jan", "revenue": 80},
            {"month": "Feb", "revenue": 120},
        ]
        executor = _make_compiled_executor(result, data)

        output = render(result.board, executor, format="text").output

        assert "revenue: $80–$120" in output

    def test_cartesian_numeric_range_uses_axis_y_format_config_prefix(self) -> None:
        """An affixed FormatConfig on axis_y.labels.format (not just a plain
        d3/predefined string) still reaches the text render's y-range display.
        """
        from dbt_charts.core.compile import compile as compile_board

        board_yaml = """\
title: Probe
charts:
  c:
    query: q
    type: bar
    x: month
    y: revenue
    style:
      axis_y:
        labels:
          format:
            spec: ",.0f"
            prefix: "€"
queries:
  q:
    sql: SELECT * FROM t
    source: test_source
rows:
  - c
"""
        result = compile_board(board_yaml)
        assert result.success and result.board is not None, result.errors

        data = [
            {"month": "Jan", "revenue": -41500},
            {"month": "Feb", "revenue": 168000},
        ]
        executor = _make_compiled_executor(result, data)

        output = render(result.board, executor, format="text").output

        assert "revenue: −€41,500–€168,000" in output

    def test_cartesian_numeric_range_uses_style_formats_alias_format_config(
        self,
    ) -> None:
        """A ``style.formats`` alias whose value is itself a ``FormatConfig``
        (``number_format.
        """
        from dbt_charts.core.compile import compile as compile_board

        board_yaml = """\
title: Probe
style:
  formats:
    eur:
      spec: ",.0f"
      prefix: "€"
charts:
  c:
    query: q
    type: bar
    x: month
    y: revenue
    style:
      number_format: eur
queries:
  q:
    sql: SELECT * FROM t
    source: test_source
rows:
  - c
"""
        result = compile_board(board_yaml)
        assert result.success and result.board is not None, result.errors

        data = [
            {"month": "Jan", "revenue": 80},
            {"month": "Feb", "revenue": 120},
        ]
        executor = _make_compiled_executor(result, data)

        output = render(result.board, executor, format="text").output

        assert "revenue: €80–€120" in output

    def test_cartesian_numeric_range_uses_inline_number_format_object(self) -> None:
        """The inline ``style.number_format`` object form (a ``FormatConfig``
        authored directly, neither via ``axis_y.labels.format`` nor a
        ``style.formats`` alias) still carries its affix to the text
        render's y-range display.

        axis_cascade.py's chart-format-fallback layer (Layer 10) used to
        collapse ``format_authored_raw`` to the bare resolved spec string,
        losing the affix this specific shape has nowhere else to ride on --
        the other two forms above (a direct axis_y.labels.format, and an
        alias) were each already pinned by a test; this inline-object form
        was the one gap.
        """
        from dbt_charts.core.compile import compile as compile_board

        board_yaml = """\
title: Probe
charts:
  c:
    query: q
    type: bar
    x: month
    y: revenue
    style:
      number_format:
        spec: ",.0f"
        prefix: "EUR "
queries:
  q:
    sql: SELECT * FROM t
    source: test_source
rows:
  - c
"""
        result = compile_board(board_yaml)
        assert result.success and result.board is not None, result.errors

        data = [
            {"month": "Jan", "revenue": 80},
            {"month": "Feb", "revenue": 120},
        ]
        executor = _make_compiled_executor(result, data)

        output = render(result.board, executor, format="text").output

        assert "revenue: EUR 80–EUR 120" in output

    def test_kpi_temporal_value_matches_svg_date_format(self, make_chart):
        """A temporal KPI value renders through the same date_short path as
        SVG, not its raw str() form.
        """
        data = [{"as_of": datetime(2024, 1, 15)}]
        chart = make_chart("kpi", value="as_of", x=None, y=None)
        board = _make_board([chart])
        executor = _make_executor(data)

        result = render(board, executor, format="text").output

        assert "15 Jan 2024" in result
        assert "2024-01-15 00:00:00" not in result

    def test_kpi_percent_range_error_isolates_only_that_chart(self, make_chart):
        """A mis-scaled percent format degrades its own chart in text, not the render."""
        data = [{"revenue": 100, "ratio": 27.6}]
        good = make_chart("kpi", id="good", value="revenue", x=None, y=None)
        bad = make_chart(
            "kpi",
            id="bad",
            value="ratio",
            x=None,
            y=None,
            style={"value": {"format": "percent"}},
        )
        board = _make_board([good, bad])
        executor = _make_executor(data)

        result = render(board, executor, format="text").output

        assert "## good (kpi)" in result
        assert "value: 100" in result
        assert "ERR-PERCENT-RANGE" in result
        assert "[chart error:" in result

    def test_cartesian_percent_range_error_isolates_only_that_chart(self, make_chart):
        """A mis-scaled percent axis format degrades that chart, not the render."""
        data = [
            {"month": "Jan", "ratio": 27.6},
            {"month": "Feb", "ratio": 31.2},
        ]
        chart = make_chart(
            "bar", x="month", y="ratio", style={"number_format": "percent"}
        )
        board = _make_board([chart])
        executor = _make_executor(data)

        result = render(board, executor, format="text").output

        assert "ERR-PERCENT-RANGE" in result
        assert "[chart error:" in result

    def test_json_format_unaffected_by_percent_range_kpi(self, make_chart):
        """--format json still serializes a percent-range-triggering KPI's data.

        Formatting (which can raise ERR-PERCENT-RANGE) runs only on the text
        path -- board_to_dict's shared output for json/yaml/data must stay
        exactly what it was before text formatting existed.
        """
        data = [{"ratio": 27.6}]
        chart = make_chart(
            "kpi", value="ratio", x=None, y=None, style={"value": {"format": "percent"}}
        )
        board = _make_board([chart])
        executor = _make_executor(data)

        result = render(board, executor, format="json").output

        payload = json.loads(result)
        item = payload["items"][0]
        assert "_error" not in item
        assert item["data"] == data
        assert "kpi_text" not in item

    def test_json_format_unaffected_by_percent_range_cartesian(self, make_chart):
        """--format json still serializes a percent-range-triggering bar's data."""
        data = [
            {"month": "Jan", "ratio": 27.6},
            {"month": "Feb", "ratio": 31.2},
        ]
        chart = make_chart(
            "bar", x="month", y="ratio", style={"number_format": "percent"}
        )
        board = _make_board([chart])
        executor = _make_executor(data)

        result = render(board, executor, format="json").output

        payload = json.loads(result)
        item = payload["items"][0]
        assert "_error" not in item
        assert item["data"] == data
        assert "y_range_display" not in item
        assert "format_aliases" not in item

    def test_kpi_headline_glyph_appears_in_text(self, make_chart):
        """An authored headline glyph shows in text, matching chart/kpi.py's draw order."""
        data = [{"revenue": 210.0}]
        chart = make_chart(
            "kpi", value="revenue", x=None, y=None, style={"glyph": {"character": "▲"}}
        )
        board = _make_board([chart])
        executor = _make_executor(data)

        result = render(board, executor, format="text").output

        assert "▲ 210" in result

    def test_kpi_support_missing_column_raises_chart_error(self, make_chart):
        """A support column absent from the query result errors that chart,
        the way the render layer does for the same authoring mistake --
        not a silently dropped value.
        """
        data = [{"revenue": 210.0}]
        chart = make_chart(
            "kpi",
            value="revenue",
            x=None,
            y=None,
            support={"value": "delta", "label": "vs last month"},
        )
        board = _make_board([chart])
        executor = _make_executor(data)

        result = render(board, executor, format="text").output

        assert "[chart error:" in result
        assert "vs last month" not in result

    def test_kpi_zero_rows_raises_chart_error(self, make_chart):
        """A KPI with no data errors that chart in text, matching the SVG
        renderer's own guard (chart/kpi.py raises for the same case) --
        not a silently empty KPI section.
        """
        data: list[dict] = []
        chart = make_chart("kpi", value="revenue", x=None, y=None)
        board = _make_board([chart])
        executor = _make_executor(data)

        result = render(board, executor, format="text").output

        assert "[chart error:" in result


class TestTextFormatWarnings:
    """Render-time warnings surface inline under a `## Warnings` heading."""

    def test_render_warning_appears_in_text_output(self) -> None:
        from unittest.mock import Mock

        from dbt_charts.core.compile import compile as compile_board
        from dbt_charts.core.execute import Executor

        board_yaml = """\
title: Probe
charts:
  c:
    query: q
    type: table
    columns:
      - x
queries:
  q:
    sql: SELECT * FROM t
    source: test_source
rows:
  - c
"""
        result = compile_board(board_yaml)
        assert result.success and result.board is not None, result.errors

        ok = Mock()
        ok.is_success = True
        ok.data = [{"x": 1}, {"x": 2}, {"x": 3}]
        ok.column_descriptions = None
        ok.resolved_relations = None
        ok.truncated_reason = "max_rows"
        mock_registry = Mock()
        mock_registry.execute.return_value = ok
        executor = Executor(
            result.board,
            adapter_registry=mock_registry,
            query_registry=result.query_registry,
        )

        render_result = render(result.board, executor, format="text")

        assert "## Warnings" in render_result.output
        assert "WARN-QUERY-RESULT-TRUNCATED" in render_result.output

    def test_no_warnings_section_when_clean(self, make_chart) -> None:
        # "count" (not "revenue"/"price"/etc.) so the render doesn't also trip
        # WARN-LIKELY-CURRENCY-OR-PERCENT-MISSING-FORMATTER — this test is
        # only about the absence of the `## Warnings` heading itself.
        data = [{"month": "Jan", "count": 100}]
        chart = make_chart("bar", x="month", y="count")
        board = _make_board([chart])
        executor = _make_executor(data)

        result = render(board, executor, format="text").output

        assert "## Warnings" not in result


class TestTextDataFormat:
    """``format="text-data"`` appends grouped-by-query row tables to the text
    summary — the exact rows, not just the compact per-chart summary above."""

    def test_text_data_appends_query_rows(self, make_chart):
        data = [{"month": "Jan", "revenue": 100}, {"month": "Feb", "revenue": 200}]
        chart = make_chart("bar", x="month", y="revenue")
        board = _make_board([chart])
        executor = _make_executor(data)

        result = render(board, executor, format="text-data").output

        assert "## Data" in result
        assert "Jan" in result
        assert "100" in result
        assert "Feb" in result
        assert "200" in result

    def test_plain_text_format_omits_data_section(self, make_chart):
        data = [{"month": "Jan", "revenue": 100}]
        chart = make_chart("bar", x="month", y="revenue")
        board = _make_board([chart])
        executor = _make_executor(data)

        result = render(board, executor, format="text").output

        assert "## Data" not in result

    def test_text_data_groups_shared_query_once(self, make_chart):
        """Two charts sharing a query name get the rows dumped exactly once."""
        data = [{"month": "Jan", "revenue": 100}]
        chart1 = make_chart("bar", id="c1", x="month", y="revenue", query_name="q")
        chart2 = make_chart("line", id="c2", x="month", y="revenue", query_name="q")
        board = _make_board([chart1, chart2])
        executor = _make_executor(data)

        result = render(board, executor, format="text-data").output

        assert result.count("### q") == 1

    def test_text_data_caps_rows_with_explicit_note(self, make_chart):
        """Row dumps are capped by max_rows_per_query, the same cap every
        other row-embedding format uses (head+tail, never silent) — the cap
        an agent dispatch site actually renders with."""
        data = [{"month": f"m{i}", "revenue": i} for i in range(60)]
        chart = make_chart("bar", x="month", y="revenue")
        board = _make_board([chart])
        executor = _make_executor(data)

        result = render(
            board, executor, format="text-data", max_rows_per_query=50
        ).output

        assert "showing first 25 and last 25 of 60 rows" in result
        # The gap row lands exactly between the last head row (m24, head=25)
        # and the first tail row (m35, of the last 25 of 60) -- never
        # mid-chunk, never missing.
        last_head_to_first_tail = result.split("| m24 |", 1)[1].split("| m35 |", 1)[0]
        assert "| ... |" in last_head_to_first_tail
        assert "| m25 |" not in last_head_to_first_tail

    def test_text_data_uncapped_by_default(self, make_chart):
        """No max_rows_per_query means no truncation note and no gap marker —
        matching every other row-embedding format's default."""
        data = [{"month": f"m{i}", "revenue": i} for i in range(60)]
        chart = make_chart("bar", x="month", y="revenue")
        board = _make_board([chart])
        executor = _make_executor(data)

        result = render(board, executor, format="text-data").output

        assert "showing" not in result.split("## Data", 1)[1]
        assert "m59" in result

    def test_text_data_trims_float_noise_past_double_precision(self, make_chart):
        """Raw-precision noise (13356.399999999998, from float arithmetic)
        is trimmed, but real digits are not rounded away."""
        data = [{"month": "Jan", "revenue": 13356.399999999998}]
        chart = make_chart("bar", x="month", y="revenue")
        board = _make_board([chart])
        executor = _make_executor(data)

        result = render(board, executor, format="text-data").output
        data_section = result.split("## Data", 1)[1]

        assert "13356.399999999998" not in data_section
        assert "13356.4" in data_section

    def test_text_data_keeps_exact_cents_on_a_large_value(self, make_chart):
        """Exact values are the point of the Data section — large values
        must not lose precision (a 6-sig-fig round would turn 1234567.89
        into 1234570.0), whether they carry cents, more digits than that,
        or are a whole number stored as a float."""
        data = [
            {"month": "Jan", "revenue": 1234567.89},
            {"month": "Feb", "revenue": 13356789.99},
            {"month": "Mar", "revenue": 1234567.0},
        ]
        chart = make_chart("bar", x="month", y="revenue")
        board = _make_board([chart])
        executor = _make_executor(data)

        result = render(board, executor, format="text-data").output
        data_section = result.split("## Data", 1)[1]

        assert "1234567.89" in data_section
        assert "13356789.99" in data_section
        assert "1234567.0" in data_section

    def test_text_data_rounds_to_significant_digits_not_decimal_places(
        self, make_chart
    ):
        """A small value must round to significant digits, not decimal
        places — round(value, 4) would zero out 0.00004."""
        data = [{"month": "Jan", "conversion_rate": 0.00004}]
        chart = make_chart("bar", x="month", y="conversion_rate")
        board = _make_board([chart])
        executor = _make_executor(data)

        result = render(board, executor, format="text-data").output
        data_section = result.split("## Data", 1)[1]

        assert "0.0 " not in data_section
        assert "4e-05" in data_section
