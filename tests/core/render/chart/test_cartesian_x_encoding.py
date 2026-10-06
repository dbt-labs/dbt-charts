"""Tests for build_cartesian_x_encoding and y_zero_scale helpers.

These helpers centralize the V1 map_x_encoding / map_y_encoding logic for
V2 emitters so that every cartesian emitter (line, area, layered, …) gets
identical ordinal-time-axis behavior with a one-liner callsite.
"""

from __future__ import annotations

import datetime as dt
import types
from typing import Any

import pytest

from dbt_charts.core.compile.config import get_chart_rendering
from dbt_charts.core.diagnostics.chart_data import ChartDataError
from dbt_charts.core.render.chart.type_inference import (
    build_cartesian_x_encoding,
    y_zero_scale,
    zero_anchor_pinned_floor,
)

# ---------------------------------------------------------------------------
# Helpers for constructing lightweight axis/scale stubs
# ---------------------------------------------------------------------------


def _axis(
    time_unit: str | None = None,
    axis_type: str | None = None,
    label_time_unit: str | None = None,
    ticks_count: int | None = None,
    ticks_time_unit: str | None = None,
    ticks_step: int | None = None,
    clock: int | None = None,
) -> Any:
    """Build a minimal ResolvedAxisStyle-like namespace for tests.

    ``scale=None`` mirrors ResolvedAxisStyle's real field so production code
    can read ``axis.scale`` directly instead of a defensive ``getattr``.
    """
    labels = types.SimpleNamespace(time_unit=label_time_unit, format=None, clock=clock)
    ticks = types.SimpleNamespace(
        count=ticks_count, time_unit=ticks_time_unit, step=ticks_step
    )
    return types.SimpleNamespace(
        time_unit=time_unit,
        type=axis_type,
        labels=labels,
        ticks=ticks,
        scale=None,
        fiscal_year_start_month=1,
    )


def _scale(
    zero: bool | str | None = None,
    scale_type: str | None = None,
    base: float | None = None,
    domain: tuple[Any, Any] | None = None,
) -> Any:
    # Mirrors ResolvedScaleStyle: continuous-scale fields are nested in .continuous,
    # and log/pow/symlog params are further nested one level under that.
    has_continuous = (
        zero is not None
        or scale_type is not None
        or base is not None
        or domain is not None
    )
    log = types.SimpleNamespace(base=base) if base is not None else None
    continuous = (
        types.SimpleNamespace(
            zero=zero,
            type=scale_type,
            log=log,
            pow=None,
            symlog=None,
            domain=domain,
        )
        if has_continuous
        else None
    )
    return types.SimpleNamespace(
        continuous=continuous,
        round=None,
        clamp=None,
        nice=None,
        padding=None,
        headroom=None,
        values=None,
        x_reverse=None,
    )


def _axis_with_scale(
    time_unit: str | None = None,
    axis_type: str | None = None,
    label_time_unit: str | None = None,
    scale_zero: bool | str | None = None,
    scale_type: str | None = None,
    scale_base: float | None = None,
    scale_domain: tuple[Any, Any] | None = None,
    clock: int | None = None,
    tick_values: tuple[float, ...] = (),
    scale_values: list[float] | None = None,
) -> Any:
    scale = _scale(scale_zero, scale_type, scale_base, scale_domain)
    if scale is not None:
        scale.values = scale_values
    labels = types.SimpleNamespace(time_unit=label_time_unit, format=None, clock=clock)
    ticks = types.SimpleNamespace(count=None, time_unit=None, step=None)
    return types.SimpleNamespace(
        time_unit=time_unit,
        type=axis_type,
        labels=labels,
        scale=scale,
        ticks=ticks,
        tick_values=tick_values,
        fiscal_year_start_month=1,
    )


def _rows(dates: list[str]) -> list[dict[str, Any]]:
    return [{"date": d} for d in dates]


def _monthly_dates(n: int, start: tuple[int, int] = (1985, 1)) -> list[str]:
    """n consecutive first-of-month ISO dates starting at `start`."""
    year, month = start
    out = []
    for _ in range(n):
        out.append(f"{year:04d}-{month:02d}-01")
        month += 1
        if month > 12:
            month = 1
            year += 1
    return out


def _daily_dates(n: int, start: str = "2024-01-01") -> list[str]:
    """n consecutive daily ISO dates starting at `start`."""
    first = dt.date.fromisoformat(start)
    return [(first + dt.timedelta(days=i)).isoformat() for i in range(n)]


_YEARMONTH_DATES = [
    "2025-01-01",
    "2025-02-01",
    "2025-03-01",
    "2025-04-01",
    "2025-05-01",
]

_YEARQUARTER_DATES = [
    "2024-01-07",
    "2024-04-07",
    "2024-07-07",
    "2024-10-06",
]


# ---------------------------------------------------------------------------
# build_cartesian_x_encoding — type decision
# ---------------------------------------------------------------------------


class TestBuildCartesianXEncodingType:
    """Type inference and axis-type-override rules."""

    def test_bucketed_yearmonth_returns_ordinal(self):
        ax = _axis(time_unit="yearmonth")
        vl_type, _, _tu = build_cartesian_x_encoding(
            _rows(_YEARMONTH_DATES), "date", ax, {}, "bar"
        )
        assert vl_type == "ordinal"

    def test_bucketed_yearquarter_returns_ordinal(self):
        ax = _axis(time_unit="yearquarter")
        vl_type, _, _tu = build_cartesian_x_encoding(
            _rows(_YEARQUARTER_DATES), "date", ax, {}, "bar"
        )
        assert vl_type == "ordinal"

    def test_bucketed_year_returns_ordinal(self):
        ax = _axis(time_unit="year")
        vl_type, _, _tu = build_cartesian_x_encoding(
            [{"date": "2024-01-01"}, {"date": "2025-01-01"}], "date", ax, {}, "bar"
        )
        assert vl_type == "ordinal"

    def test_no_time_unit_iso_date_auto_detects_grain(self):
        # When no time_unit authored, detect_time_unit fires on temporal data.
        # Monthly ISO dates → "yearmonth" → ordinal (mirrors V1 auto-detect path).
        ax = _axis()
        vl_type, _, _tu = build_cartesian_x_encoding(
            _rows(_YEARMONTH_DATES), "date", ax, {}, "bar"
        )
        assert vl_type == "ordinal"

    def test_no_time_unit_non_grain_temporal_returns_temporal(self):
        # Timestamp data that doesn't match a bucketed calendar grain stays temporal.
        ax = _axis()
        data = [{"ts": "2025-01-15T08:30:00"}, {"ts": "2025-01-16T09:45:00"}]
        vl_type, _, _tu = build_cartesian_x_encoding(data, "ts", ax, {}, "bar")
        assert vl_type == "temporal"

    def test_no_time_unit_nominal_data_returns_nominal(self):
        ax = _axis()
        data = [{"date": "Core"}, {"date": "Growth"}]
        vl_type, _, _tu = build_cartesian_x_encoding(data, "date", ax, {}, "bar")
        assert vl_type == "nominal"

    def test_axis_type_temporal_override_trumps_bucketed_grain(self):
        ax = _axis(time_unit="yearmonth", axis_type="temporal")
        vl_type, _, _tu = build_cartesian_x_encoding(
            _rows(_YEARMONTH_DATES), "date", ax, {}, "bar"
        )
        assert vl_type == "temporal"

    def test_axis_type_ordinal_override_forces_ordinal_without_time_unit(self):
        ax = _axis(axis_type="ordinal")
        data = [{"date": "Core"}, {"date": "Growth"}]
        vl_type, _, _tu = build_cartesian_x_encoding(data, "date", ax, {}, "bar")
        assert vl_type == "ordinal"

    def test_no_time_unit_no_data_returns_nominal(self):
        ax = _axis()
        vl_type, _, _tu = build_cartesian_x_encoding([], "date", ax, {}, "bar")
        assert vl_type == "nominal"

    def test_scale_type_temporal_trumps_bucketed_grain(self):
        """axis_x.scale.type: temporal is a second escape hatch (alongside the
        pre-existing axis_x.type) for forcing a continuous temporal scale over
        the auto-inferred bucketed-ordinal type."""
        ax = _axis_with_scale(time_unit="yearmonth", scale_type="temporal")
        vl_type, _, _tu = build_cartesian_x_encoding(
            _rows(_YEARMONTH_DATES), "date", ax, {}, "bar"
        )
        assert vl_type == "temporal"

    def test_scale_type_temporal_forces_continuous_over_sparse_year_data(self):
        """Sparse yearly dates with no
        authored time_unit auto-detect to a bucketed-ordinal 'year' grain.
        scale.type: temporal must force the continuous path so an authored
        domain extending past the data extent has a scale to apply to."""
        ax = _axis_with_scale(scale_type="temporal")
        data = _rows(
            ["1957-01-01", "1958-01-01", "1970-01-01", "2000-01-01", "2024-01-01"]
        )
        vl_type, _, _tu = build_cartesian_x_encoding(data, "date", ax, {}, "bar")
        assert vl_type == "temporal"

    def test_axis_type_explicit_wins_over_scale_type_temporal(self):
        """axis_x.type is the more specific/older override; when both are
        authored (conflicting), the explicit axis_x.type wins rather than
        silently picking one — mirrors precedence of an explicit setting
        over a broader sentinel."""
        ax = _axis_with_scale(
            time_unit="yearmonth", axis_type="ordinal", scale_type="temporal"
        )
        vl_type, _, _tu = build_cartesian_x_encoding(
            _rows(_YEARMONTH_DATES), "date", ax, {}, "bar"
        )
        assert vl_type == "ordinal"

    def test_scale_type_non_temporal_does_not_affect_x_type_decision(self):
        """scale.type: log (a quantitative-scale value) has no bearing on the
        ordinal/temporal field-type decision — only 'temporal' is special."""
        ax = _axis_with_scale(time_unit="year", scale_type="log")
        data = [{"date": "2024-01-01"}, {"date": "2025-01-01"}]
        vl_type, _, _tu = build_cartesian_x_encoding(data, "date", ax, {}, "bar")
        assert vl_type == "ordinal"


# ---------------------------------------------------------------------------
# build_cartesian_x_encoding — bucket-count density gate
# ---------------------------------------------------------------------------


