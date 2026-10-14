"""Redshift dialect implementation.

Redshift is PostgreSQL-based (8.0.2), so it inherits PostgreSQL behavior except
where the fork's age shows, or its own extensions diverge — string literals,
session reset, and statement_timeout, all below.
"""

from typing import Any

from dbt_charts.core.dialects.postgres import PostgresDialect

# The server's cancel and its error packet must arrive before the client's read timer fires.
_STATEMENT_CAP_HEADROOM_SECONDS = 10


class RedshiftDialect(PostgresDialect):
    """Redshift dialect — PostgreSQL, minus what the 8.0.2 fork predates."""

    name = "redshift"
    folds_identifier_case = True
    # Declared rather than inherited, and the opposite of Postgres': the fork
    # predates standard-conforming strings. QUOTE_LITERAL "appropriately doubles
    # any embedded single quotation marks and backslashes", and LIKE's default
    # escape character is written as the two-character literal '\\' precisely
    # because the literal parser collapses it to one backslash.
    # https://docs.aws.amazon.com/redshift/latest/dg/r_QUOTE_LITERAL.html
    escapes_backslashes = True

    def session_reset_statements(self) -> tuple[str, ...]:
        """Redshift's Postgres fork predates ``DISCARD`` and never gained it, so the
        pool drops the connection to clear session-scoped temp objects instead."""
        return ()

    def statement_timeout_sql(self, seconds: int) -> str:
        """Redshift's ``statement_timeout`` takes an integer of milliseconds, not
        Postgres's interval-string syntax.
        https://docs.aws.amazon.com/redshift/latest/dg/r_statement_timeout.html
        """
        return f"SET statement_timeout TO {seconds * 1000}"

    def connect_credentials(
        self,
        creds: dict[str, Any],  # type-state: explicit_any — raw source_config
        seconds: int,
    ) -> dict[str, Any]:  # type-state: explicit_any — raw source_config
        """redshift_connector applies ``connect_timeout`` as the socket timeout
        for the connection's whole life, so it also caps every read and must
        outlast the statement cap."""
        authored = (
            creds.get("connect_timeout")
            # type-state: silent_fallback — unset on the model and on dbt_profile
            # targets; 0 is the max() identity, so the cap plus headroom then applies
            or 0
        )
        return {
            **creds,
            "connect_timeout": max(authored, seconds + _STATEMENT_CAP_HEADROOM_SECONDS),
        }
