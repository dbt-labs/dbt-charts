"""Lineage rules: which output columns inherit a dbt column's format."""

from __future__ import annotations

import pytest

from dbt_charts.core.column_lineage import lineage_column_formats
from dbt_charts.core.diagnostics.execution import ExecutionError

BASE = {
    "orders": {
        "amount": "currency",
        "tax": "currency",
        "order_id": "id",
        "margin": "percent",
        "price": "currency",
        "rate": "percent_number",
    },
    "raw.events": {"amount": "currency"},
}
KNOWN = {"currency", "percent", "percent_number", "integer", "id", "year"}


def _formats(
    sql: str, setup_sql: str | None = None, dialect: str | None = None
) -> dict[str, str]:
    found = lineage_column_formats(
        sql, dialect=dialect, setup_sql=setup_sql, base_formats=BASE, known=KNOWN
    )
    return found


def _select(expr: str, frm: str = "{{ ref('orders') }}") -> dict[str, str]:
    return _formats(f"select {expr} as out from {frm}")


@pytest.mark.parametrize(
    ("expr", "expected"),
    [
        ("amount", "currency"),
        ("cast(amount as double)", "currency"),
        ("cast(amount as varchar)", None),
        ("sum(amount)", "currency"),
        ("avg(amount)", "currency"),
        ("min(amount)", "currency"),
        ("max(amount)", "currency"),
        ("median(amount)", "currency"),
        ("sum(amount) over (partition by order_id)", "currency"),
        ("sum(margin)", None),
        ("sum(rate)", None),
        ("avg(rate)", "percent_number"),
        ("max(amount, order_id)", None),
        ("percentile_cont(0.5) within group (order by amount)", "currency"),
        ("avg(margin)", "percent"),
        ("count(*)", "integer"),
        ("count(order_id)", "integer"),
        ("count(distinct order_id)", "integer"),
        ("sum(order_id)", None),
        ("max(order_id)", None),
        ("amount + tax", "currency"),
        ("amount - tax", "currency"),
        ("amount + order_id", None),
        ("amount / tax", "percent"),
        ("sum(amount) / count(distinct order_id)", "currency"),
        ("price * amount", None),
        ("amount * 100", None),
        ("amount / 100", None),
        ("amount + 1", None),
        ("round(amount, 2)", "currency"),
        ("abs(amount)", "currency"),
        ("floor(amount)", "currency"),
        ("ceil(amount)", "currency"),
        ("case when tax > 0 then amount else tax end", "currency"),
        ("case when tax > 0 then amount else order_id end", None),
        ("case when tax > 0 then amount end", "currency"),
        ("coalesce(amount, tax)", "currency"),
        ("coalesce(amount, order_id)", None),
        ("coalesce(amount, 0)", None),
        ("extract(year from order_date)", "year"),
        ("year(order_date)", "year"),
        ("extract(month from order_date)", None),
        ("case when tax > 0 then amount else null end", "currency"),
        ("upper(amount)", None),
        ("some_udf(amount)", None),
        ("unknown_column", None),
    ],
)
def test_rule_table(expr: str, expected: str | None) -> None:
    assert _select(expr).get("out") == expected


def test_authored_alias_passes_through_unchanged() -> None:
    sql = "select amount, sum(amount) as Revenue from {{ ref('orders') }} group by 1"
    assert _formats(sql) == {"amount": "currency", "Revenue": "currency"}


def test_unaliased_expression_has_no_name_to_carry_a_format() -> None:
    assert _formats("select sum(amount) from {{ ref('orders') }}") == {}


def test_source_call_reads_the_source_table_formats() -> None:
    sql = "select sum(amount) as total from {{ source('raw', 'events') }}"
    assert _formats(sql) == {"total": "currency"}


def test_bare_table_name_without_ref_is_not_a_dbt_node() -> None:
    assert _formats("select sum(amount) as total from orders") == {}


def test_cte_and_subquery_carry_the_type() -> None:
    sql = """
    with o as (select amount as a, order_id from {{ ref('orders') }})
    select sum(t.a) as revenue
    from (select a from o) t
    """
    assert _formats(sql) == {"revenue": "currency"}


def test_join_resolves_each_side_by_alias() -> None:
    sql = """
    select sum(o.amount) as revenue, count(distinct o.order_id) as n
    from {{ ref('orders') }} o join {{ source('raw', 'events') }} e
      on o.order_id = e.order_id
    """
    assert _formats(sql) == {"revenue": "currency", "n": "integer"}


def test_union_keeps_a_type_only_when_every_branch_agrees() -> None:
    same = """
    select amount as v from {{ ref('orders') }}
    union all
    select tax as v from {{ ref('orders') }}
    """
    mixed = """
    select amount as v from {{ ref('orders') }}
    union all
    select order_id as v from {{ ref('orders') }}
    """
    assert _formats(same) == {"v": "currency"}
    assert _formats(mixed) == {}


def test_union_inside_a_cte() -> None:
    sql = """
    with u as (
      select amount as v from {{ ref('orders') }}
      union all select tax from {{ ref('orders') }}
    )
    select sum(v) as total from u
    """
    assert _formats(sql) == {"total": "currency"}


def test_select_star_types_nothing() -> None:
    assert _formats("select * from {{ ref('orders') }}") == {}


def test_jinja_in_the_body_is_skeletonized() -> None:
    sql = """
    select sum(amount) as revenue from {{ ref('orders') }}
    where order_date >= {{ start }}
    {% if region %} and region = {{ region }} {% endif %}
    """
    assert _formats(sql) == {"revenue": "currency"}


