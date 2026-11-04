"""Daterange presets: relative windows a daterange value names instead of dates.

A preset is a whole daterange value (`default: last_90_days`, `?span=last_90_days`)
and resolves against the date of the render, so a board or link stays rolling.
The names are a small grammar, not a list; `parse_daterange_preset` is the one
place that knows it.
"""

import re
from collections.abc import Generator
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass
from datetime import date, timedelta
from typing import Final, Literal

from dbt_charts.core.diagnostics import ERR_INTERNAL
from dbt_charts.core.diagnostics.execution import ExecutionError

Kind = Literal[
    "today",
    "yesterday",
    "last",
    "previous",
    "before_previous",
    "this",
    "to_date",
]
Unit = Literal["day", "week", "month", "quarter", "year"]

_DAY: Final = timedelta(days=1)
_UNITS: Final[dict[str, Unit]] = {
    unit: unit for unit in ("day", "week", "month", "quarter", "year")
}
_ROLLING_KINDS: Final[dict[str, Kind]] = {
    kind: kind for kind in ("last", "previous", "before_previous")
}
_MONTHS_IN: Final[dict[str, int]] = {"month": 1, "quarter": 3, "year": 12}

GRAMMAR: Final = (
    "today, yesterday, last_<N>_<unit>, previous_<N>_<unit>, "
    "before_previous_<N>_<unit>, this_<unit>, "
    "<unit>_to_date (units: days, weeks, months, quarters, years, singular or "
    "plural; this_ and "
    "_to_date take week, month, quarter or year; fiscal_ may precede quarter or "
    "year, e.g. this_fiscal_quarter, last_4_fiscal_quarters)"
)

_ROLLING = re.compile(
    r"(?P<kind>last|previous|before_previous)_(?P<n>[1-9]\d*)_"
    r"(?P<fiscal>fiscal_)?"
    r"(?P<unit>day|week|month|quarter|year)s?"
)
_THIS = re.compile(r"this_(?P<fiscal>fiscal_)?(?P<unit>week|month|quarter|year)")
_TO_DATE = re.compile(r"(?P<fiscal>fiscal_)?(?P<unit>week|month|quarter|year)_to_date")


@dataclass(frozen=True)
class BoardClock:
    """What presets resolve on: the board's today, the month its fiscal year
    opens, and the first day of its week (0 is Sunday, as the picker reads it)."""

    today: date
    fiscal_start_month: int
    week_start: int
    time_zone: str


@dataclass(frozen=True)
class DaterangePreset:
    kind: Kind
    unit: Unit
    n: int = 1
    fiscal: bool = False

    def window(self, clock: BoardClock) -> tuple[date | None, date]:
        """The inclusive ``(start, end)`` this preset names on ``clock``.

        ``before_previous`` has no lower bound: its start is ``None``.
        """
        today = clock.today
        if self.kind == "today":
            return today, today
        if self.kind == "yesterday":
            return today - timedelta(days=1), today - timedelta(days=1)
        anchor = clock.fiscal_start_month if self.fiscal else 1
        start = _unit_start(today, self.unit, anchor, clock.week_start)
        if self.kind == "to_date":
            return start, today
        if self.kind == "this":
            return start, _shift_unit(start, self.unit, 1) - _DAY
        if self.kind == "last":
            # Calendar-aligned: the current unit and the N-1 before it, to today.
            return _shift_unit(start, self.unit, 1 - self.n), today
        first = _shift_unit(start, self.unit, -self.n)
        if self.kind == "before_previous":
            return None, first - _DAY
        # previous: the N complete units before the current one.
        return first, start - _DAY


def _add_months(d: date, months: int) -> date:
    index = d.year * 12 + d.month - 1 + months
    year, month = divmod(index, 12)
    first_of_next = date(year + (month + 1) // 12, (month + 1) % 12 + 1, 1)
    return d.replace(
        year=year, month=month + 1, day=min(d.day, (first_of_next - _DAY).day)
    )


def _unit_start(d: date, unit: Unit, anchor_month: int, week_start: int) -> date:
    if unit == "day":
        return d
    if unit == "week":
        # `week_start` counts from Sunday; `weekday()` from Monday.
        return d - timedelta(days=(d.weekday() - (week_start - 1)) % 7)
    months_in = _MONTHS_IN[unit]
    since_anchor = (d.month - anchor_month) % 12
    return _add_months(d.replace(day=1), -(since_anchor % months_in))


def _shift_unit(start: date, unit: Unit, count: int) -> date:
    if unit == "day":
        return start + timedelta(days=count)
    if unit == "week":
        return start + timedelta(weeks=count)
    return _add_months(start, count * _MONTHS_IN[unit])


def _parse(name: str) -> DaterangePreset | None:
    if name == "today" or name == "yesterday":
        return DaterangePreset(name, "day")
    kind: Kind
    n = 1
    if match := _ROLLING.fullmatch(name):
        kind = _ROLLING_KINDS[match["kind"]]
        n = int(match["n"])
    elif match := _THIS.fullmatch(name):
        kind = "this"
    elif match := _TO_DATE.fullmatch(name):
        kind = "to_date"
    else:
        return None
    unit = _UNITS[match["unit"]]
    fiscal = match["fiscal"] is not None
    if fiscal and unit not in ("quarter", "year"):
        return None
    return DaterangePreset(kind, unit, n, fiscal)


def is_daterange_preset(value: str) -> bool:
    return _parse(value) is not None


def parse_daterange_preset(name: str) -> DaterangePreset:
    """The preset ``name`` spells; ``ValueError`` stating the grammar otherwise."""
    preset = _parse(name)
    if preset is None:
        raise ValueError(
            f"daterange value must be [start, end] or a preset ({GRAMMAR}), "
            f"got {name!r}"
        )
    return preset


def resolve_daterange_preset(
    preset: str, clock: BoardClock
) -> tuple[date | None, date]:
    """The inclusive ``(start, end)`` a preset names on ``clock``."""
    return parse_daterange_preset(preset).window(clock)


def resolve_daterange_preset_iso(preset: str, clock: BoardClock) -> list[str]:
    """The preset's window as the ISO ``[start, end]`` an authored range is."""
    start, end = resolve_daterange_preset(preset, clock)
    return ["" if start is None else start.isoformat(), end.isoformat()]


# No default: resolving a preset outside a render has no board clock.
_BOARD_CLOCK: ContextVar[BoardClock] = ContextVar("dct_board_clock")


@contextmanager
def board_clock(clock: BoardClock) -> Generator[None]:
    """Scope the clock daterange presets resolve on."""
    token = _BOARD_CLOCK.set(clock)
    try:
        yield
    finally:
        _BOARD_CLOCK.reset(token)


def current_board_clock() -> BoardClock:
    """The clock of the render in progress."""
    try:
        return _BOARD_CLOCK.get()
    except LookupError:
        raise ExecutionError.from_code(
            ERR_INTERNAL,
            message="no board clock in scope: resolve presets inside board_clock()",
        ) from None


def board_clock_in_scope() -> bool:
    return _BOARD_CLOCK.get(None) is not None
