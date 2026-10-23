"""Unit tests for time_unit_detect.detect_time_unit."""

from __future__ import annotations

import datetime as dt

import pytest

from dbt_charts.core.render.chart.time_unit_detect import (
    detect_time_unit,
    normalize_labeled_temporal,
)

from ._svg_render import render_board_to_svg


class TestDetectTimeUnit:
    # ── year ────────────────────────────────────────────────────────────

    def test_year_iso_dates(self) -> None:
        values = ["2022-01-01", "2023-01-01", "2024-01-01"]
        assert detect_time_unit(values) == "year"

    def test_year_date_objects(self) -> None:
        values = [dt.date(2022, 1, 1), dt.date(2023, 1, 1), dt.date(2024, 1, 1)]
        assert detect_time_unit(values) == "year"

    # ── yearquarter ─────────────────────────────────────────────────────

    def test_yearquarter_iso_dates(self) -> None:
        values = [
            "2024-01-01",
            "2024-04-01",
            "2024-07-01",
            "2024-10-01",
            "2025-01-01",
        ]
        assert detect_time_unit(values) == "yearquarter"

    def test_yearquarter_date_objects(self) -> None:
        values = [
            dt.date(2024, 1, 1),
            dt.date(2024, 4, 1),
            dt.date(2024, 7, 1),
            dt.date(2024, 10, 1),
        ]
        assert detect_time_unit(values) == "yearquarter"

    # ── yearmonth ───────────────────────────────────────────────────────

    def test_yearmonth_iso_dates(self) -> None:
        values = [
            "2024-01-01",
            "2024-02-01",
            "2024-03-01",
            "2024-05-01",
        ]
        assert detect_time_unit(values) == "yearmonth"

    def test_yearmonth_date_objects(self) -> None:
        values = [dt.date(2024, m, 1) for m in (1, 2, 3, 5)]
        assert detect_time_unit(values) == "yearmonth"

    def test_yearmonth_sparse_gaps_detected(self) -> None:
        # Jan / Feb / May — middle months missing
        values = ["2024-01-01", "2024-02-01", "2024-05-01"]
        assert detect_time_unit(values) == "yearmonth"

    # ── yearweek ────────────────────────────────────────────────────────

    def test_yearweek_iso_dates(self) -> None:
        # Mondays only
        values = [
            "2024-01-01",  # Monday
            "2024-01-08",  # Monday
            "2024-01-15",  # Monday
        ]
        assert detect_time_unit(values) == "yearweek"

    def test_yearweek_date_objects(self) -> None:
        values = [dt.date(2024, 1, 1), dt.date(2024, 1, 8), dt.date(2024, 1, 15)]
        assert detect_time_unit(values) == "yearweek"

    def test_yearweek_sunday_aligned(self) -> None:
        # Real-world weekly reports are not always ISO-Monday-aligned —
        # Sunday-start weeks, Saturday close, mid-week pay-period reports.
        # All same-weekday + 7-day cadence still wants yearweek so the smart
        # label resolver renders monthly cadence instead of "7 Jan, 14 Jan…".
        values = ["2024-01-07", "2024-01-14", "2024-01-21", "2024-01-28"]
        assert all(dt.date.fromisoformat(v).weekday() == 6 for v in values)  # Sun
        assert detect_time_unit(values) == "yearweek"

    def test_yearweek_with_gaps_still_detects(self) -> None:
        # Missing weeks are common (holiday closures); 14-day jumps still
        # qualify since the same-weekday predicate covers any 7-day multiple.
        # Gaps: [7, 14, 14] — median = 14, within the ≤14-day gate.
        values = ["2024-01-08", "2024-01-15", "2024-01-29", "2024-02-12"]
        assert detect_time_unit(values) == "yearweek"

    def test_same_weekday_28day_spacing_not_yearweek(self) -> None:
        # 28-day spacing is 4-week intervals, not weekly. The same-weekday
        # predicate misfired on any 7-day-multiple gap; the spacing gate
        # (median > 14 days) must reject it and return None so the area/line
        # chart gets a continuous temporal scale rather than 78 ordinal weekly
        # buckets for 20 data points. Mirrors three_series.csv in area-stack-modes.
        start = dt.date(2024, 1, 7)  # Sunday
        values = [start + dt.timedelta(days=28 * i) for i in range(20)]
        assert all(v.weekday() == 6 for v in values), "fixture must be all Sundays"
        assert detect_time_unit(values) is None

    # ── yearmonthdate ───────────────────────────────────────────────────

    def test_yearmonthdate_arbitrary_dates(self) -> None:
        values = ["2024-01-01", "2024-01-15", "2024-02-07"]
        assert detect_time_unit(values) == "yearmonthdate"

    def test_yearmonthdate_date_objects(self) -> None:
        values = [dt.date(2024, 1, 15), dt.date(2024, 2, 7), dt.date(2024, 3, 22)]
        assert detect_time_unit(values) == "yearmonthdate"

    def test_yearmonthdate_midnight_datetimes(self) -> None:
        values = [
            dt.datetime(2024, 1, 15, 0, 0, 0),
            dt.datetime(2024, 2, 7, 0, 0, 0),
        ]
        assert detect_time_unit(values) == "yearmonthdate"

    # ── sub-daily → None ────────────────────────────────────────────────

    def test_subdaily_returns_none(self) -> None:
        values = [
            dt.datetime(2024, 1, 15, 9, 30, 0),
            dt.datetime(2024, 1, 15, 10, 0, 0),
        ]
        assert detect_time_unit(values) is None

    def test_subdaily_iso_string_returns_none(self) -> None:
        values = ["2024-01-15T09:30:00", "2024-01-15T10:00:00"]
        assert detect_time_unit(values) is None

    def test_subdaily_space_separator_returns_none(self) -> None:
        # DB drivers stringify datetime as "YYYY-MM-DD HH:MM:SS" (space, not T).
        # normalize_data_types calls str() on datetime objects producing this format.
        values = ["2024-01-15 09:30:00", "2024-01-15 10:00:00"]
        assert detect_time_unit(values) is None

    def test_daily_midnight_space_separator_returns_yearmonthdate(self) -> None:
        # str(datetime(2024, 1, 15, 0, 0, 0)) → "2024-01-15 00:00:00"
        # Zero hms = midnight = daily grain, not sub-daily.
        values = ["2024-01-15 00:00:00", "2024-01-16 00:00:00", "2024-01-17 00:00:00"]
        assert detect_time_unit(values) == "yearmonthdate"

    # ── edge cases ──────────────────────────────────────────────────────

    def test_fewer_than_two_distinct_returns_none(self) -> None:
        assert detect_time_unit([]) is None
        assert detect_time_unit(["2024-01-01"]) is None
        assert detect_time_unit([None]) is None

    def test_duplicate_values_counted_once(self) -> None:
        # Three rows but only 2 distinct months — still detects yearmonth
        values = ["2024-01-01", "2024-01-01", "2024-02-01", "2024-02-01"]
        assert detect_time_unit(values) == "yearmonth"

    def test_none_values_ignored(self) -> None:
        values = [None, "2024-01-01", None, "2024-02-01", None]
        assert detect_time_unit(values) == "yearmonth"

    def test_many_unparseable_raises(self) -> None:
        # ≥10% bad → fail loud
        values = ["not-a-date", "2024-01-01", "2024-02-01"]
        with pytest.raises(ValueError, match="unparseable"):
            detect_time_unit(values)

    def test_few_unparseable_ok(self) -> None:
        # <10% bad values → still detects (bad values excluded)
        # 1 bad out of 11 total distinct = 9% → ok
        values = ["bad"] + [f"2024-{m:02d}-01" for m in range(1, 12)]
        # 1/12 ≈ 8.3% → below 10% threshold
        result = detect_time_unit(values)
        assert result == "yearmonth"


