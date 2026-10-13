"""Which output columns of a rendered query are proven bounded by a date-range filter."""

from __future__ import annotations

from datetime import date

import pytest

from dbt_charts.core.compile.template.parameterized import DateRangeFilter
from dbt_charts.core.execute.date_range_axis import proven_range

_MARCH = (date(2026, 3, 1), date(2026, 3, 31))
_BETWEEN = "CAST({col} AS DATE) BETWEEN '2026-03-01' AND '2026-03-31'"


def _filter(column: str = "created_at") -> DateRangeFilter:
    return DateRangeFilter(column, *_MARCH)


def _where(col: str = "created_at") -> str:
    return _BETWEEN.format(col=col)


@pytest.mark.parametrize(
    ("sql", "dialect", "column"),
    [
        pytest.param(
            f"SELECT created_at AS d, n FROM orders WHERE {_where()}",
            "duckdb",
            "d",
            id="alias",
        ),
        pytest.param(
            "SELECT date_trunc('week', created_at) AS week, SUM(n) AS t "
            f"FROM orders WHERE {_where()} GROUP BY 1",
            "duckdb",
            "week",
            id="date-trunc",
        ),
        pytest.param(
            "SELECT CAST(date_trunc('month', created_at) AS DATE) AS m, SUM(n) AS t "
            f"FROM orders WHERE {_where()} GROUP BY 1",
            "postgres",
            "m",
            id="cast-of-trunc",
        ),
        pytest.param(
            "SELECT DATE_TRUNC(created_at, WEEK) AS week, SUM(n) AS t "
            f"FROM orders WHERE {_where()} GROUP BY 1",
            "bigquery",
            "week",
            id="bigquery-trunc",
        ),
        pytest.param(
            "WITH j AS (SELECT date_trunc('week', o.created_at) AS week, o.n "
            "FROM orders o JOIN customers c ON o.cid = c.id "
            f"WHERE {_where('o.created_at')}) "
            "SELECT week, SUM(n) AS t FROM j GROUP BY week",
            "duckdb",
            "week",
            id="cte-join-trunc",
        ),
    ],
)
def test_bounded_through_date_preserving_transforms(
    sql: str, dialect: str, column: str
) -> None:
    filter_column = "o.created_at" if "JOIN" in sql else "created_at"
    assert proven_range(sql, dialect, column, [_filter(filter_column)]) == _MARCH


@pytest.mark.parametrize(
    "sql",
    [
        pytest.param(
            f"SELECT date_trunc('week', signup_date) AS w FROM orders WHERE {_where()}",
            id="x-does-not-come-from-the-filtered-column",
        ),
        pytest.param(
            f"SELECT EXTRACT(month FROM created_at) AS m FROM orders WHERE {_where()}",
            id="extract-month",
        ),
        pytest.param(
            f"SELECT dayofweek(created_at) AS m FROM orders WHERE {_where()}",
            id="day-of-week",
        ),
        pytest.param(
            f"SELECT created_at + INTERVAL 1 DAY AS m FROM orders WHERE {_where()}",
            id="arithmetic",
        ),
        pytest.param(
            f"SELECT MIN(created_at) AS m FROM orders WHERE {_where()}",
            id="aggregate",
        ),
        pytest.param(
            f"SELECT created_at AS m FROM orders WHERE {_where()} OR status = 'x'",
            id="under-or",
        ),
        pytest.param(
            f"SELECT created_at AS m FROM orders WHERE NOT ({_where()})",
            id="under-not",
        ),
        pytest.param(
            f"SELECT created_at AS m FROM orders WHERE {_where()} AND {_where()}",
            id="two-predicates-on-the-column",
        ),
        pytest.param(
            f"SELECT created_at AS m FROM orders WHERE {_where()} "
            "UNION ALL SELECT created_at FROM other",
            id="union",
        ),
        pytest.param(
            "SELECT created_at AS m FROM orders o JOIN customers c ON o.cid = c.id "
            f"WHERE {_where()}",
            id="unqualified-column-in-a-join",
        ),
        pytest.param(
            f"SELECT created_at AS m FROM (SELECT * FROM orders WHERE {_where()}) s",
            id="star-hides-the-lineage",
        ),
        pytest.param(
            "SELECT b.created_at AS m FROM orders a JOIN orders b ON a.id = b.id "
            f"WHERE {_where('a.created_at')}",
            id="self-join-filters-the-other-side",
        ),
        pytest.param("SELECT FROM WHERE (", id="unparseable"),
    ],
)
def test_bails_when_the_link_is_not_proven(sql: str) -> None:
    assert proven_range(sql, "duckdb", "m", [_filter()]) is None


def test_two_range_filters_on_the_same_column_bail() -> None:
    sql = f"SELECT created_at AS m FROM orders WHERE {_where()}"
    filters = [
        _filter(),
        DateRangeFilter("created_at", date(2026, 1, 1), date(2026, 1, 31)),
    ]
    assert proven_range(sql, "duckdb", "m", filters) is None


def test_no_recorded_filter_means_no_bound() -> None:
    sql = f"SELECT created_at AS m FROM orders WHERE {_where()}"
    assert proven_range(sql, "duckdb", "m", []) is None


_STRAY = "CAST(created_at AS DATE) BETWEEN '2025-01-01' AND '2025-12-31'"


