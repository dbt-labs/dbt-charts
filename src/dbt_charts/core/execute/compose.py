"""Compose a planned SQL query into the one thing a backend executes.

Stage: EXECUTE
Purpose: The only place that resolves dbt refs, composes ``{{ queries.X }}``
    references, translates dialect, renders ``setup_sql`` and applies the
    select-only guard. Adapters consume the ``ResolvedQuery`` and re-prepare
    nothing.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from dbt_charts.core.compile.models.board.normalized import Board, VariableValues
from dbt_charts.core.compile.models.query.normalized import (
    AnyQuery,
    SqlQuery,
    is_sql_query,
)
from dbt_charts.core.compile.models.source import SemanticSourceConfig, is_file_source
from dbt_charts.core.compile.sql_guard import validate_select_only, validate_setup_sql
from dbt_charts.core.compile.template.parameterized import (
    render_parameterized_with_queries,
)
from dbt_charts.core.diagnostics.base import DbtChartsError
from dbt_charts.core.dialects import SQLDialect, get_dialect
from dbt_charts.core.execute.dbt_jinja import dbt_macro_kind, has_dbt_jinja
from dbt_charts.core.execute.dbt_utils import (
    DbtRefResolver,
    source_target_path,
)
from dbt_charts.core.execute.metricflow import SemanticLayer
from dbt_charts.core.execute.query_result import (
    QueryParams,
    QueryResult,
    ResolvedRelation,
    RowFetchLimit,
    handle_adapter_error,
    plain_error,
    resolve_effective_row_limit,
    resolve_setup_sql,
)
from dbt_charts.core.execute.sql_literals import (
    INLINE_PLACEHOLDERS,
    inline_params_for_dialect,
)
from dbt_charts.core.execute.sql_translate import (
    dialect_differs,
    dialect_unsupported,
    engine_dialect,
    render_in_dialect,
)
from dbt_charts.core.text.time_zones import UTC, TimeZoneName

if TYPE_CHECKING:
    from dbt_charts.core.compile.models.source import ResolvedSourceConfig
    from dbt_charts.core.project import Project


@dataclass(frozen=True)
class ResolvedQuery:
    """A SQL query with everything client-side done, ready for a backend.

    ``params`` are bindings, written in the placeholder style the composing
    caller asked for; a backend that cannot bind inlines them itself. Relations
    are the dbt refs resolved on the way, kept for provenance. ``time_zone`` is
    the session zone the query runs in: the board's, UTC for a query no board
    owns.
    """

    sql: str
    params: tuple[object, ...]  # type-state: object_annotation — DB-API binds
    setup_sql: tuple[str, ...]
    limit: RowFetchLimit
    time_zone: TimeZoneName
    resolved_relations: tuple[ResolvedRelation, ...] = ()

    def inline(self, dialect_name: str) -> str:
        """The SQL with its inline-style bindings flattened to literals escaped
        for *dialect_name*, for a backend whose driver takes only plain SQL."""
        return inline_params_for_dialect(
            self.sql, list(self.params), INLINE_PLACEHOLDERS, get_dialect(dialect_name)
        )


def _file_source_refs_error(sql: str) -> QueryResult:
    return plain_error(
        f"{dbt_macro_kind(sql)} is not available on file sources: a file "
        "source runs in local DuckDB over its files, which has no dbt "
        "relations. Query the file's table name directly."
    )


def compose(
    query: SqlQuery,
    *,
    variables: VariableValues | None,
    params: QueryParams,
    board: Board | None,
    source_config: ResolvedSourceConfig,
    dialect_name: str,
    render_dialect: SQLDialect,
    refs: DbtRefResolver,
    semantic: SemanticLayer | None,
) -> ResolvedQuery | QueryResult:
    """Resolve, compose, translate, render and guard one SQL query.

    Args:
        query: The planned query.
        variables: Variable values the SQL and ``setup_sql`` render with.
        params: Bindings the caller already rendered the SQL against, or None
            to render here. Pre-rendered SQL cannot be translated.
        board: Supplies the ``{{ queries.X }}`` registry.
        source_config: The query's resolved source.
        dialect_name: The warehouse that parses the final SQL.
        render_dialect: Placeholder style of the SQL and params returned.
        refs: Resolves dbt ``ref()`` / ``source()``.
        semantic: The source's MetricFlow bundle, if it declares one.

    Returns:
        The ``ResolvedQuery``, or the ``QueryResult`` error preparation failed
        with, so one error shape propagates whichever step produced it.
    """
    target_path = source_target_path(source_config)
    queries = board.queries if board is not None else None
    strict = not query.lenient_variables
    bound_variables = variables if variables is not None else {}

    translate = semantic is not None and params is None
    if query.dialect is not None:
        try:
            translate = translate or dialect_differs(query.dialect, source_config)
        except DbtChartsError as exc:
            return handle_adapter_error("Dialect translation", exc)

    relations: list[ResolvedRelation] = []
    if translate:
        if params is not None:
            return handle_adapter_error(
                "Dialect translation",
                dialect_unsupported(
                    query.dialect if query.dialect is not None else source_config.type,
                    source_config.type,
                    "its SQL arrived already rendered with bound parameters",
                ),
            )
        try:
            read = (
                query.dialect
                if query.dialect is not None
                else engine_dialect(source_config.type, source_config)
            )
            sql, rendered_params = render_in_dialect(
                query.sql,
                read,
                engine_dialect(read, source_config),
                bound_variables,
                queries,
                strict,
                refs,
                target_path,
                render_dialect,
                semantic,
            )
        except DbtChartsError as exc:
            return handle_adapter_error("Dialect translation", exc)
        except (ValueError, KeyError, TypeError) as exc:
            return plain_error(str(exc))
    else:
        file_source = is_file_source(source_config)
        if file_source and has_dbt_jinja(query.sql):
            return _file_source_refs_error(query.sql)
        try:
            sql, relations = (
                (query.sql, []) if file_source else refs.resolve(query.sql, target_path)
            )
        except DbtChartsError as exc:
            return handle_adapter_error("dbt ref resolution", exc)
        if params is None:
            try:
                rendered = render_parameterized_with_queries(
                    sql,
                    dict(bound_variables),
                    queries=dict(queries) if queries else None,
                    dialect=render_dialect,
                    strict=strict,
                    warehouse=get_dialect(dialect_name),
                )
            except (ValueError, KeyError, TypeError) as exc:
                return handle_adapter_error("SQL parameterization", exc)
            sql, rendered_params = rendered.sql, list(rendered.params)
            if queries:
                # A composed `{{ queries.X }}` body arrives unresolved.
                if file_source and has_dbt_jinja(sql):
                    return _file_source_refs_error(sql)
                try:
                    sql, composed_relations = (
                        (sql, []) if file_source else refs.resolve(sql, target_path)
                    )
                except DbtChartsError as exc:
                    return handle_adapter_error("dbt ref resolution", exc)
                relations = [*relations, *composed_relations]
        else:
            rendered_params = list(params)

    setup_sql: tuple[str, ...] = ()
    if query.setup_sql:
        resolved_setup = resolve_setup_sql(query.setup_sql, variables)
        if isinstance(resolved_setup, QueryResult):
            return resolved_setup
        setup_sql = (resolved_setup,)

    resolved = ResolvedQuery(
        sql=sql,
        params=tuple(rendered_params),
        setup_sql=setup_sql,
        limit=resolve_effective_row_limit(query.limit),
        time_zone=board.regional.time_zone if board is not None else UTC,
        resolved_relations=tuple(relations),
    )
    try:
        # setup_sql and the main statement have different allowlists, and the
        # guard parses literals: inline-style placeholders are not SQL any
        # engine reads, so they are flattened for the check only.
        for statement in setup_sql:
            validate_setup_sql(statement, dialect=dialect_name)
        validate_select_only(
            resolved.inline(dialect_name)
            if render_dialect is INLINE_PLACEHOLDERS
            else sql,
            dialect=dialect_name,
        )
    except Exception as exc:  # noqa: BLE001 — client-side prep boundary
        # The warehouse was never contacted, so this is not a warehouse
        # rejection; DbtChartsError codes from the guards are preserved.
        return handle_adapter_error(f"{dialect_name} query setup", exc)

    return resolved


def semantic_layer(
    project: Project, source_config: ResolvedSourceConfig
) -> (
    SemanticLayer | None
):  # type-state: optional — None is a source that declares no bundle
    """The source's MetricFlow bundle, loaded; None when it declares none.

    Reloaded when the bundle's files change, reused otherwise.

    Raises:
        ExecutionError: the library is not installed, or the bundle is
            invalid.
    """
    if (
        not isinstance(source_config, SemanticSourceConfig)
        or source_config.metricflow is None
    ):
        return None
    return SemanticLayer.load(project, source_config.metricflow)


def compose_sql(
    query: SqlQuery,
    variables: VariableValues | None,
    params: QueryParams,
    board: Board | None,
    source_config: ResolvedSourceConfig,
    render_dialect: SQLDialect,
    *,
    refs: DbtRefResolver,
    project: Project,
) -> ResolvedQuery | QueryResult:
    """``compose`` for the source's own dialect, loading its MetricFlow bundle."""
    try:
        semantic = semantic_layer(project, source_config)
    except DbtChartsError as exc:
        return handle_adapter_error("MetricFlow", exc)
    return compose(
        query,
        variables=variables,
        params=params,
        board=board,
        source_config=source_config,
        dialect_name=source_config.type,
        render_dialect=render_dialect,
        refs=refs,
        semantic=semantic,
    )