class TestNormalizeLabeledTemporal:
    def test_week_labels_converted_to_mondays(self) -> None:
        data = [{"w": "2024-W01", "v": 1}, {"w": "2024-W02", "v": 2}]
        result = normalize_labeled_temporal(data, "w")
        assert result[0]["w"] == "2024-01-01"  # Monday of W01 2024
        assert result[1]["w"] == "2024-01-08"  # Monday of W02 2024

    def test_week_labels_cross_year(self) -> None:
        # ISO week W52 in 2023 (Monday) and W01 in 2024 (Monday)
        data = [{"w": "2023-W52", "v": 1}, {"w": "2024-W01", "v": 2}]
        result = normalize_labeled_temporal(data, "w")
        assert result[0]["w"] == "2023-12-25"  # Monday of W52 2023
        assert result[1]["w"] == "2024-01-01"  # Monday of W01 2024

    def test_quarter_labels_converted_to_quarter_starts(self) -> None:
        data = [
            {"q": "2024-Q1", "v": 1},
            {"q": "2024-Q2", "v": 2},
            {"q": "2024-Q3", "v": 3},
            {"q": "2024-Q4", "v": 4},
        ]
        result = normalize_labeled_temporal(data, "q")
        assert result[0]["q"] == "2024-01-01"
        assert result[1]["q"] == "2024-04-01"
        assert result[2]["q"] == "2024-07-01"
        assert result[3]["q"] == "2024-10-01"

    def test_quarter_labels_cross_year(self) -> None:
        data = [{"q": "2023-Q4", "v": 1}, {"q": "2024-Q1", "v": 2}]
        result = normalize_labeled_temporal(data, "q")
        assert result[0]["q"] == "2023-10-01"
        assert result[1]["q"] == "2024-01-01"

    def test_non_labeled_data_unchanged(self) -> None:
        data = [{"d": "2024-01-15", "v": 1}, {"d": "2024-02-01", "v": 2}]
        result = normalize_labeled_temporal(data, "d")
        # ISO dates are not labels — pass through unchanged
        assert result == data

    def test_mixed_week_labels_and_iso_dates_raises(self) -> None:
        data = [{"d": "2024-W01", "v": 1}, {"d": "2024-01-15", "v": 2}]
        with pytest.raises(ValueError, match="mixed label"):
            normalize_labeled_temporal(data, "d")

    def test_mixed_quarter_labels_and_iso_dates_raises(self) -> None:
        data = [{"d": "2024-Q1", "v": 1}, {"d": "2024-01-01", "v": 2}]
        with pytest.raises(ValueError, match="mixed label"):
            normalize_labeled_temporal(data, "d")

    def test_mixed_quarter_and_week_labels_raises(self) -> None:
        data = [{"d": "2024-Q1", "v": 1}, {"d": "2024-W01", "v": 2}]
        with pytest.raises(ValueError, match="mixed label"):
            normalize_labeled_temporal(data, "d")

    def test_invalid_week_number_raises_clear_error(self) -> None:
        # 2024 has only 52 ISO weeks; W53 is invalid for that year
        data = [{"w": "2024-W53", "v": 1}, {"w": "2024-W01", "v": 2}]
        with pytest.raises(ValueError, match="not a valid ISO week"):
            normalize_labeled_temporal(data, "w")

    def test_non_x_fields_unchanged(self) -> None:
        data = [{"x": "2024-Q1", "y": 100}]
        result = normalize_labeled_temporal(data, "x")
        assert result[0]["y"] == 100  # other fields preserved

    def test_none_values_preserved(self) -> None:
        data = [{"w": "2024-W01", "v": 1}, {"w": None, "v": 2}]
        result = normalize_labeled_temporal(data, "w")
        assert result[0]["w"] == "2024-01-01"
        assert result[1]["w"] is None

    def test_empty_data_unchanged(self) -> None:
        assert normalize_labeled_temporal([], "x") == []


