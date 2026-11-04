"""Coverage for the board relation extractor."""

from __future__ import annotations

import pytest

from dbt_charts.core.compile.models.query.normalized import (
    SchemaQuery,
    SqlQuery,
    ValuesQuery,
)
from dbt_charts.core.inspect.relations import Relation, relations_read_by


def _sql(sql: str) -> SqlQuery:
    return SqlQuery(sql=sql, source="warehouse")


def test_a_plain_select_yields_its_table() -> None:
    read = relations_read_by({"q": _sql("SELECT id FROM analytics.orders")})

    assert read.relations == frozenset({Relation(schema="analytics", name="orders")})
    assert read.undetermined == ()


def test_an_unqualified_table_yields_a_relation_with_no_schema() -> None:
    read = relations_read_by({"q": _sql("SELECT id FROM orders")})

    assert read.relations == frozenset({Relation(schema=None, name="orders")})


def test_a_three_part_name_keeps_its_database() -> None:
    read = relations_read_by({"q": _sql("SELECT 1 FROM prod.analytics.orders")})

    assert read.relations == frozenset(
        {Relation(database="prod", schema="analytics", name="orders")}
    )


def test_every_joined_relation_is_returned() -> None:
    read = relations_read_by(
        {
            "q": _sql(
                "SELECT * FROM analytics.orders o "
                "JOIN analytics.customers c ON c.id = o.customer_id "
                "LEFT JOIN raw.regions r ON r.id = c.region_id"
            )
        }
    )

    assert read.relations == frozenset(
        {
            Relation(schema="analytics", name="orders"),
            Relation(schema="analytics", name="customers"),
            Relation(schema="raw", name="regions"),
        }
    )


def test_a_cte_name_is_never_returned_as_a_relation() -> None:
    """Probing a CTE is a guaranteed failure against a nonsense name.

    `find_all(exp.Table)` sees a CTE *reference* as a table, so the aliases have
    to be subtracted or every CTE-bearing board reports a phantom relation.
    """
    read = relations_read_by(
        {
            "q": _sql(
                "WITH recent AS (SELECT * FROM analytics.orders) SELECT * FROM recent"
            )
        }
    )

    assert read.relations == frozenset({Relation(schema="analytics", name="orders")})


def test_nested_and_multiple_ctes_are_all_subtracted() -> None:
    read = relations_read_by(
        {
            "q": _sql(
                "WITH a AS (SELECT * FROM raw.events), "
                "b AS (SELECT * FROM a JOIN raw.users u ON TRUE) "
                "SELECT * FROM b"
            )
        }
    )

    assert read.relations == frozenset(
        {Relation(schema="raw", name="events"), Relation(schema="raw", name="users")}
    )


def test_a_schema_qualified_table_sharing_a_cte_name_is_kept() -> None:
    """`analytics.orders` is a real relation even when a CTE is also called `orders`.

    Only an unqualified reference can be a CTE — subtracting by bare name would
    drop a table the board genuinely reads.
    """
    read = relations_read_by(
        {
            "q": _sql(
                "WITH orders AS (SELECT * FROM raw.orders_landing) "
                "SELECT * FROM orders UNION ALL SELECT * FROM analytics.orders"
            )
        }
    )

    assert Relation(schema="analytics", name="orders") in read.relations
    assert Relation(schema=None, name="orders") not in read.relations


def test_cte_matching_is_case_insensitive() -> None:
    """Unquoted SQL identifiers are case-insensitive; `Recent` and `recent` are one."""
    read = relations_read_by(
        {"q": _sql("WITH recent AS (SELECT * FROM raw.t) SELECT * FROM Recent")}
    )

    assert read.relations == frozenset({Relation(schema="raw", name="t")})


def test_a_subquery_is_not_a_relation() -> None:
    read = relations_read_by(
        {"q": _sql("SELECT * FROM (SELECT id FROM analytics.orders) sub")}
    )

    assert read.relations == frozenset({Relation(schema="analytics", name="orders")})