class TestBuildCartesianXEncodingDensityGate:
    """Bucketed calendar grains with no coarser label cadence to fall back on
    (yearmonth, yearquarter, year) flip to a continuous temporal scale once
    the bucket count passes ``chart_rendering.type_inference.max_ordinal_buckets`` —
    otherwise the ordinal axis renders one gridline per bucket with no filtering.
    """

    def test_high_density_yearmonth_falls_back_to_temporal(self) -> None:
        ax = _axis(time_unit="yearmonth")
        dates = _monthly_dates(480)
        vl_type, _, _tu = build_cartesian_x_encoding(
            _rows(dates), "date", ax, {}, "bar"
        )
        assert vl_type == "temporal"

    def test_low_density_yearmonth_stays_ordinal(self) -> None:
        ax = _axis(time_unit="yearmonth")
        dates = _monthly_dates(24)
        vl_type, _, _tu = build_cartesian_x_encoding(
            _rows(dates), "date", ax, {}, "bar"
        )
        assert vl_type == "ordinal"

    def test_boundary_at_threshold_stays_ordinal(self) -> None:
        max_buckets = get_chart_rendering().type_inference.max_ordinal_buckets
        ax = _axis(time_unit="yearmonth")
        dates = _monthly_dates(max_buckets)
        vl_type, _, _tu = build_cartesian_x_encoding(
            _rows(dates), "date", ax, {}, "bar"
        )
        assert vl_type == "ordinal"

    def test_boundary_one_past_threshold_falls_back_to_temporal(self) -> None:
        max_buckets = get_chart_rendering().type_inference.max_ordinal_buckets
        ax = _axis(time_unit="yearmonth")
        dates = _monthly_dates(max_buckets + 1)
        vl_type, _, _tu = build_cartesian_x_encoding(
            _rows(dates), "date", ax, {}, "bar"
        )
        assert vl_type == "temporal"

    def test_high_density_daily_unaffected_already_handled_by_label_upgrade(self):
        # 500 daily points: yearmonthdate's label cadence already auto-upgrades
        # to yearmonth (resolve_label_time_unit), so ordinal_axis_values filters
        # ticks down to ~1/month regardless of raw bucket count. The density
        # gate must not re-flip this case to temporal.
        ax = _axis()
        dates = _daily_dates(500)
        vl_type, _, tu = build_cartesian_x_encoding(_rows(dates), "date", ax, {}, "bar")
        assert tu == "yearmonthdate"
        assert vl_type == "ordinal"

    def test_explicit_ordinal_override_wins_over_density_gate(self):
        ax = _axis(time_unit="yearmonth", axis_type="ordinal")
        dates = _monthly_dates(480)
        vl_type, _, _tu = build_cartesian_x_encoding(
            _rows(dates), "date", ax, {}, "bar"
        )
        assert vl_type == "ordinal"

    def test_high_density_yearmonth_gets_smart_temporal_label_expr(self):
        # Once flipped to temporal, the existing temporal-escape-hatch branch
        # still injects the yearmonth smart cadence labelExpr.
        ax = _axis(time_unit="yearmonth")
        dates = _monthly_dates(480)
        _, ax_vl, _tu = build_cartesian_x_encoding(_rows(dates), "date", ax, {}, "bar")
        assert "labelExpr" in ax_vl
        assert "utcmonth" in ax_vl["labelExpr"]


# ---------------------------------------------------------------------------
# build_cartesian_x_encoding — fine-grain (day/week) scaffold-budget gate
# ---------------------------------------------------------------------------


def _sparse_daily_dates(n: int = 10, gap_days: int = 45) -> list[str]:
    """n dates spaced gap_days apart — daily grain, far sparser than daily."""
    first = dt.date(2023, 1, 1)
    return [(first + dt.timedelta(days=gap_days * i)).isoformat() for i in range(n)]


class TestFineGrainScaffoldBudgetGate:
    """A detected yearweek/yearmonthdate grain bands only while the ordinal
    scaffold gap-fill would enumerate stays within budget
    (synthesized empty buckets ≤ max(distinct buckets, max_ordinal_buckets)).
    Past that, the data is sparser than its detected grain — e.g. ten dates
    45 days apart, too irregular for any cadence to name, reading as "daily" —
    and banding renders sub-pixel bars across thousands of near-empty slots.
    Those charts flip to a continuous temporal scale with no timeUnit banding.
    Regularly-spaced data names its own grain before it gets here, so what
    reaches this gate is data with no legible grain of its own."""

    def test_sparse_daily_bar_flips_to_continuous_temporal(self) -> None:
        ax = _axis()
        vl_type, x_enc, tu = build_cartesian_x_encoding(
            _rows(_sparse_daily_dates()), "date", ax, {}, "bar"
        )
        assert vl_type == "temporal"
        assert tu is None

    def test_quarterly_with_stray_dates_never_reaches_a_fine_grain(self) -> None:
        # The reported repro: quarterly data plus two NULL-measure pad rows at
        # arbitrary dates. Two odd gaps out of sixteen do not move the median,
        # so the grain is yearmonth — 46 slots for 17 bars, comfortably inside
        # budget, and the bars keep their bands.
        quarters = [
            f"{y}-{m:02d}-01" for y in range(2023, 2027) for m in (1, 4, 7, 10)
        ][:15]
        dates = ["2022-11-17", *quarters, "2026-08-15"]
        ax = _axis()
        vl_type, _, tu = build_cartesian_x_encoding(_rows(dates), "date", ax, {}, "bar")
        assert tu == "yearmonth"
        assert vl_type == "ordinal"

    def test_month_end_monthly_bar_never_reaches_a_fine_grain(self) -> None:
        # LAST_DAY()-style monthly buckets sit on no bucket start, but their
        # 28–31-day cadence names the grain outright: 36 month buckets for 36
        # rows, zero synthesized.
        dates = []
        d = dt.date(2023, 1, 1)
        for _ in range(36):
            next_month = (d.replace(day=28) + dt.timedelta(days=4)).replace(day=1)
            dates.append((next_month - dt.timedelta(days=1)).isoformat())
            d = next_month
        ax = _axis()
        vl_type, _, tu = build_cartesian_x_encoding(_rows(dates), "date", ax, {}, "bar")
        assert tu == "yearmonth"
        assert vl_type == "ordinal"

    def test_dense_daily_bar_stays_ordinal(self) -> None:
        # Contiguous daily data has zero synthesized buckets — banding stands
        # (mirrors test_high_density_daily_unaffected_already_handled_by_label_upgrade).
        ax = _axis()
        vl_type, _, tu = build_cartesian_x_encoding(
            _rows(_daily_dates(500)), "date", ax, {}, "bar"
        )
        assert tu == "yearmonthdate"
        assert vl_type == "ordinal"

    def test_business_day_bar_stays_ordinal(self) -> None:
        # Weekday-only daily data: the weekend gaps synthesize ~2 buckets per
        # 5 real ones — well within budget, banding stands.
        first = dt.date(2024, 1, 1)
        dates = []
        d = first
        while len(dates) < 260:
            if d.weekday() < 5:
                dates.append(d.isoformat())
            d += dt.timedelta(days=1)
        ax = _axis()
        vl_type, _, tu = build_cartesian_x_encoding(_rows(dates), "date", ax, {}, "bar")
        assert tu == "yearmonthdate"
        assert vl_type == "ordinal"

    def test_small_multiples_disjoint_windows_stay_ordinal(self) -> None:
        # Gap-fill enumerates each panel's OWN [min, max] range, never the
        # pooled one (emitters/_channels.py's fill_one_panel). Two panels of
        # contiguous dailies 20 years apart synthesize ZERO empty buckets, so
        # banding must stand — measuring the pooled span instead would see a
        # ~7,200-bucket deficit and collapse both clusters onto a 20-year
        # continuous domain, which is strictly worse than not gating at all.
        rows = [
            {"date": d, "panel": "a"} for d in _daily_dates(60, start="2000-01-01")
        ] + [{"date": d, "panel": "b"} for d in _daily_dates(60, start="2020-01-01")]
        ax = _axis()
        vl_type, _, tu = build_cartesian_x_encoding(
            rows, "date", ax, {}, "bar", panel_fields=("panel",)
        )
        assert tu == "yearmonthdate"
        assert vl_type == "ordinal"

    def test_small_multiples_each_panel_sparse_still_flips(self) -> None:
        # Per-panel measurement is not a blanket exemption for faceted charts:
        # when each panel is itself sparser than its detected grain, the
        # deficit is real in every panel and the chart still flips.
        rows = [{"date": d, "panel": "a"} for d in _sparse_daily_dates()] + [
            {"date": d, "panel": "b"} for d in _sparse_daily_dates()
        ]
        ax = _axis()
        vl_type, _, tu = build_cartesian_x_encoding(
            rows, "date", ax, {}, "bar", panel_fields=("panel",)
        )
        assert vl_type == "temporal"
        assert tu is None

    def test_sparse_weekly_bar_flips_to_continuous_temporal(self) -> None:
        # The yearweek arm's flip direction, which no other test covers.
        # It does NOT pin the step_days == 7 constant and cannot: a 1-day step
        # only ever yields a LARGER deficit, so sparse weekly data flips
        # either way. The constant is pinned in the banding direction by
        # test_weekly_bar_79_buckets_is_ordinal, which fails when it is
        # mutated to 1. detect_time_unit returns yearweek only when every
        # value shares one weekday, so both clusters stay on the same lattice.
        first = dt.date(2020, 1, 6)  # a Monday
        near = [(first + dt.timedelta(weeks=i)).isoformat() for i in range(10)]
        far = [(first + dt.timedelta(weeks=260 + i)).isoformat() for i in range(10)]
        ax = _axis()
        vl_type, _, tu = build_cartesian_x_encoding(
            _rows(near + far), "date", ax, {}, "bar"
        )
        assert vl_type == "temporal"
        assert tu is None

    def test_verdict_is_invariant_in_panel_count(self) -> None:
        # The band scale is SHARED, so a bucket two panels both cover is one
        # band. Summing each panel's span separately would make the verdict a
        # function of panel count — identical per-panel data flipping to
        # continuous purely by gaining a sibling — while the axis it describes
        # never changed.
        first = dt.date(2024, 1, 1)
        one_panel = [(first + dt.timedelta(days=3 * i)).isoformat() for i in range(30)]
        ax = _axis()
        verdicts = set()
        for n_panels in (1, 2, 3, 6):
            rows = [
                {"date": d, "panel": f"p{p}"}
                for p in range(n_panels)
                for d in one_panel
            ]
            vl_type, _, tu = build_cartesian_x_encoding(
                rows, "date", ax, {}, "bar", panel_fields=("panel",)
            )
            verdicts.add((vl_type, tu))
        # Assert the SPECIFIC verdict, not mere uniformity: with a thin margin
        # a stricter max_ordinal_buckets makes every panel count resolve
        # ("temporal", None), which is uniform and pins nothing.
        assert verdicts == {("ordinal", "yearmonthdate")}, verdicts

    def test_panel_fields_absent_matches_single_panel_verdict(self) -> None:
        # The N=1 case must be bit-identical to no panel_fields at all — the
        # non-faceted path is what every existing test in this class pins.
        dates = _sparse_daily_dates()
        ax = _axis()
        flat = build_cartesian_x_encoding(_rows(dates), "date", ax, {}, "bar")
        paneled = build_cartesian_x_encoding(
            [{"date": d, "panel": "only"} for d in dates],
            "date",
            ax,
            {},
            "bar",
            panel_fields=("panel",),
        )
        assert (flat[0], flat[2]) == (paneled[0], paneled[2])

    def test_authored_time_unit_is_never_overridden(self) -> None:
        # An explicit time_unit is an instruction, not a guess — the budget
        # gate only applies to auto-detected grains.
        ax = _axis(time_unit="yearmonthdate")
        vl_type, _, tu = build_cartesian_x_encoding(
            _rows(_sparse_daily_dates()), "date", ax, {}, "bar"
        )
        assert tu == "yearmonthdate"
        assert vl_type == "ordinal"

    def test_sparse_daily_line_area_scatter_drop_grain(self) -> None:
        # line/area/scatter never band mark widths, but grain also paints one
        # gridline and label per bucket — an over-budget fine grain must be
        # dropped for these families too, same as bar/heatmap.
        for mark in ("line", "area", "scatter"):
            vl_type, _, tu = build_cartesian_x_encoding(
                _rows(_sparse_daily_dates()), "date", _axis(), {}, mark
            )
            assert vl_type == "temporal", mark
            assert tu is None, mark

    def test_authored_temporal_sparse_daily_drops_time_unit(self) -> None:
        # style.axis_x.type: temporal with sparse daily-grain data: the scale
        # is already continuous, but an emitted daily timeUnit would still
        # band mark widths to one day (~sub-pixel over a multi-year span).
        ax = _axis(axis_type="temporal")
        vl_type, _, tu = build_cartesian_x_encoding(
            _rows(_sparse_daily_dates()), "date", ax, {}, "bar"
        )
        assert vl_type == "temporal"
        assert tu is None


