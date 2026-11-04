"""Compile-time authoring checks for SQL query strings."""

from __future__ import annotations

import re

import sqlglot
import sqlglot.errors

from dbt_charts.core.compile.sql_guard import sqlglot_dialect

# `date '{{ var }}'`: a variable quoted as a date/time/timestamp literal.
# Only the bound-parameter backends -- backends/duckdb_local.py and backends/sqlite.py,
# whose param_style is the source dialect's own placeholder -- bind a
# variable through a real driver placeholder, which `_clean_parameter_quotes`
# (compile/template/parameterized.py) then strips the surrounding quotes from,
# so only there does this compile to invalid `date $1`. Every other adapter
# (the dbt backend for postgres/snowflake/bigquery/etc. -- its
# param_style -- and DbtBackend for dbt_profile) renders
# variables as inline literal text, so the same SQL compiles to valid
# `date '2024-01-01'`. `cast('{{ var }}' as date)` keeps the variable an
# ordinary string parameter on every dialect, so it's the dialect-agnostic fix.
_DATE_LITERAL_VARIABLE_RE = re.compile(
    r"\b(date|time|timestamp)\s*'\{\{\s*(.+?)\s*\}\}'", re.IGNORECASE
)

# The two adapters that bind variables as real query parameters instead of
# inline literal text (see the module comment above). dialect is the raw
# source `type` string from dialect_for_source, e.g. "duckdb" -- not a
# sqlglot dialect name.
_BOUND_PARAM_DIALECTS = frozenset({"duckdb", "sqlite"})


def find_date_literal_variable(
    sql: str, *, dialect: str | None
) -> re.Match[str] | None:
    """Return the match if `sql` quotes a Jinja variable as a date/time/timestamp
    literal, e.g. `date '{{ latest_month }}'`, on a dialect where that variable
    binds as a real query parameter (duckdb, sqlite). Group 1 is the keyword
    (date/time/timestamp), group 2 is the variable expression.

    On every other dialect (including None, e.g. a dbt_profile source whose
    real dialect isn't known until execute), the variable renders as inline
    literal text and the same SQL is valid -- so this returns None.
    """
    if dialect not in _BOUND_PARAM_DIALECTS:
        return None
    return _DATE_LITERAL_VARIABLE_RE.search(sql)


def _sql_parses(sql: str, dialect: str | None) -> bool:
    """Return True if sqlglot.parse succeeds without error, False otherwise."""
    try:
        sqlglot.parse(sql, read=sqlglot_dialect(dialect))
    except (sqlglot.errors.ParseError, sqlglot.errors.TokenError):
        return False
    return True


def has_literal_escaped_newlines(sql: str, *, dialect: str | None) -> bool:
    """Return True if sql contains literal \\n that is the cause of a parse failure.

    Three-step parse-replace-parse heuristic:
    1. If '\\n' not in sql → False (fast path, no parse needed)
    2. If sqlglot.parse(sql) succeeds → False (\\n is inside a SQL string literal
       or otherwise accepted — step 1 guards the cost of this parse)
    3. If sqlglot.parse(sql.replace('\\n', newline)) succeeds → True
       (the replacement fixed the failure: author used single-quoted YAML instead of
       a block scalar, so \\n was preserved literally instead of becoming a real newline)
    4. Else → False (genuine syntax/Jinja issue unrelated to \\n; step 4 is the
       absence of step 3)

    Note: SQL with Jinja expressions ({{ ref('orders') }}) bypasses detection — both
    step 2 and step 3 fail to parse Jinja skeletons, so the function returns False
    and the existing UnparseableSqlError path handles it at runtime. This is intentional.

    Args:
        sql: Raw SQL string after YAML parsing.
        dialect: dbt charts dialect name (e.g. "duckdb", "sqlserver"), or None for the default.
            Normalized to the sqlglot equivalent via sqlglot_dialect().

    Returns:
        True if the literal-\\n authoring error is detected, False otherwise.
    """
    # Step 1: fast exit — no literal \n present, skip all parsing.
    if "\\n" not in sql:
        return False

    # Step 2: parses fine as-is — \n is inside a SQL string literal or similar.
    if _sql_parses(sql, dialect):
        return False

    # Step 3: parses after replacing literal \n with a real newline — authoring mistake.
    # Step 4 is the implicit False when step 3 also fails (genuine syntax/Jinja issue).
    # Author wrote sql: 'SELECT\n...' in YAML single-quotes instead of sql: | block scalar.
    return _sql_parses(sql.replace("\\n", "\n"), dialect)
