"""Typed query verbs for the agent API."""

import json
from dataclasses import dataclass, field
from pathlib import Path, PurePosixPath
from typing import Any, Literal, Self

from pydantic import BaseModel, ConfigDict, Field, model_validator

from dbt_charts.agent_api._paths import (
    resolution_error_diagnostic,
    resolve_board_path,
)
from dbt_charts.core.compile import compile_file
from dbt_charts.core.compile.models.board.normalized import Board
from dbt_charts.core.compile.models.query.normalized import AnyQuery, SqlQuery
from dbt_charts.core.compile.normalize.queries import is_file_source_ref
from dbt_charts.core.compile.sql_guard import source_dialect
from dbt_charts.core.compile.template.daterange import board_clock
from dbt_charts.core.compile.template.variables import (
    check_variable_overrides,
    variables_from_query_pairs as variables_from_query_pairs,
)
from dbt_charts.core.diagnostics.base import DbtChartsError
from dbt_charts.core.diagnostics.codes_compile import (
    ERR_FILE_NOT_FOUND,
)
from dbt_charts.core.diagnostics.codes_execute import (
    ERR_BOARD_FILE_UNREADABLE,
    ERR_BOARD_QUERY_NOT_FOUND,
)
from dbt_charts.core.diagnostics.codes_unknown import ERR_INTERNAL
from dbt_charts.core.diagnostics.diagnostic import Diagnostic
from dbt_charts.core.diagnostics.execution import ExecutionError
from dbt_charts.core.execute.adapters import AdapterRegistry
from dbt_charts.core.inspect.query_validator import QueryDiagnostic, validate_query
from dbt_charts.core.project import InMemoryBoard, Project
from dbt_charts.core.validate import normalize_data_for_json

MAX_QUERY_LIMIT = 1000


def error_code_of(error: BaseException) -> str:
    """The registered code of *error*, or ``ERR-INTERNAL`` if nothing classified it.

    ``ERR-INTERNAL`` here means a defect: every failure a caller can cause
    carries its own code by the time it is raised.
    """
    code = error.code if isinstance(error, DbtChartsError) else None
    return (code or ERR_INTERNAL).code


def _file_missing(path: PurePosixPath) -> str:
    return str(DbtChartsError.from_code(ERR_FILE_NOT_FOUND, path=path))


def _board_unreadable(path: PurePosixPath) -> str:
    return str(DbtChartsError.from_code(ERR_BOARD_FILE_UNREADABLE, path=path))


def _query_not_found(path: PurePosixPath, name: str, available: list[str]) -> str:
    return str(
        DbtChartsError.from_code(
            ERR_BOARD_QUERY_NOT_FOUND, path=path, query_name=name, available=available
        )
    )