# ---------------------------------------------------------------------------
# build_cartesian_x_encoding — coarse-grain (month/quarter/year) budget gate
# ---------------------------------------------------------------------------


class TestCoarseGrainScaffoldBudgetGate:
    """The scaffold budget applies to month/quarter/year grains too.

    A coarse grain owes the axis one band per bucket across its span exactly
    as a fine one does; a decade-spaced series names ``year`` honestly and
    still asks for ten empty bands per real bar. The row count the coarse
    branch measures against ``max_ordinal_buckets`` cannot see that — it only
    ever counts the bars that exist."""

    def test_decennial_bar_flips_to_continuous_temporal(self) -> None:
        # Twelve readings a decade apart: an honest `year` cadence, but 111
        # bands for 12 bars — 99 of them empty.
        dates = [f"{y}-04-15" for y in range(1910, 2021, 10)]
        ax = _axis()
        vl_type, _, tu = build_cartesian_x_encoding(_rows(dates), "date", ax, {}, "bar")
        assert vl_type == "temporal"
        assert tu is None

    def test_annual_bar_stays_ordinal(self) -> None:
        # The control the flip above must not catch: contiguous years
        # synthesize nothing.
        dates = [f"{y}-12-31" for y in range(2014, 2026)]
        ax = _axis()
        vl_type, _, tu = build_cartesian_x_encoding(_rows(dates), "date", ax, {}, "bar")
        assert tu == "year"
        assert vl_type == "ordinal"

    def test_five_yearly_bar_stays_ordinal(self) -> None:
        # Sparse but not pathological: 21 bands for 5 bars is inside the
        # 60-bucket floor, so the grain stands.
        dates = [f"{y}-06-30" for y in range(1990, 2011, 5)]
        ax = _axis()
        vl_type, _, tu = build_cartesian_x_encoding(_rows(dates), "date", ax, {}, "bar")
        assert tu == "year"
        assert vl_type == "ordinal"

    def test_twelve_month_bar_stays_ordinal(self) -> None:
        ax = _axis()
        vl_type, _, tu = build_cartesian_x_encoding(
            _rows(_monthly_dates(12, start=(2024, 1))), "date", ax, {}, "bar"
        )
        assert tu == "yearmonth"
        assert vl_type == "ordinal"

    def test_quarterly_bar_stays_ordinal(self) -> None:
        dates = [f"{y}-{m:02d}-01" for y in range(2021, 2025) for m in (1, 4, 7, 10)]
        ax = _axis()
        vl_type, _, tu = build_cartesian_x_encoding(_rows(dates), "date", ax, {}, "bar")
        assert tu == "yearquarter"
        assert vl_type == "ordinal"

    def test_semiannual_bar_stays_ordinal(self) -> None:
        # Half-years band as quarters: 31 bands for 16 bars.
        dates = [f"{y}-{m:02d}-30" for y in range(2018, 2026) for m in (6, 12)]
        ax = _axis()
        vl_type, _, tu = build_cartesian_x_encoding(_rows(dates), "date", ax, {}, "bar")
        assert tu == "yearquarter"
        assert vl_type == "ordinal"

    def test_authored_coarse_grain_is_never_gated(self) -> None:
        dates = [f"{y}-04-15" for y in range(1910, 2021, 10)]
        ax = _axis(time_unit="year")
        vl_type, _, tu = build_cartesian_x_encoding(_rows(dates), "date", ax, {}, "bar")
        assert tu == "year"
        assert vl_type == "ordinal"

    def test_decennial_line_drops_the_grain(self) -> None:
        # line/area/scatter never band mark widths, but they do paint one
        # gridline and label per bucket, so they consult the same gate.
        dates = [f"{y}-04-15" for y in range(1910, 2021, 10)]
        ax = _axis()
        vl_type, _, tu = build_cartesian_x_encoding(
            _rows(dates), "date", ax, {}, "line"
        )
        assert vl_type == "temporal"
        assert tu is None


# ---------------------------------------------------------------------------
# build_cartesian_x_encoding — mark_type-driven line/area vs bar/column split
# ---------------------------------------------------------------------------


class TestBuildCartesianXEncodingMarkTypeSplit:
    """For a BUCKETED_CALENDAR_UNITS grain with no authored type, line/area
    always emit continuous temporal; bar/column keep the ordinal-band density
    gate (unaffected by mark_type)."""

    def test_year_bar_is_ordinal(self):
        ax = _axis()
        data = [{"date": "2014-01-01"}, {"date": "2015-01-01"}, {"date": "2024-01-01"}]
        vl_type, _, tu = build_cartesian_x_encoding(data, "date", ax, {}, "bar")
        assert tu == "year"
        assert vl_type == "ordinal"

    def test_year_line_is_temporal(self):
        ax = _axis()
        data = [{"date": "2014-01-01"}, {"date": "2015-01-01"}, {"date": "2024-01-01"}]
        vl_type, _, tu = build_cartesian_x_encoding(data, "date", ax, {}, "line")
        assert tu == "year"
        assert vl_type == "temporal"

    def test_year_area_is_temporal(self):
        ax = _axis()
        data = [{"date": "2014-01-01"}, {"date": "2015-01-01"}, {"date": "2024-01-01"}]
        vl_type, _, tu = build_cartesian_x_encoding(data, "date", ax, {}, "area")
        assert vl_type == "temporal"

    def test_low_density_yearmonth_scatter_is_temporal(self):
        # Scatter joins line/area's always-temporal rule for the ordinal-vs-
        # temporal split — never bar's ordinal band, since that would snap
        # its points onto equally-spaced ticks instead of their real
        # position. The scaffold-budget gate is not reached here: the grain is
        # authored, and yearmonth is not a FINE_BUCKET_UNIT.
        ax = _axis(time_unit="yearmonth")
        dates = _monthly_dates(12, start=(2024, 1))
        vl_type, _, tu = build_cartesian_x_encoding(
            _rows(dates), "date", ax, {}, "scatter"
        )
        assert tu == "yearmonth"
        assert vl_type == "temporal"

    def test_weekly_scatter_is_temporal(self):
        ax = _axis()
        dates = [
            (dt.date(2023, 1, 2) + dt.timedelta(weeks=i)).isoformat() for i in range(20)
        ]
        vl_type, _, tu = build_cartesian_x_encoding(
            _rows(dates), "date", ax, {}, "scatter"
        )
        assert tu == "yearweek"
        assert vl_type == "temporal"

    def test_year_line_step_stays_ordinal(self):
        # A band-aware step curve requires a band x-scale, so it must skip the
        # line-is-always-temporal branch above unconditionally — never the bar
        # density gate.
        ax = _axis()
        data = [{"date": "2014-01-01"}, {"date": "2015-01-01"}, {"date": "2024-01-01"}]
        vl_type, _, tu = build_cartesian_x_encoding(
            data, "date", ax, {}, "line", "step"
        )
        assert tu == "year"
        assert vl_type == "ordinal"

    def test_high_density_yearmonth_line_step_stays_ordinal(self) -> None:
        # step's band-x requirement is not negotiable at any bucket count:
        # unlike plain line/area (always temporal) and bar (density-gated to
        # temporal above max_ordinal_buckets), step must stay ordinal even
        # past the density gate, or step_band.py raises ChartDataError.
        max_buckets = get_chart_rendering().type_inference.max_ordinal_buckets
        ax = _axis(time_unit="yearmonth")
        dates = _monthly_dates(max_buckets + 1)
        vl_type, _, _tu = build_cartesian_x_encoding(
            _rows(dates), "date", ax, {}, "line", "step"
        )
        assert vl_type == "ordinal"

    def test_low_density_yearmonth_line_is_temporal(self) -> None:
        # Below max_ordinal_buckets, bar stays ordinal (existing density gate)
        # but line always goes continuous — the ordinal-vs-temporal split is
        # bar-only. The scaffold-budget gate is not reached here: the grain is
        # authored, and yearmonth is not a FINE_BUCKET_UNIT.
        ax = _axis(time_unit="yearmonth")
        dates = _monthly_dates(24)
        vl_type, _, _tu = build_cartesian_x_encoding(
            _rows(dates), "date", ax, {}, "line"
        )
        assert vl_type == "temporal"

    def test_weekly_line_79_buckets_is_temporal(self):
        # Bundled line/area regression: yearweek grain, well under the
        # bar-only density threshold, must still be temporal for line.
        ax = _axis()
        dates = [
            (dt.date(2023, 1, 2) + dt.timedelta(weeks=i)).isoformat() for i in range(79)
        ]
        vl_type, _, tu = build_cartesian_x_encoding(
            _rows(dates), "date", ax, {}, "line"
        )
        assert tu == "yearweek"
        assert vl_type == "temporal"

    def test_weekly_bar_79_buckets_is_ordinal(self):
        ax = _axis()
        dates = [
            (dt.date(2023, 1, 2) + dt.timedelta(weeks=i)).isoformat() for i in range(79)
        ]
        vl_type, _, tu = build_cartesian_x_encoding(_rows(dates), "date", ax, {}, "bar")
        assert tu == "yearweek"
        assert vl_type == "ordinal"

    def test_axis_type_override_wins_over_line_temporal_default(self):
        ax = _axis(axis_type="ordinal")
        data = [{"date": "2014-01-01"}, {"date": "2015-01-01"}]
        vl_type, _, _tu = build_cartesian_x_encoding(data, "date", ax, {}, "line")
        assert vl_type == "ordinal"


