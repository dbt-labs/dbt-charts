"""SQL run as a transient query of a stored board."""

from __future__ import annotations

import re
from collections.abc import Mapping
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from dbt_charts.agent_api.query import (
    MAX_QUERY_LIMIT,
    BoardFailure,
    CacheRef,
    ExecuteQueryResult,
    error_code_of,
    load_board,
)
from dbt_charts.core.compile.errors import CompilationError
from dbt_charts.core.compile.models.query.normalized import AnyQuery
from dbt_charts.core.compile.normalize.queries import normalize_query
from dbt_charts.core.compile.template.variables import check_variable_overrides
from dbt_charts.core.diagnostics.base import DbtChartsError
from dbt_charts.core.diagnostics.codes_execute import (
    ERR_CACHE_NOT_ATTACHED,
    ERR_CACHE_SQL_FAILED,
)
from dbt_charts.core.diagnostics.execution import ExecutionError
from dbt_charts.core.execute.adapters import AdapterRegistry
from dbt_charts.core.execute.cache_backend import QueryResultCache
from dbt_charts.core.execute.cache_composition import CompositionError
from dbt_charts.core.execute.executor import Executor
from dbt_charts.core.execute.lookup import data_age
from dbt_charts.core.execute.plan import CACHE_REF_JINJA_RE
from dbt_charts.core.project import Project, board_identity
from dbt_charts.core.validate import normalize_data_for_json

# The reserved name the transient query runs under in the board's query set.
ADHOC_QUERY = "_adhoc"
# Every `{{ queries.NAME }}` and `{{ queries.NAME.cache }}` reference in authored SQL.
_QUERY_REF_RE = re.compile(r"{{\s*queries\.([A-Za-z_]\w*)")


def _inherited_source(sql: str, queries: Mapping[str, AnyQuery]) -> str | None:
    """The one source the queries *sql* references share, else None.

    A reference inlines that query's SQL (or composes over its rows), so the
    transient query runs where the referenced query runs when the board
    declares no default source of its own.
    """
    sources = {
        source
        for name in set(_QUERY_REF_RE.findall(sql))
        if name in queries
        and isinstance(source := getattr(queries[name], "source", None), str)
    }
    return sources.pop() if len(sources) == 1 else None


def _failed(errors: list[str], error_code: str) -> ExecuteQueryResult:
    return ExecuteQueryResult(
        success=False,
        columns=[],
        data=[],
        errors=errors,
        error_code=error_code,
        row_count=0,
        truncated=False,
    )


def execute_board_sql(
    sql: str,
    path: Path,
    project: Project,
    adapter_registry: AdapterRegistry,
    result_cache: QueryResultCache | None,
    variables: dict[str, Any] | None = None,
    limit: int = MAX_QUERY_LIMIT,
) -> ExecuteQueryResult:
    """Run *sql* as if it were a query written into the board at *path*.

    It takes the board's default source (or, when the board declares none, the
    one source the queries it references share) and the board's variables
    (defaults applied, *variables* validated against the board's declarations).
    ``{{ queries.X }}``
    inlines X's definition and runs on the warehouse; ``{{ queries.X.cache }}``
    composes over X's cached rows with no warehouse call, running X first (and
    caching it) when it is not cached. The transient query itself is never
    cached, so it leaves no entry and appears in no entry's ``used_by``.
    """
    limit = min(limit, MAX_QUERY_LIMIT)
    loaded = load_board(path, project)
    if isinstance(loaded, BoardFailure):
        return _failed(loaded.errors, loaded.error_code)
    board = loaded.board
    registry = board.variable_registry or {}
    overrides = variables or {}
    all_queries = {**board.queries, **loaded.query_registry}
    source = loaded.default_source or _inherited_source(sql, all_queries)
    try:
        check_variable_overrides(registry, overrides)
        adhoc = normalize_query(
            ADHOC_QUERY,
            {"sql": sql, "cache": False, "limit": limit + 1},
            source,
            sources={**project.sources.sources, **board.sources},
        )
    except (ExecutionError, CompilationError) as exc:
        return _failed([str(exc)], error_code_of(exc))
    cache_refs = list(dict.fromkeys(CACHE_REF_JINJA_RE.findall(sql)))
    if cache_refs and result_cache is None:
        failure = DbtChartsError.from_code(ERR_CACHE_NOT_ATTACHED, query=cache_refs[0])
        return _failed([str(failure)], ERR_CACHE_NOT_ATTACHED.code)

    executor = Executor(
        board,
        adapter_registry=adapter_registry,
        query_registry={**loaded.query_registry, ADHOC_QUERY: adhoc},
        result_cache=result_cache,
        board_slug=board_identity(str(loaded.path)),
    )
    try:
        rows = executor.execute_query(ADHOC_QUERY, overrides)
    except DbtChartsError as exc:
        return _failed([str(exc)], error_code_of(exc))
    except CompositionError as exc:
        failure = DbtChartsError.from_code(ERR_CACHE_SQL_FAILED, detail=str(exc))
        return _failed([str(failure)], ERR_CACHE_SQL_FAILED.code)

    now = datetime.now(UTC)
    refs: list[CacheRef] = []
    for name in cache_refs:
        age = data_age(executor.ledger, name)
        assert age is not None  # executing the composing query ran each upstream
        as_of, hit = age
        refs.append(
            CacheRef(
                query=name,
                hit=hit,
                age_seconds=max(0, int((now - as_of).total_seconds())) if hit else None,
            )
        )
    data = normalize_data_for_json(rows)
    return ExecuteQueryResult(
        success=True,
        columns=list(data[0]) if data else [],
        data=data[:limit],
        errors=[],
        row_count=min(len(data), limit),
        truncated=len(data) > limit,
        ran_on="cache" if refs and all(r.hit for r in refs) else "warehouse",
        cache_refs=refs,
    )
