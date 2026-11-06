"""Stage fakes for tests that stand in for a query source."""

from collections.abc import Iterable
from typing import Any
from unittest.mock import MagicMock, patch

from dbt_charts.core.compile.models.board.normalized import Board, VariableValues
from dbt_charts.core.compile.models.query.normalized import AnyQuery, SqlQuery
from dbt_charts.core.compile.models.source import ResolvedSourceConfig
from dbt_charts.core.execute import run as run_stage
from dbt_charts.core.execute.collect import collect_all_query_names
from dbt_charts.core.execute.env import ExecEnv
from dbt_charts.core.execute.lookup import Ledger, Lookup, Miss
from dbt_charts.core.execute.plan import (
    CacheKey,
    PlannedQuery,
    make_context,
)
from dbt_charts.core.execute.query_result import QueryParams, QueryResult
from dbt_charts.core.execute.run import Route
from dbt_charts.core.render import render
from dbt_charts.core.render.render_result import RenderResult

from ._scripted_env import ScriptedBackend, scripted_env


def planned_query(
    name: str,
    query: SqlQuery,
    cache_key: CacheKey = ("", "", ""),
    use_store: bool = True,
    tailable: bool = True,
) -> PlannedQuery:
    """A ``PlannedQuery`` built by hand, for tests of the stages after planning."""
    return PlannedQuery(
        name,
        query,
        {},
        lambda: None,
        {},
        cache_key,
        use_store,
        tailable,
    )


def all_miss(names: Iterable[str], variables: object = None) -> Lookup:
    """Every queried name is a miss: nothing is cached."""
    query = SqlQuery(sql="SELECT 1")
    return Lookup({}, tuple(Miss(planned_query(name, query)) for name in names), {})


def render_via(mock: MagicMock, board: Board, **options: Any) -> RenderResult:
    """``render`` over a context whose queries answer from *mock*'s
    ``execute_query_result``: the rows or the error the test set."""

    env = scripted_env(ScriptedBackend())
    run_route = run_stage.run_route

    def answer(
        called_env: ExecEnv,
        route: Route,
        query: AnyQuery,
        variables: VariableValues | None,
        params: QueryParams,
        board: Board | None,
        query_name: str,
        source: ResolvedSourceConfig | None,
    ) -> QueryResult:
        # *mock* may itself be a ``Run`` over another env: only this env's
        # queries are the mock's to answer.
        if called_env is not env:
            return run_route(
                called_env, route, query, variables, params, board, query_name, source
            )
        result = mock.execute_query_result(query_name, variables)
        return QueryResult(data=result.rows, schema=result.schema)

    # A hand-built board names queries it never defines; the mock answers any.
    undefined: dict[str, AnyQuery] = {
        name: SqlQuery(sql="SELECT 1")
        for name in collect_all_query_names(board)
        if name not in board.queries
    }
    ctx = make_context(
        board,
        env,
        query_registry=undefined,
        clock=lambda: mock.rendered_at,
    )
    with patch.object(run_stage, "run_route", answer):
        return render(board, ctx, Ledger(), **options)
