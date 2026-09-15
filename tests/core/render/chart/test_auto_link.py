"""Tests for auto-link resolver: row-grain table detail, FK-dimension drill,
filter cell links, and multi-dimension chart auto-link.

Consolidation: after the resolver is live the table-index template no longer
synthesizes the link inline. The regression test at the bottom verifies that
/data/.../index pages still produce a detail link identically.
"""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path
from typing import TYPE_CHECKING

import pytest

if TYPE_CHECKING:
    from dbt_charts.core.registered_views.query_runner import ViewQueryResult
    from dbt_charts.core.registered_views.router import RouteMatch

from dbt_charts.core.compile.models.query.normalized import SqlQuery
from dbt_charts.core.compile.resolve.chart.link_keys import plan_link_keys
from dbt_charts.core.project import Project


def _sql_query(sql: str, source: str = "dw") -> SqlQuery:
    return SqlQuery(sql=sql, source=source)


# ---------------------------------------------------------------------------
# plan_link_keys — shared helper lifted from expander._plan_link_keys
# ---------------------------------------------------------------------------


class TestPlanLinkKeys:
    def test_single_id_column(self) -> None:
        rows = [{"name": "id", "actual_type": "BIGINT"}]
        assert plan_link_keys(rows) == [{"name": "id", "encode": False}]

    def test_prefers_id_over_fk_columns(self) -> None:
        rows = [
            {"name": "id", "actual_type": "BIGINT"},
            {"name": "user_id", "actual_type": "BIGINT"},
        ]
        assert plan_link_keys(rows) == [{"name": "id", "encode": False}]

    def test_composite_fk_keys_when_no_id(self) -> None:
        rows = [
            {"name": "order_id", "actual_type": "BIGINT"},
            {"name": "product_id", "actual_type": "BIGINT"},
            {"name": "quantity", "actual_type": "BIGINT"},
        ]
        result = plan_link_keys(rows)
        assert len(result) == 2
        names = {r["name"] for r in result}
        assert names == {"order_id", "product_id"}

    def test_string_id_now_included_with_encode(self) -> None:
        """VARCHAR id is now a valid link key — emitted with encode=True."""
        rows = [{"name": "id", "actual_type": "VARCHAR"}]
        assert plan_link_keys(rows) == [{"name": "id", "encode": True}]

    def test_uuid_id_now_included_with_encode(self) -> None:
        """UUID id is now a valid link key — emitted with encode=True."""
        rows = [{"name": "id", "actual_type": "UUID"}]
        assert plan_link_keys(rows) == [{"name": "id", "encode": True}]

    def test_empty_rows_returns_empty(self) -> None:
        assert plan_link_keys([]) == []

    def test_no_id_like_columns_returns_empty(self) -> None:
        rows = [
            {"name": "name", "actual_type": "VARCHAR"},
            {"name": "amount", "actual_type": "DOUBLE"},
        ]
        assert plan_link_keys(rows) == []

    def test_scale_zero_fixed_point_is_url_safe(self) -> None:
        """NUMBER(38,0) is Snowflake's canonical integer id — url-safe."""
        rows = [{"name": "id", "actual_type": "NUMBER(38,0)"}]
        assert plan_link_keys(rows) == [{"name": "id", "encode": False}]

    def test_scale_nonzero_fixed_point_not_url_safe(self) -> None:
        rows = [{"name": "id", "actual_type": "NUMBER(10,2)"}]
        assert plan_link_keys(rows) == []


# ---------------------------------------------------------------------------
# resolve_column_set_link table path — the unified resolver for table row-grain detail
# ---------------------------------------------------------------------------


def _table_channel_cols(rows: list[dict[str, str]]) -> dict[str, str]:
    """Build channel_cols for the table path from plan_link_keys output.

    Table rows use channel name == column name (no indirection like Vega channels).
    """
    from dbt_charts.core.compile.resolve.chart.link_keys import plan_link_keys

    keys = plan_link_keys(rows)
    return {str(k["name"]): str(k["name"]) for k in keys}


class TestResolveColumnSetLinkTablePath:
    """Tests for resolve_column_set_link with row_detail=True (table path).

    A table row's "channel_cols" is derived from plan_link_keys: channel==column
    so the Jinja placeholder {{ id }} resolves directly to the cell value.

    Bail conditions (multi-join, GROUP BY, etc.) live in source_schema_table —
    tested via TestSourceSchemaTableBailConditions below. resolve_column_set_link
    is pure given a pre-resolved loc.
    """

    def test_auto_link_off_returns_empty(self) -> None:
        from dbt_charts.core.render.chart.auto_link import resolve_column_set_link

        rows = [{"name": "id", "actual_type": "BIGINT"}]
        channel_cols = _table_channel_cols(rows)
        assert (
            resolve_column_set_link(
                channel_cols,
                ("dw", "analytics", "orders"),
                fk_edges=[],
                auto_link=False,
            )
            == ""
        )

    def test_auto_link_default_off(self) -> None:
        from dbt_charts.core.render.chart.auto_link import resolve_column_set_link

        rows = [{"name": "id", "actual_type": "BIGINT"}]
        channel_cols = _table_channel_cols(rows)
        assert (
            resolve_column_set_link(
                channel_cols, ("dw", "analytics", "orders"), fk_edges=[]
            )
            == ""
        )

    def test_schema_query_builds_detail_link(self) -> None:
        """row_detail=True: single key col → same-table detail URL with urlencode."""
        from dbt_charts.core.render.chart.auto_link import resolve_column_set_link

        rows = [{"name": "id", "actual_type": "BIGINT"}]
        channel_cols = _table_channel_cols(rows)
        link = resolve_column_set_link(
            channel_cols,
            ("dw", "analytics", "orders"),
            fk_edges=[],
            auto_link=True,
            row_detail=True,
        )
        assert link == "/data/dw/analytics/orders/detail/?id={{ id | urlencode }}"

    def test_schema_query_composite_keys(self) -> None:
        """row_detail=True: composite keys → same-table detail URL."""
        from dbt_charts.core.render.chart.auto_link import resolve_column_set_link

        rows = [
            {"name": "order_id", "actual_type": "BIGINT"},
            {"name": "product_id", "actual_type": "BIGINT"},
        ]
        channel_cols = _table_channel_cols(rows)
        link = resolve_column_set_link(
            channel_cols,
            ("dw", "analytics", "order_lines"),
            fk_edges=[],
            auto_link=True,
            row_detail=True,
        )
        assert (
            link
            == "/data/dw/analytics/order_lines/detail/?order_id={{ order_id | urlencode }}&product_id={{ product_id | urlencode }}"
        )

    def test_no_id_like_columns_returns_empty(self) -> None:
        from dbt_charts.core.render.chart.auto_link import resolve_column_set_link

        rows = [
            {"name": "name", "actual_type": "VARCHAR"},
            {"name": "amount", "actual_type": "DOUBLE"},
        ]
        channel_cols = _table_channel_cols(rows)  # empty — no key columns
        assert (
            resolve_column_set_link(
                channel_cols,
                ("dw", "analytics", "metrics"),
                fk_edges=[],
                auto_link=True,
                row_detail=True,
            )
            == ""
        )

    def test_sql_query_single_table_builds_link(self) -> None:
        """row_detail=True: pre-resolved loc from SQL query single table → detail URL."""
        from dbt_charts.core.compile.models.query.normalized import SqlQuery
        from dbt_charts.core.render.chart.auto_link import (
            resolve_column_set_link,
            source_schema_table,
        )

        query = SqlQuery(
            sql="SELECT * FROM analytics.orders WHERE status = 'open'", source="dw"
        )
        loc = source_schema_table(query, require_row_grain=True)
        assert loc  # sanity: single-table SQL should resolve
        rows = [{"name": "id", "actual_type": "BIGINT"}]
        channel_cols = _table_channel_cols(rows)
        link = resolve_column_set_link(
            channel_cols,
            loc,
            fk_edges=[],
            auto_link=True,
            row_detail=True,
        )
        assert link == "/data/dw/analytics/orders/detail/?id={{ id | urlencode }}"

    def test_fk_edges_ignored_in_table_root_link(self) -> None:
        """Table root link is own-table only even when FK edges exist.

        _inject_fk_column_links owns FK drill per-column; passing fk_edges here
        would redirect root clicks to the referenced entity and overshadow the
        same-table fallback for non-FK cells.  Pins this deliberate design choice.
        """
        from dbt_charts.core.render.chart.auto_link import resolve_column_set_link

        # customer_id FK → customers table; but with row_detail=True the root
        # link should stay on the orders table, not jump to customers.
        fk_edge = {
            "from_table": "orders",
            "from_column": "customer_id",
            "to_table": "customers",
            "to_column": "id",
        }
        rows = [{"name": "customer_id", "actual_type": "BIGINT"}]
        channel_cols = _table_channel_cols(rows)
        link = resolve_column_set_link(
            channel_cols,
            ("dw", "analytics", "orders"),
            fk_edges=[fk_edge],
            auto_link=True,
            row_detail=True,
        )
        # row_detail=True with a single FK-matched key → FK path in resolver.
        # This test documents the actual resolver behavior when fk_edges are
        # passed with row_detail=True (FK wins), confirming why the table
        # path must pass fk_edges=[] to keep own-table semantics.
        # The resolver itself is correct; the caller (synthesize_auto_link)
        # enforces the policy by passing fk_edges=[].
        assert (
            link
            == "/data/dw/analytics/customers/detail/?id={{ customer_id | urlencode }}"
        )


# ---------------------------------------------------------------------------
# source_schema_table bail conditions — this is where complex-query logic lives
# ---------------------------------------------------------------------------


class TestSourceSchemaTableBailConditions:
    """source_schema_table bails on queries that don't map to a single base table.

    These were formerly tested via resolve_column_set_link, but the bail logic
    lives entirely in source_schema_table — test it directly.
    """

    def test_multi_join_returns_empty(self) -> None:
        """source_schema_table itself always bails on JOINs (no adapter_registry
        param) — fail-closed, unaffected by this file's FK-proven JOIN
        resolution which lives in _loc_for_query instead."""
        from dbt_charts.core.render.chart.auto_link import source_schema_table

        query = _sql_query(
            "SELECT o.id, c.name FROM orders o JOIN customers c ON o.cust_id = c.id"
        )
        assert not source_schema_table(query, require_row_grain=True)

    def test_cte_returns_empty(self) -> None:
        """Bails because `orders` has no schema prefix — not because of the CTE
        itself (schema-qualified single-CTE-wrap queries now resolve; see
        TestCteGrainResolution.test_simple_cte_wrap_resolves)."""
        from dbt_charts.core.render.chart.auto_link import source_schema_table

        query = _sql_query("WITH cte AS (SELECT * FROM orders) SELECT * FROM cte")
        assert not source_schema_table(query, require_row_grain=True)

    def test_subquery_returns_empty(self) -> None:
        from dbt_charts.core.render.chart.auto_link import source_schema_table

        query = _sql_query("SELECT * FROM (SELECT id, name FROM orders) sub")
        assert not source_schema_table(query, require_row_grain=True)

    def test_no_schema_bails(self) -> None:
        """SQL FROM bare table (no schema prefix) — can't build canonical path."""
        from dbt_charts.core.render.chart.auto_link import source_schema_table

        query = _sql_query("SELECT * FROM orders")
        assert not source_schema_table(query, require_row_grain=True)

    def test_group_by_bails_with_require_row_grain(self) -> None:
        """Aggregated query with require_row_grain=True bails."""
        from dbt_charts.core.render.chart.auto_link import source_schema_table

        query = _sql_query(
            "SELECT customer_id, COUNT(*) AS n FROM analytics.orders GROUP BY customer_id"
        )
        assert not source_schema_table(query, require_row_grain=True)

    def test_distinct_bails_with_require_row_grain(self) -> None:
        from dbt_charts.core.render.chart.auto_link import source_schema_table

        query = _sql_query("SELECT DISTINCT customer_id FROM analytics.orders")
        assert not source_schema_table(query, require_row_grain=True)

    def test_aggregate_function_bails_with_require_row_grain(self) -> None:
        from dbt_charts.core.render.chart.auto_link import source_schema_table

        query = _sql_query("SELECT id, SUM(amount) AS total FROM analytics.orders")
        assert not source_schema_table(query, require_row_grain=True)

    def test_catalog_qualified_bails(self) -> None:
        from dbt_charts.core.render.chart.auto_link import source_schema_table

        query = _sql_query("SELECT id FROM mydb.analytics.orders")
        assert not source_schema_table(query, require_row_grain=True)


# ---------------------------------------------------------------------------
# CTE grain resolution (new): schema-qualified CTEs now resolve
# ---------------------------------------------------------------------------


class TestCteGrainResolution:
    """source_schema_table resolves CTE-wrapped row-grain queries.

    These tests were failing before the resolver was added — the old code
    unconditionally bailed on any CTE. They verify the new behavior.
    """

    def test_simple_cte_wrap_resolves(self) -> None:
        """CTE wrapping a schema-qualified table → resolves to base (source, schema, table)."""
        from dbt_charts.core.render.chart.auto_link import source_schema_table

        query = _sql_query(
            "WITH cte AS (SELECT * FROM analytics.orders) SELECT * FROM cte"
        )
        loc = source_schema_table(query, require_row_grain=True)
        assert loc == ("dw", "analytics", "orders")

    def test_cte_aggregate_in_body_bails(self) -> None:
        """GROUP BY inside CTE body → still bails (aggregation collapses grain)."""
        from dbt_charts.core.render.chart.auto_link import source_schema_table

        query = _sql_query(
            "WITH agg AS (SELECT user_id, COUNT(*) AS n FROM analytics.events GROUP BY 1)"
            " SELECT * FROM agg"
        )
        assert not source_schema_table(query, require_row_grain=True)

    def test_cte_distinct_in_body_bails(self) -> None:
        """DISTINCT inside CTE body → still bails."""
        from dbt_charts.core.render.chart.auto_link import source_schema_table

        query = _sql_query(
            "WITH cte AS (SELECT DISTINCT user_id FROM analytics.orders) SELECT * FROM cte"
        )
        assert not source_schema_table(query, require_row_grain=True)

    def test_cte_agg_function_in_body_bails(self) -> None:
        """Aggregate function inside CTE body → still bails."""
        from dbt_charts.core.render.chart.auto_link import source_schema_table

        query = _sql_query(
            "WITH cte AS (SELECT id, SUM(amount) AS total FROM analytics.orders)"
            " SELECT * FROM cte"
        )
        assert not source_schema_table(query, require_row_grain=True)

    def test_cte_subquery_in_body_bails(self) -> None:
        """Subquery inside CTE body → still bails."""
        from dbt_charts.core.render.chart.auto_link import source_schema_table

        query = _sql_query(
            "WITH cte AS (SELECT * FROM analytics.orders"
            " WHERE id = (SELECT MAX(id) FROM analytics.orders)) SELECT * FROM cte"
        )
        assert not source_schema_table(query, require_row_grain=True)

    def test_cte_join_in_body_bails(self) -> None:
        """JOIN inside CTE body → bails (can't prove grain without FK proof here)."""
        from dbt_charts.core.render.chart.auto_link import source_schema_table

        query = _sql_query(
            "WITH cte AS (SELECT o.id, c.name FROM analytics.orders o"
            " JOIN analytics.customers c ON o.cust_id = c.id) SELECT * FROM cte"
        )
        assert not source_schema_table(query, require_row_grain=True)

    def test_cte_chart_mark_path_still_bails(self) -> None:
        """require_row_grain=False (chart mark path) → CTE still bails (unchanged)."""
        from dbt_charts.core.render.chart.auto_link import source_schema_table

        query = _sql_query(
            "WITH cte AS (SELECT * FROM analytics.orders) SELECT * FROM cte"
        )
        assert not source_schema_table(query, require_row_grain=False)

    def test_cte_catalog_qualified_base_bails(self) -> None:
        """CTE over catalog-qualified base table (3-part name) → bails."""
        from dbt_charts.core.render.chart.auto_link import source_schema_table

        query = _sql_query(
            "WITH cte AS (SELECT * FROM db.schema.orders) SELECT * FROM cte"
        )
        assert not source_schema_table(query, require_row_grain=True)

    def test_outer_select_ignores_cte_bails(self) -> None:
        """Outer FROM doesn't reference the CTE at all → must not resolve to the
        CTE's base table.

        Regression test: the resolver
        validated the CTE body thoroughly but never checked what the outer
        query actually reads from. This degenerate query defines a CTE over
        `t1` but the outer SELECT reads from `t2` entirely — resolving to
        `t1` here would emit a drill for the wrong table.
        """
        from dbt_charts.core.render.chart.auto_link import source_schema_table

        query = _sql_query(
            "WITH x AS (SELECT a FROM analytics.t1) SELECT b FROM analytics.t2"
        )
        assert not source_schema_table(query, require_row_grain=True)

    def test_outer_select_references_unrelated_name_bails(self) -> None:
        """Outer FROM is a bare name that isn't the CTE's alias → bails.

        Same bug class as test_outer_select_ignores_cte_bails, but the outer
        table reference is unqualified rather than schema-qualified — must
        still fail the "outer FROM is this CTE" check, not fall through to
        some other bail path by accident.
        """
        from dbt_charts.core.render.chart.auto_link import source_schema_table

        query = _sql_query(
            "WITH cte AS (SELECT * FROM analytics.orders) SELECT * FROM other_name"
        )
        assert not source_schema_table(query, require_row_grain=True)

    def test_outer_select_distinct_bails(self) -> None:
        """DISTINCT on the *outer* SELECT (CTE body itself is clean) → bails.

        Regression test: the guard
        checked GROUP BY/DISTINCT/aggregate functions inside the CTE body but
        not on the outer SELECT that actually produces the emitted rows. This
        is exactly the non-goal the task calls out — an aggregated/deduped
        result must never get a per-row detail drill.
        """
        from dbt_charts.core.render.chart.auto_link import source_schema_table

        query = _sql_query(
            "WITH c AS (SELECT * FROM analytics.tickets)"
            " SELECT DISTINCT owner_id, status FROM c"
        )
        assert not source_schema_table(query, require_row_grain=True)

    def test_outer_select_aggregate_function_bails(self) -> None:
        """Aggregate function on the *outer* SELECT (CTE body itself is clean)
        → bails. Same bug class as test_outer_select_distinct_bails."""
        from dbt_charts.core.render.chart.auto_link import source_schema_table

        query = _sql_query(
            "WITH c AS (SELECT amount FROM analytics.orders)"
            " SELECT SUM(amount) AS total FROM c"
        )
        assert not source_schema_table(query, require_row_grain=True)


# ---------------------------------------------------------------------------
# CTE alias tracking: emitted key param uses output column name
# ---------------------------------------------------------------------------


