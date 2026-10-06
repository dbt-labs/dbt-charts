"""Regression tests: a focused chart renders at its dashboard slot width.

`chart_focus` (and `focus_on_chart()`) builds a single-chart board. Before the
fix, that board sized the chart from the theme family `preferred_width`, so the
focused render's geometry (band widths, axis-label fit) diverged from what the
chart looked like on its dashboard. The focused board must instead pin the slot
width the dashboard layout assigns to the chart, so focusing is WYSIWYG.

A chart that is defined but not placed in any layout has no dashboard slot, so
its focused render keeps the preferred-width behavior.
"""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path

from dbt_charts.core.compile import compile, focus_on_chart
from dbt_charts.core.compile.models.board.normalized import Board
from dbt_charts.core.compile.models.board.resolved import ResolvedBoard
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


def test_focused_chart_renders_at_its_dashboard_slot_width(
    tmp_path, local_project: Callable[..., Project]
):
    """chart_focus: the focused board sizes the chart at the slot width the
    dashboard's cols layout assigned it, not the theme preferred_width."""
    dashboard = compile(_THREE_COL_YAML)
    assert dashboard.success and dashboard.board is not None, dashboard.errors
    dashboard_sized = _sized_layout(dashboard.board, local_project)
    slot_width = _chart_item_width(dashboard_sized, "small_bar")

    focused = compile(_THREE_COL_YAML + "chart_focus: small_bar\n")
    assert focused.success and focused.board is not None, focused.errors
    focused_sized = _sized_layout(focused.board, local_project)
    focused_width = _chart_item_width(focused_sized, "small_bar")

    assert focused_width == slot_width, (
        f"focused chart width {focused_width:.2f}px != dashboard slot width "
        f"{slot_width:.2f}px — chart focus must reproduce the chart at the "
        f"width it had on the dashboard"
    )


def test_focus_on_chart_api_matches_dashboard_slot_width(
    tmp_path, local_project: Callable[..., Project]
):
    """The programmatic arm (render_dashboard's chart= goes through
    focus_on_chart) pins the same slot width as the YAML arm."""
    dashboard = compile(_THREE_COL_YAML)
    assert dashboard.success and dashboard.board is not None, dashboard.errors
    dashboard_sized = _sized_layout(dashboard.board, local_project)
    slot_width = _chart_item_width(dashboard_sized, "small_bar")

    focused_board = focus_on_chart(dashboard.board, "small_bar")
    focused_sized = _sized_layout(focused_board, local_project)
    focused_width = _chart_item_width(focused_sized, "small_bar")

    assert focused_width == slot_width


def test_focused_rows_chart_keeps_slot_stretched_by_wide_sibling(
    tmp_path, local_project: Callable[..., Project]
):
    """In a rows layout every item gets the full content width, so a wide
    sibling stretches the focused chart's dashboard slot beyond its own
    preferred width; the focused render keeps that stretched width."""
    yaml = """\
queries:
  q:
    type: values
    rows:
      - {cat: a, val: 10}
      - {cat: b, val: 20}
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
"""
    dashboard = compile(yaml)
    assert dashboard.success and dashboard.board is not None, dashboard.errors
    dashboard_sized = _sized_layout(dashboard.board, local_project)
    slot_width = _chart_item_width(dashboard_sized, "narrow_bar")

    from dbt_charts.core.compile.resolve import preferred_chart_width

    preferred = preferred_chart_width(
        dashboard.board.charts["narrow_bar"], dashboard.board.chart_style_context
    )
    assert slot_width > preferred  # the wide sibling stretched the slot

    focused = compile(yaml + "chart_focus: narrow_bar\n")
    assert focused.success and focused.board is not None, focused.errors
    focused_sized = _sized_layout(focused.board, local_project)
    focused_width = _chart_item_width(focused_sized, "narrow_bar")

    assert focused_width == slot_width


def test_focused_unplaced_chart_keeps_preferred_width(
    tmp_path, local_project: Callable[..., Project]
):
    """A chart defined but not placed in the layout has no dashboard slot;
    focusing it keeps the content-sized preferred-width behavior."""
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
"""
    from dbt_charts.core.compile.resolve import preferred_chart_width

    focused = compile(yaml + "chart_focus: unplaced\n")
    assert focused.success and focused.board is not None, focused.errors
    focused_sized = _sized_layout(focused.board, local_project)
    focused_width = _chart_item_width(focused_sized, "unplaced")

    expected = preferred_chart_width(
        focused.board.charts["unplaced"], focused.board.chart_style_context
    )
    assert focused_width == expected


def test_focused_chart_inside_nested_board_uses_inner_slot_width(
    tmp_path, local_project: Callable[..., Project]
):
    """A chart nested one board deep focuses at the inner slot width its
    nested cols layout assigned it on the dashboard."""
    yaml = """\
