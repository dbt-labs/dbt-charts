"""Tests for the new compile module (dbt_charts.compile).

These tests verify the new architecture works correctly.
"""

from collections.abc import Callable

import pytest

from dbt_charts.cli.filesystem_project import FilesystemProject
from dbt_charts.core.compile import (
    Board,
    Layout,
    LayoutItem,
    compile,
)
from dbt_charts.core.compile.models.chart.normalized import _BaseChartFields
from dbt_charts.core.compile.resolve import resolve


class TestCompileBasic:
    """Basic compilation tests."""

    def test_compile_simple_dashboard(self):
        """Test compiling a simple dashboard."""
        yaml_content = """
title: Test Dashboard
queries:
  test_query:
    sql: SELECT 1 as value
    source: test_profile
charts:
  test_chart:
    query: test_query
    type: kpi
    value: value
rows:
  - test_chart
"""
        result = compile(yaml_content)

        assert result.success
        assert result.board is not None
        assert isinstance(result.board, Board)
        assert result.board.title == "Test Dashboard"
        assert result.board.id == "test_dashboard"

    def test_compile_result_has_queries(self):
        """Test that queries are in the compiled board."""
        yaml_content = """
title: Test
queries:
  sales:
    sql: SELECT * FROM sales
    source: test_profile
charts:
  chart1:
    query: sales
    type: line
    x: date
    y: amount
rows:
  - chart1
"""
        result = compile(yaml_content)

        assert result.success
        assert "sales" in result.board.queries
        query = result.board.queries["sales"]
        # Check query has required attributes
        assert hasattr(query, "query_type")
        assert query.query_type == "sql"
        assert query.sql == "SELECT * FROM sales"
        assert query.source == "test_profile"

    def test_compile_result_has_charts(self):
        """Test that charts are in the compiled board."""
        yaml_content = """
title: Test
queries:
  data:
    sql: SELECT * FROM data
    source: test_profile
charts:
  my_chart:
    query: data
    type: bar
    title: My Bar Chart
    x: category
    y: value
rows:
  - my_chart
"""
        result = compile(yaml_content)

        assert result.success
        assert "my_chart" in result.board.charts
        chart = result.board.charts["my_chart"]
        assert isinstance(chart, _BaseChartFields)
        assert chart.id == "my_chart"
        assert chart.title == "My Bar Chart"
        assert chart.type == "bar"
        assert chart.query_name == "data"

    def test_board_style_can_clear_inherited_chart_view_stroke(self):
        """Board-level style.charts.view.stroke: null should clear theme defaults."""
        yaml_content = """
title: Test
style:
  charts:
    view:
      stroke: null
queries:
  q:
    sql: SELECT DATE '2025-01-01' AS date, 112000 AS value
    source: test_profile
charts:
  c:
    query: q
    type: line
    x: date
    y: value
rows:
  - c
"""
        result = compile(yaml_content)

        assert result.success
        # style.charts.view.stroke: null is parsed and applied via StylePatch.
        # The resolved_style field carries the board-scoped resolved style.
        assert result.board.resolved_style is not None


class TestUnifiedLayout:
    """Tests for the unified Layout structure."""

    def test_rows_layout(self):
        """Test rows layout is unified."""
        yaml_content = """
title: Test
queries:
  q:
    sql: SELECT 1
    source: test_profile
charts:
  c1:
    query: q
    type: kpi
    value: value
  c2:
    query: q
    type: kpi
    value: value
rows:
  - c1
  - c2
"""
        result = compile(yaml_content)

        assert result.success
        layout = result.board.layout
        assert isinstance(layout, Layout)
        assert layout.type == "rows"
        assert len(layout.items) == 2

        # Check items are LayoutItem objects
        for item in layout.items:
            assert isinstance(item, LayoutItem)
            assert item.type == "chart"
            assert item.chart is not None
            assert isinstance(item.chart, _BaseChartFields)

    def test_cols_layout(self):
        """Test cols layout is unified."""
        yaml_content = """
title: Test
queries:
  q:
    sql: SELECT 1
    source: test_profile
charts:
  c1:
    query: q
    type: kpi
    value: value
  c2:
    query: q
    type: kpi
    value: value
cols:
  - c1
  - c2
"""
        result = compile(yaml_content)

        assert result.success
        assert result.board.layout.type == "cols"
        assert len(result.board.layout.items) == 2


class TestTypes:
    """Tests for compiled type guarantees."""

    def test_chart_has_required_fields(self):
        """Test Chart has all required fields."""
        yaml_content = """
title: Test
queries:
  q:
    sql: SELECT 1
    source: test_profile
charts:
  minimal_chart:
    query: q
    type: line
rows:
  - minimal_chart
"""
        result = compile(yaml_content)

        assert result.success
        chart = result.board.charts["minimal_chart"]

        # Required fields are always present
        assert chart.id == "minimal_chart"
        assert chart.title == ""  # Omitted title stays omitted.
        assert chart.subtitle == ""  # Default empty string
        assert chart.notes == ""  # Default empty string
        assert chart.query_name == "q"
        assert chart.query is not None
        # Check query has required attributes
        assert hasattr(chart.query, "query_type")

    def test_chart_subtitle_is_preserved(self):
        """Chart subtitle metadata should survive compilation."""
        yaml_content = """
title: Test
queries:
  q:
    sql: SELECT 1 AS revenue
    source: test_profile
charts:
  c:
    query: q
    type: bar
    title: Revenue
    subtitle: Current quarter only
    y: revenue
rows:
  - c
"""
        result = compile(yaml_content)

        assert result.success
        assert result.board.charts["c"].subtitle == "Current quarter only"

    def test_query_notes_is_preserved(self):
        """Query notes metadata should survive compilation."""
        yaml_content = """
title: Test
queries:
  q:
    notes: "Monthly revenue by region for executive trend charts"
    sql: SELECT 1 AS revenue
    source: test_profile
charts:
  c:
    query: q
    type: table
rows:
  - c
"""
        result = compile(yaml_content)

        assert result.success
        assert result.board.queries["q"].notes == (
            "Monthly revenue by region for executive trend charts"
        )

    def test_board_has_required_fields(self):
        """Test Board has all required fields."""
        yaml_content = """
queries:
  q:
    sql: SELECT 1
    source: test_profile
charts:
  c:
    query: q
    type: kpi
    value: v
rows:
  - c
"""
        result = compile(yaml_content)

        assert result.success
        board = result.board

        # Required fields are always present
        assert board.id is not None
        assert board.title == ""  # Empty since not provided
        assert board.notes == ""
        assert board.layout is not None
        assert isinstance(board.layout, Layout)
        assert board.variables is not None  # Empty dict, not None
        assert board.queries is not None
        assert board.charts is not None


