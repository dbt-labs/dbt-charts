"""Warehouse results with their declared column types, read past dbt's agate table.

Stage: EXECUTE

``adapter.execute(fetch=True)`` returns an agate table: types guessed from the
cells, nested cells JSON-encoded into text. This reads the driver's own result
instead. A cursor family maps each ``cursor.description`` type code to a
:class:`ColumnType`; BigQuery maps ``RowIterator.schema``. A cell is decoded
only by its column's declared type, never by what its text looks like.
"""

from __future__ import annotations

import json
import re
import time
import uuid
from collections.abc import Callable, Sequence
from datetime import UTC
from functools import cache
from typing import Any, NamedTuple

import nanoarrow as na

from dbt_charts.core.column_types import (
    ColumnKind,
    ColumnType,
    column_type_error,
    map_to_json,
)
from dbt_charts.core.diagnostics.codes_execute import ERR_RESULT_NO_TYPE_READER
from dbt_charts.core.diagnostics.execution import ExecutionError
from dbt_charts.core.execute.duckdb_types import duckdb_column_type, typed_rows
from dbt_charts.core.execute.result import arrow_columns


class TypedRows(NamedTuple):
    columns: list[str]
    rows: list[tuple[Cell, ...]]
    types: list[ColumnType]


Cell = Any  # type-state: explicit_any — warehouse cell values and type codes
Description = Sequence[Cell]
Driver = Any  # type-state: explicit_any — untyped dbt adapter and driver objects
Decoder = Callable[[Cell], Cell]
_Slot = tuple[ColumnType, Decoder | None]
_Resolve = Callable[[Description], _Slot]


def _utc(cell: Cell) -> Cell:
    """A zoned cell as its UTC instant. A naive cell passes through untouched:
    ``astimezone`` would read it as local time, and QueryResult rejects it."""
    return cell.astimezone(UTC) if cell.tzinfo else cell


def _slot(kind: ColumnKind) -> _Slot:
    """An instant column's cells are normalized by its declared type."""
    return ColumnType(kind=kind), _utc if kind == "datetime_tz" else None


def _no_reader(reason: str) -> ExecutionError:
    return ExecutionError.from_code(ERR_RESULT_NO_TYPE_READER, reason=reason)


class _Family(NamedTuple):
    resolve: _Resolve
    # dbt-snowflake comments inside add_query; every other adapter comments in
    # its execute(), which this bypasses.
    comment_here: bool
    # dbt-sqlserver steps past leading statements that return no rows and drains
    # the rest, so a later statement's error surfaces.
    skip_empty_sets: bool = False
    # Only where the adapter's own execute() closed the cursor: dbt-spark's
    # cursor() is the connection wrapper, and closing it closes the handle.
    close_cursor: bool = False
    # SQL type names the server declares for the query's columns, asked before
    # the query runs, for drivers whose type codes cannot tell them apart.
    describe: Callable[[Driver, str], list[str]] | None = None
    # Declares codes the type table lacks by asking the connection.
    declare: Callable[[Driver, Sequence[Description]], dict[Cell, _Slot]] | None = None
    # Converts cells by declared type where the driver's own Python values differ.
    convert: (
        Callable[[Driver, Sequence[Sequence[Cell]]], list[dict[str, Cell]]] | None
    ) = None
    # The adapter's own execute() retried; this keeps that behavior.
    retry: Callable[[Driver, Callable[[], TypedRows]], TypedRows] | None = None


def fetch_typed(
    adapter: Driver,
    dialect: str,
    sql: str,
    limit: int | None,
) -> TypedRows:
    """Run *sql* and return its rows with the declared type of every column.

    ``limit`` bounds the driver's own fetch; None reads everything (Spark
    cursors have no ``fetchmany``).

    Raises:
        ExecutionError: ERR-RESULT-COLUMN-TYPE for a type the warehouse reports
            that has no mapping, or a dialect with no type reader.
    """
    if dialect in _CLIENTS:
        return _CLIENTS[dialect](adapter.connections, sql, limit)
    if dialect not in _FAMILIES:
        raise _no_reader(f"the {dialect} dbt adapter has no reader for column types")
    family = _FAMILIES[dialect]
    connections = adapter.connections
    if family.retry is not None:
        return family.retry(connections, lambda: _read(family, connections, sql, limit))
    return _read(family, connections, sql, limit)


