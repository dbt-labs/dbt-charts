"""Predicate-based time-unit detection and smart-default labelExpr for temporal axes.

detect_time_unit: pure function; given distinct non-null x-field values,
returns the VL timeUnit string or None (continuous/sub-daily).

the engine's label rule: returns a Vega expression for an
encoding time unit + label time unit pair.

Bucket string vocabulary (pattern → VL timeUnit):
  YYYY-MM         → yearmonth   (2024-01)
  Mon YYYY        → yearmonth   (Jan 2024)
  MM/YYYY         → yearmonth   (01/2024, US-only)
  YYYY-Qn         → yearquarter (2024-Q1, canonical ISO quarter)
  Qn YYYY         → yearquarter (Q1 2024)
  YYYYQn          → yearquarter (2024Q1)
  FYnnnn          → year        (FY2024 → Jan 1)
  MM/DD/YYYY      → yearmonthdate (01/15/2024, US-only)
  Mon DD[,] YYYY  → yearmonthdate (Jan 15, 2024)
  YYYY-Www        → yearweek    (2024-W01, canonical ISO week)
  W[eek ]N YYYY   → yearweek    (W32 2024, Week 32 2024)
  Half-year (H1 YYYY, YYYY-H1): not supported; treated as unparseable.
"""

from __future__ import annotations

import datetime as dt
import itertools
import re
import statistics
from dataclasses import dataclass
from typing import Any, Literal

from dbt_charts.core.constants.axis import MAX_ORDINAL_BUCKETS
from dbt_charts.core.font_measure import FontMeasurer
from dbt_charts.core.render.chart.artifacts import ChartRenderData
from dbt_charts.core.text.format_d3 import board_word, portable_strftime
from dbt_charts.core.utils import is_year_shaped

# ISO date: "2024-01-15"
_ISO_DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
# ISO datetime with T separator ("2024-01-15T14:30:00") or space from DB driver str()
_ISO_DATETIME_RE = re.compile(r"^\d{4}-\d{2}-\d{2}[T ]")
# ISO week-year: "2024-W32" (weeks 01–53)
_ISO_WEEK_RE = re.compile(r"^\d{4}-W(0[1-9]|[1-4]\d|5[0-3])$")
# Calendar quarter: "2024-Q3"
_ISO_QUARTER_RE = re.compile(r"^\d{4}-Q[1-4]$")

# ── Bucket-string patterns ──────────────────────────────────────────────────
# YYYY-MM: 2024-01 (valid month 01-12)
_YEARMONTH_STR_RE = re.compile(r"^\d{4}-(0[1-9]|1[0-2])$")
# Mon YYYY: Jan 2024
_MON_YYYY_RE = re.compile(
    r"^(Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)\s+(\d{4})$", re.IGNORECASE
)
# MM/YYYY: 01/2024 (US month/year)
_MM_YYYY_RE = re.compile(r"^(0[1-9]|1[0-2])/(\d{4})$")
# Qn YYYY: Q1 2024 (space optional)
_Q_YYYY_RE = re.compile(r"^Q([1-4])\s*(\d{4})$", re.IGNORECASE)
# YYYYQn: 2024Q1
_YYYY_Q_RE = re.compile(r"^(\d{4})Q([1-4])$", re.IGNORECASE)
# FYnnnn: FY2024
_FY_RE = re.compile(r"^FY(\d{4})$", re.IGNORECASE)
# MM/DD/YYYY: 01/15/2024 (US month/day/year)
_MM_DD_YYYY_RE = re.compile(r"^(0[1-9]|1[0-2])/(0[1-9]|[12]\d|3[01])/(\d{4})$")
# Mon DD[,] YYYY: Jan 15, 2024 or Jan 15 2024
_MON_DD_YYYY_RE = re.compile(
    r"^(Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)\s+(\d{1,2}),?\s+(\d{4})$",
    re.IGNORECASE,
)
# W[eek ]N YYYY: W32 2024, Week 32 2024
_WEEK_SPELLED_RE = re.compile(r"^W(?:eek\s*)?(\d{1,2})\s+(\d{4})$", re.IGNORECASE)

_MONTH_ABBR: dict[str, int] = {
    "jan": 1,
    "feb": 2,
    "mar": 3,
    "apr": 4,
    "may": 5,
    "jun": 6,
    "jul": 7,
    "aug": 8,
    "sep": 9,
    "oct": 10,
    "nov": 11,
    "dec": 12,
}
_QUARTER_FIRST_MONTH_LOOKUP: dict[int, int] = {1: 1, 2: 4, 3: 7, 4: 10}

# Ordered family list: (name, regex).  First match wins in _classify_bucket.
_BUCKET_FAMILIES: list[tuple[str, re.Pattern[str]]] = [
    ("iso_week", _ISO_WEEK_RE),
    ("iso_quarter", _ISO_QUARTER_RE),
    ("yearmonth_str", _YEARMONTH_STR_RE),
    ("mon_yyyy", _MON_YYYY_RE),
    ("mm_yyyy", _MM_YYYY_RE),
    ("q_yyyy", _Q_YYYY_RE),
    ("yyyy_q", _YYYY_Q_RE),
    ("fy", _FY_RE),
    ("mm_dd_yyyy", _MM_DD_YYYY_RE),
    ("mon_dd_yyyy", _MON_DD_YYYY_RE),
    ("week_spelled", _WEEK_SPELLED_RE),
]


def _classify_bucket(v: str) -> str | None:
    """Return the format-family name for a bucket string, or None."""
    for name, pattern in _BUCKET_FAMILIES:
        if pattern.match(v):
            return name
    return None


def _parse_bucket_string(value: str) -> dt.date | None:
    """Return the anchor date (first instant of bucket) for a labeled format.

    Returns None for unrecognized strings. Does NOT raise — invalid ISO weeks
    that pass the regex (e.g. W53 in a 52-week year) return None here; the
    error is raised only by the explicit ``_week_to_iso`` converter used in
    ``normalize_labeled_temporal``.
    """
    if _YEARMONTH_STR_RE.match(value):
        return dt.date(int(value[:4]), int(value[5:7]), 1)
    m = _MON_YYYY_RE.match(value)
    if m:
        month = _MONTH_ABBR[m.group(1).lower()]
        return dt.date(int(m.group(2)), month, 1)
    m = _MM_YYYY_RE.match(value)
    if m:
        return dt.date(int(m.group(2)), int(m.group(1)), 1)
    m = _Q_YYYY_RE.match(value)
    if m:
        return dt.date(int(m.group(2)), _QUARTER_FIRST_MONTH_LOOKUP[int(m.group(1))], 1)
    m = _YYYY_Q_RE.match(value)
    if m:
        return dt.date(int(m.group(1)), _QUARTER_FIRST_MONTH_LOOKUP[int(m.group(2))], 1)
    m = _FY_RE.match(value)
    if m:
        return dt.date(int(m.group(1)), 1, 1)
    m = _MM_DD_YYYY_RE.match(value)
    if m:
        try:
            return dt.date(int(m.group(3)), int(m.group(1)), int(m.group(2)))
        except ValueError:
            return None
    m = _MON_DD_YYYY_RE.match(value)
    if m:
        month = _MONTH_ABBR[m.group(1).lower()]
        try:
            return dt.date(int(m.group(3)), month, int(m.group(2)))
        except ValueError:
            return None
    m = _WEEK_SPELLED_RE.match(value)
    if m:
        week, year = int(m.group(1)), int(m.group(2))
        try:
            return dt.date.fromisocalendar(year, week, 1)
        except ValueError:
            return None
    # ISO week/quarter: delegate to existing helpers (avoid duplication);
    # return None on invalid week numbers (the convert path raises separately).
    if _ISO_WEEK_RE.match(value):
        year, week = int(value[:4]), int(value[6:])
        try:
            return dt.date.fromisocalendar(year, week, 1)
        except ValueError:
            return None
    if _ISO_QUARTER_RE.match(value):
        q = int(value[6])
        return dt.date(int(value[:4]), _QUARTER_FIRST_MONTH_LOOKUP[q], 1)
    return None


