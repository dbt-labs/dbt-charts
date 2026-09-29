"""Tests for x-axis time-unit behavior on bar/line/area charts.

After D-002: bucketed-time x-axes on bar/column default to ordinal scale; the
temporal scale is an opt-in escape hatch (axis_x.type: temporal). Line/area
are the opposite default — a bucketed-calendar grain always routes to a
continuous temporal scale (no per-bucket density gate; see
value-driven-axis-type-inference task).

Verifies:
- bar on DATE columns emits ordinal (not temporal); line/area emit temporal
- explicit time_unit override (yearquarter, yearmonth) → bar stays ordinal
- time_unit: none → temporal continuous (unchanged)
- datetime.date objects are recognized as date-like
- sparse-gap behavior on bar: ordinal collapses the gap (no missing band)
- Q-label / W-label normalization to ISO dates runs on bar and line
- band_width resizes bars on ordinal scale
- grouped bar x-offset still works on ordinal scale
- Time-part units (monthofyear/dayofweek/etc.) remain temporal
"""

from __future__ import annotations

import datetime as dt

import pytest

from dbt_charts.core.compile.config import get_theme_style
from dbt_charts.core.compile.models.chart.normalized import (
    AreaChart,
    BarChart,
    Chart,
    LineChart,
)
from dbt_charts.core.compile.models.style.authored import (
    AxisXStylePatch,
    BarChartStylePatch,
    DimensionLabelStylePatch,
)
from dbt_charts.core.compile.resolve import resolve
from dbt_charts.core.compile.resolve.style.board import resolve_style_and_context
from dbt_charts.core.render.chart.vega_lite import render_resolved_chart

_BOARD_RS, _BOARD_CTX = resolve_style_and_context(get_theme_style())


def _bar(x: str = "month", y: str = "revenue", **kwargs: object) -> Chart:
    return BarChart(id="test_bar", type="bar", x=x, y=y, **kwargs)


def _line(x: str = "month", y: str = "revenue", **kwargs: object) -> Chart:
    return LineChart(id="test_line", type="line", x=x, y=y, **kwargs)


def _area(x: str = "month", y: str = "revenue", **kwargs: object) -> Chart:
    return AreaChart(id="test_area", type="area", x=x, y=y, **kwargs)


def _monthly_data() -> list[dict]:
    return [{"month": f"2024-{m:02d}-01", "revenue": m * 100} for m in range(1, 13)]


def _quarterly_data() -> list[dict]:
    return [{"month": f"2024-{m:02d}-01", "revenue": m * 100} for m in (1, 4, 7, 10)]


def _sparse_monthly_data() -> list[dict]:
    # Jan, Feb, May — gaps in Mar/Apr
    return [
        {"month": "2024-01-01", "revenue": 100},
        {"month": "2024-02-01", "revenue": 200},
        {"month": "2024-05-01", "revenue": 500},
    ]