def _read(
    family: _Family, connections: Driver, sql: str, limit: int | None
) -> TypedRows:
    if family.comment_here:
        sql = connections._add_query_comment(sql)  # noqa: SLF001 — dbt's own hook
    described = family.describe(connections, sql) if family.describe else None
    _, cursor = connections.add_query(sql, auto_begin=False)
    try:
        while (
            family.skip_empty_sets and cursor.description is None and cursor.nextset()
        ):
            pass
        description = cursor.description
        if description is None:
            return TypedRows([], [], [])
        declared = family.declare(connections, description) if family.declare else {}
        slots = [
            declared[column[1]] if column[1] in declared else family.resolve(column)
            for column in description
        ]
        if described is not None:
            if len(described) != len(description):
                raise _no_reader(
                    f"the server described {len(described)} columns, "
                    f"the driver returned {len(description)}"
                )
            slots = [
                _slot("datetime_tz") if type_name.startswith("datetimeoffset") else slot
                for slot, type_name in zip(slots, described, strict=True)
            ]
        raw = cursor.fetchmany(limit) if limit else cursor.fetchall()
        while family.skip_empty_sets and cursor.nextset():
            pass
    finally:
        if family.close_cursor:
            cursor.close()
    decoders = [decode for _, decode in slots]
    rows = [
        tuple(
            value if decode is None or value is None else decode(value)
            for value, decode in zip(row, decoders, strict=True)
        )
        for row in raw
    ]
    columns = [column[0] for column in description]
    types = [kind for kind, _ in slots]
    if family.convert:
        converted = family.convert(description, rows)
        rows = [tuple(row.values()) for row in converted]
    return TypedRows(columns, rows, types)


def _by_code(family: str, table: Callable[[], dict[Cell, _Slot]]) -> _Resolve:
    def resolve(column: Description) -> _Slot:
        name, code = column[0], column[1]
        slots = table()
        if code not in slots:
            raise column_type_error(
                name, f"the {family} type code {code!r} has no declared mapping"
            )
        return slots[code]

    return resolve


def _slots(
    elements: dict[ColumnKind, tuple[Sequence[int], Sequence[int]]],
) -> dict[Cell, _Slot]:
    """Type code -> slot, from each kind's scalar codes and its array codes."""
    slots: dict[Cell, _Slot] = {}
    for kind, (scalars, arrays) in elements.items():
        element, decode = _slot(kind)
        slots.update(dict.fromkeys(scalars, (element, decode)))
        array = ColumnType(kind="list", element=element)
        slots.update(
            dict.fromkeys(arrays, (array, None if decode is None else _each(decode)))
        )
    return slots


@cache
def _postgres_slots() -> dict[Cell, _Slot]:
    from psycopg2 import extensions as pg  # noqa: PLC0415 — optional driver

    return _slots(
        {
            "boolean": (pg.BOOLEAN.values, pg.BOOLEANARRAY.values),
            "integer": (
                pg.INTEGER.values + pg.LONGINTEGER.values,
                pg.INTEGERARRAY.values + pg.LONGINTEGERARRAY.values,
            ),
            "float": (pg.FLOAT.values, pg.FLOATARRAY.values),
            "decimal": (pg.DECIMAL.values, pg.DECIMALARRAY.values),
            # 705 unknown and 2950 uuid have no typecaster: psycopg2 hands back text.
            "string": (pg.UNICODE.values + (705, 2950), pg.UNICODEARRAY.values),
            "date": (pg.PYDATE.values, pg.PYDATEARRAY.values),
            "datetime": (pg.PYDATETIME.values, pg.PYDATETIMEARRAY.values),
            "datetime_tz": (pg.PYDATETIMETZ.values, pg.PYDATETIMETZARRAY.values),
            "time": (pg.PYTIME.values, pg.PYTIMEARRAY.values),
            # psycopg2 parses json and jsonb itself, arrays included (JSONARRAY and
            # JSONBARRAY, which its stubs type as optional).
            "json": (pg.JSON.values + pg.JSONB.values, (199, 3807)),
        }
    )