class VariableBinding(BaseModel):
    """One variable binding in a tool call: a name–value pair.

    Used in the ``variables`` field of ``render_board`` and ``execute_sql``
    instead of an open-keyed dict, so that OpenAI strict mode can validate the
    full tool-call structure.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    name: str = Field(..., description="Variable name, e.g. 'region'")
    value: str | int | float | bool | list[str] = Field(
        ...,
        description=(
            "Value for the variable: a scalar, or a list of strings for a "
            "multiselect ([] selects none)"
        ),
    )


def variables_to_dict(
    bindings: list[VariableBinding],
) -> dict[str, str | int | float | bool | list[str]]:
    """Convert a VariableBinding list to a dict, raising on duplicate names."""
    result: dict[str, str | int | float | bool | list[str]] = {}
    for binding in bindings:
        if binding.name in result:
            raise ValueError(f"Duplicate variable name: {binding.name!r}")
        result[binding.name] = binding.value
    return result


@dataclass(frozen=True)
class BoardFailure:
    path: PurePosixPath
    errors: list[str]
    error_code: str
    available: list[str] = field(default_factory=list)


@dataclass(frozen=True)
class LoadedBoard:
    path: PurePosixPath
    board: Board
    query_registry: dict[str, AnyQuery]
    default_source: str | None
    # Root queries plus every nested board's, resolved as the executor does.
    all_queries: dict[str, AnyQuery]


def load_board(path: Path, project: Project) -> LoadedBoard | BoardFailure:
    """Resolve, read and compile the board at *path*.

    The one prelude of ``query_board``, ``lookup_board_query_sql`` and
    ``execute_board_sql``. Only the board's own text is read under the
    unreadable-file arm: a sibling the compile reads (``meta.yml``, an
    ``extends:`` base) fails as it always has.
    """
    try:
        file_path = resolve_board_path(path, project)
    except ValueError as exc:
        rejected = resolution_error_diagnostic(exc)
        return BoardFailure(PurePosixPath(path), [rejected.message], rejected.code)
    display = PurePosixPath(file_path.relpath)
    if not file_path.exists():
        return BoardFailure(display, [_file_missing(display)], ERR_FILE_NOT_FOUND.code)
    board_file = file_path.read_board(named=True)
    try:
        board_file.content  # noqa: B018 — force the read of the board's own file
    except (UnicodeDecodeError, OSError):
        return BoardFailure(
            display, [_board_unreadable(display)], ERR_BOARD_FILE_UNREADABLE.code
        )
    compile_result = compile_file(board_file)
    if not compile_result.success:
        return BoardFailure(
            display,
            [e.message for e in compile_result.errors],
            compile_result.errors[0].code,
        )
    board = compile_result.board
    assert board is not None  # compile_result.success guarantees this
    return LoadedBoard(
        display,
        board,
        compile_result.query_registry,
        compile_result.default_source,
        {**compile_result.query_registry, **board.queries},
    )


def _load_board_query(
    name: str, path: Path, project: Project
) -> LoadedBoard | BoardFailure:
    """``load_board``, then find the query *name* in it."""
    loaded = load_board(path, project)
    if isinstance(loaded, BoardFailure) or name in loaded.all_queries:
        return loaded
    available = sorted(loaded.all_queries)
    return BoardFailure(
        loaded.path,
        [_query_not_found(loaded.path, name, available)],
        ERR_BOARD_QUERY_NOT_FOUND.code,
        available,
    )


class BoardQueryLookupResult(BaseModel):
    """Result of extracting SQL from a named board query for offline use (validate/describe)."""

    model_config = ConfigDict(frozen=True)

    success: bool
    sql: str = ""
    source: str | None = None
    errors: list[str] = []
    error_code: str | None = None
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
    from dbt_charts.core.dbt_ref_check import named_source_target_path
    from dbt_charts.core.execute.adapters.adapter_registry import linked_dbt_path
    from dbt_charts.core.execute.adapters.dbt_utils import DbtRefResolver
    from dbt_charts.core.execute.executor import resolve_query_references

    loaded = _load_board_query(name, path, project)
    if isinstance(loaded, BoardFailure):
        return BoardQueryLookupResult(
            success=False,
            errors=loaded.errors,
            error_code=loaded.error_code,
            available_queries=loaded.available,
        )
    board = loaded.board

    query = loaded.all_queries[name]
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
    from dbt_charts.core.compile.models.source import is_file_source
    from dbt_charts.core.compile.sql_guard import dialect_for_source
    from dbt_charts.core.dialects import get_dialect
    from dbt_charts.core.execute.executor import merge_board_variables, utc_now
    from dbt_charts.core.execute.source_resolver import (
        DbtContext,
        DefaultSourceResolver,
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

    source_type = dialect_for_source(query.source, project.sources.sources)
    warehouse = get_dialect(source_type) if source_type else None
    dbt_refs = DbtRefResolver(project)
    target_path = named_source_target_path(
        query.source, board.sources, project.sources.sources
    )
    clock = board.clock(utc_now())
    try:
        with board_clock(clock):
            merged_vars = merge_board_variables(board, vars or {}, clock)
            # {{ queries.X }} is expanded to text first, recursively, exactly as the
            # render/serve path does (Executor.execute_query Step 4) — don't move
            # this after the two resolve calls below; it's what lets a template
            # left inside X's own SQL (a plain variable, `filter()`, a literal) reach
            # the single variable render instead of never.
            expanded_query = resolve_query_references(
                query,
                all_queries=loaded.all_queries,
                query_name=name,
            )
            assert isinstance(expanded_query, SqlQuery)
            if query.dialect is not None:
                # Resolved the way execution resolves it, so the preview agrees with
                # what runs for file and dbt_profile sources.
                dbt_path = linked_dbt_path(project)
                dbt_context = (
                    DbtContext(dbt_project_path=dbt_path)
                    if dbt_path is not None
                    else None
                )
                source_config = DefaultSourceResolver().resolve(
                    query.source, board.sources, project.sources, dbt_context, name
                )
                if source_config is None:
                    raise dialect_unsupported(
                        query.dialect,
                        "the source's warehouse",
                        "no source resolved for this query",
                    )
                if dialect_differs(query.dialect, source_config):
                    if is_file_source(source_config):
                        raise dialect_unsupported(
                            query.dialect,
                            "duckdb",
                            "queries on a file source are not translated, so write "
                            "the SQL in duckdb",
                        )
                    engine = engine_dialect(query.dialect, source_config)
                    translated_sql, translated_params = render_in_dialect(
                        expanded_query.sql,
                        query.dialect,
                        engine,
                        merged_vars,
                        loaded.all_queries,
                        not query.lenient_variables,
                        dbt_refs,
                        target_path,
                        INLINE_PLACEHOLDERS,
                    )
                    return BoardQueryLookupResult(
                        success=True,
                        sql=inline_params_for_dialect(
                            translated_sql,
                            translated_params,
                            INLINE_PLACEHOLDERS,
                            get_dialect(engine),
                        ),
                        source=query.source,
                    )
            resolved_sql, _relations = dbt_refs.resolve(expanded_query.sql, target_path)
            # A source whose warehouse is only knowable at execute previews in the
            # default dialect's spelling rather than refusing.
            spelling = warehouse if warehouse is not None else get_dialect("postgres")
            rendered = render_parameterized_with_queries(
                resolved_sql,
                merged_vars,
                queries=loaded.all_queries,
                dialect=INLINE_PLACEHOLDERS,
                strict=not query.lenient_variables,
                warehouse=spelling,
            )
            # Same second resolve pass execution's own SqlAdapter.prepare_sql runs:
            # `resolve_query_references`'s dependency-graph short-circuit only
            # recognizes the spaced `{{ queries.` spelling, so a `{{queries.X}}`
            # reference reaches `render_parameterized_with_queries` unexpanded and
            # is inlined there instead — carrying any `ref()` call X's own SQL had.
            # Do not remove this as redundant with the first resolve above.
            composed_sql, _inlined_relations = dbt_refs.resolve(
                rendered.sql, target_path
            )
    except (JinjaError, DbtChartsError) as exc:
        return BoardQueryLookupResult(
            success=False, errors=[str(exc)], error_code=error_code_of(exc)
        )

    # Offline SQL has no parameter list to travel with it, so the values go into the
    # text as literals, quoted the way the helpers above were spelled.
    sql = inline_params_for_dialect(
        composed_sql, list(rendered.params), INLINE_PLACEHOLDERS, spelling
    )
    return BoardQueryLookupResult(success=True, sql=sql, source=query.source)


class ExecuteQueryArgs(BaseModel):
    """Run a query and return its rows as CSV. Three forms. `sql` + `board` runs the SQL as a query of that board: it uses the board's source and variables, and `{{ queries.<name>.cache }}` reads the rows that board query last cached with no warehouse call, while `{{ queries.<name> }}` runs the board's definition fresh on the warehouse. `sql` + `source` runs SQL directly on a data source. `board` + `query` runs one named query exactly as the board's author wrote it, which helps when debugging a chart error like 'unknown column foo'. Prefer the board form for a question a board's queries already answer: the cached rows are the governed answer as of their age, so a period that had closed by then is answered from `{{ queries.<name>.cache }}`, and re-running the same SQL on the warehouse only costs more for the same numbers; a question cut off before the rows were cached, inside a period still open then, runs fresh with its cutoff. The result says where it ran (`ran_on`) and, per `.cache` reference, whether the rows were cached and how old. The result is CSV bounded at about 25,000 tokens; page in SQL with LIMIT and OFFSET."""

    sql: str | None = Field(
        None,
        description="SQL to execute. Use {{ variable_name }} for parameterized values, bound by 'variables'. With 'board', it may reference the board's queries as {{ queries.<name>.cache }} (cached rows, no warehouse call) or {{ queries.<name> }} (fresh). Not with 'query'.",
    )
    board: Path | None = Field(
        None,
        description="Path to the board YAML file. With 'sql', the SQL runs as a query of this board; with 'query', that named query runs.",
    )
    query: str | None = Field(
        None,
        description="Name of the query inside the board's `queries:` block. Use with 'board', not with 'sql'.",
    )
    variables: list[VariableBinding] | None = Field(
        None,
        description=(
            "Variable values to substitute, as an array of {name, value} pairs. "
            "A board query's variable left out runs as the board renders it. "
            'Example: [{"name": "region", "value": "US"}, {"name": "year", "value": 2024}]'
        ),
    )
    source: str | None = Field(
        None,
        description=(
            "Data source name, or a CSV/JSON/Parquet file path (table name = "
            "file stem), to execute against ('sql' without 'board'). Required "
            "unless the project declares no `sources:`, where SQL runs on its "
            "dbt profile."
        ),
    )
    lenient_variables: bool = Field(
        False,
        description="When True, undefined Jinja variables degrade gracefully (useful for iterative chart editing where not all variables are set). 'sql' without 'board'.",
    )
    # Display only — never reaches execute_query(). Ad-hoc SQL is the one query
    # surface with no author to describe it: a named board query carries the
    # `notes:` its YAML already declares, and this is the equivalent for
    # a query that exists only for the length of one tool call.
    description: str | None = Field(
        None,
        description="Short phrase naming what this query is for (e.g. 'Leads by industry'), shown to the user in place of the raw SQL. 'sql' only.",
    )

    model_config = ConfigDict(extra="forbid")

    @model_validator(mode="after")
    def _no_duplicate_variable_names(self) -> Self:
        if self.variables is not None:
            variables_to_dict(self.variables)
        return self

    @model_validator(mode="after")
    def _exactly_one_form(self) -> Self:
        if self.sql is not None:
            if self.query is not None:
                raise ValueError(
                    "'query' names a board query and takes no 'sql'; to run SQL on "
                    "a board pass 'sql' + 'board'"
                )
            if self.board is not None and (self.source or self.lenient_variables):
                raise ValueError(
                    "'source' and 'lenient_variables' do not apply to 'sql' + "
                    "'board': the SQL uses the board's own source and variables"
                )
        elif self.board is None or self.query is None:
            raise ValueError(
                "needs 'sql', 'sql' + 'board', or both 'board' and 'query'"
            )
        elif self.source is not None or self.lenient_variables or self.description:
            raise ValueError(
                "'source', 'lenient_variables' and 'description' apply to 'sql' only; "
                "a board query names its own source"
            )
        return self


class CacheRef(BaseModel):
    """One ``{{ queries.<name>.cache }}`` the SQL read."""

    model_config = ConfigDict(frozen=True)

    query: str
    hit: bool = Field(
        description="True when the rows were already cached; false when the "
        "query had to run on the warehouse first."
    )
    age_seconds: int | None = Field(
        default=None, description="Age of the cached rows; absent on a miss."
    )


class ExecuteQueryResult(BaseModel):
    model_config = ConfigDict(frozen=True)

    success: bool
    columns: list[str]
    data: list[dict[str, Any]]
    errors: list[str]
    error_code: str | None = Field(
        default=None,
        description=(
            "Registered ERR-* code of the failure, readable with "
            "get_diagnostic_code. Absent on success."
        ),
    )
    row_count: int
    truncated: bool
    ran_on: Literal["cache", "warehouse"] = Field(
        default="warehouse",
        description=(
            "`cache` when the SQL read only cached rows (no warehouse call); "
            "`warehouse` when any part of it ran there."
        ),
    )
    cache_refs: list[CacheRef] = Field(
        default_factory=list,
        description="Each `{{ queries.<name>.cache }}` the SQL read, with whether "
        "it was cached and how old.",
    )
    diagnostics: list[QueryDiagnostic] = Field(
        default_factory=list,
        description=(
            "Deterministic validate_query findings (WARN-MISSING-JOIN-PREDICATE, "
            "WARN-REAGGREGATION, WARN-PARSE-ERROR). "
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
            dialect = source_dialect(
                adapter_registry.resolve_source_config(source).get("type")
            )
        except Exception:  # noqa: BLE001 — best-effort dialect hint
            dialect = None
        return validate_query(sql, dialect=dialect)
    except Exception:  # noqa: BLE001 — diagnostics are advisory, never fatal
        return []


def _inline_file_source_board(source: str, project: Project) -> Board | Diagnostic:
    """Compile a root-level board whose `source:` is the inline file path, so
    the compiler's own resolver registers it. Its `select 1` never executes.
    A path the compiler refuses comes back as its first compile error; the
    board holds one source, so that is the only error it can carry."""
    text = json.dumps(
        {"title": source, "source": source, "queries": {"query": "select 1"}}
    )
    result = compile_file(
        InMemoryBoard(text, path=project.path("query.yml")), apply_meta=False
    )
    return result.errors[0] if result.board is None else result.board


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

    Runs SQL directly against the configured data sources, or against an
    inline file path (`data/orders.csv`, table name = stem) as a board's
    `source:` would. Use {{ variable_name }} syntax for parameterized values.
    """
    limit = min(limit, MAX_QUERY_LIMIT)
    fetch_limit = limit + 1

    # Structural validation runs regardless of execution outcome: a query that
    # executes cleanly can still double-count (re-aggregation) or build a cartesian
    # product, which execution alone never flags.
    diagnostics = _query_diagnostics(sql, source, adapter_registry)

    try:
        # A sourceless query runs against the project's implicit dbt source, or
        # raises ERR-SOURCE-NOT-NAMED inside adapter_registry.execute() itself —
        # the single enforcement point.
        query = SqlQuery(
            sql=sql,
            source=source,
            limit=fetch_limit,
            lenient_variables=lenient_variables,
        )
        board = None
        if source is not None and is_file_source_ref(source):
            compiled = _inline_file_source_board(source, adapter_registry.project)
            if isinstance(compiled, Diagnostic):
                return ExecuteQueryResult(
                    success=False,
                    data=[],
                    columns=[],
                    errors=[compiled.message],
                    error_code=compiled.code,
                    row_count=0,
                    truncated=False,
                    diagnostics=diagnostics,
                )
            board = compiled

        result = adapter_registry.execute(query, variables=variables, board=board)

        if result.error:
            return ExecuteQueryResult(
                success=False,
                data=[],
                columns=[],
                errors=[str(result.error)],
                error_code=error_code_of(result.error),
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
            error_code=error_code_of(e),
            row_count=0,
            truncated=False,
            diagnostics=diagnostics,
        )


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
    error_code: str | None = None
    available_queries: list[str] = []


