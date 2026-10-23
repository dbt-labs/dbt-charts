"""Render-local temporal label thinning without semantic time-unit mutation."""

from __future__ import annotations

import dataclasses
import datetime
from typing import Any
from unittest.mock import MagicMock

import pytest


def _monthly_dates(n: int, start: tuple[int, int] = (2022, 1)) -> list[str]:
    year, month = start
    dates = []
    for _ in range(n):
        dates.append(f"{year:04d}-{month:02d}-01")
        month += 1
        if month > 12:
            month = 1
            year += 1
    return dates


def _axis_x_temporal(
    label_time_unit: str | None = None,
    font_size: float = 11.0,
    fiscal_year_start_month: int = 1,
    encoding_time_unit: str | None = None,
) -> Any:
    from dbt_charts.core.compile.config import get_theme_style
    from dbt_charts.core.compile.resolve.style.axis_cascade import resolved_axis_style
    from dbt_charts.core.compile.resolve.style.board import resolve_chart_style_context

    charts = resolve_chart_style_context(get_theme_style())
    axis_x = resolved_axis_style(
        charts, "axis_x", "temporal", chart_type="", labeled=False
    )
    font = axis_x.labels.font.model_copy(update={"size": font_size})
    overlap = dataclasses.replace(axis_x.labels.overlap, skip=True, tilt=True)
    labels = dataclasses.replace(
        axis_x.labels,
        font=font,
        overlap=overlap,
        time_unit=label_time_unit,
        angle=None,
        tilt_increments=[-30.0, -60.0, -90.0],
    )
    return dataclasses.replace(
        axis_x,
        labels=labels,
        fiscal_year_start_month=fiscal_year_start_month,
        time_unit=encoding_time_unit or axis_x.time_unit,
    )


def _make_mock_measurer(width_per_char: float = 5.0) -> Any:
    measurer = MagicMock()
    measurer.measure = lambda text, size: width_per_char * len(text) * (size / 11.0)
    return measurer


class TestVisibilityUnitChain:
    @pytest.mark.parametrize(
        ("unit", "expected"),
        [
            ("yearmonthdate", "yearmonth"),
            ("yearweek", "yearmonth"),
            ("yearmonth", "yearquarter"),
            ("yearquarter", "year"),
            ("year", None),
        ],
    )
    def test_one_meaningful_skip_step(self, unit: str, expected: str | None) -> None:
        from dbt_charts.core.render.chart.time_unit_detect import (
            next_coarser_label_unit,
        )

        assert next_coarser_label_unit(unit) == expected


class TestCadenceTokenWidthYearContext:
    """``_cadence_token_width`` measures ROW 1 ONLY — the tick's own
    month/quarter text. A tick that also carries the stacked year-context
    row (``_month_label``/``_quarter_label`` painting "2024" under "Jan")
    renders that row as a SEPARATE text line at a different y
    (``_year_row_width``), never folded into this width via ``max()``: row 1
    can only ever collide with a neighbor's row 1, the year row only with a
    neighbor's year row (see ``_pair_clears``, which checks the two rows as
    two independent clearances — a max()'d block over-reserves ~6px on
    every flushed edge label that carries the year row, rejecting rungs that
    render cleanly).
    """

    def test_yearmonth_width_is_row_one_only(self) -> None:
        import datetime

        from dbt_charts.core.font_measure import get_font_measurer
        from dbt_charts.core.render.chart.time_unit_detect import (
            _cadence_token_width,
        )

        measurer = get_font_measurer(None)
        january = datetime.date(2025, 1, 6)
        width = _cadence_token_width(january, "yearmonth", measurer, 11.0, 0, 1)
        assert width == pytest.approx(measurer.measure("Jan", 11.0))

    def test_yearquarter_width_is_row_one_only(self) -> None:
        import datetime

        from dbt_charts.core.font_measure import get_font_measurer
        from dbt_charts.core.render.chart.time_unit_detect import (
            _cadence_token_width,
        )

        measurer = get_font_measurer(None)
        q1_opener = datetime.date(2025, 1, 6)
        width = _cadence_token_width(q1_opener, "yearquarter", measurer, 11.0, 0, 1)
        assert width == pytest.approx(measurer.measure("Q1", 11.0))


