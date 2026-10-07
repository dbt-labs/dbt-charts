"""Tests for the built-in data table-detail view template.

Covers the /data/<source>/<schema>/<table>/detail/ template — the single-row
page where the row is selected by key-column query params.

Tests drive through ``expand_registered_view`` with monkeypatched query results.
No fixtures shipped in the package; all query results are constructed inline.

Key invariants verified:
- Template expands to a valid AuthoredBoard that passes the validator.
- The data query uses sql_identifier (injection-safe schema/table names).
- The WHERE predicate uses {{ filter() }} for parameter binding, never string
  interpolation of key column values.
- Single-key and composite-key paths both produce a LIMIT 2 ambiguity probe.
- Identity keys are restricted to URL-round-trippable types (int/decimal/str).
- Non-identifier column names are skipped, not raised.
- table-index now links each row to the detail page via ?<col>=<value>.
"""

from __future__ import annotations

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


# ---------------------------------------------------------------------------
# Registry declarations for data_table_detail
# ---------------------------------------------------------------------------


class TestDataDetailViewDefinition:
    def test_data_table_detail_has_columns_registry_query(self) -> None:
        """data_table_detail declares a pre-template 'columns' registry query."""
        views = {v.name: v for v in _data_views()}
        view = views["data_table_detail"]
        assert view.queries is not None
        assert "columns" in view.queries

    def test_data_table_detail_route(self) -> None:
        """data_table_detail route is /data/<source>/<schema>/<table>/detail/."""
        views = {v.name: v for v in _data_views()}
        assert (
            views["data_table_detail"].route
            == "/data/<source>/<schema>/<table>/detail/"
        )

    def test_detail_route_matches(self) -> None:
        match = _router().match("/data/snowflake/analytics/orders/detail/")
        assert match is not None
        assert match.view.name == "data_table_detail"
        assert match.path_params == {
            "source": "snowflake",
            "schema": "analytics",
            "table": "orders",
        }

    def test_table_route_does_not_match_detail(self) -> None:
        """The table route does NOT match the detail URL — distinct routes."""
        match = _router().match("/data/snowflake/analytics/orders/detail/")
        assert match is not None
        assert match.view.name != "data_table"


# ---------------------------------------------------------------------------
# table-detail template expansion
# ---------------------------------------------------------------------------


