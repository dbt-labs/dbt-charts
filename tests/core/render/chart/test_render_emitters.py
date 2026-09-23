"""Tests for render-v2 family emitters and the get_emitter() registry."""

from __future__ import annotations

import dataclasses
from typing import Any

import pytest

from dbt_charts.core.compile.models.chart.resolved import (
    ResolvedAreaChart,
    ResolvedAreaStyle,
    ResolvedBarChart,
    ResolvedBarStyle,
    ResolvedGeoshapeChart,
    ResolvedGeoshapeStyle,
    ResolvedHeatmapChart,
    ResolvedHeatmapStyle,
    ResolvedLineChart,
    ResolvedLineStyle,
    ResolvedPieChart,
    ResolvedPieStyle,
    ResolvedPointMapChart,
    ResolvedPointMapStyle,
    ResolvedScatterChart,
    ResolvedScatterStyle,
)
from dbt_charts.core.compile.models.style.authored import EndpointLabelsConfig
from dbt_charts.core.compile.models.style.theme import PaddingStyle
from dbt_charts.core.compile.resolve.chart._chart_rows import regroup
from dbt_charts.core.compile.resolve.chart.label_data import (
    pie_presentation_fingerprint,
)
from dbt_charts.core.render.chart.spec import ChartSpec, RenderBox


# Required base fields (no defaults on non-None resolved model fields).
def _default_legend():
    from dbt_charts.core.compile.config import get_default_theme_name, get_theme_style
    from dbt_charts.core.compile.resolve.style.board import resolve_style

    return resolve_style(
        get_theme_style(get_default_theme_name())
    ).chart_defaults.legend


def _default_charts():
    from dbt_charts.core.compile.config import get_default_theme_name, get_theme_style
    from dbt_charts.core.compile.resolve.style.board import resolve_style

    return resolve_style(get_theme_style(get_default_theme_name())).chart_defaults


_DEFAULT_CHARTS = _default_charts()
_ZERO_PADDING = PaddingStyle(left=0.0, right=0.0, top=0.0, bottom=0.0)
_B: dict[str, Any] = {
    "variable_dependencies": frozenset(),
    "palette": (),
    "resolved_channels": {},
    "legend": _default_legend(),
    "background": _DEFAULT_CHARTS.background,
    "title_style": _DEFAULT_CHARTS.title,
    "layout_padding": _ZERO_PADDING,
}
_C: dict[str, Any] = dict(_B)
# Pie charts always resolve a non-empty palette (_resolve_pie raises
# ChartDataError otherwise) — pie fixtures need a real palette, unlike _B/_C.
_PB: dict[str, Any] = {**_B, "palette": ("#3164a3",)}

_DEFAULT_BOX = RenderBox(width=600.0, height=300.0)

# ---------------------------------------------------------------------------
# Style helper
# ---------------------------------------------------------------------------


def _make_style() -> Any:
    from dbt_charts.core.compile.config import get_default_theme_name, get_theme_style
    from dbt_charts.core.compile.resolve.style.board import resolve_chart_style_context

    return resolve_chart_style_context(get_theme_style(get_default_theme_name()))


def _bar(bar_style: ResolvedBarStyle) -> ResolvedBarChart:
    ax, ay = _make_resolved_axes("bar", "ordinal", "quantitative")
    return ResolvedBarChart(
        panel_axes=(),
        id="bar1",
        chart_type="bar",
        x="month",
        y="revenue",
        style=bar_style.model_copy(update={"axis_x": ax, "axis_y": ay}),
        **_C,
    )


def _line(line_style: ResolvedLineStyle) -> ResolvedLineChart:
    return ResolvedLineChart(
        panel_axes=(),
        id="line1",
        chart_type="line",
        x="date",
        y="value",
        style=line_style,
        **_C,
    )


def _area(area_style: ResolvedAreaStyle) -> ResolvedAreaChart:
    return ResolvedAreaChart(
        panel_axes=(),
        id="area1",
        chart_type="area",
        x="date",
        y="value",
        style=area_style,
        **_C,
    )


def _make_resolved_axes(
    chart_type: str = "scatter",
    x_type: str = "quantitative",
    y_type: str = "quantitative",
) -> tuple[Any, Any]:
    """Create minimal resolved axes for emitter tests."""
    from dbt_charts.core.compile.config import get_theme_style
    from dbt_charts.core.compile.resolve.chart._axes import _bake_cartesian_axes
    from dbt_charts.core.compile.resolve.style.axis_cascade import (
        AxisOverrides,
        build_resolved_axis,
    )
    from dbt_charts.core.compile.resolve.style.board import resolve_chart_style_context

    from ...conftest import fixture_chart_for_type

    chart_style_context = resolve_chart_style_context(get_theme_style())
    ax_merged, ay_merged, ax_band_position, ay_band_position, _, _ = (
        _bake_cartesian_axes(
            chart_style_context,
            fixture_chart_for_type(chart_type),
            chart_type,
            x_type,
            y_type,
            AxisOverrides(),
        )
    )
    return (
        build_resolved_axis(
            ax_merged,
            band_position=ax_band_position,
            chart_id="test",
            format_authored=True,
            format_is_alias=False,
        ),
        build_resolved_axis(
            ay_merged,
            band_position=ay_band_position,
            chart_id="test",
            format_authored=True,
            format_is_alias=False,
        ),
    )


def _scatter(scatter_style: ResolvedScatterStyle) -> ResolvedScatterChart:
    ax, ay = _make_resolved_axes()
    ay = dataclasses.replace(ay, is_quantitative=True, zero_anchored=True)
    return ResolvedScatterChart(
        panel_axes=(),
        id="scatter1",
        chart_type="scatter",
        x="x_val",
        y="y_val",
        style=scatter_style.model_copy(update={"axis_x": ax, "axis_y": ay}),
        **_C,
    )


def _heatmap(heatmap_style: ResolvedHeatmapStyle) -> ResolvedHeatmapChart:
    hm_ax, hm_ay = _make_resolved_axes("heatmap", "nominal", "nominal")
    return ResolvedHeatmapChart(
        panel_axes=(),
        id="heatmap1",
        chart_type="heatmap",
        x="col",
        y="row",
        style=heatmap_style.model_copy(update={"axis_x": hm_ax, "axis_y": hm_ay}),
        **_C,
    )


def _pie(pie_style: ResolvedPieStyle) -> ResolvedPieChart:
    return ResolvedPieChart(
        id="pie1",
        chart_type="pie",
        resolution_width=600.0,
        outer_fraction=0.9,
        attached_table_gap=12.0,
        hybrid_heading_gap=6.0,
        presentation_fingerprint=pie_presentation_fingerprint([]),
        slice_label_indices=(),
        theta="sales",
        style=pie_style,
        dark_companion_stops=("#0e4786",),
        **_PB,
    )


def _geoshape(geoshape_style: ResolvedGeoshapeStyle) -> ResolvedGeoshapeChart:
    return ResolvedGeoshapeChart(
        id="geo1", chart_type="geoshape", style=geoshape_style, **_B
    )


def _point_map(point_map_style: ResolvedPointMapStyle) -> ResolvedPointMapChart:
    return ResolvedPointMapChart(
        id="pm1",
        chart_type="point_map",
        projection="albersUsa",
        collapse=False,
        style=point_map_style,
        **_B,
    )


# ---------------------------------------------------------------------------
# emit() returns a ChartSpec
# ---------------------------------------------------------------------------


def test_bar_emit_returns_chart_spec(bar_style: ResolvedBarStyle) -> None:
    from dbt_charts.core.render.chart.emitters import (
        get_emitter,
    )

    assert isinstance(
        get_emitter(_bar(bar_style)).emit(
            _bar(bar_style), _DEFAULT_BOX, regroup((), [])
        ),
        ChartSpec,
    )


def test_line_emit_returns_chart_spec(line_style: ResolvedLineStyle) -> None:
    from dbt_charts.core.render.chart.emitters import (
        get_emitter,
    )

    assert isinstance(
        get_emitter(_line(line_style)).emit(
            _line(line_style), _DEFAULT_BOX, regroup((), [])
        ),
        ChartSpec,
    )


def test_area_emit_returns_chart_spec(area_style: ResolvedAreaStyle) -> None:
    from dbt_charts.core.render.chart.emitters import (
        get_emitter,
    )

    assert isinstance(
        get_emitter(_area(area_style)).emit(
            _area(area_style), _DEFAULT_BOX, regroup((), [])
        ),
        ChartSpec,
    )


def test_scatter_emit_returns_chart_spec(scatter_style: ResolvedScatterStyle) -> None:
    from dbt_charts.core.render.chart.emitters import (
        get_emitter,
    )

    chart = _scatter(scatter_style)
    assert isinstance(
        get_emitter(chart).emit(chart, _DEFAULT_BOX, regroup((), [])), ChartSpec
    )


def test_heatmap_emit_returns_chart_spec(heatmap_style: ResolvedHeatmapStyle) -> None:
    from dbt_charts.core.render.chart.emitters import (
        get_emitter,
    )

    chart = _heatmap(heatmap_style)
    assert isinstance(
        get_emitter(chart).emit(chart, _DEFAULT_BOX, regroup((), [])), ChartSpec
    )


def test_pie_emit_returns_chart_spec(pie_style: ResolvedPieStyle) -> None:
    from dbt_charts.core.render.chart.emitters import (
        get_emitter,
    )

    assert isinstance(
        get_emitter(_pie(pie_style)).emit(
            _pie(pie_style), _DEFAULT_BOX, regroup((), [])
        ),
        ChartSpec,
    )


def test_geoshape_emit_returns_chart_spec(
    geoshape_style: ResolvedGeoshapeStyle,
) -> None:
    from dbt_charts.core.render.chart.emitters import (
        get_emitter,
    )

    assert isinstance(
        get_emitter(_geoshape(geoshape_style)).emit(
            _geoshape(geoshape_style), _DEFAULT_BOX, regroup((), [])
        ),
        ChartSpec,
    )


def test_point_map_emit_returns_chart_spec(
    point_map_style: ResolvedPointMapStyle,
) -> None:
    from dbt_charts.core.render.chart.emitters import (
        get_emitter,
    )

    assert isinstance(
        get_emitter(_point_map(point_map_style)).emit(
            _point_map(point_map_style), _DEFAULT_BOX, regroup((), [])
        ),
        ChartSpec,
    )


def test_get_emitter_unregistered_type_raises_df_coded_error() -> None:
    """Dispatch fall-through indicates an engine bug (normalizer should have
    rejected the chart before render) and must stamp ERR-EMITTER-NOT-FOUND,
    not a bare ValueError."""
    from dbt_charts.core.render.chart.emitters import get_emitter
    from dbt_charts.core.render.errors import RenderError

    with pytest.raises(RenderError, match="object") as excinfo:
        get_emitter(object())
    assert excinfo.value.code is not None
    assert excinfo.value.code.code == "ERR-EMITTER-NOT-FOUND"


# ---------------------------------------------------------------------------
# ChartSpec.mark == expected family mark name
# ---------------------------------------------------------------------------


def test_bar_emit_mark_is_bar(bar_style: ResolvedBarStyle) -> None:
    """Bar emitter returns a flat spec; the hover band feature (BarHoverBandFeature)
    promotes it to layered after emit."""
    from dbt_charts.core.render.chart.emitters import (
        get_emitter,
    )

    assert (
        get_emitter(_bar(bar_style))
        .emit(_bar(bar_style), _DEFAULT_BOX, regroup((), []))
        .mark
        == "bar"
    )


def test_line_emit_mark_is_layered(line_style: ResolvedLineStyle) -> None:
    # Line emitter produces a multi-layer spec (halo + fg + hover).
    from dbt_charts.core.render.chart.emitters import (
        get_emitter,
    )

    assert (
        get_emitter(_line(line_style))
        .emit(_line(line_style), _DEFAULT_BOX, regroup((), []))
        .mark
        == "layered"
    )


def test_area_emit_mark_is_layered(area_style: ResolvedAreaStyle) -> None:
    # Area emitter produces a multi-layer spec (halo area+line, fg area+line, hover).
    from dbt_charts.core.render.chart.emitters import (
        get_emitter,
    )

    assert (
        get_emitter(_area(area_style))
        .emit(_area(area_style), _DEFAULT_BOX, regroup((), []))
        .mark
        == "layered"
    )


def test_area_curve_reaches_every_area_and_line_sublayer(
    area_style: ResolvedAreaStyle,
) -> None:
    # A plain area curve must map to VL ``interpolate`` on EVERY area- and
    # line-type sub-layer (halo fill, halo line, fg fill, fg line) so the fill
    # and its top-edge stroke agree. The fixture has halo_multiplier=2.0 +
    # stroke width 2.0, so the multi-layer halo branch is exercised. Guards the
    # emitter's per-sub-layer interpolate stamping, which the band-step tests
    # (curve="step" on a nominal/ordinal x) don't pin for a non-band curve.
    # A real date-like row (not []) resolves the "date" x field to temporal —
    # `curve: step` on a continuous x is a plain passthrough, not band mode
    # (empty data would default the x type to nominal, wrongly triggering the
    # band-aware step transform this test isn't exercising).
    from dbt_charts.core.render.chart.emitters import get_emitter

    stepped_area_mark = area_style.area_mark.model_copy(update={"curve": "step"})
    stepped = area_style.model_copy(update={"area_mark": stepped_area_mark})
    data = [{"date": "2024-01-01", "value": 10.0}]
    spec = get_emitter(_area(stepped)).emit(
        _area(stepped), _DEFAULT_BOX, regroup((), data)
    )
    path_layers = [lyr for lyr in spec.layers if lyr.mark in ("area", "line")]
    assert len(path_layers) >= 2, (
        f"Expected the multi-layer halo branch, got {[lyr.mark for lyr in spec.layers]!r}"
    )
    for lyr in path_layers:
        assert lyr.mark_props.get("interpolate") == "step", (
            f"Every area/line sub-layer must carry interpolate='step'; "
            f"got mark={lyr.mark!r} props={lyr.mark_props!r}"
        )


def test_area_curve_absent_emits_no_interpolate(
    area_style: ResolvedAreaStyle,
) -> None:
    # Default fixture has curve=None → no sub-layer carries interpolate (VL default).
    from dbt_charts.core.render.chart.emitters import get_emitter

    spec = get_emitter(_area(area_style)).emit(
        _area(area_style), _DEFAULT_BOX, regroup((), [])
    )
    assert all("interpolate" not in lyr.mark_props for lyr in spec.layers), (
        f"No sub-layer should carry interpolate when curve is None; got {[lyr.mark_props for lyr in spec.layers]!r}"
    )


def test_area_point_mark_size_adds_visible_point_overlay(
    area_style: ResolvedAreaStyle,
) -> None:
    """Authoring `style.marks.point.size` on an area chart adds a real,
    visible point-overlay sub-layer at each plotted value — mirroring
    Vega-Lite's own native `mark: {type: area, point: true}` (which compiles
    to a genuine separate symbol mark). Size 0/None (default) must add no
    such layer — points are opt-in, matching the line-chart precedent."""
    from dbt_charts.core.compile.models.style.theme import PointMarkStyle
    from dbt_charts.core.render.chart.emitters import get_emitter

    # Default fixture has point_mark unset (size None/0) — no point overlay.
    spec = get_emitter(_area(area_style)).emit(
        _area(area_style), _DEFAULT_BOX, regroup((), [])
    )
    assert not any(
        lyr.mark == "point" and lyr.mark_props.get("opacity") != 0
        for lyr in spec.layers
    ), "No visible point overlay expected when point_mark.size is unset"

    with_points = area_style.model_copy(
        update={"point_mark": PointMarkStyle(size=60.0)}
    )
    spec = get_emitter(_area(with_points)).emit(
        _area(with_points), _DEFAULT_BOX, regroup((), [])
    )
    visible_points = [
        lyr
        for lyr in spec.layers
        if lyr.mark == "point" and lyr.mark_props.get("opacity") != 0
    ]
    assert len(visible_points) == 1, (
        f"point_mark.size=60 must add exactly one visible point sub-layer; "
        f"got {[lyr.mark_props for lyr in spec.layers if lyr.mark == 'point']!r}"
    )
    assert visible_points[0].mark_props.get("size") == 60.0


def test_area_stroke_width_zero_removes_top_edge_stroke(
    area_style: ResolvedAreaStyle,
) -> None:
    """Authoring `style.marks.line.stroke.width: 0` on an area chart must
    actually remove the foreground top-edge stroke sub-layer. Vega-Lite
    compiles an area's edge as a genuine separate line mark, so its stroke
    geometry lives on `line_mark` (not `area_mark`, which is fill-only)."""
    from dbt_charts.core.compile.models.style.resolved import ResolvedStrokeStyle
    from dbt_charts.core.render.chart.emitters import get_emitter

    zeroed_stroke = area_style.line_mark.stroke.model_copy(update={"width": 0.0})
    zeroed_line_mark = area_style.line_mark.model_copy(update={"stroke": zeroed_stroke})
    zeroed = area_style.model_copy(update={"line_mark": zeroed_line_mark})
    assert isinstance(zeroed.line_mark.stroke, ResolvedStrokeStyle)

    spec = get_emitter(_area(zeroed)).emit(_area(zeroed), _DEFAULT_BOX, regroup((), []))
    fg_line_layers = [
        lyr
        for lyr in spec.layers
        if lyr.mark == "line" and lyr.mark_props.get("opacity") != 0
    ]
    assert fg_line_layers == [], (
        f"stroke.width=0 must drop the fg top-edge line sub-layer entirely; "
        f"got {[lyr.mark_props for lyr in fg_line_layers]!r}"
    )


def test_scatter_emit_mark_is_point(scatter_style: ResolvedScatterStyle) -> None:
    from dbt_charts.core.render.chart.emitters import (
        get_emitter,
    )

    chart = _scatter(scatter_style)
    assert get_emitter(chart).emit(chart, _DEFAULT_BOX, regroup((), [])).mark == "point"


def test_heatmap_emit_mark_is_rect(heatmap_style: ResolvedHeatmapStyle) -> None:
    from dbt_charts.core.render.chart.emitters import (
        get_emitter,
    )

    chart = _heatmap(heatmap_style)
    assert get_emitter(chart).emit(chart, _DEFAULT_BOX, regroup((), [])).mark == "rect"


