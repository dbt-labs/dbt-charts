"""Values adapter for inline data queries.

Stage: EXECUTE
Purpose: Return inline rows defined directly in the YAML.

This adapter is a pass-through — it simply returns the rows
already embedded in the ValuesQuery. Supports limit.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from dbt_charts.core.compile.models.source import ResolvedSourceConfig

from dbt_charts.core.compile.models.board.normalized import VariableValues
from dbt_charts.core.compile.models.query.normalized import (
    AnyQuery,
    is_values_query,
)
from dbt_charts.core.execute.adapters.base import (
    BaseAdapter,
    QueryParams,
    QueryResult,
    plain_error,
)


class ValuesAdapter(BaseAdapter):
    """Adapter for inline values queries.

    Supported query types: values

    Simply returns the rows embedded in the query definition.
    """

    @property
    def supported_types(self) -> set[str]:
        """Return supported query types."""
        return {"values"}

    def _execute(
        self,
        query: AnyQuery,
        variables: VariableValues | None = None,
        params: QueryParams = None,
        source_config: ResolvedSourceConfig | None = None,
    ) -> QueryResult:
        """Return inline rows from the query."""
        if not is_values_query(query):
            return plain_error(f"Expected values query, got {query.query_type}")

        rows = query.apply_limit(list(query.rows))

        return QueryResult(data=rows)
