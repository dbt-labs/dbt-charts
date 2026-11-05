"""A stand-in ``Executor.lookup`` for tests that mock the executor."""

from collections.abc import Iterable
from typing import Any
from unittest.mock import MagicMock

from dbt_charts.core.compile.models.query.normalized import SqlQuery
from dbt_charts.core.execute.cache_backend import ResultRows
from dbt_charts.core.execute.lookup import Lookup, Miss
from dbt_charts.core.execute.plan import CacheKey, PlannedQuery, Route, Warehouse
from dbt_charts.core.execute.query_results import QueryResults


def planned_query(
    name: str,
    query: SqlQuery,
    cache_key: CacheKey = ("", "", ""),
    use_store: bool = True,
    route: Route | None = None,
    tailable: bool = True,
) -> PlannedQuery:
    """A ``PlannedQuery`` built by hand, for tests of the stages after planning."""
    return PlannedQuery(
        name,
        query,
        {},
        lambda: None,
        route or Warehouse(),
        cache_key,
        use_store,
        tailable,
    )


def all_miss(names: Iterable[str], variables: object = None) -> Lookup:
    """Every queried name is a miss: nothing is cached."""
    query = SqlQuery(sql="SELECT 1")
    return Lookup({}, tuple(Miss(planned_query(name, query)) for name in names), {})


def collect_via_execute_query(executor: MagicMock) -> None:
    """Make a mock executor run render's pre-pass over its own ``execute_query_result``.

    Nothing is cached and ``run_misses`` is a no-op, so ``collect_results`` reads
    each looked-up query through the mock — the rows or the error the test set.
    """
    executor.lookup.side_effect = all_miss
    executor.run_misses.return_value = {}

    def collect_results(
        found: Lookup, outcomes: object, variables: dict[str, Any]
    ) -> QueryResults:
        results: dict[str, ResultRows] = {}
        errors: dict[str, Exception] = {}
        for miss in found.misses:
            try:
                results[miss.name] = executor.execute_query_result(miss.name, variables)
            except Exception as exc:  # noqa: BLE001 — the mock raises what the test set
                errors[miss.name] = exc
        return QueryResults(
            variables,
            results,
            errors,
            executor.rendered_at,
            executor.cache_hit_ats,
            {},
            executor.text_listings,
            executor.board,
            MagicMock(),
            {},
            executor.render_with_range_filters,
        )

    executor.collect_results.side_effect = collect_results