class TestYearRowWidth:
    """``_year_row_width`` measures the stacked year-context row on its own
    — always the bare year number, regardless of the tick's own vocabulary
    (month, quarter, ...). It is a separate text line from
    ``_cadence_token_width``'s row 1, checked as its own clearance in
    ``_pair_clears`` rather than folded in."""

    def test_returns_bare_year_width(self) -> None:
        import datetime

        from dbt_charts.core.font_measure import get_font_measurer
        from dbt_charts.core.render.chart.time_unit_detect import (
            _year_row_width,
        )

        measurer = get_font_measurer(None)
        january = datetime.date(2025, 1, 6)
        assert _year_row_width(january, measurer, 11.0) == pytest.approx(
            measurer.measure("2025", 11.0)
        )


class TestFiscalMonthIsYearStart:
    """``_fiscal_month_is_year_start`` is only the ``fiscal_month === 0``
    half of the real labelExpr predicate (``anchor || fiscal_month ===
    0``) — it answers "does this date's month open the fiscal year",
    independent of whether the date is also the domain's literal first
    tick. ``_pair_clears`` combines it with the leading/trailing distinction.
    """

    def test_off_fiscal_start_is_false(self) -> None:
        import datetime

        from dbt_charts.core.render.chart.time_unit_detect import (
            _fiscal_month_is_year_start,
        )

        february = datetime.date(2025, 2, 3)
        assert _fiscal_month_is_year_start(february, 1) is False

    def test_at_calendar_fiscal_start_is_true(self) -> None:
        import datetime

        from dbt_charts.core.render.chart.time_unit_detect import (
            _fiscal_month_is_year_start,
        )

        january = datetime.date(2025, 1, 6)
        assert _fiscal_month_is_year_start(january, 1) is True

    def test_respects_authored_fiscal_year_start_month(self) -> None:
        import datetime

        from dbt_charts.core.render.chart.time_unit_detect import (
            _fiscal_month_is_year_start,
        )

        # Fiscal year starts in April: April is this axis's "January"
        # equivalent, so it -- not calendar January -- opens the fiscal year.
        april = datetime.date(2025, 4, 7)
        assert _fiscal_month_is_year_start(april, 4) is True
        january = datetime.date(2026, 1, 5)
        assert _fiscal_month_is_year_start(january, 4) is False


