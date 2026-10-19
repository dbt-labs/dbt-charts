"""Unit tests for dbt_charts.core.render.chart.sql_grain: pure AST-level grain
and column-alias tracing, with no adapter/render dependency.

Full-render, end-to-end auto-link integration tests (which exercise
`sql_grain` indirectly through `automatic_link.py`) stay in `test_automatic_link.py`.
"""

from __future__ import annotations

import pytest


class TestCteAliasMapUnit:
    """build_col_output_alias_map's CTE branch — unit-level, no adapter needed."""

    def test_cte_alias_case_insensitive_match_resolves(self) -> None:
        """Regression test: unquoted SQL identifiers are case-insensitive —
        `WITH Resolved AS (...) SELECT * FROM resolved` is the same CTE
        reference the outer FROM already reads from, not a mismatch. A
        naive exact-case comparison would bail on this ordinary shape
        (mixed-case CTE names are common when hand-writing a WITH header),
        losing a drill the feature exists to produce.
        """
        from dbt_charts.core.render.chart.sql_grain import parse_sql, resolve_cte_spine

        tree = parse_sql(
            "WITH Resolved AS (SELECT * FROM main.tickets) SELECT * FROM resolved"
        )
        assert tree is not None
        assert resolve_cte_spine(tree) == ("main", "tickets")

    def test_outer_rename_of_cte_alias_does_not_leak_into_base_columns(self) -> None:
        """Regression test: when the CTE body has its own explicit projection
        (not a pass-through `SELECT *`), the outer SELECT's column names are
        the *CTE's* output names, not base-table column names — composing
        them as if they were base columns mis-maps any base column that
        happens to share that name.

        `WITH c AS (SELECT id AS a, subject FROM t) SELECT a AS z, subject
        FROM c` must map base `id -> z` (traced through `a`), and must NOT
        add a spurious `a -> z` entry — `a` is the CTE's own alias, not a
        base table column, and a real base column literally named `a` would
        be mis-resolved by such an entry.
        """
        from dbt_charts.core.render.chart.sql_grain import build_col_output_alias_map

        alias_map, _ = build_col_output_alias_map(
            "WITH c AS (SELECT id AS a, subject FROM main.t)"
            " SELECT a AS z, subject FROM c"
        )
        assert alias_map.get("id") == "z"
        assert "a" not in alias_map, (
            "The CTE's own output alias ('a') must not appear as a mapped key "
            f"— it isn't a base table column. Got: {alias_map}"
        )

    def test_identity_safe_for_unlisted_false_when_explicit_projection_omits_key(
        self,
    ) -> None:
        """`identity_safe_for_unlisted` is False whenever either layer has an
        explicit (non-star) projection — a column absent from `alias_map` in
        that case is proven absent from the output, not safe to assume
        identity for."""
        from dbt_charts.core.render.chart.sql_grain import build_col_output_alias_map

        _, identity_safe = build_col_output_alias_map(
            "WITH c AS (SELECT ticket_number AS id, subject FROM main.tickets)"
            " SELECT * FROM c"
        )
        assert identity_safe is False

    def test_identity_safe_for_unlisted_true_for_full_star_pass_through(self) -> None:
        """`identity_safe_for_unlisted` is True only for a full star-to-star
        pass-through, where every base column provably reaches the output
        under its own name."""
        from dbt_charts.core.render.chart.sql_grain import build_col_output_alias_map

        alias_map, identity_safe = build_col_output_alias_map(
            "WITH c AS (SELECT * FROM main.tickets) SELECT * FROM c"
        )
        assert alias_map == {}
        assert identity_safe is True

    def test_star_plus_explicit_alias_is_not_identity_safe(self) -> None:
        """Regression test: `SELECT *, ticket_number AS id` is not a bare
        `SELECT *` — the identity check must be strict, or the un-listed
        base `id` gets wrongly assumed identity-safe when `ticket_number`
        was actually aliased onto that same output name.
        """
        from dbt_charts.core.render.chart.sql_grain import build_col_output_alias_map

        alias_map, identity_safe = build_col_output_alias_map(
            "WITH c AS (SELECT *, ticket_number AS id FROM main.tickets) SELECT * FROM c"
        )
        assert identity_safe is False
        assert alias_map.get("ticket_number") == "id"

        alias_map2, identity_safe2 = build_col_output_alias_map(
            "WITH c AS (SELECT *, ticket_number AS id FROM main.tickets)"
            " SELECT id, subject FROM c"
        )
        assert identity_safe2 is False
        # "id" must not appear mapped to itself — it's proven to be
        # ticket_number's alias, not a pass-through of the base "id" (which
        # this query never selects at all).
        assert alias_map2.get("id") is None or alias_map2["id"] != "id"

    def test_qualified_star_is_identity_safe(self) -> None:
        """Regression test: `SELECT t.* FROM main.tickets t` is a qualified
        star, not a bare `*` — but with only one table in scope, `t.*` and
        `*` denote the exact same column set, so it must still be treated
        as full pass-through. A star check stricter than necessary
        (matching only `exp.Star`, not `exp.Column(this=exp.Star())`) would
        wrongly treat it as an explicit projection, produce a useless
        `{"*": "*"}` entry, and lose the auto-link entirely for this
        ordinary query shape — a real regression versus the pre-task
        behavior where every plain query got identity assumed.
        """
        from dbt_charts.core.render.chart.sql_grain import build_col_output_alias_map

        alias_map, identity_safe = build_col_output_alias_map(
            "SELECT t.* FROM main.tickets t"
        )
        assert alias_map == {}
        assert identity_safe is True

    def test_cte_column_alias_list_bails(self) -> None:
        """Regression test: `WITH t(ticket_number, id) AS (SELECT id,
        ticket_number FROM main.tickets) SELECT * FROM t` renames the CTE
        body's projected columns *positionally* via the column-alias list —
        the body's 1st projected column (`id`) becomes output `ticket_number`,
        and its 2nd (`ticket_number`) becomes output `id`. Without a bail for
        this syntax, the tracer derives output names straight from the
        body's own projection (`id -> id`) and gets it exactly backwards —
        the output column actually named `id` holds `ticket_number` values.
        """
        from dbt_charts.core.render.chart.sql_grain import (
            build_col_output_alias_map,
            parse_sql,
            resolve_cte_spine,
        )

        sql = (
            "WITH t(ticket_number, id) AS (SELECT id, ticket_number FROM main.tickets)"
            " SELECT * FROM t"
        )
        tree = parse_sql(sql)
        assert tree is not None
        assert not resolve_cte_spine(tree)
        assert build_col_output_alias_map(sql) == ({}, False)

    def test_duplicate_output_column_name_excluded_from_map(self) -> None:
        """Regression test: `SELECT id, ticket_number AS id FROM main.tickets`
        has two projections both claiming output name `id` — the executed
        query has exactly one `id` column, and nothing in the projection
        list proves which source column's value survives into it. Tracing
        `id -> id` (matching the base PK's own name) would look like proof
        when it's actually a coin flip against `ticket_number`'s value.
        """
        from dbt_charts.core.render.chart.sql_grain import build_col_output_alias_map

        alias_map, identity_safe = build_col_output_alias_map(
            "SELECT id, ticket_number AS id FROM main.tickets"
        )
        assert "id" not in alias_map
        assert identity_safe is False

    def test_duplicate_output_column_name_across_join_sides_excluded(self) -> None:
        """Same bug, JOIN shape: `SELECT o.id, c.id, c.name FROM orders o
        JOIN customers c ...` — both `o.id` and `c.id` project to output
        `id`. The qualifier filter alone would keep `o.id` (it matches the
        spine alias `o`), but the collision with `c.id` makes the *actual*
        output `id` column's value ambiguous regardless — must still be
        excluded, not just filtered by qualifier.
        """
        from dbt_charts.core.render.chart.sql_grain import build_col_output_alias_map

        alias_map, identity_safe = build_col_output_alias_map(
            "SELECT o.id, c.id, c.name FROM main.orders o"
            " JOIN main.customers c ON o.cust_id = c.id"
        )
        assert "id" not in alias_map
        assert identity_safe is False

    def test_compose_loop_omits_column_outer_explicitly_drops(self) -> None:
        """Regression test for the same bug class in the compose loop itself:
        a CTE-body column not selected by an *explicit* (non-star) outer
        SELECT is proven absent from the final output — it must not appear
        in `alias_map` under an assumed-identity name.

        `WITH c AS (SELECT id, subject, extra FROM t) SELECT id, subject
        FROM c` never outputs `extra` — composing `extra -> extra` via a
        naive identity fallback would wrongly claim it's present.
        """
        from dbt_charts.core.render.chart.sql_grain import build_col_output_alias_map

        alias_map, identity_safe = build_col_output_alias_map(
            "WITH c AS (SELECT id, subject, extra FROM main.t)"
            " SELECT id, subject FROM c"
        )
        assert "extra" not in alias_map, (
            "extra is not selected by the outer SELECT and must not be "
            f"assumed to pass through. Got: {alias_map}"
        )
        assert identity_safe is False


