"""Data source adapters.

Stage: EXECUTE
Purpose: Provide adapters for different data sources.

Available Adapters:
    - ValuesAdapter, SchemaAdapter: the non-SQL query types
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
from dbt_charts.core.execute.adapters.schema_adapter import SchemaAdapter
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
    "ValuesAdapter",
    "SchemaAdapter",
]