def _postgres_enums(
    connections: Driver, description: Sequence[Description]
) -> dict[Cell, _Slot]:
    """Enum columns: psycopg2 hands their cells back as text, and the description
    carries only an OID, so pg_type says which of the unknown ones are enums."""
    unmapped = sorted({column[1] for column in description} - _postgres_slots().keys())
    if not unmapped:
        return {}
    cursor = connections.get_thread_connection().handle.cursor()
    try:
        cursor.execute(
            "SELECT oid FROM pg_type WHERE typtype = 'e' AND oid = ANY(%s)", (unmapped,)
        )
        return {oid: (ColumnType(kind="string"), None) for (oid,) in cursor.fetchall()}
    finally:
        cursor.close()


@cache
def _redshift_slots() -> dict[Cell, _Slot]:
    from redshift_connector.utils.oids import RedshiftOID as oid  # noqa: PLC0415

    slots = _slots(
        {
            "boolean": ((oid.BOOLEAN,), ()),
            "integer": (
                (oid.SMALLINT, oid.INTEGER, oid.BIGINT, oid.OID),
                (oid.SMALLINT_ARRAY, oid.INTEGER_ARRAY),
            ),
            "float": ((oid.REAL, oid.FLOAT), (oid.REAL_ARRAY,)),
            "decimal": ((oid.NUMERIC,), ()),
            "string": (
                (oid.TEXT, oid.VARCHAR, oid.BPCHAR, oid.CHAR, oid.NAME, oid.UNKNOWN),
                (oid.TEXT_ARRAY, oid.VARCHAR_ARRAY, oid.CHAR_ARRAY),
            ),
            "date": ((oid.DATE,), ()),
            "datetime": ((oid.TIMESTAMP,), ()),
            "datetime_tz": ((oid.TIMESTAMPTZ,), ()),
            "time": ((oid.TIME, oid.TIMETZ), ()),
            "json": ((oid.JSON,), ()),
        }
    )
    # SUPER is the one JSON column the driver leaves as text.
    slots[int(oid.SUPER)] = (ColumnType(kind="json"), json.loads)
    return slots


_REDSHIFT_RETRYABLE = ("could not open relation with OID", "conflict with concurrent")


def _redshift_retries(
    connections: Driver, attempt: Callable[[], TypedRows]
) -> TypedRows:
    """dbt-redshift's execute() retry policy, which its add_query does not carry."""
    from dbt_common.exceptions import DbtDatabaseError  # noqa: PLC0415

    credentials = connections.profile.credentials
    retries, backoff = credentials.retries, 1
    while True:
        try:
            return attempt()
        except Exception as error:  # noqa: BLE001 — re-raised unless dbt would retry
            message = str(error)
            retryable = (
                any(known in message for known in _REDSHIFT_RETRYABLE)
                or ("schema" in message and "does not exist" in message)
                or (isinstance(error, DbtDatabaseError) and credentials.retry_all)
            )
            if retries == 0 or not retryable:
                raise
            retries -= 1
            connection = connections.get_thread_connection()
            connections.close(connection)
            connections.open(connection)
            time.sleep(backoff)
            backoff = min(max(backoff * 2, 2), 60)


_SNOWFLAKE_KINDS: dict[str, ColumnKind] = {
    "REAL": "float",
    "TEXT": "string",
    "DATE": "date",
    "TIMESTAMP": "datetime",
    "TIMESTAMP_LTZ": "datetime_tz",
    "TIMESTAMP_TZ": "datetime_tz",
    "TIMESTAMP_NTZ": "datetime",
    "TIME": "time",
    "BOOLEAN": "boolean",
    "GEOGRAPHY": "string",
}
# The connector returns these as JSON text.
_SNOWFLAKE_JSON = frozenset({"VARIANT", "OBJECT", "ARRAY", "MAP"})