def test_pie_emit_mark_is_arc(pie_style: ResolvedPieStyle) -> None:
    from dbt_charts.core.render.chart.emitters import (
        get_emitter,
    )

    spec = get_emitter(_pie(pie_style)).emit(
        _pie(pie_style), _DEFAULT_BOX, regroup((), [])
    )
    assert spec.mark == "layered", (
        f"pie outer spec must be 'layered', got {spec.mark!r}"
    )
    assert spec.layers[0].mark == "arc", (
        f"pie arc layer must have mark 'arc', got {spec.layers[0].mark!r}"
    )


def test_geoshape_emit_mark_is_geoshape(geoshape_style: ResolvedGeoshapeStyle) -> None:
    from dbt_charts.core.render.chart.emitters import (
        get_emitter,
    )

    assert (
        get_emitter(_geoshape(geoshape_style))
        .emit(_geoshape(geoshape_style), _DEFAULT_BOX, regroup((), []))
        .mark
        == "geoshape"
    )


def test_point_map_emit_mark_is_circle(
    point_map_style: ResolvedPointMapStyle,
) -> None:
    from dbt_charts.core.render.chart.emitters import (
        get_emitter,
    )

    assert (
        get_emitter(_point_map(point_map_style))
        .emit(_point_map(point_map_style), _DEFAULT_BOX, regroup((), []))
        .mark
        == "circle"
    )


def test_point_map_size_scale_pins_domain_floor_to_zero(
    point_map_style: ResolvedPointMapStyle,
) -> None:
    """The area-proportional size scale relies on VL's domain floor being
    literally 0 — but `zero: true` alone only EXTENDS a domain to include
    0, it does not CLAMP the floor there. With a negative-valued row VL
    would otherwise derive domain `[min<0, max]` from the data extent,
    making a value of exactly 0 no longer map to area 0. An explicit
    `domainMin: 0` is what actually pins the floor regardless of data."""
    from dbt_charts.core.render.chart.emitters import get_emitter

    chart = ResolvedPointMapChart(
        id="pm1",
        chart_type="point_map",
        projection="albersUsa",
        collapse=False,
        size="magnitude",
        style=point_map_style,
        **_B,
    )
    spec = get_emitter(chart).emit(chart, _DEFAULT_BOX, regroup((), []))
    assert spec.encoding["size"]["scale"]["domainMin"] == 0


# ---------------------------------------------------------------------------
# Structural encoding assertions — key presence only, not values
# ---------------------------------------------------------------------------


def test_bar_emit_encoding_has_x_and_y(bar_style: ResolvedBarStyle) -> None:
    from dbt_charts.core.render.chart.emitters import (
        get_emitter,
    )

    spec = get_emitter(_bar(bar_style)).emit(
        _bar(bar_style), _DEFAULT_BOX, regroup((), [])
    )
    assert "x" in spec.encoding
    assert "y" in spec.encoding


def test_line_emit_encoding_has_x_and_y(line_style: ResolvedLineStyle) -> None:
    from dbt_charts.core.render.chart.emitters import (
        get_emitter,
    )

    spec = get_emitter(_line(line_style)).emit(
        _line(line_style), _DEFAULT_BOX, regroup((), [])
    )
    assert "x" in spec.encoding
    assert "y" in spec.encoding


def test_line_emit_point_overlay_layers_when_point_size_set() -> None:
    """When point_mark.size > 0, emitter produces halo-point and fg-point layers."""
    from dbt_charts.core.compile.models.style.resolved import (
        ResolvedLineMarkStyle,
        ResolvedSeriesLabelStyle,
        ResolvedStrokeStyle,
    )
    from dbt_charts.core.compile.models.style.theme import (
        PointLabelsStyle,
        PointMarkStyle,
    )
    from dbt_charts.core.render.chart.emitters import get_emitter
    from dbt_charts.core.render.chart.translate import translate_to_vl

    pt_size = 36.0
    halo_mult = 2.0
    bg = "#FAF7F0"
    fg_color = "#443924"
    _ax, _ay = _make_resolved_axes("line", "temporal", "quantitative")

    style = ResolvedLineStyle(
        series_label=ResolvedSeriesLabelStyle(
            font_family="Inter",
            font_size=11.0,
            font_weight="400",
            font_style="normal",
            dark_companion_palette=(),
            gap_px=18.2,
        ),
        line_mark=ResolvedLineMarkStyle(
            stroke=ResolvedStrokeStyle(width=3.0),
            halo_multiplier=halo_mult,
            curve=None,
            labels=PointLabelsStyle(),
        ),
        point_mark=PointMarkStyle(size=pt_size, filled=True, stroke_width=3.0),
        endpoint_labels=EndpointLabelsConfig(
            visible=False, label_offset=8.0, height=20.0
        ),
        single_series_fill=fg_color,
        tooltip_format="",
        label_usable_ratio=0.8,
        dashes=[],
        axis_x=_ax,
        axis_y=_ay,
    )
    chart = ResolvedLineChart(
        panel_axes=(),
        id="line1",
        chart_type="line",
        x="date",
        y="value",
        style=style,
        **{**_C, "background": bg},
    )
    spec = get_emitter(chart).emit(chart, _DEFAULT_BOX, regroup((), []))
    vl = translate_to_vl(spec)
    layers = vl["layer"]

    # layer order: halo_line, halo_point, fg_line, fg_point, hover_overlay
    assert len(layers) == 5, (
        f"expected 5 layers, got {len(layers)}: {[layer['mark'] for layer in layers]}"
    )

    halo_pt = layers[1]["mark"]
    assert halo_pt["type"] == "point"
    assert halo_pt["filled"] is True
    assert halo_pt["fill"] == bg
    assert halo_pt["stroke"] == bg
    assert halo_pt["size"] == pt_size * halo_mult
    assert halo_pt["opacity"] == 1
    assert halo_pt["tooltip"] is False

    fg_pt = layers[3]["mark"]
    assert fg_pt["type"] == "point"
    assert fg_pt["size"] == pt_size
    assert fg_pt["opacity"] == 1
    assert fg_pt["fillOpacity"] == 1
    assert fg_pt["strokeOpacity"] == 1
    assert fg_pt["tooltip"] is True
    assert fg_pt["color"] == fg_color


def test_line_emit_no_point_layers_when_point_size_unset(
    line_style: ResolvedLineStyle,
) -> None:
    """When point_mark.size is None, emitter produces only 3 layers (halo_line, fg_line, hover)."""
    from dbt_charts.core.render.chart.emitters import get_emitter
    from dbt_charts.core.render.chart.translate import translate_to_vl

    spec = get_emitter(_line(line_style)).emit(
        _line(line_style), _DEFAULT_BOX, regroup((), [])
    )
    vl = translate_to_vl(spec)
    layers = vl["layer"]
    marks = [lay["mark"] for lay in layers]
    visible_points = [
        m
        for m in marks
        if isinstance(m, dict) and m.get("type") == "point" and m.get("opacity", 1) != 0
    ]
    assert visible_points == [], (
        f"expected no visible point layers, got {visible_points}"
    )


def test_line_dashes_merge_color_and_strokedash_legend(
    line_style: ResolvedLineStyle,
) -> None:
    """A series line with style.dashes must pin color + strokeDash to the same
    explicit domain (so the baseline rule's field-less rows don't add an
    ``undefined`` member and the two legends merge) and mark the color legend as
    a wide stroke sample so dash patterns read as line swatches, not circles."""
    from dbt_charts.core.compile.models.chart.resolved import ResolvedStyleChannel
    from dbt_charts.core.render.chart.emitters import get_emitter

    color_ch = ResolvedStyleChannel(channel="color", mode="series", data_field="series")
    shown_legend = _default_legend().model_copy(update={"visible": True})
    chart = ResolvedLineChart(
        panel_axes=(),
        id="dash_line",
        chart_type="line",
        x="month",
        y="value",
        style=line_style.model_copy(update={"dashes": [[12, 16], [2, 6], []]}),
        **{
            **_C,
            "resolved_channels": {"color": color_ch},
            "legend": shown_legend,
        },
    )
    data = [
        {"month": "2025-01", "value": 1, "series": "alpha"},
        {"month": "2025-01", "value": 2, "series": "bravo"},
        {"month": "2025-01", "value": 3, "series": "charlie"},
    ]
    spec = get_emitter(chart).emit(chart, _DEFAULT_BOX, regroup((), data))
    color_enc = spec.encoding["color"]
    dash_enc = spec.encoding["strokeDash"]
    domain = ["alpha", "bravo", "charlie"]
    assert color_enc["scale"]["domain"] == domain
    assert dash_enc["scale"]["domain"] == domain
    assert dash_enc["scale"]["range"] == [[12, 16], [2, 6], []]
    assert color_enc["legend"]["symbolType"] == "stroke"
    assert color_enc["legend"]["symbolSize"] > 100


def test_area_emit_encoding_has_x_and_y(area_style: ResolvedAreaStyle) -> None:
    from dbt_charts.core.render.chart.emitters import (
        get_emitter,
    )

    spec = get_emitter(_area(area_style)).emit(
        _area(area_style), _DEFAULT_BOX, regroup((), [])
    )
    assert "x" in spec.encoding
    assert "y" in spec.encoding


def test_scatter_emit_encoding_has_x_and_y(scatter_style: ResolvedScatterStyle) -> None:
    from dbt_charts.core.render.chart.emitters import (
        get_emitter,
    )

    chart = _scatter(scatter_style)
    spec = get_emitter(chart).emit(chart, _DEFAULT_BOX, regroup((), []))
    assert "x" in spec.encoding
    assert "y" in spec.encoding


def test_scatter_categorical_y_sort_stays_out_of_bar_stack_blast_radius(
    scatter_style: ResolvedScatterStyle,
) -> None:
    """A categorical-y scatter (dot plot) is a dimension axis, so its authored
    sort carries the same ``op: min`` every dimension axis pins — the aggregate
    a point mark's own VL inference lands on, stated rather than left implicit.
    What it must never pick up is bar's stacking ``sum``: a fix scoped to bar's
    grouped-column ``y.stack`` suppression cannot reach this axis.
    """
    from dbt_charts.core.compile.models.chart.authored import ChartSort
    from dbt_charts.core.render.chart.emitters import get_emitter

    # A nominal y bake, not the default quantitative one: a real resolve()
    # never bakes a quantitative theme format onto a categorical y (that
    # mismatch can't happen in production — see gate_label_format's
    # docstring), and the quantitative default's baked format now correctly
    # raises rather than silently dropping.
    ax, ay = _make_resolved_axes("scatter", "quantitative", "nominal")
    chart = ResolvedScatterChart(
        id="scatter_dot_sorted",
        chart_type="scatter",
        x="hours",
        y="team",
        sort=ChartSort(by="hours", order="desc"),
        style=scatter_style.model_copy(update={"axis_x": ax, "axis_y": ay}),
        panel_axes=(),
        **_C,
    )
    data = [{"team": "Onboarding", "hours": 1.8}, {"team": "Support", "hours": 2.4}]
    spec = get_emitter(chart).emit(chart, _DEFAULT_BOX, regroup((), data))
    assert spec.encoding["y"]["sort"] == {
        "field": "hours",
        "order": "descending",
        "op": "min",
    }, f"got {spec.encoding['y'].get('sort')!r}"


def test_heatmap_emit_encoding_has_x_and_y(heatmap_style: ResolvedHeatmapStyle) -> None:
    from dbt_charts.core.render.chart.emitters import (
        get_emitter,
    )

    chart = _heatmap(heatmap_style)
    spec = get_emitter(chart).emit(chart, _DEFAULT_BOX, regroup((), []))
    assert "x" in spec.encoding
    assert "y" in spec.encoding


def test_heatmap_unauthored_sort_preserves_query_order_on_both_axes(
    heatmap_style: ResolvedHeatmapStyle,
) -> None:
    """No authored `sort:` must keep both axes in query row order (VL
    ``sort: null``), not VL's default alphabetical domain."""
    from dbt_charts.core.render.chart.emitters import get_emitter

    chart = _heatmap(heatmap_style)
    data = [
        {"col": "Feb", "row": "low", "val": 2},
        {"col": "Jan", "row": "high", "val": 5},
    ]
    spec = get_emitter(chart).emit(chart, _DEFAULT_BOX, regroup((), data))
    assert spec.encoding["x"]["sort"] is None, f"got {spec.encoding['x'].get('sort')!r}"
    assert spec.encoding["y"]["sort"] is None, f"got {spec.encoding['y'].get('sort')!r}"


def test_heatmap_authored_sort_reaches_y_encoding(
    heatmap_style: ResolvedHeatmapStyle,
) -> None:
    """An authored `chart.sort` must reach the y encoding the same way it
    already reaches x."""
    from dbt_charts.core.compile.models.chart.authored import ChartSort
    from dbt_charts.core.render.chart.emitters import get_emitter

    hm_ax, hm_ay = _make_resolved_axes("heatmap", "nominal", "nominal")
    chart = ResolvedHeatmapChart(
        panel_axes=(),
        id="heatmap_sorted",
        chart_type="heatmap",
        x="col",
        y="row",
        sort=ChartSort(by="val", order="desc"),
        style=heatmap_style.model_copy(update={"axis_x": hm_ax, "axis_y": hm_ay}),
        **_C,
    )
    data = [
        {"col": "Feb", "row": "low", "val": 2},
        {"col": "Jan", "row": "high", "val": 5},
    ]
    spec = get_emitter(chart).emit(chart, _DEFAULT_BOX, regroup((), data))
    expected = {"field": "val", "order": "descending", "op": "min"}
    assert spec.encoding["y"]["sort"] == expected, (
        f"got {spec.encoding['y'].get('sort')!r}"
    )
    assert spec.encoding["x"]["sort"] == expected, (
        f"got {spec.encoding['x'].get('sort')!r}"
    )
    # An explicit domain outranks `sort` in Vega-Lite, so the key alone
    # proves nothing about the order VL actually draws -- the pinned domain
    # is what decides it (high=5 outranks low=2, descending).
    assert spec.encoding["y"]["scale"]["domain"] == ["high", "low"], (
        f"got {spec.encoding['y'].get('scale')!r}"
    )


def test_wide_heatmap_unauthored_sort_reaches_every_layer_y(
    heatmap_style: ResolvedHeatmapStyle,
) -> None:
    """The multi-measure (`y: [...]`) path's layers share one y-position
    scale, so an unsorted layer left VL to alphabetize the merged domain —
    disagreeing with the top-level x's own query-order default on the same
    chart. Every layer's y must get the same `sort: None` default."""
    from dbt_charts.core.render.chart.emitters import get_emitter

    hm_ax, hm_ay = _make_resolved_axes("heatmap", "nominal", "nominal")
    chart = ResolvedHeatmapChart(
        panel_axes=(),
        id="heatmap_wide",
        chart_type="heatmap",
        x="col",
        y=["low", "zeta", "high", "alpha"],
        style=heatmap_style.model_copy(update={"axis_x": hm_ax, "axis_y": hm_ay}),
        **_C,
    )
    data = [{"col": "Feb"}, {"col": "Jan"}]
    spec = get_emitter(chart).emit(chart, _DEFAULT_BOX, regroup((), data))
    assert spec.encoding["x"]["sort"] is None
    assert len(spec.layers) == 4
    for layer in spec.layers:
        assert layer.encoding["y"]["sort"] is None, f"got {layer.encoding['y']!r}"


def test_wide_heatmap_authored_sort_reaches_every_layer_y(
    heatmap_style: ResolvedHeatmapStyle,
) -> None:
    """The authored half of the same per-layer y pin: each layer's own
    `scale.domain` — the value that actually decides drawn order, since an
    explicit domain outranks `sort` in Vega-Lite — must reflect `chart.sort`,
    not just carry the `sort` key."""
    from dbt_charts.core.compile.models.chart.authored import ChartSort
    from dbt_charts.core.render.chart.emitters import get_emitter

    hm_ax, hm_ay = _make_resolved_axes("heatmap", "nominal", "nominal")
    chart = ResolvedHeatmapChart(
        panel_axes=(),
        id="heatmap_wide_sorted",
        chart_type="heatmap",
        x="col",
        y=["m1", "m2"],
        sort=ChartSort(by="seq", order="desc"),
        style=heatmap_style.model_copy(update={"axis_x": hm_ax, "axis_y": hm_ay}),
        **_C,
    )
    # Query row order, alphabetical order and sort-desc-by-seq order are all
    # different for these three values -- same discriminating shape as
    # test_authored_sort_x_domain.py's four-orders fixture -- so a pin that
    # silently fell back to either default would fail this, not just happen
    # to pass.
    data = [
        {"col": "A", "m1": "Feb", "m2": "Q2", "seq": 2},
        {"col": "B", "m1": "Mar", "m2": "Q3", "seq": 3},
        {"col": "C", "m1": "Jan", "m2": "Q1", "seq": 1},
    ]
    spec = get_emitter(chart).emit(chart, _DEFAULT_BOX, regroup((), data))
    m1_layer, m2_layer = spec.layers
    assert m1_layer.encoding["y"]["scale"]["domain"] == ["Mar", "Feb", "Jan"], (
        f"got {m1_layer.encoding['y'].get('scale')!r}"
    )
    assert m2_layer.encoding["y"]["scale"]["domain"] == ["Q3", "Q2", "Q1"], (
        f"got {m2_layer.encoding['y'].get('scale')!r}"
    )


def test_pie_emit_encoding_has_theta(pie_style: ResolvedPieStyle) -> None:
    from dbt_charts.core.render.chart.emitters import (
        get_emitter,
    )

    spec = get_emitter(_pie(pie_style)).emit(
        _pie(pie_style), _DEFAULT_BOX, regroup((), [])
    )
    assert "theta" in spec.layers[0].encoding, (
        "theta encoding must be on the arc layer (spec.layers[0]), not the outer spec"
    )


# ---------------------------------------------------------------------------
# Encoding-intent routing: hints must NOT land in spec.config (hint-bag ban)
# ---------------------------------------------------------------------------


def test_bar_stack_in_y_encoding_not_config(bar_style: ResolvedBarStyle) -> None:
    from dbt_charts.core.render.chart.emitters.bar import BarEmitter

    ax, ay = _make_resolved_axes("bar", "ordinal", "quantitative")
    bar = ResolvedBarChart(
        panel_axes=(),
        id="b",
        chart_type="bar",
        x="month",
        y="revenue",
        stack="normalize",
        style=bar_style.model_copy(update={"axis_x": ax, "axis_y": ay}),
        **_C,
    )
    spec = BarEmitter().emit(
        bar, _DEFAULT_BOX, regroup((), [{"month": "Jan", "revenue": 100}])
    )
    assert "stack" not in spec.config
    assert spec.encoding["y"]["stack"] == "normalize"


