"""Unit tests for the sub-day clock label vocabulary.

Covers the pure-function default: ``default_subday_label_expr_for`` and its
two private helpers, ``_has_subday_component`` and ``_midnight_count``.
End-to-end wiring through ``build_cartesian_x_encoding`` (width-fallback,
authored-expr override) lives in
``test_cartesian_x_encoding.py::TestBuildCartesianXEncodingSubdayClock``.
Real rendered-tick verification (Vega's own generated ticks, not the
labelExpr we authored) lives in ``test_subday_clock_render.py``.
"""

from __future__ import annotations

import datetime as dt
import random

from dbt_charts.core.render.chart.time_unit_detect import (
    _has_hour_boundary,
    _has_subday_component,
    _midnight_count,
    default_subday_label_expr_for,
    default_subday_tick_interval,
    predicted_tick_count,
    predicted_tick_count_ceiling,
)

# A typical resolved tick count, used throughout so every "should apply"
# fixture below sits comfortably inside the tick-cadence window rather than
# incidentally riding one of its edges. This is the number
# default_subday_label_expr_for's caller resolves BEFORE calling it — either
# an authored ticks.count, or predicted_tick_count(available_width) — never
# computed inside the function itself. Matches predicted_tick_count(600.0).
_TYPICAL_TICK_COUNT = 13

# One calendar day of hourly instants, 2026-08-11T00:00 .. T23:00 (single midnight).
_ONE_DAY_HOURLY = [f"2026-08-11T{h:02d}:00:00" for h in range(24)]
# Two full days plus the closing midnight, 2026-08-11T00:00 .. 2026-08-13T00:00.
_TWO_DAY_HOURLY = (
    [f"2026-08-11T{h:02d}:00:00" for h in range(24)]
    + [f"2026-08-12T{h:02d}:00:00" for h in range(24)]
    + ["2026-08-13T00:00:00"]
)


class TestHasSubdayComponent:
    def test_false_for_date_only_values(self) -> None:
        parsed = [dt.date(2024, 1, 1), dt.date(2024, 1, 2)]
        assert _has_subday_component(parsed) is False

    def test_false_for_midnight_only_datetimes(self) -> None:
        parsed = [dt.datetime(2024, 1, 1, 0, 0, 0), dt.datetime(2024, 1, 2, 0, 0, 0)]
        assert _has_subday_component(parsed) is False

    def test_true_when_any_value_carries_an_hour(self) -> None:
        parsed = [dt.datetime(2024, 1, 1, 0, 0, 0), dt.datetime(2024, 1, 1, 9, 0, 0)]
        assert _has_subday_component(parsed) is True

    def test_true_when_only_minutes_are_nonzero(self) -> None:
        parsed = [dt.datetime(2024, 1, 1, 9, 30, 0)]
        assert _has_subday_component(parsed) is True


class TestMidnightCount:
    def test_single_day_domain_has_one_midnight(self) -> None:
        start = dt.datetime(2026, 8, 11, 0, 0)
        end = dt.datetime(2026, 8, 11, 23, 0)
        assert _midnight_count(start, end) == 1

    def test_two_day_domain_has_two_midnights(self) -> None:
        start = dt.datetime(2026, 8, 11, 0, 0)
        end = dt.datetime(2026, 8, 13, 0, 0)
        assert _midnight_count(start, end) == 2

    def test_closed_single_day_domain_has_one_midnight(self) -> None:
        # Midnight to the next midnight is one day of content: the closing
        # instant opens no day the chart shows, so no date row is owed.
        start = dt.datetime(2026, 8, 11, 0, 0)
        end = dt.datetime(2026, 8, 12, 0, 0)
        assert _midnight_count(start, end) == 1

    def test_mid_day_open_closing_on_midnight_keeps_both_midnights(self) -> None:
        # Opens at 18:00, so the two drawn midnights are different days and
        # need the date row to tell them apart.
        start = dt.datetime(2026, 8, 11, 18, 0)
        end = dt.datetime(2026, 8, 13, 0, 0)
        assert _midnight_count(start, end) == 2

    def test_domain_that_never_reaches_a_midnight_has_zero(self) -> None:
        start = dt.datetime(2026, 8, 11, 9, 0)
        end = dt.datetime(2026, 8, 11, 17, 0)
        assert _midnight_count(start, end) == 0

    def test_domain_opening_exactly_on_a_midnight_counts_it(self) -> None:
        start = dt.datetime(2026, 8, 11, 0, 0)
        end = dt.datetime(2026, 8, 11, 17, 0)
        assert _midnight_count(start, end) == 1