class TestDefaultOrdinalForBucketedTime:
    """After D-002: bar/line/area on bucketed-time data default to ordinal."""

    def test_bar_monthly_emits_ordinal(self) -> None:
        chart = _bar()
        data = _monthly_data()
        resolved = resolve(chart, data, chart_style_context=_BOARD_CTX)
        spec = render_resolved_chart(resolved, data, _BOARD_RS).payload

        x_enc = spec.get("encoding", {}).get("x", {})
        assert x_enc.get("type") == "ordinal", f"Expected ordinal, got: {x_enc}"
        assert "timeUnit" not in x_enc

    def test_line_monthly_emits_temporal(self) -> None:
        # Line/area always route a bucketed-calendar grain to a continuous
        # temporal scale (no per-bucket density gate — a trend line has no
        # gridline-comb problem the way ordinal bar bands do).
        chart = _line()
        data = _monthly_data()
        resolved = resolve(chart, data, chart_style_context=_BOARD_CTX)
        spec = render_resolved_chart(resolved, data, _BOARD_RS).payload

        x_enc = spec.get("encoding", {}).get("x", {})
        assert x_enc.get("type") == "temporal"
        assert x_enc.get("timeUnit") == "utcyearmonth"

    def test_area_monthly_emits_temporal(self) -> None:
        chart = _area()
        data = _monthly_data()
        resolved = resolve(chart, data, chart_style_context=_BOARD_CTX)
        spec = render_resolved_chart(resolved, data, _BOARD_RS).payload

        x_enc = spec.get("encoding", {}).get("x", {})
        assert x_enc.get("type") == "temporal"
        assert x_enc.get("timeUnit") == "utcyearmonth"

    def test_explicit_time_unit_override_yearquarter(self) -> None:
        # Explicit yearquarter → still ordinal by default (no timeUnit in encoding).
        # Quarter-grained rows: three months in one quarter is a bucket
        # collision, not one point per bucket.
        style = BarChartStylePatch(axis_x=AxisXStylePatch(time_unit="yearquarter"))
        chart = _bar(style=style)
        data = _quarterly_data()
        resolved = resolve(chart, data, chart_style_context=_BOARD_CTX)
        spec = render_resolved_chart(resolved, data, _BOARD_RS).payload

        x_enc = spec.get("encoding", {}).get("x", {})
        assert x_enc.get("type") == "ordinal"
        assert "timeUnit" not in x_enc

    def test_time_unit_none_disables_timeunit(self) -> None:
        style = BarChartStylePatch(axis_x=AxisXStylePatch(time_unit="none"))
        chart = _bar(style=style)
        data = _monthly_data()
        resolved = resolve(chart, data, chart_style_context=_BOARD_CTX)
        spec = render_resolved_chart(resolved, data, _BOARD_RS).payload

        x_enc = spec.get("encoding", {}).get("x", {})
        assert "timeUnit" not in x_enc, (
            f"time_unit=none should disable timeUnit: {x_enc}"
        )
        assert x_enc.get("type") == "temporal"

    def test_date_objects_recognized_as_date_like(self) -> None:
        # datetime.date values → ordinal (not nominal, not temporal)
        data = [
            {"month": dt.date(2024, 1, 1), "revenue": 100},
            {"month": dt.date(2024, 2, 1), "revenue": 200},
            {"month": dt.date(2024, 3, 1), "revenue": 300},
        ]
        chart = _bar()
        resolved = resolve(chart, data, chart_style_context=_BOARD_CTX)
        spec = render_resolved_chart(resolved, data, _BOARD_RS).payload

        x_enc = spec.get("encoding", {}).get("x", {})
        assert x_enc.get("type") == "ordinal", f"Expected ordinal: {x_enc}"
        assert "timeUnit" not in x_enc


