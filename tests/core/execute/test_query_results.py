"""Collect stage: plain-data ``QueryResults`` that render reads rows from."""

from datetime import UTC, datetime
from typing import Any
from unittest.mock import Mock

import pytest

from dbt_charts.core.diagnostics.execution import QueryError
from dbt_charts.core.execute.lookup import CachedRows
from dbt_charts.core.execute.query_results import (
    QueryResults,
    UncollectedQueryError,
    collect,
)
from dbt_charts.core.execute.run import Failed, Rows

from ..._results import rows_result

AT = datetime(2026, 1, 1, tzinfo=UTC)
VARS: dict[str, Any] = {"year": 2024}


def _collect(
    hits: dict[str, CachedRows], outcomes: dict[str, Rows | Failed]
) -> QueryResults:
    return collect(
        hits,
        outcomes,
        variables=VARS,
        rendered_at=AT,
        cache_hit_ats=[AT],
        truncations={},
        listings=Mock(),
        board=Mock(),
        adapter_registry=Mock(),
        column_format_errors={},
        range_filters=Mock(),
    )


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


def test_same_name_under_another_variable_set_is_served_from_its_own_collection() -> (
    None
):
    base = _collect({"q": CachedRows(rows_result([{"a": 1}]), None)}, {})
    stripped: dict[str, Any] = {}
    other = QueryResults(
        stripped,
        {"q": rows_result([{"a": 2}])},
        {},
        AT,
        [],
        {},
        Mock(),
        Mock(),
        Mock(),
        {},
        Mock(),
    )
    combined = base.with_collection(other)
    assert combined.execute_query("q", VARS) == [{"a": 1}]
    assert combined.execute_query("q", stripped) == [{"a": 2}]
    with pytest.raises(UncollectedQueryError, match="variables"):
        combined.execute_query("q", {"year": 1999})
