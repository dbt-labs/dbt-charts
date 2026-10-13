"""DuckDB dialect implementation.

DuckDB uses:
- $1, $2, ... for parameter placeholders (also supports ?)
"""

from collections.abc import Sequence

from dbt_charts.core.dialects.base import (
    SQLDialect,
    ansi_information_schema_columns_sql,
    ansi_information_schema_tables_sql,
)


class DuckDBDialect(SQLDialect):
    """DuckDB dialect with $1, $2 parameter style.

    DuckDB is similar to PostgreSQL and uses the same parameter syntax.

    Example:
        >>> dialect = DuckDBDialect()
        >>> dialect.param(1)
        '$1'
    """

    name = "duckdb"
    folds_identifier_case = True
    # Standard-conforming like Postgres: SELECT 'C:\\Users' returns the eight
    # characters typed. C-style escapes are the separate E'...' form.
    # https://duckdb.org/docs/stable/sql/data_types/literal_types
    escapes_backslashes = False

    def param(self, index: int) -> str:
        """Generate DuckDB parameter placeholder.

        Args:
            index: 1-based parameter index

        Returns:
            Parameter placeholder in $N format
        """
        return f"${index}"

    def statement_timeout_sql(self, seconds: int) -> None:
        """No-op: DuckDB is an in-process local file database with no server
        to enforce a statement timeout against — max_query_duration_seconds
        has no effect here.
        """
        return None

    def bulk_schema_sql(self, scope: str = "") -> str:
        """DuckDB serves the whole file's schema from the ANSI columns view."""
        return ansi_information_schema_columns_sql()

    def bulk_tables_sql(self, scope: str, schemas: Sequence[str]) -> str:
        """DuckDB lists the whole file from the ANSI tables view."""
        return ansi_information_schema_tables_sql(
            self.schema_condition(schemas, "table_schema")
        )
