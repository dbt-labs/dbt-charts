"""Tests for column-option query promotion at compile time.

Pins:
1. Normalizer promotes options.column and top-level variable.column to
   _var_{name}_options synthetic named queries with the right SQL.
2. The variable's option-query pointer is rewritten to the synthetic name.
3. Invalid identifiers (SQL injection risk) raise a CompilationError.
4. collect_all_query_names includes the promoted column-option query.
5. Rendering the same board twice executes the query against the adapter only
   once — second render hits the persistent cache.
6. A failing column-option query degrades the dropdown to empty without
   aborting the render.
"""

from __future__ import annotations

from unittest.mock import Mock

import pytest

from dbt_charts.core.compile.models.query.normalized import SqlQuery
from dbt_charts.core.diagnostics.execution import QueryError
from dbt_charts.core.execute import Executor
from dbt_charts.core.execute.adapters.base import QueryResult
from dbt_charts.core.execute.collect import collect_all_query_names

from .conftest import compile_with_board_sources

# ─────────────────────────────────────────────────────────────────────────────
# YAML fixtures — every case in this file is one minimal board that differs only
# in the column ref it binds, so they all come from the one builder.
# ─────────────────────────────────────────────────────────────────────────────


def _board_with_column_ref(col_ref: str, top_level: bool = False) -> str:
    """Build a minimal board binding ``region`` to ``col_ref``."""
    binding = (
        f"    column: {col_ref!r}"
        if top_level
        else f"    options:\n      column: {col_ref!r}"
    )
    return f"""\
title: Column Ref Test
sources:
  db:
    type: duckdb
    path: ":memory:"
source: db
queries:
  sales:
    sql: SELECT 1 AS x
charts:
  c:
    query: sales
    type: bar
    x: x
    y: x
variables:
  region:
    input: select
{binding}
rows:
  - c
"""


_OPTIONS_COLUMN_YAML = _board_with_column_ref("regions.name")
_TOP_LEVEL_COLUMN_YAML = _board_with_column_ref("regions.name", top_level=True)
_INVALID_IDENTIFIER_YAML = _board_with_column_ref("regions; DROP TABLE users--")
_INVALID_COLUMN_ONLY_YAML = _board_with_column_ref("name")


# ─────────────────────────────────────────────────────────────────────────────
# Test 1: Normalizer produces the right SqlQuery + key
# ─────────────────────────────────────────────────────────────────────────────


class TestNormalizerPromotion:
    def test_options_column_produces_synthetic_query(self) -> None:
        """options.column → _var_{name}_options in board.queries with SELECT DISTINCT."""
        result = compile_with_board_sources(_OPTIONS_COLUMN_YAML)
        assert result.success, f"Compilation failed: {result.errors}"
        board = result.board

        assert "_var_options_region" in board.queries
        q = board.queries["_var_options_region"]
        assert isinstance(q, SqlQuery)
        assert "SELECT DISTINCT" in q.sql.upper()
        assert "regions" in q.sql
        assert "name" in q.sql
        assert "ORDER BY" in q.sql.upper()

    def test_options_column_pointer_rewritten(self) -> None:
        """After promotion, var.options.query points to the synthetic name."""
        result = compile_with_board_sources(_OPTIONS_COLUMN_YAML)
        assert result.success
        var = result.board.variables["region"]
        assert var.options is not None
        assert var.options.query == "_var_options_region"
        # The column field is cleared after promotion
        assert var.options.column is None

    def test_top_level_column_produces_synthetic_query(self) -> None:
        """top-level variable.column → _var_{name}_options in board.queries."""
        result = compile_with_board_sources(_TOP_LEVEL_COLUMN_YAML)
        assert result.success, f"Compilation failed: {result.errors}"
        board = result.board

        assert "_var_options_region" in board.queries
        q = board.queries["_var_options_region"]
        assert isinstance(q, SqlQuery)
        assert "SELECT DISTINCT" in q.sql.upper()
        assert "regions" in q.sql

    def test_top_level_column_pointer_rewritten(self) -> None:
        """After promotion from var.column, var.get_option_query() returns synthetic name."""
        result = compile_with_board_sources(_TOP_LEVEL_COLUMN_YAML)
        assert result.success
        var = result.board.variables["region"]
        # After promotion, the query is routed through options.query or var.query
        assert var.get_option_query() == "_var_options_region"

    def test_invalid_identifier_raises_at_compile_time(self) -> None:
        """column ref containing SQL injection chars raises CompilationError."""
        result = compile_with_board_sources(_INVALID_IDENTIFIER_YAML)
        assert not result.success
        assert any(
            "region" in str(e).lower() or "identifier" in str(e).lower()
            for e in result.errors
        )

    def test_column_without_dot_raises_at_compile_time(self) -> None:
        """column ref without 'table.column' dot notation raises CompilationError."""
        result = compile_with_board_sources(_INVALID_COLUMN_ONLY_YAML)
        assert not result.success

    def test_column_without_dot_carries_the_registered_code(self) -> None:
        """A missing dot is an authoring mistake, not an internal defect —
        ERR-INTERNAL is a bug signal per `dbt-charts/AGENTS.md`, not a tier."""
        result = compile_with_board_sources(_INVALID_COLUMN_ONLY_YAML)

        assert [e.code for e in result.errors] == ["ERR-VALIDATION-FIELD"]

    def test_sql_is_select_distinct_col_from_tbl_order_by(self) -> None:
        """Synthesized SQL matches canonical form: SELECT DISTINCT col FROM tbl ORDER BY col."""
        result = compile_with_board_sources(_OPTIONS_COLUMN_YAML)
        assert result.success
        q = result.board.queries["_var_options_region"]
        assert isinstance(q, SqlQuery)
        expected = "SELECT DISTINCT name FROM regions ORDER BY name"
        assert q.sql.strip() == expected


