"""Integration tests for all layout types.

Tests that all layout types (rows, cols, grid, tabs) render correctly.
"""

import re
import xml.etree.ElementTree as ET
from collections.abc import Callable
from pathlib import Path

from dbt_charts.cli.filesystem_project import FilesystemProject
from dbt_charts.core.compile import compile, compile_file
from dbt_charts.core.compile.config import (
    get_default_theme_name,
    get_theme_style,
)
from dbt_charts.core.execute import Executor
from dbt_charts.core.execute.adapters import build_adapter_registry
from dbt_charts.core.render import render

from .._paths import DBT_CHARTS_DIR


class TestRowsLayout:
    """Tests for rows layout."""

    def test_rows_layout_renders(self, local_project: Callable[..., FilesystemProject]):
        """Test rows layout renders correctly."""
        yaml_content = """
title: Rows Layout Test
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
rows:
  - chart1
  - chart2
"""
        result = compile(yaml_content)
        assert result.success
        assert result.board is not None
        assert result.board.layout.type == "rows"

        executor = Executor(
            result.board,
            adapter_registry=build_adapter_registry(local_project(Path.cwd())),
            query_registry=result.query_registry,
        )

        svg_output = render(result.board, executor, format="svg").output
        assert isinstance(svg_output, str)
        assert "<svg" in svg_output

        html_output = render(result.board, executor, format="html").output
        assert isinstance(html_output, str)
        # HTML format now wraps SVG output in minimal HTML document
        assert "<!DOCTYPE html>" in html_output
        assert "<svg" in html_output
        wrapper_style = re.search(
            r"\.dbt-charts-wrapper\s*\{([^}]*)\}", html_output, re.DOTALL
        )
        assert wrapper_style is not None
        assert "max-width:" not in wrapper_style.group(1)
        # No wrapper padding — the wrapper must not introduce its own gap
        # around the SVG; any layout spacing belongs to the board itself.
        assert "padding:" not in wrapper_style.group(1)


class TestColsLayout:
    """Tests for cols layout."""

    def test_cols_layout_renders(self, local_project: Callable[..., FilesystemProject]):
        """Test cols layout renders correctly."""
        yaml_content = """
title: Cols Layout Test
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
        assert result.success
        assert result.board is not None
        assert result.board.layout.type == "cols"

        executor = Executor(
            result.board,
            adapter_registry=build_adapter_registry(local_project(Path.cwd())),
            query_registry=result.query_registry,
        )

        svg_output = render(result.board, executor, format="svg").output
        assert isinstance(svg_output, str)
        assert "<svg" in svg_output

        html_output = render(result.board, executor, format="html").output
        assert isinstance(html_output, str)
        # HTML format now wraps SVG output in minimal HTML document
        assert "<!DOCTYPE html>" in html_output
        assert "<svg" in html_output


class TestGridLayout:
    """Tests for grid layout."""

    def test_grid_layout_renders(self, local_project: Callable[..., FilesystemProject]):
        """Test grid layout renders correctly."""
        yaml_content = """
title: Grid Layout Test
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
grid:
  columns: 24
  items:
    - item: chart1
      col: 0
      row: 0
      col_span: 12
      row_span: 2
    - item: chart2
      col: 12
      row: 0
      col_span: 12
      row_span: 2
"""
        result = compile(yaml_content)
        assert result.success
        assert result.board is not None
        assert result.board.layout.type == "grid"
        assert result.board.layout.columns == 24

        executor = Executor(
            result.board,
            adapter_registry=build_adapter_registry(local_project(Path.cwd())),
            query_registry=result.query_registry,
        )

        svg_output = render(result.board, executor, format="svg").output
        assert isinstance(svg_output, str)
        assert "<svg" in svg_output

        html_output = render(result.board, executor, format="html").output
        assert isinstance(html_output, str)
        # HTML format now wraps SVG output in minimal HTML document
        assert "<!DOCTYPE html>" in html_output
        assert "<svg" in html_output

    def test_grid_default_columns_is_set(self):
        """Grid layout without explicit columns should supply a non-None integer default."""
        yaml_content = """
title: Default Grid Test
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
grid:
  items:
    - item: chart1
"""
        result = compile(yaml_content)
        assert result.success
        assert result.board is not None
        assert result.board.layout.type == "grid"
        assert result.board.layout.columns is not None
        assert isinstance(result.board.layout.columns, int)


class TestTabsLayout:
    """Tests for tabs layout."""

    def test_tabs_layout_renders(self, local_project: Callable[..., FilesystemProject]):
        """Test tabs layout renders correctly."""
        yaml_content = """
title: Tabs Layout Test
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
tabs:
  items:
    - title: Bar Chart
      rows:
        - chart1
    - title: Line Chart
      rows:
        - chart2
