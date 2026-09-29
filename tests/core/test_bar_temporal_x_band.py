# Regression: bar charts on temporal x-axis rendered as thin sticks because
# ``map_x_encoding`` emitted ``scale.type=utc`` (continuous), so Vega-Lite drew
# 5px-wide bars at each exact timestamp. At narrow column widths (e.g. 3-col
# layout on 1200px page → ~400px/col) those sticks collapsed into one visual
# position. The 955 ARR by Segment chart was the repro: 12 months × 2 segments
# stacked into one ~10px column.
#
# Test pins the *invariant* (some form of discrete bands) without locking in
# the implementation: ordinal/nominal x-type, OR ``timeUnit``, OR
# ``scale.type=band`` all satisfy the bar-width requirement.

from __future__ import annotations

from dbt_charts.core.compile.config import get_theme_style
from dbt_charts.core.compile.models.chart.normalized import BarChart, Chart
from dbt_charts.core.compile.models.style.authored import (
    AxisXStylePatch,
    BarChartStylePatch,
    DimensionLabelStylePatch,
)
from dbt_charts.core.compile.resolve import resolve
from dbt_charts.core.compile.resolve.style.board import resolve_style_and_context
from dbt_charts.core.render.chart.vega_lite import render_resolved_chart

_RESOLVED_STYLE, _BOARD_STYLE = resolve_style_and_context(get_theme_style())


def _bar_with_color() -> Chart:
    return BarChart(
        id="arr_by_segment_repro",
        type="bar",
        x="month",
        y="revenue",
        color="segment",
        style=BarChartStylePatch(
            axis_x=AxisXStylePatch(labels=DimensionLabelStylePatch(format="%b %Y"))
        ),
    )


def _12_months_2_segments() -> list[dict]:
    rows = []
    for m in range(1, 13):
        for seg in ("ENTERPRISE", "COMMERCIAL"):
            rows.append(
                {
                    "month": f"2025-{m:02d}-01",
                    "revenue": 1_000_000 + m * 10_000,
                    "segment": seg,
                }
            )
    return rows


class TestBarTemporalAxisProducesDiscreteBands:
    def test_bar_on_temporal_x_uses_discrete_axis(self) -> None:
        # The fix can be either x.type=="ordinal" OR a timeUnit/band scale.
        # Pin the invariant — not the implementation: the spec must NOT produce
        # 5px sticks at exact temporal timestamps. That means EITHER the type
        # is discrete OR timeUnit is set OR scale.type is band.
        chart = _bar_with_color()
        data = _12_months_2_segments()
        resolved = resolve(chart, data, chart_style_context=_BOARD_STYLE)
        vl_spec = render_resolved_chart(resolved, data, _RESOLVED_STYLE).payload

        x_enc = vl_spec.get("encoding", {}).get("x", {})
        is_discrete = x_enc.get("type") in ("ordinal", "nominal")
        has_time_unit = "timeUnit" in x_enc
        has_band_scale = x_enc.get("scale", {}).get("type") == "band"

        assert is_discrete or has_time_unit or has_band_scale, (
            f"Bar chart on temporal x must produce discrete bands so bars get "
            f"proper width (not 5px sticks). Got x encoding: {x_enc}. Either "
            f"type='ordinal'/'nominal', or add timeUnit, or scale.type='band'."
        )

    def test_bar_on_temporal_x_preserves_time_format_label(self) -> None:
        # Whatever fix is applied, ``%b %Y`` labels must still render. On the
        # temporal path d3-time-format applies natively (axis.format); on the
        # discrete path the ``format`` directive is rewritten into a labelExpr
        # (`utcFormat(toDate(datum.value), '%b %Y')`) — emitting ``formatType:
        # "time"`` on a string-domain ordinal scale silently drops every label.
        chart = _bar_with_color()
        data = _12_months_2_segments()
        resolved = resolve(chart, data, chart_style_context=_BOARD_STYLE)
        vl_spec = render_resolved_chart(resolved, data, _RESOLVED_STYLE).payload

        x_enc = vl_spec.get("encoding", {}).get("x", {})
        x_axis = x_enc.get("axis", {})
        if x_enc.get("type") in ("ordinal", "nominal"):
            # `in`, not `==`: a discrete axis keeps one tick per bucket, so
            # the expression also carries the gate deciding which of them
            # speak (test_authored_format_keeps_the_cadence_gate.py). What
            # this test pins is unchanged — the format reaches the labels,
            # and neither `format` nor `formatType: "time"` survives on a
            # string-domain scale, where they would drop every label.
            assert "utcFormat(toDate(datum.value), '%b %Y')" in str(
                x_axis.get("labelExpr", "")
            ), f"discrete axis must route time format through labelExpr: {x_axis}"
            assert "format" not in x_axis
            assert x_axis.get("formatType") != "time"
        else:
            assert x_axis.get("format") == "%b %Y", (
                f"temporal axis.format must round-trip; got {x_axis}"
            )


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


def _bar_marks(vl_spec: dict) -> list[dict]:
    """Every visible bar-mark dict in vl_spec's top-level layers, in order."""
    return [
        layer["mark"]
        for layer in vl_spec.get("layer", [])
        if isinstance(layer.get("mark"), dict)
        and layer["mark"].get("type") == "bar"
        and layer["mark"].get("opacity") != 0
    ]


