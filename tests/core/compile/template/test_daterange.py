"""Daterange presets resolve to inclusive windows ending on the render's date."""

from datetime import date

import pytest

from dbt_charts.core.compile.template.daterange import (
    BoardClock,
    is_daterange_preset,
    resolve_daterange_preset,
    resolve_daterange_preset_iso,
)

_TODAY = date(2026, 10, 20)  # a Tuesday
_CLOCK = BoardClock(today=_TODAY, fiscal_start_month=1, week_start=1)


def _window(
    preset: str,
    today: date = _TODAY,
    fiscal_start_month: int = 1,
    week_start: int = 1,
) -> tuple:
    return resolve_daterange_preset(
        preset, BoardClock(today, fiscal_start_month, week_start)
    )


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
def test_the_original_presets_keep_their_windows(preset: str, start: date) -> None:
    assert resolve_daterange_preset(preset, _CLOCK) == (start, _TODAY)


@pytest.mark.parametrize(
    ("preset", "window"),
    [
        ("yesterday", (date(2026, 10, 19), date(2026, 10, 19))),
        ("last_1_day", (_TODAY, _TODAY)),
        ("last_30_days", (date(2026, 9, 21), _TODAY)),
        ("last_2_weeks", (date(2026, 10, 7), _TODAY)),
        ("last_3_months", (date(2026, 7, 21), _TODAY)),
        ("last_2_quarters", (date(2026, 4, 21), _TODAY)),
        ("last_13_months", (date(2025, 9, 21), _TODAY)),
        ("previous_3_days", (date(2026, 10, 17), date(2026, 10, 19))),
        ("previous_1_week", (date(2026, 10, 12), date(2026, 10, 18))),
        ("previous_3_months", (date(2026, 7, 1), date(2026, 9, 30))),
        ("previous_3_quarters", (date(2026, 1, 1), date(2026, 9, 30))),
        ("previous_1_year", (date(2025, 1, 1), date(2025, 12, 31))),
        ("this_week", (date(2026, 10, 19), date(2026, 10, 25))),
        ("this_month", (date(2026, 10, 1), date(2026, 10, 31))),
        ("this_quarter", (date(2026, 10, 1), date(2026, 12, 31))),
        ("this_year", (date(2026, 1, 1), date(2026, 12, 31))),
        ("week_to_date", (date(2026, 10, 19), _TODAY)),
        ("quarter_to_date", (date(2026, 10, 1), _TODAY)),
    ],
)
def test_the_grammar_forms(preset: str, window: tuple) -> None:
    assert resolve_daterange_preset(preset, _CLOCK) == window


def test_a_monday_week_holds_a_sunday_in_the_week_before() -> None:
    assert _window("this_week", date(2026, 10, 25)) == (
        date(2026, 10, 19),
        date(2026, 10, 25),
    )


def test_weeks_follow_the_board_week_start() -> None:
    # Index 0 is Sunday, as the picker calendar reads it.
    assert _window("this_week", week_start=0) == (
        date(2026, 10, 18),
        date(2026, 10, 24),
    )
    assert _window("previous_1_week", week_start=0) == (
        date(2026, 10, 11),
        date(2026, 10, 17),
    )
    assert _window("week_to_date", date(2026, 10, 25), week_start=0) == (
        date(2026, 10, 25),
        date(2026, 10, 25),
    )
    assert _window("this_week", week_start=6) == (
        date(2026, 10, 17),
        date(2026, 10, 23),
    )


def test_month_arithmetic_clamps_to_month_end() -> None:
    assert _window("last_1_month", date(2026, 3, 31)) == (
        date(2026, 3, 1),
        date(2026, 3, 31),
    )
    assert _window("last_12_months", date(2028, 2, 29)) == (
        date(2027, 3, 1),
        date(2028, 2, 29),
    )


def test_fiscal_forms_follow_the_fiscal_start_month() -> None:
    # Fiscal year opens in July: today sits in Q2 (Oct-Dec) of FY starting 2026-07.
    assert _window("this_fiscal_year", fiscal_start_month=7) == (
        date(2026, 7, 1),
        date(2027, 6, 30),
    )
    assert _window("this_fiscal_quarter", fiscal_start_month=7) == (
        date(2026, 10, 1),
        date(2026, 12, 31),
    )
    assert _window("fiscal_year_to_date", fiscal_start_month=7) == (
        date(2026, 7, 1),
        _TODAY,
    )
    assert _window("previous_1_fiscal_year", fiscal_start_month=7) == (
        date(2025, 7, 1),
        date(2026, 6, 30),
    )
    assert _window("previous_2_fiscal_quarters", fiscal_start_month=2) == (
        date(2026, 2, 1),
        date(2026, 7, 31),
    )
    assert _window("fiscal_quarter_to_date", fiscal_start_month=2) == (
        date(2026, 8, 1),
        _TODAY,
    )


@pytest.mark.parametrize(
    "name",
    [
        "last_0_days",
        "last_days",
        "last_3_fortnights",
        "last_2_fiscal_years",
        "this_fiscal_month",
        "this_day",
        "day_to_date",
        "fiscal_week_to_date",
        "next_7_days",
        "Last_7_days",
        "",
    ],
)
def test_a_name_outside_the_grammar_states_the_grammar(name: str) -> None:
    assert not is_daterange_preset(name)
    with pytest.raises(
        ValueError, match=rf"last_<N>_<unit>.*<unit>_to_date.*got {name!r}"
    ):
        resolve_daterange_preset(name, _CLOCK)


def test_a_preset_as_iso_is_an_authored_range() -> None:
    assert resolve_daterange_preset_iso("month_to_date", _CLOCK) == [
        "2026-10-01",
        "2026-10-20",
    ]


def test_the_grammar_text_allows_singular_and_plural_units() -> None:
    from dbt_charts.core.compile.template.daterange import GRAMMAR

    assert "singular or plural" in GRAMMAR
