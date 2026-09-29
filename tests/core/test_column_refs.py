"""Tests for core/column_refs.py — base-table column extraction from compiled queries."""

from __future__ import annotations

import pytest
import yaml

from dbt_charts.core.column_refs import extract_base_column_refs, parse_sql_statements
from dbt_charts.core.compile import compile as compile_board
from dbt_charts.core.compile.config import ProjectSourcesConfig

_SOURCES = ProjectSourcesConfig(sources={"s": {"type": "duckdb", "path": ":memory:"}})


def _compile(queries: dict[str, str], **board_extra):
    result = compile_board(
        yaml.dump(
            {
                "source": "s",
                "queries": queries,
                "charts": {name: {"query": name, "type": "table"} for name in queries},
                "rows": list(queries),
                **board_extra,
            }
        ),
        project_sources=_SOURCES,
    )
    assert result.board is not None, result.errors
    return result


def test_plain_select_records_table_and_column():
    refs = extract_base_column_refs(_compile({"q": "SELECT customer_id FROM orders"}))
    assert refs["q"].columns == {("orders", "customer_id")}
    assert refs["q"].indeterminate is None


def test_resolves_through_ctes_and_aliases():
    sql = """
    WITH recent AS (
      SELECT o.customer_id AS cid, o.amount FROM orders o
    )
    SELECT r.cid, u.name FROM recent r JOIN users u ON u.id = r.cid
    """
    refs = extract_base_column_refs(_compile({"q": sql}))
    assert ("orders", "customer_id") in refs["q"].columns
    assert ("orders", "amount") in refs["q"].columns
    assert ("users", "id") in refs["q"].columns
    assert ("users", "name") in refs["q"].columns
    # The CTE itself is not a base table.
    assert not any(t == "recent" for t, _ in refs["q"].columns)


def test_dbt_ref_call_resolves_to_the_model_name():
    refs = extract_base_column_refs(
        _compile({"q": "SELECT customer_id FROM {{ ref('orders') }}"})
    )
    assert refs["q"].columns == {("orders", "customer_id")}


def test_dbt_source_call_resolves_to_the_dotted_source_name():
    """`raw.orders` is the canonical resolved-source spelling — the one the
    author can map back to their dbt source, and the one --table narrows on."""
    refs = extract_base_column_refs(
        _compile({"q": "SELECT customer_id FROM {{ source('raw', 'orders') }}"})
    )
    assert refs["q"].columns == {("raw.orders", "customer_id")}


def test_unsupported_ref_spelling_is_indeterminate_not_a_placeholder_table():
    """Package-qualified ref() is deliberately outside the substitution regex;
    the skeleton would otherwise turn it into a placeholder *table* that
    --table narrowing silently filters out."""
    refs = extract_base_column_refs(
        _compile({"q": "SELECT customer_id FROM {{ ref('pkg', 'orders') }}"})
    )
    assert refs["q"].columns == set()
    assert refs["q"].indeterminate is not None
    assert "spelling" in refs["q"].indeterminate


def test_statement_position_jinja_branches_union_their_columns():
    """A column read only in an {% else %} body is still a dependency."""
    sql = (
        "{% if true %} SELECT customer_id FROM orders "
        "{% else %} SELECT legacy_customer_id FROM orders {% endif %}"
    )
    refs = extract_base_column_refs(_compile({"q": sql}))
    assert ("orders", "customer_id") in refs["q"].columns
    assert ("orders", "legacy_customer_id") in refs["q"].columns
    assert refs["q"].indeterminate is None


def test_expression_position_jinja_branch_is_indeterminate_never_dropped():
    """The branch-join renders expression-position {% if %} unparseable — the
    honest answer is indeterminate, never a column set missing the {% else %}
    branch's column."""
    sql = (
        "SELECT {% if true %} customer_id {% else %} legacy_customer_id "
        "{% endif %} FROM orders"
    )
    refs = extract_base_column_refs(_compile({"q": sql}))
    assert refs["q"].indeterminate is not None