class TestHasHourBoundary:
    def test_domain_crossing_an_hour_is_true(self) -> None:
        start = dt.datetime(2026, 8, 11, 9, 5)
        end = dt.datetime(2026, 8, 11, 10, 5)
        assert _has_hour_boundary(start, end) is True

    def test_domain_never_reaching_an_hour_is_false(self) -> None:
        start = dt.datetime(2026, 8, 11, 9, 5)
        end = dt.datetime(2026, 8, 11, 9, 55)
        assert _has_hour_boundary(start, end) is False

    def test_domain_opening_exactly_on_the_hour_is_true(self) -> None:
        start = dt.datetime(2026, 8, 11, 9, 0)
        end = dt.datetime(2026, 8, 11, 9, 55)
        assert _has_hour_boundary(start, end) is True

    def test_domain_closing_exactly_on_the_hour_is_true(self) -> None:
        start = dt.datetime(2026, 8, 11, 9, 5)
        end = dt.datetime(2026, 8, 11, 10, 0)
        assert _has_hour_boundary(start, end) is True


class TestDefaultSubdayLabelExprFor:
    def test_none_for_date_only_values(self) -> None:
        assert (
            default_subday_label_expr_for(
                ["2024-01-01", "2024-01-02"],
                12,
                narrow=False,
                tick_count=_TYPICAL_TICK_COUNT,
                tick_count_ceiling=_TYPICAL_TICK_COUNT,
            )
            is None
        )

    def test_none_for_a_single_instant(self) -> None:
        assert (
            default_subday_label_expr_for(
                ["2026-08-11T09:00:00"],
                12,
                narrow=False,
                tick_count=_TYPICAL_TICK_COUNT,
                tick_count_ceiling=_TYPICAL_TICK_COUNT,
            )
            is None
        )

    def test_none_when_tick_count_is_unknown(self) -> None:
        """Without a resolved tick count — no authored cadence and no width
        to predict from — the tick step Vega will draw can't be known, so
        the vocabulary can't be shown to be safe. Skip it rather than guess.
        """
        assert (
            default_subday_label_expr_for(
                _ONE_DAY_HOURLY,
                12,
                narrow=False,
                tick_count=None,
                tick_count_ceiling=None,
            )
            is None
        )

    def test_clock_24_is_a_plain_time_short_format(self) -> None:
        expr = default_subday_label_expr_for(
            _ONE_DAY_HOURLY,
            24,
            narrow=False,
            tick_count=_TYPICAL_TICK_COUNT,
            tick_count_ceiling=_TYPICAL_TICK_COUNT,
        )
        assert expr == "timeFormat(datum.value, '%H:%M')"
        # Rule 3's anchor is a 12-hour-only concern -- rule 2 already gives
        # every 24-hour tick a full HH:MM label, so no anchor belongs on this
        # path at all.
        assert "datum.index" not in expr

    def test_clock_none_defaults_to_12(self) -> None:
        as_none = default_subday_label_expr_for(
            _ONE_DAY_HOURLY,
            None,
            narrow=False,
            tick_count=_TYPICAL_TICK_COUNT,
            tick_count_ceiling=_TYPICAL_TICK_COUNT,
        )
        as_twelve = default_subday_label_expr_for(
            _ONE_DAY_HOURLY,
            12,
            narrow=False,
            tick_count=_TYPICAL_TICK_COUNT,
            tick_count_ceiling=_TYPICAL_TICK_COUNT,
        )
        assert as_none == as_twelve

    def test_clock_12_words_vocabulary_carries_noon_and_midnight(self) -> None:
        expr = default_subday_label_expr_for(
            _ONE_DAY_HOURLY,
            12,
            narrow=False,
            tick_count=_TYPICAL_TICK_COUNT,
            tick_count_ceiling=_TYPICAL_TICK_COUNT,
        )
        assert expr is not None
        assert "'Midnight'" in expr
        assert "'Noon'" in expr
        # Rule 3: sub-hour ticks carry minutes only; the hour carries the
        # full (word-or-numeral) label.
        assert "minutes(datum.value) !== 0" in expr
        assert "':%M'" in expr
        # Rule 2 conditional: minute compaction only fires on the meridiem
        # clock, never as a bare zero-padded 12-hour form.
        assert "%-I%p" in expr

    def test_narrow_fallback_drops_the_words(self) -> None:
        expr = default_subday_label_expr_for(
            _ONE_DAY_HOURLY,
            12,
            narrow=True,
            tick_count=_TYPICAL_TICK_COUNT,
            tick_count_ceiling=_TYPICAL_TICK_COUNT,
        )
        assert expr is not None
        assert "Midnight" not in expr
        assert "Noon" not in expr
        # Still hour-anchored: minutes-only between hours, full 12-hour
        # numeral (with meridiem) at the hour.
        assert "minutes(datum.value) !== 0" in expr
        assert "%-I%p" in expr

    def test_narrow_fallback_is_a_no_op_for_clock_24(self) -> None:
        wide = default_subday_label_expr_for(
            _ONE_DAY_HOURLY,
            24,
            narrow=False,
            tick_count=_TYPICAL_TICK_COUNT,
            tick_count_ceiling=_TYPICAL_TICK_COUNT,
        )
        narrow = default_subday_label_expr_for(
            _ONE_DAY_HOURLY,
            24,
            narrow=True,
            tick_count=_TYPICAL_TICK_COUNT,
            tick_count_ceiling=_TYPICAL_TICK_COUNT,
        )
        assert wide == narrow

    def test_hour_boundary_present_keeps_the_plain_minutes_gate(self) -> None:
        """A domain that DOES cross an hour boundary needs no extra anchor —
        the hour itself already satisfies rule 3 wherever it falls.
        """
        values = ["2026-08-11T09:50:00", "2026-08-11T10:10:00"]
        expr = default_subday_label_expr_for(
            values,
            12,
            narrow=False,
            tick_count=_TYPICAL_TICK_COUNT,
            tick_count_ceiling=_TYPICAL_TICK_COUNT,
        )
        assert expr is not None
        assert "datum.index" not in expr

    def test_no_hour_boundary_anchors_the_first_tick_with_hour_and_minutes(
        self,
    ) -> None:
        """09:05 -> 09:55 never reaches an hour — the first tick must take
        a full-form label regardless of its own minutes (rule 3's anchor
        rule), and that full form must include the minutes: a bare hour
        label ("9am") would claim a clock the 09:05 tick does not have.
        """
        values = ["2026-08-11T09:05:00", "2026-08-11T09:55:00"]
        expr = default_subday_label_expr_for(
            values,
            12,
            narrow=False,
            tick_count=_TYPICAL_TICK_COUNT,
            tick_count_ceiling=_TYPICAL_TICK_COUNT,
        )
        assert expr is not None
        assert "datum.index === 0" in expr
        # The anchor's own full form carries both the hour AND the minutes.
        assert "%-I:%M%p" in expr
        # Every other tick still falls back to minute-only.
        assert "':%M'" in expr

    def test_single_midnight_domain_renders_single_row(self) -> None:
        expr = default_subday_label_expr_for(
            _ONE_DAY_HOURLY,
            12,
            narrow=False,
            tick_count=_TYPICAL_TICK_COUNT,
            tick_count_ceiling=_TYPICAL_TICK_COUNT,
        )
        assert expr is not None
        assert not expr.startswith("[")

    def test_multi_midnight_domain_adds_a_date_context_row(self) -> None:
        expr = default_subday_label_expr_for(
            _TWO_DAY_HOURLY,
            12,
            narrow=False,
            tick_count=_TYPICAL_TICK_COUNT,
            tick_count_ceiling=_TYPICAL_TICK_COUNT,
        )
        assert expr is not None
        assert expr.startswith("[") and expr.endswith("]")
        assert "'%b %-d'" in expr
        assert "hours(datum.value) === 0" in expr

    def test_context_row_never_appears_without_its_identity_row(self) -> None:
        """Rule 5's structural guarantee: a context cell never renders alone.

        The labelExpr array form always carries the full row-1 vocabulary as
        its first element — there is no code path that emits row 2 without
        row 1, because both are produced by one return statement.
        """
        expr = default_subday_label_expr_for(
            _TWO_DAY_HOURLY,
            12,
            narrow=False,
            tick_count=_TYPICAL_TICK_COUNT,
            tick_count_ceiling=_TYPICAL_TICK_COUNT,
        )
        assert expr is not None
        # Row 1 (the full clock vocabulary) and row 2 (the date gate) are
        # both present in the one array literal this function returns in a
        # single statement — there is no branch that emits one without the
        # other.
        assert "'Midnight'" in expr and "'Noon'" in expr
        assert "'%b %-d'" in expr

    def test_date_row_gate_is_the_same_for_both_clocks(self) -> None:
        expr24 = default_subday_label_expr_for(
            _TWO_DAY_HOURLY,
            24,
            narrow=False,
            tick_count=_TYPICAL_TICK_COUNT,
            tick_count_ceiling=_TYPICAL_TICK_COUNT,
        )
        assert expr24 is not None
        assert expr24.startswith("[")

    def test_tz_aware_utc_timestamps_do_not_crash(self) -> None:
        """Regression: a 'Z'-suffixed ISO string parses tz-aware, and comparing
        it against the naive datetimes _midnight_count builds internally used
        to raise TypeError. Reproduced by test_chart_date_bucket_shapes.py's
        "noon-utc iso month stamps" case once this default started running.
        """
        values = ["2024-01-01T12:00:00Z", "2024-01-01T21:00:00Z"]
        expr = default_subday_label_expr_for(
            values,
            None,
            narrow=False,
            tick_count=_TYPICAL_TICK_COUNT,
            tick_count_ceiling=_TYPICAL_TICK_COUNT,
        )
        assert expr is not None


