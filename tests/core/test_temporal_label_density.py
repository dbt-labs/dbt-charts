"""Tests: decouple label density from tick/grid density on temporal axes.

``style.axis_x.scale.values`` pins ticks, gridlines, AND labels to one list.
Sparse editorial labels (first + last + every-5) then force unevenly-spaced
gridlines. ``style.axis_x.labels.values`` is the new primitive: it filters
which ticks show LABEL TEXT (via a Vega labelExpr membership test), while
tick/grid density stays governed by ``axis_x.scale.values`` (or the auto-fill
default) exactly as before — fully decoupled.

Mechanism: ticks/gridlines are left at their natural (dense) rhythm; a
labelExpr blanks any tick whose date isn't in the authored ``labels.values``
list. Verified empirically against real Vega (vl_convert) before this file
was written — see ai_notes probes under the task's scratch directory.
"""

from __future__ import annotations

import datetime as dt

import pytest

from dbt_charts.core.compile.config import get_theme_style
from dbt_charts.core.compile.models.chart.normalized import BarChart, LineChart
from dbt_charts.core.compile.models.style.authored import (
    AxisXStylePatch,
    BarChartStylePatch,
    DimensionLabelStylePatch,
    LineChartStylePatch,
    XScaleStylePatch,
)
from dbt_charts.core.compile.resolve import resolve
from dbt_charts.core.compile.resolve.style.board import resolve_style_and_context
from dbt_charts.core.diagnostics.chart_data import ChartDataError
from dbt_charts.core.render.chart.vega_lite import render_resolved_chart

_BOARD_RS, _BOARD_CTX = resolve_style_and_context(get_theme_style())


def _annual_data(start: int = 2008, end: int = 2025) -> list[dict]:
    """18 annual points, 2008-01-01 .. 2025-01-01 — mirrors the audit repro board."""
    return [
        {"launch_year": f"{y}-01-01", "launches": 100 + i * 7}
        for i, y in enumerate(range(start, end + 1))
    ]


_SPARSE_LABELS = ["2008-01-01", "2010-01-01", "2015-01-01", "2020-01-01", "2025-01-01"]


class TestSchemaField:
    """style.axis_x.labels.values exists and flows through the resolved cascade."""

    def test_axis_element_style_patch_accepts_values(self):
        patch = DimensionLabelStylePatch(values=_SPARSE_LABELS)
        assert patch.values == _SPARSE_LABELS

    def test_axis_style_patch_accepts_nested_label_values(self):
        patch = AxisXStylePatch(labels=DimensionLabelStylePatch(values=_SPARSE_LABELS))
        assert patch.labels.values == _SPARSE_LABELS