def _snowflake(column: Description) -> _Slot:
    from snowflake.connector.constants import FIELD_ID_TO_NAME  # noqa: PLC0415

    name, code, scale = column[0], column[1], column[5]
    type_name = FIELD_ID_TO_NAME.get(code)
    if type_name == "FIXED":
        return ColumnType(kind="integer" if scale == 0 else "decimal"), None
    if type_name in _SNOWFLAKE_KINDS:
        return _slot(_SNOWFLAKE_KINDS[type_name])
    if type_name in _SNOWFLAKE_JSON:
        return ColumnType(kind="json"), json.loads
    raise column_type_error(
        name, f"the snowflake type code {code!r} ({type_name}) has no declared mapping"
    )


# The Hive thrift type names pyhive reports upper-cased with a _TYPE suffix.
_HIVE_KINDS: dict[str, ColumnKind] = {
    **dict.fromkeys(("tinyint", "smallint", "int", "bigint"), "integer"),
    **dict.fromkeys(("float", "double"), "float"),
    **dict.fromkeys(("string", "varchar", "char"), "string"),
    "boolean": "boolean",
    "binary": "bytes",
    "decimal": "decimal",
    "date": "date",
    "timestamp_ntz": "datetime",
}
# Spark's thrift and ODBC cursors return TIMESTAMP as the session's wall clock.
_SPARK_KINDS: dict[str, ColumnKind] = {**_HIVE_KINDS, "timestamp": "datetime"}
_HIVE_NESTED = frozenset({"array", "map", "struct"})


def _split_top_level(text: str) -> list[str]:
    parts, depth, start = [], 0, 0
    for index, char in enumerate(text):
        depth += char in "<("
        depth -= char in ">)"
        if char == "," and depth == 0:
            parts.append(text[start:index])
            start = index + 1
    return [*parts, text[start:]]


def _each(decode: Decoder | None) -> Decoder:
    return lambda items: [
        item if decode is None or item is None else decode(item) for item in items
    ]


def _hive_named(name: str, text: str, kinds: dict[str, ColumnKind]) -> _Slot:
    """A type spelled as Spark's ``simpleString`` (the session method):
    ``array<int>``, ``struct<a:int,b:string>``, ``decimal(10,2)``."""
    text = text.strip()
    base = re.split(r"[<(]", text, maxsplit=1)[0].lower()
    inner = text[text.find("<") + 1 : -1]
    if base == "array" and "<" in text:
        element, decode = _hive_named(name, inner, kinds)
        return ColumnType(kind="list", element=element), (
            None if decode is None else _each(decode)
        )
    if base == "struct" and "<" in text:
        fields = [
            (
                field.partition(":")[0].strip("` "),
                _hive_named(name, field.partition(":")[2], kinds),
            )
            for field in _split_top_level(inner)
        ]
        decoders = [decode for _, (_, decode) in fields]
        names = [field for field, _ in fields]
        return (
            ColumnType(kind="struct", fields=tuple((n, slot[0]) for n, slot in fields)),
            lambda row: {
                n: v if d is None or v is None else d(v)
                for n, v, d in zip(names, row, decoders, strict=True)
            },
        )
    if base == "map" and "<" in text:
        return ColumnType(kind="json"), None
    if base in ("null", "void"):
        return ColumnType(kind="string"), None
    if base == "variant":
        return ColumnType(kind="json"), json.loads
    if base in kinds:
        return _slot(kinds[base])
    raise column_type_error(name, f"the hive type {text!r} has no declared mapping")


def _spark(column: Description) -> _Slot:
    """Thrift names a nested column ``array``, ``map`` or ``struct`` with no
    element type, so it is declared ``json``; a NULL column has no value to type
    and reads as ``string``."""
    name, code = column[0], column[1]
    if not isinstance(code, str):
        return _pyodbc(column)  # dbt-spark's ODBC wrapper
    key = code.lower().removesuffix("_type")
    if key in _HIVE_NESTED:
        return ColumnType(kind="json"), _hive_json(name)
    return _hive_named(name, code if "<" in code else key, _SPARK_KINDS)


