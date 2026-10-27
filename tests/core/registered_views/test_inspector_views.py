"""Tests for the inspector registered views.

Verifies that all four inspector route/template pairs
(source / schema / table / column) go through the registered-view
expansion pipeline and produce valid authored boards.

The old /inspect/ family (query-param model profiling, _substitute_template_vars /
render_inspect_dashboard) is a separate feature and is NOT retired here.
Retiring /inspect/ is deferred to a follow-up task.
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import pytest

from dbt_charts.core.column_types import literal_schema
from dbt_charts.core.execute.adapters.base import QueryResult
from dbt_charts.core.registered_views.expander import expand_registered_view
from dbt_charts.core.registered_views.loader import load_builtin_registry
from dbt_charts.core.registered_views.models import RegisteredView
from dbt_charts.core.registered_views.router import RouteRouter


@pytest.fixture
def duckdb_project(tmp_path: Path) -> Path:
    """A minimal project with a real DuckDB source and a seeded table.

    No super_schema cache is baked, so the `type: schema` query returns bare
    ``actual_type`` — this doubles as the graceful cache-less degradation path.
    """
    import duckdb

    db = tmp_path / "wh.duckdb"
    conn = duckdb.connect(str(db))
    conn.execute(
        "CREATE TABLE orders ("
        "order_id INTEGER, status VARCHAR, amount INTEGER, "
        "created_at TIMESTAMP, pickup_time TIME)"
    )
    conn.execute(
        "INSERT INTO orders VALUES "
        "(1, 'open', 10, TIMESTAMP '2024-01-02 09:00', TIME '09:00'), "
        "(2, 'closed', 20, TIMESTAMP '2024-02-03 10:00', TIME '10:00'), "
        "(3, 'open', 30, TIMESTAMP '2024-02-04 11:00', TIME '11:00')"
    )
    conn.close()
    (tmp_path / "dbt_charts.yml").write_text(
        f"sources:\n  wh:\n    type: duckdb\n    path: '{db}'\n"
    )
    (tmp_path / "charts").mkdir()
    return tmp_path


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _router() -> RouteRouter:
    return RouteRouter(load_builtin_registry())


def _inspector_views() -> list[RegisteredView]:
    """Return only the inspector-family views from the built-in registry."""
    return [v for v in load_builtin_registry() if v.name.startswith("inspector_")]


# ---------------------------------------------------------------------------
# Route matching: all four inspector routes must be declared and matchable
# ---------------------------------------------------------------------------


class TestInspectorRouteMatching:
    def test_source_route_matches(self) -> None:
        router = _router()
        match = router.match("/inspector/snowflake/")
        assert match is not None
        assert match.view.name == "inspector_source"
        assert match.path_params == {"source": "snowflake"}

    def test_schema_route_matches(self) -> None:
        router = _router()
        match = router.match("/inspector/snowflake/analytics/")
        assert match is not None
        assert match.view.name == "inspector_schema"
        assert match.path_params == {"source": "snowflake", "schema": "analytics"}

    def test_table_route_matches(self) -> None:
        router = _router()
        match = router.match("/inspector/snowflake/analytics/orders/")
        assert match is not None
        assert match.view.name == "inspector_table"
        assert match.path_params == {
            "source": "snowflake",
            "schema": "analytics",
            "table": "orders",
        }

    def test_column_route_matches(self) -> None:
        router = _router()
        match = router.match("/inspector/snowflake/analytics/orders/status/")
        assert match is not None
        assert match.view.name == "inspector_column"
        assert match.path_params == {
            "source": "snowflake",
            "schema": "analytics",
            "table": "orders",
            "column": "status",
        }

    def test_data_routes_not_matched_by_inspector(self) -> None:
        router = _router()
        # Inspector routes must not collide with data routes
        match = router.match("/data/snowflake/analytics/orders/")
        assert match is not None
        assert match.view.name == "data_table"

    def test_inspector_routes_not_matched_by_data(self) -> None:
        router = _router()
        match = router.match("/inspector/snowflake/")
        assert match is not None
        assert match.view.name == "inspector_source"
        # Must not match data_source route
        assert match.view.route != "/data/<source>/"


# ---------------------------------------------------------------------------
# Inspector views: correct target scopes and template paths
# ---------------------------------------------------------------------------


class TestInspectorViewDefinitions:
    def test_all_four_inspector_views_are_declared(self) -> None:
        inspector_views = _inspector_views()
        names = {v.name for v in inspector_views}
        assert names == {
            "inspector_source",
            "inspector_schema",
            "inspector_table",
            "inspector_column",
        }

    def test_inspector_table_has_columns_pre_template_query(self) -> None:
        # The table page transposes the column profiles into a describe-style
        # overview in the view layer, so it declares a `columns` pre-template
        # schema query whose result the template pivots.
        views = {v.name: v for v in _inspector_views()}
        assert views["inspector_table"].queries is not None
        assert "columns" in views["inspector_table"].queries

    def test_inspector_column_has_col_pre_template_query(self) -> None:
        # The column page branches its layout on the column's type in the view
        # layer, so it declares a `col` pre-template schema query whose result is
        # available to the template's Jinja conditions.
        views = {v.name: v for v in _inspector_views()}
        assert views["inspector_column"].queries is not None
        assert "col" in views["inspector_column"].queries


# ---------------------------------------------------------------------------
# Template expansion: all four inspector templates expand to valid AuthoredBoard
# ---------------------------------------------------------------------------


class TestInspectorTemplateExpansion:
    """Inspector templates expand correctly through the registered-view pipeline."""

    def test_source_template_title_contains_source(self) -> None:
        router = _router()
        match = router.match("/inspector/my_warehouse/")
        assert match is not None
        board = expand_registered_view(match, query_results={})
        assert board.title is not None
        assert "my_warehouse" in board.title

    def test_schema_template_title_contains_schema(self) -> None:
        router = _router()
        match = router.match("/inspector/snowflake/analytics/")
        assert match is not None
        board = expand_registered_view(match, query_results={})
        assert board.title is not None
        assert "analytics" in board.title

    def test_table_template_title_contains_table(self) -> None:
        router = _router()
        match = router.match("/inspector/snowflake/analytics/orders/")
        assert match is not None
        board = expand_registered_view(match, query_results={})
        assert board.title is not None
        assert "orders" in board.title

    def test_column_template_title_contains_column(self) -> None:
        router = _router()
        match = router.match("/inspector/snowflake/analytics/orders/status/")
        assert match is not None
        board = expand_registered_view(match, query_results={})
        assert board.title is not None
        assert "status" in board.title


# ---------------------------------------------------------------------------
# Generated boards pass the normal validator
# ---------------------------------------------------------------------------


class TestInspectorGeneratedBoardValidation:
    def test_source_board_passes_validator(self) -> None:
        from dbt_charts.core.compile.validate.dispatch import validate_board

        router = _router()
        match = router.match("/inspector/snowflake/")
        assert match is not None
        board = expand_registered_view(match, query_results={})
        errors = validate_board(board)
        assert errors == [], f"Validation errors: {errors}"

    def test_schema_board_passes_validator(self) -> None:
        from dbt_charts.core.compile.validate.dispatch import validate_board

        router = _router()
        match = router.match("/inspector/snowflake/analytics/")
        assert match is not None
        board = expand_registered_view(match, query_results={})
        errors = validate_board(board)
        assert errors == [], f"Validation errors: {errors}"

    def test_table_board_passes_validator(self) -> None:
        from dbt_charts.core.compile.validate.dispatch import validate_board

        router = _router()
        match = router.match("/inspector/snowflake/analytics/orders/")
        assert match is not None
        board = expand_registered_view(match, query_results={})
        errors = validate_board(board)
        assert errors == [], f"Validation errors: {errors}"

    def test_column_board_passes_validator(self) -> None:
        from dbt_charts.core.compile.validate.dispatch import validate_board

        router = _router()
        match = router.match("/inspector/snowflake/analytics/orders/status/")
        assert match is not None
        board = expand_registered_view(match, query_results={})
        errors = validate_board(board)
        assert errors == [], f"Validation errors: {errors}"


# ---------------------------------------------------------------------------
# Drill-down links: each non-leaf list page links its rows to the next level
# ---------------------------------------------------------------------------


class TestInspectorDrillDownLinks:
    """Non-leaf inspector list pages must link each row to the next level.

    Mirrors the /data/ browser's child-link behavior (see
    test_data_index_views.py) so /inspector/<source>/ rows navigate to
    /inspector/<source>/<schema>/, and so on down to the column profile.
    """

    def _list_link(self, path: str) -> str | None:
        match = _router().match(path)
        assert match is not None
        board = expand_registered_view(match, query_results={})
        assert board.charts is not None
        # `type` (the union discriminator) and `link` (_BaseChartFields) are
        # guaranteed on every chart model — access directly so a field rename
        # fails loudly instead of silently returning None.
        table_charts = [c for c in board.charts.values() if c.type == "table"]
        assert table_charts, f"No table chart found in {path} board"
        return table_charts[0].link

    def test_source_list_links_to_schema(self) -> None:
        link = self._list_link("/inspector/snowflake/")
        assert link is not None, "Schema list must link each row for drill-down"
        assert "{{ name }}" in link, (
            f"Link must build a child URL from name, got: {link!r}"
        )
        assert link == "/inspector/snowflake/{{ name }}/", f"Unexpected link: {link!r}"

    def test_schema_list_links_to_table(self) -> None:
        link = self._list_link("/inspector/snowflake/analytics/")
        assert link is not None, "Table list must link each row for drill-down"
        assert "{{ name }}" in link, (
            f"Link must build a child URL from name, got: {link!r}"
        )
        assert link == "/inspector/snowflake/analytics/{{ name }}/", (
            f"Unexpected link: {link!r}"
        )

    # The table → column drill-down is via the pivoted overview's column-header
    # links (data columns are table columns there, not rows), covered by
    # TestInspectorProfileRichness.test_table_page_column_headers_link_to_column_pages.

    def test_column_profile_is_leaf_with_no_link(self) -> None:
        # The column page is the leaf profile — no further drill-down.
        link = self._list_link("/inspector/snowflake/analytics/orders/status/")
        assert link is None, f"Leaf column page must not carry a link, got: {link!r}"


# ---------------------------------------------------------------------------
# Profiling richness: table/column pages render the super_schema profile the
# `type: schema` query returns, not just name + type.
# ---------------------------------------------------------------------------


class TestInspectorProfileRichness:
    """The browsable /inspector/ pages surface the full super_schema profile.

    The `type: schema` query already returns role/semantic_type/null_percentage/
    stats per column; these tests pin that the table and column templates render
    those fields (plus a sample-data / distribution query), not just name+type.
    """

    def _table_charts(self, path: str) -> list:
        match = _router().match(path)
        assert match is not None
        board = expand_registered_view(match, query_results={})
        assert board.charts is not None
        return list(board.charts.values())

    def _all_referenced_columns(self, charts: list) -> set[str]:
        cols: set[str] = set()
        for c in charts:
            columns = getattr(c.style, "columns", None) if c.style else None
            if columns:
                cols.update(columns.keys())
        return cols

    def test_table_page_transposes_column_profiles(self) -> None:
        """The table overview is a describe-style pivot: one table column per data
        column, and the characteristics (Type/Role/Null %/…) as rows."""
        from dbt_charts.core.compile.template.query_result import QueryResult

        match = _router().match("/inspector/snowflake/analytics/orders/")
        assert match is not None
        board = expand_registered_view(
            match,
            {
                "columns": QueryResult(
                    "q",
                    rows=[
                        {
                            "name": "amount",
                            "actual_type": "BIGINT",
                            "role": "measure",
                            "semantic_type": "count",
                            "null_percentage": 0.0,
                            "distinct_count": 5,
                        },
                    ],
                )
            },
        )
        assert board.queries is not None
        profile = board.queries["profile"]
        # Data column becomes a table column; the label column leads.
        assert profile.columns == ["Attribute", "amount"]
        # Characteristics are the rows, first cell is the characteristic label.
        row_labels = [row[0] for row in profile.values]
        assert row_labels == ["Type", "Role", "Semantic", "Null %", "Distinct"]
        # The Type row carries the column's actual_type value.
        type_row = profile.values[0]
        assert type_row == ["Type", "BIGINT"]

    def test_table_page_column_headers_link_to_column_pages(self) -> None:
        """Each data column's header links to its column inspector page (drill-down)."""
        from dbt_charts.core.compile.template.query_result import QueryResult

        match = _router().match("/inspector/snowflake/analytics/orders/")
        assert match is not None
        board = expand_registered_view(
            match,
            {
                "columns": QueryResult(
                    "q", rows=[{"name": "amount", "actual_type": "BIGINT"}]
                )
            },
        )
        assert board.charts is not None
        table = next(
            c
            for c in board.charts.values()
            if c.type == "table" and c.query == "profile"
        )
        header_link = table.style.columns["amount"].header_link
        assert header_link == "/inspector/snowflake/analytics/orders/amount/"

    def test_table_page_has_sample_data_query(self) -> None:
        match = _router().match("/inspector/snowflake/analytics/orders/")
        assert match is not None
        board = expand_registered_view(match, query_results={})
        assert board.queries is not None
        assert "sample_data" in board.queries, (
            f"Table inspector must query sample rows; queries={list(board.queries)}"
        )

    def test_column_page_renders_profile_fields(self) -> None:
        charts = self._table_charts("/inspector/snowflake/analytics/orders/status/")
        referenced = self._all_referenced_columns(charts)
        for field in ("role", "semantic_type"):
            assert field in referenced, (
                f"Column inspector must render the '{field}' field; "
                f"referenced columns were {sorted(referenced)}"
            )

    def test_column_page_branches_by_type(self) -> None:
        """The column page picks its charts from the column's type in the view layer.

        Numeric columns get a histogram distribution; non-numeric columns get a
        top-values (`categories`) breakdown — chosen from the `col` pre-template
        result, not baked statically.
        """
        from dbt_charts.core.compile.template.query_result import QueryResult

        match = _router().match("/inspector/wh/main/orders/amount/")
        assert match is not None

        numeric = expand_registered_view(
            match, {"col": QueryResult("q", rows=[{"actual_type": "BIGINT"}])}
        )
        assert numeric.queries is not None
        assert "histogram" in numeric.queries

        categorical = expand_registered_view(
            match, {"col": QueryResult("q", rows=[{"actual_type": "VARCHAR"}])}
        )
        assert categorical.queries is not None
        assert "categories" in categorical.queries
        assert "histogram" not in categorical.queries

        # INTERVAL contains 'INT' but must NOT be treated as numeric — it falls to
        # the generic top-values branch, not min/max/histogram.
        interval = expand_registered_view(
            match, {"col": QueryResult("q", rows=[{"actual_type": "INTERVAL"}])}
        )
        assert interval.queries is not None
        assert "categories" in interval.queries
        assert "histogram" not in interval.queries

        # TIMESTAMP/DATE get the date branch (monthly timeline)…
        temporal = expand_registered_view(
            match, {"col": QueryResult("q", rows=[{"actual_type": "TIMESTAMP"}])}
        )
        assert temporal.queries is not None
        assert "monthly" in temporal.queries

        # …but bare TIME (time-of-day) can't be ::DATE-cast, so it takes the
        # generic branch instead of the date SQL that would error the whole page.
        time_col = expand_registered_view(
            match, {"col": QueryResult("q", rows=[{"actual_type": "TIME"}])}
        )
        assert time_col.queries is not None
        assert "categories" in time_col.queries
        assert "monthly" not in time_col.queries

    def test_table_page_renders_against_real_data(self, duckdb_project: Path) -> None:
        """The enriched table page renders end-to-end against a real (cache-less)
        source: the overview inventory lists the real columns and the detail
        expanders render. No super_schema cache is present, so this also pins the
        graceful cache-less path (real content, not an error page)."""
        from fastapi.testclient import TestClient

        from dbt_charts.cli.filesystem_project import FilesystemProject
        from dbt_charts.core.serve.server import create_server

        app = create_server(FilesystemProject(duckdb_project))
        with TestClient(app, raise_server_exceptions=False) as client:
            response = client.get("/inspector/wh/main/orders/")
        assert response.status_code == 200
        # Real data columns become table columns in the describe overview.
        assert "order_id" in response.text
        assert "status" in response.text
        # The describe rows (characteristics) and the always-visible sample render.
        assert "Distinct" in response.text
        assert "Sample Data" in response.text

    def test_column_pages_render_per_type_against_real_data(
        self, duckdb_project: Path
    ) -> None:
        """Numeric and categorical column pages each render their type's charts."""
        from fastapi.testclient import TestClient

        from dbt_charts.cli.filesystem_project import FilesystemProject
        from dbt_charts.core.serve.server import create_server

        app = create_server(FilesystemProject(duckdb_project))
        with TestClient(app, raise_server_exceptions=False) as client:
            numeric = client.get("/inspector/wh/main/orders/amount/")
            categorical = client.get("/inspector/wh/main/orders/status/")
            temporal = client.get("/inspector/wh/main/orders/created_at/")
            time_of_day = client.get("/inspector/wh/main/orders/pickup_time/")
        assert numeric.status_code == 200
        assert "Distribution" in numeric.text  # numeric histogram section
        assert categorical.status_code == 200
        assert "Share" in categorical.text  # categorical pie section
        # TIMESTAMP → date branch renders the monthly timeline.
        assert temporal.status_code == 200
        assert "Records per Month" in temporal.text
        # Bare TIME must NOT take the date branch (::DATE cast would error the
        # whole page); it renders via the generic branch (Share pie + frequency).
        assert time_of_day.status_code == 200
        assert "Value Frequency" in time_of_day.text