def test_the_same_relation_read_twice_is_returned_once() -> None:
    read = relations_read_by(
        {
            "a": _sql("SELECT 1 FROM analytics.orders"),
            "b": _sql("SELECT 2 FROM analytics.orders"),
        }
    )

    assert read.relations == frozenset({Relation(schema="analytics", name="orders")})


def test_a_values_query_contributes_nothing() -> None:
    """It touches no warehouse, so it has no relation to probe."""
    read = relations_read_by({"v": ValuesQuery(values=[[1], [2]], columns=["x"])})

    assert read.relations == frozenset()
    assert read.undetermined == ()


def test_a_schema_query_yields_its_literal_table() -> None:
    """SchemaQuery names its relation in fields and forbids Jinja — no parsing needed."""
    read = relations_read_by(
        {"s": SchemaQuery(schema="analytics", table="orders", source="warehouse")}
    )

    assert read.relations == frozenset({Relation(schema="analytics", name="orders")})


def test_a_schema_query_without_a_table_is_an_introspection_listing() -> None:
    """`schema:` with no `table:` lists tables — it reads no single relation."""
    read = relations_read_by({"s": SchemaQuery(schema="analytics", source="warehouse")})

    assert read.relations == frozenset()


def test_unparseable_sql_is_reported_not_silently_dropped() -> None:
    """An empty result would read as "nothing to check" and pass a credential
    that can read nothing. The query name has to surface instead."""
    read = relations_read_by({"broken": _sql("SELECT FROM WHERE ((")})

    assert read.relations == frozenset()
    assert read.undetermined == ("broken",)


def test_jinja_bearing_sql_is_reported_as_undetermined() -> None:
    """Unrendered dbt Jinja is not SQL. Guessing past it would invent relations."""
    read = relations_read_by({"t": _sql("SELECT * FROM {{ ref('orders') }}")})

    assert read.undetermined == ("t",)


def test_one_unparseable_query_does_not_hide_the_others() -> None:
    read = relations_read_by(
        {
            "ok": _sql("SELECT 1 FROM analytics.orders"),
            "bad": _sql("SELECT FROM WHERE (("),
        }
    )

    assert read.relations == frozenset({Relation(schema="analytics", name="orders")})
    assert read.undetermined == ("bad",)


def test_undetermined_names_are_sorted_for_a_stable_report() -> None:
    read = relations_read_by({"z": _sql("SELECT FROM (("), "a": _sql("SELECT FROM ((")})

    assert read.undetermined == ("a", "z")


@pytest.mark.parametrize(
    "sql",
    [
        "SELECT * FROM analytics.orders WHERE id IN (SELECT id FROM raw.flags)",
        "SELECT * FROM analytics.orders UNION SELECT * FROM raw.flags",
    ],
)
def test_relations_are_found_wherever_they_appear(sql: str) -> None:
    read = relations_read_by({"q": _sql(sql)})

    assert read.relations == frozenset(
        {
            Relation(schema="analytics", name="orders"),
            Relation(schema="raw", name="flags"),
        }
    )


def test_every_statement_of_a_multi_statement_query_is_read() -> None:
    """`parse_one` keeps the first statement and drops the rest silently.

    A dropped statement reads as "this query touches nothing else", so its
    relations go unprobed while the query reports as fully checked.
    """
    read = relations_read_by(
        {"q": _sql("SELECT id FROM analytics.orders; SELECT id FROM raw.flags")}
    )

    assert read.relations == frozenset(
        {
            Relation(schema="analytics", name="orders"),
            Relation(schema="raw", name="flags"),
        }
    )
    assert read.undetermined == ()


def test_a_table_function_is_not_a_relation() -> None:
    """`FROM lookback_days(7)` names a function, not something to probe.

    A DuckDB table macro, Snowflake `flatten`, or Postgres `jsonb_to_recordset`
    all parse as a table whose `this` is a function. Emitting one sends the probe
    after a name that was never in the warehouse.
    """
    read = relations_read_by(
        {"q": _sql("SELECT * FROM lookback_days(7) JOIN analytics.orders USING (id)")}
    )

    assert read.relations == frozenset({Relation(schema="analytics", name="orders")})