class TestSubdayTickCadenceGate:
    """The vocabulary applies only when Vega's OWN tick generator — driven by
    domain span and chart width, not by the data's row-level cadence — would
    draw ticks inside the vocabulary's [1 minute, 1 day) window.

    Regression coverage: gating on the DATA's median gap (or the fraction of rows
    that individually carry a sub-day component) bounds a quantity adjacent
    to the one that actually decides what gets rendered. Vega computes tick
    *positions* from the scale domain's [min, max] and the chart's pixel
    width alone — how densely real rows fill that domain never enters the
    calculation — so only [domain span, chart width] can predict what Vega
    will draw. The boundary constants in time_unit_detect.py
    (``_TICK_DAY_STEP_BOUNDARY_HOURS`` / ``_TICK_MINUTE_STEP_BOUNDARY_SECONDS``)
    were derived from Vega-Lite's own compiled ``tickCount: ceil(width/40)``
    signal and d3-time's documented geometric-mean interval switch, then
    confirmed against real ``vl_convert.vegalite_to_svg`` renders.
    """

    def test_weekly_09_00_domain_26_points_falls_back_to_none(self) -> None:
        """Weekly Mondays at 09:00, 26 points, ~175-day
        span. At a typical 600px card Vega's own generator lands on
        day-grain (or coarser) ticks for any span past ~10.6 days — this
        domain is seventeen times that, so every tick would read "Midnight"
        and the date row would never carry a year.
        """
        start = dt.date(2024, 1, 1)
        values = [
            (start + dt.timedelta(weeks=i)).isoformat() + "T09:00:00" for i in range(26)
        ]
        assert (
            default_subday_label_expr_for(
                values,
                12,
                narrow=False,
                tick_count=_TYPICAL_TICK_COUNT,
                tick_count_ceiling=_TYPICAL_TICK_COUNT,
            )
            is None
        )

    def test_weekly_09_00_domain_51_points_falls_back_to_none(self) -> None:
        """Same shape as the weekly-Mondays case above, ~350-day span."""
        start = dt.date(2024, 1, 1)
        values = [
            (start + dt.timedelta(weeks=i)).isoformat() + "T09:00:00" for i in range(51)
        ]
        assert (
            default_subday_label_expr_for(
                values,
                12,
                narrow=False,
                tick_count=_TYPICAL_TICK_COUNT,
                tick_count_ceiling=_TYPICAL_TICK_COUNT,
            )
            is None
        )

    def test_monthly_09_00_domain_11_points_falls_back_to_none(self) -> None:
        """Monthly at 09:00, 11 points, ~300-day span."""
        start = dt.date(2024, 1, 1)
        values = [
            (start + dt.timedelta(days=30 * i)).isoformat() + "T09:00:00"
            for i in range(11)
        ]
        assert (
            default_subday_label_expr_for(
                values,
                12,
                narrow=False,
                tick_count=_TYPICAL_TICK_COUNT,
                tick_count_ceiling=_TYPICAL_TICK_COUNT,
            )
            is None
        )

    def test_500_raw_events_over_180_days_falls_back_to_none(self) -> None:
        """Raw, irregularly-spaced events. The per-row
        gaps here are dense (sub-day median) and would pass any
        data-cadence floor — the gate must reject this on domain span alone.
        """
        rng = random.Random(0)
        start = dt.datetime(2024, 1, 1)
        values = [
            (start + dt.timedelta(days=180 * rng.random())).isoformat()
            for _ in range(500)
        ]
        assert (
            default_subday_label_expr_for(
                values,
                12,
                narrow=False,
                tick_count=_TYPICAL_TICK_COUNT,
                tick_count_ceiling=_TYPICAL_TICK_COUNT,
            )
            is None
        )

    def test_300_raw_events_over_200_days_crossing_new_year_falls_back_to_none(
        self,
    ) -> None:
        """Same shape as the raw-events case above, spanning a year boundary — the
        date-context row would also be unable to disambiguate the two years.
        """
        rng = random.Random(1)
        start = dt.datetime(2024, 11, 1)
        values = [
            (start + dt.timedelta(days=200 * rng.random())).isoformat()
            for _ in range(300)
        ]
        assert (
            default_subday_label_expr_for(
                values,
                12,
                narrow=False,
                tick_count=_TYPICAL_TICK_COUNT,
                tick_count_ceiling=_TYPICAL_TICK_COUNT,
            )
            is None
        )

    def test_two_minute_domain_falls_back_to_none(self) -> None:
        """A median
        row-to-row gap of exactly one minute passed the old data-cadence
        floor, but Vega's own tick generator, at any ordinary chart width,
        subdivides a 2-minute domain into 15s/30s ticks — well under the
        vocabulary's minute-level floor. ``test_subday_clock_render.py``
        confirms this doesn't just fall back cleanly: Vega's own default
        format for this same domain renders with no duplicate labels either.
        """
        values = [
            "2026-08-11T09:00:00",
            "2026-08-11T09:01:00",
            "2026-08-11T09:02:00",
        ]
        assert (
            default_subday_label_expr_for(
                values,
                12,
                narrow=False,
                tick_count=_TYPICAL_TICK_COUNT,
                tick_count_ceiling=_TYPICAL_TICK_COUNT,
            )
            is None
        )

    def test_three_hourly_cadence_with_midnight_readings_still_applies(self) -> None:
        """A real intraday ladder that happens to include
        exact-midnight readings (one every day) must still get the
        vocabulary. The deleted fraction-of-subday-rows gate used to count
        every midnight reading against the data, making the vocabulary
        unreachable at exactly this cadence — one of the worksheet's own
        named rungs (3-hourly and coarser).
        """
        start = dt.datetime(2026, 8, 11, 0, 0)
        values = [(start + dt.timedelta(hours=3 * i)).isoformat() for i in range(16)]
        expr = default_subday_label_expr_for(
            values,
            12,
            narrow=False,
            tick_count=_TYPICAL_TICK_COUNT,
            tick_count_ceiling=_TYPICAL_TICK_COUNT,
        )
        assert expr is not None
        assert "'Midnight'" in expr

    def test_fewer_ticks_tightens_the_ceiling(self) -> None:
        """The safe span window is a joint function of span AND tick count,
        not span alone — a caller resolving fewer ticks (a narrower plot, or
        an authored ``ticks.count``) legitimately stops applying the
        vocabulary to the identical 9-day domain that applies at 15 ticks.
        """
        start = dt.datetime(2024, 1, 1, 9, 0)
        values = [start.isoformat(), (start + dt.timedelta(days=9)).isoformat()]
        assert (
            default_subday_label_expr_for(
                values, 12, narrow=False, tick_count=15, tick_count_ceiling=15
            )
            is not None
        )
        assert (
            default_subday_label_expr_for(
                values, 12, narrow=False, tick_count=4, tick_count_ceiling=4
            )
            is None
        )

    def test_tick_count_ceiling_tightens_the_lower_gate_independently(self) -> None:
        """At a declared 600px card, predicted_tick_count
        under-counts Vega's real tickCount (13 vs the real 14) enough to open
        a band of spans -- [551.5s, 594.0s) -- that clear the under-counted
        floor while Vega's own generator already subdivides them into
        sub-minute ticks. Passing the higher ceiling (14) for the lower gate
        must bail here even though the plain tick_count (13) alone would not.
        """
        start = dt.datetime(2026, 8, 11, 9, 0)
        values = [start.isoformat(), (start + dt.timedelta(seconds=570)).isoformat()]
        assert (
            default_subday_label_expr_for(
                values, 12, narrow=False, tick_count=13, tick_count_ceiling=13
            )
            is not None
        )
        assert (
            default_subday_label_expr_for(
                values, 12, narrow=False, tick_count=13, tick_count_ceiling=14
            )
            is None
        )


