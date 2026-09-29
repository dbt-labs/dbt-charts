"""Tests for layout rendering functions.

Tests the dbt_charts.core.render.layouts module for rendering different layout types.
"""

import importlib
from collections.abc import Callable
from pathlib import Path
from unittest.mock import Mock

import pytest

from dbt_charts.cli.filesystem_project import FilesystemProject
from dbt_charts.core.compile import compile
from dbt_charts.core.compile.config import get_theme_style
from dbt_charts.core.compile.resolve.style.board import resolve_style
from dbt_charts.core.execute import Executor
from dbt_charts.core.execute.adapters import build_adapter_registry
from dbt_charts.core.render.layouts import (
    render_cols_layout,
    render_grid_layout,
    render_rows_layout,
    render_tabs_layout,
)

from ._board_utils import apply_static_layout

# Import the layouts module using importlib to avoid name shadowing issues
layouts_module = importlib.import_module("dbt_charts.core.render.layouts")

# HTML layout functions have been removed (SVG-First Migration)
# HTML format now wraps SVG output instead of having separate rendering paths


class TestRowsLayoutRendering:
    """Tests for rows layout rendering."""

    def test_render_rows_layout_svg_empty(self):
        """Test rendering empty rows layout."""
        from dbt_charts.core.compile.config import (
            get_theme_style,
        )
        from dbt_charts.core.compile.resolve.style.board import resolve_style

        mock_executor = Mock()
        items = []

        rs = resolve_style(get_theme_style())
        result = render_rows_layout(
            items,
            mock_executor,
            {},
            800.0,
            600.0,
            0.0,
            20.0,
            resolved_style=rs,
            render_cache={},
            painted_canvas=None,
        )

        assert result == ("", 0.0)

    def test_render_rows_layout_svg_with_charts(
        self, local_project: Callable[..., FilesystemProject]
    ):
        """Test rendering rows layout with charts."""
        yaml_content = """
title: Test Dashboard
queries:
  q1:
    type: values
    rows:
      - {month: Jan, revenue: 100}
      - {month: Feb, revenue: 150}
charts:
  c1:
    query: q1
    type: bar
    x: month
    y: revenue
rows:
  - c1
"""
        result = compile(yaml_content)
        assert result.success

        executor = Executor(
            result.board,
            adapter_registry=build_adapter_registry(local_project(Path.cwd())),
            query_registry=result.query_registry,
        )

        rs = resolve_style(get_theme_style("clarity"))
        from dbt_charts.core.compile.config import reset_config
        from dbt_charts.core.render.board_resolve import (
            build_resolved_board_static as resolve_board,
        )

        reset_config()
        resolved_board = resolve_board(result.board)
        svg, total_height = render_rows_layout(
            resolved_board.layout.items,
            executor,
            {},
            800.0,
            600.0,
            0.0,
            20.0,
            resolved_style=rs,
            render_cache={},
            painted_canvas=None,
        )
        assert svg  # non-empty SVG fragment
        assert total_height > 0


class TestColsLayoutRendering:
    """Tests for cols layout rendering."""

    def test_render_cols_layout_svg_empty(self):
        """Test rendering empty cols layout."""
        from dbt_charts.core.compile.config import (
            get_theme_style,
        )
        from dbt_charts.core.compile.resolve.style.board import resolve_style

        mock_executor = Mock()
        items = []

        rs = resolve_style(get_theme_style())
        result = render_cols_layout(
            items,
            mock_executor,
            {},
            800.0,
            600.0,
            0.0,
            20.0,
            resolved_style=rs,
            render_cache={},
            painted_canvas=None,
        )

        assert result == ("", 0.0)

    def test_render_cols_layout_svg_with_charts(
        self, local_project: Callable[..., FilesystemProject]
    ):
        """Test rendering cols layout with charts."""
        yaml_content = """
title: Test Dashboard
queries:
  q1:
    type: values
    rows:
      - {month: Jan, revenue: 100}
      - {month: Feb, revenue: 150}
charts:
  c1:
    query: q1
    type: bar
    x: month
    y: revenue
  c2:
    query: q1
    type: line
    x: month
    y: revenue
cols:
  - c1
  - c2
"""
        result = compile(yaml_content)
        assert result.success

        executor = Executor(
            result.board,
            adapter_registry=build_adapter_registry(local_project(Path.cwd())),
            query_registry=result.query_registry,
        )

        rs = resolve_style(get_theme_style("clarity"))
        from dbt_charts.core.compile.config import reset_config
        from dbt_charts.core.render.board_resolve import (
            build_resolved_board_static as resolve_board,
        )

        reset_config()
        resolved_board = resolve_board(result.board)
        svg, _max_height = render_cols_layout(
            resolved_board.layout.items,
            executor,
            {},
            800.0,
            600.0,
            0.0,
            20.0,
            resolved_style=rs,
            render_cache={},
            painted_canvas=None,
        )
        assert svg  # non-empty SVG fragment
        assert _max_height > 0