def test_jinja_outside_table_position_still_yields_its_relations() -> None:
    """The common real board: a variable filter in WHERE, literal tables in FROM.

    Refusing every query containing Jinja returned nothing for these, so the
    probe had nothing to check on exactly the boards it exists to check.
    """
    read = relations_read_by(
        {
            "q": _sql(
                "SELECT d.id FROM documents d "
                "LEFT JOIN users u ON d.owner_user_id = u.id "
                "WHERE {{ filter('owner_plan', plan) }}"
            )
        }
    )

    assert read.relations == frozenset(
        {Relation(name="documents"), Relation(name="users")}
    )
    assert read.undetermined == ()


def test_a_database_qualified_name_with_no_schema_keeps_its_database() -> None:
    """`prod..orders` is legal on Snowflake and T-SQL — the database is not a schema."""
    read = relations_read_by({"q": _sql("SELECT id FROM prod..orders")})

    assert read.relations == frozenset(
        {Relation(name="orders", schema=None, database="prod")}
    )


def test_a_templated_schema_is_undetermined_not_a_fabricated_relation() -> None:
    """`{{ target.schema }}.orders` is mainstream dbt, and the schema decides the read.

    Checking only the table name let the placeholder through in the schema slot:
    the probe chased a relation in no warehouse while the one the board really
    reads went unprobed, and the query still reported as fully checked.
    """
    read = relations_read_by({"q": _sql("SELECT * FROM {{ target.schema }}.orders")})

    assert read.relations == frozenset()
    assert read.undetermined == ("q",)


def test_setup_sql_relations_are_read_and_its_temp_is_not_emitted() -> None:
    """`setup_sql` runs on the same connection, so its reads need the same grant.

    Reading `sql` alone reported the temp view — a name no credential can ever be
    granted — and silently omitted `raw.events`, the only real warehouse read.
    """
    read = relations_read_by(
        {
            "q": SqlQuery(
                sql="SELECT * FROM recent",
                setup_sql="CREATE TEMP VIEW recent AS SELECT * FROM raw.events",
                source="warehouse",
            )
        }
    )

    assert read.relations == frozenset({Relation(schema="raw", name="events")})
    assert read.undetermined == ()


def test_an_if_branch_over_a_join_reports_both_sides() -> None:
    """A `{% if %}`-guarded JOIN must over-report, never under-report.

    A superset is the safe direction for a least-privilege probe: a relation the
    board might read is one the credential should be able to read.
    """
    read = relations_read_by(
        {
            "q": _sql(
                "SELECT o.id FROM analytics.orders o "
                "{% if with_users %}JOIN analytics.users u ON u.id = o.user_id{% endif %}"
            )
        }
    )

    assert read.relations == frozenset(
        {
            Relation(schema="analytics", name="orders"),
            Relation(schema="analytics", name="users"),
        }
    )


def test_a_templated_database_is_undetermined_too() -> None:
    """The third name part gets the same check as the first two.

    Without it, dropping `database` from the tuple keeps the suite green while
    a fabricated relation ships with `undetermined=()`.
    """
    read = relations_read_by(
        {"q": _sql("SELECT * FROM {{ target.database }}.analytics.orders")}
    )

    assert read.relations == frozenset()
    assert read.undetermined == ("q",)


def test_an_unsupported_jinja_node_is_reported_not_silently_dropped() -> None:
    """The skeletonizer default-denies node types it does not model.

    `{% macro %}` can carry SQL the walker never emits, so the skeleton would be
    a truthful parse of an untruthful query. Reporting the query as undetermined
    is the only honest answer; letting the error escape would take the whole
    board down over one query.
    """
    read = relations_read_by(
        {
            "q": _sql(
                "{% macro hidden() %}SELECT * FROM secret.table{% endmacro %}SELECT 1"
            )
        }
    )

    assert read.undetermined == ("q",)


