"""A context plus its ledger: what one render of a test board shares.

Tests that read a query several times, or hand the same state to ``render``,
build one ``Run`` and call it; each read is the one-shot ``execute`` over the
shared ledger, so the per-render memo holds exactly as it does in a render.
"""

from collections.abc import Callable
from datetime import datetime
from typing import Any

from dbt_charts.core.compile.models.board.normalized import Board, VariableValues
from dbt_charts.core.compile.models.chart.normalized import Chart
from dbt_charts.core.compile.models.query.normalized import AnyQuery
from dbt_charts.core.compile.template.listings import ListingCall, TextListings
from dbt_charts.core.dbt_manifest import RawTableFormats
from dbt_charts.core.diagnostics.base import DbtChartsError
from dbt_charts.core.diagnostics.execution import ExecutionError
from dbt_charts.core.execute.cache_backend import QueryResultCache, ResultRows
from dbt_charts.core.execute.env import ExecEnv
from dbt_charts.core.execute.link_schema import LinkSchema, UnplannedLinkSchemaError
from dbt_charts.core.execute.lookup import Ledger, Lookup, cache_hit_ats, lookup_names
from dbt_charts.core.execute.plan import (
    ExecContext,
    base_column_formats,
    make_context,
    render_with_range_filters,
    text_listings,
    utc_now,
)
from dbt_charts.core.execute.query_results import QueryResults, execute
from dbt_charts.core.execute.run import Outcome, run_misses
from dbt_charts.core.render import render
from dbt_charts.core.render.render_result import RenderResult


class Run:
    """Also a lazy ``ResolveDataProvider``: each read executes through the ledger."""

    def __init__(
        self,
        board: Board,
        env: ExecEnv,
        query_registry: dict[str, AnyQuery] | None = None,
        use_cache: bool = True,
        result_cache: QueryResultCache | None = None,
        clock: Callable[[], datetime] = utc_now,
        board_slug: str | None = None,
    ) -> None:
        self.ctx: ExecContext = make_context(
            board,
            env,
            query_registry,
            use_cache,
            result_cache,
            clock,
            board_slug,
        )
        self.ledger = Ledger()

    def results(
        self,
        names: list[str],
        variables: VariableValues | None = None,
        use_cache: bool | None = None,
        force_refresh: bool = False,
    ) -> QueryResults:
        return execute(
            self.ctx,
            names,
            variables or {},
            ledger=self.ledger,
            use_cache=use_cache,
            force_refresh=force_refresh,
        )

    def execute_query_result(
        self,
        query_name: str,
        variables: VariableValues | None = None,
        use_cache: bool | None = None,
        force_refresh: bool = False,
    ) -> ResultRows:
        return self.results(
            [query_name], variables, use_cache, force_refresh
        ).execute_query_result(query_name, variables or {})

    def execute_query(
        self,
        query_name: str,
        variables: VariableValues | None = None,
        use_cache: bool | None = None,
        force_refresh: bool = False,
    ) -> list[dict[str, Any]]:
        return self.execute_query_result(
            query_name, variables, use_cache, force_refresh
        ).rows

    def execute_chart(
        self,
        chart: Chart | str,
        variables: VariableValues | None = None,
        use_cache: bool | None = None,
    ) -> list[dict[str, Any]]:
        if isinstance(chart, str):
            if chart not in self.ctx.board.charts:
                raise ExecutionError(f"Chart '{chart}' not found")
            chart = self.ctx.board.charts[chart]
        if chart.query_name is None:
            return []
        return self.execute_query(chart.query_name, variables, use_cache)

    @property
    def board(self) -> Board:
        return self.ctx.board

    @property
    def rendered_at(self) -> datetime:
        return self.ctx.rendered_at

    @property
    def cache_hit_ats(self) -> list[datetime]:
        return cache_hit_ats(self.ledger)

    @property
    def link_schema(self) -> LinkSchema:
        raise UnplannedLinkSchemaError(
            "Run reads lazily and plans no automatic links; render with_link_schema "
            "results instead."
        )

    @property
    def column_format_errors(self) -> dict[str, DbtChartsError]:
        return self.ledger.column_format_errors

    def render_with_range_filters(
        self, query_name: str, variables: VariableValues | None
    ) -> Any:
        return render_with_range_filters(self.ctx, query_name, variables)

    def base_column_formats(self, source: str | None) -> RawTableFormats:
        return base_column_formats(self.ctx, source)

    def text_listings(
        self, base_dir: str, calls: frozenset[ListingCall]
    ) -> TextListings:
        return text_listings(self.ctx, base_dir, calls)

    def lookup(
        self, names: list[str], variables: VariableValues | None = None
    ) -> Lookup:
        return lookup_names(
            self.ctx, self.ledger, names, variables, resume_incremental=False
        )

    def run_misses(self, found: Lookup, max_workers: int) -> dict[str, Outcome]:
        return run_misses(self.ctx, self.ledger, found, max_workers)


def render_run(board: Board, run: Run, **options: Any) -> RenderResult:
    return render(board, run.ctx, run.ledger, **options)