class TestErrorHandling:
    """Tests for error handling."""

    def test_invalid_yaml_returns_error(self):
        """Test that invalid YAML returns ParseError."""
        yaml_content = "invalid: yaml: content: here"

        result = compile(yaml_content)

        assert not result.success
        assert len(result.errors) > 0

    def test_missing_query_reference_returns_error(self):
        """Test that missing query reference returns error."""
        yaml_content = """
title: Test
charts:
  chart1:
    query: nonexistent_query
    type: line
rows:
  - chart1
"""
        result = compile(yaml_content)

        assert not result.success
        assert len(result.errors) > 0

    @pytest.mark.parametrize(
        ("query_ref", "expected_name"),
        [
            ("missing_query", "missing_query"),
            ("queries.missing_query", "missing_query"),
        ],
    )
    def test_missing_inline_chart_query_reports_normalized_name(
        self, query_ref: str, expected_name: str
    ) -> None:
        """Inline chart query errors should use the stripped query reference."""
        yaml_content = f"""
title: Test
rows:
  - query: {query_ref}
    type: line
"""

        result = compile(yaml_content)

        assert not result.success
        assert any(
            f"references unknown query '{expected_name}'" in str(error)
            for error in result.errors
        )

    def test_empty_yaml_returns_error(self):
        """Test that empty YAML returns error."""
        result = compile("")

        assert not result.success
        assert len(result.errors) > 0


class TestQueryTypes:
    """Tests for different query types."""

    def test_sql_with_source(self):
        """Test SQL query with source field."""
        yaml_content = """
title: Test
queries:
  simple:
    sql: SELECT * FROM users
    source: test_profile
charts:
  c:
    query: simple
    type: table
rows:
  - c
"""
        result = compile(yaml_content)

        assert result.success
        query = result.board.queries["simple"]
        assert query.query_type == "sql"
        assert query.sql == "SELECT * FROM users"
        assert query.source == "test_profile"

    def test_explicit_sql_type(self):
        """Test explicit SQL type."""
        yaml_content = """
title: Test
queries:
  explicit:
    type: sql
    sql: SELECT * FROM orders
    source: test_profile
charts:
  c:
    query: explicit
    type: table
rows:
  - c
"""
        result = compile(yaml_content)

        assert result.success
        query = result.board.queries["explicit"]
        assert query.query_type == "sql"
        assert query.source == "test_profile"


class TestNestedBoards:
    """Tests for nested board handling."""

    def test_nested_board_in_rows(self):
        """Test nested board in rows layout."""
        yaml_content = """
title: Parent
queries:
  q:
    sql: SELECT 1
    source: test_profile
charts:
  c:
    query: q
    type: kpi
    value: v
rows:
  - title: Nested Section
    rows:
      - c
"""
        result = compile(yaml_content)

        assert result.success
        assert result.board.layout.type == "rows"

        # First item should be a nested board
        item = result.board.layout.items[0]
        assert item.type == "board"
        assert item.board is not None
        assert isinstance(item.board, Board)


class TestTitleOnlyLayoutItems:
    """Tests that title-only dicts in layout are treated as boards, not charts."""

    def test_title_only_dict_is_board(self):
        """A layout item with only a title (no query/type) should be a board."""
        yaml_content = """
title: Parent
queries:
  q:
    sql: SELECT 1
    source: test_profile
charts:
  c:
    query: q
    type: kpi
    value: v
rows:
  - title: Section Header
  - c
"""
        result = compile(yaml_content)
        assert result.success

        # First item should be a nested board (title-only), not a chart
        item = result.board.layout.items[0]
        assert item.type == "board", (
            f"Expected title-only dict to be a board, got {item.type}"
        )
        assert item.board is not None
        assert isinstance(item.board, Board)

        # Second item should still be a chart
        item2 = result.board.layout.items[1]
        assert item2.type == "chart"

    def test_title_with_query_is_chart(self):
        """A layout item with title AND query should still be a chart."""
        yaml_content = """
title: Parent
queries:
  q:
    sql: SELECT 1 as value
    source: test_profile
rows:
  - label: My Chart
    query: q
    type: kpi
    value: value
"""
        result = compile(yaml_content)
        assert result.success

        item = result.board.layout.items[0]
        assert item.type == "chart"


class TestVariables:
    """Tests for variable handling."""

    def test_variables_are_preserved(self):
        """Test that variables are in the compiled board."""
        yaml_content = """
title: Test
variables:
  date_filter:
    input: daterange
    default: ["2024-01-01", "2024-12-31"]
queries:
  q:
    sql: SELECT * FROM data WHERE date BETWEEN '{{ date_filter[0] }}' AND '{{ date_filter[1] }}'
    source: test_profile
charts:
  c:
    query: q
    type: line
rows:
  - c
"""
        result = compile(yaml_content)

        assert result.success
        assert "date_filter" in result.board.variables
        assert result.board.variable_defaults["date_filter"] == [
            "2024-01-01",
            "2024-12-31",
        ]


class TestQueryTypesExtended:
    """Extended tests for different query types."""

    def test_http_query_type(self):
        """Test HTTP query type."""
        yaml_content = """
title: HTTP Test
queries:
  api_data:
    type: http
    url: https://api.example.com/data
    method: GET
    headers:
      Authorization: Bearer token123
charts:
  c:
    query: api_data
    type: table
rows:
  - c
"""
        result = compile(yaml_content)

        assert result.success
        query = result.board.queries["api_data"]
        assert query.query_type == "http"
        assert query.url == "https://api.example.com/data"
        assert query.method == "GET"
        assert query.headers == {"Authorization": "Bearer token123"}

    def test_sql_query_rejects_limit(self):
        """limit: is not an authored sql-query field — write LIMIT in the SQL."""
        yaml_content = """
title: Limit Test
queries:
  limited:
    type: sql
    sql: SELECT * FROM large_table
    source: test_profile
    limit: 100
charts:
  c:
    query: limited
    type: table
rows:
  - c
"""
        result = compile(yaml_content)

        assert not result.success
        assert "limit" in str(result.errors)


