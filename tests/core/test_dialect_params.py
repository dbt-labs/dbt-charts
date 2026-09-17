"""Tests for dialect parameter conversion (uses_named_params + param placeholders)."""

import pytest

from dbt_charts.core.dialects import get_dialect


class TestUsesNamedParams:
    """Dialects correctly report whether they use named parameters."""

    @pytest.mark.parametrize(
        "dialect_name",
        ["duckdb", "postgres", "mysql", "snowflake", "redshift", "athena"],
    )
    def test_positional_dialects(self, dialect_name: str) -> None:
        dialect = get_dialect(dialect_name)
        assert dialect.uses_named_params is False

    @pytest.mark.parametrize(
        "dialect_name",
        ["bigquery", "sqlserver", "databricks", "clickhouse"],
    )
    def test_named_dialects(self, dialect_name: str) -> None:
        dialect = get_dialect(dialect_name)
        assert dialect.uses_named_params is True
