"""Query execution module.

Stage: EXECUTE (Service)
Purpose: Execute queries for compiled boards.

This is a SERVICE module, not a sequential stage. It's called lazily
by the render module when charts need data.

Entry Points:
    - Executor: Main executor class
    - Executor.execute_query(query_name, variables) -> List[Dict]
    - Executor.execute_chart(chart, variables) -> List[Dict]

Inputs:
    - Board: Board with compiled queries
    - Query name and variables

Outputs:
    - List[Dict[str, Any]]: Query results (list of row dictionaries)

Dependencies:
    - dbt_charts.compile (for Board, AnyQuery types)

This module can import from compile/ (needs types) but should NOT
import from render/ (render imports us, not vice versa).
"""

from __future__ import annotations

import importlib
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from dbt_charts.core.diagnostics.execution import (
        AdapterError,
        ExecutionError,
        QueryError,
    )
    from dbt_charts.core.execute.cache_backend import (
        CachedQueryFailure,
        QueryResultCache,
    )
    from dbt_charts.core.execute.cache_keys import SECRET_FIELDS, source_identity
    from dbt_charts.core.execute.duckdb_cache import (
        compute_query_hash,
        compute_source_hash,
        compute_variables_hash,
    )
    from dbt_charts.core.execute.executor import Executor

__all__ = [
    # Executor
    "Executor",
    # Cache Protocol
    "QueryResultCache",
    # DuckDB Cache
    "CachedQueryFailure",
    "SECRET_FIELDS",
    "compute_query_hash",
    "compute_source_hash",
    "compute_variables_hash",
    "source_identity",
    # Errors
    "ExecutionError",
    "QueryError",
    "AdapterError",
]

# Submodule (sqlite_utils, adapters, etc.) imports run this __init__ first, so
# these convenience re-exports must not eagerly pull in cache_keys' (and
# transitively core.compile's) heavy import chain just to expose Executor.
_LAZY_ATTRS: dict[str, tuple[str, str]] = {
    "AdapterError": ("dbt_charts.core.diagnostics.execution", "AdapterError"),
    "ExecutionError": ("dbt_charts.core.diagnostics.execution", "ExecutionError"),
    "QueryError": ("dbt_charts.core.diagnostics.execution", "QueryError"),
    "CachedQueryFailure": (
        "dbt_charts.core.execute.cache_backend",
        "CachedQueryFailure",
    ),
    "QueryResultCache": ("dbt_charts.core.execute.cache_backend", "QueryResultCache"),
    "SECRET_FIELDS": ("dbt_charts.core.execute.cache_keys", "SECRET_FIELDS"),
    "source_identity": ("dbt_charts.core.execute.cache_keys", "source_identity"),
    "compute_query_hash": (
        "dbt_charts.core.execute.duckdb_cache",
        "compute_query_hash",
    ),
    "compute_source_hash": (
        "dbt_charts.core.execute.duckdb_cache",
        "compute_source_hash",
    ),
    "compute_variables_hash": (
        "dbt_charts.core.execute.duckdb_cache",
        "compute_variables_hash",
    ),
    "Executor": ("dbt_charts.core.execute.executor", "Executor"),
}


def __getattr__(name: str) -> Any:
    try:
        module_path, attr_name = _LAZY_ATTRS[name]
    except KeyError:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}") from None
    module = importlib.import_module(module_path)
    value = getattr(module, attr_name)
    globals()[name] = value  # cache: repeated access is O(1) and `is` identity holds
    return value


def __dir__() -> list[str]:
    return sorted(set(globals()) | set(_LAZY_ATTRS))
