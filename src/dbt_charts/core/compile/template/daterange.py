"""Daterange presets: rolling windows a daterange value names instead of dates.

A preset id is a whole daterange value (`default: last_90_days`, `?span=last_90_days`)
and resolves against the date of the render, so a board or link stays rolling.
"""

from collections.abc import Callable
from datetime import date, timedelta
from itertools import chain
from typing import Final

_Window = Callable[[date], tuple[date, date]]


def _last_days(n: int) -> _Window:
    return lambda today: (today - timedelta(days=n - 1), today)


def _last_12_months(today: date) -> tuple[date, date]:
    # Feb 29 has no counterpart a year back; the window then starts on Mar 1.
    if today.month == 2 and today.day == 29:
        return date(today.year - 1, 3, 1), today
    return today.replace(year=today.year - 1) + timedelta(days=1), today


# The control's preset rail draws each group followed by a divider.
DATERANGE_PRESET_GROUPS: Final[tuple[tuple[tuple[str, _Window], ...], ...]] = (
    (
        ("today", lambda today: (today, today)),
        ("last_7_days", _last_days(7)),
        ("last_28_days", _last_days(28)),
        ("last_90_days", _last_days(90)),
        ("last_12_months", _last_12_months),
    ),
    (
        ("month_to_date", lambda today: (today.replace(day=1), today)),
        ("year_to_date", lambda today: (today.replace(month=1, day=1), today)),
    ),
)

DATERANGE_PRESETS: Final[dict[str, _Window]] = dict(
    chain.from_iterable(DATERANGE_PRESET_GROUPS)
)


def check_daterange_preset(preset: str) -> None:
    """Raise ``ValueError`` naming the known ids unless ``preset`` is one."""
    if preset not in DATERANGE_PRESETS:
        raise ValueError(
            f"daterange value must be [start, end] or a preset "
            f"({', '.join(DATERANGE_PRESETS)}), got {preset!r}"
        )


def resolve_daterange_preset(preset: str, today: date) -> tuple[date, date]:
    """The inclusive ``(start, end)`` a preset names on ``today``."""
    check_daterange_preset(preset)
    return DATERANGE_PRESETS[preset](today)


def resolve_daterange_preset_iso(preset: str, today: date) -> list[str]:
    """The preset's window as the ISO ``[start, end]`` an authored range is."""
    return [d.isoformat() for d in resolve_daterange_preset(preset, today)]
