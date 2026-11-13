"""Stage fakes for tests that stand in for a query source."""

from collections.abc import Iterable
from typing import Any, cast
from unittest.mock import MagicMock, patch

from dbt_charts.core.compile.models.board.normalized import Board
from dbt_charts.core.execute import plan_names, run as run_stage
from dbt_charts.core.execute.env import ExecEnv
from dbt_charts.core.execute.lookup import Ledger, Lookup, Miss
from dbt_charts.core.execute.plan import CacheKey, QueryRun, make_context
from dbt_charts.core.execute.query_plan import PlannedQuery, PlannedSqlQuery, QueryPlan
from dbt_charts.core.execute.query_result import QueryResult
from dbt_charts.core.render import render
from dbt_charts.core.render.render_result import RenderResult

from . import _plans
from ._compile import _TEXTS, plan_of
from ._scripted_env import ScriptedBackend, scripted_env


def planned_query(
    name: str,
    entry: PlannedQuery | None = None,
    cache_key: CacheKey = ("", "", ""),
    use_store: bool = True,
    tailable: bool = True,
) -> QueryRun:
    """A ``QueryRun`` built by hand, for tests of the stages after planning."""
    return QueryRun(
        name,
        entry or sql_entry("SELECT 1"),
        lambda: None,
        {},
        cache_key,
        use_store,
        tailable,
    )


def sql_entry(
    sql: str, **fields: Any
) -> PlannedSqlQuery:  # type-state: explicit_any — entry fields a test overrides
    """A plan entry for a SQL query, caching on with no ttl."""
    entry: PlannedSqlQuery = {
        "type": "sql",
        "source": None,
        "cache": {"enabled": True, "ttl": None, "prefetch": False},
        "formats": {},
        "depends_on": [],
        "sql": sql,
        "setup_sql": None,
        "dialect": None,
        "variable_dependencies": [],
        "incremental": None,
    }
    return cast(PlannedSqlQuery, {**entry, **fields})


def all_miss(names: Iterable[str], variables: object = None) -> Lookup:
    """Every queried name is a miss: nothing is cached."""
    return Lookup({}, tuple(Miss(planned_query(name)) for name in names), {})


def _hand_built_plan(board: Board) -> QueryPlan:
    """A plan of the queries *board*'s charts and layers name, for a board built by hand."""
    charts = board.placed_charts()
    layers = {
        chart.id: [layer.query for layer in plan_names._layers(chart)]
        for chart in charts
    }
    names = {c.query_name for c in charts if c.query_name is not None}
    names.update(name for keys in layers.values() for name in keys if name)
    return _plans.plan_of(
        {name: sql_entry("SELECT 1") for name in names},
        chart_queries={
            chart.id: {"query": chart.query_name, "layers": layers[chart.id]}
            for chart in charts
        },
    )


def render_via(mock: MagicMock, board: Board, **options: Any) -> RenderResult:
    """``render`` over a context whose queries answer from *mock*'s
    ``execute_query_result``: the rows or the error the test set."""

    env = scripted_env(ScriptedBackend())
    run_planned = run_stage.run_planned

    def answer(
        called_env: ExecEnv,
        plan: QueryPlan,
        query_name: str,
        **kwargs: Any,  # type-state: explicit_any — run_planned's keyword arguments
    ) -> QueryResult:
        # *mock* may itself be a ``Run`` over another env: only this env's
        # queries are the mock's to answer.
        if called_env is not env:
            return run_planned(called_env, plan, query_name, **kwargs)
        result = mock.execute_query_result(query_name, None)
        return QueryResult(data=result.rows, schema=result.schema)

    # A board built by hand has no text for the engine to plan; its charts' query
    # names are all a stubbed render reads from the plan.
    plan = (
        plan_of(board, env, mock.rendered_at, all_queries=True)
        if id(board) in _TEXTS
        else _hand_built_plan(board)
    )
    ctx = make_context(
        board,
        plan,
        env,
        clock=lambda: mock.rendered_at,
    )
    with patch.object(run_stage, "run_planned", answer):
        return render(board, ctx, Ledger(), **options)