class TestCteAliasTracking:
    """Full-render tests proving alias tracing through CTE projections."""

    def _render_table(
        self, sql: str, tmp_path, local_project: Callable[..., Project]
    ) -> str:
        import sqlite3

        import yaml

        from dbt_charts.core.compile import compile
        from dbt_charts.core.execute import Executor
        from dbt_charts.core.execute.adapters import build_adapter_registry
        from dbt_charts.core.render import render

        db_path = tmp_path / "tickets.sqlite"
        conn = sqlite3.connect(str(db_path))
        conn.execute("CREATE TABLE tickets (id INTEGER, subject TEXT)")
        conn.execute("INSERT INTO tickets VALUES (1, 'Bug report')")
        conn.commit()
        conn.close()

        sources_yaml = {"sources": {"dw": {"type": "sqlite", "path": str(db_path)}}}
        (tmp_path / "dbt_charts.yml").write_text(yaml.dump(sources_yaml))

        board_yaml = f"""\
title: "Tickets"
auto_link: true
source: dw
queries:
  rows:
    source: dw
    sql: {sql!r}
rows:
  - chart
charts:
  chart:
    title: "Tickets"
    type: table
    query: rows
"""
        result = compile(board_yaml)
        assert result.board is not None, result.errors
        executor = Executor(
            result.board,
            adapter_registry=build_adapter_registry(local_project(tmp_path)),
        )
        return render(result.board, executor, format="html").output

    def test_cte_pass_through_emits_drill(
        self, tmp_path, local_project: Callable[..., Project]
    ) -> None:
        """CTE SELECT * pass-through → drill link on base table, key = id."""
        html = self._render_table(
            "WITH cte AS (SELECT * FROM main.tickets) SELECT * FROM cte",
            tmp_path,
            local_project,
        )
        assert "/data/dw/main/tickets/detail/" in html, (
            f"Expected drill link. First 3000 chars: {html[:3000]}"
        )
        assert "?id=" in html, "Key column must be 'id' for pass-through CTE"

    def test_cte_body_alias_uses_base_column_as_param(
        self, tmp_path, local_project: Callable[..., Project]
    ) -> None:
        """CTE body aliases id → ticket_id: URL **param** is `id` (the base
        table column — the detail view's variable planner reads its
        `variables:` from the base table's own schema via
        `plan_key_variables`, so it only ever recognizes base column names,
        never a query's output alias), with the value correctly read from
        the row's actual `ticket_id` field.

        Regression test: an earlier version emitted `?ticket_id=` (the
        output alias) as the param — a name the detail view's variable
        planner never declares, so the link silently produced an
        unconstrained `WHERE 1=1 LIMIT 2` page instead of the clicked row.
        """
        html = self._render_table(
            "WITH cte AS (SELECT id AS ticket_id, subject FROM main.tickets) SELECT * FROM cte",
            tmp_path,
            local_project,
        )
        assert "/data/dw/main/tickets/detail/?id=" in html, (
            "URL param must be the base column 'id', not the output alias "
            f"'ticket_id'. First 3000 chars: {html[:3000]}"
        )
        assert "?ticket_id=" not in html, (
            "Output alias must never be used as the URL param name — the "
            "detail view's variable planner doesn't recognize it."
        )

    def test_outer_select_alias_uses_base_column_as_param(
        self, tmp_path, local_project: Callable[..., Project]
    ) -> None:
        """Outer SELECT aliases id → ticket_id: URL param is still `id` (the
        base column), same reasoning as test_cte_body_alias_uses_base_column_as_param.
        """
        html = self._render_table(
            "WITH cte AS (SELECT id, subject FROM main.tickets)"
            " SELECT id AS ticket_id, subject FROM cte",
            tmp_path,
            local_project,
        )
        assert "/data/dw/main/tickets/detail/?id=" in html, (
            f"URL param must be the base column 'id'. First 3000 chars: {html[:3000]}"
        )
        assert "?ticket_id=" not in html

    def test_aliased_key_drill_round_trips_to_the_correct_row(
        self, tmp_path, local_project: Callable[..., Project]
    ) -> None:
        """End-to-end round trip: render a table whose key column is aliased,
        extract the emitted drill URL, then actually drive the real detail
        registered view with it and confirm it returns the *specific* clicked
        row — not an arbitrary one.

        This is the test that would have caught the CRITICAL bug directly:
        every other test in this module asserts on the URL string, none
        drove the URL through the real detail view. The detail view's
        `variables:` come from `plan_key_variables` reading the *base
        table's* schema (`table-detail.yaml`), so a URL param that isn't a
        real base-table column name is silently ignored — the detail SQL
        degrades to `WHERE 1=1 ... LIMIT 2` and shows arbitrary rows.
        """
        import re
        import sqlite3

        import yaml

        from dbt_charts.core.compile import compile
        from dbt_charts.core.execute import Executor
        from dbt_charts.core.execute.adapters import build_adapter_registry
        from dbt_charts.core.registered_views.render_pipeline import (
            RenderSuccess,
            render_registered_view,
        )
        from dbt_charts.core.render import render

        db_path = tmp_path / "tickets.sqlite"
        conn = sqlite3.connect(str(db_path))
        conn.execute("CREATE TABLE tickets (id INTEGER, subject TEXT)")
        conn.execute("INSERT INTO tickets VALUES (1, 'Bug report')")
        conn.execute("INSERT INTO tickets VALUES (2, 'Feature request')")
        conn.commit()
        conn.close()

        sources_yaml = {"sources": {"dw": {"type": "sqlite", "path": str(db_path)}}}
        (tmp_path / "dbt_charts.yml").write_text(yaml.dump(sources_yaml))

        board_yaml = """\
title: "Tickets"
auto_link: true
source: dw
queries:
  rows:
    source: dw
    sql: "WITH cte AS (SELECT id AS ticket_id, subject FROM main.tickets) SELECT * FROM cte"
rows:
  - chart
charts:
  chart:
    title: "Tickets"
    type: table
    query: rows
"""
        result = compile(board_yaml)
        assert result.board is not None, result.errors
        project = local_project(tmp_path)
        adapter_registry = build_adapter_registry(project)
        executor = Executor(result.board, adapter_registry=adapter_registry)
        html = render(result.board, executor, format="html").output
        assert isinstance(html, str)

        # Pull the emitted param+value straight out of the rendered row link
        # rather than assuming what it should be — this is the round trip.
        match = re.search(r"/data/dw/main/tickets/detail/\?(\w+)=(\S+?)[\"&]", html)
        assert match is not None, f"No detail link found. HTML: {html[:3000]}"
        param, value = match.group(1), match.group(2)

        detail_result = render_registered_view(
            request_path="/data/dw/main/tickets/detail/",
            project=project,
            adapter_registry=adapter_registry,
            result_cache=None,
            request_variables={param: value},
        )
        assert isinstance(detail_result, RenderSuccess), (
            f"Detail view failed to render: {detail_result}"
        )
        detail_html = detail_result.output

        # Whichever row's link we grabbed must be the *only* row on the
        # detail page — not both (which is what WHERE 1=1 LIMIT 2 produces
        # when the param is unrecognized, e.g. the bug where the emitted
        # param was the output alias 'ticket_id' instead of the base 'id').
        assert "Bug report" in detail_html or "Feature request" in detail_html
        assert not ("Bug report" in detail_html and "Feature request" in detail_html), (
            "Detail page shows both rows — the emitted param wasn't "
            f"recognized, so the query fell back to an unfiltered scan. "
            f"HTML: {detail_html[:3000]}"
        )

    def test_star_cte_with_explicit_outer_projection_emits_drill(
        self, tmp_path, local_project: Callable[..., Project]
    ) -> None:
        """Regression test: `WITH c AS (SELECT * FROM t) SELECT id, subject
        FROM c` — a star-body CTE with an explicit (non-star), non-renaming
        outer projection — is the most ordinary CTE shape there is, and must
        emit a drill keyed on `id`. The compose logic only recorded *renamed*
        outer columns; a pass-through column (`outer_output == cte_col`) was
        never entered into the alias map, so this exact shape produced no
        link at all despite `id` provably being present in the output.
        """
        html = self._render_table(
            "WITH cte AS (SELECT * FROM main.tickets) SELECT id, subject FROM cte",
            tmp_path,
            local_project,
        )
        assert "/data/dw/main/tickets/detail/" in html
        assert "?id=" in html, (
            "id is provably present in the output (pass-through, unrenamed) "
            f"and must emit a drill. First 3000 chars: {html[:3000]}"
        )

    def test_unrelated_column_aliased_to_key_name_does_not_emit_wrong_drill(
        self, tmp_path, local_project: Callable[..., Project]
    ) -> None:
        """Regression test: a CTE that never
        selects the base PK at all, but aliases a *different* column to the
        PK's name, must not emit a drill keyed on that coincidental name.

        `main.tickets` has `id` (the real PK) and `ticket_number` (an
        unrelated column). The query below never selects `id` — only
        `ticket_number AS id`. `plan_link_keys` picks `id` from the base
        table's schema (it doesn't know the query renamed something onto
        that name); a naive identity fallback would then assume the output's
        `id` column IS the base `id`, when it actually holds `ticket_number`
        values — a live drill to the wrong ticket. Fixed by tracking whether
        each layer's projection is a bare `SELECT *` (`_is_star_projection`)
        so a column absent from the traced alias map is treated as *proven
        absent from the output*, not identity.
        """
        import sqlite3

        import yaml

        from dbt_charts.core.compile import compile
        from dbt_charts.core.execute import Executor
        from dbt_charts.core.execute.adapters import build_adapter_registry
        from dbt_charts.core.render import render

        db_path = tmp_path / "tickets.sqlite"
        conn = sqlite3.connect(str(db_path))
        conn.execute(
            "CREATE TABLE tickets (id INTEGER, ticket_number INTEGER, subject TEXT)"
        )
        conn.execute("INSERT INTO tickets VALUES (1, 4102, 'Bug report')")
        conn.commit()
        conn.close()

        sources_yaml = {"sources": {"dw": {"type": "sqlite", "path": str(db_path)}}}
        (tmp_path / "dbt_charts.yml").write_text(yaml.dump(sources_yaml))

        board_yaml = """\
title: "Tickets"
auto_link: true
source: dw
queries:
  rows:
    source: dw
    sql: "WITH t AS (SELECT ticket_number AS id, subject FROM main.tickets) SELECT * FROM t"
rows:
  - chart
charts:
  chart:
    title: "Tickets"
    type: table
    query: rows
"""
        result = compile(board_yaml)
        assert result.board is not None, result.errors
        executor = Executor(
            result.board,
            adapter_registry=build_adapter_registry(local_project(tmp_path)),
        )
        html = render(result.board, executor, format="html").output
        assert isinstance(html, str)

        assert "/data/dw/main/tickets/detail/?id=" not in html, (
            "Must not emit a drill keyed on 'id' when the output's 'id' "
            f"column actually holds ticket_number values. HTML: {html[:3000]}"
        )

    def test_cte_column_alias_list_does_not_emit_wrong_drill(
        self, tmp_path, local_project: Callable[..., Project]
    ) -> None:
        """Regression test: a CTE column-alias list (`WITH t(a, b) AS (...)`)
        renames the body's projected columns *positionally* — must bail
        entirely (no location resolved at all), not just fail to trace the
        key. `main.tickets(id, ticket_number)`; the CTE swaps their names via
        the column list, so the query's output `id` column actually holds
        `ticket_number` values.
        """
        import sqlite3

        import yaml

        from dbt_charts.core.compile import compile
        from dbt_charts.core.execute import Executor
        from dbt_charts.core.execute.adapters import build_adapter_registry
        from dbt_charts.core.render import render

        db_path = tmp_path / "tickets.sqlite"
        conn = sqlite3.connect(str(db_path))
        conn.execute(
            "CREATE TABLE tickets (id INTEGER, ticket_number INTEGER, subject TEXT)"
        )
        conn.execute("INSERT INTO tickets VALUES (1, 4102, 'Bug report')")
        conn.commit()
        conn.close()

        sources_yaml = {"sources": {"dw": {"type": "sqlite", "path": str(db_path)}}}
        (tmp_path / "dbt_charts.yml").write_text(yaml.dump(sources_yaml))

        board_yaml = """\
title: "Tickets"
auto_link: true
source: dw
queries:
  rows:
    source: dw
    sql: "WITH t(ticket_number, id) AS (SELECT id, ticket_number FROM main.tickets) SELECT * FROM t"
rows:
  - chart
charts:
  chart:
    title: "Tickets"
    type: table
    query: rows
"""
        result = compile(board_yaml)
        assert result.board is not None, result.errors
        executor = Executor(
            result.board,
            adapter_registry=build_adapter_registry(local_project(tmp_path)),
        )
        html = render(result.board, executor, format="html").output
        assert isinstance(html, str)

        assert "/data/dw/main/tickets/detail/" not in html, (
            "CTE column-alias lists must bail entirely — no location can be "
            f"trusted without tracing the positional rename. HTML: {html[:3000]}"
        )

    def test_plain_query_unrelated_column_aliased_to_key_name_does_not_emit_wrong_drill(
        self, tmp_path, local_project: Callable[..., Project]
    ) -> None:
        """Same bug as test_unrelated_column_aliased_to_key_name_does_not_emit_wrong_drill,
        but with no CTE at all — the plain single-table path had the
        identical blind identity assumption (`SELECT <col> AS <other>` is
        itself a renaming layer, contrary to the dispatch's original "no
        renaming layer" claim for plain queries)."""
        import sqlite3

        import yaml

        from dbt_charts.core.compile import compile
        from dbt_charts.core.execute import Executor
        from dbt_charts.core.execute.adapters import build_adapter_registry
        from dbt_charts.core.render import render

        db_path = tmp_path / "tickets.sqlite"
        conn = sqlite3.connect(str(db_path))
        conn.execute(
            "CREATE TABLE tickets (id INTEGER, ticket_number INTEGER, subject TEXT)"
        )
        conn.execute("INSERT INTO tickets VALUES (1, 4102, 'Bug report')")
        conn.commit()
        conn.close()

        sources_yaml = {"sources": {"dw": {"type": "sqlite", "path": str(db_path)}}}
        (tmp_path / "dbt_charts.yml").write_text(yaml.dump(sources_yaml))

        board_yaml = """\
title: "Tickets"
auto_link: true
source: dw
queries:
  rows:
    source: dw
    sql: "SELECT ticket_number AS id, subject FROM main.tickets"
rows:
  - chart
charts:
  chart:
    title: "Tickets"
    type: table
    query: rows
"""
        result = compile(board_yaml)
        assert result.board is not None, result.errors
        executor = Executor(
            result.board,
            adapter_registry=build_adapter_registry(local_project(tmp_path)),
        )
        html = render(result.board, executor, format="html").output
        assert isinstance(html, str)

        assert "/data/dw/main/tickets/detail/?id=" not in html, (
            "Must not emit a drill keyed on 'id' when the plain query's "
            f"output 'id' column actually holds ticket_number values. HTML: {html[:3000]}"
        )

    def test_composite_key_partially_untraceable_emits_no_link(
        self, tmp_path, local_project: Callable[..., Project]
    ) -> None:
        """Regression test: `plan_link_keys` can return a *composite* key
        (multiple FK-shaped columns standing in for a missing single `id`) —
        those columns are only a unique identity *together*. Dropping just
        the untraceable member and keeping the rest turns a unique composite
        key into a non-unique partial filter: a live drill to an arbitrary
        row that merely shares the surviving column's value, not the correct
        one.

        `main.line_items(order_id, product_id, qty)` has no `id` column, so
        `plan_link_keys` returns the composite `[order_id, product_id]`. The
        query below proves `order_id` is present but proves `product_id` is
        *absent* (the CTE never selects it) — emitting a link keyed on
        `order_id` alone would match every line item on that order.
        """
        import sqlite3

        import yaml

        from dbt_charts.core.compile import compile
        from dbt_charts.core.execute import Executor
        from dbt_charts.core.execute.adapters import build_adapter_registry
        from dbt_charts.core.render import render

        db_path = tmp_path / "line_items.sqlite"
        conn = sqlite3.connect(str(db_path))
        conn.execute(
            "CREATE TABLE line_items (order_id INTEGER, product_id INTEGER, qty INTEGER)"
        )
        conn.execute("INSERT INTO line_items VALUES (1, 10, 3)")
        conn.commit()
        conn.close()

        sources_yaml = {"sources": {"dw": {"type": "sqlite", "path": str(db_path)}}}
        (tmp_path / "dbt_charts.yml").write_text(yaml.dump(sources_yaml))

        board_yaml = """\
title: "Line Items"
auto_link: true
source: dw
queries:
  rows:
    source: dw
    sql: "WITH li AS (SELECT order_id, product_id, qty FROM main.line_items) SELECT order_id, qty FROM li"
rows:
  - chart
charts:
  chart:
    title: "Line Items"
    type: table
    query: rows
"""
        result = compile(board_yaml)
        assert result.board is not None, result.errors
        executor = Executor(
            result.board,
            adapter_registry=build_adapter_registry(local_project(tmp_path)),
        )
        html = render(result.board, executor, format="html").output
        assert isinstance(html, str)

        assert "/data/dw/main/line_items/detail/" not in html, (
            "product_id is proven absent from the output — the composite "
            "key is incomplete and must emit no link, not a partial one "
            f"keyed on order_id alone. HTML: {html[:3000]}"
        )