_TIME_UNIT_TO_VL: dict[str, str] = {
    "monthofyear": "month",
    "dayofweek": "day",
    "dayofmonth": "date",
    "dayofyear": "dayofyear",
    "hourofday": "hours",
}

# Calendar-bucketed units → default ordinal scale.
# Distinct from time-part units (monthofyear etc.) which stay temporal.
BUCKETED_CALENDAR_UNITS: frozenset[str] = frozenset(
    {"year", "yearquarter", "yearmonth", "yearweek", "yearmonthdate"}
)

# Day/week buckets accumulate ~52-365 slots per year of span, so a fine grain
# is either within the scaffold budget or it is not — there is no second,
# row-count question worth asking of it. Coarse grains need both: the budget
# (a decade-spaced series names "year" honestly and still owes ten empty
# bands per bar) and a plain cap on how many distinct values a band axis may
# carry.
FINE_BUCKET_UNITS: frozenset[str] = frozenset({"yearweek", "yearmonthdate"})

# Cyclic time-part units that remain temporal (not ordinal).
TIME_PART_UNITS: frozenset[str] = frozenset(_TIME_UNIT_TO_VL.keys())

# Label-cadence coarsening chain: when labels don't fit at the current grain,
# the overlap resolver steps one rung coarser and re-measures. day/week collapse
# to month, then quarter, then year. ``year`` is terminal.
_COARSER_LABEL_UNIT: dict[str, str | None] = {
    "yearmonthdate": "yearmonth",
    "yearweek": "yearmonth",
    "yearmonth": "yearquarter",
    "yearquarter": "year",
    "year": None,
}


def next_coarser_label_unit(label_unit: str) -> str | None:
    """Return the next-coarser bucketed label grain, or None if already coarsest.

    Drives the cadence ladder: day/week → month → quarter → year. ``year`` is
    terminal (None). Raises ValueError for any non-bucketed grain — cadence
    stepping only applies to BUCKETED_CALENDAR_UNITS, so a cyclic time-part unit
    (monthofyear, dayofweek, …) reaching here is a caller bug, not a no-op.
    """
    if label_unit not in _COARSER_LABEL_UNIT:
        raise ValueError(
            f"next_coarser_label_unit: {label_unit!r} is not a bucketed calendar "
            f"grain; cadence stepping applies only to "
            f"{sorted(BUCKETED_CALENDAR_UNITS)}"
        )
    return _COARSER_LABEL_UNIT[label_unit]


def _fiscal_month_is_year_start(d: dt.date, fiscal_year_start_month: int) -> bool:
    """True when ``d`` falls in the fiscal year's first month.

    Mirrors ``_fiscal_month_expr``'s ``fiscal_month === 0`` condition — only
    half of the real predicate ``_month_label``/``_quarter_label`` use to
    decide whether a tick's labelExpr stacks a year-context row under the
    month/quarter text. The full predicate is ``anchor || fiscal_month ===
    0``; ``anchor`` (the first LABELED tick — ``visible_indices[0]`` in
    the engine, not the domain's literal index 0, which differ whenever
    a domain opens before its first opener) is the caller's responsibility —
    see ``_pair_clears``, which receives it as ``is_anchor``.
    """
    return (d.month - fiscal_year_start_month) % 12 == 0


# The bucketed grains whose label is one row of text. `yearmonthdate` is the
# exception: `_day_label` stacks the day number over a month/year context row,
# so it has no single string to return and is measured as the two rows it is.
SINGLE_ROW_CADENCE_GRAINS: frozenset[str] = BUCKETED_CALENDAR_UNITS - {"yearmonthdate"}


