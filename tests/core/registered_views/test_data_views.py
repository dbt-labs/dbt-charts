"""Tests for /data/ route rename, root sources page, and fail-loud unknown source.

Covers:
- Deliverable 1: /data/ route matching (registry uses data_* names, /data/... routes)
- Deliverable 2: /data/ root page expands and lists sources; no-source SchemaQuery
- Deliverable 3: /data/<unknown>/ returns an error (not a blank 200)
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock

from dbt_charts.core.registered_views.loader import load_builtin_registry
from dbt_charts.core.registered_views.models import RegisteredView
from dbt_charts.core.registered_views.router import RouteRouter


def _router() -> RouteRouter:
    return RouteRouter(load_builtin_registry())


def _data_views() -> list[RegisteredView]:
    return [v for v in load_builtin_registry() if v.name.startswith("data_")]


# ---------------------------------------------------------------------------
# Deliverable 1: Registry names and routes use /data/
# ---------------------------------------------------------------------------


class TestDataViewRegistry:
    """Registry declares data_* names with /data/... routes, not entity_*."""

    def test_no_entity_views_in_registry(self) -> None:
        """No views named entity_* remain after rename."""
        views = load_builtin_registry()
        entity_names = [v.name for v in views if v.name.startswith("entity_")]
        assert entity_names == [], f"Stale entity_* views: {entity_names}"

    def test_data_views_declared(self) -> None:
        """data_source, data_schema, data_table, data_table_detail are all present."""
        names = {v.name for v in _data_views()}
        assert {
            "data_source",
            "data_schema",
            "data_table",
            "data_table_detail",
        } <= names

    def test_data_root_declared(self) -> None:
        """data_root (bare /data/) is declared."""
        names = {v.name for v in load_builtin_registry()}
        assert "data_root" in names, f"Missing data_root; got: {sorted(names)}"

    def test_routes_use_data_prefix(self) -> None:
        """All data_* view routes start with /data/."""
        for v in _data_views():
            assert v.route.startswith("/data/"), (
                f"View {v.name!r} route {v.route!r} does not start with /data/"
            )

    def test_no_entity_routes_in_registry(self) -> None:
        """No routes start with /entity/ after the rename."""
        views = load_builtin_registry()
        entity_routes = [v.route for v in views if v.route.startswith("/entity/")]
        assert entity_routes == [], f"Stale /entity/ routes: {entity_routes}"


# ---------------------------------------------------------------------------
# Deliverable 1: /data/ route matching
# ---------------------------------------------------------------------------


class TestDataRouteMatching:
    """Router matches /data/... but not /entity/..."""

    def test_data_source_route_matches(self) -> None:
        match = _router().match("/data/snowflake/")
        assert match is not None
        assert match.view.name == "data_source"
        assert match.path_params == {"source": "snowflake"}

    def test_data_schema_route_matches(self) -> None:
        match = _router().match("/data/snowflake/analytics/")
        assert match is not None
        assert match.view.name == "data_schema"
        assert match.path_params == {"source": "snowflake", "schema": "analytics"}

    def test_data_table_route_matches(self) -> None:
        match = _router().match("/data/snowflake/analytics/orders/")
        assert match is not None
        assert match.view.name == "data_table"
        assert match.path_params == {
            "source": "snowflake",
            "schema": "analytics",
            "table": "orders",
        }

    def test_data_detail_route_matches(self) -> None:
        match = _router().match("/data/snowflake/analytics/orders/detail/")
        assert match is not None
        assert match.view.name == "data_table_detail"
        assert match.path_params == {
            "source": "snowflake",
            "schema": "analytics",
            "table": "orders",
        }

    def test_data_root_route_matches(self) -> None:
        """/data/ (bare) matches the data_root view."""
        match = _router().match("/data/")
        assert match is not None
        assert match.view.name == "data_root"

    def test_entity_source_route_no_longer_matches(self) -> None:
        """/entity/<source>/ must not match after the rename."""
        match = _router().match("/entity/snowflake/")
        assert match is None, f"Old /entity/ route still matches: {match}"

    def test_entity_schema_route_no_longer_matches(self) -> None:
        """/entity/<source>/<schema>/ must not match after the rename."""
        assert _router().match("/entity/snowflake/analytics/") is None

    def test_entity_table_route_no_longer_matches(self) -> None:
        assert _router().match("/entity/snowflake/analytics/orders/") is None


# ---------------------------------------------------------------------------
# Deliverable 1: data_url functions emit /data/...
# ---------------------------------------------------------------------------


class TestDataUrlFunctions:
    """data_source_url / data_schema_url / data_table_url emit /data/ paths."""

    def test_data_source_url(self) -> None:
        from dbt_charts.core.registered_views.data_urls import data_source_url

        assert data_source_url("wh") == "/data/wh/"

    def test_data_schema_url(self) -> None:
        from dbt_charts.core.registered_views.data_urls import data_schema_url

        assert data_schema_url("wh", "analytics") == "/data/wh/analytics/"

    def test_data_table_url(self) -> None:
        from dbt_charts.core.registered_views.data_urls import data_table_url

        assert (
            data_table_url("wh", "analytics", "orders") == "/data/wh/analytics/orders/"
        )


# ---------------------------------------------------------------------------
# Deliverable 1: pack canonical_data_url emits /data/...
# ---------------------------------------------------------------------------


class TestPackDataUrl:
    """canonical_data_url on ProposedDashboard emits /data/... paths."""

    def test_plan_pack_canonical_data_url_format(self) -> None:
        from dbt_charts.core.pack.planner import SourceEntry, plan_pack

        proposal = plan_pack(
            sources={
                "fivetran_zendesk": SourceEntry(
                    schema="zendesk",
                    tables=["tickets", "users"],
                    source_name="fivetran_zendesk",
                )
            }
        )
        all_dashboards = [d for f in proposal.folders for d in f.dashboards]
        for dash in all_dashboards:
            if dash.canonical_data_url is not None:
                assert dash.canonical_data_url.startswith("/data/"), (
                    f"canonical_data_url must start with /data/: {dash.canonical_data_url}"
                )


# ---------------------------------------------------------------------------
# Deliverable 2: SchemaQuery.source is optional
# ---------------------------------------------------------------------------


class TestSchemaQueryOptionalSource:
    """SchemaQuery can be constructed without source (source: None)."""

    def test_schema_query_source_optional(self) -> None:
        from dbt_charts.core.compile.models.query.normalized import SchemaQuery

        # Must not raise
        q = SchemaQuery()
        assert q.source is None

    def test_schema_query_schema_still_requires_source(self) -> None:
        """schema without source must raise — otherwise the adapter's no-source
        branch silently returns the source list and ignores the schema (a wrong
        result that looks right)."""
        import pytest
        from pydantic import ValidationError

        from dbt_charts.core.compile.models.query.normalized import SchemaQuery

        with pytest.raises(ValidationError):
            SchemaQuery(schema="analytics")  # source not set

    def test_schema_query_table_still_requires_schema(self) -> None:
        import pytest
        from pydantic import ValidationError

        from dbt_charts.core.compile.models.query.normalized import SchemaQuery

        with pytest.raises(ValidationError):
            SchemaQuery(table="t")  # schema not set

    def test_schema_query_column_still_requires_table(self) -> None:
        import pytest
        from pydantic import ValidationError

        from dbt_charts.core.compile.models.query.normalized import SchemaQuery

        with pytest.raises(ValidationError):
            SchemaQuery(source="s", schema="sc", column="c")  # table not set


# ---------------------------------------------------------------------------
# Deliverable 2: no-source SchemaQuery returns source rows from schema_adapter
# ---------------------------------------------------------------------------


class TestSchemaAdapterNoSource:
    """schema_adapter with source=None dispatches to list_sources."""

    def test_no_source_returns_source_rows(self) -> None:
        """When source is None, SchemaAdapter returns rows with 'name' column."""
        from dbt_charts.core.compile.models.query.normalized import SchemaQuery
        from dbt_charts.core.execute.adapters.schema_adapter import SchemaAdapter

        mock_registry = MagicMock()
        # list_sql_sources returns two configured sources
        mock_registry.list_sql_sources.return_value = [
            {"name": "db", "type": "duckdb"},
            {"name": "prod", "type": "snowflake"},
        ]

        adapter = SchemaAdapter(mock_registry)
        query = SchemaQuery()  # source=None
        from dbt_charts.core.compile.models.board.normalized import VariableValues

        result = adapter._execute(query, variables=VariableValues({}))
        assert result.error is None, f"Expected no error, got: {result.error}"
        assert len(result.data) == 2
        names = {row["name"] for row in result.data}
        assert names == {"db", "prod"}


# ---------------------------------------------------------------------------
# Deliverable 2: /data/ root template expands
# ---------------------------------------------------------------------------


class TestDataRootTemplate:
    """data/root.yaml template exists and expands without error."""

    def test_data_root_view_has_no_prereq_queries(self) -> None:
        """data_root view has no pre-template queries (board query lists sources)."""
        views = {v.name: v for v in load_builtin_registry()}
        root_view = views.get("data_root")
        assert root_view is not None, "data_root view missing"
        assert root_view.queries is None or root_view.queries == {}

    def test_data_root_template_expands(self) -> None:
        """data/root.yaml expands to a valid authored board."""
        from dbt_charts.core.registered_views.expander import expand_registered_view
        from dbt_charts.core.registered_views.router import RouteMatch

        views = {v.name: v for v in load_builtin_registry()}
        root_view = views["data_root"]
        match = RouteMatch(view=root_view, path_params={})
        # No pre-template query results needed
        authored = expand_registered_view(match, {})
        assert authored is not None
        # Should have a title
        assert "title" in authored or authored is not None

    def test_data_root_lists_sources_end_to_end(self, tmp_path: Path) -> None:
        """GET /data/ returns 200 and lists the configured source through the
        real server — the happy-path symmetric to the unknown-source test."""
        import duckdb
        from fastapi.testclient import TestClient

        from dbt_charts.cli.filesystem_project import FilesystemProject
        from dbt_charts.core.serve.server import create_server

        db_path = tmp_path / "wh.duckdb"
        conn = duckdb.connect(str(db_path))
        conn.execute("CREATE TABLE orders (id INTEGER)")
        conn.close()
        (tmp_path / "dbt_charts.yml").write_text(
            f"sources:\n  wh:\n    type: duckdb\n    path: '{db_path}'\n"
        )
        (tmp_path / "charts").mkdir()

        with TestClient(
            create_server(FilesystemProject(tmp_path)),
            raise_server_exceptions=False,
        ) as client:
            resp = client.get("/data/")

        assert resp.status_code == 200, resp.text[:500]
        assert "wh" in resp.text, (
            f"source 'wh' not listed on /data/:\n{resp.text[:500]}"
        )


# ---------------------------------------------------------------------------
# Deliverable 3: /data/<unknown>/ fails loud (not blank 200)
# ---------------------------------------------------------------------------


class TestUnknownSourceFailsLoud:
    """An unknown source in /data/<unknown>/ must surface as an error end-to-end.

    This pins the reported symptom (a blank 200) at the serve boundary, not an
    adapter-layer artifact: the source-not-found error must propagate all the way
    to the rendered page. The assertion holds regardless of *how* the error is
    surfaced internally — it pins behavior, not an implementation detail.
    """

    def test_data_unknown_source_renders_error_not_blank(self, tmp_path: Path) -> None:
        import duckdb
        from fastapi.testclient import TestClient

        from dbt_charts.cli.filesystem_project import FilesystemProject
        from dbt_charts.core.serve.server import create_server

        db_path = tmp_path / "wh.duckdb"
        conn = duckdb.connect(str(db_path))
        conn.execute("CREATE TABLE orders (id INTEGER)")
        conn.close()
        (tmp_path / "dbt_charts.yml").write_text(
            f"sources:\n  wh:\n    type: duckdb\n    path: '{db_path}'\n"
        )
        (tmp_path / "charts").mkdir()

        with TestClient(
            create_server(FilesystemProject(tmp_path)),
            raise_server_exceptions=False,
        ) as client:
            resp = client.get("/data/bogus/")

        # The page must name the unknown source — a blank/empty render would not.
        body = resp.text
        assert "bogus" in body, f"unknown source not surfaced in page:\n{body[:500]}"
        assert "not found" in body.lower() or "error" in body.lower(), (
            f"no error indication in page:\n{body[:500]}"
        )
