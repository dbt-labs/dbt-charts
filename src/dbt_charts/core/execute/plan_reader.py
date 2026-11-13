"""Read a ``QueryPlan``: its variable values, clock, embeds, setup chain and sources.

Stage: EXECUTE (plan)
Purpose: What every stage that holds a plan asks of it, kept below the stages
    so plan, lookup, run and the entry points in ``planned.py`` share one reading.
    A plan may hold source declarations with connection settings or credentials:
    nothing here writes one anywhere.
"""

import re
from collections.abc import Mapping
from datetime import date
from typing import Any

from dbt_charts.core.compile.config import ProjectSourcesConfig
from dbt_charts.core.compile.models.source import ResolvedSourceConfig
from dbt_charts.core.compile.template.daterange import BoardClock
from dbt_charts.core.compile.template.jinja import (
    expand_query_refs,
    extract_sql_variable_dependencies,
)
from dbt_charts.core.compile.template.variables import VariableValues
from dbt_charts.core.diagnostics.base import DbtChartsError
from dbt_charts.core.diagnostics.codes_compile import ERR_SOURCE_NOT_FOUND
from dbt_charts.core.execute.env import ExecEnv
from dbt_charts.core.execute.plan_sources import resolve_authored_source
from dbt_charts.core.execute.query_plan import (
    JsonValue,
    PlannedQuery,
    PlannedSqlQuery,
    QueryPlan,
)
from dbt_charts.core.execute.query_result import RowFetchLimit
from dbt_charts.core.text.time_zones import TimeZoneName

# `{{ queries.X }}`: the embeds whose setup_sql a query inherits.
_EMBED = re.compile(r"\{\{\s*queries\.(\w+)\s*\}\}")
# Every `{{ queries.X }}` and `{{ queries.X.cache }}`: what a query embeds.
_REF = re.compile(r"\{\{\s*queries\.([A-Za-z_]\w*)")

# The tags of a variable value the engine spells; a host built-in is echoed raw.
_TAGGED = frozenset(
    {"unset", "multiselect", "daterange", "text", "int", "float", "bool", "date"}
)


def _atom(
    wire: Mapping[str, JsonValue],
) -> object:  # type-state: object_annotation — a DB-API bind value
    value = wire["value"]
    if wire["kind"] == "date":
        assert isinstance(value, str)
        return date.fromisoformat(value)
    return value


def _slot(
    wire: Mapping[str, JsonValue],
) -> object:  # type-state: object_annotation — a DB-API bind value
    return None if wire["kind"] == "unset" else _atom(wire)


def board_clock(plan: QueryPlan) -> BoardClock:
    """The clock a run dates presets on, from the plan's header: the one place it is derived."""
    return BoardClock(
        date.fromisoformat(plan["today"]),
        plan["fiscal_year_start_month"],
        plan["week_start"],
        plan["time_zone"],
    )


def plan_variables(plan: QueryPlan) -> VariableValues:
    """The values a run binds into queries, as the Python values a bind takes: a date is a
    ``date``, a multiselect a list, a daterange its ``[start, end]``, an unset value ``None``.
    A host built-in, which the plan echoes untagged, is passed through."""
    bound: VariableValues = {}
    for name, wire in plan["variables"]["query"].items():
        if not (isinstance(wire, dict) and wire.get("kind") in _TAGGED):
            bound[name] = wire
            continue
        match wire["kind"]:
            case "unset":
                bound[name] = None
            case "multiselect":
                members = wire["values"]
                assert isinstance(members, list)
                bound[name] = [_slot(m) for m in members if isinstance(m, dict)]
            case "daterange":
                start, end = wire["start"], wire["end"]
                assert isinstance(start, dict) and isinstance(end, dict)
                bound[name] = [_slot(start), _slot(end)]
            case _:
                bound[name] = _atom(wire)
    return bound


def local_name(key: str) -> str:
    """The name a ``{{ queries.X }}`` spells for the plan key *key*: a query of another
    file is keyed ``<file>#<name>``, and its SQL embeds the queries of its own file by name."""
    return key.partition("#")[2] or key


def reachable(plan: QueryPlan, name: str) -> dict[str, PlannedQuery]:
    """The query keyed *name* and every query it embeds, transitively, by the names its SQL
    spells: the registry a ``{{ queries.X }}`` in it expands against. A query embeds only
    those of its own file, so no two share a local name."""
    found: dict[str, PlannedQuery] = {}
    pending = [name]
    while pending:
        current = pending.pop()
        if local_name(current) not in found:
            found[local_name(current)] = plan["queries"][current]
            pending.extend(found[local_name(current)]["depends_on"])
    return found


def setup_sql_chain(registry: Mapping[str, PlannedQuery], name: str) -> str | None:
    """The setup_sql of *name* and of every query it embeds, dependencies first, each
    distinct statement once, joined."""
    seen: set[str] = set()
    ordered: list[str] = []

    def walk(current: str) -> None:
        entry = registry.get(current)
        if current in seen or entry is None or entry["type"] != "sql":
            return
        seen.add(current)
        for embedded in _EMBED.findall(entry["sql"]):
            walk(embedded)
        if entry["setup_sql"] and entry["setup_sql"] not in ordered:
            ordered.append(entry["setup_sql"])

    walk(local_name(name))
    return "\n".join(ordered) if ordered else None


