"""Collect and order setup_sql preambles across query dependencies.

When query B depends on query A via {{ queries.A }}, and A declares
setup_sql, that preamble must execute before B's main body runs.
This module walks the dependency graph, collects setup_sql blocks
in topological order, and deduplicates identical blocks.
"""

from __future__ import annotations

import re

from dbt_charts.core.compile.models.query.normalized import (
    AnyQuery,
    is_sql_query,
)

_QUERY_REF_PATTERN = re.compile(r"\{\{\s*queries\.(\w+)\s*\}\}")


def collect_setup_sql(
    query_name: str,
    queries: dict[str, AnyQuery],
) -> list[str]:
    """Collect setup_sql statements for a query and all its dependencies.

    Walks the dependency graph depth-first, returns setup_sql blocks in
    dependency order (dependencies before dependents). Identical blocks
    are deduplicated while preserving the earliest occurrence.

    Args:
        query_name: Name of the query to collect setup_sql for.
        queries: Full query registry.

    Returns:
        Ordered list of unique setup_sql strings to execute before the main query.
    """
    seen_names: set[str] = set()
    ordered: list[str] = []
    seen_sql: set[str] = set()

    def _walk(name: str) -> None:
        if name in seen_names or name not in queries:
            return
        seen_names.add(name)

        query = queries[name]
        # Walk dependencies first (depth-first, dependencies before self)
        if is_sql_query(query):
            for ref in _QUERY_REF_PATTERN.findall(query.sql):
                _walk(ref)

        # Then collect this query's setup_sql (after deps)
        if is_sql_query(query) and query.setup_sql:
            if query.setup_sql not in seen_sql:
                seen_sql.add(query.setup_sql)
                ordered.append(query.setup_sql)

    _walk(query_name)
    return ordered