class TestTableDetailTemplate:
    def _match(self) -> object:
        match = _router().match("/data/snowflake/analytics/orders/detail/")
        assert match is not None
        return match

    def _col_results(self) -> dict[str, QueryResult]:
        return {
            "columns": _col_rows(
                ("id", "BIGINT"),
                ("status", "VARCHAR"),
                ("created_at", "TIMESTAMP"),
            )
        }

    def test_passes_validator(self) -> None:
        from dbt_charts.core.compile.validate.dispatch import validate_board

        board = expand_registered_view(self._match(), query_results=self._col_results())
        errors = validate_board(board)
        assert errors == [], f"Validation errors: {errors}"

    def test_title_contains_path_context(self) -> None:
        """Detail page title includes the table name."""
        board = expand_registered_view(self._match(), query_results=self._col_results())
        assert board.title is not None
        assert "orders" in board.title

    def test_has_sql_data_query(self) -> None:
        """Detail page has a board-level SQL query that selects from the table."""
        from dbt_charts.core.compile.models.query.authored import _BaseQueryFields

        board = expand_registered_view(self._match(), query_results=self._col_results())
        assert board.queries is not None
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

    def test_sql_uses_sql_identifier_for_relation(self) -> None:
        """SQL query uses sql_identifier() quoting for schema and table, not raw path params."""
        from dbt_charts.core.compile.models.query.authored import _BaseQueryFields

        board = expand_registered_view(self._match(), query_results=self._col_results())
        assert board.queries is not None
        sql_queries = [
            q
            for q in board.queries.values()
            if isinstance(q, _BaseQueryFields) and q.sql is not None
        ]
        assert sql_queries
        sql = sql_queries[0].sql
        assert sql is not None
        # Table and schema appear double-quoted (sql_identifier output)
        assert '"analytics"' in sql, f"Expected double-quoted schema, got: {sql!r}"
        assert '"orders"' in sql, f"Expected double-quoted table, got: {sql!r}"

    def test_sql_identifier_handles_injection_payload(self) -> None:
        """A double-quote in schema/table name is rejected, not injected into SQL."""
        from dbt_charts.core.registered_views.expander import ExpansionError

        match = _router().match('/data/snowflake/bad"schema/orders/detail/')
        # The route param regex is `[^\s/]+`, which admits a double quote — so
        # this payload really does reach the expander. Asserted rather than
        # skipped: a `return` here would silently pass if routing ever tightened.
        assert match is not None
        with pytest.raises(ExpansionError, match="not a valid SQL identifier"):
            expand_registered_view(
                match,
                query_results={
                    "columns": _col_rows(("id", "BIGINT"), ("status", "VARCHAR"))
                },
            )

    def test_where_clause_uses_filter_for_parameter_binding(self) -> None:
        """The WHERE predicate uses {{ filter() }} — never bare string interpolation.

        Verifies that key-column values flow through parameter binding so the
        executed SQL has placeholders ($1, ?, %s, etc.) not raw injected values.
        """
        from dbt_charts.core.compile.models.query.authored import _BaseQueryFields
        from dbt_charts.core.compile.template.parameterized import render_parameterized

        board = expand_registered_view(self._match(), query_results=self._col_results())
        assert board.queries is not None
        sql_queries = [
            q
            for q in board.queries.values()
            if isinstance(q, _BaseQueryFields) and q.sql is not None
        ]
        assert sql_queries
        sql_template = sql_queries[0].sql
        assert sql_template is not None

        # With status set: must produce a parameterized WHERE constraint
        result_set = render_parameterized(
            sql_template,
            {"status": "closed", "id": None, "created_at": None},
            profile_type="postgres",
        )
        assert "status" in result_set.sql
        assert result_set.params == ["closed"], (
            f"Expected ['closed'] params, got: {result_set.params!r}"
        )

        # With status unset: must produce 1=1 (pass-through, no param)
        result_unset = render_parameterized(
            sql_template,
            {"status": None, "id": None, "created_at": None},
            profile_type="postgres",
        )
        assert result_unset.params == [], (
            f"Expected no params when unset, got: {result_unset.params!r}"
        )

    def test_limit_2_probe_not_limit_1(self) -> None:
        """The detail SQL uses LIMIT 2, never LIMIT 1.

        LIMIT 1 would silently pick the first of many matches. LIMIT 2 is the
        minimal probe that lets the rendered table expose an empty (zero) or
        ambiguous (two) result instead. This pins the SQL *shape*; it does not
        claim the query raises on a non-unique key (it does not — the table just
        shows what it gets).
        """
        from dbt_charts.core.compile.models.query.authored import _BaseQueryFields

        board = expand_registered_view(self._match(), query_results=self._col_results())
        assert board.queries is not None
        sql_queries = [
            q
            for q in board.queries.values()
            if isinstance(q, _BaseQueryFields) and q.sql is not None
        ]
        assert sql_queries
        sql = sql_queries[0].sql
        assert sql is not None
        # Must have a LIMIT clause that allows exactly-one detection
        # (limit 2 is the minimal probe: zero → empty, one → ok, two → ambiguous)
        sql_upper = sql.upper()
        assert "LIMIT" in sql_upper, "Detail SQL must have a LIMIT clause"
        # Must NOT use LIMIT 1 alone — that silently picks the first row
        # instead of exposing an ambiguous state (non-negotiable #4)
        assert "LIMIT 1" not in sql_upper.replace("LIMIT 10", ""), (
            "LIMIT 1 silently picks the first row — use LIMIT 2 to detect ambiguity"
        )

    def test_per_column_variables_generated_for_key_columns(self) -> None:
        """Detail key set: numeric id + string bind; the timestamp is excluded.

        Unlike the browse-filter set, the key set MUST include the numeric
        primary key — a row keyed only by ``id BIGINT`` can't be selected
        otherwise. But a TIMESTAMP can't round-trip through a URL ``=`` param
        (sub-second precision, formatting), so it must NOT be an identity key.
        """
        board = expand_registered_view(self._match(), query_results=self._col_results())
        variables = board.variables or {}
        assert "id" in variables, f"numeric id key missing: {list(variables.keys())}"
        assert "status" in variables
        assert "created_at" not in variables, (
            "TIMESTAMP can't round-trip as a URL equality key — must be excluded"
        )

    def test_float_and_fractional_decimal_excluded_from_key(self) -> None:
        """Inexact numerics (FLOAT, DECIMAL with scale) can't be equality keys.

        NUMBER(38,0) is an exact integer id and stays; price NUMBER(10,2) and a
        DOUBLE measure would silently select zero rows if compared with ``=``.
        """
        cols = {
            "columns": _col_rows(
                ("account_id", "NUMBER(38,0)"),  # exact integer id — keep
                ("price", "NUMBER(10,2)"),  # money — drop
                ("ratio", "DOUBLE"),  # float — drop
            )
        }
        board = expand_registered_view(self._match(), query_results=cols)
        variables = board.variables or {}
        assert "account_id" in variables
        assert "price" not in variables
        assert "ratio" not in variables

    def test_non_identifier_column_names_skipped(self) -> None:
        """Non-identifier columns (spaces, dashes) are skipped, not raised."""
        from dbt_charts.core.compile.validate.dispatch import validate_board

        mixed_cols = {
            "columns": _col_rows(
                ("id", "BIGINT"),
                ("status", "VARCHAR"),  # valid
                ("first name", "VARCHAR"),  # space — skip
                ("order-date", "TIMESTAMP"),  # dash — skip
            )
        }
        board = expand_registered_view(self._match(), query_results=mixed_cols)
        errors = validate_board(board)
        assert errors == [], f"Validation errors: {errors}"
        variables = board.variables or {}
        assert "status" in variables
        assert "first name" not in variables
        assert "order-date" not in variables

    def test_complex_type_columns_excluded_from_key(self) -> None:
        """ARRAY/JSON columns can't ride in a URL param, so they aren't key vars."""
        complex_cols = {
            "columns": _col_rows(
                ("id", "BIGINT"),
                ("tags", "ARRAY<VARCHAR>"),
                ("payload", "JSON"),
            )
        }
        board = expand_registered_view(self._match(), query_results=complex_cols)
        variables = board.variables or {}
        assert "id" in variables
        assert "tags" not in variables
        assert "payload" not in variables

    def test_expands_with_empty_columns_result(self) -> None:
        """table-detail expands cleanly when the pre-template columns query returns no rows."""
        from dbt_charts.core.compile.validate.dispatch import validate_board

        empty_results: dict[str, QueryResult] = {"columns": QueryResult("q", rows=[])}
        board = expand_registered_view(self._match(), query_results=empty_results)
        errors = validate_board(board)
        assert errors == [], f"Validation errors on empty columns result: {errors}"


