"""SQLite dialect implementation.

SQLite uses:
- ? for parameter placeholders. Using ? (all identical) causes
  render_parameterized to emit one ? per variable occurrence
  (deduplication is disabled because param(1) == param(2)), with
  params in text order. _execute_sqlite passes the param list
  directly to conn.execute() — no inlining, so Python date/datetime
  objects are handled correctly by sqlite3's default adapters.
- No ILIKE (only LIKE, which is case-insensitive for ASCII on SQLite)
"""

from collections.abc import Sequence

from dbt_charts.core.dialects.base import SQLDialect


class SQLiteDialect(SQLDialect):
    """SQLite dialect with ? placeholder style for native parameter binding.

    Using ? (all identical) disables render_parameterized's $N deduplication,
    so every variable reference emits its own ? and corresponding param entry
    (in text order). _execute_sqlite passes params to conn.execute() directly.

    Example:
        >>> dialect = SQLiteDialect()
        >>> dialect.param(1)
        '?'
    """

    name = "sqlite"
    # "C-style escapes using the backslash character are not supported because
    # they are not standard SQL" — a quote is escaped by doubling it.
    # https://www.sqlite.org/lang_expr.html
    escapes_backslashes = False

    def param(self, index: int) -> str:
        """Generate SQLite parameter placeholder.

        Returns ? regardless of index. sqlite3 binds positionally in text order.

        Args:
            index: 1-based parameter index (unused — all positions use ?)

        Returns:
            '?' placeholder for sqlite3 positional binding
        """
        return "?"

    def statement_timeout_sql(self, seconds: int) -> None:
        """No-op: SQLite is an in-process local file database with no server
        to enforce a statement timeout against — max_query_duration_seconds
        has no effect here.
        """
        return None

    def date_expr(self, column: str) -> str:
        """DATE(x): CAST(x AS DATE) takes NUMERIC affinity on SQLite (no DATE
        type exists), truncating a date-like string to its leading integer run
        ('2024-01-15' -> 2024) and comparing an INTEGER against TEXT bounds,
        which SQLite always orders as false. DATE(x) parses the string and
        returns the normalized date text instead.
        """
        return f"DATE({column})"

    def bulk_schema_sql(self, scope: str = "") -> str:
        """Whole-database schema in one query.

        SQLite has no ``information_schema``; the table-valued
        ``pragma_table_info(name)`` joins every relation in ``sqlite_master`` to
        its columns. Tables AND views are included (matching the ANSI dialects,
        whose ``information_schema.columns`` covers both). Schema is always
        ``main``; internal ``sqlite_*`` relations excluded.
        """
        return (
            "SELECT 'main' AS table_schema, m.name AS table_name, "
            "p.name AS column_name, p.type AS data_type "
            "FROM sqlite_master m JOIN pragma_table_info(m.name) p "
            "WHERE m.type IN ('table', 'view') AND m.name NOT LIKE 'sqlite_%' "
            "ORDER BY m.name, p.cid"
        )

    def bulk_tables_sql(self, scope: str, schemas: Sequence[str]) -> str:
        """Whole-database tables and views from ``sqlite_master``."""
        extra = f"AND {self.schema_condition(schemas, chr(39) + 'main' + chr(39))} "
        return (
            "SELECT 'main' AS table_schema, name AS table_name "
            "FROM sqlite_master "
            "WHERE type IN ('table', 'view') AND name NOT LIKE 'sqlite_%' "
            f"{extra}"
            "ORDER BY name"
        )