def _fail(
    name: str,
    path: PurePosixPath,
    errors: list[str],
    error_code: str,
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
        error_code=error_code,
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
    from dbt_charts.core.execute.executor import (
        merge_board_variables,
        resolve_query_references,
        utc_now,
    )

    limit = min(limit, MAX_QUERY_LIMIT)

    loaded = _load_board_query(name, path, project)
    if isinstance(loaded, BoardFailure):
        return _fail(
            name,
            loaded.path,
            loaded.errors,
            loaded.error_code,
            loaded.available,
        )
    board, resolved_display = loaded.board, loaded.path

    clock = board.clock(utc_now())
    try:
        check_variable_overrides(board.variable_registry or {}, vars or {})
        merged_vars = merge_board_variables(board, vars or {}, clock)
    except ExecutionError as exc:
        return _fail(name, resolved_display, [str(exc)], error_code_of(exc))

    query = loaded.all_queries[name].model_copy(update={"limit": limit + 1})
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
                all_queries=loaded.all_queries,
                query_name=name,
            )
        with board_clock(clock):
            exec_result = adapter_registry.execute(
                query, variables=merged_vars, board=board, query_name=name
            )
    except Exception as e:  # noqa: BLE001
        return _fail(
            name,
            resolved_display,
            [str(e)],
            error_code_of(e),
            sql=sql,
            notes=noted,
        )

    if exec_result.error:
        return _fail(
            name,
            resolved_display,
            [str(exec_result.error)],
            error_code_of(exec_result.error),
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