# ---------------------------------------------------------------------------
# Server integration: /inspector/* routes served through registered-view pipeline
# ---------------------------------------------------------------------------


def _write_wh_source(tmp_path: Path) -> None:
    """Declare a resolvable 'snowflake' source so dialect resolution succeeds.

    Only the table-level route below needs this — its template calls
    sql_identifier(), which lazily resolves the matched source's dialect on
    first use. An in-memory DuckDB stand-in is enough to make
    ``resolve_source_config("snowflake")`` succeed without needing a real
    warehouse.
    """
    (tmp_path / "dbt_charts.yml").write_text(
        "sources:\n  snowflake:\n    type: duckdb\n    path: ':memory:'\n"
    )


class TestInspectorServerRoute:
    """Verify the server routes /inspector/* through expand_registered_view."""

    def test_inspector_source_returns_200(self, tmp_path: Path) -> None:
        """No source configured at all — inspector_source's template never
        calls sql_identifier(), so the lazy dialect resolver must never touch
        the (empty) adapter registry."""
        from fastapi.testclient import TestClient

        from dbt_charts.cli.filesystem_project import FilesystemProject
        from dbt_charts.core.serve.server import create_server

        app = create_server(FilesystemProject(tmp_path))
        with TestClient(app, raise_server_exceptions=False) as client:
            response = client.get("/inspector/snowflake/")
        # Must succeed through registered-view pipeline (not 404)
        assert response.status_code == 200

    def test_inspector_schema_returns_200(self, tmp_path: Path) -> None:
        """Same as above for inspector_schema — no configured source needed."""
        from fastapi.testclient import TestClient

        from dbt_charts.cli.filesystem_project import FilesystemProject
        from dbt_charts.core.serve.server import create_server

        app = create_server(FilesystemProject(tmp_path))
        with TestClient(app, raise_server_exceptions=False) as client:
            response = client.get("/inspector/snowflake/analytics/")
        assert response.status_code == 200

    def test_inspector_unmatched_does_not_match_registered_view(
        self, tmp_path: Path
    ) -> None:
        from fastapi.testclient import TestClient

        from dbt_charts.cli.filesystem_project import FilesystemProject
        from dbt_charts.core.serve.server import create_server

        # /inspector/ alone (no source segment) is not a registered route
        app = create_server(FilesystemProject(tmp_path))
        with TestClient(app, raise_server_exceptions=False) as client:
            response = client.get("/inspector/")
        # No registered view matches — falls through to 404 (no directory/board at that path)
        assert response.status_code == 404

    def test_old_inspect_route_still_works(self, tmp_path: Path) -> None:
        """The old /inspect/<template>/ route continues to work.

        The old /inspect/ family (query-param model profiling) is a separate
        feature from /inspector/<source>/... (path-based registered views).
        This test verifies it still returns 200 even with missing data (errors
        are rendered as HTML, not as HTTP error codes).
        """
        from fastapi.testclient import TestClient

        from dbt_charts.cli.filesystem_project import FilesystemProject
        from dbt_charts.core.serve.server import create_server

        app = create_server(FilesystemProject(tmp_path))
        with TestClient(app, raise_server_exceptions=False) as client:
            response = client.get("/inspect/model/?model=orders")
        # Errors are rendered as HTML — old route returns 200 even on query failure
        assert response.status_code == 200

    def test_inspector_table_with_schema_query_returns_200(
        self, tmp_path: Path
    ) -> None:
        """Table inspector page renders via the registered-view pipeline."""
        from fastapi.testclient import TestClient

        from dbt_charts.cli.filesystem_project import FilesystemProject
        from dbt_charts.core.serve.server import create_server

        # Mock the schema adapter so we don't need a real warehouse
        _write_wh_source(tmp_path)
        mock_result = QueryResult(
            data=[
                {"name": "order_id", "actual_type": "BIGINT"},
                {"name": "status", "actual_type": "VARCHAR"},
            ],
            schema=literal_schema(
                [
                    {"name": "order_id", "actual_type": "BIGINT"},
                    {"name": "status", "actual_type": "VARCHAR"},
                ]
            ),
        )
        app = create_server(FilesystemProject(tmp_path))
        with (
            patch(
                "dbt_charts.core.execute.adapters.adapter_registry.AdapterRegistry.execute",
                return_value=mock_result,
            ),
            TestClient(app, raise_server_exceptions=False) as client,
        ):
            response = client.get("/inspector/snowflake/analytics/orders/")
        assert response.status_code == 200

    def test_inspector_table_schema_query_executes_exactly_once(
        self, tmp_path: Path
    ) -> None:
        """Schema query for /inspector/<source>/<schema>/<table>/ runs exactly once.

        The registered-view pipeline runs the schema query via the board's own
        query executor. It must not also run a duplicate pre-template registry
        query for the same data. The table page also runs one ``sample_data``
        SQL query, so we count the ``SchemaQuery`` executions specifically —
        exactly one — rather than total calls.
        """
        from unittest.mock import MagicMock

        from fastapi.testclient import TestClient

        from dbt_charts.cli.filesystem_project import FilesystemProject
        from dbt_charts.core.serve.server import create_server

        _write_wh_source(tmp_path)
        schema_result = QueryResult(
            data=[
                {"name": "order_id", "actual_type": "BIGINT"},
                {"name": "status", "actual_type": "VARCHAR"},
            ],
            schema=literal_schema(
                [
                    {"name": "order_id", "actual_type": "BIGINT"},
                    {"name": "status", "actual_type": "VARCHAR"},
                ]
            ),
        )
        mock_execute = MagicMock(return_value=schema_result)
        app = create_server(FilesystemProject(tmp_path))
        with (
            patch(
                "dbt_charts.core.execute.adapters.adapter_registry.AdapterRegistry.execute",
                mock_execute,
            ),
            TestClient(app, raise_server_exceptions=False) as client,
        ):
            response = client.get("/inspector/snowflake/analytics/orders/")
        assert response.status_code == 200
        # The schema query must execute exactly once — not once pre-template and
        # once again during board rendering. Other query types (the sample_data
        # SQL query) are counted separately.
        schema_executions = [
            call
            for call in mock_execute.call_args_list
            if call.args and type(call.args[0]).__name__ == "SchemaQuery"
        ]
        assert len(schema_executions) == 1, (
            f"Expected one SchemaQuery execution, got {len(schema_executions)}"
        )


