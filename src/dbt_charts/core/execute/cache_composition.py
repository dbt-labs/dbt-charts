"""Isolated-DuckDB execution over named in-memory rowsets.

Runs author-controlled SQL over named row-dict sets in a fresh in-process DuckDB
with external access disabled. The engine's namespace holds ONLY the registered
rows — no application tables, no other cache entries, nothing on any shared or
application-database connection — so the SQL cannot read data outside the rows it
explicitly references. This is the one home for both ``{{ queries.X.cache }}``
composition (fed the upstream rows by the executor) and the Cloud file-source
path (fed the materialized file rows).
"""

from __future__ import annotations

import json
from typing import TYPE_CHECKING, Any

from dbt_charts.core.execute.adapters.base import reject_duplicate_columns
from dbt_charts.core.execute.arrow_ingest import ingest, rows_to_arrow
from dbt_charts.core.execute.cache_backend import CacheRows

if TYPE_CHECKING:
    import duckdb


def quote_identifier(identifier: str) -> str:
    """Quote a SQL identifier for DuckDB, doubling embedded quotes."""
    escaped = identifier.replace('"', '""')
    return f'"{escaped}"'


def rows_from_result(
    result: duckdb.DuckDBPyConnection, limit: int | None = None
) -> CacheRows:
    """Reconstruct row dicts from an executed DuckDB result, decoding JSON columns.

    list/dict values are written to a JSON column as text, and DuckDB hands a
    JSON-typed column back as a Python ``str``. Decode those columns here so the
    round-trip is symmetric — callers (and the renderer) expect the original
    ``list``/``dict``, not the serialized string.

    ``limit`` bounds the fetch itself via ``fetchmany()`` instead of pulling
    every row over and slicing after — None (the default) fetches everything,
    unaffected for every caller that doesn't pass one.
    """
    description = result.description
    columns = [d[0] for d in description]
    json_cols = {i for i, d in enumerate(description) if str(d[1]) == "JSON"}
    reject_duplicate_columns(columns)
    rows = result.fetchmany(limit) if limit else result.fetchall()
    if not json_cols:
        return [dict(zip(columns, row, strict=False)) for row in rows]
    return [
        dict(
            zip(
                columns,
                [
                    json.loads(v) if i in json_cols and v is not None else v
                    for i, v in enumerate(row)
                ],
                strict=False,
            )
        )
        for row in rows
    ]


def compose_over_named_rows(
    sql: str,
    named_rows: dict[str, CacheRows],
    params: list[Any],
    limit: int | None = None,
) -> CacheRows:
    """Execute *sql* over *named_rows* in an isolated in-process DuckDB.

    Each entry in *named_rows* is copied into a table under its key. *sql* is
    parameterized ($1/$2/…) with *params*
    bound by the driver — never string-interpolated — and external access is
    disabled, so the composing SQL can reach neither the filesystem/network nor
    any table other than the registered rows.

    Args:
        sql: Parameterized composing SQL; every table reference must resolve to
            a key of *named_rows*.
        named_rows: Mapping of table name → row dicts to register.
        params: Positional parameter values in $1/$2/… order.
        limit: Bounds the fetch itself via ``fetchmany()`` — a join across two
            upstream tables each near the row ceiling can otherwise blow up
            memory during composition, before any post-fetch check runs. None
            fetches everything.

    Returns:
        List of row dicts.

    Raises:
        RuntimeError: Wraps any DuckDB execution error with context (including a
            reference to a table that is not one of the registered rowsets).
    """
    import duckdb

    conn = duckdb.connect(":memory:", config={"enable_external_access": False})
    try:
        for name, rows in named_rows.items():
            ingest(conn, quote_identifier(name), rows_to_arrow(rows))
        return rows_from_result(conn.execute(sql, params), limit=limit)
    except Exception as e:  # noqa: BLE001 — duckdb and row-to-Arrow conversion raise various exception types at the composition boundary
        raise RuntimeError(f"Isolated-engine query failed: {e}\nSQL:\n{sql}") from e
    finally:
        conn.close()
