"""Advice for a warehouse's unknown-column rejection, from the relation's real columns."""

from __future__ import annotations

import re
from typing import TYPE_CHECKING

import sqlglot
import sqlglot.errors
from sqlglot import exp

from dbt_charts.core.compile.parse.yaml_error_formatter import nearest_names

if TYPE_CHECKING:
    import duckdb

# The DuckDB binder messages that name the missing column.
_MISSING_COLUMN_RE = re.compile(
    r'(?:Referenced column|does not have a column named) "([^"]+)"'
)


def _quoted(column: str) -> str:
    return f'"{column}"' if not re.fullmatch(r"[a-z_][a-z0-9_]*", column) else column


def duckdb_unknown_column_advice(
    conn: duckdb.DuckDBPyConnection, sql: str, error_text: str
) -> str | None:
    """Name the nearest real column, or list the table's columns when none is near.

    Only a query over one base table is advised: with a JOIN, CTE or subquery the
    missing name may belong to any relation, and a suggestion from the wrong one
    is worse than none. Returns None for those, when the error names no column,
    or when the table cannot be described, so the warehouse's message stands alone.
    The caller holds the connection's execute lock.
    """
    import duckdb

    match = _MISSING_COLUMN_RE.search(error_text)
    if match is None:
        return None
    try:
        tree = sqlglot.parse_one(sql, read="duckdb")
    except sqlglot.errors.SqlglotError:
        return None
    if any(tree.find(kind) for kind in (exp.CTE, exp.Join, exp.Subquery)):
        return None
    tables = list(tree.find_all(exp.Table))
    if len(tables) != 1 or not isinstance(tables[0].this, exp.Identifier):
        return None
    table = tables[0]
    bare = exp.Table(
        this=table.this.copy(),
        db=table.args.get("db"),
        catalog=table.args.get("catalog"),
    )
    try:
        rows = conn.execute(f"DESCRIBE {bare.sql('duckdb')}").fetchall()
    except duckdb.Error:
        return None
    columns = [row[0] for row in rows]
    near = nearest_names(match.group(1), columns, limit=2)
    if near:
        names = " or ".join(f'"{column}"' for column in near)
        return f"Did you mean {names}? (a column of {table.name})"
    return f"Columns in {table.name}: {', '.join(_quoted(c) for c in columns)}."
