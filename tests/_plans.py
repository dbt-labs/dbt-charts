"""A hand-built ``QueryPlan``, the wire shape ``_engine.plan`` returns, for tests that read one."""

from dbt_charts.core.execute.query_plan import (
    ChartQueries,
    JsonValue,
    PlannedCache,
    PlannedQuery,
    PlannedSqlQuery,
    QueryPlan,
)

CACHE: PlannedCache = {"enabled": True, "ttl": None, "prefetch": False}


def sql_entry(
    sql: str,
    *,
    source: str | None = None,
    depends_on: list[str] | None = None,
    setup_sql: str | None = None,
    dialect: str | None = None,
) -> PlannedSqlQuery:
    return {
        "type": "sql",
        "source": source,
        "cache": CACHE,
        "formats": {},
        "depends_on": depends_on or [],
        "sql": sql,
        "setup_sql": setup_sql,
        "dialect": dialect,
        "variable_dependencies": [],
        "incremental": None,
    }


def plan_of(
    queries: dict[str, PlannedQuery],
    *,
    variables: dict[str, JsonValue] | None = None,
    project: dict[str, dict[str, JsonValue]] | None = None,
    inline: dict[str, dict[str, JsonValue]] | None = None,
    chart_queries: dict[str, ChartQueries] | None = None,
) -> QueryPlan:
    return {
        "version": 1,
        "board_id": "b",
        "time_zone": "America/New_York",
        "today": "2026-11-10",
        "week_start": 1,
        "fiscal_year_start_month": 1,
        "prefetch": [],
        "chart_queries": chart_queries or {},
        "variable_queries": {},
        "queries": queries,
        "variables": {"query": variables or {}, "render": {}, "presets": {}},
        "sources": {"project": project or {}, "inline": inline or {}},
    }
