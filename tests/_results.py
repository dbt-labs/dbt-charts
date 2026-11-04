"""Typed results for tests that hand rows to a cache."""

from __future__ import annotations

from typing import Any

from dbt_charts.core.column_types import ColumnType, literal_schema
from dbt_charts.core.execute.cache_backend import ResultRows


def rows_schema(
    rows: list[dict[str, Any]],  # type-state: explicit_any — test rows
) -> dict[str, ColumnType]:
    """The schema *rows*' literal values declare (an all-null column is a string)."""
    return literal_schema(rows, untyped=ColumnType(kind="string"))


def rows_result(
    rows: list[dict[str, Any]],  # type-state: explicit_any — test rows
) -> ResultRows:
    return ResultRows(rows, rows_schema(rows))
