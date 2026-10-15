"""CannedShapeAdapter — test-only canned-row builder.

Parses a SQL SELECT and produces one canned row keyed by SELECT aliases — a
placeholder for warehouse SQL adapters that aren't available in CI. Currently
only exercised by its own unit test in test_canned_shape_adapter.py.

This file is TESTS-ONLY. Never imported from production code.
"""

from __future__ import annotations

from typing import Any

import sqlglot
import sqlglot.errors

from dbt_charts.core.compile.models.query.normalized import SqlQuery


def canned_row_for_sql(
    query: SqlQuery, dialect: str = "bigquery"
) -> list[dict[str, Any]]:
    """Return one canned row by parsing the SQL SELECT aliases.

    Uses *dialect* for sqlglot parsing (the query's `source` is a name/path
    string post-D-12, not a dialect-bearing config, so callers pass the dialect
    explicitly). Raises sqlglot.errors.ParseError if the SQL cannot be parsed —
    a SQL we can't parse is a gate bug.
    """
    statement = sqlglot.parse_one(query.sql, dialect=dialect)
    select = (
        statement
        if isinstance(statement, sqlglot.exp.Select)
        else statement.find(sqlglot.exp.Select)
    )
    if select is None:
        raise sqlglot.errors.ParseError(f"No SELECT found in sql={query.sql[:80]!r}")

    aliases = [
        expr.alias_or_name or f"col_{i}" for i, expr in enumerate(select.expressions)
    ]
    if not aliases:
        raise sqlglot.errors.ParseError(
            f"No SELECT columns found in sql={query.sql[:80]!r}"
        )

    import re

    _NUMERIC_RE = re.compile(
        r"(^|_)(count|total|sum|avg|amount|revenue|arr|mrr|num|n|ct|qty)(_|$)",
        re.IGNORECASE,
    )
    return [{a: (1 if _NUMERIC_RE.search(a) else "a") for a in aliases}]
