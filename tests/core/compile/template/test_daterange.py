"""Daterange presets resolve to inclusive windows ending on the render's date."""

from datetime import date

import pytest

from dbt_charts.core.compile.template.daterange import (
    resolve_daterange_preset,
    resolve_daterange_preset_iso,
)

_TODAY = date(2026, 10, 20)


@pytest.mark.parametrize(
    ("preset", "start"),
    [
        ("today", date(2026, 10, 20)),
        ("last_7_days", date(2026, 10, 14)),
        ("last_28_days", date(2026, 9, 23)),
        ("last_90_days", date(2026, 7, 23)),
        ("last_12_months", date(2025, 10, 21)),
        ("month_to_date", date(2026, 10, 1)),
        ("year_to_date", date(2026, 1, 1)),
    ],
)
def test_a_preset_ends_today(preset: str, start: date) -> None:
    assert resolve_daterange_preset(preset, _TODAY) == (start, _TODAY)


def test_last_12_months_from_a_leap_day_starts_on_march_first() -> None:
    leap_day = date(2028, 2, 29)
    assert resolve_daterange_preset("last_12_months", leap_day) == (
        date(2027, 3, 1),
        leap_day,
    )


def test_an_unknown_preset_names_the_known_ones() -> None:
    with pytest.raises(ValueError, match=r"last_90_days.*got 'last_91_days'"):
        resolve_daterange_preset("last_91_days", _TODAY)


def test_a_preset_as_iso_is_an_authored_range() -> None:
    assert resolve_daterange_preset_iso("month_to_date", _TODAY) == [
        "2026-10-01",
        "2026-10-20",
    ]
