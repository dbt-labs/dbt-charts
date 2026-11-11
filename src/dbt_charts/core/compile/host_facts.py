"""The host-facts wire contract `CompiledBoard.host_facts_json` returns: versioned JSON, read here.

What `dct validate`'s host checks read off a compiled board. Not a class the engine builds: a
reader refuses any version but its own.
"""

import json
from collections.abc import Mapping
from typing import Literal, TypedDict

from dbt_charts.core.compile.models.query.normalized import AnyQuery, is_sql_query

HOST_FACTS_VERSION = 1


class _QueryFacts(TypedDict):
    authored: bool
    source: str | None
    ignore: list[str]


class SqlHostQuery(_QueryFacts):
    """A SQL query: `sql`, `setup_sql` and `dialect` as authored, `ref()`/`source()` calls intact."""

    type: Literal["sql"]
    sql: str
    setup_sql: str | None
    dialect: str | None


class DataHostQuery(_QueryFacts):
    """A `values` or `schema` query: it carries no SQL text."""

    type: Literal["values", "schema"]


# One query of the board, placed by a chart or not. A query the author wrote is keyed by name (or
# `<file>#<name>`) and `authored`; one the compile hoisted from inline SQL is keyed by its
# position (`@charts.<pos>.query`).
HostQuery = SqlHostQuery | DataHostQuery


class UnboundChart(TypedDict):
    """A chart that names no channel its family binds by position."""

    chart: str
    type: str
    query: str | None


class HostFacts(TypedDict):
    version: int
    queries: dict[str, HostQuery]
    # Per chart public id: (query, channel label, column), as `chart_column_refs` lists them.
    column_refs: dict[str, list[tuple[str, str, str]]]
    unbound_charts: list[UnboundChart]
    # (field path, link, exempt from anchoring), as `iter_board_links` yields them.
    links: list[tuple[str, str, bool]]
    warnings_ignore: dict[str, list[str]]


def parse_host_facts(text: str) -> HostFacts:
    """The facts `host_facts_json` wrote, or an error naming a version this reader does not know."""
    facts: HostFacts = json.loads(text)
    if facts["version"] != HOST_FACTS_VERSION:
        raise ValueError(
            f"host facts version {facts['version']} is not the version "
            f"{HOST_FACTS_VERSION} this reader knows"
        )
    return facts


def ignored(query: AnyQuery) -> list[str]:
    if query.ignore is None:
        return []
    return list(query.ignore)


def sql_host_queries(registry: Mapping[str, AnyQuery]) -> dict[str, HostQuery]:
    """The SQL queries of a Python-compiled registry, as the facts list them.

    The render path compiles in Python until it switches to the engine; its
    host checks read the same shape `dct validate` reads off the engine.
    """
    return {
        name: {
            "authored": True,
            "type": "sql",
            "source": query.source,
            "ignore": ignored(query),
            "sql": query.sql,
            "setup_sql": query.setup_sql,
            "dialect": query.dialect,
        }
        for name, query in registry.items()
        if is_sql_query(query)
    }


def sql_texts(queries: Mapping[str, HostQuery]) -> dict[str, SqlHostQuery]:
    return {name: query for name, query in queries.items() if query["type"] == "sql"}
