"""Render-local temporal label thinning without semantic time-unit mutation."""

from __future__ import annotations

import dataclasses
import datetime
from typing import Any
from unittest.mock import MagicMock, patch

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
        charts, "axis_x", "temporal", chart_type="", label_authored=False
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


class TestTemporalOverlapResolution:
    def test_short_daily_domain_keeps_daily_format(self) -> None:
        from dbt_charts.core.render.chart.emitters._label_overlap import (
            resolve_axis_x_overlap,
        )

        axis = _axis_x_temporal()
        start = datetime.date(2024, 1, 1)
        data = [
            {"x": (start + datetime.timedelta(days=offset)).isoformat()}
            for offset in range(30)
        ]
        measurer = _make_mock_measurer(width_per_char=5.0)
        with patch(
            "dbt_charts.core.render.chart.emitters._label_overlap.get_font_measurer",
            return_value=measurer,
        ):
            layout = resolve_axis_x_overlap(
                axis,
                "x",
                data,
                1.0,
                edge_labels_flushed=False,
                chart_width=600,
                continuous_temporal=False,
            )

        assert layout.format_time_unit == "yearmonthdate"
        assert layout.visibility_time_unit is None
        assert layout.label_overlap == "allow"

    def test_short_weekly_domain_keeps_weekly_format(self) -> None:
        from dbt_charts.core.render.chart.emitters._label_overlap import (
            resolve_axis_x_overlap,
        )

        axis = _axis_x_temporal()
        start = datetime.date(2024, 1, 1)
        data = [
            {"x": (start + datetime.timedelta(weeks=offset)).isoformat()}
            for offset in range(5)
        ]
        measurer = _make_mock_measurer(width_per_char=5.0)
        with patch(
            "dbt_charts.core.render.chart.emitters._label_overlap.get_font_measurer",
            return_value=measurer,
        ):
            layout = resolve_axis_x_overlap(
                axis,
                "x",
                data,
                1.0,
                edge_labels_flushed=False,
                chart_width=600,
                continuous_temporal=False,
            )

        assert layout.format_time_unit == "yearweek"
        assert layout.visibility_time_unit is None
        assert layout.label_overlap == "allow"

    def test_daily_format_steps_through_monday_before_month(self) -> None:
        from dbt_charts.core.render.chart.emitters._label_overlap import (
            resolve_axis_x_overlap,
        )

        axis = _axis_x_temporal()
        start = datetime.date(2024, 1, 1)
        data = [
            {"x": (start + datetime.timedelta(days=offset)).isoformat()}
            for offset in range(60)
        ]
        measurer = _make_mock_measurer(width_per_char=5.0)
        with patch(
            "dbt_charts.core.render.chart.emitters._label_overlap.get_font_measurer",
            return_value=measurer,
        ):
            daily = resolve_axis_x_overlap(
                axis,
                "x",
                data,
                1.0,
                edge_labels_flushed=False,
                chart_width=1200,
                continuous_temporal=False,
            )
            mondays = resolve_axis_x_overlap(
                axis,
                "x",
                data,
                1.0,
                edge_labels_flushed=False,
                chart_width=300,
                continuous_temporal=False,
            )
            months = resolve_axis_x_overlap(
                axis,
                "x",
                data,
                1.0,
                edge_labels_flushed=False,
                chart_width=70,
                continuous_temporal=False,
            )

        assert daily.format_time_unit == "yearmonthdate"
        assert mondays.format_time_unit == "yearweek"
        assert mondays.visibility_time_unit == "yearweek"
        assert months.format_time_unit == "yearmonth"

    def test_weekly_format_promotes_only_when_day_numbers_do_not_fit(self) -> None:
        from dbt_charts.core.render.chart.emitters._label_overlap import (
            resolve_axis_x_overlap,
        )

        axis = _axis_x_temporal()
        start = datetime.date(2024, 1, 1)
        data = [
            {"x": (start + datetime.timedelta(weeks=offset)).isoformat()}
            for offset in range(8)
        ]
        measurer = _make_mock_measurer(width_per_char=5.0)
        with patch(
            "dbt_charts.core.render.chart.emitters._label_overlap.get_font_measurer",
            return_value=measurer,
        ):
            weekly = resolve_axis_x_overlap(
                axis,
                "x",
                data,
                1.0,
                edge_labels_flushed=False,
                chart_width=240,
                continuous_temporal=False,
            )
            monthly = resolve_axis_x_overlap(
                axis,
                "x",
                data,
                1.0,
                edge_labels_flushed=False,
                chart_width=70,
                continuous_temporal=False,
            )

        assert weekly.format_time_unit == "yearweek"
        assert monthly.format_time_unit == "yearmonth"

    def test_first_visible_year_context_can_trigger_month_promotion(self) -> None:
        from dbt_charts.core.render.chart.emitters._label_overlap import (
            resolve_axis_x_overlap,
        )

        axis = _axis_x_temporal()
        start = datetime.date(2024, 1, 29)
        data = [
            {"x": (start + datetime.timedelta(weeks=offset)).isoformat()}
            for offset in range(8)
        ]
        measurer = MagicMock()
        measurer.measure = lambda text, size: 100.0 if "'" in text else 1.0
        with patch(
            "dbt_charts.core.render.chart.emitters._label_overlap.get_font_measurer",
            return_value=measurer,
        ):
            layout = resolve_axis_x_overlap(
                axis,
                "x",
                data,
                1.0,
                edge_labels_flushed=False,
                chart_width=80,
                continuous_temporal=False,
            )

        assert layout.format_time_unit == "yearmonth"
        assert layout.anchor_index == 1

    def test_ordinal_unaligned_daily_axis_keeps_month_resolution(self) -> None:
        from dbt_charts.core.render.chart.emitters._label_overlap import (
            resolve_axis_x_overlap,
        )

        axis = _axis_x_temporal()
        start = datetime.date(2024, 1, 1)
        data = [
            {"x": (start + datetime.timedelta(days=offset)).isoformat()}
            for offset in range(60)
        ]
        measurer = _make_mock_measurer(width_per_char=5.0)
        with patch(
            "dbt_charts.core.render.chart.emitters._label_overlap.get_font_measurer",
            return_value=measurer,
        ):
            layout = resolve_axis_x_overlap(
                axis,
                "x",
                data,
                1.0,
                bucket_aligned_temporal=False,
                edge_labels_flushed=False,
                chart_width=300,
                continuous_temporal=False,
            )

        assert layout.format_time_unit == "yearmonth"

    def test_continuous_daily_axis_steps_to_weekly_day_numbers(self) -> None:
        from dbt_charts.core.render.chart.emitters._label_overlap import (
            resolve_axis_x_overlap,
        )

        axis = _axis_x_temporal()
        start = datetime.date(2024, 1, 1)
        data = [
            {"x": (start + datetime.timedelta(days=offset)).isoformat()}
            for offset in range(60)
        ]
        measurer = _make_mock_measurer(width_per_char=5.0)
        with patch(
            "dbt_charts.core.render.chart.emitters._label_overlap.get_font_measurer",
            return_value=measurer,
        ):
            layout = resolve_axis_x_overlap(
                axis,
                "x",
                data,
                1.0,
                bucket_aligned_temporal=False,
                edge_labels_flushed=False,
                chart_width=300,
                continuous_temporal=True,
            )

        assert layout.visibility_time_unit == "yearweek"
        assert layout.angle == 0.0

    def test_gappy_continuous_weekly_axis_is_measured_over_its_span(self) -> None:
        from dbt_charts.core.render.chart.emitters._label_overlap import (
            resolve_axis_x_overlap,
        )

        axis = _axis_x_temporal()
        start = datetime.date(2024, 1, 1)
        data = [
            {"x": (start + datetime.timedelta(weeks=offset)).isoformat()}
            for offset in range(30)
            if offset % 3 != 1
        ]
        measurer = _make_mock_measurer(width_per_char=5.0)
        with patch(
            "dbt_charts.core.render.chart.emitters._label_overlap.get_font_measurer",
            return_value=measurer,
        ):
            layout = resolve_axis_x_overlap(
                axis,
                "x",
                data,
                1.0,
                bucket_aligned_temporal=False,
                edge_labels_flushed=False,
                chart_width=200,
                continuous_temporal=True,
            )

        assert layout.format_time_unit == "yearmonth"
        assert layout.angle == 0.0

    def test_authored_continuous_daily_labels_measure_the_two_row_shape(self) -> None:
        # Regression: with an authored `labels.time_unit: yearmonthdate` on a
        # continuous axis, width measurement must reflect what _day_label
        # actually draws (two rows, mostly a bare day number) — not the
        # deleted single-row "%-d %b" vocabulary, which is wide enough to
        # wrongly promote or tilt labels that fit at their real width.
        from dbt_charts.core.render.chart.emitters._label_overlap import (
            resolve_axis_x_overlap,
        )

        axis = _axis_x_temporal(label_time_unit="yearmonthdate")
        start = datetime.date(2024, 1, 1)
        data = [
            {"x": (start + datetime.timedelta(days=offset)).isoformat()}
            for offset in range(20)
        ]
        measurer = _make_mock_measurer(width_per_char=5.0)
        with patch(
            "dbt_charts.core.render.chart.emitters._label_overlap.get_font_measurer",
            return_value=measurer,
        ):
            layout = resolve_axis_x_overlap(
                axis,
                "x",
                data,
                1.0,
                bucket_aligned_temporal=False,
                edge_labels_flushed=False,
                chart_width=440,
                continuous_temporal=False,
            )

        assert layout.format_time_unit == "yearmonthdate"
        assert layout.label_overlap == "allow"
        assert layout.angle == 0.0

    def test_weekly_domain_crossing_two_months_keeps_weekly_when_it_fits(self) -> None:
        from dbt_charts.core.render.chart.emitters._label_overlap import (
            resolve_axis_x_overlap,
        )

        axis = _axis_x_temporal()
        start = datetime.date(2024, 1, 1)
        data = [
            {"x": (start + datetime.timedelta(weeks=offset)).isoformat()}
            for offset in range(6)
        ]
        measurer = _make_mock_measurer(width_per_char=5.0)
        with patch(
            "dbt_charts.core.render.chart.emitters._label_overlap.get_font_measurer",
            return_value=measurer,
        ):
            layout = resolve_axis_x_overlap(
                axis,
                "x",
                data,
                1.0,
                edge_labels_flushed=False,
                chart_width=600,
                continuous_temporal=False,
            )

        assert layout.format_time_unit == "yearweek"
        assert layout.visibility_time_unit is None
        assert layout.label_overlap == "allow"

    @pytest.mark.parametrize(
        ("start", "step", "count", "expected_format"),
        [
            (
                datetime.date(2024, 1, 1),
                datetime.timedelta(days=1),
                180,
                "yearweek",
            ),
            (
                datetime.date(2024, 1, 1),
                datetime.timedelta(weeks=1),
                70,
                "yearmonth",
            ),
        ],
    )
    def test_long_submonth_domain_promotes_to_month_format(
        self,
        start: datetime.date,
        step: datetime.timedelta,
        count: int,
        expected_format: str,
    ) -> None:
        from dbt_charts.core.render.chart.emitters._label_overlap import (
            resolve_axis_x_overlap,
        )

        axis = _axis_x_temporal()
        data = [{"x": (start + step * offset).isoformat()} for offset in range(count)]
        measurer = _make_mock_measurer(width_per_char=5.0)
        with patch(
            "dbt_charts.core.render.chart.emitters._label_overlap.get_font_measurer",
            return_value=measurer,
        ):
            layout = resolve_axis_x_overlap(
                axis,
                "x",
                data,
                1.0,
                edge_labels_flushed=False,
                chart_width=600,
                continuous_temporal=False,
            )

        assert layout.format_time_unit == expected_format
        assert layout.visibility_time_unit == expected_format
        assert layout.angle == 0.0

    def test_month_labels_are_not_thinned_when_their_text_does_not_overlap(
        self,
    ) -> None:
        from dbt_charts.core.render.chart.emitters._label_overlap import (
            resolve_axis_x_overlap,
        )

        axis = _axis_x_temporal()
        start = datetime.date(2024, 12, 23)
        data = [
            {"x": (start + datetime.timedelta(weeks=offset)).isoformat()}
            for offset in range(72)
        ]
        measurer = _make_mock_measurer(width_per_char=7.0)
        with patch(
            "dbt_charts.core.render.chart.emitters._label_overlap.get_font_measurer",
            return_value=measurer,
        ):
            layout = resolve_axis_x_overlap(
                axis,
                "x",
                data,
                0.8,
                edge_labels_flushed=True,
                chart_width=564,
                continuous_temporal=False,
            )

        assert layout.format_time_unit == "yearmonth"
        assert layout.visibility_time_unit == "yearmonth"
        assert layout.angle == 0.0

    def test_cyclic_encoding_measures_its_distinct_tick_labels(self) -> None:
        from dbt_charts.core.render.chart.emitters._label_overlap import (
            resolve_axis_x_overlap,
        )

        axis = _axis_x_temporal(encoding_time_unit="monthofyear")
        start = datetime.date(2022, 1, 1)
        data = [
            {"x": (start + datetime.timedelta(days=offset)).isoformat()}
            for offset in range(365)
        ]
        measurer = _make_mock_measurer(width_per_char=5.0)
        with patch(
            "dbt_charts.core.render.chart.emitters._label_overlap.get_font_measurer",
            return_value=measurer,
        ):
            layout = resolve_axis_x_overlap(
                axis,
                "x",
                data,
                1.0,
                edge_labels_flushed=False,
                chart_width=300,
                continuous_temporal=False,
            )

        assert layout.label_overlap == "allow"
        assert layout.angle == 0.0
        assert layout.format_time_unit == "monthofyear"
        assert layout.visibility_time_unit is None

    def test_cyclic_label_unit_uses_generic_skip_then_tilt(self) -> None:
        from dbt_charts.core.render.chart.emitters._label_overlap import (
            resolve_axis_x_overlap,
        )

        axis = _axis_x_temporal(label_time_unit="monthofyear")
        data = [{"x": value} for value in _monthly_dates(24)]
        measurer = _make_mock_measurer(width_per_char=8.0)
        with patch(
            "dbt_charts.core.render.chart.emitters._label_overlap.get_font_measurer",
            return_value=measurer,
        ):
            layout = resolve_axis_x_overlap(
                axis,
                "x",
                data,
                1.0,
                edge_labels_flushed=False,
                chart_width=80,
                continuous_temporal=False,
            )

        assert axis.labels.time_unit == "monthofyear"
        assert layout.visibility_time_unit is None
        assert layout.label_overlap == "parity"
        assert layout.angle != 0.0

    def test_disabled_smart_time_format_still_uses_skip_then_tilt(self) -> None:
        from dbt_charts.core.render.chart.emitters._label_overlap import (
            resolve_axis_x_overlap,
        )

        axis = _axis_x_temporal(label_time_unit="none")
        data = [{"x": value} for value in _monthly_dates(24)]
        measurer = _make_mock_measurer(width_per_char=8.0)
        with patch(
            "dbt_charts.core.render.chart.emitters._label_overlap.get_font_measurer",
            return_value=measurer,
        ):
            layout = resolve_axis_x_overlap(
                axis,
                "x",
                data,
                1.0,
                edge_labels_flushed=False,
                chart_width=80,
                continuous_temporal=False,
            )

        assert layout.visibility_time_unit is None
        assert layout.label_overlap == "parity"
        assert layout.angle != 0.0

    def test_disabled_smart_time_format_measures_raw_bucket_labels(self) -> None:
        from dbt_charts.core.render.chart.emitters._label_overlap import (
            resolve_axis_x_overlap,
        )

        axis = _axis_x_temporal(label_time_unit="none")
        data = [{"x": value} for value in _monthly_dates(24)]
        measurer = _make_mock_measurer(width_per_char=5.0)
        with patch(
            "dbt_charts.core.render.chart.emitters._label_overlap.get_font_measurer",
            return_value=measurer,
        ):
            layout = resolve_axis_x_overlap(
                axis,
                "x",
                data,
                1.0,
                edge_labels_flushed=False,
                chart_width=600,
                continuous_temporal=False,
            )

        assert layout.label_overlap == "parity"

    def test_width_never_changes_resolved_time_units(self) -> None:
        from dbt_charts.core.render.chart.emitters._label_overlap import (
            resolve_axis_x_overlap,
        )

        axis = _axis_x_temporal(label_time_unit="yearmonth")
        data = [{"x": value} for value in _monthly_dates(24)]
        measurer = _make_mock_measurer()

        with patch(
            "dbt_charts.core.render.chart.emitters._label_overlap.get_font_measurer",
            return_value=measurer,
        ):
            wide = resolve_axis_x_overlap(
                axis,
                "x",
                data,
                1.0,
                edge_labels_flushed=False,
                chart_width=10_000,
                continuous_temporal=False,
            )
            narrow = resolve_axis_x_overlap(
                axis,
                "x",
                data,
                1.0,
                edge_labels_flushed=False,
                chart_width=300,
                continuous_temporal=False,
            )

        assert axis.labels.time_unit == "yearmonth"
        assert wide.visibility_time_unit == "yearmonth"
        assert narrow.visibility_time_unit == "yearquarter"
        assert wide.angle == 0.0

    def test_fiscal_start_phases_monthly_thinning(self) -> None:
        from dbt_charts.core.render.chart.emitters._label_overlap import (
            resolve_axis_x_overlap,
        )
        from dbt_charts.core.render.chart.type_inference import (
            build_cartesian_x_encoding,
        )

        axis = _axis_x_temporal(fiscal_year_start_month=3)
        data = [{"x": value} for value in _monthly_dates(12)]
        measurer = _make_mock_measurer()
        with patch(
            "dbt_charts.core.render.chart.emitters._label_overlap.get_font_measurer",
            return_value=measurer,
        ):
            layout = resolve_axis_x_overlap(
                axis,
                "x",
                data,
                1.0,
                edge_labels_flushed=False,
                chart_width=150,
                continuous_temporal=False,
            )

        _, ax_vl, _ = build_cartesian_x_encoding(
            data,
            "x",
            axis,
            {},
            "bar",
            visibility_time_unit=layout.visibility_time_unit,
            label_anchor_index=layout.anchor_index,
        )
        expr = ax_vl["labelExpr"]
        assert layout.visibility_time_unit == "yearquarter"
        assert "- 2 + 12) % 12) % 3 === 0" in expr
        assert (
            "utcFormat(toDate(datum.value), '%Y-%m') === "
            "utcFormat(toDate(\"2022-03-01\"), '%Y-%m')"
        ) in expr
        assert "datum.index" not in expr
        assert ax_vl["values"] == _monthly_dates(12)

    def test_thinning_precedes_tilt(self) -> None:
        from dbt_charts.core.render.chart.emitters._label_overlap import (
            resolve_axis_x_overlap,
        )

        axis = _axis_x_temporal()
        data = [{"x": value} for value in _monthly_dates(24)]
        measurer = _make_mock_measurer(width_per_char=8.0)
        with patch(
            "dbt_charts.core.render.chart.emitters._label_overlap.get_font_measurer",
            return_value=measurer,
        ):
            thinned = resolve_axis_x_overlap(
                axis,
                "x",
                data,
                1.0,
                edge_labels_flushed=False,
                chart_width=500,
                continuous_temporal=False,
            )
            # Narrow enough that even the looped ladder's terminal (year)
            # rung doesn't fit flat — still exercises thin-before-tilt.
            tilted = resolve_axis_x_overlap(
                axis,
                "x",
                data,
                1.0,
                edge_labels_flushed=False,
                chart_width=20,
                continuous_temporal=False,
            )

        assert thinned.visibility_time_unit == "yearquarter"
        assert thinned.angle == 0.0
        assert tilted.visibility_time_unit == "year"
        assert tilted.angle != 0.0

    def test_authored_quarter_format_promotes_to_year_text_when_thinned_to_year(
        self,
    ) -> None:
        """The looped ladder's Q1 amendment: once thinning reaches ``year``,
        the label vocabulary promotes to the bare year (``2024``), not the
        author's original ``yearquarter`` vocabulary (which would otherwise
        repeat ``Q1`` at every surviving, January-only tick)."""
        from dbt_charts.core.render.chart.emitters._label_overlap import (
            resolve_axis_x_overlap,
        )
        from dbt_charts.core.render.chart.time_unit_detect import default_label_expr_for

        axis = _axis_x_temporal(label_time_unit="yearquarter")
        data = [{"x": value} for value in _monthly_dates(36)]
        measurer = _make_mock_measurer()
        with patch(
            "dbt_charts.core.render.chart.emitters._label_overlap.get_font_measurer",
            return_value=measurer,
        ):
            layout = resolve_axis_x_overlap(
                axis,
                "x",
                data,
                1.0,
                edge_labels_flushed=False,
                chart_width=90,
                continuous_temporal=False,
            )

        expr = default_label_expr_for(
            "yearmonth",
            layout.format_time_unit,
            layout.visibility_time_unit,
        )
        assert layout.visibility_time_unit == "year"
        assert layout.format_time_unit == "year"
        assert "'Q'" not in expr
        assert "'%b'" not in expr
        assert "'%Y'" in expr

    def test_year_labels_use_parity_skip_before_tilt(self) -> None:
        from dbt_charts.core.render.chart.emitters._label_overlap import (
            resolve_axis_x_overlap,
        )

        axis = _axis_x_temporal(label_time_unit="year")
        data = [{"x": f"{year}-01-01"} for year in range(2000, 2025)]
        measurer = _make_mock_measurer()
        with patch(
            "dbt_charts.core.render.chart.emitters._label_overlap.get_font_measurer",
            return_value=measurer,
        ):
            layout = resolve_axis_x_overlap(
                axis,
                "x",
                data,
                1.0,
                edge_labels_flushed=False,
                chart_width=300,
                continuous_temporal=False,
            )

        assert layout.label_overlap == "parity"
        assert layout.visibility_time_unit == "year"

    def test_continuous_temporal_render_local_thinning_keeps_flushed_edge_label(
        self,
    ) -> None:
        """A continuous (line/area) axis whose weekly data gets render-locally
        thinned to month labels — never authored — must still get an explicit
        ``values`` list carrying the domain-start opener.

        Regression: ``area-charts_0`` (6 weekly points, Jan 5 - Feb 9 2026) and
        ``playground/xaxis-cadence-ladder`` axis 6 both lost their leading
        "Jan" label and, with it, all year context. Vega's own month-interval
        tick generator only places ticks on true calendar-month boundaries
        (Feb 1 here) — Jan 1 falls outside the domain (which starts Jan 5), so
        without an explicit ``values`` override Vega silently drops the tick
        entirely (it never enters the DOM, it is not merely hidden via
        ``labelOverlap``). Before this fix, only an AUTHORED coarser
        ``labels.time_unit`` triggered the explicit ``values`` injection
        (``label_tick_cadence``) — a render-local ladder promotion did not.
        """
        from dbt_charts.core.render.chart.emitters._label_overlap import (
            resolve_axis_x_overlap,
        )
        from dbt_charts.core.render.chart.type_inference import (
            build_cartesian_x_encoding,
        )

        axis = _axis_x_temporal()
        dates = [
            "2026-01-05",
            "2026-01-12",
            "2026-01-19",
            "2026-01-26",
            "2026-02-02",
            "2026-02-09",
        ]
        data = [{"x": value} for value in dates]
        measurer = _make_mock_measurer(width_per_char=5.0)
        with patch(
            "dbt_charts.core.render.chart.emitters._label_overlap.get_font_measurer",
            return_value=measurer,
        ):
            # bucket_aligned_temporal=False mirrors the real area/line emitter
            # call (curve != "step").
            layout = resolve_axis_x_overlap(
                axis,
                "x",
                data,
                1.0,
                bucket_aligned_temporal=False,
                edge_labels_flushed=True,
                chart_width=60,
                continuous_temporal=False,
            )

        assert layout.visibility_time_unit == "yearmonth"
        assert layout.anchor_index == 0

        _, ax_vl, _ = build_cartesian_x_encoding(
            data,
            "x",
            axis,
            {},
            "area",
            format_time_unit=layout.format_time_unit,
            visibility_time_unit=layout.visibility_time_unit,
            label_anchor_index=layout.anchor_index,
        )

        assert "values" in ax_vl
        assert ax_vl["values"][0] == dates[0]
        assert ax_vl["values"] == ["2026-01-05", "2026-02-02"]

    def test_thinned_native_week_labels_do_not_shift_off_the_real_date(self) -> None:
        """A continuous axis keeping its native ``yearweek`` vocabulary (author
        set ``labels.time_unit: yearweek``, matching the encoding grain) while
        render-locally thinned to a coarser visibility must show the REAL
        bucket date, not one shifted by a day.

        Regression: injecting real opener values (this file's previous test)
        made ``datum.value`` a genuine per-row bucket key for this axis too —
        but the shared temporal labelExpr call still unconditionally passed
        ``ticks_are_buckets=False``, which exists to correct Vega's own
        Sunday-anchored continuous ``utcyearweek`` ticks to the represented
        Monday. Applying that correction to an already-exact injected date
        shifts it a real day forward (``playground/time-unit-label-cadence-
        matrix``'s ``native_week`` chart read "2Apr'24" for a domain that has
        no April 2nd row — the real bucket is April 1st).
        """
        from dbt_charts.core.render.chart.type_inference import (
            build_cartesian_x_encoding,
        )

        axis = _axis_x_temporal(
            label_time_unit="yearweek", encoding_time_unit="yearweek"
        )
        dates = [
            (datetime.date(2024, 2, 12) + datetime.timedelta(weeks=i)).isoformat()
            for i in range(52)
        ]
        data = [{"x": value} for value in dates]

        _, ax_vl, _ = build_cartesian_x_encoding(
            data,
            "x",
            axis,
            {},
            "line",
            format_time_unit="yearweek",
            visibility_time_unit="yearquarter",
        )

        assert "2024-04-01" in ax_vl["values"]
        assert "utcOffset" not in ax_vl["labelExpr"]