class TestDetectTimeUnitLabeledFormats:
    """detect_time_unit accepts DATE_LIKE bucket string vocabulary."""

    # ── YYYY-MM → yearmonth ──────────────────────────────────────────────
    def test_yearmonth_yyyy_mm_strings(self) -> None:
        vals = [f"2024-{m:02d}" for m in range(1, 5)]
        assert detect_time_unit(vals) == "yearmonth"

    def test_yearmonth_yyyy_mm_sparse(self) -> None:
        assert detect_time_unit(["2024-01", "2024-06", "2024-12"]) == "yearmonth"

    def test_yearmonth_yyyy_mm_cross_year(self) -> None:
        assert detect_time_unit(["2023-11", "2023-12", "2024-01"]) == "yearmonth"

    # ── Mon YYYY → yearmonth ─────────────────────────────────────────────
    def test_yearmonth_mon_yyyy_strings(self) -> None:
        assert detect_time_unit(["Jan 2024", "Feb 2024", "Mar 2024"]) == "yearmonth"

    def test_yearmonth_mon_yyyy_case_insensitive(self) -> None:
        assert detect_time_unit(["jan 2024", "FEB 2024", "Mar 2024"]) == "yearmonth"

    # ── MM/YYYY → yearmonth ──────────────────────────────────────────────
    def test_yearmonth_mm_yyyy_strings(self) -> None:
        assert detect_time_unit(["01/2024", "02/2024", "03/2024"]) == "yearmonth"

    # ── Q1 YYYY / 2024Q1 alternate quarter spellings → yearquarter ───────
    def test_yearquarter_q_yyyy_strings(self) -> None:
        assert detect_time_unit(["Q1 2024", "Q2 2024", "Q3 2024"]) == "yearquarter"

    def test_yearquarter_yyyy_q_strings(self) -> None:
        assert detect_time_unit(["2024Q1", "2024Q2", "2024Q3"]) == "yearquarter"

    # ── FY2024 → year ────────────────────────────────────────────────────
    def test_year_fy_strings(self) -> None:
        assert detect_time_unit(["FY2022", "FY2023", "FY2024"]) == "year"

    # ── MM/DD/YYYY → yearmonthdate ───────────────────────────────────────
    def test_yearmonthdate_mm_dd_yyyy(self) -> None:
        assert (
            detect_time_unit(["01/15/2024", "02/20/2024", "03/05/2024"])
            == "yearmonthdate"
        )

    # ── Mon DD, YYYY → yearmonthdate ─────────────────────────────────────
    def test_yearmonthdate_mon_dd_yyyy(self) -> None:
        assert detect_time_unit(["Jan 15, 2024", "Feb 20, 2024"]) == "yearmonthdate"

    def test_yearmonthdate_mon_dd_yyyy_no_comma(self) -> None:
        assert detect_time_unit(["Jan 15 2024", "Feb 20 2024"]) == "yearmonthdate"

    # ── Week spelled (W32 2024 / Week 32 2024) → yearweek ────────────────
    def test_yearweek_w_spelled(self) -> None:
        assert detect_time_unit(["W01 2024", "W02 2024", "W03 2024"]) == "yearweek"

    def test_yearweek_week_spelled(self) -> None:
        assert detect_time_unit(["Week 1 2024", "Week 2 2024"]) == "yearweek"