# ---------------------------------------------------------------------------
# build_cartesian_x_encoding — axis values injection
# ---------------------------------------------------------------------------


class TestBuildCartesianXEncodingValues:
    """ordinal_axis_values is injected for bucketed calendar grains."""

    def test_yearmonth_injects_values(self):
        ax = _axis(time_unit="yearmonth")
        _, ax_vl, _tu = build_cartesian_x_encoding(
            _rows(_YEARMONTH_DATES), "date", ax, {}, "bar"
        )
        assert "values" in ax_vl
        assert ax_vl["values"] == _YEARMONTH_DATES

    def test_existing_values_not_overwritten(self):
        ax = _axis(time_unit="yearmonth")
        existing_values = ["2025-06-01"]
        _, ax_vl, _tu = build_cartesian_x_encoding(
            _rows(_YEARMONTH_DATES), "date", ax, {"values": existing_values}, "bar"
        )
        assert ax_vl["values"] == existing_values

    def test_no_time_unit_no_values_injected_for_non_grain_data(self):
        # Non-grain timestamps (hours differ) — detect_time_unit returns None → no values.
        ax = _axis()
        data = [{"date": "2025-01-15T08:30:00"}, {"date": "2025-01-16T09:45:00"}]
        _, ax_vl, _tu = build_cartesian_x_encoding(data, "date", ax, {}, "bar")
        assert "values" not in ax_vl

    def test_yearquarter_injects_values(self):
        ax = _axis(time_unit="yearquarter")
        _, ax_vl, _tu = build_cartesian_x_encoding(
            _rows(_YEARQUARTER_DATES), "date", ax, {}, "bar"
        )
        assert "values" in ax_vl
        # Values are first week of each quarter (filtered by label cadence)
        assert len(ax_vl["values"]) == len(_YEARQUARTER_DATES)

    def test_authored_quarter_label_unit_filters_ordinal_ticks(self):
        dates = _monthly_dates(12, start=(2024, 1))
        ax = _axis(time_unit="yearmonth", label_time_unit="yearquarter")
        _, ax_vl, _tu = build_cartesian_x_encoding(_rows(dates), "date", ax, {}, "bar")
        assert ax_vl["values"] == [
            "2024-01-01",
            "2024-04-01",
            "2024-07-01",
            "2024-10-01",
        ]

    def test_auto_visibility_thinning_keeps_encoding_grain_ticks(self):
        dates = _monthly_dates(12, start=(2024, 1))
        ax = _axis(time_unit="yearmonth")
        _, ax_vl, _tu = build_cartesian_x_encoding(
            _rows(dates),
            "date",
            ax,
            {},
            "bar",
            visibility_time_unit="yearquarter",
        )
        assert ax_vl["values"] == dates

    def test_authored_quarter_label_unit_sets_temporal_tick_values(self):
        dates = _monthly_dates(12, start=(2024, 1))
        ax = _axis(
            time_unit="yearmonth",
            axis_type="temporal",
            label_time_unit="yearquarter",
        )
        _, ax_vl, _tu = build_cartesian_x_encoding(_rows(dates), "date", ax, {}, "line")
        assert ax_vl["values"] == [
            "2024-01-01",
            "2024-04-01",
            "2024-07-01",
            "2024-10-01",
        ]
        assert "tickCount" not in ax_vl

    def test_ax_vl_not_mutated_in_place(self):
        ax = _axis(time_unit="yearmonth")
        original = {}
        _, returned, _tu = build_cartesian_x_encoding(
            _rows(_YEARMONTH_DATES), "date", ax, original, "bar"
        )
        # Returned dict must be a new object, not the same reference
        assert returned is not original

    def test_no_data_no_values_injected(self):
        ax = _axis(time_unit="yearmonth")
        _, ax_vl, _tu = build_cartesian_x_encoding([], "date", ax, {}, "bar")
        assert "values" not in ax_vl


# ---------------------------------------------------------------------------
# build_cartesian_x_encoding — labelExpr injection
# ---------------------------------------------------------------------------


class TestBuildCartesianXEncodingLabelExpr:
    """default_label_expr_for is injected when data is temporal and no format set."""

    def test_yearmonth_with_temporal_data_injects_label_expr(self):
        ax = _axis(time_unit="yearmonth")
        _, ax_vl, _tu = build_cartesian_x_encoding(
            _rows(_YEARMONTH_DATES), "date", ax, {}, "bar"
        )
        assert "labelExpr" in ax_vl
        # Should reference utcmonth and utcFormat as in V1
        assert "utcmonth" in ax_vl["labelExpr"]
        assert "utcFormat" in ax_vl["labelExpr"]

    def test_bucketed_month_strings_inject_label_expr(self):
        """Raw YYYY-MM bucket strings (ordinal, not full ISO) still get the smart
        time labelExpr — V1 normalized them to dates first; V2 reads them raw."""
        data = [{"date": f"2025-{m:02d}"} for m in range(1, 13)]
        ax = _axis()  # no authored time_unit; detected from data
        vl_type, ax_vl, _tu = build_cartesian_x_encoding(data, "date", ax, {}, "bar")
        assert vl_type == "ordinal"
        assert "labelExpr" in ax_vl
        assert "utcFormat" in ax_vl["labelExpr"]

    def test_existing_label_expr_not_overwritten(self):
        ax = _axis(time_unit="yearmonth")
        custom_expr = "datum.value"
        _, ax_vl, _tu = build_cartesian_x_encoding(
            _rows(_YEARMONTH_DATES), "date", ax, {"labelExpr": custom_expr}, "bar"
        )
        assert ax_vl["labelExpr"] == custom_expr

    def test_explicit_time_format_routes_to_label_expr_suppressing_smart(self):
        """Authored time-format on an ordinal axis is routed through utcFormat labelExpr
        (d3-format rejects %-directives on ordinal scales), which also suppresses the
        auto-generated smart cadence labelExpr.
        """
        ax = _axis(time_unit="yearmonth")
        _, ax_vl, _tu = build_cartesian_x_encoding(
            _rows(_YEARMONTH_DATES), "date", ax, {"format": "%Y-%m"}, "bar"
        )
        assert ax_vl.get("labelExpr") == "utcFormat(toDate(datum.value), '%Y-%m')"
        assert "format" not in ax_vl

    def test_nominal_series_data_no_label_expr(self):
        """Non-temporal data (nominal strings) should not get labelExpr."""
        ax = _axis(time_unit="yearmonth")
        data = [{"date": "Core"}, {"date": "Growth"}]
        _, ax_vl, _tu = build_cartesian_x_encoding(data, "date", ax, {}, "bar")
        assert "labelExpr" not in ax_vl

    def test_no_calendar_grain_gets_the_subday_clock_default(self):
        # Non-grain, sub-day timestamps get the default clock vocabulary
        # rather than no labelExpr at all — see
        # TestBuildCartesianXEncodingSubdayClock below for the full ladder.
        # Requires a known chart width: the vocabulary's tick-cadence gate
        # predicts Vega's own tick step from (domain span, chart width), so
        # without a width it can't tell whether this 25h13m domain is safe.
        ax = _axis()
        data = [{"date": "2025-01-15T08:30:00"}, {"date": "2025-01-16T09:45:00"}]
        _, ax_vl, _tu = build_cartesian_x_encoding(
            data, "date", ax, {}, "bar", outer_chart_width=600.0
        )
        assert "labelExpr" in ax_vl
        assert "minutes(datum.value) !== 0" in ax_vl["labelExpr"]

    def test_yearquarter_label_expr_references_quarter(self):
        ax = _axis(time_unit="yearquarter")
        _, ax_vl, _tu = build_cartesian_x_encoding(
            _rows(_YEARQUARTER_DATES), "date", ax, {}, "bar"
        )
        assert "labelExpr" in ax_vl
        # Quarter label mentions Q
        assert "'Q'" in ax_vl["labelExpr"] or "floor(" in ax_vl["labelExpr"]

    def test_existing_properties_in_ax_vl_are_preserved(self):
        ax = _axis(time_unit="yearmonth")
        _, ax_vl, _tu = build_cartesian_x_encoding(
            _rows(_YEARMONTH_DATES), "date", ax, {"grid": True, "ticks": False}, "bar"
        )
        assert ax_vl["grid"] is True
        assert ax_vl["ticks"] is False

    def test_full_vertical_label_angle_inlines_year_context(self):
        # A fully vertical labelAngle rotates the stacked second row into a
        # horizontal offset that spills into the neighboring tick's label —
        # the year must flow inline on one row instead.
        ax = _axis(time_unit="yearmonth")
        _, ax_vl, _tu = build_cartesian_x_encoding(
            _rows(_YEARMONTH_DATES), "date", ax, {"labelAngle": -90}, "bar"
        )
        assert "labelExpr" in ax_vl
        assert "[" not in ax_vl["labelExpr"]

    def test_moderate_label_angle_keeps_two_row_array(self):
        ax = _axis(time_unit="yearmonth")
        _, ax_vl, _tu = build_cartesian_x_encoding(
            _rows(_YEARMONTH_DATES), "date", ax, {"labelAngle": -45}, "bar"
        )
        assert "labelExpr" in ax_vl
        assert "[" in ax_vl["labelExpr"]


