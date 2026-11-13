"""A chart in a ``cols:`` row grows to the row height a taller table or KPI sets.

Direct placement (``cols: [chart, table]``) and placement inside a nested board
(``cols: [{width: 60%, rows: [chart]}, ...]``) must agree: the chart's slot
fills its cell either way.
"""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path

import pytest

from dbt_charts.cli.filesystem_project import FilesystemProject
from dbt_charts.core.compile import compile
from dbt_charts.core.compile.models.board.normalized import Board, LayoutItem
from dbt_charts.core.execute.env import build_exec_env
from dbt_charts.core.render.layout_sizing import calculate_data_aware_layout

from ..._run import Run

_QUERIES = """
queries:
  total_revenue:
    type: values
    columns: [revenue]
    values:
      - [50000]
  by_week:
    type: values
    columns: [week, revenue]
    values:
      - [Jan 1, 12000]
      - [Jan 8, 9800]
      - [Jan 15, 8600]
      - [Jan 22, 15400]
      - [Jan 29, 4200]
  by_rep:
    type: values
    columns: [rep, revenue]
    values:
""" + "".join(f"      - [rep{i}, {1000 + i * 37}]\n" for i in range(20))

_CHARTS = """
charts:
  weekly:
    query: by_week
    type: {chart_type}
    {channels}
  reps:
    query: by_rep
    type: table
"""

_LINE = "x: week\n    y: revenue"
_PIE = "theta: revenue\n    color: week"

_ProjectFactory = Callable[..., FilesystemProject]


def _size(
    layout_yaml: str, local_project: _ProjectFactory, chart_type: str = "line"
) -> Board:
    channels = _PIE if chart_type == "pie" else _LINE
    yaml_content = (
        _QUERIES
        + _CHARTS.format(chart_type=chart_type, channels=channels)
        + layout_yaml
    )
    result = compile(yaml_content)
    assert result.success, result.errors
    executor = Run(
        result.board,
        env=build_exec_env(local_project(Path.cwd())),
    )
    board, _ = calculate_data_aware_layout(result.board, executor, {}, pre_resolved={})
    return board


def _only_chart_item(board: Board) -> LayoutItem:
    (item,) = board.layout.items
    assert item.chart is not None
    return item


def test_direct_chart_fills_row_beside_taller_table(
    local_project: _ProjectFactory,
) -> None:
    board = _size("cols:\n  - weekly\n  - reps\n", local_project)
    chart_item, table_item = board.layout.items

    assert table_item.height > 0
    assert chart_item.height == pytest.approx(table_item.height)


def test_nested_chart_fills_cell_beside_taller_table(
    local_project: _ProjectFactory,
) -> None:
    board = _size(
        """
cols:
  - width: "60%"
    rows: [weekly]
  - width: "40%"
    rows: [reps]
""",
        local_project,
    )
    chart_cell, table_cell = board.layout.items
    assert chart_cell.board is not None
    chart_item = _only_chart_item(chart_cell.board)

    assert chart_cell.height == pytest.approx(table_cell.height)
    assert chart_item.height == pytest.approx(chart_cell.board.layout.content_height)


def test_nested_pie_keeps_its_natural_height_beside_taller_table(
    local_project: _ProjectFactory,
) -> None:
    pies = _size(
        """
cols:
  - rows: [weekly]
  - rows: [weekly]
""",
        local_project,
        "pie",
    )
    natural_cell = pies.layout.items[0]
    assert natural_cell.board is not None
    natural = _only_chart_item(natural_cell.board)
    board = _size(
        """
cols:
  - rows: [weekly]
  - rows: [reps]
""",
        local_project,
        "pie",
    )
    chart_cell, table_cell = board.layout.items
    assert chart_cell.board is not None
    pie_item = _only_chart_item(chart_cell.board)

    assert table_cell.height > pie_item.height
    assert pie_item.height <= natural.height


def test_nested_charts_fill_their_cells_beside_uneven_prose(
    local_project: _ProjectFactory,
) -> None:
    board = _size(
        """
  total:
    query: total_revenue
    type: kpi
    value: revenue
cols:
  - total
  - text: Short
    rows: [weekly]
  - text: |
      A long paragraph of prose that wraps across several lines and makes
      this nested board the tallest item in the row, so it sets the row's
      height. A long paragraph of prose that wraps across several lines and
      makes this nested board the tallest item in the row.
    rows: [weekly]
""",
        local_project,
    )
    _, short_cell, tall_cell = board.layout.items

    for cell in (short_cell, tall_cell):
        assert cell.board is not None
        assert _only_chart_item(cell.board).height == pytest.approx(
            cell.board.layout.content_height
        )


def test_nested_chart_stops_at_table_content_not_its_authored_cell(
    local_project: _ProjectFactory,
) -> None:
    def chart_height(table_cell_height: str) -> float:
        board = _size(
            f"""
cols:
  - width: "60%"
    rows: [weekly]
  - width: "40%"{table_cell_height}
    rows: [reps]
""",
            local_project,
        )
        chart_cell = board.layout.items[0]
        assert chart_cell.board is not None
        return _only_chart_item(chart_cell.board).height

    assert chart_height("\n    height: 900") == pytest.approx(chart_height(""))
