"""The QueryPlan wire contract `_engine.plan` returns: versioned JSON, read here.

Not a class the engine builds: a reader refuses any version but its own.
"""

import json
from typing import Literal, TypedDict

QUERY_PLAN_VERSION = 1

# JSON the engine spells and Python passes through unread: a values query's keys, a variable
# value ({"kind": ..., "value": ...}).
JsonValue = None | bool | int | float | str | list["JsonValue"] | dict[str, "JsonValue"]


class PlannedCache(TypedDict):
    """A query's resolved cache policy; `ttl` is the duration as authored (`1h`), null for none."""

    enabled: bool
    ttl: str | None
    prefetch: bool


class _PlannedBase(TypedDict):
    source: str | None
    cache: PlannedCache
    formats: dict[str, str]
    depends_on: list[str]


class PlannedSqlQuery(_PlannedBase):
    """A SQL query. `incremental` is the effective watermark column (its own, else its board's);
    `variable_dependencies` are those of its own text and of every query it embeds."""

    type: Literal["sql"]
    sql: str
    setup_sql: str | None
    dialect: str | None
    variable_dependencies: list[str]
    incremental: str | None


class PlannedDataQuery(_PlannedBase):
    """A `values` or `schema` query: the keys the author wrote, carried unread in `spec`."""

    type: Literal["values", "schema"]
    spec: JsonValue


PlannedQuery = PlannedSqlQuery | PlannedDataQuery


class PlanVariables(TypedDict):
    """The values a run binds into queries and the values render draws, by variable name."""

    query: dict[str, JsonValue]
    render: dict[str, JsonValue]
    #: The preset id of each daterange variable whose value is a preset (its default or the
    #: caller's), by name; fixed dates have no entry.
    presets: dict[str, str]


class PlanSources(TypedDict):
    """The sources a run resolves queries against, as the declarations the host passed to compile
    (`project`, by name) and the file sources queries name inline (`inline`, by the ref as
    authored). A ref the project also declares is in `project` only; which wins is the
    resolver's call, not the plan's."""

    project: dict[str, dict[str, JsonValue]]
    inline: dict[str, dict[str, JsonValue]]


class ChartQueries(TypedDict):
    """The plan keys of the queries a chart reads: its own (null for a chart that reads none) and
    each layer's, in order (null for a layer that draws the chart's)."""

    query: str | None
    layers: list[str | None]


class VariableQueries(TypedDict):
    """The plan keys of a variable's `query:`, `options.query:` and `enabled:` probe query, each
    null when it has none."""

    query: str | None
    options: str | None
    enabled: str | None


class QueryPlan(TypedDict):
    version: int
    board_id: str
    time_zone: str
    #: The civil date `now_epoch` is in `time_zone`, `YYYY-MM-DD`: the day presets are dated on.
    today: str
    #: 0 is Sunday.
    week_start: int
    fiscal_year_start_month: int
    #: The queries only hidden tabs read whose cache policy prefetches; empty unless the plan
    #: was scoped to the active tabs.
    prefetch: list[str]
    #: By public chart id, for the charts the plan shows; keys are those of `queries`.
    chart_queries: dict[str, ChartQueries]
    #: By variable name, for the variables the plan shows; keys are those of `queries`.
    variable_queries: dict[str, VariableQueries]
    queries: dict[str, PlannedQuery]
    variables: PlanVariables
    sources: PlanSources


def parse_query_plan(text: str) -> QueryPlan:
    """The plan `_engine.plan` wrote, or an error naming a version this reader does not know."""
    plan: QueryPlan = json.loads(text)
    if plan["version"] != QUERY_PLAN_VERSION:
        raise ValueError(
            f"QueryPlan version {plan['version']} is not the version "
            f"{QUERY_PLAN_VERSION} this reader knows"
        )
    return plan