class TestPredictedTickCount:
    """``predicted_tick_count`` — used only when nothing authored a tick
    cadence. Subtracts a conservative, measured y-axis chrome allowance
    (``_ESTIMATED_Y_AXIS_CHROME_PX``) before applying Vega-Lite's own
    ``ceil(width/40)`` tickCount default, because vl_convert's
    ``autosize: fit`` reserves y-axis chrome only its own layout engine
    measures — invisible to Python before the spec is compiled (see the
    module-level comment above the constant for the real renders that
    measured it).
    """

    def test_subtracts_the_chrome_allowance_before_the_pitch_formula(self) -> None:
        # (600 - 100) / 40 == 12.5 -> ceil 13.
        assert predicted_tick_count(600.0) == 13
        # (1000 - 100) / 40 == 22.5 -> ceil 23.
        assert predicted_tick_count(1000.0) == 23

    def test_none_for_unknown_width(self) -> None:
        assert predicted_tick_count(None) is None

    def test_none_when_chrome_allowance_consumes_the_whole_width(self) -> None:
        assert predicted_tick_count(100.0) is None
        assert predicted_tick_count(50.0) is None
        assert predicted_tick_count(-10.0) is None


class TestPredictedTickCountCeiling:
    """``predicted_tick_count_ceiling`` — the safe UPPER bound counterpart to
    ``predicted_tick_count``'s LOWER bound. No chrome allowance: Vega's real
    plot rectangle can only be narrower than the declared width, so the bare
    ``ceil(width/40)`` can never undershoot Vega's real tickCount.
    """

    def test_no_chrome_subtracted_before_the_pitch_formula(self) -> None:
        # 600 / 40 == 15 exactly -> ceil 15 (vs. predicted_tick_count's 13).
        assert predicted_tick_count_ceiling(600.0) == 15
        assert predicted_tick_count_ceiling(556.0) == 14

    def test_none_for_unknown_or_non_positive_width(self) -> None:
        assert predicted_tick_count_ceiling(None) is None
        assert predicted_tick_count_ceiling(0.0) is None
        assert predicted_tick_count_ceiling(-10.0) is None