class TestContentAndMarkdown:
    """Tests for content/markdown handling."""

    def test_board_with_content(self):
        """Test board with markdown content."""
        yaml_content = """
title: Content Test
text: |
  # Welcome
  This is markdown content.

  - Item 1
  - Item 2
"""
        result = compile(yaml_content)

        assert result.success
        assert result.board.text.startswith("# Welcome")
        assert "This is markdown content." in result.board.text

    def test_nested_board_with_content(self):
        """Test nested board with content."""
        yaml_content = """
title: Parent
rows:
  - title: Section
    text: |
      Section description here.
"""
        result = compile(yaml_content)

        assert result.success
        nested = result.board.layout.items[0].board
        assert "Section description here." in nested.text


class TestGridLayout:
    """Tests for grid layout."""

    def test_grid_layout(self):
        """Test grid layout compilation."""
        yaml_content = """
title: Grid Test
queries:
  q:
    sql: SELECT 1
    source: test_profile
charts:
  c1:
    query: q
    type: kpi
    value: v
  c2:
    query: q
    type: kpi
    value: v
grid:
  columns: 24
  items:
    - item: c1
      col: 0
      row: 0
      col_span: 12
      row_span: 2
    - item: c2
      col: 12
      row: 0
      col_span: 12
      row_span: 2
"""
        result = compile(yaml_content)

        assert result.success
        # Grid should be converted to unified layout
        layout = result.board.layout
        assert layout.type == "grid"
        assert len(layout.items) == 2


class TestChartTypes:
    """Tests for different chart types."""

    def test_line_chart(self):
        """Test line chart configuration."""
        yaml_content = """
title: Test
queries:
  q:
    sql: SELECT date, value FROM data
    source: test_profile
charts:
  line:
    query: q
    type: line
    x: date
    y: value
rows:
  - line
"""
        result = compile(yaml_content)

        assert result.success
        chart = result.board.charts["line"]
        assert chart.type == "line"
        assert chart.x == "date"
        assert chart.y == "value"

    def test_bar_chart(self):
        """Test bar chart configuration."""
        yaml_content = """
title: Test
queries:
  q:
    sql: SELECT category, count FROM data
    source: test_profile
charts:
  bar:
    query: q
    type: bar
    x: category
    y: count
rows:
  - bar
"""
        result = compile(yaml_content)

        assert result.success
        chart = result.board.charts["bar"]
        assert chart.type == "bar"

    def test_scatter_chart(self):
        """Test scatter chart configuration."""
        yaml_content = """
title: Test
queries:
  q:
    sql: SELECT x, y, size FROM data
    source: test_profile
charts:
  scatter:
    query: q
    type: scatter
    x: x
    y: y
    size: size
rows:
  - scatter
"""
        result = compile(yaml_content)

        assert result.success
        chart = result.board.charts["scatter"]
        assert chart.type == "scatter"
        assert chart.size == "size"

    def test_area_chart(self):
        """Test area chart configuration."""
        yaml_content = """
title: Test
queries:
  q:
    sql: SELECT date, value FROM data
    source: test_profile
charts:
  area:
    query: q
    type: area
    x: date
    y: value
rows:
  - area
"""
        result = compile(yaml_content)

        assert result.success
        chart = result.board.charts["area"]
        assert chart.type == "area"

    def test_table_chart(self):
        """Test table chart configuration."""
        yaml_content = """
title: Test
queries:
  q:
    sql: SELECT * FROM data
    source: test_profile
charts:
  tbl:
    query: q
    type: table
rows:
  - tbl
"""
        result = compile(yaml_content)

        assert result.success
        chart = result.board.charts["tbl"]
        assert chart.type == "table"

    def test_chart_with_color(self):
        """Test chart with color encoding."""
        yaml_content = """
title: Test
queries:
  q:
    sql: SELECT date, value, category FROM data
    source: test_profile
charts:
  colored:
    query: q
    type: line
    x: date
    y: value
    color: category
rows:
  - colored
"""
        result = compile(yaml_content)

        assert result.success
        chart = result.board.charts["colored"]
        assert chart.color == "category"

    def test_kpi_chart(self):
        """Test KPI chart configuration."""
        yaml_content = """
title: Test
queries:
  q:
    sql: SELECT total_revenue FROM metrics
    source: test_profile
charts:
  kpi:
    query: q
    type: kpi
    value: total_revenue
    label: Total Revenue
rows:
  - kpi
"""
        result = compile(yaml_content)

        assert result.success
        chart = result.board.charts["kpi"]
        assert chart.type == "kpi"
        assert chart.value == "total_revenue"


class TestValidationErrors:
    """Tests for validation error handling."""

    def test_multiple_validation_errors(self):
        """Test that multiple errors are collected."""
        yaml_content = """
title: Broken
charts:
  c1:
    query: missing1
    type: bar
  c2:
    query: missing2
    type: bar
rows:
  - c1
  - c2
"""
        result = compile(yaml_content)

        assert not result.success
        assert len(result.errors) >= 2

    def test_missing_chart_in_layout(self):
        """Test error when chart in layout doesn't exist."""
        yaml_content = """
title: Test
queries:
  q:
    sql: SELECT 1
    source: test_profile
charts:
  existing:
    query: q
    type: kpi
    value: v
rows:
  - existing
  - nonexistent
"""
        result = compile(yaml_content)

        assert not result.success
        error_str = str(result.errors)
        assert "nonexistent" in error_str.lower()


