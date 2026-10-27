"""Declared column types and the literal-typing rule.

Leaf module: compile validates inline literals with it and execute declares
every result's columns with it.
"""

from __future__ import annotations

from collections import Counter
from collections.abc import Iterable, Mapping, Sequence
from datetime import UTC, date, datetime, time
from decimal import Decimal
from typing import Any, Literal, Self
from uuid import UUID

from pydantic import BaseModel, model_validator

from dbt_charts.core.diagnostics.codes_execute import (
    ERR_RESULT_COLUMN_TYPE,
    ERR_RESULT_DUPLICATE_COLUMN,
)
from dbt_charts.core.diagnostics.execution import ExecutionError

ColumnKind = Literal[
    "string",
    "integer",
    "float",
    "decimal",
    "boolean",
    "date",
    "datetime",
    "datetime_tz",
    "time",
    "bytes",
    "json",
    "list",
    "struct",
]


class ColumnType(BaseModel, frozen=True):
    """A result column's declared type: the source's own, never read off a cell.

    ``datetime`` holds naive wall-clock cells; ``datetime_tz`` holds instants, as
    aware ``datetime`` cells in UTC.

    ``list`` carries its ``element`` type and ``struct`` its ordered ``fields``;
    every other kind carries neither.
    """

    kind: ColumnKind
    element: ColumnType | None = None
    fields: tuple[tuple[str, ColumnType], ...] | None = None

    @model_validator(mode="after")
    def _only_nested_kinds_have_children(self) -> Self:
        if (self.kind == "list") != (self.element is not None):
            raise ValueError("exactly the list kind carries an element type")
        if (self.kind == "struct") != (self.fields is not None):
            raise ValueError("exactly the struct kind carries fields")
        return self

    def __str__(self) -> str:
        if self.element is not None:
            return f"list<{self.element}>"
        if self.fields is not None:
            return "struct<{" + ", ".join(f"{n}: {t}" for n, t in self.fields) + "}>"
        return self.kind


def column_type_error(column: str, reason: str) -> ExecutionError:
    return ExecutionError.from_code(
        ERR_RESULT_COLUMN_TYPE, column=column, reason=reason
    )


# bool before int and datetime before date: each is a subclass of the next. A
# datetime is ``datetime_tz`` instead when aware (see _literal_kind).
_LITERAL_KINDS: tuple[tuple[type, ColumnKind], ...] = (
    (bool, "boolean"),
    (int, "integer"),
    (float, "float"),
    (Decimal, "decimal"),
    (str, "string"),
    (datetime, "datetime"),
    (date, "date"),
    (time, "time"),
    (bytes, "bytes"),
    (list, "list"),
    (dict, "struct"),
)


def literal_schema(
    rows: Sequence[Mapping[str, Any]],  # type-state: explicit_any — inline cell values
    columns: Sequence[str] | None = None,
    *,
    untyped: ColumnType | None = None,
) -> dict[str, ColumnType]:
    """Column types of inline rows, whose literal is its own declaration.

    The one place types are read off values, for the sources with no schema
    to declare them: a values query's YAML, an HTTP response's JSON, SQLite's
    untyped expressions. Columns default to the keys of all rows, in first-seen
    order. A column that
    mixes kinds is rejected; integers and floats together are numbers and read
    as float. A column with no non-null value has nothing to read a kind from:
    it is rejected unless the source passes the *untyped* type to declare for it.
    """
    names = (
        columns
        if columns is not None
        else list(dict.fromkeys(key for row in rows for key in row))
    )
    return {
        name: _literal_type(
            name, [row[name] for row in rows if row.get(name) is not None], untyped
        )
        for name in names
    }


def _literal_kind(value: Any) -> ColumnKind | None:  # type-state: explicit_any — a cell
    for python_type, kind in _LITERAL_KINDS:
        if isinstance(value, python_type):
            if kind == "datetime" and value.utcoffset() is not None:
                return "datetime_tz"
            return kind
    return None


def _literal_type(
    path: str,
    values: list[Any],  # type-state: explicit_any — inline cell values
    untyped: ColumnType | None,
) -> ColumnType:
    column = path.split(".", 1)[0].removesuffix("[]")
    where = "" if path == column else f"{path} "
    if not values:
        if untyped is None:
            raise column_type_error(column, f"{where}holds no non-null value to type")
        return untyped
    kinds: set[ColumnKind] = set()
    for value in values:
        value_kind = _literal_kind(value)
        if value_kind is None:
            raise column_type_error(
                column, f"{where}holds an unsupported {type(value).__name__} value"
            )
        kinds.add(value_kind)
    if kinds == {"integer", "float"}:
        kinds = {"float"}
    if len(kinds) > 1:
        raise column_type_error(
            column, f"{where}mixes {', '.join(sorted(kinds))}; use one kind per column"
        )
    kind = kinds.pop()
    if kind == "list":
        items = [item for value in values for item in value if item is not None]
        return ColumnType(
            kind="list", element=_literal_type(f"{path}[]", items, untyped)
        )
    if kind == "struct":
        names = dict.fromkeys(name for value in values for name in value)
        for name in names:
            if not isinstance(name, str):
                raise column_type_error(
                    column,
                    f"{where}has the key {name!r} of type {type(name).__name__}; "
                    "keys must be text",
                )
        return ColumnType(
            kind="struct",
            fields=tuple(
                (
                    name,
                    _literal_type(
                        f"{path}.{name}",
                        [v[name] for v in values if v.get(name) is not None],
                        untyped,
                    ),
                )
                for name in names
            ),
        )
    return ColumnType(kind=kind)