class TestPlainQueryColOutputAliasMap:
    """build_col_output_alias_map's plain (no CTE, no JOIN) branch."""

    def test_duplicate_output_column_name_via_expression_alias_excluded_from_map(
        self,
    ) -> None:
        """Regression test: `SELECT id, UPPER(subject) AS id FROM t` has two
        projections both claiming output name `id` — one a bare column, the
        other an expression. The duplicate-output check
        used to only tally column-sourced projections toward the collision
        count, so an expression-aliased duplicate was invisible to it and
        the bare `id -> id` entry survived unexcluded — tracing `id` to
        itself when the executed query's single `id` column might actually
        hold `UPPER(subject)`'s value instead.
        """
        from dbt_charts.core.render.chart.sql_grain import build_col_output_alias_map

        alias_map, identity_safe = build_col_output_alias_map(
            "SELECT id, UPPER(subject) AS id FROM main.tickets"
        )
        assert "id" not in alias_map
        assert identity_safe is False

    def test_duplicate_output_column_name_differing_only_by_case_excluded(
        self,
    ) -> None:
        """Regression test: `SELECT id, ticket_number AS ID FROM t` has two
        projections whose output names differ only by case. Most engines
        fold unquoted output column names to one canonical case, so the
        executed result has exactly one `id`/`ID` column — a case-sensitive
        collision counter would treat `id` and `ID` as two distinct output
        names, miss the collision, and let `id -> id` survive into
        `alias_map` even though the surviving value might actually be
        `ticket_number`'s.
        """
        from dbt_charts.core.render.chart.sql_grain import build_col_output_alias_map

        alias_map, identity_safe = build_col_output_alias_map(
            "SELECT id, ticket_number AS ID FROM main.tickets"
        )
        assert "id" not in alias_map
        assert identity_safe is False

    def test_star_plus_expression_alias_plain_query_proves_nothing(self) -> None:
        """Known coverage gap, documented rather than silently regressed:
        `SELECT *, UPPER(subject) AS shout FROM t` (a star alongside an
        explicit projection, no CTE/JOIN) is not a bare `SELECT *`, so
        the identity check rejects it and the trace
        only follows bare-column projections — the
        star itself is invisible to it. Every base column (e.g. `id`) is
        therefore untraced, not just the shadowed name, and
        `identity_safe_for_unlisted` is `False`.

        This fails safe (no drill emitted, never a wrong one) but is a real
        coverage loss versus this exact shape on `main`, where a plain query
        always assumed identity unconditionally — before this task added
        the collision detection that this exact shape needs.
        """
        from dbt_charts.core.render.chart.sql_grain import build_col_output_alias_map

        alias_map, identity_safe = build_col_output_alias_map(
            "SELECT *, UPPER(subject) AS shout FROM main.tickets"
        )
        assert "id" not in alias_map
        assert identity_safe is False

    def test_star_plus_alias_collision_is_an_undetected_gap(self) -> None:
        """Known coverage gap, documented rather than silently fixed:
        `SELECT t.*, t.id AS subject FROM main.tickets t` (`tickets` has
        columns `id, subject`) produces two `subject` output columns in the
        executed result — one from the star, one from the explicit alias —
        but the collision counter tallies only listed projections; a star
        over a table (`t.*`) lists none, so the columns it expands to are
        invisible to the counter and no collision is flagged for `subject`.

        This is the same wrong-drill class the duplicate-output-name check
        exists to catch, reached through a shape the check cannot see —
        detecting it would need the base column list, which
        `build_col_output_alias_map` deliberately does not take (it reasons
        about the SQL text alone). Low-probability shape (needs an alias
        whose output name collides with a base column the star already
        emits); documented here rather than redesigned.
        """
        from dbt_charts.core.render.chart.sql_grain import build_col_output_alias_map

        alias_map, identity_safe = build_col_output_alias_map(
            "SELECT t.*, t.id AS subject FROM main.tickets t"
        )
        assert alias_map == {"id": "subject"}
        assert identity_safe is False