def test_vertical_bar_suppresses_legend_when_legend_visible_false(
    bar_style: ResolvedBarStyle,
) -> None:
    """Vertical multi-series bar honors legend_visible=False (matches horizontal)."""
    from dbt_charts.core.compile.models.chart.resolved import ResolvedStyleChannel
    from dbt_charts.core.render.chart.emitters.bar import BarEmitter

    ax, ay = _make_resolved_axes("bar", "ordinal", "quantitative")
    ch = ResolvedStyleChannel(channel="color", mode="series", data_field="cat")
    bar = ResolvedBarChart(
        panel_axes=(),
        id="vb",
        chart_type="bar",
        x="month",
        y="revenue",
        stack="zero",
        style=bar_style.model_copy(update={"axis_x": ax, "axis_y": ay}),
        **{
            **_C,
            "resolved_channels": {"color": ch},
            "legend": _default_legend().model_copy(update={"visible": False}),
        },
    )
    spec = BarEmitter().emit(
        bar, _DEFAULT_BOX, regroup((), [{"month": "Jan", "cat": "a", "revenue": 5}])
    )
    assert spec.encoding["color"]["legend"] is None


def test_horizontal_stacked_bar_emits_stack_order(bar_style: ResolvedBarStyle) -> None:
    """Stacked horizontal bar emits the global-sum z-order (calculate + order).

    Without it VL stacks segments in data-insertion order; the emitter stacks by
    global sum per series (largest at baseline), baked as a precomputed index via
    __df_series_order — the SAME order authority the legend/labels use.
    Mirrors the vertical path.
    """
    from dbt_charts.core.compile.models.chart.resolved import ResolvedStyleChannel
    from dbt_charts.core.render.chart.emitters.bar import (
        _DF_SERIES_ORDER_KEY,
        BarEmitter,
    )

    ax, ay = _make_resolved_axes("bar", "nominal", "quantitative")
    ch = ResolvedStyleChannel(channel="color", mode="series", data_field="priority")
    bar = ResolvedBarChart(
        panel_axes=(),
        id="hsb",
        chart_type="bar",
        x="team",
        y="tickets",
        stack="zero",
        orientation="horizontal",
        style=bar_style.model_copy(update={"axis_x": ax, "axis_y": ay}),
        **{**_C, "resolved_channels": {"color": ch}},
    )
    data = [
        {"team": "A", "priority": "low", "tickets": 5},
        {"team": "A", "priority": "high", "tickets": 9},
    ]
    spec = BarEmitter().emit(bar, _DEFAULT_BOX, regroup((), data))
    assert spec.encoding["order"] == {
        "field": _DF_SERIES_ORDER_KEY,
        "sort": "ascending",
    }
    # "high" (larger sum) ranked ahead of "low" -> index 0 (baseline).
    assert any(
        t.get("calculate") and t.get("as") == _DF_SERIES_ORDER_KEY
        for t in spec.transforms
    ), f"missing stack-order calculate transform; transforms={spec.transforms!r}"


def test_horizontal_grouped_bar_no_stack_order(bar_style: ResolvedBarStyle) -> None:
    """Grouped (stack='none') horizontal bars carry no stack-order transform/order."""
    from dbt_charts.core.compile.models.chart.resolved import ResolvedStyleChannel
    from dbt_charts.core.render.chart.emitters.bar import BarEmitter

    ax, ay = _make_resolved_axes("bar", "nominal", "quantitative")
    ch = ResolvedStyleChannel(channel="color", mode="series", data_field="priority")
    bar = ResolvedBarChart(
        panel_axes=(),
        id="hgb",
        chart_type="bar",
        x="team",
        y="tickets",
        stack="none",
        orientation="horizontal",
        style=bar_style.model_copy(update={"axis_x": ax, "axis_y": ay}),
        **{**_C, "resolved_channels": {"color": ch}},
    )
    spec = BarEmitter().emit(
        bar, _DEFAULT_BOX, regroup((), [{"team": "A", "priority": "low", "tickets": 5}])
    )
    assert "order" not in spec.encoding
    assert spec.transforms == []


def test_vertical_stacked_bar_degenerate_stack_follows_query_order(
    bar_style: ResolvedBarStyle,
) -> None:
    """When color maps 1:1 with x (each bar has exactly one segment), the
    legend/z-order follows the query's own row order instead of ranking
    series by their (here meaningless, since each series appears in only
    one category) stacked total.

    Regression for https://github.com/dbt-labs/dbt-charts/issues/28: sorting
    by global sum turned query order "mango, apple, cherry, banana" into
    "apple, mango, banana, cherry".
    """
    from dbt_charts.core.compile.models.chart.resolved import ResolvedStyleChannel
    from dbt_charts.core.render.chart.emitters.bar import (
        _DF_SERIES_ORDER_KEY,
        BarEmitter,
    )

    ax, ay = _make_resolved_axes("bar", "nominal", "quantitative")
    ch = ResolvedStyleChannel(channel="color", mode="series", data_field="fruit")
    bar = ResolvedBarChart(
        panel_axes=(),
        id="degenerate",
        chart_type="bar",
        x="quarter",
        y="revenue",
        stack="zero",
        style=bar_style.model_copy(update={"axis_x": ax, "axis_y": ay}),
        **{
            **_C,
            "resolved_channels": {"color": ch},
            "legend": _default_legend().model_copy(update={"visible": True}),
        },
    )
    data = [
        {"quarter": "Q1", "fruit": "mango", "revenue": 30},
        {"quarter": "Q2", "fruit": "apple", "revenue": 50},
        {"quarter": "Q3", "fruit": "cherry", "revenue": 10},
        {"quarter": "Q4", "fruit": "banana", "revenue": 20},
    ]
    spec = BarEmitter().emit(bar, _DEFAULT_BOX, regroup((), data))

    # Global-sum order would be apple, mango, banana, cherry. A degenerate
    # (one-segment-per-bar) stack has no visual top/bottom to reverse against,
    # so -- unlike a real stack -- the legend reads query order directly,
    # matching the bars left to right (mango, apple, cherry, banana).
    assert spec.encoding["color"]["legend"]["values"] == [
        "mango",
        "apple",
        "cherry",
        "banana",
    ]
    order_expr = next(
        t["calculate"] for t in spec.transforms if t.get("as") == _DF_SERIES_ORDER_KEY
    )
    # Baseline (stack bottom, index 0) is the first query row's series.
    assert order_expr.index('"mango"') < order_expr.index('"apple"')
    assert order_expr.index('"apple"') < order_expr.index('"cherry"')
    assert order_expr.index('"cherry"') < order_expr.index('"banana"')


def test_horizontal_stacked_bar_degenerate_stack_follows_query_order(
    bar_style: ResolvedBarStyle,
) -> None:
    """Mirrors the vertical case: a horizontal degenerate stack (one segment
    per category) also preserves query order instead of ranking by total."""
    from dbt_charts.core.compile.models.chart.resolved import ResolvedStyleChannel
    from dbt_charts.core.render.chart.emitters.bar import BarEmitter

    ax, ay = _make_resolved_axes("bar", "nominal", "quantitative")
    ch = ResolvedStyleChannel(channel="color", mode="series", data_field="fruit")
    bar = ResolvedBarChart(
        panel_axes=(),
        id="degenerate-h",
        chart_type="bar",
        x="quarter",
        y="revenue",
        stack="zero",
        orientation="horizontal",
        style=bar_style.model_copy(update={"axis_x": ax, "axis_y": ay}),
        **{
            **_C,
            "resolved_channels": {"color": ch},
            "legend": _default_legend().model_copy(update={"visible": True}),
        },
    )
    data = [
        {"quarter": "Q1", "fruit": "mango", "revenue": 30},
        {"quarter": "Q2", "fruit": "apple", "revenue": 50},
        {"quarter": "Q3", "fruit": "cherry", "revenue": 10},
        {"quarter": "Q4", "fruit": "banana", "revenue": 20},
    ]
    spec = BarEmitter().emit(bar, _DEFAULT_BOX, regroup((), data))

    # Horizontal's "left-first" legend convention is baseline-first, unreversed.
    assert spec.encoding["color"]["legend"]["values"] == [
        "mango",
        "apple",
        "cherry",
        "banana",
    ]


def test_support_table_series_order_strip_returns_stacked_order_verbatim(
    bar_style: ResolvedBarStyle,
) -> None:
    """A stacked bar's per-series support-table strip must return the
    emitter's OWN already-computed order verbatim -- never re-derive it from
    raw rows, which risks disagreeing with what the chart actually renders
    (see ``degenerate_or_stacked_series_order`` and the pipeline-level
    chart<->strip agreement test in ``test_chart_support_table_pipeline.py``,
    which exercises the real ``ChartSpec.stacked_series_order`` threading
    this unit test stands in for)."""
    from dbt_charts.core.compile.models.chart.resolved import ResolvedStyleChannel
    from dbt_charts.core.render.chart.support_table_attachment import (
        _series_order_strip,
    )

    ax, ay = _make_resolved_axes("bar", "nominal", "quantitative")
    ch = ResolvedStyleChannel(channel="color", mode="series", data_field="fruit")
    bar = ResolvedBarChart(
        panel_axes=(),
        id="strip-degenerate",
        chart_type="bar",
        x="quarter",
        y="revenue",
        stack="zero",
        style=bar_style.model_copy(update={"axis_x": ax, "axis_y": ay}),
        **{**_C, "resolved_channels": {"color": ch}},
    )
    data = [{"quarter": "Q1", "fruit": "mango", "revenue": 30}]
    order = _series_order_strip(
        "bar", bar, data, "fruit", {"mango"}, ["mango", "apple", "cherry"]
    )
    assert order == ["mango", "apple", "cherry"]


def test_support_table_series_order_strip_falls_back_when_chart_pins_none(
    bar_style: ResolvedBarStyle,
) -> None:
    """When the emitter pins no definite order for a stacked-bar shape (an
    ordinal color column, a wide-measure bar, ...), the strip reproduces
    the same total-ranked (sum-descending) order VL itself uses with no
    order channel present -- matching this function's own pre-existing
    behavior for those shapes -- rather than falling back to an unrelated
    alphabetical constant."""
    from dbt_charts.core.compile.models.chart.resolved import ResolvedStyleChannel
    from dbt_charts.core.render.chart.support_table_attachment import (
        _series_order_strip,
    )

    ax, ay = _make_resolved_axes("bar", "nominal", "quantitative")
    ch = ResolvedStyleChannel(channel="color", mode="series", data_field="fruit")
    bar = ResolvedBarChart(
        panel_axes=(),
        id="strip-no-pinned-order",
        chart_type="bar",
        x="quarter",
        y="revenue",
        stack="zero",
        style=bar_style.model_copy(update={"axis_x": ax, "axis_y": ay}),
        **{**_C, "resolved_channels": {"color": ch}},
    )
    # Deliberately NOT alphabetical-order-equivalent: sum-ranked descending
    # (apple=90, mango=30, cherry=10) must read differently from
    # alphabetical (apple, cherry, mango) to actually pin which one this is.
    data = [
        {"quarter": "Q1", "fruit": "mango", "revenue": 30},
        {"quarter": "Q2", "fruit": "apple", "revenue": 90},
        {"quarter": "Q3", "fruit": "cherry", "revenue": 10},
    ]
    order = _series_order_strip(
        "bar", bar, data, "fruit", {"cherry", "apple", "mango"}, None
    )
    assert order == ["apple", "mango", "cherry"]


def test_vertical_stacked_bar_degenerate_stack_honors_authored_stack_order(
    bar_style: ResolvedBarStyle,
) -> None:
    """An explicit ``style.bar.stack_order`` is never overridden by the
    degenerate-stack query-order default -- the author asked for a specific
    order and gets it."""
    from dbt_charts.core.compile.models.chart.resolved import ResolvedStyleChannel
    from dbt_charts.core.render.chart.emitters.bar import BarEmitter

    ax, ay = _make_resolved_axes("bar", "nominal", "quantitative")
    ch = ResolvedStyleChannel(channel="color", mode="series", data_field="fruit")
    bar = ResolvedBarChart(
        panel_axes=(),
        id="degenerate-explicit",
        chart_type="bar",
        x="quarter",
        y="revenue",
        stack="zero",
        style=bar_style.model_copy(
            update={"axis_x": ax, "axis_y": ay, "stack_order": "alphabetical"}
        ),
        **{
            **_C,
            "resolved_channels": {"color": ch},
            "legend": _default_legend().model_copy(update={"visible": True}),
        },
    )
    data = [
        {"quarter": "Q1", "fruit": "mango", "revenue": 30},
        {"quarter": "Q2", "fruit": "apple", "revenue": 50},
        {"quarter": "Q3", "fruit": "cherry", "revenue": 10},
    ]
    spec = BarEmitter().emit(bar, _DEFAULT_BOX, regroup((), data))

    # Alphabetical baseline-first: apple, cherry, mango -- reversed for the
    # legend. Query order (what dropping this guard would produce instead,
    # unreversed since the degenerate branch never reverses) is mango,
    # apple, cherry -- a different list, so this actually pins the guard.
    assert spec.encoding["color"]["legend"]["values"] == ["mango", "cherry", "apple"]


def test_vertical_stacked_bar_degenerate_stack_skipped_on_quantitative_x(
    bar_style: ResolvedBarStyle,
) -> None:
    """The degenerate-stack override never fires for a continuous
    (quantitative) x, even when color is 1:1 with it -- a continuous scale
    renders bars ordered by VALUE, not row order, so the x-domain
    first-occurrence fallback would draw a legend the axis doesn't match."""
    from dbt_charts.core.compile.models.chart.resolved import ResolvedStyleChannel
    from dbt_charts.core.render.chart.emitters.bar import BarEmitter

    ax, ay = _make_resolved_axes("bar", "quantitative", "quantitative")
    ch = ResolvedStyleChannel(channel="color", mode="series", data_field="fruit")
    bar = ResolvedBarChart(
        panel_axes=(),
        id="degenerate-quantitative-x",
        chart_type="bar",
        x="code",
        y="revenue",
        stack="zero",
        style=bar_style.model_copy(update={"axis_x": ax, "axis_y": ay}),
        **{
            **_C,
            "resolved_channels": {"color": ch},
            "legend": _default_legend().model_copy(update={"visible": True}),
        },
    )
    data = [
        {"code": 1, "fruit": "mango", "revenue": 30},
        {"code": 2, "fruit": "apple", "revenue": 50},
        {"code": 3, "fruit": "cherry", "revenue": 10},
    ]
    spec = BarEmitter().emit(bar, _DEFAULT_BOX, regroup((), data))

    # Sum-ranked descending (apple=50, mango=30, cherry=10), reversed for the
    # legend: cherry, mango, apple. The degenerate (buggy) order would
    # instead read mango, apple, cherry -- query order, unreversed.
    assert spec.encoding["color"]["legend"]["values"] == ["cherry", "mango", "apple"]


def test_horizontal_stacked_bar_degenerate_stack_honors_authored_sort(
    bar_style: ResolvedBarStyle,
) -> None:
    """The degenerate-stack override must reproduce an authored ``chart.sort``
    by a non-numeric column, not just the unsorted (first-occurrence) case --
    that only ranks correctly under the same ``min``-aggregate op the
    chart's own category encoding uses (``bar_sort_to_vl``/``bar_sort_op``).
    The wrong op (``sum``) drops every row under numeric coercion and
    silently falls back to raw query order instead -- see
    _stacked_series_order's ``bar_sort_to_vl`` call."""
    from dbt_charts.core.compile.models.chart.authored import ChartSort
    from dbt_charts.core.compile.models.chart.resolved import ResolvedStyleChannel
    from dbt_charts.core.render.chart.emitters.bar import BarEmitter

    ax, ay = _make_resolved_axes("bar", "nominal", "quantitative")
    ch = ResolvedStyleChannel(channel="color", mode="series", data_field="fruit")
    bar = ResolvedBarChart(
        panel_axes=(),
        id="degenerate-sorted",
        chart_type="bar",
        x="quarter",
        y="revenue",
        stack="zero",
        orientation="horizontal",
        sort=ChartSort(by="tier", order="asc"),
        style=bar_style.model_copy(update={"axis_x": ax, "axis_y": ay}),
        **{
            **_C,
            "resolved_channels": {"color": ch},
            "legend": _default_legend().model_copy(update={"visible": True}),
        },
    )
    data = [
        {"quarter": "Q1", "fruit": "mango", "revenue": 30, "tier": "C"},
        {"quarter": "Q2", "fruit": "apple", "revenue": 50, "tier": "A"},
        {"quarter": "Q3", "fruit": "cherry", "revenue": 10, "tier": "D"},
        {"quarter": "Q4", "fruit": "banana", "revenue": 20, "tier": "B"},
    ]
    spec = BarEmitter().emit(bar, _DEFAULT_BOX, regroup((), data))

    # Tier-ascending x order is Q2(A), Q4(B), Q1(C), Q3(D) -> apple, banana,
    # mango, cherry. Falling back to first-occurrence order (the "sum" op
    # bug) would instead give mango, apple, cherry, banana.
    assert spec.encoding["color"]["legend"]["values"] == [
        "apple",
        "banana",
        "mango",
        "cherry",
    ]


def test_vertical_stacked_bar_degenerate_stack_with_repeated_color(
    bar_style: ResolvedBarStyle,
) -> None:
    """A degenerate stack (color 1:1 with x) can still repeat a color across
    several x categories -- e.g. the same status recurring on different days.
    Each x still carries exactly one color, so ``_is_color_1to1_with_x``
    still holds; the fix must not silently fall back to the stacked-total
    default just because a color value repeats."""
    from dbt_charts.core.compile.models.chart.resolved import ResolvedStyleChannel
    from dbt_charts.core.render.chart.emitters.bar import BarEmitter

    ax, ay = _make_resolved_axes("bar", "nominal", "quantitative")
    ch = ResolvedStyleChannel(channel="color", mode="series", data_field="status")
    bar = ResolvedBarChart(
        panel_axes=(),
        id="degenerate-repeat",
        chart_type="bar",
        x="day",
        y="count",
        stack="zero",
        style=bar_style.model_copy(update={"axis_x": ax, "axis_y": ay}),
        **{
            **_C,
            "resolved_channels": {"color": ch},
            "legend": _default_legend().model_copy(update={"visible": True}),
        },
    )
    data = [
        {"day": "Mon", "status": "C", "count": 5},
        {"day": "Tue", "status": "A", "count": 50},
        {"day": "Wed", "status": "B", "count": 1},
        {"day": "Thu", "status": "C", "count": 5},
    ]
    spec = BarEmitter().emit(bar, _DEFAULT_BOX, regroup((), data))

    # Global-sum order (A=50, C=10, B=1) would give a legend of B, C, A.
    # Query first-occurrence order (C, A, B) is what the fix must produce.
    assert spec.encoding["color"]["legend"]["values"] == ["C", "A", "B"]


