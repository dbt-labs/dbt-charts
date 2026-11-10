"""SQL identifiers: the one quoter, and the spelling rules between SQL text,
the warehouse catalog, and the result column names boards reference."""

import re

from sqlglot import exp
from sqlglot.dialects.dialect import Dialect, NormalizationStrategy

_CASE_KEEPING_DIALECTS = frozenset({"duckdb", "sqlite"})

# sqlglot quoting is not an escape boundary (BigQuery leaves a backslash
# unescaped), so deny quote chars, backslash, brackets, and control chars
# instead of escaping them. Braces are denied because board SQL is re-rendered as Jinja.
_IDENTIFIER_FORBIDDEN_RE = re.compile(r"[\"'`\\\[\]{}\x00-\x1f\x7f-\x9f]")


def sql_identifier(name: str, sqlglot_dialect: str | None) -> str:
    """Validate and quote a SQL identifier for the given sqlglot dialect.

    Args:
        name: Raw identifier string.
        sqlglot_dialect: A sqlglot dialect name (``"bigquery"``, ``"tsql"``),
            or ``None`` for sqlglot's default (ANSI double-quoting). Not a
            dbt charts source type; translate with
            ``compile.sql_guard.sqlglot_dialect``.

    Returns:
        Quoted SQL identifier string, e.g. ``"orders"`` (ANSI) or
        `` `orders` `` (BigQuery).

    Raises:
        ValueError: If `name` is empty or contains a quote, backslash,
            bracket, brace, or control character.
    """
    if not name or _IDENTIFIER_FORBIDDEN_RE.search(name):
        raise ValueError(
            f"{name!r} is not a valid SQL identifier: quote characters, "
            "backslashes, brackets, braces, and control characters are not "
            "allowed."
        )
    return exp.to_identifier(name, quoted=True).sql(dialect=sqlglot_dialect)


def catalog_name(name: str, quoted: bool, sqlglot_dialect: str | None) -> str:
    """The catalog spelling of an identifier written in SQL.

    An unquoted name folds where the dialect folds (Snowflake uppercases,
    Postgres lowercases); a quoted name is exact. A case-insensitive dialect
    stores names as created, so the written spelling is kept, as it is for an
    unknown dialect (``None``).
    """
    if quoted or sqlglot_dialect is None:
        return name
    strategy = Dialect.get_or_raise(sqlglot_dialect).NORMALIZATION_STRATEGY
    if strategy is NormalizationStrategy.UPPERCASE:
        return name.upper()
    if strategy is NormalizationStrategy.LOWERCASE:
        return name.lower()
    return name


def result_column_name(name: str, sqlglot_dialect: str | None) -> str:
    """The name a warehouse column carries in a query result, so the name boards
    reference it by.

    Sources on the local DuckDB and SQLite engines (lake compute speaks DuckDB)
    keep the warehouse's spelling; every other source's backend lowercases it.
    An unknown dialect (``None``) keeps every name.
    """
    if sqlglot_dialect is None or sqlglot_dialect in _CASE_KEEPING_DIALECTS:
        return name
    return name.lower()