def test_select_star_from_a_base_table_is_indeterminate():
    """A star hides which columns are consumed — omitting the board would read
    as 'safe to rename', the one wrong answer that matters."""
    refs = extract_base_column_refs(_compile({"q": "SELECT * FROM orders"}))
    assert refs["q"].indeterminate is not None
    assert "*" in refs["q"].indeterminate


def test_star_inside_a_cte_is_indeterminate_not_expanded_from_aliases():
    """qualify can expand a CTE's star from its declared alias list with no
    schema, fabricating determinate base columns out of alias names — stars
    are ruled on before qualify ever runs."""
    sql = "WITH c(a, b) AS (SELECT * FROM orders) SELECT a FROM c"
    refs = extract_base_column_refs(_compile({"q": sql}))
    assert refs["q"].indeterminate is not None
    assert "*" in refs["q"].indeterminate


def test_count_star_is_not_indeterminate():
    refs = extract_base_column_refs(
        _compile({"q": "SELECT COUNT(*) AS n FROM orders WHERE customer_id > 0"})
    )
    assert refs["q"].columns == {("orders", "customer_id")}
    assert refs["q"].indeterminate is None


def test_unparseable_sql_is_indeterminate_not_omitted():
    refs = extract_base_column_refs(
        _compile({"q": "SELECT customer_id FROM WHERE FROM orders"})
    )
    assert "q" in refs
    assert refs["q"].indeterminate is not None


def test_templated_expression_in_column_position_is_indeterminate():
    """`WHERE {{ filter('country', country) }}` is the canonical variable
    filter — the filtered column lives inside the jinja call, so the query's
    column set cannot be known statically and must never read as complete."""
    result = compile_board(
        yaml.dump(
            {
                "source": "s",
                "variables": {"day_col": {"input": "text", "default": "day"}},
                "queries": {
                    "q": "SELECT customer_id FROM orders WHERE {{ day_col }} = 1"
                },
                "charts": {"c": {"query": "q", "type": "table"}},
                "rows": ["c"],
            }
        ),
        project_sources=_SOURCES,
    )
    assert result.board is not None, result.errors
    refs = extract_base_column_refs(result)
    assert refs["q"].columns == set()
    assert refs["q"].indeterminate is not None
    assert "templated" in refs["q"].indeterminate


def test_templated_value_inside_a_literal_does_not_disturb_extraction():
    result = compile_board(
        yaml.dump(
            {
                "source": "s",
                "variables": {"day": {"input": "text", "default": "2026-01-01"}},
                "queries": {
                    "q": "SELECT customer_id FROM orders WHERE day = '{{ day }}'"
                },
                "charts": {"c": {"query": "q", "type": "table"}},
                "rows": ["c"],
            }
        ),
        project_sources=_SOURCES,
    )
    assert result.board is not None, result.errors
    refs = extract_base_column_refs(result)
    assert refs["q"].columns == {("orders", "customer_id"), ("orders", "day")}
    assert refs["q"].indeterminate is None


def test_unresolvable_dialect_falls_back_to_a_generic_parse():
    """A dbt_profile source's dialect is unknowable until execute — a generic
    parse that succeeds yields the right columns, and one that fails lands on
    the parse-error indeterminate. Zero hits for dbt-profile projects would
    gut the index for its primary audience."""
    result = compile_board(
        yaml.dump(
            {
                "source": "s",
                "queries": {"q": "SELECT customer_id FROM orders"},
                "charts": {"c": {"query": "q", "type": "table"}},
                "rows": ["c"],
            }
        )
    )
    assert result.board is not None, result.errors
    refs = extract_base_column_refs(result)
    assert refs["q"].columns == {("orders", "customer_id")}
    assert refs["q"].indeterminate is None


