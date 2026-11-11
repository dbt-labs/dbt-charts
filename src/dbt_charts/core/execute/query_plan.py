"""The QueryPlan wire contract `_engine.plan` returns: versioned JSON, read here.

Not a class the engine builds: a reader refuses any version but its own.
"""

import json
from typing import Literal, NotRequired, TypedDict

QUERY_PLAN_VERSION = 1

# JSON the engine spells and Python passes through unread: a cache layer, a values query's keys,
# a variable value ({"kind": ..., "value": ...}).
JsonValue = None | bool | int | float | str | list["JsonValue"] | dict[str, "JsonValue"]


class PlannedQuery(TypedDict):
    """One query a run reads. A SQL query carries `sql`, with the variables it reads (its own and its
    embeds') and its incremental watermark column; any other kind carries `spec`."""

    type: Literal["sql", "values", "schema"]
    source: str | None
    cache: JsonValue
    formats: dict[str, str]
    depends_on: list[str]
    sql: NotRequired[str]
    setup_sql: NotRequired[str | None]
    dialect: NotRequired[str | None]
    variable_dependencies: NotRequired[list[str]]
    incremental: NotRequired[str | None]
    spec: NotRequired[JsonValue]


class PlanVariables(TypedDict):
    """The values a run binds into queries and the values render draws, by variable name."""

    query: dict[str, JsonValue]
    render: dict[str, JsonValue]


class PlanSources(TypedDict):
    """The sources a run resolves queries against, as the declarations the host passed to compile
    (`project`, by name) and the file sources queries name inline (`inline`, by the ref as
    authored). A ref the project also declares is in `project` only; which wins is the
    resolver's call, not the plan's."""

    project: dict[str, JsonValue]
    inline: dict[str, JsonValue]


class QueryPlan(TypedDict):
    version: int
    board_id: str
    time_zone: str
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