def test_pivot_keeps_missing_and_null_profile_fields_null() -> None:
    from dbt_charts.core.compile.template.query_result import QueryRow
    from dbt_charts.core.registered_views.expander import _pivot_column_profiles

    cells: list[dict[str, float | None]] = [
        {"null_percentage": 12.5},
        {"null_percentage": None},
        {},
    ]
    cols = [QueryRow("cols", c) for c in cells]
    assert _pivot_column_profiles(cols, [["Nulls", "null_percentage"]]) == [
        ["Nulls", "12.5", None, None]
    ]


def test_pivot_formats_counts_and_percentages() -> None:
    from dbt_charts.core.compile.template.query_result import QueryRow
    from dbt_charts.core.registered_views.expander import _pivot_column_profiles

    cells: list[dict[str, str | int | float]] = [
        {"actual_type": "BIGINT", "null_percentage": 50.82, "distinct_count": 4824},
        {"actual_type": "VARCHAR", "null_percentage": 0.0, "distinct_count": 238},
    ]
    cols = [QueryRow("cols", c) for c in cells]
    characteristics = [
        ["Type", "actual_type"],
        ["Null %", "null_percentage"],
        ["Distinct", "distinct_count"],
    ]
    assert _pivot_column_profiles(cols, characteristics) == [
        ["Type", "BIGINT", "VARCHAR"],
        ["Null %", "50.82", "0"],
        ["Distinct", "4,824", "238"],
    ]
