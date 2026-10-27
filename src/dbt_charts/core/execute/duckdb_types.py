"""DuckDB result types → declared column types, and cells that match them.

Stage: EXECUTE

A map is ``json`` (an object, see ``map_to_json``), and a NULL-typed column is
``string``. An interval, a union, a struct with unnamed fields and a map whose
key has no JSON text form have no declared type.
"""

from __future__ import annotations

import json
from collections.abc import Callable, Sequence
from datetime import UTC
from typing import TYPE_CHECKING, Any

from dbt_charts.core.column_types import (
    ColumnKind,
    ColumnType,
    column_type_error,
    map_to_json,
    reject_duplicate_columns,
)

if TYPE_CHECKING:
    from duckdb.sqltypes import DuckDBPyType

_SCALARS: dict[str, ColumnKind] = {
    **dict.fromkeys(
        (
            "bignum",
            "varint",
            "tinyint",
            "smallint",
            "integer",
            "bigint",
            "hugeint",
            "utinyint",
            "usmallint",
            "uinteger",
            "ubigint",
            "uhugeint",
        ),
        "integer",
    ),
    "float": "float",
    "double": "float",
    "decimal": "decimal",
    "boolean": "boolean",
    **dict.fromkeys(("varchar", "uuid", "enum", "bit", "null"), "string"),
    "map": "json",
    "blob": "bytes",
    "date": "date",
    **dict.fromkeys(("time", "time with time zone", "time_ns"), "time"),
    **dict.fromkeys(
        (
            "timestamp",
            "timestamp_s",
            "timestamp_ms",
            "timestamp_ns",
        ),
        "datetime",
    ),
    "timestamp with time zone": "datetime_tz",
}


_UNKEYED = frozenset({"struct", "list", "array", "map", "blob", "bit", "union"})


def _fields(dtype: DuckDBPyType) -> list[tuple[str, DuckDBPyType]]:
    """A nested type's leading children (a list's element, a struct's fields);
    a fixed-size array's trailing size child is not a type. The stubs type
    children as ``object``."""
    count = 1 if dtype.id in ("list", "array") else len(dtype.children)
    fields = []
    for field, child in dtype.children[:count]:
        assert isinstance(child, type(dtype))
        fields.append((field, child))
    return fields


def duckdb_column_type(name: str, dtype: DuckDBPyType) -> ColumnType:
    """The declared type of DuckDB column *name*.

    Raises:
        ExecutionError: ERR-RESULT-COLUMN-TYPE for a type outside the
            vocabulary (interval, union, an unnamed struct field, ...).
    """
    if str(dtype) == "JSON":
        return ColumnType(kind="json")
    if dtype.id in ("list", "array"):
        return ColumnType(
            kind="list", element=duckdb_column_type(name, _fields(dtype)[0][1])
        )
    if dtype.id == "struct":
        children = _fields(dtype)
        if any(not field for field, _ in children):
            raise column_type_error(
                name, f"the DuckDB type {dtype} has unnamed fields; name them"
            )
        return ColumnType(
            kind="struct",
            fields=tuple(
                (field, duckdb_column_type(name, child)) for field, child in children
            ),
        )
    if dtype.id == "map" and _fields(dtype)[0][1].id in _UNKEYED:
        raise column_type_error(
            name, f"the DuckDB map key type {_fields(dtype)[0][1]} has no JSON form"
        )
    if dtype.id not in _SCALARS:
        raise column_type_error(
            name,
            f"the DuckDB type {dtype} has no declared mapping; "
            "cast it in the query (e.g. to VARCHAR)",
        )
    return ColumnType(kind=_SCALARS[dtype.id])


def duckdb_schema(
    description: list[tuple[str, DuckDBPyType, None, None, None, None, None]],
) -> dict[str, ColumnType]:
    """Declared types for a DuckDB result's ``cursor.description``."""
    reject_duplicate_columns([name for name, *_ in description])
    return {name: duckdb_column_type(name, dtype) for name, dtype, *_ in description}


_Cell = Any  # type-state: explicit_any — a warehouse cell
_Convert = Callable[[_Cell], _Cell]


def _converter(column: str, dtype: DuckDBPyType) -> _Convert | None:
    """How to turn DuckDB's cell of *dtype* into the Python value its declared
    type names, or None when the cell already is that value. Built from the
    DuckDB type because a map's declared ``json`` keeps no value type."""
    if str(dtype) == "JSON":  # JSON arrives as text
        return json.loads
    if dtype.id in ("bignum", "varint"):  # arrives as text
        return int
    if dtype.id == "timestamp with time zone":  # arrives in the session TimeZone
        return lambda cell: cell.astimezone(UTC)
    if dtype.id == "uuid":
        return str
    if dtype.id in ("list", "array"):
        element = _converter(column, _fields(dtype)[0][1])
        return lambda cell: [
            item if element is None or item is None else element(item) for item in cell
        ]
    if dtype.id == "struct":
        converters = [(name, _converter(column, kind)) for name, kind in _fields(dtype)]
        return lambda cell: {
            name: cell[name] if fn is None or cell[name] is None else fn(cell[name])
            for name, fn in converters
        }
    if dtype.id == "map":
        value = _converter(column, _fields(dtype)[1][1])
        return lambda cell: map_to_json(
            column,
            (
                (key, item if value is None or item is None else value(item))
                for key, item in cell.items()
            ),
        )
    return None


def typed_rows(
    description: list[tuple[str, DuckDBPyType, None, None, None, None, None]],
    rows: Sequence[Sequence[Any]],  # type-state: explicit_any — warehouse cells
) -> list[dict[str, Any]]:  # type-state: explicit_any — warehouse cells
    """DuckDB result *rows* as dicts keyed by the columns of *description*, each
    cell converted to the Python value its declared type names: JSON decoded at
    any depth, a map as a JSON object whose values are converted too, a
    fixed-size array as a list, a BIGNUM as an int, a UUID as a str, a TIMESTAMPTZ
    as an aware UTC datetime (a TIMESTAMP stays naive)."""
    converters = [_converter(name, dtype) for name, dtype, *_ in description]
    names = [name for name, *_ in description]
    reject_duplicate_columns(names)
    return [
        {
            name: cell if fn is None or cell is None else fn(cell)
            for name, fn, cell in zip(names, converters, row, strict=True)
        }
        for row in rows
    ]
