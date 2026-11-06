"""The data seam the post-resolve render path depends on.

``render_board_svg`` and everything below it need only three things from the
execution layer: fetch a query's rows and their declared types, ask when the cache entries backing them
were written, and ask when this render happened. ``QueryResults`` provides them,
and so does ``ReplayDataProvider``: replaying a recorded board from a serialized
artifact has rows on hand and no warehouse to reach.

Typing the render path against this Protocol rather than the concrete
``QueryResults`` is what makes such a provider a type-checked participant instead
of a duck-typed stand-in smuggled past an annotation. It is also a step toward the
boundary ``core/AGENTS.md`` commits to — render as a pure function of
``(Resolved*, data)`` plus foreign-format translation.

**Deliberately absent**, because the post-resolve path does not use them:
``board``, ``link_schema`` and the other resolve-time reads. The resolve
path takes ``ResolveDataProvider``, which adds them; ``QueryResults`` satisfies
it. ``render()`` collects its queries up front and reads ``QueryResults``, which
never executes a query.

Keep this Protocol minimal. Widening it to accommodate a new render-time reach
into the execution layer is the signal that the reach itself belongs earlier, on
the resolve path, where the value can be baked into ``Resolved*``.
"""

from __future__ import annotations

from datetime import datetime
from typing import TYPE_CHECKING, Protocol

from dbt_charts.core.execute.cache_backend import CacheRows, ResultRows

if TYPE_CHECKING:
    from dbt_charts.core.compile.models.board.normalized import Board, VariableValues
    from dbt_charts.core.compile.models.chart.normalized import Chart
    from dbt_charts.core.compile.template.listings import ListingCall, TextListings
    from dbt_charts.core.compile.template.parameterized import DateRangeFilter
    from dbt_charts.core.dbt_manifest import RawTableFormats
    from dbt_charts.core.diagnostics.base import DbtChartsError
    from dbt_charts.core.execute.link_schema import LinkSchema


# Not @runtime_checkable: `cache_hit_ats` is a property, so this is a data
# protocol — `issubclass` is unsupported against it and `isinstance` would only
# check attribute presence, not signatures. Conformance is a type-checker
# concern, asserted at the seam in test_chart_data_provider.py.
class ChartDataProvider(Protocol):
    """Supplies query results and board-text listings to the post-resolve render
    path."""

    def execute_query(self, query_name: str, variables: VariableValues) -> CacheRows:
        """Return the rows for ``query_name`` under ``variables``.

        Every post-resolve call site passes exactly ``(query_name, variables)``
        with a real mapping. An implementation may accept more than the Protocol
        promises (``ReplayDataProvider`` takes ``VariableValues | None``);
        declaring only what the seam uses keeps a provider from modeling cache
        control it has no concept of.
        """
        ...

    def execute_query_result(
        self, query_name: str, variables: VariableValues
    ) -> ResultRows:
        """:meth:`execute_query` with the declared type of each column: what the
        engine draws from: a declared non-string kind decides, a string column
        keeps shape detection."""
        ...

    def text_listings(
        self, base_dir: str, calls: frozenset[ListingCall]
    ) -> TextListings:
        """What the listing *calls* in text written under *base_dir* return."""
        ...

    @property
    def cache_hit_ats(self) -> list[datetime]:
        """Write times of the cache entries served this render.

        Empty means everything ran fresh. Render takes ``min()`` of these for the
        board's "data as of" stamp, falling back to render time when empty.
        """
        ...

    @property
    def rendered_at(self) -> datetime:
        """The instant this render stands for, read once by the provider.

        Render never reads a clock of its own: the same board over the same data
        and the same ``rendered_at`` is the same bytes, so two renders compare
        equal without stripping timestamps first.
        """
        ...


class ResolveDataProvider(ChartDataProvider, Protocol):
    """A ``ChartDataProvider`` plus the resolve-time context the board resolve
    path reads: the board's link settings, the schema and foreign keys auto-link
    needs (planned before render), and the dbt column formats lineage starts
    from. ``QueryResults`` satisfies it."""

    @property
    def board(self) -> Board: ...

    @property
    def link_schema(self) -> LinkSchema: ...

    def base_column_formats(self, source: str | None) -> RawTableFormats: ...

    @property
    def column_format_errors(self) -> dict[str, DbtChartsError]: ...

    def render_with_range_filters(
        self, query_name: str, variables: VariableValues
    ) -> tuple[str, str | None, list[DateRangeFilter]] | None: ...


def chart_rows(
    chart: Chart, provider: ChartDataProvider, variables: VariableValues
) -> ResultRows:
    """The typed rows behind *chart*; a chart with no query (blank, callout) has
    none."""
    if chart.query_name is None:
        return ResultRows([], {})
    return provider.execute_query_result(chart.query_name, variables)
