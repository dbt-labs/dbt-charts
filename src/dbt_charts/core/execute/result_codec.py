"""One JSON codec for a result's cells, driven by the declared schema.

Stage: EXECUTE (Cache)

The local cache, the Cloud cache and board recordings all store a result as
``{"schema": [[column, type], ...], "rows": [...]}``. JSON has no decimal, date,
time or bytes, so each cell is written as text and restored by its column's
declared type, recursing into ``list`` and ``struct``. Nothing is read off a
cell's shape; a cell that disagrees with its declaration raises on write.
"""

from __future__ import annotations

import base64
from collections.abc import Callable
from datetime import UTC, date, datetime, time, timedelta
from decimal import Decimal
from typing import Any

from dbt_charts.core.column_types import ColumnKind, ColumnType, column_type_error
from dbt_charts.core.diagnostics.execution import ExecutionError
from dbt_charts.core.execute.cache_backend import CacheRows, ResultRows

_Cell = Any  # type-state: explicit_any — a result cell, any depth
_Json = Any  # type-state: explicit_any — a JSON-safe value

# kind -> (accepts the cell, cell to JSON, JSON to cell); None converts nothing.
_Scalar = tuple[
    Callable[[_Cell], bool],
    Callable[[_Cell], _Json] | None,
    Callable[[_Json], _Cell] | None,
]


def _is(python_type: type, *, not_: tuple[type, ...] = ()) -> Callable[[_Cell], bool]:
    return lambda cell: isinstance(cell, python_type) and not isinstance(cell, not_)


_SCALARS: dict[ColumnKind, _Scalar] = {
    "string": (_is(str), None, None),
    "integer": (_is(int, not_=(bool,)), None, None),
    "float": (_is(float), None, None),
    "boolean": (_is(bool), None, None),
    "decimal": (_is(Decimal), str, Decimal),
    "date": (_is(date, not_=(datetime,)), date.isoformat, date.fromisoformat),
    # Naive wall-clock time; the declared cell is never stamped with a zone.
    "datetime": (
        lambda cell: isinstance(cell, datetime) and cell.utcoffset() is None,
        datetime.isoformat,
        datetime.fromisoformat,
    ),
    # An instant, normalized to UTC: the restored tzinfo is exactly UTC.
    "datetime_tz": (
        lambda cell: isinstance(cell, datetime) and cell.utcoffset() == timedelta(0),
        datetime.isoformat,
        lambda text: datetime.fromisoformat(text).astimezone(UTC),
    ),
    "time": (_is(time), time.isoformat, time.fromisoformat),
    "bytes": (
        _is(bytes),
        lambda cell: base64.b64encode(cell).decode("ascii"),
        base64.b64decode,
    ),
}

_JSON_LEAVES = (str, int, float, bool)


def encode_cell(cell: _Cell, declared: ColumnType, column: str) -> _Json:
    """*cell* as JSON-safe data, raising ERR-RESULT-COLUMN-TYPE when it is not
    what *declared* says."""
    if cell is None:
        return None
    if declared.element is not None:
        if not isinstance(cell, list):
            raise _mismatch(column, declared, cell)
        return [encode_cell(item, declared.element, column) for item in cell]
    if declared.fields is not None:
        if not isinstance(cell, dict):
            raise _mismatch(column, declared, cell)
        types = dict(declared.fields)
        for key in cell:
            if key not in types:
                raise column_type_error(
                    column, f"holds the field {key!r} its struct does not declare"
                )
        return {
            key: encode_cell(item, types[key], column) for key, item in cell.items()
        }
    if declared.kind == "json":
        _require_json(cell, column)
        return cell
    accepts, to_json, _ = _SCALARS[declared.kind]
    if not accepts(cell):
        raise _mismatch(column, declared, cell)
    return cell if to_json is None else to_json(cell)


def decode_cell(cell: _Json, declared: ColumnType) -> _Cell:
    """The cell :func:`encode_cell` wrote, restored by its declared type."""
    if cell is None:
        return None
    if declared.element is not None:
        return [decode_cell(item, declared.element) for item in cell]
    if declared.fields is not None:
        types = dict(declared.fields)
        return {key: decode_cell(item, types[key]) for key, item in cell.items()}
    from_json = None if declared.kind == "json" else _SCALARS[declared.kind][2]
    return cell if from_json is None else from_json(cell)


def encode_result(result: ResultRows) -> dict[str, _Json]:
    """*result* as the JSON-safe document every store writes.

    Raises:
        ExecutionError: ERR-RESULT-COLUMN-TYPE for a cell that does not match its
            column's declared type, or a row holding a column the schema lacks.
    """
    schema = result.schema
    for row in result.rows:
        for name in row:
            if name not in schema:
                raise column_type_error(name, "is in a row but not in the schema")
    return {
        "schema": [
            [name, declared.model_dump(mode="json", exclude_none=True)]
            for name, declared in schema.items()
        ],
        "rows": [
            {name: encode_cell(cell, schema[name], name) for name, cell in row.items()}
            for row in result.rows
        ],
    }


def decode_result(document: dict[str, _Json]) -> ResultRows:
    """The :class:`ResultRows` that :func:`encode_result` wrote."""
    schema = {
        name: ColumnType.model_validate(declared)
        for name, declared in document["schema"]
    }
    rows: CacheRows = [
        {name: decode_cell(cell, schema[name]) for name, cell in row.items()}
        for row in document["rows"]
    ]
    return ResultRows(rows=rows, schema=schema)


def _mismatch(column: str, declared: ColumnType, cell: _Cell) -> ExecutionError:
    return column_type_error(
        column, f"declared {declared} but holds {type(cell).__name__} {cell!r}"
    )


def _require_json(cell: _Cell, column: str) -> None:
    """A ``json`` cell is decoded JSON: leaves, lists and string-keyed objects."""
    if cell is None or isinstance(cell, _JSON_LEAVES):
        return
    if isinstance(cell, list):
        for item in cell:
            _require_json(item, column)
    elif isinstance(cell, dict) and all(isinstance(key, str) for key in cell):
        for item in cell.values():
            _require_json(item, column)
    else:
        raise _mismatch(column, ColumnType(kind="json"), cell)