class TestNormalizeLabeledTemporalNewFormats:
    """normalize_labeled_temporal converts new bucket formats to ISO dates."""

    def test_yyyy_mm_normalized_to_iso(self) -> None:
        data = [{"m": "2024-01", "v": 1}, {"m": "2024-02", "v": 2}]
        result = normalize_labeled_temporal(data, "m")
        assert result[0]["m"] == "2024-01-01"
        assert result[1]["m"] == "2024-02-01"

    def test_mon_yyyy_normalized_to_iso(self) -> None:
        data = [{"m": "Jan 2024", "v": 1}, {"m": "Feb 2024", "v": 2}]
        result = normalize_labeled_temporal(data, "m")
        assert result[0]["m"] == "2024-01-01"
        assert result[1]["m"] == "2024-02-01"

    def test_mm_yyyy_normalized_to_iso(self) -> None:
        data = [{"m": "01/2024", "v": 1}, {"m": "03/2024", "v": 2}]
        result = normalize_labeled_temporal(data, "m")
        assert result[0]["m"] == "2024-01-01"
        assert result[1]["m"] == "2024-03-01"

    def test_q_yyyy_normalized_to_iso(self) -> None:
        data = [{"m": "Q1 2024", "v": 1}, {"m": "Q2 2024", "v": 2}]
        result = normalize_labeled_temporal(data, "m")
        assert result[0]["m"] == "2024-01-01"
        assert result[1]["m"] == "2024-04-01"

    def test_yyyy_q_normalized_to_iso(self) -> None:
        data = [{"m": "2024Q1", "v": 1}, {"m": "2024Q2", "v": 2}]
        result = normalize_labeled_temporal(data, "m")
        assert result[0]["m"] == "2024-01-01"
        assert result[1]["m"] == "2024-04-01"

    def test_fy_normalized_to_iso(self) -> None:
        data = [{"m": "FY2023", "v": 1}, {"m": "FY2024", "v": 2}]
        result = normalize_labeled_temporal(data, "m")
        assert result[0]["m"] == "2023-01-01"
        assert result[1]["m"] == "2024-01-01"

    def test_mixed_yyyy_mm_and_mon_yyyy_raises(self) -> None:
        data = [{"m": "2024-01", "v": 1}, {"m": "Feb 2024", "v": 2}]
        with pytest.raises(ValueError, match="mixed label"):
            normalize_labeled_temporal(data, "m")

    def test_mixed_yyyy_mm_and_iso_date_raises(self) -> None:
        data = [{"m": "2024-01", "v": 1}, {"m": "2024-02-01", "v": 2}]
        with pytest.raises(ValueError, match="mixed label"):
            normalize_labeled_temporal(data, "m")

    def test_none_preserved_with_yyyy_mm(self) -> None:
        data = [{"m": "2024-01", "v": 1}, {"m": None, "v": 2}]
        result = normalize_labeled_temporal(data, "m")
        assert result[0]["m"] == "2024-01-01"
        assert result[1]["m"] is None


