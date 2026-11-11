"""Tests for cascading variable dependencies.

Tests the feature where variables can depend on other variables through
their options queries, enabling cascading dropdowns (e.g., Country → State → City).
"""

from .conftest import compile_with_board_sources


class TestCascadingVariableDependencies:
    """Test variable dependency detection and circular dependency checking."""

    def test_simple_variable_dependency(self):
        """Test that variable dependencies are detected from options query SQL."""
        yaml_content = """
title: Cascading Test

sources:
  db:
    type: duckdb
    path: ":memory:"

variables:
  country:
    input: select
    options:
      query: country_options
      column: country

  state:
    input: select
    options:
      query: state_options
      column: state

queries:
  country_options:
    sql: SELECT DISTINCT country FROM locations
    source: db

  state_options:
    sql: |
      SELECT DISTINCT state FROM locations
      WHERE {{ filter('country', country) }}
    source: db

charts:
  map:
    title: Locations
    type: bar
    query:
      sql: SELECT * FROM locations WHERE {{ filter('country', country) }} AND {{ filter('state', state) }}
      source: db
    x: city
    y: population

rows:
  - map
"""
        result = compile_with_board_sources(yaml_content)
        assert result.success, f"Compilation failed: {result.errors}"
        board = result.board

        # Check that state variable depends on country
        state_var = board.variables.get("state")
        assert state_var is not None
        assert "country" in state_var.variable_dependencies

        # Check that country variable has no dependencies
        country_var = board.variables.get("country")
        assert country_var is not None
        assert len(country_var.variable_dependencies) == 0

    def test_multi_level_cascade(self):
        """Test three-level cascading: country → state → city."""
        yaml_content = """
title: Multi-Level Cascade

sources:
  db:
    type: duckdb
    path: ":memory:"

variables:
  country:
    input: select
    options:
      query: country_options
      column: country

  state:
    input: select
    options:
      query: state_options
      column: state

  city:
    input: select
    options:
      query: city_options
      column: city

queries:
  country_options:
    sql: SELECT DISTINCT country FROM locations
    source: db

  state_options:
    sql: |
      SELECT DISTINCT state FROM locations
      WHERE {{ filter('country', country) }}
    source: db

  city_options:
    sql: |
      SELECT DISTINCT city FROM locations
      WHERE {{ filter('country', country) }}
        AND {{ filter('state', state) }}
    source: db

charts:
  chart1:
    title: Test
    type: bar
    query:
      sql: SELECT 1 as x, 2 as y
      source: db
    x: x
    y: y

rows:
  - chart1
"""
        result = compile_with_board_sources(yaml_content)
        assert result.success, f"Compilation failed: {result.errors}"
        board = result.board

        # country has no deps
        assert len(board.variables["country"].variable_dependencies) == 0

        # state depends on country
        assert board.variables["state"].variable_dependencies == {"country"}

        # city depends on both country and state
        assert board.variables["city"].variable_dependencies == {"country", "state"}

    def test_circular_dependency_detection(self):
        """Test that circular variable dependencies are detected and raise an error."""
        yaml_content = """
title: Circular Dependency Test

sources:
  db:
    type: duckdb
    path: ":memory:"

variables:
  var_a:
    input: select
    options:
      query: query_a
      column: value

  var_b:
    input: select
    options:
      query: query_b
      column: value

queries:
  query_a:
    sql: SELECT value FROM t WHERE {{ filter('x', var_b) }}
    source: db

  query_b:
    sql: SELECT value FROM t WHERE {{ filter('x', var_a) }}
    source: db

charts:
  chart1:
    title: Test
    type: bar
    query:
      sql: SELECT 1 as x, 2 as y
      source: db
    x: x
    y: y

rows:
  - chart1
"""
        result = compile_with_board_sources(yaml_content)
        assert not result.success
        assert any("Circular variable dependency" in str(e) for e in result.errors)
        assert [e.code for e in result.errors] == ["ERR-VARIABLE-CYCLE"]

    def test_chart_transitive_dependencies(self):
        """Test that chart dependencies include transitive variable dependencies."""
        yaml_content = """
title: Transitive Deps Test

sources:
  db:
    type: duckdb
    path: ":memory:"

variables:
  country:
    input: select
    options:
      query: country_options
      column: country

  state:
    input: select
    options:
      query: state_options
      column: state

queries:
  country_options:
    sql: SELECT DISTINCT country FROM locations
    source: db

  state_options:
    sql: |
      SELECT DISTINCT state FROM locations
      WHERE {{ filter('country', country) }}
    source: db

charts:
  state_chart:
    title: By State
    type: bar
    query:
      sql: |
        SELECT state, COUNT(*) as count FROM locations
        WHERE {{ filter('state', state) }}
        GROUP BY state
      source: db
    x: state
    y: count

rows:
  - state_chart
"""
        result = compile_with_board_sources(yaml_content)
        assert result.success, f"Compilation failed: {result.errors}"
        board = result.board

        # Chart directly uses 'state' variable
        # But 'state' depends on 'country', so chart should depend on both
        state_chart = board.charts["state_chart"]
        assert "state" in state_chart.variable_dependencies
        assert "country" in state_chart.variable_dependencies

    def test_no_self_dependency(self):
        """Test that a variable cannot depend on itself."""
        yaml_content = """
title: Self Dependency Test

sources:
  db:
    type: duckdb
    path: ":memory:"

variables:
  category:
    input: select
    options:
      query: category_options
      column: category

queries:
  category_options:
    sql: |
      SELECT DISTINCT category FROM products
      WHERE {{ filter('category', category) }}
    source: db

charts:
  chart1:
    title: Test
    type: bar
    query:
      sql: SELECT 1 as x, 2 as y
      source: db
    x: x
    y: y

rows:
  - chart1
"""
        result = compile_with_board_sources(yaml_content)
        assert result.success, f"Compilation failed: {result.errors}"
        board = result.board

        # Variable should not depend on itself
        category_var = board.variables["category"]
        assert "category" not in category_var.variable_dependencies

    def test_inline_sql_option_query_promoted_to_named_query(self):
        """Test that inline SQL in options.query is promoted to a synthetic named query."""
        yaml_content = """
title: Inline SQL Options Test
source: db

sources:
  db:
    type: duckdb
    path: ":memory:"

variables:
  category:
    input: select
    options:
      query: |
        SELECT DISTINCT product_category as value
        FROM products
        WHERE product_category IS NOT NULL
        ORDER BY 1

queries:
  main_data:
    sql: SELECT * FROM products WHERE {{ filter('product_category', category) }}
    source: db

charts:
  chart1:
    title: Test
    type: bar
    query: main_data
    x: product_category
    y: revenue

rows:
  - chart1
"""
        result = compile_with_board_sources(yaml_content)
        assert result.success, f"Compilation failed: {result.errors}"
        board = result.board

        # The inline SQL should have been promoted to a synthetic named query
        var = board.variables["category"]
        assert var.options.query == "_var_options_category"

        # The synthetic query must exist in board.queries and contain the original SQL
        assert "_var_options_category" in board.queries
        assert "SELECT DISTINCT" in board.queries["_var_options_category"].sql.upper()

    def test_inline_sql_option_query_with_variable_dependencies(self):
        """Test inline SQL in options.query that references other variables."""
        yaml_content = """
title: Inline SQL Deps Test
source: db

sources:
  db:
    type: duckdb
    path: ":memory:"

variables:
  country:
    input: select
    options:
      query: country_opts

  state:
    input: select
    options:
      query: |
        SELECT DISTINCT state as value FROM locations
        WHERE {{ filter('country', country) }}

queries:
  country_opts:
    sql: SELECT DISTINCT country FROM locations
    source: db

charts:
  chart1:
    title: Test
    type: bar
    query:
      sql: SELECT 1 as x, 2 as y
      source: db
    x: x
    y: y

rows:
  - chart1
"""
        result = compile_with_board_sources(yaml_content)
        assert result.success, f"Compilation failed: {result.errors}"
        board = result.board

        # state's inline SQL references country, so it should depend on country
        state_var = board.variables["state"]
        assert "country" in state_var.variable_dependencies

        # The inline SQL should have been promoted
        assert state_var.options.query == "_var_options_state"

    def test_named_query_reference_not_promoted(self):
        """Test that a normal query name reference is not treated as inline SQL."""
        yaml_content = """
title: Named Query Ref Test

sources:
  db:
    type: duckdb
    path: ":memory:"

variables:
  category:
    input: select
    options:
      query: category_options

queries:
  category_options:
    sql: SELECT DISTINCT category FROM products
    source: db

charts:
  chart1:
    title: Test
    type: bar
    query:
      sql: SELECT 1 as x, 2 as y
      source: db
    x: x
    y: y

rows:
  - chart1
"""
        result = compile_with_board_sources(yaml_content)
        assert result.success, f"Compilation failed: {result.errors}"
        board = result.board

        # The query name should remain unchanged (not promoted)
        var = board.variables["category"]
        assert var.options.query == "category_options"

    def test_variable_without_query_options(self):
        """Test that variables with static options have no dependencies."""
        yaml_content = """
title: Static Options Test

sources:
  db:
    type: duckdb
    path: ":memory:"

variables:
  category:
    input: select
    options:
      static: [Electronics, Clothing, Food]

charts:
  chart1:
    title: Test
    type: bar
    query:
      sql: SELECT 1 as x, 2 as y
      source: db
    x: x
    y: y

rows:
  - chart1
"""
        result = compile_with_board_sources(yaml_content)
        assert result.success, f"Compilation failed: {result.errors}"
        board = result.board

        # Variable with static options should have no dependencies
        category_var = board.variables["category"]
        assert len(category_var.variable_dependencies) == 0
