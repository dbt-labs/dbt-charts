"""Per-dialect bulk_schema_sql shape checks.

The DuckDB path is executed end-to-end in
``dbt-charts/tests/core/inspect/test_bulk_schema.py``; these pin the SQL shape for
dialects we can't spin up in CI (BigQuery region qualifier, Snowflake), and the
base-class no-bulk-form contract.
"""

from __future__ import annotations

import pytest

from dbt_charts.core.dialects import get_dialect
from dbt_charts.core.dialects.base import SQLDialect
from dbt_charts.core.inspect.bulk_schema import bulk_schema_scope


class _BareDialect(SQLDialect):
    name = "bare"

    def param(self, index: int) -> str:
        return "?"


def test_base_dialect_has_no_bulk_form() -> None:
    with pytest.raises(NotImplementedError):
        _BareDialect().bulk_schema_sql()


def test_ansi_dialects_query_information_schema_columns() -> None:
    for name in ("duckdb", "postgres"):
        sql = get_dialect(name).bulk_schema_sql()
        assert "information_schema.columns" in sql.lower()
        assert "ordinal_position" in sql.lower()


def test_sqlite_reads_pragma_table_info() -> None:
    sql = get_dialect("sqlite").bulk_schema_sql()
    assert "pragma_table_info" in sql.lower()
    assert "sqlite_master" in sql.lower()


def test_bigquery_requires_region_qualifier() -> None:
    dialect = get_dialect("bigquery")
    # Without a region we cannot address the whole warehouse in one query.
    with pytest.raises(NotImplementedError):
        dialect.bulk_schema_sql()
    sql = dialect.bulk_schema_sql("region-us")
    assert "`region-us`.INFORMATION_SCHEMA.COLUMNS" in sql
    # Must NOT emit a bare, per-dataset INFORMATION_SCHEMA reference.
    assert "FROM INFORMATION_SCHEMA.COLUMNS" not in sql


def test_snowflake_qualifies_database_when_given() -> None:
    dialect = get_dialect("snowflake")
    assert "INFORMATION_SCHEMA.COLUMNS" in dialect.bulk_schema_sql()
    # Database is double-quoted so case-sensitive names resolve correctly.
    assert '"ANALYTICS".INFORMATION_SCHEMA.COLUMNS' in dialect.bulk_schema_sql(
        "ANALYTICS"
    )
    # Embedded double-quotes are doubled (Snowflake identifier escaping).
    assert '"a""b".' in dialect.bulk_schema_sql('a"b')


def test_databricks_qualifies_catalog_with_full_types() -> None:
    sql = get_dialect("databricks").bulk_schema_sql("main")
    assert "FROM `main`.information_schema.columns" in sql
    assert "full_data_type AS data_type" in sql
    assert "ordinal_position" in sql


def test_databricks_without_catalog_is_unqualified() -> None:
    sql = get_dialect("databricks").bulk_schema_sql()
    assert "FROM information_schema.columns" in sql


@pytest.mark.parametrize("catalog", ["hive_metastore", "HIVE_METASTORE"])
def test_databricks_hive_metastore_has_no_bulk_form(catalog: str) -> None:
    with pytest.raises(NotImplementedError):
        get_dialect("databricks").bulk_schema_sql(catalog)


def test_spark_has_no_bulk_form() -> None:
    with pytest.raises(NotImplementedError):
        get_dialect("spark").bulk_schema_sql("main")


def test_databricks_scope_is_the_catalog() -> None:
    assert bulk_schema_scope("databricks", {"catalog": "main"}) == "main"


def test_clickhouse_reads_system_columns() -> None:
    """One query over system.columns, aliased to the contract's column names
    and with the server's own catalogs excluded."""
    sql = get_dialect("clickhouse").bulk_schema_sql()
    assert "FROM system.columns" in sql
    for alias in ("table_schema", "table_name", "column_name", "data_type"):
        assert f"AS {alias}" in sql
    assert "'system'" in sql
    assert sql.endswith("ORDER BY database, table, position")