# ---------------------------------------------------------------------------
# JOIN grain resolution: FK-proven JOINs
# ---------------------------------------------------------------------------


class TestJoinColOutputAliasMap:
    """build_col_output_alias_map's JOIN branch — unit-level, no adapter needed."""

    def test_spine_qualified_columns_are_traced(self) -> None:
        from dbt_charts.core.render.chart.sql_grain import build_col_output_alias_map

        alias_map, identity_safe = build_col_output_alias_map(
            "SELECT o.id, o.amount, c.name FROM main.orders o"
            " JOIN main.customers c ON o.cust_id = c.id"
        )
        assert alias_map == {"id": "id", "amount": "amount"}
        assert identity_safe is False

    def test_non_spine_column_sharing_key_name_is_not_traced(self) -> None:
        """The reviewed failure scenario: output has an 'id' column, but it's
        customers.id, not orders.id (the spine) — must not appear in the map."""
        from dbt_charts.core.render.chart.sql_grain import build_col_output_alias_map

        alias_map, identity_safe = build_col_output_alias_map(
            "SELECT c.id, c.name, o.amount FROM main.orders o"
            " JOIN main.customers c ON o.cust_id = c.id"
        )
        assert "id" not in alias_map
        assert alias_map == {"amount": "amount"}
        assert identity_safe is False

    def test_spine_column_aliased_is_traced_by_output_name(self) -> None:
        from dbt_charts.core.render.chart.sql_grain import build_col_output_alias_map

        alias_map, identity_safe = build_col_output_alias_map(
            "SELECT o.id AS order_id, c.name FROM main.orders o"
            " JOIN main.customers c ON o.cust_id = c.id"
        )
        assert alias_map == {"id": "order_id"}
        assert identity_safe is False

    def test_star_projection_across_join_is_never_identity_safe(self) -> None:
        """Unlike the CTE case, a bare SELECT * across a join is ambiguous —
        two tables can share a column name — so identity is never assumed."""
        from dbt_charts.core.render.chart.sql_grain import build_col_output_alias_map

        alias_map, identity_safe = build_col_output_alias_map(
            "SELECT * FROM main.orders o JOIN main.customers c ON o.cust_id = c.id"
        )
        assert alias_map == {}
        assert identity_safe is False

    def test_cte_alongside_join_is_never_identity_safe(self) -> None:
        """Regression test: a query with an (unrelated) CTE *and* a top-level
        JOIN matched neither `build_col_output_alias_map`'s CTE branch
        (`not has_joins` required) nor its JOIN branch (`not ctes` required),
        so it fell through to the "plain query" default of `({}, True)` —
        wrongly assuming identity for a shape that was never traced at all.
        """
        from dbt_charts.core.render.chart.sql_grain import build_col_output_alias_map

        alias_map, identity_safe = build_col_output_alias_map(
            "WITH thresholds AS (SELECT 100 AS min_amount)"
            " SELECT c.id, c.name, o.amount FROM main.orders o"
            " JOIN main.customers c ON o.cust_id = c.id"
        )
        assert alias_map == {}
        assert identity_safe is False