class TestGridLayoutRendering:
    """Tests for grid layout rendering."""

    def test_render_grid_layout_svg_with_positions(
        self, local_project: Callable[..., FilesystemProject]
    ):
        """Test rendering grid layout with positioned items."""
        yaml_content = """
title: Test Dashboard
queries:
  q1:
    type: values
    rows:
      - {month: Jan, revenue: 100}
      - {month: Feb, revenue: 150}
charts:
  c1:
    query: q1
    type: bar
    x: month
    y: revenue
grid:
  columns: 24
  items:
    - item: c1
      col: 0
      row: 0
      col_span: 12
      row_span: 2
"""
        result = compile(yaml_content)
        assert result.success

        executor = Executor(
            result.board,
            adapter_registry=build_adapter_registry(local_project(Path.cwd())),
            query_registry=result.query_registry,
        )

        rs = resolve_style(get_theme_style("clarity"))
        from dbt_charts.core.compile.config import reset_config
        from dbt_charts.core.render.board_resolve import (
            build_resolved_board_static as resolve_board,
        )

        reset_config()
        resolved_board = resolve_board(result.board)
        svg, _max_bottom = render_grid_layout(
            resolved_board.layout.items,
            executor,
            {},
            800.0,
            600.0,
            0.0,
            20.0,
            resolved_style=rs,
            render_cache={},
            painted_canvas=None,
        )
        assert svg  # non-empty SVG fragment
        assert "translate(" in svg


class TestTabsLayoutRendering:
    """Tests for tabs layout rendering."""

    def test_render_tabs_layout_svg(
        self, local_project: Callable[..., FilesystemProject]
    ):
        """Test rendering tabs layout."""
        yaml_content = """
title: Test Dashboard
queries:
  q1:
    type: values
    rows:
      - {month: Jan, revenue: 100}
      - {month: Feb, revenue: 150}
charts:
  c1:
    query: q1
    type: bar
    x: month
    y: revenue
  c2:
    query: q1
    type: line
    x: month
    y: revenue
tabs:
  items:
    - title: Sales Bar
      rows:
        - c1
    - title: Sales Line
      rows:
        - c2
"""
        result = compile(yaml_content)
        assert result.success

        executor = Executor(
            result.board,
            adapter_registry=build_adapter_registry(local_project(Path.cwd())),
            query_registry=result.query_registry,
        )

        from dbt_charts.core.compile.config import reset_config
        from dbt_charts.core.render.board_resolve import (
            build_resolved_board_static as resolve_board,
        )

        reset_config()
        resolved_board = resolve_board(result.board)
        # Get tab titles from layout
        tab_titles = resolved_board.layout.tab_titles or []

        rs = resolve_style(get_theme_style("clarity"))
        svg, _actual_height = render_tabs_layout(
            resolved_board.layout.items,
            executor,
            {},
            800.0,
            600.0,
            tab_titles,
            0,
            "top",
            resolved_style=rs,
            render_cache={},
            painted_canvas=None,
        )
        assert "<svg" in svg
        assert "Sales Bar" in svg or "Tab 1" in svg