def test_inline_chart_sql_is_indexed_not_skipped():
    """The registry *name* is synthetic; the SQL is authored. A reverse index
    has no execute-time second chance — omission is the answer it gives."""
    result = compile_board(
        yaml.dump(
            {
                "source": "s",
                "charts": {
                    "c": {
                        "query": {"sql": "SELECT customer_id FROM orders"},
                        "type": "table",
                    }
                },
                "rows": ["c"],
            }
        ),
        project_sources=_SOURCES,
    )
    assert result.board is not None, result.errors
    refs = extract_base_column_refs(result)
    all_columns = set().union(*(r.columns for r in refs.values()))
    assert ("orders", "customer_id") in all_columns


def test_correlated_subquery_resolves_through_the_outer_scope():
    sql = "SELECT a FROM x WHERE EXISTS (SELECT 1 FROM y WHERE y.k = x.k)"
    refs = extract_base_column_refs(_compile({"q": sql}))
    assert refs["q"].indeterminate is None
    assert ("x", "k") in refs["q"].columns
    assert ("y", "k") in refs["q"].columns


def test_three_part_table_names_keep_their_catalog():
    refs = extract_base_column_refs(
        _compile({"q": "SELECT o.id FROM my_project.analytics.orders o"})
    )
    assert refs["q"].columns == {("my_project.analytics.orders", "id")}


def test_templated_trailing_table_part_is_indeterminate():
    """`FROM analytics.{{ tbl }}` — the placeholder is the *name* part, so the
    joined key does not start with the prefix; each part is tested alone."""
    result = compile_board(
        yaml.dump(
            {
                "source": "s",
                "variables": {"tbl": {"input": "text", "default": "orders"}},
                "queries": {"q": "SELECT id FROM analytics.{{ tbl }}"},
                "charts": {"c": {"query": "q", "type": "table"}},
                "rows": ["c"],
            }
        ),
        project_sources=_SOURCES,
    )
    assert result.board is not None, result.errors
    refs = extract_base_column_refs(result)
    assert refs["q"].columns == set()
    assert refs["q"].indeterminate is not None


def test_suffix_templated_identifiers_are_indeterminate():
    """build_skeleton splices placeholders inline, so `events_{{ env }}`
    becomes `events___dct_j0__` — a prefix check reads it as a determinate
    table that cannot exist."""
    for sql in (
        "SELECT customer_id FROM events_{{ env }}",
        "SELECT amount_{{ currency }} FROM orders",
    ):
        result = compile_board(
            yaml.dump(
                {
                    "source": "s",
                    "variables": {
                        "env": {"input": "text", "default": "prod"},
                        "currency": {"input": "text", "default": "usd"},
                    },
                    "queries": {"q": sql},
                    "charts": {"c": {"query": "q", "type": "table"}},
                    "rows": ["c"],
                }
            ),
            project_sources=_SOURCES,
        )
        assert result.board is not None, result.errors
        refs = extract_base_column_refs(result)
        assert refs["q"].columns == set(), sql
        assert refs["q"].indeterminate is not None, sql
        assert "templated" in refs["q"].indeterminate, sql


def test_snowflake_uppercasing_does_not_defeat_placeholder_detection():
    """qualify normalizes identifiers per dialect and Snowflake uppercases
    them — __dct_j0__ arrives as __DCT_J0__, and a case-sensitive guard reads
    a templated identifier as a determinate column or table."""
    snowflake = ProjectSourcesConfig(
        sources={
            "s": {
                "type": "snowflake",
                "account": "a",
                "user": "u",
                "password": "p",
                "database": "d",
            }
        }
    )
    for sql in (
        "SELECT customer_id FROM orders WHERE {{ day_col }} = 1",
        "SELECT customer_id FROM events_{{ env }}",
    ):
        result = compile_board(
            yaml.dump(
                {
                    "source": "s",
                    "variables": {
                        "day_col": {"input": "text", "default": "day"},
                        "env": {"input": "text", "default": "prod"},
                    },
                    "queries": {"q": sql},
                    "charts": {"c": {"query": "q", "type": "table"}},
                    "rows": ["c"],
                }
            ),
            project_sources=snowflake,
        )
        assert result.board is not None, result.errors
        refs = extract_base_column_refs(result)
        assert refs["q"].columns == set(), sql
        assert refs["q"].indeterminate is not None, sql
        assert "templated" in refs["q"].indeterminate, sql


