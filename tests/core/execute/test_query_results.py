"""Collect stage: plain-data ``QueryResults`` that render reads rows from."""

from datetime import UTC, datetime
from typing import Any
from unittest.mock import Mock

import pytest

from dbt_charts.core.compile import compile
from dbt_charts.core.diagnostics.execution import QueryError
from dbt_charts.core.execute.lookup import CachedRows, Ledger, Lookup
from dbt_charts.core.execute.plan import make_context
from dbt_charts.core.execute.query_results import (
    QueryResults,
    UncollectedQueryError,
    collect,
)
from dbt_charts.core.execute.run import Failed, Rows

from ..._compile import plan_of
from ..._results import rows_result
from ..._scripted_env import ScriptedBackend, scripted_env

AT = datetime(2026, 1, 1, tzinfo=UTC)
VARS: dict[str, Any] = {"year": 2024}


def _collect(
    hits: dict[str, CachedRows], outcomes: dict[str, Rows | Failed]
) -> QueryResults:
    names = (*hits, *outcomes)
    queries = "".join(
        f"  {name}:\n    sql: SELECT 1 AS a\n    source: s\n" for name in names
    )
    board = compile(f"title: T\nqueries:\n{queries or '  {}'}").board
    assert board is not None
    env = scripted_env(ScriptedBackend())
    ctx = make_context(
        board, plan_of(board, env, AT, all_queries=True), env, clock=lambda: AT
    )
    ledger = Ledger(ages=[("cached", AT, Mock(), True)])
    return collect(ctx, ledger, Lookup(hits, (), {}), outcomes, VARS)


def test_rows_come_from_hits_and_outcomes() -> None:
    results = _collect(
        {"cached": CachedRows(rows_result([{"a": 1}]), AT)},
        {"fresh": Rows(rows_result([{"b": 2}]), AT)},
    )
    assert results.execute_query("cached", VARS) == [{"a": 1}]
    assert results.execute_query("queries.fresh", VARS) == [{"b": 2}]


def test_failed_outcome_raises_its_error() -> None:
    error = QueryError("boom", "bad")
    results = _collect({}, {"bad": Failed(error, error)})
    with pytest.raises(QueryError) as caught:
        results.execute_query("bad", VARS)
    assert caught.value is error


def test_uncollected_query_is_an_error_not_a_fetch() -> None:
    results = _collect({}, {})
    with pytest.raises(UncollectedQueryError, match="'late'"):
        results.execute_query("late", VARS)


def test_other_variable_values_are_an_error() -> None:
    results = _collect({"q": CachedRows(rows_result([{"a": 1}]), None)}, {})
    with pytest.raises(UncollectedQueryError, match="variables"):
        results.execute_query("q", {"year": 1999})