# HTML layout tests have been removed (SVG-First Migration)
# HTML format now wraps SVG output instead of having separate rendering paths
# See tests/integration/test_layouts.py for HTML format tests that verify
# the SVG content is properly wrapped in HTML


def _resolve_nested(board):
    from dbt_charts.core.render.board_resolve import (
        build_resolved_nested_board_static as resolve_nested_board,
    )

    return resolve_nested_board(board)


class TestNestedBoardContainerSizing:
    """Nested boards size to actual rendered content.

    After the height-propagation fix, render_nested_board returns the actual
    rendered height (bottom-up from content) rather than the pre-allocated slot.
    """

    def _get_svg_height(self, svg: str) -> float:
        import re

        match = re.search(r'<svg[^>]+height="([^"]+)"', svg)
        assert match, "SVG must have a height attribute"
        return float(match.group(1))

    def _compile_and_size(
        self, yaml_content: str, local_project: Callable[..., FilesystemProject]
    ):
        from dbt_charts.core.render.layout_sizing import calculate_data_aware_layout

        result = compile(yaml_content)
        assert result.success
        executor = Executor(
            result.board,
            adapter_registry=build_adapter_registry(local_project(Path.cwd())),
            query_registry=result.query_registry,
        )
        board, _ = calculate_data_aware_layout(
            result.board, executor, {}, pre_resolved={}
        )
        return board, executor

    def test_nested_board_returns_tuple(
        self, local_project: Callable[..., FilesystemProject]
    ):
        """render_nested_board must return (svg_string, actual_height)."""
        from dbt_charts.core.render.boards import render_nested_board

        yaml_content = """
cols:
  - rows:
      - text: "Short item"
        style:
          background: aliceblue
    style:
      background: mistyrose
  - text: |
      Line 1

      Line 2

      Line 3

      Line 4

      Line 5

      Line 6
    style:
      background: honeydew
"""
        board, executor = self._compile_and_size(yaml_content, local_project)
        nested_item = board.layout.items[0]
        assert nested_item.board is not None

        svg, actual_height = render_nested_board(
            _resolve_nested(nested_item.board),
            executor,
            {},
            nested_item.width,
            nested_item.height,
            0.0,
            render_cache={},
            painted_canvas=None,
        )
        assert actual_height > 0
        assert "<svg" in svg

    def test_nested_board_svg_height_matches_returned_actual_height(
        self, local_project: Callable[..., FilesystemProject]
    ):
        """The height attribute in the SVG must match the returned actual_height."""
        from dbt_charts.core.render.boards import render_nested_board

        yaml_content = """
rows:
  - title: inner
    style:
      background: aliceblue
    rows:
      - text: "Short item"
"""
        board, executor = self._compile_and_size(yaml_content, local_project)
        nested_item = board.layout.items[0]
        assert nested_item.board is not None

        svg, actual_height = render_nested_board(
            _resolve_nested(nested_item.board),
            executor,
            {},
            nested_item.width,
            nested_item.height,
            0.0,
            render_cache={},
            painted_canvas=None,
        )
        assert self._get_svg_height(svg) == pytest.approx(actual_height, abs=1.0), (
            "SVG height must match the returned actual_height"
        )

    def test_nested_board_content_only_renders_without_error(
        self, local_project: Callable[..., FilesystemProject]
    ):
        """Boards with only content (no layout items) render successfully."""
        from dbt_charts.core.render.boards import render_nested_board

        yaml_content = """
title: "outer"
rows:
  - title: "inner box"
    style:
      background: "#f5f5f5"
      padding: "16px"
    rows:
      - text: |
          # Hello, World!
        style:
          background: honeydew
          padding: "20px"
"""
        board, executor = self._compile_and_size(yaml_content, local_project)
        inner_box_item = board.layout.items[0]
        assert inner_box_item.board is not None
        honeydew_item = inner_box_item.board.layout.items[0]
        assert honeydew_item.board is not None

        svg, actual_height = render_nested_board(
            _resolve_nested(honeydew_item.board),
            executor,
            {},
            honeydew_item.width,
            honeydew_item.height,
            0.0,
            render_cache={},
            painted_canvas=None,
        )
        assert "<svg" in svg
        assert actual_height > 0


