"""Compose, resolve and run one query of a ``QueryPlan``, by name.

Stage: EXECUTE
Purpose: The entry points a caller holding a plan uses to compose and translate a
    query: the same composition a run does, read off the plan's entries instead
    of compiled query models. Source resolution and the run are
    ``plan_reader.resolve_planned_source`` and ``run.run_planned``. The plan carries the SQL, the variable
    values, the time zone and the sources; nothing here reads a board.

    The plan holds source declarations, which may carry connection settings or
    credentials. It stays in memory: nothing here writes it, or any declaration
    in it, to a log, an error message or a cache key.

Entry Points:
    - compose_query(plan, name, ...) -> ResolvedQuery | QueryResult
    - translate_query(plan, name, ...) -> ResolvedQuery | QueryResult
    - plan_board(compiled, variables, ...) -> QueryPlan
"""

import json
from dataclasses import replace
from datetime import datetime

from dbt_charts import _engine
from dbt_charts.core.compile.models.board.normalized import VariableValues
from dbt_charts.core.compile.models.source import (
    ResolvedSourceConfig,
    is_file_source,
)
from dbt_charts.core.compile.template.variables import parse_variable_json_strings
from dbt_charts.core.diagnostics.base import DbtChartsError
from dbt_charts.core.diagnostics.execution import ExecutionError
from dbt_charts.core.diagnostics.registry import REGISTRY, ErrorCode
from dbt_charts.core.execute.compose import (
    ResolvedQuery,
    compose_file_source,
    compose_sql,
    file_source_dialect_refusal,
)
from dbt_charts.core.execute.dbt_utils import DbtRefResolver
from dbt_charts.core.execute.plan_reader import compose_fields
from dbt_charts.core.execute.query_plan import (
    PlannedSqlQuery,
    QueryPlan,
    parse_query_plan,
)
from dbt_charts.core.execute.query_result import (
    QueryResult,
    handle_adapter_error,
    plain_error,
    resolve_effective_row_limit,
)
from dbt_charts.core.execute.sql_literals import INLINE_PLACEHOLDERS
from dbt_charts.core.execute.sql_translate import dialect_differs, engine_dialect
from dbt_charts.core.project import Project


def _sql_entry(plan: QueryPlan, name: str) -> PlannedSqlQuery | QueryResult:
    entry = plan["queries"][name]
    if entry["type"] != "sql":
        return plain_error(f"Expected SQL query, got {entry['type']}")
    return entry


def plan_board(
    compiled: _engine.CompiledBoard,
    variables: VariableValues | None,
    *,
    builtins: VariableValues | None,
    now: datetime,
    strict: bool,
    active_tab_only: bool,
    only_charts: list[str] | None,
    all_queries: bool = False,
) -> QueryPlan:
    """The plan of *compiled* under the caller's *variables*, at the instant *now*.

    *variables* are what the caller set (JSON strings parsed as every caller does);
    *builtins* are host values echoed untagged into the plan. *strict* refuses a
    variable the board does not declare. *only_charts* names the charts to plan
    (their queries, the board's text queries and the variables they depend on);
    `None` plans the whole board.

    Raises:
        ExecutionError: The engine refuses the values, carrying its code.
    """
    given = dict(variables) if variables else {}
    host_values = dict(builtins) if builtins else {}
    try:
        text = _engine.plan(
            compiled,
            json.dumps(parse_variable_json_strings(given), default=str),
            json.dumps(host_values, default=str),
            int(now.timestamp()),
            all_queries,
            active_tab_only,
            strict,
            only_charts,
        )
    except _engine.RenderDiagnosticError as refusal:
        registered = REGISTRY.get(refusal.code)
        if not isinstance(registered, ErrorCode):
            raise TypeError(
                f"engine refused with {refusal.code}, which is not an error code"
            ) from refusal
        raise ExecutionError.from_code(registered, **refusal.params) from refusal
    return parse_query_plan(text)


def compose_query(
    plan: QueryPlan,
    name: str,
    *,
    source_config: ResolvedSourceConfig,
    refs: DbtRefResolver,
    project: Project,
) -> ResolvedQuery | QueryResult:
    """The query *name* of *plan* as a run would send it, composed but not sent.

    Same composition a run does, stopping at the connection, with params in the
    inline style: variables bind the plan's query values, ``{{ queries.X }}``
    expands from the plan's entries, and the setup_sql chain comes from the
    plan. The caller resolves the source, which it has to know anyway to decide
    that a dry run is the right mechanism. A query that is not SQL comes back as
    the error result.

    Raises:
        KeyError: The plan holds no query *name*.
    """
    entry = _sql_entry(plan, name)
    if isinstance(entry, QueryResult):
        return entry
    fields = compose_fields(
        plan, name, entry, strict=True, limit=resolve_effective_row_limit(None)
    )
    if is_file_source(source_config):
        return compose_file_source(
            params=None,
            source_config=source_config,
            render_dialect=INLINE_PLACEHOLDERS,
            refs=refs,
            **fields,
        )
    return compose_sql(
        params=None,
        source_config=source_config,
        render_dialect=INLINE_PLACEHOLDERS,
        refs=refs,
        project=project,
        **fields,
    )


def translate_query(
    plan: QueryPlan,
    name: str,
    *,
    source_config: ResolvedSourceConfig,
    refs: DbtRefResolver,
    project: Project,
) -> ResolvedQuery | QueryResult:
    """The query as its source's engine would receive it, values inlined (``params == ()``).

    For a caller that must put text of its own around the SQL, like an
    ``EXPLAIN`` prefix, and so cannot let a run translate afterwards. The query
    must carry a ``dialect:``. A translation failure comes back as the error a
    run would have returned.

    Raises:
        KeyError: The plan holds no query *name*.
    """
    entry = _sql_entry(plan, name)
    if isinstance(entry, QueryResult):
        return entry
    dialect = entry["dialect"]
    assert dialect is not None
    try:
        differs = dialect_differs(dialect, source_config)
    except DbtChartsError as exc:
        return handle_adapter_error("Dialect translation", exc)
    if differs and is_file_source(source_config):
        return file_source_dialect_refusal(dialect)
    composed = compose_query(
        plan, name, source_config=source_config, refs=refs, project=project
    )
    if isinstance(composed, QueryResult):
        return composed
    return replace(
        composed, sql=composed.inline(engine_dialect(dialect, source_config)), params=()
    )