class TestDenseGridSparseLabels:
    """The editorial pattern: dense ordinal ticks/gridlines, sparse labels."""

    def test_bar_chart_label_values_keeps_dense_ticks(self):
        style = BarChartStylePatch(
            axis_x=AxisXStylePatch(
                labels=DimensionLabelStylePatch(values=_SPARSE_LABELS)
            )
        )
        data = _annual_data()
        chart = BarChart(
            id="annual_bar", type="bar", x="launch_year", y="launches", style=style
        )
        resolved = resolve(chart, data, chart_style_context=_BOARD_CTX)
        spec = render_resolved_chart(resolved, data, _BOARD_RS).payload

        x_axis = spec["encoding"]["x"]["axis"]
        # No axis_x.scale.values authored -> tick/grid density stays at the dense,
        # auto-filled default (one per data point), independent of the sparse
        # label list.
        tick_values = x_axis.get("values")
        assert tick_values is not None
        assert len(tick_values) == len(data), (
            f"tick/grid values must stay dense (18), got {len(tick_values)}: "
            f"{tick_values}"
        )

    def test_bar_chart_label_values_filters_label_expr(self):
        style = BarChartStylePatch(
            axis_x=AxisXStylePatch(
                labels=DimensionLabelStylePatch(values=_SPARSE_LABELS)
            )
        )
        data = _annual_data()
        chart = BarChart(
            id="annual_bar", type="bar", x="launch_year", y="launches", style=style
        )
        resolved = resolve(chart, data, chart_style_context=_BOARD_CTX)
        spec = render_resolved_chart(resolved, data, _BOARD_RS).payload

        label_expr = spec["encoding"]["x"]["axis"].get("labelExpr")
        assert label_expr is not None
        assert "indexof(" in label_expr
        assert "time(toDate(datum.value))" in label_expr

    def test_explicit_tick_values_unaffected_by_label_values(self):
        """Authoring BOTH axis_x.scale.values (dense) and labels.values (sparse):

        tick/grid stays whatever axis_x.scale.values says; labels.values only
        narrows which of those ticks carry label text.
        """
        every_other_year = [f"{y}-01-01" for y in range(2008, 2026, 2)]
        style = BarChartStylePatch(
            axis_x=AxisXStylePatch(
                scale=XScaleStylePatch(values=every_other_year),
                labels=DimensionLabelStylePatch(values=_SPARSE_LABELS),
            )
        )
        data = _annual_data()
        chart = BarChart(
            id="annual_bar", type="bar", x="launch_year", y="launches", style=style
        )
        resolved = resolve(chart, data, chart_style_context=_BOARD_CTX)
        spec = render_resolved_chart(resolved, data, _BOARD_RS).payload

        x_axis = spec["encoding"]["x"]["axis"]
        assert x_axis.get("values") == every_other_year
        assert "indexof(" in x_axis.get("labelExpr", "")


class TestContinuousTemporalEscapeHatch:
    """The same decoupling on the continuous (type: temporal) x-scale."""

    def test_temporal_escape_hatch_label_values_sets_label_expr(self):
        style = LineChartStylePatch(
            axis_x=AxisXStylePatch(
                type="temporal",
                labels=DimensionLabelStylePatch(values=_SPARSE_LABELS),
            )
        )
        data = _annual_data()
        chart = LineChart(
            id="annual_line", type="line", x="launch_year", y="launches", style=style
        )
        resolved = resolve(chart, data, chart_style_context=_BOARD_CTX)
        spec = render_resolved_chart(resolved, data, _BOARD_RS).payload

        x_enc = spec["encoding"]["x"]
        assert x_enc["type"] == "temporal"
        label_expr = x_enc["axis"].get("labelExpr")
        assert label_expr is not None
        assert "indexof(" in label_expr


