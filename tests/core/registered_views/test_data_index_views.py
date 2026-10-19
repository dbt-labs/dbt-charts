"""Tests for the built-in data index view templates.

Covers source-index, schema-index, and table-index templates — expanded via
``expand_registered_view`` and validated through the normal board pipeline.

Tests drive through ``expand_registered_view`` with monkeypatched query results
where needed (table-index pre-template columns query). No fixtures shipped in
the package; all query results are constructed inline.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from dbt_charts.core.compile.template.query_result import QueryResult
from dbt_charts.core.registered_views.expander import expand_registered_view
from dbt_charts.core.registered_views.loader import load_builtin_registry
from dbt_charts.core.registered_views.models import RegisteredView
from dbt_charts.core.registered_views.router import RouteRouter


def _router() -> RouteRouter:
    return RouteRouter(load_builtin_registry())


def _data_views() -> list[RegisteredView]:
    return [v for v in load_builtin_registry() if v.name.startswith("data_")]


def _col_rows(*cols: tuple[str, str]) -> QueryResult:
    return QueryResult("q", rows=[{"name": n, "actual_type": t} for n, t in cols])


def _schema_rows(*names: str) -> QueryResult:
    return QueryResult("q", rows=[{"name": n} for n in names])


# ---------------------------------------------------------------------------
# Registry declarations
# ---------------------------------------------------------------------------


class TestDataViewDefinitions:
    def test_all_data_views_are_declared(self) -> None:
        names = {v.name for v in _data_views()}
        assert {
            "data_source",
            "data_schema",
            "data_table",
            "data_table_detail",
        } <= names

    def test_table_has_columns_registry_query(self) -> None:
        """data_table declares a pre-template 'columns' registry query."""
        views = {v.name: v for v in _data_views()}
        view = views["data_table"]
        assert view.queries is not None
        assert "columns" in view.queries

    def test_source_has_no_registry_queries(self) -> None:
        """data_source needs no pre-template queries — schema list is a board query."""
        views = {v.name: v for v in _data_views()}
        assert views["data_source"].queries is None

    def test_schema_has_no_registry_queries(self) -> None:
        """data_schema needs no pre-template queries — table list is a board query."""
        views = {v.name: v for v in _data_views()}
        assert views["data_schema"].queries is None


# ---------------------------------------------------------------------------
# Route matching
# ---------------------------------------------------------------------------


class TestDataRouteMatching:
    def test_source_route_matches(self) -> None:
        match = _router().match("/data/snowflake/")
        assert match is not None
        assert match.view.name == "data_source"
        assert match.path_params == {"source": "snowflake"}

    def test_schema_route_matches(self) -> None:
        match = _router().match("/data/snowflake/analytics/")
        assert match is not None
        assert match.view.name == "data_schema"
        assert match.path_params == {"source": "snowflake", "schema": "analytics"}

    def test_table_route_matches(self) -> None:
        match = _router().match("/data/snowflake/analytics/orders/")
        assert match is not None
        assert match.view.name == "data_table"
        assert match.path_params == {
            "source": "snowflake",
            "schema": "analytics",
            "table": "orders",
        }


# ---------------------------------------------------------------------------
# source-index template
# ---------------------------------------------------------------------------


class TestSourceIndexTemplate:
    def test_expands_to_authored_board(self) -> None:
        from dbt_charts.core.compile.models.board.authored import AuthoredBoard

        match = _router().match("/data/snowflake/")
        assert match is not None
        board = expand_registered_view(match, query_results={})
        assert isinstance(board, AuthoredBoard)

    def test_title_contains_source(self) -> None:
        match = _router().match("/data/my_warehouse/")
        assert match is not None
        board = expand_registered_view(match, query_results={})
        assert board.title is not None
        assert "my_warehouse" in board.title

    def test_passes_validator(self) -> None:
        from dbt_charts.core.compile.validate.dispatch import validate_board

        match = _router().match("/data/snowflake/")
        assert match is not None
        board = expand_registered_view(match, query_results={})
        errors = validate_board(board)
        assert errors == [], f"Validation errors: {errors}"

    def test_has_schema_list_table_chart(self) -> None:
        """Source page contains a table chart that lists schemas."""
        match = _router().match("/data/snowflake/")
        assert match is not None
        board = expand_registered_view(match, query_results={})
        # Must have at least one chart of type table
        assert board.charts is not None
        chart_types = {getattr(c, "type", None) for c in board.charts.values()}
        assert "table" in chart_types, (
            f"Expected a table chart, got types: {chart_types}"
        )

    def test_schema_list_chart_has_child_link(self) -> None:
        """Schema list links each row to /data/<source>/<schema>/."""
        match = _router().match("/data/snowflake/")
        assert match is not None
        board = expand_registered_view(match, query_results={})
        assert board.charts is not None
        # Find the table chart that uses the schemas query
        table_charts = [
            c for c in board.charts.values() if getattr(c, "type", None) == "table"
        ]
        assert table_charts, "No table chart found in source-index board"
        # The link must reference the name column and build a child URL
        chart = table_charts[0]
        link = getattr(chart, "link", None)
        assert link is not None, (
            "Schema list table must have a link for child navigation"
        )
        assert "{{ name }}" in link, (
            f"Link must use {{{{ name }}}} to build child URL, got: {link!r}"
        )
        assert "/data/snowflake/" in link, (
            f"Link must include source path, got: {link!r}"
        )

    def test_has_schemas_board_query(self) -> None:
        """Source page has a board-level query that fetches schemas for this source."""
        match = _router().match("/data/snowflake/")
        assert match is not None
        board = expand_registered_view(match, query_results={})
        assert board.queries is not None
        # Should have a query that returns schemas for the source
        assert len(board.queries) >= 1


# ---------------------------------------------------------------------------
# schema-index template
# ---------------------------------------------------------------------------


class TestSchemaIndexTemplate:
    def test_expands_to_authored_board(self) -> None:
        from dbt_charts.core.compile.models.board.authored import AuthoredBoard

        match = _router().match("/data/snowflake/analytics/")
        assert match is not None
        board = expand_registered_view(match, query_results={})
        assert isinstance(board, AuthoredBoard)

    def test_title_contains_source_and_schema(self) -> None:
        match = _router().match("/data/snowflake/analytics/")
        assert match is not None
        board = expand_registered_view(match, query_results={})
        assert board.title is not None
        assert "snowflake" in board.title
        assert "analytics" in board.title

    def test_passes_validator(self) -> None:
        from dbt_charts.core.compile.validate.dispatch import validate_board

        match = _router().match("/data/snowflake/analytics/")
        assert match is not None
        board = expand_registered_view(match, query_results={})
        errors = validate_board(board)
        assert errors == [], f"Validation errors: {errors}"

    def test_has_table_list_table_chart(self) -> None:
        """Schema page contains a table chart that lists tables."""
        match = _router().match("/data/snowflake/analytics/")
        assert match is not None
        board = expand_registered_view(match, query_results={})
        assert board.charts is not None
        chart_types = {getattr(c, "type", None) for c in board.charts.values()}
        assert "table" in chart_types

    def test_table_list_chart_has_child_link(self) -> None:
        """Table list links each row to /data/<source>/<schema>/<table>/."""
        match = _router().match("/data/snowflake/analytics/")
        assert match is not None
        board = expand_registered_view(match, query_results={})
        assert board.charts is not None
        table_charts = [
            c for c in board.charts.values() if getattr(c, "type", None) == "table"
        ]
        assert table_charts
        chart = table_charts[0]
        link = getattr(chart, "link", None)
        assert link is not None, "Table list must have a link for child navigation"
        assert "{{ name }}" in link, (
            f"Link must use {{{{ name }}}} to build child URL, got: {link!r}"
        )
        assert "/data/snowflake/analytics/" in link


# ---------------------------------------------------------------------------
# table-index template
# ---------------------------------------------------------------------------


class TestTableIndexTemplate:
    def _match(self) -> object:
        match = _router().match("/data/snowflake/analytics/orders/")
        assert match is not None
        return match

    def _col_results(self) -> dict[str, QueryResult]:
        return {
            "columns": _col_rows(
                ("order_id", "BIGINT"),
                ("status", "VARCHAR"),
                ("created_at", "TIMESTAMP"),
            )
        }

    def test_expands_to_authored_board(self) -> None:
        from dbt_charts.core.compile.models.board.authored import AuthoredBoard

        match = self._match()
        board = expand_registered_view(match, query_results=self._col_results())
        assert isinstance(board, AuthoredBoard)

    def test_title_contains_table(self) -> None:
        match = self._match()
        board = expand_registered_view(match, query_results=self._col_results())
        assert board.title is not None
        assert "orders" in board.title

    def test_passes_validator(self) -> None:
        from dbt_charts.core.compile.validate.dispatch import validate_board

        match = self._match()
        board = expand_registered_view(match, query_results=self._col_results())
        errors = validate_board(board)
        assert errors == [], f"Validation errors: {errors}"

    def test_has_sql_data_query(self) -> None:
        """Table page has a board-level SQL query that selects rows from the table."""
        match = self._match()
        board = expand_registered_view(match, query_results=self._col_results())
        assert board.queries is not None
        # Should have at least one SQL query for the rows
        from dbt_charts.core.compile.models.query.authored import _BaseQueryFields

        sql_queries = [
            q
            for q in board.queries.values()
            if isinstance(q, _BaseQueryFields)
            and (q.type is None or q.type == "sql")
            and q.sql is not None
        ]
        assert sql_queries, (
            f"Expected at least one SQL query, got: {list(board.queries.keys())}"
        )

    def test_has_rows_table_chart(self) -> None:
        """Table page contains a table chart rendering the row data."""
        match = self._match()
        board = expand_registered_view(match, query_results=self._col_results())
        assert board.charts is not None
        chart_types = {getattr(c, "type", None) for c in board.charts.values()}
        assert "table" in chart_types

    def test_per_column_variables_generated(self) -> None:
        """Table index generates one variable per filterable column (id == column name)."""
        match = self._match()
        board = expand_registered_view(match, query_results=self._col_results())
        assert board.variables is not None
        # status (VARCHAR) and created_at (TIMESTAMP) are filterable defaults;
        # order_id (BIGINT) is excluded from default filter selection.
        assert "status" in board.variables, (
            f"status variable missing: {list(board.variables.keys())}"
        )
        assert "created_at" in board.variables, "created_at variable missing"

    def test_variable_ids_match_column_names_exactly(self) -> None:
        """Variable ids must exactly match source column names (no mangling)."""
        match = self._match()
        board = expand_registered_view(match, query_results=self._col_results())
        assert board.variables is not None
        # Every variable key must be a valid column name from the query result
        col_names = {"order_id", "status", "created_at"}
        for var_id in board.variables:
            assert var_id in col_names, (
                f"Variable id {var_id!r} is not a column name from the columns query"
            )

    def test_rows_table_has_automatic_link_enabled(self) -> None:
        """After consolidation, the expanded board has links.auto=True so the
        resolver synthesizes the detail link at render time. The template no
        longer hand-builds the link: string on the chart."""
        match = self._match()
        board = expand_registered_view(match, query_results=self._col_results())
        assert board.links.auto is True, (
            "Expanded table-index board must have links.auto=True so the resolver fires"
        )
        assert board.charts is not None
        table_charts = [
            c for c in board.charts.values() if getattr(c, "type", None) == "table"
        ]
        assert table_charts, "No table chart found"
        chart = table_charts[0]
        # After consolidation: chart has no explicit link: — resolver injects it.
        link = getattr(chart, "link", None)
        assert link is None, (
            f"After consolidation the chart must have no explicit link:, got: {link!r}"
        )

    def test_generated_filters_applied_to_sql(self) -> None:
        """Variables emitted for filterable columns are wired into the data query.

        When a variable is set, render_parameterized must produce a WHERE clause
        that constrains that column.  When unset (None), the clause is 1=1.
        """
        from dbt_charts.core.compile.models.query.authored import _BaseQueryFields
        from dbt_charts.core.compile.template.parameterized import render_parameterized

        match = self._match()
        board = expand_registered_view(match, query_results=self._col_results())
        assert board.queries is not None

        sql_queries = [
            q
            for q in board.queries.values()
            if isinstance(q, _BaseQueryFields) and q.sql is not None
        ]
        assert sql_queries, "Expected at least one SQL query"
        sql_template = sql_queries[0].sql
        assert sql_template is not None

        # Filter set: status = "closed" → must produce a WHERE constraint
        r_set = render_parameterized(
            sql_template,
            {"status": "closed", "created_at": None},
            profile_type="postgres",
        )
        assert "status" in r_set.sql and "1=1" not in r_set.sql.split("status")[
            0
        ].replace("WHERE 1=1", ""), f"Expected status filter in SQL, got: {r_set.sql!r}"
        assert r_set.params == ["closed"], f"Expected ['closed'], got: {r_set.params!r}"

        # Filter unset: status = None → must produce 1=1 (pass-through)
        r_unset = render_parameterized(
            sql_template,
            {"status": None, "created_at": None},
            profile_type="postgres",
        )
        assert r_unset.params == [], (
            f"Expected no params when unset, got: {r_unset.params!r}"
        )

    def test_expands_with_no_filterable_columns(self) -> None:
        """Table-index expands cleanly when all columns are numeric (no generated variables)."""
        from dbt_charts.core.compile.validate.dispatch import validate_board

        match = self._match()
        numeric_results = {
            "columns": _col_rows(("amount", "FLOAT"), ("count", "BIGINT"))
        }
        board = expand_registered_view(match, query_results=numeric_results)
        errors = validate_board(board)
        assert errors == [], f"Validation errors on numeric-only columns: {errors}"
        # No variables generated for numeric columns
        variables = board.variables or {}
        assert "amount" not in variables
        assert "count" not in variables

    def test_expands_with_empty_columns_result(self) -> None:
        """Table-index expands cleanly when the pre-template columns query returns no rows."""
        from dbt_charts.core.compile.validate.dispatch import validate_board

        match = self._match()
        empty_results = {"columns": QueryResult("q", rows=[])}
        board = expand_registered_view(match, query_results=empty_results)
        errors = validate_board(board)
        assert errors == [], f"Validation errors on empty columns result: {errors}"

    def test_non_identifier_column_names_are_skipped_not_raised(self) -> None:
        """table-index skips columns with non-identifier names (spaces, dashes, leading
        digits) instead of raising InvalidColumnNameError — common in real warehouses.
        """
        from dbt_charts.core.compile.validate.dispatch import validate_board

        match = self._match()
        mixed_results = {
            "columns": _col_rows(
                ("status", "VARCHAR"),  # valid — must get a variable
                ("first name", "VARCHAR"),  # space — must be skipped
                ("order-date", "TIMESTAMP"),  # dash — must be skipped
                ("2nd_col", "VARCHAR"),  # leading digit — must be skipped
            )
        }
        board = expand_registered_view(match, query_results=mixed_results)
        errors = validate_board(board)
        assert errors == [], f"Validation errors: {errors}"
        variables = board.variables or {}
        assert "status" in variables, "valid column must get a variable"
        assert "first name" not in variables, "space column must be skipped"
        assert "order-date" not in variables, "dash column must be skipped"
        assert "2nd_col" not in variables, "leading-digit column must be skipped"


# ---------------------------------------------------------------------------
# registry.yaml: data_source and data_schema have no pre-template queries
# (consistent with the board-query design per the decision doc)
# ---------------------------------------------------------------------------


class TestDataRegistryQueryDeclarations:
    def test_data_source_no_registry_query(self) -> None:
        """data_source has no pre-template registry query (schemas fetched in board)."""
        views = {v.name: v for v in _data_views()}
        assert (
            views["data_source"].queries is None or views["data_source"].queries == {}
        )

    def test_data_schema_no_registry_query(self) -> None:
        """data_schema has no pre-template registry query (tables fetched in board)."""
        views = {v.name: v for v in _data_views()}
        assert (
            views["data_schema"].queries is None or views["data_schema"].queries == {}
        )

    def test_data_table_has_columns_query(self) -> None:
        """data_table has exactly one pre-template query named 'columns'."""
        views = {v.name: v for v in _data_views()}
        view = views["data_table"]
        assert view.queries is not None
        assert list(view.queries.keys()) == ["columns"]


# ---------------------------------------------------------------------------
# Real server: dotted and non-ASCII table names must stay browsable
#
# Regression coverage for the round-3 review finding — an ASCII-only
# identifier allowlist 500'd table pages the /data/ index itself links to.
# The denylist that replaced it must not reject legal quoted-identifier
# characters (dots, non-ASCII letters).
# ---------------------------------------------------------------------------


@pytest.fixture
def duckdb_project_with_dotted_and_nonascii_tables(tmp_path: Path) -> Path:
    import duckdb

    db = tmp_path / "wh.duckdb"
    conn = duckdb.connect(str(db))
    conn.execute("CREATE SCHEMA analytics")
    conn.execute('CREATE TABLE analytics."orders.v2" (id INTEGER, status VARCHAR)')
    conn.execute('CREATE TABLE analytics."ventas_señor" (id INTEGER, monto INTEGER)')
    conn.execute(
        """INSERT INTO analytics."orders.v2" VALUES (1, 'open'), (2, 'closed')"""
    )
    conn.execute("""INSERT INTO analytics."ventas_señor" VALUES (1, 100)""")
    conn.close()
    (tmp_path / "dbt_charts.yml").write_text(
        f"sources:\n  wh:\n    type: duckdb\n    path: '{db}'\n"
    )
    (tmp_path / "charts").mkdir()
    return tmp_path


class TestTableIndexRealServerDottedAndNonAsciiNames:
    def test_dotted_table_name_returns_200_with_rows(
        self, duckdb_project_with_dotted_and_nonascii_tables: Path
    ) -> None:
        from fastapi.testclient import TestClient

        from dbt_charts.cli.filesystem_project import FilesystemProject
        from dbt_charts.core.serve.server import create_server

        app = create_server(
            FilesystemProject(duckdb_project_with_dotted_and_nonascii_tables)
        )
        with TestClient(app, raise_server_exceptions=False) as client:
            response = client.get("/data/wh/analytics/orders.v2/")
        assert response.status_code == 200
        assert "status" in response.text

    def test_non_ascii_table_name_returns_200_with_rows(
        self, duckdb_project_with_dotted_and_nonascii_tables: Path
    ) -> None:
        from fastapi.testclient import TestClient

        from dbt_charts.cli.filesystem_project import FilesystemProject
        from dbt_charts.core.serve.server import create_server

        app = create_server(
            FilesystemProject(duckdb_project_with_dotted_and_nonascii_tables)
        )
        with TestClient(app, raise_server_exceptions=False) as client:
            response = client.get("/data/wh/analytics/ventas_señor/")
        assert response.status_code == 200
        assert "ventas_señor" in response.text
        assert "id" in response.text
