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

from typing import Any

import pytest
from pydantic import TypeAdapter, ValidationError

from dbt_charts.core.compile.config import get_theme_style, reset_config
from dbt_charts.core.compile.models.chart.normalized import Chart
from dbt_charts.core.compile.models.style.theme.axis import AxisXStyle
from dbt_charts.core.compile.resolve.style.board import resolve_style_and_context
from dbt_charts.core.render.chart.time_unit_detect import complete_ordinal_time_series
from dbt_charts.core.render.chart.vega_lite import generate_vega_lite_spec


@pytest.fixture(autouse=True)
def _reset():
    reset_config()
    yield
    reset_config()


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


def _step_band_board():
    compiled = get_theme_style("clarity")
    fam_style = compiled.charts.line
    base_mark = fam_style.marks.line
    new_mark = base_mark.model_copy(update={"halo_multiplier": 0.0, "curve": "step"})
    new_marks = fam_style.marks.model_copy(update={"line": new_mark})
    new_fam = fam_style.model_copy(update={"marks": new_marks})
    charts = compiled.charts.model_copy(update={"line": new_fam})
    return resolve_style_and_context(compiled.model_copy(update={"charts": charts}))


# Data starting March 1 2025 (mid Q1) spanning into 2026 — the year-boundary
# bucket (Q1 2026) is the one whose second label line used to silently drop
# because the mis-anchored bucket landed on month=March (month != 1), not
# month=January.
_MID_QUARTER_DATA = [
    {"month": "2025-03-01", "revenue": 10},
    {"month": "2025-06-01", "revenue": 20},
    {"month": "2025-09-01", "revenue": 15},
    {"month": "2025-12-01", "revenue": 25},
    {"month": "2026-03-01", "revenue": 30},
]


def _step_band_chart(fiscal_year_start_month: int | None = None) -> Chart:
    axis_x: dict[str, Any] = {"time_unit": "yearquarter"}
    if fiscal_year_start_month is not None:
        axis_x["fiscal_year_start_month"] = fiscal_year_start_month
    return TypeAdapter(Chart).validate_python(
        {
            "id": "repro",
            "type": "line",
            "x": "month",
            "y": "revenue",
            "query": {"query_type": "sql", "sql": "SELECT 1", "source": "test"},
            "query_name": "q",
            "style": {"axis_x": axis_x},
        }
    )


class TestYearBoundaryLabelRegression:
    def test_mid_quarter_data_still_shows_year_boundary_label(self) -> None:
        """Regression for the reported bug: step-band + yearquarter + data
        starting mid-quarter must still open a Jan-anchored bucket, and that
        bucket's labelExpr gate must recognize it as a year boundary (so the
        second "20XX" label line is not silently dropped)."""
        rs, ctx = _step_band_board()
        chart = _step_band_chart()
        spec = generate_vega_lite_spec(
            chart, _MID_QUARTER_DATA, board_style=rs, chart_style_context=ctx
        )
        x = spec["encoding"]["x"]
        assert x["type"] == "ordinal"
        # True calendar-quarter anchors, not the mis-anchored Mar/Jun/Sep/Dec.
        assert x["axis"]["values"] == [
            "2025-01-01",
            "2025-04-01",
            "2025-07-01",
            "2025-10-01",
            "2026-01-01",
        ]
        label_expr = x["axis"]["labelExpr"]
        # The year-boundary gate (month === 0, i.e. January) must be present
        # and must be satisfiable by the 2026-01-01 bucket — this is exactly
        # the check that silently failed when the bucket landed on March.
        assert "utcmonth(toDate(datum.value)) === 0" in label_expr

    def test_fiscal_offset_shifts_year_boundary_gate_and_quarter_numbers(self) -> None:
        rs, ctx = _step_band_board()
        chart = _step_band_chart(fiscal_year_start_month=4)
        spec = generate_vega_lite_spec(
            chart, _MID_QUARTER_DATA, board_style=rs, chart_style_context=ctx
        )
        x = spec["encoding"]["x"]
        # Bucket anchors are unaffected here (March floors into the Jan-Mar
        # fiscal quarter either way) — the offset's effect is on the
        # labelExpr's year-boundary gate and Q-numbering, not these dates.
        assert x["axis"]["values"] == [
            "2025-01-01",
            "2025-04-01",
            "2025-07-01",
            "2025-10-01",
            "2026-01-01",
        ]
        label_expr = x["axis"]["labelExpr"]
        # Fiscal month 0 (year open) is April, expressed as a shifted-mod
        # expression rather than the bare calendar-month check.
        assert "utcmonth(toDate(datum.value)) - 3" in label_expr
        assert "utcmonth(toDate(datum.value)) === 0" not in label_expr