def test_vertical_stacked_bar_with_gradient_color_emits_no_order(
    bar_style: ResolvedBarStyle,
) -> None:
    """A stacked bar with a gradient (continuous) color channel gets no
    series-order transform — the spatial-order authority only applies to
    discrete series-mode color; a continuous scale has no meaningful
    discrete stack order to bake into a calculate index lookup."""
    from dbt_charts.core.compile.models.chart.resolved import ResolvedStyleChannel
    from dbt_charts.core.compile.models.primitives import ScaleTargetConfig
    from dbt_charts.core.render.chart.emitters.bar import BarEmitter

    ax, ay = _make_resolved_axes("bar", "nominal", "quantitative")
    scale = ScaleTargetConfig(palette=["#fff", "#000"])
    ch = ResolvedStyleChannel(
        channel="color", mode="gradient", data_field="score", scale=scale
    )
    bar = ResolvedBarChart(
        panel_axes=(),
        id="gb",
        chart_type="bar",
        x="team",
        y="tickets",
        stack="zero",
        orientation="vertical",
        style=bar_style.model_copy(update={"axis_x": ax, "axis_y": ay}),
        **{**_C, "resolved_channels": {"color": ch}},
    )
    spec = BarEmitter().emit(
        bar, _DEFAULT_BOX, regroup((), [{"team": "A", "score": 5, "tickets": 5}])
    )
    assert "order" not in spec.encoding
    # The gradient channel's own scale (range only, no domain) is untouched —
    # never overwritten by the discrete spatial-order domain/range pair.
    assert spec.encoding["color"]["scale"] == {"range": ["#fff", "#000"]}
    assert spec.transforms == []


def test_bar_vertical_honors_chart_sort_field(bar_style: ResolvedBarStyle) -> None:
    """A vertical bar with chart.sort emits a field-based VL sort on the x axis.

    Mirrors V1 _apply_chart_sort: chart.sort {by, order} → x_enc["sort"]
    {"field": by, "order": "ascending"|"descending"}. Without it the categorical
    axis falls back to alphabetical order (the reported queue-by-age bug).
    """
    from dbt_charts.core.compile.models.chart.authored import ChartSort
    from dbt_charts.core.render.chart.emitters.bar import BarEmitter

    ax, ay = _make_resolved_axes("bar", "ordinal", "quantitative")
    bar = ResolvedBarChart(
        panel_axes=(),
        id="b",
        chart_type="bar",
        x="age_band",
        y="tickets",
        sort=ChartSort(by="age_order", order="asc"),
        style=bar_style.model_copy(update={"axis_x": ax, "axis_y": ay}),
        **_C,
    )
    spec = BarEmitter().emit(
        bar, _DEFAULT_BOX, regroup((), [{"age_band": "1 · <= 7", "tickets": 5}])
    )
    assert spec.encoding["x"]["sort"] == {
        "field": "age_order",
        "order": "ascending",
        # The category's own key value — bar pins the aggregate rather than
        # leaving it to VL's spec-dependent inference (``bar_sort_to_vl``).
        "op": "min",
    }


def test_area_emit_honors_baked_zero_scale(area_style: ResolvedAreaStyle) -> None:
    """Area emitter zero-anchors when resolve baked scale.zero=True (domainMin pin).

    The data-driven zero decision lives in resolve (_bake_y_zero); the emitter just
    translates a baked scale.zero=True into a domainMin-anchored VL y-scale.
    """
    import dataclasses

    from dbt_charts.core.compile.models.style.resolved import (
        ResolvedScaleContinuousStyle,
        ResolvedScaleStyle,
    )
    from dbt_charts.core.render.chart.emitters.area import AreaEmitter

    ax, ay = _make_resolved_axes("area", "ordinal", "quantitative")
    ay = dataclasses.replace(
        ay, scale=ResolvedScaleStyle(continuous=ResolvedScaleContinuousStyle(zero=True))
    )
    area = ResolvedAreaChart(
        panel_axes=(),
        id="a",
        chart_type="area",
        x="month",
        y="revenue",
        style=area_style.model_copy(update={"axis_x": ax, "axis_y": ay}),
        **_C,
    )
    spec = AreaEmitter().emit(
        area,
        _DEFAULT_BOX,
        regroup(
            (), [{"month": "Jan", "revenue": 100}, {"month": "Feb", "revenue": 200}]
        ),
    )
    y_scale = spec.encoding["y"].get("scale", {})
    assert y_scale.get("zero") is True, (
        f"area y-scale must zero-anchor, got {y_scale!r}"
    )
    assert y_scale.get("domainMin", 0.0) <= 0.0


def test_pie_emitter_donut_uses_vl_expr(pie_style: ResolvedPieStyle) -> None:
    """inner_radius emitted as a VL expr sourced from the shared hole geometry.

    innerRadius must be exactly pie_hole_radius_expr(outer_fraction, inner_ratio)
    so the emitted hole and the overflow detector's measured hole cannot drift.
    """
    from dbt_charts.core.render.chart.emitters.pie import (
        PieEmitter,
        pie_hole_radius_expr,
    )

    pie = ResolvedPieChart(
        id="p",
        chart_type="pie",
        resolution_width=600.0,
        outer_fraction=0.9,
        attached_table_gap=12.0,
        hybrid_heading_gap=6.0,
        presentation_fingerprint=pie_presentation_fingerprint([{"sales": 100}]),
        slice_label_indices=(),
        theta="sales",
        style=pie_style.model_copy(update={"inner_radius": 0.6}),
        dark_companion_stops=("#0e4786",),
        **_PB,
    )
    spec = PieEmitter().emit(pie, _DEFAULT_BOX, regroup((), [{"sales": 100}]))
    assert spec.mark == "layered"
    arc_layer = spec.layers[0]
    assert arc_layer.mark == "arc"
    outer = arc_layer.mark_props.get("outerRadius")
    inner = arc_layer.mark_props.get("innerRadius")
    assert isinstance(outer, dict) and "expr" in outer, (
        f"outerRadius must be a VL expr, got {outer!r}"
    )
    assert isinstance(inner, dict) and "expr" in inner, (
        f"innerRadius must be a VL expr, got {inner!r}"
    )
    assert inner["expr"] == pie_hole_radius_expr(0.9, 0.6), (
        f"innerRadius must come from the shared hole geometry: {inner['expr']!r}"
    )


def test_pie_emitter_solid_pie_sets_inner_radius_zero(
    pie_style: ResolvedPieStyle,
) -> None:
    """Solid pie (no inner_radius) sets innerRadius=0 — matching oracle explicit 0."""
    from dbt_charts.core.render.chart.emitters.pie import PieEmitter

    pie = ResolvedPieChart(
        id="p2",
        chart_type="pie",
        resolution_width=600.0,
        outer_fraction=0.9,
        attached_table_gap=12.0,
        hybrid_heading_gap=6.0,
        presentation_fingerprint=pie_presentation_fingerprint([{"sales": 100}]),
        slice_label_indices=(),
        theta="sales",
        style=pie_style,
        dark_companion_stops=("#0e4786",),
        **_PB,
    )
    spec = PieEmitter().emit(pie, _DEFAULT_BOX, regroup((), [{"sales": 100}]))
    assert spec.mark == "layered"
    arc_layer = spec.layers[0]
    assert arc_layer.mark == "arc"
    assert arc_layer.mark_props.get("innerRadius") == 0, (
        f"Solid pie must set innerRadius=0, got {arc_layer.mark_props.get('innerRadius')!r}"
    )


def test_bar_emit_multi_measure_folds_to_one_unit_spec(
    bar_style: ResolvedBarStyle,
) -> None:
    """wide_measures on bar folds measures so VL can stack or group them."""
    from dbt_charts.core.compile.resolve.chart._wide_fields import (
        WIDE_LABEL_FIELD,
        WIDE_VALUE_FIELD,
    )
    from dbt_charts.core.render.chart.emitters.bar import BarEmitter

    bar = ResolvedBarChart(
        panel_axes=(),
        id="b2",
        chart_type="bar",
        x="month",
        y=WIDE_VALUE_FIELD,
        wide_measures=("revenue", "cost"),
        style=bar_style,
        **_C,
    )
    spec = BarEmitter().emit(
        bar, _DEFAULT_BOX, regroup((), [{"month": "Jan", "revenue": 100, "cost": 80}])
    )
    assert spec.mark == "bar"
    # Grouped (no stack): fold order follows the same alphabetical display
    # order the color domain/legend pin, not authored y: list order -- see
    # fold_wide_measures's docstring.
    assert spec.transforms[0]["fold"] == ["cost", "revenue"]
    assert spec.encoding["y"]["field"] == WIDE_VALUE_FIELD
    assert spec.encoding["color"]["field"] == WIDE_LABEL_FIELD


def test_bar_emit_multi_measure_horizontal_categorical_on_y(
    bar_style: ResolvedBarStyle,
) -> None:
    """Horizontal multi-measure bars place categories on y and folded values on x."""
    from dbt_charts.core.compile.resolve.chart._wide_fields import (
        WIDE_VALUE_FIELD,
    )
    from dbt_charts.core.render.chart.emitters.bar import BarEmitter

    bar = ResolvedBarChart(
        panel_axes=(),
        id="b_horiz",
        chart_type="bar",
        x="category",
        y=WIDE_VALUE_FIELD,
        wide_measures=("revenue", "cost"),
        orientation="horizontal",
        style=bar_style,
        **_C,
    )
    data = [{"category": "A", "revenue": 100, "cost": 80}]
    spec = BarEmitter().emit(bar, _DEFAULT_BOX, regroup((), data))
    assert spec.mark == "bar"
    # Shared categorical field must be on y (the categorical axis for horizontal bars)
    assert "y" in spec.encoding, (
        "horizontal multi-measure: chart.x must go on shared y encoding"
    )
    assert spec.encoding["y"]["field"] == "category"
    assert spec.encoding["x"]["field"] == WIDE_VALUE_FIELD


def test_bar_emit_mixed_sign_split_keeps_y_on_outer_encoding_and_per_layer_radius(
    bar_style: ResolvedBarStyle,
) -> None:
    """Mixed-sign data with a corner radius splits into pos/neg sub-layers
    (_layers.py:317-365). The outer layered ChartSpec must still carry its own
    y encoding — features that read spec.encoding at the top level (e.g.
    MirrorAxisFeature) depend on it — and each sub-layer must keep its own
    distinct per-sign corner radius, not merge or lose it."""
    from dbt_charts.core.compile.models.primitives import BorderStyle
    from dbt_charts.core.compile.models.style.theme import BarMarkStyle
    from dbt_charts.core.render.chart.emitters.bar import BarEmitter

    radius_style = bar_style.model_copy(
        update={
            "mark": BarMarkStyle(
                border=BorderStyle(width=1.0, color="#000", radius=6.0)
            )
        }
    )
    bar = ResolvedBarChart(
        panel_axes=(),
        id="b_mixed",
        chart_type="bar",
        x="month",
        y="value",
        style=radius_style,
        **_C,
    )
    data = [
        {"month": "Jan", "value": 100},
        {"month": "Feb", "value": -40},
        {"month": "Mar", "value": 260},
    ]
    spec = BarEmitter().emit(bar, _DEFAULT_BOX, regroup((), data))
    assert spec.mark == "layered"
    assert isinstance(spec.encoding.get("y"), dict), (
        "outer layered ChartSpec must retain its own y encoding"
    )
    # The hover band (opacity=0) is appended as an additional sub-layer; filter
    # to the visible bar layers that carry corner-radius props.
    visible_bar_layers = [
        la
        for la in spec.layers
        if la.mark == "bar" and la.mark_props.get("opacity") != 0
    ]
    assert len(visible_bar_layers) == 2, (
        f"expected 2 visible bar layers (pos+neg); got {len(visible_bar_layers)}"
    )
    pos_layer, neg_layer = visible_bar_layers
    assert pos_layer.mark_props.get("cornerRadiusEnd") == 6.0
    assert "cornerRadiusEnd" not in neg_layer.mark_props
    assert neg_layer.mark_props.get("cornerRadiusBottomLeft") == 6.0
    assert neg_layer.mark_props.get("cornerRadiusBottomRight") == 6.0


def test_bar_emit_mixed_sign_split_declares_axis_only_on_outer_encoding(
    bar_style: ResolvedBarStyle,
) -> None:
    """Regression: the pos/neg corner-radius split must not copy the outer
    encoding's y ``axis`` def onto each sub-layer — that draws the same axis
    once per sub-layer PLUS once on the shared outer encoding (triple-drawn
    for a 2-layer split). The sub-layers still need the measure channel's
    field/type/scale for their own marks; only ``axis`` is exclusive to the
    outer (shared-scale) encoding."""
    from dbt_charts.core.compile.models.primitives import BorderStyle
    from dbt_charts.core.compile.models.style.theme import BarMarkStyle
    from dbt_charts.core.render.chart.emitters.bar import BarEmitter

    radius_style = bar_style.model_copy(
        update={
            "mark": BarMarkStyle(
                border=BorderStyle(width=1.0, color="#000", radius=6.0)
            )
        }
    )
    bar = ResolvedBarChart(
        panel_axes=(),
        id="b_mixed",
        chart_type="bar",
        x="month",
        y="value",
        style=radius_style,
        **_C,
    )
    data = [
        {"month": "Jan", "value": 100},
        {"month": "Feb", "value": -40},
        {"month": "Mar", "value": 260},
    ]
    spec = BarEmitter().emit(bar, _DEFAULT_BOX, regroup((), data))
    outer_y = spec.encoding.get("y")
    assert isinstance(outer_y, dict) and outer_y.get("axis"), (
        "outer layered ChartSpec must keep the axis def for MirrorAxisFeature"
    )
    # The hover band layer uses a fixed-value y expression (no field); check only
    # the visible bar sub-layers (pos + neg) for the axis-isolation invariant.
    visible_bar_layers = [
        la
        for la in spec.layers
        if la.mark == "bar" and la.mark_props.get("opacity") != 0
    ]
    for layer in visible_bar_layers:
        layer_y = layer.encoding.get("y")
        assert isinstance(layer_y, dict)
        assert "axis" not in layer_y, (
            "sub-layer y encoding must not carry its own axis def — that "
            "duplicates the outer axis once per sub-layer"
        )
        assert layer_y.get("field") == outer_y.get("field")


def test_emitter_sets_palette_in_config(bar_style: ResolvedBarStyle) -> None:
    """Emitter with non-empty chart.palette must set config.range.category — matching oracle."""
    from dbt_charts.core.render.chart.emitters.bar import BarEmitter

    ax, ay = _make_resolved_axes("bar", "ordinal", "quantitative")
    bar = ResolvedBarChart(
        panel_axes=(),
        id="b3",
        chart_type="bar",
        x="month",
        y="revenue",
        palette=("#ff0000", "#00ff00"),
        variable_dependencies=frozenset(),
        resolved_channels={},
        style=bar_style.model_copy(update={"axis_x": ax, "axis_y": ay}),
        legend=_default_legend(),
        background=_DEFAULT_CHARTS.background,
        title_style=_DEFAULT_CHARTS.title,
        layout_padding=_ZERO_PADDING,
    )
    spec = BarEmitter().emit(
        bar, _DEFAULT_BOX, regroup((), [{"month": "Jan", "revenue": 100}])
    )
    assert spec.config.get("range", {}).get("category") == [
        "#ff0000",
        "#00ff00",
    ], f"palette must land in config.range.category, got config={spec.config!r}"


def test_heatmap_color_scheme_in_color_encoding_not_config(
    heatmap_style: ResolvedHeatmapStyle,
) -> None:
    """color_gradient goes on encoding.color.scale when a resolved color channel is present.
    When there is no resolved color channel, scheme is not emitted (avoids malformed VL).
    """
    from dbt_charts.core.compile.models.chart.resolved import ResolvedStyleChannel
    from dbt_charts.core.compile.models.primitives import ScaleTargetConfig
    from dbt_charts.core.render.chart.emitters.heatmap import HeatmapEmitter

    # With series color channel: scheme lands on encoding.color.scale.
    ch = ResolvedStyleChannel(channel="color", mode="series", data_field="value")
    blues_style = heatmap_style.model_copy(
        update={"color_gradient": ScaleTargetConfig(palette="blues")}
    )
    _hm_ax, _hm_ay = _make_resolved_axes("heatmap", "nominal", "nominal")
    hm_with_color = ResolvedHeatmapChart(
        panel_axes=(),
        id="h",
        chart_type="heatmap",
        x="col",
        y="row",
        resolved_channels={"color": ch},
        style=blues_style.model_copy(update={"axis_x": _hm_ax, "axis_y": _hm_ay}),
        variable_dependencies=frozenset(),
        palette=(),
        legend=_default_legend(),
        background=_DEFAULT_CHARTS.background,
        title_style=_DEFAULT_CHARTS.title,
        layout_padding=_ZERO_PADDING,
    )
    spec = HeatmapEmitter().emit(
        hm_with_color, _DEFAULT_BOX, regroup((), [{"col": "A", "row": "1", "value": 1}])
    )
    assert "color_gradient" not in spec.config
    assert spec.encoding.get("color", {}).get("scale", {}).get("scheme") == "blues"

    # Without resolved color channel: no malformed encoding{color:{scale:{scheme}}} with no field.
    hm_no_color = ResolvedHeatmapChart(
        panel_axes=(),
        id="h2",
        chart_type="heatmap",
        x="col",
        y="row",
        style=blues_style.model_copy(update={"axis_x": _hm_ax, "axis_y": _hm_ay}),
        **_C,
    )
    spec2 = HeatmapEmitter().emit(
        hm_no_color, _DEFAULT_BOX, regroup((), [{"col": "A", "row": "1"}])
    )
    assert "color" not in spec2.encoding, (
        "No color encoding must be emitted when resolved_channels has no color — "
        "scheme without field is malformed VL"
    )


