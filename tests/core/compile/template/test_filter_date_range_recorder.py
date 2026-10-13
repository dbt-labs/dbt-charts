"""filter_date_range() reports each range it emits to a caller's list, as a side
effect: the helper still returns a plain SQL string and adds no context name."""

from __future__ import annotations

from datetime import date, datetime

import pytest

from dbt_charts.core.compile.template.parameterized import (
    DateRangeFilter,
    render_parameterized,
)
from dbt_charts.core.dialects import get_dialect

_SQL = "SELECT 1 FROM t WHERE {{ filter_date_range('t.created_at', date_range) }}"


def test_render_parameterized_records_date_bounds() -> None:
    filters: list[DateRangeFilter] = []
    out = render_parameterized(
        _SQL,
        {"date_range": [date(2026, 3, 1), date(2026, 3, 31)]},
        dialect=get_dialect("duckdb"),
        range_filters=filters,
    )
    assert filters == [
        DateRangeFilter("t.created_at", date(2026, 3, 1), date(2026, 3, 31))
    ]
    assert isinstance(out.sql, str)


def test_iso_strings_record_as_dates_and_timed_ranges_record_nothing() -> None:
    filters: list[DateRangeFilter] = []
    render_parameterized(
        _SQL,
        {"date_range": ["2026-03-01", "2026-03-31"]},
        dialect=get_dialect("duckdb"),
        range_filters=filters,
    )
    render_parameterized(
        _SQL,
        {"date_range": [datetime(2026, 4, 1, 8), datetime(2026, 4, 30, 17)]},
        dialect=get_dialect("duckdb"),
        range_filters=filters,
    )
    assert [(f.start, f.end) for f in filters] == [
        (date(2026, 3, 1), date(2026, 3, 31)),
    ]


def test_empty_range_records_nothing() -> None:
    filters: list[DateRangeFilter] = []
    render_parameterized(
        _SQL,
        {"date_range": None},
        dialect=get_dialect("duckdb"),
        range_filters=filters,
    )
    assert filters == []


@pytest.mark.parametrize("bound", ["2026/03/01", "not a date"])
def test_unparseable_bound_records_nothing_and_does_not_raise(bound: str) -> None:
    filters: list[DateRangeFilter] = []
    out = render_parameterized(
        _SQL,
        {"date_range": [bound, "2026-03-31"]},
        dialect=get_dialect("duckdb"),
        range_filters=filters,
    )
    assert "BETWEEN" in out.sql
    assert filters == []