class TestSubdayYearCollisionGate:
    """``_SUBDAY_MAX_DOMAIN_SPAN`` guards a label-*text* collision (two years
    printing the same "%b %-d"), a different failure mode than
    ``TestSubdayTickCadenceGate`` above and not implied by it. At the
    typical 15-tick count most tests in this module use, the tick-cadence
    ceiling already bails around 10.6 days — far short of 365 — so these use
    a synthetic, unrealistically high tick count to isolate the
    year-collision boundary itself.
    """

    _MANY_TICKS_TO_ISOLATE_THE_YEAR_CEILING = 625

    def test_domain_just_under_the_year_span_ceiling_still_applies(self) -> None:
        """364 days apart: two instants can only share a calendar month/day
        at least 365 days apart, so a 364-day domain can never produce the
        year-collision the ceiling exists to prevent — the vocabulary stays
        in scope right up to that boundary.
        """
        values = ["2024-01-01T09:00:00", "2024-12-30T09:00:00"]
        assert (
            default_subday_label_expr_for(
                values,
                12,
                narrow=False,
                tick_count=self._MANY_TICKS_TO_ISOLATE_THE_YEAR_CEILING,
                tick_count_ceiling=self._MANY_TICKS_TO_ISOLATE_THE_YEAR_CEILING,
            )
            is not None
        )

    def test_domain_at_the_year_span_ceiling_falls_back_to_none(self) -> None:
        """365 days apart is exactly the minimum gap at which two instants
        can land on the same calendar month/day in different years — the
        ceiling bails here, one day past the case above.
        """
        values = ["2024-01-01T09:00:00", "2025-01-01T09:00:00"]
        assert (
            default_subday_label_expr_for(
                values,
                12,
                narrow=False,
                tick_count=self._MANY_TICKS_TO_ISOLATE_THE_YEAR_CEILING,
                tick_count_ceiling=self._MANY_TICKS_TO_ISOLATE_THE_YEAR_CEILING,
            )
            is None
        )