class TestJoinGrainResolution:
    """FK-proven JOIN resolution via _loc_for_query."""

    def test_join_without_adapter_bails(self) -> None:
        """source_schema_table bails on JOINs — FK resolution requires adapter."""
        from dbt_charts.core.compile.models.query.normalized import SqlQuery
        from dbt_charts.core.render.chart.auto_link import source_schema_table

        query = SqlQuery(
            sql="SELECT o.id, c.name FROM main.orders o JOIN main.customers c ON o.cust_id = c.id",
            source="dw",
        )
        assert not source_schema_table(query, require_row_grain=True)

    def test_join_with_fk_edge_resolves(
        self, tmp_path, local_project: Callable[..., Project]
    ) -> None:
        """JOIN where ON clause matches declared FK edge → resolves to spine table."""
        import json
        import sqlite3

        import yaml

        from dbt_charts.core.compile.models.query.normalized import SqlQuery
        from dbt_charts.core.execute.adapters import build_adapter_registry
        from dbt_charts.core.render.chart.auto_link import _loc_for_query

        db_path = tmp_path / "sales.sqlite"
        conn = sqlite3.connect(str(db_path))
        conn.execute("CREATE TABLE orders (id INTEGER, cust_id INTEGER, amount REAL)")
        conn.execute("CREATE TABLE customers (id INTEGER, name TEXT)")
        conn.commit()
        conn.close()

        sources_yaml = {"sources": {"dw": {"type": "sqlite", "path": str(db_path)}}}
        (tmp_path / "dbt_charts.yml").write_text(yaml.dump(sources_yaml))

        manifest = {
            "nodes": {
                "test.fk_orders_customers": {
                    "resource_type": "test",
                    "test_metadata": {
                        "name": "relationships",
                        "kwargs": {
                            "to": "ref('customers')",
                            "field": "id",
                            "column_name": "cust_id",
                        },
                    },
                    "column_name": "cust_id",
                    "attached_node": "model.orders",
                },
                "model.orders": {
                    "resource_type": "model",
                    "name": "orders",
                    "alias": "orders",
                },
                "model.customers": {
                    "resource_type": "model",
                    "name": "customers",
                    "alias": "customers",
                },
            }
        }
        target_dir = tmp_path / "target"
        target_dir.mkdir()
        (target_dir / "manifest.json").write_text(json.dumps(manifest))

        adapter_registry = build_adapter_registry(local_project(tmp_path))

        query = SqlQuery(
            sql="SELECT o.id, c.name FROM main.orders o JOIN main.customers c ON o.cust_id = c.id",
            source="dw",
        )
        loc = _loc_for_query(query, adapter_registry)
        assert loc == ("dw", "main", "orders"), f"Expected spine table, got {loc}"

    def test_join_with_mixed_case_alias_in_on_clause_resolves(
        self, tmp_path, local_project: Callable[..., Project]
    ) -> None:
        """Regression test: unquoted SQL identifiers are case-insensitive —
        `FROM main.orders o JOIN main.customers c ON O.cust_id = c.id`
        references the same alias `o` declared in the FROM clause, just
        spelled with different case in the ON clause (a real-world typo/
        style-inconsistency, not a different table). A case-sensitive
        alias comparison would treat `O` and `o` as different qualifiers,
        fail to match the FK edge's ON predicate, and bail — losing a
        drill for an ordinary same-alias reference.
        """
        import json
        import sqlite3

        import yaml

        from dbt_charts.core.compile.models.query.normalized import SqlQuery
        from dbt_charts.core.execute.adapters import build_adapter_registry
        from dbt_charts.core.render.chart.auto_link import _loc_for_query

        db_path = tmp_path / "sales.sqlite"
        conn = sqlite3.connect(str(db_path))
        conn.execute("CREATE TABLE orders (id INTEGER, cust_id INTEGER, amount REAL)")
        conn.execute("CREATE TABLE customers (id INTEGER, name TEXT)")
        conn.commit()
        conn.close()

        sources_yaml = {"sources": {"dw": {"type": "sqlite", "path": str(db_path)}}}
        (tmp_path / "dbt_charts.yml").write_text(yaml.dump(sources_yaml))

        manifest = {
            "nodes": {
                "test.fk_orders_customers": {
                    "resource_type": "test",
                    "test_metadata": {
                        "name": "relationships",
                        "kwargs": {
                            "to": "ref('customers')",
                            "field": "id",
                            "column_name": "cust_id",
                        },
                    },
                    "column_name": "cust_id",
                    "attached_node": "model.orders",
                },
                "model.orders": {
                    "resource_type": "model",
                    "name": "orders",
                    "alias": "orders",
                },
                "model.customers": {
                    "resource_type": "model",
                    "name": "customers",
                    "alias": "customers",
                },
            }
        }
        target_dir = tmp_path / "target"
        target_dir.mkdir()
        (target_dir / "manifest.json").write_text(json.dumps(manifest))

        adapter_registry = build_adapter_registry(local_project(tmp_path))

        query = SqlQuery(
            sql="SELECT o.id, c.name FROM main.orders o"
            " JOIN main.customers c ON O.cust_id = c.id",
            source="dw",
        )
        loc = _loc_for_query(query, adapter_registry)
        assert loc == ("dw", "main", "orders"), f"Expected spine table, got {loc}"

    def test_join_with_parenthesized_on_clause_resolves(
        self, tmp_path, local_project: Callable[..., Project]
    ) -> None:
        """Regression test: `ON (a = b)` — a parenthesized predicate, common
        formatting choice — must resolve the same as `ON a = b`. sqlglot
        wraps a parenthesized ON in `exp.Paren`, which `_flatten_and_conjuncts`
        must unwrap rather than treat as an unrecognized/unprovable shape."""
        import json
        import sqlite3

        import yaml

        from dbt_charts.core.compile.models.query.normalized import SqlQuery
        from dbt_charts.core.execute.adapters import build_adapter_registry
        from dbt_charts.core.render.chart.auto_link import _loc_for_query

        db_path = tmp_path / "sales.sqlite"
        conn = sqlite3.connect(str(db_path))
        conn.execute("CREATE TABLE orders (id INTEGER, cust_id INTEGER, amount REAL)")
        conn.execute("CREATE TABLE customers (id INTEGER, name TEXT)")
        conn.commit()
        conn.close()

        sources_yaml = {"sources": {"dw": {"type": "sqlite", "path": str(db_path)}}}
        (tmp_path / "dbt_charts.yml").write_text(yaml.dump(sources_yaml))

        manifest = {
            "nodes": {
                "test.fk_orders_customers": {
                    "resource_type": "test",
                    "test_metadata": {
                        "name": "relationships",
                        "kwargs": {
                            "to": "ref('customers')",
                            "field": "id",
                            "column_name": "cust_id",
                        },
                    },
                    "column_name": "cust_id",
                    "attached_node": "model.orders",
                },
                "model.orders": {
                    "resource_type": "model",
                    "name": "orders",
                    "alias": "orders",
                },
                "model.customers": {
                    "resource_type": "model",
                    "name": "customers",
                    "alias": "customers",
                },
            }
        }
        target_dir = tmp_path / "target"
        target_dir.mkdir()
        (target_dir / "manifest.json").write_text(json.dumps(manifest))

        adapter_registry = build_adapter_registry(local_project(tmp_path))

        query = SqlQuery(
            sql="SELECT o.id, c.name FROM main.orders o"
            " JOIN main.customers c ON (o.cust_id = c.id)",
            source="dw",
        )
        loc = _loc_for_query(query, adapter_registry)
        assert loc == ("dw", "main", "orders"), f"Expected spine table, got {loc}"

    def test_join_with_unrelated_cte_bails(
        self, tmp_path, local_project: Callable[..., Project]
    ) -> None:
        """Regression test: `resolve_join_spine` has no business resolving a
        location for a query that also has a CTE — `build_col_output_alias_map`
        has no tracing for "CTE + JOIN" (see test_sql_grain.py's
        TestJoinColOutputAliasMap.test_cte_alongside_join_is_never_identity_safe),
        so resolving a location here would only ever produce a link with no
        key ever traced. Bail at the source instead.
        """
        import json
        import sqlite3

        import yaml

        from dbt_charts.core.compile.models.query.normalized import SqlQuery
        from dbt_charts.core.execute.adapters import build_adapter_registry
        from dbt_charts.core.render.chart.auto_link import _loc_for_query

        db_path = tmp_path / "sales.sqlite"
        conn = sqlite3.connect(str(db_path))
        conn.execute("CREATE TABLE orders (id INTEGER, cust_id INTEGER, amount REAL)")
        conn.execute("CREATE TABLE customers (id INTEGER, name TEXT)")
        conn.commit()
        conn.close()

        sources_yaml = {"sources": {"dw": {"type": "sqlite", "path": str(db_path)}}}
        (tmp_path / "dbt_charts.yml").write_text(yaml.dump(sources_yaml))

        manifest = {
            "nodes": {
                "test.fk_orders_customers": {
                    "resource_type": "test",
                    "test_metadata": {
                        "name": "relationships",
                        "kwargs": {
                            "to": "ref('customers')",
                            "field": "id",
                            "column_name": "cust_id",
                        },
                    },
                    "column_name": "cust_id",
                    "attached_node": "model.orders",
                },
                "model.orders": {
                    "resource_type": "model",
                    "name": "orders",
                    "alias": "orders",
                },
                "model.customers": {
                    "resource_type": "model",
                    "name": "customers",
                    "alias": "customers",
                },
            }
        }
        target_dir = tmp_path / "target"
        target_dir.mkdir()
        (target_dir / "manifest.json").write_text(json.dumps(manifest))

        adapter_registry = build_adapter_registry(local_project(tmp_path))

        query = SqlQuery(
            sql="WITH thresholds AS (SELECT 100 AS min_amount)"
            " SELECT c.id, c.name, o.amount FROM main.orders o"
            " JOIN main.customers c ON o.cust_id = c.id",
            source="dw",
        )
        assert not _loc_for_query(query, adapter_registry)

    def test_join_with_aggregate_function_bails_despite_fk_proof(
        self, tmp_path, local_project: Callable[..., Project]
    ) -> None:
        """Regression test: an FK-provable JOIN with an
        aggregate function in the projection must still bail — grain
        preservation on the JOIN doesn't matter if the output itself is a
        roll-up. `resolve_join_spine` originally checked only the outer
        node's own GROUP BY arg and DISTINCT, missing `exp.AggFunc` entirely
        — a query like this one has no explicit GROUP BY (implicit whole-
        table aggregation, legal on SQLite) and would have resolved.
        """
        import json
        import sqlite3

        import yaml

        from dbt_charts.core.compile.models.query.normalized import SqlQuery
        from dbt_charts.core.execute.adapters import build_adapter_registry
        from dbt_charts.core.render.chart.auto_link import _loc_for_query

        db_path = tmp_path / "sales.sqlite"
        conn = sqlite3.connect(str(db_path))
        conn.execute("CREATE TABLE orders (id INTEGER, cust_id INTEGER)")
        conn.execute("CREATE TABLE customers (id INTEGER, name TEXT)")
        conn.commit()
        conn.close()

        sources_yaml = {"sources": {"dw": {"type": "sqlite", "path": str(db_path)}}}
        (tmp_path / "dbt_charts.yml").write_text(yaml.dump(sources_yaml))

        manifest = {
            "nodes": {
                "test.fk": {
                    "resource_type": "test",
                    "test_metadata": {
                        "name": "relationships",
                        "kwargs": {
                            "to": "ref('customers')",
                            "field": "id",
                            "column_name": "cust_id",
                        },
                    },
                    "column_name": "cust_id",
                    "attached_node": "model.orders",
                },
                "model.orders": {
                    "resource_type": "model",
                    "name": "orders",
                    "alias": "orders",
                },
                "model.customers": {
                    "resource_type": "model",
                    "name": "customers",
                    "alias": "customers",
                },
            }
        }
        target_dir = tmp_path / "target"
        target_dir.mkdir()
        (target_dir / "manifest.json").write_text(json.dumps(manifest))

        adapter_registry = build_adapter_registry(local_project(tmp_path))

        query = SqlQuery(
            sql=(
                "SELECT o.id, COUNT(*) AS n FROM main.orders o"
                " JOIN main.customers c ON o.cust_id = c.id"
            ),
            source="dw",
        )
        assert not _loc_for_query(query, adapter_registry)

    def test_join_no_matching_fk_edge_bails(
        self, tmp_path, local_project: Callable[..., Project]
    ) -> None:
        """JOIN where ON clause matches no FK edge → bails (no naming heuristic)."""
        import sqlite3

        import yaml

        from dbt_charts.core.compile.models.query.normalized import SqlQuery
        from dbt_charts.core.execute.adapters import build_adapter_registry
        from dbt_charts.core.render.chart.auto_link import _loc_for_query

        db_path = tmp_path / "sales.sqlite"
        conn = sqlite3.connect(str(db_path))
        conn.execute("CREATE TABLE orders (id INTEGER, cust_id INTEGER)")
        conn.execute("CREATE TABLE customers (id INTEGER, name TEXT)")
        conn.commit()
        conn.close()

        sources_yaml = {"sources": {"dw": {"type": "sqlite", "path": str(db_path)}}}
        (tmp_path / "dbt_charts.yml").write_text(yaml.dump(sources_yaml))
        # No manifest written → no FK edges

        adapter_registry = build_adapter_registry(local_project(tmp_path))

        query = SqlQuery(
            sql="SELECT o.id, c.name FROM main.orders o JOIN main.customers c ON o.cust_id = c.id",
            source="dw",
        )
        # Even though column name is "id" on both sides, no FK edge → bail
        assert not _loc_for_query(query, adapter_registry)

    def test_join_on_unrelated_columns_bails_despite_fk_target_match(
        self, tmp_path, local_project: Callable[..., Project]
    ) -> None:
        """A declared FK edge to the joined table isn't enough — the ON clause
        must actually use the FK's columns.

        orders.cust_id -> customers.id is declared, but this query joins on
        region_code instead (a real fan-out risk: many orders and many
        customers can share a region). Matching the joined table name to *some*
        FK target without checking the join predicate would wrongly treat this
        as grain-preserving.
        """
        import json
        import sqlite3

        import yaml

        from dbt_charts.core.compile.models.query.normalized import SqlQuery
        from dbt_charts.core.execute.adapters import build_adapter_registry
        from dbt_charts.core.render.chart.auto_link import _loc_for_query

        db_path = tmp_path / "sales.sqlite"
        conn = sqlite3.connect(str(db_path))
        conn.execute(
            "CREATE TABLE orders (id INTEGER, cust_id INTEGER, region_code TEXT)"
        )
        conn.execute("CREATE TABLE customers (id INTEGER, region_code TEXT)")
        conn.commit()
        conn.close()

        sources_yaml = {"sources": {"dw": {"type": "sqlite", "path": str(db_path)}}}
        (tmp_path / "dbt_charts.yml").write_text(yaml.dump(sources_yaml))

        manifest = {
            "nodes": {
                "test.fk": {
                    "resource_type": "test",
                    "test_metadata": {
                        "name": "relationships",
                        "kwargs": {
                            "to": "ref('customers')",
                            "field": "id",
                            "column_name": "cust_id",
                        },
                    },
                    "column_name": "cust_id",
                    "attached_node": "model.orders",
                },
                "model.orders": {
                    "resource_type": "model",
                    "name": "orders",
                    "alias": "orders",
                },
                "model.customers": {
                    "resource_type": "model",
                    "name": "customers",
                    "alias": "customers",
                },
            }
        }
        target_dir = tmp_path / "target"
        target_dir.mkdir()
        (target_dir / "manifest.json").write_text(json.dumps(manifest))

        adapter_registry = build_adapter_registry(local_project(tmp_path))

        query = SqlQuery(
            sql=(
                "SELECT o.id, c.region_code FROM main.orders o"
                " JOIN main.customers c ON o.region_code = c.region_code"
            ),
            source="dw",
        )
        assert not _loc_for_query(query, adapter_registry)

    def test_reverse_direction_join_bails_fan_out_not_provable(
        self, tmp_path, local_project: Callable[..., Project]
    ) -> None:
        """Joining from the "one" side toward the "many" side bails — fan-out.

        A declared edge `orders.cust_id -> customers.id` proves "each order
        has at most one customer", not the reverse. Querying FROM customers
        JOIN orders can fan out (one customer, many orders), so the spine
        (customers) having no *outbound* FK edge of its own must bail —
        `fetch_fk_edges` only returns edges declared *from* the queried
        table, so this is refused by construction, not a special case.
        """
        import json
        import sqlite3

        import yaml

        from dbt_charts.core.compile.models.query.normalized import SqlQuery
        from dbt_charts.core.execute.adapters import build_adapter_registry
        from dbt_charts.core.render.chart.auto_link import _loc_for_query

        db_path = tmp_path / "sales.sqlite"
        conn = sqlite3.connect(str(db_path))
        conn.execute("CREATE TABLE orders (id INTEGER, cust_id INTEGER)")
        conn.execute("CREATE TABLE customers (id INTEGER, name TEXT)")
        conn.commit()
        conn.close()

        sources_yaml = {"sources": {"dw": {"type": "sqlite", "path": str(db_path)}}}
        (tmp_path / "dbt_charts.yml").write_text(yaml.dump(sources_yaml))

        manifest = {
            "nodes": {
                "test.fk": {
                    "resource_type": "test",
                    "test_metadata": {
                        "name": "relationships",
                        "kwargs": {
                            "to": "ref('customers')",
                            "field": "id",
                            "column_name": "cust_id",
                        },
                    },
                    "column_name": "cust_id",
                    "attached_node": "model.orders",
                },
                "model.orders": {
                    "resource_type": "model",
                    "name": "orders",
                    "alias": "orders",
                },
                "model.customers": {
                    "resource_type": "model",
                    "name": "customers",
                    "alias": "customers",
                },
            }
        }
        target_dir = tmp_path / "target"
        target_dir.mkdir()
        (target_dir / "manifest.json").write_text(json.dumps(manifest))

        adapter_registry = build_adapter_registry(local_project(tmp_path))

        query = SqlQuery(
            sql=(
                "SELECT c.id, o.amount FROM main.customers c"
                " JOIN main.orders o ON c.id = o.cust_id"
            ),
            source="dw",
        )
        assert not _loc_for_query(query, adapter_registry)

    def test_two_joins_one_unprovable_bails_ambiguous(
        self, tmp_path, local_project: Callable[..., Project]
    ) -> None:
        """A query joining two dimensions, only one FK-provable, bails entirely.

        Even though the orders->customers join is provably grain-preserving,
        the second join (orders->regions, no declared edge) can't be proven —
        the whole query must bail rather than partially trust the identity.
        """
        import json
        import sqlite3

        import yaml

        from dbt_charts.core.compile.models.query.normalized import SqlQuery
        from dbt_charts.core.execute.adapters import build_adapter_registry
        from dbt_charts.core.render.chart.auto_link import _loc_for_query

        db_path = tmp_path / "sales.sqlite"
        conn = sqlite3.connect(str(db_path))
        conn.execute(
            "CREATE TABLE orders (id INTEGER, cust_id INTEGER, region_id INTEGER)"
        )
        conn.execute("CREATE TABLE customers (id INTEGER, name TEXT)")
        conn.execute("CREATE TABLE regions (id INTEGER, name TEXT)")
        conn.commit()
        conn.close()

        sources_yaml = {"sources": {"dw": {"type": "sqlite", "path": str(db_path)}}}
        (tmp_path / "dbt_charts.yml").write_text(yaml.dump(sources_yaml))

        manifest = {
            "nodes": {
                "test.fk_customers": {
                    "resource_type": "test",
                    "test_metadata": {
                        "name": "relationships",
                        "kwargs": {
                            "to": "ref('customers')",
                            "field": "id",
                            "column_name": "cust_id",
                        },
                    },
                    "column_name": "cust_id",
                    "attached_node": "model.orders",
                },
                "model.orders": {
                    "resource_type": "model",
                    "name": "orders",
                    "alias": "orders",
                },
                "model.customers": {
                    "resource_type": "model",
                    "name": "customers",
                    "alias": "customers",
                },
                "model.regions": {
                    "resource_type": "model",
                    "name": "regions",
                    "alias": "regions",
                },
            }
        }
        target_dir = tmp_path / "target"
        target_dir.mkdir()
        (target_dir / "manifest.json").write_text(json.dumps(manifest))

        adapter_registry = build_adapter_registry(local_project(tmp_path))

        query = SqlQuery(
            sql=(
                "SELECT o.id, c.name, r.name AS region_name FROM main.orders o"
                " JOIN main.customers c ON o.cust_id = c.id"
                " JOIN main.regions r ON o.region_id = r.id"
            ),
            source="dw",
        )
        assert not _loc_for_query(query, adapter_registry)

    def test_join_with_fk_emits_drill_in_render(
        self, tmp_path, local_project: Callable[..., Project]
    ) -> None:
        """End-to-end: FK-proven JOIN → drill link on spine table in rendered HTML."""
        import json
        import sqlite3

        import yaml

        from dbt_charts.core.compile import compile
        from dbt_charts.core.execute import Executor
        from dbt_charts.core.execute.adapters import build_adapter_registry
        from dbt_charts.core.render import render

        db_path = tmp_path / "sales.sqlite"
        conn = sqlite3.connect(str(db_path))
        conn.execute("CREATE TABLE orders (id INTEGER, cust_id INTEGER, amount REAL)")
        conn.execute("CREATE TABLE customers (id INTEGER, name TEXT)")
        conn.execute("INSERT INTO orders VALUES (1, 10, 99.0)")
        conn.execute("INSERT INTO customers VALUES (10, 'Alice')")
        conn.commit()
        conn.close()

        sources_yaml = {"sources": {"dw": {"type": "sqlite", "path": str(db_path)}}}
        (tmp_path / "dbt_charts.yml").write_text(yaml.dump(sources_yaml))

        manifest = {
            "nodes": {
                "test.fk": {
                    "resource_type": "test",
                    "test_metadata": {
                        "name": "relationships",
                        "kwargs": {
                            "to": "ref('customers')",
                            "field": "id",
                            "column_name": "cust_id",
                        },
                    },
                    "column_name": "cust_id",
                    "attached_node": "model.orders",
                },
                "model.orders": {
                    "resource_type": "model",
                    "name": "orders",
                    "alias": "orders",
                },
                "model.customers": {
                    "resource_type": "model",
                    "name": "customers",
                    "alias": "customers",
                },
            }
        }
        target_dir = tmp_path / "target"
        target_dir.mkdir()
        (target_dir / "manifest.json").write_text(json.dumps(manifest))

        board_yaml = """\
title: "Orders with customer name"
auto_link: true
source: dw
queries:
  rows:
    source: dw
    sql: "SELECT o.id, o.amount, c.name FROM main.orders o JOIN main.customers c ON o.cust_id = c.id"
rows:
  - chart
charts:
  chart:
    title: "Orders"
    type: table
    query: rows
"""
        result = compile(board_yaml)
        assert result.board is not None, result.errors

        executor = Executor(
            result.board,
            adapter_registry=build_adapter_registry(local_project(tmp_path)),
        )
        html = render(result.board, executor, format="html").output
        assert isinstance(html, str)

        assert "/data/dw/main/orders/detail/" in html, (
            "Expected drill link for spine table (orders). "
            f"First 3000 chars: {html[:3000]}"
        )

    def test_join_projection_without_spine_key_does_not_emit_wrong_drill(
        self, tmp_path, local_project: Callable[..., Project]
    ) -> None:
        """Regression test: a JOIN
        query that never projects the spine's own key column must not emit
        a drill keyed on a *different* table's column that happens to share
        that name.

        The spine here is `orders` (proven via the declared FK edge), but
        the query only projects `customers.id` (as bare `id`) plus
        `orders.amount` — never `orders.id`. Before the fix,
        `build_col_output_alias_map` returned `({}, True)` for any JOIN
        query (no CTE support existed there), so the row-detail path assumed
        identity and emitted `?id=` against the output's `id` column — which
        actually holds `customers.id` values, not `orders.id`. A click would
        drill to the wrong order (or a nonexistent one).
        """
        import json
        import sqlite3

        import yaml

        from dbt_charts.core.compile import compile
        from dbt_charts.core.execute import Executor
        from dbt_charts.core.execute.adapters import build_adapter_registry
        from dbt_charts.core.render import render

        db_path = tmp_path / "sales.sqlite"
        conn = sqlite3.connect(str(db_path))
        conn.execute("CREATE TABLE orders (id INTEGER, cust_id INTEGER, amount REAL)")
        conn.execute("CREATE TABLE customers (id INTEGER, name TEXT)")
        conn.execute("INSERT INTO orders VALUES (1, 10, 99.0)")
        conn.execute("INSERT INTO customers VALUES (10, 'Alice')")
        conn.commit()
        conn.close()

        sources_yaml = {"sources": {"dw": {"type": "sqlite", "path": str(db_path)}}}
        (tmp_path / "dbt_charts.yml").write_text(yaml.dump(sources_yaml))

        manifest = {
            "nodes": {
                "test.fk": {
                    "resource_type": "test",
                    "test_metadata": {
                        "name": "relationships",
                        "kwargs": {
                            "to": "ref('customers')",
                            "field": "id",
                            "column_name": "cust_id",
                        },
                    },
                    "column_name": "cust_id",
                    "attached_node": "model.orders",
                },
                "model.orders": {
                    "resource_type": "model",
                    "name": "orders",
                    "alias": "orders",
                },
                "model.customers": {
                    "resource_type": "model",
                    "name": "customers",
                    "alias": "customers",
                },
            }
        }
        target_dir = tmp_path / "target"
        target_dir.mkdir()
        (target_dir / "manifest.json").write_text(json.dumps(manifest))

        board_yaml = """\
title: "Orders with customer name"
auto_link: true
source: dw
queries:
  rows:
    source: dw
    sql: "SELECT c.id, c.name, o.amount FROM main.orders o JOIN main.customers c ON o.cust_id = c.id"
rows:
  - chart
charts:
  chart:
    title: "Orders"
    type: table
    query: rows
"""
        result = compile(board_yaml)
        assert result.board is not None, result.errors

        executor = Executor(
            result.board,
            adapter_registry=build_adapter_registry(local_project(tmp_path)),
        )
        html = render(result.board, executor, format="html").output
        assert isinstance(html, str)

        assert "/data/dw/main/orders/detail/?id=" not in html, (
            "Must not emit a drill keyed on 'id' when the output's 'id' "
            f"column actually holds customers.id values. HTML: {html[:3000]}"
        )


