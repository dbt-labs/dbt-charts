"""Arrow → QueryResult. The only Arrow-to-rows conversion in the codebase.

Stage: EXECUTE

Every driver on the Arrow seam returns a ``nanoarrow.Array`` of the result's
struct type and hands it here, so the type policy is written once instead of
once per warehouse. The policy reads the Arrow type and nothing else: a board's
number format is a chart-layer concern, and letting it reach the data layer
would make one warehouse answer differently depending on which chart asked.

Decimals are narrowed here: a whole number that does not fit 64 bits raises
rather than wrapping into a plausible wrong value.
"""

from __future__ import annotations

from decimal import Context, Decimal
from typing import TYPE_CHECKING, Any

import nanoarrow as na

from dbt_charts.core.column_types import (
    ColumnKind,
    ColumnType,
    as_declared,
    column_type_error,
    map_to_json,
    reject_duplicate_columns,
)
from dbt_charts.core.diagnostics.codes_execute import ERR_RESULT_NUMERIC_OVERFLOW
from dbt_charts.core.diagnostics.execution import ExecutionError
from dbt_charts.core.execute.adapters.base import (
    QueryResult,
    RowFetchLimit,
    apply_row_limit_truncation,
)
from dbt_charts.core.execute.arrow_ingest import relabel

if TYPE_CHECKING:
    from collections.abc import Iterable

    from nanoarrow._array import CArray
    from nanoarrow._schema import CSchema

    from dbt_charts.core.execute.adapters.base import ResolvedRelation

_INT64_MIN = -(2**63)
_INT64_MAX = 2**63 - 1
# Formats nanoarrow 0.9 crashes on rather than reads: string_view, binary_view.
_VIEW_FORMATS = frozenset({"vu", "vz"})


def read_bounded_stream(
    source: object,  # type-state: object_annotation — any Arrow stream exporter
    limit: RowFetchLimit,
) -> na.Array:
    """Read the Arrow stream *source* exports until ``limit.fetch_limit`` rows
    are in hand, releasing it before returning.

    A result holding view arrays is read through an in-memory DuckDB, which
    hands it back as plain arrays.
    """
    stream = na.c_array_stream(source)
    try:
        schema = stream.get_schema()
        if not _has_format(schema, _VIEW_FORMATS):
            return read_bounded_table(stream, schema, limit)
        import duckdb

        with duckdb.connect(config={"enable_external_access": False}) as conn:
            conn.execute("SET TimeZone='UTC'")
            conn.register("_dct_result", stream)
            return na.Array(
                conn.sql(f"SELECT * FROM _dct_result LIMIT {limit.fetch_limit}")
            )
    finally:
        stream.release()


def read_bounded_table(
    batches: Iterable[CArray], schema: CSchema, limit: RowFetchLimit
) -> na.Array:
    """Read *batches* until ``limit.fetch_limit`` rows are in hand.

    Every driver bounds its fetch by reading the Arrow stream rather than by
    rewriting the SQL, so an author's own LIMIT, a SHOW and a DESCRIBE all
    behave exactly as they would without a ceiling. Batches are large, so the
    bound is coarse; what it buys is not fetching a million rows to return a
    thousand.

    *schema* is passed rather than taken from the first batch because a query
    that returns no rows returns no batches either, and the result still has
    to carry its columns.
    """
    collected: list[CArray] = []
    fetched = 0
    for batch in batches:
        collected.append(batch)
        fetched += batch.length
        if fetched >= limit.fetch_limit:
            break
    if not collected:
        return na.Array.from_chunks([], schema)
    return na.Array.from_chunks(collected)