# pyodbc reports the Python type class of the column's cells, which dbt-sqlserver
# and dbt-spark's ODBC wrapper pass through; dbt-sqlserver's own tests spell the
# same codes as these names.
_PYTHON_KINDS: dict[str, ColumnKind] = {
    "int": "integer",
    "float": "float",
    "str": "string",
    "bool": "boolean",
    **dict.fromkeys(("bytes", "bytearray"), "bytes"),
    "decimal.Decimal": "decimal",
    "datetime.date": "date",
    "datetime.datetime": "datetime",
    "datetime.time": "time",
}


def _pyodbc(column: Description) -> _Slot:
    name, code = column[0], column[1]
    key = (
        code
        if isinstance(code, str)
        else code.__qualname__
        if code.__module__ == "builtins"
        else f"{code.__module__}.{code.__qualname__}"
    )
    if key not in _PYTHON_KINDS:
        raise column_type_error(
            name, f"the ODBC type code {code!r} has no declared mapping"
        )
    return ColumnType(kind=_PYTHON_KINDS[key]), None


def _sqlserver_describe(connections: Driver, sql: str) -> list[str]:
    """pyodbc reports ``datetime2`` and ``datetimeoffset`` both as
    ``datetime.datetime``; the server's own description tells them apart."""
    cursor = connections.get_thread_connection().handle.cursor()
    try:
        cursor.execute("EXEC sp_describe_first_result_set @tsql = ?", sql)
        return [
            column.system_type_name
            for column in cursor.fetchall()
            if not column.is_hidden
        ]
    finally:
        cursor.close()


_MYSQL_BLOB_CODES = (249, 250, 251, 252)
_MYSQL_BINARY_FLAG = 128
_MYSQL_BINARY_CHARSET = 63


@cache
def _mysql_slots() -> dict[Cell, _Slot]:
    slots = _slots(
        {
            "integer": ((1, 2, 3, 8, 9, 13), ()),
            "float": ((4, 5), ()),
            "decimal": ((0, 246), ()),
            "string": ((6, 15, 247, 248, 253, 254), ()),
            "date": ((10, 14), ()),
            "datetime": ((7, 12), ()),
        }
    )
    slots[245] = (ColumnType(kind="json"), json.loads)
    return slots


def _mysql(column: Description) -> _Slot:
    """TEXT and BLOB share a type code. The binary collation (charset 63, description
    index 8) tells them apart; drivers that report only flags give the BINARY flag
    (index 7)."""
    name, code = column[0], column[1]
    if code not in _MYSQL_BLOB_CODES:
        return _by_code("mysql", _mysql_slots)(column)
    if len(column) > 8:
        binary = column[8] == _MYSQL_BINARY_CHARSET
    elif len(column) > 7:
        binary = bool(column[7] & _MYSQL_BINARY_FLAG)
    else:
        raise column_type_error(
            name, "the mysql driver reports no charset or flags to tell TEXT from BLOB"
        )
    return ColumnType(kind="bytes" if binary else "string"), None


_ATHENA_KINDS: dict[str, ColumnKind] = {
    **dict.fromkeys(("tinyint", "smallint", "integer", "bigint"), "integer"),
    **dict.fromkeys(("float", "real", "double"), "float"),
    **dict.fromkeys(("char", "varchar", "string"), "string"),
    "boolean": "boolean",
    "varbinary": "bytes",
    "decimal": "decimal",
    "date": "date",
    "time": "time",
    "timestamp": "datetime",
    "timestamp with time zone": "datetime_tz",
    "json": "json",
    "unknown": "string",
}


def _athena(column: Description) -> _Slot:
    name, code = column[0], column[1]
    if code in ("array", "map", "row"):
        raise column_type_error(
            name,
            f"the athena {code} type reaches the driver as ambiguous, untyped text; "
            f"use CAST({name} AS JSON) in the SQL",
        )
    if code not in _ATHENA_KINDS:
        raise column_type_error(
            name, f"the athena type {code!r} has no declared mapping"
        )
    return _slot(_ATHENA_KINDS[code])