class TestValidation:
    """No magic — label.values on a non-temporal axis errors loudly."""

    def test_label_values_on_nominal_x_axis_raises(self):
        # LineChart never auto-swaps orientation (unlike bar, which routes a
        # nominal category axis to horizontal/VL-y when data isn't temporal)
        # so its x-encoding reliably exercises build_cartesian_x_encoding.
        style = LineChartStylePatch(
            axis_x=AxisXStylePatch(
                labels=DimensionLabelStylePatch(values=["foo", "bar"])
            )
        )
        data = [
            {"category": "foo", "count": 1},
            {"category": "bar", "count": 2},
            {"category": "baz", "count": 3},
        ]
        chart = LineChart(
            id="cat_line", type="line", x="category", y="count", style=style
        )
        resolved = resolve(chart, data, chart_style_context=_BOARD_CTX)
        with pytest.raises(ChartDataError, match="labels.values"):
            render_resolved_chart(resolved, data, _BOARD_RS)

    def test_label_values_on_bar_auto_orient_horizontal_raises(self):
        # Bar auto-orients horizontal whenever x is nominal (non-temporal) —
        # the common path. _emit_horizontal builds its categorical axis via
        # axis_to_vl(...) directly, bypassing build_cartesian_x_encoding (and
        # therefore its labels.values guard) entirely — regression for the
        # silent no-op this test pins.
        style = BarChartStylePatch(
            axis_x=AxisXStylePatch(labels=DimensionLabelStylePatch(values=["foo"]))
        )
        data = [
            {"category": "foo", "count": 1},
            {"category": "bar", "count": 2},
            {"category": "baz", "count": 3},
        ]
        chart = BarChart(id="cat_bar", type="bar", x="category", y="count", style=style)
        resolved = resolve(chart, data, chart_style_context=_BOARD_CTX)
        assert resolved.orientation == "horizontal"
        with pytest.raises(ChartDataError, match="labels.values"):
            render_resolved_chart(resolved, data, _BOARD_RS)

    def test_label_values_on_forced_horizontal_bar_raises_even_with_iso_x(self):
        # An authored orientation: horizontal (not auto-inferred) with x data
        # that IS a valid ISO date is still rejected: _emit_horizontal's
        # categorical axis is unconditionally VL "nominal" (bar.py y_enc),
        # so there is no date-aware label path to honor labels.values on
        # regardless of whether the values are ISO-shaped. Pins that the
        # guard's cause/remedy name the orientation, not a false ISO-format
        # complaint, on this reachable path.
        style = BarChartStylePatch(
            orientation="horizontal",
            axis_x=AxisXStylePatch(
                labels=DimensionLabelStylePatch(values=["2024-01-01", "2024-07-01"])
            ),
        )
        data = [{"month": f"2024-{i:02d}-01", "v": i * 10} for i in range(1, 13)]
        chart = BarChart(id="ym_bar", type="bar", x="month", y="v", style=style)
        resolved = resolve(chart, data, chart_style_context=_BOARD_CTX)
        assert resolved.orientation == "horizontal"
        with pytest.raises(ChartDataError, match="never honors it"):
            render_resolved_chart(resolved, data, _BOARD_RS)

    def test_label_values_on_quantitative_x_axis_raises(self):
        style = LineChartStylePatch(
            axis_x=AxisXStylePatch(labels=DimensionLabelStylePatch(values=[1, 2, 3]))
        )
        data = [{"x": 1, "y": 1}, {"x": 2, "y": 4}, {"x": 3, "y": 9}]
        chart = LineChart(id="num_line", type="line", x="x", y="y", style=style)
        resolved = resolve(chart, data, chart_style_context=_BOARD_CTX)
        with pytest.raises(ChartDataError, match="labels.values"):
            render_resolved_chart(resolved, data, _BOARD_RS)

    def test_label_values_on_axis_y_raises_at_resolve(self):
        """axis_y.labels is AxisLabelStylePatch — values is structurally absent.

        Authoring via a dict (as YAML parsing would) triggers a ValidationError
        since AxisLabelStylePatch rejects extra inputs.
        """
        from pydantic import ValidationError

        with pytest.raises(ValidationError):
            BarChartStylePatch.model_validate(
                {"axis_y": {"labels": {"values": [1, 2, 3]}}}
            )

    def test_non_iso_label_value_raises_clear_error(self):
        style = BarChartStylePatch(
            axis_x=AxisXStylePatch(
                labels=DimensionLabelStylePatch(values=["not-a-date"])
            )
        )
        data = _annual_data()
        chart = BarChart(
            id="annual_bar", type="bar", x="launch_year", y="launches", style=style
        )
        resolved = resolve(chart, data, chart_style_context=_BOARD_CTX)
        with pytest.raises(ChartDataError, match="ISO"):
            render_resolved_chart(resolved, data, _BOARD_RS)


