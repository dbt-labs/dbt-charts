"""SQL Server dialect implementation.

SQL Server uses:
- @p1, @p2, ... for parameter placeholders
"""

import re
from typing import Any

from dbt_charts.core.dialects.base import SQLDialect

# ODBC reports an expired statement as SQLSTATE HYT00 with this text, and a
# login that timed out as HYT00 too ("Login timeout expired"), so the state
# alone cannot tell a cancelled query from a connection that never opened.
_QUERY_TIMEOUT_RE = re.compile(r"\[HYT00\][^\n]*\bQuery timeout expired\b")


def _profile_cap_seconds(
    value: Any,  # type-state: explicit_any — raw profile value
) -> int:
    """A profile's ``query_timeout`` as whole seconds, where 0 means no cap.

    Accepts what dbt-sqlserver itself accepts: an int or an integer-like
    string, never a bool (an ``int`` in Python that would read ``true`` as one
    second) and never a negative.
    """
    if isinstance(value, bool) or not isinstance(value, (int, str)):
        raise ValueError(f"query_timeout must be whole seconds, got {value!r}")
    try:
        seconds = int(value)
    except ValueError as exc:
        raise ValueError(f"query_timeout must be whole seconds, got {value!r}") from exc
    if seconds < 0:
        raise ValueError(f"query_timeout must be >= 0 seconds, got {value!r}")
    return seconds


class SQLServerDialect(SQLDialect):
    """SQL Server dialect with @pN parameter style.

    SQL Server uses named parameters with @ prefix.

    statement_timeout_sql() is not overridden: T-SQL has no session-level
    ``SET`` for a per-statement cap. See :meth:`connect_credentials`.

    Example:
        >>> dialect = SQLServerDialect()
        >>> dialect.param(1)
        '@p1'
    """

    name = "sqlserver"
    # T-SQL character constants have one escape: an embedded quote is written as
    # two single quotation marks. Backslash carries no meaning.
    # https://learn.microsoft.com/en-us/sql/t-sql/data-types/constants-transact-sql
    escapes_backslashes = False
    uses_named_params = True

    def param(self, index: int) -> str:
        """Generate SQL Server parameter placeholder.

        SQL Server uses @pN named style.

        Args:
            index: 1-based parameter index

        Returns:
            Parameter placeholder in @pN format
        """
        return f"@p{index}"

    def resolve_timeout_seconds(
        self,
        creds: dict[str, Any],  # type-state: explicit_any — raw source_config
        seconds: int,
    ) -> int:
        """Narrow the ceiling to a profile that caps itself harder.

        ``query_timeout`` is a dbt profile key, so a value there is the author's
        own setting; the stricter of the two wins. ``0`` is dbt-sqlserver's "no
        timeout", which narrows nothing.
        """
        authored = creds.get(
            "query_timeout"
        )  # type-state: silent_fallback — an unauthored query_timeout narrows nothing, the documented dbt-sqlserver default
        if authored is None:
            return seconds
        cap = _profile_cap_seconds(authored)
        return min(seconds, cap) if cap else seconds

    def connect_credentials(
        self,
        creds: dict[str, Any],  # type-state: explicit_any — raw source_config
        seconds: int,
    ) -> dict[str, Any]:  # type-state: explicit_any — raw source_config
        """Put the resolved cap on the credentials' ``query_timeout``.

        dbt-sqlserver sets it as the pyodbc connection's ``timeout`` (the ODBC
        query timeout) when it opens the connection, so it holds for every
        statement on a pooled connection.

        Raises:
            ValueError: the ``mssql-python`` backend ignores the credential, so
                the cap would silently not hold.
        """
        if creds.get("backend") == "mssql-python":
            raise ValueError(
                "max_query_duration_seconds cannot be enforced on the "
                "mssql-python backend, which ignores query_timeout; use the "
                "default pyodbc backend"
            )
        return {**creds, "query_timeout": seconds}

    def is_statement_timeout_error(self, exc: Exception) -> bool:
        """Return True when the ODBC driver cancelled the statement at ``query_timeout``.

        dbt-sqlserver re-raises the driver's error as a ``DbtDatabaseError``
        that keeps the driver's text, so the text is what there is to read.
        """
        return _QUERY_TIMEOUT_RE.search(str(exc)) is not None