def test_setup_sql_view_carries_the_type() -> None:
    setup = "create temp view big as select amount as a from {{ ref('orders') }}"
    assert _formats("select sum(a) as revenue from big", setup_sql=setup) == {
        "revenue": "currency"
    }


def test_unparseable_sql_types_nothing() -> None:
    assert _formats("select from from") == {}


def test_unknown_meta_format_errors_naming_model_and_column() -> None:
    with pytest.raises(ExecutionError, match=r"orders.*amount.*curency"):
        lineage_column_formats(
            "select sum(amount) as revenue from {{ ref('orders') }}",
            dialect=None,
            setup_sql=None,
            base_formats={"orders": {"amount": "curency"}},
            known=KNOWN,
        )


def test_unknown_meta_format_on_an_unread_column_is_ignored() -> None:
    assert lineage_column_formats(
        "select order_id as i from {{ ref('orders') }}",
        dialect=None,
        setup_sql=None,
        base_formats={"orders": {"amount": "curency", "order_id": "id"}},
        known=KNOWN,
    ) == {"i": "id"}


def test_iff_is_typed_like_case_under_snowflake() -> None:
    sql = "select iff(tax > 0, amount, tax) as v from {{ ref('orders') }}"
    assert _formats(sql, dialect="snowflake") == {"v": "currency"}


def test_a_setup_relation_shadows_a_model_of_the_same_name() -> None:
    setup = (
        "create temp view orders as select order_id as amount from {{ ref('orders') }}"
    )
    assert _formats("select amount as a from orders", setup_sql=setup) == {"a": "id"}


def test_an_excluded_output_is_not_derived_so_its_bad_format_never_raises() -> None:
    found = lineage_column_formats(
        "select sum(amount) as revenue from {{ ref('orders') }}",
        dialect=None,
        setup_sql=None,
        base_formats={"orders": {"amount": "curency"}},
        known=KNOWN,
        exclude={"revenue"},
    )
    assert found == {}


@pytest.mark.parametrize("spec", [None, 5, {"x": 1}])
def test_a_non_string_meta_format_errors_when_the_column_is_read(spec: object) -> None:
    sql = "select sum(amount) as revenue, count(*) as n from {{ ref('orders') }}"
    base = {"orders": {"amount": spec}}
    with pytest.raises(ExecutionError, match=r"orders.*amount"):
        lineage_column_formats(
            sql, dialect=None, setup_sql=None, base_formats=base, known=KNOWN
        )
    assert lineage_column_formats(
        "select count(*) as n from {{ ref('orders') }}",
        dialect=None,
        setup_sql=None,
        base_formats=base,
        known=KNOWN,
    ) == {"n": "integer"}


def test_an_unaliased_projection_does_not_shift_later_columns_formats() -> None:
    sql = "select month, sum(amount), count(*) as orders from {{ ref('orders') }}"
    assert _formats(sql) == {"orders": "integer"}


def test_an_unaliased_projection_in_a_setup_view_does_not_shift_either() -> None:
    setup = (
        "create temp view v as select order_id, sum(amount), count(*) as n "
        "from {{ ref('orders') }} group by 1"
    )
    assert _formats("select n as k from v", setup_sql=setup) == {"k": "integer"}


def test_a_setup_view_column_the_query_never_reads_never_raises() -> None:
    setup = (
        "create temp view v as select amount as a, tax as t from {{ ref('orders') }}"
    )
    assert lineage_column_formats(
        "select t as k from v",
        dialect=None,
        setup_sql=setup,
        base_formats={"orders": {"amount": "curency", "tax": "currency"}},
        known=KNOWN,
    ) == {"k": "currency"}


def test_an_excluded_output_read_through_a_setup_view_never_raises() -> None:
    setup = "create temp view v as select amount as a from {{ ref('orders') }}"
    assert (
        lineage_column_formats(
            "select a as k from v",
            dialect=None,
            setup_sql=setup,
            base_formats={"orders": {"amount": "curency"}},
            known=KNOWN,
            exclude={"k"},
        )
        == {}
    )


@pytest.mark.parametrize("dialect", [None, "clickhouse", "duckdb"])
@pytest.mark.parametrize(
    ("sql", "exclude"),
    [
        ("select margin as amount, amount as b from {{ ref('orders') }}", ()),
        ("select bad as amount, amount as b from {{ ref('orders') }}", ("amount",)),
    ],
)
def test_a_column_named_like_another_projections_alias_types_nothing(
    sql: str, exclude: tuple[str, ...], dialect: str | None
) -> None:
    found = lineage_column_formats(
        sql,
        dialect=dialect,
        setup_sql=None,
        base_formats={
            "orders": {"amount": "currency", "margin": "percent", "bad": "curency"}
        },
        known=KNOWN,
        exclude=exclude,
    )
    assert "b" not in found


def test_a_cte_named_like_the_model_is_not_the_model() -> None:
    sql = """
    with orders as (select margin as amount, order_id from {{ ref('orders') }})
    select o.amount as filtered, r.amount as all_amount
    from orders o join {{ ref('orders') }} r on o.order_id = r.order_id
    """
    assert _formats(sql) == {"filtered": "percent", "all_amount": "currency"}


def test_a_setup_view_named_like_the_model_is_not_the_model() -> None:
    setup = (
        "create view orders as select order_id as amount from {{ ref('customers') }}"
    )
    assert _formats("select amount as x from {{ ref('orders') }}", setup_sql=setup) == {
        "x": "currency"
    }


def test_a_bare_name_is_not_the_model_because_a_ref_appears_elsewhere() -> None:
    sql = """
    select b.amount as bare, r.amount as ref_amount
    from orders b join {{ ref('orders') }} r on b.order_id = r.order_id
    """
    assert _formats(sql) == {"ref_amount": "currency"}