class TestPairClearsEdgeFlush:
    """Vega flushes a temporal axis's literal first/last rendered tick to the
    plot edge (text-anchor start/end) whenever it is genuinely at the domain
    boundary — regardless of whether that tick's date lands on a calendar
    boundary (day 1, quarter start, ...). A weekly-encoded domain that opens
    mid-month (e.g. the week of Jan 6) still has its month label flushed,
    because the flush is positional (``dates[0]``), not calendar-semantic.

    ``_pair_clears`` must reserve the tick's *full* measured width at that
    edge, matching the real render, not half — the day==1-style gate on
    ``_is_calendar_tick`` under-reserved it and let a real collision (Jan
    2025 / Feb 2025 on ``playground/editorial-stress-test``) go undetected.
    """

    def test_edge_opener_off_calendar_boundary_still_reserves_full_flush_width(
        self,
    ) -> None:
        import datetime

        from dbt_charts.core.font_measure import get_font_measurer
        from dbt_charts.core.render.chart.time_unit_detect import _pair_clears

        measurer = get_font_measurer(None)
        dates = [
            datetime.date(2025, 1, 6),
            datetime.date(2025, 1, 13),
            datetime.date(2025, 1, 20),
            datetime.date(2025, 1, 27),
            datetime.date(2025, 2, 3),
        ]
        size = 11.0
        w_jan = measurer.measure("Jan", size)
        w_feb = measurer.measure("Feb", size)
        # Real Vega flushes dates[0] (Jan, day=6 — not a calendar boundary)
        # to the plot edge: it reserves the full row-1 width ("Jan"), not
        # half. Pick a band whose clearance sits strictly between the buggy
        # half-width estimate and the real full-width one, so the two
        # assertions below discriminate cleanly.
        buggy_extent = w_jan / 2 + w_feb / 2
        real_extent = w_jan + w_feb / 2
        band = (buggy_extent + real_extent) / 2 / 4
        assert not _pair_clears(
            0,
            4,
            dates,
            "yearweek",
            "yearmonth",
            measurer,
            size,
            band,
            edge_labels_flushed=True,
            is_anchor=True,
            fiscal_year_start_month=1,
            authored_format=None,
        )
        # A non-flushed axis (e.g. authored labels.flush: false) keeps the
        # half-width assumption and clears the same gap.
        assert _pair_clears(
            0,
            4,
            dates,
            "yearweek",
            "yearmonth",
            measurer,
            size,
            band,
            edge_labels_flushed=False,
            is_anchor=True,
            fiscal_year_start_month=1,
            authored_format=None,
        )

    def test_trailing_opener_off_calendar_boundary_keeps_half_width(self) -> None:
        """Unlike the leading edge, a real render drops (rather than
        full-width-flushes) a trailing tick that opens off the calendar
        boundary — see ``_pair_clears``'s docstring. Reserving full width
        there would over-detect a collision the real render never has.
        """
        import datetime

        from dbt_charts.core.font_measure import get_font_measurer
        from dbt_charts.core.render.chart.time_unit_detect import _pair_clears

        measurer = get_font_measurer(None)
        dates = [
            datetime.date(2025, 3, 3),
            datetime.date(2025, 4, 7),
            datetime.date(2025, 5, 5),  # trailing opener, day=5, not day=1
        ]
        size = 11.0
        w_apr = measurer.measure("Apr", size)
        w_may = measurer.measure("May", size)
        buggy_extent = w_apr / 2 + w_may / 2
        real_extent = w_apr / 2 + w_may
        band = (buggy_extent + real_extent) / 2
        assert _pair_clears(
            1,
            2,
            dates,
            "yearweek",
            "yearmonth",
            measurer,
            size,
            band,
            edge_labels_flushed=True,
            is_anchor=False,
            fiscal_year_start_month=1,
            authored_format=None,
        )

    def test_off_cadence_anchor_keeps_half_width(self) -> None:
        """A domain opening off the label cadence has its first LABELED tick
        interior, and the real render centers it (``text-anchor: middle``) —
        only ``dates[0]`` sits at the scale's range edge and gets flushed. So
        ``left_flush`` stays keyed on ``i == 0``, not on ``is_anchor``.

        The two halves differ only in where the domain opens: identical label
        vocabulary (Q4 -> Q1, year rows 2015 -> 2016), identical clearance.
        The band sits between the half-width and full-width reservations, so
        keying ``left_flush`` on ``is_anchor`` would flip the first assertion.
        """
        import datetime

        from dbt_charts.core.font_measure import get_font_measurer
        from dbt_charts.core.render.chart.time_unit_detect import _pair_clears

        measurer = get_font_measurer(None)
        size = 11.0
        off_cadence = [  # opens in August: quarterly openers are 2, 5, 8
            datetime.date(2015, 8, 1),
            datetime.date(2015, 9, 1),
            datetime.date(2015, 10, 1),
            datetime.date(2015, 11, 1),
            datetime.date(2015, 12, 1),
            datetime.date(2016, 1, 1),
            datetime.date(2016, 2, 1),
            datetime.date(2016, 3, 1),
            datetime.date(2016, 4, 1),
        ]
        on_cadence = off_cadence[2:]  # opens in October: openers are 0, 3, 6
        w_q4 = measurer.measure("Q4", size)
        w_q1 = measurer.measure("Q1", size)
        y_2015 = measurer.measure("2015", size)
        y_2016 = measurer.measure("2016", size)
        half = max((w_q4 + w_q1) / 2, (y_2015 + y_2016) / 2)
        full = max(w_q4 + w_q1 / 2, y_2015 + y_2016 / 2)
        band = (half + full) / 2 / 3  # three bands separate the two openers

        assert _pair_clears(
            2,
            5,
            off_cadence,
            "yearmonth",
            "yearquarter",
            measurer,
            size,
            band,
            edge_labels_flushed=True,
            is_anchor=True,
            fiscal_year_start_month=1,
            authored_format=None,
        )
        # Same pair of labels, same clearance, but now genuinely at dates[0] —
        # Vega flushes it, so it reserves its full width and does not clear.
        assert not _pair_clears(
            0,
            3,
            on_cadence,
            "yearmonth",
            "yearquarter",
            measurer,
            size,
            band,
            edge_labels_flushed=True,
            is_anchor=True,
            fiscal_year_start_month=1,
            authored_format=None,
        )