# ---------------------------------------------------------------------------
# build_cartesian_x_encoding — sub-day clock default
# ---------------------------------------------------------------------------

# One calendar day of hourly instants — a single-midnight domain.
_ONE_DAY_HOURLY = _rows([f"2026-08-11T{h:02d}:00:00" for h in range(24)])


class TestBuildCartesianXEncodingSubdayClock:
    """The sub-day clock vocabulary is the default on a continuous temporal axis."""

    def test_default_clock_is_the_noon_midnight_words(self):
        ax = _axis()
        _, ax_vl, tu = build_cartesian_x_encoding(
            _ONE_DAY_HOURLY, "date", ax, {}, "line", outer_chart_width=600.0
        )
        assert tu is None
        assert "labelExpr" in ax_vl
        assert "'Midnight'" in ax_vl["labelExpr"]
        assert "'Noon'" in ax_vl["labelExpr"]

    def test_clock_24_authored_is_a_plain_format(self):
        ax = _axis(clock=24)
        _, ax_vl, _tu = build_cartesian_x_encoding(
            _ONE_DAY_HOURLY, "date", ax, {}, "line", outer_chart_width=600.0
        )
        assert ax_vl["labelExpr"] == "timeFormat(datum.value, '%H:%M')"

    def test_narrow_card_falls_back_to_compact_numerals(self):
        ax = _axis()
        _, ax_vl, _tu = build_cartesian_x_encoding(
            _ONE_DAY_HOURLY,
            "date",
            ax,
            {},
            "line",
            outer_chart_width=300,
        )
        assert "labelExpr" in ax_vl
        assert "Midnight" not in ax_vl["labelExpr"]
        assert "Noon" not in ax_vl["labelExpr"]

    def test_wide_card_keeps_the_words(self):
        ax = _axis()
        _, ax_vl, _tu = build_cartesian_x_encoding(
            _ONE_DAY_HOURLY,
            "date",
            ax,
            {},
            "line",
            outer_chart_width=700,
        )
        assert "'Midnight'" in ax_vl["labelExpr"]

    def test_authored_scale_domain_drives_the_gate_not_the_data_extent(self):
        """An authored axis_x.scale.continuous.domain is what
        Vega actually renders across — cartesian_x_scale_domain applies it to
        the compiled spec AFTER this function decides. 11 points at 1-minute
        spacing (09:00 -> 09:10) clear the tick-cadence gate on the DATA's
        own extent, but the AUTHORED domain here runs into a second day
        (two midnights), which the gate must see to add the date-context
        row — otherwise two ticks read the same bare clock text for
        different days with no date row to disambiguate them (rule 5's
        exact hazard).
        """
        data = _rows(
            [f"2026-08-11T09:{m:02d}:00" for m in range(11)],
        )
        ax = _axis_with_scale(
            scale_domain=("2026-08-11T00:00:00", "2026-08-12T06:00:00")
        )
        _, ax_vl, _tu = build_cartesian_x_encoding(
            data, "date", ax, {}, "line", outer_chart_width=600.0
        )
        assert "labelExpr" in ax_vl
        assert ax_vl["labelExpr"].startswith("[")
        assert "'%b %-d'" in ax_vl["labelExpr"]

    def test_date_only_authored_domain_mixed_with_naive_data_bails(self):
        """A date-only authored domain (Vega parses
        date-only strings as UTC midnight) framing naive sub-day data (Vega
        parses those as local) is the same "neither accessor can get right"
        hazard the values-only mixed-clock bail already exists to catch --
        the gate must compare the domain against the data, not just the
        domain against itself.
        """
        data = _rows(
            [
                "2026-08-11T06:00:00",
                "2026-08-11T12:00:00",
                "2026-08-11T20:00:00",
            ]
        )
        ax = _axis_with_scale(scale_domain=("2026-08-11", "2026-08-12"))
        _, ax_vl, _tu = build_cartesian_x_encoding(
            data, "date", ax, {}, "line", outer_chart_width=600.0
        )
        assert "labelExpr" not in ax_vl

    def test_reserved_chrome_narrows_the_predicted_tick_count(self):
        """Passing our own known chrome reservation (e.g. the endpoint-label
        rail) narrows the predicted tick count further than the card width
        alone would, correctly withholding the vocabulary for a domain that
        card width alone would still (over-confidently) admit.
        """
        start = dt.datetime(2026, 8, 11, 0, 0)
        data = _rows(
            [
                (start + dt.timedelta(hours=h)).isoformat()
                for h in range(0, int(9.5 * 24) + 1, 3)
            ]
        )
        ax = _axis()
        _, no_reserve, _tu = build_cartesian_x_encoding(
            data, "date", ax, {}, "line", outer_chart_width=650.0
        )
        assert "labelExpr" in no_reserve
        _, with_reserve, _tu = build_cartesian_x_encoding(
            data,
            "date",
            ax,
            {},
            "line",
            outer_chart_width=650.0,
            plot_width=600.0,
        )
        assert "labelExpr" not in with_reserve

    def test_word_vocabulary_boundary_at_600px(self):
        """Real-render measurements (a plain line chart, no reserved chrome,
        TZ=UTC, hourly data at 600px): an 8-day domain renders the words cleanly, but 10.0/10.4/11.0
        days all rendered "Midnight" on every tick before this fix (Vega's
        own generator lands on day-grain ticks there) — vl_convert's own
        ``autosize: fit`` reserves y-axis chrome Python can't see before
        compiling the spec, so the width-only prediction has to assume a
        conservative allowance for it (``_ESTIMATED_Y_AXIS_CHROME_PX``)
        rather than the bare card width. The identical 9.5-day domain, which
        rendered correctly before this fix (20 ticks, no duplicates), now
        conservatively falls back to Vega's own default format rather than
        the vocabulary — a deliberate trade for never wrongly applying it.
        """
        start = dt.datetime(2026, 8, 11, 0, 0)

        def applies(span_days: float) -> bool:
            data = _rows(
                [
                    (start + dt.timedelta(hours=h)).isoformat()
                    for h in range(0, int(span_days * 24) + 1, 3)
                ]
            )
            _, ax_vl, _tu = build_cartesian_x_encoding(
                data, "date", _axis(), {}, "line", outer_chart_width=600.0
            )
            return "labelExpr" in ax_vl

        assert applies(8.0) is True
        assert applies(10.0) is False
        assert applies(10.4) is False
        assert applies(11.0) is False

    def test_authored_tick_count_overrides_the_width_prediction(self):
        """An authored ticks.count is the axis's real tick count — the gate
        must read it instead of predicting from width. A 3-day hourly domain
        applies the vocabulary by default (Vega draws Midnight/6am/Noon/6pm
        ticks), but thinning to 3 ticks makes every one of them a day
        boundary, so the vocabulary must withhold rather than print
        "Midnight" four times over.
        """
        start = dt.datetime(2026, 8, 11, 0, 0)
        data = _rows(
            [(start + dt.timedelta(hours=h)).isoformat() for h in range(3 * 24 + 1)]
        )
        default_ax = _axis()
        _, default_vl, _tu = build_cartesian_x_encoding(
            data, "date", default_ax, {}, "line", outer_chart_width=600.0
        )
        assert "labelExpr" in default_vl
        thinned_ax = _axis(ticks_count=3)
        _, thinned_vl, _tu = build_cartesian_x_encoding(
            data, "date", thinned_ax, {}, "line", outer_chart_width=600.0
        )
        assert "labelExpr" not in thinned_vl

    def test_authored_tick_time_unit_always_bails(self):
        """ticks.time_unit only ever names a day-grain-or-coarser interval
        (_TEMPORAL_TICK_INTERVAL's finest entry is "day") — every tick it
        produces lands on local midnight or coarser, so the vocabulary can
        never apply once it's authored, regardless of span or width.
        """
        ax = _axis(ticks_time_unit="yearmonthdate")
        _, ax_vl, _tu = build_cartesian_x_encoding(
            _ONE_DAY_HOURLY, "date", ax, {}, "line", outer_chart_width=600.0
        )
        assert "labelExpr" not in ax_vl

    def test_authored_expr_wins_over_the_default(self):
        # outer_chart_width is required: without it the subday gate's
        # tick_count is unresolvable and the guarded block never runs at
        # all, so the "authored wins" assertion below would pass whether or
        # not the precedence check inside the block is actually correct.
        ax = _axis()
        _, ax_vl, _tu = build_cartesian_x_encoding(
            _ONE_DAY_HOURLY,
            "date",
            ax,
            {"labelExpr": "datum.value"},
            "line",
            outer_chart_width=600.0,
        )
        assert ax_vl["labelExpr"] == "datum.value"

    def test_authored_format_wins_over_the_default(self):
        # See test_authored_expr_wins_over_the_default's comment on
        # outer_chart_width — required for this test to exercise anything.
        ax = _axis()
        _, ax_vl, _tu = build_cartesian_x_encoding(
            _ONE_DAY_HOURLY,
            "date",
            ax,
            {"format": "%H"},
            "line",
            outer_chart_width=600.0,
        )
        assert "labelExpr" not in ax_vl
        assert ax_vl["format"] == "%H"

    def test_daily_continuous_data_with_no_subday_component_is_untouched(self):
        # Calendar-only continuous data (no hour/minute/second) is not this
        # vocabulary's concern. Same weekday, 63 days apart (> the 14-day
        # weekly-cadence ceiling): detect_time_unit returns None because no
        # bucketed grain fits, not because of a sub-day component. A width is
        # passed so the assertion actually exercises _has_subday_component
        # rather than short-circuiting on the "no tick count to gate on" bail.
        ax = _axis()
        data = _rows(["2025-01-06", "2025-03-10"])
        _, ax_vl, tu = build_cartesian_x_encoding(
            data, "date", ax, {}, "line", outer_chart_width=600.0
        )
        assert tu is None
        assert "labelExpr" not in ax_vl


# ---------------------------------------------------------------------------
# build_cartesian_x_encoding — authored ticks.count on temporal axes
# ---------------------------------------------------------------------------


