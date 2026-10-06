"""validate_query verb — stateless SQL lint, returns typed QueryDiagnostic list."""

from __future__ import annotations

from typing import Literal, overload

from dbt_charts.core.compile.sql_guard import require_sqlglot_dialect
from dbt_charts.core.inspect.query_validator import (
    QueryDiagnostic,
    validate_query as _core_validate,
)

__all__ = ["QueryDiagnostic", "require_sqlglot_dialect", "validate_query"]


@overload
def validate_query(
    sql: str,
    *,
    dialect: str | None = ...,
    suppress: set[str] | None = ...,
    return_suppressed: Literal[False] = ...,
) -> list[QueryDiagnostic]: ...


@overload
def validate_query(
    sql: str,
    *,
    dialect: str | None = ...,
    suppress: set[str] | None = ...,
    return_suppressed: Literal[True],
) -> tuple[list[QueryDiagnostic], list[QueryDiagnostic]]: ...


def validate_query(
    sql: str,
    *,
    dialect: str | None = None,
    suppress: set[str] | None = None,
    return_suppressed: bool = False,
) -> list[QueryDiagnostic] | tuple[list[QueryDiagnostic], list[QueryDiagnostic]]:
    """Run static SQL validation and return typed diagnostics.

    Stateless — requires no project, adapter, or warehouse connection.

    Args:
        sql: SQL query string to validate.
        dialect: Optional SQLGlot dialect name (e.g. "duckdb", "bigquery").
        suppress: Optional set of diagnostic codes to suppress from the active list.
        return_suppressed: If True, return (active, suppressed) tuple instead of
            just the active list. Used by CLI --show-suppressed.

    Returns:
        list[QueryDiagnostic] — active diagnostics (default).
        tuple[list[QueryDiagnostic], list[QueryDiagnostic]] — (active, suppressed)
            when return_suppressed=True.
    """
    if return_suppressed:
        return _core_validate(
            sql, dialect=dialect, suppress=suppress, return_suppressed=True
        )
    return _core_validate(sql, dialect=dialect, suppress=suppress)
