"""Data source adapters.

Stage: EXECUTE
Purpose: Provide adapters for different data sources.

Available Adapters:
    - SqlAdapter: Raw SQL queries
    - DbtAdapter: dbt-integrated SQL queries
"""

from dbt_charts.core.execute.adapters.adapter_registry import (
    LOCAL_AUTHORING_REGISTRY_KWARGS,
    AdapterRegistry,
    build_adapter_registry,
)
from dbt_charts.core.execute.adapters.base import (
    BaseAdapter,
    QueryResult,
    handle_adapter_error,
)
from dbt_charts.core.execute.adapters.dbt_adapter import DbtAdapter
from dbt_charts.core.execute.adapters.duckdb_adapter import DuckDBAdapter
from dbt_charts.core.execute.adapters.schema_adapter import SchemaAdapter
from dbt_charts.core.execute.adapters.sql_adapter import SqlAdapter
from dbt_charts.core.execute.adapters.values_adapter import ValuesAdapter

__all__ = [
    # Base
    "BaseAdapter",
    "QueryResult",
    "handle_adapter_error",
    # Registry
    "AdapterRegistry",
    "LOCAL_AUTHORING_REGISTRY_KWARGS",
    "build_adapter_registry",
    # Adapters
    "SqlAdapter",
    "DuckDBAdapter",
    "DbtAdapter",
    "ValuesAdapter",
    "SchemaAdapter",
]
