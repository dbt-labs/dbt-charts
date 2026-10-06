"""A focused chart renders at its own intrinsic width.

`chart_focus` (and `focus_on_chart()`) builds a single-chart board that sizes the
chart exactly as if it were the only chart on a board with no width: its
preferred width, or its authored `width:`. The slot it had on the source board,
and that board's authored `frame.width`, do not carry over.
"""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path

from dbt_charts.core.compile import compile, focus_on_chart
from dbt_charts.core.compile.models.board.normalized import Board
from dbt_charts.core.compile.models.board.resolved import ResolvedBoard
from dbt_charts.core.compile.resolve import preferred_chart_width
from dbt_charts.core.compile.sizing import board_container_width
from dbt_charts.core.execute.adapters import build_adapter_registry
from dbt_charts.core.execute.executor import Executor
from dbt_charts.core.project import Project
from dbt_charts.core.render.board_resolve import build_resolved_board

_THREE_COL_YAML = """\
queries:
  q:
    type: values
    rows:
      - {cat: a, val: 10}
      - {cat: b, val: 20}
      - {cat: c, val: 15}
charts:
  small_bar:
    query: q
    type: bar
    x: cat
    y: val
  other1:
    query: q
    type: bar
    x: cat
    y: val
  other2:
    query: q
    type: bar
    x: cat
    y: val
cols:
  - small_bar
  - other1
  - other2
"""


def _sized_layout(
    board: Board,
    local_project: Callable[..., Project],
) -> ResolvedBoard:
    executor = Executor(
        board,
        adapter_registry=build_adapter_registry(local_project(Path.cwd())),
        query_registry=None,
    )
    resolved_board, _cache = build_resolved_board(
        board, executor, {}, render_first=False
    )
    return resolved_board


def _chart_item_width(resolved_board: ResolvedBoard, chart_id: str) -> float:
    for item in resolved_board.layout.items:
        if item.chart is not None and item.chart.id == chart_id:
            return item.width
    raise AssertionError(f"chart {chart_id!r} not found in resolved layout")


def _preferred(board: Board, chart_id: str) -> float:
    return preferred_chart_width(board.charts[chart_id], board.chart_style_context)


def test_chart_in_cols_half_focuses_at_its_own_preferred_width(
    local_project: Callable[..., Project],
):
    dashboard = compile(_THREE_COL_YAML)
    assert dashboard.success and dashboard.board is not None, dashboard.errors
    dashboard_sized = _sized_layout(dashboard.board, local_project)
    assert _chart_item_width(dashboard_sized, "small_bar") != _preferred(
        dashboard.board, "small_bar"
    )

    focused = compile(_THREE_COL_YAML + "chart_focus: small_bar\n")
    assert focused.success and focused.board is not None, focused.errors
    focused_sized = _sized_layout(focused.board, local_project)
    assert _chart_item_width(focused_sized, "small_bar") == _preferred(
        focused.board, "small_bar"
    )


def test_yaml_and_api_focus_give_identical_widths(
    local_project: Callable[..., Project],
):
    dashboard = compile(_THREE_COL_YAML)
    assert dashboard.success and dashboard.board is not None, dashboard.errors
    api_board = focus_on_chart(dashboard.board, "small_bar")
    yaml_result = compile(_THREE_COL_YAML + "chart_focus: small_bar\n")
    assert yaml_result.success and yaml_result.board is not None
    assert _chart_item_width(
        _sized_layout(api_board, local_project), "small_bar"
    ) == _chart_item_width(_sized_layout(yaml_result.board, local_project), "small_bar")
    assert board_container_width(
        api_board, api_board.variable_defaults
    ) == board_container_width(yaml_result.board, yaml_result.board.variable_defaults)


def test_authored_frame_width_does_not_carry_into_focused_board(
    local_project: Callable[..., Project],
):
    yaml = "style:\n  frame:\n    width: 1200\n" + _THREE_COL_YAML
    dashboard = compile(yaml)
    assert dashboard.success and dashboard.board is not None, dashboard.errors
    assert (
        board_container_width(dashboard.board, dashboard.board.variable_defaults)
        == 1200.0
    )

    focused = focus_on_chart(dashboard.board, "small_bar")
    preferred = _preferred(dashboard.board, "small_bar")
    margin = float(dashboard.board.resolved_style.frame.margin)
    assert (
        board_container_width(focused, focused.variable_defaults)
        == preferred + 2 * margin
    )
    assert _chart_item_width(_sized_layout(focused, local_project), "small_bar") == (
        preferred
    )


def test_focused_chart_ignores_wide_rows_sibling(
    local_project: Callable[..., Project],
):
    yaml = """\
queries:
  q:
    type: values
    rows:
      - {cat: a, val: 10}
charts:
  narrow_bar:
    query: q
    type: bar
    x: cat
    y: val
  wide_sibling:
    query: q
    type: bar
    x: cat
    y: val
    width: 1100
rows:
  - narrow_bar
  - wide_sibling
chart_focus: narrow_bar
"""
    focused = compile(yaml)
    assert focused.success and focused.board is not None, focused.errors
    assert _chart_item_width(
        _sized_layout(focused.board, local_project), "narrow_bar"
    ) == _preferred(focused.board, "narrow_bar")


def test_focused_chart_keeps_authored_width(
    local_project: Callable[..., Project],
):
    yaml = """\
queries:
  q:
    type: values
    rows:
      - {cat: a, val: 10}
charts:
  fixed:
    query: q
    type: bar
    x: cat
    y: val
    width: 320
  other:
    query: q
    type: bar
    x: cat
    y: val
cols:
  - fixed
  - other
chart_focus: fixed
"""
    focused = compile(yaml)
    assert focused.success and focused.board is not None, focused.errors
    assert (
        _chart_item_width(_sized_layout(focused.board, local_project), "fixed") == 320.0
    )


def test_focused_unplaced_chart_keeps_preferred_width(
    local_project: Callable[..., Project],
):
    yaml = """\
queries:
  q:
    type: values
    rows:
      - {cat: a, val: 10}
charts:
  placed:
    query: q
    type: bar
    x: cat
    y: val
  unplaced:
    query: q
    type: bar
    x: cat
    y: val
rows:
  - placed
chart_focus: unplaced
"""
    focused = compile(yaml)
    assert focused.success and focused.board is not None, focused.errors
    assert _chart_item_width(
        _sized_layout(focused.board, local_project), "unplaced"
    ) == _preferred(focused.board, "unplaced")