def test_heatmap_color_gradient_domain_min_max_reaches_scale(
    heatmap_style: ResolvedHeatmapStyle,
) -> None:
    """min/max authored on the heatmap gradient must reach the VL scale, not be silently dropped."""
    from dbt_charts.core.compile.models.chart.resolved import ResolvedStyleChannel
    from dbt_charts.core.compile.models.primitives import ScaleTargetConfig
    from dbt_charts.core.render.chart.emitters.heatmap import HeatmapEmitter

    ch = ResolvedStyleChannel(channel="color", mode="series", data_field="value")
    bounded_style = heatmap_style.model_copy(
        update={"color_gradient": ScaleTargetConfig(palette="blues", min=0, max=100)}
    )
    _hm_ax, _hm_ay = _make_resolved_axes("heatmap", "nominal", "nominal")
    hm_with_color = ResolvedHeatmapChart(
        panel_axes=(),
        id="h",
        chart_type="heatmap",
        x="col",
        y="row",
        resolved_channels={"color": ch},
        style=bounded_style.model_copy(update={"axis_x": _hm_ax, "axis_y": _hm_ay}),
        variable_dependencies=frozenset(),
        palette=(),
        legend=_default_legend(),
        background=_DEFAULT_CHARTS.background,
        title_style=_DEFAULT_CHARTS.title,
        layout_padding=_ZERO_PADDING,
    )
    spec = HeatmapEmitter().emit(
        hm_with_color, _DEFAULT_BOX, regroup((), [{"col": "A", "row": "1", "value": 1}])
    )
    assert spec.encoding["color"]["scale"]["domain"] == [0, 100]


def test_heatmap_emits_visible_color_legend_by_default(
    heatmap_style: ResolvedHeatmapStyle,
) -> None:
    """A heatmap's color channel encodes the whole measure — it must draw a key.

    Regression pin: the emitter used to hardcode encoding.color.legend = None
    unconditionally, so a reader had no way to decode dark-vs-light. With a
    resolved legend that permits visibility (as heatmap's family default now
    does — see test_editorial_heatmap_legend_visible_true), VL must get a
    real legend config so it renders its native continuous gradient legend.
    """
    from dbt_charts.core.compile.models.chart.resolved import ResolvedStyleChannel
    from dbt_charts.core.render.chart.emitters.heatmap import HeatmapEmitter

    ch = ResolvedStyleChannel(channel="color", mode="series", data_field="value")
    _hm_ax, _hm_ay = _make_resolved_axes("heatmap", "nominal", "nominal")
    chart = ResolvedHeatmapChart(
        panel_axes=(),
        id="h",
        chart_type="heatmap",
        x="col",
        y="row",
        resolved_channels={"color": ch},
        style=heatmap_style.model_copy(update={"axis_x": _hm_ax, "axis_y": _hm_ay}),
        variable_dependencies=frozenset(),
        palette=(),
        legend=_default_legend().model_copy(update={"visible": True}),
        background=_DEFAULT_CHARTS.background,
        layout_padding=_ZERO_PADDING,
        title_style=_DEFAULT_CHARTS.title,
    )
    spec = HeatmapEmitter().emit(
        chart, _DEFAULT_BOX, regroup((), [{"col": "A", "row": "1", "value": 1}])
    )
    color_enc = spec.encoding["color"]
    assert color_enc["type"] == "quantitative", color_enc
    assert color_enc["legend"] is not None, (
        "heatmap color legend must not be unconditionally suppressed — "
        f"got {color_enc!r}"
    )


def test_heatmap_legend_visible_false_suppresses_color_legend(
    heatmap_style: ResolvedHeatmapStyle,
) -> None:
    """An author can still turn a heatmap's legend off explicitly."""
    from dbt_charts.core.compile.models.chart.resolved import ResolvedStyleChannel
    from dbt_charts.core.render.chart.emitters.heatmap import HeatmapEmitter

    ch = ResolvedStyleChannel(channel="color", mode="series", data_field="value")
    _hm_ax, _hm_ay = _make_resolved_axes("heatmap", "nominal", "nominal")
    chart = ResolvedHeatmapChart(
        panel_axes=(),
        id="h",
        chart_type="heatmap",
        x="col",
        y="row",
        resolved_channels={"color": ch},
        style=heatmap_style.model_copy(update={"axis_x": _hm_ax, "axis_y": _hm_ay}),
        variable_dependencies=frozenset(),
        palette=(),
        legend=_default_legend().model_copy(update={"visible": False}),
        background=_DEFAULT_CHARTS.background,
        layout_padding=_ZERO_PADDING,
        title_style=_DEFAULT_CHARTS.title,
    )
    spec = HeatmapEmitter().emit(
        chart, _DEFAULT_BOX, regroup((), [{"col": "A", "row": "1", "value": 1}])
    )
    assert spec.encoding["color"]["legend"] is None


def test_heatmap_chart_local_gradient_palette_keeps_cascaded_domain() -> None:
    """A chart-local gradient palette override must not drop a cascade-level
    (theme/board) gradient domain — author-reachable regression.

    Authoring `color: <field>` plus a chart-local `style.color.gradient.palette`
    (no min/max) upgrades the color channel to gradient mode via
    normalize_chart_channels, using ONLY the chart-local gradient dict — no
    min/max. Meanwhile `chart.style.color_gradient` is the cascade-complete
    merge (theme/board defaults + chart-local override) and does carry the
    domain. The emitter must prefer the cascade-complete value, not the
    chart-local-only channel scale.
    """
    from dbt_charts.core.compile.config import get_theme_style
    from dbt_charts.core.compile.models.chart.normalized import HeatmapChart
    from dbt_charts.core.compile.models.primitives import ScaleTargetConfigPatch
    from dbt_charts.core.compile.models.style.authored import HeatmapChartStylePatch
    from dbt_charts.core.compile.resolve import resolve
    from dbt_charts.core.compile.resolve.style.board import resolve_chart_style_context
    from dbt_charts.core.render.chart.emitters.heatmap import HeatmapEmitter

    theme = get_theme_style()
    bounded_gradient = theme.charts.heatmap.color.model_copy(
        update={"gradient": ScaleTargetConfigPatch(palette="blues", min=0, max=100)}
    )
    bounded_heatmap = theme.charts.heatmap.model_copy(
        update={"color": bounded_gradient}
    )
    theme_with_bounds = theme.model_copy(
        update={"charts": theme.charts.model_copy(update={"heatmap": bounded_heatmap})}
    )
    board_style = resolve_chart_style_context(theme_with_bounds)

    chart = HeatmapChart(
        id="h",
        type="heatmap",
        x="col",
        y="row",
        color="value",
        style=HeatmapChartStylePatch.model_validate(
            {"color": {"gradient": {"palette": ["#ffffff", "#0000ff"]}}}
        ),
    )
    data = [{"col": "A", "row": "1", "value": 1}]
    resolved = resolve(chart, data, board_style)
    assert resolved.resolved_channels["color"].mode == "gradient", (
        "test setup must reach the chart-local gradient-mode upgrade path, "
        f"got mode={resolved.resolved_channels['color'].mode!r}"
    )
    spec = HeatmapEmitter().emit(resolved, _DEFAULT_BOX, regroup((), data))
    scale = spec.encoding["color"]["scale"]
    assert scale["domain"] == [0, 100], (
        "cascaded min/max must survive a chart-local gradient palette override, "
        f"got scale={scale!r}"
    )
    assert scale["range"] == ["#ffffff", "#0000ff"], (
        "the chart's own gradient palette must win over the theme's default "
        f"scheme (the palette leaf always overrides in the cascade), got scale={scale!r}"
    )


def test_geoshape_projection_in_typed_field_not_config(
    geoshape_style: ResolvedGeoshapeStyle,
) -> None:
    """Emitter reads geo_projection_type (baked at resolve) — not a config lookup."""
    from dbt_charts.core.render.chart.emitters.geo import GeoshapeEmitter

    geo = ResolvedGeoshapeChart(
        id="g",
        chart_type="geoshape",
        geo_projection_type="albersUsa",
        style=geoshape_style,
        **_B,
    )
    spec = GeoshapeEmitter().emit(geo, _DEFAULT_BOX, regroup((), []))
    assert "projection" not in spec.config
    assert spec.projection == {"type": "albersUsa"}


def test_geoshape_choropleth_bad_key_raises_chart_data_error(
    geoshape_style: ResolvedGeoshapeStyle,
) -> None:
    """String-keyed data against a numeric-format geo source raises ChartDataError.

    Regression: v2 _emit_choropleth must call validate_map_lookup_key_contract
    like the oracle does — skipping it yields a silent all-null choropleth.
    """
    import pytest

    from dbt_charts.core.diagnostics.chart_data import ChartDataError
    from dbt_charts.core.render.chart.emitters.geo import GeoshapeEmitter

    geo = ResolvedGeoshapeChart(
        id="geo1",
        chart_type="geoshape",
        geo_url="https://example.com/us-10m.json",
        geo_format_type="topojson",
        geo_feature="states",
        geo_join_key="id",
        geo_projection_type="albersUsa",
        geo_key_format="numeric",
        geo_key_examples=["6", "48", "36"],
        lookup_field="state_name",
        value_field="pop",
        style=geoshape_style,
        **_B,
    )
    data = [
        {"state_name": "California", "pop": 39_500_000},
        {"state_name": "Texas", "pop": 29_000_000},
    ]
    with pytest.raises(ChartDataError):
        GeoshapeEmitter().emit(geo, _DEFAULT_BOX, regroup((), data))


def test_geoshape_complex_projection_emits_params(
    geoshape_style: ResolvedGeoshapeStyle,
) -> None:
    """Complex projection (with params) is assembled from baked semantic fields."""
    from dbt_charts.core.render.chart.emitters.geo import GeoshapeEmitter

    geo = ResolvedGeoshapeChart(
        id="g",
        chart_type="geoshape",
        geo_projection_type="conicEqualArea",
        geo_projection_params={"center": [-96, 38], "scale": 1200},
        style=geoshape_style,
        **_B,
    )
    spec = GeoshapeEmitter().emit(geo, _DEFAULT_BOX, regroup((), []))
    assert spec.projection == {
        "type": "conicEqualArea",
        "center": [-96, 38],
        "scale": 1200,
    }


def test_geoshape_categorical_color_field_uses_board_slot(
    geoshape_style: ResolvedGeoshapeStyle,
) -> None:
    """A board-bound `color:` field paints the choropleth by slot, not gradient.

    Distinct from the ordinary numeric choropleth (value_field magnitude): a
    genuinely categorical, board-bound color field must key each region's
    fill off `category_colors`, the same as any other chart's color channel.
    """
    from dbt_charts.core.compile.models.chart.resolved import ResolvedStyleChannel
    from dbt_charts.core.compile.models.style.theme.category_colors import (
        CategoryColorScale,
    )
    from dbt_charts.core.render.chart.emitters.geo import GeoshapeEmitter

    scale = CategoryColorScale(
        field="status", slots={"Warm": 1, "Cold": 0}, overrides={}
    )
    geo = ResolvedGeoshapeChart(
        id="geo1",
        chart_type="geoshape",
        geo_url="https://example.com/us-10m.json",
        geo_format_type="topojson",
        geo_feature="states",
        geo_join_key="id",
        geo_projection_type="albersUsa",
        lookup_field="state_name",
        value_field="status",
        category_colors=(scale,),
        style=geoshape_style,
        **{
            **_B,
            "palette": ("#111111", "#222222"),
            "resolved_channels": {
                "color": ResolvedStyleChannel(
                    channel="color", mode="series", data_field="status"
                )
            },
        },
    )
    data = [
        {"state_name": "California", "status": "Warm"},
        {"state_name": "Texas", "status": "Cold"},
    ]
    spec = GeoshapeEmitter().emit(geo, _DEFAULT_BOX, regroup((), data))
    color_enc = spec.layers[1].encoding["color"]
    assert color_enc["type"] == "nominal"
    color_of = dict(
        zip(color_enc["scale"]["domain"], color_enc["scale"]["range"], strict=True)
    )
    assert color_of["Warm"] == "#222222"
    assert color_of["Cold"] == "#111111"
    # The tooltip must follow the categorical color type — a quantitative
    # d3 format applied to a string ("Warm"/"Cold") renders NaN.
    tooltip_fields = spec.layers[1].encoding["tooltip"]
    status_tooltip = next(f for f in tooltip_fields if f["field"] == "status")
    assert status_tooltip["type"] == "nominal"
    assert "format" not in status_tooltip


def test_resolve_geo_projection_structured_projection_extracts_type() -> None:
    """_resolve_geo_projection must extract .type from a Projection object, not str() it.

    str(Projection(type='mercator')) yields "type='mercator'" — a broken VL projection name.
    The resolver is the single point that canonicalizes structured projections into bare strings;
    the emitter reads chart.geo_projection_type (already a str) via ADR-008.
    """
    from dbt_charts.core.compile.models.vega_lite.contracts import Projection
    from dbt_charts.core.compile.resolve.chart.geo import _resolve_geo_projection

    proj_type, proj_params = _resolve_geo_projection(Projection(type="mercator"), None)
    assert proj_type == "mercator", f"expected 'mercator', got {proj_type!r}"
    assert proj_params is None


# ---------------------------------------------------------------------------
# HIGH regression: dual color source — pie/geo must use resolved_channels only
# ---------------------------------------------------------------------------


def test_pie_emitter_color_uses_only_resolved_channels(
    pie_style: ResolvedPieStyle,
) -> None:
    """PieEmitter must not fall back to chart.color when resolved_channels has no color.
    All cartesian emitters use resolved_channels exclusively; pie must be consistent."""
    from dbt_charts.core.render.chart.emitters.pie import PieEmitter

    # chart.color is set but resolved_channels has no color entry (empty data)
    chart = ResolvedPieChart(
        id="p1",
        chart_type="pie",
        resolution_width=600.0,
        outer_fraction=0.9,
        attached_table_gap=12.0,
        hybrid_heading_gap=6.0,
        presentation_fingerprint=pie_presentation_fingerprint([]),
        slice_label_indices=(),
        theta="value",
        color="category",
        style=pie_style,
        dark_companion_stops=("#0e4786",),
        **_PB,
    )
    data: list[dict[str, Any]] = []  # empty → resolved_channels won't have color
    spec = PieEmitter().emit(chart, _DEFAULT_BOX, regroup((), data))
    assert "color" not in spec.layers[0].encoding, (
        "PieEmitter fell back to chart.color when resolved_channels had no color; "
        "use resolved_channels exclusively to be consistent with cartesian emitters"
    )


def test_pie_emitter_no_color_label_uses_dark_companion_fill(
    pie_style: ResolvedPieStyle,
) -> None:
    """No-color pie slice labels ink with the baked dark-companion stop, not the
    theme's static label.font color — mirrors the color-channel branch, which
    never reads label.font.color either."""
    from dbt_charts.core.render.chart.emitters.pie import PieEmitter

    chart = _pie(pie_style).model_copy(update={"dark_companion_stops": ("#123456",)})
    spec = PieEmitter().emit(chart, _DEFAULT_BOX, regroup((), []))

    label_layer = next(
        layer for layer in spec.layers if "lineHeight" in layer.mark_props
    )
    assert label_layer.mark_props.get("fill") == "#123456"


def test_pie_emitter_no_color_arc_and_label_share_palette_zero(
    pie_style: ResolvedPieStyle,
) -> None:
    """No-color pie wedges are explicitly filled with palette[0] — the same
    color the label's dark-companion ink is derived from — so the label
    actually anchors to a color the wedge displays, not an arbitrary one."""
    from dbt_charts.core.render.chart.emitters.pie import PieEmitter

    chart = _pie(pie_style)
    spec = PieEmitter().emit(chart, _DEFAULT_BOX, regroup((), []))

    arc_layer = spec.layers[0]
    assert arc_layer.mark == "arc"
    assert arc_layer.mark_props.get("fill") == chart.palette[0]


def test_pie_emitter_total_value_font_style_reaches_mark(
    pie_style: ResolvedPieStyle,
) -> None:
    """font.style authored on pie.total.value reaches the center-total value
    mark as VL's fontStyle — the shared FontStyle→mark helper's contract."""
    from dbt_charts.core.compile.models.chart.authored import ChartTotal
    from dbt_charts.core.render.chart.emitters.pie import PieEmitter

    total_style = pie_style.total_style
    italic_value = total_style.value.model_copy(
        update={"font": total_style.value.font.model_copy(update={"style": "italic"})}
    )
    style = pie_style.model_copy(
        update={"total_style": total_style.model_copy(update={"value": italic_value})}
    )
    chart = _pie(style).model_copy(update={"total": ChartTotal(visible=True)})
    spec = PieEmitter().emit(chart, _DEFAULT_BOX, regroup((), []))

    value_layer = next(
        layer for layer in spec.layers if layer.mark_props.get("baseline") == "bottom"
    )
    assert value_layer.mark_props.get("fontStyle") == "italic"


def test_pie_emitter_slice_label_font_style_reaches_mark(
    pie_style: ResolvedPieStyle,
) -> None:
    """font.style authored on the slice-labels font reaches the leader-line
    label mark as VL's fontStyle, mirroring family/size/weight."""
    from dbt_charts.core.render.chart.emitters.pie import PieEmitter

    labels = pie_style.slice_mark.labels
    assert labels is not None
    italic_labels = labels.model_copy(
        update={"font": labels.font.model_copy(update={"style": "italic"})}
    )
    style = pie_style.model_copy(
        update={
            "slice_mark": pie_style.slice_mark.model_copy(
                update={"labels": italic_labels}
            )
        }
    )
    chart = _pie(style)
    spec = PieEmitter().emit(chart, _DEFAULT_BOX, regroup((), []))

    label_layer = next(
        layer for layer in spec.layers if "lineHeight" in layer.mark_props
    )
    assert label_layer.mark_props.get("fontStyle") == "italic"


def test_geoshape_emitter_color_uses_only_resolved_channels(
    geoshape_style: ResolvedGeoshapeStyle,
) -> None:
    """GeoshapeEmitter must not fall back to chart.color when resolved_channels has no color."""
    from dbt_charts.core.compile.models.chart.resolved.geoshape import (
        ResolvedGeoshapeChart,
    )
    from dbt_charts.core.render.chart.emitters.geo import GeoshapeEmitter

    # With no resolved_channels and no color field, emitter must not emit color encoding.
    chart = ResolvedGeoshapeChart(
        id="g1",
        chart_type="geoshape",
        style=geoshape_style,
        **_B,
    )
    data: list[dict[str, Any]] = []
    spec = GeoshapeEmitter().emit(chart, _DEFAULT_BOX, regroup((), data))
    assert "color" not in spec.encoding, (
        "GeoshapeEmitter must not emit color encoding when resolved_channels has no color"
    )


