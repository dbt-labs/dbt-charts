"""ReplayDataProvider serves a recording and refuses to guess."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from dbt_charts.core.execute.chart_data_provider import ChartDataProvider
from dbt_charts.core.execute.replay_provider import (
    ReplayDataProvider,
    UnrecordedQueryError,
    VariableMismatchError,
)

_RECORDED_AT = datetime(2026, 8, 1, 12, 0, tzinfo=UTC)


def _provider(**kwargs: object) -> ReplayDataProvider:
    defaults: dict[str, object] = {
        "rows_by_query": {"sales": [{"month": "Jan", "revenue": 100}]},
        "recorded_at": _RECORDED_AT,
    }
    defaults.update(kwargs)
    return ReplayDataProvider(**defaults)  # type: ignore[arg-type]


def _replay_is_a_provider(replay: ReplayDataProvider) -> ChartDataProvider:
    """Type-level assertion, enforced by the type checkers in CI."""
    return replay


class TestServesTheRecording:
    def test_returns_recorded_rows(self) -> None:
        assert _provider().execute_query("sales") == [{"month": "Jan", "revenue": 100}]

    def test_strips_the_queries_prefix(self) -> None:
        """Render passes both bare and `queries.`-prefixed names."""
        assert _provider().execute_query("queries.sales") == [
            {"month": "Jan", "revenue": 100}
        ]

    def test_cache_hit_ats_reports_the_recording_time(self) -> None:
        """This becomes the board's "data as of" stamp.

        Returning empty would make render fall back to render time, so a
        replayed board would claim data captured days ago is current.
        """
        assert _provider().cache_hit_ats == [_RECORDED_AT]


class TestRefusesToGuess:
    def test_unrecorded_query_raises(self) -> None:
        with pytest.raises(UnrecordedQueryError, match="not in the recording"):
            _provider().execute_query("missing")

    def test_unrecorded_query_does_not_return_empty_rows(self) -> None:
        """An empty rowset renders as "no data" — plausible and wrong.

        This is the specific silent-failure this provider exists to avoid, so it
        is pinned separately from the raise above.
        """
        with pytest.raises(UnrecordedQueryError):
            _provider().execute_query("missing")

    def test_mismatched_variables_raise(self) -> None:
        replay = _provider(variables={"region": "west"})
        with pytest.raises(VariableMismatchError, match="one variable state"):
            replay.execute_query("sales", {"region": "east"})

    def test_matching_variables_are_served(self) -> None:
        replay = _provider(variables={"region": "west"})
        assert replay.execute_query("sales", {"region": "west"})

    def test_none_variables_skip_the_check(self) -> None:
        """Render passes None where a query takes no variables."""
        assert _provider(variables={"region": "west"}).execute_query("sales", None)