def _hive_json(column: str) -> Decoder:
    def decode(text: Cell) -> Cell:
        try:
            return json.loads(text)
        except json.JSONDecodeError as error:
            raise column_type_error(
                column,
                f"the thrift text of this nested column is not JSON ({error}); "
                "select to_json(column) in the SQL",
            ) from error

    return decode


def _duckdb(column: Description) -> _Slot:
    return duckdb_column_type(column[0], column[1]), None


_FAMILIES: dict[str, _Family] = {
    "mysql": _Family(_mysql, True),
    "athena": _Family(_athena, True),
    "sqlserver": _Family(
        _pyodbc,
        True,
        skip_empty_sets=True,
        close_cursor=True,
        describe=_sqlserver_describe,
    ),
    "duckdb": _Family(_duckdb, True, convert=typed_rows),
    "postgres": _Family(
        _by_code("postgres", _postgres_slots), True, declare=_postgres_enums
    ),
    "redshift": _Family(
        _by_code("redshift", _redshift_slots), True, retry=_redshift_retries
    ),
    "snowflake": _Family(_snowflake, False),
    "spark": _Family(_spark, True),
}


_BIGQUERY_KINDS: dict[str, ColumnKind] = {
    **dict.fromkeys(("INTEGER", "INT64"), "integer"),
    **dict.fromkeys(("FLOAT", "FLOAT64"), "float"),
    **dict.fromkeys(("NUMERIC", "BIGNUMERIC", "DECIMAL", "BIGDECIMAL"), "decimal"),
    **dict.fromkeys(("BOOLEAN", "BOOL"), "boolean"),
    **dict.fromkeys(("STRING", "GEOGRAPHY"), "string"),
    "BYTES": "bytes",
    "DATE": "date",
    "TIME": "time",
    "TIMESTAMP": "datetime_tz",  # the client returns aware UTC cells
    "DATETIME": "datetime",
    "JSON": "json",
}


def _bigquery_type(column: str, field: Driver) -> ColumnType:
    if field.field_type in ("RECORD", "STRUCT"):
        declared = ColumnType(
            kind="struct",
            fields=tuple((f.name, _bigquery_type(column, f)) for f in field.fields),
        )
    elif field.field_type in _BIGQUERY_KINDS:
        declared = ColumnType(kind=_BIGQUERY_KINDS[field.field_type])
    else:
        raise column_type_error(
            column, f"the BigQuery type {field.field_type} has no declared mapping"
        )
    return (
        ColumnType(kind="list", element=declared)
        if field.mode == "REPEATED"
        else declared
    )


_CLICKHOUSE_KINDS: dict[str, ColumnKind] = {
    "Float32": "float",
    "Float64": "float",
    "Bool": "boolean",
    **dict.fromkeys(("String", "Nothing"), "string"),
    "Enum8": "string",
    "Enum16": "string",
    "Date": "date",
    "Date32": "date",
    "Map": "json",  # a dict
    "JSON": "json",
}


def _clickhouse_type(column: str, declared: Driver) -> ColumnType:
    base = declared.base_type  # Nullable and LowCardinality wrap it
    if base == "Array":
        return ColumnType(
            kind="list", element=_clickhouse_type(column, declared.element_type)
        )
    if base == "Tuple":
        if not declared.element_names:
            raise column_type_error(
                column, f"the unnamed {declared.name} has no declared mapping"
            )
        return ColumnType(
            kind="struct",
            fields=tuple(
                (name, _clickhouse_type(column, element))
                for name, element in zip(
                    declared.element_names, declared.element_types, strict=True
                )
            ),
        )
    if base in ("DateTime", "DateTime64"):
        # A zone on the type makes the column an instant.
        return ColumnType(kind="datetime_tz" if declared.tzinfo else "datetime")
    if re.fullmatch(r"U?Int\d+", base):
        return ColumnType(kind="integer")
    if re.fullmatch(r"Decimal\d*", base):
        return ColumnType(kind="decimal")
    if base not in _CLICKHOUSE_KINDS:
        raise column_type_error(
            column, f"the ClickHouse type {declared.name} has no declared mapping"
        )
    return ColumnType(kind=_CLICKHOUSE_KINDS[base])