class TestCategoricalOverlapResolution:
    def test_tilts_without_skipping(self) -> None:
        from dbt_charts.core.render.chart.emitters._label_overlap import (
            resolve_axis_x_overlap,
        )

        axis = _axis_x_temporal()
        data = [{"x": f"Long category {i}"} for i in range(10)]
        measurer = _make_mock_measurer()
        with patch(
            "dbt_charts.core.render.chart.emitters._label_overlap.get_font_measurer",
            return_value=measurer,
        ):
            layout = resolve_axis_x_overlap(
                axis,
                "x",
                data,
                1.0,
                edge_labels_flushed=False,
                chart_width=500,
                continuous_temporal=False,
            )

        assert layout.label_overlap == "allow"
        assert layout.angle != 0.0

    def test_uses_steepest_tilt_without_skipping_when_labels_do_not_fit(self) -> None:
        from dbt_charts.core.render.chart.emitters._label_overlap import (
            resolve_axis_x_overlap,
        )

        axis = _axis_x_temporal()
        data = [{"x": f"Very long category {i}"} for i in range(20)]
        measurer = _make_mock_measurer()
        with patch(
            "dbt_charts.core.render.chart.emitters._label_overlap.get_font_measurer",
            return_value=measurer,
        ):
            layout = resolve_axis_x_overlap(
                axis,
                "x",
                data,
                1.0,
                edge_labels_flushed=False,
                chart_width=200,
                continuous_temporal=False,
            )

        assert layout.label_overlap == "allow"
        assert layout.angle == -90.0


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