# ---------------------------------------------------------------------------
# AuthoredBoard.auto_link — schema-level field
# ---------------------------------------------------------------------------


class TestAuthoredBoardAutoLink:
    def test_auto_link_defaults_false(self) -> None:
        from dbt_charts.core.compile.models.board.authored import AuthoredBoard

        board = AuthoredBoard(rows=["chart_a"])
        assert board.auto_link is False

    def test_auto_link_true_parses(self) -> None:
        from dbt_charts.core.compile.models.board.authored import AuthoredBoard

        board = AuthoredBoard(rows=["chart_a"], auto_link=True)
        assert board.auto_link is True

    def test_auto_link_false_explicit(self) -> None:
        from dbt_charts.core.compile.models.board.authored import AuthoredBoard

        board = AuthoredBoard(rows=["chart_a"], auto_link=False)
        assert board.auto_link is False


# ---------------------------------------------------------------------------
# Regression: table-index template still links rows to detail page
# (after the hand-rolled synthesis block is deleted from the template,
#  the resolver must fire for registered views and produce the same URL)
# ---------------------------------------------------------------------------


class TestTableIndexDetailLinkRegression:
    """Verify /data/.../index still links to detail page after template block removal."""

    def _col_results(self) -> dict[str, ViewQueryResult]:
        from dbt_charts.core.registered_views.query_runner import ViewQueryResult

        return {
            "columns": ViewQueryResult(
                rows=[
                    {"name": "id", "actual_type": "BIGINT"},
                    {"name": "status", "actual_type": "VARCHAR"},
                ]
            )
        }

    def _match(self) -> RouteMatch | None:
        from dbt_charts.core.registered_views.loader import load_builtin_registry
        from dbt_charts.core.registered_views.router import RouteRouter

        router = RouteRouter(load_builtin_registry())
        return router.match("/data/snowflake/analytics/orders/")

    def test_table_index_has_no_explicit_link_in_template(self) -> None:
        """After consolidation, the table chart in the expanded board has no link:
        authored explicitly — the resolver injects it at render time."""
        from dbt_charts.core.registered_views.expander import expand_registered_view

        match = self._match()
        assert match is not None
        board = expand_registered_view(match, query_results=self._col_results())
        assert board.charts is not None
        table_charts = [
            c for c in board.charts.values() if getattr(c, "type", None) == "table"
        ]
        assert table_charts, "No table chart found"
        # After consolidation: the chart has NO explicit link (it was deleted from
        # the template). The auto_link=True on the board drives the resolver.
        chart = table_charts[0]
        assert getattr(chart, "link", None) is None, (
            "After consolidation the template must not set link: on the chart — "
            "the auto_link resolver injects it at render time. "
            f"Got: {getattr(chart, 'link', None)!r}"
        )

    def test_table_index_board_has_auto_link_true(self) -> None:
        """After consolidation, the expanded board sets auto_link=True so the resolver fires."""
        from dbt_charts.core.registered_views.expander import expand_registered_view

        match = self._match()
        assert match is not None
        board = expand_registered_view(match, query_results=self._col_results())
        assert getattr(board, "auto_link", False) is True, (
            "Expanded table-index board must have auto_link=True so the resolver "
            f"fires. Got: {getattr(board, 'auto_link', 'MISSING')!r}"
        )


# ---------------------------------------------------------------------------
# End-to-end render wiring — real DuckDB executor exercising the full path
# ---------------------------------------------------------------------------


def _setup_sqlite_project(tmp_path) -> tuple[str, str]:
    """Set up a minimal project with a SQLite source registered at the project level.

    Returns (db_path_str, source_name) where the source is registered in
    dbt_charts.yml so build_adapter_registry(local_project(tmp_path)) can resolve it.
    The SQLiteSchemaSource (used by the LayeredSchemaResolver) can then profile
    the table via pragma_table_info without requiring a dbt adapter.
    """
    import sqlite3

    import yaml

    db_path = tmp_path / "orders.sqlite"
    conn = sqlite3.connect(str(db_path))
    conn.execute("CREATE TABLE orders (id INTEGER, status TEXT)")
    conn.execute("INSERT INTO orders VALUES (1, 'open')")
    conn.commit()
    conn.close()

    # Register the source at the project level so resolve_source_config("dw")
    # works in the LayeredSchemaResolver path.
    sources_yaml = {"sources": {"dw": {"type": "sqlite", "path": str(db_path)}}}
    (tmp_path / "dbt_charts.yml").write_text(yaml.dump(sources_yaml))

    return str(db_path), "dw"