# get_emitter rejecting non-VL families (kpi/table/spark_bar/callout — routed
# before emit via BoardRenderSession.render_svg_family) is pinned by
# test_get_emitter_rejects_non_vl in test_v2_non_vl_routing.py.


# ---------------------------------------------------------------------------
# channel_to_encoding — series-mode type inference
# ---------------------------------------------------------------------------


def test_channel_to_encoding_series_numeric_data_is_quantitative() -> None:
    """Series mode with numeric column must infer quantitative — not hardcode nominal."""
    from dbt_charts.core.compile.models.chart.resolved import ResolvedStyleChannel
    from dbt_charts.core.render.chart.emitters._channels import channel_to_encoding

    ch = ResolvedStyleChannel(channel="color", mode="series", data_field="value")
    result = channel_to_encoding(ch, [{"value": 42}])
    assert result == {"field": "value", "type": "quantitative"}


def test_channel_to_encoding_series_empty_data_is_nominal() -> None:
    """Series mode with no data stays nominal (infer_vega_type_from_data contract)."""
    from dbt_charts.core.compile.models.chart.resolved import ResolvedStyleChannel
    from dbt_charts.core.render.chart.emitters._channels import channel_to_encoding

    ch = ResolvedStyleChannel(channel="color", mode="series", data_field="value")
    result = channel_to_encoding(ch, [])
    assert result == {"field": "value", "type": "nominal"}


def test_heatmap_emitter_string_series_color_is_nominal(
    heatmap_style: ResolvedHeatmapStyle,
) -> None:
    """HeatmapEmitter: series-mode color with STRING data must emit type=nominal.

    Regression pin for removing the old workaround that hardcoded 'quantitative'
    regardless of data type — with the fix, a string color column on a heatmap
    correctly gets nominal (e.g. a categorical breakdown rather than a gradient).
    """
    from dbt_charts.core.compile.models.chart.resolved import ResolvedStyleChannel
    from dbt_charts.core.compile.models.chart.resolved.heatmap import (
        ResolvedHeatmapChart,
    )
    from dbt_charts.core.render.chart.emitters.heatmap import HeatmapEmitter

    ch = ResolvedStyleChannel(channel="color", mode="series", data_field="category")
    _s_ax, _s_ay = _make_resolved_axes("heatmap", "nominal", "nominal")
    chart = ResolvedHeatmapChart(
        panel_axes=(),
        id="hm1",
        chart_type="heatmap",
        x="cat",
        y="sub",
        resolved_channels={"color": ch},
        style=heatmap_style.model_copy(update={"axis_x": _s_ax, "axis_y": _s_ay}),
        variable_dependencies=frozenset(),
        palette=(),
        legend=_default_legend(),
        background=_DEFAULT_CHARTS.background,
        title_style=_DEFAULT_CHARTS.title,
        layout_padding=_ZERO_PADDING,
    )
    data = [{"cat": "A", "sub": "X", "category": "low"}]
    spec = HeatmapEmitter().emit(chart, _DEFAULT_BOX, regroup((), data))
    assert spec.encoding["color"]["type"] == "nominal", (
        f"Expected nominal for string series color, got {spec.encoding['color']!r}"
    )


def test_heatmap_emitter_numeric_axis_fields_are_nominal(
    heatmap_style: ResolvedHeatmapStyle,
) -> None:
    """Heatmap x/y are grid dimensions, so they must be nominal band scales even
    when the data is numeric (e.g. hour_of_day 0..23).

    Regression pin: inferring the vega type from data made an integer x-axis
    quantitative, which collapsed each rect cell to zero width (``h0``) instead
    of a band cell. Matches oracle _map_rect which hardcodes nominal x/y.
    """
    from dbt_charts.core.compile.models.chart.resolved.heatmap import (
        ResolvedHeatmapChart,
    )
    from dbt_charts.core.render.chart.emitters.heatmap import HeatmapEmitter

    _n_ax, _n_ay = _make_resolved_axes("heatmap", "nominal", "nominal")
    chart = ResolvedHeatmapChart(
        panel_axes=(),
        id="hm_num",
        chart_type="heatmap",
        x="hour_of_day",
        y="day_of_week",
        style=heatmap_style.model_copy(update={"axis_x": _n_ax, "axis_y": _n_ay}),
        **_C,
    )
    data = [{"hour_of_day": h, "day_of_week": "Mon"} for h in range(24)]
    spec = HeatmapEmitter().emit(chart, _DEFAULT_BOX, regroup((), data))
    assert spec.encoding["x"]["type"] == "nominal", spec.encoding["x"]
    assert spec.encoding["y"]["type"] == "nominal", spec.encoding["y"]


def test_point_map_emitter_series_color_is_quantitative(
    point_map_style: ResolvedPointMapStyle,
) -> None:
    """PointMapEmitter: series-mode color with numeric data must emit type=quantitative.

    Regression pin for bubble_map bug: channel_to_encoding hardcoded 'nominal',
    oracle (geo.py) calls infer_vega_type_from_data which returns 'quantitative'.
    """
    from dbt_charts.core.compile.models.chart.resolved import ResolvedStyleChannel
    from dbt_charts.core.compile.models.chart.resolved.point_map import (
        ResolvedPointMapChart,
    )
    from dbt_charts.core.render.chart.emitters.geo import PointMapEmitter

    ch = ResolvedStyleChannel(channel="color", mode="series", data_field="intensity")
    chart = ResolvedPointMapChart(
        id="pm1",
        chart_type="point_map",
        latitude="lat",
        longitude="lon",
        projection="albersUsa",
        collapse=False,
        resolved_channels={"color": ch},
        style=point_map_style,
        variable_dependencies=frozenset(),
        palette=(),
        legend=_default_legend(),
        background=_DEFAULT_CHARTS.background,
        title_style=_DEFAULT_CHARTS.title,
        layout_padding=_ZERO_PADDING,
    )
    data = [{"lat": 37.0, "lon": -122.0, "intensity": 99}]
    spec = PointMapEmitter().emit(chart, _DEFAULT_BOX, regroup((), data))
    assert spec.encoding["color"]["type"] == "quantitative", (
        f"Expected quantitative for numeric series color, got {spec.encoding['color']!r}"
    )


# ---------------------------------------------------------------------------
# RC-3: channel_to_encoding axis helper extension
# ---------------------------------------------------------------------------


def test_channel_to_encoding_with_axis_style_emits_axis_key() -> None:
    """axis_style kwarg must produce an 'axis' key via axis_to_vl."""
    from dbt_charts.core.compile.models.chart.resolved import ResolvedStyleChannel
    from dbt_charts.core.render.chart.emitters._channels import channel_to_encoding

    style = _make_style()
    ch = ResolvedStyleChannel(channel="x", mode="series", data_field="month")
    result = channel_to_encoding(ch, [{"month": "Jan"}], axis_style=style.axis)
    assert result is not None
    assert "axis" in result, f"Expected 'axis' key, got {list(result.keys())}"


def test_channel_to_encoding_with_title_emits_title() -> None:
    """title kwarg must appear in the encoding dict."""
    from dbt_charts.core.compile.models.chart.resolved import ResolvedStyleChannel
    from dbt_charts.core.render.chart.emitters._channels import channel_to_encoding

    ch = ResolvedStyleChannel(channel="y", mode="series", data_field="revenue")
    result = channel_to_encoding(ch, [{"revenue": 100}], title="Revenue")
    assert result is not None
    assert result.get("title") == "Revenue"


def test_channel_to_encoding_with_format_str() -> None:
    """format_str kwarg must appear as 'format' in the encoding dict."""
    from dbt_charts.core.compile.models.chart.resolved import ResolvedStyleChannel
    from dbt_charts.core.render.chart.emitters._channels import channel_to_encoding

    ch = ResolvedStyleChannel(channel="y", mode="series", data_field="revenue")
    result = channel_to_encoding(ch, [{"revenue": 100}], format_str=",.0f")
    assert result is not None
    assert result.get("format") == ",.0f"


def test_channel_to_encoding_no_overrides_has_no_axis_title_format() -> None:
    """Default call (no optional params) must not inject axis/title/format/scale."""
    from dbt_charts.core.compile.models.chart.resolved import ResolvedStyleChannel
    from dbt_charts.core.render.chart.emitters._channels import channel_to_encoding

    ch = ResolvedStyleChannel(channel="x", mode="series", data_field="month")
    result = channel_to_encoding(ch, [{"month": "Jan"}])
    assert result is not None
    for key in ("axis", "title", "format", "scale"):
        assert key not in result, f"Unexpected '{key}' in result: {result}"


# ---------------------------------------------------------------------------
# MEDIUM-4 regression: gradient channel + scale override must raise, not clobber
# ---------------------------------------------------------------------------


def test_gradient_channel_with_scale_override_raises() -> None:
    """Passing scale= to a gradient channel that already has a scale must raise
    ValueError — not silently clobber the gradient scale dict."""
    from dbt_charts.core.compile.models.chart.resolved import ResolvedStyleChannel
    from dbt_charts.core.compile.models.primitives import ScaleTargetConfig
    from dbt_charts.core.render.chart.emitters._channels import channel_to_encoding

    ch = ResolvedStyleChannel(
        channel="color",
        mode="gradient",
        data_field="value",
        scale=ScaleTargetConfig(palette=["#fff", "#000"]),
    )
    with pytest.raises(ValueError, match="scale"):
        channel_to_encoding(ch, [{"value": 1}], scale={"type": "linear"})


# ---------------------------------------------------------------------------
# Regression: pie resolve populates ResolvedPieStyle
# ---------------------------------------------------------------------------


def test_pie_resolve_style_slice_is_resolved_pie_style() -> None:
    from dbt_charts.core.compile.config import get_theme_style
    from dbt_charts.core.compile.models.chart.normalized import PieChart
    from dbt_charts.core.compile.models.query.normalized import SqlQuery
    from dbt_charts.core.compile.models.style.resolved import ResolvedPieStyle
    from dbt_charts.core.compile.resolve import resolve
    from dbt_charts.core.compile.resolve.style.board import resolve_chart_style_context

    compiled = PieChart(
        id="p",
        type="pie",
        theta="amount",
        query=SqlQuery(sql="SELECT 1 AS amount", source="test_db"),
        query_name="q",
    )
    board_style = resolve_chart_style_context(get_theme_style())
    resolved = resolve(compiled, [{"amount": 100}], board_style)
    assert isinstance(resolved.style, ResolvedPieStyle)
    assert isinstance(resolved.style.tooltip_format, str)


# ---------------------------------------------------------------------------
# TDD: stack="none" sentinel → grouped (xOffset) output, no VL "none" emitted
# ---------------------------------------------------------------------------


def test_bar_stack_none_resolves_to_grouped_no_stack_in_vl() -> None:
    """stack='none' must produce grouped output — xOffset encoding, no VL 'none' emitted.

    Uses numeric x (quarter) so _bar_orientation → vertical → xOffset is the
    grouping channel (for horizontal bars it would be yOffset; both are correct).
    An authored ``axis_x.type: ordinal`` keeps quarter on a discrete (band)
    scale so this test exercises genuine band-scale grouping — ``xOffset`` is
    actually emitted unconditionally regardless of x's VL type. A continuous
    (quantitative) x instead needs an explicit ``y.stack: null`` to stop VL's
    own auto-stack from collapsing the grouped bars (see
    ``test_grouped_bar_on_quantitative_x_paints_visible_marks`` in
    ``test_mark_no_paint_guard.py`` for that case, and
    ``test_grouped_bar_stack_null_only_emitted_for_continuous_x`` below for
    the gate itself).
    """
    from dbt_charts.core.compile.config import get_default_theme_name, get_theme_style
    from dbt_charts.core.compile.models.chart.normalized import BarChart
    from dbt_charts.core.compile.models.chart.resolved.bar import ResolvedBarChart
    from dbt_charts.core.compile.models.query.normalized import SqlQuery
    from dbt_charts.core.compile.models.style.authored import (
        AxisXStylePatch,
        BarChartStylePatch,
    )
    from dbt_charts.core.compile.resolve import resolve
    from dbt_charts.core.compile.resolve.style.board import resolve_style
    from dbt_charts.core.render.chart.session import BoardRenderSession

    # Numeric quarter → vertical bar → grouped uses xOffset.
    data = [
        {"quarter": 1, "revenue": 100, "region": "A"},
        {"quarter": 1, "revenue": 80, "region": "B"},
        {"quarter": 2, "revenue": 110, "region": "A"},
        {"quarter": 2, "revenue": 90, "region": "B"},
    ]
    compiled = BarChart(
        id="grouped_bar",
        type="bar",
        stack="none",
        x="quarter",
        y="revenue",
        color="region",
        style=BarChartStylePatch(axis_x=AxisXStylePatch(type="ordinal")),
        query=SqlQuery(sql="SELECT 1", source="test_db"),
        query_name="q",
    )
    board_context = _make_style()
    board_style = resolve_style(get_theme_style(get_default_theme_name()))
    resolved_chart = resolve(compiled, data, chart_style_context=board_context)
    assert isinstance(resolved_chart, ResolvedBarChart)
    assert resolved_chart.stack == "none", (
        f"resolved.stack should be 'none', got {resolved_chart.stack!r}"
    )
    assert resolved_chart.orientation == "vertical", (
        f"numeric x should produce vertical bar, got {resolved_chart.orientation!r}"
    )

    session = BoardRenderSession.create(board_style)
    spec = session.emit_chart(
        resolved_chart, _DEFAULT_BOX, {resolved_chart.query_name: data}
    )
    vl = session.finalize_vl(spec)

    # The literal string "none" must never leak into VL's stack property.
    assert vl["encoding"]["y"].get("stack") != "none", (
        "literal 'none' must not be emitted into VL stack property"
    )
    # xOffset on the color field indicates grouped columns on a vertical bar
    # — checked on the encoding itself, not a substring of the dumped spec,
    # since the always-on zero-baseline rule layer also carries a static
    # (unrelated) `xOffset: {value: 0}`.
    assert vl["encoding"]["xOffset"]["field"] == "region", (
        "grouped vertical bar must produce an xOffset encoding on the color field"
    )


def _grouped_bar_chart(x_field: str) -> Any:
    from dbt_charts.core.compile.models.chart.normalized import BarChart
    from dbt_charts.core.compile.models.query.normalized import SqlQuery
    from dbt_charts.core.compile.models.style.authored import BarChartStylePatch

    return BarChart(
        id="grouped_stack_gate",
        type="bar",
        stack="none",
        x=x_field,
        y="val",
        color="series",
        style=BarChartStylePatch(orientation="vertical"),
        query=SqlQuery(sql="SELECT 1", source="test_db"),
        query_name="q",
    )


def test_grouped_bar_stack_null_only_emitted_for_continuous_x() -> None:
    """``y.stack: null`` (VL auto-stack suppression) is only needed on a
    genuinely continuous x — ``xOffset`` already disambiguates a discrete
    (nominal/ordinal) x, so each x/color pair is already its own group and
    VL's auto-stack is a no-op there. Emitting the key anyway used to flip
    VL's own inferred sort-op default (sum → min) for every sorted grouped
    vertical bar, regardless of whether the chart's x was ever continuous —
    see the two ``test_grouped_bar_*_sort_*`` tests below for that
    regression.
    """
    from dbt_charts.core.compile.config import get_default_theme_name, get_theme_style
    from dbt_charts.core.compile.resolve import resolve
    from dbt_charts.core.compile.resolve.style.board import resolve_style
    from dbt_charts.core.render.chart.session import BoardRenderSession

    board_context = _make_style()
    board_style = resolve_style(get_theme_style(get_default_theme_name()))
    session = BoardRenderSession.create(board_style)

    data_cat = [
        {"cat": "P", "series": "A", "val": 1},
        {"cat": "P", "series": "B", "val": 100},
        {"cat": "Q", "series": "A", "val": 50},
        {"cat": "Q", "series": "B", "val": 40},
    ]
    resolved_cat = resolve(
        _grouped_bar_chart("cat"), data_cat, chart_style_context=board_context
    )
    spec_cat = session.emit_chart(resolved_cat, _DEFAULT_BOX, {"q": data_cat})
    vl_cat = session.finalize_vl(spec_cat)
    assert "stack" not in vl_cat["encoding"]["y"], (
        "a discrete (nominal) x already disambiguates via xOffset — no "
        f"stack key should be emitted, got {vl_cat['encoding']['y'].get('stack')!r}"
    )

    data_num = [
        {"x_num": 1, "series": "A", "val": 1},
        {"x_num": 1, "series": "B", "val": 100},
        {"x_num": 2, "series": "A", "val": 50},
        {"x_num": 2, "series": "B", "val": 40},
    ]
    resolved_num = resolve(
        _grouped_bar_chart("x_num"), data_num, chart_style_context=board_context
    )
    spec_num = session.emit_chart(resolved_num, _DEFAULT_BOX, {"q": data_num})
    vl_num = session.finalize_vl(spec_num)
    assert vl_num["encoding"]["y"]["stack"] is None, (
        "a continuous (quantitative) x must explicitly suppress VL's "
        f"implicit auto-stack, got {vl_num['encoding']['y'].get('stack')!r}"
    )


def _grouped_sort_chart(orientation: str) -> Any:
    from dbt_charts.core.compile.models.chart.normalized import BarChart
    from dbt_charts.core.compile.models.query.normalized import SqlQuery
    from dbt_charts.core.compile.models.style.authored import BarChartStylePatch

    return BarChart(
        id="grouped_sort",
        type="bar",
        stack="none",
        x="cat",
        y="val",
        color="series",
        sort={"by": "val", "order": "desc"},
        style=BarChartStylePatch(orientation=orientation),
        query=SqlQuery(sql="SELECT 1", source="test_db"),
        query_name="q",
    )