def expanded_sql(plan: QueryPlan, name: str) -> str:
    """The SQL of the query *name* with every ``{{ queries.X }}`` expanded down the chain: the
    text that executes, which source choice, composition and the cache key read. A
    ``{{ queries.X.cache }}`` reference stays for the cache composer."""
    return expand_query_refs(local_name(name), reachable(plan, name))


def env_sources(env: ExecEnv) -> ProjectSourcesConfig:
    """The registry a plan is compiled against when the caller connects: the project's
    declarations with the env's run-time registrations over them."""
    runtime = env.sources.runtime_config()
    return ProjectSourcesConfig(
        sources={**env.project.declared_sources.sources, **runtime.sources},
        implicit_source=runtime.implicit_source,
    )


def planned_project_sources(plan: QueryPlan, env: ExecEnv) -> ProjectSourcesConfig:
    """The sources a resolver consults: the declarations the plan was compiled against, which
    carry the env's run-time registrations too (the host compiles them in)."""
    return ProjectSourcesConfig(
        sources=plan["sources"]["project"],
        implicit_source=env.sources.runtime_config().implicit_source,
    )


def resolve_planned_source(
    plan: QueryPlan, name: str, env: ExecEnv
) -> ResolvedSourceConfig | None:
    """The source the query *name* runs against, by the plan's declarations.

    The resolver's own precedence decides between the two kinds: a default
    resolver takes the inline entry (a file source the query names by path), an
    allowlisting one the project's. None for a query that needs no connection.

    Raises:
        KeyError: The plan holds no query *name*.
        DbtChartsError: The resolver refuses the source.
    """
    entry = plan["queries"][name]
    # Chosen on the SQL that executes, `{{ queries.X }}` expanded: a `ref()` in an embedded
    # query decides the source of the query that embeds it.
    sql = expanded_sql(plan, name) if entry["type"] == "sql" else None
    return resolve_authored_source(
        entry["source"],
        sql,
        sources=env.sources,
        project_sources=planned_project_sources(plan, env),
        resolver=env.resolver,
        dbt_context=env.dbt_context,
        board_sources=plan["sources"]["inline"],
        query_name=name,
    )


def compose_fields(
    plan: QueryPlan,
    name: str,
    entry: PlannedSqlQuery,
    *,
    strict: bool,
    limit: RowFetchLimit,
) -> dict[str, Any]:  # type-state: explicit_any — kwargs spread into compose
    registry = reachable(plan, name)
    return {
        # Every `{{ queries.X }}` expanded down the chain before composition, which inlines
        # one level; a `.cache` reference stays for the cache composer.
        "sql": expanded_sql(plan, name),
        "setup_sql": setup_sql_chain(registry, name),
        "dialect": entry["dialect"],
        "strict": strict,
        "limit": limit,
        "queries": registry,
        "time_zone": TimeZoneName(plan["time_zone"]),
        "variables": plan_variables(plan),
    }


def replaced_sql(plan: QueryPlan, name: str, sql: str) -> QueryPlan:
    """*plan* with the query *name* running *sql* instead, its embedded queries'
    setup_sql chained into its own: a copy, the plan is never changed in place."""
    entry = plan["queries"][name]
    assert entry["type"] == "sql"
    chain = setup_sql_chain(reachable(plan, name), name)
    return {
        **plan,
        "queries": {**plan["queries"], name: {**entry, "sql": sql, "setup_sql": chain}},
    }


def embedded_queries(sql: str) -> list[str]:
    """The names of the queries *sql* embeds, as ``{{ queries.X }}`` or ``{{ queries.X.cache }}``."""
    return list(dict.fromkeys(_REF.findall(sql)))


def with_adhoc_query(
    plan: QueryPlan, name: str, sql: str, source: str | None
) -> QueryPlan:
    """*plan* plus one SQL entry, for SQL typed at run time that no board holds.

    Constructs one plan entry; not a planner: the cache is off, no setup_sql, no dialect, and
    the variable dependencies are those of the text and of the queries it embeds. *source* is a
    name the plan declares (project or inline), or None for the default source.

    Raises:
        ValueError: *name* is already a query of the plan.
        DbtChartsError: *source* is not declared by the plan.
    """
    if name in plan["queries"]:
        raise ValueError(f"the plan already holds a query named {name!r}")
    declared = plan["sources"]["project"].keys() | plan["sources"]["inline"].keys()
    if source is not None and source not in declared:
        raise DbtChartsError.from_code(
            ERR_SOURCE_NOT_FOUND,
            query_name=name,
            source=source,
            available=sorted(declared),
            advice="",
        )
    embeds = embedded_queries(sql)
    dependencies = extract_sql_variable_dependencies(sql, None)
    for embedded in embeds:
        upstream = plan["queries"][embedded]
        if upstream["type"] == "sql":
            dependencies |= set(upstream["variable_dependencies"])
    entry: PlannedSqlQuery = {
        "type": "sql",
        "source": source,
        "cache": {"enabled": False, "ttl": None, "prefetch": False},
        "formats": {},
        "depends_on": embeds,
        "sql": sql,
        "setup_sql": None,
        "dialect": None,
        "variable_dependencies": sorted(dependencies),
        "incremental": None,
    }
    return {**plan, "queries": {**plan["queries"], name: entry}}