# ─────────────────────────────────────────────────────────────────────────────
# Schema-qualified table refs. The table half of a column ref may name any
# number of dot-separated identifiers (schema.table, database.schema.table,
# BigQuery's project.dataset.table) — a table outside the connection's default
# schema is otherwise unreachable through the shorthand. Every part must still
# be a bare identifier, so the guard stays a whitelist.
# ─────────────────────────────────────────────────────────────────────────────


class TestSchemaQualifiedColumnRef:
    @pytest.mark.parametrize("top_level", [False, True], ids=["options", "top_level"])
    def test_schema_qualified_table_compiles(self, top_level: bool) -> None:
        """schema.table.column reaches a table outside the default schema."""
        result = compile_with_board_sources(
            _board_with_column_ref("gis.fact_sales.property_type", top_level)
        )
        assert result.success, f"Compilation failed: {result.errors}"
        q = result.board.queries["_var_options_region"]
        assert isinstance(q, SqlQuery)
        assert q.sql.strip() == (
            "SELECT DISTINCT property_type FROM gis.fact_sales ORDER BY property_type"
        )

    def test_three_part_table_compiles(self) -> None:
        """database.schema.table qualification also survives — depth is not capped."""
        result = compile_with_board_sources(
            _board_with_column_ref("warehouse.gis.fact_sales.property_type")
        )
        assert result.success, f"Compilation failed: {result.errors}"
        q = result.board.queries["_var_options_region"]
        assert isinstance(q, SqlQuery)
        assert "FROM warehouse.gis.fact_sales" in q.sql

    @pytest.mark.parametrize(
        "col_ref",
        [
            "gis..fact_sales.property_type",
            ".fact_sales.property_type",
            "gis.fact_sales..property_type",
            "gis.fact sales.property_type",
            'gis."fact_sales".property_type',
            "gis;DROP TABLE users--.fact_sales.property_type",
            "gis.fact_sales.property_type; DROP TABLE users--",
            "gis.fact_sales.property_type--",
            "gis.fact_sales.",
        ],
        ids=[
            "empty_middle_part",
            "leading_dot",
            "empty_part_before_column",
            "whitespace_in_table_part",
            "quoted_table_part",
            "injection_in_table",
            "injection_in_column",
            "comment_marker_in_column",
            "trailing_dot_empty_column",
        ],
    )
    def test_malformed_refs_still_rejected(self, col_ref: str) -> None:
        """Allowing dots must not open the guard to anything but bare identifiers."""
        result = compile_with_board_sources(_board_with_column_ref(col_ref))
        assert not result.success, f"{col_ref!r} should not compile"
        assert any("identifier" in str(e).lower() for e in result.errors), (
            f"{col_ref!r} rejected, but not for being a bad identifier: {result.errors}"
        )

    def test_rejection_names_the_qualified_form(self) -> None:
        """The error teaches the qualified spelling — the whole point of the fix.

        Pinned on one case rather than all nine: "identifier" alone was in the
        old message too, so without this the hint could be reverted silently.
        """
        result = compile_with_board_sources(
            _board_with_column_ref("gis.fact sales.col")
        )
        assert not result.success
        assert any("schema-qualified" in str(e) for e in result.errors), result.errors


