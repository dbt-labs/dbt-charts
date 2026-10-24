"""Tests for render-v2 family emitters and the get_emitter() registry."""

from __future__ import annotations

from typing import Any

import pytest

from dbt_charts.core.compile.models.chart.resolved import (
    ResolvedBarChart,
    ResolvedBarStyle,
    ResolvedGeoshapeChart,
    ResolvedGeoshapeStyle,
    ResolvedPointMapChart,
    ResolvedPointMapStyle,
)
from dbt_charts.core.compile.models.primitives import ResolvedFormat
from dbt_charts.core.compile.models.style.theme import PaddingStyle
from dbt_charts.core.compile.resolve.chart._chart_rows import regroup
from dbt_charts.core.compile.resolve.style.typography import concrete_title_style
from dbt_charts.core.render.chart.spec import ChartSpec, RenderBox

from ...conftest import baked_format


# Required base fields (no defaults on non-None resolved model fields).
def _default_legend():
    from dbt_charts.core.compile.config import get_default_theme_name, get_theme_style
    from dbt_charts.core.compile.resolve.style.board import resolve_style

    return resolve_style(
        get_theme_style(get_default_theme_name())
    ).chart_defaults.legend


def _default_charts():
    from dbt_charts.core.compile.config import get_default_theme_name, get_theme_style
    from dbt_charts.core.compile.resolve.style.board import resolve_chart_style_context

    return resolve_chart_style_context(get_theme_style(get_default_theme_name()))


_DEFAULT_CHARTS = _default_charts()
_ZERO_PADDING = PaddingStyle(left=0.0, right=0.0, top=0.0, bottom=0.0)
_B: dict[str, Any] = {
    "variable_dependencies": frozenset(),
    "palette": (),
    "resolved_channels": {},
    "legend": _default_legend(),
    "background": _DEFAULT_CHARTS.background,
    "canvas": _DEFAULT_CHARTS.ink_canvas,
    "title_style": concrete_title_style(_DEFAULT_CHARTS),
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
        field_labels={},
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
    baked = _bake_cartesian_axes(
        chart_style_context,
        fixture_chart_for_type(chart_type),
        chart_type,
        x_type,
        y_type,
        AxisOverrides(),
    )
    ax_merged, ay_merged, ax_band_position, ay_band_position = (
        baked.x.style,
        baked.y.style,
        baked.x.band_position,
        baked.y.band_position,
    )

    return (
        build_resolved_axis(
            ax_merged,
            format=baked_format(ax_merged),
            band_position=ax_band_position,
            chart_id="test",
            formats=None,
        ),
        build_resolved_axis(
            ay_merged,
            format=baked_format(ay_merged),
            band_position=ay_band_position,
            chart_id="test",
            formats=None,
        ),
    )


def _geoshape(geoshape_style: ResolvedGeoshapeStyle) -> ResolvedGeoshapeChart:
    return ResolvedGeoshapeChart(
        id="geo1",
        chart_type="geoshape",
        style=geoshape_style,
        **_B,
        field_labels={},
    )


def _point_map(point_map_style: ResolvedPointMapStyle) -> ResolvedPointMapChart:
    return ResolvedPointMapChart(
        id="pm1",
        chart_type="point_map",
        projection="albersUsa",
        collapse=False,
        style=point_map_style,
        **_B,
        field_labels={},
    )


# ---------------------------------------------------------------------------
# emit() returns a ChartSpec
# ---------------------------------------------------------------------------


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
        field_labels={},
    )
    spec = get_emitter(chart).emit(chart, _DEFAULT_BOX, regroup((), []))
    assert spec.encoding["size"]["scale"]["domainMin"] == 0


# ---------------------------------------------------------------------------
# Structural encoding assertions — key presence only, not values
# ---------------------------------------------------------------------------


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
        field_labels={},
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
        field_labels={},
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
        field_labels={},
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
        field_labels={},
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
    from dbt_charts.core.compile.models.chart.normalized.geoshape import GeoshapeChart
    from dbt_charts.core.compile.models.vega_lite.contracts import Projection
    from dbt_charts.core.compile.resolve.chart.geo import _resolve_geo_projection

    chart = GeoshapeChart(
        id="g",
        type="geoshape",
        geo_source="us-states",
        projection=Projection(type="mercator"),
    )
    proj_type, proj_params = _resolve_geo_projection(chart, "us-states", None)
    assert proj_type == "mercator", f"expected 'mercator', got {proj_type!r}"
    assert proj_params is None


# ---------------------------------------------------------------------------
# HIGH regression: dual color source — pie/geo must use resolved_channels only
# ---------------------------------------------------------------------------


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
        field_labels={},
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
        canvas=_DEFAULT_CHARTS.ink_canvas,
        title_style=concrete_title_style(_DEFAULT_CHARTS),
        layout_padding=_ZERO_PADDING,
        field_labels={},
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

    axis, _ = _make_resolved_axes()
    ch = ResolvedStyleChannel(channel="x", mode="series", data_field="month")
    result = channel_to_encoding(ch, [{"month": "Jan"}], axis_style=axis)
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
    assert isinstance(resolved.style.tooltip_format, ResolvedFormat)


# ---------------------------------------------------------------------------
# TDD: stack="none" sentinel → grouped (xOffset) output, no VL "none" emitted
# ---------------------------------------------------------------------------


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


# ---------------------------------------------------------------------------
# Bar x-axis type: datetime.date values → ordinal, not temporal
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# Chart title emitted in VL spec
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# the engine's x-axis builder wiring — line, area, bar, layered
# ---------------------------------------------------------------------------


# ---- Line ----------------------------------------------------------------


# ---- Area ----------------------------------------------------------------


# ---- Bar (vertical) -------------------------------------------------------


# ---------------------------------------------------------------------------
# RenderBox: box.width drives tilt, not chart.resolved_width
# ---------------------------------------------------------------------------


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