class TestNormalizeLabeledTemporalYearShaped:
    """Year-shaped x values (INTEGER or bare 4-digit VARCHAR) normalize to ISO."""

    def test_integer_years_normalized_to_iso(self) -> None:
        data = [{"y": 2014, "v": 1}, {"y": 2015, "v": 2}]
        result = normalize_labeled_temporal(data, "y")
        assert result[0]["y"] == "2014-01-01"
        assert result[1]["y"] == "2015-01-01"

    def test_string_years_normalized_to_iso(self) -> None:
        data = [{"y": "2014", "v": 1}, {"y": "2015", "v": 2}]
        result = normalize_labeled_temporal(data, "y")
        assert result[0]["y"] == "2014-01-01"
        assert result[1]["y"] == "2015-01-01"

    def test_normalized_years_detect_as_year_time_unit(self) -> None:
        data = [{"y": 2014, "v": 1}, {"y": 2015, "v": 2}]
        result = normalize_labeled_temporal(data, "y")
        assert detect_time_unit([row["y"] for row in result]) == "year"

    def test_non_year_integers_unchanged(self) -> None:
        data = [{"y": 1, "v": 1}, {"y": 2, "v": 2}]
        result = normalize_labeled_temporal(data, "y")
        assert result == data

    def test_none_preserved_with_integer_years(self) -> None:
        data = [{"y": 2014, "v": 1}, {"y": None, "v": 2}]
        result = normalize_labeled_temporal(data, "y")
        assert result[0]["y"] == "2014-01-01"
        assert result[1]["y"] is None