def compose_query(
    query: AnyQuery,
    *,
    board: Board,
    variables: VariableValues,
    source_config: ResolvedSourceConfig,
    refs: DbtRefResolver,
    project: Project,
) -> ResolvedQuery | QueryResult:
    """The query a run would send, composed but not sent.

    Same composition a run does, stopping at the connection, with params in
    the inline style. This is what a caller needs when the warehouse itself
    offers a way to inspect a query it never runs, such as a BigQuery dry run.
    The caller resolves the source, which it has to know anyway to decide that
    a dry run is the right mechanism.
    """
    if not is_sql_query(query):
        return plain_error(f"Expected SQL query, got {query.query_type}")
    return compose_sql(
        query,
        variables,
        None,
        board,
        source_config,
        INLINE_PLACEHOLDERS,
        refs=refs,
        project=project,
    )


def translate_query(
    query: SqlQuery,
    *,
    board: Board,
    variables: VariableValues,
    source_config: ResolvedSourceConfig,
    refs: DbtRefResolver,
    project: Project,
) -> SqlQuery | QueryResult:
    """The query as its source's engine would receive it, values inlined.

    For a caller that must put text of its own around the SQL, like a
    ``EXPLAIN`` prefix, and so cannot let a run translate afterwards. The query
    must carry a ``dialect:``; the result carries none. One already in its
    source's dialect comes back unchanged. A translation failure comes back as
    the error a run would have returned.
    """
    assert query.dialect is not None
    try:
        differs = dialect_differs(query.dialect, source_config)
    except DbtChartsError as exc:
        return handle_adapter_error("Dialect translation", exc)
    if not differs:
        return query
    if is_file_source(source_config):
        return file_source_dialect_refusal(query.dialect)
    resolved = compose_query(
        query,
        board=board,
        variables=variables,
        source_config=source_config,
        refs=refs,
        project=project,
    )
    if isinstance(resolved, QueryResult):
        return resolved
    sql = resolved.inline(engine_dialect(query.dialect, source_config))
    return query.model_copy(update={"sql": sql, "dialect": None})


def file_source_dialect_refusal(dialect: str) -> QueryResult:
    return handle_adapter_error(
        "File-source execution",
        dialect_unsupported(
            dialect,
            "duckdb",
            "queries on a file source are not translated, so write the SQL in duckdb",
        ),
    )