# ---------------------------------------------------------------------------
# Composite key (multiple query params)
# ---------------------------------------------------------------------------


class TestDetailCompositeKey:
    """The detail page handles composite keys as multiple column variables."""

    def _match(self) -> object:
        match = _router().match("/data/dw/sales/line_items/detail/")
        assert match is not None
        return match

    def _col_results(self) -> dict[str, QueryResult]:
        return {
            "columns": _col_rows(
                ("order_id", "BIGINT"),
                ("line_no", "BIGINT"),
                ("sku", "VARCHAR"),
            )
        }

    def test_composite_key_passes_validator(self) -> None:
        from dbt_charts.core.compile.validate.dispatch import validate_board

        board = expand_registered_view(self._match(), query_results=self._col_results())
        errors = validate_board(board)
        assert errors == [], f"Validation errors: {errors}"

    def test_composite_key_columns_all_get_variables(self) -> None:
        """Every composite-key column binds — including the numeric id columns.

        A ``(order_id BIGINT, line_no BIGINT)`` PK is the natural composite key;
        if the numeric columns were dropped the detail link would carry nothing
        and the page could never select one row.
        """
        board = expand_registered_view(self._match(), query_results=self._col_results())
        variables = board.variables or {}
        for col in ("order_id", "line_no", "sku"):
            assert col in variables, f"{col} variable missing: {list(variables.keys())}"


# ---------------------------------------------------------------------------
# table-index template: link synthesis moved to auto_link resolver
# ---------------------------------------------------------------------------


class TestTableIndexDetailLink:
    """After consolidation, table-index.yaml sets auto_link=True on the board
    and carries NO explicit link: on the chart. The auto_link resolver injects
    the detail-page link at render time. Key-selection tests live in
    test_auto_link.TestPlanLinkKeys and test_auto_link.TestResolveAutoLink.
    """

    def _match(self) -> object:
        match = _router().match("/data/snowflake/analytics/orders/")
        assert match is not None
        return match

    def _col_results(self) -> dict[str, QueryResult]:
        return {
            "columns": _col_rows(
                ("id", "BIGINT"),
                ("status", "VARCHAR"),
            )
        }

    def test_expanded_board_has_auto_link_true(self) -> None:
        """Expanded table-index board must have auto_link=True so the resolver fires."""
        board = expand_registered_view(self._match(), query_results=self._col_results())
        assert getattr(board, "auto_link", False) is True, (
            f"Expected auto_link=True on expanded board, got: {getattr(board, 'auto_link', 'MISSING')!r}"
        )

    def test_expanded_board_chart_has_no_explicit_link(self) -> None:
        """After consolidation the template must not set link: on the chart.
        The auto_link resolver injects it at render time using plan_link_keys.
        """
        board = expand_registered_view(self._match(), query_results=self._col_results())
        assert board.charts is not None
        chart = board.charts.get("row_data")
        assert chart is not None
        link = getattr(chart, "link", None)
        assert link is None, (
            "After consolidation the template must not set link: on the chart — "
            f"the auto_link resolver injects it at render time. Got: {link!r}"
        )
