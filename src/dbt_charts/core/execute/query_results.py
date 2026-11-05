"""Collect stage: the rows (or errors) a render reads.

Stage: EXECUTE (collect)
Purpose: Fold the lookup's hits and the run's outcomes into ``QueryResults``,
    the only thing the render path reads query data from. A ``QueryResults``
    never executes a query: a name nobody collected is an error, not a lazy
    fetch.

Entry Points:
    - collect(hits, outcomes, ...) -> QueryResults
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass, replace
from datetime import datetime
from typing import TYPE_CHECKING

from dbt_charts.core.diagnostics.base import DbtChartsError
from dbt_charts.core.diagnostics.execution import ExecutionError
from dbt_charts.core.execute.cache_backend import CacheRows, ResultRows
from dbt_charts.core.execute.lookup import CachedRows
from dbt_charts.core.execute.run import Failed, Outcome

if TYPE_CHECKING:
    from dbt_charts.core.compile.models.board.normalized import Board, VariableValues
    from dbt_charts.core.compile.template.listings import ListingCall, TextListings
    from dbt_charts.core.compile.template.parameterized import DateRangeFilter
    from dbt_charts.core.execute.adapters.adapter_registry import AdapterRegistry
    from dbt_charts.core.execute.cache_backend import TruncationInfo


class UncollectedQueryError(ExecutionError):
    """Render asked for rows nobody collected.

    The render's query set is gathered up front; a name outside it, or the same
    name under other variable values, is a gap in that gathering. Returning rows
    from a different variable state would render plausible, wrong numbers.
    """


@dataclass(frozen=True)
class QueryResults:
    """What one render knows about its queries. Implements ``ChartDataProvider``.

    ``board`` and ``adapter_registry`` are resolve-time context for the auto-link
    pass, which reads the board's link settings and asks the adapters for schema
    and foreign keys. They are not collected query data.
    """

    variables: VariableValues
    results: Mapping[str, ResultRows]
    errors: Mapping[str, Exception]
    rendered_at: datetime
    cache_hit_ats: list[datetime]
    truncations: Mapping[str, TruncationInfo]
    listings: Callable[[str, frozenset[ListingCall]], TextListings]
    board: Board
    adapter_registry: AdapterRegistry
    # Shared with the executor and written during resolve: the lineage failure of
    # a query's dbt column formats, raised where a chart reading it resolves.
    column_format_errors: dict[str, DbtChartsError]
    range_filters: Callable[
        [str, VariableValues], tuple[str, str | None, list[DateRangeFilter]] | None
    ]
    # Late queries collected under other variable values (``plan_extra``).
    collections: tuple[QueryResults, ...] = ()

    def with_collection(self, other: QueryResults) -> QueryResults:
        """These results plus *other*, collected under its own variable values."""
        return replace(self, collections=(*self.collections, other))

    def execute_query(self, query_name: str, variables: VariableValues) -> CacheRows:
        """The rows collected for ``query_name``; raises its recorded error.

        Raises:
            UncollectedQueryError: The query was not collected, or *variables*
                differ from the ones it, and every late collection, was
                collected under.
        """
        return self.execute_query_result(query_name, variables).rows

    def execute_query_result(
        self, query_name: str, variables: VariableValues
    ) -> ResultRows:
        """The rows collected for ``query_name`` with their declared column
        types; raises its recorded error.

        Raises:
            UncollectedQueryError: The query was not collected, or *variables*
                differ from the ones it was collected under.
        """
        name = query_name.removeprefix("queries.")
        held = [
            h for h in (self, *self.collections) if dict(h.variables) == dict(variables)
        ]
        if not held:
            raise UncollectedQueryError(
                f"Query {name!r} was collected under variables "
                f"{dict(self.variables)!r}, but render asked for {dict(variables)!r}."
            )
        for collection in held:
            if name in collection.errors:
                raise collection.errors[name]
            if name in collection.results:
                return collection.results[name]
        collected = sorted({n for h in held for n in (*h.results, *h.errors)})
        raise UncollectedQueryError(
            f"Query {name!r} was not collected before render under variables "
            f"{dict(variables)!r} (collected: {collected})."
        )

    def render_with_range_filters(
        self, query_name: str, variables: VariableValues
    ) -> tuple[str, str | None, list[DateRangeFilter]] | None:
        """The SQL *query_name* runs as and the date ranges its filters recorded;
        rendering SQL text, not executing it."""
        return self.range_filters(query_name, variables)

    def text_listings(
        self, base_dir: str, calls: frozenset[ListingCall]
    ) -> TextListings:
        return self.listings(base_dir, calls)


def collect(
    hits: Mapping[str, CachedRows],
    outcomes: Mapping[str, Outcome],
    *,
    variables: VariableValues,
    rendered_at: datetime,
    cache_hit_ats: list[datetime],
    truncations: Mapping[str, TruncationInfo],
    listings: Callable[[str, frozenset[ListingCall]], TextListings],
    board: Board,
    adapter_registry: AdapterRegistry,
    column_format_errors: dict[str, DbtChartsError],
    range_filters: Callable[
        [str, VariableValues], tuple[str, str | None, list[DateRangeFilter]] | None
    ],
) -> QueryResults:
    """Fold *hits* and *outcomes* (``Rows`` or ``Failed``, one per query) into
    ``QueryResults``. A name is in at most one of the two inputs."""
    results: dict[str, ResultRows] = {name: hit.result for name, hit in hits.items()}
    errors: dict[str, Exception] = {}
    for name, outcome in outcomes.items():
        if isinstance(outcome, Failed):
            errors[name] = outcome.error
        else:
            results[name] = outcome.result
    return QueryResults(
        variables,
        results,
        errors,
        rendered_at,
        cache_hit_ats,
        truncations,
        listings,
        board,
        adapter_registry,
        column_format_errors,
        range_filters,
    )