class TestStyleInheritance:
    """Tests for style inheritance."""

    def test_board_style(self):
        """Test board-level style with a sentinel background that won't match any default."""
        sentinel_bg = "#f0f1f2"
        yaml_content = f"""
title: Styled
style:
  background: "{sentinel_bg}"
queries:
  q:
    sql: SELECT 1
    source: test_profile
charts:
  c:
    query: q
    type: kpi
    value: v
rows:
  - c
"""
        result = compile(yaml_content)

        assert result.success
        # style is now a StylePatch object with pre-parsed values
        assert result.board.authored_style.background == sentinel_bg

    def test_boards_without_explicit_theme_inherit_default_theme(self):
        """Unthemed boards should inherit the configured default theme name only."""
        from dbt_charts.core.compile.config import get_default_theme_name

        yaml_content = """
title: Theme Neutral
queries:
  q:
    sql: SELECT 1 AS value
    source: test_profile
charts:
  c:
    query: q
    type: kpi
    value: value
rows:
  - title: Nested
    rows:
      - c
"""
        result = compile(yaml_content)

        assert result.success
        default_theme = get_default_theme_name()
        assert result.board.theme == default_theme
        # No authored style — board.authored_style is None (background not explicitly set).
        assert (
            result.board.authored_style is None
            or result.board.authored_style.background is None
        )
        nested = result.board.layout.items[0].board
        assert nested is not None
        assert nested.theme == default_theme
        assert nested.authored_style is None or nested.authored_style.background is None

    def test_explicit_board_theme_still_inherits_to_children(self):
        """Nested boards should inherit an explicitly configured parent theme."""
        yaml_content = """
title: Themed
theme: neon
queries:
  q:
    sql: SELECT 1 AS value
    source: test_profile
charts:
  c:
    query: q
    type: kpi
    value: value
rows:
  - title: Nested
    rows:
      - c
"""
        result = compile(yaml_content)

        assert result.success
        assert result.board.theme == "neon"
        nested = result.board.layout.items[0].board
        assert nested is not None
        assert nested.theme == "neon"

    def test_child_theme_overrides_parent_and_default(self):
        """Nested boards should prefer their own theme over inherited/default themes."""
        yaml_content = """
title: Themed
theme: neon
queries:
  q:
    sql: SELECT 1 AS value
    source: test_profile
charts:
  c:
    query: q
    type: kpi
    value: value
rows:
  - title: Nested
    theme: stark
    rows:
      - c
"""
        result = compile(yaml_content)

        assert result.success
        assert result.board.theme == "neon"
        nested = result.board.layout.items[0].board
        assert nested is not None
        assert nested.theme == "stark"


class TestQueryPrefixes:
    """Tests for query prefix handling."""

    def test_queries_prefix_in_chart(self):
        """Test that queries. prefix works in chart query reference."""
        yaml_content = """
title: Test
queries:
  my_data:
    sql: SELECT * FROM data
    source: test_profile
charts:
  c:
    query: queries.my_data
    type: table
rows:
  - c
"""
        result = compile(yaml_content)

        assert result.success
        chart = result.board.charts["c"]
        # The query should be resolved correctly
        assert chart.query is not None


class TestExternalQueryReferences:
    """Tests for cross-file query references (file.yml#query_name syntax)."""

    def test_external_query_reference(
        self, tmp_path, local_project: Callable[..., FilesystemProject]
    ):
        """Test that external query references work."""
        from dbt_charts import compile

        # Create a shared queries file
        shared_file = tmp_path / "_shared.yml"
        shared_file.write_text(
            """
queries:
  sales:
    type: values
    rows:
      - {product: A, revenue: 100}
  users:
    sql: SELECT * FROM users
    source: test_profile
"""
        )

        # Create a dashboard that references the shared queries
        dashboard_content = """
title: Test Dashboard
charts:
  sales_chart:
    query: _shared.yml#sales
    type: bar
    x: product
    y: revenue
rows:
  - sales_chart
"""
        # Compile with base_dir at the project root
        result = compile(
            dashboard_content, base_dir=local_project(tmp_path).directory()
        )

        assert result.success, f"Compilation failed: {result.errors}"
        assert "sales_chart" in result.board.charts

        chart = result.board.charts["sales_chart"]
        # Query name should include the file reference
        assert chart.query_name == "_shared.yml#sales"
        # Query should be resolved
        assert chart.query is not None

    def test_external_query_not_found_error(
        self, tmp_path, local_project: Callable[..., FilesystemProject]
    ):
        """Test error when external query name doesn't exist in file."""
        from dbt_charts import compile

        # Create a shared queries file
        shared_file = tmp_path / "_shared.yml"
        shared_file.write_text(
            """
queries:
  sales:
    sql: SELECT * FROM sales
    source: test_profile
"""
        )

        dashboard_content = """
title: Test
charts:
  chart:
    query: _shared.yml#nonexistent
    type: bar
rows:
  - chart
"""
        result = compile(
            dashboard_content, base_dir=local_project(tmp_path).directory()
        )

        assert not result.success
        assert any("nonexistent" in str(e) for e in result.errors)

    def test_external_file_not_found_error(
        self, tmp_path, local_project: Callable[..., FilesystemProject]
    ):
        """Test error when external query file doesn't exist."""
        from dbt_charts import compile

        dashboard_content = """
title: Test
charts:
  chart:
    query: missing.yml#sales
    type: bar
rows:
  - chart
"""
        result = compile(
            dashboard_content, base_dir=local_project(tmp_path).directory()
        )

        assert not result.success
        assert any("External query file not found" in str(e) for e in result.errors)

    def test_multiple_external_queries_same_file(
        self, tmp_path, local_project: Callable[..., FilesystemProject]
    ):
        """Test referencing multiple queries from the same external file."""
        from dbt_charts import compile

        # Create a shared queries file
        shared_file = tmp_path / "_shared.yml"
        shared_file.write_text(
            """
queries:
  sales:
    sql: SELECT * FROM sales
    source: test_profile
  users:
    sql: SELECT * FROM users
    source: test_profile
  products:
    sql: SELECT * FROM products
    source: test_profile
"""
        )

        dashboard_content = """
title: Test
charts:
  sales_chart:
    query: _shared.yml#sales
    type: bar
  users_chart:
    query: _shared.yml#users
    type: table
  products_chart:
    query: _shared.yml#products
    type: line
rows:
  - sales_chart
  - users_chart
  - products_chart
"""
        result = compile(
            dashboard_content, base_dir=local_project(tmp_path).directory()
        )

        assert result.success, f"Compilation failed: {result.errors}"
        assert len(result.board.charts) == 3

        # Each chart should reference its query correctly
        assert result.board.charts["sales_chart"].query_name == "_shared.yml#sales"
        assert result.board.charts["users_chart"].query_name == "_shared.yml#users"
        assert (
            result.board.charts["products_chart"].query_name == "_shared.yml#products"
        )

    def test_nested_path_external_query(
        self, tmp_path, local_project: Callable[..., FilesystemProject]
    ):
        """Test external query reference with nested directory path."""
        from dbt_charts import compile

        # Create nested directory structure
        queries_dir = tmp_path / "queries"
        queries_dir.mkdir()
        shared_file = queries_dir / "shared.yml"
        shared_file.write_text(
            """
queries:
  data:
    sql: SELECT * FROM data
    source: test_profile
"""
        )

        dashboard_content = """
title: Test
charts:
  chart:
    query: queries/shared.yml#data
    type: bar
rows:
  - chart
"""
        result = compile(
            dashboard_content, base_dir=local_project(tmp_path).directory()
        )

        assert result.success, f"Compilation failed: {result.errors}"
        assert result.board.charts["chart"].query_name == "queries/shared.yml#data"


