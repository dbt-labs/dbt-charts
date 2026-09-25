"""Tests for layout sizing calculation.

Tests the dbt_charts.core.render.sizing module for calculating dimensions
of layout items, with a focus on the cols-with-nested-rows scenario
that ensures proper height equalization, and render-ready sizing fields.
"""

import dataclasses
import importlib as _importlib

import pytest
from pydantic import TypeAdapter

# The direct import `import dbt_charts.core.render.converters.chart` fails because
# dbt_charts.core imports `render` as a function, shadowing the render subpackage
# in the attribute chain. importlib.import_module bypasses this.
_converters_chart = _importlib.import_module("dbt_charts.core.render.converters.chart")

from collections.abc import Callable
from pathlib import Path

from dbt_charts.cli.filesystem_project import FilesystemProject
from dbt_charts.core.compile import compile
from dbt_charts.core.compile.config import (
    get_config,
    get_default_theme_name,
    get_theme_style,
)
from dbt_charts.core.compile.models.board.normalized import Board
from dbt_charts.core.compile.models.chart.normalized import KpiChart, TableChart
from dbt_charts.core.compile.resolve.style.board import (
    resolve_chart_style_context,
    resolve_style,
    resolve_style_and_context,
)
from dbt_charts.core.execute.adapters import build_adapter_registry
from dbt_charts.core.render.sizing import get_chart_content_height
from mdsvg.fonts import FontFaces

from ._board_utils import (
    _default_chart_style_context,
    _default_resolved_style,
    apply_static_layout,
)
from ._svg_render import cumulative_ink_x as _cumulative_ink_x

_DEFAULT_STYLE = resolve_style(get_theme_style())


class TestColsWithNestedRows:
    """Tests for the cols-with-nested-rows scenario.

    This is the critical scenario from the sizing strategy:

    cols:
      - chart1           # Left half
      - rows:            # Right half, split vertically
          - chart2
          - chart3

    Visual result:
    ┌─────────────────────┬──────────────┐
    │                     │   Chart 2    │
    │      Chart 1        ├──────────────┤
    │     (50% width)     │   Chart 3    │
    │                     │  (50% width) │
    └─────────────────────┴──────────────┘

    Key: Chart 1 must have the same height as the combined Chart 2+3 container.
    """

    def test_cols_with_nested_rows_height_equalization(self):
        """Test that items in cols layout get equal height, including nested rows."""
        yaml_content = """
title: Height Equalization Test
queries:
  sales:
    type: values
    rows:
      - {month: Jan, revenue: 100}
      - {month: Feb, revenue: 150}
charts:
  chart1:
    query: sales
    type: bar
    x: month
    y: revenue
  chart2:
    query: sales
    type: line
    x: month
    y: revenue
  chart3:
    query: sales
    type: area
    x: month
    y: revenue
cols:
  - chart1
  - rows:
      - chart2
      - chart3
"""
        result = compile(yaml_content)
        assert result.success, f"Compile failed: {result.errors}"
        assert result.board is not None

        # Apply layout sizing
        board = apply_static_layout(result.board)

        # Get the two items in the cols layout
        assert len(board.layout.items) == 2
        chart1_item = board.layout.items[0]
        nested_board_item = board.layout.items[1]

        # Both items should have the same height (they're in cols)
        assert chart1_item.height == nested_board_item.height
        assert chart1_item.height > 0

        # Both items should have equal width (50% each)
        assert chart1_item.width == nested_board_item.width
        assert chart1_item.width_fraction == 0.5
        assert nested_board_item.width_fraction == 0.5

        # Check nested board has charts with correct dimensions
        assert nested_board_item.board is not None
        nested_layout = nested_board_item.board.layout
        assert nested_layout.type == "rows"
        assert len(nested_layout.items) == 2

        # Each nested chart should have half the height of the parent
        chart2_item = nested_layout.items[0]
        chart3_item = nested_layout.items[1]

        # Both nested charts should have equal height
        assert chart2_item.height == chart3_item.height
        assert chart2_item.height > 0

        # Nested charts together should roughly equal the parent height.
        # The nested rows board uses child_gap from resolved_style (0 by default),
        # so chart2+chart3 tile exactly to fill the nested board height.
        total_nested_height = chart2_item.height + chart3_item.height
        assert total_nested_height <= nested_board_item.height
        assert total_nested_height > nested_board_item.height * 0.9


class TestRowsLayout:
    """Tests for rows layout sizing."""

    def test_rows_layout_distributes_height(self):
        """Test that rows layout distributes height equally."""
        yaml_content = """
title: Rows Test
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
  c2:
    query: q
    type: line
    x: month
    y: revenue
rows:
  - c1
  - c2
"""
        result = compile(yaml_content)
        assert result.success

        board = apply_static_layout(result.board)

        # Check items have calculated dimensions
        assert len(board.layout.items) == 2
        item1 = board.layout.items[0]
        item2 = board.layout.items[1]

        # Both should have full width
        assert item1.width_fraction == 1.0
        assert item2.width_fraction == 1.0

        # Both should have equal height
        assert item1.height == item2.height
        assert item1.height > 0


class TestColsLayout:
    """Tests for cols layout sizing."""

    def test_cols_layout_distributes_width(self):
        """Test that cols layout distributes width equally and sets same height."""
        yaml_content = """
title: Cols Test
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
  c2:
    query: q
    type: line
    x: month
    y: revenue
cols:
  - c1
  - c2
"""
        result = compile(yaml_content)
        assert result.success

        board = apply_static_layout(result.board)

        # Check items have calculated dimensions
        assert len(board.layout.items) == 2
        item1 = board.layout.items[0]
        item2 = board.layout.items[1]

        # Both should have equal width (50%)
        assert item1.width_fraction == 0.5
        assert item2.width_fraction == 0.5
        assert item1.width == item2.width

        # KEY: Both should have the SAME height (container height)
        assert item1.height == item2.height
        assert item1.height > 0

    def test_mixed_auto_and_specified_cols_do_not_collapse_auto_item(self):
        """Auto cols keep visible width when mixed with over-constrained specified siblings."""
        yaml_content = """
title: Mixed Col Widths
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
  c2:
    query: q
    type: line
    x: month
    y: revenue
  c3:
    query: q
    type: area
    x: month
    y: revenue
cols:
  - c1
  - width: 30%
    rows:
      - c2
  - width: 70%
    rows:
      - c3
"""
        result = compile(yaml_content)
        assert result.success

        board = apply_static_layout(result.board)

        auto_item = board.layout.items[0]
        middle_item = board.layout.items[1]
        right_item = board.layout.items[2]

        assert auto_item.width > 0
        assert middle_item.width > 0
        assert right_item.width > 0
        assert auto_item.x == 0.0
        assert middle_item.x > auto_item.x
        assert right_item.x > middle_item.x
        assert middle_item.width < right_item.width
        total_fraction = (
            auto_item.width_fraction
            + middle_item.width_fraction
            + right_item.width_fraction
        )
        assert round(total_fraction, 6) == 1.0


class TestGridLayout:
    """Tests for grid layout sizing."""

    def test_grid_item_dimensions(self):
        """Test that grid items get correct dimensions based on span."""
        # Create a simple board with grid layout directly
        from dbt_charts.core.compile.models.board.normalized import Layout, LayoutItem

        # Create mock items with grid positions
        item1 = LayoutItem(
            type="chart",
            col=0,
            row=0,
            col_span=12,  # 12 columns wide
            row_span=2,  # 2 rows tall
        )
        item2 = LayoutItem(
            type="chart",
            col=12,
            row=0,
            col_span=12,  # 12 columns wide
            row_span=1,  # 1 row tall
        )

        layout = Layout(type="grid", columns=24, items=[item1, item2])

        compiled_board = Board(
            id="test",
            layout=layout,
            resolved_style=_default_resolved_style(),
            chart_style_context=_default_chart_style_context(),
            level=0,
        )

        # Apply sizing
        compiled_board = apply_static_layout(compiled_board)

        # Item1 should have dimensions calculated
        assert compiled_board.layout.items[0].width > 0
        assert compiled_board.layout.items[0].height > 0


class TestTabsLayout:
    """Tests for tabs layout sizing."""

    def test_tabs_items_get_full_size(self):
        """Test that each tab item gets full container size minus tab bar."""
        yaml_content = """
title: Tabs Test
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
  c2:
    query: q
    type: line
    x: month
    y: revenue
tabs:
  items:
    - title: "Tab 1"
      rows:
        - c1
    - title: "Tab 2"
      rows:
        - c2
"""
        result = compile(yaml_content)
        assert result.success, f"Compile failed: {result.errors}"

        board = apply_static_layout(result.board)

        # Check items have calculated dimensions
        for item in board.layout.items:
            # All tabs should have full width
            assert item.width_fraction == 1.0
            assert item.width > 0
            # All tabs should have same height (full height minus tab bar)
            assert item.height > 0

    def test_tabs_size_to_active_tab_not_union(self):
        """A tabbed board must reserve the active tab's height, not the tallest.

        Before the fix, both tabs below rendered at the SAME height (the
        union of one chart and nine charts). The default tab ("One", a
        single chart) must now size shorter than the nine-chart tab.
        """
        yaml_content = """
title: Test
queries:
  q:
    type: values
    rows:
      - {a: 1}
charts:
  c1: {query: q, type: table}
  c2: {query: q, type: table}
  c3: {query: q, type: table}
  c4: {query: q, type: table}
  c5: {query: q, type: table}
  c6: {query: q, type: table}
  c7: {query: q, type: table}
  c8: {query: q, type: table}
  c9: {query: q, type: table}
tabs:
  id: t
  default: One
  items:
    - title: One
      rows: [{cols: [c1]}]
    - title: Nine
      rows:
        - {cols: [c2, c3, c4]}
        - {cols: [c5, c6, c7]}
        - {cols: [c8, c9, c1]}
"""
        result = compile(yaml_content)
        assert result.success, f"Compile failed: {result.errors}"

        board = apply_static_layout(result.board)

        assert board.layout.content_height < 400.0, (
            f"default tab ('One', a single chart) reserved "
            f"{board.layout.content_height}px — sizing must measure the "
            f"active tab's own content, not the union with the nine-chart tab"
        )


class TestWidthFraction:
    """Tests for width fraction calculation."""

    def test_width_fraction_calculated_correctly(self):
        """Test that width_fraction is set correctly for various layouts."""
        yaml_content = """
title: Width Fraction Test
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
  c2:
    query: q
    type: line
    x: month
    y: revenue
  c3:
    query: q
    type: area
    x: month
    y: revenue
cols:
  - c1
  - c2
  - c3
"""
        result = compile(yaml_content)
        assert result.success

        board = apply_static_layout(result.board)

        # Three items should each get 1/3 width
        for item in board.layout.items:
            assert abs(item.width_fraction - (1.0 / 3.0)) < 0.001


class TestNestedLayoutDimensions:
    """Tests for nested layout dimension propagation."""

    def test_nested_layout_receives_parent_dimensions(self):
        """Test that nested layouts receive dimensions from parent."""
        yaml_content = """
title: Nested Dimensions Test
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
  c2:
    query: q
    type: line
    x: month
    y: revenue
cols:
  - rows:
      - c1
      - c2
"""
        result = compile(yaml_content)
        assert result.success, f"Compile failed: {result.errors}"

        board = apply_static_layout(result.board)

        # Get nested board
        nested_item = board.layout.items[0]
        assert nested_item.board is not None

        # Nested board should have layout dimensions set
        nested_board = nested_item.board
        assert nested_board.layout.width > 0
        assert nested_board.layout.height > 0

        # Nested board dimensions should match parent item dimensions
        assert nested_board.layout.width == nested_item.width
        assert nested_board.layout.height == nested_item.height


class TestContentBoxAccounting:
    """Tests for root and nested board content-box sizing."""

    def test_preferred_width_measure_respects_layout_direction(self):
        from dbt_charts.core.compile.models.board.normalized import Layout, LayoutItem
        from dbt_charts.core.compile.models.chart.normalized import KpiChart
        from dbt_charts.core.compile.sizing import measure_preferred_layout_width

        first = KpiChart(
            id="first", type="kpi", value="value", style={"preferred_width": 300.0}
        )
        second = KpiChart(
            id="second", type="kpi", value="value", style={"preferred_width": 600.0}
        )
        style = _default_resolved_style()
        ctx = _default_chart_style_context()
        items = [
            LayoutItem(type="chart", chart=first),
            LayoutItem(type="chart", chart=second),
        ]

        assert (
            measure_preferred_layout_width(
                Layout(type="rows", items=items),
                style,
                ctx,
                max_content_width=1152.0,
                gap=16.0,
                card_gap=0.0,
            )
            == 600.0
        )
        assert (
            measure_preferred_layout_width(
                Layout(type="cols", items=items),
                style,
                ctx,
                max_content_width=1152.0,
                gap=16.0,
                card_gap=0.0,
            )
            == 916.0
        )
        assert (
            measure_preferred_layout_width(
                Layout(type="tabs", items=items),
                style,
                ctx,
                max_content_width=1152.0,
                gap=16.0,
                card_gap=0.0,
            )
            == 600.0
        )

    def test_preferred_width_measure_respects_grid_spans_and_wrapper_widths(self):
        from dbt_charts.core.compile.models.board.normalized import Layout, LayoutItem
        from dbt_charts.core.compile.models.chart.normalized import KpiChart
        from dbt_charts.core.compile.sizing import measure_preferred_layout_width

        first = KpiChart(
            id="first", type="kpi", value="value", style={"preferred_width": 300.0}
        )
        second = KpiChart(
            id="second", type="kpi", value="value", style={"preferred_width": 300.0}
        )
        style = _default_resolved_style()
        ctx = _default_chart_style_context()
        grid_items = [
            LayoutItem(type="chart", chart=first, col=0, col_span=12),
            LayoutItem(type="chart", chart=second, col=12, col_span=12),
        ]

        assert (
            measure_preferred_layout_width(
                Layout(type="grid", items=grid_items, columns=24),
                style,
                ctx,
                max_content_width=1152.0,
                gap=16.0,
                card_gap=0.0,
            )
            == 616.0
        )

        wrapped = [
            LayoutItem(type="chart", chart=first, user_width="50%"),
            LayoutItem(type="chart", chart=second, user_width="200px"),
        ]
        assert (
            measure_preferred_layout_width(
                Layout(type="cols", items=wrapped),
                style,
                ctx,
                max_content_width=1000.0,
                gap=16.0,
                card_gap=0.0,
            )
            == 716.0
        )

    def test_sparse_grid_measure_preserves_item_preferred_width(self):
        from dbt_charts.core.compile.models.board.normalized import Layout, LayoutItem
        from dbt_charts.core.compile.models.chart.normalized import KpiChart
        from dbt_charts.core.compile.sizing import measure_preferred_layout_width
        from dbt_charts.core.render.sizing import _calculate_grid_dimensions

        chart = KpiChart(
            id="metric", type="kpi", value="value", style={"preferred_width": 300.0}
        )
        item = LayoutItem(
            type="chart", chart=chart, col=0, row=0, col_span=6, row_span=1
        )
        layout = Layout(type="grid", items=[item], columns=24)
        measured = measure_preferred_layout_width(
            layout,
            _default_resolved_style(),
            _default_chart_style_context(),
            max_content_width=2000.0,
            gap=16.0,
            card_gap=0.0,
        )

        assert measured == 1248.0
        _calculate_grid_dimensions(
            layout.items,
            measured,
            600.0,
            columns=24,
            card_gap=0.0,
            gap=16.0,
            resolved_style=resolve_style(get_theme_style()),
        )
        assert item.width == 300.0

    def test_preferred_width_measure_returns_none_without_charts(self):
        from dbt_charts.core.compile.models.board.normalized import Layout
        from dbt_charts.core.compile.sizing import measure_preferred_layout_width

        assert (
            measure_preferred_layout_width(
                Layout(type="rows", items=[]),
                _default_resolved_style(),
                _default_chart_style_context(),
                max_content_width=1152.0,
                gap=16.0,
                card_gap=0.0,
            )
            is None
        )

    def test_preferred_width_measure_drops_cols_gap_for_chart_free_item(self):
        from dbt_charts.core.compile.sizing import measure_preferred_layout_width

        result = compile(
            """
queries:
  q:
    type: values
    rows:
      - value: 1
charts:
  metric:
    query: q
    type: kpi
    value: value
    style:
      preferred_width: 300
cols:
  - metric
  - text: Context
"""
        )
        assert result.success, result.errors
        assert result.board is not None

        # The text item is auto-width (no chart preference, no explicit width): it
        # contributes neither a measured width nor a gap, so the intrinsic width is
        # just the kpi's 300px preference — no gap added.
        assert (
            measure_preferred_layout_width(
                result.board.layout,
                result.board.resolved_style,
                result.board.chart_style_context,
                max_content_width=1000.0,
                gap=16.0,
                card_gap=0.0,
            )
            == 300.0
        )

    def test_preferred_width_measure_adds_nested_board_chrome(self):
        from dbt_charts.core.compile.sizing import measure_preferred_layout_width

        result = compile(
            """
queries:
  q:
    type: values
    rows:
      - value: 1
charts:
  metric:
    query: q
    type: kpi
    value: value
    style:
      preferred_width: 300
rows:
  - style:
      padding: 10px
      margin: 5px
    rows:
      - metric
"""
        )
        assert result.success, result.errors
        assert result.board is not None

        assert (
            measure_preferred_layout_width(
                result.board.layout,
                result.board.resolved_style,
                result.board.chart_style_context,
                max_content_width=1000.0,
                gap=16.0,
                card_gap=0.0,
            )
            == 330.0
        )

    def test_root_layout_items_use_root_content_width(self):
        """Root layout items should size against the padded content box."""
        yaml_content = """
title: Root Content Width Test
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
        assert result.board is not None

        board = apply_static_layout(result.board)
        expected_content_width = get_theme_style(
            get_default_theme_name()
        ).frame.max_width - (2 * get_theme_style().frame.margin)

        assert board.layout.width == get_theme_style().frame.max_width
        assert board.layout.content_width == expected_content_width
        assert board.layout.items[0].width == expected_content_width

    def test_inset_thick_borders_still_reserve_full_stroke_width(self):
        """Inset thick borders should still reserve the full stroke width."""
        yaml_content = """
title: Border Accounting Test
rows:
  - title: Thick Border Card
    style:
      border: "8px solid #1976d2"
    text: |
      This nested board should not lose a full extra border width on each side.