def test_an_explain_statement_is_undetermined_not_empty() -> None:
    """sqlglot degrades a statement it does not model to an opaque Command.

    `EXPLAIN` is an accepted authored shape, and its inner SELECT still needs the
    grant. Walking a Command finds no tables at all, which reads as "reads
    nothing, fully checked" — the exact false clean this module exists to prevent.
    """
    read = relations_read_by(
        {"q": _sql("EXPLAIN ANALYZE SELECT * FROM analytics.orders")}
    )

    assert read.relations == frozenset()
    assert read.undetermined == ("q",)


def test_an_if_else_choosing_between_tables_is_undetermined() -> None:
    """The skeletonizer splits branches on `;`, so the else branch stands alone.

    A lone `hourly_orders` parses as a bare column expression, not a statement:
    the relation read whenever the condition is false was silently absent while
    the query reported as fully checked.
    """
    read = relations_read_by(
        {
            "q": _sql(
                "SELECT * FROM {% if daily %}daily_orders"
                "{% else %}hourly_orders{% endif %}"
            )
        }
    )

    assert read.undetermined == ("q",)


def test_a_temp_created_with_a_column_list_is_not_emitted() -> None:
    """A parenthesized column list wraps the target, hiding it from a direct read."""
    read = relations_read_by(
        {
            "q": SqlQuery(
                sql="SELECT * FROM recent",
                setup_sql=(
                    "CREATE TEMP VIEW recent (id, ts) AS SELECT id, ts FROM raw.events"
                ),
                source="warehouse",
            )
        }
    )

    assert read.relations == frozenset({Relation(schema="raw", name="events")})


def test_a_temp_function_is_not_emitted_as_a_relation() -> None:
    """`CREATE TEMP FUNCTION f(x INT) AS ...` wraps its name in a signature node.

    The temp's own name is a session-local object no GRANT can cover, so emitting
    it leaves the user with no action that makes the check pass.
    """
    read = relations_read_by(
        {
            "q": SqlQuery(
                sql="SELECT lookback(1) AS d FROM analytics.orders",
                setup_sql="CREATE TEMP FUNCTION lookback(n INT) AS (n * 2)",
                source="warehouse",
            )
        }
    )

    assert read.relations == frozenset({Relation(schema="analytics", name="orders")})


def test_a_qualified_table_sharing_a_temp_name_is_kept() -> None:
    """The temp subtraction is by *bare* name, exactly like the CTE one.

    Without that gate, a setup_sql temp called `orders` would delete
    `analytics.orders` from the result — the relation the board genuinely reads
    going unprobed while the query reports as fully checked.
    """
    read = relations_read_by(
        {
            "q": SqlQuery(
                sql="SELECT * FROM analytics.orders",
                setup_sql="CREATE TEMP VIEW orders AS SELECT * FROM raw.orders_landing",
                source="warehouse",
            )
        }
    )

    assert Relation(schema="analytics", name="orders") in read.relations


def test_a_templated_table_alias_is_undetermined() -> None:
    """`AS` is optional, so a bare `{{ … }}` after a table is parsed as its alias.

    The elided text can add relations of its own — `{{ optional_join() }}`
    expanding to `JOIN secret.audit …` — so the same refusal that covers the
    name, schema, and database slots has to cover the one next to them.
    """
    read = relations_read_by(
        {"q": _sql("SELECT * FROM analytics.orders {{ optional_join() }}")}
    )

    assert read.undetermined == ("q",)


def test_a_templated_slot_on_a_table_function_is_still_refused() -> None:
    """The empty-name skip must not run before the templated-slot refusal.

    A table function carries an alias like any other table, so skipping it on the
    empty name exited the loop body before the Jinja slots were read — and the
    macro's elided text can add a relation of its own.
    """
    read = relations_read_by(
        {
            "q": _sql(
                "SELECT * FROM lookback_days(7) {{ optional_join() }} "
                "JOIN analytics.orders USING (id)"
            )
        }
    )

    assert read.undetermined == ("q",)


def test_a_trailing_comment_does_not_make_a_query_undetermined() -> None:
    """`;` plus a trailing comment parses as a bare separator, not a statement."""
    read = relations_read_by(
        {"q": _sql("SELECT id FROM analytics.orders;  -- daily orders")}
    )

    assert read.relations == frozenset({Relation(schema="analytics", name="orders")})
    assert read.undetermined == ()