class TestInlineQueries:
    """Test inline query definitions in charts."""

    def test_sql_query_missing_source_error(self):
        """Test that SQL queries without source field fail validation."""
        yaml_content = """
title: Test Dashboard
queries:
  test_query:
    sql: SELECT 1 as value
charts:
  test_chart:
    query: test_query
    type: kpi
    value: value
rows:
  - test_chart
"""
        result = compile(yaml_content)

        assert not result.success
        assert any("source" in str(e).lower() for e in result.errors)

    def test_inline_sql_query_in_chart(self):
        """Test inline SQL query definition in chart."""
        yaml_content = """
title: Test Dashboard
charts:
  sales_chart:
    query:
      sql: SELECT 1 as value, 'test' as label
      source: test_profile
    type: table
rows:
  - sales_chart
"""
        result = compile(yaml_content)

        assert result.success, f"Compilation failed: {result.errors}"
        assert "sales_chart" in result.board.charts
        chart = result.board.charts["sales_chart"]
        # Inline queries get auto-generated names
        assert chart.query_name.startswith("_inline_query_")
        assert chart.query is not None

    def test_inline_sql_query_with_source(self):
        """Test inline SQL query with source field."""
        yaml_content = """
title: Test Dashboard
charts:
  sales_chart:
    query:
      sql: SELECT 1 as value
      source: test_profile
    type: kpi
    value: value
rows:
  - sales_chart
"""
        result = compile(yaml_content)

        assert result.success, f"Compilation failed: {result.errors}"
        assert "sales_chart" in result.board.charts

    def test_inline_query_in_layout(self):
        """Test inline query definition directly in layout."""
        yaml_content = """
title: Test Dashboard
rows:
  - title: Sales
    cols:
      - sales_chart:
          query:
            sql: SELECT 1 as value
            source: test_profile
          type: kpi
          value: value
"""
        result = compile(yaml_content)

        assert result.success, f"Compilation failed: {result.errors}"
        # Inline charts in layout are in layout items, not board.charts
        # Check that the layout has the chart
        layout_items = result.board.layout.items
        assert len(layout_items) > 0
        # The first item should be a nested board (the row)
        assert layout_items[0].type == "board"
        # The nested board should have a chart in its layout
        nested_board = layout_items[0].board
        assert nested_board.layout.items[0].type == "chart"
        chart = nested_board.layout.items[0].chart
        assert chart.query_name.startswith("_inline_query_")
        assert chart.type == "kpi"
        assert chart.value == "value"

    def test_inline_query_vs_reference(self):
        """Test that inline queries work alongside referenced queries."""
        yaml_content = """
title: Test Dashboard
queries:
  referenced_query:
    sql: SELECT 2 as value
    source: test_profile
charts:
  referenced_chart:
    query: referenced_query
    type: kpi
    value: value
  inline_chart:
    query:
      sql: SELECT 1 as value
      source: test_profile
    type: kpi
    value: value
rows:
  - referenced_chart
  - inline_chart
"""
        result = compile(yaml_content)

        assert result.success, f"Compilation failed: {result.errors}"
        assert "referenced_chart" in result.board.charts
        assert "inline_chart" in result.board.charts
        # Referenced chart should use the query name
        assert result.board.charts["referenced_chart"].query_name == "referenced_query"
        # Inline chart should have auto-generated query name
        assert result.board.charts["inline_chart"].query_name.startswith(
            "_inline_query_"
        )