class TestSparseGapPreservation:
    def test_sparse_monthly_data_uses_ordinal(self) -> None:
        # Jan/Feb/May on ordinal: gaps collapse (no Mar/Apr bands appear).
        # Use temporal escape hatch if gap preservation is needed.
        chart = _bar()
        data = _sparse_monthly_data()
        resolved = resolve(chart, data, chart_style_context=_BOARD_CTX)
        spec = render_resolved_chart(resolved, data, _BOARD_RS).payload

        x_enc = spec.get("encoding", {}).get("x", {})
        assert x_enc.get("type") == "ordinal"
        assert "timeUnit" not in x_enc

    def test_time_format_works_on_ordinal(self) -> None:
        # Explicit time-format on ordinal axes routes through labelExpr because
        # `formatType: "time"` silently drops labels on a string-domain ordinal
        # scale (d3-time-format gets the raw string and expects a Date).
        style = BarChartStylePatch(
            axis_x=AxisXStylePatch(labels=DimensionLabelStylePatch(format="%b %Y"))
        )
        chart = _bar(style=style)
        data = _monthly_data()
        resolved = resolve(chart, data, chart_style_context=_BOARD_CTX)
        spec = render_resolved_chart(resolved, data, _BOARD_RS).payload

        x_enc = spec.get("encoding", {}).get("x", {})
        x_axis = x_enc.get("axis", {})
        label_expr = str(x_axis.get("labelExpr", ""))
        assert "utcFormat(toDate(datum.value), '%b %Y')" in label_expr, (
            f"Time format must route through labelExpr on ordinal: {x_axis}"
        )
        assert "format" not in x_axis
        assert x_axis.get("formatType") != "time"
        # `in`, not `==`: the expression also carries the label-period gate
        # that decides WHICH of this axis's per-bucket ticks speak. The three
        # assertions above are this test's actual subject — no `formatType:
        # "time"`, no raw `format`, the author's spec reaching the labels —
        # and all three still hold. See
        # test_authored_format_keeps_the_cadence_gate.py for the gate itself.

    def test_quarter_labels_auto_detect_yearquarter_ordinal(self) -> None:
        # "2024-Q1" strings are normalized to ISO quarter-start dates and
        # detected as yearquarter → ordinal with ISO dates as category keys.
        data = [
            {"quarter": "2024-Q1", "revenue": 100},
            {"quarter": "2024-Q2", "revenue": 200},
            {"quarter": "2024-Q3", "revenue": 300},
        ]
        chart = BarChart(id="test_quarter_auto", type="bar", x="quarter", y="revenue")
        resolved = resolve(chart, data, chart_style_context=_BOARD_CTX)
        spec = render_resolved_chart(resolved, data, _BOARD_RS).payload

        x_enc = spec.get("encoding", {}).get("x", {})
        assert x_enc.get("type") == "ordinal", f"Expected ordinal: {x_enc}"
        assert "timeUnit" not in x_enc
        # Data values are still normalized to ISO quarter-start dates
        inline_values = spec.get("data", {}).get("values", [])
        assert inline_values[0]["quarter"] == "2024-01-01"
        assert inline_values[1]["quarter"] == "2024-04-01"
        assert inline_values[2]["quarter"] == "2024-07-01"

    def test_week_labels_auto_detect_yearweek_ordinal(self) -> None:
        # "2024-W01" strings normalized to ISO Mondays → yearweek → ordinal.
        data = [
            {"week": "2024-W01", "revenue": 100},
            {"week": "2024-W02", "revenue": 200},
            {"week": "2024-W03", "revenue": 300},
        ]
        chart = BarChart(id="test_week_auto", type="bar", x="week", y="revenue")
        resolved = resolve(chart, data, chart_style_context=_BOARD_CTX)
        spec = render_resolved_chart(resolved, data, _BOARD_RS).payload

        x_enc = spec.get("encoding", {}).get("x", {})
        assert x_enc.get("type") == "ordinal", f"Expected ordinal: {x_enc}"
        assert "timeUnit" not in x_enc
        inline_values = spec.get("data", {}).get("values", [])
        # 2024-W01 Monday = 2024-01-01; 2024-W02 = 2024-01-08; 2024-W03 = 2024-01-15
        assert inline_values[0]["week"] == "2024-01-01"
        assert inline_values[1]["week"] == "2024-01-08"
        assert inline_values[2]["week"] == "2024-01-15"

    def test_quarter_labels_with_explicit_override(self) -> None:
        # Explicit time_unit: yearquarter with quarter label strings.
        data = [
            {"quarter": "2024-Q1", "revenue": 100},
            {"quarter": "2024-Q2", "revenue": 200},
            {"quarter": "2024-Q3", "revenue": 300},
        ]
        style = BarChartStylePatch(axis_x=AxisXStylePatch(time_unit="yearquarter"))
        chart = BarChart(
            id="test_quarter_explicit",
            type="bar",
            x="quarter",
            y="revenue",
            style=style,
        )
        resolved = resolve(chart, data, chart_style_context=_BOARD_CTX)
        spec = render_resolved_chart(resolved, data, _BOARD_RS).payload

        x_enc = spec.get("encoding", {}).get("x", {})
        assert x_enc.get("type") == "ordinal", f"Expected ordinal: {x_enc}"
        assert "timeUnit" not in x_enc

    def test_explicit_time_unit_on_iso_quarterly_data(self) -> None:
        # ISO dates on quarter starts with explicit time_unit: yearquarter → ordinal.
        data = [
            {"quarter": "2024-01-01", "revenue": 100},
            {"quarter": "2024-04-01", "revenue": 200},
            {"quarter": "2024-07-01", "revenue": 300},
        ]
        style = BarChartStylePatch(axis_x=AxisXStylePatch(time_unit="yearquarter"))
        chart = BarChart(
            id="test_iso_quarterly",
            type="bar",
            x="quarter",
            y="revenue",
            style=style,
        )
        resolved = resolve(chart, data, chart_style_context=_BOARD_CTX)
        spec = render_resolved_chart(resolved, data, _BOARD_RS).payload

        x_enc = spec.get("encoding", {}).get("x", {})
        assert x_enc.get("type") == "ordinal", f"Expected ordinal: {x_enc}"
        assert "timeUnit" not in x_enc

    def test_invalid_time_unit_value_raises_validation_error(self) -> None:
        # time_unit is a Literal type — garbage values must be rejected at construction.
        from pydantic import ValidationError

        with pytest.raises(ValidationError):
            AxisXStylePatch(time_unit="banana")