def test_a_macro_supplying_the_whole_from_clause_is_undetermined() -> None:
    """With no FROM in the authored text there is no table node to inspect.

    `SELECT count(*) {{ from_and_where() }}` parses as a select expression with
    an implicit alias, so a refusal scoped to table nodes never runs — while the
    macro can supply `FROM secret.audit` and the query reports as clean.
    """
    read = relations_read_by({"q": _sql("SELECT count(*) {{ from_and_where() }}")})

    assert read.undetermined == ("q",)


def test_an_unquoted_placeholder_in_a_relation_function_is_undetermined() -> None:
    """`IDENTIFIER({{ table_ref }})`, where the Jinja supplies its own quotes.

    The placeholder parses as a column reference — the one shape the value
    carve-out allows — but it sits inside a table reference, where it names the
    relation. Ancestry decides, so relation position overrides the carve-out.
    """
    read = relations_read_by({"q": _sql("SELECT * FROM IDENTIFIER({{ table_ref }})")})

    assert read.relations == frozenset()
    assert read.undetermined == ("q",)


def test_a_four_part_name_is_undetermined_rather_than_mis_assigned() -> None:
    """No Jinja needed: `srv.db.dbo.orders` has more parts than the model holds.

    catalog/db take the first two and the rest fold into a Dot, so every part
    lands one position out and `dbo` disappears — a relation that exists nowhere,
    reported as determined, with no grant the user could add to make it pass.
    """
    read = relations_read_by({"q": _sql("SELECT * FROM srv.db.dbo.orders")})

    assert read.relations == frozenset()
    assert read.undetermined == ("q",)


def test_a_templated_predicate_value_is_still_determinable() -> None:
    """The other half of the invariant: a placeholder standing for a *value*.

    A variable filter parses as a column reference and cannot change which
    relation is read, so the query stays determinable — the common real board.
    """
    read = relations_read_by(
        {"q": _sql("SELECT id FROM analytics.orders WHERE {{ filter('plan', p) }}")}
    )

    assert read.relations == frozenset({Relation(schema="analytics", name="orders")})
    assert read.undetermined == ()


def test_a_placeholder_inside_a_relation_returning_function_is_undetermined() -> None:
    """`IDENTIFIER('{{ name }}')` carries the placeholder as a literal, not a name.

    Snowflake's IDENTIFIER resolves a string to an object, so the template picks
    the relation — but a check keyed on identifier nodes never sees it.
    """
    read = relations_read_by(
        {"q": _sql("SELECT * FROM IDENTIFIER('{{ target.table }}')")}
    )

    assert read.undetermined == ("q",)


def test_a_quoted_variable_value_keeps_the_query_determinable() -> None:
    """The dominant spelling of a board variable: quoted, because it is a string.

    `WHERE order_date >= '{{ start_date }}'` cannot change which relation is
    read. Refusing it turned whole example boards fully undetermined, which is
    the exact failure this extractor exists to avoid — nothing to check on the
    boards it was built for.
    """
    read = relations_read_by(
        {
            "q": _sql(
                "SELECT id FROM analytics.orders "
                "WHERE order_date >= '{{ start_date }}' AND region = '{{ region }}'"
            )
        }
    )

    assert read.relations == frozenset({Relation(schema="analytics", name="orders")})
    assert read.undetermined == ()


def test_a_quoted_value_in_a_join_condition_is_still_determinable() -> None:
    """A JOIN's ON clause is a predicate, not a relation position."""
    read = relations_read_by(
        {
            "q": _sql(
                "SELECT o.id FROM analytics.orders o "
                "JOIN analytics.users u ON u.id = o.user_id AND u.plan = '{{ plan }}'"
            )
        }
    )

    assert read.relations == frozenset(
        {
            Relation(schema="analytics", name="orders"),
            Relation(schema="analytics", name="users"),
        }
    )
    assert read.undetermined == ()
