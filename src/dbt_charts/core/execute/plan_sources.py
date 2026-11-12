"""Source resolution: which source a query runs on, and what the project declares.

Stage: EXECUTE (plan)
Purpose: Functions over a registry's data (``SourceRegistry``, resolver, dbt
    context, project) that name the source a query will execute against and list
    or read the sources the project declares.

Entry Points:
    - resolve_query_source(query, sources, resolver, dbt_context) -> source | None
    - resolve_source_config(sources, project, source) -> dict
    - list_sql_sources(sources) -> list[dict]
"""

from typing import TYPE_CHECKING, Any

from dbt_charts.core.compile.config import ProjectSourcesConfig
from dbt_charts.core.compile.models.query.normalized import (
    AnyQuery,
    ValuesQuery,
    is_sql_query,
)
from dbt_charts.core.compile.models.source import (
    DbtProfileSourceConfig,
)
from dbt_charts.core.diagnostics.base import DbtChartsError
from dbt_charts.core.diagnostics.codes_compile import ERR_SOURCE_NOT_FOUND
from dbt_charts.core.diagnostics.codes_execute import (
    ERR_SOURCE_NOT_FOUND_EMPTY,
    ERR_SOURCE_NOT_NAMED,
)
from dbt_charts.core.execute.dbt_jinja import has_dbt_jinja
from dbt_charts.core.execute.source_registry import SourceRegistry
from dbt_charts.core.execute.source_resolver import (
    AD_HOC_QUERY_NAME,
    DbtContext,
    SourceResolver,
)
from dbt_charts.core.project import Project
from dbt_charts.core.project_data_files import unknown_source_advice

if TYPE_CHECKING:
    from dbt_charts.core.compile.models.board.normalized import Board
    from dbt_charts.core.compile.models.source import ResolvedSourceConfig


RawSource = dict[str, Any]  # type-state: explicit_any — authored source config


def effective_source(sources: SourceRegistry, source: str | None) -> str | None:
    """*source*, or the project's implicit dbt source when it names none.

    The one rule for SQL that names no source; None when there is neither.
    """
    return source if source is not None else sources.runtime_config().implicit_source


def list_sql_sources(
    sources: SourceRegistry,
) -> list[RawSource]:
    """Return configured SQL sources from the project source registry."""
    listed: dict[str, RawSource] = {}
    for name, config in sources.all().items():
        source_type = str(
            config.get("type", "unknown")  # type-state: silent_fallback — label only
        )
        source_info: RawSource = {
            "name": name,
            "type": source_type,
        }
        db_path = config.get("path")
        if db_path:
            source_info["path"] = str(db_path)
        if source_type == "duckdb" and str(db_path) == ":memory:":
            source_info["in_memory"] = True
        listed[name] = source_info
    return [listed[name] for name in sorted(listed)]


def source_not_named_error(sources: SourceRegistry) -> DbtChartsError:
    """Build (not raise) ERR-SOURCE-NOT-NAMED, the single message for "source is
    None and there is no default to fall back to". Shared by
    ``resolve_source_config(None)`` (raises it) and the sourceless-SQL guard in
    ``run_query`` (returns it as a QueryResult error).
    """
    return DbtChartsError.from_code(
        ERR_SOURCE_NOT_NAMED,
        available=[s["name"] for s in list_sql_sources(sources)],
    )


def resolve_source_config(
    sources: SourceRegistry, project: Project, source: str | None = None
) -> RawSource:
    """Resolve a full source_config dict for use with InspectConnection.

    Returns the warehouse-specific dict (type + credentials).

    Args:
        sources: The project's sources, plus any registered at runtime.
        project: Names the files an unknown source's advice points at.
        source: Named source profile. Every query must name a configured
            source; None falls back only to the project's implicit dbt
            source (``effective_source``).

    Returns:
        source_config dict with at least a 'type' key.

    Raises:
        DbtChartsError: ERR-SOURCE-NOT-NAMED if source is None and there
            is no implicit source;
            ERR-SOURCE-NOT-FOUND if the named source is missing but
            others are configured; ERR-SOURCE-NOT-FOUND-EMPTY if no
            sources are configured.
    """
    source = effective_source(sources, source)
    if source is None:
        raise source_not_named_error(sources)

    config = sources.get(source)
    if config and config.get("type"):
        return config

    available = [item["name"] for item in list_sql_sources(sources)]
    advice = unknown_source_advice(project, source)
    if available:
        raise DbtChartsError.from_code(
            ERR_SOURCE_NOT_FOUND,
            query_name=AD_HOC_QUERY_NAME,
            source=source,
            available=available,
            advice=advice,
        )
    raise DbtChartsError.from_code(
        ERR_SOURCE_NOT_FOUND_EMPTY, source=source, advice=advice
    )