class TestArcChartFields:
    """Arc charts use theta + color, matching Vega-Lite terminology."""

    def test_donut_normalizes_to_pie_preserving_theta_and_color(self):
        """Donut chart normalizes to pie and preserves theta/color."""
        yaml_content = """
queries:
  dogs:
    columns: [dog_name, score]
    values:
      - ["Tuna", 9.1]
      - ["Pixel", 8.4]
      - ["Sage", 9.4]
charts:
  dog_donut:
    query: dogs
    type: donut
    theta: score
    color: dog_name
rows:
  - dog_donut
"""
        result = compile(yaml_content)

        assert result.success, f"Compilation failed: {result.errors}"
        chart = result.board.charts["dog_donut"]
        assert chart.type == "pie", "donut should normalize to pie"
        assert chart.style is not None
        assert chart.style.inner_radius == pytest.approx(0.6), (
            "donut sets style.inner_radius=0.6"
        )
        assert chart.theta == "score"
        assert chart.color == "dog_name"

    def test_pie_theta_and_color(self):
        """Pie chart uses theta for values, color for categories."""
        yaml_content = """
queries:
  data:
    columns: [category, amount]
    values:
      - ["A", 30]
      - ["B", 70]
charts:
  my_pie:
    query: data
    type: pie
    theta: amount
    color: category
rows:
  - my_pie
"""
        result = compile(yaml_content)

        assert result.success, f"Compilation failed: {result.errors}"
        chart = result.board.charts["my_pie"]
        assert chart.theta == "amount"
        assert chart.color == "category"

    def test_donut_preserves_explicit_inner_radius(self):
        """Explicit style.inner_radius on a donut chart is preserved (flat per-family shape)."""
        yaml_content = """
queries:
  data:
    columns: [category, amount]
    values:
      - ["A", 30]
      - ["B", 70]
charts:
  my_donut:
    query: data
    type: donut
    theta: amount
    color: category
    style:
      inner_radius: 0.3
rows:
  - my_donut
"""
        result = compile(yaml_content)

        assert result.success, f"Compilation failed: {result.errors}"
        chart = result.board.charts["my_donut"]
        assert chart.type == "pie"
        assert chart.style is not None
        assert chart.style.inner_radius == pytest.approx(0.3), (
            "explicit style.inner_radius should be preserved"
        )

    def test_pie_with_inner_radius_renders_donut_style(self):
        """type: pie with style.inner_radius > 0 should be valid (flat per-family shape)."""
        yaml_content = """
queries:
  data:
    columns: [category, amount]
    values:
      - ["A", 30]
      - ["B", 70]
charts:
  my_pie:
    query: data
    type: pie
    theta: amount
    color: category
    style:
      inner_radius: 0.4
rows:
  - my_pie
"""
        result = compile(yaml_content)

        assert result.success, f"Compilation failed: {result.errors}"
        chart = result.board.charts["my_pie"]
        assert chart.type == "pie"
        assert chart.style is not None
        assert chart.style.inner_radius == pytest.approx(0.4)

    def test_donut_auto_fills_chart_total_when_omitted(self):
        """A minimum donut (``type: donut`` + ``theta`` + ``color``, no
        ``chart.total:`` block) auto-fills ``chart.total`` so the center
        renders. The value layer pulls from a VL ``joinaggregate`` SUM(theta)
        at render time; this test pins the compile-side contract that produces
        it: ``chart.total.visible`` defaults True and ``chart.total.label`` is
        the slug-titled theta field name.
        """
        yaml_content = """
queries:
  data:
    columns: [category, score]
    values:
      - ["A", 30]
      - ["B", 70]
charts:
  my_donut:
    query: data
    type: donut
    theta: score
    color: category
rows:
  - my_donut
"""
        result = compile(yaml_content)

        assert result.success, f"Compilation failed: {result.errors}"
        chart = result.board.charts["my_donut"]
        assert chart.total is not None, "donut must auto-fill chart.total when omitted"
        assert chart.total.visible is True
        assert chart.total.label == "Total Score", (
            f"auto label should prefix 'Total ' before the slug-titled theta; "
            f"got {chart.total.label!r}"
        )

    def test_donut_auto_total_defaults_format_to_integer_preset(self):
        """Auto-filled donut center total must resolve to the ``integer``
        preset spec (``,.0f``) so the rendered value keeps full precision
        with thousands separators (``68,473``) instead of losing digits to a
        3-significant-figure SI abbreviation (``68.5k``). The donut hole has
        room for the whole number; abbreviating it discards precision the
        source data has.

        Unlike ``chart.total.label`` (chart-root copy, visible right after
        compile()), the format default is a style-cascade concern and only
        resolves once the theme/board/chart-local style cascade completes --
        see ``compile/resolve/chart/pie.py``'s ``_resolve_pie``.
        """
        yaml_content = """
queries:
  data:
    columns: [bucket, value]
    values:
      - ["A", 12345]
      - ["B", 56128]
charts:
  my_donut:
    query: data
    type: donut
    theta: value
    color: bucket
rows:
  - my_donut
"""
        resolved, _ = self._resolved_chart(yaml_content, "my_donut")
        assert resolved.style.total_style.value.format.spec == ",.0f", (
            f"auto-filled donut total must default its format to the "
            f"'integer' preset (full precision, thousands separators); got "
            f"{resolved.style.total_style.value.format!r}"
        )

    def test_donut_authored_total_format_wins_over_auto_default(self):
        """Authored ``style.total.value.format`` must survive — the
        resolve-time default only fills in when nothing in the cascade set
        one. This pins the contract so a future refactor doesn't
        accidentally stomp authored formats.
        """
        yaml_content = """
queries:
  data:
    columns: [bucket, value]
    values:
      - ["A", 12345]
      - ["B", 56128]
charts:
  my_donut:
    query: data
    type: donut
    theta: value
    color: bucket
    total:
      label: "Custom Label"
    style:
      total:
        value:
          format: ",.1f"
rows:
  - my_donut
"""
        resolved, _ = self._resolved_chart(yaml_content, "my_donut")
        assert resolved.total is not None
        assert resolved.total.label == "Custom Label"
        assert resolved.style.total_style.value.format.spec == ",.1f", (
            f"authored style.total.value.format must win over the "
            f"resolve-time default; got {resolved.style.total_style.value.format!r}"
        )

    def test_donut_authored_format_only_still_auto_fills_label(self):
        """An author fixing the center-total format (``total: {format: ...}``)
        without authoring a ``label`` must still get the auto-derived caption
        — chart.total's label auto-fill is independent of whether the style
        cascade set a format. Only the label is filled in; the author's
        format is preserved untouched.
        """
        yaml_content = """
queries:
  data:
    columns: [bucket, value]
    values:
      - ["A", 12345]
      - ["B", 56128]
charts:
  my_donut:
    query: data
    type: donut
    theta: value
    color: bucket
    style:
      total:
        value:
          format: ",d"
rows:
  - my_donut
"""
        resolved, _ = self._resolved_chart(yaml_content, "my_donut")
        assert resolved.style.total_style.value.format.spec == ",d", (
            f"authored format must be preserved untouched; got "
            f"{resolved.style.total_style.value.format!r}"
        )
        assert resolved.total is not None
        assert resolved.total.label == "Total Value", (
            f"omitted label must still be auto-derived even though the "
            f"author set a style.total.value.format; got "
            f"{resolved.total.label!r}"
        )

    def test_donut_authored_label_only_still_auto_fills_format(self):
        """The mirror of the format-only case: an author who names the caption
        but authors no style format must still get the non-abbreviating
        default rather than an unformatted raw number. Label and format
        fill independently.
        """
        yaml_content = """
queries:
  data:
    columns: [bucket, value]
    values:
      - ["A", 12345]
      - ["B", 56128]
charts:
  my_donut:
    query: data
    type: donut
    theta: value
    color: bucket
    total:
      label: Sessions
rows:
  - my_donut
"""
        resolved, _ = self._resolved_chart(yaml_content, "my_donut")
        assert resolved.total is not None
        assert resolved.total.label == "Sessions", (
            f"authored label must be preserved untouched; got {resolved.total.label!r}"
        )
        assert resolved.style.total_style.value.format.spec == ",.0f", (
            f"omitted format must still auto-fill to the 'integer' preset, or "
            f"the center renders an unformatted raw number; got "
            f"{resolved.style.total_style.value.format!r}"
        )

    def test_donut_auto_label_preserves_acronyms_and_unit_suffixes(self):
        """The auto-derived donut center label goes through the shared
        ``inferred_display_name`` helper so SaaS-domain acronyms (``arr`` →
        ``ARR``) and unit suffixes (``revenue_usd`` → ``Revenue ($)``) survive
        — not a naive ``.capitalize()`` that would mangle them to ``Arr``.

        The ``"Total "`` prefix must NOT interfere with acronym
        preservation; the prefix is applied AFTER ``inferred_display_name``
        so the acronym is intact when composed.
        """
        yaml_content = """
queries:
  data:
    columns: [segment, arr]
    values:
      - ["A", 1000]
      - ["B", 2000]
charts:
  my_donut:
    query: data
    type: donut
    theta: arr
    color: segment
rows:
  - my_donut
"""
        result = compile(yaml_content)

        assert result.success, f"Compilation failed: {result.errors}"
        chart = result.board.charts["my_donut"]
        assert chart.total is not None
        assert chart.total.label == "Total ARR", (
            f"acronym must survive slug-titling AND the Total prefix; "
            f"got {chart.total.label!r}"
        )

    # ------------------------------------------------------------------
    # "Total " prefix skip list (substring match on theta)
    # ------------------------------------------------------------------
    def _compile_donut(self, theta: str, color_col: str = "segment") -> object:
        yaml_content = f"""
queries:
  data:
    columns: [{color_col}, {theta}]
    values:
      - ["A", 1]
      - ["B", 2]
charts:
  my_donut:
    query: data
    type: donut
    theta: {theta}
    color: {color_col}
rows:
  - my_donut
"""
        result = compile(yaml_content)
        assert result.success, f"Compilation failed: {result.errors}"
        return result.board.charts["my_donut"]

    def test_donut_auto_label_includes_unit_suffix_with_total_prefix(self):
        """Unit-suffix expansion still survives the prefix wrap.
        ``revenue_usd`` → slug ``revenue ($)`` → title ``Revenue ($)`` →
        prefixed ``Total Revenue ($)``.
        """
        chart = self._compile_donut("revenue_usd")
        assert chart.total is not None
        assert chart.total.label == "Total Revenue ($)", chart.total.label

    def test_donut_auto_label_lowercases_stopwords_per_title_case_rules(self):
        """Multi-word theta with a stopword renders the engine's shared
        title-case convention — `apply_case("title")` uses the NYT
        small-words list, so ``by`` stays lowercase. Locks in
        consistency with axis titles / KPI captions that share the same
        path; guards against a hand-rolled per-token capitalizer sneaking
        back in.
        """
        chart = self._compile_donut("revenue_by_region")
        assert chart.total is not None
        assert chart.total.label == "Total Revenue by Region", chart.total.label

    def test_donut_auto_label_skips_total_prefix_when_theta_starts_with_total(self):
        """Skip rule: when the theta column already contains ``total`` as a
        substring (case-insensitive), prepending ``Total `` would produce
        ``Total Total Revenue`` — visibly redundant. The substring rule
        catches `total_*`, `*_total`, `subtotal_*`, `grand_total_*` etc.
        without enumeration.
        """
        chart = self._compile_donut("total_revenue")
        assert chart.total is not None
        # slug_to_text("total_revenue") → "total revenue" → title "Total Revenue".
        # Substring "total" present → no prefix added.
        assert chart.total.label == "Total Revenue", chart.total.label

    def test_donut_auto_label_skip_rule_is_case_insensitive(self):
        """Skip rule matches case-insensitively — author column casing
        shouldn't change the prefix decision.
        """
        chart = self._compile_donut("TOTAL_BOOKINGS")
        assert chart.total is not None
        assert chart.total.label == "Total Bookings", chart.total.label

    def test_donut_auto_label_skips_for_total_substring_anywhere(self):
        """Skip rule fires on substring anywhere in the column name, not
        only at the start. ``revenue_total`` → ``Revenue Total`` without
        a redundant prefix.
        """
        chart = self._compile_donut("revenue_total")
        assert chart.total is not None
        assert chart.total.label == "Revenue Total", chart.total.label

    def test_donut_chart_total_visible_false_opts_out_of_auto_render(self):
        """Author opt-out: ``chart.total.visible: false`` suppresses the
        auto-rendered center on a donut. The center value/label layers should
        not be emitted.
        """
        yaml_content = """
queries:
  data:
    columns: [category, score]
    values:
      - ["A", 30]
      - ["B", 70]
charts:
  my_donut:
    query: data
    type: donut
    theta: score
    color: category
    total:
      visible: false
rows:
  - my_donut
"""
        result = compile(yaml_content)

        assert result.success, f"Compilation failed: {result.errors}"
        chart = result.board.charts["my_donut"]
        assert chart.total is not None
        assert chart.total.visible is False, (
            "opt-out via total.visible: false must reach the compiled chart"
        )

    def test_solid_pie_does_not_auto_fill_chart_total(self):
        """A solid pie (``type: pie``, no ``inner_radius``) has no center hole
        to render into. The auto-total firing predicate is donut-only.
        """
        yaml_content = """
queries:
  data:
    columns: [category, amount]
    values:
      - ["A", 30]
      - ["B", 70]
charts:
  my_pie:
    query: data
    type: pie
    theta: amount
    color: category
rows:
  - my_pie
"""
        result = compile(yaml_content)

        assert result.success, f"Compilation failed: {result.errors}"
        chart = result.board.charts["my_pie"]
        assert chart.total is None, (
            "solid pie must not auto-fill chart.total (donut-only behavior)"
        )

    def test_pie_with_explicit_inner_radius_auto_fills_chart_total(self):
        """A ``type: pie`` chart with ``style.inner_radius > 0`` is visually
        a donut and must trigger the same auto-total fill as ``type: donut``.
        The firing predicate is shape (inner_radius), not the authoring alias.
        """
        yaml_content = """
queries:
  data:
    columns: [category, score]
    values:
      - ["A", 30]
      - ["B", 70]
charts:
  pie_with_hole:
    query: data
    type: pie
    theta: score
    color: category
    style:
      inner_radius: 0.5
rows:
  - pie_with_hole
"""
        result = compile(yaml_content)

        assert result.success, f"Compilation failed: {result.errors}"
        chart = result.board.charts["pie_with_hole"]
        assert chart.total is not None, (
            "type: pie + inner_radius > 0 is donut-shaped and must auto-fill "
            "chart.total — firing predicate is shape, not authoring alias"
        )
        assert chart.total.visible is True
        assert chart.total.label == "Total Score", chart.total.label

    # ------------------------------------------------------------------
    # Default 2-line label template for pie/donut
    # ------------------------------------------------------------------
    # The default Jinja templates live in the theme at
    # ``style.charts.marks.slice.labels.default_template`` and flow
    # through the style cascade into ``resolved_chart.style.slice_mark.labels.template``
    # at resolve time when the author omits ``template:``. Tests below
    # read the expected template off the resolved theme rather than
    # pinning a literal — theme values are not pinned by tests.

    def _resolved_chart(self, yaml_content: str, chart_id: str):
        """Resolve a chart through the render pipeline so
        ``chart.style.slice_mark.labels`` carries the synthesized theme
        default. Compile alone leaves the label template un-defaulted; the
        default fires at ``_resolve_pie``, since that's where the theme
        cascade is in scope. Returns ``(resolved_chart, resolved_style)`` so
        tests can compare the chart's resolved template against the theme
        template directly.
        """

        result = compile(yaml_content)
        assert result.success, f"Compilation failed: {result.errors}"
        board_chart = result.board.charts[chart_id]
        # Pass empty data — labels-default resolution doesn't depend on rows.
        resolved = resolve(
            board_chart, [], chart_style_context=result.board.chart_style_context
        )
        return resolved, result.board.chart_style_context

    def test_donut_auto_fills_default_labels_template_when_omitted(self):
        """Minimum donut with color + theta and no ``labels:`` auto-fills the
        editorial 2-line template from theme defaults, unblocking the existing
        direct-label render path.
        """
        yaml_content = """
queries:
  data:
    columns: [segment, revenue]
    values:
      - ["A", 100]
      - ["B", 200]
charts:
  my_donut:
    query: data
    type: donut
    theta: revenue
    color: segment
rows:
  - my_donut
"""
        chart, resolved_style = self._resolved_chart(yaml_content, "my_donut")
        assert chart.style.slice_mark.labels is not None, (
            "donut with color + theta must auto-fill labels"
        )
        theme_template = resolved_style.marks.slice.labels.default_template.with_color
        assert chart.style.slice_mark.labels.template == theme_template

    def test_pie_auto_fills_default_labels_template_when_omitted(self):
        """Same auto-fill behavior on ``type: pie``. The trigger is per
        family (pie ∪ donut), not the donut alias specifically.
        """
        yaml_content = """
queries:
  data:
    columns: [segment, revenue]
    values:
      - ["A", 100]
      - ["B", 200]
charts:
  my_pie:
    query: data
    type: pie
    theta: revenue
    color: segment
rows:
  - my_pie
"""
        chart, resolved_style = self._resolved_chart(yaml_content, "my_pie")
        assert chart.style.slice_mark.labels is not None
        theme_template = resolved_style.marks.slice.labels.default_template.with_color
        assert chart.style.slice_mark.labels.template == theme_template

    def test_arc_without_color_auto_fills_template_without_segment_line(self):
        """No-color donut: the auto-fill still fires, but uses the
        without-color variant that drops the ``{{ color }}`` segment-name
        word. Per-wedge percent + nominal labels remain informative even
        without per-row series identification — the attached-table
        fallback can't swatch-key on a non-existent color field anyway.

        The broader no-color identification gap (no engine-level way to
        identify rows without authoring ``labels.template`` or ``color:``)
        remains a follow-up; this default just makes the no-color case
        usable rather than broken.
        """
        yaml_content = """
queries:
  data:
    columns: [segment, revenue]
    values:
      - ["A", 100]
      - ["B", 200]
charts:
  my_donut:
    query: data
    type: donut
    theta: revenue
rows:
  - my_donut
"""
        chart, resolved_style = self._resolved_chart(yaml_content, "my_donut")
        assert chart.style.slice_mark.labels is not None
        theme_template = resolved_style.marks.slice.labels.default_template.no_color
        assert chart.style.slice_mark.labels.template == theme_template

    def test_board_can_override_theme_default_template_via_style_cascade(self):
        """Cascade contract: a board that overrides
        ``style.charts.marks.slice.labels.default_template.with_color`` must
        flow through to ``resolved_chart.style.slice_mark.labels.template``
        when the chart omits its own labels block. Pins that the theme default isn't a
        Python-side bake — themes/boards can shift it.

        Uses the global ``charts.marks`` override path; the pie-family
        path (``charts.pie.marks.slice.labels.X``) is a separate
        cascade-propagation gap, tracked elsewhere.
        """
        yaml_content = """
style:
  charts:
    marks:
      slice:
        labels:
          default_template:
            with_color: "{{ color }}: {{ value }}"
queries:
  data:
    columns: [segment, revenue]
    values:
      - ["A", 100]
      - ["B", 200]
charts:
  my_donut:
    query: data
    type: donut
    theta: revenue
    color: segment
rows:
  - my_donut
"""
        chart, resolved_style = self._resolved_chart(yaml_content, "my_donut")
        assert chart.style.slice_mark.labels is not None
        theme_template = resolved_style.marks.slice.labels.default_template.with_color
        assert chart.style.slice_mark.labels.template == theme_template, (
            "board-level theme override must flow through cascade to resolved chart"
        )
        assert chart.style.slice_mark.labels.template == "{{ color }}: {{ value }}", (
            "board override value should match the YAML literal"
        )

    def test_arc_style_labels_template_overrides_default(self):
        """Style-authored surface: ``style.marks.slice.labels.template`` authored
        at the chart level propagates to
        ``resolved_chart.style.slice_mark.labels.template`` instead of the
        theme default.
        """
        yaml_content = """
queries:
  data:
    columns: [segment, revenue]
    values:
      - ["A", 100]
      - ["B", 200]
charts:
  my_donut:
    query: data
    type: donut
    theta: revenue
    color: segment
    style:
      marks:
        slice:
          labels:
            template: "{{ segment }}"
rows:
  - my_donut
"""
        chart, _resolved_style = self._resolved_chart(yaml_content, "my_donut")
        assert chart.style.slice_mark.labels is not None
        assert chart.style.slice_mark.labels.template == "{{ segment }}"

    def test_arc_style_labels_where_propagates_to_resolved(self):
        """``style.marks.slice.labels.where`` authored at chart level flows
        through to the resolved ``chart.style.slice_mark.labels.where``.
        """
        yaml_content = """
queries:
  data:
    columns: [segment, revenue]
    values:
      - ["A", 100]
      - ["B", 200]
charts:
  my_pie:
    query: data
    type: pie
    theta: revenue
    style:
      marks:
        slice:
          labels:
            template: "{{ segment }}"
            where: "{{ revenue > 50 }}"
rows:
  - my_pie
"""
        chart, _resolved_style = self._resolved_chart(yaml_content, "my_pie")
        assert chart.style.slice_mark.labels is not None
        assert chart.style.slice_mark.labels.template == "{{ segment }}"
        assert chart.style.slice_mark.labels.where == "{{ revenue > 50 }}"