def test_grouped_bar_vertical_sort_orders_x_domain_by_the_category_value() -> None:
    """A vertical grouped bar's authored x sort folds each category with ``min``.

    Nothing stacks in a grouped bar, so there is no stacked total for a sort by
    the measure to mean — each category is ordered by the measure's own value
    there, the same reading every other sort column gets. What Vega-Lite would
    infer left to itself is not that: it lands on ``sum`` on a banded x (where
    ``xOffset`` disambiguates and no ``y.stack: null`` is emitted) and on
    ``min`` where one is, an order that moves with how the spec happened to
    compose. The emitter pins the aggregate instead, so this guard verifies the
    actual rendered category order rather than the pinned ``op``.

    Category P (1, 100 → sum 101, min 1) and Q (50, 40 → sum 90, min 40) sort
    oppositely under sum-desc vs min-desc, so this is a real behavioral fork,
    not a cosmetic one.
    """
    import vl_convert as vlc

    from dbt_charts.core.compile.config import get_default_theme_name, get_theme_style
    from dbt_charts.core.compile.resolve import resolve
    from dbt_charts.core.compile.resolve.style.board import resolve_style
    from dbt_charts.core.render.chart.session import BoardRenderSession

    data = [
        {"cat": "P", "series": "A", "val": 1},
        {"cat": "P", "series": "B", "val": 100},
        {"cat": "Q", "series": "A", "val": 50},
        {"cat": "Q", "series": "B", "val": 40},
    ]
    board_context = _make_style()
    board_style = resolve_style(get_theme_style(get_default_theme_name()))
    resolved_chart = resolve(
        _grouped_sort_chart("vertical"), data, chart_style_context=board_context
    )

    session = BoardRenderSession.create(board_style)
    spec = session.emit_chart(
        resolved_chart, _DEFAULT_BOX, {resolved_chart.query_name: data}
    )
    vl = session.finalize_vl(spec)

    svg = vlc.vegalite_to_svg(vl)
    # Vega renders x-axis category ticks as <text> elements in domain order;
    # find them by their known text content rather than position.
    positions = {cat: svg.index(f">{cat}<") for cat in ("P", "Q")}
    rendered_order = sorted(positions, key=positions.get)
    assert rendered_order == ["Q", "P"], (
        "rendered category order must follow desc-by-min sort (Q/P), got "
        f"{rendered_order!r} — sum-vs-min sort aggregate is being silently "
        "flipped"
    )


def test_grouped_bar_horizontal_sort_matches_the_vertical_aggregate() -> None:
    """Control for the vertical case above: a horizontal grouped bar folds its
    categorical (VL y) sort with the same ``min``, so the two orientations
    cannot disagree about what a grouped bar's sort means.
    """
    import vl_convert as vlc

    from dbt_charts.core.compile.config import get_default_theme_name, get_theme_style
    from dbt_charts.core.compile.resolve import resolve
    from dbt_charts.core.compile.resolve.style.board import resolve_style
    from dbt_charts.core.render.chart.session import BoardRenderSession

    data = [
        {"cat": "P", "series": "A", "val": 1},
        {"cat": "P", "series": "B", "val": 100},
        {"cat": "Q", "series": "A", "val": 50},
        {"cat": "Q", "series": "B", "val": 40},
    ]
    board_context = _make_style()
    board_style = resolve_style(get_theme_style(get_default_theme_name()))
    resolved_chart = resolve(
        _grouped_sort_chart("horizontal"), data, chart_style_context=board_context
    )

    session = BoardRenderSession.create(board_style)
    spec = session.emit_chart(
        resolved_chart, _DEFAULT_BOX, {resolved_chart.query_name: data}
    )
    vl = session.finalize_vl(spec)

    svg = vlc.vegalite_to_svg(vl)
    positions = {cat: svg.index(f">{cat}<") for cat in ("P", "Q")}
    rendered_order = sorted(positions, key=positions.get)
    assert rendered_order == ["Q", "P"], (
        "rendered category order must follow desc-by-min sort (Q/P), got "
        f"{rendered_order!r} — sum-vs-min sort aggregate is being silently "
        "flipped"
    )


# ---------------------------------------------------------------------------
# legend_visible=False → "legend": null in color encoding
# ---------------------------------------------------------------------------


def test_channel_to_encoding_legend_hidden_emits_legend_null() -> None:
    """legend_hidden=True must add ``"legend": None`` to a series color encoding."""
    from dbt_charts.core.compile.models.chart.resolved import ResolvedStyleChannel
    from dbt_charts.core.render.chart.emitters._channels import channel_to_encoding

    ch = ResolvedStyleChannel(channel="color", mode="series", data_field="region")
    result = channel_to_encoding(
        ch, [{"region": "A", "revenue": 1}], legend_hidden=True
    )
    assert result is not None
    assert result.get("legend") is None, f"Expected legend=None, got: {result}"
    assert "legend" in result, (
        "Key 'legend' must be present (with None value) when hidden"
    )


def test_bar_legend_visible_false_suppresses_legend(
    bar_style: ResolvedBarStyle,
) -> None:
    """ResolvedBarChart with legend_visible=False must produce color encoding with legend=null."""
    from dbt_charts.core.compile.config import get_default_theme_name, get_theme_style
    from dbt_charts.core.compile.models.chart.normalized import BarChart
    from dbt_charts.core.compile.models.query.normalized import SqlQuery
    from dbt_charts.core.compile.resolve import resolve
    from dbt_charts.core.compile.resolve.style.board import resolve_style
    from dbt_charts.core.render.chart.session import BoardRenderSession

    data = [
        {"month": "Jan", "revenue": 100, "region": "A"},
        {"month": "Jan", "revenue": 80, "region": "B"},
    ]
    compiled = BarChart(
        id="legend_bar",
        type="bar",
        x="month",
        y="revenue",
        color="region",
        query=SqlQuery(sql="SELECT 1", source="test_db"),
        query_name="q",
    )
    board_context = _make_style()
    board_style = resolve_style(get_theme_style(get_default_theme_name()))
    resolved_chart = resolve(compiled, data, chart_style_context=board_context)
    assert isinstance(resolved_chart, ResolvedBarChart)

    # Inject legend.visible=False to simulate style.legend.visible=False
    resolved_hidden = resolved_chart.model_copy(
        update={"legend": resolved_chart.legend.model_copy(update={"visible": False})}
    )

    session = BoardRenderSession.create(board_style)
    spec = session.emit_chart(
        resolved_hidden, _DEFAULT_BOX, {resolved_hidden.query_name: data}
    )
    vl = session.finalize_vl(spec)

    # Color encoding must have "legend": null (VL suppresses the legend)
    color_enc = vl.get("encoding", {}).get("color", {})
    assert "legend" in color_enc, f"color encoding missing 'legend' key: {color_enc}"
    assert color_enc["legend"] is None, (
        f"Expected legend=null, got: {color_enc['legend']}"
    )


# ---------------------------------------------------------------------------
# Bar x-axis type: datetime.date values → ordinal, not temporal
# ---------------------------------------------------------------------------


def test_bar_x_axis_date_values_produce_ordinal_not_temporal(
    bar_style: ResolvedBarStyle,
) -> None:
    """Bar x-axis encoding must be 'ordinal' (not 'temporal') when data has date objects.

    Bar x is always a categorical grouping field. VL 'temporal' creates a continuous
    scale which breaks bar layout; 'ordinal' keeps bars discrete.
    """
    import datetime

    from dbt_charts.core.compile.config import get_default_theme_name, get_theme_style
    from dbt_charts.core.compile.models.chart.normalized import BarChart
    from dbt_charts.core.compile.models.query.normalized import SqlQuery
    from dbt_charts.core.compile.resolve import resolve
    from dbt_charts.core.compile.resolve.style.board import resolve_style
    from dbt_charts.core.render.chart.session import BoardRenderSession

    data = [
        {"month": datetime.date(2024, 1, 1), "revenue": 100},
        {"month": datetime.date(2024, 2, 1), "revenue": 200},
    ]
    compiled = BarChart(
        id="date_bar",
        type="bar",
        x="month",
        y="revenue",
        query=SqlQuery(sql="SELECT 1", source="test_db"),
        query_name="q",
    )
    board_context = _make_style()
    board_style = resolve_style(get_theme_style(get_default_theme_name()))
    resolved_chart = resolve(compiled, data, chart_style_context=board_context)
    session = BoardRenderSession.create(board_style)
    spec = session.emit_chart(
        resolved_chart, _DEFAULT_BOX, {resolved_chart.query_name: data}
    )
    vl = session.finalize_vl(spec)

    x_enc = vl.get("encoding", {}).get("x", {})
    assert x_enc.get("type") != "temporal", (
        f"Bar x-axis must not be 'temporal' for date data; got: {x_enc.get('type')}"
    )
    assert x_enc.get("type") == "ordinal", (
        f"Bar x-axis must be 'ordinal' for date data; got: {x_enc.get('type')}"
    )


# ---------------------------------------------------------------------------
# Chart title emitted in VL spec
# ---------------------------------------------------------------------------


def test_bar_title_appears_in_vl(bar_style: ResolvedBarStyle) -> None:
    """ResolvedBarChart.title must appear as spec['title']['text'] in the VL output."""
    from dbt_charts.core.compile.config import get_default_theme_name, get_theme_style
    from dbt_charts.core.compile.models.chart.normalized import BarChart
    from dbt_charts.core.compile.models.query.normalized import SqlQuery
    from dbt_charts.core.compile.resolve import resolve
    from dbt_charts.core.compile.resolve.style.board import resolve_style
    from dbt_charts.core.render.chart.session import BoardRenderSession

    data = [{"month": "Jan", "revenue": 100}]
    compiled = BarChart(
        id="titled_bar",
        type="bar",
        x="month",
        y="revenue",
        title="My Revenue Chart",
        query=SqlQuery(sql="SELECT 1", source="test_db"),
        query_name="q",
    )
    board_context = _make_style()
    board_style = resolve_style(get_theme_style(get_default_theme_name()))
    resolved_chart = resolve(compiled, data, chart_style_context=board_context)
    session = BoardRenderSession.create(board_style)
    spec = session.emit_chart(
        resolved_chart, _DEFAULT_BOX, {resolved_chart.query_name: data}
    )
    vl = session.finalize_vl(spec)

    title_block = vl.get("title")
    assert title_block is not None, (
        "VL spec must have a 'title' key when chart has title"
    )
    assert title_block.get("text") == "My Revenue Chart", (
        f"title text mismatch; got: {title_block}"
    )


# ---------------------------------------------------------------------------
# build_cartesian_x_encoding wiring — line, area, bar, layered
# ---------------------------------------------------------------------------

# Shared test data for yearmonth bucketed grain
_YEARMONTH_DATA = [
    {"date": f"2025-0{i}-01", "value": float(i * 10_000)} for i in range(1, 6)
]
_YEARWEEK_DATA = [
    {"date": f"2024-01-{day:02d}", "value": float(day * 1_000)}
    for day in (1, 8, 15, 22, 29)
]


def _yearmonth_axis() -> Any:
    """Resolved x-axis with time_unit='yearmonth' for bucketed-grain wiring tests."""
    import dataclasses

    ax, _ = _make_resolved_axes("line", "temporal", "quantitative")
    return dataclasses.replace(ax, time_unit="yearmonth")


def _yearweek_axis(axis: Any) -> Any:
    import dataclasses

    return dataclasses.replace(axis, time_unit="yearweek")


def _zero_ay() -> Any:
    """Resolved y-axis with scale.zero=True to exercise y_zero_scale wiring."""
    import dataclasses

    from dbt_charts.core.compile.models.style.resolved import (
        ResolvedScaleContinuousStyle,
        ResolvedScaleStyle,
    )

    _, ay = _make_resolved_axes("line", "temporal", "quantitative")
    return dataclasses.replace(
        ay, scale=ResolvedScaleStyle(continuous=ResolvedScaleContinuousStyle(zero=True))
    )


# ---- Line ----------------------------------------------------------------


def test_line_x_type_temporal_for_yearmonth_axis(line_style: ResolvedLineStyle) -> None:
    """Line emitter must call build_cartesian_x_encoding — temporal for bucketed grain."""
    from dbt_charts.core.render.chart.emitters.line import LineEmitter

    chart = ResolvedLineChart(
        panel_axes=(),
        id="l1",
        chart_type="line",
        x="date",
        y="value",
        style=line_style.model_copy(update={"axis_x": _yearmonth_axis()}),
        **_C,
    )
    spec = LineEmitter().emit(chart, _DEFAULT_BOX, regroup((), _YEARMONTH_DATA))
    assert spec.encoding["x"]["type"] == "temporal"


def test_line_x_axis_label_expr_injected_for_yearmonth(
    line_style: ResolvedLineStyle,
) -> None:
    """build_cartesian_x_encoding injects a smart-cadence labelExpr; the
    continuous temporal path has no axis.values (that's the ordinal-only
    tick-density mechanism — see bar's equivalent test)."""
    from dbt_charts.core.render.chart.emitters.line import LineEmitter

    chart = ResolvedLineChart(
        panel_axes=(),
        id="l1",
        chart_type="line",
        x="date",
        y="value",
        style=line_style.model_copy(update={"axis_x": _yearmonth_axis()}),
        **_C,
    )
    spec = LineEmitter().emit(chart, _DEFAULT_BOX, regroup((), _YEARMONTH_DATA))
    ax_vl = spec.encoding["x"]["axis"]
    assert "labelExpr" in ax_vl, "x axis should have labelExpr"


def test_line_yearweek_axis_uses_day_number_labels(
    line_style: ResolvedLineStyle,
) -> None:
    from dbt_charts.core.render.chart.emitters.line import LineEmitter

    chart = ResolvedLineChart(
        panel_axes=(),
        id="l1",
        chart_type="line",
        x="date",
        y="value",
        style=line_style.model_copy(
            update={"axis_x": _yearweek_axis(line_style.axis_x)}
        ),
        **_C,
    )
    spec = LineEmitter().emit(chart, _DEFAULT_BOX, regroup((), _YEARWEEK_DATA))
    axis = spec.encoding["x"]["axis"]

    assert "%-d" in axis["labelExpr"]
    assert "W%V" not in axis["labelExpr"]
    assert "utcOffset('day', toDate(datum.value), 1)" in axis["labelExpr"]
    assert "values" not in axis


def test_line_y_scale_zero_true_when_axis_scale_zero(
    line_style: ResolvedLineStyle,
) -> None:
    """y_zero_scale is wired — axis scale.zero=True → y encoding emits zero+domainMin."""
    import dataclasses

    from dbt_charts.core.render.chart.emitters.line import LineEmitter

    ax, _ = _make_resolved_axes("line", "temporal", "quantitative")
    chart = ResolvedLineChart(
        panel_axes=(),
        id="l1",
        chart_type="line",
        x="date",
        y="value",
        style=line_style.model_copy(
            update={
                "axis_x": dataclasses.replace(ax, time_unit="yearmonth"),
                "axis_y": _zero_ay(),
            }
        ),
        **_C,
    )
    spec = LineEmitter().emit(chart, _DEFAULT_BOX, regroup((), _YEARMONTH_DATA))
    scale = spec.encoding["y"]["scale"]
    assert scale["zero"] is True
    # This axis carries no tick ladder, so no rung supplies a floor and none
    # is pinned — `zero: True` is what anchors the domain. See
    # TestYZeroScale.test_scale_zero_true_no_ticks_pins_no_domain_min.
    assert "domainMin" not in scale


# ---- Area ----------------------------------------------------------------


def test_area_x_type_temporal_for_yearmonth_axis(area_style: ResolvedAreaStyle) -> None:
    """Area emitter x type = temporal when yearmonth axis."""
    from dbt_charts.core.render.chart.emitters.area import AreaEmitter

    chart = ResolvedAreaChart(
        panel_axes=(),
        id="a1",
        chart_type="area",
        x="date",
        y="value",
        style=area_style.model_copy(update={"axis_x": _yearmonth_axis()}),
        **_C,
    )
    spec = AreaEmitter().emit(chart, _DEFAULT_BOX, regroup((), _YEARMONTH_DATA))
    assert spec.encoding["x"]["type"] == "temporal"


def test_area_x_axis_label_expr_injected_for_yearmonth(
    area_style: ResolvedAreaStyle,
) -> None:
    """Area: labelExpr in x axis for yearmonth grain (no axis.values — that's
    the ordinal-only tick-density mechanism)."""
    from dbt_charts.core.render.chart.emitters.area import AreaEmitter

    chart = ResolvedAreaChart(
        panel_axes=(),
        id="a1",
        chart_type="area",
        x="date",
        y="value",
        style=area_style.model_copy(update={"axis_x": _yearmonth_axis()}),
        **_C,
    )
    spec = AreaEmitter().emit(chart, _DEFAULT_BOX, regroup((), _YEARMONTH_DATA))
    ax_vl = spec.encoding["x"]["axis"]
    assert "labelExpr" in ax_vl, "x axis should have labelExpr"


def test_area_yearweek_axis_uses_day_number_labels(
    area_style: ResolvedAreaStyle,
) -> None:
    from dbt_charts.core.render.chart.emitters.area import AreaEmitter

    chart = ResolvedAreaChart(
        panel_axes=(),
        id="a1",
        chart_type="area",
        x="date",
        y="value",
        style=area_style.model_copy(
            update={"axis_x": _yearweek_axis(area_style.axis_x)}
        ),
        **_C,
    )
    spec = AreaEmitter().emit(chart, _DEFAULT_BOX, regroup((), _YEARWEEK_DATA))
    axis = spec.encoding["x"]["axis"]

    assert "%-d" in axis["labelExpr"]
    assert "W%V" not in axis["labelExpr"]
    assert "utcOffset('day', toDate(datum.value), 1)" in axis["labelExpr"]
    assert "values" not in axis


def test_area_y_scale_zero_true_when_axis_scale_zero(
    area_style: ResolvedAreaStyle,
) -> None:
    """Area: y_zero_scale wired — zero=True axis → zero+domainMin in y encoding."""
    import dataclasses

    from dbt_charts.core.render.chart.emitters.area import AreaEmitter

    ax, _ = _make_resolved_axes("area", "temporal", "quantitative")
    chart = ResolvedAreaChart(
        panel_axes=(),
        id="a1",
        chart_type="area",
        x="date",
        y="value",
        style=area_style.model_copy(
            update={
                "axis_x": dataclasses.replace(ax, time_unit="yearmonth"),
                "axis_y": _zero_ay(),
            }
        ),
        **_C,
    )
    spec = AreaEmitter().emit(chart, _DEFAULT_BOX, regroup((), _YEARMONTH_DATA))
    scale = spec.encoding["y"]["scale"]
    assert scale["zero"] is True
    # No tick ladder on this axis, so no rung supplies a floor — see the line
    # twin above.
    assert "domainMin" not in scale