class TestScopeTracedColOutputAliasMap:
    """Tracing follows the scope walk, so only what the SQL proves is mapped."""

    @staticmethod
    def _map(sql: str) -> tuple[dict[str, str], bool]:
        from dbt_charts.core.render.chart.sql_grain import build_col_output_alias_map

        return build_col_output_alias_map(sql)

    def test_qualified_star_of_the_joined_side_is_not_identity(self) -> None:
        """`c.*` carries customers' columns, not the spine's."""
        assert self._map(
            "SELECT c.* FROM main.orders o JOIN main.customers c ON o.cust_id = c.id"
        ) == ({}, False)

    def test_qualified_star_of_the_spine_across_a_join_proves_nothing(self) -> None:
        assert self._map(
            "SELECT o.* FROM main.orders o JOIN main.customers c ON o.cust_id = c.id"
        ) == ({}, False)

    def test_cte_referenced_in_another_case_is_still_the_cte(self) -> None:
        """`FROM T` reads the CTE `t`; treating it as a base table would trace
        `id` to itself when the CTE's `id` holds `ticket_number`."""
        alias_map, identity_safe = self._map(
            "WITH t AS (SELECT ticket_number AS id, subject FROM main.tickets)"
            " SELECT id, subject FROM T"
        )
        assert alias_map == {"ticket_number": "id", "subject": "subject"}
        assert identity_safe is False
        assert self._map(
            "WITH t AS (SELECT ticket_number AS id FROM main.tickets) SELECT * FROM T"
        ) == ({"ticket_number": "id"}, False)

    def test_qualifier_in_another_case_still_traces(self) -> None:
        assert self._map("SELECT T.id FROM main.tickets AS t") == ({"id": "id"}, False)

    def test_scope_binding_error_traces_nothing(self) -> None:
        assert self._map("SELECT s1.t.id FROM s1.t JOIN s2.t ON s1.t.id = s2.t.id") == (
            {},
            False,
        )

    def test_if_else_fragment_still_traces(self) -> None:
        alias_map, _ = self._map(
            "SELECT id AS tid FROM main.tickets WHERE 1=1"
            " {% if status %} AND s = 1 {% else %} AND x = 2 {% endif %}"
        )
        assert alias_map == {"id": "tid"}

    def test_multi_cte_chain_is_traced(self) -> None:
        alias_map, identity_safe = self._map(
            "WITH a AS (SELECT id AS x, subject FROM main.t),"
            " b AS (SELECT x AS y FROM a) SELECT y AS z FROM b"
        )
        assert alias_map == {"id": "z"}
        assert identity_safe is False

    def test_from_subquery_is_traced(self) -> None:
        alias_map, _ = self._map(
            "SELECT a AS z FROM (SELECT id AS a, subject FROM main.t) s"
        )
        assert alias_map == {"id": "z"}

    def test_subquery_column_alias_list_traces_nothing(self) -> None:
        assert self._map(
            "SELECT * FROM (SELECT id, subject FROM main.t) AS s(subject, id)"
        ) == ({}, False)

    def test_star_over_cte_with_duplicate_names_excludes_them(self) -> None:
        alias_map, _ = self._map(
            "WITH c AS (SELECT id, ticket_number AS id, subject FROM main.t)"
            " SELECT * FROM c"
        )
        assert alias_map == {"subject": "subject"}

    def test_column_named_like_a_sibling_alias_is_not_traced(self) -> None:
        """`x` in `SELECT id AS x, x AS y` may be the alias or a base column,
        depending on the dialect; fail closed even where the old walker linked."""
        alias_map, _ = self._map("SELECT id AS x, x AS y FROM main.t")
        assert alias_map == {"id": "x"}


