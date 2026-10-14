"""Python values to Arrow, through nanoarrow.

Stage: EXECUTE

The one home for building Arrow from Python rows: the result cache and cache
composition hand row dicts to DuckDB through it, and the Trino driver builds its
batches with it. nanoarrow builds int, float, bool, string and binary arrays
from Python lists itself; every other type here is built as its storage type
(int32 days, int64 microseconds, 16-byte little-endian unscaled decimals) and
relabeled, since the memory layout is identical.
"""

from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from datetime import UTC, date, datetime, time, timedelta
from decimal import Decimal
from typing import TYPE_CHECKING, Any, TypeAlias
from uuid import UUID

import nanoarrow as na

if TYPE_CHECKING:
    import duckdb
    from nanoarrow._array import CArray
    from nanoarrow._schema import CSchema

_EPOCH = datetime(1970, 1, 1)
_EPOCH_UTC = datetime(1970, 1, 1, tzinfo=UTC)
_EPOCH_DATE = date(1970, 1, 1)
_US = timedelta(microseconds=1)
_MAX_DECIMAL_PRECISION = 38
_Capsule: TypeAlias = (
    object  # type-state: object_annotation — PyCapsule has no Python type
)


class RelationStream:
    """A DuckDB relation offered to another connection as one Arrow stream.

    Exposing only ``__arrow_c_stream__`` is the point: handed the relation
    itself, DuckDB would try to run it on the receiving connection. The stream
    is single-use, so the receiver copies it into a table rather than querying
    a registration twice.
    """

    def __init__(self, relation: duckdb.DuckDBPyRelation) -> None:
        self._relation = relation

    def __arrow_c_stream__(self, requested_schema: _Capsule | None = None) -> _Capsule:
        return self._relation.__arrow_c_stream__(requested_schema)


class _Relabeled:
    """An array whose buffers are read under a different, same-layout schema."""

    def __init__(self, schema: CSchema, array: CArray) -> None:
        self._schema = schema
        self._array = array

    def __arrow_c_array__(
        self, requested_schema: _Capsule | None = None
    ) -> tuple[_Capsule, _Capsule]:
        del requested_schema
        _, array = self._array.__arrow_c_array__()
        return self._schema.__arrow_c_schema__(), array


def relabel(array: CArray, schema: CSchema) -> CArray:
    """Read *array*'s buffers as *schema*; the two must share a memory layout."""
    return na.c_array(_Relabeled(schema, array))


def _kind(value: Any) -> str:  # type-state: explicit_any — one Python cell value
    if isinstance(value, bool):
        return "bool"
    if isinstance(value, int):
        return "int"
    if isinstance(value, float):
        return "float"
    if isinstance(value, str):
        return "str"
    if isinstance(value, bytes):
        return "bytes"
    if isinstance(value, Decimal):
        return "decimal"
    if isinstance(value, datetime):
        return "datetime" if value.tzinfo is None else "datetime_tz"
    if isinstance(value, date):
        return "date"
    if isinstance(value, time):
        return "time"
    if isinstance(value, timedelta):
        return "timedelta"
    if isinstance(value, (list, dict)):
        return "json"
    if isinstance(value, UUID):
        return "uuid"
    raise TypeError(f"{type(value).__name__} has no Arrow type here")


def _decimal_type(values: list[Decimal]) -> na.Schema:
    scale = 0
    integer_digits = 1
    for value in values:
        if not value.is_finite():
            raise ValueError(f"{value} has no Arrow decimal representation")
        _, digits, exponent = value.as_tuple()
        assert isinstance(exponent, int)
        scale = max(scale, -exponent)
        integer_digits = max(integer_digits, len(digits) + exponent)
    precision = integer_digits + scale
    if precision > _MAX_DECIMAL_PRECISION:
        raise ValueError(
            f"Decimal needs precision {precision}, more than the "
            f"{_MAX_DECIMAL_PRECISION} the query engine's DECIMAL holds"
        )
    return na.decimal128(precision, scale)