# ── Fiscal offset must force ordinal on temporal-scale charts too ─────────────


class TestFiscalOffsetForcesOrdinalOnTemporalScale:
    def test_plain_curve_with_fiscal_offset_does_not_emit_vl_timeunit(self) -> None:
        """Line/area normally resolve a bucketed yearquarter grain to a
        continuous temporal scale with VL's native timeUnit. VL's timeUnit
        has no fiscal-offset concept and would silently re-bucket to true
        calendar quarters, discarding the authored offset. A non-default
        offset must force the same ordinal/pre-bucketed path step-band uses,
        regardless of curve."""
        rs, ctx = resolve_style_and_context(get_theme_style("clarity"))
        chart = TypeAdapter(Chart).validate_python(
            {
                "id": "plain",
                "type": "line",
                "x": "month",
                "y": "revenue",
                "query": {"query_type": "sql", "sql": "SELECT 1", "source": "test"},
                "query_name": "q",
                "style": {
                    "axis_x": {
                        "time_unit": "yearquarter",
                        "fiscal_year_start_month": 4,
                    }
                },
            }
        )
        spec = generate_vega_lite_spec(
            chart, _MID_QUARTER_DATA, board_style=rs, chart_style_context=ctx
        )
        x = spec["encoding"]["x"]
        assert x["type"] == "ordinal"
        assert "timeUnit" not in x
        # Fiscally-aware bucketing took effect via the forced ordinal path —
        # same anchors the step-band regression test above asserts.
        assert x["axis"]["values"] == [
            "2025-01-01",
            "2025-04-01",
            "2025-07-01",
            "2025-10-01",
            "2026-01-01",
        ]

    def test_default_offset_plain_curve_still_resolves_temporal(self) -> None:
        """Sanity check: at the default offset, temporal-scale behavior for
        line/area is unchanged — this would fail if the override fired
        unconditionally instead of gating on a non-default offset."""
        rs, ctx = resolve_style_and_context(get_theme_style("clarity"))
        chart = TypeAdapter(Chart).validate_python(
            {
                "id": "plain_default",
                "type": "line",
                "x": "month",
                "y": "revenue",
                "query": {"query_type": "sql", "sql": "SELECT 1", "source": "test"},
                "query_name": "q",
                "style": {"axis_x": {"time_unit": "yearquarter"}},
            }
        )
        spec = generate_vega_lite_spec(
            chart, _MID_QUARTER_DATA, board_style=rs, chart_style_context=ctx
        )
        x = spec["encoding"]["x"]
        assert x["type"] == "temporal"
        assert x["timeUnit"] == "utcyearquarter"


# ── label.time_unit coarser than the encoding grain: overlap must use labeled count ──
#
# Regression: monthly-grained data (17 buckets) with a quarterly label cadence
# (axis_x.label.time_unit: yearquarter) mostly renders BLANK tick text — only
# the ~6 quarter-opening ticks show labels (see opens_label_period).
# The overlap resolver must measure clearance against the VISIBLE label count
# (~6) rather than the raw bucket count (17), so a coarse cadence that fits
# its ~6 labeled ticks is not mis-flagged as overcrowded.


def _area_chart(axis_x: dict[str, Any]) -> Chart:
    return TypeAdapter(Chart).validate_python(
        {
            "id": "cumulative_area",
            "type": "area",
            "x": "month",
            "y": "cumulative",
            "query": {"query_type": "sql", "sql": "SELECT 1", "source": "test"},
            "query_name": "q",
            "style": {"axis_x": axis_x},
        }
    )


