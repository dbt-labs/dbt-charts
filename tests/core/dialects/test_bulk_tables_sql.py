"""Per-dialect bulk_tables_sql shape checks.

Mirrors ``test_bulk_schema_sql.py``: the tables-only listing takes the same scope
plus the schemas to list, and returns ``table_schema``, ``table_name`` rows without
reading any column. The dialect escapes the schema names itself.
"""

from __future__ import annotations

import pytest

from dbt_charts.core.dialects import get_dialect
from dbt_charts.core.dialects.base import SQLDialect
from dbt_charts.core.inspect.bulk_schema import parse_bulk_table_rows

SCHEMAS = ["Analytics", "raw"]


class _BareDialect(SQLDialect):
    name = "bare"

    def param(self, index: int) -> str:
        return "?"


def _sql(name: str, schemas: list[str] = SCHEMAS) -> str:
    return get_dialect(name).bulk_tables_sql(
        "main" if name == "databricks" else "region-us" if name == "bigquery" else "",
        schemas,
    )


def test_base_dialect_has_no_bulk_tables_form() -> None:
    with pytest.raises(NotImplementedError):
        _BareDialect().bulk_tables_sql("", SCHEMAS)


@pytest.mark.parametrize("name", ["duckdb", "postgres", "redshift"])
def test_ansi_dialects_query_information_schema_tables(name: str) -> None:
    sql = _sql(name).lower()
    assert "from information_schema.tables" in sql
    assert "column" not in sql.replace("table_name", "")


def test_sqlite_reads_sqlite_master_without_columns() -> None:
    sql = _sql("sqlite").lower()
    assert "sqlite_master" in sql
    assert "pragma_table_info" not in sql


def test_bigquery_requires_region_qualifier() -> None:
    with pytest.raises(NotImplementedError):
        get_dialect("bigquery").bulk_tables_sql("", SCHEMAS)
    sql = _sql("bigquery")
    assert "`region-us`.INFORMATION_SCHEMA.TABLES" in sql
    assert "FROM INFORMATION_SCHEMA.TABLES" not in sql


def test_snowflake_qualifies_the_database_and_skips_its_information_schema() -> None:
    dialect = get_dialect("snowflake")
    assert "FROM INFORMATION_SCHEMA.TABLES" in dialect.bulk_tables_sql("", SCHEMAS)
    assert '"ANALYTICS".INFORMATION_SCHEMA.TABLES' in dialect.bulk_tables_sql(
        "ANALYTICS", SCHEMAS
    )
    assert '"a""b".' in dialect.bulk_tables_sql('a"b', SCHEMAS)
    assert "<> 'INFORMATION_SCHEMA'" in dialect.bulk_tables_sql("", SCHEMAS)


def test_databricks_qualifies_the_catalog() -> None:
    dialect = get_dialect("databricks")
    assert "FROM `main`.information_schema.tables" in dialect.bulk_tables_sql(
        "main", SCHEMAS
    )
    assert "FROM information_schema.tables" in dialect.bulk_tables_sql("", SCHEMAS)


@pytest.mark.parametrize("catalog", ["hive_metastore", "HIVE_METASTORE"])
def test_databricks_hive_metastore_has_no_bulk_tables_form(catalog: str) -> None:
    with pytest.raises(NotImplementedError):
        get_dialect("databricks").bulk_tables_sql(catalog, SCHEMAS)


def test_clickhouse_reads_system_tables() -> None:
    sql = _sql("clickhouse").lower()
    assert "from system.tables" in sql
    assert "system.columns" not in sql


def test_spark_has_no_bulk_tables_form() -> None:
    with pytest.raises(NotImplementedError):
        get_dialect("spark").bulk_tables_sql("main", SCHEMAS)


def test_table_rows_group_by_schema_and_ignore_case_of_column_names() -> None:
    rows = [
        {"TABLE_SCHEMA": "a", "TABLE_NAME": "t1"},
        {"table_schema": "a", "table_name": "t2"},
        {"table_schema": "b", "table_name": "t1"},
        {"table_schema": "", "table_name": "skipped"},
    ]
    assert parse_bulk_table_rows(rows) == {"a": ["t1", "t2"], "b": ["t1"]}


@pytest.mark.parametrize("name", ["duckdb", "redshift", "snowflake", "databricks"])
def test_folding_dialects_compare_schemas_case_insensitively(name: str) -> None:
    assert "LOWER(table_schema) IN ('analytics', 'raw')" in _sql(name)


@pytest.mark.parametrize("name", ["postgres", "clickhouse"])
def test_exact_dialects_compare_schemas_verbatim(name: str) -> None:
    sql = _sql(name)
    column = "database" if name == "clickhouse" else "table_schema"
    assert f"{column} IN ('Analytics', 'raw')" in sql
    assert "LOWER(" not in sql


def test_bigquery_filters_datasets_inside_the_region_scope() -> None:
    sql = _sql("bigquery")
    assert "`region-us`.INFORMATION_SCHEMA.TABLES WHERE table_schema IN" in sql
    assert sql.index("WHERE") < sql.index("ORDER BY")


def test_sqlite_keeps_main_only_when_listed() -> None:
    assert "AND 'main' IN ('main')" in _sql("sqlite", ["main"])
    assert "AND 'main' IN ('other')" in _sql("sqlite", ["other"])


@pytest.mark.parametrize(
    "name",
    [
        "duckdb",
        "postgres",
        "redshift",
        "snowflake",
        "databricks",
        "clickhouse",
        "sqlite",
    ],
)
def test_no_schemas_matches_nothing(name: str) -> None:
    assert "1 = 0" in _sql(name, [])


@pytest.mark.parametrize(
    ("name", "escaped"),
    [
        ("bigquery", r"'it\'s'"),
        ("databricks", r"'it\'s'"),
        ("postgres", "'it''s'"),
        ("snowflake", r"'it\'s'"),
    ],
)
def test_dialect_escapes_schema_names_itself(name: str, escaped: str) -> None:
    dialect = get_dialect(name)
    assert dialect.string_literal("it's") == escaped
    assert escaped.lower() in _sql(name, ["it's"]).lower()