class TestResolveTemporalLabelVisibilityYearRowOverprint:
    """Regression for the same defect at the resolver's own public entry
    point: an axis whose anchor tick opens off the fiscal-year boundary
    (October) paired against an interior January must not be reported as
    fitting ``yearquarter`` flat when the two ticks' year rows actually
    overprint in the real render."""

    def test_30_monthly_points_opening_october_does_not_fit_yearquarter(
        self,
    ) -> None:
        import datetime

        from dbt_charts.core.font_measure import get_font_measurer
        from dbt_charts.core.render.chart.time_unit_detect import (
            resolve_temporal_label_visibility,
        )

        dates = []
        year, month = 2023, 10
        for _ in range(30):
            dates.append(datetime.date(year, month, 1))
            month += 1
            if month > 12:
                month = 1
                year += 1

        measurer = get_font_measurer(None)
        result = resolve_temporal_label_visibility(
            dates,
            "yearmonth",
            "yearmonth",
            measurer,
            11.0,
            10.0,
            fiscal_year_start_month=1,
            edge_labels_flushed=True,
        )
        assert result != ("yearquarter", True)


class TestResolveTemporalLabelVisibilityAnchorIsFirstLabeledOpener:
    """Regression: the labelExpr's ``anchor`` is the first *visible*
    (labeled) tick, not the domain's literal index 0 -- ``_pair_clears``
    used to test ``i == 0`` where the correct predicate is the pair's
    ordinal position (``k == 0`` in ``temporal_visibility_fits``). A domain
    that opens before its first labeled opener at a candidate grain (18
    monthly points starting Feb 2023; the first quarter opener at
    ``fiscal_year_start_month=7`` is April 2023, domain index 2) must still
    treat that first labeled tick as the anchor and give it a year row --
    otherwise its real collision with the next labeled tick's year row goes
    undetected and the ladder wrongly reports ``yearquarter`` as fitting."""

    def test_18_monthly_points_opening_before_first_quarter_opener_does_not_fit_yearquarter(
        self,
    ) -> None:
        import datetime

        from dbt_charts.core.font_measure import get_font_measurer
        from dbt_charts.core.render.chart.time_unit_detect import (
            resolve_temporal_label_visibility,
        )

        dates = []
        year, month = 2023, 2
        for _ in range(18):
            dates.append(datetime.date(year, month, 1))
            month += 1
            if month > 12:
                month = 1
                year += 1

        measurer = get_font_measurer(None)
        result = resolve_temporal_label_visibility(
            dates,
            "yearmonth",
            "yearmonth",
            measurer,
            11.0,
            8.5,
            fiscal_year_start_month=7,
            edge_labels_flushed=True,
        )
        assert result == ("year", True)


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


