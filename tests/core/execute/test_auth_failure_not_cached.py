"""A credentials failure is never cached: re-authenticating must take effect."""

from __future__ import annotations

import pytest
import yaml

from dbt_charts.core.column_types import literal_schema
from dbt_charts.core.compile import compile as compile_board
from dbt_charts.core.compile.models.source import DuckDBSourceConfig
from dbt_charts.core.diagnostics.codes_execute import (
    ERR_WAREHOUSE_AUTH,
    ERR_WAREHOUSE_CONNECTION,
    ERR_WAREHOUSE_RUNTIME,
)
from dbt_charts.core.diagnostics.execution import QueryError
from dbt_charts.core.diagnostics.registry import ErrorCode
from dbt_charts.core.execute.cache_backend import CachedQueryFailure
from dbt_charts.core.execute.query_result import (
    QueryResult,
    classify_warehouse_error,
    connection_failure,
)
from dbt_charts.core.execute.trivial_local_cache import TrivialDuckDBCache

from ..._run import Run
from ..._scripted_env import ScriptedBackend, scripted_env

_SOURCE = DuckDBSourceConfig(type="duckdb", path=":memory:")


def _scripted(*results: QueryResult) -> ScriptedBackend:
    """A backend that answers with the scripted results in order, one per execution."""
    return ScriptedBackend(sequence=list(results))


def _executor(backend: ScriptedBackend, cache: TrivialDuckDBCache) -> Run:
    document = {
        "queries": {"q": {"sql": "SELECT 1 AS v", "source": "wh"}},
        "charts": {"c": {"query": "q", "type": "table"}},
    }
    board = compile_board(yaml.dump(document)).board
    assert board is not None
    return Run(
        board,
        env=scripted_env(backend, source=_SOURCE),
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
    backend = _scripted(
        failure, QueryResult(data=[{"v": 1}], schema=literal_schema([{"v": 1}]))
    )

    with pytest.raises(QueryError) as raised:
        _executor(backend, cache).execute_query("q")
    assert raised.value.code is code

    assert _executor(backend, cache).execute_query("q") == [{"v": 1}]
    assert backend.execute.call_count == 2


def test_ordinary_query_failure_is_still_cached() -> None:
    cache = TrivialDuckDBCache()
    backend = _scripted(
        _failure("Resources exceeded during query execution", "bigquery"),
        QueryResult(data=[{"v": 1}], schema=literal_schema([{"v": 1}])),
    )

    with pytest.raises(QueryError) as raised:
        _executor(backend, cache).execute_query("q")
    assert raised.value.code is ERR_WAREHOUSE_RUNTIME

    with pytest.raises(QueryError, match="Resources exceeded") as replayed:
        _executor(backend, cache).execute_query("q")
    assert isinstance(replayed.value.__cause__, CachedQueryFailure)
    assert replayed.value.code is ERR_WAREHOUSE_RUNTIME
    assert backend.execute.call_count == 1


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