def arrow_to_query_result(
    table: na.Array,
    limit: RowFetchLimit,
    resolved_relations: list[ResolvedRelation] | None = None,
) -> QueryResult:
    """Convert a driver's Arrow table into the engine's row-dict result.

    Column names keep the case the query gave them, as the in-process DuckDB
    adapter does. A map becomes a JSON object (see ``map_to_json``).

    The result's schema is the Arrow schema, mapped by :func:`declared_arrow_type`.

    Args:
        table: The driver's result, already bounded to ``limit.fetch_limit`` rows
            (drivers stop reading batches once they have that many).
        limit: The resolved row limit the driver fetched against. Truncation is
            flagged only when the ``execution.max_rows`` ceiling, not the
            author's own ``Query.limit``, was the binding value.
        resolved_relations: dbt refs/sources resolved while preparing the SQL.

    Raises:
        ExecutionError: ERR-RESULT-NUMERIC-OVERFLOW when a scale-0 decimal
            column holds a value wider than a 64-bit integer;
            ERR-RESULT-COLUMN-TYPE when a column's Arrow type has no mapping.
    """
    names, column_types, columns = arrow_columns(table)
    data: list[dict[str, Any]] = (  # type-state: explicit_any — warehouse row values
        [dict(zip(names, values, strict=True)) for values in zip(*columns, strict=True)]
        if columns
        else [{} for _ in range(len(table))]
    )
    rows, truncated_reason = apply_row_limit_truncation(data, limit)
    return QueryResult(
        data=rows,
        columns=names,
        schema=column_types,
        resolved_relations=resolved_relations,
        truncated_reason=truncated_reason,
    )


def arrow_columns(
    table: na.Array,
) -> tuple[
    list[str],
    dict[str, ColumnType],
    list[list[Any]],  # type-state: explicit_any — warehouse cell values
]:
    """The column names, declared types and declared-form cells of *table*."""
    schema = na.c_schema(table.schema)
    fields = list(schema.children)
    names = [_name(field) for field in fields]
    reject_duplicate_columns(names)
    column_types = {
        name: declared_arrow_type(name, field, narrowed=True)
        for name, field in zip(names, fields, strict=True)
    }
    storage = _storage(schema)
    if _needs_decoding(schema):
        chunks = [relabel(na.c_array(chunk), storage) for chunk in table.iter_chunks()]
        table = (
            na.Array.from_chunks(chunks)
            if chunks
            else na.Array.from_chunks([], storage)
        )
    columns = [
        _narrow_decimals(name, field, _decoded(name, table.child(i).to_pylist(), field))
        for i, (name, field) in enumerate(zip(names, fields, strict=True))
    ]
    # A zoned timestamp arrives in its own zone; the declared cell is UTC.
    columns = [
        [as_declared(cell, column_types[name]) for cell in values]
        if "datetime_tz" in str(column_types[name])
        else values
        for name, values in zip(names, columns, strict=True)
    ]
    return names, column_types, columns


# Arrow C data interface format strings.
_ARROW_SCALARS: dict[str, ColumnKind] = {
    **dict.fromkeys("cCsSiIlL", "integer"),
    **dict.fromkeys("efg", "float"),
    "b": "boolean",
    **dict.fromkeys(("u", "U"), "string"),
    **dict.fromkeys(("z", "Z"), "bytes"),
    "n": "string",
    **dict.fromkeys(("tdD", "tdm"), "date"),
}
# Times carry their unit after the prefix; a fixed-size binary its width.
_ARROW_PREFIXES: tuple[tuple[str, ColumnKind], ...] = (
    ("tt", "time"),
    ("w:", "bytes"),
)


def declared_arrow_type(
    name: str, field: CSchema, *, narrowed: bool = False
) -> ColumnType:
    """The declared type of the Arrow *field* that holds column *name*.

    A map is ``json``, its cells a dict, and a NULL-typed column is ``string``.
    With *narrowed*, a top-level decimal is an ``integer`` (scale 0) or a
    ``float``: what its cells are narrowed to (:func:`_narrow_decimals`).

    Raises:
        ExecutionError: ERR-RESULT-COLUMN-TYPE for a type outside the
            vocabulary (duration, interval, union, a map keyed by a nested
            type, ...).
    """
    field = _held(field)
    form = field.format
    if form == "+m":
        (entries,) = field.children
        key_form = _held(next(iter(entries.children))).format
        if key_form.startswith(("+", "z", "Z", "w:")):
            raise column_type_error(
                name, f"the map key of Arrow format {key_form!r} has no JSON form"
            )
        return ColumnType(kind="json")
    if form in ("+l", "+L") or form.startswith("+w:"):
        (item,) = field.children
        return ColumnType(kind="list", element=declared_arrow_type(name, item))
    if form == "+s":
        return ColumnType(
            kind="struct",
            fields=tuple(
                (_name(child), declared_arrow_type(name, child))
                for child in field.children
            ),
        )
    if _is_decimal(field):
        if not narrowed:
            return ColumnType(kind="decimal")
        return ColumnType(kind="integer" if _decimal_params(field)[1] == 0 else "float")
    if form in _ARROW_SCALARS:
        return ColumnType(kind=_ARROW_SCALARS[form])
    if form.startswith("ts"):  # tsu:<zone>: a zone makes it an instant
        return ColumnType(kind="datetime_tz" if form.partition(":")[2] else "datetime")
    for prefix, kind in _ARROW_PREFIXES:
        if form.startswith(prefix):
            return ColumnType(kind=kind)
    raise column_type_error(
        name,
        f"the Arrow format {form!r} has no declared mapping; "
        "cast it in the query (e.g. to VARCHAR)",
    )


