"""Typed query verbs for the agent API."""

import sys
from pathlib import Path, PurePosixPath
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, model_validator

if sys.version_info >= (3, 11):
    from typing import Self
else:
    from typing_extensions import Self

from dbt_charts.agent_api._paths import resolve_board_path
from dbt_charts.core.compile import compile_file
from dbt_charts.core.compile.models.query.normalized import SqlQuery
from dbt_charts.core.execute.adapters import AdapterRegistry
from dbt_charts.core.inspect.query_validator import QueryDiagnostic, validate_query
from dbt_charts.core.project import Project
from dbt_charts.core.validate import normalize_data_for_json

MAX_QUERY_LIMIT = 1000


class VariableBinding(BaseModel):
    """One variable binding in a tool call: a name–value pair.

    Used in the ``variables`` / ``vars`` fields of ``render_board``,
    ``execute_query``, and ``query_board`` instead of an open-keyed dict, so
    that OpenAI strict mode can validate the full tool-call structure.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    name: str = Field(..., description="Variable name, e.g. 'region'")
    value: str | int | float | bool = Field(
        ..., description="Scalar value for the variable"
    )


def variables_to_dict(
    bindings: list[VariableBinding],
) -> dict[str, str | int | float | bool]:
    """Convert a VariableBinding list to a dict, raising on duplicate names."""
    result: dict[str, str | int | float | bool] = {}
    for binding in bindings:
        if binding.name in result:
            raise ValueError(f"Duplicate variable name: {binding.name!r}")
        result[binding.name] = binding.value
    return result


class BoardQueryLookupResult(BaseModel):
    """Result of extracting SQL from a named board query for offline use (validate/describe)."""

    model_config = ConfigDict(frozen=True)

    success: bool
    sql: str = ""
    source: str | None = None
    errors: list[str] = []
    available_queries: list[str] = []


def lookup_board_query_sql(
    name: str,
    path: Path,
    *,
    project: Project,
    vars: dict[str, Any] | None = None,
) -> BoardQueryLookupResult:
    """Compile a board file, render the SQL template, and return it for offline use.

    Expands ``{{ queries.X }}``, resolves dbt ``ref()``/``source()`` against the
    project's manifest, and substitutes variables, so callers receive valid SQL
    rather than a raw Jinja template.  Used by validate and describe paths that
    need the rendered SQL text without executing it against a warehouse — the
    manifest is a file read, so this opens no connection.
    """
    from dbt_charts.core.compile.errors import JinjaError
    from dbt_charts.core.compile.template.parameterized import (
        render_parameterized_with_queries,
    )
    from dbt_charts.core.diagnostics.execution import ExecutionError
    from dbt_charts.core.execute.adapters.dbt_utils import DbtRefResolver
    from dbt_charts.core.execute.executor import resolve_query_references

    try:
        file_path = resolve_board_path(path, project)
    except ValueError as e:
        return BoardQueryLookupResult(success=False, errors=[str(e)])

    if not file_path.exists():
        return BoardQueryLookupResult(
            success=False, errors=[f"File not found: {file_path.relpath}"]
        )

    compile_result = compile_file(file_path.read_board())
    if not compile_result.success:
        return BoardQueryLookupResult(
            success=False, errors=[e.message for e in compile_result.errors]
        )

    board = compile_result.board
    assert board is not None  # compile_result.success guarantees this

    if name not in board.queries:
        return BoardQueryLookupResult(
            success=False,
            errors=[f"Unknown query: '{name}'"],
            available_queries=sorted(board.queries.keys()),
        )

    query = board.queries[name]
    if not isinstance(query, SqlQuery):
        return BoardQueryLookupResult(
            success=False,
            errors=[
                f"Query '{name}' is not a SQL query (type: {type(query).__name__})."
            ],
        )

    # The same coercion and dialect execution uses, so the preview shows the
    # SQL the warehouse would get. `dialect_for_source` answers None for a
    # source whose warehouse is only knowable at execute (`dbt_profile`, a
    # file source); this preview then falls to the default dialect rather than
    # refusing.
    from dbt_charts.core.compile.normalize.queries import dialect_for_source
    from dbt_charts.core.dialects import get_dialect
    from dbt_charts.core.execute.executor import merge_board_variables

    source_type = dialect_for_source(query.source, project.sources.sources)
    warehouse = get_dialect(source_type) if source_type else None
    dbt_refs = DbtRefResolver(project)
    try:
        merged_vars = merge_board_variables(board, vars or {})
        # {{ queries.X }} is expanded to text first, recursively, exactly as the
        # render/serve path does (Executor.execute_query Step 4) — don't move
        # this after the two resolve calls below; it's what lets a template
        # left inside X's own SQL (a plain variable, `filter()`, a literal) reach
        # the single variable render instead of never.
        expanded_query = resolve_query_references(
            query,
            all_queries={**board.queries, **compile_result.query_registry},
            query_name=name,
        )
        assert isinstance(expanded_query, SqlQuery)
        resolved_sql, _relations = dbt_refs.resolve(expanded_query.sql)
        rendered = render_parameterized_with_queries(
            resolved_sql,
            merged_vars,
            queries=board.queries,
            strict=not query.lenient_variables,
            warehouse=warehouse,
        )
        # Same second resolve pass execution's own SqlAdapter.prepare_sql runs:
        # `resolve_query_references`'s dependency-graph short-circuit only
        # recognizes the spaced `{{ queries.` spelling, so a `{{queries.X}}`
        # reference reaches `render_parameterized_with_queries` unexpanded and
        # is inlined there instead — carrying any `ref()` call X's own SQL had.
        # Do not remove this as redundant with the first resolve above.
        composed_sql, _inlined_relations = dbt_refs.resolve(rendered.sql)
    except (JinjaError, ExecutionError) as exc:
        return BoardQueryLookupResult(success=False, errors=[str(exc)])

    return BoardQueryLookupResult(success=True, sql=composed_sql, source=query.source)


class ExecuteQueryArgs(BaseModel):
    """Execute a SQL query against the project's data sources and return results. Use {{ variable_name }} for parameterized values — these work identically in dashboard YAML, so queries you test here will be cached when reused in dashboards. Great for exploring data shape before writing chart configs. Return values in natural units — never scale for display (no /1000, no _k/_m columns); compact display belongs to the chart's number format (e.g. currency)."""

    sql: str = Field(
        ...,
        description="SQL query to execute. Use {{ variable_name }} for parameterized values — these work identically in dashboard YAML, so queries you test here will be cached when reused in dashboards.",
    )
    variables: list[VariableBinding] | None = Field(
        None,
        description=(
            "Variable values to substitute, as an array of {name, value} pairs. "
            'Example: [{"name": "region", "value": "US"}, {"name": "year", "value": 2024}]'
        ),
    )
    source: str | None = Field(None, description="Data source name to execute against")
    limit: int | None = Field(None, description="Maximum rows to return (default 50)")
    lenient_variables: bool = Field(
        False,
        description="When True, undefined Jinja variables degrade gracefully (useful for iterative chart editing where not all variables are set).",
    )
    # Display only — never reaches execute_query(). Ad-hoc SQL is the one query
    # surface with no author to describe it: a named board query carries the
    # `notes:` its YAML already declares, and this is the equivalent for
    # a query that exists only for the length of one tool call.
    description: str | None = Field(
        None,
        description="Short phrase naming what this query is for (e.g. 'Leads by industry'), shown to the user in place of the raw SQL.",
    )

    @model_validator(mode="after")
    def _no_duplicate_variable_names(self) -> Self:
        if self.variables is not None:
            variables_to_dict(self.variables)
        return self


class ExecuteQueryResult(BaseModel):
    model_config = ConfigDict(frozen=True)

    success: bool
    columns: list[str]
    data: list[dict[str, Any]]
    errors: list[str]
    row_count: int
    truncated: bool
    diagnostics: list[QueryDiagnostic] = Field(
        default_factory=list,
        description=(
            "Deterministic validate_query findings (WARN-FANOUT-RISK, "
            "WARN-MISSING-JOIN-PREDICATE, WARN-REAGGREGATION, WARN-PARSE-ERROR). "
            "Surfaced as warnings — the query still executes. A clean query yields []."
        ),
    )


def _query_diagnostics(
    sql: str, source: str | None, adapter_registry: AdapterRegistry
) -> list[QueryDiagnostic]:
    """Run the structural validator over *sql*; never raise on a real query.

    Resolves the source dialect when possible so dialect-specific parsing is
    correct, but a validator or resolution failure degrades to no diagnostics —
    validation must never block returning real results.
    """
    try:
        dialect: str | None = None
        try:
            dialect = adapter_registry.resolve_source_config(source).get("type")
        except Exception:  # noqa: BLE001 — best-effort dialect hint
            dialect = None
        return validate_query(sql, dialect=dialect)
    except Exception:  # noqa: BLE001 — diagnostics are advisory, never fatal
        return []


def execute_query(
    sql: str,
    variables: dict[str, Any] | None = None,
    source: str | None = None,
    limit: int = 50,
    *,
    adapter_registry: AdapterRegistry,
    lenient_variables: bool = False,
) -> ExecuteQueryResult:
    """Execute a SQL query and return the results.

    Runs SQL directly against the configured data sources. Use {{ variable_name }}
    syntax for parameterized values — these work identically in dashboard YAML,
    so queries tested here will be cached when reused in dashboards.
    """
    limit = min(limit, MAX_QUERY_LIMIT)
    fetch_limit = limit + 1

    # Structural validation runs regardless of execution outcome: a query that
    # executes cleanly can still double-count (fanout) or build a cartesian
    # product, which execution alone never flags.
    diagnostics = _query_diagnostics(sql, source, adapter_registry)

    try:
        # A sourceless query raises ERR-NO-DEFAULT-SOURCE inside
        # adapter_registry.execute() itself — the single enforcement point.
        query = SqlQuery(
            sql=sql,
            source=source,
            limit=fetch_limit,
            lenient_variables=lenient_variables,
        )

        result = adapter_registry.execute(query, variables=variables)

        if result.error:
            return ExecuteQueryResult(
                success=False,
                data=[],
                columns=[],
                errors=[result.error],
                row_count=0,
                truncated=False,
                diagnostics=diagnostics,
            )

        data = normalize_data_for_json(result.data)
        columns = list(data[0].keys()) if data else []
        truncated = len(data) > limit
        data = data[:limit]

        return ExecuteQueryResult(
            success=True,
            data=data,
            columns=columns,
            errors=[],
            row_count=len(data),
            truncated=truncated,
            diagnostics=diagnostics,
        )

    except Exception as e:  # noqa: BLE001
        return ExecuteQueryResult(
            success=False,
            data=[],
            columns=[],
            errors=[str(e)],
            row_count=0,
            truncated=False,
            diagnostics=diagnostics,
        )


class QueryBoardArgs(BaseModel):
    """Run one named query from a board YAML file and return its columns and sample rows. Unlike execute_query (which takes raw SQL), query_board runs a query by name from the board the user actually authored — including Jinja variable substitution and support for non-SQL query types (values, http). Use this to inspect what a named query produces when debugging chart errors like 'unknown column foo'."""

    name: str = Field(
        ..., description="Name of the query inside the board's `queries:` block"
    )
    path: Path = Field(..., description="Path to the board YAML file")
    vars: list[VariableBinding] | None = Field(
        None,
        description=(
            "Variable overrides as an array of {name, value} pairs. "
            'Example: [{"name": "country", "value": "FR"}]'
        ),
    )
    limit: int = Field(
        20, ge=1, le=1000, description="Max rows to return (default 20, max 1000)"
    )

    model_config = ConfigDict(extra="forbid")

    @model_validator(mode="after")
    def _no_duplicate_variable_names(self) -> Self:
        if self.vars is not None:
            variables_to_dict(self.vars)
        return self


class QueryBoardResult(BaseModel):
    model_config = ConfigDict(frozen=True)

    success: bool
    name: str
    path: PurePosixPath
    query_type: str | None = None
    sql: str | None = None
    #: The query's authored `notes:`, when its YAML declares one. Lets a
    #: caller label the run with what it is for rather than its identifier.
    notes: str | None = None
    columns: list[str] = []
    data: list[dict[str, Any]] = []
    row_count: int = 0
    truncated: bool = False
    errors: list[str] = []
    available_queries: list[str] = []


def _fail(
    name: str,
    path: PurePosixPath,
    errors: list[str],
    available: list[str] | None = None,
    sql: str | None = None,
    notes: str | None = None,
) -> QueryBoardResult:
    """A failure reports the query it was running whenever it got far enough to
    know it: an undefined-variable error is unreadable without the SQL holding
    the `{{ }}` that went unbound. Failures before the query is looked up
    (unresolvable path, compile error, unknown name) have neither to report."""
    return QueryBoardResult(
        success=False,
        name=name,
        path=path,
        sql=sql,
        notes=notes,
        errors=errors,
        available_queries=available or [],
    )


def query_board(
    name: str,
    path: Path,
    project: Project,
    vars: dict[str, Any] | None = None,
    limit: int = 20,
    *,
    adapter_registry: AdapterRegistry,
) -> QueryBoardResult:
    """Run one named query from a compiled board and return its sample rows."""
    from dbt_charts.core.execute.executor import resolve_query_references

    limit = min(limit, MAX_QUERY_LIMIT)

    try:
        file_path = resolve_board_path(path, project)
    except ValueError as e:
        # Raw-input echo — resolution failed, so this is the caller's rejected
        # path, not a validated project-relative identity.
        return _fail(name, PurePosixPath(path), [str(e)])

    resolved_display = PurePosixPath(file_path.relpath)
    if not file_path.exists():
        return _fail(name, resolved_display, [f"File not found: {resolved_display}"])

    compile_result = compile_file(file_path.read_board())
    if not compile_result.success:
        return _fail(name, resolved_display, [e.message for e in compile_result.errors])

    board = compile_result.board
    assert board is not None  # compile_result.success guarantees this
    if name not in board.queries:
        return _fail(
            name,
            resolved_display,
            [f"Unknown query: '{name}'"],
            sorted(board.queries.keys()),
        )

    merged_vars = {**board.variable_defaults, **(vars or {})}
    query = board.queries[name].model_copy(update={"limit": limit + 1})
    sql = query.sql if isinstance(query, SqlQuery) else None
    noted = query.notes

    # A SQL query against a file source runs through adapter_registry.execute's
    # own materializer dispatch (or its refusal, when no materializer is
    # configured for this registry) and surfaces through exec_result.error
    # below — one dispatch point, not a second copy here.
    try:
        if isinstance(query, SqlQuery):
            # AdapterRegistry._compose_query_refs inlines {{ queries.X }} in a
            # single non-recursive Jinja pass — don't remove this call as
            # redundant with it. Expand refs to text first, exactly as the
            # render/serve path does (Executor.execute_query Step 4), so
            # composition below sees one flat template with nothing left to
            # inline.
            query = resolve_query_references(
                query,
                all_queries={**board.queries, **compile_result.query_registry},
                query_name=name,
            )
        exec_result = adapter_registry.execute(
            query, variables=merged_vars or None, board=board, query_name=name
        )
    except Exception as e:  # noqa: BLE001
        return _fail(name, resolved_display, [str(e)], sql=sql, notes=noted)

    if exec_result.error:
        return _fail(
            name,
            resolved_display,
            [exec_result.error],
            sql=sql,
            notes=noted,
        )

    data = normalize_data_for_json(exec_result.data)
    truncated = len(data) > limit
    data = data[:limit]

    return QueryBoardResult(
        success=True,
        name=name,
        path=resolved_display,
        query_type=query.query_type,
        sql=sql,
        notes=noted,
        columns=list(data[0].keys()) if data else [],
        data=data,
        row_count=len(data),
        truncated=truncated,
    )