class TestSubdayOffsetTimezoneAgreement:
    """The date-row gate (Python) and the rendered content gate (Vega) must
    read the same clock for offset-bearing timestamps — both sides now
    convert to UTC explicitly rather than one converting and the other
    reading local wall-clock components.
    """

    def test_offset_bearing_values_use_utc_accessors(self) -> None:
        values = ["2026-08-11T05:00:00+05:00", "2026-08-12T23:00:00+05:00"]
        expr = default_subday_label_expr_for(
            values,
            12,
            narrow=False,
            tick_count=_TYPICAL_TICK_COUNT,
            tick_count_ceiling=_TYPICAL_TICK_COUNT,
        )
        assert expr is not None
        assert "utchours" in expr
        assert "utcFormat" in expr
        # No bare local accessor call anywhere — every occurrence of
        # "hours"/"timeFormat" in the expression is the "utc"-prefixed form.
        assert "timeFormat(datum.value" not in expr
        assert expr.count("hours(datum.value)") == expr.count("utchours(datum.value)")

    def test_offset_bearing_domain_crossing_two_utc_midnights_shows_date_row(
        self,
    ) -> None:
        """08-11T05:00+05:00 and 08-12T23:00+05:00 are 08-11T00:00Z and
        08-12T18:00Z in UTC — two distinct UTC midnights fall in between
        (08-11T00:00Z and 08-12T00:00Z), so the date row must appear.
        Before the fix, the Python gate read the *local* wall clocks
        (08-11 05:00 -> 08-12 23:00, one midnight) and suppressed it while
        Vega's own hours() — evaluated on the same UTC instants — would
        still render two indistinguishable bare "Midnight" ticks.
        """
        values = ["2026-08-11T05:00:00+05:00", "2026-08-12T23:00:00+05:00"]
        expr = default_subday_label_expr_for(
            values,
            12,
            narrow=False,
            tick_count=_TYPICAL_TICK_COUNT,
            tick_count_ceiling=_TYPICAL_TICK_COUNT,
        )
        assert expr is not None
        assert expr.startswith("[") and expr.endswith("]")

    def test_naive_values_keep_local_accessors(self) -> None:
        """No offset anywhere in the domain: unchanged from before this fix,
        matching Vega-Lite's own local-time parsing of a naive datetime
        string on a continuous temporal scale.
        """
        expr = default_subday_label_expr_for(
            _TWO_DAY_HOURLY,
            12,
            narrow=False,
            tick_count=_TYPICAL_TICK_COUNT,
            tick_count_ceiling=_TYPICAL_TICK_COUNT,
        )
        assert expr is not None
        assert "hours(datum.value)" in expr
        assert "utchours" not in expr

    def test_domain_mixing_naive_and_offset_values_bails(self) -> None:
        """A domain mixing a naive and an offset-bearing value has no clock
        either accessor choice reads correctly: Vega parses the naive value
        as local regardless of what we pick, and Python's own gate math
        (``instants``) would normalize the offset value to UTC while leaving
        the naive one untouched, silently reading a blended clock. Neither
        ``any()`` nor ``all()`` on the offset flags is right for this
        domain — bail rather than mislabel a tick for viewers off UTC.
        """
        values = ["2026-08-11T09:00:00", "2026-08-12T09:00:00+00:00"]
        expr = default_subday_label_expr_for(
            values,
            12,
            narrow=False,
            tick_count=_TYPICAL_TICK_COUNT,
            tick_count_ceiling=_TYPICAL_TICK_COUNT,
        )
        assert expr is None

    def test_domain_mixing_naive_and_bare_date_only_bails(self) -> None:
        """A bare date-only value ("2026-08-11") is ALSO an absolute instant
        — Vega parses a date-only string as UTC midnight, never local (the
        same convention the calendar-bucketed vocabulary relies on). Mixed
        with a naive datetime, this is the identical hazard the explicit-
        offset case above bails on: `dt.date` carries no `tzinfo` attribute
        at all, so a check gated only on `isinstance(p, dt.datetime) and
        p.tzinfo is not None` misses it entirely and reads local accessors
        for a domain Vega will read partly as UTC.
        """
        values = ["2026-08-11", "2026-08-11T09:00:00"]
        expr = default_subday_label_expr_for(
            values,
            12,
            narrow=False,
            tick_count=_TYPICAL_TICK_COUNT,
            tick_count_ceiling=_TYPICAL_TICK_COUNT,
        )
        assert expr is None

    def test_date_only_authored_domain_mixed_with_naive_data_bails(self) -> None:
        """The mixed-clock bail must compare the AUTHORED
        DOMAIN against the DATA, not just against itself. A date-only
        authored domain (Vega parses date-only strings as UTC midnight) that
        frames naive sub-day data (Vega parses those as local) is the same
        "neither accessor can get right" hazard the values-only mixed check
        above bails on -- reachable only once an authored domain is in play,
        which nothing before this test exercised (the existing authored-
        domain coverage uses a full ISO naive-datetime domain, not a
        date-only one).
        """
        values = [
            "2026-08-11T06:00:00",
            "2026-08-11T12:00:00",
            "2026-08-11T20:00:00",
        ]
        expr = default_subday_label_expr_for(
            values,
            12,
            narrow=False,
            tick_count=_TYPICAL_TICK_COUNT,
            tick_count_ceiling=_TYPICAL_TICK_COUNT,
            authored_domain=("2026-08-11", "2026-08-12"),
        )
        assert expr is None


