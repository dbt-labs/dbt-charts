"""Tests for fill: engine fills missing time buckets on ordinal time-unit x-axes.

All triggering conditions tested here produce ordinal bucketed-time charts.
The gap-fill helper complete_ordinal_time_series is tested directly in
test_detect_time_unit.py; these tests verify integration through profile /
render_standard_vega_spec.
"""

from __future__ import annotations

import datetime as dt

import pytest

from dbt_charts.core.compile.config import get_theme_style
from dbt_charts.core.compile.models.style.authored import (
    AxisXStylePatch,
)
from dbt_charts.core.compile.resolve.style.board import resolve_style_and_context
from dbt_charts.core.diagnostics.chart_data import ChartDataError
from dbt_charts.core.diagnostics.codes_render import ERR_GAP_FILL_BUCKET_COLLISION
from dbt_charts.core.render.chart.time_unit_detect import complete_ordinal_time_series

_BOARD_RS, _BOARD_CTX = resolve_style_and_context(get_theme_style())

# ── Fixtures ──────────────────────────────────────────────────────────────────


def _jan_feb_may_data() -> list[dict]:
    """Single series, three months with Mar/Apr missing."""
    return [
        {"month": "2024-01-01", "revenue": 100},
        {"month": "2024-02-01", "revenue": 200},
        {"month": "2024-05-01", "revenue": 500},
    ]


def _stacked_weekly_data() -> list[dict]:
    """Two sources; week 2 completely missing for source B."""
    return [
        {"week": "2024-01-01", "source": "A", "signups": 10},
        {"week": "2024-01-01", "source": "B", "signups": 5},
        {"week": "2024-01-08", "source": "A", "signups": 20},
        # 2024-01-08 / source B intentionally absent
        {"week": "2024-01-15", "source": "A", "signups": 15},
        {"week": "2024-01-15", "source": "B", "signups": 8},
    ]


# ── Unit tests for complete_ordinal_time_series ────────────────────────────────


