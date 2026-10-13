"""Tests for JSON render output format."""

import json
from unittest.mock import MagicMock

from dbt_charts.core.compile.models.board.normalized import Board, Layout, LayoutItem
from dbt_charts.core.compile.models.chart.normalized import Chart
from dbt_charts.core.execute.executor import Executor
from dbt_charts.core.render.renderer import render

from ._board_utils import _default_chart_style_context, _default_resolved_style
from ._svg_render import PINNED_RENDER_TIME


def _make_executor(data: list[dict]) -> MagicMock:
    """Create a mock executor that returns the given data for any chart.

    ``cache_hit_ats`` must be a real (empty) list, not the default MagicMock
    attribute: render() now draws the board for every format (not just svg),
    and the svg footer/timestamp code iterates this attribute directly.
    """
    executor = MagicMock(spec=Executor)
    executor.render_with_range_filters.return_value = None
    executor.execute_chart.return_value = data
    executor.cache_hit_ats = []
    executor.rendered_at = PINNED_RENDER_TIME
    return executor


def _make_board(charts: list[Chart], title: str = "Test Board") -> Board:
    """Create a Board with chart items in a rows layout."""
    items = [
        LayoutItem(type="chart", chart=chart, width=600, height=300) for chart in charts
    ]
    return Board(
        id="test-board",
        title=title,
        layout=Layout(type="rows", items=items, width=600, height=600),
        resolved_style=_default_resolved_style(),
        chart_style_context=_default_chart_style_context(),
        level=1,
    )


class TestJsonFormat:
    def test_basic_json_output(self, make_chart):
        """JSON format returns valid JSON with board title and items."""
        data = [{"month": "Jan", "revenue": 100}, {"month": "Feb", "revenue": 200}]
        chart = make_chart("bar", x="month", y="revenue", title="Revenue")
        board = _make_board([chart])
        executor = _make_executor(data)

        result = render(board, executor, format="json").output

        parsed = json.loads(result)
        assert parsed["title"] == "Test Board"
        assert "items" in parsed
        assert len(parsed["items"]) == 1

    def test_chart_item_has_resolved_fields(self, make_chart):
        """Each chart item includes resolved chart type and field mappings."""
        data = [{"month": "Jan", "revenue": 100}]
        chart = make_chart("bar", x="month", y="revenue")
        board = _make_board([chart])
        executor = _make_executor(data)

        result = render(board, executor, format="json").output

        parsed = json.loads(result)
        item = parsed["items"][0]
        assert item["type"] == "chart"
        assert item["chart"]["chart_type"] == "bar"
        assert item["chart"]["x"] == "month"
        assert item["chart"]["y"] == "revenue"

    def test_chart_item_has_executed_data(self, make_chart):
        """Each chart item includes the executed query data."""
        data = [{"month": "Jan", "revenue": 100}, {"month": "Feb", "revenue": 200}]
        chart = make_chart("bar", x="month", y="revenue")
        board = _make_board([chart])
        executor = _make_executor(data)

        result = render(board, executor, format="json").output

        parsed = json.loads(result)
        item = parsed["items"][0]
        assert item["data"] == data

    def test_nested_board(self, make_chart):
        """Nested boards recurse in JSON output."""
        data = [{"x": 1, "y": 2}]
        chart = make_chart("bar")
        inner_board = Board(
            id="inner",
            title="Inner Board",
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
            title="Outer Board",
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

        result = render(outer_board, executor, format="json").output

        parsed = json.loads(result)
        assert parsed["title"] == "Outer Board"
        nested = parsed["items"][0]
        assert nested["type"] == "board"
        assert nested["board"]["title"] == "Inner Board"
        assert len(nested["board"]["items"]) == 1

    def test_multiple_charts(self, make_chart):
        """Multiple charts in a board all appear in items."""
        data = [{"x": 1, "y": 2}]
        chart1 = make_chart("bar", id="chart1", title="Chart 1")
        chart2 = make_chart("line", id="chart2", title="Chart 2")
        board = _make_board([chart1, chart2])
        executor = _make_executor(data)

        result = render(board, executor, format="json").output

        parsed = json.loads(result)
        assert len(parsed["items"]) == 2
        assert parsed["items"][0]["chart"]["id"] == "chart1"
        assert parsed["items"][1]["chart"]["id"] == "chart2"

    def test_kpi_chart(self, make_chart):
        """KPI charts include `value` field in JSON output."""
        data = [{"revenue": 42000}]
        chart = make_chart("kpi", value="revenue", x=None, y=None)
        board = _make_board([chart])
        executor = _make_executor(data)

        result = render(board, executor, format="json").output

        parsed = json.loads(result)
        item = parsed["items"][0]
        assert item["chart"]["chart_type"] == "kpi"
        assert item["chart"]["value"] == "revenue"

    def test_chart_item_excludes_removed_mark_and_encoding_passthrough(
        self, make_chart
    ):
        """JSON output reflects the authored dbt charts surface, not removed VL passthrough."""
        data = [{"date": "2024-01-01", "value": 10, "series": "A"}]
        chart = make_chart("line", x="date", y="value")
        board = _make_board([chart])
        executor = _make_executor(data)

        result = render(board, executor, format="json").output

        parsed = json.loads(result)
        item = parsed["items"][0]
        assert "mark" not in item["chart"]
        assert "encoding" not in item["chart"]

    def test_max_rows_per_query_truncates_with_record(self, make_chart):
        """A row cap truncates item data and records shown/total explicitly."""
        data = [{"month": f"m{i}", "revenue": i} for i in range(4)]
        board = _make_board([make_chart("bar", x="month", y="revenue")])
        executor = _make_executor(data)

        result = render(board, executor, format="json", max_rows_per_query=3).output

        parsed = json.loads(result)
        item = parsed["items"][0]
        assert item["data"] == [data[0], data[1], data[3]]
        assert item["rows_truncated"] == {"head": 2, "tail": 1, "total": 4}