# ─────────────────────────────────────────────────────────────────────────────
# Test 2: collect_all_query_names includes promoted column-option query
# ─────────────────────────────────────────────────────────────────────────────


class TestCollectAllQueryNamesIncludesColumnOption:
    def test_options_column_included_in_all_query_names(self) -> None:
        """Promoted column-option query joins the parallel pre-render phase."""
        result = compile_with_board_sources(_OPTIONS_COLUMN_YAML)
        assert result.success
        names = collect_all_query_names(result.board)
        assert "_var_options_region" in names

    def test_top_level_column_included_in_all_query_names(self) -> None:
        result = compile_with_board_sources(_TOP_LEVEL_COLUMN_YAML)
        assert result.success
        names = collect_all_query_names(result.board)
        assert "_var_options_region" in names


# ─────────────────────────────────────────────────────────────────────────────
# Test 3: Cache test — second render hits the cache, not the adapter again
# ─────────────────────────────────────────────────────────────────────────────

_RENDER_CACHE_YAML = """\
title: Cache Test
sources:
  db:
    type: duckdb
    path: ":memory:"
source: db
queries:
  sales:
    sql: SELECT 1 AS x, 1 AS y
charts:
  c:
    query: sales
    type: bar
    x: x
    y: y
variables:
  region:
    input: select
    options:
      column: regions.name
rows:
  - c
"""


class TestColumnOptionUsesNamedQueryCache:
    def test_second_render_does_not_re_execute_column_option_query(self) -> None:
        """Column-option query hits the executor cache on second render, not the adapter."""
        from dbt_charts.core.execute.parallel import execute_queries_parallel

        result = compile_with_board_sources(_RENDER_CACHE_YAML)
        assert result.success

        registry = Mock()
        registry.execute.return_value = QueryResult(data=[{"name": "East"}])
        executor = Executor(board=result.board, adapter_registry=registry)

        # First render: pre-execute all queries (what renderer.py does)
        all_names = collect_all_query_names(result.board)
        execute_queries_parallel(executor, all_names)

        calls_after_first_render = registry.execute.call_count

        # Second render: same pre-execute — the column-option query is cached
        execute_queries_parallel(executor, all_names)

        # The adapter must not have been called again for the column option query
        assert registry.execute.call_count == calls_after_first_render


# ─────────────────────────────────────────────────────────────────────────────
# Test 4: Best-effort — failing column-option query does not abort the render
# ─────────────────────────────────────────────────────────────────────────────

_BEST_EFFORT_YAML = """\
title: Best Effort Test
sources:
  db:
    type: duckdb
    path: ":memory:"
source: db
queries:
  sales:
    sql: SELECT 1 AS x, 1 AS y
charts:
  c:
    query: sales
    type: bar
    x: x
    y: y
variables:
  region:
    input: select
    options:
      column: regions.name
rows:
  - c
"""


class TestBestEffortColumnOptionDegradation:
    def test_failing_column_option_query_gives_empty_options_not_error(self) -> None:
        """When the column-option query fails, the dropdown renders empty, not an error."""
        from dbt_charts.core.render.variables_resolve import resolve_query_options

        result = compile_with_board_sources(_BEST_EFFORT_YAML)
        assert result.success

        # Executor that fails the column-option query
        registry = Mock()
        registry.execute.return_value = QueryResult(
            data=None, error=QueryError("table not found")
        )
        executor = Executor(board=result.board, adapter_registry=registry)

        # Store the error on the executor as the named-query pipeline would
        executor._query_errors["_var_options_region"] = RuntimeError("table not found")

        # resolve_query_options must return [] (not raise) when query failed
        options = resolve_query_options("_var_options_region", executor, variables={})
        assert options == []

    def test_render_does_not_abort_when_column_option_query_fails(self) -> None:
        """Full render completes even when a column-option query raises an error."""
        from dbt_charts.core.render.renderer import render

        result = compile_with_board_sources(_BEST_EFFORT_YAML)
        assert result.success

        # Adapter that succeeds for 'sales' but fails for the column-option query
        call_count = {"n": 0}

        def adapter_execute(query, variables=None, **kw):
            call_count["n"] += 1
            sql = getattr(query, "sql", "")
            if "regions" in sql:
                return QueryResult(
                    data=None, error=QueryError("no such table: regions")
                )
            return QueryResult(data=[{"x": 1, "y": 2}])

        registry = Mock()
        registry.execute.side_effect = adapter_execute

        executor = Executor(board=result.board, adapter_registry=registry)

        # Must not raise — column-option failure is best-effort
        svg = render(result.board, executor, format="svg")
        assert svg  # some output was produced