class TestCompleteOrdinalTimeSeries:
    """Direct tests of the gap-fill helper function."""

    def test_single_series_yearmonth_fills_missing(self) -> None:
        data = _jan_feb_may_data()
        result = complete_ordinal_time_series(
            data, "month", "yearmonth", [], "null", fiscal_year_start_month=1
        )
        months = [r["month"] for r in result]
        assert "2024-03-01" in months, f"Mar missing from {months}"
        assert "2024-04-01" in months, f"Apr missing from {months}"

    def test_single_series_yearmonth_null_synthetic_measures(self) -> None:
        data = _jan_feb_may_data()
        result = complete_ordinal_time_series(
            data, "month", "yearmonth", [], "null", fiscal_year_start_month=1
        )
        mar_row = next(r for r in result if r["month"] == "2024-03-01")
        assert mar_row["revenue"] is None, (
            f"Expected null fill, got {mar_row['revenue']}"
        )

    def test_single_series_yearmonth_zero_fill_for_zero(self) -> None:
        data = _jan_feb_may_data()
        result = complete_ordinal_time_series(
            data, "month", "yearmonth", [], "zero", fiscal_year_start_month=1
        )
        mar_row = next(r for r in result if r["month"] == "2024-03-01")
        assert mar_row["revenue"] == 0, f"Expected zero fill, got {mar_row['revenue']}"

    def test_existing_rows_preserved(self) -> None:
        data = _jan_feb_may_data()
        result = complete_ordinal_time_series(
            data, "month", "yearmonth", [], "null", fiscal_year_start_month=1
        )
        jan = next(r for r in result if r["month"] == "2024-01-01")
        assert jan["revenue"] == 100

    def test_empty_dataset_returns_unchanged(self) -> None:
        result = complete_ordinal_time_series(
            [], "month", "yearmonth", [], "null", fiscal_year_start_month=1
        )
        assert result == []

    def test_multi_series_cross_join(self) -> None:
        data = _stacked_weekly_data()
        result = complete_ordinal_time_series(
            data, "week", "yearweek", ["source"], "null", fiscal_year_start_month=1
        )
        weeks = sorted({r["week"] for r in result})
        sources = sorted({r["source"] for r in result})
        # Every (week, source) combination must be present
        for w in weeks:
            for s in sources:
                rows = [r for r in result if r["week"] == w and r["source"] == s]
                assert len(rows) == 1, (
                    f"Expected exactly 1 row for ({w}, {s}), got {rows}"
                )

    def test_multi_series_missing_row_has_null_measure(self) -> None:
        data = _stacked_weekly_data()
        result = complete_ordinal_time_series(
            data, "week", "yearweek", ["source"], "null", fiscal_year_start_month=1
        )
        # 2024-01-08 / source B was absent in source data
        missing = next(
            (r for r in result if r["week"] == "2024-01-08" and r["source"] == "B"),
            None,
        )
        assert missing is not None, "Synthesized row for (week2, B) not found"
        assert missing["signups"] is None

    def test_determinism_order(self) -> None:
        """Synthesized rows ordered (bucket asc, dim asc)."""
        data = _stacked_weekly_data()
        r1 = complete_ordinal_time_series(
            data, "week", "yearweek", ["source"], "null", fiscal_year_start_month=1
        )
        r2 = complete_ordinal_time_series(
            data, "week", "yearweek", ["source"], "null", fiscal_year_start_month=1
        )
        assert [row["week"] for row in r1] == [row["week"] for row in r2]
        assert [row.get("source") for row in r1] == [row.get("source") for row in r2]

    def test_categories_not_invented(self) -> None:
        """Only dim values appearing in the data are cross-joined."""
        data = [
            {"week": "2024-01-01", "source": "A", "signups": 10},
            {"week": "2024-01-15", "source": "A", "signups": 15},
        ]
        result = complete_ordinal_time_series(
            data, "week", "yearweek", ["source"], "null", fiscal_year_start_month=1
        )
        sources = {r.get("source") for r in result}
        assert sources == {"A"}, f"Unexpected sources: {sources}"

    def test_yearquarter_step(self) -> None:
        data = [
            {"quarter": "2024-01-01", "revenue": 100},
            {"quarter": "2024-10-01", "revenue": 400},
        ]
        result = complete_ordinal_time_series(
            data, "quarter", "yearquarter", [], "null", fiscal_year_start_month=1
        )
        quarters = [r["quarter"] for r in result]
        assert "2024-04-01" in quarters, "Q2 missing"
        assert "2024-07-01" in quarters, "Q3 missing"

    def test_year_step(self) -> None:
        data = [
            {"yr": "2022-01-01", "revenue": 100},
            {"yr": "2024-01-01", "revenue": 300},
        ]
        result = complete_ordinal_time_series(
            data, "yr", "year", [], "null", fiscal_year_start_month=1
        )
        years = [r["yr"] for r in result]
        assert "2023-01-01" in years, "2023 missing"

    def test_yearmonthdate_step(self) -> None:
        data = [
            {"day": "2024-01-01", "revenue": 1},
            {"day": "2024-01-03", "revenue": 3},
        ]
        result = complete_ordinal_time_series(
            data, "day", "yearmonthdate", [], "null", fiscal_year_start_month=1
        )
        days = [r["day"] for r in result]
        assert "2024-01-02" in days, "2024-01-02 missing"