def infer_type(
    values: Sequence[Any],  # type-state: explicit_any — Python cell values
) -> na.Schema:
    """The Arrow type of a column of Python values.

    ``int`` and ``float`` together are ``float64``; any other mix of kinds
    raises ``TypeError``. list and dict values are carried as JSON text.

    Raises:
        TypeError: The column mixes kinds, or holds a type with no Arrow form.
        ValueError: A Decimal column needs precision above 38.
    """
    present = [value for value in values if value is not None]
    kinds = {_kind(value) for value in present}
    if kinds == {"int", "float"}:
        kinds = {"float"}
    if len(kinds) > 1:
        raise TypeError(f"column mixes {', '.join(sorted(kinds))}")
    kind = kinds.pop() if kinds else "null"
    if kind == "decimal":
        return _decimal_type(present)
    return {
        "null": na.null(),
        "bool": na.bool_(),
        "int": na.int64(),
        "float": na.float64(),
        "str": na.string(),
        "json": na.string(),
        "uuid": na.string(),
        "bytes": na.binary(),
        "date": na.date32(),
        "datetime": na.timestamp("us"),
        "datetime_tz": na.timestamp("us", "UTC"),
        "time": na.time64("us"),
        "timedelta": na.duration("us"),
    }[kind]


def _micros(value: datetime) -> int:
    if value.tzinfo is None:
        return (value - _EPOCH) // _US
    return (value - _EPOCH_UTC) // _US


def _time_micros(value: time) -> int:
    return (
        (value.hour * 60 + value.minute) * 60 + value.second
    ) * 1_000_000 + value.microsecond


def _unscaled(value: Decimal, scale: int, precision: int) -> bytes:
    """*value* as DECIMAL(*precision*, *scale*) storage, in integer arithmetic:
    Decimal arithmetic would round past its context's 28 digits."""
    sign, digits, exponent = value.as_tuple()
    if not isinstance(exponent, int):
        raise ValueError(f"{value} has no Arrow decimal representation")
    unscaled = int("".join(map(str, digits)))
    shift = exponent + scale
    if shift >= 0:
        unscaled *= 10**shift
    else:
        unscaled, dropped = divmod(unscaled, 10**-shift)
        if dropped:
            raise ValueError(f"{value} has more than {scale} decimal places")
    if len(str(unscaled)) > precision:
        raise ValueError(f"{value} does not fit DECIMAL({precision}, {scale})")
    return (-unscaled if sign else unscaled).to_bytes(16, "little", signed=True)