def _month_ends(start: dt.date, count: int) -> list[dt.date]:
    """`count` consecutive month-end dates, first one in `start`'s month."""
    out: list[dt.date] = []
    year, month = start.year, start.month
    for _ in range(count):
        nxt = dt.date(year + month // 12, month % 12 + 1, 1)
        out.append(nxt - dt.timedelta(days=1))
        year, month = nxt.year, nxt.month
    return out


def _quarter_ends(start: dt.date, count: int) -> list[dt.date]:
    """`count` consecutive quarter-end dates, first one in `start`'s quarter."""
    return _month_ends(start, count * 3)[::3]


def _render_bar_of_dates(dates: list[dt.date], fiscal_year_start_month: int = 1) -> str:
    """SVG of a defaults-only bar chart over `dates` — no authored time_unit."""
    rows = "\n".join(
        f"      - {{d: '{d.isoformat()}', v: {i + 1}}}" for i, d in enumerate(dates)
    )
    return render_board_to_svg(
        "title: Cadence\n"
        "queries:\n"
        "  q:\n"
        "    type: values\n"
        "    rows:\n"
        f"{rows}\n"
        "charts:\n"
        "  c:\n"
        "    query: q\n"
        "    type: bar\n"
        "    x: d\n"
        "    y: v\n"
        "    style:\n"
        "      axis_x:\n"
        f"        fiscal_year_start_month: {fiscal_year_start_month}\n"
        "rows:\n"
        "  - c\n"
    )


class TestDetectCadence:
    """Grain follows how far apart values sit, not where they land.

    Every case here is a real reporting cadence — a month-end close, a
    quarter-end close, a fixed billing day — that sits on no bucket start,
    so position alone reaches none of them.
    """

    def test_month_ends_detect_yearmonth(self) -> None:
        values = _month_ends(dt.date(2022, 1, 1), 36)
        assert detect_time_unit(values) == "yearmonth"

    def test_month_starts_still_detect_yearmonth(self) -> None:
        values = [d.replace(day=1) for d in _month_ends(dt.date(2022, 1, 1), 36)]
        assert detect_time_unit(values) == "yearmonth"

    def test_fixed_day_of_month_billing_detects_yearmonth(self) -> None:
        # Invoices dated the 15th — never day 1, never a month end.
        values = [d.replace(day=15) for d in _month_ends(dt.date(2022, 1, 1), 36)]
        assert detect_time_unit(values) == "yearmonth"

    def test_quarter_ends_detect_yearquarter(self) -> None:
        values = _quarter_ends(dt.date(2021, 3, 1), 16)
        assert [d.month for d in values[:4]] == [3, 6, 9, 12]
        assert detect_time_unit(values) == "yearquarter"

    def test_year_ends_detect_year(self) -> None:
        values = [dt.date(y, 12, 31) for y in range(2014, 2026)]
        assert detect_time_unit(values) == "year"

    def test_fiscal_year_ends_detect_year(self) -> None:
        # Jan 31 close — a calendar year apart, never on a calendar boundary.
        values = [dt.date(y, 1, 31) for y in range(2014, 2026)]
        assert detect_time_unit(values) == "year"

    def test_contiguous_daily_control_unchanged(self) -> None:
        values = [dt.date(2024, 1, 1) + dt.timedelta(days=i) for i in range(120)]
        assert detect_time_unit(values) == "yearmonthdate"

    def test_business_daily_control_unchanged(self) -> None:
        # Weekday-only dailies: 1-day gaps with a 3-day gap every weekend.
        values = [
            d
            for d in (dt.date(2024, 1, 1) + dt.timedelta(days=i) for i in range(90))
            if d.weekday() < 5
        ]
        assert detect_time_unit(values) == "yearmonthdate"


class TestDetectCadenceAmbiguousShapes:
    """Deliberate verdicts for shapes where no cadence is legible."""

    def test_two_point_series_has_no_cadence(self) -> None:
        # One interval is a measurement, not a rhythm — nothing repeats yet.
        assert detect_time_unit([dt.date(2024, 1, 31), dt.date(2024, 2, 29)]) == (
            "yearmonthdate"
        )

    def test_three_point_series_is_enough_cadence(self) -> None:
        # Two agreeing intervals is the minimum evidence of a repeat.
        values = [dt.date(2024, 1, 31), dt.date(2024, 2, 29), dt.date(2024, 3, 31)]
        assert detect_time_unit(values) == "yearmonth"

    def test_irregular_event_dates_stay_daily(self) -> None:
        # One value per month, but the intervals disagree — they land one per
        # month by accident, so month buckets would move each point.
        values = [dt.date(2024, 1, 15), dt.date(2024, 2, 7), dt.date(2024, 3, 22)]
        assert detect_time_unit(values) == "yearmonthdate"

    def test_dailies_with_stray_month_ends_stay_daily(self) -> None:
        # Mixed grains: the dominant cadence is daily and the median says so.
        values = [dt.date(2024, 1, 1) + dt.timedelta(days=i) for i in range(60)]
        values += [dt.date(2024, 4, 30), dt.date(2024, 5, 31), dt.date(2024, 6, 30)]
        assert detect_time_unit(values) == "yearmonthdate"

    def test_month_ends_with_stray_dailies_stay_daily(self) -> None:
        # The other mixing order: month buckets would collide the two dailies
        # into one bucket, so the coarse grain is refused outright.
        values = _month_ends(dt.date(2022, 1, 1), 12)
        values += [dt.date(2022, 6, 2), dt.date(2022, 6, 3)]
        assert detect_time_unit(values) == "yearmonthdate"

    def test_monthly_with_a_missing_month_still_yearmonth(self) -> None:
        # A skipped bucket is a whole number of periods — cadence survives.
        values = [d for d in _month_ends(dt.date(2022, 1, 1), 24) if d.month != 4]
        assert detect_time_unit(values) == "yearmonth"

    def test_fortnightly_same_weekday_stays_yearweek(self) -> None:
        # 14-day cadence keeps its weekday, so the weekly branch owns it and
        # its ≤14-day median gate still says yes. Unchanged.
        start = dt.date(2024, 1, 2)  # Tuesday
        values = [start + dt.timedelta(days=14 * i) for i in range(12)]
        assert detect_time_unit(values) == "yearweek"

    def test_retail_454_calendar_returns_none(self) -> None:
        # 4-5-4 periods are 28 or 35 days — always a whole number of weeks, so
        # every boundary shares a weekday and the weekly branch's median-gap
        # gate rejects it. No Gregorian bucket fits: a 5-week period would put
        # two boundaries in one calendar month.
        start = dt.date(2024, 2, 3)  # Saturday
        lengths = [28, 35, 28] * 4
        values = [start]
        for length in lengths:
            values.append(values[-1] + dt.timedelta(days=length))
        assert len({d.weekday() for d in values}) == 1
        assert detect_time_unit(values) is None

    def test_semiannual_detects_yearquarter(self) -> None:
        # Half-years have no bucket of their own; quarters divide them evenly
        # and never collide two points into one slot.
        values = [dt.date(y, m, 30) for y in range(2018, 2026) for m in (6, 12)]
        assert detect_time_unit(values) == "yearquarter"

    def test_bimonthly_detects_yearmonth(self) -> None:
        values = [
            dt.date(2023 + (i * 2) // 12, (i * 2) % 12 + 1, 20) for i in range(12)
        ]
        assert detect_time_unit(values) == "yearmonth"

    def test_month_end_beside_the_next_month_open_stays_daily(self) -> None:
        # A month-end close and the next month's day-1 open sit one day apart.
        # Scoring that one-day gap as "zero months" would read the pair as a
        # clean monthly rhythm and band two rows a month apart, so a gap
        # shorter than a period counts as one whole period off.
        values = [
            dt.date(2024, 1, 31),
            dt.date(2024, 2, 1),
            dt.date(2024, 3, 31),
            dt.date(2024, 4, 1),
        ]
        assert detect_time_unit(values) == "yearmonthdate"

    def test_subdaily_month_end_timestamps_still_none(self) -> None:
        values = [
            dt.datetime.combine(d, dt.time(23, 59, 59))
            for d in _month_ends(dt.date(2022, 1, 1), 24)
        ]
        assert detect_time_unit(values) is None


class TestDetectedGrainNeverCollidesRows:
    """A detected grain must keep every distinct value in its own bucket.

    Downstream, two rows landing in one gap-fill bucket is a hard error — the
    engine will not guess which value the bucket means. That error is safe to
    raise against an *authored* ``time_unit``, where an author asked for the
    grain. Detection has no such license: an ordinary board that happens to
    hold one off-cadence date (the day billing started, alongside month-end
    snapshots) would be detected as monthly and then refuse to render, with
    nobody having authored anything.
    """

    def test_off_cadence_extra_date_refuses_the_coarse_grain(self) -> None:
        # January holds two values — the 15th and the month end — so a month
        # bucket cannot represent this series without dropping one.
        values = [dt.date(2025, 1, 15), *_month_ends(dt.date(2025, 1, 1), 12)]
        assert detect_time_unit(values) == "yearmonthdate"

    def test_a_clean_month_end_series_still_detects_yearmonth(self) -> None:
        # The rule is a collision guard, not a cadence veto: one value per
        # month still names its grain.
        assert detect_time_unit(_month_ends(dt.date(2025, 1, 1), 12)) == "yearmonth"

    def test_a_quarterly_series_with_a_doubled_quarter_falls_to_yearmonth(self) -> None:
        # Quarter ends, plus one extra reading inside Q3 2024. Quarter buckets
        # collide, month buckets do not, so detection steps down one rung
        # rather than all the way to daily.
        values = _month_ends(dt.date(2023, 3, 1), 34)[::3]
        values.append(dt.date(2024, 8, 31))
        assert detect_time_unit(values) == "yearmonth"

    def test_billing_start_plus_month_ends_renders(self) -> None:
        """End to end: the shape above must produce a chart, not an error."""
        dates = [dt.date(2025, 1, 15), *_month_ends(dt.date(2025, 1, 1), 12)]
        assert "ERR-" not in _render_bar_of_dates(dates)

    def test_off_cadence_date_refuses_a_grain_the_fiscal_anchor_would_collide(
        self,
    ) -> None:
        # Sixteen quarter-end closes plus one mid-January reading. Calendar
        # quarters keep all seventeen apart, but a fiscal year opening in
        # February folds the December close and the January reading into one
        # quarter. Detection never sees the axis, so a quarter/year grain has
        # to survive every anchor before it can be named.
        values = [*_quarter_ends(dt.date(2021, 3, 1), 16), dt.date(2025, 1, 15)]
        assert detect_time_unit(values) == "yearmonth"

    def test_that_shape_renders_under_a_february_fiscal_year(self) -> None:
        """End to end: a non-default anchor must not produce an error card."""
        dates = [*_quarter_ends(dt.date(2021, 3, 1), 16), dt.date(2025, 1, 15)]
        assert "ERR-" not in _render_bar_of_dates(dates, fiscal_year_start_month=2)