# ---- Bar (vertical) -------------------------------------------------------


def _bar_yearmonth(bar_style: ResolvedBarStyle) -> ResolvedBarChart:
    """Bar chart with yearmonth x axis and axis scale.zero=True for wiring tests."""
    import dataclasses

    ax, _ = _make_resolved_axes("bar", "ordinal", "quantitative")
    ax_ym = dataclasses.replace(ax, time_unit="yearmonth")
    return ResolvedBarChart(
        panel_axes=(),
        id="b1",
        chart_type="bar",
        x="date",
        y="value",
        style=bar_style.model_copy(update={"axis_x": ax_ym, "axis_y": _zero_ay()}),
        **_C,
    )


def test_bar_vertical_x_type_ordinal_for_yearmonth_axis(
    bar_style: ResolvedBarStyle,
) -> None:
    """Bar vertical: build_cartesian_x_encoding wired — x type = ordinal for yearmonth."""
    from dbt_charts.core.render.chart.emitters.bar import BarEmitter

    chart = _bar_yearmonth(bar_style)
    spec = BarEmitter().emit(chart, _DEFAULT_BOX, regroup((), _YEARMONTH_DATA))
    assert spec.encoding["x"]["type"] == "ordinal"


def test_bar_vertical_x_axis_values_injected(bar_style: ResolvedBarStyle) -> None:
    """Bar vertical: values + labelExpr injected in x axis for yearmonth."""
    from dbt_charts.core.render.chart.emitters.bar import BarEmitter

    chart = _bar_yearmonth(bar_style)
    spec = BarEmitter().emit(chart, _DEFAULT_BOX, regroup((), _YEARMONTH_DATA))
    ax_vl = spec.encoding["x"]["axis"]
    assert "values" in ax_vl
    assert "labelExpr" in ax_vl


def test_bar_vertical_y_scale_zero_true_when_axis_scale_zero(
    bar_style: ResolvedBarStyle,
) -> None:
    """Bar vertical: y_zero_scale wired — zero=True axis → zero+domainMin in y scale."""
    from dbt_charts.core.render.chart.emitters.bar import BarEmitter

    chart = _bar_yearmonth(bar_style)
    spec = BarEmitter().emit(chart, _DEFAULT_BOX, regroup((), _YEARMONTH_DATA))
    scale = spec.encoding["y"]["scale"]
    assert scale["zero"] is True
    # No tick ladder on this axis, so no rung supplies a floor — `nice: False`
    # holds the top edge at the exact data max in its place. See the line twin
    # above and TestYZeroScale.test_scale_zero_true_no_ticks_pins_no_domain_min.
    assert "domainMin" not in scale
    assert scale["nice"] is False


def test_bar_vertical_y_ticks_injected_in_axis(bar_style: ResolvedBarStyle) -> None:
    """Bar vertical: nice_tick_values computed for non-stacked bars too."""
    import dataclasses

    from dbt_charts.core.render.chart.emitters.bar import BarEmitter

    ax, ay = _make_resolved_axes("bar", "ordinal", "quantitative")
    # D-03: tick_values are baked by resolve(); set them here to simulate resolve().
    ay_ticks = dataclasses.replace(
        ay,
        ticks=dataclasses.replace(ay.ticks, count=5),
        tick_values=(0.0, 200.0, 400.0, 600.0, 800.0),
    )
    chart = ResolvedBarChart(
        panel_axes=(),
        id="b1",
        chart_type="bar",
        x="date",
        y="value",
        style=bar_style.model_copy(update={"axis_x": ax, "axis_y": ay_ticks}),
        **_C,
    )
    spec = BarEmitter().emit(chart, _DEFAULT_BOX, regroup((), _YEARMONTH_DATA))
    # tick_values baked at resolve() are injected into axis.values by the emitter
    assert "values" in spec.encoding["y"]["axis"]


# ---------------------------------------------------------------------------
# RenderBox: box.width drives tilt, not chart.resolved_width
# ---------------------------------------------------------------------------


def test_bar_emit_box_width_drives_axis_tilt(bar_style: ResolvedBarStyle) -> None:
    """Narrow box → tilted x-axis labels; wide box → no tilt.

    chart.resolved_width is held constant (wide) so only box.width can explain
    the difference once the implementation threads RenderBox through.
    """
    from dbt_charts.core.render.chart.emitters.bar import BarEmitter

    # Build a bar chart with many distinct x values so narrow width forces tilt.
    months = [f"Month {i:02d}" for i in range(1, 25)]
    data = [{"month": m, "value": i * 10} for i, m in enumerate(months)]

    chart = _bar(bar_style)  # resolved_width=600 inside _C

    narrow_box = RenderBox(width=80.0, height=300.0)
    wide_box = RenderBox(width=2000.0, height=300.0)

    narrow_spec = BarEmitter().emit(chart, narrow_box, regroup((), data))
    wide_spec = BarEmitter().emit(chart, wide_box, regroup((), data))

    def _x_axis(spec: ChartSpec) -> dict[str, Any]:
        enc = spec.encoding
        x = enc.get("x") or {}
        return x.get("axis") or {}

    narrow_ax = _x_axis(narrow_spec)
    wide_ax = _x_axis(wide_spec)

    # Wide box: labels fit horizontally → no tilt (angle 0 or absent).
    wide_angle = wide_ax.get("labelAngle", 0)
    assert wide_angle == 0, (
        f"Wide box should produce no tilt, got labelAngle={wide_angle!r}"
    )

    # Narrow box: labels don't fit → non-zero tilt.
    narrow_angle = narrow_ax.get("labelAngle", 0)
    assert narrow_angle != 0, (
        f"Narrow box should produce tilt, got labelAngle={narrow_angle!r}"
    )


def test_resolved_chart_is_fully_frozen(bar_style: ResolvedBarStyle) -> None:
    """Assigning any field on a ResolvedChart must raise ValidationError.

    Previously __setattr__ allowed ``chart.height = h``; after removing that
    allowlist, ResolvedChart must be fully frozen.
    """
    import pytest
    from pydantic import ValidationError

    chart = _bar(bar_style)
    with pytest.raises((ValidationError, TypeError)):
        chart.height = 999.0  # type: ignore[misc]


# ---------------------------------------------------------------------------
# Exhaustive binding-capability guard
# ---------------------------------------------------------------------------


def _find_color_scale(spec: ChartSpec) -> dict[str, Any] | None:
    """First explicit ``encoding.color.scale`` in ``spec`` or any nested layer.

    Chart-family-agnostic: the exhaustive test below drives eight different
    emitters, each nesting its color-bearing mark at a different depth (a
    plain bar's color sits on the outer spec; a pie's sits on its arc
    sub-layer; a geoshape's sits on its choropleth sub-layer) — this walks
    every layer rather than hard-coding one family's shape.
    """
    color = (spec.encoding or {}).get("color")
    if isinstance(color, dict) and isinstance(color.get("scale"), dict):
        return color["scale"]
    for layer in spec.layers:
        found = _find_color_scale(layer)
        if found is not None:
            return found
    return None


def test_binding_capability_matches_every_authored_chart_family(
    bar_style: ResolvedBarStyle,
    line_style: ResolvedLineStyle,
    area_style: ResolvedAreaStyle,
    scatter_style: ResolvedScatterStyle,
    heatmap_style: ResolvedHeatmapStyle,
    pie_style: ResolvedPieStyle,
    geoshape_style: ResolvedGeoshapeStyle,
    point_map_style: ResolvedPointMapStyle,
) -> None:
    """Exhaustive drift guard for board-wide category-color binding.

    This is the test that ends the whack-a-mole played
    with ``categorical_channel_fields``'s capability list: every
    ``AuthoredChart`` family (enumerated live from ``AUTHORED_CHART_TYPE_TAGS``,
    never hand-copied) must make an explicit binding-capability choice in
    ``_BINDING_CAPABLE_CHART_TYPES`` (``compile/resolve/style/category_colors.py``),
    and a declared-capable one must actually paint an explicit,
    board-consistent ``scale`` from its real emitter — not merely widen the
    domain and leave it unread.

    A new chart family (or a chart-type tag added to an existing class, the
    way ``histogram`` shares ``BarChart``) that is not added to this test's
    own ``_EXPECTED_CAPABLE``/``_NORMALIZED`` tables fails immediately with a
    ``KeyError`` or a set-mismatch assertion — there is no path through this
    test that silently ignores an unenumerated tag.
    """
    from dbt_charts.core.compile.models.chart.authored import AUTHORED_CHART_TYPE_TAGS
    from dbt_charts.core.compile.models.chart.normalized.area import (
        AreaChart as NormAreaChart,
    )
    from dbt_charts.core.compile.models.chart.normalized.bar import (
        BarChart as NormBarChart,
    )
    from dbt_charts.core.compile.models.chart.normalized.callout import (
        CalloutChart as NormCalloutChart,
    )
    from dbt_charts.core.compile.models.chart.normalized.geoshape import (
        GeoshapeChart as NormGeoshapeChart,
    )
    from dbt_charts.core.compile.models.chart.normalized.heatmap import (
        HeatmapChart as NormHeatmapChart,
    )
    from dbt_charts.core.compile.models.chart.normalized.kpi import (
        KpiChart as NormKpiChart,
    )
    from dbt_charts.core.compile.models.chart.normalized.line import (
        LineChart as NormLineChart,
    )
    from dbt_charts.core.compile.models.chart.normalized.pie import (
        PieChart as NormPieChart,
    )
    from dbt_charts.core.compile.models.chart.normalized.point_map import (
        PointMapChart as NormPointMapChart,
    )
    from dbt_charts.core.compile.models.chart.normalized.scatter import (
        ScatterChart as NormScatterChart,
    )
    from dbt_charts.core.compile.models.chart.normalized.spark_bar import (
        SparkBarChart as NormSparkBarChart,
    )
    from dbt_charts.core.compile.models.chart.normalized.table import (
        TableChart as NormTableChart,
    )
    from dbt_charts.core.compile.models.chart.resolved import ResolvedStyleChannel
    from dbt_charts.core.compile.models.chart.resolved.geoshape import (
        ResolvedGeoshapeChart,
    )
    from dbt_charts.core.compile.models.style.theme.category_colors import (
        CategoryColorScale,
    )
    from dbt_charts.core.compile.resolve.style.category_colors import (
        categorical_channel_fields,
    )
    from dbt_charts.core.render.chart.emitters import get_emitter

    # tag -> minimal normalized instance, with color="cat" set whenever the
    # family declares the channel at all (whether or not it's capable).
    normalized_by_tag: dict[str, Any] = {
        "bar": NormBarChart(id="t", type="bar", x="x", color="cat"),
        "histogram": NormBarChart(id="t", type="histogram", x="x", color="cat"),
        "line": NormLineChart(id="t", type="line", x="x", y="y", color="cat"),
        "area": NormAreaChart(id="t", type="area", x="x", y="y", color="cat"),
        "scatter": NormScatterChart(id="t", type="scatter", x="x", y="y", color="cat"),
        "heatmap": NormHeatmapChart(id="t", type="heatmap", x="x", y="y", color="cat"),
        "pie": NormPieChart(id="t", type="pie", theta="v", color="cat"),
        "donut": NormPieChart(id="t", type="donut", theta="v", color="cat"),
        "kpi": NormKpiChart(id="t", type="kpi", value="v"),
        "table": NormTableChart(id="t", type="table"),
        "point_map": NormPointMapChart(id="t", type="point_map", color="cat"),
        "bubble_map": NormPointMapChart(id="t", type="bubble_map", color="cat"),
        "map": NormGeoshapeChart(id="t", type="map", color="cat"),
        "geoshape": NormGeoshapeChart(id="t", type="geoshape", color="cat"),
        "callout": NormCalloutChart(id="t", type="callout", message="hi"),
        "spark_bar": NormSparkBarChart(
            id="t", type="spark_bar", x="x", y="y", color="cat"
        ),
    }
    assert set(normalized_by_tag) == set(AUTHORED_CHART_TYPE_TAGS), (
        "every AuthoredChart family tag needs a fixture in this table -- a "
        "tag present in one set but not the other means this test itself is "
        "out of sync with the schema, which is exactly the drift this test "
        "exists to catch"
    )

    expected_capable: dict[str, bool] = {
        "bar": True,
        "histogram": False,
        "line": True,
        "area": True,
        "scatter": True,
        "heatmap": True,
        "pie": True,
        "donut": True,
        "kpi": False,
        "table": False,
        "point_map": True,
        "bubble_map": True,
        "map": True,
        "geoshape": True,
        "callout": False,
        "spark_bar": False,
    }
    assert set(expected_capable) == set(AUTHORED_CHART_TYPE_TAGS)

    for tag in AUTHORED_CHART_TYPE_TAGS:
        drawn = categorical_channel_fields(normalized_by_tag[tag])
        if expected_capable[tag]:
            assert drawn == ("cat",), (
                f"{tag!r} is declared binding-capable but "
                f"categorical_channel_fields returned {drawn!r}"
            )
        else:
            assert drawn == (), (
                f"{tag!r} is declared NOT binding-capable but "
                f"categorical_channel_fields returned {drawn!r} -- an "
                "unconverted family must never widen the board's domain"
            )

    # Behavioral half: every declared-capable tag's underlying RESOLVED
    # family (donut shares pie's; map/bubble_map share geoshape's/point_map's)
    # must have its real emitter actually paint a board-consistent scale --
    # not just a compile-time declaration nothing reads.
    scale = CategoryColorScale(field="cat", slots={"A": 0, "B": 1}, overrides={})
    palette = ("#111111", "#222222")
    channel = {
        "color": ResolvedStyleChannel(channel="color", mode="series", data_field="cat")
    }

    def _proof(spec: ChartSpec) -> None:
        found = _find_color_scale(spec)
        assert found is not None, f"no color scale in emitted spec: {spec}"
        color_of = dict(zip(found["domain"], found["range"], strict=True))
        assert {"A", "B"} <= set(color_of), found
        assert color_of["A"] == palette[0]
        assert color_of["B"] == palette[1]

    bar_chart = _bar(bar_style).model_copy(
        update={
            "color": "cat",
            "stack": "none",
            "resolved_channels": channel,
            "category_colors": (scale,),
            "palette": palette,
        }
    )
    bar_data = [
        {"month": "Jan", "revenue": 10, "cat": "A"},
        {"month": "Feb", "revenue": 20, "cat": "B"},
    ]
    _proof(get_emitter(bar_chart).emit(bar_chart, _DEFAULT_BOX, regroup((), bar_data)))

    line_chart = _line(line_style).model_copy(
        update={
            "color": "cat",
            "resolved_channels": channel,
            "category_colors": (scale,),
            "palette": palette,
        }
    )
    line_data = [
        {"date": "2024-01-01", "value": 10, "cat": "A"},
        {"date": "2024-01-02", "value": 20, "cat": "B"},
    ]
    _proof(
        get_emitter(line_chart).emit(line_chart, _DEFAULT_BOX, regroup((), line_data))
    )

    area_chart = _area(area_style).model_copy(
        update={
            "color": "cat",
            "resolved_channels": channel,
            "category_colors": (scale,),
            "palette": palette,
        }
    )
    area_data = [
        {"date": "2024-01-01", "value": 10, "cat": "A"},
        {"date": "2024-01-02", "value": 20, "cat": "B"},
    ]
    _proof(
        get_emitter(area_chart).emit(area_chart, _DEFAULT_BOX, regroup((), area_data))
    )

    scatter_chart = _scatter(scatter_style).model_copy(
        update={
            "color": "cat",
            "resolved_channels": channel,
            "category_colors": (scale,),
            "palette": palette,
        }
    )
    scatter_data = [
        {"x_val": 1, "y_val": 10, "cat": "A"},
        {"x_val": 2, "y_val": 20, "cat": "B"},
    ]
    _proof(
        get_emitter(scatter_chart).emit(
            scatter_chart, _DEFAULT_BOX, regroup((), scatter_data)
        )
    )

    heatmap_chart = _heatmap(heatmap_style).model_copy(
        update={
            "color": "cat",
            "resolved_channels": channel,
            "category_colors": (scale,),
            "palette": palette,
        }
    )
    heatmap_data = [
        {"col": "c1", "row": "r1", "cat": "A"},
        {"col": "c2", "row": "r2", "cat": "B"},
    ]
    _proof(
        get_emitter(heatmap_chart).emit(
            heatmap_chart, _DEFAULT_BOX, regroup((), heatmap_data)
        )
    )

    pie_data = [{"sales": 10, "cat": "A"}, {"sales": 20, "cat": "B"}]
    pie_chart = _pie(pie_style).model_copy(
        update={
            "color": "cat",
            "resolved_channels": channel,
            "category_colors": (scale,),
            "palette": palette,
            "presentation_fingerprint": pie_presentation_fingerprint(pie_data),
            "dark_companion_stops": ("#000000", "#111111"),
        }
    )
    _proof(get_emitter(pie_chart).emit(pie_chart, _DEFAULT_BOX, regroup((), pie_data)))

    geo_chart = ResolvedGeoshapeChart(
        id="geo1",
        chart_type="geoshape",
        geo_url="https://example.com/us-10m.json",
        geo_format_type="topojson",
        geo_feature="states",
        geo_join_key="id",
        geo_projection_type="albersUsa",
        lookup_field="region",
        value_field="cat",
        category_colors=(scale,),
        style=geoshape_style,
        **{**_B, "palette": palette, "resolved_channels": channel},
    )
    geo_data = [{"region": "r1", "cat": "A"}, {"region": "r2", "cat": "B"}]
    _proof(get_emitter(geo_chart).emit(geo_chart, _DEFAULT_BOX, regroup((), geo_data)))

    point_map_chart = _point_map(point_map_style).model_copy(
        update={
            "latitude": "lat",
            "longitude": "lon",
            "resolved_channels": channel,
            "category_colors": (scale,),
            "palette": palette,
        }
    )
    # Real CONUS coordinates -- albersUsa (this fixture's default projection)
    # drops out-of-region rows before the color scale is even built.
    point_map_data = [
        {"lat": 40.7, "lon": -74.0, "cat": "A"},
        {"lat": 34.0, "lon": -118.2, "cat": "B"},
    ]
    _proof(
        get_emitter(point_map_chart).emit(
            point_map_chart, _DEFAULT_BOX, regroup((), point_map_data)
        )
    )