"""
        result = compile(yaml_content)
        assert result.success
        assert result.board is not None

        board = apply_static_layout(result.board)
        nested_item = board.layout.items[0]
        assert nested_item.board is not None

        nested_board = nested_item.board
        assert nested_board.layout.content_width == nested_item.width - 16.0


class TestRenderReadySizing:
    """Tests for render-ready sizing fields (calculated_width, calculated_height, aspect_ratio).

    These fields preserve calculated pixel dimensions for consistent rendering
    across SVG and HTML outputs, and enable responsive CSS rendering.
    """

    def test_render_ready_sizing_fields_are_set(self):
        """Test that calculated_width, calculated_height, and aspect_ratio are set on layout items."""
        yaml_content = """
title: Render Ready Sizing Test
queries:
  sales:
    type: values
    rows:
      - {month: Jan, revenue: 100}
      - {month: Feb, revenue: 150}
charts:
  chart1:
    query: sales
    type: bar
    x: month
    y: revenue
  chart2:
    query: sales
    type: line
    x: month
    y: revenue
cols:
  - chart1
  - chart2
"""
        result = compile(yaml_content)
        assert result.success, f"Compile failed: {result.errors}"
        assert result.board is not None

        # Apply layout sizing
        board = apply_static_layout(result.board)

        # Check that render-ready sizing fields are set on all items
        for item in board.layout.items:
            # calculated_width and calculated_height should be set
            assert item.calculated_width is not None, "calculated_width should be set"
            assert item.calculated_height is not None, "calculated_height should be set"
            assert item.calculated_width > 0, "calculated_width should be positive"
            assert item.calculated_height > 0, "calculated_height should be positive"

            # calculated_width/height should match width/height
            assert item.calculated_width == item.width, (
                "calculated_width should match width"
            )
            assert item.calculated_height == item.height, (
                "calculated_height should match height"
            )

            # aspect_ratio should be calculated
            assert item.aspect_ratio is not None, "aspect_ratio should be set"
            assert item.aspect_ratio > 0, "aspect_ratio should be positive"
            assert (
                abs(
                    item.aspect_ratio - (item.calculated_width / item.calculated_height)
                )
                < 0.001
            ), "aspect_ratio should equal calculated_width / calculated_height"

    def test_render_ready_sizing_with_user_specified_width(self):
        """Test that render-ready sizing works with user-specified widths (percentages)."""
        yaml_content = """
title: Render Ready Sizing with User Width
queries:
  sales:
    type: values
    rows:
      - {month: Jan, revenue: 100}
      - {month: Feb, revenue: 150}
charts:
  chart1:
    query: sales
    type: bar
    x: month
    y: revenue
  chart2:
    query: sales
    type: line
    x: month
    y: revenue
cols:
  - width: 30%
    rows:
      - chart1
  - width: 200px
    rows:
      - chart2
"""
        result = compile(yaml_content)
        assert result.success, f"Compile failed: {result.errors}"
        assert result.board is not None

        # Apply layout sizing
        board = apply_static_layout(result.board)

        # Check that render-ready sizing fields are set correctly
        chart1_item = board.layout.items[0]
        chart2_item = board.layout.items[1]

        # Both should have render-ready sizing fields
        assert chart1_item.calculated_width is not None
        assert chart1_item.calculated_height is not None
        assert chart1_item.aspect_ratio is not None

        assert chart2_item.calculated_width is not None
        assert chart2_item.calculated_height is not None
        assert chart2_item.aspect_ratio is not None

        # Chart 2 should have width close to 200px (accounting for gaps)
        # Chart 1 should have width approximately 30% of available space
        assert chart2_item.calculated_width <= 210, (
            "Chart 2 width should be close to 200px"
        )
        assert (
            chart1_item.calculated_width < chart2_item.calculated_width
            or abs(chart1_item.calculated_width - (board.layout.width * 0.3)) < 50
        ), "Chart 1 width should be approximately 30%"


class TestVariableResolutionInSizing:
    """Tests that variables are resolved during sizing for correct height calculation."""

    def test_markdown_with_variables_resolved_during_sizing(self):
        """Test that markdown text with variables is resolved during sizing.

        Variables should be resolved using their defaults so that the height
        calculation is based on the actual rendered text, not the template.
        """
        from dbt_charts.core.render.sizing import get_markdown_text_height

        # Without variable resolution, "Hello {{name}}" is 15 chars
        # With resolution to "World", "Hello World" is 11 chars
        # The height should be based on resolved text

        text_with_var = "Hello {{name}}"
        text_resolved = "Hello World"

        # Without variables - uses literal template
        height_no_vars = get_markdown_text_height(
            text_with_var,
            width=800.0,
            text_style=_DEFAULT_STYLE.text,
            resolved_style=_DEFAULT_STYLE,
        )

        # With variables - resolves template
        height_with_vars = get_markdown_text_height(
            text_with_var,
            width=800.0,
            variable_values={"name": "World"},
            text_style=_DEFAULT_STYLE.text,
            resolved_style=_DEFAULT_STYLE,
        )

        # Height with resolved text
        height_resolved = get_markdown_text_height(
            text_resolved,
            width=800.0,
            text_style=_DEFAULT_STYLE.text,
            resolved_style=_DEFAULT_STYLE,
        )

        # With variables, height should match the resolved text height
        assert abs(height_with_vars - height_resolved) < 1.0, (
            "Height with vars should match resolved text height"
        )

        # Heights should all be positive (sanity check)
        assert height_no_vars > 0, "Height without vars should be positive"
        assert height_with_vars > 0, "Height with vars should be positive"

    def test_title_with_variables_resolved_during_sizing(self):
        """Test that titles with variables are resolved during sizing."""
        from dbt_charts.core.render.sizing import get_title_height

        title_with_var = "Dashboard for {{region}}"
        title_resolved = "Dashboard for North America"

        # With variables - resolves template
        height_with_vars = get_title_height(
            title_with_var, width=800.0, variable_values={"region": "North America"}
        )

        # Height with resolved text
        height_resolved = get_title_height(title_resolved, width=800.0)

        # With variables, height should match the resolved text height
        assert abs(height_with_vars - height_resolved) < 1.0, (
            "Title height with vars should match resolved title height"
        )

    def test_title_height_uses_title_font_path(self, monkeypatch):
        """Title layout measurement matches render_title: style.title.font.family,
        never the body family, whatever text.font.family happens to be set to.
        """
        import mdsvg
        from dbt_charts.core.compile.models.primitives import FontStyle
        from dbt_charts.core.fonts import SOURCE_SERIF_4_FONT_FAMILY, get_face
        from dbt_charts.core.render.sizing import get_title_height

        captured: dict[str, str] = {}

        class FakeSize:
            height = 42.0

        def fake_measure(
            *_args: object, fonts: FontFaces, **_kwargs: object
        ) -> FakeSize:
            captured["regular"] = fonts.regular.path
            return FakeSize()

        monkeypatch.setattr(mdsvg, "measure", fake_measure)

        base = get_theme_style()
        seed = base.model_copy(
            update={
                "text": base.text.model_copy(
                    update={
                        "font": FontStyle(
                            family="'Inter Variable', Inter, system-ui, sans-serif"
                        )
                    }
                ),
                "title": base.title.model_copy(
                    update={"font": FontStyle(family="Source Serif 4, Georgia, serif")}
                ),
            }
        )
        style = resolve_style(seed)

        height = get_title_height(
            "Measured in the title family", 400.0, resolved_style=style
        )

        assert height == 42.0
        assert captured["regular"] == str(
            get_face(SOURCE_SERIF_4_FONT_FAMILY).measure_path
        )

    def test_apply_static_layout_uses_variable_registry(self):
        """Static sizing pass uses variable_registry (variable_defaults) for Jinja sizing."""
        yaml_content = """
variables:
  region:
    input: select
    default: North America
    options:
      static: [North America, Europe, Asia Pacific]
text: |
  ## Sales Report for {{region}}

  This dashboard shows sales data for the selected region.
  The {{region}} market is performing well this quarter.

queries:
  sales:
    type: values
    rows:
      - {month: Jan, revenue: 100}
      - {month: Feb, revenue: 150}
charts:
  chart1:
    query: sales
    type: bar
    x: month
    y: revenue
rows:
  - chart1