def test_duckdb_columns_is_indeterminate_in_every_spelling():
    """COLUMNS selects by pattern, not by name — the regex spelling carries no
    Star node, so a star-keyed check reads it as consuming nothing."""
    for sql in (
        "SELECT COLUMNS(*) FROM orders",
        "SELECT COLUMNS('^sales_') FROM orders",
    ):
        refs = extract_base_column_refs(_compile({"q": sql}))
        assert refs["q"].indeterminate is not None, sql
        assert "COLUMNS" in refs["q"].indeterminate, sql


def test_unqualified_having_column_is_indeterminate_never_dropped():
    """sqlglot's scope filter drops an unqualified column whose nearest clause
    is HAVING or QUALIFY — without the fail-closed cross-check it vanishes
    from the index with indeterminate=None."""
    sql = (
        "SELECT o.customer_id FROM orders o JOIN users u ON u.id = o.customer_id "
        "GROUP BY o.customer_id HAVING SUM(refund_amount) > 100"
    )
    refs = extract_base_column_refs(_compile({"q": sql}))
    assert refs["q"].columns == set()
    assert refs["q"].indeterminate is not None
    assert "refund_amount" in refs["q"].indeterminate


def test_fully_qualified_join_resolves_every_pair():
    sql = (
        "SELECT o.customer_id, u.name FROM orders o "
        "JOIN users u ON u.id = o.customer_id"
    )
    refs = extract_base_column_refs(_compile({"q": sql}))
    assert refs["q"].columns == {
        ("orders", "customer_id"),
        ("users", "id"),
        ("users", "name"),
    }
    assert refs["q"].indeterminate is None


def test_unqualified_column_in_a_join_is_ambiguous_not_guessed():
    sql = "SELECT customer_id FROM orders JOIN users ON users.id = orders.customer_id"
    refs = extract_base_column_refs(_compile({"q": sql}))
    assert refs["q"].columns == set()
    assert refs["q"].indeterminate is not None
    assert "ambiguous" in refs["q"].indeterminate


def test_setup_sql_temp_view_is_not_a_base_table():
    """The body's FROM reads the temp view; the base tables are the ones the
    view definition reads."""
    result = compile_board(
        yaml.dump(
            {
                "source": "s",
                "queries": {
                    "q": {
                        "setup_sql": (
                            "CREATE TEMP VIEW recent AS SELECT customer_id FROM orders"
                        ),
                        "sql": "SELECT customer_id FROM recent",
                    }
                },
                "charts": {"c": {"query": "q", "type": "table"}},
                "rows": ["c"],
            }
        ),
        project_sources=_SOURCES,
    )
    assert result.board is not None, result.errors
    refs = extract_base_column_refs(result)
    assert refs["q"].columns == {("orders", "customer_id")}
    assert refs["q"].indeterminate is None


def test_setup_sql_function_body_is_not_an_indeterminacy():
    result = compile_board(
        yaml.dump(
            {
                "source": "s",
                "queries": {
                    "q": {
                        "setup_sql": "CREATE MACRO double_it(x) AS x * 2",
                        "sql": "SELECT double_it(amount) AS d, customer_id FROM orders",
                    }
                },
                "charts": {"c": {"query": "q", "type": "table"}},
                "rows": ["c"],
            }
        ),
        project_sources=_SOURCES,
    )
    assert result.board is not None, result.errors
    refs = extract_base_column_refs(result)
    assert refs["q"].indeterminate is None
    assert ("orders", "customer_id") in refs["q"].columns