class TestGapFillBucketCollision:
    """Two rows differing only by time-of-day collapse to the same
    `_ordinal_bucket_key` and collide in `complete_ordinal_time_series`'s
    `existing` lookup. `validate_preaggregated_data` cannot catch this: it
    runs before gap-fill, on the still-distinct raw x values. A collision
    cannot happen on legitimately grain-aligned data, so raising here costs
    nothing on valid input.
    """

    def test_datetime_objects_same_day_raise(self) -> None:
        rows = [
            {"day": dt.datetime(2024, 1, 1, 9, 0, 0), "revenue": 100},
            {"day": dt.datetime(2024, 1, 1, 15, 0, 0), "revenue": 500},
            {"day": dt.datetime(2024, 1, 2, 9, 0, 0), "revenue": 200},
        ]
        with pytest.raises(ChartDataError) as exc_info:
            complete_ordinal_time_series(
                rows, "day", "yearmonthdate", [], "null", fiscal_year_start_month=1
            )
        assert exc_info.value.code is ERR_GAP_FILL_BUCKET_COLLISION, (
            f"Expected ERR-GAP-FILL-BUCKET-COLLISION but got {exc_info.value.code!r}"
        )

    def test_iso_datetime_strings_same_day_raise(self) -> None:
        rows = [
            {"day": "2024-01-01T09:00:00", "revenue": 100},
            {"day": "2024-01-01T15:00:00", "revenue": 500},
            {"day": "2024-01-02T09:00:00", "revenue": 200},
        ]
        with pytest.raises(ChartDataError) as exc_info:
            complete_ordinal_time_series(
                rows, "day", "yearmonthdate", [], "null", fiscal_year_start_month=1
            )
        assert exc_info.value.code is ERR_GAP_FILL_BUCKET_COLLISION, (
            f"Expected ERR-GAP-FILL-BUCKET-COLLISION but got {exc_info.value.code!r}"
        )

    def test_dim_field_collision_names_the_dim_in_message(self) -> None:
        """`dim_fields = [color_field]` (`_channels.py`) is the production-common
        shape — two rows sharing both bucket and dim value must still raise, and
        the message must name the colliding dim, not just the bucket.
        """
        rows = [
            {
                "day": dt.datetime(2024, 1, 1, 9, 0, 0),
                "region": "west",
                "revenue": 100,
            },
            {
                "day": dt.datetime(2024, 1, 1, 15, 0, 0),
                "region": "west",
                "revenue": 500,
            },
            {"day": dt.datetime(2024, 1, 2, 9, 0, 0), "region": "west", "revenue": 200},
        ]
        with pytest.raises(ChartDataError) as exc_info:
            complete_ordinal_time_series(
                rows,
                "day",
                "yearmonthdate",
                ["region"],
                "null",
                fiscal_year_start_month=1,
            )
        assert exc_info.value.code is ERR_GAP_FILL_BUCKET_COLLISION, (
            f"Expected ERR-GAP-FILL-BUCKET-COLLISION but got {exc_info.value.code!r}"
        )
        assert "region='west'" in str(exc_info.value), (
            f"Expected the colliding dim in the message, got: {exc_info.value}"
        )


# ── Integration tests through render pipeline ──────────────────────────────────


class TestGapFillIntegration:
    """Gap-fill wires into render_resolved_chart; verify via rendered VL spec data."""

    def test_complete_ordinal_time_series_normalizes_existing_date_rows(self) -> None:
        """Existing date-object rows use the same ISO key as synthesized rows."""
        data = [
            {"month": dt.date(2024, 1, 1), "series": "A", "revenue": 100},
            {"month": "2024-03-01", "series": "A", "revenue": 300},
        ]

        filled = complete_ordinal_time_series(
            data, "month", "yearmonth", ["series"], "zero", fiscal_year_start_month=1
        )

        assert [row["month"] for row in filled] == [
            "2024-01-01",
            "2024-02-01",
            "2024-03-01",
        ]
        assert [row["revenue"] for row in filled] == [100, 0, 300]

    def test_complete_ordinal_time_series_normalizes_existing_datetime_rows(
        self,
    ) -> None:
        """Existing datetime rows use calendar-bucket keys for gap-fill lookup."""
        data = [
            {"month": dt.datetime(2024, 1, 1), "series": "A", "revenue": 100},
            {"month": "2024-03-01", "series": "A", "revenue": 300},
        ]

        filled = complete_ordinal_time_series(
            data, "month", "yearmonth", ["series"], "zero", fiscal_year_start_month=1
        )

        assert [row["month"] for row in filled] == [
            "2024-01-01",
            "2024-02-01",
            "2024-03-01",
        ]
        assert [row["revenue"] for row in filled] == [100, 0, 300]


# ── Regression: categories-not-in-data caveat ─────────────────────────────────


class TestCategoryNotInDataCaveat:
    """Engine only cross-joins over categories that appear in the data window."""

    def test_synthetic_rows_use_only_observed_categories(self) -> None:
        data = [
            {"week": "2024-01-01", "source": "organic", "signups": 10},
            {"week": "2024-01-15", "source": "organic", "signups": 15},
            # "paid" source has zero rows in the data window
        ]
        result = complete_ordinal_time_series(
            data, "week", "yearweek", ["source"], "null", fiscal_year_start_month=1
        )
        sources = {r.get("source") for r in result}
        assert "paid" not in sources, "Engine must not invent unseen categories"
        assert sources == {"organic"}


# ── Tests for fill: interpolate-* ─────────────────────────────────────


