"""Query execution engine.

Stage: EXECUTE (Service)
Purpose: Execute queries for compiled boards.

Entry Points:
    - Executor.execute_query(query_name, variables) -> List[Dict]
    - Executor.execute_chart(chart, variables) -> List[Dict]

The executor:
- Resolves which adapter to use for each query
- Executes queries and returns data
- Caches results for efficiency

Does NOT:
- Compile boards
- Render charts

Dependencies:
    - dbt_charts.compile (for types)
    - .adapters (for data source adapters)

See also:
    - render/renderer.py: Uses executor for data
"""

from collections.abc import Callable, Iterable, Mapping
from dataclasses import replace
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any

from dbt_charts.core.compile.models.board.normalized import (
    Board,
    VariableValues,
)
from dbt_charts.core.compile.models.chart.normalized import Chart
from dbt_charts.core.compile.models.query.normalized import (
    AnyQuery,
)
from dbt_charts.core.compile.template.daterange import board_clock
from dbt_charts.core.compile.template.listings import ListingCall, TextListings
from dbt_charts.core.compile.template.parameterized import DateRangeFilter
from dbt_charts.core.diagnostics.base import DbtChartsError
from dbt_charts.core.diagnostics.execution import ExecutionError
from dbt_charts.core.execute.cache_backend import (
    ResultRows,
)
from dbt_charts.core.execute.lookup import (
    CachedRows,
    Failure,
    Ledger,
    Lookup,
    cache_hit_ats,
    lookup_and_record,
)
from dbt_charts.core.execute.plan import (
    ExecContext,
    PlannedQuery,
    plan,
    render_with_range_filters,
    text_listings,
)
from dbt_charts.core.execute.query_results import QueryResults, collect
from dbt_charts.core.execute.run import (
    Failed,
    Outcome,
    Rows,
    check_formats,
    format_checked,
    read_query,
    run_misses,
)

if TYPE_CHECKING:
    from dbt_charts.core.execute.adapters.adapter_registry import AdapterRegistry
    from dbt_charts.core.execute.adapters.base import ResolvedRelation
    from dbt_charts.core.execute.cache_backend import QueryResultCache


def utc_now() -> datetime:
    """The real clock, in UTC — ``Executor``'s default time source."""
    return datetime.now(UTC)