class TestBucketStringDomains:
    """Bucket-string x data reaching the encoder unnormalized (bar path).

    ISO-shaped bucket strings (YYYY-MM) are UTC-safe under Vega's
    toDate(datum.value) so the membership filter works — allowed. Non-ISO
    bucket labels (Q1 2024) parse local/NaN in JS, which would silently
    blank every label — rejected with the coded error instead.
    """

    def test_yearmonth_bucket_strings_allowed_on_bar(self):
        style = BarChartStylePatch(
            axis_x=AxisXStylePatch(
                labels=DimensionLabelStylePatch(values=["2024-01-01", "2024-07-01"])
            )
        )
        data = [{"m": f"2024-{i:02d}", "v": i * 10} for i in range(1, 13)]
        chart = BarChart(id="ym_bar", type="bar", x="m", y="v", style=style)
        resolved = resolve(chart, data, chart_style_context=_BOARD_CTX)
        spec = render_resolved_chart(resolved, data, _BOARD_RS).payload

        x_axis = spec["encoding"]["x"]["axis"]
        assert "indexof(" in x_axis.get("labelExpr", "")
        # Tick/grid density untouched: one per month bucket.
        assert len(x_axis.get("values", [])) == 12

    def test_non_iso_bucket_strings_rejected(self):
        # "Qn YYYY" is now normalized to ISO on the bar x path too (bar calls
        # the same normalize_labeled_temporal as line — see
        # value-driven-axis-type-inference), so use a shape that's temporal
        # enough to keep bar's orientation vertical (_is_temporal_value
        # matches dash-separated MM-DD-YYYY) but isn't a recognized bucket
        # family (only slash-separated MM/DD/YYYY normalizes) to pin the
        # non-ISO rejection.
        style = BarChartStylePatch(
            axis_x=AxisXStylePatch(
                labels=DimensionLabelStylePatch(values=["2024-01-01"])
            )
        )
        data = [{"m": f"0{i}-15-2024", "v": i * 10} for i in range(1, 5)]
        chart = BarChart(id="mdyd_bar", type="bar", x="m", y="v", style=style)
        resolved = resolve(chart, data, chart_style_context=_BOARD_CTX)
        with pytest.raises(ChartDataError, match="labels.values"):
            render_resolved_chart(resolved, data, _BOARD_RS)

    def test_midnight_datetime_strings_normalize_to_utc_safe_domain(self):
        """'...T00:00:00' strings would parse LOCAL in JS and silently blank
        every label — but the bucketed-time pipeline normalizes them to
        date-only ISO before the encoder, so the filter stays exact. Pin the
        safety property itself: no raise AND every emitted domain value is a
        date-only ISO string (guard test below covers the unnormalized path).
        """
        style = LineChartStylePatch(
            axis_x=AxisXStylePatch(
                labels=DimensionLabelStylePatch(values=["2024-01-01"])
            )
        )
        data = [{"m": f"2024-{i:02d}-01T00:00:00", "v": i * 10} for i in range(1, 13)]
        chart = LineChart(id="dt_line", type="line", x="m", y="v", style=style)
        resolved = resolve(chart, data, chart_style_context=_BOARD_CTX)
        spec = render_resolved_chart(resolved, data, _BOARD_RS).payload

        x_enc = spec["encoding"]["x"]
        assert "indexof(" in x_enc["axis"].get("labelExpr", "")
        for v in x_enc["axis"].get("values", []):
            assert "T" not in v, f"domain value {v!r} is not UTC-safe date-only ISO"

    def test_subdaily_timestamps_on_forced_ordinal_axis_rejected(self):
        """Sub-daily timestamps skip bucket normalization; on an authored
        ordinal axis the raw '...T13:30:00' strings would parse LOCAL in JS
        and silently blank every label — must raise loudly instead."""
        style = LineChartStylePatch(
            axis_x=AxisXStylePatch(
                type="ordinal",
                labels=DimensionLabelStylePatch(values=["2024-01-15"]),
            )
        )
        data = [{"m": f"2024-01-15T{h:02d}:30:00", "v": h} for h in range(6)]
        chart = LineChart(id="hr_line", type="line", x="m", y="v", style=style)
        resolved = resolve(chart, data, chart_style_context=_BOARD_CTX)
        with pytest.raises(ChartDataError, match="labels.values"):
            render_resolved_chart(resolved, data, _BOARD_RS)


