"""A credentials failure is never cached: re-authenticating must take effect."""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import pytest
import yaml

from dbt_charts.core.compile import compile as compile_board
from dbt_charts.core.diagnostics.codes_execute import (
    ERR_WAREHOUSE_AUTH,
    ERR_WAREHOUSE_CONNECTION,
    ERR_WAREHOUSE_RUNTIME,
)
from dbt_charts.core.diagnostics.execution import QueryError
from dbt_charts.core.diagnostics.registry import ErrorCode
from dbt_charts.core.execute.adapters.base import (
    QueryResult,
    classify_warehouse_error,
    connection_failure,
)
from dbt_charts.core.execute.cache_backend import CachedQueryFailure
from dbt_charts.core.execute.executor import Executor
from dbt_charts.core.execute.trivial_local_cache import TrivialDuckDBCache


class _ScriptedRegistry:
    """Hands back the scripted results in order, one per execution."""

    def __init__(self, *results: QueryResult) -> None:
        self.results = list(results)
        self.calls = 0
        self.project_sources = SimpleNamespace(sources={})

    def execute(self, *args: Any, **kwargs: Any) -> QueryResult:
        self.calls += 1
        return self.results.pop(0)


def _executor(registry: _ScriptedRegistry, cache: TrivialDuckDBCache) -> Executor:
    document = {
        "queries": {"q": {"sql": "SELECT 1 AS v", "source": "wh"}},
        "charts": {"c": {"query": "q", "type": "table"}},
    }
    board = compile_board(yaml.dump(document)).board
    assert board is not None
    return Executor(
        board,
        adapter_registry=registry,
        query_registry={"q": board.queries["q"]},
        result_cache=cache,
    )


def _failure(text: str, dialect: str) -> QueryResult:
    return classify_warehouse_error("dbt SQL execution", Exception(text), dialect)


@pytest.mark.parametrize(
    ("failure", "code"),
    [
        (
            _failure("invalid_grant: Token has been expired or revoked.", "bigquery"),
            ERR_WAREHOUSE_AUTH,
        ),
        (
            connection_failure("bigquery", Exception("could not connect")),
            ERR_WAREHOUSE_CONNECTION,
        ),
    ],
)
def test_credential_or_connection_failure_does_not_replay_once_fixed(
    failure: QueryResult, code: ErrorCode
) -> None:
    cache = TrivialDuckDBCache()
    registry = _ScriptedRegistry(failure, QueryResult(data=[{"v": 1}]))

    with pytest.raises(QueryError) as raised:
        _executor(registry, cache).execute_query("q")
    assert raised.value.code is code

    assert _executor(registry, cache).execute_query("q") == [{"v": 1}]
    assert registry.calls == 2


def test_ordinary_query_failure_is_still_cached() -> None:
    cache = TrivialDuckDBCache()
    registry = _ScriptedRegistry(
        _failure("Resources exceeded during query execution", "bigquery"),
        QueryResult(data=[{"v": 1}]),
    )

    with pytest.raises(QueryError) as raised:
        _executor(registry, cache).execute_query("q")
    assert raised.value.code is ERR_WAREHOUSE_RUNTIME

    with pytest.raises(CachedQueryFailure, match="Resources exceeded"):
        _executor(registry, cache).execute_query("q")
    assert registry.calls == 1


@pytest.mark.parametrize(
    ("dialect", "text"),
    [
        ("bigquery", "invalid_grant: Bad Request"),
        ("bigquery", "Reauthentication is needed. Please run `gcloud auth login`"),
        (
            "bigquery",
            "Your default credentials were not found. To set up Application "
            "Default Credentials",
        ),
        ("snowflake", "250001: Incorrect username or password was specified."),
        ("snowflake", "390114 (08001): Authentication token has expired."),
        ("postgres", 'FATAL: password authentication failed for user "bob"'),
        ("redshift", 'FATAL: password authentication failed for user "bob"'),
    ],
)
def test_auth_failure_names_reauthenticating_not_the_sql(
    dialect: str, text: str
) -> None:
    error = _failure(text, dialect).error

    assert error is not None
    assert error.code is ERR_WAREHOUSE_AUTH
    assert text in str(error)
    assert "Change the SQL" not in str(error)
    assert "credentials" in str(error)
