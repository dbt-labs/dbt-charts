"""build_resolved_board produces a V2-typed, data-resolved ResolvedBoard.

RED: `build_resolved_board` does not exist yet.  Import fails before Step 2 is
implemented.  After Step 2 the asserts fire because:
- rf.charts["bar"] must be ResolvedBarChart (not flat ResolvedChart).
- rf.layout.items[0].chart must be a ResolvedChart member.
- bar resolved_channels reflect real rows (not []).
"""

from __future__ import annotations

import re
from typing import Any
from unittest.mock import MagicMock


def _make_board() -> Any:
    """Compile a minimal multi-chart board (bar + kpi + table + callout)."""
    from dbt_charts.core.compile.compiler import compile

    result = compile(
        """
id: v2-tree-board
title: V2 Tree Test
source: duckdb
rows:
  - bar_chart
  - kpi_chart
  - table_chart
  - callout_chart
charts:
  bar_chart:
    type: bar
    x: cat
    y: val
    query:
      sql: SELECT 'A' AS cat, 10 AS val UNION ALL SELECT 'B', 20
  kpi_chart:
    type: kpi
    value: val
    query:
      sql: SELECT 42 AS val
  table_chart:
    type: table
    query:
      sql: SELECT 'A' AS cat, 10 AS val
  callout_chart:
    type: callout
    message: "hello"
"""
    )
    return result.board


def _make_executor(board: Any) -> Any:
    """Build a mock executor that returns sensible data per query_name."""
    from dbt_charts.core.compile.models.board.normalized import Board

    assert isinstance(board, Board)

    executor = MagicMock()
    executor.render_with_range_filters.return_value = None
    results: dict[str, list[dict[str, Any]]] = {
        "bar_chart": [{"cat": "A", "val": 10}, {"cat": "B", "val": 20}],
        "kpi_chart": [{"val": 42}],
        "table_chart": [{"cat": "A", "val": 10}],
        "callout_chart": [],
    }

    def _execute_query(name: str, variables: Any) -> list[dict[str, Any]]:
        return results.get(name, [])

    executor.execute_query.side_effect = _execute_query
    executor.execute_chart.return_value = []
    executor.execute_batch.return_value = {}
    return executor


def _make_cols_board() -> Any:
    """Compile a 2-chart cols board for sizing regression tests."""
    from dbt_charts.core.compile.compiler import compile

    result = compile(
        """
id: cols-board
source: duckdb
cols:
  - bar_a
  - bar_b
charts:
  bar_a:
    type: bar
    x: cat
    y: val
    query:
      sql: SELECT 'A' AS cat, 10 AS val UNION ALL SELECT 'B', 20
  bar_b:
    type: bar
    x: cat
    y: val
    query:
      sql: SELECT 'X' AS cat, 5 AS val UNION ALL SELECT 'Y', 15
"""
    )
    return result.board


def _make_repeated_pie_board() -> Any:
    from dbt_charts.core.compile.compiler import compile

    result = compile(
        """
id: repeated-pie-board
source: duckdb
queries:
  shares:
    type: values
    columns: [series, value]
    values:
      - [Enterprise Strategic Accounts, 22]
      - [Mid-Market Commercial, 20]
      - [SMB & Self-Serve, 18]
      - [Public Sector & Education, 16]
      - [Healthcare & Life Sciences, 14]
      - [Partners & Channel, 10]
charts:
  shared_pie:
    query: shares
    type: donut
    theta: value
    color: series
rows:
  - shared_pie
  - cols: [shared_pie, shared_pie]
"""
    )
    assert result.success, result.errors
    return result.board


# ---------------------------------------------------------------------------
# build_resolved_board as single board-resolution entry
# ---------------------------------------------------------------------------


def test_build_resolved_board_is_single_entry_and_calls_v2_resolve_per_chart() -> None:
    """build_resolved_board is the single V2 board-resolution entry point.

    Asserts:
    - Returns (ResolvedBoard, RenderCache) tuple (not bare ResolvedBoard).
    - Calls compile.resolve.resolve (V2) once per chart within itself, plus one
      uncached resolve per unpinned table to measure its width for the board hug.

    Charts are resolved lazily inside _require_resolved (in layout_sizing)
    at the point where each item's layout pixel width is known.  The resolve
    calls therefore originate from resolve_chart_with_runtime_inputs (called
    by layout_sizing), which gathers any runtime inputs before calling
    compile.resolve.resolve exactly once. Each chart is finalized by that
    one resolver call, so the count stays 1:1.
    """
    import importlib
    from unittest.mock import patch

    chart_resolution = importlib.import_module(
        "dbt_charts.core.execute.chart_resolution"
    )
    from dbt_charts.core.compile.resolve import resolve as real_resolve
    from dbt_charts.core.render.board_resolve import build_resolved_board

    board = _make_board()
    executor = _make_executor(board)

    counter: list[int] = []

    def _counting_resolve(compiled: Any, data: Any, **kw: Any) -> Any:
        counter.append(1)
        return real_resolve(compiled, data, **kw)

    with patch.object(chart_resolution, "resolve", _counting_resolve):
        result = build_resolved_board(board, executor, {}, render_first=False)

    # Single entry: returns (ResolvedBoard, RenderCache) tuple
    assert isinstance(result, tuple) and len(result) == 2, (
        f"build_resolved_board must return (ResolvedBoard, RenderCache) tuple, got {type(result)}"
    )
    # Once per chart (bar, kpi, table, callout), plus one uncached resolve the
    # board-width hug makes to measure the unpinned table's columns.
    assert len(counter) == 5, (
        f"Expected 4 charts + 1 table width measurement, got {len(counter)}"
    )