def test_order_by_a_projected_column_stays_determinate():
    """sqlglot's scope filter excludes an ORDER BY restatement of a projection
    from Scope.columns — the fail-closed cross-check must forgive that shape,
    not throw away an answer the walk already resolved."""
    for sql in (
        "SELECT month, revenue FROM orders ORDER BY month",
        "SELECT month AS m, revenue FROM orders ORDER BY m DESC",
        "SELECT month, revenue FROM orders ORDER BY 1",
        "SELECT DISTINCT ON (month) month, revenue FROM orders ORDER BY month",
    ):
        refs = extract_base_column_refs(_compile({"q": sql}))["q"]
        assert refs.indeterminate is None, (sql, refs.indeterminate)
        assert refs.columns == {("orders", "month"), ("orders", "revenue")}, sql


def test_set_operation_order_by_stays_determinate():
    """A set operation's own ORDER BY sits in a scope with no table sources —
    the arms are traversed as scopes of their own — so its columns resolve to
    no table. They restate the arms' output columns, which the arm scopes
    already attributed."""
    arms = (
        "SELECT month, revenue FROM {{ ref('orders') }} WHERE kind = 'a' "
        "UNION ALL "
        "SELECT month, revenue FROM {{ ref('orders') }} WHERE kind = 'b'"
    )
    for sql in (
        arms,
        f"{arms} ORDER BY 1, 2",
        f"{arms} ORDER BY month",
        f"{arms} ORDER BY revenue DESC",
    ):
        refs = extract_base_column_refs(_compile({"q": sql}))["q"]
        assert refs.indeterminate is None, (sql, refs.indeterminate)
        assert refs.columns == {
            ("orders", "month"),
            ("orders", "revenue"),
            ("orders", "kind"),
        }, sql


def test_set_operation_order_by_an_unprojected_column_is_indeterminate():
    """The exemption is bounded by the operation's output columns: a name the
    arms don't project still has no table, and still fails closed."""
    sql = (
        "SELECT month, revenue FROM orders "
        "UNION ALL "
        "SELECT month, revenue FROM archive "
        "ORDER BY zzz"
    )
    refs = extract_base_column_refs(_compile({"q": sql}))["q"]
    assert refs.columns == set()
    assert refs.indeterminate is not None
    assert "zzz" in refs.indeterminate


def test_having_nested_under_a_set_operation_order_by_is_indeterminate():
    """The exemption reads the clause's own owner, never any query above it.
    An unqualified HAVING column is dropped from every scope; when it happens
    to share a name with one of an enclosing set operation's output columns,
    forgiving it would report `t3.a` as unread — the silent omission the
    honesty contract exists to prevent."""
    sql = (
        "SELECT a FROM t1 UNION ALL SELECT a FROM t2 "
        "ORDER BY (SELECT 1 FROM t3 HAVING SUM(a) > 1)"
    )
    refs = extract_base_column_refs(_compile({"q": sql}))["q"]
    assert refs.columns == set()
    assert refs.indeterminate is not None
    assert "'a'" in refs.indeterminate


def test_setup_sql_dotted_temp_view_is_not_a_base_table():
    """A schema-qualified temp view read back by its dotted name must be
    subtracted — only the bare spelling in `defined` leaves the view standing
    as a phantom base table."""
    result = compile_board(
        yaml.dump(
            {
                "source": "s",
                "queries": {
                    "q": {
                        "setup_sql": (
                            "CREATE TEMP VIEW temp.recent AS "
                            "SELECT customer_id FROM orders"
                        ),
                        "sql": "SELECT customer_id FROM temp.recent",
                    }
                },
                "charts": {"c": {"query": "q", "type": "table"}},
                "rows": ["c"],
            }
        ),
        project_sources=_SOURCES,
    )
    assert result.board is not None, result.errors
    refs = extract_base_column_refs(result)
    assert refs["q"].columns == {("orders", "customer_id")}
    assert refs["q"].indeterminate is None