def encode(
    values: Sequence[Any],  # type-state: explicit_any — Python cell values
    schema: na.Schema,
) -> CArray:
    """Build one Arrow array of *schema* from Python values (None is null).

    Raises:
        ValueError: A value does not fit *schema*.
    """
    type_ = schema.type
    if type_ == na.Type.NULL:
        return na.c_array_from_buffers(schema, len(values), [])
    if type_ == na.Type.DATE32:
        return relabel(
            na.c_array(
                [None if v is None else (v - _EPOCH_DATE).days for v in values],
                na.int32(),
            ),
            na.c_schema(schema),
        )
    if type_ == na.Type.TIMESTAMP:
        return relabel(
            na.c_array([None if v is None else _micros(v) for v in values], na.int64()),
            na.c_schema(schema),
        )
    if type_ == na.Type.TIME64:
        return relabel(
            na.c_array(
                [None if v is None else _time_micros(v) for v in values], na.int64()
            ),
            na.c_schema(schema),
        )
    if type_ == na.Type.DURATION:
        return relabel(
            na.c_array([None if v is None else v // _US for v in values], na.int64()),
            na.c_schema(schema),
        )
    if type_ == na.Type.DECIMAL128:
        precision, scale = schema.precision, schema.scale
        return relabel(
            na.c_array(
                [None if v is None else _unscaled(v, scale, precision) for v in values],
                na.fixed_size_binary(16),
            ),
            na.c_schema(schema),
        )
    if type_ == na.Type.STRING:
        values = [
            json.dumps(v)
            if isinstance(v, (list, dict))
            else str(v)
            if isinstance(v, UUID)
            else v
            for v in values
        ]
    elif type_ == na.Type.DOUBLE:
        values = [None if v is None else float(v) for v in values]
    return na.c_array(values, schema)


def table(columns: Sequence[tuple[str, CArray]], length: int) -> CArray:
    """Gather same-length arrays into one struct array, the Arrow form of a table.

    A list of pairs rather than a dict: a warehouse result may repeat a name.
    """
    schema = na.struct([na.Schema(array.schema, name=name) for name, array in columns])
    return na.c_array_from_buffers(
        schema, length, [None], children=[array for _, array in columns]
    )


def rows_to_arrow(
    rows: Sequence[Mapping[str, Any]],  # type-state: explicit_any — row dict values
) -> na.Array:
    """Build an Arrow table from row dicts; columns are the first row's keys.

    A column's type is inferred from all its non-null values (see
    :func:`infer_type`).

    Raises:
        TypeError: A column mixes kinds.
        ValueError: A Decimal column needs precision above 38.
    """
    names = list(rows[0]) if rows else []
    columns = [
        (name, _encode_inferred([row.get(name) for row in rows])) for name in names
    ]
    return na.Array(table(columns, len(rows)))


def insert_rows(
    conn: duckdb.DuckDBPyConnection,
    target: str,
    rows: Sequence[Mapping[str, Any]],  # type-state: explicit_any — row dict values
) -> None:
    """INSERT row dicts into the existing table *target* (already quoted), in
    its column order, each cell converted as a bound parameter would be.

    A column holding several Python kinds is staged as one typed column per
    kind, each CAST to the column's type: the conversion DuckDB gives a
    parameter of that kind.
    """
    target_types = [row[1] for row in conn.execute(f"DESCRIBE {target}").fetchall()]
    staged: list[tuple[str, CArray]] = []
    selects: list[str] = []
    for name, target_type in zip(list(rows[0]), target_types, strict=True):
        casts: list[str] = []
        for part in _split_by_kind([row.get(name) for row in rows]):
            column = f"_c{len(staged)}"
            staged.append((column, _encode_inferred(part)))
            casts.append(f"CAST({column} AS {target_type})")
        selects.append(casts[0] if len(casts) == 1 else f"coalesce({', '.join(casts)})")
    view = "_dct_arrow_insert"
    conn.register(view, na.Array(table(staged, len(rows))))
    try:
        conn.execute(f"INSERT INTO {target} SELECT {', '.join(selects)} FROM {view}")
    finally:
        conn.unregister(view)


def _encode_inferred(
    values: list[Any],  # type-state: explicit_any — Python cell values
) -> CArray:
    return encode(values, infer_type(values))


def _split_by_kind(
    values: list[Any],  # type-state: explicit_any — Python cell values
) -> list[list[Any]]:  # type-state: explicit_any — Python cell values
    """*values* as one list per Python kind, each null where another kind sits.

    A column with one kind (int and float count as one) stays whole.
    """
    kinds = {_kind(value) for value in values if value is not None}
    if len(kinds) <= 1 or kinds == {"int", "float"}:
        return [values]
    return [
        [None if v is None or _kind(v) != kind else v for v in values]
        for kind in sorted(kinds)
    ]


def ingest(
    conn: duckdb.DuckDBPyConnection,
    target: str,
    data: object,  # type-state: object_annotation — any Arrow stream exporter
) -> None:
    """Copy one Arrow stream into a new table *target* (already quoted).

    The registration is dropped before returning, since a stream cannot be
    read twice.
    """
    view = "_dct_arrow_ingest"
    conn.register(view, data)
    try:
        conn.execute(f"CREATE OR REPLACE TABLE {target} AS SELECT * FROM {view}")
    finally:
        conn.unregister(view)