def _minutes_from_0900(count: int, every: int) -> list[str]:
    start = dt.datetime(2026, 3, 2, 9, 0)
    return [(start + dt.timedelta(minutes=every * i)).isoformat() for i in range(count)]


class TestDefaultSubdayTickInterval:
    def test_picks_the_finest_rung_that_fits(self) -> None:
        # 2 hours at 1000px fits 14 ticks: 5-minute (24) is too many, 10-minute fits.
        values = _minutes_from_0900(31, 4)
        assert default_subday_tick_interval(values, None, 1000.0) == ("minutes", 10, 12)

    def test_whole_hour_rungs_for_a_longer_span(self) -> None:
        values = _minutes_from_0900(53, 15)
        assert default_subday_tick_interval(values, None, 1000.0) == ("hours", 1, 13)

    def test_authored_domain_decides_the_span(self) -> None:
        values = _minutes_from_0900(31, 4)
        domain = ("2026-03-02T00:00:00", "2026-03-03T00:00:00")
        assert default_subday_tick_interval(values, domain, 1000.0) == ("hours", 2, 12)

    def test_no_rung_fits_a_multi_day_span(self) -> None:
        values = _minutes_from_0900(14 * 24 * 4, 15)
        assert default_subday_tick_interval(values, None, 1000.0) is None

    def test_no_width_no_ladder(self) -> None:
        assert default_subday_tick_interval(_ONE_DAY_HOURLY, None, None) is None

    def test_a_day_only_series_is_not_subday(self) -> None:
        values = [f"2026-03-{d:02d}" for d in range(1, 10)]
        assert default_subday_tick_interval(values, None, 1000.0) is None

    def test_utc_anchored_values_keep_vegas_ticks(self) -> None:
        values = [f"{v}Z" for v in _minutes_from_0900(31, 4)]
        assert default_subday_tick_interval(values, None, 1000.0) is None