def resolve_query_source(
    query: AnyQuery,
    *,
    sources: SourceRegistry,
    resolver: SourceResolver,
    dbt_context: DbtContext | None,
    board: "Board | None" = None,
    query_name: str = AD_HOC_QUERY_NAME,
) -> "ResolvedSourceConfig | None":
    """Resolve the source a query will actually execute against.

    ``resolve_source_config`` consults project ``sources:`` only. A source
    declared board-level, or reachable only through a dbt profile, is
    invisible to it, so anything that needs to know where a query will run
    (execution, the ``--warehouse`` check) must come through here instead.

    Returns None for a source-less non-SQL query (inline values,
    schema list-all): those have no connection and must not be gated on one.

    SQL that names no source runs against the project's implicit dbt source
    when it has one (``implicit_dbt_source``).

    SQL whose source name resolves to nothing, in a project with a dbt
    project and no ``sources:``, runs on the default source
    (:func:`default_sql_source`): never None.

    Raises:
        DbtChartsError: ERR-SOURCE-NOT-NAMED when SQL names no source and
            there is no implicit one to fall back to, or
            whatever the resolver raises for an unresolvable name.
    """
    return resolve_authored_source(
        None if isinstance(query, ValuesQuery) else query.source,
        query.sql if is_sql_query(query) else None,
        sources=sources,
        project_sources=sources.runtime_config(),
        resolver=resolver,
        dbt_context=dbt_context,
        board_sources=board.sources if board is not None else {},
        query_name=query_name,
    )


def resolve_authored_source(
    authored: str | None,
    sql: str | None,
    *,
    sources: SourceRegistry,
    project_sources: ProjectSourcesConfig,
    resolver: SourceResolver,
    dbt_context: DbtContext | None,
    board_sources: dict[str, dict[str, Any]],  # type-state: explicit_any — raw
    query_name: str,
) -> "ResolvedSourceConfig | None":
    """:func:`resolve_query_source` over the query's fields: its authored source
    name, and its ``sql`` (None for a query that is not SQL)."""
    if authored is None:
        if sql is None:
            return None
        authored = effective_source(sources, None)
        if authored is None:
            raise source_not_named_error(sources)
    resolved = resolver.resolve(
        authored=authored,
        board_sources=board_sources,
        project_sources=project_sources,
        dbt_context=dbt_context,
        query_name=query_name,
    )
    if resolved is None and sql is not None:
        return default_sql_source(
            sql, project_sources, resolver, board_sources, dbt_context, query_name
        )
    return resolved


def default_sql_source(
    sql: str,
    project_sources: ProjectSourcesConfig,
    resolver: SourceResolver,
    board_sources: dict[str, dict[str, Any]],  # type-state: explicit_any — raw
    dbt_context: DbtContext | None,
    query_name: str,
) -> "ResolvedSourceConfig":
    """The source SQL runs on when its authored one defers to the dbt project.

    The one rule: SQL using dbt Jinja (``ref()``/``source()``) runs on the dbt
    target, resolved like a ``dbt_profile`` source; any other SQL runs on the
    project's local DuckDB warehouse. Nothing after resolution re-decides it.

    Raises:
        DbtChartsError: a ``dbt_profile`` / local warehouse resolution
            failure.
    """
    # duckdb_local imports the adapters package, which imports this module.
    from dbt_charts.core.execute.backends.duckdb_local import default_duckdb_source

    assert dbt_context is not None, (
        "a resolver deferred a SQL query's source with no dbt project linked"
    )
    if has_dbt_jinja(sql):
        resolved = resolver.resolve(
            authored=DbtProfileSourceConfig(type="dbt_profile"),
            board_sources=board_sources,
            project_sources=project_sources,
            dbt_context=dbt_context,
            query_name=query_name,
        )
        assert resolved is not None  # a source config always resolves
        return resolved
    return default_duckdb_source(dbt_context.dbt_project_path)