def reject_duplicate_columns(columns: Sequence[str]) -> None:
    """Raise ERR-RESULT-DUPLICATE-COLUMN if *columns* repeats a name.

    Call this on the final column names, before rows are keyed by them.
    """
    duplicated = sorted(name for name, n in Counter(columns).items() if n > 1)
    if duplicated:
        raise ExecutionError.from_code(
            ERR_RESULT_DUPLICATE_COLUMN,
            columns=", ".join(repr(c) for c in duplicated),
        )


def declared_literal_cells(
    rows: Sequence[Mapping[str, Any]],  # type-state: explicit_any — inline cell values
    schema: Mapping[str, ColumnType],
) -> list[dict[str, Any]]:  # type-state: explicit_any — inline cell values
    """*rows* with every cell made what its column's declaration says, at any
    depth: an integer in a ``float`` column a float (a literal column that mixes
    the two is declared float), an aware datetime in a ``datetime_tz`` column UTC."""
    return [
        {
            key: as_declared(cell, schema[key]) if key in schema else cell
            for key, cell in row.items()
        }
        for row in rows
    ]


def as_declared(
    cell: Any,  # type-state: explicit_any — one cell value
    declared: ColumnType,
) -> Any:  # type-state: explicit_any — one cell value
    """*cell* as its column's declaration says, at any depth (see
    :func:`declared_literal_cells`)."""
    if cell is None:
        return None
    if declared.kind == "float":
        return float(cell) if type(cell) is int else cell
    if declared.kind == "datetime_tz":
        return cell.astimezone(UTC)
    if declared.element is not None:
        return [as_declared(item, declared.element) for item in cell]
    if declared.fields is not None:
        return {
            name: as_declared(cell[name], kind) if name in cell else None
            for name, kind in declared.fields
            if name in cell
        }
    return cell


_MapKey = Any  # type-state: explicit_any — a map key
_MapValue = Any  # type-state: explicit_any — a map value
_Entry = tuple[_MapKey, _MapValue]


def map_to_json(
    column: str,
    entries: Iterable[_Entry],
) -> dict[str, Any]:  # type-state: explicit_any — JSON values
    """A MAP's *entries* as the JSON object a ``json`` column holds.

    Keys become strings (a number, date, time, UUID or boolean by its text; a
    struct, list or bytes key has none and raises), and two keys with the same
    text raise rather than one winning. Values become JSON values: a decimal is
    a float, a date, datetime or time its ISO text, a UUID its text, and a
    nested map or struct an object.

    Raises:
        ExecutionError: ERR-RESULT-COLUMN-TYPE for a key or value with no JSON
            form, or a repeated key.
    """
    out: dict[str, Any] = {}  # type-state: explicit_any — JSON values
    for key, value in entries:
        text = _json_key(column, key)
        if text in out:
            raise column_type_error(column, f"a map holds the key {text!r} twice")
        out[text] = _json_value(column, value)
    return out


def _json_key(column: str, key: Any) -> str:  # type-state: explicit_any — a map key
    if isinstance(key, bool):
        return "true" if key else "false"
    if isinstance(key, str):
        return key
    if isinstance(key, int | float | Decimal | UUID):
        return str(key)
    if isinstance(key, date | time):
        return key.isoformat()
    raise column_type_error(
        column, f"a map key of type {type(key).__name__} has no JSON form"
    )


def _json_value(
    column: str,
    value: Any,  # type-state: explicit_any — a map value
) -> Any:  # type-state: explicit_any — a JSON value
    if value is None or isinstance(value, bool | int | float | str):
        return value
    if isinstance(value, Decimal):
        return float(value)
    if isinstance(value, date | time):
        return value.isoformat()
    if isinstance(value, UUID):
        return str(value)
    if isinstance(value, list | tuple):
        return [_json_value(column, item) for item in value]
    if isinstance(value, dict):
        return map_to_json(column, value.items())
    raise column_type_error(
        column, f"a map value of type {type(value).__name__} has no JSON form"
    )


def check_datetime_cells(
    schema: Mapping[str, ColumnType],
    rows: Sequence[Mapping[str, Any]],  # type-state: explicit_any — result cell values
) -> None:
    """Raise ERR-RESULT-COLUMN-TYPE for a naive cell in a ``datetime_tz`` column
    or an aware one in a ``datetime`` column, at any depth. Nothing is coerced: a
    producer that mislabels instants as wall-clock times, or the reverse, would
    print the wrong hour once a board zone applies."""
    for row in rows:
        for column, declared in schema.items():
            _check_datetime_cell(column, row.get(column), declared)


def _check_datetime_cell(
    column: str,
    cell: Any,  # type-state: explicit_any — one result cell value
    declared: ColumnType,
) -> None:
    if cell is None:
        return
    if declared.kind in ("datetime", "datetime_tz") and isinstance(cell, datetime):
        aware = cell.utcoffset() is not None
        if aware != (declared.kind == "datetime_tz"):
            raise column_type_error(
                column,
                f"declared {declared.kind} but holds a "
                f"{'tz-aware' if aware else 'naive'} datetime",
            )
    elif declared.element is not None:
        for item in cell:
            _check_datetime_cell(column, item, declared.element)
    elif declared.fields is not None:
        for name, field_type in declared.fields:
            if name in cell:
                _check_datetime_cell(column, cell[name], field_type)
