"""Tests for the dbt-side attribution transport.

The comment and the label payload are driven independently, so BigQuery can take
structured labels while its query text stays byte-identical.
"""

from __future__ import annotations

import json

from dbt_charts.core.attribution import attribute
from dbt_charts.core.execute.backends.query_header import QueryHeader


class TestQueryHeader:
    def test_payload_carries_engine_identity(self) -> None:
        header = QueryHeader(emit_sql_comment=True)
        payload = json.loads(header.comment.query_comment)
        assert payload["app"] == "dbt-charts"
        assert "dbt_charts_version" in payload

    def test_payload_carries_authored_attribution(self) -> None:
        header = QueryHeader(emit_sql_comment=True)
        with attribute({}, {"team": "architecture-analytics"}):
            payload = json.loads(header.comment.query_comment)
        assert payload["team"] == "architecture-analytics"

    def test_authored_attribution_does_not_outlive_its_scope(self) -> None:
        """One header serves a shared pool — a captured value would mislabel the
        next source's queries."""
        header = QueryHeader(emit_sql_comment=True)
        with attribute({}, {"team": "marketing"}):
            pass
        assert "team" not in json.loads(header.comment.query_comment)

    def test_each_scope_sees_its_own_authored_attribution(self) -> None:
        header = QueryHeader(emit_sql_comment=True)
        with attribute({}, {"team": "marketing"}):
            first = json.loads(header.comment.query_comment)["team"]
        with attribute({}, {"team": "finance"}):
            second = json.loads(header.comment.query_comment)["team"]
        assert (first, second) == ("marketing", "finance")

    def test_payload_carries_runtime_context(self) -> None:
        header = QueryHeader(emit_sql_comment=True)
        with attribute({"surface": "cloud", "board": "revenue"}, {}):
            payload = json.loads(header.comment.query_comment)
        assert payload["dbt_charts_surface"] == "cloud"
        assert payload["dbt_charts_board"] == "revenue"

    def test_add_prepends_the_payload_as_a_comment(self) -> None:
        header = QueryHeader(emit_sql_comment=True)
        out = header.add("SELECT 1")
        assert out.startswith("/* {")
        assert out.endswith("SELECT 1")

    def test_add_is_a_noop_when_the_comment_is_suppressed(self) -> None:
        """BigQuery takes labels only — its query text must stay byte-identical."""
        header = QueryHeader(emit_sql_comment=False)
        assert header.add("SELECT 1") == "SELECT 1"

    def test_labels_survive_when_the_comment_is_suppressed(self) -> None:
        """The two members are independent: suppressing one must not empty the other."""
        header = QueryHeader(emit_sql_comment=False)
        with attribute({}, {"team": "x"}):
            payload = json.loads(header.comment.query_comment)
        assert payload["team"] == "x"
        assert payload["app"] == "dbt-charts"

    def test_survives_dbts_connection_lifecycle(self) -> None:
        """`connection_named` calls set()/reset() on every connection open."""
        header = QueryHeader(emit_sql_comment=True)
        header.set("dbt_charts_query", None)
        header.reset()
        with attribute({}, {"team": "x"}):
            assert json.loads(header.comment.query_comment)["team"] == "x"

    def test_payload_never_closes_the_comment_early(self) -> None:
        """`*/` inside the payload would terminate the comment and corrupt the SQL."""
        header = QueryHeader(emit_sql_comment=True)
        with attribute({"surface": "cli", "board": "a/b"}, {}):
            assert "*/" not in header.comment.query_comment