class TestBuildCartesianXEncodingTickCount:
    """Authored axis_x.ticks.count maps to VL axis.tickCount on temporal scales.

    Continuous or bucketed-but-density-flipped x axes both resolve to
    vl_type == "temporal" (see TestBuildCartesianXEncodingDensityGate) — count
    applies uniformly to both. Quantitative axes get the same passthrough; see
    TestBuildCartesianXEncodingQuantitativeCadence. Ordinal axes are the ones
    out of scope: VL tickCount has no meaning against a discrete domain.
    """

    def test_continuous_temporal_with_count_sets_tick_count(self):
        ax = _axis(ticks_count=14)
        data = [{"ts": "2025-01-15T08:30:00"}, {"ts": "2025-01-16T09:45:00"}]
        vl_type, ax_vl, _tu = build_cartesian_x_encoding(data, "ts", ax, {}, "bar")
        assert vl_type == "temporal"
        assert ax_vl["tickCount"] == 14

    def test_continuous_temporal_no_authored_count_omits_tick_count(self):
        ax = _axis()  # ticks_count defaults to None
        data = [{"ts": "2025-01-15T08:30:00"}, {"ts": "2025-01-16T09:45:00"}]
        _, ax_vl, _tu = build_cartesian_x_encoding(data, "ts", ax, {}, "bar")
        assert "tickCount" not in ax_vl

    def test_density_flipped_bucketed_monthly_with_count_sets_tick_count(self):
        # High-density yearmonth flips to temporal (density gate) — count must
        # still take, and the smart yearmonth labelExpr must still be present.
        ax = _axis(time_unit="yearmonth", ticks_count=14)
        dates = _monthly_dates(480)
        vl_type, ax_vl, _tu = build_cartesian_x_encoding(
            _rows(dates), "date", ax, {}, "bar"
        )
        assert vl_type == "temporal"
        assert ax_vl["tickCount"] == 14
        assert "labelExpr" in ax_vl
        assert "utcmonth" in ax_vl["labelExpr"]

    def test_ordinal_axis_with_count_ignores_tick_count(self):
        # Low-density yearmonth stays ordinal — tickCount is a temporal-scale
        # concept only; ordinal scales get no tickCount emission.
        ax = _axis(time_unit="yearmonth", ticks_count=14)
        dates = _monthly_dates(24)
        vl_type, ax_vl, _tu = build_cartesian_x_encoding(
            _rows(dates), "date", ax, {}, "bar"
        )
        assert vl_type == "ordinal"
        assert "tickCount" not in ax_vl

    def test_existing_tick_count_not_overwritten(self):
        ax = _axis(ticks_count=14)
        data = [{"ts": "2025-01-15T08:30:00"}, {"ts": "2025-01-16T09:45:00"}]
        _, ax_vl, _tu = build_cartesian_x_encoding(
            data, "ts", ax, {"tickCount": 6}, "bar"
        )
        assert ax_vl["tickCount"] == 6


# ---------------------------------------------------------------------------
# build_cartesian_x_encoding — step-anchored tick cadence (interval/step)
# ---------------------------------------------------------------------------


class TestBuildCartesianXEncodingIntervalStep:
    """Authored axis_x.ticks.time_unit/step map to VL axis.tickCount: {interval, step}.

    Sibling to TestBuildCartesianXEncodingTickCount's plain ``count`` int, but
    unlike ``count``, an authored time_unit on a non-temporal (ordinal) x-axis
    raises rather than silently no-opping. The sibling boundary checks live at
    compile time (``_bake_cartesian_axes``: time_unit/step on axis_y) and on
    the model (``time_unit`` + ``count`` together). A bare ``step`` is gated
    at render alongside this one — data-dependent vl_type is only known here.
    """

    def test_continuous_temporal_with_interval_only_sets_tick_count_dict(self):
        ax = _axis(ticks_time_unit="year")
        data = [{"ts": "2025-01-15T08:30:00"}, {"ts": "2025-01-16T09:45:00"}]
        vl_type, ax_vl, _tu = build_cartesian_x_encoding(data, "ts", ax, {}, "line")
        assert vl_type == "temporal"
        assert ax_vl["tickCount"] == {"interval": "year"}

    def test_continuous_temporal_with_interval_and_step_sets_both(self):
        ax = _axis(ticks_time_unit="year", ticks_step=5)
        data = [{"ts": "2025-01-15T08:30:00"}, {"ts": "2025-01-16T09:45:00"}]
        vl_type, ax_vl, _tu = build_cartesian_x_encoding(data, "ts", ax, {}, "line")
        assert vl_type == "temporal"
        assert ax_vl["tickCount"] == {"interval": "year", "step": 5}

    def test_density_flipped_bucketed_yearly_with_interval_sets_tick_count(self):
        ax = _axis(time_unit="year", ticks_time_unit="year", ticks_step=10)
        dates = [f"{y}-01-01" for y in range(1900, 2026)]  # 126 buckets > 60
        vl_type, ax_vl, _tu = build_cartesian_x_encoding(
            _rows(dates), "date", ax, {}, "bar"
        )
        assert vl_type == "temporal"
        assert ax_vl["tickCount"] == {"interval": "year", "step": 10}

    def test_ordinal_axis_with_interval_raises(self):
        # Low-density yearmonth stays ordinal on a bar chart — interval/step
        # require a genuinely continuous temporal scale, so this must raise
        # (not silently no-op like plain ``count`` does in the sibling test).
        ax = _axis(time_unit="yearmonth", ticks_time_unit="yearmonth")
        dates = _monthly_dates(24)
        with pytest.raises(ChartDataError, match="ticks.time_unit"):
            build_cartesian_x_encoding(_rows(dates), "date", ax, {}, "bar")

    def test_nominal_axis_with_interval_raises(self):
        ax = _axis(ticks_time_unit="year")
        data = [{"category": "foo"}, {"category": "bar"}]
        with pytest.raises(ChartDataError, match="ticks.time_unit"):
            build_cartesian_x_encoding(data, "category", ax, {}, "line")

    def test_existing_tick_count_not_overwritten_by_interval(self):
        ax = _axis(ticks_time_unit="year")
        data = [{"ts": "2025-01-15T08:30:00"}, {"ts": "2025-01-16T09:45:00"}]
        _, ax_vl, _tu = build_cartesian_x_encoding(
            data, "ts", ax, {"tickCount": 6}, "line"
        )
        assert ax_vl["tickCount"] == 6


# ---------------------------------------------------------------------------
# build_cartesian_x_encoding — quantitative tick cadence (count / step)
# ---------------------------------------------------------------------------


class TestBuildCartesianXEncodingQuantitativeCadence:
    """A quantitative x-axis reaches VL's own numeric cadence properties.

    ``count`` passes through as ``tickCount`` (advisory — Vega rounds to a
    nearby nice step) and a bare ``step`` as ``tickMinStep`` (a hard floor).
    ``step`` without ``time_unit`` is meaningless anywhere else, so it raises
    on a temporal or ordinal axis rather than no-opping — the same reasoning
    that makes ``ticks.time_unit`` raise off a temporal scale.
    """

    def test_quantitative_with_count_sets_tick_count(self):
        ax = _axis(ticks_count=4)
        data = [{"customers": float(i) * 140.0} for i in range(30)]
        vl_type, ax_vl, _tu = build_cartesian_x_encoding(
            data, "customers", ax, {}, "scatter"
        )
        assert vl_type == "quantitative"
        assert ax_vl["tickCount"] == 4

    def test_quantitative_with_step_sets_tick_min_step(self):
        ax = _axis(ticks_step=1000)
        data = [{"customers": float(i) * 140.0} for i in range(30)]
        vl_type, ax_vl, _tu = build_cartesian_x_encoding(
            data, "customers", ax, {}, "scatter"
        )
        assert vl_type == "quantitative"
        assert ax_vl["tickMinStep"] == 1000

    def test_quantitative_with_count_and_step_emits_both(self):
        """Not mutually exclusive: ``count`` is VL's target, ``step`` its floor.
        Only ``time_unit`` + ``count`` is a genuine conflict."""
        ax = _axis(ticks_count=4, ticks_step=1000)
        data = [{"customers": float(i) * 140.0} for i in range(30)]
        _, ax_vl, _tu = build_cartesian_x_encoding(data, "customers", ax, {}, "scatter")
        assert ax_vl["tickCount"] == 4
        assert ax_vl["tickMinStep"] == 1000

    def test_quantitative_without_cadence_emits_neither(self):
        ax = _axis()
        data = [{"customers": float(i) * 140.0} for i in range(30)]
        _, ax_vl, _tu = build_cartesian_x_encoding(data, "customers", ax, {}, "scatter")
        assert "tickCount" not in ax_vl
        assert "tickMinStep" not in ax_vl

    def test_temporal_with_bare_step_raises_pointing_at_time_unit(self):
        """``step`` alone names no calendar cadence. The message must send the
        author to ``time_unit``, the field that works here."""
        ax = _axis(ticks_step=1000)
        data = [{"ts": "2025-01-15T08:30:00"}, {"ts": "2025-01-16T09:45:00"}]
        with pytest.raises(ChartDataError, match="ticks.step") as exc:
            build_cartesian_x_encoding(data, "ts", ax, {}, "line")
        assert "ticks.time_unit" in str(exc.value)

    def test_ordinal_with_bare_step_raises_without_recommending_count(self):
        """An ordinal domain has no numeric interval — and ``count`` is inert
        there too, so the message must not send the author to it."""
        ax = _axis(time_unit="yearmonth", ticks_step=1000)
        dates = _monthly_dates(24)
        with pytest.raises(ChartDataError, match="ticks.step") as exc:
            build_cartesian_x_encoding(_rows(dates), "date", ax, {}, "bar")
        assert "ticks.count" not in str(exc.value)

    def test_ordinal_with_count_still_silently_ignored(self):
        """Unchanged: ``count`` no-ops on ordinal rather than raising."""
        ax = _axis(time_unit="yearmonth", ticks_count=14)
        dates = _monthly_dates(24)
        vl_type, ax_vl, _tu = build_cartesian_x_encoding(
            _rows(dates), "date", ax, {}, "bar"
        )
        assert vl_type == "ordinal"
        assert "tickCount" not in ax_vl


# ---------------------------------------------------------------------------
# build_cartesian_x_encoding — label-only thinning
# ---------------------------------------------------------------------------