class TestPairClearsCarriesYearRow:
    """The anchor tick is always ``anchor`` in the real labelExpr
    (``anchor || fiscal_month === 0``) -- the first LABELED tick carries the
    stacked year row whatever month it opens on, not only at a fiscal-year
    boundary. It is not the domain's literal first tick: the two diverge
    whenever the domain opens before its first labeled opener. But that row is a SEPARATE text line at a
    different y than row 1 (the tick's own month/quarter text) — it can
    only ever collide with a neighbor's own year row, never with a
    neighbor's row 1. A neighbor that does not itself carry a year row
    has nothing painted on that line to collide with, so the row is
    entirely inert for that pair's clearance check.
    """

    def test_leading_flush_year_row_does_not_widen_clearance_against_plain_neighbor(
        self,
    ) -> None:
        """The over-reservation bug this task fixes: a flushed "Mar" tick's
        hidden "2025" year row must NOT widen the clearance test against a
        neighbor ("Apr") that carries no year row of its own — the year
        row's own width is irrelevant to a pair where only one side has
        one. Only row 1 ("Mar" vs "Apr") governs."""
        import datetime

        from dbt_charts.core.font_measure import get_font_measurer
        from dbt_charts.core.render.chart.time_unit_detect import _pair_clears

        measurer = get_font_measurer(None)
        # Domain opens in March -- not a fiscal-year-start month -- so the
        # leading tick still carries the year row (anchor is unconditional),
        # but the trailing member of this pair (Apr) is neither the domain's
        # first nor last tick, so it never carries one itself. A third date
        # keeps this pair off the *trailing* edge (j == len(dates)-1 would
        # trigger its own, unrelated flush reservation on Apr).
        dates = [
            datetime.date(2025, 3, 1),
            datetime.date(2025, 4, 1),
            datetime.date(2025, 5, 1),
        ]
        size = 11.0
        w_mar = measurer.measure("Mar", size)
        w_2025 = measurer.measure("2025", size)
        w_apr = measurer.measure("Apr", size)
        buggy_extent = w_2025 + w_apr / 2  # old bug: maxed in the year row
        real_extent = w_mar + w_apr / 2  # correct: row 1 only
        band = (buggy_extent + real_extent) / 2
        assert _pair_clears(
            0,
            1,
            dates,
            "yearmonth",
            "yearmonth",
            measurer,
            size,
            band,
            edge_labels_flushed=True,
            is_anchor=True,
            fiscal_year_start_month=1,
            authored_format=None,
        )

    def test_both_edges_carrying_year_row_check_it_as_its_own_clearance(self) -> None:
        """When BOTH members of a pair carry the year row — a short domain
        whose only two labeled ticks are the leading and trailing edge, and
        the trailing one lands on a fiscal-year boundary — the year row is
        no longer inert: it is checked as its own independent clearance,
        alongside (not folded into) the row-1 check."""
        import datetime

        from dbt_charts.core.font_measure import get_font_measurer
        from dbt_charts.core.render.chart.time_unit_detect import _pair_clears

        measurer = get_font_measurer(None)
        dates = [datetime.date(2025, 1, 1), datetime.date(2026, 1, 1)]
        size = 11.0
        w_jan = measurer.measure("Jan", size)
        w_2025 = measurer.measure("2025", size)
        w_2026 = measurer.measure("2026", size)
        # Both ticks are flushed edges here, so both reserve full (not
        # half) width on each row they paint. Pick a band that clears row 1
        # ("Jan" + "Jan") but not the wider year row ("2025" + "2026").
        row1_extent = w_jan + w_jan
        year_extent = w_2025 + w_2026
        band = (row1_extent + year_extent) / 2
        assert not _pair_clears(
            0,
            1,
            dates,
            "yearmonth",
            "yearmonth",
            measurer,
            size,
            band,
            edge_labels_flushed=True,
            is_anchor=True,
            fiscal_year_start_month=1,
            authored_format=None,
        )
        # A band wide enough for both rows clears cleanly.
        assert _pair_clears(
            0,
            1,
            dates,
            "yearmonth",
            "yearmonth",
            measurer,
            size,
            year_extent,
            edge_labels_flushed=True,
            is_anchor=True,
            fiscal_year_start_month=1,
            authored_format=None,
        )

    def test_interior_fiscal_year_start_tick_carries_year_row_even_unflushed(
        self,
    ) -> None:
        """An interior January (or fiscal-year-start month) paints its year
        row whether or not it is a flush edge — the real labelExpr condition
        is ``anchor || fiscal_month === 0``, and the second half fires on ANY
        tick. The old code modeled ``j_carries_year_row`` as
        ``right_flush and fiscal_month_is_year_start`` — gated on being the
        domain's literal LAST tick — so an interior January paired against
        the anchor (which is never the trailing edge here) was reported as
        carrying no year row at all, silently skipping the clearance check
        the real render fails."""
        import datetime

        from dbt_charts.core.font_measure import get_font_measurer
        from dbt_charts.core.render.chart.time_unit_detect import _pair_clears

        measurer = get_font_measurer(None)
        # Anchor opens October (quarter format); the second labeled quarter
        # opener is an interior January — nowhere near the domain's last
        # tick. Both carry the year row for real.
        dates = [
            datetime.date(2023, 10, 1),
            datetime.date(2024, 1, 1),
            datetime.date(2024, 4, 1),
            datetime.date(2024, 7, 1),
        ]
        size = 11.0
        w_2023 = measurer.measure("2023", size)
        w_2024 = measurer.measure("2024", size)
        w_q4 = measurer.measure("Q4", size)
        w_q1 = measurer.measure("Q1", size)
        row1_extent = w_q4 + w_q1 / 2
        year_extent = w_2023 + w_2024 / 2
        # Wide enough for row 1, too narrow for the year row.
        band = (row1_extent + year_extent) / 2
        assert not _pair_clears(
            0,
            1,
            dates,
            "yearquarter",
            "yearquarter",
            measurer,
            size,
            band,
            edge_labels_flushed=True,
            is_anchor=True,
            fiscal_year_start_month=1,
            authored_format=None,
        )


def _weekly_dates(
    n: int, start: datetime.date = datetime.date(2010, 1, 4)
) -> list[str]:
    return [
        (start + datetime.timedelta(weeks=offset)).isoformat() for offset in range(n)
    ]


def _daily_dates(n: int, start: datetime.date = datetime.date(2000, 1, 1)) -> list[str]:
    return [
        (start + datetime.timedelta(days=offset)).isoformat() for offset in range(n)
    ]
