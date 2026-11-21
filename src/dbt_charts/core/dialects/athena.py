"""Athena dialect implementation.

Athena uses:
- ? for positional parameters
"""

from __future__ import annotations

from dbt_charts.core.dialects.base import SQLDialect


class AthenaDialect(SQLDialect):
    """Athena dialect with ? parameter style.

    AWS Athena is based on Presto/Trino.

    Example:
        >>> dialect = AthenaDialect()
        >>> dialect.param(1)
        '?'
    """

    name = "athena"
    # Trino/Presto (and Athena on their engines) escape a quote by doubling it;
    # backslash is an ordinary character outside the U&'...' unicode form.
    # https://trino.io/docs/current/language/types.html
    escapes_backslashes = False

    def param(self, index: int) -> str:
        """Generate Athena parameter placeholder.

        Athena uses ? for positional parameters.

        Args:
            index: 1-based parameter index (ignored for positional)

        Returns:
            Parameter placeholder '?'
        """
        return "?"
