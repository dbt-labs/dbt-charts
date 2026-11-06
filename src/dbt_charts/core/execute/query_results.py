"""Collect stage: the rows (or errors) a render reads.

Stage: EXECUTE (collect)
Purpose: Fold the lookup's hits and the run's outcomes into ``QueryResults``,
    the only thing the render path reads query data from. A ``QueryResults``
    never executes a query: a name nobody collected is an error, not a lazy
    fetch.

    ``execute`` composes plan, lookup, run and collect for a caller that wants
    rows once, with no stage of its own to drive.

Entry Points:
    - collect(ctx, ledger, found, outcomes, variables) -> QueryResults
    - execute(ctx, names, variables, max_workers) -> QueryResults
"""

from __future__ import annotations

from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass, replace
from datetime import datetime
from functools import partial
from typing import TYPE_CHECKING

from dbt_charts.core.diagnostics.base import DbtChartsError
from dbt_charts.core.diagnostics.execution import ExecutionError
from dbt_charts.core.execute.cache_backend import CacheRows, ResultRows
from dbt_charts.core.execute.link_schema import LinkSchema, UnplannedLinkSchemaError
from dbt_charts.core.execute.lookup import Ledger, Lookup, cache_hit_ats, lookup_names
from dbt_charts.core.execute.plan import (
    ExecContext,
    base_column_formats,
    render_with_range_filters,
    text_listings,
)
from dbt_charts.core.execute.run import (
    Failed,
    Outcome,
    Rows,
    format_checked,
    run_misses,
)

if TYPE_CHECKING:
    from dbt_charts.core.compile.models.board.normalized import Board, VariableValues
    from dbt_charts.core.compile.template.listings import ListingCall, TextListings
    from dbt_charts.core.compile.template.parameterized import DateRangeFilter
    from dbt_charts.core.dbt_manifest import RawTableFormats
    from dbt_charts.core.execute.cache_backend import TruncationInfo
    from dbt_charts.core.execute.query_result import ResolvedRelation


class UncollectedQueryError(ExecutionError):
    """Render asked for rows nobody collected.

    The render's query set is gathered up front; a name outside it, or the same
    name under other variable values, is a gap in that gathering. Returning rows
    from a different variable state would render plausible, wrong numbers.
    """


@dataclass(frozen=True)
class QueryResults:
    """What one render knows about its queries. Implements ``ChartDataProvider``.

    ``board``, ``link_schema`` and ``base_formats`` are resolve-time context: the
    auto-link pass reads the board's link settings and the schema and foreign
    keys planned for it, and column-format lineage starts from the dbt columns'
    formats. They are not collected query data.
    """

    variables: VariableValues
    results: Mapping[str, ResultRows]
    errors: Mapping[str, Exception]
    rendered_at: datetime
    cache_hit_ats: list[datetime]
    truncations: Mapping[str, TruncationInfo]
    # dbt relation lineage of each query that ran, by name; absent for a query
    # with none (a non-dbt source, or one served from a cache).
    provenance: Mapping[str, list[ResolvedRelation]]
    listings: Callable[[str, frozenset[ListingCall]], TextListings]
    board: Board
    # None until ``with_link_schema`` plans it; reading it then is an error.
    planned_links: LinkSchema | None
    base_formats: Callable[[str | None], RawTableFormats]
    # Shared with the render's ledger and written during resolve: the lineage
    # failure of a query's dbt column formats, raised where a chart reading it
    # resolves.
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

    @property
    def link_schema(self) -> LinkSchema:
        if self.planned_links is None:
            raise UnplannedLinkSchemaError(
                "Automatic links were not planned for these results; "
                "call with_link_schema before resolving a board with links.auto."
            )
        return self.planned_links

    def base_column_formats(self, source: str | None) -> RawTableFormats:
        """The dbt columns' ``meta.format`` behind the source *source* names."""
        return self.base_formats(source)

    def text_listings(
        self, base_dir: str, calls: frozenset[ListingCall]
    ) -> TextListings:
        return self.listings(base_dir, calls)


def collect(
    ctx: ExecContext,
    ledger: Ledger,
    found: Lookup,
    outcomes: Mapping[str, Outcome],
    variables: VariableValues,
) -> QueryResults:
    """Fold the lookup's hits and the run's *outcomes* (``Rows`` or ``Failed``,
    one per missed or failed query) into ``QueryResults``.

    *variables* are the ones the queries were looked up under; render reads
    rows only under them. A hit is checked against its ``formats:`` here, as a
    miss is in ``run_misses``. A name is in at most one of the two inputs.
    """
    results: dict[str, ResultRows] = {}
    errors: dict[str, Exception] = {}
    checked = dict(outcomes)
    for name, hit in found.hits.items():
        outcome = format_checked(ctx, name, Rows(hit.result, None))
        if isinstance(outcome, Failed):
            checked[name] = outcome
        else:
            results[name] = hit.result
    for name, outcome in checked.items():
        if isinstance(outcome, Failed):
            errors[name] = outcome.error
        else:
            results[name] = outcome.result
    return QueryResults(
        variables,
        results,
        errors,
        ctx.rendered_at,
        cache_hit_ats(ledger),
        dict(ledger.truncations),
        {name: list(relations) for name, relations in ledger.provenance.items()},
        partial(text_listings, ctx),
        ctx.board,
        None,
        partial(base_column_formats, ctx),
        ledger.column_format_errors,
        partial(render_with_range_filters, ctx),
    )


def execute(
    ctx: ExecContext,
    names: Iterable[str],
    variables: VariableValues,
    max_workers: int | None = None,
    *,
    ledger: Ledger | None = None,
    use_cache: bool | None = None,
    force_refresh: bool = False,
) -> QueryResults:
    """Plan, look up, run and collect *names* in one call.

    Reading a name from the result raises that query's error, so nothing is
    raised here for a query that fails. A stored incremental query refreshes its
    tail, unlike render's pre-pass.

    Args:
        names: Query names, with or without the ``queries.`` prefix.
        variables: Caller values; the result serves rows only under these.
        max_workers: Pool width; None runs on the calling thread.
        ledger: Memo and error memo to share across calls, so a query already run
            through it answers from there. None: a ledger for this call alone.
        use_cache: Overrides the context's persistent-store participation. The
            per-render memo is unaffected: a query run through *ledger* returns
            those same rows.
        force_refresh: Clear the memo, error memo and store entry first.
    """
    ledger = Ledger() if ledger is None else ledger
    found = lookup_names(
        ctx,
        ledger,
        names,
        variables,
        resume_incremental=True,
        use_cache=use_cache,
        force_refresh=force_refresh,
    )
    return collect(
        ctx, ledger, found, run_misses(ctx, ledger, found, max_workers), variables
    )