def _name(field: CSchema) -> str:
    if field.name is None:
        raise ValueError("Arrow result column has no name")
    return field.name


def _has_format(schema: CSchema, formats: frozenset[str]) -> bool:
    return schema.format in formats or any(
        _has_format(child, formats) for child in schema.children
    )


def _is_decimal(schema: CSchema) -> bool:
    return schema.format.startswith("d:")


def _held(schema: CSchema) -> CSchema:
    """The type a column's values have: its dictionary's, if it is encoded."""
    return schema.dictionary if schema.dictionary is not None else schema


def _needs_decoding(schema: CSchema) -> bool:
    if schema.dictionary is not None:
        return _needs_decoding(schema.dictionary)
    return (
        _is_decimal(schema)
        or schema.format == "+m"
        or any(_needs_decoding(child) for child in schema.children)
    )


def _storage(schema: CSchema) -> CSchema:
    """*schema* as the layout nanoarrow reads correctly: a map as the list of
    key/value structs it stores, a decimal as its raw fixed-size bytes (nanoarrow
    0.9 decodes those unsigned, so every negative decimal would come back wrong).
    """
    if schema.dictionary is not None:
        return schema.modify(dictionary=_storage(schema.dictionary))
    if _is_decimal(schema):
        return schema.modify(format=f"w:{_decimal_params(schema)[2] // 8}")
    return schema.modify(
        format="+l" if schema.format == "+m" else schema.format,
        children=[_storage(child) for child in schema.children],
    )


def _decoded(
    name: str,
    values: list[Any],  # type-state: explicit_any — warehouse cell values
    field: CSchema,
) -> list[Any]:  # type-state: explicit_any — warehouse cell values
    if not _needs_decoding(field):
        return values
    return [_decode(name, value, field) for value in values]


def _decode(
    name: str,
    value: Any,  # type-state: explicit_any — one warehouse cell value
    field: CSchema,
) -> Any:  # type-state: explicit_any — one warehouse cell value
    """One value read through :func:`_storage`, turned back into what *field* holds."""
    if value is None:
        return None
    field = _held(field)
    if _is_decimal(field):
        precision, scale, _ = _decimal_params(field)
        return Decimal(int.from_bytes(value, "little", signed=True)).scaleb(
            -scale, Context(prec=precision)
        )
    children = list(field.children)
    if field.format == "+m":
        entries = [_decode(name, item, children[0]) for item in value]
        return map_to_json(name, ((e["key"], e["value"]) for e in entries))
    if field.format == "+s":
        return {
            child.name: _decode(name, value[child.name], child) for child in children
        }
    if children:
        return [_decode(name, item, children[0]) for item in value]
    return value


def _narrow_decimals(
    name: str,
    field: CSchema,
    values: list[Any],  # type-state: explicit_any — warehouse cell values
) -> list[Any]:  # type-state: explicit_any — warehouse cell values
    """Turn a decimal column into the Python numbers it is actually holding.

    Scale 0 is a whole number, so it becomes ``int``; anything scaled becomes
    ``float``. The integer narrowing is the one that can lose information, so
    a value outside int64 fails loudly.
    """
    field = _held(field)
    if not _is_decimal(field):
        return values
    if _decimal_params(field)[1] != 0:
        return [None if v is None else float(v) for v in values]
    narrowed = [None if v is None else int(v) for v in values]
    if any(v is not None and not _INT64_MIN <= v <= _INT64_MAX for v in narrowed):
        raise ExecutionError.from_code(ERR_RESULT_NUMERIC_OVERFLOW, column=name)
    return narrowed


def _decimal_params(field: CSchema) -> tuple[int, int, int]:
    """Precision, scale and bit width from a decimal format ``d:P,S[,W]``."""
    parts = [int(part) for part in field.format[2:].split(",")]
    return parts[0], parts[1], parts[2] if len(parts) == 3 else 128