def test_extraction_requires_a_compiled_board():
    result = compile_board("title: X\nrows:\n  - nope\n")
    assert result.board is None
    with pytest.raises(ValueError, match="compiled board"):
        extract_base_column_refs(result)


@pytest.mark.parametrize(
    "inner",
    [
        "WITH final AS (SELECT sales_channel FROM raw)",
        "WITH RECURSIVE final AS (SELECT 1 AS n)",
        "WITH final (x) AS (SELECT sales_channel FROM raw)",
        "WITH final AS MATERIALIZED (SELECT sales_channel FROM raw)",
    ],
)
def test_parenthesized_body_with_its_own_with_is_indeterminate(inner):
    """sqlglot overwrites the parenthesized body's WITH with the outer one, so
    the tree resolves `final` to the wrong CTE; the seam must refuse it."""
    sql = f"WITH final AS (SELECT customer_id FROM raw) ({inner} SELECT * FROM final)"
    reason = parse_sql_statements(sql, None)
    assert isinstance(reason, str)
    assert "dropped a WITH" in reason


@pytest.mark.parametrize(
    ("sql", "dialect"),
    [
        ("SELECT CAST(x AS TIMESTAMP WITH TIME ZONE) AS x FROM raw", None),
        ("SELECT v FROM UNNEST(ARRAY[1, 2]) WITH ORDINALITY AS t(v, i)", None),
        ("SELECT v FROM UNNEST(ARRAY[1, 2]) WITH ORDINALITY t(v, i)", None),
        ("SELECT v FROM UNNEST(ARRAY[1, 2]) WITH ORDINALITY AS t(v, i)", "trino"),
        (
            "SELECT a.x FROM raw AS a CROSS JOIN UNNEST(a.arr) WITH OFFSET AS pos",
            "bigquery",
        ),
        ("SELECT x FROM raw WITH (NOLOCK)", "tsql"),
        ("WITH 1 AS one SELECT one FROM raw", "clickhouse"),
        (
            "WITH RECURSIVE r AS (SELECT 1 AS n UNION ALL SELECT n + 1 FROM r) "
            "SELECT n FROM r",
            None,
        ),
        ("WITH a (x, y) AS (SELECT 1, 2) SELECT x, y FROM a", None),
        (
            "WITH a AS (WITH b AS (SELECT x FROM raw) SELECT x FROM b) "
            "SELECT x FROM (WITH c AS (SELECT x FROM a) SELECT x FROM c) AS t",
            None,
        ),
        (
            "WITH a AS (WITH b AS (SELECT x FROM raw) SELECT x FROM b) "
            "SELECT x FROM (WITH c AS (SELECT x FROM a) SELECT x FROM c) AS t",
            "databricks",
        ),
        (
            "WITH a AS (WITH b AS (SELECT x FROM raw) SELECT x FROM b) "
            "SELECT x FROM (WITH c AS (SELECT x FROM a) SELECT x FROM c) AS t",
            "spark",
        ),
        (
            "WITH a AS (WITH b AS (SELECT x FROM raw) SELECT x FROM b) "
            "SELECT x FROM (WITH c AS (SELECT x FROM a) SELECT x FROM c) AS t",
            "tsql",
        ),
        ("SELECT v FROM UNNEST(ARRAY[1, 2]) WITH ORDINALITY AS t(v, i)", "snowflake"),
        (
            "WITH a AS (SELECT 1 AS x) SELECT x FROM a; WITH b AS (SELECT 2 AS y) "
            "SELECT y FROM b",
            None,
        ),
    ],
)
def test_with_keywords_that_lose_nothing_still_parse(sql, dialect):
    assert not isinstance(parse_sql_statements(sql, dialect), str)