@pytest.mark.parametrize(
    "sql",
    [
        pytest.param(
            f"WITH f AS (SELECT 1 FROM other WHERE {_where()}) "
            f"SELECT created_at AS m FROM orders WHERE {_STRAY}",
            id="filter-in-an-unrelated-cte",
        ),
        pytest.param(
            f"SELECT created_at AS m FROM orders WHERE {_STRAY} "
            f"AND id IN (SELECT id FROM other WHERE {_where()})",
            id="filter-in-an-in-subquery",
        ),
        pytest.param(
            "SELECT o.created_at AS m FROM orders o LEFT JOIN other t "
            f"ON t.id = o.id AND {_where('t.created_at')} "
            f"WHERE CAST(o.created_at AS DATE) BETWEEN '2025-01-01' AND '2025-12-31'",
            id="filter-in-a-join-on",
        ),
        pytest.param(
            f"SELECT created_at AS m FROM orders WHERE {_STRAY} -- {_where()}",
            id="filter-in-a-comment",
        ),
        pytest.param(
            f"SELECT created_at AS m FROM orders WHERE {_STRAY}",
            id="filter-rendered-nowhere",
        ),
    ],
)
def test_a_predicate_that_is_not_the_recorded_filter_proves_nothing(sql: str) -> None:
    assert proven_range(sql, "duckdb", "m", [_filter()]) is None


@pytest.mark.parametrize(
    ("sql", "dialect"),
    [
        pytest.param(
            f"SELECT date(created_at, 'localtime') AS m FROM orders WHERE {_where()}",
            "sqlite",
            id="sqlite-localtime",
        ),
        pytest.param(
            f"SELECT date(created_at, '+1 month') AS m FROM orders WHERE {_where()}",
            "sqlite",
            id="sqlite-modifier",
        ),
        pytest.param(
            "SELECT DATE(created_at, 'America/New_York') AS m "
            f"FROM orders WHERE {_where()}",
            "bigquery",
            id="bigquery-date-zone",
        ),
        pytest.param(
            "SELECT TIMESTAMP_TRUNC(created_at, DAY, 'America/New_York') AS m "
            f"FROM orders WHERE {_where()}",
            "bigquery",
            id="bigquery-trunc-zone",
        ),
        pytest.param(
            "SELECT date_trunc('day', created_at, 'Asia/Tokyo') AS m "
            f"FROM orders WHERE {_where()}",
            "postgres",
            id="postgres-trunc-zone",
        ),
    ],
)
def test_a_transform_that_can_shift_the_date_proves_nothing(
    sql: str, dialect: str
) -> None:
    assert proven_range(sql, dialect, "m", [_filter()]) is None


@pytest.mark.parametrize(
    ("expr", "dialect"),
    [
        ("date_trunc('week', created_at)", "duckdb"),
        ("date_trunc('week', created_at)", "postgres"),
        ("DATE_TRUNC('week', created_at)", "snowflake"),
        ("DATE_TRUNC('MONTH', created_at)", "snowflake"),
        ("DATE_TRUNC(created_at, WEEK)", "bigquery"),
        ("TIMESTAMP_TRUNC(created_at, DAY)", "bigquery"),
        ("DATE(created_at)", "bigquery"),
        ("date_trunc('week', created_at)", "redshift"),
        ("date_trunc('week', created_at)", "databricks"),
        ("DATE(created_at)", "sqlite"),
        ("CAST(created_at AS DATE)", "snowflake"),
    ],
)
def test_plain_date_transforms_prove_the_range_on_every_dialect(
    expr: str, dialect: str
) -> None:
    sql = f"SELECT {expr} AS m, SUM(n) AS t FROM orders WHERE {_where()} GROUP BY 1"
    assert proven_range(sql, dialect, "m", [_filter()]) == _MARCH


def test_a_truncated_column_in_the_predicate_proves_nothing() -> None:
    sql = (
        "SELECT created_at AS m FROM orders "
        "WHERE DATE_TRUNC('year', created_at) BETWEEN '2026-03-01' AND '2026-03-31'"
    )
    assert proven_range(sql, "duckdb", "m", [_filter()]) is None


@pytest.mark.parametrize(
    ("dialect", "predicate"),
    [
        pytest.param(
            "duckdb",
            "created_at BETWEEN DATE '2026-03-01' - INTERVAL '1' YEAR "
            "AND DATE '2026-03-31' - INTERVAL '1' YEAR",
            id="interval-arithmetic",
        ),
        pytest.param(
            "sqlite",
            "date(created_at) BETWEEN date('2026-03-01', '-1 year') "
            "AND date('2026-03-31', '-1 year')",
            id="sqlite-modifier",
        ),
    ],
)
def test_a_bound_computed_from_the_recorded_dates_proves_nothing(
    dialect: str, predicate: str
) -> None:
    # Year over year: `cur` carries the recorded filter, `prior` shifts its bounds.
    sql = (
        f"WITH cur AS (SELECT created_at FROM orders WHERE {_where()}), "
        f"prior AS (SELECT created_at FROM orders WHERE {predicate}) "
        "SELECT p.created_at AS d FROM prior p"
    )
    assert proven_range(sql, dialect, "d", [_filter()]) is None
