"""The one SQL identifier quoter: validate, then quote for a sqlglot dialect."""

import re

from sqlglot import exp

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
