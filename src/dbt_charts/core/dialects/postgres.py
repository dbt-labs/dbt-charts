"""PostgreSQL dialect implementation.

PostgreSQL uses:
- $1, $2, ... for parameter placeholders
"""

from dbt_charts.core.dialects.base import (
    SQLDialect,
    ansi_information_schema_columns_sql,
)


class PostgresDialect(SQLDialect):
    """PostgreSQL dialect with $1, $2 parameter style.

    PostgreSQL is the default dialect and uses standard SQL syntax
    with numbered parameter placeholders.

    Example:
        >>> dialect = PostgresDialect()
        >>> dialect.param(1)
        '$1'
        >>> dialect.params(3)
        ['$1', '$2', '$3']
    """

    name = "postgres"
    # standard_conforming_strings has defaulted to on since 9.1, so a backslash
    # in an ordinary '...' constant is a plain character; escapes need E'...'.
    # https://www.postgresql.org/docs/current/sql-syntax-lexical.html
    escapes_backslashes = False

    def param(self, index: int) -> str:
        """Generate PostgreSQL parameter placeholder.

        Args:
            index: 1-based parameter index

        Returns:
            Parameter placeholder in $N format
        """
        return f"${index}"

    def statement_timeout_sql(self, seconds: int) -> str:
        """Generate the session-level statement_timeout SET statement.

        Not inherited by RedshiftDialect — Redshift's fork takes an integer of
        milliseconds, not this interval-string syntax.
        """
        return f"SET statement_timeout = '{seconds}s'"

    def is_statement_timeout_error(self, exc: Exception) -> bool:
        """Return True when Postgres cancelled the statement due to statement_timeout."""
        return "statement timeout" in str(exc).lower()

    def session_reset_statements(self) -> tuple[str, ...]:
        """Drop session-scoped temp objects, keeping the connection's own settings.

        ``DISCARD TEMP`` rather than ``DISCARD ALL``: the latter also runs
        ``RESET ALL`` and ``SET SESSION AUTHORIZATION DEFAULT``, throwing away
        the ``statement_timeout`` sent at connection setup and any ``set role``
        the profile asked dbt for. Postgres refuses ``DISCARD`` inside a
        transaction block, which is safe here only because these connections are
        opened in autocommit.

        Not inherited by RedshiftDialect — Redshift has no ``DISCARD``.
        """
        return ("DISCARD TEMP",)

    def bulk_schema_sql(self, scope: str = "") -> str:
        """Postgres/Redshift serve the whole database from the ANSI columns view."""
        return ansi_information_schema_columns_sql()