class TestBuildCartesianXEncodingLabelThinning:
    """Resolved label grains constrain ticks; visibility thinning does not."""

    def test_monthly_bar_thinned_to_quarters_keeps_monthly_ticks(self):
        ax = _axis(time_unit="yearmonth")
        dates = _monthly_dates(12, start=(2025, 1))
        _, ax_vl, _tu = build_cartesian_x_encoding(
            _rows(dates),
            "date",
            ax,
            {"ticks": False},
            "bar",
            visibility_time_unit="yearquarter",
        )
        assert ax_vl["values"] == dates
        assert ax_vl["ticks"] is True
        assert "'%b'" in ax_vl["labelExpr"]
        assert "utcmonth(toDate(datum.value)) % 3 === 0" in ax_vl["labelExpr"]

    def test_authored_quarter_format_keeps_quarter_vocabulary(self):
        ax = _axis(time_unit="yearmonth", label_time_unit="yearquarter")
        dates = _monthly_dates(12, start=(2025, 1))
        _, ax_vl, _tu = build_cartesian_x_encoding(_rows(dates), "date", ax, {}, "bar")
        assert ax_vl["values"] == dates[::3]
        assert ax_vl["ticks"] is True
        assert "'Q'" in ax_vl["labelExpr"]
        assert "'%b'" not in ax_vl["labelExpr"]

    def test_authored_quarter_grain_anchors_at_its_own_grain_even_when_visibility_promotes_further(
        self,
    ):
        """An authored coarser grain (``label_time_unit: yearquarter``) sets
        ``label_tick_cadence`` and, per ``default_label_expr_for``'s
        ``anchor_grain`` docstring, the anchor comparison must run at that
        authored grain (``%Y-%m``) even when render-local ladder promotion
        (``visibility_time_unit``) thins the label cadence further, to
        ``year``. Comparing at the coarser visibility grain instead would
        match every tick inside the anchor's year, not just the anchor's own
        quarter -- the exact defect ``anchor_grain`` exists to prevent."""
        ax = _axis(time_unit="yearmonth", label_time_unit="yearquarter")
        dates = _monthly_dates(48, start=(2025, 1))
        _, ax_vl, _tu = build_cartesian_x_encoding(
            _rows(dates),
            "date",
            ax,
            {},
            "bar",
            visibility_time_unit="year",
        )
        assert "'%Y-%m') === " in ax_vl["labelExpr"]
        assert "'%Y') === " not in ax_vl["labelExpr"]

    def test_same_grain_visibility_keeps_ticks_off_and_full_values(self):
        # Same-grain visibility may still parity-skip label text later, but it
        # does not remove ticks.
        ax = _axis(time_unit="yearmonth")
        dates = _monthly_dates(5, start=(2025, 1))
        _, ax_vl, _tu = build_cartesian_x_encoding(
            _rows(dates),
            "date",
            ax,
            {"ticks": False},
            "bar",
            visibility_time_unit="yearmonth",
        )
        assert ax_vl["values"] == dates
        assert ax_vl["ticks"] is False

    def test_weekly_bar_promoted_to_month_labels_and_ticks(self):
        # Render-local (non-authored) promotion restores ticks for every
        # weekly band — `values` never re-grains to just the month openers;
        # only the label TEXT thins via labelExpr gating. See
        # type_inference.py's label_tick_cadence vs visibility_thinned split.
        ax = _axis()  # no authored time_unit or label_time_unit
        dates = [
            (dt.date(2023, 1, 2) + dt.timedelta(weeks=i)).isoformat() for i in range(20)
        ]
        _, ax_vl, tu = build_cartesian_x_encoding(
            _rows(dates),
            "date",
            ax,
            {"ticks": False},
            "bar",
            format_time_unit="yearmonth",
            visibility_time_unit="yearmonth",
        )
        assert tu == "yearweek"
        assert ax_vl["ticks"] is True
        assert ax_vl["values"] == dates
        assert "'%b'" in ax_vl["labelExpr"]
        assert "W%V" not in ax_vl["labelExpr"]
        assert "utcdate(toDate(datum.value)) <= 7" in ax_vl["labelExpr"]

    def test_line_authored_label_unit_uses_source_openers_as_ticks(self):
        ax = _axis(time_unit="yearmonth", label_time_unit="yearquarter")
        dates = _monthly_dates(12, start=(2025, 1))
        vl_type, ax_vl, _tu = build_cartesian_x_encoding(
            _rows(dates), "date", ax, {}, "line"
        )
        assert vl_type == "temporal"
        assert ax_vl["values"] == [
            "2025-01-01",
            "2025-04-01",
            "2025-07-01",
            "2025-10-01",
        ]
        assert "tickCount" not in ax_vl

    @pytest.mark.parametrize(
        ("time_unit", "expected"),
        [
            ("year", {"interval": "year", "step": 1}),
            ("yearquarter", {"interval": "month", "step": 3}),
            ("yearmonth", {"interval": "month", "step": 1}),
        ],
    )
    def test_temporal_default_tick_interval_matches_encoding_grain(
        self, time_unit: str, expected: dict[str, object]
    ) -> None:
        ax = _axis(time_unit=time_unit)
        dates = _monthly_dates(12, start=(2025, 1))
        _, ax_vl, _tu = build_cartesian_x_encoding(_rows(dates), "date", ax, {}, "line")
        assert ax_vl["tickCount"] == expected

    def test_temporal_auto_native_daily_labels_keep_daily_tick_interval(self) -> None:
        ax = _axis(time_unit="yearmonthdate")
        dates = _daily_dates(30)
        _, ax_vl, _tu = build_cartesian_x_encoding(
            _rows(dates),
            "date",
            ax,
            {},
            "line",
            format_time_unit="yearmonthdate",
        )
        assert "values" not in ax_vl
        assert ax_vl["tickCount"] == {"interval": "day", "step": 1}
        assert "'%-d'" in ax_vl["labelExpr"]

    def test_daily_bar_promoted_to_mondays_changes_ticks_with_format(self) -> None:
        # Render-local promotion: every daily band keeps its tick (`values`
        # stays the full daily list); only the label text thins to Mondays.
        ax = _axis(time_unit="yearmonthdate")
        dates = _daily_dates(21)
        _, ax_vl, _tu = build_cartesian_x_encoding(
            _rows(dates),
            "date",
            ax,
            {"ticks": False},
            "bar",
            format_time_unit="yearweek",
            visibility_time_unit="yearweek",
        )
        assert ax_vl["values"] == dates
        assert ax_vl["ticks"] is True
        assert "utcday(toDate(datum.value)) === 1" in ax_vl["labelExpr"]

    def test_continuous_daily_axis_matches_weekly_apostrophe_year_vocabulary(
        self,
    ) -> None:
        ax = _axis(time_unit="yearmonthdate")
        dates = _daily_dates(10)
        _, ax_vl, _tu = build_cartesian_x_encoding(
            _rows(dates),
            "date",
            ax,
            {},
            "line",
            format_time_unit="yearmonthdate",
        )
        assert "'%-d'" in ax_vl["labelExpr"]
        assert '"\'"' in ax_vl["labelExpr"]
        assert "'%y'" in ax_vl["labelExpr"]

    def test_temporal_auto_promoted_month_labels_use_real_openers_not_tickcount(
        self,
    ) -> None:
        # Render-local promotion (no authored labels.time_unit): the axis
        # DOES collapse to an explicit month-opener `values` list. Relying on
        # VL's own tickCount interval instead is what dropped the domain's
        # leading label whenever its date isn't itself a calendar boundary
        # (e.g. a weekly series opening on a non-1st-of-month date) — Vega's
        # month-interval generator only places ticks on true calendar
        # boundaries within the domain, so a non-boundary domain start never
        # gets a tick at all. Explicit `values`, built from the real
        # calendar-bucket openers, always includes it.
        ax = _axis(time_unit="yearweek")
        dates = [
            (dt.date(2023, 1, 2) + dt.timedelta(weeks=i)).isoformat() for i in range(70)
        ]
        _, ax_vl, _tu = build_cartesian_x_encoding(
            _rows(dates),
            "date",
            ax,
            {},
            "line",
            format_time_unit="yearmonth",
            visibility_time_unit="yearmonth",
        )
        # Thinned to one opener per represented month (16), not the full
        # 70-week list — a regression back to the untinned week list would
        # still pass an values[0]-only check.
        assert len(ax_vl["values"]) == 16
        assert ax_vl["values"][0] == dates[0]
        assert ax_vl["values"][1] == "2023-02-06"
        assert "tickCount" not in ax_vl
        assert "'%b'" in ax_vl["labelExpr"]

    def test_bar_data_untouched_when_values_thinned(self):
        # Bar band positions come from the ordinal scale DOMAIN (built from
        # `data` by the caller), never from axis.values — build_cartesian_x_
        # encoding must never filter or otherwise mutate `data` itself, so
        # restricting the emitted values can never drop a bar.
        ax = _axis(time_unit="yearmonth", label_time_unit="yearquarter")
        dates = _monthly_dates(12, start=(2025, 1))
        rows = _rows(dates)
        original_len = len(rows)
        _, ax_vl, _tu = build_cartesian_x_encoding(rows, "date", ax, {}, "bar")
        assert len(ax_vl["values"]) == 4
        assert len(rows) == original_len

    def test_cyclic_label_time_unit_does_not_crash(self):
        # Regression: a cyclic label unit (monthofyear, dayofweek, etc.) is
        # permitted by AxisLabelStyle.label.time_unit's Literal but is NOT
        # a bucketed calendar unit, so it must bypass the smart label path.
        # A bar with a cyclic label unit must render normally: no crash,
        # no ticks override, no thinned values.
        for cyclic_unit in ("monthofyear", "dayofweek", "dayofmonth", "hourofday"):
            ax = _axis(time_unit="yearmonth", label_time_unit=cyclic_unit)
            dates = _monthly_dates(12, start=(2025, 1))
            _, ax_vl, _tu = build_cartesian_x_encoding(
                _rows(dates), "date", ax, {"ticks": False}, "bar"
            )
            # Must not crash; thinning must not engage for a cyclic label unit.
            assert ax_vl.get("ticks") is False, (
                f"ticks must stay off for cyclic label unit {cyclic_unit!r}"
            )
            assert ax_vl.get("values") == dates, (
                f"values must remain the full grain for cyclic label unit {cyclic_unit!r}"
            )


# ---------------------------------------------------------------------------
# build_cartesian_x_encoding — mark_type == "heatmap" (nominal grid axis)
# ---------------------------------------------------------------------------