class TestFilterExprShape:
    """Pin the emitted expression shape so a refactor can't silently drift
    from the vl_convert-probed form."""

    def test_label_values_filter_expr_full_shape(self):
        from dbt_charts.core.render.chart.time_unit_detect import (
            label_values_filter_expr,
        )

        expr = label_values_filter_expr(["2008-01-01"], "datum.label")
        assert expr == (
            "indexof([1199145600000.0], time(toDate(datum.value))) === -1"
            " ? '' : (datum.label)"
        )


class TestDateObjectEntries:
    """YAML-native dates parse to dt.date objects — the object branch must work too."""

    def test_date_objects_accepted_and_filter_emitted(self):
        sparse_dates = [dt.date(y, 1, 1) for y in (2008, 2010, 2015, 2020, 2025)]
        style = BarChartStylePatch(
            axis_x=AxisXStylePatch(labels=DimensionLabelStylePatch(values=sparse_dates))
        )
        data = _annual_data()
        chart = BarChart(
            id="annual_bar", type="bar", x="launch_year", y="launches", style=style
        )
        resolved = resolve(chart, data, chart_style_context=_BOARD_CTX)
        spec = render_resolved_chart(resolved, data, _BOARD_RS).payload

        label_expr = spec["encoding"]["x"]["axis"].get("labelExpr", "")
        assert "indexof(" in label_expr
        # Same UTC-ms membership list as the ISO-string form: 2008-01-01 UTC.
        assert "1199145600000.0" in label_expr

    def test_z_suffix_datetime_string_parses(self):
        """A '...Z' datetime string parses as UTC."""
        from dbt_charts.core.render.chart.time_unit_detect import (
            label_values_filter_expr,
        )

        expr = label_values_filter_expr(["2008-01-01T00:00:00Z"], "datum.label")
        assert "1199145600000.0" in expr

    def test_z_suffix_date_only_string_is_rejected(self):
        """'2024-01-01Z' is neither an ISO date nor an ISO datetime."""
        from dbt_charts.core.render.chart.time_unit_detect import (
            label_values_filter_expr,
        )

        with pytest.raises(ChartDataError) as excinfo:
            label_values_filter_expr(["2024-01-01Z"], "datum.label")
        assert excinfo.value.code.code == "ERR-LABEL-VALUES-INVALID-DATE"

    def test_datetime_objects_accepted(self):
        sparse = [dt.datetime(2008, 1, 1), dt.datetime(2025, 1, 1)]
        style = BarChartStylePatch(
            axis_x=AxisXStylePatch(labels=DimensionLabelStylePatch(values=sparse))
        )
        data = _annual_data()
        chart = BarChart(
            id="annual_bar", type="bar", x="launch_year", y="launches", style=style
        )
        resolved = resolve(chart, data, chart_style_context=_BOARD_CTX)
        spec = render_resolved_chart(resolved, data, _BOARD_RS).payload
        assert "indexof(" in spec["encoding"]["x"]["axis"].get("labelExpr", "")


class TestFormatInteraction:
    """Authored format still governs label text on the ticks that survive the filter."""

    def test_custom_format_flows_through_label_values_filter(self):
        style = BarChartStylePatch(
            axis_x=AxisXStylePatch(
                labels=DimensionLabelStylePatch(format="%Y", values=_SPARSE_LABELS),
            )
        )
        data = _annual_data()
        chart = BarChart(
            id="annual_bar", type="bar", x="launch_year", y="launches", style=style
        )
        resolved = resolve(chart, data, chart_style_context=_BOARD_CTX)
        spec = render_resolved_chart(resolved, data, _BOARD_RS).payload

        x_axis = spec["encoding"]["x"]["axis"]
        # Ordinal bucketed-time axes route a d3-time-format `format` string
        # into an explicit utcFormat(...) labelExpr (see type_inference.py);
        # the label.values filter must wrap THAT, not silently drop it.
        assert "format" not in x_axis
        label_expr = x_axis.get("labelExpr", "")
        assert "%Y" in label_expr
        assert "indexof(" in label_expr
