"""The continuous x domain a proven filter range pins: where it ends depends on
whether the mark sits at its bucket start (line, area) or spans the bucket (bar)."""

from __future__ import annotations

from datetime import date

import pytest

from dbt_charts.core.render.chart.time_unit_detect import temporal_range_domain

_YEAR = (date(2026, 1, 1), date(2026, 12, 31))
_MONTHS = [{"m": "2026-03-01"}, {"m": "2026-04-01"}]


@pytest.mark.parametrize(
    ("spans_bucket", "end"),
    [(False, "2026-12-01"), (True, "2027-01-01")],
    ids=["point-marks-end-at-the-last-bucket-start", "bars-end-at-the-bucket-end"],
)
def test_bucketed_domain_end(spans_bucket: bool, end: str) -> None:
    domain = temporal_range_domain(
        _YEAR, "yearmonth", [r["m"] for r in _MONTHS], 1, spans_bucket=spans_bucket
    )
    assert domain == ["2026-01-01", end]


@pytest.mark.parametrize("spans_bucket", [False, True])
def test_unbucketed_domain_keeps_the_whole_last_day(spans_bucket: bool) -> None:
    domain = temporal_range_domain(
        _YEAR, None, ["2026-03-05T10:00:00"], 1, spans_bucket=spans_bucket
    )
    assert domain == ["2026-01-01T00:00:00", "2027-01-01T00:00:00"]


def test_date_only_values_get_date_only_bounds() -> None:
    domain = temporal_range_domain(_YEAR, None, ["2026-03-05"], 1, spans_bucket=False)
    assert domain == ["2026-01-01", "2027-01-01"]


@pytest.mark.parametrize(
    "values",
    [
        pytest.param(["2025-12-01"], id="before-the-start"),
        pytest.param(["2027-01-02"], id="after-the-end"),
        pytest.param(["2026-03-05", "2026-03-05T10:00:00"], id="mixed-clocks"),
    ],
)
def test_a_value_outside_the_domain_leaves_the_axis_alone(values: list[str]) -> None:
    assert temporal_range_domain(_YEAR, None, values, 1, spans_bucket=False) is None