class TestInterpolateGapHandling:
    """complete_ordinal_time_series with fill interpolate-* modes."""

    def test_single_series_interior_gap_linearly_interpolated(self) -> None:
        """Jan=100, Feb=200, May=500 → Mar and Apr linearly interpolated."""
        data = _jan_feb_may_data()
        result = complete_ordinal_time_series(
            data, "month", "yearmonth", [], "linear", fiscal_year_start_month=1
        )
        # Mar is bucket index 2 out of 5 (0-based: Jan=0, Feb=1, Mar=2, Apr=3, May=4)
        # Feb=200, May=500; gap spans Mar and Apr (2 synthetic buckets).
        # Linear interpolation between bucket index 1 (Feb, 200) and bucket index 4 (May, 500):
        # step = (500 - 200) / (4 - 1) = 100
        # Mar = 200 + 1*100 = 300
        # Apr = 200 + 2*100 = 400
        rows_by_month = {r["month"]: r for r in result}
        assert "2024-03-01" in rows_by_month, "Mar should be synthesized"
        assert "2024-04-01" in rows_by_month, "Apr should be synthesized"
        assert rows_by_month["2024-03-01"]["revenue"] == pytest.approx(300.0)
        assert rows_by_month["2024-04-01"]["revenue"] == pytest.approx(400.0)

    def test_interpolate_preserves_existing_rows(self) -> None:
        """Original data rows are unchanged by interpolation."""
        data = _jan_feb_may_data()
        result = complete_ordinal_time_series(
            data, "month", "yearmonth", [], "linear", fiscal_year_start_month=1
        )
        rows_by_month = {r["month"]: r for r in result}
        assert rows_by_month["2024-01-01"]["revenue"] == 100
        assert rows_by_month["2024-02-01"]["revenue"] == 200
        assert rows_by_month["2024-05-01"]["revenue"] == 500

    def test_leading_trailing_synthetic_buckets_stay_null(self) -> None:
        """Multi-series: synth rows before a series' first real point stay null.

        Series A spans Jan–Apr; series B only starts in Mar. The engine
        cross-joins the full [Jan, Apr] window with both series, so B gets
        Jan and Feb synthesized. Those lack a left anchor, so 'linear' must
        leave them null. Mar–Apr interior gap would be interpolated, but here
        B has real data in Mar and Apr so no interior null to fill.
        """
        data = [
            {"month": "2024-01-01", "series": "A", "revenue": 100},
            {"month": "2024-02-01", "series": "A", "revenue": 200},
            {"month": "2024-03-01", "series": "A", "revenue": 300},
            {"month": "2024-04-01", "series": "A", "revenue": 400},
            # B only starts in March — Jan and Feb will be synthetic leading rows
            {"month": "2024-03-01", "series": "B", "revenue": 30},
            {"month": "2024-04-01", "series": "B", "revenue": 40},
        ]
        result = complete_ordinal_time_series(
            data, "month", "yearmonth", ["series"], "linear", fiscal_year_start_month=1
        )
        rows = {(r["month"], r["series"]): r for r in result}
        # Leading synthetic rows for B (no left anchor) must stay null
        assert rows[("2024-01-01", "B")]["revenue"] is None
        assert rows[("2024-02-01", "B")]["revenue"] is None
        # Real B rows are preserved
        assert rows[("2024-03-01", "B")]["revenue"] == 30
        assert rows[("2024-04-01", "B")]["revenue"] == 40

    def test_multi_series_interpolated_independently(self) -> None:
        """Each series group is interpolated independently (not across groups)."""
        # Two series: Core (100, ?, 400) and Growth (200, ?, 500)
        # The ? is Feb = the middle month. Core: Jan=100, Mar=400; Growth: Jan=200, Mar=500.
        data = [
            {"month": "2024-01-01", "segment": "Core", "revenue": 100},
            {"month": "2024-03-01", "segment": "Core", "revenue": 400},
            {"month": "2024-01-01", "segment": "Growth", "revenue": 200},
            {"month": "2024-03-01", "segment": "Growth", "revenue": 500},
        ]
        result = complete_ordinal_time_series(
            data, "month", "yearmonth", ["segment"], "linear", fiscal_year_start_month=1
        )
        rows = {(r["month"], r["segment"]): r for r in result}
        # Core: Feb should be midpoint(100, 400) = 250
        core_feb = rows[("2024-02-01", "Core")]
        assert core_feb["revenue"] == pytest.approx(250.0)
        # Growth: Feb should be midpoint(200, 500) = 350
        growth_feb = rows[("2024-02-01", "Growth")]
        assert growth_feb["revenue"] == pytest.approx(350.0)

    def test_regression_zero_still_zero(self) -> None:
        """Regression: 'zero' mode still fills with 0 after interpolate is added."""
        data = _jan_feb_may_data()
        result = complete_ordinal_time_series(
            data, "month", "yearmonth", [], "zero", fiscal_year_start_month=1
        )
        rows_by_month = {r["month"]: r for r in result}
        assert rows_by_month["2024-03-01"]["revenue"] == 0
        assert rows_by_month["2024-04-01"]["revenue"] == 0

    def test_step_after_forward_fill(self) -> None:
        """step-after (Looker): hold last observed value across interior gaps."""
        data = _jan_feb_may_data()
        result = complete_ordinal_time_series(
            data, "month", "yearmonth", [], "step-after", fiscal_year_start_month=1
        )
        rows_by_month = {r["month"]: r for r in result}
        assert rows_by_month["2024-03-01"]["revenue"] == 200
        assert rows_by_month["2024-04-01"]["revenue"] == 200

    def test_step_before_uses_next_observed(self) -> None:
        """step-before (Looker): interior gaps take the upcoming observed value."""
        data = _jan_feb_may_data()
        result = complete_ordinal_time_series(
            data, "month", "yearmonth", [], "step-before", fiscal_year_start_month=1
        )
        rows_by_month = {r["month"]: r for r in result}
        assert rows_by_month["2024-03-01"]["revenue"] == 500
        assert rows_by_month["2024-04-01"]["revenue"] == 500

    def test_step_after_leading_synthetic_stays_null(self) -> None:
        """step-after does not back-fill from a future anchor before the first observation."""
        data = [
            {"month": "2024-01-01", "series": "A", "revenue": 100},
            {"month": "2024-02-01", "series": "A", "revenue": 200},
            {"month": "2024-03-01", "series": "B", "revenue": 300},
            {"month": "2024-04-01", "series": "B", "revenue": 400},
        ]
        result = complete_ordinal_time_series(
            data,
            "month",
            "yearmonth",
            ["series"],
            "step-after",
            fiscal_year_start_month=1,
        )
        rows = {(r["month"], r["series"]): r for r in result}
        assert rows[("2024-01-01", "B")]["revenue"] is None
        assert rows[("2024-02-01", "B")]["revenue"] is None

    def test_step_before_trailing_synthetic_stays_null(self) -> None:
        """step-before does not forward-fill from a past anchor after the last observation."""
        data = [
            {"month": "2024-01-01", "series": "A", "revenue": 100},
            {"month": "2024-02-01", "series": "A", "revenue": 200},
            {"month": "2024-03-01", "series": "B", "revenue": 300},
            {"month": "2024-04-01", "series": "A", "revenue": 400},
        ]
        result = complete_ordinal_time_series(
            data,
            "month",
            "yearmonth",
            ["series"],
            "step-before",
            fiscal_year_start_month=1,
        )
        rows = {(r["month"], r["series"]): r for r in result}
        assert rows[("2024-04-01", "B")]["revenue"] is None

    def test_step_center_switches_at_midpoint(self) -> None:
        """step-center (Looker): switch to the right anchor at the gap midpoint."""
        data = _jan_feb_may_data()
        result = complete_ordinal_time_series(
            data, "month", "yearmonth", [], "step-center", fiscal_year_start_month=1
        )
        rows_by_month = {r["month"]: r for r in result}
        assert rows_by_month["2024-03-01"]["revenue"] == 200
        assert rows_by_month["2024-04-01"]["revenue"] == 500

    def test_curve_smoothstep_between_anchors(self) -> None:
        """curve: smoothstep easing between left and right observed values."""
        data = _jan_feb_may_data()
        result = complete_ordinal_time_series(
            data, "month", "yearmonth", [], "curve", fiscal_year_start_month=1
        )
        rows_by_month = {r["month"]: r for r in result}
        assert rows_by_month["2024-03-01"]["revenue"] == pytest.approx(
            200 + 300 * 7 / 27
        )
        assert rows_by_month["2024-04-01"]["revenue"] == pytest.approx(
            200 + 300 * 20 / 27
        )

    def test_schema_fill_accepts_fill_modes(self) -> None:
        """AxisXStylePatch accepts all gap-fill fill values."""
        for mode in (
            "linear",
            "step-after",
            "step-before",
            "step-center",
            "curve",
        ):
            patch = AxisXStylePatch(fill=mode)
            assert patch.fill == mode

    def test_schema_fill_rejects_unknown(self) -> None:
        """AxisXStylePatch rejects an unknown fill value."""
        from pydantic import ValidationError

        with pytest.raises(ValidationError):
            AxisXStylePatch(fill="sprinkles")
