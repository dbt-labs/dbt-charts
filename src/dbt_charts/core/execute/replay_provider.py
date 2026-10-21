"""Serve a board's recorded rows back, with no warehouse.

A resolved-board artifact plus the rows it was rendered against is enough to
re-render that board exactly: the post-resolve render path asks only for rows
and cache timestamps (see ``chart_data_provider``). This provider answers
both from a recording, so a replayed render opens no connection and runs no
query.

Every unknown becomes an error rather than a plausible-looking default. A board
is a claim about data; serving it with silently-empty rows, or with rows captured
under different variable values, produces something that looks right and is
wrong — the failure mode this codebase treats as worse than a crash.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import TYPE_CHECKING

from dbt_charts.core.execute.cache_backend import CacheRows

if TYPE_CHECKING:
    from dbt_charts.core.compile.models.board.normalized import VariableValues
    from dbt_charts.core.compile.template.listings import ListingCall, TextListings


class UnrecordedQueryError(KeyError):
    """The board asked for a query the recording does not contain.

    Means the recording and the resolved board came from different emits, or the
    recording is truncated. Returning ``[]`` instead would render an empty chart
    that looks like "no data" rather than "artifact is broken".
    """


class VariableMismatchError(ValueError):
    """Replay was asked for rows under variable values it did not record.

    A recording captures one variable state. Serving its rows under different
    values would silently answer a question the data cannot answer — the board
    would render successfully and show the wrong numbers.
    """


class UnrecordedListingError(KeyError):
    """The board's text lists boards, and a recording holds no project tree."""


@dataclass(frozen=True)
class ReplayDataProvider:
    """A ``ChartDataProvider`` backed by a recording instead of a warehouse.

    Attributes:
        rows_by_query: Recorded rows keyed by query name (no ``queries.`` prefix).
        recorded_at: When the rows were captured. Required, not optional: it
            becomes the board's "data as of" stamp, and a replayed board with no
            timestamp would fall back to render time and claim the data is fresh.
        variables: The variable values the rows were captured under. Replay
            refuses to serve them under any other values.
    """

    rows_by_query: dict[str, CacheRows]
    recorded_at: datetime
    variables: VariableValues = field(default_factory=dict)

    def execute_query(
        self,
        query_name: str,
        variables: VariableValues | None = None,
    ) -> CacheRows:
        """Return the recorded rows for ``query_name``.

        Raises:
            UnrecordedQueryError: If the recording has no entry for the query.
            VariableMismatchError: If ``variables`` differs from what was recorded.
        """
        if variables is not None and dict(variables) != self.variables:
            raise VariableMismatchError(
                f"Replay recorded query {query_name!r} under variables "
                f"{self.variables!r}, but was asked for {dict(variables)!r}. "
                "A recording captures one variable state; re-record to serve "
                "another."
            )
        key = query_name.removeprefix("queries.")
        if key not in self.rows_by_query:
            raise UnrecordedQueryError(
                f"Query {key!r} is not in the recording "
                f"({sorted(self.rows_by_query)}). The board and its recorded "
                "data came from different emits, or the recording is truncated."
            )
        return self.rows_by_query[key]

    def text_listings(
        self, base_dir: str, calls: frozenset[ListingCall]
    ) -> TextListings:
        """Always raises: ``render_board_from_artifact`` rejects listing boards
        before rendering, so reaching this is a caller bypassing that check."""
        raise UnrecordedListingError(
            f"Board text under {base_dir!r} lists boards ({sorted(calls)}), "
            "which a recording does not capture."
        )

    @property
    def cache_hit_ats(self) -> list[datetime]:
        """The recording time, so the board stamps "data as of" honestly."""
        return [self.recorded_at]

    @property
    def rendered_at(self) -> datetime:
        """The recording time: a replay reproduces the recorded render, its
        stamp included, so one recording replays to the same bytes on any day."""
        return self.recorded_at