queries:
  q:
    type: values
    rows:
      - {cat: a, val: 10}
      - {cat: b, val: 20}
charts:
  inner_bar:
    query: q
    type: bar
    x: cat
    y: val
  inner_other:
    query: q
    type: bar
    x: cat
    y: val
rows:
  - cols:
      - inner_bar
      - inner_other
"""
    dashboard = compile(yaml)
    assert dashboard.success and dashboard.board is not None, dashboard.errors
    dashboard_sized = _sized_layout(dashboard.board, local_project)

    def find_nested_width(resolved_board: ResolvedBoard, chart_id: str) -> float:
        stack = [resolved_board.layout]
        while stack:
            layout = stack.pop()
            for item in layout.items:
                if item.chart is not None and item.chart.id == chart_id:
                    return item.width
                if item.board is not None:
                    stack.append(item.board.layout)
        raise AssertionError(f"chart {chart_id!r} not found")

    slot_width = find_nested_width(dashboard_sized, "inner_bar")

    focused = compile(yaml + "chart_focus: inner_bar\n")
    assert focused.success and focused.board is not None, focused.errors
    focused_sized = _sized_layout(focused.board, local_project)
    focused_width = _chart_item_width(focused_sized, "inner_bar")

    assert focused_width == slot_width


def test_focused_chart_on_authored_width_board_hugs_the_pinned_slot(
    tmp_path, local_project: Callable[..., Project]
):
    """An authored board width must not carry into the focused export: the
    focused board pins the dashboard slot and hugs it, rather than rendering
    one chart on a full-board canvas."""
    from dbt_charts.core.compile.sizing import board_container_width

    yaml = "style:\n  frame:\n    width: 1800\n" + _THREE_COL_YAML
    dashboard = compile(yaml)
    assert dashboard.success and dashboard.board is not None, dashboard.errors
    assert (
        board_container_width(dashboard.board, dashboard.board.variable_defaults)
        == 1800.0
    )
    dashboard_sized = _sized_layout(dashboard.board, local_project)
    slot_width = _chart_item_width(dashboard_sized, "small_bar")

    focused_board = focus_on_chart(dashboard.board, "small_bar")
    focused_sized = _sized_layout(focused_board, local_project)
    focused_width = _chart_item_width(focused_sized, "small_bar")

    assert focused_width == slot_width
    assert (
        board_container_width(focused_board, focused_board.variable_defaults) < 1800.0
    )


def test_focused_chart_from_wide_board_keeps_its_wide_slot(
    tmp_path, local_project: Callable[..., Project]
):
    """WYSIWYG must survive a slot wider than the theme's max_width: the
    focused container is the pinned slot plus margins, never the hug bound."""
    from dbt_charts.core.compile.sizing import board_container_width, chart_slot_width

    yaml = (
        "style:\n  frame:\n    width: 1800\n"
        "queries:\n  q:\n    type: values\n    rows:\n"
        "      - {cat: a, val: 10}\n      - {cat: b, val: 20}\n"
        "charts:\n  wide_bar:\n    query: q\n    type: bar\n    x: cat\n    y: val\n"
        "rows:\n  - wide_bar\n"
    )
    dashboard = compile(yaml)
    assert dashboard.success and dashboard.board is not None, dashboard.errors
    slot_width = chart_slot_width(
        dashboard.board, "wide_bar", dashboard.board.variable_defaults
    )
    assert slot_width is not None
    margin = float(dashboard.board.resolved_style.frame.margin)
    assert slot_width > float(dashboard.board.resolved_style.frame.max_width)

    focused_board = focus_on_chart(dashboard.board, "wide_bar")
    focused_sized = _sized_layout(focused_board, local_project)
    assert _chart_item_width(focused_sized, "wide_bar") == slot_width
    assert (
        board_container_width(focused_board, focused_board.variable_defaults)
        == slot_width + 2 * margin
    )