"""
        result = compile(yaml_content)
        assert result.success
        assert result.board is not None
        assert result.board.layout.type == "tabs"
        assert result.board.layout.tab_titles is not None
        assert "Bar Chart" in result.board.layout.tab_titles

        executor = Executor(
            result.board,
            adapter_registry=build_adapter_registry(local_project(Path.cwd())),
            query_registry=result.query_registry,
        )

        svg_output = render(result.board, executor, format="svg").output
        assert isinstance(svg_output, str)
        assert "<svg" in svg_output
        assert "Bar Chart" in svg_output or "Tab 1" in svg_output

        html_output = render(result.board, executor, format="html").output
        assert isinstance(html_output, str)
        # HTML format now wraps SVG output in minimal HTML document
        assert "<!DOCTYPE html>" in html_output
        assert "<svg" in html_output
        # Tab content is rendered in SVG, check tab title appears
        assert "Bar Chart" in html_output


class TestNestedLayouts:
    """Tests for nested layouts."""

    def test_cols_within_rows(self, local_project: Callable[..., FilesystemProject]):
        """Test cols layout nested within rows."""
        yaml_content = """
title: Nested Layout Test
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
rows:
  - title: Top Section
    cols:
      - chart1
      - chart2
  - chart1
"""
        result = compile(yaml_content)
        assert result.success
        assert result.board is not None
        assert result.board.layout.type == "rows"

        executor = Executor(
            result.board,
            adapter_registry=build_adapter_registry(local_project(Path.cwd())),
            query_registry=result.query_registry,
        )

        svg_output = render(result.board, executor, format="svg").output
        assert isinstance(svg_output, str)
        assert "<svg" in svg_output

    def test_reference_example_top_level_cols_remain_visible(
        self, local_project: Callable[..., FilesystemProject]
    ):
        """The nested-layouts playground example should not collapse any first-row column."""
        project = local_project(DBT_CHARTS_DIR)
        result = compile_file(
            project.path(
                "examples/playground/charts/cards/nested-layouts.yml"
            ).read_board()
        )
        assert result.success
        assert result.board is not None

        executor = Executor(
            result.board,
            adapter_registry=build_adapter_registry(project),
            query_registry=result.query_registry,
        )
        svg_output = render(result.board, executor, format="svg").output
        assert isinstance(svg_output, str)

        first_section = result.board.layout.items[0].board
        assert first_section is not None

        widths = [item.width for item in first_section.layout.items]
        assert len(widths) == 3
        assert all(width > 0 for width in widths)
        assert "50% width" in svg_output


class TestRootBoxModelRendering:
    """Tests for rendered SVG geometry at the root content box."""

    def test_root_layout_renders_inside_padded_content_box(
        self, local_project: Callable[..., FilesystemProject]
    ):
        """Root layout children should render at content-box width, not outer width."""
        yaml_content = """
title: Root Render Contract
rows:
  - title: Card
    text: |
      Root layout children should render inside the padded content box.
"""
        result = compile(yaml_content)
        assert result.success
        assert result.board is not None

        executor = Executor(
            result.board,
            adapter_registry=build_adapter_registry(local_project(Path.cwd())),
            query_registry=result.query_registry,
        )
        svg_output = render(result.board, executor, format="svg").output
        assert isinstance(svg_output, str)
        expected_content_width = get_theme_style(
            get_default_theme_name()
        ).frame.max_width - (2 * get_theme_style().frame.margin)
        root = ET.fromstring(svg_output)
        ns = {"svg": "http://www.w3.org/2000/svg"}
        nested_widths: list[float] = []

        # After integer pixel-snap, the margin translate emits as an integer
        # ("translate(24, ") even when the margin value is a float (24.0).
        # Accept both forms so this test isn't fragile to formatting.
        margin_int = int(round(get_theme_style().frame.margin))
        margin_prefixes = {
            f"translate({get_theme_style().frame.margin}, ",
            f"translate({margin_int}, ",
        }
        for group in root.findall(".//svg:g", ns):
            transform = group.attrib.get("transform", "")
            if not any(transform.startswith(p) for p in margin_prefixes):
                continue
            # Any depth, not just one <g> down: a no-op layout-content translate
            # (x_offset=0, y_layout=0) is no longer wrapped in its own <g> (see
            # svg_utils.translate_group), so the nested board's <svg> can sit
            # directly under the margin group instead of one level deeper.
            nested_widths.extend(
                float(svg.attrib["width"])
                for svg in group.findall(".//svg:svg", ns)
                if "width" in svg.attrib
            )

        # Prose hugs its own width, so it sits inside the content box, never wider.
        assert nested_widths
        assert max(nested_widths) <= expected_content_width
        assert get_theme_style().frame.max_width not in nested_widths
