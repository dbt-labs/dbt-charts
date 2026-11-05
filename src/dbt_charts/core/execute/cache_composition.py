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

import nanoarrow as na

from dbt_charts.core.column_types import ColumnType
from dbt_charts.core.execute.arrow_ingest import (
    arrow_type,
    decimal_precision_scale,
    encode,
    table as arrow_table,
)
from dbt_charts.core.execute.cache_backend import CacheRows
from dbt_charts.core.execute.duckdb_types import duckdb_schema, typed_rows

if TYPE_CHECKING:
    import duckdb
    from nanoarrow._array import CArray


class CompositionError(RuntimeError):
    """The isolated engine refused a query composed over cached rows."""


def quote_identifier(identifier: str) -> str:
    """Quote a SQL identifier for DuckDB, doubling embedded quotes."""
    escaped = identifier.replace('"', '""')
    return f'"{escaped}"'


def rows_from_result(
    result: duckdb.DuckDBPyConnection, limit: int | None = None
) -> CacheRows:
    """Reconstruct row dicts from an executed DuckDB result, each cell converted
    to the Python value its declared type names (see ``typed_rows``).

    ``limit`` bounds the fetch itself via ``fetchmany()`` instead of pulling
    every row over and slicing after — None (the default) fetches everything,
    unaffected for every caller that doesn't pass one.
    """
    rows = result.fetchmany(limit) if limit else result.fetchall()
    return typed_rows(result.description, rows)


_DUCKDB_TYPES: dict[str, str] = {
    "string": "VARCHAR",
    "integer": "BIGINT",
    "float": "DOUBLE",
    "boolean": "BOOLEAN",
    "date": "DATE",
    "datetime": "TIMESTAMP",
    "datetime_tz": "TIMESTAMPTZ",
    "time": "TIME",
    "bytes": "BLOB",
    "json": "JSON",
}


def _ddl_type(
    column_type: ColumnType,
    cells: list[Any],  # type-state: explicit_any — one column's Python cell values
) -> str:
    """DuckDB type for *column_type*; a decimal's precision and scale come from the
    *cells* (the declared type carries none), raising when no exact type exists."""
    present = [cell for cell in cells if cell is not None]
    if column_type.element is not None:
        elements = [item for cell in present for item in cell]
        return f"{_ddl_type(column_type.element, elements)}[]"
    if column_type.fields is not None:
        fields = ", ".join(
            f"{quote_identifier(name)} "
            f"{_ddl_type(field, [cell[name] for cell in present])}"
            for name, field in column_type.fields
        )
        return f"STRUCT({fields})"
    if column_type.kind == "decimal":
        precision, scale = decimal_precision_scale(present)
        return f"DECIMAL({precision}, {scale})"
    return _DUCKDB_TYPES[column_type.kind]


# The ``arrow_ingest`` Python kind of each scalar declared kind; a JSON cell is
# loaded as its text.
_PYTHON_KINDS = {
    "string": "str",
    "integer": "int",
    "float": "float",
    "decimal": "decimal",
    "boolean": "bool",
    "date": "date",
    "datetime": "datetime",
    "datetime_tz": "datetime_tz",
    "time": "time",
    "bytes": "bytes",
    "json": "str",
}


def _json_text(
    column_type: ColumnType,
    cell: Any,  # type-state: explicit_any — one Python cell value
) -> Any:  # type-state: explicit_any — the cell with its JSON values as text
    """*cell* with every JSON value replaced by its text, so DuckDB never infers a
    struct from the documents."""
    if cell is None:
        return None
    if column_type.element is not None:
        return [_json_text(column_type.element, item) for item in cell]
    if column_type.fields is not None:
        return {
            name: _json_text(field, cell[name]) for name, field in column_type.fields
        }
    return json.dumps(cell) if column_type.kind == "json" else cell


def _register(
    conn: duckdb.DuckDBPyConnection,
    name: str,
    rows: CacheRows,
    schema: dict[str, ColumnType],
) -> None:
    """Create table *name* from the declared *schema* and insert *rows* in one
    statement. Scalar columns travel as one Arrow table typed from the
    declaration; list and struct columns as one bound list parameter each (a
    row-wise executemany is ~150x slower). A JSON cell is loaded as its text."""
    values = [[row[c] for row in rows] for c in schema]
    ddl = [
        _ddl_type(t, column) for t, column in zip(schema.values(), values, strict=True)
    ]
    columns = ", ".join(
        f"{quote_identifier(c)} {d}" for c, d in zip(schema, ddl, strict=True)
    )
    table = quote_identifier(name)
    conn.execute(f"CREATE TABLE {table} ({columns})")
    if not rows:
        return
    arrays: list[tuple[str, CArray]] = []
    nested: list[list[Any]] = []  # type-state: explicit_any — bound cell values
    unnests: list[str] = []
    selects: list[str] = []
    for column_type, column, column_ddl in zip(
        schema.values(), values, ddl, strict=True
    ):
        if column_type.element is None and column_type.fields is None:
            column = [_json_text(column_type, cell) for cell in column]
            present = [cell for cell in column if cell is not None]
            kind = _PYTHON_KINDS[column_type.kind]
            label = f"_s{len(arrays)}"
            arrays.append((label, encode(column, arrow_type(kind, present))))
            selects.append(label)
        else:
            label = f"_n{len(nested)}"
            nested.append([_json_text(column_type, cell) for cell in column])
            unnests.append(f"CAST(unnest(${len(nested)}) AS {column_ddl}) AS {label}")
            selects.append(label)
    sources = []
    view = "_dct_composition_scalars"
    if arrays:
        conn.register(view, na.Array(arrow_table(arrays, len(rows))))
        sources.append(view)
    if unnests:
        sources.append(f"(SELECT {', '.join(unnests)})")
    try:
        conn.execute(
            f"INSERT INTO {table} SELECT {', '.join(selects)} "
            f"FROM {' POSITIONAL JOIN '.join(sources)}",
            nested,
        )
    finally:
        if arrays:
            conn.unregister(view)


def compose_over_named_rows(
    sql: str,
    named_rows: dict[str, CacheRows],
    params: list[Any],
    schemas: dict[str, dict[str, ColumnType]],
    limit: int | None = None,
) -> tuple[CacheRows, dict[str, ColumnType]]:
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
        schemas: Declared column types of each table in *named_rows*.
        limit: Bounds the fetch itself via ``fetchmany()`` — a join across two
            upstream tables each near the row ceiling can otherwise blow up
            memory during composition, before any post-fetch check runs. None
            fetches everything.

    Returns:
        The row dicts and the declared type of each result column.

    Raises:
        CompositionError: Wraps any DuckDB execution error with context (including
            a reference to a table that is not one of the registered rowsets).
    """
    import duckdb

    conn = duckdb.connect(":memory:", config={"enable_external_access": False})
    try:
        conn.execute("SET TimeZone='UTC'")
        for name, rows in named_rows.items():
            _register(conn, name, rows, schemas[name])
        result = conn.execute(sql, params)
        return rows_from_result(result, limit=limit), duckdb_schema(result.description)
    except Exception as e:  # noqa: BLE001 — duckdb raises various exception types at the composition boundary
        raise CompositionError(f"Isolated-engine query failed: {e}\nSQL:\n{sql}") from e
    finally:
        conn.close()