class Executor:
    """Executes queries for boards.

    Stage: EXECUTE (Service Module)

    The executor manages query execution for a compiled board.
    It handles:
    - Query lookup from the board
    - Adapter selection based on query type
    - Result caching for efficiency
    - Variable substitution

    Does NOT:
    - Compile boards (use compile module)
    - Render charts (use render module)

    Attributes:
        board: The compiled board
        adapter_registry: Registry of adapters
        query_registry: Complete query registry (for cross-references)

    Example:
        >>> from pathlib import Path
        >>> from dbt_charts.core.compile import compile
        >>> from dbt_charts.core.execute import Executor
        >>> from dbt_charts.core.execute.adapters import build_adapter_registry
        >>> from dbt_charts.cli.filesystem_project import FilesystemProject
        >>>
        >>> result = compile(yaml_content)
        >>> registry = build_adapter_registry(FilesystemProject(Path.cwd()))
        >>> executor = Executor(result.board, registry, query_registry=result.query_registry)
        >>>
        >>> # Execute a query
        >>> data = executor.execute_query("sales", {"year": 2024})
        >>> print(data)  # [{"date": "2024-01", "amount": 1000}, ...]
    """

    def __init__(
        self,
        board: Board,
        adapter_registry: "AdapterRegistry",
        query_registry: dict[str, AnyQuery] | None = None,
        use_cache: bool = True,
        result_cache: "QueryResultCache | None" = None,
        clock: Callable[[], datetime] = utc_now,
        board_slug: str | None = None,
    ) -> None:
        """Initialize executor.

        Args:
            board: Compiled board
            adapter_registry: Adapter registry. Build at the entry point with
                ``build_adapter_registry(FilesystemProject(root), ...)``
                (``FilesystemProject`` from ``dbt_charts.cli.filesystem_project``);
                the executor does not invent one from cwd.
            query_registry: Optional query registry for cross-file references
            use_cache: Default *persistent-store* participation for query
                execution. False means no-store: results are neither read from
                nor written to ``result_cache``. The per-render in-memory memo
                is unaffected — one execution per query per Executor holds
                regardless, because resolve and render must see identical rows
                (see ``_execute_result``).
            result_cache: Optional cache backend for persistent caching (Suite context)
            clock: This render's only time source. Read once here for
                ``rendered_at`` and again as each query's rows come back; pin it
                and the render's bytes stop depending on when it ran.
            board_slug: The board's file-identity slug (``board_slug(relpath)``),
                recorded on every cache entry this render writes so the cache can
                be listed by board. None for a board with no file (in-memory
                content), whose entries carry the compiled id instead.
        """
        rendered_at = clock()
        self.ctx = ExecContext(
            board=board,
            adapter_registry=adapter_registry,
            query_registry=query_registry
            or {},  # type-state: silent_fallback — None: the board's own queries are the whole registry
            result_cache=result_cache,
            use_cache=use_cache,
            clock=clock,
            rendered_at=rendered_at,
            board_slug=board_slug,
        )
        self.ledger = Ledger()
        # Query name -> why its dbt column formats could not be derived; raised
        # where a chart reading that query resolves (column_formats.py).
        self.column_format_errors: dict[str, DbtChartsError] = {}

    @property
    def cache_hit_ats(self) -> list[datetime]:
        # The ChartDataProvider seam: the executor is still passed as one.
        return cache_hit_ats(self.ledger)

    @property
    def board(self) -> Board:
        return self.ctx.board

    @property
    def adapter_registry(self) -> "AdapterRegistry":
        return self.ctx.adapter_registry

    @property
    def rendered_at(self) -> datetime:
        return self.ctx.rendered_at

    @property
    def query_registry(self) -> dict[str, AnyQuery]:
        return self.ctx.query_registry

    @query_registry.setter
    def query_registry(self, registry: dict[str, AnyQuery]) -> None:
        # replace() shares the memos, so a late-query overlay keeps them.
        self.ctx = replace(self.ctx, query_registry=registry)

    def render_with_range_filters(
        self, query_name: str, variables: VariableValues | None
    ) -> tuple[str, str | None, list[DateRangeFilter]] | None:
        return render_with_range_filters(self.ctx, query_name, variables)

    def text_listings(
        self, base_dir: str, calls: frozenset[ListingCall]
    ) -> TextListings:
        return text_listings(self.ctx, base_dir, calls)

    def execute_query(
        self,
        query_name: str,
        variables: VariableValues | None = None,
        use_cache: bool | None = None,
        force_refresh: bool = False,
    ) -> list[dict[str, Any]]:  # type-state: explicit_any — DB rows
        """Execute a query and return results.

        Stage: EXECUTE (Main Entry Point)

        Looks up the query by name, resolves the appropriate adapter,
        executes the query, and returns the data.

        Args:
            query_name: Name of query to execute (may include "queries." prefix)
            variables: Variable values for query resolution
            use_cache: Whether to read/write the persistent result store for
                this call (defaults to the instance setting). Does not disable
                the per-render memo — a query already executed by this
                Executor returns those same rows.
            force_refresh: If True, clear both success and failure caches
                (memo included) for this query and re-run it from scratch.

        Returns:
            List of dictionaries with query results (each dict is a row)

        Raises:
            ExecutionError: If query not found or execution fails, or if the
                query's ``formats:`` names a column the rows do not carry
                (a zero-row result carries no columns and passes)
            QueryError: If a cached failure exists within its retry window —
                rebuilt with the code it was first raised with, the
                ``CachedQueryFailure`` as its ``__cause__``

        Example:
            >>> data = executor.execute_query("sales", {"year": 2024})
            >>> for row in data:
            ...     print(f"{row['date']}: {row['amount']}")
        """
        return self.execute_query_result(
            query_name, variables, use_cache, force_refresh
        ).rows

    def execute_query_result(
        self,
        query_name: str,
        variables: VariableValues | None = None,
        use_cache: bool | None = None,
        force_refresh: bool = False,
    ) -> ResultRows:
        """:meth:`execute_query` with the result's declared schema: rows plus the
        type of each column, the same whether the rows came from a warehouse run,
        the per-render memo or the persistent cache."""
        # Outside a render nothing scopes the clock a preset literal in the SQL
        # (`filter_date_range('d', 'last_7_days')`) dates on.
        with board_clock(self.board.clock(self.rendered_at)):
            result = self._execute_result(
                query_name, variables, use_cache, force_refresh
            )
        check_formats(self.ctx, query_name.removeprefix("queries."), result)
        return result

    def _execute_result(
        self,
        query_name: str,
        variables: VariableValues | None = None,
        use_cache: bool | None = None,
        force_refresh: bool = False,
    ) -> ResultRows:
        """Run (or recall from cache) a query; ``execute_query_result`` checks it."""
        # The plan strips the "queries." prefix, merges variables with board
        # defaults, looks up the query and keys it; lookup and run both read it.
        # Its source resolves lazily: a memo or persistent hit must not resolve,
        # and a resolver failure is raised inside the query's failure boundary
        # below, never out of the board.
        [planned] = plan(self.ctx, [query_name], variables, use_cache)
        query_name, cache_key = planned.name, planned.cache_key

        # ────────────────────────────────────────────────────────────────
        # Two different caches here, and only one of them is optional.
        # The per-render `_cache` dict is not a freshness cache at all — it is
        # the render's identity guarantee: one execution per query per
        # Executor, since every chart consuming a query calls execute_query on
        # top of the parallel prefetch. It is unconditional. A query with tied
        # ORDER BY values can legally return a different row order on each
        # execution, so a second execution within one emission would let the
        # resolve pass and the render/record pass see different rows (the
        # ERR-RESOLVED-PIE-DATA-MISMATCH flake). `force_refresh` is the one
        # way to drop it. The plan's `use_store` (call-level `use_cache`, else
        # instance, and the resolved policy) governs only the *persistent*
        # store: a disabled query is never read from nor written to that store (no-store, not store-and-ignore). Folding `enabled`
        # into a memo gate would fan one live query out to a warehouse round
        # trip per consumer, each at a different instant — two charts off one
        # query could then disagree. So the policy scopes the memo *key*
        # instead (memo_key): a disabled query still memoizes for its own
        # consumers, it just no longer shares a slot with a query whose policy
        # differs.
        # ────────────────────────────────────────────────────────────────

        # force_refresh: clear all caches for this key before running
        if force_refresh:
            # Every policy slot for this content, not just the caller's. The
            # line below clears the *shared* persistent entry for all of them,
            # so popping one slot would leave a different-policy sibling serving
            # pre-refresh rows against a store that no longer has them —
            # narrowing what force_refresh has always meant.
            for key in [k for k in self.ledger.memo if k[:3] == cache_key]:
                del self.ledger.memo[key]
            self.ledger.errors.pop(query_name, None)
            if self.ctx.result_cache:
                self.ctx.result_cache.clear(*cache_key)

        return read_query(self.ctx, self.ledger, planned, force_refresh=force_refresh)

    def run_misses(self, found: Lookup, max_workers: int) -> dict[str, Outcome]:
        return run_misses(self.ctx, self.ledger, found, max_workers)

    def collect_results(
        self,
        found: Lookup,
        outcomes: Mapping[str, Outcome],
        variables: VariableValues,
    ) -> QueryResults:
        """Fold the lookup's hits and the run's *outcomes* into ``QueryResults``.

        *variables* are the ones the queries were looked up under; render reads
        rows only under them. A hit is checked against its ``formats:`` here, as
        a miss is in :meth:`run_misses`.
        """
        hits: dict[str, CachedRows] = {}
        checked = dict(outcomes)
        for name, hit in found.hits.items():
            outcome = format_checked(self.ctx, name, Rows(hit.result, None))
            if isinstance(outcome, Failed):
                checked[name] = outcome
            else:
                hits[name] = hit
        return collect(
            hits,
            checked,
            variables=variables,
            rendered_at=self.rendered_at,
            cache_hit_ats=self.cache_hit_ats,
            truncations=dict(self.ledger.truncations),
            listings=self.text_listings,
            board=self.board,
            adapter_registry=self.adapter_registry,
            column_format_errors=self.column_format_errors,
            range_filters=self.render_with_range_filters,
        )

    def execute_chart(
        self,
        chart: Chart | str,
        variables: VariableValues | None = None,
        use_cache: bool | None = None,
    ) -> list[dict[str, Any]]:
        """Execute the query for a chart (see :meth:`execute_chart_result`)."""
        return self.execute_chart_result(chart, variables, use_cache).rows

    def execute_chart_result(
        self,
        chart: Chart | str,
        variables: VariableValues | None = None,
        use_cache: bool | None = None,
    ) -> ResultRows:
        """Execute the query for a chart, with its declared column types.

        Convenience method that handles chart → query lookup.

        Args:
            chart: Chart or chart name string
            variables: Variable values
            use_cache: Whether to use the persistent result store
                (defaults to instance setting)

        Returns:
            Query results for the chart

        Example:
            >>> chart = board.charts["revenue"]
            >>> data = executor.execute_chart(chart, {"year": 2024})
        """
        if isinstance(chart, str):
            if chart not in self.board.charts:
                raise ExecutionError(f"Chart '{chart}' not found")
            chart = self.board.charts[chart]

        # Handle blank charts (no query) - return empty data
        if chart.query_name is None:
            return ResultRows([], {})

        # Get data (use_cache will fall back to instance default in execute_query)
        return self.execute_query_result(chart.query_name, variables, use_cache)

    def get_query_provenance(self, query_name: str) -> list["ResolvedRelation"]:
        """Get cached dbt relation lineage for a query.

        Returns the ResolvedRelation objects captured during execution, or an
        empty list when no provenance is available (non-dbt queries, or not yet
        executed). Empty rather than None because the distinction carries no
        information — a nullable that nothing distinguishes is the type-state
        rot ``scripts/type_state_counter.py`` counts.
        """
        if query_name and query_name.startswith("queries."):
            query_name = query_name[8:]
        if query_name not in self.ledger.provenance:
            return []
        return self.ledger.provenance[query_name]

    def lookup(
        self,
        query_names: Iterable[str],
        variables: VariableValues | None = None,
    ) -> Lookup:
        """Which of *query_names* are answered without executing, applying hits.

        The same variable normalization and cache-key computation as
        ``execute_query``, then the one ``lookup`` over the memo, the error memo
        and the persistent store. Store hits warm the per-render memo, so
        ``execute_query`` serves them next. A cached error is a failure, not a
        hit — that query goes through ``execute_query`` so it is raised rather
        than skipped. A stored incremental query is a hit as stored: only a
        direct ``execute_query`` runs its tail. Likewise an ``ExecutionError``
        raised *while computing* the cache key (a missing file source, an
        unknown query) is that query's
        failure, never raised here: a fresh ``execute_query`` turns it into a
        proper per-chart diagnostic.

        Args:
            query_names: Names of the queries (may include the "queries." prefix).
            variables: Variable values — same values passed to execute_query.
        """
        planned: list[PlannedQuery] = []
        key_failures: dict[str, Failure] = {}
        for raw_name in query_names:
            try:
                planned += plan(self.ctx, [raw_name], variables)
            except ExecutionError as exc:
                key_failures[raw_name.removeprefix("queries.")] = Failure(exc)
        found = lookup_and_record(
            self.ctx, self.ledger, planned, resume_incremental=False
        )
        return Lookup(found.hits, found.misses, {**found.failures, **key_failures})