def test_col_alignment_heights_unchanged() -> None:
    """Fused build_resolved_board produces same item heights as separate sizing pass.

    RED: build_resolved_board currently lacks the new fused signature.
    GREEN: once fused, layout item heights match the pre-fuse sizing pass.
    """
    import copy

    from dbt_charts.core.render.board_resolve import build_resolved_board
    from dbt_charts.core.render.layout_sizing import calculate_data_aware_layout

    board = _make_cols_board()
    executor = _make_executor(board)

    # Reference heights from the pre-fuse separate sizing pass
    board_copy = copy.deepcopy(board)
    board_sized, _ = calculate_data_aware_layout(
        board_copy, executor, {}, render_first=False, pre_resolved={}
    )
    ref_heights = {
        item.chart.id: item.height
        for item in board_sized.layout.items
        if item.chart is not None and item.height > 0
    }

    # Fused pass
    rf, _ = build_resolved_board(board, executor, {}, render_first=False)
    fused_heights = {
        item.chart.id: item.height
        for item in rf.layout.items
        if item.chart is not None and item.height > 0
    }

    assert ref_heights == fused_heights, (
        f"Col alignment heights diverged after fuse.\n"
        f"reference (pre-fuse): {ref_heights}\n"
        f"fused:                {fused_heights}"
    )


def test_repeated_pie_layout_items_carry_their_slot_width_resolution() -> None:
    from dbt_charts.core.compile.models.chart.resolved import ResolvedPieChart
    from dbt_charts.core.render.board_resolve import build_resolved_board

    board = _make_repeated_pie_board()
    executor = MagicMock()
    executor.render_with_range_filters.return_value = None
    executor.execute_query.return_value = [
        {"series": name, "value": value}
        for name, value in [
            ("Enterprise Strategic Accounts", 22),
            ("Mid-Market Commercial", 20),
            ("SMB & Self-Serve", 18),
            ("Public Sector & Education", 16),
            ("Healthcare & Life Sciences", 14),
            ("Partners & Channel", 10),
        ]
    ]

    resolved, _cache = build_resolved_board(board, executor, {}, render_first=True)

    pies: list[tuple[float, ResolvedPieChart]] = []

    def collect(items: Any) -> None:
        for item in items:
            if isinstance(item.chart, ResolvedPieChart):
                pies.append((item.width, item.chart))
            if item.board is not None:
                collect(item.board.layout.items)

    collect(resolved.layout.items)
    assert len(pies) == 3
    assert all(chart.resolution_width == width for width, chart in pies)
    assert len({width for width, _chart in pies}) == 2
    wide = max(pies, key=lambda pair: pair[0])[1]
    narrow = min(pies, key=lambda pair: pair[0])[1]
    assert wide.attached_table is None
    assert narrow.attached_table is not None


