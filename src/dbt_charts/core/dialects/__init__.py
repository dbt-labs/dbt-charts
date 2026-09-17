"""SQL dialect registry for database-specific behavior.

This module provides a registry of SQL dialects for different database backends.
Use get_dialect() to obtain the appropriate dialect instance for a given
database profile type.

Example:
    >>> from dbt_charts.core.dialects import get_dialect
    >>> dialect = get_dialect('postgres')
    >>> dialect.param(1)
    '$1'
    >>> dialect = get_dialect('mysql')
    >>> dialect.param(1)
    '%s'
"""

from dbt_charts.core.dialects.athena import AthenaDialect
from dbt_charts.core.dialects.base import VALID_OPERATORS, SQLDialect
from dbt_charts.core.dialects.bigquery import BigQueryDialect
from dbt_charts.core.dialects.clickhouse import ClickHouseDialect
from dbt_charts.core.dialects.databricks import DatabricksDialect, SparkDialect
from dbt_charts.core.dialects.duckdb import DuckDBDialect
from dbt_charts.core.dialects.mysql import MySQLDialect
from dbt_charts.core.dialects.postgres import PostgresDialect
from dbt_charts.core.dialects.redshift import RedshiftDialect
from dbt_charts.core.dialects.snowflake import SnowflakeDialect
from dbt_charts.core.dialects.sqlite import SQLiteDialect
from dbt_charts.core.dialects.sqlserver import SQLServerDialect

# Singleton instances for each dialect
_postgres_dialect = PostgresDialect()
_duckdb_dialect = DuckDBDialect()
_mysql_dialect = MySQLDialect()
_snowflake_dialect = SnowflakeDialect()
_bigquery_dialect = BigQueryDialect()
_redshift_dialect = RedshiftDialect()
_sqlserver_dialect = SQLServerDialect()
_databricks_dialect = DatabricksDialect()
_spark_dialect = SparkDialect()
_athena_dialect = AthenaDialect()
_clickhouse_dialect = ClickHouseDialect()
_sqlite_dialect = SQLiteDialect()

# Registry mapping profile types to dialect instances
DIALECTS: dict[str, SQLDialect] = {
    # PostgreSQL (default)
    "postgres": _postgres_dialect,
    "postgresql": _postgres_dialect,
    # DuckDB
    "duckdb": _duckdb_dialect,
    # SQLite
    "sqlite": _sqlite_dialect,
    # MySQL / MariaDB
    "mysql": _mysql_dialect,
    "mariadb": _mysql_dialect,
    # Snowflake
    "snowflake": _snowflake_dialect,
    # BigQuery
    "bigquery": _bigquery_dialect,
    # Redshift
    "redshift": _redshift_dialect,
    # SQL Server
    "sqlserver": _sqlserver_dialect,
    "mssql": _sqlserver_dialect,
    # Databricks
    "databricks": _databricks_dialect,
    # Spark (dbt-spark's Hive/Thrift/ODBC cursors — no fetchmany, unlike dbt-databricks)
    "spark": _spark_dialect,
    # Athena
    "athena": _athena_dialect,
    "presto": _athena_dialect,
    "trino": _athena_dialect,
    # ClickHouse
    "clickhouse": _clickhouse_dialect,
}

# Default dialect when profile type is unknown
DEFAULT_DIALECT = _postgres_dialect


def get_dialect(profile_type: str) -> SQLDialect:
    """Get the appropriate SQL dialect for a database profile type.

    Returns a singleton dialect instance for the given profile type.
    Falls back to PostgreSQL dialect for unknown types.

    Args:
        profile_type: Database type string (e.g., 'postgres', 'mysql', 'snowflake')

    Returns:
        SQLDialect instance for the specified database type

    Example:
        >>> dialect = get_dialect('postgres')
        >>> dialect.param(1)
        '$1'
        >>> dialect = get_dialect('unknown')  # Falls back to PostgreSQL
        >>> dialect.name
        'postgres'
    """
    return DIALECTS.get(profile_type.lower(), DEFAULT_DIALECT)


def list_dialects() -> list[str]:
    """List all supported dialect profile types.

    Returns:
        List of profile type strings that are supported.
    """
    return sorted(set(DIALECTS.keys()))


__all__ = [
    # Base class and constants
    "SQLDialect",
    "VALID_OPERATORS",
    # Dialect implementations
    "PostgresDialect",
    "DuckDBDialect",
    "SQLiteDialect",
    "MySQLDialect",
    "SnowflakeDialect",
    "BigQueryDialect",
    "RedshiftDialect",
    "SQLServerDialect",
    "DatabricksDialect",
    "AthenaDialect",
    "ClickHouseDialect",
    # Registry
    "DIALECTS",
    "get_dialect",
    "list_dialects",
]