def _first_bar_mark(vl_spec: dict) -> dict:
    marks = _bar_marks(vl_spec)
    if not marks:
        raise AssertionError(f"no bar-mark layer found in: {vl_spec.get('layer')}")
    return marks[0]


class TestBarOnTemporalXKeepsCornerRadius:
    """The bucket-count density gate (type_inference.py) flips a bar chart's
    bucketed-calendar x-axis to a continuous temporal scale once the bucket
    count exceeds MAX_ORDINAL_BUCKETS. Corner radius must survive that: VL's
    native relative mark width (``{"band": fraction}``) already renders bars
    correctly positioned at every density, on both temporal and ordinal
    scales, under faceting, multi-layer shared scales, and grouped-bar
    spacing — so there is no reason to touch it. A separate, corner-radius-
    independent phenomenon exists where this theme's background-colored halo
    stroke can visually swallow a bar once its own width drops below ~1px, at
    any x-scale type — a stroke-sizing question, unaffected by this class.
    """

    def test_dense_monthly_bar_temporal_x_keeps_corner_radius(self) -> None:
        # Explicit style override (not the theme default) — behavior under
        # override, not a pinned default: dbt-charts/AGENTS.md's Implementation
        # philosophy forbids pinning theme values like border.radius/band_width.
        chart = BarChart(
            id="dense_monthly",
            type="bar",
            x="month",
            y="revenue",
            style=BarChartStylePatch(
                marks={"bar": {"band_width": 0.6, "border": {"radius": 9}}}
            ),
        )
        data = [
            {"month": d, "revenue": 1000 + i} for i, d in enumerate(_monthly_dates(480))
        ]
        resolved = resolve(chart, data, chart_style_context=_BOARD_STYLE)
        vl_spec = render_resolved_chart(resolved, data, _RESOLVED_STYLE).payload

        x_enc = vl_spec.get("encoding", {}).get("x", {})
        assert x_enc.get("type") == "temporal", (
            "480 monthly buckets must trip the density gate to temporal; "
            f"got x encoding: {x_enc}"
        )
        mark_props = _first_bar_mark(vl_spec)
        assert mark_props.get("cornerRadiusEnd") == 9.0, (
            "bar mark must keep its configured corner radius on a temporal "
            f"x-scale. Got: {mark_props}"
        )
        assert mark_props.get("width") == {"band": 0.6}, (
            "bar mark width on a temporal x-scale must stay VL's native "
            f"relative shorthand — untouched by this fix. Got: {mark_props}"
        )

    def test_sparse_monthly_bar_keeps_corner_radius(self) -> None:
        # Control: under the density gate threshold, x stays ordinal and the
        # default rounded-top-corner bar styling is untouched.
        chart = BarChart(id="sparse_monthly", type="bar", x="month", y="revenue")
        data = [
            {"month": d, "revenue": 1000 + i} for i, d in enumerate(_monthly_dates(24))
        ]
        resolved = resolve(chart, data, chart_style_context=_BOARD_STYLE)
        vl_spec = render_resolved_chart(resolved, data, _RESOLVED_STYLE).payload

        x_enc = vl_spec.get("encoding", {}).get("x", {})
        assert x_enc.get("type") == "ordinal"
        mark_props = _first_bar_mark(vl_spec)
        assert mark_props.get("cornerRadiusEnd") == 2.0
        width = mark_props.get("width")
        assert isinstance(width, dict) and "band" in width, (
            f"ordinal path keeps VL's native relative shorthand. Got: {width!r}"
        )

    def test_dense_monthly_mixed_sign_bar_splits_correctly_on_temporal_x(self) -> None:
        # The mixed-sign two-layer split in emit_bar_layer is gated on
        # radius is not None — pin it produces the same pos/neg tip-corner
        # shape on a temporal x as the ordinal case (test_negative_bar_edge_
        # rounding.py). Explicit override, not the theme default, so a theme
        # change to marks.bar.border.radius can't silently make this a no-op.
        chart = BarChart(
            id="dense_monthly_mixed",
            type="bar",
            x="month",
            y="revenue",
            style=BarChartStylePatch(marks={"bar": {"border": {"radius": 4}}}),
        )
        data = [
            {"month": d, "revenue": (1000 + i) if i % 2 == 0 else -(1000 + i)}
            for i, d in enumerate(_monthly_dates(480))
        ]
        resolved = resolve(chart, data, chart_style_context=_BOARD_STYLE)
        vl_spec = render_resolved_chart(resolved, data, _RESOLVED_STYLE).payload

        x_enc = vl_spec.get("encoding", {}).get("x", {})
        assert x_enc.get("type") == "temporal"

        bar_marks = _bar_marks(vl_spec)
        assert len(bar_marks) == 2, f"expected 2 visible bar layers; got {bar_marks}"
        pos_mark, neg_mark = bar_marks
        assert pos_mark.get("cornerRadiusEnd") == 4.0
        assert neg_mark.get("cornerRadiusBottomLeft") == 4.0
        assert neg_mark.get("cornerRadiusBottomRight") == 4.0
        assert "cornerRadiusEnd" not in neg_mark