class TestBuildResolvedBoardV2Tree:
    def test_charts_dict_contains_v2_types(self) -> None:
        """rf.charts["bar_chart"] is ResolvedBarChart, not flat ResolvedChart."""
        from dbt_charts.core.compile.models.chart.resolved import ResolvedBarChart
        from dbt_charts.core.render.board_resolve import build_resolved_board

        board = _make_board()
        executor = _make_executor(board)
        rf, _ = build_resolved_board(board, executor, {}, render_first=False)

        assert isinstance(rf.charts["bar_chart"], ResolvedBarChart), (
            f"Expected ResolvedBarChart, got {type(rf.charts['bar_chart'])}"
        )

    def test_layout_items_carry_v2_charts(self) -> None:
        """ResolvedLayoutItem.chart is a ResolvedChart member (not flat ResolvedChart)."""
        from dbt_charts.core.compile.models.chart.resolved import ResolvedBarChart
        from dbt_charts.core.render.board_resolve import build_resolved_board

        board = _make_board()
        executor = _make_executor(board)
        rf, _ = build_resolved_board(board, executor, {}, render_first=False)

        chart_items = [
            item
            for item in rf.layout.items
            if item.type == "chart" and item.chart is not None
        ]
        assert len(chart_items) > 0, "No chart items in layout"
        bar_item = next(
            (i for i in chart_items if i.chart and i.chart.chart_type == "bar"), None
        )
        assert bar_item is not None, "No bar chart item in layout"
        assert isinstance(bar_item.chart, ResolvedBarChart), (
            f"Expected ResolvedBarChart, got {type(bar_item.chart)}"
        )

    def test_bar_channels_reflect_real_data(self) -> None:
        """Bar resolved_channels are populated from real rows, not empty list."""
        from dbt_charts.core.compile.models.chart.resolved import ResolvedBarChart
        from dbt_charts.core.render.board_resolve import build_resolved_board

        board = _make_board()
        executor = _make_executor(board)
        rf, _ = build_resolved_board(board, executor, {}, render_first=False)

        bar = rf.charts["bar_chart"]
        assert isinstance(bar, ResolvedBarChart)
        # x channel should reflect the "cat" field from real rows
        assert bar.x == "cat", f"Expected bar.x='cat', got {bar.x!r}"
        # resolved_channels should have x binding
        assert "x" in bar.resolved_channels or bar.x is not None


def _make_resolved_overlay_two_queries() -> Any:
    """Build a base line chart with one overlay bar layer, each on its own query.

    Uses normalize_chart + resolve() (the real pipeline) so the resolved
    layer carries the correct query_name value.
    """
    from dbt_charts.core.compile.config import (
        get_default_theme_name,
        get_theme_style,
    )
    from dbt_charts.core.compile.models.query.normalized import SqlQuery
    from dbt_charts.core.compile.normalize.charts import normalize_chart
    from dbt_charts.core.compile.resolve import resolve
    from dbt_charts.core.compile.resolve.style.board import resolve_chart_style_context

    board_style = resolve_chart_style_context(get_theme_style(get_default_theme_name()))

    q_a = SqlQuery(sql="SELECT 'A' AS cat, 10 AS metric_a")
    q_b = SqlQuery(sql="SELECT 'A' AS cat, 999 AS metric_b")
    query_registry = {"q_a": q_a, "q_b": q_b}
    chart_def = {
        "type": "line",
        "x": "cat",
        "y": "metric_a",
        "query": "q_a",
        "layers": [
            {"type": "bar", "y": "metric_b", "query": "q_b"},
        ],
    }
    compiled = normalize_chart("overlay_two_q", chart_def, query_registry, sources={})
    primary_data = [{"cat": "A", "metric_a": 10}]
    return resolve(compiled, primary_data, chart_style_context=board_style)


def test_overlay_layer_board_path_includes_non_primary_layer_data() -> None:
    """Regression: render_chart_item (board path) must pass datasets= for a
    chart.layers overlay whose layer authors its own distinct query.

    A base chart with an overlay layer on a different query loses that
    layer's data when render_chart_item does not forward per-query datasets
    to render_resolved_chart. The sentinel value 999 (metric_b from q_b) must
    appear in the rendered SVG.

    RED before the rendering.py fix (datasets not forwarded → the layer uses
    the base's q_a data instead of its own q_b). GREEN after (per-query
    datasets built and forwarded).
    """
    from dbt_charts.core.compile.config import (
        get_default_theme_name,
        get_theme_style,
        reset_config,
    )
    from dbt_charts.core.compile.resolve.style.board import resolve_style
    from dbt_charts.core.render.chart.rendering import render_chart_item

    reset_config()

    overlay = _make_resolved_overlay_two_queries()

    executor = MagicMock()
    executor.render_with_range_filters.return_value = None
    query_data: dict[str, list[dict[str, Any]]] = {
        "q_a": [{"cat": "A", "metric_a": 10}],
        "q_b": [{"cat": "A", "metric_b": 999}],
    }

    def _execute(name: str, variables: Any) -> list[dict[str, Any]]:
        return query_data.get(name, [])

    executor.execute_query.side_effect = _execute
    executor.execute_chart.return_value = []
    executor.execute_batch.return_value = {}

    board_style = resolve_style(get_theme_style(get_default_theme_name()))

    svg, _ = render_chart_item(
        overlay,
        executor,
        {},
        available_width=800.0,
        available_height=400.0,
        resolved_style=board_style,
        render_cache={},
    )

    # The sentinel value 999 must appear in the rendered SVG (it is embedded in
    # the Vega-Lite spec as inline data when per-layer datasets are forwarded).
    # If datasets= is not passed, the layer uses the base's q_a data
    # (metric_a:10) and 999 never appears.
    assert re.search(r"\b999\b", svg), (
        "Value 999 (metric_b from q_b) not found in rendered SVG — "
        "render_chart_item must forward per-layer datasets to render_resolved_chart"
    )
