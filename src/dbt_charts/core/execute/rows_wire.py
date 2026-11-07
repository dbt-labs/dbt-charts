"""A query result as the engine reads it: rows with tagged cells, and the
declared type of each column, written once per result."""

from __future__ import annotations

import datetime
import decimal
import json
import math
from typing import Any

from dbt_charts.core.column_types import ColumnType
from dbt_charts.core.utils import CellValue

JsonDict = dict[str, Any]  # type-state: explicit_any — JSON payload and engine params
_JsonValue = Any  # type-state: explicit_any — a query cell or a JSON container of them


def json_default(obj: CellValue) -> str | float:
    """JSON hook for query cells: ISO dates, numeric Decimals as numbers (a
    str()'d Decimal would reach the engine as a string cell and flip its
    numeric-type verdicts), str() for anything else."""
    if isinstance(obj, (datetime.date, datetime.datetime)):
        return obj.isoformat()
    if isinstance(obj, decimal.Decimal):
        return float(obj)
    return str(obj)


def encode_cells(value: _JsonValue) -> _JsonValue:
    """`value` with every Decimal and non-finite float tagged, since JSON has
    neither: a finite Decimal as `{"decimal": str(value)}`, a non-finite float or
    Decimal as `{"non_finite": "nan" | "inf" | "-inf", "text": str(value)}`. The
    engine decodes both once, when it loads the rows; `text` labels a pivot
    dimension."""
    if isinstance(value, float | decimal.Decimal):
        if not math.isfinite(value):
            kind = "nan" if math.isnan(value) else ("inf" if value > 0 else "-inf")
            return {"non_finite": kind, "text": str(value)}
        if isinstance(value, decimal.Decimal):
            return {"decimal": str(value)}
    if isinstance(value, dict):
        return {k: encode_cells(v) for k, v in value.items()}
    if isinstance(value, list | tuple):
        return [encode_cells(v) for v in value]
    return value


def schema_wire(schema: dict[str, ColumnType]) -> JsonDict:
    """Each column's declared type as the engine reads it."""
    return {name: declared.model_dump(mode="json") for name, declared in schema.items()}


def dumps_rows(
    rows: list[dict[str, Any]],  # type-state: explicit_any — query cells
    schema: dict[str, ColumnType],
) -> str:
    """The JSON `_engine.load_rows` parses."""
    return json.dumps(
        {"rows": encode_cells(rows), "schema": schema_wire(schema)},
        default=json_default,
        allow_nan=False,
    )