class TestCardPaddingRendering:
    """Card padding (16px inset) behavior differs by renderer family.

    SVG-family charts (kpi/table/spark_bar) still receive an outer SVG
    translate wrapper.  Vega-family charts (bar/line/etc.) receive card_pad
    as Vega internal padding instead — no outer translate.
    """

    def test_vega_chart_item_no_translate(
        self, local_project: Callable[..., FilesystemProject]
    ):
        """Vega-family chart items must NOT be wrapped in SVG translate.

        card_pad is forwarded as Vega's internal padding instead, so board
        titles align with chart content without a double inset.
        """
        from dbt_charts.core.render.chart.rendering import render_layout_item

        yaml_content = """
title: Card Padding Render Test
queries:
  q:
    type: values
    rows:
      - {month: Jan, revenue: 100}
      - {month: Feb, revenue: 150}
charts:
  c1:
    query: q
    type: bar
    x: month
    y: revenue
rows:
  - c1
"""
        result = compile(yaml_content)
        assert result.success

        board = apply_static_layout(result.board)
        executor = Executor(
            board,
            adapter_registry=build_adapter_registry(local_project(Path.cwd())),
            query_registry=result.query_registry,
        )

        from dbt_charts.core.compile.resolve.style.board import resolve_style
        from dbt_charts.core.render.board_resolve import (
            build_resolved_board_static as resolve_board,
        )

        rs = resolve_style(get_theme_style())
        resolved_board = resolve_board(board)
        chart_item = resolved_board.layout.items[0]
        svg, actual_height = render_layout_item(
            chart_item,
            executor,
            {},
            0.0,
            chart_item.width,
            chart_item.height,
            resolved_style=rs,
            render_cache={},
            painted_canvas=None,
        )

        card_padding = float(get_theme_style().frame.card_padding)
        # Vega-family: must NOT have outer translate wrapper
        assert f'<g transform="translate({card_padding}' not in svg, (
            f"Vega chart must NOT have outer translate({card_padding}, ...) — "
            "card_pad is now Vega's internal padding"
        )
        # actual_height is the Vega SVG height directly (no inflation)
        assert actual_height > 0

    def test_board_item_does_not_get_card_padding_translate(
        self, local_project: Callable[..., FilesystemProject]
    ):
        """Nested board items should NOT have a card_padding translate wrapper."""
        from dbt_charts.core.render.chart.rendering import render_layout_item

        yaml_content = """
title: No Card Padding on Boards
queries:
  q:
    type: values
    rows:
      - {month: Jan, revenue: 100}
      - {month: Feb, revenue: 150}
charts:
  c1:
    query: q
    type: bar
    x: month
    y: revenue
rows:
  - title: Section
    rows:
      - c1
"""
        result = compile(yaml_content)
        assert result.success

        board = apply_static_layout(result.board)
        executor = Executor(
            board,
            adapter_registry=build_adapter_registry(local_project(Path.cwd())),
            query_registry=result.query_registry,
        )

        from dbt_charts.core.compile.resolve.style.board import resolve_style
        from dbt_charts.core.render.board_resolve import (
            build_resolved_board_static as resolve_board,
        )

        rs = resolve_style(get_theme_style())
        resolved_board = resolve_board(board)
        board_item = resolved_board.layout.items[0]
        assert board_item.type == "board"
        svg, actual_height = render_layout_item(
            board_item,
            executor,
            {},
            0.0,
            board_item.width,
            board_item.height,
            resolved_style=rs,
            render_cache={},
            painted_canvas=None,
        )

        card_padding = float(get_theme_style().frame.card_padding)
        # The board item itself should NOT be wrapped in card_padding translate.
        # However, its inner chart children SHOULD have card_padding.
        # Check that the outermost SVG element is NOT a card_padding translate group.
        assert not svg.startswith(f'<g transform="translate({card_padding}'), (
            "Board item SVG should NOT start with card_padding translate"
        )