def cadence_label_text(
    d: dt.date,
    format_tu: str,
    authored_format: str | None = None,
) -> str:
    """The text one tick paints at ``format_tu``, in the vocabulary Vega uses.

    ``axis.labels.format`` replaces the per-grain
    TEXT wherever it is authored, on both sides of the engine: here for the
    measurement, and in the engine's label rule's own ``authored_format``
    for what Vega paints. One row of the author's spec per speaking tick —
    so the stacked second rows the per-grain vocabulary implies (the
    year-context row in ``_pair_clears``, the two-row day label) paint
    nothing under it, which is what lets the measurement drop them.

    It replaces only the text. Which ticks speak is a separate decision the
    format never touches — see the engine's label rule's gate.

    It covers ``SINGLE_ROW_CADENCE_GRAINS`` only, and is NOT the only place
    label text is decided: ``yearmonthdate``'s two-row shape
    (``_cadence_token_width`` below, and the engine's widths loop), the
    time-part vocabulary, and the ordinal bucket-string path — the last two in
    the engine — are separate, deliberately un-unified paths that a label-text
    fix has to visit too.

    Callers pass the *resolved* format: a predefined alias is already
    expanded to a raw d3-time-format spec by ``resolve_format`` at compile
    time, and a d3 *number* format (which ``portable_strftime`` cannot
    render) is filtered out by the ``is_time_format`` gate at the entry
    point (``authored_time_format``).
    """
    if authored_format is not None:
        return portable_strftime(d, authored_format)
    if format_tu == "year":
        return str(d.year)
    if format_tu == "yearquarter":
        return board_word("time.quarter", n=str((d.month - 1) // 3 + 1))
    if format_tu == "yearmonth":
        return portable_strftime(d, "%b")
    if format_tu == "yearweek":
        return portable_strftime(d, "W%V")
    raise ValueError(
        f"cadence_label_text: {format_tu!r} is not in SINGLE_ROW_CADENCE_GRAINS"
    )


def _cadence_token_width(
    d: dt.date,
    format_tu: str,
    measurer: FontMeasurer,
    size: float,
    position: int,
    fiscal_year_start_month: int,
    authored_format: str | None = None,
) -> float:
    """Rendered width of one label's own row (row 1) in its stable format vocabulary.

    A tick that carries the stacked year-context row (``_year_row_width``)
    paints that row as a SEPARATE text line, one row below this one, sharing
    only the flush edge's x position — never a wider block folded into this
    return via ``max()``. Row 1 can only ever collide with a neighbor's row
    1; the year row only with a neighbor's year row. See ``_pair_clears``,
    which checks the two rows as two independent clearances.

    An authored format collapses every grain to one row of its own text,
    ``yearmonthdate`` included (see ``cadence_label_text``).
    """
    if format_tu not in BUCKETED_CALENDAR_UNITS:
        # No row shape to measure. Checked BEFORE the authored-format branch,
        # not inside it: whether a grain is measurable is a property of the
        # grain, and gating the raise on an unrelated field would give the
        # same input two opposite verdicts. Reachable — support_table_
        # attachment's ladder call derives format_tu from an authorable
        # labels.time_unit with no BUCKETED_CALENDAR_UNITS precondition, so a
        # time-part unit ("monthofyear", "hourofday") arrives here, and
        # falling through to the day shape would silently measure str(d.day).
        raise ValueError(
            f"_cadence_token_width: {format_tu!r} is not a bucketed calendar grain"
        )
    if authored_format is not None or format_tu in SINGLE_ROW_CADENCE_GRAINS:
        return measurer.measure(cadence_label_text(d, format_tu, authored_format), size)
    # _day_label renders two rows ("%-d" over a possibly-blank
    # month/year row) — measure that shape, not a single-row string
    # nothing draws. Deliberately still `max()`'d, unlike the single-row
    # grains: those have a simple two-way row-2 shape (blank, or the bare
    # year, gated by one boolean — `_fiscal_month_is_year_start`), which
    # `_pair_clears` checks as its own independent clearance. The day
    # path's row 2 (`day_week_context`) is a three-way shape — blank, bare
    # month, or month+year — gated by an `opens_month` condition
    # `_pair_clears` has no equivalent for today. Folding it via `max()`
    # over-reserves here the same way it did for month/quarter before that
    # fix, but under-reserving it without also teaching `_pair_clears` the
    # three-way shape would UNDER-detect a real day-axis collision.
    return max(
        measurer.measure(str(d.day), size),
        measurer.measure(
            day_week_context(d, format_tu, position, fiscal_year_start_month), size
        ),
    )


def _year_row_width(d: dt.date, measurer: FontMeasurer, size: float) -> float:
    """Rendered width of the stacked year-context row under a month/quarter tick.

    A separate text line at a different y than row 1 (``_cadence_token_width``)
    — checked as its own, independent clearance in ``_pair_clears``.
    """
    return measurer.measure(str(d.year), size)


def _pair_clears(
    i: int,
    j: int,
    dates: list[dt.date],
    encoding_tu: str,
    format_tu: str,
    measurer: FontMeasurer,
    size: float,
    band: float,
    edge_labels_flushed: bool,
    is_anchor: bool,
    fiscal_year_start_month: int,
    authored_format: str | None,
) -> bool:
    """True when labeled buckets *i* and *j* (i < j) do not overlap.

    Vega flushes a temporal axis's literal first rendered tick (``dates[0]``)
    to the plot's left edge whenever the axis is flush-enabled — this is
    positional, not calendar-semantic. A weekly source value that opens a
    month label without landing on the calendar's day-1 boundary (e.g. the
    week of Jan 6) still gets flushed when it is genuinely the domain's
    first bucket, so it must reserve its full measured width there, not
    half (confirmed against a real render: ``playground/editorial-stress-test``'s
    ``line_pipeline`` chart flushes its "Jan" tick to ``text-anchor: start``
    even though the underlying date is Jan 6, not Jan 1).

    The trailing edge does not mirror this: a real render of a domain whose
    last bucket opens a label off the calendar boundary (e.g. a week
    landing on May 4) does not flush that tick full-width — Vega drops it
    from the axis entirely rather than widen it. Reserving full width there
    would over-detect collisions the real render never has, so the last
    tick keeps the calendar-boundary gate.

    Whether a tick carries the year-context row is a text-anchor question,
    not a flush-edge question: the labelExpr's real condition is ``anchor ||
    fiscal_month === 0``, and either half can fire on ANY tick, interior or
    edge — a interior January (or fiscal-year-start month) paints its year
    row regardless of whether the axis flushes edges at all. ``anchor`` is
    the first *visible* (labeled) tick, not the domain's literal first
    bucket — the real spec compares ``datum.index === anchor_index`` where
    ``anchor_index`` is ``visible_indices[0]`` (the engine's label cadence), and
    the caller here (``temporal_visibility_fits``) passes ``is_anchor`` for
    exactly the pair whose ``i`` is that first labeled opener (``k == 0``).
    ``is_anchor`` and ``i == 0`` coincide only when the domain's literal
    first bucket also happens to be labeled at this candidate grain — a
    domain that opens off the label cadence (e.g. monthly data opening in
    August against a quarterly candidate) has its anchor at some ``i > 0``.
    Every other tick, ``i`` or ``j``, carries the row only when it lands on
    the fiscal year's start month. ``left_flush``/``right_flush`` answer a
    different question — how far a tick's own reserved extent stretches —
    and stay scoped to that (``left_flush`` still tests literal ``i == 0``,
    since it answers whether Vega positionally flushes the domain's own
    first bucket, not whether this tick is the labelExpr's semantic anchor).

    Confirmed against a real render: monthly data opening 2015-08 against a
    quarterly cadence emits ``axis.values[0] = 2015-10-01`` and paints that
    label — the axis's first RENDERED tick — ``text-anchor: middle``, while
    the same chart opening 2015-01 paints its first ``text-anchor: start``.
    So the flush keys off position at the range edge, not ordinal among
    rendered ticks, and only ``dates[0]`` can ever be flushed. Keying
    ``left_flush`` on ``is_anchor`` would reserve full width for an interior
    label and coarsen an axis that has the room (measured on that domain at
    a 12px band: 14 quarterly labels down to 4 yearly ones).

    A tick that carries the year row renders it as a SEPARATE text line
    (``_year_row_width``), one row below the tick's own text
    (``_cadence_token_width``) — not a wider block reserved for both. Row 1
    of one tick can only ever collide with row 1 of its neighbor; the year
    row only with a neighbor's year row, and only when the neighbor
    renders one too (nothing paints there otherwise, so there is nothing to
    collide with). The two rows are therefore two independent clearances,
    not one max()'d width.
    """
    clearance = (j - i) * band
    left_flush = edge_labels_flushed and i == 0
    right_flush = (
        edge_labels_flushed
        and j == len(dates) - 1
        and _is_calendar_tick(dates[j], encoding_tu, format_tu, fiscal_year_start_month)
    )
    # No stacked year row under an authored format — see `cadence_label_text`.
    stacks_year_row = authored_format is None and format_tu in {
        "yearmonth",
        "yearquarter",
    }
    i_carries_year_row = stacks_year_row and (
        is_anchor or _fiscal_month_is_year_start(dates[i], fiscal_year_start_month)
    )
    j_carries_year_row = stacks_year_row and _fiscal_month_is_year_start(
        dates[j], fiscal_year_start_month
    )

    wi = _cadence_token_width(
        dates[i], format_tu, measurer, size, i, fiscal_year_start_month, authored_format
    )
    wj = _cadence_token_width(
        dates[j], format_tu, measurer, size, j, fiscal_year_start_month, authored_format
    )
    left_extent = wi if left_flush else wi / 2
    right_extent = wj if right_flush else wj / 2
    if left_extent + right_extent > clearance:
        return False

    if i_carries_year_row and j_carries_year_row:
        yi = _year_row_width(dates[i], measurer, size)
        yj = _year_row_width(dates[j], measurer, size)
        left_year_extent = yi if left_flush else yi / 2
        right_year_extent = yj if right_flush else yj / 2
        if left_year_extent + right_year_extent > clearance:
            return False

    return True


def _is_calendar_tick(
    date: dt.date,
    encoding_tu: str,
    format_tu: str,
    fiscal_year_start_month: int,
) -> bool:
    """Whether a labeled source value falls on Vega's calendar tick.

    Only the trailing edge in ``_pair_clears`` still needs this — see that
    function's docstring for why the leading edge no longer does.
    """
    if format_tu == "year":
        return date.day == 1 and (date.month - fiscal_year_start_month) % 12 == 0
    if format_tu == "yearquarter":
        return date.day == 1 and (date.month - fiscal_year_start_month) % 3 == 0
    if format_tu == "yearmonth":
        return date.day == 1
    if format_tu == "yearweek":
        return date.weekday() == 0
    return format_tu == encoding_tu


def temporal_visibility_fits(
    labeled: list[int],
    dates: list[dt.date],
    encoding_tu: str,
    format_tu: str,
    measurer: FontMeasurer,
    size: float,
    band: float,
    *,
    edge_labels_flushed: bool,
    fiscal_year_start_month: int,
    authored_format: str | None = None,
) -> bool:
    """True when every consecutive pair of labeled buckets clears the gap.

    ``labeled`` is the opener set at this visibility grain.
    ``authored_format`` is ``axis.labels.format`` when it is a d3-time-format
    — the text Vega really paints (see ``cadence_label_text``).
    """
    if len(labeled) < 2:
        return True
    for k in range(len(labeled) - 1):
        i, j = labeled[k], labeled[k + 1]
        if not _pair_clears(
            i,
            j,
            dates,
            encoding_tu,
            format_tu,
            measurer,
            size,
            band,
            edge_labels_flushed,
            k == 0,
            fiscal_year_start_month,
            authored_format,
        ):
            return False
    return True


def _ordinal_bucket_key(value: Any) -> str:
    """Date-only ISO string a row's x-value shares with its scaffold bucket.

    ``complete_ordinal_time_series`` enumerates buckets as date-only ISO
    strings (``bucket.isoformat()``) and looks up each row by this key, so a
    string value must collapse to the same date-only form as a `datetime`/
    `date` object — an ISO datetime string left as-is (``"2024-01-01T00:00:00"``)
    would never match its scaffold bucket (``"2024-01-01"``), and every row
    would synthesize as a missing (null) bucket.
    """
    parsed = _parse_date(value)
    if isinstance(parsed, dt.datetime):
        return parsed.date().isoformat()
    if isinstance(parsed, dt.date):
        return parsed.isoformat()
    return str(value)


def _parse_date(value: Any) -> dt.date | dt.datetime | None:
    """Parse value to date/datetime. Returns None if unparseable."""
    if isinstance(value, dt.datetime):
        return value
    if isinstance(value, dt.date):
        return value
    if isinstance(value, str):
        if _ISO_DATETIME_RE.match(value):
            try:
                return dt.datetime.fromisoformat(value)
            except ValueError:
                return None
        if _ISO_DATE_RE.match(value):
            try:
                return dt.date.fromisoformat(value)
            except ValueError:
                return None
        return _parse_bucket_string(value)
    return None


# Nominal bucket lengths in days, coarsest first — the ladder _detect_cadence
# walks. Weeks are absent on purpose: the same-weekday branch above already
# owns weekly detection, and a yearweek bucket is anchored to whatever weekday
# the data uses (see _floor_to_bucket_start), so a series with no consistent
# weekday has no week bucket to be assigned to.
_CADENCE_PERIOD_DAYS: tuple[tuple[str, float], ...] = (
    ("year", 365.25),
    ("yearquarter", 91.31),
    ("yearmonth", 30.44),
)
# How far a gap may sit from a whole number of periods. Month lengths swing
# 28–31 days, so a Jan→Feb step is 0.08 of a period short of nominal — the
# widest wobble a real calendar cadence produces (quarters reach 0.015, years
# 0.001). Dates that merely happen to land one per month deviate ~0.25 and up.
_CADENCE_TOLERANCE = 0.15

# The grains whose bucket boundaries move with ``fiscal_year_start_month``.
_ANCHOR_SENSITIVE_UNITS: frozenset[str] = frozenset({"year", "yearquarter"})


def _detect_cadence(dates: list[dt.date]) -> str | None:
    """Coarsest grain whose period the gaps between *dates* are multiples of.

    Cadence, not calendar position: a value's day-of-month says nothing about
    its grain (month ends, quarter ends and the 15th of the month are all
    monthly-or-coarser), but the distance to its neighbor says everything.

    A grain is accepted on two conditions. The MEDIAN gap must sit within
    ``_CADENCE_TOLERANCE`` of a whole number of periods, so one late report
    or a skipped month does not disqualify an otherwise obvious rhythm. And
    every value must floor to a bucket of its own: downstream gap-fill
    refuses to guess which of two values a shared bucket means
    (ERR-GAP-FILL-BUCKET-COLLISION), which is a fair answer to an authored
    ``time_unit`` and not to a board that authored nothing, so the collision
    is settled here by declining the grain and trying a finer one.

    A fiscal offset moves year and quarter boundaries, and downstream
    gap-fill keys rows through the axis's own ``fiscal_year_start_month``,
    which detection — a pure function of the values, which is the only reason
    its call sites all agree on one grain — never sees. So those two rungs
    must keep the values apart under EVERY anchor, not just the calendar one.
    That costs a real cadence nothing: values a whole period apart cannot
    share a bucket wherever the boundaries fall. It declines the grain
    exactly when two values sit less than a period apart, which is where
    banding them together was the wrong answer anyway.

    Returns None when no rung fits — the caller keeps the daily fallthrough.
    """
    ordered = sorted(dates)
    gaps = [(ordered[i] - ordered[i - 1]).days for i in range(1, len(ordered))]
    # One interval is a measurement, not a rhythm: nothing has repeated yet.
    if len(gaps) < 2:
        return None
    distinct_count = len(set(ordered))
    for unit, period in _CADENCE_PERIOD_DAYS:
        # Clamped at 1: a sub-period gap is a whole period off its nearest
        # real multiple, and rounding it to a 0-step would score it as a
        # near-perfect fit for every grain.
        steps = [max(1, round(gap / period)) for gap in gaps]
        deviations = [
            abs(gap / period - step) for gap, step in zip(gaps, steps, strict=True)
        ]
        if statistics.median(deviations) > _CADENCE_TOLERANCE:
            continue
        anchors = range(1, 13) if unit in _ANCHOR_SENSITIVE_UNITS else (1,)
        if all(
            len({_floor_to_bucket_start(d, unit, anchor) for d in ordered})
            == distinct_count
            for anchor in anchors
        ):
            return unit
    return None


def detect_time_unit(values: list[Any]) -> str | None:
    """Detect VL timeUnit from distinct non-null x-field values.

    Returns one of: "year", "yearquarter", "yearmonth", "yearweek",
    "yearmonthdate", or None (sub-daily continuous or insufficient data).

    Coarse grains are reached two ways: values sitting on a bucket start
    (the position predicates below), or values spaced a bucket apart
    (``_detect_cadence``).

    Raises ValueError when ≥10% of distinct values are unparseable strings.
    """
    distinct = list({v for v in values if v is not None})
    if len(distinct) < 2:
        return None

    parsed: list[dt.date | dt.datetime] = []
    bad: list[Any] = []
    for v in distinct:
        p = _parse_date(v)
        if p is None:
            bad.append(v)
        else:
            parsed.append(p)

    if bad and len(bad) / len(distinct) >= 0.1:
        examples = bad[:5]
        raise ValueError(
            f"Couldn't auto-detect timeUnit: ≥10% unparseable date values: {examples}. "
            "Set style.axis_x.time_unit explicitly or fix the query."
        )

    if not parsed or len(parsed) < 2:
        return None

    # Sub-daily fallthrough: any nonzero hms → continuous
    for p in parsed:
        if isinstance(p, dt.datetime) and (p.hour or p.minute or p.second):
            return None

    # Normalize to date for predicate checks
    dates = [p.date() if isinstance(p, dt.datetime) else p for p in parsed]

    # Predicate check: coarsest to finest
    if all(d.month == 1 and d.day == 1 for d in dates):
        return "year"
    if all(d.day == 1 and d.month in (1, 4, 7, 10) for d in dates):
        return "yearquarter"
    if all(d.day == 1 for d in dates):
        return "yearmonth"
    # Weekly cadence: every distinct value falls on the same weekday AND the
    # median consecutive gap is ≤ 14 days. The same-weekday check alone fires
    # on any 7-day-multiple spacing (28, 35, 42 … days); 28-day-spaced Sunday
    # data is not weekly data — it would enumerate ~78 weekly ordinal buckets
    # for 20 actual data points. The median gap gate rejects those cases and
    # returns None (continuous temporal) rather than falling through to
    # yearmonthdate, which would be worse (daily-bucket enumeration of the
    # same sparse span). Threshold ≤ 14 preserves the existing behavior for
    # weekly series with occasional holiday skips (max gap 14 days, median 14).
    if len({d.weekday() for d in dates}) == 1:
        sorted_dates = sorted(dates)
        gaps = [
            (sorted_dates[i] - sorted_dates[i - 1]).days
            for i in range(1, len(sorted_dates))
        ]
        if statistics.median(gaps) <= 14:
            return "yearweek"
        # Same weekday but non-weekly spacing — no recognizable bucket grain.
        # Every 7-day-multiple cadence lands here (28-day, 4-5-4 retail), which
        # is why _detect_cadence below never has to reason about them.
        return None
    cadence = _detect_cadence(dates)
    if cadence is not None:
        return cadence
    return "yearmonthdate"


# A bucket ladder is either a month ladder or a day ladder; these are its
# step sizes. Between them they cover BUCKETED_CALENDAR_UNITS exactly, which
# is what lets _bucket_index be total without a fallback.
_MONTH_LADDER_STEP: dict[str, int] = {"year": 12, "yearquarter": 3, "yearmonth": 1}
_DAY_LADDER_STEP: dict[str, int] = {"yearweek": 7, "yearmonthdate": 1}


def _bucket_index(bucket_start: dt.date, time_unit: str) -> int:
    """Position of a floored bucket start on its grain's ladder.

    Consecutive buckets differ by exactly 1, so the band count of a span is a
    subtraction. ``bucket_start`` must already be floored — the caller floors
    through ``_floor_to_bucket_start``, which is also what rejects a unit
    neither ladder covers. ``yearweek`` has no absolute boundary to floor to,
    so its ladder is a fixed 7-day step from wherever the data starts.
    """
    if time_unit in _MONTH_LADDER_STEP:
        months = bucket_start.year * 12 + bucket_start.month - 1
        return months // _MONTH_LADDER_STEP[time_unit]
    return bucket_start.toordinal() // _DAY_LADDER_STEP[time_unit]


def ordinal_scaffold_within_budget(
    panels: list[list[Any]],  # type-state: explicit_any — raw x cells
    time_unit: str,
) -> bool:
    """Whether banding *panels*' values at *time_unit* keeps a legible band axis.

    A banded bucketed-time x owes the axis one slot per grain bucket across
    its [min, max] span (``complete_ordinal_time_series`` synthesizes the
    missing ones), so the rendered band count is the SPAN, not the row count.
    The two diverge whenever data is sparser than its own grain, at any
    grain: ten dates 45 days apart detect as "daily" and would enumerate ~45
    slots per real bar, and twelve readings a decade apart honestly detect as
    "year" and still enumerate ten empty bands per real bar.

    The span is measured PER PANEL and summed, because that is what
    ``fill_one_panel`` materializes: each panel enumerates only its own
    [min, max] range, never the pooled one (see the density-gate paragraph
    in ``emitters/_channels.py``). Pooling the span instead would charge the
    chart for the gaps BETWEEN panels, which nothing ever enumerates — two
    small-multiple panels of contiguous dailies twenty years apart synthesize
    zero buckets but would score a ~7,200-bucket deficit and get flipped onto
    a continuous twenty-year domain, the very failure this gate exists to
    prevent. A non-faceted chart is the one-panel case and is unaffected.

    The budget: synthesized empty buckets may not exceed ``max(distinct
    buckets, MAX_ORDINAL_BUCKETS)`` — dense
    series (contiguous dailies, weekday-only data, weekly-with-holiday-gaps)
    pass untouched, while a scaffold dominated by empty slots means the
    detected grain is finer than the data's own and banding must not fire.

    Unparseable values are ignored: ``_ordinal_bucket_key`` keys them through
    ``str(value)``, which matches no enumerated bucket, so they neither
    occupy a slot nor extend a span. ``detect_time_unit`` already raised at
    ≥10% unparseable, so what is left cannot move the verdict far.
    """
    total_bands, occupied = _band_counts(panels, time_unit)
    return total_bands - occupied <= max(occupied, MAX_ORDINAL_BUCKETS)


def range_extension_keeps_band_axis(
    panels: list[list[Any]],  # type-state: explicit_any — raw x cells
    time_unit: str,
    date_range: tuple[dt.date, dt.date],
) -> bool:
    """Whether scaffolding *panels* out to *date_range* leaves the band axis a band axis.

    The extended axis must pass the scaffold budget, and a coarse grain, which
    bar also flips to continuous time past ``MAX_ORDINAL_BUCKETS`` bands, must
    stay under that count in total.
    """
    extended = [[*values, *date_range] for values in panels]
    if not ordinal_scaffold_within_budget(extended, time_unit):
        return False
    if time_unit in FINE_BUCKET_UNITS:
        return True
    total_bands, _ = _band_counts(extended, time_unit)
    return total_bands <= MAX_ORDINAL_BUCKETS


def _band_counts(
    panels: list[list[Any]],  # type-state: explicit_any — raw x cells
    time_unit: str,
) -> tuple[int, int]:
    """The bands the shared scale spans and how many of them hold a value."""
    spans: list[tuple[int, int]] = []
    occupied: set[dt.date] = set()
    for values in panels:
        distinct: set[dt.date] = set()
        for v in {v for v in values if v is not None}:
            parsed = _parse_date(v)
            if parsed is None:
                continue
            d = parsed.date() if isinstance(parsed, dt.datetime) else parsed
            distinct.add(_floor_to_bucket_start(d, time_unit, 1))
        if not distinct:
            continue
        # Index the floored starts rather than walking _enumerate_buckets:
        # the same slots, without allocating a date per bucket on data whose
        # defining property is a span pathologically larger than its row count.
        lo = _bucket_index(min(distinct), time_unit)
        hi = _bucket_index(max(distinct), time_unit)
        spans.append((lo, hi))
        occupied |= distinct
    # Merge the panels' ranges before counting: the band scale is SHARED, so a
    # bucket two panels both cover is one band, not two. Counting each panel's
    # span separately would make the verdict a function of panel count —
    # identical per-panel data would flip to continuous purely by gaining a
    # sibling — while the axis it describes never changed.
    total_bands = 0
    merged_hi: int | None = None
    for lo, hi in sorted(spans):
        start = lo if merged_hi is None or lo > merged_hi else merged_hi + 1
        if hi >= start:
            total_bands += hi - start + 1
        merged_hi = hi if merged_hi is None else max(merged_hi, hi)
    return total_bands, len(occupied)


_MIXED_LABEL_MSG = (
    "Couldn't auto-detect timeUnit: column contains mixed label and "
    "non-label values. "
    "Set style.axis_x.time_unit explicitly."
)


def _week_to_iso(v: str) -> str:
    year, week = int(v[:4]), int(v[6:])
    try:
        return dt.date.fromisocalendar(year, week, 1).isoformat()
    except ValueError as exc:
        raise ValueError(
            f"Couldn't auto-detect timeUnit: '{v}' is not a valid ISO week "
            f"(week {week} does not exist in year {year}). "
            "Set style.axis_x.time_unit explicitly or fix the query."
        ) from exc


def _quarter_to_iso(v: str) -> str:
    return dt.date(int(v[:4]), _QUARTER_FIRST_MONTH_LOOKUP[int(v[6])], 1).isoformat()


def _bucket_to_iso(v: str) -> str:
    """Convert any recognized bucket string to an ISO date string."""
    if _ISO_WEEK_RE.match(v):
        return _week_to_iso(v)
    if _ISO_QUARTER_RE.match(v):
        return _quarter_to_iso(v)
    anchor = _parse_bucket_string(v)
    if anchor is None:
        raise ValueError(
            f"Couldn't auto-detect timeUnit: unrecognized bucket format '{v}'. "
            "Set style.axis_x.time_unit explicitly or fix the query."
        )
    return anchor.isoformat()


def normalize_labeled_temporal(
    data: list[dict[str, Any]], field: str
) -> list[dict[str, Any]]:
    """Convert labeled bucket strings in *field* to ISO dates.

    Supported formats: YYYY-Www, YYYY-Qn, Qn YYYY, YYYYQn, YYYY-MM,
    Mon YYYY, MM/YYYY, FYnnnn, MM/DD/YYYY, Mon DD YYYY, W[eek ]N YYYY.

    When all non-null values share the same format family, returns a copy
    with values replaced by the ISO date for the first instant of each
    bucket. Returns *data* unchanged when the field uses plain ISO dates
    or date/datetime objects (no labeling needed).

    Raises ValueError when:
    - labeled values are mixed with ISO date strings (or other non-labeled)
    - values span more than one format family (e.g. YYYY-Www with YYYY-Qn)
    - an ISO week label encodes an invalid week number
    """
    values = [row[field] for row in data if field in row and row[field] is not None]
    if not values:
        return data

    if is_year_shaped(values):
        return [
            (
                {**row, field: dt.date(int(row[field]), 1, 1).isoformat()}
                if field in row and row[field] is not None
                else row
            )
            for row in data
        ]

    def _is_iso(v: Any) -> bool:
        return isinstance(v, str) and bool(
            _ISO_DATE_RE.match(v) or _ISO_DATETIME_RE.match(v)
        )

    def _is_labeled(v: Any) -> bool:
        return isinstance(v, str) and not _is_iso(v) and _classify_bucket(v) is not None

    labeled = [v for v in values if _is_labeled(v)]
    if not labeled:
        return data

    # Any ISO dates mixed in → mixed label error
    if any(_is_iso(v) for v in values):
        raise ValueError(_MIXED_LABEL_MSG)

    # Any unrecognized strings mixed in → mixed label error
    if len(labeled) < len(values):
        raise ValueError(_MIXED_LABEL_MSG)

    # All are labeled — check they're all the same format family
    families = {_classify_bucket(v) for v in labeled}
    if len(families) > 1:
        raise ValueError(_MIXED_LABEL_MSG)

    return [
        (
            {**row, field: _bucket_to_iso(row[field])}
            if field in row and row[field] is not None
            else row
        )
        for row in data
    ]


def _next_bucket(date: dt.date, time_unit: str) -> dt.date:
    """Return the first date of the next bucket at the given grain.

    A fiscal offset does not change this step: once a bucket start is
    correctly anchored (see ``_floor_to_bucket_start``), advancing by a fixed
    +1/+3/+12-month increment preserves the anchor's month-of-year alignment
    regardless of which month the fiscal year starts in.
    """
    if time_unit == "yearmonthdate":
        return date + dt.timedelta(days=1)
    if time_unit == "yearweek":
        return date + dt.timedelta(weeks=1)
    if time_unit == "yearmonth":
        # Advance to first of the next month
        if date.month == 12:
            return dt.date(date.year + 1, 1, 1)
        return dt.date(date.year, date.month + 1, 1)
    if time_unit == "yearquarter":
        # Advance by 3 months
        new_month = date.month + 3
        if new_month > 12:
            return dt.date(date.year + 1, new_month - 12, 1)
        return dt.date(date.year, new_month, 1)
    if time_unit == "year":
        return dt.date(date.year + 1, date.month, 1)
    raise ValueError(f"Unsupported time_unit for bucket stepping: {time_unit!r}")


def _floor_to_period_start(
    date: dt.date, period_months: int, start_month: int
) -> dt.date:
    """Floor date to the most recent period boundary of length period_months.

    Boundaries are the months {start_month, start_month + period_months, ...}
    (mod 12). Works in a month-index space shifted so start_month becomes the
    period origin, floors to a period_months multiple, then shifts back.
    """
    offset = start_month - 1
    total_months = date.year * 12 + (date.month - 1)
    shifted = total_months - offset
    floored_shifted = shifted - (shifted % period_months)
    floored_total = floored_shifted + offset
    year, month0 = divmod(floored_total, 12)
    return dt.date(year, month0 + 1, 1)


def _floor_to_bucket_start(date: dt.date, time_unit: str, start_month: int) -> dt.date:
    """Floor date to the start of its enclosing bucket for time_unit.

    Only `year` and `yearquarter` grains have configurable anchoring — a
    fiscal offset shifts which month opens the year/quarter. `yearmonth`
    buckets always start on day 1. `yearmonthdate` (daily) has no coarser
    boundary to floor to. `yearweek` has NO universal "day 1" the way months
    do — detect_time_unit accepts any consistent weekday as a week anchor
    (Sunday-start data is common, not just ISO Monday-start), so flooring to
    the ISO Monday would shift a Sunday-anchored week's bucket dates by up to
    6 days. yearweek's bucket start is whatever weekday the data already uses,
    so this returns the date unchanged and ``_enclosing_bucket`` — which knows
    where the ladder starts — steps a row onto it.
    """
    if time_unit == "year":
        return _floor_to_period_start(date, 12, start_month)
    if time_unit == "yearquarter":
        return _floor_to_period_start(date, 3, start_month)
    if time_unit == "yearmonth":
        return date.replace(day=1)
    if time_unit in ("yearweek", "yearmonthdate"):
        return date
    raise ValueError(f"Unsupported time_unit for bucket flooring: {time_unit!r}")


def _enumerate_buckets(
    min_date: dt.date,
    max_date: dt.date,
    time_unit: str,
    fiscal_year_start_month: int,
) -> list[dt.date]:
    """Return every bucket date from min_date's enclosing bucket to max_date."""
    buckets: list[dt.date] = []
    current = _floor_to_bucket_start(min_date, time_unit, fiscal_year_start_month)
    while current <= max_date:
        buckets.append(current)
        current = _next_bucket(current, time_unit)
    return buckets


def _enclosing_bucket(
    date: dt.date, time_unit: str, fiscal_year_start_month: int, week_anchor: dt.date
) -> dt.date:
    """The ``_enumerate_buckets`` slot *date* falls inside.

    The row-side twin of ``_enumerate_buckets``: a scaffold enumerates bucket
    STARTS, so a row must be looked up by the start of the bucket that
    CONTAINS it, not by its own value. Every cadence that reports a period by
    its last instant — ``LAST_DAY(month)`` month-ends, quarter-ends, year-ends,
    a fiscal year's March 31 — otherwise matches no enumerated bucket and
    vanishes from the chart while the axis still draws the full ladder.

    ``week_anchor`` is the ladder's first bucket. ``yearweek`` is the one grain
    with no absolute boundary to floor to (Sunday- and Monday-anchored weeks
    are both ordinary), so its ladder is defined by where the data starts and
    a row belongs to the 7-day step it lands in. Every other grain has an
    absolute boundary and ignores the anchor.
    """
    if time_unit == "yearweek":
        return week_anchor + dt.timedelta(days=7 * ((date - week_anchor).days // 7))
    return _floor_to_bucket_start(date, time_unit, fiscal_year_start_month)


_GAP_FILL_HANDLINGS = frozenset(
    {
        "linear",
        "step-after",
        "step-before",
        "step-center",
        "curve",
    }
)


def _smoothstep(t: float) -> float:
    """Hermite ease: 0 at t=0, 1 at t=1, flat derivatives at endpoints."""
    return t * t * (3.0 - 2.0 * t)


def _apply_gap_fill_handling(
    rows: list[dict[str, Any]],
    x_field: str,
    dim_fields: list[str],
    dim_combos: list[tuple[Any, ...]],
    measure_cols: list[str],
    mode: str,
) -> None:
    """Fill null measures on synthetic buckets; mutates ``rows`` in place."""
    key_to_row: dict[tuple[Any, ...], dict[str, Any]] = {}
    bucket_order: dict[str, int] = {}
    for row in rows:
        bkt = row[x_field]
        if bkt not in bucket_order:
            bucket_order[bkt] = len(bucket_order)
        combo = tuple(row.get(d) for d in dim_fields)
        key_to_row[(bkt, *combo)] = row

    all_buckets_sorted = sorted(bucket_order, key=lambda b: bucket_order[b])

    for combo in dim_combos:
        group_rows: list[dict[str, Any]] = []
        for bkt in all_buckets_sorted:
            key = (bkt, *combo)
            if key in key_to_row:
                group_rows.append(key_to_row[key])

        for col in measure_cols:
            n = len(group_rows)
            # Only observed (query) rows are anchors — not values filled in this pass.
            # Any non-null value is a valid anchor; arithmetic below raises loudly if
            # the value is non-numeric (e.g. a string) rather than silently skipping it.
            anchors = [idx for idx in range(n) if group_rows[idx][col] is not None]
            for i, row in enumerate(group_rows):
                if row[col] is not None:
                    continue
                left_anchors = [a for a in anchors if a < i]
                right_anchors = [a for a in anchors if a > i]
                left_idx = left_anchors[-1] if left_anchors else -1
                right_idx = right_anchors[0] if right_anchors else -1

                if mode == "step-after":
                    if left_idx >= 0:
                        row[col] = group_rows[left_idx][col]
                    continue

                if mode == "step-before":
                    if right_idx >= 0:
                        row[col] = group_rows[right_idx][col]
                    continue

                if left_idx < 0 or right_idx < 0:
                    continue

                left_val = group_rows[left_idx][col]
                right_val = group_rows[right_idx][col]
                gap_width = right_idx - left_idx
                gap_pos = i - left_idx
                t = gap_pos / gap_width

                if mode == "linear":
                    row[col] = left_val + (right_val - left_val) * t
                elif mode == "step-center":
                    mid = left_idx + gap_width / 2
                    row[col] = left_val if i < mid else right_val
                elif mode == "curve":
                    row[col] = left_val + (right_val - left_val) * _smoothstep(t)


def canonicalize_ordinal_x(data: ChartRenderData, x_field: str) -> ChartRenderData:
    """Rewrite ``x_field`` to a date-only ISO string, keeping row order.

    A raw date/datetime object or a non-date-only date-like string (e.g. an
    ISO datetime with a "T"/space time component) otherwise reaches the
    emitted spec unstringified or with a stray time component, which Vega's
    JS ``Date`` parser can read as local time, shifting every point by the
    runtime's UTC offset.
    """
    return [
        row
        if x_field not in row or row[x_field] is None
        else {**row, x_field: _ordinal_bucket_key(row[x_field])}
        for row in data
    ]


def canonicalize_and_sort_ordinal_x(
    data: ChartRenderData, x_field: str
) -> ChartRenderData:
    """``canonicalize_ordinal_x``, then sort chronologically.

    Used when a bucketed calendar grain resolves to a continuous temporal
    scale and no missing-bucket synthesis is needed, but the other two
    ``complete_ordinal_time_series`` side effects still must apply.
    """
    return sorted(
        canonicalize_ordinal_x(data, x_field), key=lambda row: str(row.get(x_field))
    )


def complete_ordinal_time_series(
    data: list[dict[str, Any]],
    x_field: str,
    time_unit: str,
    dim_fields: list[str],
    fill: str,
    fiscal_year_start_month: int,
    date_range: tuple[dt.date, dt.date] | None = None,
) -> list[dict[str, Any]]:
    """Synthesize missing time-bucket rows so every bucket in [min, max] is present.

    For ordinal bucketed-time charts the engine must supply every bucket
    between the dataset's min and max so the ordinal x-axis has a slot for
    each period. Without this, missing buckets simply disappear from the axis.
    A ``date_range`` widens [min, max] to it, so edge buckets are filled by
    ``fill`` like interior ones.

    A row is placed by the bucket it falls INSIDE (``_enclosing_bucket``), not
    by whether its value already sits on a bucket start — the scaffold and the
    lookup must derive their keys from the same function or a month-end,
    quarter-end or fixed-day-of-month series matches nothing and every row is
    replaced by a synthesized null. Placing is not aggregating: two rows in one
    bucket raise rather than merge (see Raises).

    Args:
        data: rows from the query (non-empty; caller must guard empty datasets).
        x_field: the x-encoding column name (must be ISO date strings or
            datetime.date objects after normalize_labeled_temporal runs).
        time_unit: one of BUCKETED_CALENDAR_UNITS (year, yearquarter, yearmonth,
            yearweek, yearmonthdate).
        dim_fields: categorical dimension columns to cross-join over (e.g. the
            color/series field). Engine only cross-joins over values actually
            present in the data window.
        fill: "null" fills missing measure columns with None;
            "zero" fills with 0; interpolate-* modes fill interior synthetic
            buckets (see ``_GAP_FILL_HANDLINGS``).
        fiscal_year_start_month: calendar month (1=Jan..12=Dec) that anchors
            year/yearquarter bucket boundaries; 1 (default) is the calendar
            convention. Floors the enclosing bucket of the dataset's min date
            to this anchor before enumerating forward, so mid-period data
            (e.g. starting in March) still yields a full Jan/Apr/Jul/Oct-
            anchored (or fiscally-shifted) bucket range.
        date_range: Range the scaffold spans when it reaches past the data.

    Returns:
        A new list of dicts, sorted (bucket asc, dim_1 asc, …), with the
        original rows merged in. When no buckets are missing, returns data
        sorted by the same key. If data is empty, returns data unchanged.

    Raises:
        ChartDataError: (ERR-GAP-FILL-BUCKET-COLLISION) when two rows fall in
            the same (bucket, *dim_vals) — two timestamps on one calendar day
            under yearmonthdate, or a finer series under a coarser authored
            grain (monthly rows under yearquarter). There is no one value to
            plot for that bucket and combining them would be an aggregation
            the query layer owns; can't happen on grain-aligned data.
    """
    if not data:
        return data

    # Collect all x values; parse to date for comparison
    raw_x_values = [
        row[x_field] for row in data if x_field in row and row[x_field] is not None
    ]
    if not raw_x_values:
        return data

    x_iso = [_ordinal_bucket_key(v) for v in raw_x_values]

    # Parse to dates for arithmetic
    parsed_dates: list[dt.date] = []
    for iso in x_iso:
        d = _parse_date(iso)
        if d is not None:
            parsed_dates.append(d.date() if isinstance(d, dt.datetime) else d)

    if not parsed_dates:
        return data

    min_date = min(parsed_dates)
    max_date = max(parsed_dates)
    range_start, range_end = (min_date, max_date) if date_range is None else date_range
    # A yearweek bucket has no absolute boundary: the range's first bucket is the
    # 7-day step, counted from the data's own first week, that contains its start.
    all_buckets = _enumerate_buckets(
        _enclosing_bucket(
            min(min_date, range_start), time_unit, fiscal_year_start_month, min_date
        ),
        max(max_date, range_end),
        time_unit,
        fiscal_year_start_month,
    )

    # Determine distinct dimension values from actual data
    dim_values: list[list[Any]] = []
    for dim in dim_fields:
        seen: list[Any] = []
        seen_set: set[Any] = set()
        for row in data:
            v = row.get(dim)
            if v not in seen_set:
                seen_set.add(v)
                seen.append(v)
        dim_values.append(sorted(seen, key=lambda x: (x is None, x)))

    # Identify measure columns: all non-x, non-dim columns
    sample_keys = list(data[0].keys())
    dim_set = set(dim_fields) | {x_field}
    measure_cols = [k for k in sample_keys if k not in dim_set]

    # For fill modes other than "zero", synthesized rows start as None; the
    # second pass (_apply_gap_fill_handling) overwrites them for interior gaps.
    fill_value = 0 if fill == "zero" else None

    # min_date's own enclosing bucket, so the ladder is never empty and
    # all_buckets[0] is always the anchor yearweek's step arithmetic needs.
    week_anchor = all_buckets[0]

    def _bucket_key(
        value: Any,  # type-state: explicit_any — raw query cell, _parse_date's domain
    ) -> str:
        parsed = _parse_date(value)
        if parsed is None:
            # Unparseable or null x. Keyed through str() so it matches no
            # enumerated bucket: such a value has no bucket to belong to, and
            # it was already excluded from the [min, max] span above.
            return _ordinal_bucket_key(value)
        date = parsed.date() if isinstance(parsed, dt.datetime) else parsed
        return _enclosing_bucket(
            date, time_unit, fiscal_year_start_month, week_anchor
        ).isoformat()

    # Build lookup from (bucket_str, *dim_vals) → row
    def _row_key(row: dict[str, Any]) -> tuple[Any, ...]:
        return (_bucket_key(row.get(x_field)),) + tuple(row.get(d) for d in dim_fields)

    existing: dict[tuple[Any, ...], dict[str, Any]] = {
        _row_key(row): row for row in data
    }

    if len(existing) != len(data):
        # A last-wins dict comprehension above would otherwise silently
        # discard every row but one for a bucket/dim combo that collides —
        # e.g. two timestamps on the same calendar day under a
        # yearmonthdate grain. A collision can't happen on legitimately
        # grain-aligned data, so this check costs nothing on valid input.
        from collections import Counter

        from dbt_charts.core.diagnostics.chart_data import ChartDataError
        from dbt_charts.core.diagnostics.codes_render import (
            ERR_GAP_FILL_BUCKET_COLLISION,
        )

        key_counts = Counter(_row_key(row) for row in data)
        collision_key = next(key for key, count in key_counts.items() if count > 1)
        colliding_rows = [row for row in data if _row_key(row) == collision_key]
        bucket, *dim_vals = collision_key
        dim_desc = (
            " ("
            + ", ".join(
                f"{dim}={val!r}" for dim, val in zip(dim_fields, dim_vals, strict=True)
            )
            + ")"
            if dim_fields
            else ""
        )
        raise ChartDataError.from_code(
            ERR_GAP_FILL_BUCKET_COLLISION,
            x_field=x_field,
            value_a=colliding_rows[0].get(x_field),
            value_b=colliding_rows[1].get(x_field),
            time_unit=time_unit,
            bucket=bucket,
            dim_desc=dim_desc,
        )

    # Generate full scaffold via cross-product of buckets × dim combinations
    if dim_fields:
        dim_combos: list[tuple[Any, ...]] = list(itertools.product(*dim_values))
    else:
        dim_combos = [()]

    result: list[dict[str, Any]] = []
    for bucket in all_buckets:
        bucket_str = bucket.isoformat()
        for combo in dim_combos:
            key = (bucket_str,) + combo
            if key in existing:
                row = dict(existing[key])
                row[x_field] = bucket_str
                result.append(row)
            else:
                # Synthesize a row: bucket value + dim values + filled measures
                synth: dict[str, Any] = {x_field: bucket_str}
                for dim, val in zip(dim_fields, combo, strict=True):
                    synth[dim] = val
                for col in measure_cols:
                    synth[col] = fill_value
                result.append(synth)

    if fill in _GAP_FILL_HANDLINGS:
        _apply_gap_fill_handling(
            result, x_field, dim_fields, dim_combos, measure_cols, fill
        )

    return result


def _opens_fiscal_period(date: dt.date, period_length: int, start_month: int) -> bool:
    """True when date.month opens a fiscal period of period_length months.

    period_length=12 → year boundary; period_length=3 → quarter boundary.
    start_month=1 (default) reproduces the plain calendar check
    (month == 1, or month % 3 == 1 for quarters).
    """
    return (date.month - start_month) % period_length == 0


def is_label_opener(
    date: dt.date,
    encoding_unit: str,
    label_unit: str,
    fiscal_year_start_month: int = 1,
) -> bool:
    """Return True when *date* opens a new label period.

    Python-boolean mirror of ``opens_label_period``'s JS gate — used by the
    overlap resolver to decide which bucket indices carry a label.

    fiscal_year_start_month (1=Jan..12=Dec, default 1) shifts the year/quarter
    boundary check for the "year"/"yearquarter" label cadences; it is a no-op
    for "yearmonth"/"yearweek"/"yearmonthdate".
    """
    if label_unit == "year":
        is_year_open = _opens_fiscal_period(date, 12, fiscal_year_start_month)
        if encoding_unit in {"yearweek", "yearmonthdate"}:
            return is_year_open and date.day <= 7
        return is_year_open and date.day == 1
    if label_unit == "yearquarter":
        is_quarter_open = _opens_fiscal_period(date, 3, fiscal_year_start_month)
        if encoding_unit == "yearweek":
            return is_quarter_open and date.day <= 7
        if encoding_unit == "yearmonthdate":
            return is_quarter_open and date.day == 1
        return is_quarter_open and date.day == 1  # yearmonth
    if label_unit == "yearmonth":
        if encoding_unit == "yearweek":
            return date.day <= 7
        if encoding_unit == "yearmonthdate":
            return date.day == 1
    if label_unit == "yearweek" and encoding_unit == "yearmonthdate":
        return date.weekday() == 0
    return True  # same cadence or unknown — include all


# (value_expr, format_fn, month_fn, date_fn, fiscal_year_start_month, anchor_expr,
# steep_tilt) -> a Vega expression for one tick's text.


def day_week_context(
    date: dt.date,
    format_time_unit: str,
    position: int,
    fiscal_year_start_month: int,
) -> str:
    """Python mirror of ``_day_number_label``'s row-two text (``_week_label``/
    ``_day_label`` above): ``%b'%y`` on the anchor or fiscal-year-opening
    tick, bare ``%b`` on a month opener, otherwise blank. Render-layer width
    measurement calls this so it measures what actually gets drawn."""
    opens_month = date.day <= 7 if format_time_unit == "yearweek" else date.day == 1
    carries_year = position == 0 or (
        opens_month and (date.month - fiscal_year_start_month) % 12 == 0
    )
    if carries_year:
        return portable_strftime(date, "%b'%y")
    return portable_strftime(date, "%b") if opens_month else ""


# Above this, the vocabulary's own date-context row (``%b %-d``, no year)
# can print the same text for two different years — exactly the ambiguity
# rule 5 exists to prevent, just extended across a year boundary instead of
# a day one. 365 days is the tight bound, not a round number: two instants
# can only land on the same calendar month/day if they are at least 365
# days apart (366 across a leap day), so any domain narrower than this can
# never produce that collision, and any domain at or beyond it can. This
# check is independent of the tick-cadence gate below — it protects a
# label-text collision, not a tick-grain one — but in practice the
# tick-cadence ceiling fires first at any chart width narrower than
# roughly 21,600px, so this is the backstop for widths beyond that.

# The vocabulary only makes sense when Vega's OWN tick generator — not our
# data's cadence — actually produces ticks with varying, sub-day clock
# values. Vega-Lite's compiled Vega spec sets a continuous temporal axis's
# tick count to `ceil(width / 40)` (confirmed by inspecting
# `vl_convert.vegalite_to_vega`'s emitted `tickCount` signal); d3-time's own
# tick-interval table then switches from one "nice" step to the next at the
# geometric mean of the two neighboring step durations (confirmed against
# real `vl_convert.vegalite_to_svg` renders at both boundaries below, to
# within a tenth of the finer unit). Multiplying that boundary by the tick
# count converts it from "target seconds-per-tick" back to "domain span":
#
#   - at or above the day boundary, every tick Vega draws lands on local
#     midnight (a day/week/month/year step), so `hours(datum.value) === 0`
#     is true everywhere and the vocabulary would print "Midnight" on every
#     tick regardless of what the underlying data looks like;
#   - below the minute boundary, Vega's ticks are closer together than a
#     minute, which the vocabulary has no rung to describe (rule 3's finest
#     discriminator is minutes) and would render identical labels on
#     distinct ticks.
#
# Both bounds are span-and-width joint quantities, not data-cadence ones —
# the row values beyond the domain's own min/max never enter this
# computation, because Vega's tick *positions* come from the scale domain,
# not from how densely the underlying rows are packed inside it.

# vl_convert renders every chart under `autosize: {type: "fit"}`: the VL
# spec's declared `width` is the OUTER box, and Vega solves internally for a
# smaller plot rectangle by subtracting the y-axis's own rendered chrome
# (tick marks, label text, padding) — a data-dependent amount only Vega's
# layout engine measures exactly, invisible to Python before the spec is
# compiled. Measured directly against this engine's default theme at a
# declared width of 600px (`<path class="background" ... d="M0,0h<N>...">`
# in the compiled SVG gives the real plot rectangle): 49px for small
# integers, 59px for decimals, 63px for six-digit values, 88px for six-digit
# negatives. This constant is a conservative allowance above the widest of
# those, not a measurement of any one axis — it can only make the tick-count
# prediction below UNDER-count relative to Vega's real tickCount, except for a
# y-axis whose real chrome exceeds even this allowance (an unusually long
# custom number format), where the prediction can still overshoot the real
# plot width. An under-counted tick_count is the SAFE direction for the upper
# (day-grain) gate alone — it lowers that gate's span ceiling, biasing toward
# bailing rather than wrongly applying. It is the UNSAFE direction for the
# lower (minute-grain) gate: that gate's floor shrinks right along with it,
# so a real tickCount higher than predicted can slip a span through that
# Vega itself subdivides into sub-minute ticks (confirmed by a real render:
# a declared 600px card predicts 13 here while Vega draws 14, opening a
# ~42s band of domains that clear this under-counted floor but not the real
# one). ``predicted_tick_count_ceiling`` below is the safe counterpart for
# that gate. See the engine's sub-day label rule's docstring for why a wider
# margin trades away a bit of the vocabulary's reach rather than closing that
# risk outright — only invoking vl_convert itself would close it exactly.


# The steps a person reads off a clock face. d3-time's own tick ladder (what
# Vega draws by default) has no 10-minute or 2-hour rung.

# Room one sub-day tick label needs: the widest is the hour word at the house
# size ("Midnight"), with air on both sides.


# Past this span a header naming the time of day on every point is noise: the
# reader is scanning days, not hours.


@dataclass(frozen=True)
class SubdayHeader:
    """How a sub-day temporal x names a point in its tooltip header.

    ``scope`` is ``"time"`` when every point falls on one calendar day (the date
    is the chart's context), ``"datetime"`` otherwise. ``utc`` and ``clock`` match
    the axis labels' accessors and clock register.
    """

    scope: Literal["time", "datetime"]
    utc: bool
    clock: int | None
    seconds: bool