def _clickhouse_decoder(column: str, declared: Driver) -> Decoder | None:
    """A Map cell, at any depth, becomes the JSON object map_to_json builds; a
    zoned DateTime the UTC instant."""
    base = declared.base_type
    if base in ("DateTime", "DateTime64") and declared.tzinfo:
        return _utc
    if base == "Map":
        value = _clickhouse_decoder(column, declared.value_type)
        return lambda cell: map_to_json(
            column,
            (
                (k, v if value is None or v is None else value(v))
                for k, v in cell.items()
            ),
        )
    if base == "Array":
        element = _clickhouse_decoder(column, declared.element_type)
        return None if element is None else _each(element)
    if base == "Tuple" and declared.element_names:
        decoders = [
            _clickhouse_decoder(column, element) for element in declared.element_types
        ]
        if not any(decoders):
            return None
        return lambda cell: {
            name: cell[name] if d is None or cell[name] is None else d(cell[name])
            for name, d in zip(declared.element_names, decoders, strict=True)
        }
    return None


def _fetch_clickhouse(connections: Driver, sql: str, limit: int | None) -> TypedRows:
    client = connections.get_thread_connection().handle
    result = client.query(
        connections._add_query_comment(sql),  # noqa: SLF001 — dbt's own hook
        query_id=str(uuid.uuid4()),
        # "schema": a zone on the column type gives aware cells, a bare DateTime
        # naive ones; the default would hand a bare column back in the server zone.
        tz_mode="schema",
    )
    if not hasattr(result, "column_types"):
        raise _no_reader(
            "the ClickHouse native protocol reports no column types; use http"
        )
    types = [
        _clickhouse_type(name, declared)
        for name, declared in zip(result.column_names, result.column_types, strict=True)
    ]
    decoders = [
        _clickhouse_decoder(name, declared)
        for name, declared in zip(result.column_names, result.column_types, strict=True)
    ]
    rows = [
        tuple(
            cell if decode is None or cell is None else decode(cell)
            for cell, decode in zip(row, decoders, strict=True)
        )
        for row in result.result_set
    ]
    return TypedRows(list(result.column_names), rows, types)


def _fetch_bigquery(connections: Driver, sql: str, limit: int | None) -> TypedRows:
    _, iterator = connections.raw_execute_with_comment(sql, limit=limit)
    fields = list(iterator.schema)
    types = [_bigquery_type(field.name, field) for field in fields]
    rows = [tuple(row.values()) for row in iterator]
    return TypedRows([field.name for field in fields], rows, types)


def _fetch_databricks(connections: Driver, sql: str, limit: int | None) -> TypedRows:
    """databricks-sql's ``cursor.description`` cannot tell TIMESTAMP from
    TIMESTAMP_NTZ or type a nested column, so the result is read as Arrow and
    typed by the same policy as every other Arrow driver."""
    _, cursor = connections.add_query(
        connections._add_query_comment(sql),  # noqa: SLF001 — dbt's own hook
        auto_begin=False,
    )
    # dbt-databricks' CursorWrapper exposes no Arrow fetch; the databricks-sql
    # cursor it wraps does.
    raw = cursor._cursor  # noqa: SLF001 — see above
    try:
        table = raw.fetchmany_arrow(limit) if limit else raw.fetchall_arrow()
    finally:
        cursor.close()
    names, types, columns = arrow_columns(na.Array(table))
    return TypedRows(names, list(zip(*columns, strict=True)), list(types.values()))


_CLIENTS: dict[str, Callable[[Driver, str, int | None], TypedRows]] = {
    "databricks": _fetch_databricks,
    "bigquery": _fetch_bigquery,
    "clickhouse": _fetch_clickhouse,
}