def _monthly_data(n_months: int = 17) -> list[dict[str, Any]]:
    import datetime as dt

    data = []
    d = dt.date(2024, 3, 1)
    for i in range(n_months):
        data.append({"month": d.isoformat(), "cumulative": 1000 + i * 500})
        month = d.month + 1
        year = d.year + (1 if month > 12 else 0)
        month = 1 if month > 12 else month
        d = dt.date(year, month, 1)
    return data


def _quarterly_data() -> list[dict[str, Any]]:
    """The quarter buckets ``_monthly_data()``'s 17-month span covers."""
    import datetime as dt

    return [
        {"month": dt.date(year, month, 1).isoformat(), "cumulative": 1000 + i * 500}
        for i, (year, month) in enumerate(
            [
                (2024, 1),
                (2024, 4),
                (2024, 7),
                (2024, 10),
                (2025, 1),
                (2025, 4),
                (2025, 7),
            ]
        )
    ]


class TestCoarserLabelCadenceOverlapWidth:
    def test_label_time_unit_matches_equivalent_encoding_time_unit_sizing(
        self,
    ) -> None:
        """label.time_unit: yearquarter over monthly data must look the same
        as encoding time_unit: yearquarter — same font size, same angle —
        because both display the same visible quarterly labels.

        The two sides are fed differently on purpose: a quarterly *encoding*
        owes its rows the quarterly grain, so monthly rows under it are a
        bucket collision (two months in one quarter), not a drop to the
        quarter openers. A quarterly *label cadence* leaves the monthly
        encoding alone and only thins the ticks.
        """
        rs, ctx = resolve_style_and_context(get_theme_style("clarity"))
        data = _monthly_data()

        encoding_chart = _area_chart({"time_unit": "yearquarter"})
        encoding_spec = generate_vega_lite_spec(
            encoding_chart,
            _quarterly_data(),
            board_style=rs,
            chart_style_context=ctx,
        )
        encoding_axis = encoding_spec["encoding"]["x"]["axis"]

        label_chart = _area_chart({"labels": {"time_unit": "yearquarter"}})
        label_spec = generate_vega_lite_spec(
            label_chart, data, board_style=rs, chart_style_context=ctx
        )
        label_axis = label_spec["encoding"]["x"]["axis"]

        assert label_axis["labelFontSize"] == encoding_axis["labelFontSize"]
        assert label_axis["labelAngle"] == encoding_axis["labelAngle"] == 0.0


# ── Authored label cadence also sets the default tick cadence ───────────────
class TestTemporalPathUsesAuthoredLabelTicks:
    def test_tick_values_follow_fiscal_quarters(self) -> None:
        rs, ctx = resolve_style_and_context(get_theme_style("clarity"))
        data = _monthly_data(n_months=17)  # starts 2024-03-01
        chart = _area_chart(
            {"labels": {"time_unit": "yearquarter"}, "fiscal_year_start_month": 3}
        )
        spec = generate_vega_lite_spec(
            chart, data, board_style=rs, chart_style_context=ctx
        )
        x = spec["encoding"]["x"]
        assert x["type"] == "temporal"
        assert x["axis"]["values"] == [
            "2024-03-01",
            "2024-06-01",
            "2024-09-01",
            "2024-12-01",
            "2025-03-01",
            "2025-06-01",
        ]
        assert "tickCount" not in x["axis"]

    def test_narrow_width_still_shows_every_quarter_label(self) -> None:
        """The real-world trigger: at a realistic (narrow) chart width, VL's
        own tick algorithm is bypassed by the authored fiscal-quarter values,
        so every requested quarter label renders independent of chart width.

        For yearmonth encoding with an authored yearquarter format, the
        surviving labels retain fiscal Q1-Q4 vocabulary."""
        import re

        import vl_convert as vlc

        rs, ctx = resolve_style_and_context(get_theme_style("clarity"))
        data = _monthly_data(n_months=17)
        chart = _area_chart(
            {"labels": {"time_unit": "yearquarter"}, "fiscal_year_start_month": 3}
        )
        spec = generate_vega_lite_spec(
            chart, data, board_style=rs, chart_style_context=ctx, width=389.76
        )
        svg = vlc.vegalite_to_svg(spec, vl_version="5.20")
        quarter_texts = re.findall(r">(Q[1-4])<", svg)
        assert quarter_texts
        assert not re.findall(r">(Mar|Jun|Sep|Dec)<", svg)