class TestTemporalBarSizing:
    def test_line_default_emits_temporal(self) -> None:
        chart = _line()
        data = _monthly_data()
        resolved = resolve(chart, data, chart_style_context=_BOARD_CTX)
        spec = render_resolved_chart(resolved, data, _BOARD_RS).payload

        x_enc = spec.get("encoding", {}).get("x", {})
        assert x_enc.get("type") == "temporal"

    def test_area_default_emits_temporal(self) -> None:
        chart = _area()
        data = _monthly_data()
        resolved = resolve(chart, data, chart_style_context=_BOARD_CTX)
        spec = render_resolved_chart(resolved, data, _BOARD_RS).payload

        x_enc = spec.get("encoding", {}).get("x", {})
        assert x_enc.get("type") == "temporal"

    def test_band_width_resizes_bars_on_ordinal_scale(self) -> None:
        # ``bar.band_width`` maps to mark.width: {band: N} which controls bar
        # width as a fraction of the band step. On ordinal scales the band step
        # comes from the band scale itself — paddingInner controls spacing but
        # mark.width: {band: N} also applies.
        import json
        import re

        import vl_convert as vlc

        def _bar_widths(band_width: float) -> list[float]:
            # band_width lives at bar.marks.bar.band_width (ADR-015 marks namespace)
            style = BarChartStylePatch.model_validate(
                {"marks": {"bar": {"band_width": band_width}}}
            )
            chart = _bar(style=style)
            data = _monthly_data()
            resolved = resolve(chart, data, chart_style_context=_BOARD_CTX)
            spec = render_resolved_chart(
                resolved, data, _BOARD_RS, width=600, height=300
            ).payload
            svg = vlc.vegalite_to_svg(json.dumps(spec))
            return [
                float(w)
                for w in re.findall(
                    r'aria-roledescription="bar"[^>]*d="M[\d.]+,[\d.]+h([\d.]+)v',
                    svg,
                )
            ]

        narrow = _bar_widths(0.3)
        wide = _bar_widths(0.9)
        assert narrow and wide, "expected bars in both renders"
        assert wide[0] > narrow[0] * 2.0, (
            f"bar.band_width must visibly resize bars on ordinal x; got "
            f"narrow(0.3)={narrow[0]} vs wide(0.9)={wide[0]}"
        )

    def test_band_width_emits_mark_width_band(self) -> None:
        # band_width lives at bar.marks.bar.band_width (ADR-015 marks namespace)
        style = BarChartStylePatch.model_validate(
            {"marks": {"bar": {"band_width": 0.8}}}
        )
        chart = _bar(style=style)
        data = _monthly_data()
        resolved = resolve(chart, data, chart_style_context=_BOARD_CTX)
        spec = render_resolved_chart(resolved, data, _BOARD_RS).payload
        # Bar mark sits in layer[0] when the zero-baseline rule is added.
        bar_mark = spec.get("mark") or spec.get("layer", [{"mark": {}}])[0].get(
            "mark", {}
        )
        assert bar_mark.get("width") == {"band": 0.8}


class TestTimePartUnits:
    def test_monthofyear_maps_to_vega_lite_month_on_temporal_scale(self) -> None:
        style = BarChartStylePatch(axis_x=AxisXStylePatch(time_unit="monthofyear"))
        chart = _bar(style=style)
        data = _monthly_data()
        resolved = resolve(chart, data, chart_style_context=_BOARD_CTX)
        spec = render_resolved_chart(resolved, data, _BOARD_RS).payload

        x_enc = spec.get("encoding", {}).get("x", {})
        assert x_enc.get("type") == "temporal"
        assert x_enc.get("timeUnit") == "month"

    def test_dayofweek_maps_to_vega_lite_day_on_temporal_scale(self) -> None:
        style = BarChartStylePatch(axis_x=AxisXStylePatch(time_unit="dayofweek"))
        chart = _bar(x="day", style=style)
        data = [
            {"day": "2024-01-01", "revenue": 100},
            {"day": "2024-01-02", "revenue": 200},
        ]
        resolved = resolve(chart, data, chart_style_context=_BOARD_CTX)
        spec = render_resolved_chart(resolved, data, _BOARD_RS).payload

        x_enc = spec.get("encoding", {}).get("x", {})
        assert x_enc.get("type") == "temporal"
        assert x_enc.get("timeUnit") == "day"

    def test_all_time_part_names_map_to_vega_lite_primitives(self) -> None:
        cases = {
            "dayofmonth": "date",
            "dayofyear": "dayofyear",
            "hourofday": "hours",
        }
        data = [
            {"ts": "2024-01-01T00:00:00", "revenue": 100},
            {"ts": "2024-01-02T01:00:00", "revenue": 200},
        ]
        for authored, expected in cases.items():
            style = BarChartStylePatch(axis_x=AxisXStylePatch(time_unit=authored))
            chart = _bar(x="ts", style=style)
            resolved = resolve(chart, data, chart_style_context=_BOARD_CTX)
            spec = render_resolved_chart(resolved, data, _BOARD_RS).payload

            x_enc = spec.get("encoding", {}).get("x", {})
            assert x_enc.get("type") == "temporal"
            assert x_enc.get("timeUnit") == expected