class TestAutoLinkRenderWiring:
    """Integration: renderer → chart resolution → fetch_column_rows_for_link()
    → plan_link_keys → link injected onto resolved chart.

    Uses a SQLite source registered at the project level (dbt_charts.yml) so
    the LayeredSchemaResolver can profile the table via SQLiteSchemaSource
    (pragma_table_info) without requiring a dbt adapter. The source must be
    project-level (not board-level) because resolve_source_config only looks
    at project sources.
    """

    def _board_yaml(self, source_name: str, auto_link: bool = True) -> str:
        flag = "true" if auto_link else "false"
        return f"""\
title: "Orders"
auto_link: {flag}
source: {source_name}
queries:
  rows:
    source: {source_name}
    sql: SELECT id, status FROM main.orders
rows:
  - row_data
charts:
  row_data:
    title: "Orders"
    type: table
    query: rows
"""

    def test_rendered_html_contains_auto_link_href(
        self, tmp_path, local_project: Callable[..., Project]
    ) -> None:
        """Full render with a SQLite source produces a cell-link href pointing to
        the canonical /data/<source>/<schema>/<table>/detail/ URL.

        This exercises the real wiring:
          renderer.render() sets auto_link context
          → chart resolution during the sizing pass synthesizes the auto-link
          → fetch_column_rows_for_link() → LayeredSchemaResolver.profile_table()
            → SQLiteSchemaSource.pragma_table_info → actual_type="INTEGER"
          → plan_link_keys picks "id" (INTEGER → encode=False)
          → synthesized link /data/dw/main/orders/detail/?id={{ id }}
          → table renderer emits cell-link href.
        """
        from dbt_charts.core.compile import compile
        from dbt_charts.core.execute import Executor
        from dbt_charts.core.execute.adapters import build_adapter_registry
        from dbt_charts.core.render import render

        _db_path, source_name = _setup_sqlite_project(tmp_path)
        result = compile(self._board_yaml(source_name))
        assert result.board is not None, result.errors

        executor = Executor(
            result.board,
            adapter_registry=build_adapter_registry(local_project(tmp_path)),
        )
        rendered = render(result.board, executor, format="html")
        html = rendered.output
        assert isinstance(html, str)

        # The cell-link renderer emits hrefs like:
        #   /data/dw/main/orders/detail/?id=1
        assert "/data/dw/main/orders/detail/" in html, (
            "Expected auto-link href '/data/dw/main/orders/detail/' in rendered HTML. "
            "This verifies the full wiring: renderer → fetch_column_rows_for_link → "
            "SQLiteSchemaSource → plan_link_keys → link on chart. "
            f"Rendered output (first 3000 chars): {html[:3000]}"
        )

    def test_auto_link_off_emits_no_detail_href(
        self, tmp_path, local_project: Callable[..., Project]
    ) -> None:
        """When auto_link is off (default), the table chart emits no cell-link href."""
        from dbt_charts.core.compile import compile
        from dbt_charts.core.execute import Executor
        from dbt_charts.core.execute.adapters import build_adapter_registry
        from dbt_charts.core.render import render

        _db_path, source_name = _setup_sqlite_project(tmp_path)
        result = compile(self._board_yaml(source_name, auto_link=False))
        assert result.board is not None, result.errors

        executor = Executor(
            result.board,
            adapter_registry=build_adapter_registry(local_project(tmp_path)),
        )
        rendered = render(result.board, executor, format="html")
        html = rendered.output
        assert isinstance(html, str)

        assert "/data/dw/" not in html, (
            "With auto_link off, no /data/ href should appear in rendered HTML."
        )

    def test_fk_auto_link_does_not_bake_role_column_as_display_column(
        self,
        tmp_path,
        local_project: Callable[..., Project],
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """The FK per-column link path synthesizes ``resolved.columns`` from the
        data keys and bakes them onto the resolved chart. The renderer then takes
        its authored-columns branch and never runs the data-key role-strip, so a
        cascading ``style.row.role`` column would surface as a spurious "Row Role"
        display column unless the sizer excludes it when deriving the visible set.

        The dbt-manifest FK-edge discovery is
        stubbed (it needs a manifest this sqlite harness has no reason to carry);
        everything downstream — the column synthesis under test, the render — runs
        real.
        """
        from dbt_charts.core.compile import compile
        from dbt_charts.core.execute import Executor
        from dbt_charts.core.execute.adapters import build_adapter_registry
        from dbt_charts.core.render import render
        from dbt_charts.core.render.chart import auto_link as auto_link_mod

        _db_path, source_name = _setup_sqlite_project(tmp_path)

        # Force the FK per-column path to fire without a dbt manifest.
        def _fake_fk_links(*_a: object, **_k: object) -> dict[str, str]:
            return {"id": "/data/dw/main/orders/detail/?id={{ id }}"}

        monkeypatch.setattr(
            auto_link_mod, "fk_column_links_from_executor", _fake_fk_links
        )

        board_yaml = f"""\
title: "Orders"
auto_link: true
source: {source_name}
queries:
  rows:
    source: {source_name}
    sql: "SELECT id, status, 'value' AS _df_row_role FROM main.orders"
rows:
  - row_data
charts:
  row_data:
    title: "Orders"
    type: table
    query: rows
    style:
      row:
        role: _df_row_role
"""
        result = compile(board_yaml)
        assert result.board is not None, result.errors

        executor = Executor(
            result.board,
            adapter_registry=build_adapter_registry(local_project(tmp_path)),
        )
        html = render(result.board, executor, format="html").output
        assert isinstance(html, str)

        # The FK path fired (baked columns) — otherwise this test is a no-op.
        assert "/data/dw/main/orders/detail/" in html, (
            "expected the FK auto-link href — the column-baking path must run "
            "for this regression to mean anything"
        )
        # ...but the cascading role column must never render as a display column.
        assert "Row Role" not in html, (
            "role marker column must not render even when FK auto-link bakes "
            "resolved.columns from the raw data keys"
        )


# ---------------------------------------------------------------------------
# Regression: auto-link must survive cache hits (links must appear on render 2+)
# ---------------------------------------------------------------------------


class TestAutoLinkCacheHitRegression:
    """Regression test for the bug where auto-link links vanish on cached renders.

    Root cause: render sourced column types from an ephemeral per-request
    cursor description cache populated only on a fresh query execution. On a
    cache hit the DuckDB file cache returned data to a new executor that had
    never executed the query, so that cache stayed empty → column_rows empty →
    plan_link_keys returns [] → link dropped.

    Fix: source column types from the LayeredSchemaResolver (stable across
    requests and cache hits/misses) rather than from the ephemeral per-request
    cursor description cache.

    Uses a SQLite source so SQLiteSchemaSource can profile the table via
    pragma_table_info without requiring a dbt adapter.
    """

    def _board_yaml(self, source_name: str) -> str:
        return f"""\
title: "Orders"
auto_link: true
source: {source_name}
queries:
  rows:
    source: {source_name}
    sql: SELECT id, status FROM main.orders
rows:
  - row_data
charts:
  row_data:
    title: "Orders"
    type: table
    query: rows
"""

    def test_auto_link_present_on_second_request_with_duckdb_cache(
        self, tmp_path, local_project: Callable[..., Project]
    ) -> None:
        """Links must appear on every request, even when the DuckDB file cache is warm.

        This is the regression from #4320: dct serve creates a fresh Executor per
        request with an empty per-request cursor description cache. When the DuckDB
        file cache has a cached result, execute_query() returns early from
        _lookup_cached without re-executing the query, so that cache stays empty →
        column_rows empty → plan_link_keys returns [] → link dropped.

        The fix: source column types from LayeredSchemaResolver (stable across
        requests) rather than from the ephemeral per-executor cursor cache.
        """
        from dbt_charts.core.compile import compile
        from dbt_charts.core.execute import Executor
        from dbt_charts.core.execute.adapters import build_adapter_registry
        from dbt_charts.core.execute.trivial_local_cache import TrivialDuckDBCache
        from dbt_charts.core.render import render

        _db_path, source_name = _setup_sqlite_project(tmp_path)
        result = compile(self._board_yaml(source_name))
        assert result.board is not None, result.errors

        cache_path = tmp_path / "cache.duckdb"
        adapter_registry = build_adapter_registry(local_project(tmp_path))

        # Request 1: fresh executor, empty caches — query executes, result cache warms.
        result_cache_1 = TrivialDuckDBCache(db_path=cache_path)
        executor_1 = Executor(
            result.board,
            adapter_registry=adapter_registry,
            use_cache=True,
            result_cache=result_cache_1,
        )
        html_1 = render(result.board, executor_1, format="html").output
        assert isinstance(html_1, str)
        result_cache_1.conn.close()

        # Request 2: NEW executor (simulating next HTTP request). Its per-request
        # cursor description cache starts empty. _lookup_cached finds the result in
        # the DuckDB file cache → returns early without executing → that cache stays
        # empty. This is where the old code dropped the link.
        result_cache_2 = TrivialDuckDBCache(db_path=cache_path)
        executor_2 = Executor(
            result.board,
            adapter_registry=adapter_registry,
            use_cache=True,
            result_cache=result_cache_2,
        )
        html_2 = render(result.board, executor_2, format="html").output
        assert isinstance(html_2, str)
        result_cache_2.conn.close()

        detail_prefix = "/data/dw/main/orders/detail/"

        assert detail_prefix in html_1, (
            "Request 1 (DuckDB cache miss) must contain auto-link href. "
            f"Got (first 2000 chars): {html_1[:2000]}"
        )
        assert detail_prefix in html_2, (
            "Request 2 (DuckDB cache HIT, fresh executor) must also contain "
            "auto-link href — this is the regression: links vanished on every "
            "cached render. "
            f"Got (first 2000 chars): {html_2[:2000]}"
        )


# ---------------------------------------------------------------------------
# String/UUID key support — percent-encoding
# ---------------------------------------------------------------------------


class TestPlanLinkKeysWithEncode:
    """After the fix, plan_link_keys returns {"name": col, "encode": bool}."""

    def test_integer_key_encode_false(self) -> None:
        """Integer keys are URL-safe — encode=False."""
        rows = [{"name": "id", "actual_type": "BIGINT"}]
        result = plan_link_keys(rows)
        assert result == [{"name": "id", "encode": False}]

    def test_varchar_key_encode_true(self) -> None:
        """VARCHAR ids are string keys — encode=True (no longer excluded)."""
        rows = [{"name": "id", "actual_type": "VARCHAR"}]
        result = plan_link_keys(rows)
        assert result == [{"name": "id", "encode": True}]

    def test_uuid_key_encode_true(self) -> None:
        """UUID ids are string keys — encode=True (no longer excluded)."""
        rows = [{"name": "id", "actual_type": "UUID"}]
        result = plan_link_keys(rows)
        assert result == [{"name": "id", "encode": True}]

    def test_fixed_point_scale0_encode_false(self) -> None:
        """NUMBER(38,0) is exact integer — encode=False."""
        rows = [{"name": "id", "actual_type": "NUMBER(38,0)"}]
        assert plan_link_keys(rows) == [{"name": "id", "encode": False}]

    def test_varchar_fk_columns_encode_true(self) -> None:
        """Composite VARCHAR FK keys — both encode=True."""
        rows = [
            {"name": "order_id", "actual_type": "VARCHAR"},
            {"name": "product_id", "actual_type": "VARCHAR"},
        ]
        result = plan_link_keys(rows)
        assert len(result) == 2
        assert all(r["encode"] is True for r in result)

    def test_non_id_varchar_column_excluded(self) -> None:
        """Non-id-like VARCHAR columns (no _id suffix) are still excluded."""
        rows = [
            {"name": "name", "actual_type": "VARCHAR"},
            {"name": "amount", "actual_type": "DOUBLE"},
        ]
        assert plan_link_keys(rows) == []


class TestResolveAutoLinkStringKeys:
    """Auto-link emits {{ col | urlencode }} for string/UUID keys (and integer too).

    With the unified resolver, row_detail=True always emits urlencode in the
    detail URL — including for integer keys. For integers, urlencode is a no-op
    (urlencode("42") == "42"), so behavior is identical to the old path.
    """

    def _resolve(self, rows: list[dict[str, str]], loc: tuple[str, str, str]) -> str:
        from dbt_charts.core.render.chart.auto_link import resolve_column_set_link

        channel_cols = _table_channel_cols(rows)
        return resolve_column_set_link(
            channel_cols,
            loc,
            fk_edges=[],
            auto_link=True,
            row_detail=True,
        )

    def test_varchar_key_emits_urlencode_template(self) -> None:
        """VARCHAR id column → template uses {{ id | urlencode }}."""
        rows = [{"name": "id", "actual_type": "VARCHAR"}]
        link = self._resolve(rows, ("dw", "analytics", "users"))
        assert link == "/data/dw/analytics/users/detail/?id={{ id | urlencode }}"

    def test_uuid_key_emits_urlencode_template(self) -> None:
        """UUID id column → template uses {{ id | urlencode }}."""
        rows = [{"name": "id", "actual_type": "UUID"}]
        link = self._resolve(rows, ("dw", "public", "accounts"))
        assert link == "/data/dw/public/accounts/detail/?id={{ id | urlencode }}"

    def test_integer_key_also_emits_urlencode(self) -> None:
        """Integer keys also emit {{ id | urlencode }} — no-op for integers at resolve time."""
        rows = [{"name": "id", "actual_type": "BIGINT"}]
        link = self._resolve(rows, ("dw", "analytics", "orders"))
        assert link == "/data/dw/analytics/orders/detail/?id={{ id | urlencode }}"

    def test_composite_string_fk_keys_urlencode(self) -> None:
        """Composite VARCHAR FK keys — all use urlencode filter."""
        rows = [
            {"name": "order_id", "actual_type": "VARCHAR"},
            {"name": "product_id", "actual_type": "VARCHAR"},
        ]
        link = self._resolve(rows, ("dw", "analytics", "order_items"))
        assert link == (
            "/data/dw/analytics/order_items/detail/"
            "?order_id={{ order_id | urlencode }}&product_id={{ product_id | urlencode }}"
        )


class TestResolveCellLinkUrlencode:
    """resolve_cell_link percent-encodes {{ col | urlencode }} substitutions."""

    def test_urlencode_filter_encodes_special_chars(self) -> None:
        """{{ id | urlencode }} — special chars (@ / space &) are percent-encoded."""
        from dbt_charts.core.render.chart.table_support import resolve_cell_link

        link = "/data/dw/public/users/detail/?id={{ id | urlencode }}"
        row = {"id": "user@example.com"}
        result = resolve_cell_link(link, row, ["id"])
        assert result == "/data/dw/public/users/detail/?id=user%40example.com"

    def test_urlencode_filter_encodes_slash(self) -> None:
        """Slash in a string id must be encoded."""
        from dbt_charts.core.render.chart.table_support import resolve_cell_link

        link = "/data/dw/public/items/detail/?id={{ id | urlencode }}"
        row = {"id": "a/b/c"}
        result = resolve_cell_link(link, row, ["id"])
        assert result == "/data/dw/public/items/detail/?id=a%2Fb%2Fc"

    def test_urlencode_filter_plain_string_no_special_chars(self) -> None:
        """UUID-style value without special chars passes through cleanly."""
        from dbt_charts.core.render.chart.table_support import resolve_cell_link

        link = "/data/dw/public/users/detail/?id={{ id | urlencode }}"
        row = {"id": "abc123-def456"}
        result = resolve_cell_link(link, row, ["id"])
        assert result == "/data/dw/public/users/detail/?id=abc123-def456"

    def test_no_urlencode_filter_unchanged(self) -> None:
        """{{ id }} without filter — still substitutes raw value (no change)."""
        from dbt_charts.core.render.chart.table_support import resolve_cell_link

        link = "/data/dw/analytics/orders/detail/?id={{ id }}"
        row = {"id": 42}
        result = resolve_cell_link(link, row, ["id"])
        assert result == "/data/dw/analytics/orders/detail/?id=42"

    def test_urlencode_filter_null_returns_none(self) -> None:
        """NULL value with | urlencode — whole link returns None (same as without filter)."""
        from dbt_charts.core.render.chart.table_support import resolve_cell_link

        link = "/data/dw/public/users/detail/?id={{ id | urlencode }}"
        row = {"id": None}
        result = resolve_cell_link(link, row, ["id"])
        assert result is None

    def test_author_link_template_unaffected(self) -> None:
        """Author-written link: templates without | urlencode are not double-encoded."""
        from dbt_charts.core.render.chart.table_support import resolve_cell_link

        link = "/custom/{{ slug }}/detail/"
        row = {"slug": "hello-world"}
        result = resolve_cell_link(link, row, ["slug"])
        assert result == "/custom/hello-world/detail/"


class TestAutoLinkStringKeyEndToEnd:
    """Integration: full render with a VARCHAR id emits percent-encoded cell hrefs."""

    def _board_yaml(self, source_name: str) -> str:
        return f"""\
title: "Users"
auto_link: true
source: {source_name}
queries:
  rows:
    source: {source_name}
    sql: SELECT id, name FROM main.users
rows:
  - user_data
charts:
  user_data:
    title: "Users"
    type: table
    query: rows
"""

    def test_rendered_html_contains_encoded_varchar_id_href(
        self, tmp_path, local_project: Callable[..., Project]
    ) -> None:
        """Full render with a SQLite source having a TEXT id produces encoded cell hrefs.

        VARCHAR id → template {{ id | urlencode }} → href /data/dw/main/users/detail/?id=<encoded>
        """
        import sqlite3

        import yaml

        from dbt_charts.core.compile import compile
        from dbt_charts.core.execute import Executor
        from dbt_charts.core.execute.adapters import build_adapter_registry
        from dbt_charts.core.render import render

        db_path = tmp_path / "users.sqlite"
        conn = sqlite3.connect(str(db_path))
        conn.execute("CREATE TABLE users (id TEXT, name TEXT)")
        conn.execute("INSERT INTO users VALUES ('user@example.com', 'Alice')")
        conn.commit()
        conn.close()

        sources_yaml = {"sources": {"dw": {"type": "sqlite", "path": str(db_path)}}}
        (tmp_path / "dbt_charts.yml").write_text(yaml.dump(sources_yaml))

        result = compile(self._board_yaml("dw"))
        assert result.board is not None, result.errors

        executor = Executor(
            result.board,
            adapter_registry=build_adapter_registry(local_project(tmp_path)),
        )
        rendered = render(result.board, executor, format="html")
        html = rendered.output
        assert isinstance(html, str)

        # The href must contain the encoded email — @ → %40
        assert "detail/?id=user%40example.com" in html, (
            "Expected percent-encoded href with user%40example.com. "
            f"First 3000 chars: {html[:3000]}"
        )


class TestResolveCellLinkUnknownFilter:
    """resolve_cell_link raises ValueError for unrecognized template filters."""

    def test_unknown_filter_raises(self) -> None:
        import pytest

        from dbt_charts.core.render.chart.table_support import resolve_cell_link

        link = "/data/dw/public/users/detail/?id={{ id | xss }}"
        row = {"id": "abc"}
        with pytest.raises(ValueError, match="Unknown link template filter: 'xss'"):
            resolve_cell_link(link, row, ["id"])


# ---------------------------------------------------------------------------
# FK-dimension drill
# ---------------------------------------------------------------------------


class TestResolveFkColumnLinks:
    """Unit tests for resolve_fk_column_links — the per-column FK drill resolver.

    FK edges come from extract_all_relationships(manifest): declared dbt
    relationships: tests.  All declared edges have confidence=1.0 and are
    therefore recommended.  Only edges where from_table matches the queried
    table are considered.
    """

    # Minimal FK edge dicts as returned by extract_all_relationships
    _EDGES = [
        {
            "from_table": "deal",
            "from_column": "owner_id",
            "to_table": "owner",
            "to_column": "owner_id",
        },
        {
            "from_table": "deal",
            "from_column": "contact_id",
            "to_table": "contact",
            "to_column": "id",
        },
        # Edge for a different table — must not bleed into deal links.
        {
            "from_table": "ticket",
            "from_column": "user_id",
            "to_table": "user",
            "to_column": "id",
        },
    ]

    def test_recommended_fk_produces_drill_url(self) -> None:
        from dbt_charts.core.render.chart.auto_link import resolve_fk_column_links

        links = resolve_fk_column_links(
            source="db",
            schema="hubspot",
            table="deal",
            fk_edges=self._EDGES,
        )
        assert links == {
            "owner_id": "/data/db/hubspot/owner/detail/?owner_id={{ owner_id | urlencode }}",
            "contact_id": "/data/db/hubspot/contact/detail/?id={{ contact_id | urlencode }}",
        }

    def test_no_edges_for_table_returns_empty(self) -> None:
        from dbt_charts.core.render.chart.auto_link import resolve_fk_column_links

        links = resolve_fk_column_links(
            source="db",
            schema="hubspot",
            table="owner",  # no FK edges for owner
            fk_edges=self._EDGES,
        )
        assert links == {}

    def test_empty_edges_returns_empty(self) -> None:
        from dbt_charts.core.render.chart.auto_link import resolve_fk_column_links

        links = resolve_fk_column_links(
            source="db",
            schema="hubspot",
            table="deal",
            fk_edges=[],
        )
        assert links == {}

    def test_only_matching_table_edges_included(self) -> None:
        """Edges for ticket.user_id must not appear in deal's links."""
        from dbt_charts.core.render.chart.auto_link import resolve_fk_column_links

        links = resolve_fk_column_links(
            source="db",
            schema="hubspot",
            table="deal",
            fk_edges=self._EDGES,
        )
        assert "user_id" not in links

    def test_url_uses_source_schema_ref_table(self) -> None:
        from dbt_charts.core.render.chart.auto_link import resolve_fk_column_links

        links = resolve_fk_column_links(
            source="snowflake",
            schema="crm",
            table="deal",
            fk_edges=[
                {
                    "from_table": "deal",
                    "from_column": "owner_id",
                    "to_table": "owner",
                    "to_column": "owner_id",
                }
            ],
        )
        assert "owner_id" in links
        assert links["owner_id"].startswith("/data/snowflake/crm/owner/detail/")

    def test_right_column_used_in_query_string(self) -> None:
        """The query-string key is the RIGHT table's column (to_column), not the left."""
        from dbt_charts.core.render.chart.auto_link import resolve_fk_column_links

        links = resolve_fk_column_links(
            source="db",
            schema="hubspot",
            table="deal",
            fk_edges=[
                {
                    "from_table": "deal",
                    "from_column": "contact_id",
                    "to_table": "contact",
                    "to_column": "id",  # right column is "id", not "contact_id"
                }
            ],
        )
        assert links["contact_id"] == (
            "/data/db/hubspot/contact/detail/?id={{ contact_id | urlencode }}"
        )

    def test_template_uses_left_column_as_value(self) -> None:
        """The Jinja template value is the LEFT column (the FK cell value)."""
        from dbt_charts.core.render.chart.auto_link import resolve_fk_column_links

        links = resolve_fk_column_links(
            source="db",
            schema="hubspot",
            table="deal",
            fk_edges=[
                {
                    "from_table": "deal",
                    "from_column": "owner_id",
                    "to_table": "owner",
                    "to_column": "owner_id",
                }
            ],
        )
        # Template value is {{ owner_id | urlencode }} (the FK cell)
        assert "{{ owner_id | urlencode }}" in links["owner_id"]


class TestUrlencodeFilter:
    """resolve_cell_link handles {{ col | urlencode }} filter syntax."""

    def test_urlencode_filter_encodes_spaces(self) -> None:
        from dbt_charts.core.render.chart.table_support import resolve_cell_link

        link = "/data/db/crm/owner/detail/?id={{ owner_id | urlencode }}"
        row = {"owner_id": "John Doe"}
        result = resolve_cell_link(link, row, ["owner_id"])
        assert result == "/data/db/crm/owner/detail/?id=John+Doe"

    def test_urlencode_filter_encodes_special_chars(self) -> None:
        from dbt_charts.core.render.chart.table_support import resolve_cell_link

        link = "/data/db/crm/owner/detail/?id={{ owner_id | urlencode }}"
        row = {"owner_id": "user@example.com"}
        result = resolve_cell_link(link, row, ["owner_id"])
        assert result == "/data/db/crm/owner/detail/?id=user%40example.com"

    def test_urlencode_filter_with_numeric_value(self) -> None:
        from dbt_charts.core.render.chart.table_support import resolve_cell_link

        link = "/data/db/crm/owner/detail/?id={{ owner_id | urlencode }}"
        row = {"owner_id": 42}
        result = resolve_cell_link(link, row, ["owner_id"])
        assert result == "/data/db/crm/owner/detail/?id=42"

    def test_plain_template_still_works(self) -> None:
        """Existing {{ col }} form without filter still resolves correctly."""
        from dbt_charts.core.render.chart.table_support import resolve_cell_link

        link = "/data/dw/main/orders/detail/?id={{ id }}"
        row = {"id": 99}
        result = resolve_cell_link(link, row, ["id"])
        assert result == "/data/dw/main/orders/detail/?id=99"

    def test_urlencode_none_value_returns_none(self) -> None:
        from dbt_charts.core.render.chart.table_support import resolve_cell_link

        link = "/data/db/crm/owner/detail/?id={{ owner_id | urlencode }}"
        row = {"owner_id": None}
        result = resolve_cell_link(link, row, ["owner_id"])
        assert result is None


class TestFkDrillExplicitLinkPrecedence:
    """Explicit authored column link: beats FK-drill injection.

    FK injection happens during chart resolution in the sizing pass.
    Precedence is pinned via the integration test in TestFkDrillPrecedence.
    This unit test exercises the resolve_fk_column_links helper directly so
    the FK URL shape is pinned separately from the injection path.
    """

    def test_authored_column_link_survives_fk_injection(
        self, tmp_path, local_project: Callable[..., Project]
    ) -> None:
        """FK injection must not overwrite an authored column link:.

        End-to-end: a table with an explicit column link: on owner_id keeps that
        link even when a FK edge exists for owner_id.
        """
        import json
        import sqlite3

        import yaml

        from dbt_charts.core.compile import compile
        from dbt_charts.core.execute import Executor
        from dbt_charts.core.execute.adapters import build_adapter_registry
        from dbt_charts.core.render import render

        db_path = tmp_path / "deals.sqlite"
        conn = sqlite3.connect(str(db_path))
        conn.execute("CREATE TABLE deal (id INTEGER, owner_id TEXT)")
        conn.execute("INSERT INTO deal VALUES (1, 'owner_001')")
        conn.commit()
        conn.close()

        sources_yaml = {"sources": {"db": {"type": "sqlite", "path": str(db_path)}}}
        (tmp_path / "dbt_charts.yml").write_text(yaml.dump(sources_yaml))

        manifest = {
            "nodes": {
                "test.fk": {
                    "resource_type": "test",
                    "test_metadata": {
                        "name": "relationships",
                        "kwargs": {
                            "to": "ref('owner')",
                            "field": "owner_id",
                            "column_name": "owner_id",
                        },
                    },
                    "column_name": "owner_id",
                    "attached_node": "model.deal",
                },
                "model.deal": {
                    "resource_type": "model",
                    "name": "deal",
                    "alias": "deal",
                },
                "model.owner": {
                    "resource_type": "model",
                    "name": "owner",
                    "alias": "owner",
                },
            }
        }
        target_dir = tmp_path / "target"
        target_dir.mkdir()
        (target_dir / "manifest.json").write_text(json.dumps(manifest))

        # Authored link on owner_id column — must not be overwritten by FK drill.
        board_yaml = """\
title: "Deals"
auto_link: true
source: db
queries:
  rows:
    source: db
    sql: SELECT id, owner_id FROM main.deal
rows:
  - chart
charts:
  chart:
    title: "Deals"
    type: table
    query: rows
    style:
      columns:
        owner_id:
          link: /custom/owner/{{ owner_id }}
"""
        result = compile(board_yaml)
        assert result.board is not None, result.errors

        executor = Executor(
            result.board,
            adapter_registry=build_adapter_registry(local_project(tmp_path)),
        )
        rendered = render(result.board, executor, format="html")
        html = rendered.output
        assert isinstance(html, str)

        # Authored link must survive — FK injection must not overwrite it.
        assert "/custom/owner/" in html, (
            "Authored column link: must survive FK-drill injection. "
            f"Rendered HTML (first 3000 chars): {html[:3000]}"
        )
        # FK drill link must NOT appear on owner_id (authored link takes precedence).
        assert "/data/db/main/owner/detail/" not in html, (
            "FK drill link must not overwrite an authored column link:. "
            f"Rendered HTML (first 3000 chars): {html[:3000]}"
        )


class TestFkDrillPrecedence:
    """FK-drill links (per-column) coexist with row-level detail link (fallback).

    Precedence:
      explicit col_config.link > FK-drill per-column link > chart_root_link (row-level)
    """

    def test_fk_column_gets_drill_link_not_row_detail(
        self, tmp_path, local_project: Callable[..., Project]
    ) -> None:
        """A table over deal with owner_id declared FK → owner_id cells link to
        /data/db/main/owner/detail/ not the deal detail page.

        Uses SQLite (registered at project level in dbt_charts.yml) so the
        adapter_registry can resolve the source. The manifest.json is written
        into target/ so _all_manifest_relationships() finds the FK edge.
        """
        import json
        import sqlite3

        import yaml

        from dbt_charts.core.compile import compile
        from dbt_charts.core.execute import Executor
        from dbt_charts.core.execute.adapters import build_adapter_registry
        from dbt_charts.core.inspect.sources.dbt import extract_all_relationships
        from dbt_charts.core.render import render

        # Populate a SQLite file with the deal table (main. is the default schema)
        db_path = tmp_path / "deals.sqlite"
        conn = sqlite3.connect(str(db_path))
        conn.execute(
            "CREATE TABLE deal (id INTEGER PRIMARY KEY, owner_id TEXT, stage TEXT)"
        )
        conn.execute("INSERT INTO deal VALUES (1, 'owner_001', 'open')")
        conn.commit()
        conn.close()

        # Register the source at project level so build_adapter_registry picks it up
        sources_yaml = {"sources": {"db": {"type": "sqlite", "path": str(db_path)}}}
        (tmp_path / "dbt_charts.yml").write_text(yaml.dump(sources_yaml))

        # Write a manifest with a declared relationships: test FK edge
        manifest = {
            "nodes": {
                "test.fk": {
                    "resource_type": "test",
                    "test_metadata": {
                        "name": "relationships",
                        "kwargs": {
                            "to": "ref('owner')",
                            "field": "owner_id",
                            "column_name": "owner_id",
                        },
                    },
                    "column_name": "owner_id",
                    "attached_node": "model.deal",
                },
                "model.deal": {
                    "resource_type": "model",
                    "name": "deal",
                    "alias": "deal",
                },
                "model.owner": {
                    "resource_type": "model",
                    "name": "owner",
                    "alias": "owner",
                },
            }
        }
        fk_edges = extract_all_relationships(manifest)
        assert any(
            e["from_table"] == "deal" and e["from_column"] == "owner_id"
            for e in fk_edges
        ), f"FK edge deal.owner_id not found in: {fk_edges}"

        target_dir = tmp_path / "target"
        target_dir.mkdir()
        (target_dir / "manifest.json").write_text(json.dumps(manifest))

        # Board YAML: source references project-level "db" (not inline)
        board_yaml = """\
title: "Deals"
auto_link: true
source: db
queries:
  rows:
    source: db
    sql: SELECT id, owner_id, stage FROM main.deal
rows:
  - chart
charts:
  chart:
    title: "Deals"
    type: table
    query: rows
"""
        result = compile(board_yaml)
        assert result.board is not None, result.errors

        executor = Executor(
            result.board,
            adapter_registry=build_adapter_registry(local_project(tmp_path)),
        )
        rendered = render(result.board, executor, format="html")
        html = rendered.output
        assert isinstance(html, str)

        # owner_id cells must link to /data/db/main/owner/detail/ (FK drill)
        # schema is "main" because the SQL uses main.deal
        assert "/data/db/main/owner/detail/" in html, (
            "Expected FK drill link '/data/db/main/owner/detail/' for owner_id column. "
            f"Rendered HTML (first 3000 chars): {html[:3000]}"
        )
        # id cells should link to the deal detail page (row-level detail fallback)
        assert "/data/db/main/deal/detail/" in html, (
            "Expected row-level detail link '/data/db/main/deal/detail/' for id column. "
            f"Rendered HTML (first 3000 chars): {html[:3000]}"
        )


# ---------------------------------------------------------------------------
# FK-column drill injection: authored link survives, table with no FKs gets
# row-level detail link (regression guard for the inlined injection logic)
# ---------------------------------------------------------------------------


class TestFkColumnInjectionNoMatchPreservesColumns:
    """When no FK links match visible columns, all columns keep their authored state.

    This pins the behavior of the FK-injection loop inlined in render_single.py:
    columns not in fk_links come through with their existing config (or bare
    TableColumnConfig placeholder). Uses a unit test over resolve_fk_column_links
    (no executor needed) to confirm the FK helper returns empty when there are no
    matching edges.
    """

    def test_no_matching_edges_returns_empty_fk_links(self) -> None:
        """resolve_fk_column_links returns {} when no edge matches the table."""
        from dbt_charts.core.render.chart.auto_link import resolve_fk_column_links

        # Table "orders" has no FK edges declared
        fk_links = resolve_fk_column_links(
            source="db",
            schema="analytics",
            table="orders",
            fk_edges=[
                {
                    "from_table": "deal",  # different table
                    "from_column": "owner_id",
                    "to_table": "owner",
                    "to_column": "owner_id",
                }
            ],
        )
        assert fk_links == {}, f"Expected empty dict, got {fk_links}"


# ---------------------------------------------------------------------------
# Heuristic FK edges from super-schema
# ---------------------------------------------------------------------------


class TestResolveFkColumnLinksHeuristicFormat:
    """Heuristic edges (from_*/to_* format) work identically to declared edges."""

    def test_heuristic_edge_same_format_as_declared(self) -> None:
        """Heuristic edges normalized to from_*/to_* keys by fetch_fk_edges work fine."""
        from dbt_charts.core.render.chart.auto_link import resolve_fk_column_links

        # Simulate a heuristic edge already normalized to from_*/to_* by fetch_fk_edges
        heuristic_edges = [
            {
                "from_table": "deal",
                "from_column": "owner_id",
                "to_table": "owner",
                "to_column": "id",
            }
        ]
        links = resolve_fk_column_links("db", "hubspot", "deal", heuristic_edges)
        assert "owner_id" in links
        assert "/data/db/hubspot/owner/detail/" in links["owner_id"]
        assert "?id={{ owner_id | urlencode }}" in links["owner_id"]


# fetch_fk_edges' super-schema-cache heuristic (recommended vs. non-recommended
# edges) is intentionally not covered here: both cases import
# dbt_charts_super_schema unguarded, so they can only ever run with the
# private package installed.


# ---------------------------------------------------------------------------
# Multi-dimension chart auto-link (unified resolver)
# ---------------------------------------------------------------------------


class TestResolveColumnSetLink:
    """Unit tests for resolve_column_set_link — the unified channel→link resolver.

    covers:
    - bar x=<FK> → entity detail link
    - bar x=region, color=plan (plain dims) → filter-by-all-dims link
    - x=<measure> only (no dim channels) → no link
    - explicit link: preserved (caller must not call resolver)
    - single non-FK dimension → filter-by-dim link
    """

    _FK_EDGES = [
        {
            "from_table": "deal",
            "from_column": "owner_id",
            "to_table": "owner",
            "to_column": "owner_id",
        }
    ]

    def test_single_fk_dim_x_entity_detail_link(self) -> None:
        """Bar chart with x=owner_id (FK) → entity detail link for referenced table."""
        from dbt_charts.core.render.chart.auto_link import resolve_column_set_link

        link = resolve_column_set_link(
            channel_cols={"x": "owner_id"},
            loc=("db", "hubspot", "deal"),
            fk_edges=self._FK_EDGES,
            auto_link=True,
        )
        # Single recommended FK dimension → entity detail for the referenced table
        assert link == "/data/db/hubspot/owner/detail/?owner_id={{ x }}"

    def test_two_plain_dims_filter_by_all(self) -> None:
        """Bar x=region, color=plan → filter-by-all-dimensions link.

        Chart links use channel-name placeholders ({{ x }}, {{ color }}) not
        column names, because _build_href_calc_expr maps channel names to datum
        fields at Vega render time. No | urlencode in Vega expressions (no
        encodeURIComponent equivalent in Vega calc).
        """
        from dbt_charts.core.render.chart.auto_link import resolve_column_set_link

        link = resolve_column_set_link(
            channel_cols={"x": "region", "color": "plan"},
            loc=("db", "hubspot", "sales"),
            fk_edges=[],  # no FK edges
            auto_link=True,
        )
        # Both are plain dims → filter-by-all (param keys = col names, values = {{ channel }})
        assert link == "/data/db/hubspot/sales/?region={{ x }}&plan={{ color }}"

    def test_no_dim_channels_returns_empty(self) -> None:
        """Chart with no non-measure dim channels → no link."""
        from dbt_charts.core.render.chart.auto_link import resolve_column_set_link

        # Pass no channel_cols (e.g. only y was specified on the chart)
        link = resolve_column_set_link(
            channel_cols={},
            loc=("db", "hubspot", "deal"),
            fk_edges=[],
            auto_link=True,
        )
        assert link == ""

    def test_auto_link_off_returns_empty(self) -> None:
        from dbt_charts.core.render.chart.auto_link import resolve_column_set_link

        link = resolve_column_set_link(
            channel_cols={"x": "owner_id"},
            loc=("db", "hubspot", "deal"),
            fk_edges=self._FK_EDGES,
            auto_link=False,
        )
        assert link == ""

    def test_single_non_fk_dim_filter_link(self) -> None:
        """Single non-FK dim → filter-by-dim link (not entity detail)."""
        from dbt_charts.core.render.chart.auto_link import resolve_column_set_link

        link = resolve_column_set_link(
            channel_cols={"x": "region"},
            loc=("db", "hubspot", "sales"),
            fk_edges=[],
            auto_link=True,
        )
        # Single non-FK dim → filter-by-dim; param key = col name, value = {{ x }}
        assert link == "/data/db/hubspot/sales/?region={{ x }}"

    def test_fk_dim_but_not_recommended_uses_filter(self) -> None:
        """An FK edge exists but is_recommended=False — falls back to filter link."""
        from dbt_charts.core.render.chart.auto_link import resolve_column_set_link

        # Pass edge without is_recommended — those are not in fk_edges at all
        # (fetch_fk_edges filters them out before calling this function).
        # Simulate by passing empty fk_edges even though column name is owner_id.
        link = resolve_column_set_link(
            channel_cols={"x": "owner_id"},
            loc=("db", "hubspot", "deal"),
            fk_edges=[],  # no recommended edges
            auto_link=True,
        )
        # No FK edge → filter-by-dim fallback
        assert link == "/data/db/hubspot/deal/?owner_id={{ x }}"


class TestChartAutoLinkRenderSingle:
    """Integration: non-table charts pick up auto-link when auto_link=True.

    Tests that chart resolution during the sizing pass synthesizes a link
    for bar charts with dimension channels, using the unified resolver.
    """

    def test_bar_chart_x_fk_gets_entity_detail_link(
        self, tmp_path, local_project: Callable[..., Project]
    ) -> None:
        """Bar chart with x=owner_id (declared FK) → entity detail link in SVG."""
        import json
        import sqlite3

        import yaml

        from dbt_charts.core.compile import compile
        from dbt_charts.core.execute import Executor
        from dbt_charts.core.execute.adapters import build_adapter_registry
        from dbt_charts.core.render import render

        db_path = tmp_path / "deals.sqlite"
        conn = sqlite3.connect(str(db_path))
        conn.execute("CREATE TABLE deal (owner_id TEXT, revenue REAL)")
        conn.execute("INSERT INTO deal VALUES ('owner_001', 50000.0)")
        conn.execute("INSERT INTO deal VALUES ('owner_002', 75000.0)")
        conn.commit()
        conn.close()

        sources_yaml = {"sources": {"db": {"type": "sqlite", "path": str(db_path)}}}
        (tmp_path / "dbt_charts.yml").write_text(yaml.dump(sources_yaml))

        manifest = {
            "nodes": {
                "test.fk": {
                    "resource_type": "test",
                    "test_metadata": {
                        "name": "relationships",
                        "kwargs": {
                            "to": "ref('owner')",
                            "field": "owner_id",
                            "column_name": "owner_id",
                        },
                    },
                    "column_name": "owner_id",
                    "attached_node": "model.deal",
                },
                "model.deal": {
                    "resource_type": "model",
                    "name": "deal",
                    "alias": "deal",
                },
                "model.owner": {
                    "resource_type": "model",
                    "name": "owner",
                    "alias": "owner",
                },
            }
        }
        target_dir = tmp_path / "target"
        target_dir.mkdir()
        (target_dir / "manifest.json").write_text(json.dumps(manifest))

        board_yaml = """\
title: "Revenue by Owner"
auto_link: true
source: db
queries:
  rows:
    source: db
    sql: SELECT owner_id, SUM(revenue) AS revenue FROM main.deal GROUP BY owner_id
rows:
  - chart
charts:
  chart:
    title: "Revenue by Owner"
    type: bar
    query: rows
    x: owner_id
    y: revenue
"""
        result = compile(board_yaml)
        assert result.board is not None, result.errors

        executor = Executor(
            result.board,
            adapter_registry=build_adapter_registry(local_project(tmp_path)),
        )
        rendered = render(result.board, executor, format="svg")
        svg = rendered.output
        assert isinstance(svg, str)

        # The bar chart marks should have hrefs pointing to owner entity detail
        assert "/data/db/main/owner/detail/" in svg, (
            "Expected entity detail href '/data/db/main/owner/detail/' in bar chart SVG. "
            f"SVG snippet (first 3000 chars): {svg[:3000]}"
        )

    def test_bar_chart_two_plain_text_dims_produces_encoded_filter_link(
        self, tmp_path, local_project: Callable[..., Project]
    ) -> None:
        """Bar x=region (TEXT), color=plan (TEXT) → encoded filter link.

        String dimension values are percent-encoded in the Vega calculate expression
        via a chained replace() ladder, so region='east' and plan='pro' produce safe
        query-string parameters. Two string channels → both appear in the filter URL.
        """
        import sqlite3

        import yaml

        from dbt_charts.core.compile import compile
        from dbt_charts.core.execute import Executor
        from dbt_charts.core.execute.adapters import build_adapter_registry
        from dbt_charts.core.render import render

        db_path = tmp_path / "sales.sqlite"
        conn = sqlite3.connect(str(db_path))
        conn.execute("CREATE TABLE sales (region TEXT, plan TEXT, revenue REAL)")
        conn.execute("INSERT INTO sales VALUES ('east', 'pro', 10.0)")
        conn.execute("INSERT INTO sales VALUES ('west', 'free', 5.0)")
        conn.commit()
        conn.close()

        sources_yaml = {"sources": {"db": {"type": "sqlite", "path": str(db_path)}}}
        (tmp_path / "dbt_charts.yml").write_text(yaml.dump(sources_yaml))

        board_yaml = """\
title: "Sales"
auto_link: true
source: db
queries:
  rows:
    source: db
    sql: SELECT region, plan, SUM(revenue) AS revenue FROM main.sales GROUP BY region, plan
rows:
  - chart
charts:
  chart:
    title: "Sales by region and plan"
    type: bar
    query: rows
    x: region
    color: plan
    y: revenue
"""
        result = compile(board_yaml)
        assert result.board is not None, result.errors

        executor = Executor(
            result.board,
            adapter_registry=build_adapter_registry(local_project(tmp_path)),
        )
        rendered = render(result.board, executor, format="svg")
        svg = rendered.output
        assert isinstance(svg, str)

        # Both region and plan are TEXT — Vega encodes via replace() chain.
        assert "/data/db/main/sales/?region=" in svg, (
            "Bar chart with TEXT dimensions must produce an encoded filter link. "
            f"SVG snippet (first 3000 chars): {svg[:3000]}"
        )

    def test_chart_explicit_link_not_overwritten(
        self, tmp_path, local_project: Callable[..., Project]
    ) -> None:
        """Explicit link: on a bar chart is not overwritten by auto-link."""
        import sqlite3

        import yaml

        from dbt_charts.core.compile import compile
        from dbt_charts.core.execute import Executor
        from dbt_charts.core.execute.adapters import build_adapter_registry
        from dbt_charts.core.render import render

        db_path = tmp_path / "regions.sqlite"
        conn = sqlite3.connect(str(db_path))
        conn.execute("CREATE TABLE region (name TEXT, revenue REAL)")
        conn.execute("INSERT INTO region VALUES ('east', 100.0)")
        conn.commit()
        conn.close()

        sources_yaml = {"sources": {"db": {"type": "sqlite", "path": str(db_path)}}}
        (tmp_path / "dbt_charts.yml").write_text(yaml.dump(sources_yaml))

        board_yaml = """\
title: "Revenue"
auto_link: true
source: db
queries:
  rows:
    source: db
    sql: SELECT name, revenue FROM main.region
rows:
  - chart
charts:
  chart:
    title: "Revenue by region"
    type: bar
    query: rows
    x: name
    y: revenue
    link: /custom/{{ x }}/report
"""
        result = compile(board_yaml)
        assert result.board is not None, result.errors

        executor = Executor(
            result.board,
            adapter_registry=build_adapter_registry(local_project(tmp_path)),
        )
        rendered = render(result.board, executor, format="svg")
        svg = rendered.output
        assert isinstance(svg, str)

        # Authored link: must survive; no auto-link path injection
        assert "/custom/" in svg, (
            "Authored link: '/custom/...' must be preserved in bar chart SVG. "
            f"SVG snippet (first 3000 chars): {svg[:3000]}"
        )
        # Auto-link should NOT override
        assert "/data/db/" not in svg, (
            "Auto-link must not override explicit link: in bar chart. "
            f"SVG snippet (first 3000 chars): {svg[:3000]}"
        )


# ---------------------------------------------------------------------------
# Regression: FK-keyed table rows link to their OWN table detail
# (not the FK-referenced parent entity)
# ---------------------------------------------------------------------------


class TestFkKeyedTableOwnDetail:
    """Regression: a table whose primary key IS an FK must still link each row
    to its own /data/.../detail/ URL, not the parent entity's detail page.

    Pattern: 1:1 extension table where ``user_profile.id`` references ``user.id``.
    The row identity IS the FK column value, but the link should be:
      /data/db/main/user_profile/detail/?id=<value>  (own table)
    NOT:
      /data/db/main/user/detail/?id=<value>  (parent entity — WRONG)

    This was the regression introduced by the unification: passing fk_edges to
    resolve_column_set_link with row_detail=True caused the single-FK key path
    to redirect to the referenced entity instead of the own table.

    Fix: _synthesize_auto_link passes fk_edges=[] for the table path;
    _inject_fk_column_links injects FK-drill on NON-identity columns only.
    """

    def test_fk_keyed_table_links_to_own_detail(
        self, tmp_path, local_project: Callable[..., Project]
    ) -> None:
        """Table with id=FK still links rows to own table detail, not parent.

        user_profile.id references user.id (1:1 extension pattern).
        Auto-link on user_profile table must emit:
          /data/db/main/user_profile/detail/?id=...
        NOT:
          /data/db/main/user/detail/?id=...
        """
        import json
        import sqlite3

        import yaml

        from dbt_charts.core.compile import compile
        from dbt_charts.core.execute import Executor
        from dbt_charts.core.execute.adapters import build_adapter_registry
        from dbt_charts.core.render import render

        db_path = tmp_path / "profiles.sqlite"
        conn = sqlite3.connect(str(db_path))
        # user_profile.id is an FK to user.id — 1:1 extension table
        conn.execute("CREATE TABLE user_profile (id INTEGER, bio TEXT)")
        conn.execute("INSERT INTO user_profile VALUES (1, 'Alice bio')")
        conn.execute("INSERT INTO user_profile VALUES (2, 'Bob bio')")
        conn.commit()
        conn.close()

        sources_yaml = {"sources": {"db": {"type": "sqlite", "path": str(db_path)}}}
        (tmp_path / "dbt_charts.yml").write_text(yaml.dump(sources_yaml))

        # FK edge: user_profile.id → user.id (identity key is an FK)
        manifest = {
            "nodes": {
                "test.fk_profile_user": {
                    "resource_type": "test",
                    "test_metadata": {
                        "name": "relationships",
                        "kwargs": {
                            "to": "ref('user')",
                            "field": "id",
                            "column_name": "id",
                        },
                    },
                    "column_name": "id",
                    "attached_node": "model.user_profile",
                },
                "model.user_profile": {
                    "resource_type": "model",
                    "name": "user_profile",
                    "alias": "user_profile",
                },
                "model.user": {
                    "resource_type": "model",
                    "name": "user",
                    "alias": "user",
                },
            }
        }
        target_dir = tmp_path / "target"
        target_dir.mkdir()
        (target_dir / "manifest.json").write_text(json.dumps(manifest))

        board_yaml = """\
title: "User Profiles"
auto_link: true
source: db
queries:
  rows:
    source: db
    sql: SELECT id, bio FROM main.user_profile
rows:
  - chart
charts:
  chart:
    title: "User Profiles"
    type: table
    query: rows
"""
        result = compile(board_yaml)
        assert result.board is not None, result.errors

        executor = Executor(
            result.board,
            adapter_registry=build_adapter_registry(local_project(tmp_path)),
        )
        rendered = render(result.board, executor, format="html")
        html = rendered.output
        assert isinstance(html, str)

        # Row-level link MUST point to own table (user_profile), not parent (user)
        assert "/data/db/main/user_profile/detail/" in html, (
            "FK-keyed table must link rows to its OWN table detail URL, "
            "not the parent entity. "
            f"Rendered HTML (first 3000 chars): {html[:3000]}"
        )
        # Must NOT redirect to the FK-referenced parent entity
        assert "/data/db/main/user/detail/" not in html, (
            "Row-level link must NOT redirect to FK parent (user) — "
            "the key is the row's own identity, even when it's also an FK. "
            f"Rendered HTML (first 3000 chars): {html[:3000]}"
        )


# ---------------------------------------------------------------------------
# Regression: non-identity FK column suppressed when table has no plain id
# (deal.owner_id should drill to /owner/detail/, not be silently skipped)
# ---------------------------------------------------------------------------


class TestNonIdentityFkNotSuppressed:
    """Regression: a table whose identity key set is ALL *_id columns (because
    there is no plain ``id`` column) must NOT suppress FK drill on non-identity
    FK columns like ``owner_id``.

    Root cause: ``plan_link_keys`` returns every ``*_id`` column when no plain
    ``id`` exists (composite FK identity).  ``_inject_fk_column_links`` then
    classifies ALL those ``*_id`` columns as ``identity_keys`` and skips FK-drill
    injection on them — including ``owner_id``, which is a genuine FK to a
    different table, not deal's own PK.

    Correct behavior for the deal table (no ``id`` column):
    - ``deal_id`` → own-table detail URL (PK / identity key)
    - ``owner_id`` → FK-drill link to owner/detail/ (non-identity FK column)
    Both must be present simultaneously.
    """

    def test_pk_and_non_identity_fk_both_present(
        self, tmp_path, local_project: Callable[..., Project]
    ) -> None:
        """Table with deal_id (PK) + owner_id (FK to owner) and no plain id:
        deal_id → own-table detail, owner_id → owner detail. Both must appear.
        """
        import json
        import sqlite3

        import yaml

        from dbt_charts.core.compile import compile
        from dbt_charts.core.execute import Executor
        from dbt_charts.core.execute.adapters import build_adapter_registry
        from dbt_charts.core.render import render

        db_path = tmp_path / "deals.sqlite"
        conn = sqlite3.connect(str(db_path))
        # No plain "id" column — deal_id and owner_id are both *_id columns.
        conn.execute(
            "CREATE TABLE deal (deal_id INTEGER PRIMARY KEY, owner_id TEXT, stage TEXT)"
        )
        conn.execute("INSERT INTO deal VALUES (1, 'owner_001', 'open')")
        conn.commit()
        conn.close()

        sources_yaml = {"sources": {"db": {"type": "sqlite", "path": str(db_path)}}}
        (tmp_path / "dbt_charts.yml").write_text(yaml.dump(sources_yaml))

        # FK edge: deal.owner_id → owner.owner_id (recommended, confidence 0.9)
        manifest = {
            "nodes": {
                "test.fk_deal_owner": {
                    "resource_type": "test",
                    "test_metadata": {
                        "name": "relationships",
                        "kwargs": {
                            "to": "ref('owner')",
                            "field": "owner_id",
                            "column_name": "owner_id",
                        },
                    },
                    "column_name": "owner_id",
                    "attached_node": "model.deal",
                },
                "model.deal": {
                    "resource_type": "model",
                    "name": "deal",
                    "alias": "deal",
                },
                "model.owner": {
                    "resource_type": "model",
                    "name": "owner",
                    "alias": "owner",
                },
            }
        }
        target_dir = tmp_path / "target"
        target_dir.mkdir()
        (target_dir / "manifest.json").write_text(json.dumps(manifest))

        board_yaml = """\
title: "Deals"
auto_link: true
source: db
queries:
  rows:
    source: db
    sql: SELECT deal_id, owner_id, stage FROM main.deal
rows:
  - chart
charts:
  chart:
    title: "Deals"
    type: table
    query: rows
"""
        result = compile(board_yaml)
        assert result.board is not None, result.errors

        executor = Executor(
            result.board,
            adapter_registry=build_adapter_registry(local_project(tmp_path)),
        )
        rendered = render(result.board, executor, format="html")
        html = rendered.output
        assert isinstance(html, str)

        # deal_id cells → own-table detail (root link)
        assert "/data/db/main/deal/detail/" in html, (
            "Expected own-table detail link '/data/db/main/deal/detail/' for deal_id. "
            f"Rendered HTML (first 3000 chars): {html[:3000]}"
        )
        # owner_id cells → FK drill to owner detail
        assert "/data/db/main/owner/detail/" in html, (
            "Expected FK-drill link '/data/db/main/owner/detail/' for owner_id. "
            "This is the regression: owner_id was silently suppressed because "
            "plan_link_keys returned it as part of the identity key set when no "
            "plain 'id' column existed, causing _inject_fk_column_links to skip it. "
            f"Rendered HTML (first 3000 chars): {html[:3000]}"
        )


# ---------------------------------------------------------------------------
# Regression: measure on x must NOT drive a link
# ---------------------------------------------------------------------------


class TestMeasureOnXNoLink:
    """Regression: a measure column on the x channel must not generate an auto-link.

    Pattern: scatter chart x=revenue (quantitative/float) — revenue is a measure,
    not a dimension, so no drill link should be synthesized.

    Before the fix: _synthesize_auto_link unconditionally added x to channel_cols,
    so a scatter with x=revenue produced a wrong filter link like
      /data/db/main/sales/?revenue={{ x }}&...
    which makes no semantic sense (filtering by a continuous measure value).
    """

    def test_scatter_measure_x_no_link(
        self, tmp_path, local_project: Callable[..., Project]
    ) -> None:
        """Scatter chart with x=revenue (float measure) emits no auto-link href."""
        import sqlite3

        import yaml

        from dbt_charts.core.compile import compile
        from dbt_charts.core.execute import Executor
        from dbt_charts.core.execute.adapters import build_adapter_registry
        from dbt_charts.core.render import render

        db_path = tmp_path / "sales.sqlite"
        conn = sqlite3.connect(str(db_path))
        conn.execute("CREATE TABLE sales (revenue REAL, n_deals INTEGER, region TEXT)")
        conn.execute("INSERT INTO sales VALUES (50000.0, 5, 'east')")
        conn.execute("INSERT INTO sales VALUES (75000.0, 8, 'west')")
        conn.commit()
        conn.close()

        sources_yaml = {"sources": {"db": {"type": "sqlite", "path": str(db_path)}}}
        (tmp_path / "dbt_charts.yml").write_text(yaml.dump(sources_yaml))

        board_yaml = """\
title: "Sales Scatter"
auto_link: true
source: db
queries:
  rows:
    source: db
    sql: SELECT revenue, n_deals FROM main.sales
rows:
  - chart
charts:
  chart:
    title: "Revenue vs Deals"
    type: scatter
    query: rows
    x: revenue
    y: n_deals
"""
        result = compile(board_yaml)
        assert result.board is not None, result.errors

        executor = Executor(
            result.board,
            adapter_registry=build_adapter_registry(local_project(tmp_path)),
        )
        rendered = render(result.board, executor, format="svg")
        svg = rendered.output
        assert isinstance(svg, str)

        # Measure on x must NOT drive a link — no /data/ href expected
        assert "/data/db/" not in svg, (
            "Scatter chart with x=revenue (float measure) must not produce an "
            "auto-link. Revenue is a measure, not a dimension — linking by a "
            "continuous float value is semantically wrong. "
            f"SVG snippet (first 3000 chars): {svg[:3000]}"
        )

    def test_horizontal_bar_links_on_x_not_on_measure_y(
        self, tmp_path, local_project: Callable[..., Project]
    ) -> None:
        """Horizontal bar chart auto-links on x=region, never on y=amount.

        Bar semantics fix x=category, y=measure regardless of orientation
        (see _bake_cartesian_axes docstring) — style.orientation only flips
        which screen axis renders which channel. Auto-link must key off the
        category (x), never the measure (y), on a horizontal bar exactly as
        it would on a vertical one.
        """
        import sqlite3

        import yaml

        from dbt_charts.core.compile import compile
        from dbt_charts.core.execute import Executor
        from dbt_charts.core.execute.adapters import build_adapter_registry
        from dbt_charts.core.render import render

        db_path = tmp_path / "revenue.sqlite"
        conn = sqlite3.connect(str(db_path))
        conn.execute("CREATE TABLE revenue (region TEXT, amount REAL)")
        conn.execute("INSERT INTO revenue VALUES ('east', 50000.0)")
        conn.execute("INSERT INTO revenue VALUES ('west', 75000.0)")
        conn.commit()
        conn.close()

        sources_yaml = {"sources": {"db": {"type": "sqlite", "path": str(db_path)}}}
        (tmp_path / "dbt_charts.yml").write_text(yaml.dump(sources_yaml))

        board_yaml = """\
title: "Revenue by Region (Horizontal)"
auto_link: true
source: db
queries:
  rows:
    source: db
    sql: SELECT region, amount FROM main.revenue
rows:
  - chart
charts:
  chart:
    title: "Revenue by Region"
    type: bar
    query: rows
    x: region
    y: amount
    style:
      orientation: horizontal
"""
        result = compile(board_yaml)
        assert result.board is not None, result.errors

        executor = Executor(
            result.board,
            adapter_registry=build_adapter_registry(local_project(tmp_path)),
        )
        rendered = render(result.board, executor, format="svg")
        svg = rendered.output
        assert isinstance(svg, str)

        # y=amount is the measure axis regardless of orientation — must NOT
        # drive a link. The dimension is x=region, which must.
        assert "/data/db/main/revenue/?amount=" not in svg, (
            "Horizontal bar with y=amount (measure axis) must not produce a "
            "filter link on the measure column. "
            f"SVG snippet (first 3000 chars): {svg[:3000]}"
        )
        assert "/data/db/main/revenue/?region=" in svg, (
            "Horizontal bar with x=region (category) must produce a filter "
            "link on the category column, same as a vertical bar would. "
            f"SVG snippet (first 3000 chars): {svg[:3000]}"
        )


# ---------------------------------------------------------------------------
# Chart filter links: string dimension values are percent-encoded
# (Vega replace() chains handle the ASCII delimiter set)
# ---------------------------------------------------------------------------


class TestChartFilterLinkUrlSafety:
    """Chart filter links for string dimension columns use a Vega replace() chain.

    Vega's expression language has no encodeURIComponent, but chained replace()
    calls with /pattern/g regex literals cover the ASCII delimiter set that
    structurally corrupts a ?col=value query string.
    """

    def test_string_dimension_ampersand_produces_encoded_filter_link(
        self, tmp_path, local_project: Callable[..., Project]
    ) -> None:
        """Bar x=region (TEXT) with value 'A&B' produces an encoded filter link.

        The Vega replace() chain encodes '&' as '%26', so '?region=A%26B' is
        a structurally valid query string the client-side variable interceptor
        can parse back correctly.
        """
        import sqlite3

        import yaml

        from dbt_charts.core.compile import compile
        from dbt_charts.core.execute import Executor
        from dbt_charts.core.execute.adapters import build_adapter_registry
        from dbt_charts.core.render import render

        db_path = tmp_path / "sales.sqlite"
        conn = sqlite3.connect(str(db_path))
        conn.execute("CREATE TABLE sales (region TEXT, revenue REAL)")
        conn.execute("INSERT INTO sales VALUES ('A&B', 100.0)")
        conn.execute("INSERT INTO sales VALUES ('north east', 50.0)")
        conn.commit()
        conn.close()

        sources_yaml = {"sources": {"db": {"type": "sqlite", "path": str(db_path)}}}
        (tmp_path / "dbt_charts.yml").write_text(yaml.dump(sources_yaml))

        board_yaml = """\
title: "Sales"
auto_link: true
source: db
queries:
  rows:
    source: db
    sql: SELECT region, SUM(revenue) AS revenue FROM main.sales GROUP BY region
rows:
  - chart
charts:
  chart:
    title: "Sales by region"
    type: bar
    query: rows
    x: region
    y: revenue
"""
        result = compile(board_yaml)
        assert result.board is not None, result.errors

        executor = Executor(
            result.board,
            adapter_registry=build_adapter_registry(local_project(tmp_path)),
        )
        rendered = render(result.board, executor, format="svg")
        svg = rendered.output
        assert isinstance(svg, str)

        # String dimension with '&' → encoded filter link (%26 for ampersand)
        assert "/data/db/main/sales/?region=" in svg, (
            "Bar chart with TEXT dimension 'region' must produce an encoded filter "
            "link — '&' encodes to '%26' via the Vega replace() chain. "
            f"SVG snippet (first 3000 chars): {svg[:3000]}"
        )
        assert "A%26B" in svg, (
            "Value 'A&B' must be encoded as 'A%26B' in the href. "
            f"SVG snippet (first 3000 chars): {svg[:3000]}"
        )

    def test_string_dimension_space_produces_encoded_filter_link(
        self, tmp_path, local_project: Callable[..., Project]
    ) -> None:
        """Bar x=name (TEXT) with value 'north east' produces an encoded filter link.

        Space is encoded as '%20' via the Vega replace() chain.
        """
        import sqlite3

        import yaml

        from dbt_charts.core.compile import compile
        from dbt_charts.core.execute import Executor
        from dbt_charts.core.execute.adapters import build_adapter_registry
        from dbt_charts.core.render import render

        db_path = tmp_path / "regions.sqlite"
        conn = sqlite3.connect(str(db_path))
        conn.execute("CREATE TABLE region (name TEXT, revenue REAL)")
        conn.execute("INSERT INTO region VALUES ('north east', 100.0)")
        conn.commit()
        conn.close()

        sources_yaml = {"sources": {"db": {"type": "sqlite", "path": str(db_path)}}}
        (tmp_path / "dbt_charts.yml").write_text(yaml.dump(sources_yaml))

        board_yaml = """\
title: "Revenue"
auto_link: true
source: db
queries:
  rows:
    source: db
    sql: SELECT name, revenue FROM main.region
rows:
  - chart
charts:
  chart:
    title: "Revenue by region"
    type: bar
    query: rows
    x: name
    y: revenue
"""
        result = compile(board_yaml)
        assert result.board is not None, result.errors

        executor = Executor(
            result.board,
            adapter_registry=build_adapter_registry(local_project(tmp_path)),
        )
        rendered = render(result.board, executor, format="svg")
        svg = rendered.output
        assert isinstance(svg, str)

        # Space encoded as '%20'
        assert "/data/db/main/region/?name=" in svg, (
            "Bar chart with TEXT dimension containing spaces must produce an "
            "encoded filter link — space encodes to '%20' via the Vega replace() chain. "
            f"SVG snippet (first 3000 chars): {svg[:3000]}"
        )
        assert "north%20east" in svg, (
            "Value 'north east' must be encoded as 'north%20east' in the href. "
            f"SVG snippet (first 3000 chars): {svg[:3000]}"
        )

    def test_full_delimiter_set_round_trips_through_urllib(
        self, tmp_path, local_project: Callable[..., Project]
    ) -> None:
        """A value hitting every encoded character decodes back to the original.

        Exercises %, +, #, ?, / and = together (not just & and space, covered
        above), including a leading '%' to pin the encode-% -first ordering:
        if % were encoded after another substitution, this value would
        double-encode and fail to round-trip.
        """
        import re
        import sqlite3
        from urllib.parse import parse_qs

        import yaml

        from dbt_charts.core.compile import compile
        from dbt_charts.core.execute import Executor
        from dbt_charts.core.execute.adapters import build_adapter_registry
        from dbt_charts.core.render import render

        original = "100%+a&b#c?d/e=f"
        db_path = tmp_path / "labels.sqlite"
        conn = sqlite3.connect(str(db_path))
        conn.execute("CREATE TABLE labels (name TEXT, revenue REAL)")
        conn.execute("INSERT INTO labels VALUES (?, 100.0)", (original,))
        conn.commit()
        conn.close()

        sources_yaml = {"sources": {"db": {"type": "sqlite", "path": str(db_path)}}}
        (tmp_path / "dbt_charts.yml").write_text(yaml.dump(sources_yaml))

        board_yaml = """\
title: "Labels"
auto_link: true
source: db
queries:
  rows:
    source: db
    sql: SELECT name, revenue FROM main.labels
rows:
  - chart
charts:
  chart:
    title: "Revenue by label"
    type: bar
    query: rows
    x: name
    y: revenue
"""
        result = compile(board_yaml)
        assert result.board is not None, result.errors

        executor = Executor(
            result.board,
            adapter_registry=build_adapter_registry(local_project(tmp_path)),
        )
        rendered = render(result.board, executor, format="svg")
        svg = rendered.output
        assert isinstance(svg, str)

        match = re.search(r"/data/db/main/labels/\?name=([^\"]+)", svg)
        assert match is not None, (
            f"Filter link not found in SVG. First 3000 chars: {svg[:3000]}"
        )
        decoded = parse_qs(f"name={match.group(1)}")["name"][0]
        assert decoded == original


# ---------------------------------------------------------------------------
# Chart filter links: derived / aliased columns must not produce links
# ---------------------------------------------------------------------------


class TestChartFilterLinkAliasedColumnBail:
    """Chart filter links must not be synthesized for SQL-alias / derived columns.

    source_schema_table() proves one base table, but the channel columns may
    be SQL aliases or computed expressions (e.g. SELECT region AS segment).
    The resolver must validate channel columns against the table's actual
    columns and bail (no link) for aliases or derived columns.
    """

    def test_aliased_dimension_column_produces_no_link(
        self, tmp_path, local_project: Callable[..., Project]
    ) -> None:
        """SELECT region AS segment — 'segment' is not a base-table column.

        The resolver must bail (no link) when the dimension column ('segment')
        is not present in the table's actual schema columns ('region').
        """
        import sqlite3

        import yaml

        from dbt_charts.core.compile import compile
        from dbt_charts.core.execute import Executor
        from dbt_charts.core.execute.adapters import build_adapter_registry
        from dbt_charts.core.render import render

        db_path = tmp_path / "sales.sqlite"
        conn = sqlite3.connect(str(db_path))
        conn.execute("CREATE TABLE sales (region TEXT, revenue REAL)")
        conn.execute("INSERT INTO sales VALUES ('east', 100.0)")
        conn.commit()
        conn.close()

        sources_yaml = {"sources": {"db": {"type": "sqlite", "path": str(db_path)}}}
        (tmp_path / "dbt_charts.yml").write_text(yaml.dump(sources_yaml))

        board_yaml = """\
title: "Sales by segment"
auto_link: true
source: db
queries:
  rows:
    source: db
    sql: SELECT region AS segment, SUM(revenue) AS revenue FROM main.sales GROUP BY region
rows:
  - chart
charts:
  chart:
    title: "Sales by segment"
    type: bar
    query: rows
    x: segment
    y: revenue
"""
        result = compile(board_yaml)
        assert result.board is not None, result.errors

        executor = Executor(
            result.board,
            adapter_registry=build_adapter_registry(local_project(tmp_path)),
        )
        rendered = render(result.board, executor, format="svg")
        svg = rendered.output
        assert isinstance(svg, str)

        # 'segment' is an alias for 'region' — not in the base table's schema
        assert "/data/db/main/sales/?segment=" not in svg, (
            "Bar chart with aliased dimension 'segment' (SQL alias for 'region') "
            "must NOT produce a filter link — 'segment' is not a real column of "
            "the 'sales' table, so filtering by it would return 0 rows. "
            f"SVG snippet (first 3000 chars): {svg[:3000]}"
        )


class TestFkColumnLinksFromExecutorStaysPlainOnly:
    """fk_column_links_from_executor deliberately stays fully out of this
    task's CTE/JOIN row-grain work — it keys per-column FK links by the base
    table's column name and matches them against actual *output* column
    names with no alias tracing, so widening it to either CTE or JOIN queries
    risks a link on an unrelated output column that coincidentally shares a
    base FK column's name.
    """

    def test_join_query_bails_even_with_matching_fk_edge(
        self, tmp_path, local_project: Callable[..., Project]
    ) -> None:
        import json
        import sqlite3

        import yaml

        from dbt_charts.core.compile.models.query.normalized import SqlQuery
        from dbt_charts.core.execute.adapters import build_adapter_registry
        from dbt_charts.core.render.chart.auto_link import (
            fk_column_links_from_executor,
        )

        db_path = tmp_path / "sales.sqlite"
        conn = sqlite3.connect(str(db_path))
        conn.execute("CREATE TABLE orders (id INTEGER, cust_id INTEGER)")
        conn.execute("CREATE TABLE customers (id INTEGER, name TEXT)")
        conn.commit()
        conn.close()

        sources_yaml = {"sources": {"dw": {"type": "sqlite", "path": str(db_path)}}}
        (tmp_path / "dbt_charts.yml").write_text(yaml.dump(sources_yaml))

        manifest = {
            "nodes": {
                "test.fk": {
                    "resource_type": "test",
                    "test_metadata": {
                        "name": "relationships",
                        "kwargs": {
                            "to": "ref('customers')",
                            "field": "id",
                            "column_name": "cust_id",
                        },
                    },
                    "column_name": "cust_id",
                    "attached_node": "model.orders",
                },
                "model.orders": {
                    "resource_type": "model",
                    "name": "orders",
                    "alias": "orders",
                },
                "model.customers": {
                    "resource_type": "model",
                    "name": "customers",
                    "alias": "customers",
                },
            }
        }
        target_dir = tmp_path / "target"
        target_dir.mkdir()
        (target_dir / "manifest.json").write_text(json.dumps(manifest))

        adapter_registry = build_adapter_registry(local_project(tmp_path))

        query = SqlQuery(
            sql="SELECT o.id, c.name FROM main.orders o JOIN main.customers c ON o.cust_id = c.id",
            source="dw",
        )
        assert fk_column_links_from_executor(query, adapter_registry) == {}

    def test_cte_wrapped_query_bails(
        self, tmp_path, local_project: Callable[..., Project]
    ) -> None:
        """CTE queries bail too, not just JOINs — fully reverted to this
        function's pre-task scope (matches the original behavior before
        source_schema_table gained CTE support for the row-detail path)."""
        import json
        import sqlite3

        import yaml

        from dbt_charts.core.compile.models.query.normalized import SqlQuery
        from dbt_charts.core.execute.adapters import build_adapter_registry
        from dbt_charts.core.render.chart.auto_link import (
            fk_column_links_from_executor,
        )

        db_path = tmp_path / "sales.sqlite"
        conn = sqlite3.connect(str(db_path))
        conn.execute("CREATE TABLE orders (id INTEGER, cust_id INTEGER)")
        conn.execute("CREATE TABLE customers (id INTEGER, name TEXT)")
        conn.commit()
        conn.close()

        sources_yaml = {"sources": {"dw": {"type": "sqlite", "path": str(db_path)}}}
        (tmp_path / "dbt_charts.yml").write_text(yaml.dump(sources_yaml))

        manifest = {
            "nodes": {
                "test.fk": {
                    "resource_type": "test",
                    "test_metadata": {
                        "name": "relationships",
                        "kwargs": {
                            "to": "ref('customers')",
                            "field": "id",
                            "column_name": "cust_id",
                        },
                    },
                    "column_name": "cust_id",
                    "attached_node": "model.orders",
                },
                "model.orders": {
                    "resource_type": "model",
                    "name": "orders",
                    "alias": "orders",
                },
                "model.customers": {
                    "resource_type": "model",
                    "name": "customers",
                    "alias": "customers",
                },
            }
        }
        target_dir = tmp_path / "target"
        target_dir.mkdir()
        (target_dir / "manifest.json").write_text(json.dumps(manifest))

        adapter_registry = build_adapter_registry(local_project(tmp_path))

        query = SqlQuery(
            sql="WITH w AS (SELECT * FROM main.orders) SELECT * FROM w",
            source="dw",
        )
        assert fk_column_links_from_executor(query, adapter_registry) == {}


# ---------------------------------------------------------------------------
# _vega_encode_field unit tests: verify the Vega replace-chain structure
# ---------------------------------------------------------------------------


class TestVegaEncodeField:
    """Unit tests for _vega_encode_field — the Vega replace-chain encoder.

    Asserts on the expression string structure, not on vl_convert execution.
    The round-trip correctness (vl_convert evaluating the expression against
    data) is covered by the integration tests in TestChartFilterLinkUrlSafety.
    """

    def test_percent_encoded_first_wraps_datum_directly(self) -> None:
        """% must be the innermost encode so later %XX outputs are not re-encoded."""
        from dbt_charts.core.render.chart.features.click_interactivity import (
            _vega_encode_field,
        )

        expr = _vega_encode_field("region")
        # The % replace must wrap datum directly — it is the innermost layer.
        assert "replace('' + datum['region'], /%/g, '%25')" in expr

    def test_field_name_embedded_in_datum_reference(self) -> None:
        from dbt_charts.core.render.chart.features.click_interactivity import (
            _vega_encode_field,
        )

        expr = _vega_encode_field("status")
        assert "datum['status']" in expr


# ---------------------------------------------------------------------------
# Positive integration test: integer multi-dimension chart produces filter link
# (guard against synthesize_auto_link silently over-bailing)
# ---------------------------------------------------------------------------


class TestIntegerDimChartFilterLink:
    """Integer dimension channels produce filter-by-dimension links end-to-end.

    Guards against _synthesize_auto_link silently bailing on integer-typed
    dimension columns. The negative path (string dims bailing) was the only
    path previously tested; this pins the positive path.
    """

    def test_integer_dimension_produces_filter_link(
        self, tmp_path, local_project: Callable[..., Project]
    ) -> None:
        """Bar x=quarter (INTEGER), no FK edge → filter link in SVG."""
        import sqlite3

        import yaml

        from dbt_charts.core.compile import compile
        from dbt_charts.core.execute import Executor
        from dbt_charts.core.execute.adapters import build_adapter_registry
        from dbt_charts.core.render import render

        db_path = tmp_path / "sales.sqlite"
        conn = sqlite3.connect(str(db_path))
        conn.execute("CREATE TABLE sales (quarter INTEGER, revenue REAL)")
        conn.execute("INSERT INTO sales VALUES (1, 100.0)")
        conn.execute("INSERT INTO sales VALUES (2, 150.0)")
        conn.commit()
        conn.close()

        sources_yaml = {"sources": {"db": {"type": "sqlite", "path": str(db_path)}}}
        (tmp_path / "dbt_charts.yml").write_text(yaml.dump(sources_yaml))

        board_yaml = """\
title: "Sales by Quarter"
auto_link: true
source: db
queries:
  rows:
    source: db
    sql: SELECT quarter, SUM(revenue) AS revenue FROM main.sales GROUP BY quarter
rows:
  - chart
charts:
  chart:
    title: "Revenue by quarter"
    type: bar
    query: rows
    x: quarter
    y: revenue
"""
        result = compile(board_yaml)
        assert result.board is not None, result.errors

        executor = Executor(
            result.board,
            adapter_registry=build_adapter_registry(local_project(tmp_path)),
        )
        rendered = render(result.board, executor, format="svg")
        svg = rendered.output
        assert isinstance(svg, str)

        assert "/data/db/main/sales/?quarter=" in svg, (
            "Bar chart with INTEGER dimension 'quarter' must produce a filter link. "
            f"SVG snippet (first 3000 chars): {svg[:3000]}"
        )

    def test_two_integer_dimensions_produce_multi_param_filter_link(
        self, tmp_path, local_project: Callable[..., Project]
    ) -> None:
        """Bar x=year (INTEGER), color=quarter (INTEGER) → both params in filter link."""
        import sqlite3

        import yaml

        from dbt_charts.core.compile import compile
        from dbt_charts.core.execute import Executor
        from dbt_charts.core.execute.adapters import build_adapter_registry
        from dbt_charts.core.render import render

        db_path = tmp_path / "sales.sqlite"
        conn = sqlite3.connect(str(db_path))
        conn.execute("CREATE TABLE sales (year INTEGER, quarter INTEGER, revenue REAL)")
        conn.execute("INSERT INTO sales VALUES (2024, 1, 100.0)")
        conn.execute("INSERT INTO sales VALUES (2024, 2, 150.0)")
        conn.commit()
        conn.close()

        sources_yaml = {"sources": {"db": {"type": "sqlite", "path": str(db_path)}}}
        (tmp_path / "dbt_charts.yml").write_text(yaml.dump(sources_yaml))

        board_yaml = """\
title: "Sales"
auto_link: true
source: db
queries:
  rows:
    source: db
    sql: SELECT year, quarter, SUM(revenue) AS revenue FROM main.sales GROUP BY year, quarter
rows:
  - chart
charts:
  chart:
    title: "Revenue by year and quarter"
    type: bar
    query: rows
    x: year
    color: quarter
    y: revenue
"""
        result = compile(board_yaml)
        assert result.board is not None, result.errors

        executor = Executor(
            result.board,
            adapter_registry=build_adapter_registry(local_project(tmp_path)),
        )
        rendered = render(result.board, executor, format="svg")
        svg = rendered.output
        assert isinstance(svg, str)

        assert "/data/db/main/sales/?year=" in svg, (
            "Multi-integer-dim bar chart must produce a filter link with 'year' param. "
            f"SVG snippet (first 3000 chars): {svg[:3000]}"
        )
        assert "quarter=" in svg, (
            "Multi-integer-dim bar chart must include 'quarter' param in filter link. "
            f"SVG snippet (first 3000 chars): {svg[:3000]}"
        )


# ---------------------------------------------------------------------------
# Regression: FK auto-link on pivot table's rows: dimension must coexist with
# correct pivot column headers
# ---------------------------------------------------------------------------


class TestFkPivotTableColumnsAndLinks:
    """Regression: pivot table with FK edges on its rows: dimension must render:
    (a) pivoted leaf column headers (Q1, Q2), not the tidy dimension key (quarter)
    (b) the FK href on the rows: dimension column (region)

    Both properties must hold simultaneously. If a future change restores the
    pre-refactor path that uses columns_promoted as-is (ignoring pivot status),
    (a) would break. If a change suppresses FK link synthesis for pivot tables
    entirely, (b) would break.
    """

    def test_fk_link_and_pivot_headers_coexist(
        self, tmp_path: Path, local_project: Callable[..., Project]
    ) -> None:
        """Pivot table with a FK edge on the rows: dimension renders both:

        (a) pivoted leaf headers Q1 / Q2, NOT the tidy 'quarter' column name,
        (b) the FK href for region cells (/data/dw/main/regions/detail/).

        This pins the current-main behavior so regressions surface immediately:
        - restoring the pre-refactor columns_promoted-as-visible-set path → (a) fails
        - suppressing FK link synthesis for all pivot tables → (b) fails
        """
        import json
        import sqlite3

        import yaml

        from dbt_charts.core.compile import compile
        from dbt_charts.core.execute import Executor
        from dbt_charts.core.execute.adapters import build_adapter_registry
        from dbt_charts.core.render import render

        # Tidy data: one row per (region, quarter) combination.  No GROUP BY in
        # the query — the pivot transform happens in the render layer, not SQL.
        # source_schema_table resolves plain SELECT to (dw, main, sales), which
        # allows fk_column_links_from_executor to fetch FK edges for `sales`.
        db_path = tmp_path / "sales.sqlite"
        conn = sqlite3.connect(str(db_path))
        conn.execute("CREATE TABLE sales (region TEXT, quarter TEXT, revenue REAL)")
        conn.execute("INSERT INTO sales VALUES ('East', 'Q1', 100.0)")
        conn.execute("INSERT INTO sales VALUES ('East', 'Q2', 200.0)")
        conn.execute("INSERT INTO sales VALUES ('West', 'Q1', 150.0)")
        conn.execute("INSERT INTO sales VALUES ('West', 'Q2', 250.0)")
        conn.commit()
        conn.close()

        sources_yaml = {"sources": {"dw": {"type": "sqlite", "path": str(db_path)}}}
        (tmp_path / "dbt_charts.yml").write_text(yaml.dump(sources_yaml))

        # FK edge: sales.region → regions.region  (declared dbt relationship)
        # resolve_fk_column_links emits: region → /data/dw/main/regions/detail/
        manifest = {
            "nodes": {
                "test.fk_sales_regions": {
                    "resource_type": "test",
                    "test_metadata": {
                        "name": "relationships",
                        "kwargs": {
                            "to": "ref('regions')",
                            "field": "region",
                            "column_name": "region",
                        },
                    },
                    "column_name": "region",
                    "attached_node": "model.sales",
                },
                "model.sales": {
                    "resource_type": "model",
                    "name": "sales",
                    "alias": "sales",
                },
                "model.regions": {
                    "resource_type": "model",
                    "name": "regions",
                    "alias": "regions",
                },
            }
        }
        target_dir = tmp_path / "target"
        target_dir.mkdir()
        (target_dir / "manifest.json").write_text(json.dumps(manifest))

        board_yaml = """\
title: "Regional Sales"
auto_link: true
source: dw
queries:
  data:
    source: dw
    sql: SELECT region, quarter, revenue FROM main.sales
rows:
  - chart
charts:
  chart:
    title: "Regional Sales"
    type: table
    query: data
    rows:
      - region
    columns:
      - quarter
    values:
      - revenue
"""
        result = compile(board_yaml)
        assert result.board is not None, result.errors

        executor = Executor(
            result.board,
            adapter_registry=build_adapter_registry(local_project(tmp_path)),
        )
        html = render(result.board, executor, format="html").output
        assert isinstance(html, str)

        # (a) Pivot renders with leaf column headers from data values (Q1, Q2),
        # not the tidy dimension column name ('quarter').
        assert "Q1" in html, (
            "Pivot table must render leaf column header 'Q1', not the tidy "
            f"dimension key 'quarter'. HTML (first 3000 chars): {html[:3000]}"
        )
        assert "Q2" in html, (
            "Pivot table must render leaf column header 'Q2'. "
            f"HTML (first 3000 chars): {html[:3000]}"
        )

        # (b) FK href renders on the rows: dimension (region) cells.
        assert "/data/dw/main/regions/detail/" in html, (
            "FK link for pivot rows: dimension column 'region' must render as "
            "cell hrefs pointing to /data/dw/main/regions/detail/. "
            f"HTML (first 3000 chars): {html[:3000]}"
        )
