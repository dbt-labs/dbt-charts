"""Snowflake dialect implementation.

Snowflake uses:
- ? for positional parameters or :name for named parameters
"""

from collections.abc import Sequence

from dbt_charts.core.dialects.base import SQLDialect


class SnowflakeDialect(SQLDialect):
    """Snowflake dialect with ? parameter style.

    Snowflake uses positional ? placeholders.

    Example:
        >>> dialect = SnowflakeDialect()
        >>> dialect.param(1)
        '?'
    """

    name = "snowflake"
    folds_identifier_case = True
    # Single-quoted constants use backslash escape sequences: a constant holding
    # a backslash 'must escape the backslash with a second backslash'.
    # https://docs.snowflake.com/en/sql-reference/data-types-text
    escapes_backslashes = True

    def param(self, index: int) -> str:
        """Generate Snowflake parameter placeholder.

        Snowflake uses ? for positional parameters.

        Args:
            index: 1-based parameter index (ignored for positional)

        Returns:
            Parameter placeholder '?'
        """
        return "?"

    def statement_timeout_sql(self, seconds: int) -> str:
        """Generate the session-level STATEMENT_TIMEOUT_IN_SECONDS parameter."""
        return f"ALTER SESSION SET STATEMENT_TIMEOUT_IN_SECONDS = {seconds}"

    def is_statement_timeout_error(self, exc: Exception) -> bool:
        """Return True when Snowflake rejected the statement for exceeding its timeout."""
        return "statement timeout" in str(exc).lower()

    def bulk_schema_sql(self, scope: str = "") -> str:
        """Whole-database columns from Snowflake's per-database INFORMATION_SCHEMA.

        ``scope`` names the database to qualify the view; when omitted the
        connection's current database is used. The database is double-quoted (and
        embedded quotes doubled) so a case-sensitive or reserved-word database
        name resolves correctly rather than being folded to upper-case. (Account-
        wide coverage would be ``SNOWFLAKE.ACCOUNT_USAGE.COLUMNS``, which lags
        ~90 min — deliberately not used here so the result is live.)
        """
        db = f'"{scope.replace(chr(34), chr(34) * 2)}".' if scope else ""
        return (
            "SELECT table_schema, table_name, column_name, data_type "
            f"FROM {db}INFORMATION_SCHEMA.COLUMNS "
            "WHERE table_schema <> 'INFORMATION_SCHEMA' "
            "ORDER BY table_schema, table_name, ordinal_position"
        )

    def bulk_tables_sql(self, scope: str, schemas: Sequence[str]) -> str:
        """Whole-database tables; ``scope`` qualifies the database as in
        :meth:`bulk_schema_sql`."""
        db = f'"{scope.replace(chr(34), chr(34) * 2)}".' if scope else ""
        condition = self.schema_condition(schemas, "table_schema")
        extra = f"AND {condition} "
        return (
            "SELECT table_schema, table_name "
            f"FROM {db}INFORMATION_SCHEMA.TABLES "
            "WHERE table_schema <> 'INFORMATION_SCHEMA' "
            f"{extra}"
            "ORDER BY table_schema, table_name"
        )