class TestCadenceLadderReachesYear:
    """Regression coverage for the looped cadence ladder + sparse ceiling.

    Unlike the mock-measurer tests above, these use the real font measurer
    (no ``get_font_measurer`` patch) with ``label_usable_ratio=0.8`` — the
    same combination the task brief's own Q2 measurement table used — so the
    chart widths below reproduce that table's outcomes directly.
    """

    def test_137_monthly_points_reaches_year_flat(self) -> None:
        """This IS the reported chart — matrix cell ``mo137_1050``, a line
        chart, so ``edge_labels_flushed=True``."""
        from dbt_charts.core.render.chart.emitters._label_overlap import (
            resolve_axis_x_overlap,
        )

        axis = _axis_x_temporal()
        data = [{"x": value} for value in _monthly_dates(137, start=(2015, 4))]

        layout = resolve_axis_x_overlap(
            axis,
            "x",
            data,
            0.8,
            edge_labels_flushed=True,
            chart_width=1050,
            continuous_temporal=False,
        )

        assert layout.visibility_time_unit == "year"
        assert layout.format_time_unit == "year"
        assert layout.angle == 0.0

    def test_year_reachable_from_monthly_weekly_and_daily(self) -> None:
        from dbt_charts.core.render.chart.emitters._label_overlap import (
            resolve_axis_x_overlap,
        )

        axis = _axis_x_temporal()
        cases = [
            _monthly_dates(137, start=(2015, 4)),
            _weekly_dates(300),
            _daily_dates(3650),
        ]
        for values in cases:
            data = [{"x": value} for value in values]
            layout = resolve_axis_x_overlap(
                axis,
                "x",
                data,
                0.8,
                edge_labels_flushed=True,
                chart_width=500,
                continuous_temporal=False,
            )
            assert layout.visibility_time_unit == "year", values[0]

    def test_sparse_ceiling_is_a_two_sided_window(self) -> None:
        """Same 60-month series, two card widths: the ceiling picks a
        different rung on each — proof a proportional rule cannot do this
        job (see the task brief's Q2 table). Both are line-chart matrix
        cells (``mo60_282``, ``mo60_1128``), so ``edge_labels_flushed=True``."""
        from dbt_charts.core.render.chart.emitters._label_overlap import (
            resolve_axis_x_overlap,
        )

        axis = _axis_x_temporal()
        data = [{"x": value} for value in _monthly_dates(60)]

        narrow = resolve_axis_x_overlap(
            axis,
            "x",
            data,
            0.8,
            edge_labels_flushed=True,
            chart_width=282,
            continuous_temporal=False,
        )
        wide = resolve_axis_x_overlap(
            axis,
            "x",
            data,
            0.8,
            edge_labels_flushed=True,
            chart_width=1128,
            continuous_temporal=False,
        )

        assert narrow.visibility_time_unit == "year"
        assert wide.visibility_time_unit == "yearquarter"

    def test_60_monthly_at_658_prefers_sparse_year_over_quarter_overlap(self) -> None:
        """The ceiling is a preference, not a hard stop: at 658px neither
        month nor quarter fits flat (quarter's flushed first label, carrying
        its stacked year row, needs 34.3px against 26.3px of clearance) — only
        year fits, even though its 105.3px spacing exceeds the 90px ceiling.
        Before this fix the ladder gave up at quarter (which does not fit
        either) and rotated, printing "JanApr" on top of itself."""
        from dbt_charts.core.render.chart.emitters._label_overlap import (
            resolve_axis_x_overlap,
        )

        axis = _axis_x_temporal()
        data = [{"x": value} for value in _monthly_dates(60)]

        layout = resolve_axis_x_overlap(
            axis,
            "x",
            data,
            0.8,
            edge_labels_flushed=True,
            chart_width=658,
            continuous_temporal=False,
        )

        assert layout.visibility_time_unit == "year"
        assert layout.angle == 0.0

    def test_260_weekly_at_658_prefers_sparse_year_over_quarter_overlap(self) -> None:
        """Same shape as the monthly case above, weekly-grained: the ceiling
        yields to a flat, sparse year cadence rather than a colliding
        quarter."""
        from dbt_charts.core.render.chart.emitters._label_overlap import (
            resolve_axis_x_overlap,
        )

        axis = _axis_x_temporal()
        data = [{"x": value} for value in _weekly_dates(260)]

        layout = resolve_axis_x_overlap(
            axis,
            "x",
            data,
            0.8,
            edge_labels_flushed=True,
            chart_width=658,
            continuous_temporal=False,
        )

        assert layout.visibility_time_unit == "year"
        assert layout.angle == 0.0

    def test_24_months_stays_at_month_not_over_coarsened_at_1128(self) -> None:
        """24 monthly points at 1128px already clear the collision test at
        native month cadence — the ladder must not walk past a rung that
        already fits just because a coarser one would look tidier. Line
        matrix cell ``mo24_1128``, so ``edge_labels_flushed=True``."""
        from dbt_charts.core.render.chart.emitters._label_overlap import (
            resolve_axis_x_overlap,
        )

        axis = _axis_x_temporal()
        data = [{"x": value} for value in _monthly_dates(24)]

        layout = resolve_axis_x_overlap(
            axis,
            "x",
            data,
            0.8,
            edge_labels_flushed=True,
            chart_width=1128,
            continuous_temporal=False,
        )

        assert layout.visibility_time_unit == "yearmonth"
        assert layout.format_time_unit == "yearmonth"

    def test_137_monthly_at_282_still_tilts(self) -> None:
        """At 282px even year cadence collides — no rung fits flat at any
        spacing, so rotation remains the correct fallback. Proves the ceiling
        fix does not simply delete the collision check. Line matrix cell
        ``mo137_282``, so ``edge_labels_flushed=True`` (matching the real
        cell — see the coordinator's Phase 3 trace)."""
        from dbt_charts.core.render.chart.emitters._label_overlap import (
            resolve_axis_x_overlap,
        )

        axis = _axis_x_temporal()
        data = [{"x": value} for value in _monthly_dates(137, start=(2015, 4))]

        layout = resolve_axis_x_overlap(
            axis,
            "x",
            data,
            0.8,
            edge_labels_flushed=True,
            chart_width=282,
            continuous_temporal=False,
        )

        assert layout.angle is not None
        assert layout.angle < 0.0

    def test_24_months_stays_at_quarter_not_year(self) -> None:
        """Too few labels at year cadence (2 of them) must not win just
        because 2 labels always trivially 'fit' — the sparse ceiling should
        reject the near-empty year rung and stay at quarter.

        Covers both flush modes: ``edge_labels_flushed=False`` is bar's
        setting, ``=True`` is line/area/scatter's (and the reported chart's,
        matrix cell ``mo24_282``) — the two produce different collision math
        at the flushed first label, so a test pinning only one mode cannot
        catch a regression that breaks the other. Before the two-row width
        fix, the flushed case over-reserved ~6px on the flushed quarter
        label's stacked year row (18.9px "Q1" vs 24.8px "2024", taken as a
        single max() block instead of two independently-checked rows) and
        wrongly rejected quarter, over-coarsening to ``year``."""
        from dbt_charts.core.render.chart.emitters._label_overlap import (
            resolve_axis_x_overlap,
        )

        axis = _axis_x_temporal()
        data = [{"x": value} for value in _monthly_dates(24)]

        bar = resolve_axis_x_overlap(
            axis,
            "x",
            data,
            0.8,
            edge_labels_flushed=False,
            chart_width=282,
            continuous_temporal=False,
        )
        line = resolve_axis_x_overlap(
            axis,
            "x",
            data,
            0.8,
            edge_labels_flushed=True,
            chart_width=282,
            continuous_temporal=False,
        )

        assert bar.visibility_time_unit == "yearquarter"
        assert line.visibility_time_unit == "yearquarter"

    def test_bar_chart_keeps_monthly_ticks_under_year_labels(self) -> None:
        """A bar axis thinned by the ladder — not by an authored
        labels.time_unit — must keep every monthly bucket in `values` and
        restore `ticks: True`, even once the label text promotes to year.

        48 months (under ``chart_rendering.type_inference.max_ordinal_buckets``,
        60) keeps this chart on bar's ordinal band scale rather than the
        density gate's continuous-temporal fallback — the branch this
        regression is actually about.
        """
        from dbt_charts.core.render.chart.emitters._label_overlap import (
            resolve_axis_x_overlap,
        )
        from dbt_charts.core.render.chart.type_inference import (
            build_cartesian_x_encoding,
        )

        axis = _axis_x_temporal()
        month_values = _monthly_dates(48, start=(2015, 4))
        data = [{"x": value} for value in month_values]

        layout = resolve_axis_x_overlap(
            axis,
            "x",
            data,
            0.8,
            edge_labels_flushed=False,
            chart_width=200,
            continuous_temporal=False,
        )
        assert layout.visibility_time_unit == "year"

        _, ax_vl, _ = build_cartesian_x_encoding(
            data,
            "x",
            axis,
            {},
            "bar",
            format_time_unit=layout.format_time_unit,
            visibility_time_unit=layout.visibility_time_unit,
            label_anchor_index=layout.anchor_index,
        )

        assert ax_vl["values"] == month_values
        assert ax_vl["ticks"] is True

    def test_no_visible_label_set_repeats_the_same_token(self) -> None:
        """Once thinning reaches year cadence, the labelExpr must read the
        bare year (`toDate → '%Y'`), never the month vocabulary (`'%b'`) —
        the latter would render the identical token ("Jan") at every
        surviving, January-only tick."""
        from dbt_charts.core.render.chart.emitters._label_overlap import (
            resolve_axis_x_overlap,
        )
        from dbt_charts.core.render.chart.time_unit_detect import (
            default_label_expr_for,
        )

        axis = _axis_x_temporal()
        data = [{"x": value} for value in _monthly_dates(137, start=(2015, 4))]

        layout = resolve_axis_x_overlap(
            axis,
            "x",
            data,
            0.8,
            edge_labels_flushed=True,
            chart_width=1050,
            continuous_temporal=False,
        )
        assert layout.visibility_time_unit == "year"

        expr = default_label_expr_for(
            "yearmonth", layout.format_time_unit, layout.visibility_time_unit
        )
        assert expr is not None
        assert "'%Y'" in expr
        assert "'%b'" not in expr
