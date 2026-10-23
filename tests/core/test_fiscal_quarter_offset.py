"""Tests for calendar-quarter bucket anchoring and the fiscal-quarter offset.

Covers the two bugs/features from the fiscal-quarter-bucketing task:

1. ``complete_ordinal_time_series``/``_enumerate_buckets`` used to walk forward
   from the raw minimum date in the data instead of a calendar-period
   boundary — data starting mid-quarter (e.g. March) mis-anchored every
   downstream "quarter" bucket. Fixed by flooring the min date to its
   enclosing bucket before enumerating forward.
2. A new ``axis_x.fiscal_year_start_month`` axis config (default 1 = the
   calendar convention) threads through bucket enumeration, label-opener
   detection, the VL labelExpr, and label-overlap width measurement — and
   forces the ordinal/pre-bucketed path (instead of VL's native ``timeUnit``)
   on temporal-scale charts whenever a non-default offset is configured, since
   VL's ``timeUnit`` transform has no fiscal-offset concept and would
   silently re-bucket to calendar-aligned boundaries.
"""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from dbt_charts.core.compile.models.style.theme.axis import AxisXStyle
from dbt_charts.core.render.chart.time_unit_detect import complete_ordinal_time_series


class TestFiscalYearStartMonthValidation:
    @pytest.mark.parametrize("value", [0, 13, -1, 100])
    def test_out_of_range_value_raises(self, value: int) -> None:
        with pytest.raises(ValidationError, match="fiscal_year_start_month"):
            AxisXStyle(fill="null", fiscal_year_start_month=value)

    @pytest.mark.parametrize("value", [1, 4, 12])
    def test_in_range_value_accepted(self, value: int) -> None:
        axis = AxisXStyle(fill="null", fiscal_year_start_month=value)
        assert axis.fiscal_year_start_month == value


# ── Bucket anchoring: complete_ordinal_time_series / _enumerate_buckets ────────


class TestBucketAnchoring:
    def test_mid_quarter_start_anchors_to_true_calendar_quarters(self) -> None:
        """Data starting March 1 (mid Q1) must still enumerate Jan/Apr/Jul."""
        data = [
            {"quarter": "2025-03-01", "revenue": 10},
            {"quarter": "2025-08-01", "revenue": 20},
        ]
        result = complete_ordinal_time_series(
            data, "quarter", "yearquarter", [], "null", fiscal_year_start_month=1
        )
        quarters = [r["quarter"] for r in result]
        assert quarters == ["2025-01-01", "2025-04-01", "2025-07-01"]

    def test_non_default_fiscal_offset_shifts_quarter_anchors(self) -> None:
        """fiscal_year_start_month=4 anchors quarters to Apr/Jul/Oct/Jan."""
        data = [
            {"quarter": "2025-05-01", "revenue": 10},
            {"quarter": "2025-11-01", "revenue": 20},
        ]
        result = complete_ordinal_time_series(
            data, "quarter", "yearquarter", [], "null", fiscal_year_start_month=4
        )
        quarters = [r["quarter"] for r in result]
        assert quarters[0] == "2025-04-01"
        assert quarters[-1] == "2025-10-01"
        assert all(dt[5:7] in ("04", "07", "10", "01") for dt in quarters)

    def test_year_grain_anchors_to_fiscal_start_month(self) -> None:
        data = [{"yr": "2024-06-01", "revenue": 1}]
        result = complete_ordinal_time_series(
            data, "yr", "year", [], "null", fiscal_year_start_month=4
        )
        assert [r["yr"] for r in result] == ["2024-04-01"]

    def test_yearmonth_grain_unaffected_by_fiscal_offset(self) -> None:
        """A month is a month regardless of fiscal_year_start_month."""
        data = [{"month": "2024-03-01", "revenue": 1}]
        default = complete_ordinal_time_series(
            data, "month", "yearmonth", [], "null", fiscal_year_start_month=1
        )
        offset = complete_ordinal_time_series(
            data, "month", "yearmonth", [], "null", fiscal_year_start_month=4
        )
        assert default == offset == [{"month": "2024-03-01", "revenue": 1}]


# ── Rendering regression: dropped year label on mid-quarter-start data ─────────


# ── Fiscal offset must force ordinal on temporal-scale charts too ─────────────


# ── label.time_unit coarser than the encoding grain: overlap must use labeled count ──
#
# Regression: monthly-grained data (17 buckets) with a quarterly label cadence
# (axis_x.label.time_unit: yearquarter) mostly renders BLANK tick text — only
# the ~6 quarter-opening ticks show labels (see opens_label_period).
# The overlap resolver must measure clearance against the VISIBLE label count
# (~6) rather than the raw bucket count (17), so a coarse cadence that fits
# its ~6 labeled ticks is not mis-flagged as overcrowded.
