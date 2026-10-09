"""MySQL dialect implementation.

MySQL uses:
- %s for parameter placeholders (positional)
"""

from dbt_charts.core.dialects.base import SQLDialect


class MySQLDialect(SQLDialect):
    """MySQL dialect with %s parameter style.

    MySQL uses format-style parameter placeholders.

    Example:
        >>> dialect = MySQLDialect()
        >>> dialect.param(1)
        '%s'
    """

    name = "mysql"
    # Each escape sequence begins with a backslash, the escape character, unless
    # the server runs NO_BACKSLASH_ESCAPES — not the default, and MariaDB agrees.
    # https://dev.mysql.com/doc/refman/8.4/en/string-literals.html
    escapes_backslashes = True

    def param(self, index: int) -> str:
        """Generate MySQL parameter placeholder.

        MySQL uses %s for all parameters (positional, not indexed).

        Args:
            index: 1-based parameter index (ignored for MySQL)

        Returns:
            Parameter placeholder '%s'
        """
        return "%s"

    def quote(self, identifier: str) -> str:
        """Backtick-quote an identifier. Double quotes only work under the
        non-default ANSI_QUOTES mode, so backticks are the portable form.

        https://dev.mysql.com/doc/refman/8.4/en/identifiers.html
        """
        return "`" + identifier.replace("`", "``") + "`"