"""
        result = compile(yaml_content)
        assert result.success, f"Compile failed: {result.errors}"
        assert result.board is not None

        # Verify variable_registry is populated
        assert result.board.variable_registry is not None
        assert "region" in result.board.variable_registry
        assert result.board.variable_registry["region"].default == "North America"

        # Apply layout sizing
        board = apply_static_layout(result.board)

        # The layout height should be calculated correctly
        # (if variables weren't resolved, the height calculation would use
        # literal "{{region}}" which is shorter than "North America")
        assert board.layout.height > 0, "Layout height should be positive"

        # The text mentions {{region}} twice, which expands to "North America"
        # This should result in a reasonable height for the text
        # We can't easily test the exact height, but we can verify it's reasonable
        assert board.layout.height > 300, "Layout should have reasonable height"

    def test_jinja_comments_stripped_during_sizing(self):
        """Test that Jinja comments {# ... #} are stripped during sizing.

        Comments should not contribute to the rendered height - the sizing
        should be based on the visible text only.
        """
        from dbt_charts.core.render.sizing import get_markdown_text_height

        # Content with a Jinja comment
        text_with_comment = """{# This is a hidden comment that should not appear #}
Hello World

{# Another comment
   spanning multiple lines #}
This is visible text."""

        # Same text without comments
        text_without_comment = """
Hello World

This is visible text."""

        height_with_comment = get_markdown_text_height(
            text_with_comment,
            width=800.0,
            text_style=_DEFAULT_STYLE.text,
            resolved_style=_DEFAULT_STYLE,
        )
        height_without_comment = get_markdown_text_height(
            text_without_comment,
            width=800.0,
            text_style=_DEFAULT_STYLE.text,
            resolved_style=_DEFAULT_STYLE,
        )

        # Heights should be the same since comments are stripped
        assert abs(height_with_comment - height_without_comment) < 1.0, (
            "Height should be same with or without Jinja comments"
        )

    def test_jinja_block_tags_resolved_during_sizing(self):
        """Test that Jinja block tags {% ... %} are processed during sizing.

        Control flow tags like {% if %} should be evaluated, affecting
        what text is included in the height calculation.
        """
        from dbt_charts.core.render.sizing import get_markdown_text_height

        # Content with Jinja if block
        text_with_if = """{% if show_header %}
# Big Header

This is extra text that only shows when show_header is true.
{% endif %}
Main text here."""

        # When show_header is True, we get the header
        height_with_header = get_markdown_text_height(
            text_with_if,
            width=800.0,
            variable_values={"show_header": True},
            text_style=_DEFAULT_STYLE.text,
            resolved_style=_DEFAULT_STYLE,
        )

        # When show_header is False, we don't get the header
        height_without_header = get_markdown_text_height(
            text_with_if,
            width=800.0,
            variable_values={"show_header": False},
            text_style=_DEFAULT_STYLE.text,
            resolved_style=_DEFAULT_STYLE,
        )

        # Height with header should be significantly larger
        assert height_with_header > height_without_header, (
            "Height should be larger when if-block text is included"
        )

        # Both should be positive
        assert height_with_header > 0
        assert height_without_header > 0

    def test_variable_with_long_value_increases_height(self):
        """Test that variables expanding to long values correctly increase height.

        When a short template like {{description}} expands to a long paragraph,
        the height should increase due to word wrapping.
        """
        from dbt_charts.core.render.sizing import get_markdown_text_height

        text = "## Summary\n\n{{description}}"

        # Short value - fits on one line
        short_value = "Brief summary."

        # Long value - will wrap to multiple lines at narrow width
        long_value = (
            "This is a very long description that contains multiple sentences "
            "and will definitely need to wrap across several lines when rendered "
            "at a narrow width. The text measurement and word wrapping logic "
            "should handle this correctly, resulting in a taller rendered height."
        )

        # Use a narrow width to force wrapping
        narrow_width = 300.0

        height_short = get_markdown_text_height(
            text,
            width=narrow_width,
            variable_values={"description": short_value},
            text_style=_DEFAULT_STYLE.text,
            resolved_style=_DEFAULT_STYLE,
        )

        height_long = get_markdown_text_height(
            text,
            width=narrow_width,
            variable_values={"description": long_value},
            text_style=_DEFAULT_STYLE.text,
            resolved_style=_DEFAULT_STYLE,
        )

        # Long value should result in greater height due to wrapping
        assert height_long > height_short, (
            "Long variable value should result in greater height"
        )

        # The difference should be substantial (multiple lines)
        assert height_long > height_short * 1.5, (
            "Long value should be significantly taller (multiple lines)"
        )

    def test_title_with_jinja_comment_stripped(self):
        """Test that Jinja comments in titles are stripped during sizing."""
        from dbt_charts.core.render.sizing import get_title_height

        title_with_comment = "Dashboard Title {# internal note #}"
        title_clean = "Dashboard Title "

        height_with_comment = get_title_height(title_with_comment, width=800.0)
        height_clean = get_title_height(title_clean, width=800.0)

        # Heights should be essentially the same
        assert abs(height_with_comment - height_clean) < 1.0, (
            "Title height should be same with or without Jinja comment"
        )


class TestMeasureVsRenderConsistency:
    """Tests that mdsvg measure() and render() return consistent heights.

    We use measure() for performance in sizing, but need to ensure it gives
    the same height as render() would produce.
    """

    # Tolerance for floating point comparison (sub-pixel precision)
    TOLERANCE = 0.01

    def _get_render_height(self, text: str, width: float, style) -> float:
        """Extract height from rendered SVG."""
        import re

        from mdsvg import render

        svg = render(text, width=width, padding=0.0, style=style)
        height_match = re.search(r'height="([0-9.]+)"', svg)
        return float(height_match.group(1)) if height_match else 0.0

    def _get_measure_height(self, text: str, width: float, style) -> float:
        """Get height from measure()."""
        from mdsvg import measure

        size = measure(text, width=width, padding=0.0, style=style)
        return size.height

    def _assert_heights_equal(
        self, render_height: float, measure_height: float, msg: str
    ):
        """Assert heights are equal within floating point tolerance."""
        assert abs(render_height - measure_height) < self.TOLERANCE, (
            f"{msg}: render={render_height}, measure={measure_height}"
        )

    def test_simple_paragraph(self):
        """Test that simple paragraph gives same height."""
        from dbt_charts.core.render.sizing import get_compact_style

        text = "This is a simple paragraph of text."
        width = 400.0
        style = get_compact_style(resolve_style(get_theme_style()))

        render_height = self._get_render_height(text, width, style)
        measure_height = self._get_measure_height(text, width, style)

        self._assert_heights_equal(
            render_height, measure_height, "Height mismatch for simple paragraph"
        )

    def test_heading(self):
        """Test that headings give same height."""
        from dbt_charts.core.render.sizing import get_compact_style

        text = "# Main Heading"
        width = 400.0
        style = get_compact_style(resolve_style(get_theme_style()))

        render_height = self._get_render_height(text, width, style)
        measure_height = self._get_measure_height(text, width, style)

        self._assert_heights_equal(
            render_height, measure_height, "Height mismatch for heading"
        )

    def test_multi_level_headings(self):
        """Test that multiple heading levels give same height."""
        from dbt_charts.core.render.sizing import get_compact_style

        text = """# H1 Title

## H2 Section

### H3 Subsection

#### H4 Detail"""
        width = 600.0
        style = get_compact_style(resolve_style(get_theme_style()))

        render_height = self._get_render_height(text, width, style)
        measure_height = self._get_measure_height(text, width, style)

        self._assert_heights_equal(
            render_height, measure_height, "Height mismatch for multi-level headings"
        )

    def test_bullet_list(self):
        """Test that bullet lists give same height."""
        from dbt_charts.core.render.sizing import get_compact_style

        text = """- First item
- Second item with **bold**
- Third item with *italic*
- Fourth item"""
        width = 400.0
        style = get_compact_style(resolve_style(get_theme_style()))

        render_height = self._get_render_height(text, width, style)
        measure_height = self._get_measure_height(text, width, style)

        self._assert_heights_equal(
            render_height, measure_height, "Height mismatch for bullet list"
        )

    def test_numbered_list(self):
        """Test that numbered lists give same height."""
        from dbt_charts.core.render.sizing import get_compact_style

        text = """1. First item
2. Second item
3. Third item with longer text that might wrap"""
        width = 300.0
        style = get_compact_style(resolve_style(get_theme_style()))

        render_height = self._get_render_height(text, width, style)
        measure_height = self._get_measure_height(text, width, style)

        self._assert_heights_equal(
            render_height, measure_height, "Height mismatch for numbered list"
        )

    def test_table(self):
        """Test that tables give same height."""
        from dbt_charts.core.render.sizing import get_compact_style

        text = """| Column 1 | Column 2 | Column 3 |
|----------|----------|----------|
| Data A   | Data B   | Data C   |
| Data D   | Data E   | Data F   |"""
        width = 500.0
        style = get_compact_style(resolve_style(get_theme_style()))

        render_height = self._get_render_height(text, width, style)
        measure_height = self._get_measure_height(text, width, style)

        self._assert_heights_equal(
            render_height, measure_height, "Height mismatch for table"
        )

    def test_blockquote(self):
        """Test that blockquotes give same height."""
        from dbt_charts.core.render.sizing import get_compact_style

        text = """> This is a blockquote with some wisdom.
> It spans multiple lines."""
        width = 400.0
        style = get_compact_style(resolve_style(get_theme_style()))

        render_height = self._get_render_height(text, width, style)
        measure_height = self._get_measure_height(text, width, style)

        self._assert_heights_equal(
            render_height, measure_height, "Height mismatch for blockquote"
        )

    def test_code_block(self):
        """Test that code blocks give same height."""
        from dbt_charts.core.render.sizing import get_compact_style

        text = """```python
def hello():
    print("Hello, world!")
```"""
        width = 400.0
        style = get_compact_style(resolve_style(get_theme_style()))

        render_height = self._get_render_height(text, width, style)
        measure_height = self._get_measure_height(text, width, style)

        self._assert_heights_equal(
            render_height, measure_height, "Height mismatch for code block"
        )

    def test_inline_formatting(self):
        """Test that inline formatting (bold, italic, code) gives same height."""
        from dbt_charts.core.render.sizing import get_compact_style

        text = "Text with **bold**, *italic*, and `inline code` formatting."
        width = 400.0
        style = get_compact_style(resolve_style(get_theme_style()))

        render_height = self._get_render_height(text, width, style)
        measure_height = self._get_measure_height(text, width, style)

        self._assert_heights_equal(
            render_height, measure_height, "Height mismatch for inline formatting"
        )

    def test_word_wrapping_narrow_width(self):
        """Test that word wrapping at narrow width gives same height."""
        from dbt_charts.core.render.sizing import get_compact_style

        text = (
            "This is a longer paragraph that will definitely need to wrap "
            "across multiple lines when rendered at a narrow width."
        )
        width = 200.0  # Narrow to force wrapping
        style = get_compact_style(resolve_style(get_theme_style()))

        render_height = self._get_render_height(text, width, style)
        measure_height = self._get_measure_height(text, width, style)

        self._assert_heights_equal(
            render_height, measure_height, "Height mismatch for word wrapping"
        )

    def test_complex_document(self):
        """Test that a complex document with mixed text gives same height."""
        from dbt_charts.core.render.sizing import get_compact_style

        text = """# Dashboard Summary

This dashboard shows **key metrics** for the selected region.

## Highlights

- Revenue up 15% YoY
- Customer satisfaction at *94%*
- Active users: `1,247`

| Metric | Value | Change |
|--------|-------|--------|
| Revenue | $2.4M | +15% |
| Users | 1,247 | +8% |

> Note: Figures are preliminary.

```sql
SELECT * FROM metrics WHERE date > '2024-01-01'
```
"""
        width = 600.0
        style = get_compact_style(resolve_style(get_theme_style()))

        render_height = self._get_render_height(text, width, style)
        measure_height = self._get_measure_height(text, width, style)

        self._assert_heights_equal(
            render_height, measure_height, "Height mismatch for complex document"
        )

    def test_various_widths(self):
        """Test that heights match across various widths."""
        from dbt_charts.core.render.sizing import get_compact_style

        text = "A medium length paragraph that tests different widths."
        style = get_compact_style(resolve_style(get_theme_style()))

        for width in [200.0, 400.0, 600.0, 800.0, 1000.0]:
            render_height = self._get_render_height(text, width, style)
            measure_height = self._get_measure_height(text, width, style)

            self._assert_heights_equal(
                render_height, measure_height, f"Height mismatch at width {width}"
            )

    def test_empty_text(self):
        """Test that empty text gives same height (zero)."""
        from dbt_charts.core.render.sizing import get_compact_style

        text = ""
        width = 400.0
        style = get_compact_style(resolve_style(get_theme_style()))

        render_height = self._get_render_height(text, width, style)
        measure_height = self._get_measure_height(text, width, style)

        assert render_height == measure_height == 0.0, (
            f"Empty text should have zero height: "
            f"render={render_height}, measure={measure_height}"
        )


class TestAspectRatioDrivenSizing:
    """Tests for config-driven aspect ratio sizing.

    Plot-style charts should derive their height from width / aspect_ratio
    using values from default_config.yml. KPI and table charts are excluded.
    """

    @staticmethod
    def _chart(type: str = "bar", **kwargs):

        from dbt_charts.core.compile.models.chart.normalized import Chart

        defaults: dict = {"id": "test", "type": type}
        # Satisfy required fields for special chart types
        if type == "kpi":
            defaults.setdefault("value", "value")
        if type in ("pie", "donut"):
            defaults.setdefault("theta", "value")
        defaults.update(kwargs)
        return TypeAdapter(Chart).validate_python(defaults)

    def test_plot_chart_height_from_aspect_ratio(self):
        """Standard chart height = width / aspect_ratio from config."""
        expected_ratio = float(get_theme_style().charts.aspect_ratio)  # 1.5

        chart = self._chart("bar")
        width = 600.0
        height = get_chart_content_height(
            chart,
            width=width,
            resolved_style=resolve_style(get_theme_style()),
        )

        expected_height = width / expected_ratio
        assert abs(height - expected_height) < 0.01, (
            f"Bar chart height should be {expected_height} (width={width} / "
            f"aspect_ratio={expected_ratio}), got {height}"
        )

    def test_kpi_excluded_from_aspect_sizing(self):
        """KPI charts read height from the cascade, not aspect ratio."""
        resolved = resolve_style(get_theme_style())
        cascade_height = resolved.chart_defaults.kpi.default_height

        chart = self._chart("kpi")
        height = get_chart_content_height(chart, resolved_style=resolved)

        # KPI should use cascade height, not aspect-derived.
        assert height == cascade_height, (
            f"KPI height should be cascade value ({cascade_height}), got {height}"
        )

    def test_table_excluded_from_aspect_sizing(self):
        """Table charts use data-driven or fixed height, not aspect ratio."""
        chart = self._chart("table")
        height = get_chart_content_height(
            chart,
            width=600.0,
            resolved_style=resolve_style(get_theme_style()),
        )
        aspect_derived = 600.0 / float(get_theme_style().charts.aspect_ratio)

        # Table should use its fixed default, not aspect-derived
        assert height != aspect_derived, (
            f"Table height ({height}) must not match aspect-derived value "
            f"({aspect_derived})"
        )
        assert height > 0, f"Table height should be positive, got {height}"

    def _get_table_style_defaults(self):
        """Return (tc, ctx) using TableChartStyle — after table migration."""
        ctx = resolve_chart_style_context(get_theme_style())
        return ctx.table, ctx

    def _expected_title_height(self, chart, tc, width: float = 800.0) -> int:
        """Expected title-block height — calls the production helper.

        These pagination tests pin page_rows selection across default /
        override / inherit modes; the title-block term cancels out as a
        constant. The sizer/renderer cross-check lives in
        `test_data_aware_table_height_includes_subtitle_space`.
        """
        from dbt_charts.core.compile.resolve.style.typography import resolve_title_font
        from dbt_charts.core.render.chart.table import compute_table_title_block_layout

        _, charts_resolved = self._get_table_style_defaults()
        return compute_table_title_block_layout(
            chart_title=chart.title,
            chart_subtitle=chart.subtitle,
            table_width=width,
            tc=tc,
            padding=int(tc.outer_padding),
            title_style=charts_resolved.title,
            card_padding=float(resolve_style(get_theme_style()).frame.card_padding),
            title_font=resolve_title_font(charts_resolved, width),
        ).height

    def test_data_aware_table_height_uses_default_pagination(self):
        """Auto-sized tables should reserve space for the paginated row count."""
        from dbt_charts.core.compile.resolve import resolve
        from dbt_charts.core.render.chart.table import _PAGINATION_CONTROL_HEIGHT
        from dbt_charts.core.render.chart.table_support import reserve_header_band
        from dbt_charts.core.render.layout_sizing import _get_table_height_from_data

        class _Executor:
            @staticmethod
            def execute_chart(chart, variables):
                return [{"name": f"row{i}"} for i in range(100)]

        chart = self._chart("table", title="Paged Table")
        _rs, _ctx = resolve_style_and_context(get_theme_style())
        resolved = resolve(chart, [], _ctx)
        height = _get_table_height_from_data(
            chart,
            resolved,
            _Executor(),
            {},
            card_padding=float(_rs.frame.card_padding),
        )

        tc, charts_style = self._get_table_style_defaults()
        assert charts_style.pagination is not None
        row_height = int(tc.row.height)
        header_body_gap = int(row_height * 0.25)
        header_height = reserve_header_band(tc, int(tc.font.size))
        expected = (
            self._expected_title_height(chart, tc)
            + header_height
            + header_body_gap
            + (int(charts_style.pagination.page_rows) * row_height)
            + int(tc.outer_padding)
            + int(tc.bottom_padding)
            + _PAGINATION_CONTROL_HEIGHT  # multi-page tables include controls
        )
        assert height == expected

    def test_data_aware_table_height_respects_chart_pagination_override(self):
        """Chart-level pagination should override the default page size in layout sizing."""
        from dbt_charts.core.compile.resolve import resolve
        from dbt_charts.core.render.chart.table import _PAGINATION_CONTROL_HEIGHT
        from dbt_charts.core.render.chart.table_support import reserve_header_band
        from dbt_charts.core.render.layout_sizing import _get_table_height_from_data

        class _Executor:
            @staticmethod
            def execute_chart(chart, variables):
                return [{"name": f"row{i}"} for i in range(100)]

        chart = self._chart(
            "table",
            title="Paged Table",
            style={"pagination": {"enabled": True, "page_rows": 5}},
        )
        _rs, _ctx = resolve_style_and_context(get_theme_style())
        resolved = resolve(chart, [], _ctx)
        height = _get_table_height_from_data(
            chart,
            resolved,
            _Executor(),
            {},
            card_padding=float(_rs.frame.card_padding),
        )

        tc, _ = self._get_table_style_defaults()
        row_height = int(tc.row.height)
        header_body_gap = int(row_height * 0.25)
        header_height = reserve_header_band(tc, int(tc.font.size))
        expected = (
            self._expected_title_height(chart, tc)
            + header_height
            + header_body_gap
            + (5 * row_height)
            + int(tc.outer_padding)
            + int(tc.bottom_padding)
            + _PAGINATION_CONTROL_HEIGHT
        )
        assert height == expected

    def test_data_aware_table_height_chart_pagination_inherits_default_page_rows(self):
        """Enabled chart pagination without page_rows should inherit the default size."""
        from dbt_charts.core.compile.resolve import resolve
        from dbt_charts.core.render.chart.table import _PAGINATION_CONTROL_HEIGHT
        from dbt_charts.core.render.chart.table_support import reserve_header_band
        from dbt_charts.core.render.layout_sizing import _get_table_height_from_data

        class _Executor:
            @staticmethod
            def execute_chart(chart, variables):
                return [{"name": f"row{i}"} for i in range(100)]

        chart = self._chart(
            "table", title="Paged Table", style={"pagination": {"enabled": True}}
        )
        _rs, _ctx = resolve_style_and_context(get_theme_style())
        resolved = resolve(chart, [], _ctx)
        height = _get_table_height_from_data(
            chart,
            resolved,
            _Executor(),
            {},
            card_padding=float(_rs.frame.card_padding),
        )

        tc, charts_style = self._get_table_style_defaults()
        assert charts_style.pagination is not None
        row_height = int(tc.row.height)
        header_body_gap = int(row_height * 0.25)
        header_height = reserve_header_band(tc, int(tc.font.size))
        expected = (
            self._expected_title_height(chart, tc)
            + header_height
            + header_body_gap
            + (int(charts_style.pagination.page_rows) * row_height)
            + int(tc.outer_padding)
            + int(tc.bottom_padding)
            + _PAGINATION_CONTROL_HEIGHT
        )
        assert height == expected

    def test_data_aware_table_height_includes_header_body_gap(self):
        """Data-aware height must include the header-to-data gap to prevent bottom clipping."""
        from dbt_charts.core.compile.resolve import resolve
        from dbt_charts.core.render.chart.table_support import reserve_header_band
        from dbt_charts.core.render.layout_sizing import _get_table_height_from_data

        class _Executor:
            @staticmethod
            def execute_chart(chart, variables):
                return [{"name": f"row{i}"} for i in range(5)]

        chart = self._chart("table")  # no title, no pagination
        _rs, _ctx = resolve_style_and_context(get_theme_style())
        resolved = resolve(chart, [], _ctx)
        height = _get_table_height_from_data(
            chart,
            resolved,
            _Executor(),
            {},
            card_padding=float(_rs.frame.card_padding),
        )

        tc, _ = self._get_table_style_defaults()
        row_height = int(tc.row.height)
        header_body_gap = int(row_height * 0.25)
        header_height = reserve_header_band(tc, int(tc.font.size))
        expected = (
            header_height
            + header_body_gap
            + (5 * row_height)
            + int(tc.outer_padding)
            + int(tc.bottom_padding)
        )
        assert height == expected, (
            f"height {height} is missing header_body_gap={header_body_gap}; expected {expected}"
        )

    def test_data_aware_table_height_includes_subtitle_space(self):
        """Subtitle adds vertical space to the title block, so the sizer must
        reserve more chrome than `tc.title.height` when a subtitle is present.

        Regression: the sizer used `int(tc.title.height)` directly, but the
        renderer (table.py) expanded the title block to fit subtitle text. The
        slot ended up too short by ~one row, and the renderer truncated the last
        data row with a "+ N more rows" indicator (or paginated unexpectedly).

        Also asserts the sizer-allocated slot is tall enough to render the same
        chart without truncating any rows — catches drift between the sizer
        chrome calc and the renderer's actual layout, which is the bug class
        the shared helper exists to prevent.
        """
        from dbt_charts.core.compile.models.style.authored import (
            TableChartStylePatch,
        )
        from dbt_charts.core.compile.resolve import resolve
        from dbt_charts.core.render.chart.vega_lite import render_resolved_chart
        from dbt_charts.core.render.converters.chart import render_chart_artifact
        from dbt_charts.core.render.layout_sizing import _get_table_height_from_data

        _DATA = [{"name": f"row{i}", "value": i} for i in range(8)]

        class _Executor:
            @staticmethod
            def execute_chart(chart, variables):
                return _DATA

        chart_no_subtitle = self._chart(
            "table",
            title="Median Household Income",
            style={"pagination": {"enabled": False}},
        )
        chart_with_subtitle = self._chart(
            "table",
            title="Median Household Income",
            subtitle="Sequential blue scale on income (named palette)",
            style={"pagination": {"enabled": False}},
        )
        _resolved, _ctx = resolve_style_and_context(get_theme_style())
        resolved_no_subtitle = resolve(chart_no_subtitle, [], _ctx, width=560.0)
        resolved_with_subtitle = resolve(chart_with_subtitle, [], _ctx, width=560.0)
        height_no_subtitle = _get_table_height_from_data(
            chart_no_subtitle,
            resolved_no_subtitle,
            _Executor(),
            {},
            card_padding=float(_resolved.frame.card_padding),
            width=560.0,
        )
        height_with_subtitle = _get_table_height_from_data(
            chart_with_subtitle,
            resolved_with_subtitle,
            _Executor(),
            {},
            card_padding=float(_resolved.frame.card_padding),
            width=560.0,
        )
        assert height_with_subtitle > height_no_subtitle, (
            "Subtitle should add height to the title block; "
            f"got {height_with_subtitle} with subtitle vs {height_no_subtitle} without."
        )

        # Sizer-renderer agreement: render the subtitled chart at the size the
        # sizer allocated, confirm no row is truncated. Drifts between the
        # sizer's title-block math and the renderer's would clip rows here.
        table_v2 = TableChart(
            id="test",
            type="table",
            title="Median Household Income",
            subtitle="Sequential blue scale on income (named palette)",
            style=TableChartStylePatch(pagination={"enabled": False}),
        )
        resolved_table = resolve(table_v2, _DATA, chart_style_context=_ctx)
        artifact = render_resolved_chart(
            resolved_table,
            _DATA,
            _resolved,
            width=560.0,
            height=height_with_subtitle,
        )
        svg = render_chart_artifact(
            artifact,
            "svg",
            _resolved,
            width=560.0,
            height=height_with_subtitle,
            chart_id="chart",
        )
        assert "more rows" not in svg, (
            "Renderer truncated rows at sizer-allocated height — "
            "title-block chrome calc has drifted between sizer and renderer."
        )

    def test_per_type_aspect_ratio_override(self):
        """Chart types with their own aspect_ratio use it instead of global."""
        # pie's aspect_ratio lives on charts.pie.
        pie_ratio = float(get_theme_style().charts.pie.aspect_ratio or 0)
        assert pie_ratio > 0, "pie should have per-type aspect_ratio in config"

        chart = self._chart("pie")
        width = 400.0
        height = get_chart_content_height(
            chart,
            width=width,
            resolved_style=resolve_style(get_theme_style()),
        )

        expected_height = width / pie_ratio
        assert abs(height - expected_height) < 0.01, (
            f"Pie chart height should be {expected_height}, got {height}"
        )

    def test_bubble_map_height_matches_point_map_family(self):
        """bubble_map is a type alias for the point_map family style slot
        (compile/normalize/charts.py's _AUTHORED_FAMILY_TO_MONOLITHIC_KEY and
        resolve/chart/_kwargs.py's _FAMILY_SLOT both route it there) — the
        static sizing pass must apply the same alias. Before the fix,
        `getattr(resolved_style.chart_defaults, "bubble_map", None)` returns
        None (only `point_map` is declared on ResolvedChartDefaults), so a
        bubble_map chart silently falls back to the global aspect_ratio
        (1.5) instead of the point_map family's (1.2), diverging from every
        other point_map-family chart at the same width.
        """
        rs = resolve_style(get_theme_style())
        width = 400.0

        point_map_height = get_chart_content_height(
            self._chart("point_map"), width=width, resolved_style=rs
        )
        bubble_map_height = get_chart_content_height(
            self._chart("bubble_map"), width=width, resolved_style=rs
        )

        assert bubble_map_height == point_map_height, (
            f"bubble_map height ({bubble_map_height}) must match point_map "
            f"height ({point_map_height}) at the same width — both belong to "
            "the point_map family style slot."
        )

    def test_donut_height_matches_pie_family(self):
        """donut is a type alias for the pie family style slot (_FAMILY_SLOT
        routes it there) — the static sizing pass must apply the same alias.
        `getattr(resolved_style.chart_defaults, "donut", None)` returns None
        (only `pie` is declared on ResolvedChartDefaults), so a donut chart
        silently falls back to the global aspect_ratio instead of pie's.
        """
        rs = resolve_style(get_theme_style())
        width = 400.0

        pie_height = get_chart_content_height(
            self._chart("pie"), width=width, resolved_style=rs
        )
        donut_height = get_chart_content_height(
            self._chart("donut"), width=width, resolved_style=rs
        )

        assert donut_height == pie_height, (
            f"donut height ({donut_height}) must match pie height "
            f"({pie_height}) at the same width — both belong to the pie "
            "family style slot."
        )

    def test_map_height_matches_geoshape_family(self):
        """map is a type alias for the geoshape family style slot
        (_FAMILY_SLOT routes it there) — the static sizing pass must apply
        the same alias, not silently fall back to the global aspect_ratio.
        """
        rs = resolve_style(get_theme_style())
        width = 400.0

        geoshape_height = get_chart_content_height(
            self._chart("geoshape"), width=width, resolved_style=rs
        )
        map_height = get_chart_content_height(
            self._chart("map"), width=width, resolved_style=rs
        )

        assert map_height == geoshape_height, (
            f"map height ({map_height}) must match geoshape height "
            f"({geoshape_height}) at the same width — both belong to the "
            "geoshape family style slot."
        )

    def test_aspect_height_respects_min_max(self):
        """Aspect-derived height is clamped to config min/max."""
        min_h = float(get_theme_style().charts.min_height)  # 150
        max_h = float(get_theme_style().charts.max_height)  # 400

        chart = self._chart("line")
        rs = resolve_style(get_theme_style())

        # Very narrow width -> height would be below min
        height_narrow = get_chart_content_height(chart, width=100.0, resolved_style=rs)
        assert height_narrow >= min_h, (
            f"Height {height_narrow} should be >= min_height {min_h}"
        )

        # Very wide width -> height would be above max
        height_wide = get_chart_content_height(chart, width=5000.0, resolved_style=rs)
        assert height_wide <= max_h, (
            f"Height {height_wide} should be <= max_height {max_h}"
        )

    def test_cap_engages_at_default_board_width(self):
        """max_height cap must engage at the default board width.

        At the default aspect ratio, width / aspect_ratio exceeds max_height for
        a full-width card. Asserts structural behavior (the cap fires) rather than
        pinning the exact cap value, so legitimate default tuning doesn't break
        this test.
        """
        get_config()  # ensure settings initialized
        max_h = float(get_theme_style().charts.max_height)
        aspect = float(get_theme_style().charts.aspect_ratio)
        board_width = float(get_theme_style().frame.max_width)

        # Sanity-check the test premise: aspect-derived height must exceed the cap.
        assert board_width / aspect > max_h, (
            f"test premise failed: board_width({board_width}) / aspect({aspect}) "
            f"= {board_width / aspect} must exceed max_height({max_h}) for the cap to engage"
        )

        chart = self._chart("bar")
        height = get_chart_content_height(
            chart,
            width=board_width,
            resolved_style=resolve_style(get_theme_style()),
        )
        assert height == max_h, (
            f"Full-width chart should be clamped to max_height ({max_h}), got {height}"
        )

    def test_layout_uses_aspect_ratio_for_cols(self):
        """Charts in cols layout get aspect-ratio-driven heights based on their widths."""
        yaml_content = """
title: Aspect Ratio Cols Test
queries:
  sales:
    type: values
    rows:
      - {month: Jan, revenue: 100}
      - {month: Feb, revenue: 150}
charts:
  chart1:
    query: sales
    type: bar
    x: month
    y: revenue
  chart2:
    query: sales
    type: line
    x: month
    y: revenue
cols:
  - chart1
  - chart2
"""
        result = compile(yaml_content)
        assert result.success, f"Compile failed: {result.errors}"

        board = apply_static_layout(result.board)
        aspect = float(get_theme_style().charts.aspect_ratio)

        # Vega charts render at full item width; card_pad is Vega-internal padding.
        # height = full_width / aspect_ratio + 2*card_pad (top+bottom Vega padding).
        card_pad = float(get_theme_style().frame.card_padding)
        item = board.layout.items[0]
        expected_height = item.width / aspect
        # Clamp to min/max
        min_h = float(get_theme_style().charts.min_height)
        max_h = float(get_theme_style().charts.max_height)
        expected_height = max(min_h, min(max_h, expected_height))
        expected_height += 2 * card_pad  # Vega top+bottom internal padding

        assert abs(item.height - expected_height) < 1.0, (
            f"Chart height {item.height} should be ~{expected_height} "
            f"(width={item.width} / aspect={aspect} + 2*card_pad={2 * card_pad})"
        )


class TestCardPadding:
    """Card padding applies to leaf items (charts) only, not nested boards."""

    def test_chart_item_height_includes_card_padding(self):
        """Chart item height should include card_padding on top and bottom."""
        yaml_content = """
title: Card Padding Test
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
        card_padding = float(get_theme_style().frame.card_padding)

        chart_item = board.layout.items[0]
        # Vega charts use full item width; card_pad = Vega internal top+bottom padding.
        # item.height = get_chart_content_height(width=item.width) + 2*card_padding
        chart_content_height = get_chart_content_height(
            chart_item.chart,
            width=chart_item.width,
            resolved_style=board.resolved_style,
        )
        assert chart_item.height > chart_content_height
        assert abs(chart_item.height - (chart_content_height + 2 * card_padding)) < 1.0

    def test_nested_board_height_excludes_card_padding(self):
        """Nested board items should NOT include card_padding — boards are containers, not cards."""
        yaml_content = """
title: Nested Board No Card Padding
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
        card_padding = float(get_theme_style().frame.card_padding)

        # The outer item is a board (container), not a card
        board_item = board.layout.items[0]
        assert board_item.type == "board"

        # The inner chart IS a card
        inner_chart = board_item.board.layout.items[0]
        assert inner_chart.type == "chart"

        # The board item's height should NOT have card_padding added at the board level.
        # Only the inner chart's height includes card_padding.
        # Vega charts use full item width; card_pad = Vega internal padding.
        inner_chart_content_h = get_chart_content_height(
            inner_chart.chart,
            width=inner_chart.width,
            resolved_style=board_item.board.resolved_style,
        )
        # Inner chart height = content_h + 2*card_padding (Vega's internal top+bottom)
        assert (
            abs(inner_chart.height - (inner_chart_content_h + 2 * card_padding)) < 1.0
        )

    def test_card_padding_config_exists(self):
        """The card_padding config field must exist on style.frame."""
        assert float(get_theme_style().frame.card_padding) > 0


class TestCardGap:
    """card_gap toggle controls inter-item spacing."""

    def test_card_gap_false_uses_config_gap(self):
        """When card_gap is false (default), gap comes from get_theme_style().layout.rows.gap."""
        yaml_content = """
title: No Card Gap
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
  c2:
    query: q
    type: line
    x: month
    y: revenue
rows:
  - c1
  - c2
"""
        result = compile(yaml_content)
        assert result.success
        board = apply_static_layout(result.board)

        rows_gap = float(get_theme_style().layout.rows.gap)
        item1 = board.layout.items[0]
        item2 = board.layout.items[1]
        # Both items should have equal height (same chart type).
        assert item1.height == item2.height, "Items should have equal height"
        # item2 is offset by the config gap (not 0).
        assert abs(item2.y - (item1.height + rows_gap)) < 1.0, (
            f"item2.y ({item2.y}) should equal item1.height + rows.gap "
            f"({item1.height} + {rows_gap})"
        )

    def test_card_gap_true_produces_gap(self):
        """When card_gap is true, layout height increases by the inter-item gap."""
        # Compile same board with and without card_gap to isolate the gap effect
        base_yaml = """
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
  c2:
    query: q
    type: line
    x: month
    y: revenue
rows:
  - c1
  - c2
"""
        result_off = compile(base_yaml)
        assert result_off.success
        board_off = apply_static_layout(result_off.board)

        result_on = compile("card_gap: true\n" + base_yaml)
        assert result_on.success
        board_on = apply_static_layout(result_on.board)

        card_gap = float(get_theme_style().frame.card_gap)
        rows_gap = float(get_theme_style().layout.rows.gap)

        # card_gap replaces the default rows.gap, so the height diff is
        # (card_gap - rows_gap) per inter-item space.
        height_diff = board_on.layout.height - board_off.layout.height
        n_items = 2
        expected_diff = (n_items - 1) * (card_gap - rows_gap)
        assert abs(height_diff - expected_diff) < 1.0, (
            f"Enabling card_gap should shift height by {expected_diff}px "
            f"(card_gap={card_gap} replacing rows_gap={rows_gap}), "
            f"but height difference is {height_diff}"
        )

    def test_card_gap_on_nested_board_raises(self):
        """card_gap on a nested board must raise CompilationError — it's root-only."""
        yaml_content = """
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
  - rows:
      - c1
    card_gap: true
"""
        result = compile(yaml_content)
        assert not result.success
        assert len(result.errors) == 1
        # result.errors is now list[Diagnostic]
        assert "card_gap" in result.errors[0].message.lower()
        assert "root" in result.errors[0].message.lower()


class TestNestedBoardVariableSizing:
    """Tests that nested boards with variables get correct height allocation."""

    def test_nested_board_with_variables_reserves_controls_height(self):
        """A nested board with visible variables must include variable controls height.

        _get_item_content_height and _calculate_nested_board_layout both need to
        account for the variable controls row so charts below don't overflow their
        allocated space.
        """
        yaml_content = """
title: Nested Board Variable Sizing
queries:
  sales:
    type: values
    rows:
      - {month: Jan, revenue: 100}
      - {month: Feb, revenue: 150}
rows:
  - variables:
      region:
        input: select
        options:
          static: [North, South, East, West]
    charts:
      chart1:
        query: sales
        type: bar
        x: month
        y: revenue
    rows:
      - chart1
"""
        result = compile(yaml_content)
        assert result.success

        board = apply_static_layout(result.board)
        variables_height = float(get_theme_style().variables.container_height)

        # Root layout has one item (the nested board)
        assert len(board.layout.items) == 1
        nested_item = board.layout.items[0]
        assert nested_item.board is not None
        nested_board = nested_item.board

        # nested_board.layout.content_height is what charts get to use.
        # It must be smaller than the outer item height by at least the variable
        # controls height + gap (plus padding/margin).
        assert (
            nested_board.layout.content_height <= nested_item.height - variables_height
        ), "nested board layout height does not account for variable controls row"

    def test_nested_board_without_variables_unaffected(self):
        """Nested boards without variables must not have their height altered."""
        yaml_content = """
title: No Variables
queries:
  sales:
    type: values
    rows:
      - {month: Jan, revenue: 100}
      - {month: Feb, revenue: 150}
rows:
  - charts:
      chart1:
        query: sales
        type: bar
        x: month
        y: revenue
    rows:
      - chart1
"""
        result = compile(yaml_content)
        assert result.success

        board = apply_static_layout(result.board)
        variables_height = float(get_theme_style().variables.container_height)

        nested_item = board.layout.items[0]
        assert nested_item.board is not None
        nested_board = nested_item.board

        # Without variables, layout content height should NOT be reduced by variables_height.
        # Allow for title/padding but variable controls space should not be deducted.
        # The content_height should be close to item.height minus padding.
        from dbt_charts.core.compile.models.style.resolved import (
            effective_padding as _effective_padding,
        )

        nrs = nested_board.resolved_style
        padding = _effective_padding(nrs).vertical + (
            nrs.margin.vertical if nrs.margin else 0.0
        )
        # Verify no extra variables_height deduction
        assert (
            nested_board.layout.content_height
            >= nested_item.height - padding - variables_height
        ), "nested board without variables has unexpectedly small content height"


class TestUserHeight:
    """Tests for user-specified height on layout items.

    The YAML `height` property on a chart or nested board should be
    respected by the sizing engine, analogous to how `width` works
    for cols layouts.
    """

    def test_rows_item_respects_layout_height_px(self):
        """A chart in a rows layout with height: 200 gets exactly 200px."""
        yaml_content = """
title: User Height Test
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
  c2:
    query: q
    type: line
    x: month
    y: revenue
rows:
  - height: 200
    rows:
      - c1
  - c2
"""
        result = compile(yaml_content)
        assert result.success

        board = apply_static_layout(result.board)
        items = board.layout.items

        # First item should have exactly 200px height
        assert items[0].height == 200.0, f"Expected 200.0, got {items[0].height}"
        # Second item should get its content-aware default (not 200)
        assert items[1].height > 0

    def test_rows_item_respects_layout_height_percent(self):
        """A chart with height: 50% in rows gets half the available height."""
        yaml_content = """
title: Percent Height Test
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
  c2:
    query: q
    type: line
    x: month
    y: revenue
rows:
  - height: 50%
    rows:
      - c1
  - c2
"""
        result = compile(yaml_content)
        assert result.success

        board = apply_static_layout(result.board)
        items = board.layout.items

        # The first item gets 50% of (available_height - inter_item_gap).
        rows_gap = float(get_theme_style().layout.rows.gap)
        available_content_height = board.layout.content_height
        n_items = len(items)
        expected = (available_content_height - rows_gap * (n_items - 1)) * 0.5
        assert abs(items[0].height - expected) < 1.0, (
            f"Expected ~{expected:.1f}, got {items[0].height:.1f}"
        )

    def test_cols_layout_height_sets_shared_row_height(self):
        """In cols, if any item has layout_height, it influences the shared row height."""
        yaml_content = """
title: Cols Height Test
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
  c2:
    query: q
    type: line
    x: month
    y: revenue
cols:
  - height: 500
    rows:
      - c1
  - c2
"""
        result = compile(yaml_content)
        assert result.success

        board = apply_static_layout(result.board)
        items = board.layout.items

        # In cols, all items share the same height.
        # With layout_height=500 on the first item, the shared height
        # should be at least 500 (it's the max of content heights).
        assert items[0].height == items[1].height  # cols share height
        assert items[0].height >= 500.0, f"Expected >= 500, got {items[0].height}"

    def test_cols_layout_height_percent(self):
        """In cols, height: 50% resolves against available_height."""
        from dbt_charts.core.compile.models.board.normalized import LayoutItem
        from dbt_charts.core.render.sizing import _calculate_cols_dimensions

        item1 = LayoutItem(type="chart", layout_height="50%")
        item2 = LayoutItem(type="chart")

        _calculate_cols_dimensions(
            [item1, item2],
            800.0,
            1000.0,
            0.0,
            gap=10.0,
            resolved_style=resolve_style(get_theme_style()),
        )

        # 50% of available_height=1000 → 500; both items share the same height
        assert item1.height == item2.height
        assert item1.height >= 500.0, f"Expected >= 500, got {item1.height}"

    def test_grid_item_respects_layout_height(self):
        """A grid item with layout_height should use the specified height."""
        from dbt_charts.core.compile.models.board.normalized import Layout, LayoutItem
        from dbt_charts.core.render.sizing import _calculate_grid_dimensions

        item1 = LayoutItem(type="chart", col=0, row=0, col_span=12, layout_height="400")
        item2 = LayoutItem(type="chart", col=12, row=0, col_span=12)

        layout = Layout(type="grid", columns=24, items=[item1, item2])
        _calculate_grid_dimensions(
            layout.items,
            800.0,
            600.0,
            columns=24,
            card_gap=0.0,
            gap=10.0,
            resolved_style=resolve_style(get_theme_style()),
        )

        # Both items share the row, so both get height=400 (max of the row)
        assert item1.height == 400.0, f"Expected 400.0, got {item1.height}"
        assert item2.height == 400.0, f"Expected 400.0, got {item2.height}"

    def test_rows_specified_percent_heights_no_longer_starve_auto_sibling(self):
        """A specified height is a ceiling, not an exact reservation.

        Before the fix: two 90% rows each claimed 90% of the available
        content height outright (no clamp to what their content actually
        needed), so the two together over-claimed the budget and the auto
        sibling (c3) was starved to 0. After the fix: each 90% row clamps to its own
        content height, freeing the unclaimed budget back to the layout, so
        the auto sibling gets its own natural content height instead of 0.
        """
        yaml_content = """
title: Overbudget Height Test
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
  c2:
    query: q
    type: line
    x: month
    y: revenue
  c3:
    query: q
    type: area
    x: month
    y: revenue
rows:
  - height: 90%
    rows:
      - c1
  - height: 90%
    rows:
      - c2
  - c3
"""
        result = compile(yaml_content)
        assert result.success

        board = apply_static_layout(result.board)
        items = board.layout.items

        # The auto item (c3) is no longer starved — the two 90% rows clamped
        # to their own content need instead of claiming their full 90% share.
        assert items[2].height > 0.0
        # The two 90% items still resolve to the same height as each other
        # (identical chart family/content on both).
        assert items[0].height == items[1].height
        assert items[0].height > 0
        # Neither claimed its full 90%-of-available share: both fell back to
        # content, which is well under the (now much larger, since it also
        # includes c3's freed room) available budget.
        assert items[0].height < 0.9 * board.layout.content_height


class TestDimensionValidation:
    """Tests for zero/negative dimension validation at compile time."""

    def test_height_zero_rejected(self):
        """height: 0 should raise a compilation error."""
        yaml_content = """
title: Zero Height
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
  - height: 0
    rows:
      - c1
"""
        result = compile(yaml_content)
        assert not result.success
        assert any("height" in str(e).lower() for e in result.errors)

    def test_height_negative_rejected(self):
        """height: -10 should raise a compilation error."""
        yaml_content = """
title: Negative Height
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
  - height: -10
    rows:
      - c1
"""
        result = compile(yaml_content)
        assert not result.success
        assert any("height" in str(e).lower() for e in result.errors)

    def test_width_zero_rejected(self):
        """width: 0 should raise a compilation error."""
        yaml_content = """
title: Zero Width
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
cols:
  - width: 0
    rows:
      - c1
"""
        result = compile(yaml_content)
        assert not result.success
        assert any("width" in str(e).lower() for e in result.errors)

    def test_positive_dimensions_accepted(self):
        """Positive heights and widths should compile successfully."""
        yaml_content = """
title: Valid Dimensions
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
  - height: 200
    rows:
      - c1
"""
        result = compile(yaml_content)
        assert result.success


# ===========================================================================
# SVG render cache — populated by sizing, reused by render
# ===========================================================================


class TestRenderFirstSizing:
    """Render-first Vega sizing: actual heights from vl-convert, cached for render pass."""

    _MOCK_SVG_300 = (
        '<svg xmlns="http://www.w3.org/2000/svg" width="400" height="300"><g/></svg>'
    )
    _MOCK_SVG_2000 = (
        '<svg xmlns="http://www.w3.org/2000/svg" width="400" height="2000"><g/></svg>'
    )

    def _compile_bar(self, local_project: Callable[..., FilesystemProject]) -> tuple:
        from dbt_charts.core.compile import compile
        from dbt_charts.core.execute.executor import Executor

        yaml_content = """
title: Cache Test
queries:
  q:
    type: values
    rows:
      - {month: "2024-01", revenue: 1000}
      - {month: "2024-02", revenue: 1500}
      - {month: "2024-03", revenue: 1200}
charts:
  bar1:
    query: q
    type: bar
    x: month
    y: revenue
rows:
  - bar1
"""
        result = compile(yaml_content)
        assert result.success
        executor = Executor(
            result.board,
            adapter_registry=build_adapter_registry(local_project(Path.cwd())),
            query_registry=result.query_registry,
        )
        return result.board, executor

    def test_calculate_data_aware_layout_returns_render_cache_tuple(
        self, local_project: Callable[..., FilesystemProject]
    ):
        """calculate_data_aware_layout must return (board, render_cache) tuple."""
        from unittest.mock import patch

        from dbt_charts.core.render.layout_sizing import calculate_data_aware_layout

        board, executor = self._compile_bar(local_project)
        with patch.object(
            _converters_chart, "render_vega_spec", return_value=self._MOCK_SVG_300
        ):
            ret = calculate_data_aware_layout(board, executor, {}, pre_resolved={})

        assert isinstance(ret, tuple), (
            f"calculate_data_aware_layout must return (board, render_cache), got {type(ret)}"
        )
        sized_board, render_cache = ret
        assert isinstance(sized_board, Board)
        assert isinstance(render_cache, dict)

    def test_render_cache_populated_for_vega_chart(
        self, local_project: Callable[..., FilesystemProject]
    ):
        """After data-aware layout, cache has (svg, actual_height) for each Vega chart.

        When the rendered SVG is taller than the static estimate, the actual height wins.
        """
        from unittest.mock import patch

        from dbt_charts.core.render.layout_sizing import calculate_data_aware_layout

        board, executor = self._compile_bar(local_project)
        with patch.object(
            _converters_chart, "render_vega_spec", return_value=self._MOCK_SVG_2000
        ):
            sized_board, render_cache = calculate_data_aware_layout(
                board, executor, {}, pre_resolved={}
            )

        bar_item = sized_board.layout.items[0]
        assert bar_item.chart is not None
        cache_entry = next(
            (v for k, v in render_cache.items() if k[0] == bar_item.chart.id), None
        )
        assert cache_entry is not None, (
            f"bar chart '{bar_item.chart.id}' missing from render cache"
        )
        cached_svg, cached_height = cache_entry
        assert isinstance(cached_svg, str)
        assert cached_height == 2000.0

    def test_render_cache_uses_static_estimate_as_floor(
        self, local_project: Callable[..., FilesystemProject]
    ):
        """When the rendered SVG is shorter than the static estimate, the estimate wins."""
        from unittest.mock import patch

        from dbt_charts.core.render.layout_sizing import calculate_data_aware_layout

        board, executor = self._compile_bar(local_project)
        with patch.object(
            _converters_chart, "render_vega_spec", return_value=self._MOCK_SVG_300
        ):
            sized_board, render_cache = calculate_data_aware_layout(
                board, executor, {}, pre_resolved={}
            )

        bar_item = sized_board.layout.items[0]
        assert bar_item.chart is not None
        cache_entry = next(
            (v for k, v in render_cache.items() if k[0] == bar_item.chart.id), None
        )
        assert cache_entry is not None
        _, cached_height = cache_entry
        # 300 < static estimate; cached height must equal the static estimate (floor)
        assert cached_height > 300.0

    def test_render_cache_not_populated_for_table_chart(
        self, local_project: Callable[..., FilesystemProject]
    ):
        """Table charts use data-driven sizing, not render-first; not in cache."""
        from unittest.mock import patch

        from dbt_charts.core.compile import compile
        from dbt_charts.core.execute.executor import Executor
        from dbt_charts.core.render.layout_sizing import calculate_data_aware_layout

        yaml_content = """
title: Table Test
queries:
  q:
    type: values
    rows:
      - {month: Jan, revenue: 100}
      - {month: Feb, revenue: 150}
charts:
  t1:
    query: q
    type: table
rows:
  - t1
"""
        result = compile(yaml_content)
        assert result.success
        executor = Executor(
            result.board,
            adapter_registry=build_adapter_registry(local_project(Path.cwd())),
            query_registry=result.query_registry,
        )

        with patch.object(
            _converters_chart, "render_vega_spec", return_value=self._MOCK_SVG_300
        ):
            sized_board, render_cache = calculate_data_aware_layout(
                result.board, executor, {}, pre_resolved={}
            )

        table_chart_id = sized_board.layout.items[0].chart.id
        assert not any(k[0] == table_chart_id for k in render_cache), (
            "Table chart must NOT be in render cache"
        )

    def test_render_first_height_used_for_item_sizing(
        self, local_project: Callable[..., FilesystemProject]
    ):
        """Item height uses actual rendered height when it exceeds the static estimate."""
        from unittest.mock import patch

        from dbt_charts.core.render.layout_sizing import calculate_data_aware_layout

        MOCK_SVG_2000 = '<svg xmlns="http://www.w3.org/2000/svg" width="400" height="2000"><g/></svg>'
        board, executor = self._compile_bar(local_project)

        with patch.object(
            _converters_chart, "render_vega_spec", return_value=MOCK_SVG_2000
        ):
            sized_board, _ = calculate_data_aware_layout(
                board, executor, {}, pre_resolved={}
            )

        bar_item = sized_board.layout.items[0]
        # Vega charts: item height = actual SVG height (card_pad is Vega-internal)
        expected = 2000.0
        assert abs(bar_item.height - expected) < 1.0, (
            f"Item height {bar_item.height} should equal SVG height {expected} (no 2*card_pad inflation)"
        )


# ===========================================================================
# Render-first path honors resolved layout slot height
# ===========================================================================


class TestRenderFirstSlotHeight:
    """Render-first path passes the layout slot height to render_chart_to_svg.

    When a layout wrapper declares an explicit height (e.g. rows: [- height: 600,
    rows: [bar]]), the sizing pass sets item.height = 600. The render-first branch
    must pass that slot height to render_chart_to_svg instead of the aspect-ratio
    static_estimate — so Vega draws a 600px SVG, not a ~400px one.
    """

    _VALUES_QUERY = """
queries:
  q:
    type: values
    rows:
      - {month: "2024-01", revenue: 1000}
      - {month: "2024-02", revenue: 1500}
      - {month: "2024-03", revenue: 1200}
charts:
  bar1:
    query: q
    type: bar
    x: month
    y: revenue
"""

    def _echo_height_render(self):
        """Return a mock render_vega_spec that echoes the requested height in the SVG."""

        def mock_render(spec, fmt, width, height, is_placeholder, **kwargs):
            w = int(width) if width else 400
            h = int(height) if height else 400
            return (
                f'<svg xmlns="http://www.w3.org/2000/svg" '
                f'width="{w}" height="{h}"><g/></svg>'
            )

        return mock_render

    def test_explicit_wrapper_height_re_renders_at_resolved_chart_slot(
        self, local_project: Callable[..., FilesystemProject]
    ):
        """Explicit wrapper height yields a cache entry at the chart's final slot height.

        Before the fix: sizing renders at the aspect-ratio estimate and the cache key
        does not match the chart's final layout height, so the render pass misses and
        re-renders with height=None. After the fix: a post-layout pass re-renders at
        the chart item's resolved slot height and stores that exact cache entry.
        """
        import importlib as _importlib
        from unittest.mock import patch

        _conv = _importlib.import_module("dbt_charts.core.render.converters.chart")

        from dbt_charts.core.compile import compile
        from dbt_charts.core.execute.adapters import build_adapter_registry
        from dbt_charts.core.execute.executor import Executor
        from dbt_charts.core.render.layout_sizing import calculate_data_aware_layout

        yaml_content = (
            self._VALUES_QUERY
            + """
rows:
  - height: 600
    rows:
      - bar1
"""
        )
        result = compile(yaml_content)
        assert result.success, f"Compile failed: {result.errors}"

        executor = Executor(
            result.board,
            adapter_registry=build_adapter_registry(local_project(Path.cwd())),
            query_registry=result.query_registry,
        )

        with patch.object(
            _conv, "render_vega_spec", side_effect=self._echo_height_render()
        ):
            sized_board, render_cache = calculate_data_aware_layout(
                result.board, executor, {}, pre_resolved={}
            )

        # The bar chart is inside the nested board (wrapper item).
        wrapper_item = sized_board.layout.items[0]
        assert wrapper_item.board is not None
        bar_item = wrapper_item.board.layout.items[0]
        assert bar_item.chart is not None

        # An authored wrapper height is a ceiling, not an exact allocation:
        # the bar's own content resolves
        # well under 600, so the wrapper shrinks to match the nested chart's
        # resolved slot height rather than keeping the raw authored value.
        assert bar_item.height > 0.0
        assert bar_item.height < 600.0
        assert abs(wrapper_item.height - bar_item.height) < 2.0

        # The render cache must have an entry at the slot height so the renderer hits.
        chart_id = bar_item.chart.id
        cache_entry = render_cache.get((chart_id, bar_item.width, bar_item.height))
        assert cache_entry is not None, (
            f"Expected render cache entry at key ({chart_id!r}, {bar_item.width}, "
            f"{bar_item.height}) — renderer will miss without it"
        )
        _cached_svg, cached_height = cache_entry
        assert abs(cached_height - bar_item.height) < 2.0, (
            f"Cached SVG height should match the final slot height, got {cached_height:.1f} vs {bar_item.height:.1f}"
        )

    def test_no_slot_height_uses_aspect_ratio_estimate(
        self, local_project: Callable[..., FilesystemProject]
    ):
        """Board without a wrapper height still uses the aspect-ratio static_estimate.

        The fallback path (item.height == 0) must be preserved so boards without an
        explicit slot height still get sensible aspect-ratio-based sizing.
        """
        import importlib as _importlib
        from unittest.mock import patch

        _conv = _importlib.import_module("dbt_charts.core.render.converters.chart")

        from dbt_charts.core.compile import compile
        from dbt_charts.core.execute.adapters import build_adapter_registry
        from dbt_charts.core.execute.executor import Executor
        from dbt_charts.core.render.layout_sizing import calculate_data_aware_layout
        from dbt_charts.core.render.sizing import get_chart_content_height

        yaml_content = (
            self._VALUES_QUERY
            + """
rows:
  - bar1
"""
        )
        result = compile(yaml_content)
        assert result.success

        executor = Executor(
            result.board,
            adapter_registry=build_adapter_registry(local_project(Path.cwd())),
            query_registry=result.query_registry,
        )

        captured_heights: list[float] = []

        def capturing_render(spec, fmt, width, height, is_placeholder, **kwargs):
            captured_heights.append(float(height) if height is not None else 0.0)
            w = int(width) if width else 400
            h = int(height) if height else 400
            return (
                f'<svg xmlns="http://www.w3.org/2000/svg" '
                f'width="{w}" height="{h}"><g/></svg>'
            )

        with patch.object(_conv, "render_vega_spec", side_effect=capturing_render):
            sized_board, _ = calculate_data_aware_layout(
                result.board, executor, {}, pre_resolved={}
            )

        assert captured_heights, "render_vega_spec was never called"

        # Compute the expected static_estimate (aspect-ratio path, includes 2*card_pad).
        content_width = max(
            float(get_theme_style().frame.max_width)
            - 2 * float(get_theme_style().frame.margin),
            0.0,
        )
        card_pad = float(get_theme_style().frame.card_padding)
        bar_item = sized_board.layout.items[0]
        assert bar_item.chart is not None
        static_content_h = get_chart_content_height(
            bar_item.chart,
            width=content_width,
            resolved_style=result.board.resolved_style,
        )
        expected_estimate = static_content_h + 2 * card_pad

        first_render_height = captured_heights[0]
        assert abs(first_render_height - expected_estimate) < 2.0, (
            f"Expected render_chart_to_svg called with aspect-ratio estimate "
            f"≈{expected_estimate:.1f}, got {first_render_height:.1f} — "
            f"the fallback path (no slot height) must be preserved"
        )


# ===========================================================================
# Cols height alignment using actual rendered heights
# ===========================================================================


class TestColsHeightAlignment:
    """All items in a cols layout are aligned to the tallest actual rendered height."""

    _COLS_YAML = """
title: Cols Alignment Test
queries:
  q:
    type: values
    rows:
      - {month: "2024-01", revenue: 1000}
      - {month: "2024-02", revenue: 1500}
      - {month: "2024-03", revenue: 1200}
charts:
  tall_chart:
    query: q
    type: bar
    x: month
    y: revenue
  short_chart:
    query: q
    type: line
    x: month
    y: revenue
cols:
  - tall_chart
  - short_chart
"""

    def _height_mock(self) -> tuple:
        """Return (call_count_list, mock_render_fn).

        The first call (tall_chart initial render) returns height=2000, exceeding the
        static estimate.  All subsequent calls echo back the requested height, which
        covers: short_chart initial render (echoes static_estimate) and re-renders
        for cols alignment (echoes the alignment target).
        """
        call_count = [0]

        def mock_render(spec, fmt, width, height, is_placeholder, **kwargs):
            call_count[0] += 1
            w = int(width) if width else 400
            if call_count[0] == 1:
                # tall_chart initial sizing render: exceeds the static estimate
                return (
                    f'<svg xmlns="http://www.w3.org/2000/svg" '
                    f'width="{w}" height="2000"><g/></svg>'
                )
            # short_chart initial + any re-renders: echo back the requested height
            h = int(height) if height is not None else 400
            return (
                f'<svg xmlns="http://www.w3.org/2000/svg" '
                f'width="{w}" height="{h}"><g/></svg>'
            )

        return call_count, mock_render

    def test_cols_items_height_aligned_to_tallest_rendered(
        self, local_project: Callable[..., FilesystemProject]
    ):
        """After sizing, both cols items have height = max(actual) + 2*card_pad."""
        from unittest.mock import patch

        from dbt_charts.core.compile import compile
        from dbt_charts.core.execute.executor import Executor
        from dbt_charts.core.render.layout_sizing import calculate_data_aware_layout

        result = compile(self._COLS_YAML)
        assert result.success
        executor = Executor(
            result.board,
            adapter_registry=build_adapter_registry(local_project(Path.cwd())),
            query_registry=result.query_registry,
        )
        _, mock_render = self._height_mock()

        with patch.object(
            _converters_chart, "render_vega_spec", side_effect=mock_render
        ):
            sized_board, _ = calculate_data_aware_layout(
                result.board, executor, {}, pre_resolved={}
            )

        items = sized_board.layout.items
        assert len(items) == 2

        assert items[0].height == items[1].height, (
            f"Cols items must be aligned: {items[0].height} != {items[1].height}"
        )
        # Vega charts: item height = actual SVG height (card_pad is Vega-internal)
        expected = 2000.0
        assert abs(items[0].height - expected) < 1.0, (
            f"Expected height {expected}, got {items[0].height}"
        )

    def test_tallest_item_not_re_rendered(
        self, local_project: Callable[..., FilesystemProject]
    ):
        """The tallest cols item keeps its cached SVG; only shorter items re-render."""
        from unittest.mock import patch

        from dbt_charts.core.compile import compile
        from dbt_charts.core.execute.executor import Executor
        from dbt_charts.core.render.layout_sizing import calculate_data_aware_layout

        result = compile(self._COLS_YAML)
        assert result.success
        executor = Executor(
            result.board,
            adapter_registry=build_adapter_registry(local_project(Path.cwd())),
            query_registry=result.query_registry,
        )
        call_count, mock_render = self._height_mock()

        with patch.object(
            _converters_chart, "render_vega_spec", side_effect=mock_render
        ):
            calculate_data_aware_layout(result.board, executor, {}, pre_resolved={})

        # 2 initial renders + 1 re-render of shorter item = 3 (tallest NOT re-rendered)
        assert call_count[0] == 3, (
            f"Expected 3 vl-convert calls (2 initial + 1 re-render), got {call_count[0]}"
        )


# ===========================================================================
# Board title/text should use the board content box directly
# ===========================================================================


class TestCardPaddingTitleText:
    """Board-level title and text inset by card_padding (matching chart content)."""

    def test_title_x_offset_includes_card_padding(
        self, local_project: Callable[..., FilesystemProject]
    ):
        """Board title SVG is positioned at x = page_padding + card_padding."""

        from dbt_charts.core.compile import compile
        from dbt_charts.core.execute.executor import Executor
        from dbt_charts.core.render.renderer import render

        yaml_content = """
title: Phase4 Title Test
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
        from unittest.mock import patch

        MOCK_SVG = '<svg xmlns="http://www.w3.org/2000/svg" width="400" height="300"><g/></svg>'
        result = compile(yaml_content)
        assert result.success
        executor = Executor(
            result.board,
            adapter_registry=build_adapter_registry(local_project(Path.cwd())),
            query_registry=result.query_registry,
        )

        with patch.object(_converters_chart, "render_vega_spec", return_value=MOCK_SVG):
            svg = render(result.board, executor, format="svg").output

        page_padding = float(get_theme_style().frame.margin)
        card_padding = float(get_theme_style().frame.card_padding)
        expected_x = page_padding + card_padding

        # The tagged group sits at page_padding; its padded box then wraps the
        # title (mdsvg → SVG) in its own inset translate by card_padding — the
        # two sum to the same absolute position as before the retag.
        actual_x = _cumulative_ink_x(svg, "title", until="svg")
        assert abs(actual_x - expected_x) < 1.0, (
            f"Title x-offset should be page_padding({page_padding}) + "
            f"card_padding({card_padding}) = {expected_x}, got {actual_x}"
        )

    def test_text_x_offset_includes_card_padding(
        self, local_project: Callable[..., FilesystemProject]
    ):
        """Board markdown text should start at page_padding + card_padding."""
        from unittest.mock import patch

        from dbt_charts.core.compile import compile
        from dbt_charts.core.execute.executor import Executor
        from dbt_charts.core.render.renderer import render

        yaml_content = """
text: |
  This paragraph should use the full board content width rather than a narrower
  card-padded width.
"""
        result = compile(yaml_content)
        assert result.success
        executor = Executor(
            result.board,
            adapter_registry=build_adapter_registry(local_project(Path.cwd())),
            query_registry=result.query_registry,
        )
        mock_svg = '<svg xmlns="http://www.w3.org/2000/svg" width="400" height="300"><g/></svg>'

        with patch.object(_converters_chart, "render_vega_spec", return_value=mock_svg):
            svg = render(result.board, executor, format="svg").output

        page_padding = float(get_theme_style().frame.margin)
        card_padding = float(get_theme_style().frame.card_padding)
        actual_x = _cumulative_ink_x(svg, "text", until="style")
        expected_x = page_padding + card_padding
        assert abs(actual_x - expected_x) < 1.0, (
            f"Text x-offset should be page_padding({page_padding}) + "
            f"card_padding({card_padding}) = {expected_x}, got {actual_x}"
        )

    def test_nested_text_height_uses_card_padded_width(
        self,
        local_project: Callable[..., FilesystemProject],
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Nested board text sizing must match the card-padded render width."""
        from dbt_charts.core.execute.executor import Executor
        from dbt_charts.core.render import layout_sizing, sizing as sizing_module

        yaml_content = """
queries:
  kpis:
    type: values
    rows:
      - {open_tickets: 214, solved_tickets: 92}
charts:
  open_tickets_kpi:
    query: kpis
    type: kpi
    value: open_tickets
  solved_tickets_kpi:
    query: kpis
    type: kpi
    value: solved_tickets
rows:
  - cols:
      - width: "48%"
        text: |
          This long support workload paragraph should be measured at the same
          inset width used by rendering so it cannot overlap the neighboring
          KPI column when the text wraps near the right edge.
      - width: "52%"
        rows:
          - cols: [open_tickets_kpi, solved_tickets_kpi]
"""
        result = compile(yaml_content)
        assert result.success
        executor = Executor(
            result.board,
            adapter_registry=build_adapter_registry(local_project(Path.cwd())),
            query_registry=result.query_registry,
        )

        measured_widths: list[float] = []

        def record_text_height(*args: object, **_kwargs: object) -> float:
            width = args[1]
            assert isinstance(width, float)
            measured_widths.append(width)
            return 40.0

        monkeypatch.setattr(
            sizing_module, "get_markdown_text_height", record_text_height
        )

        sized_board, _ = layout_sizing.calculate_data_aware_layout(
            result.board, executor, {}, pre_resolved={}
        )

        first_row = sized_board.layout.items[0].board
        assert first_row is not None
        text_board = first_row.layout.items[0].board
        assert text_board is not None
        expected_width = text_board.layout.content_width - (
            2 * float(text_board.resolved_style.frame.card_padding)
        )

        assert measured_widths
        assert measured_widths[-1] == pytest.approx(expected_width)

    def test_body_markdown_wrap_keeps_browser_font_slack(self) -> None:
        """Body Markdown wrap leaves room for browser font-metric drift."""
        import importlib

        from dbt_charts.core.compile.resolve.style.board import resolve_style

        boards = importlib.import_module("dbt_charts.core.render.boards")
        text = (
            "Dundersign's Zendesk workspace routes every ticket to a single agent of "
            "record, so a per-rep workload split has no signal here. This board reframes "
            "workload the way a growing team would need to next: by **ticket type**, "
            "ranking segments by open volume, how long they've been sitting, and how "
            "much of that load is urgent or high priority."
        )

        svg, _ = boards._render_text_svg(
            text,
            {},
            497.92,
            resolve_style(get_theme_style()),
            text_style=resolve_style(get_theme_style()).text,
        )

        assert (
            "workload split has no signal here. This board reframes "
            "workload the way a growing team would need</text>" not in svg
        )


# ===========================================================================
# card_gap as per-item margin (not gap between siblings)
# ===========================================================================


class TestCardGapBehavior:
    """card_gap is an inter-item gap (N-1 times), not per-item height inflation; page_padding unchanged."""

    def test_card_gap_page_padding_not_reduced(
        self, local_project: Callable[..., FilesystemProject]
    ):
        """With card_gap=true, page_padding stays at configured value (no compensation hack)."""
        from unittest.mock import patch

        from dbt_charts.core.compile import compile
        from dbt_charts.core.execute.executor import Executor
        from dbt_charts.core.render.renderer import render

        MOCK_SVG = '<svg xmlns="http://www.w3.org/2000/svg" width="400" height="300"><g/></svg>'
        yaml_content = """
title: CardGap Test
card_gap: true
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
        executor = Executor(
            result.board,
            adapter_registry=build_adapter_registry(local_project(Path.cwd())),
            query_registry=result.query_registry,
        )

        with patch.object(_converters_chart, "render_vega_spec", return_value=MOCK_SVG):
            svg = render(result.board, executor, format="svg").output

        page_padding = float(get_theme_style().frame.margin)
        # The root SVG <rect> background starts at (0,0) and spans the full width.
        # The first content element (title or chart group) should be offset by page_padding,
        # not max(page_padding - card_gap, 0).
        # We check that the outer SVG viewBox/width matches what we'd expect
        # with full page_padding (not 0 from the old compensation).
        # The title group's cumulative translate-x (its own position plus its
        # padded box's inset) should be page_padding + card_padding.
        actual_x = _cumulative_ink_x(svg, "title", until="svg")
        # Old code: page_padding_reduced = max(20-24,0)=0, so title x = 0 (or card_padding applied)
        # After fix: page_padding stays at 20, so title x >= page_padding (20)
        # With card_padding applied: title x = page_padding + card_padding = 36
        assert actual_x >= page_padding - 1.0, (
            f"With card_gap=true, title x ({actual_x}) should be >= page_padding ({page_padding}). "
            f"Old code reduced page_padding to 0; new code keeps it at {page_padding}."
        )

    def test_card_gap_is_gap_between_items(self):
        """card_gap is an inter-item gap, not internal item height inflation."""
        from dbt_charts.core.compile import compile

        yaml_content = """
title: Margin Not Gap
card_gap: true
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
  c2:
    query: q
    type: line
    x: month
    y: revenue
rows:
  - c1
  - c2
"""
        result = compile(yaml_content)
        assert result.success

        board = apply_static_layout(result.board)
        items = board.layout.items
        assert len(items) == 2

        card_gap = float(get_theme_style().frame.card_gap)

        # card_gap is a gap: item2.y should be item1.height + card_gap.
        item1, item2 = items[0], items[1]
        assert abs(item2.y - (item1.height + card_gap)) < 1.0, (
            f"With card_gap as gap, item2.y ({item2.y}) should equal "
            f"item1.height + card_gap ({item1.height + card_gap})."
        )

        # Item heights are NOT inflated by card_gap.
        yaml_no_margin = yaml_content.replace("card_gap: true\n", "")
        result2 = compile(yaml_no_margin)
        assert result2.success
        board2 = apply_static_layout(result2.board)
        item1_no_margin = board2.layout.items[0]

        assert abs(item1.height - item1_no_margin.height) < 1.0, (
            f"Item height ({item1.height}) should be unchanged by card_gap; "
            f"baseline is {item1_no_margin.height}. card_gap is a gap, not height inflation."
        )


class TestLayoutGapFromConfig:
    """Tests that layout gap values are read from config, not hardcoded to 0."""

    def test_rows_gap_from_config(self):
        """Rows layout without card_gap uses get_theme_style().layout.rows.gap between items."""
        yaml_content = """
title: Rows Gap Test
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
  c2:
    query: q
    type: line
    x: month
    y: revenue
rows:
  - c1
  - c2
"""
        result = compile(yaml_content)
        assert result.success

        board = apply_static_layout(result.board)
        expected_gap = float(get_theme_style().layout.rows.gap)

        item1, item2 = board.layout.items[0], board.layout.items[1]
        assert abs(item2.y - (item1.height + expected_gap)) < 1.0, (
            f"item2.y ({item2.y}) should equal item1.height + rows.gap "
            f"({item1.height} + {expected_gap})"
        )

    def test_cols_gap_from_config(self):
        """Cols layout without card_gap uses get_theme_style().layout.cols.gap between items."""
        yaml_content = """
title: Cols Gap Test
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
  c2:
    query: q
    type: line
    x: month
    y: revenue
cols:
  - c1
  - c2
"""
        result = compile(yaml_content)
        assert result.success

        board = apply_static_layout(result.board)
        expected_gap = float(get_theme_style().layout.cols.gap)
        content_width = float(get_theme_style().frame.max_width) - 2 * float(
            get_theme_style().frame.margin
        )

        item1, item2 = board.layout.items[0], board.layout.items[1]
        assert abs(item1.width + expected_gap + item2.width - content_width) < 1.0, (
            f"item1.width + gap + item2.width ({item1.width} + {expected_gap} + "
            f"{item2.width}) should equal content_width ({content_width})"
        )

    def test_details_summary_height_from_config(self):
        """Collapsed details item height equals get_theme_style().layout.details.summary_height."""
        yaml_content = """
title: Details Test
rows:
  - details: "Show More"
    text: "Hidden content"
"""
        result = compile(yaml_content)
        assert result.success

        board = apply_static_layout(result.board)
        expected_height = float(get_theme_style().layout.details.summary_height)

        item = board.layout.items[0]
        assert abs(item.height - expected_height) < 1.0, (
            f"Collapsed details item height ({item.height}) should equal "
            f"get_theme_style().layout.details.summary_height ({expected_height})"
        )


# =============================================================================
# compute_variable_controls_height — uses variables_style, not the global get_config()
# =============================================================================


class TestComputeVariableControlsHeight:
    """compute_variable_controls_height uses provided variables_style, not the global get_config()."""

    def test_uses_provided_style_container_height(self):
        """Single-row result equals variables_style.container_height."""
        # Build a style with a distinctive container_height
        from dbt_charts.core.compile.models.variable.authored import Variable
        from dbt_charts.core.compile.resolve.style.board import resolve_style
        from dbt_charts.core.render.sizing import compute_variable_controls_height

        custom_style = resolve_style(get_theme_style()).variables.model_copy(
            update={"container_height": 99.0, "gap": 0.0}
        )
        variable_defs = {"x": Variable(label="X", input="text", default="a")}
        result = compute_variable_controls_height(
            variable_defs,
            9999.0,  # wide — fits in one row
            {},
            custom_style,
        )
        assert result == 99.0, f"Expected container_height=99, got {result}"

    def test_empty_variables_returns_zero(self):
        """No variables → height is 0 regardless of style."""
        from dbt_charts.core.compile.resolve.style.board import resolve_style
        from dbt_charts.core.render.sizing import compute_variable_controls_height

        result = compute_variable_controls_height(
            {}, 400.0, {}, resolve_style(get_theme_style()).variables
        )
        assert result == 0.0


# ===========================================================================
# SVG-family chart sizing — explicit per-renderer contracts (no shared bucket)
# ===========================================================================


class TestSvgFamilyChartSizing:
    """Each SVG-family renderer has an explicit sizing contract, not a shared bucket.

    Acceptance criteria from M2:
    - DEFAULT_CHART_HEIGHT (300px) is not used for error or spark_bar at compile time
    - spark_bar gets a data-driven natural height at render time
    - error gets render-first natural height (already working; guarded here)
    - rows/cols layout still aligns after bucket removal
    """

    @staticmethod
    def _chart(type: str = "bar", **kwargs):

        from dbt_charts.core.compile.models.chart.normalized import Chart

        defaults: dict = {"id": "test", "type": type}
        if type == "kpi":
            defaults.setdefault("value", "value")
        if type == "callout":
            defaults.setdefault("message", "test callout")
        defaults.update(kwargs)
        return TypeAdapter(Chart).validate_python(defaults)

    def test_callout_chart_compile_height_is_zero_placeholder(self):
        """Callout chart has no compile-time height — render-first supplies it.

        Callout charts size from natural rendered output at render time, so the
        compile-time estimate is a 0 placeholder (no magic floor constant).
        """
        from dbt_charts.core.render.sizing import get_chart_content_height

        chart = self._chart("callout")
        assert (
            get_chart_content_height(
                chart,
                resolved_style=resolve_style(get_theme_style()),
            )
            == 0.0
        )

    def test_spark_bar_compile_height_is_config_derived(self):
        """spark_bar compile-time height equals the config-derived upper bound.

        Formula: body (max_bars * (bar.height + bar.padding) + bar.padding),
        plus title_height when the chart has a title. Render-first data-aware
        sizing overrides this at render time, but compile-only callers
        (yaml/json/text output, compile-only callers) rely on this
        value instead of the old DEFAULT_CHART_HEIGHT fallback.
        """
        from dbt_charts.core.compile.config import get_chart_rendering
        from dbt_charts.core.render.sizing import get_chart_content_height

        sb = get_theme_style().charts.spark_bar
        body = sb.max_bars * (sb.bar.height + sb.bar.padding) + sb.bar.padding
        rs = resolve_style(get_theme_style())
        # No title
        assert (
            get_chart_content_height(self._chart("spark_bar"), resolved_style=rs)
            == body
        )
        # With title — adds chart_rendering.spark_bar.title_height

        titled = self._chart("spark_bar", title="Titled")
        assert (
            get_chart_content_height(titled, resolved_style=rs)
            == body + get_chart_rendering().spark_bar.title_height
        )

    def test_spark_bar_compile_only_layout_uses_config_estimate(self):
        """Compile-only sizing (render_first=False path) applies the
        config-derived spark_bar estimate.
        """
        yaml_content = """
title: Spark Bar Compile Only
queries:
  q:
    type: values
    rows:
      - {month: Jan, revenue: 100}
      - {month: Feb, revenue: 150}
charts:
  sb:
    query: q
    type: spark_bar
    x: revenue
    y: month
rows:
  - sb
"""
        result = compile(yaml_content)
        assert result.success, f"Compile failed: {result.errors}"
        board = apply_static_layout(result.board)
        sb_item = board.layout.items[0]
        # Omitted title means no title row; chart ids stay internal.
        assert sb_item.chart.title == ""

        sb = get_theme_style().charts.spark_bar
        card_pad = float(get_theme_style().frame.card_padding)
        body = sb.max_bars * (sb.bar.height + sb.bar.padding) + sb.bar.padding
        expected = body + 2 * card_pad
        assert abs(sb_item.height - expected) < 1.0, (
            f"spark_bar compile-only item height {sb_item.height} should derive "
            f"from config ({expected})."
        )

    def test_spark_bar_data_aware_height_from_natural_render(
        self, local_project: Callable[..., FilesystemProject]
    ):
        """spark_bar gets natural height from render-first sizing (height=None path).

        The data-aware height provider must call render_chart_to_svg(spark_bar, height=None)
        so the renderer computes its own natural height from the data rows.
        """
        import importlib
        from unittest.mock import patch

        from dbt_charts.core.compile import compile
        from dbt_charts.core.execute.executor import Executor

        _layout_sizing = importlib.import_module("dbt_charts.core.render.layout_sizing")
        calculate_data_aware_layout = _layout_sizing.calculate_data_aware_layout

        yaml_content = """
title: Spark Bar Natural Height
queries:
  q:
    type: values
    rows:
      - {month: Jan, revenue: 100}
      - {month: Feb, revenue: 150}
charts:
  sb:
    query: q
    type: spark_bar
    x: revenue
    y: month
rows:
  - sb
"""
        result = compile(yaml_content)
        assert result.success, f"Compile failed: {result.errors}"
        executor = Executor(
            result.board,
            adapter_registry=build_adapter_registry(local_project(Path.cwd())),
            query_registry=result.query_registry,
        )

        # Mock a small natural-height SVG (50px) — smaller than DEFAULT_CHART_HEIGHT
        MOCK_NATURAL_SVG = (
            '<svg xmlns="http://www.w3.org/2000/svg" width="400" height="50"><g/></svg>'
        )

        recorded_height_args: list = []

        def mock_render_chart_to_svg(
            resolved, executor, variables, width, *, height=None, **kwargs
        ):
            recorded_height_args.append(height)
            return MOCK_NATURAL_SVG, 400.0, 50.0, None

        # Patch _render_chart_to_svg in layout_sizing so the V2 sizing pass
        # is intercepted (V2 uses _render_chart_to_svg, not render_unresolved_chart_to_svg).
        with patch.object(
            _layout_sizing,
            "_render_chart_to_svg",
            side_effect=mock_render_chart_to_svg,
        ):
            sized_board, _ = calculate_data_aware_layout(
                result.board, executor, {}, pre_resolved={}
            )

        # Every render_chart_to_svg call for spark_bar must use natural sizing
        # (the provider runs during both layout_height and layout_items passes).
        assert recorded_height_args, (
            "render_chart_to_svg was never called for spark_bar"
        )
        assert all(h is None for h in recorded_height_args), (
            f"spark_bar must be rendered with height=None on every call, "
            f"got heights={recorded_height_args}"
        )

        # The item height incorporates the natural SVG height (50px) + 2*card_pad
        sb_item = sized_board.layout.items[0]
        card_pad = float(get_theme_style().frame.card_padding)
        expected = 50.0 + 2 * card_pad
        assert abs(sb_item.height - expected) < 1.0, (
            f"spark_bar item height {sb_item.height} should equal "
            f"natural_height(50) + 2*card_pad({2 * card_pad}) = {expected}"
        )

    def test_cols_with_spark_bar_still_aligns(self):
        """cols layout with a spark_bar next to a bar chart must still produce valid heights.

        After removing the fixed-height bucket, both items should have positive heights
        and the cols layout should assign the same height to both.
        """
        yaml_content = """
title: Cols Spark Bar Alignment
queries:
  q:
    type: values
    rows:
      - {month: Jan, revenue: 100}
      - {month: Feb, revenue: 150}
charts:
  bar1:
    query: q
    type: bar
    x: month
    y: revenue
  sb:
    query: q
    type: spark_bar
    x: revenue
    y: month
cols:
  - bar1
  - sb
"""
        result = compile(yaml_content)
        assert result.success, f"Compile failed: {result.errors}"

        board = apply_static_layout(result.board)

        items = board.layout.items
        assert len(items) == 2
        # Both items get the same height in cols
        assert items[0].height == items[1].height
        assert items[0].height > 0


class TestKpiCascadeHeight:
    """KPI height must flow from resolved_style.charts.kpi.default_height, not a constant.

    Regression guard for DEFAULT_KPI_HEIGHT removal (item 4 of PR #2637 design-debt cleanup).
    """

    @staticmethod
    def _kpi_chart():
        return KpiChart(id="kpi1", type="kpi", value="amount")

    def test_get_chart_content_height_reads_resolved_style_kpi(self):
        """get_chart_content_height with resolved_style returns cascade kpi.default_height."""
        import dataclasses

        resolved = resolve_style(get_theme_style())
        DISTINCTIVE = 99.0
        new_kpi = resolved.chart_defaults.kpi.model_copy(
            update={"default_height": DISTINCTIVE}
        )
        new_chart_defaults = dataclasses.replace(resolved.chart_defaults, kpi=new_kpi)
        new_rs = dataclasses.replace(resolved, chart_defaults=new_chart_defaults)

        height = get_chart_content_height(self._kpi_chart(), resolved_style=new_rs)
        assert height == DISTINCTIVE, (
            f"KPI height should read from resolved_style.charts.kpi.default_height "
            f"({DISTINCTIVE}), got {height}"
        )

    def test_apply_static_layout_threads_kpi_height_from_resolved_style(self):
        """apply_static_layout passes resolved_style down so KPI item height uses cascade."""
        import dataclasses

        result = compile(
            """
queries:
  q:
    type: values
    rows:
      - {month: Jan, revenue: 100}
      - {month: Feb, revenue: 150}
charts:
  kpi1:
    type: kpi
    query: q
    value: revenue
rows:
  - kpi1
"""
        )
        assert result.success, f"Compile failed: {result.errors}"
        board = result.board

        DISTINCTIVE = 99.0
        new_kpi = board.resolved_style.chart_defaults.kpi.model_copy(
            update={"default_height": DISTINCTIVE}
        )
        new_chart_defaults = dataclasses.replace(
            board.resolved_style.chart_defaults, kpi=new_kpi
        )
        new_rs = dataclasses.replace(
            board.resolved_style, chart_defaults=new_chart_defaults
        )
        board_with_override = board.model_copy(update={"resolved_style": new_rs})

        recalculated = apply_static_layout(board_with_override)
        kpi_item = recalculated.layout.items[0]

        card_pad = float(get_theme_style().frame.card_padding)
        expected = DISTINCTIVE + 2 * card_pad
        assert kpi_item.height == expected, (
            f"KPI item height should be DISTINCTIVE ({DISTINCTIVE}) + "
            f"2*card_pad ({card_pad}) = {expected}, got {kpi_item.height}"
        )


class TestSparkBarCascadeHeight:
    """spark_bar static height must read resolved_style.charts.spark_bar, not
    the global default theme — same class of bug as get_board_gap's dropped
    layout gap override (audit: 2026-07-13 sweep).
    """

    @staticmethod
    def _spark_bar_chart():
        from dbt_charts.core.compile.models.chart.normalized import SparkBarChart

        return SparkBarChart(id="sb1", type="spark_bar", x="revenue", y="month")

    def test_get_chart_content_height_reads_resolved_style_spark_bar(self) -> None:
        import dataclasses

        resolved = resolve_style(get_theme_style())
        DISTINCTIVE_MAX_BARS = 3
        new_spark_bar = resolved.chart_defaults.spark_bar.model_copy(
            update={"max_bars": DISTINCTIVE_MAX_BARS}
        )
        new_chart_defaults = dataclasses.replace(
            resolved.chart_defaults, spark_bar=new_spark_bar
        )
        new_rs = dataclasses.replace(resolved, chart_defaults=new_chart_defaults)

        sb = new_spark_bar
        expected = sb.max_bars * (sb.bar.height + sb.bar.padding) + sb.bar.padding
        height = get_chart_content_height(
            self._spark_bar_chart(), resolved_style=new_rs
        )
        assert height == pytest.approx(expected)
        assert height != get_chart_content_height(
            self._spark_bar_chart(), resolved_style=resolved
        )


class TestCardPaddingCascadeHeight:
    """Non-callout chart item height adds 2*frame.card_padding from the board's
    resolved style, not the global default theme.
    """

    def test_get_item_content_height_reads_resolved_style_card_padding(self) -> None:
        import dataclasses

        from dbt_charts.core.compile.models.board.normalized import LayoutItem
        from dbt_charts.core.render.sizing import get_item_content_height

        resolved = resolve_style(get_theme_style())
        DISTINCTIVE_CARD_PADDING = 321.0
        new_board = resolved.frame.model_copy(
            update={"card_padding": DISTINCTIVE_CARD_PADDING}
        )
        new_rs = dataclasses.replace(resolved, frame=new_board)

        item = LayoutItem(type="chart", chart=TestKpiCascadeHeight._kpi_chart())
        height = get_item_content_height(
            item, card_gap=0.0, gap=0.0, resolved_style=new_rs
        )
        expected = (
            float(new_rs.chart_defaults.kpi.default_height)
            + 2 * DISTINCTIVE_CARD_PADDING
        )
        assert height == pytest.approx(expected)
        assert height != get_item_content_height(
            item, card_gap=0.0, gap=0.0, resolved_style=resolved
        )


class TestDetailsSummaryHeightCascade:
    """Details chrome (collapsed bar + expanded summary overhead) must read
    resolved_style.layout.details.summary_height, not the global theme.
    """

    def test_collapsed_details_item_height_reads_resolved_style(self) -> None:
        import dataclasses

        from dbt_charts.core.compile.models.board.normalized import (
            Board,
            Layout,
            LayoutItem,
        )
        from dbt_charts.core.render.sizing import get_item_content_height

        resolved = resolve_style(get_theme_style())
        DISTINCTIVE_SUMMARY_HEIGHT = 456.0
        new_layout = resolved.layout.model_copy(
            update={
                "details": resolved.layout.details.model_copy(
                    update={"summary_height": DISTINCTIVE_SUMMARY_HEIGHT}
                )
            }
        )
        new_rs = dataclasses.replace(resolved, layout=new_layout)

        nested = Board(
            id="nested",
            layout=Layout(type="rows"),
            resolved_style=new_rs,
            chart_style_context=_default_chart_style_context(),
            level=1,
        )
        item = LayoutItem(
            type="board",
            board=nested,
            details_variable="expanded",
            details_summary="Details",
        )
        height = get_item_content_height(
            item,
            card_gap=0.0,
            gap=0.0,
            variable_values={"expanded": False},
            resolved_style=new_rs,
        )
        assert height == pytest.approx(DISTINCTIVE_SUMMARY_HEIGHT)

    def test_details_chrome_height_reads_resolved_style(self) -> None:
        import dataclasses

        from dbt_charts.core.compile.models.board.normalized import LayoutItem
        from dbt_charts.core.render.sizing import details_chrome_height

        resolved = resolve_style(get_theme_style())
        DISTINCTIVE_SUMMARY_HEIGHT = 654.0
        new_layout = resolved.layout.model_copy(
            update={
                "details": resolved.layout.details.model_copy(
                    update={"summary_height": DISTINCTIVE_SUMMARY_HEIGHT}
                )
            }
        )
        new_rs = dataclasses.replace(resolved, layout=new_layout)

        item = LayoutItem(
            type="board", details_variable="expanded", details_summary="Details"
        )
        height = details_chrome_height(
            item, gap=10.0, card_gap=5.0, resolved_style=new_rs
        )
        assert height == pytest.approx(DISTINCTIVE_SUMMARY_HEIGHT + 10.0 + 5.0)


class TestNestedDetailsChromeUsesContainerStyle:
    """The details summary-bar chrome around a nested/expanded details board
    is drawn by the *container*, not the nested board — render_layout_item
    (render/chart/rendering.py) reads details_chrome_height from the
    container's resolved_style. _calculate_nested_board_layout must agree,
    or sizing and rendering disagree for a nested board whose own theme
    overrides layout.details.summary_height differently from the container's.
    """

    @staticmethod
    def _resolved_style_with_summary_height(summary_height: float):
        resolved = resolve_style(get_theme_style())
        new_layout = resolved.layout.model_copy(
            update={
                "details": resolved.layout.details.model_copy(
                    update={"summary_height": summary_height}
                )
            }
        )
        return dataclasses.replace(resolved, layout=new_layout)

    def _nested_board(self, summary_height: float):
        from dbt_charts.core.compile.models.board.normalized import Board, Layout

        return Board(
            id="nested",
            layout=Layout(type="rows"),
            resolved_style=self._resolved_style_with_summary_height(summary_height),
            chart_style_context=_default_chart_style_context(),
            level=1,
        )

    def _expanded_item(self, nested):
        from dbt_charts.core.compile.models.board.normalized import LayoutItem

        item = LayoutItem(
            type="board",
            board=nested,
            details_variable="expanded",
            details_summary="Details",
        )
        item.width = 400.0
        item.height = 500.0
        return item

    def test_content_height_tracks_container_summary_height(self) -> None:
        from dbt_charts.core.render.sizing import _calculate_nested_board_layout

        # Nested board's own summary_height is fixed; only the container's moves.
        nested = self._nested_board(summary_height=999.0)
        item = self._expanded_item(nested)

        _calculate_nested_board_layout(
            item,
            card_gap=0.0,
            gap=0.0,
            variable_values={"expanded": True},
            resolved_style=self._resolved_style_with_summary_height(40.0),
        )
        height_with_40 = nested.layout.content_height

        _calculate_nested_board_layout(
            item,
            card_gap=0.0,
            gap=0.0,
            variable_values={"expanded": True},
            resolved_style=self._resolved_style_with_summary_height(140.0),
        )
        height_with_140 = nested.layout.content_height

        # A 100px larger container chrome reserves 100px less content height.
        assert height_with_40 - height_with_140 == pytest.approx(100.0)

    def test_content_height_ignores_nested_boards_own_summary_height(self) -> None:
        from dbt_charts.core.render.sizing import _calculate_nested_board_layout

        container_rs = self._resolved_style_with_summary_height(40.0)

        nested_small = self._nested_board(summary_height=1.0)
        item_small = self._expanded_item(nested_small)
        _calculate_nested_board_layout(
            item_small,
            card_gap=0.0,
            gap=0.0,
            variable_values={"expanded": True},
            resolved_style=container_rs,
        )

        nested_large = self._nested_board(summary_height=999.0)
        item_large = self._expanded_item(nested_large)
        _calculate_nested_board_layout(
            item_large,
            card_gap=0.0,
            gap=0.0,
            variable_values={"expanded": True},
            resolved_style=container_rs,
        )

        # Same container chrome height regardless of the nested board's own
        # (irrelevant) layout.details.summary_height.
        assert nested_small.layout.content_height == pytest.approx(
            nested_large.layout.content_height
        )


class TestTabsBarHeightCascade:
    """Tabs bar overhead must read resolved_style.layout.tabs.bar_height, not
    the global default theme.
    """

    def test_measure_tabs_layout_height_reads_resolved_style(self) -> None:
        import dataclasses

        from dbt_charts.core.compile.models.board.normalized import Layout, LayoutItem
        from dbt_charts.core.render.sizing import _measure_tabs_layout_height

        resolved = resolve_style(get_theme_style())
        DISTINCTIVE_BAR_HEIGHT = 234.0
        new_layout = resolved.layout.model_copy(
            update={
                "tabs": resolved.layout.tabs.model_copy(
                    update={"bar_height": DISTINCTIVE_BAR_HEIGHT}
                )
            }
        )
        new_rs = dataclasses.replace(resolved, layout=new_layout)

        item = LayoutItem(type="chart")
        baseline_height = _measure_tabs_layout_height(
            Layout(type="tabs", items=[item]),
            card_gap=0.0,
            gap=0.0,
            available_width=800.0,
            variable_values=None,
            resolved_style=resolved,
        )
        distinctive_height = _measure_tabs_layout_height(
            Layout(type="tabs", items=[item]),
            card_gap=0.0,
            gap=0.0,
            available_width=800.0,
            variable_values=None,
            resolved_style=new_rs,
        )
        # Content measurement is unaffected by bar_height; only the tab-bar
        # overhead added on top should move, by exactly the delta.
        assert distinctive_height - baseline_height == pytest.approx(
            DISTINCTIVE_BAR_HEIGHT - float(resolved.layout.tabs.bar_height)
        )


def test_compact_style_pins_heading_sizes_from_theme() -> None:
    # Regression: markdown headings (h1-h6) used to fall back to mdsvg's
    # scale × base_font_size (h2 = 14 × 1.6 = 22.4px), ignoring the theme's
    # title.sizes ramp. get_compact_style now wires every h*_size from
    # style.title.sizes so `##` in a `text:` block renders at the theme size.
    from dbt_charts.core.render.sizing import get_compact_style

    style = get_compact_style()
    sizes = get_theme_style().title.sizes

    assert style.h1_size == float(sizes[0])
    assert style.h2_size == float(sizes[1])
    assert style.h3_size == float(sizes[2])
    assert style.h4_size == float(sizes[3])
    assert style.h5_size == float(sizes[4])
    assert style.h6_size == float(sizes[5])
    # get_heading_size returns the absolute size, not base × scale.
    assert style.get_heading_size(2) == float(sizes[1])


def test_compact_style_h1_size_override_for_board_titles() -> None:
    # Board title rendering passes a width-resolved h1 pixel size so a single
    # h1 heading lands on the responsive target (replaces the legacy
    # base_font_size / 2.0 hack that depended on h1_scale = 2.0).
    from dbt_charts.core.render.sizing import get_compact_style

    style = get_compact_style(h1_size=32.0)
    assert style.h1_size == 32.0
    assert style.get_heading_size(1) == 32.0


def test_compact_style_heading_weight_defaults_from_theme() -> None:
    # The theme controls overall heading weight (title.font.weight). When the
    # caller doesn't pass an explicit override, get_compact_style must pull
    # the weight from the theme rather than letting mdsvg fall back to
    # the OSS-default 'bold'.
    from dbt_charts.core.render.sizing import get_compact_style

    style = get_compact_style()
    theme_weight = get_theme_style().title.font.weight or 500
    expected = theme_weight if isinstance(theme_weight, str) else int(theme_weight)
    assert style.heading_font_weight == expected


def test_compact_style_heading_line_height_from_theme() -> None:
    # Markdown headings used to inherit the body line-height (1.6), giving
    # wrapped headlines a too-loose vertical gap. The theme now owns a
    # heading-specific line_height on style.title.font which threads through
    # mdsvg's heading_line_height for any text-block heading.
    from dbt_charts.core.compile.resolve.style.board import resolve_style
    from dbt_charts.core.render.sizing import get_compact_style

    rs = resolve_style(get_theme_style())
    style = get_compact_style(rs)
    assert style.heading_line_height == rs.title.font.line_height
    # Headlines should be tighter than body prose.
    assert style.heading_line_height < style.line_height


def test_compact_style_omits_default_text_weight() -> None:
    # Browser/SVG default text weight is normal/400. Omitting that default
    # avoids changing every markdown SVG snapshot while still letting
    # non-default text.font.weight overrides flow through.
    from dbt_charts.core.render.sizing import get_compact_style

    style = get_compact_style()
    assert style.font_weight is None


def test_compact_style_uses_merged_text_typography() -> None:
    from dbt_charts.core.compile.resolve.style.board import resolve_style
    from dbt_charts.core.render.sizing import get_compact_style

    base = resolve_style(get_theme_style())
    text_font = base.text.font.model_copy(
        update={
            "family": "Sentinel Body",
            "size": 18.0,
            "weight": 600,
            "line_height": 1.9,
        }
    )
    text = base.text.model_copy(update={"font": text_font})
    root_font = base.font.model_copy(update={"family": "Wrong Root"})
    merged = dataclasses.replace(base, text=text)
    merged = dataclasses.replace(merged, font=root_font)

    style = get_compact_style(merged)

    assert style.font_family.startswith("Sentinel Body")
    assert "Wrong Root" not in style.font_family
    assert style.base_font_size == 18.0
    assert style.font_weight == 600
    assert style.line_height == 1.9


def test_block_margins_land_on_the_baseline_grid() -> None:
    """Every prose block margin is a whole number of line boxes.

    Columns only align across the gutter if the vertical rhythm is a grid. A
    fractional margin -- the previous 1.5 / 0.75 / 0.5 targets -- puts a column
    containing a paragraph break out of phase with one that has none, so lines
    stop lining up horizontally. Asserting the grid property rather than the
    specific multipliers keeps this true when the multipliers are retuned.
    """
    from dbt_charts.core.compile.resolve.style.board import resolve_style
    from dbt_charts.core.render.sizing import get_compact_style

    base = resolve_style(get_theme_style())
    text_font = base.text.font.model_copy(update={"size": 16.0, "line_height": 1.25})
    text = base.text.model_copy(update={"font": text_font})
    merged = dataclasses.replace(base, text=text)

    style = get_compact_style(merged)
    line_box = 16.0 * 1.25

    for name in (
        "paragraph_spacing",
        "heading_margin_top_px",
        "heading_margin_bottom_px",
    ):
        value = getattr(style, name)
        boxes = value / line_box
        assert boxes == pytest.approx(round(boxes), abs=1e-9), (
            f"{name} is {boxes:.3f} line boxes; a baseline grid needs a whole number"
        )


def test_block_margins_scale_with_the_text_they_belong_to() -> None:
    """Doubling the line box doubles every block margin."""
    from dbt_charts.core.compile.resolve.style.board import resolve_style
    from dbt_charts.core.render.sizing import get_compact_style

    base = resolve_style(get_theme_style())

    def margins(size: float, leading: float) -> tuple[float, float, float]:
        font = base.text.font.model_copy(update={"size": size, "line_height": leading})
        merged = dataclasses.replace(
            base, text=base.text.model_copy(update={"font": font})
        )
        s = get_compact_style(merged)
        return (
            s.paragraph_spacing,
            s.heading_margin_top_px,
            s.heading_margin_bottom_px,
        )

    single = margins(10.0, 1.2)
    double = margins(20.0, 1.2)
    for one, two in zip(single, double, strict=True):
        assert two == pytest.approx(one * 2.0)


def test_paragraph_margin_bottom_drives_paragraph_spacing() -> None:
    """style.text.paragraph.margin_bottom (in lh units) controls paragraph_spacing.

    Stage 2: paragraph_spacing = font_size * line_height * paragraph.margin_bottom.
    A distinctive theme value for margin_bottom must flow through.
    """
    from dbt_charts.core.compile.models.style.authored import StylePatch
    from dbt_charts.core.compile.resolve.style.board import resolve_style
    from dbt_charts.core.render.sizing import compact_style_kwargs

    base = get_theme_style()
    patch = StylePatch.model_validate({"text": {"paragraph": {"margin_bottom": 0.73}}})
    rs = resolve_style(base, patch)
    kwargs = compact_style_kwargs(rs)
    font_size = rs.text.font.size
    assert font_size is not None
    line_height = rs.text.font.line_height
    assert line_height is not None
    assert kwargs["paragraph_spacing"] == font_size * line_height * 0.73, (
        "paragraph_spacing must equal font_size * line_height * text.paragraph.margin_bottom"
    )


def test_heading_margins_driven_by_text_heading_style() -> None:
    """style.text.heading.margin_top/bottom (in lh units) drive heading_margin_*_px.

    Stage 2: the hardcoded 1.5 / 0.75 constants are replaced by theme values.
    Distinctive theme overrides must propagate.
    """
    from dbt_charts.core.compile.models.style.authored import StylePatch
    from dbt_charts.core.compile.resolve.style.board import resolve_style
    from dbt_charts.core.render.sizing import compact_style_kwargs

    base = get_theme_style()
    patch = StylePatch.model_validate(
        {"text": {"heading": {"margin_top": 1.11, "margin_bottom": 0.55}}}
    )
    rs = resolve_style(base, patch)
    kwargs = compact_style_kwargs(rs)
    font_size = rs.text.font.size
    assert font_size is not None
    line_height = rs.text.font.line_height
    assert line_height is not None
    body_line = font_size * line_height
    assert kwargs["heading_margin_top_px"] == body_line * 1.11
    assert kwargs["heading_margin_bottom_px"] == body_line * 0.55


def test_compact_style_uses_merged_heading_typography() -> None:
    from dbt_charts.core.compile.resolve.style.board import resolve_style
    from dbt_charts.core.render.sizing import get_compact_style

    base = resolve_style(get_theme_style())
    title_font = base.title.font.model_copy(update={"weight": 700, "line_height": 1.15})
    title = base.title.model_copy(
        update={
            "font": title_font,
            "sizes": [31.0, 23.0, 19.0, 17.0, 15.0, 13.0],
        }
    )
    merged = dataclasses.replace(base, title=title)

    style = get_compact_style(merged)

    assert style.h1_size == 31.0
    assert style.h2_size == 23.0
    assert style.h3_size == 19.0
    assert style.h4_size == 17.0
    assert style.h5_size == 15.0
    assert style.h6_size == 13.0
    assert style.heading_font_weight == 700
    assert style.heading_line_height == 1.15


def test_markdown_text_height_uses_merged_text_typography() -> None:
    from dbt_charts.core.compile.resolve.style.board import resolve_style
    from dbt_charts.core.render.sizing import get_markdown_text_height

    base = resolve_style(get_theme_style())
    text_font = base.text.font.model_copy(update={"size": 24.0, "line_height": 1.8})
    text = base.text.model_copy(update={"font": text_font})
    merged = dataclasses.replace(base, text=text)
    content = (
        "Revenue quality improved across every region in the latest operating review."
    )

    default_height = get_markdown_text_height(
        content,
        width=180.0,
        text_style=_DEFAULT_STYLE.text,
        resolved_style=_DEFAULT_STYLE,
    )
    merged_height = get_markdown_text_height(
        content,
        width=180.0,
        resolved_style=merged,
        text_style=merged.text,
    )

    assert merged_height > default_height


def test_title_height_uses_merged_title_typography() -> None:
    from dbt_charts.core.compile.resolve.style.board import resolve_style
    from dbt_charts.core.render.sizing import get_title_height

    base = resolve_style(get_theme_style())
    title_font = base.title.font.model_copy(update={"weight": 700, "line_height": 1.2})
    title = base.title.model_copy(
        update={
            "font": title_font,
            "sizes": [48.0, 36.0, 30.0, 24.0, 20.0, 18.0],
        }
    )
    merged = dataclasses.replace(base, title=title)
    heading = "Quarterly revenue performance by region"

    default_height = get_title_height(heading, width=240.0, level=1)
    merged_height = get_title_height(
        heading, width=240.0, level=1, resolved_style=merged
    )

    assert merged_height > default_height


def test_compact_style_renders_h2_at_theme_size() -> None:
    # End-to-end: rendering `## Heading` through the compact style must emit
    # font-size matching style.title.sizes[1], not 14 × 1.6 = 22.4.
    from dbt_charts.core.render.sizing import get_compact_style
    from mdsvg import render as render_markdown

    style = get_compact_style()
    expected_size = float(get_theme_style().title.sizes[1])
    svg = render_markdown("## Section heading", width=400, style=style)
    # mdsvg emits font-size as an attribute on the heading text element.
    expected_attr = f'font-size="{int(expected_size) if expected_size == int(expected_size) else expected_size}"'
    assert expected_attr in svg, f"Expected {expected_attr} in SVG"
    assert 'font-size="22.4"' not in svg


def test_column_gutter_is_derived_from_the_line_box() -> None:
    """The gutter scales with the type it separates, not with the row gap.

    It used to fall back to ``layout.rows.gap`` -- 8px against a ~20px line box,
    a row gap doing a gutter's job and far too tight between wide columns. It is
    now 1.5 line boxes, so it tracks prose size the way every other typographic
    measure does. A distinctive ``rows.gap`` must therefore NOT move it.
    """
    from dbt_charts.core.compile.resolve.style.board import resolve_style
    from dbt_charts.core.render.sizing import get_markdown_text_height

    base = resolve_style(get_theme_style())
    rows = base.layout.rows.model_copy(update={"gap": 200.0})
    layout = base.layout.model_copy(update={"rows": rows})
    row_gap_changed = dataclasses.replace(base, layout=layout)

    text_style = base.text.model_copy(
        update={"column": base.text.column.model_copy(update={"max_number": 2})}
    )
    long_text = " ".join(["Revenue is recognized when control transfers."] * 40)

    unchanged = get_markdown_text_height(
        long_text, 600.0, text_style=text_style, resolved_style=base
    )
    with_big_row_gap = get_markdown_text_height(
        long_text, 600.0, text_style=text_style, resolved_style=row_gap_changed
    )
    assert with_big_row_gap == unchanged, "the gutter must not read layout.rows.gap"


def test_compact_style_top_aligns_letterboxed_images() -> None:
    # Regression: board text-block images render with fetch_image_sizes=False, so
    # mdsvg uses image_fallback_aspect_ratio (16:9) and letterboxes any image
    # whose box height exceeds the auto-fit. Default xMidYMid centers the image
    # in the letterbox; xMidYMin top-aligns it. Top-align matches user
    # expectation for stacked/columned image grids (compare UI).
    from dbt_charts.core.render.sizing import get_compact_style

    style = get_compact_style(resolve_style(get_theme_style()))
    assert style.image_preserve_aspect_ratio == "xMidYMin meet", (
        f"Expected xMidYMin meet (top-align), got "
        f"{style.image_preserve_aspect_ratio!r}. Centering letterboxed images "
        f"creates surprising vertical gaps in the compare UI."
    )


class TestEmptyLayoutDoesNotCollapse:
    """Regression: removing the MIN_CONTENT_HEIGHT floor (60px anti-collapse
    constant) must not crash or produce invalid heights for empty/minimal
    layouts. Accumulators now init to 0; empty cols/rows must still render to a
    finite, non-negative height (sized to content, no NaN/negative/crash).
    """

    @staticmethod
    def _viewbox_height(
        yaml: str, local_project: Callable[..., FilesystemProject]
    ) -> float:
        import re

        from dbt_charts.core.compile import compile
        from dbt_charts.core.execute import Executor
        from dbt_charts.core.execute.adapters import build_adapter_registry
        from dbt_charts.core.render import render

        result = compile(yaml)
        assert not result.errors, [e.message for e in result.errors]
        assert result.board is not None
        svg = render(
            result.board,
            Executor(
                result.board,
                adapter_registry=build_adapter_registry(local_project(Path("/tmp"))),
            ),
            format="svg",
        ).output
        if isinstance(svg, bytes):
            svg = svg.decode("utf-8")
        m = re.search(r'viewBox="0 0 [0-9.]+ ([0-9.]+)"', svg)
        assert m is not None, "no viewBox in rendered SVG"
        return float(m.group(1))

    def test_empty_rows_layout_renders_finite_height(
        self, local_project: Callable[..., FilesystemProject]
    ) -> None:
        h = self._viewbox_height("title: T\nrows: []\n", local_project)
        import math

        assert math.isfinite(h) and h >= 0.0

    def test_empty_cols_layout_renders_finite_height(
        self, local_project: Callable[..., FilesystemProject]
    ) -> None:
        h = self._viewbox_height("title: T\nrows:\n  - cols: []\n", local_project)
        import math

        assert math.isfinite(h) and h >= 0.0


class TestPivotedTableHeight:
    """A pivoted table reserves height for POST-pivot rows, not the raw long rows.

    Regression: the layout sizer counted raw query rows (len(data)) for table
    height, but the table renderer collapses long-form rows into a wide cross-tab
    via pivot. A 32x32 board (1024 long rows -> 32 wide rows) reserved ~32x too
    much vertical space, leaving a ~20,000px phantom gap below the board.
    """

    _PIVOT_YAML = """
title: Pivot
queries:
  grid:
    columns: [rk, ck, v]
    values:
      - [r1, a, 1]
      - [r1, b, 2]
      - [r2, a, 3]
      - [r2, b, 4]
charts:
  g:
    query: grid
    type: table
    rows:
      - rk
    columns:
      - ck
    values:
      - v
rows:
  - g
"""

    # Same two post-pivot rows, already wide — no pivot.
    _CONTROL_YAML = """
title: Control
queries:
  grid:
    columns: [rk, a, b]
    values:
      - [r1, 1, 2]
      - [r2, 3, 4]
charts:
  g:
    query: grid
    type: table
rows:
  - g
"""

    def _table_height(
        self, yaml_content: str, local_project: Callable[..., FilesystemProject]
    ) -> float:
        from dbt_charts.core.execute.executor import Executor
        from dbt_charts.core.render.layout_sizing import calculate_data_aware_layout

        result = compile(yaml_content)
        assert result.success, f"Compile failed: {result.errors}"
        assert result.board is not None
        executor = Executor(
            result.board,
            adapter_registry=build_adapter_registry(local_project(Path.cwd())),
            query_registry=result.query_registry,
        )
        sized_board, _ = calculate_data_aware_layout(
            result.board, executor, {}, pre_resolved={}
        )
        return sized_board.layout.items[0].height

    def test_pivoted_table_height_matches_postpivot_rowcount(
        self, local_project: Callable[..., FilesystemProject]
    ):
        pivot_height = self._table_height(self._PIVOT_YAML, local_project)
        control_height = self._table_height(self._CONTROL_YAML, local_project)
        # 4 long rows pivot to 2 wide rows; height must match a real 2-row table,
        # not the 4-row height the sizer reserved before the fix.
        assert pivot_height == control_height

    # Two role="total" rows sharing one rows-dim label ("Total") bucket into a
    # single bottom row, so the six long rows reshape to three wide rows
    # (r1, r2, Total). Pins that the sizer counts POST-pivot rows via
    # pivot_table_data, not the six raw rows the query returns. (Distinct total
    # labels would stay distinct — see test_distinct_total_labels_render_as_
    # separate_rows — but a single grand total is one row.)
    _PIVOT_TOTALS_YAML = """
title: Pivot Totals
queries:
  grid:
    columns: [rk, ck, v, role]
    values:
      - [r1, a, 1, value]
      - [r1, b, 2, value]
      - [r2, a, 3, value]
      - [r2, b, 4, value]
      - [Total, a, 4, total]
      - [Total, b, 6, total]
charts:
  g:
    query: grid
    type: table
    rows: [rk]
    columns: [ck]
    values: [v]
    style:
      row:
        role: role
rows:
  - g
"""

    # Same three post-pivot rows, already wide, no role marker — no pivot.
    _CONTROL_3ROW_YAML = """
title: Control 3-row
queries:
  grid:
    columns: [rk, a, b]
    values:
      - [r1, 1, 2]
      - [r2, 3, 4]
      - [Total, 4, 6]
charts:
  g:
    query: grid
    type: table
rows:
  - g
"""

    def test_pivot_with_role_tagged_totals_sizes_to_merged_rowcount(
        self, local_project: Callable[..., FilesystemProject]
    ):
        pivot_height = self._table_height(self._PIVOT_TOTALS_YAML, local_project)
        control_height = self._table_height(self._CONTROL_3ROW_YAML, local_project)
        # 6 long rows (4 detail + 2 same-label totals) reshape to 3 wide rows
        # (r1, r2, Total). Pins that the sizer reshapes via pivot_table_data
        # rather than reserving height for all 6 raw rows.
        assert pivot_height == control_height

    # Two measures under one column dim, with `rows` OMITTED so the sizer infers
    # the row dims. Both measures must be resolved out of the row-dim inference —
    # if only values[0] were passed, values[1] would be treated as a row dim and
    # split r1/r2 into four wide rows instead of two.
    _PIVOT_TWO_MEASURE_YAML = """
title: Pivot Two Measure
queries:
  grid:
    columns: [rk, ck, v1, v2]
    values:
      - [r1, a, 1, 10]
      - [r1, b, 2, 20]
      - [r2, a, 3, 30]
      - [r2, b, 4, 40]
charts:
  g:
    query: grid
    type: table
    columns: [ck]
    values: [v1, v2]
rows:
  - g
"""

    _PIVOT_TWO_MEASURE_4KEY_YAML = """
title: Pivot Two Measure 4-key
queries:
  grid:
    columns: [rk, ck, v1, v2]
    values:
      - [r1, a, 1, 10]
      - [r1, b, 2, 20]
      - [r2, a, 3, 30]
      - [r2, b, 4, 40]
      - [r3, a, 5, 50]
      - [r3, b, 6, 60]
      - [r4, a, 7, 70]
      - [r4, b, 8, 80]
charts:
  g:
    query: grid
    type: table
    columns: [ck]
    values: [v1, v2]
rows:
  - g
"""

    # Four already-wide rows — the flat counterpart of the 4-key pivots.
    _CONTROL_4ROW_YAML = """
title: Control 4-row
queries:
  grid:
    columns: [rk, a, b]
    values:
      - [r1, 1, 2]
      - [r2, 3, 4]
      - [r3, 5, 6]
      - [r4, 7, 8]
charts:
  g:
    query: grid
    type: table
rows:
  - g
"""

    def _two_extra_rows_cost(
        self, local_project: Callable[..., FilesystemProject]
    ) -> float:
        """What two more table rows cost the sizer, measured on flat controls.

        Pivots that draw group-header rows can't be compared to a flat control
        outright — the header band differs. Comparing the cost of two extra
        row-dim keys cancels the header out and isolates the row count.
        """
        return self._table_height(
            self._CONTROL_4ROW_YAML, local_project
        ) - self._table_height(self._CONTROL_YAML, local_project)

    def test_two_measure_pivot_sizes_to_distinct_row_dim_count(
        self, local_project: Callable[..., FilesystemProject]
    ):
        two_keys = self._table_height(self._PIVOT_TWO_MEASURE_YAML, local_project)
        four_keys = self._table_height(self._PIVOT_TWO_MEASURE_4KEY_YAML, local_project)
        # Two more row-dim keys cost two table rows, not the four long rows they
        # add. Both measures must also resolve out of the row-dim inference — if
        # only values[0] did, v2 would split every key in two.
        assert four_keys - two_keys == self._two_extra_rows_cost(local_project)

    # Nested column dims (year × quarter). The renderer reshapes this the same
    # way it reshapes a single-dim pivot; the sizer must too. Two years, so the
    # group-header row spans more than one column tuple.
    _PIVOT_MULTI_DIM_YAML = """
title: Pivot Multi Dim
queries:
  grid:
    columns: [rk, yr, qt, v]
    values:
      - [r1, 2024, q1, 1]
      - [r1, 2024, q2, 2]
      - [r1, 2025, q1, 3]
      - [r1, 2025, q2, 4]
      - [r2, 2024, q1, 5]
      - [r2, 2024, q2, 6]
      - [r2, 2025, q1, 7]
      - [r2, 2025, q2, 8]
charts:
  g:
    query: grid
    type: table
    rows: [rk]
    columns: [yr, qt]
    values: [v]
rows:
  - g
"""

    _PIVOT_MULTI_DIM_4KEY_YAML = """
title: Pivot Multi Dim 4-key
queries:
  grid:
    columns: [rk, yr, qt, v]
    values:
      - [r1, 2024, q1, 1]
      - [r1, 2024, q2, 2]
      - [r1, 2025, q1, 3]
      - [r1, 2025, q2, 4]
      - [r2, 2024, q1, 5]
      - [r2, 2024, q2, 6]
      - [r2, 2025, q1, 7]
      - [r2, 2025, q2, 8]
      - [r3, 2024, q1, 9]
      - [r3, 2024, q2, 10]
      - [r3, 2025, q1, 11]
      - [r3, 2025, q2, 12]
      - [r4, 2024, q1, 13]
      - [r4, 2024, q2, 14]
      - [r4, 2025, q1, 15]
      - [r4, 2025, q2, 16]
charts:
  g:
    query: grid
    type: table
    rows: [rk]
    columns: [yr, qt]
    values: [v]
rows:
  - g
"""

    def test_multi_dim_pivot_sizes_to_postpivot_rowcount(
        self, local_project: Callable[..., FilesystemProject]
    ):
        two_keys = self._table_height(self._PIVOT_MULTI_DIM_YAML, local_project)
        four_keys = self._table_height(self._PIVOT_MULTI_DIM_4KEY_YAML, local_project)
        # Two more row-dim keys cost two table rows, not the four long rows they
        # add — the sizer reshapes a nested (year × quarter) pivot too.
        assert four_keys - two_keys == self._two_extra_rows_cost(local_project)

    # `values` omitted — the measure is inferred (v). Same 2 post-pivot rows.
    _PIVOT_INFERRED_VALUES_YAML = """
title: Pivot Inferred Values
queries:
  grid:
    columns: [rk, ck, v]
    values:
      - [r1, a, 1]
      - [r1, b, 2]
      - [r2, a, 3]
      - [r2, b, 4]
charts:
  g:
    query: grid
    type: table
    rows: [rk]
    columns: [ck]
rows:
  - g
"""

    def test_values_omitted_pivot_sizes_to_postpivot_rowcount(
        self, local_project: Callable[..., FilesystemProject]
    ):
        pivot_height = self._table_height(
            self._PIVOT_INFERRED_VALUES_YAML, local_project
        )
        control_height = self._table_height(self._CONTROL_YAML, local_project)
        assert pivot_height == control_height

    def test_multi_dim_pivot_slot_fits_every_row(
        self, local_project: Callable[..., FilesystemProject]
    ):
        """The reserved slot must hold what the renderer draws.

        A multi-dim pivot draws one group-header row per outer column dim ABOVE
        the leaf header. Reserving a single header band leaves the slot a row
        short, and the renderer paginates rows away that the data has room for.
        """
        from dbt_charts.core.execute.executor import Executor
        from dbt_charts.core.render import render

        result = compile(self._PIVOT_MULTI_DIM_YAML)
        assert result.success, f"Compile failed: {result.errors}"
        assert result.board is not None
        executor = Executor(
            result.board,
            adapter_registry=build_adapter_registry(local_project(Path.cwd())),
            query_registry=result.query_registry,
        )
        svg = render(result.board, executor, format="svg").output
        assert isinstance(svg, str)
        assert ">r1<" in svg and ">r2<" in svg


class TestTitleCaseMeasurement:
    """Regression: get_title_height must apply the same case transform as the renderer.

    The draw half (_render_title_svg in boards.py) applies apply_case() before
    drawing. The measure half must match, otherwise measured height < drawn height
    when the transform widens glyphs (upper/title case on a lowercase title).
    """

    def test_get_title_height_applies_case_transform_to_measured_string(
        self, monkeypatch
    ):
        """Measured string must be the case-transformed title, not the raw authored text."""
        import mdsvg
        from dbt_charts.core.compile.models.primitives import FontStyle
        from dbt_charts.core.render.sizing import get_title_height

        captured: list[str] = []

        class FakeSize:
            height = 30.0

        def fake_measure(text: str, *_args: object, **_kwargs: object) -> FakeSize:
            captured.append(text)
            return FakeSize()

        monkeypatch.setattr(mdsvg, "measure", fake_measure)

        base = get_theme_style()
        # Force case: upper so the transform is unambiguous.
        seed = base.model_copy(
            update={
                "title": base.title.model_copy(update={"font": FontStyle(case="upper")})
            }
        )
        style = resolve_style(seed)

        authored_title = "monthly revenue by region"
        get_title_height(authored_title, 400.0, resolved_style=style)

        assert captured, "mdsvg.measure was not called"
        # board_title_markdown wraps the title as "# {title}", so the measured
        # string must contain the uppercased text, not the authored lowercase.
        assert "MONTHLY REVENUE BY REGION" in captured[0], (
            f"Measured string {captured[0]!r} does not contain the upper-cased title; "
            "get_title_height is not applying the case transform before measuring"
        )
