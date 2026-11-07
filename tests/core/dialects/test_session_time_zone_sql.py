"""Per-dialect session time zone statement: warehouses that can take one get it
as SQL, the rest report None so the caller warns instead of guessing."""

from __future__ import annotations

import pytest

from dbt_charts.core.dialects import get_dialect


@pytest.mark.parametrize(
    ("dialect", "expected"),
    [
        ("postgres", "SET TIME ZONE 'America/New_York'"),
        ("redshift", "SET TIME ZONE 'America/New_York'"),
        ("duckdb", "SET TIME ZONE 'America/New_York'"),
        ("lakecompute", "SET TIME ZONE 'America/New_York'"),
        ("databricks", "SET TIME ZONE 'America/New_York'"),
        ("spark", "SET TIME ZONE 'America/New_York'"),
        ("snowflake", "ALTER SESSION SET TIMEZONE = 'America/New_York'"),
    ],
)
def test_dialect_sets_the_session_zone(dialect: str, expected: str) -> None:
    assert get_dialect(dialect).session_time_zone_sql("America/New_York") == expected


@pytest.mark.parametrize(
    "dialect",
    [
        "sqlite",
        "sqlserver",
        "fabric",
        "athena",
        "trino",
        "bigquery",
        "clickhouse",
        "mysql",
    ],
)
def test_dialect_without_a_session_zone_statement_reports_none(dialect: str) -> None:
    assert get_dialect(dialect).session_time_zone_sql("America/New_York") is None