class TestSelfNamedQualifier:
    def test_cte_qualifier_naming_itself_traces_nothing(self) -> None:
        """`payload.id` inside CTE `payload` may be a struct column; following it
        to the CTE itself would recurse forever."""
        from dbt_charts.core.render.chart.sql_grain import build_col_output_alias_map

        assert build_col_output_alias_map(
            "WITH payload AS (SELECT payload.id FROM main.events e)"
            " SELECT id FROM payload"
        ) == ({}, False)


def test_recursion_overflow_traces_nothing(monkeypatch: pytest.MonkeyPatch) -> None:
    from dbt_charts.core.render.chart import sql_grain

    def overflow(scope: object) -> None:
        raise RecursionError

    monkeypatch.setattr(sql_grain, "_outputs", overflow)
    assert sql_grain.build_col_output_alias_map("SELECT id FROM main.t") == ({}, False)


class TestColumnNameCaseAcrossLayers:
    """Unquoted column names are case-insensitive, so a layer reads them so."""

    def test_outer_column_in_another_case_reads_the_cte_column(self) -> None:
        from dbt_charts.core.render.chart.sql_grain import build_col_output_alias_map

        assert build_col_output_alias_map(
            "WITH c AS (SELECT id FROM main.t) SELECT ID FROM c"
        ) == ({"id": "ID"}, False)
        assert build_col_output_alias_map(
            "WITH c AS (SELECT id AS Tid FROM main.t) SELECT tid FROM c"
        ) == ({"id": "tid"}, False)

    def test_columns_differing_by_more_than_case_do_not_match(self) -> None:
        from dbt_charts.core.render.chart.sql_grain import build_col_output_alias_map

        assert build_col_output_alias_map(
            "WITH c AS (SELECT id AS tid FROM main.t) SELECT t_id FROM c"
        ) == ({}, False)