class TestBuildCartesianXEncodingHeatmapMarkType:
    """``resolve_cartesian_x_type``'s ``mark_type == "heatmap"`` branch pins
    the VL type to nominal (heatmap's grid axis must never promote to a
    band/ordinal or continuous scale) while still getting the shared
    bucketed-calendar labelExpr/tick-thinning that the ordinal branch already
    computes for bar."""

    def test_monthly_nominal_type_is_kept_not_promoted_to_ordinal(self):
        ax = _axis(time_unit="yearmonth")
        vl_type, ax_vl, tu = build_cartesian_x_encoding(
            _rows(_YEARMONTH_DATES),
            "date",
            ax,
            {},
            "heatmap",
        )
        assert vl_type == "nominal"
        assert tu == "yearmonth"

    def test_monthly_nominal_gets_smart_month_label_expr(self):
        ax = _axis(time_unit="yearmonth")
        _, ax_vl, _tu = build_cartesian_x_encoding(
            _rows(_YEARMONTH_DATES),
            "date",
            ax,
            {},
            "heatmap",
        )
        assert "'%b'" in ax_vl["labelExpr"]
        # Domain value stays the raw ISO date — only the painted label changes.
        assert ax_vl["values"] == _YEARMONTH_DATES

    def test_weekly_nominal_thins_ticks_and_labels_to_month_openers(self):
        # 52 weekly bands promoted to 12 month-opener ticks — mirrors
        # test_weekly_bar_promoted_to_month_labels_and_ticks, forced nominal.
        # Unlike bar/histogram, heatmap has no _LABEL_THINNING_TICK_MARK_TYPES
        # restoration, so a render-local promotion collapses `values` (ticks
        # and labels stay in lockstep) instead of keeping every band.
        ax = _axis()
        dates = [
            (dt.date(2023, 1, 2) + dt.timedelta(weeks=i)).isoformat() for i in range(20)
        ]
        _, ax_vl, tu = build_cartesian_x_encoding(
            _rows(dates),
            "date",
            ax,
            {},
            "heatmap",
            format_time_unit="yearmonth",
            visibility_time_unit="yearmonth",
        )
        assert tu == "yearweek"
        assert ax_vl["values"] == [
            "2023-01-02",
            "2023-02-06",
            "2023-03-06",
            "2023-04-03",
            "2023-05-01",
        ]
        assert "'%b'" in ax_vl["labelExpr"]

    def test_non_temporal_nominal_data_untouched(self):
        # Numeric/non-date heatmap x (e.g. hour_of_day) gets no vocabulary.
        ax = _axis()
        data = [{"date": "Core"}, {"date": "Growth"}]
        vl_type, ax_vl, tu = build_cartesian_x_encoding(data, "date", ax, {}, "heatmap")
        assert vl_type == "nominal"
        assert tu is None
        assert "labelExpr" not in ax_vl
        assert "values" not in ax_vl

    def test_no_data_stays_nominal(self):
        ax = _axis()
        vl_type, ax_vl, _tu = build_cartesian_x_encoding([], "date", ax, {}, "heatmap")
        assert vl_type == "nominal"

    def test_axis_type_ordinal_authored_still_stays_nominal(self):
        # heatmap's grid axis wins outright over an authored axis_x.type — the
        # mark_type dispatch short-circuits before the authored-type branches
        # below it are ever consulted for this mark_type.
        ax = _axis(axis_type="ordinal")
        data = [{"date": "Core"}, {"date": "Growth"}]
        vl_type, _, _tu = build_cartesian_x_encoding(data, "date", ax, {}, "heatmap")
        assert vl_type == "nominal"


# ---------------------------------------------------------------------------
# y_zero_scale
# ---------------------------------------------------------------------------


class TestYZeroScale:
    """y_zero_scale returns the correct VL scale dict."""

    def test_scale_zero_true_no_ticks_pins_no_domain_min(self):
        """No ladder means no rung to pin: a literal 0.0 floor is legal only
        while the data is non-negative, and on an all-negative axis it pins 0
        as the BOTTOM of the domain, collapsing every mark onto one pixel row.
        `nice: False` stands in for it, because an explicit domainMin is what
        suppresses Vega-Lite's default nice-rounding and the top edge must
        stay at the exact data max."""
        ax = _axis_with_scale(scale_zero=True)
        result = y_zero_scale(ax)
        assert result == {"nice": False, "zero": True}

    def test_scale_zero_true_with_ticks_uses_first_tick_as_domain_min(self):
        ticks = (0.0, 50_000.0, 100_000.0, 150_000.0, 200_000.0)
        ax = _axis_with_scale(scale_zero=True, tick_values=ticks)
        result = y_zero_scale(ax)
        assert result == {"domainMin": 0.0, "zero": True}

    def test_scale_zero_true_with_nonzero_first_tick(self):
        ticks = (80_000.0, 100_000.0, 120_000.0)
        ax = _axis_with_scale(scale_zero=True, tick_values=ticks)
        result = y_zero_scale(ax)
        assert result == {"domainMin": 80_000.0, "zero": True}

    def test_authored_scale_values_never_source_the_floor(self):
        """The ladder is filtered inside the shared decision, so no caller can
        forget it. An authored `scale.values` populates tick_values verbatim
        and says nothing about the domain, so it must leave the floor
        unpinned."""
        ticks = (25.0, 50.0, 75.0, 100.0)
        ax = _axis_with_scale(
            scale_zero=True, tick_values=ticks, scale_values=list(ticks)
        )
        assert y_zero_scale(ax) == {"nice": False, "zero": True}
        assert zero_anchor_pinned_floor(ax) is None

    def test_scale_zero_false_returns_zero_false(self):
        ax = _axis_with_scale(scale_zero=False)
        result = y_zero_scale(ax)
        assert result == {"zero": False}

    def test_scale_none_returns_zero_false(self):
        ax = _axis_with_scale(scale_zero=None)
        result = y_zero_scale(ax)
        assert result == {"zero": False}

    def test_no_scale_attr_returns_zero_false(self):
        ax = _axis()  # no scale attribute
        result = y_zero_scale(ax)
        assert result == {"zero": False}

    def test_axis_none_returns_zero_false(self):
        result = y_zero_scale(None)
        assert result == {"zero": False}

    def test_scale_type_log_reaches_vl_scale_dict(self):
        """type: log must reach the y-scale dict for line/area/bar — this is
        the bug fix: previously y_zero_scale read only scale.zero, silently
        dropping scale.type on these families (scatter alone honored it)."""
        ax = _axis_with_scale(scale_type="log")
        result = y_zero_scale(ax)
        assert result == {"type": "log", "zero": False}

    def test_scale_type_log_with_base_reaches_vl_scale_dict(self):
        ax = _axis_with_scale(scale_type="log", scale_base=2.0)
        result = y_zero_scale(ax)
        assert result == {"type": "log", "base": 2.0, "zero": False}


class TestVisibilityThreading:
    @staticmethod
    def _months(n: int) -> list[dict[str, str]]:
        return [{"date": f"{2020 + i // 12:04d}-{i % 12 + 1:02d}-01"} for i in range(n)]

    def test_ordinal_path_honors_visibility_time_unit(self) -> None:
        ax = _axis(time_unit="yearmonth")
        vl_type, ax_vl, _tu = build_cartesian_x_encoding(
            self._months(12),
            "date",
            ax,
            {},
            "bar",
            visibility_time_unit="yearquarter",
        )
        assert vl_type == "ordinal"
        assert "utcmonth(toDate(datum.value)) % 3 === 0" in ax_vl["labelExpr"]
        assert (
            "utcFormat(toDate(datum.value), '%Y-%m') === "
            "utcFormat(toDate(\"2020-01-01\"), '%Y-%m')"
        ) in ax_vl["labelExpr"]
        assert "datum.index" not in ax_vl["labelExpr"]

    def test_temporal_quarter_cadence_anchors_by_value_not_index(self) -> None:
        ax = _axis(time_unit="yearmonth")
        max_ordinal = get_chart_rendering().type_inference.max_ordinal_buckets
        n = max_ordinal + 6
        vl_type, ax_vl, _tu = build_cartesian_x_encoding(
            self._months(n),
            "date",
            ax,
            {},
            "bar",
            visibility_time_unit="yearquarter",
        )
        assert vl_type == "temporal"
        assert "utcmonth(toDate(datum.value)) % 3 === 0" in ax_vl["labelExpr"]
        # Jan 2020 is itself a quarter opener — anchor should reference it by value,
        # not by raw tick index (which would fire on whatever month starts the domain).
        assert "datum.index === 0" not in ax_vl["labelExpr"]
        assert "utcFormat(toDate(\"2020-01-01\"), '%Y-%m')" in ax_vl["labelExpr"]

    def test_temporal_same_grain_no_visibility_uses_index_anchor(self) -> None:
        # When encoding and label grain match with no visibility thinning, the
        # guard keeps anchor_value="" so datum.index === 0 fires for the first
        # tick.  A value anchor comparing at %Y-%m could miss Vega-generated
        # ticks; the index anchor is more robust on this path.
        max_ordinal = get_chart_rendering().type_inference.max_ordinal_buckets
        n = max_ordinal + 6
        vl_type, ax_vl, _tu = build_cartesian_x_encoding(
            self._months(n),
            "date",
            _axis(time_unit="yearmonth"),
            {},
            "bar",
        )
        assert vl_type == "temporal"
        assert "datum.index === 0" in ax_vl["labelExpr"]
        # No value-comparison anchor — the anchor is the raw index check.
        assert "utcFormat(toDate(datum.value), '%Y-%m') ===" not in ax_vl["labelExpr"]

    def test_temporal_non_opener_start_anchors_to_first_quarter_opener(self) -> None:
        # Domain starts February 2024 — a non-quarter-opening month.
        # The anchor must reference April 2024 (the first real quarter opener in
        # the domain), not datum.index === 0 (which would always show February's
        # label, overlapping April's).
        max_ordinal = get_chart_rendering().type_inference.max_ordinal_buckets
        n = max_ordinal + 6
        months: list[dict[str, str]] = []
        for i in range(n):
            month_offset = 1 + i  # offset from Jan 2024: i=0 → Feb 2024
            year = 2024 + month_offset // 12
            month = month_offset % 12 + 1
            months.append({"date": f"{year:04d}-{month:02d}-01"})

        ax = _axis(time_unit="yearmonth")
        vl_type, ax_vl, _tu = build_cartesian_x_encoding(
            months,
            "date",
            ax,
            {},
            "bar",
            visibility_time_unit="yearquarter",
        )
        assert vl_type == "temporal"
        assert "utcmonth(toDate(datum.value)) % 3 === 0" in ax_vl["labelExpr"]
        # Must NOT anchor on raw tick index 0 (Feb); must anchor on April (first
        # real quarter opener in the domain) via a calendar-semantic value check.
        assert "datum.index === 0" not in ax_vl["labelExpr"]
        assert "2024-04-01" in ax_vl["labelExpr"]
