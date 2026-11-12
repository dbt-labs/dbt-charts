"""``compose`` over a ``SqlQuery``: the fields it reads, as the field-wise ``compose`` takes them."""

from typing import Any

from dbt_charts.core.compile.models.query.normalized import SqlQuery
from dbt_charts.core.execute.compose import ResolvedQuery, compose
from dbt_charts.core.execute.query_result import (
    QueryResult,
    resolve_effective_row_limit,
)
from dbt_charts.core.execute.sql_translate import QueryRegistry
from dbt_charts.core.text.time_zones import UTC


def compose_query_fields(
    query: SqlQuery,
    queries: QueryRegistry | None = None,
    **kwargs: Any,  # type-state: explicit_any — the rest of compose's keywords, as a test passes them
) -> ResolvedQuery | QueryResult:
    return compose(
        query.sql,
        setup_sql=query.setup_sql,
        dialect=query.dialect,
        strict=not query.lenient_variables,
        limit=resolve_effective_row_limit(query.limit),
        queries=queries,
        time_zone=UTC,
        **kwargs,
    )
