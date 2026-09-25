"""Tests for the typed per-layer union (BarLayer/LineLayer/AreaLayer/ScatterLayer)
on cartesian chart families (BarChart, LineChart, AreaChart, ScatterChart).

Covers:
- Layer union discriminates on `type`
- Each layer's `style.marks.*` accepts its matching *MarkStylePatch and rejects foreign fields
- `layers` is accepted on bar/line/area/scatter and rejected on pie/kpi/heatmap
- A layer `type` outside the four (e.g. `circle`) is rejected
- `encoding` and literal `#color` on a layer are rejected
- A line layer can set BOTH marks.line.stroke.width AND marks.point.size independently
- A style-less layer resolves to board base marks
- Parity across bar/area/scatter for their mark sets
"""

from __future__ import annotations

import pytest
from pydantic import TypeAdapter, ValidationError

from dbt_charts.core.compile.resolve.chart._chart_rows import regroup
from dbt_charts.core.render.chart.spec import RenderBox

_DEFAULT_BOX = RenderBox(width=600.0, height=300.0)

from dbt_charts.core.compile.models.chart.authored import (
    AreaChart,
    BarChart,
    HeatmapChart,
    KpiChart,
    LineChart,
    PieChart,
    ScatterChart,
)
from dbt_charts.core.compile.models.chart.authored._layer import (
    AreaLayer,
    BarChartBarLayer,
    BarLayer,
    CartesianLayer,
    LineLayer,
    ScatterLayer,
)

_layer_ta: TypeAdapter[CartesianLayer] = TypeAdapter(CartesianLayer)


# ── discriminated union ────────────────────────────────────────────────────


def test_bar_layer_discriminated() -> None:
    layer = _layer_ta.validate_python({"type": "bar", "y": "revenue"})
    assert isinstance(layer, BarLayer)
    assert layer.y == "revenue"


def test_line_layer_discriminated() -> None:
    layer = _layer_ta.validate_python({"type": "line", "y": "target"})
    assert isinstance(layer, LineLayer)


def test_area_layer_discriminated() -> None:
    layer = _layer_ta.validate_python({"type": "area", "y": "costs"})
    assert isinstance(layer, AreaLayer)


def test_scatter_layer_discriminated() -> None:
    layer = _layer_ta.validate_python({"type": "scatter", "x": "a", "y": "b"})
    assert isinstance(layer, ScatterLayer)


def test_unknown_layer_type_rejected() -> None:
    """circle is not in the four-type union — must raise."""
    with pytest.raises(ValidationError):
        _layer_ta.validate_python({"type": "circle", "y": "v"})


# ── per-layer style: matching patch accepted, foreign field rejected ───────


def test_line_layer_style_accepts_own_patch() -> None:
    layer = _layer_ta.validate_python(
        {
            "type": "line",
            "y": "v",
            "style": {"marks": {"line": {"stroke": {"width": 2}}}},
        }
    )
    assert isinstance(layer, LineLayer)
    assert layer.style is not None


def test_line_layer_style_rejects_foreign_field() -> None:
    """padding belongs in bar marks — LineLayerStyle must forbid unknown top-level fields."""
    with pytest.raises(ValidationError, match="extra_forbidden"):
        _layer_ta.validate_python({"type": "line", "y": "v", "style": {"padding": 0.5}})


def test_bar_layer_style_accepts_own_patch() -> None:
    layer = _layer_ta.validate_python(
        {"type": "bar", "y": "revenue", "style": {"marks": {"bar": {"padding": 0.2}}}}
    )
    assert isinstance(layer, BarLayer)
    assert layer.style is not None


def test_bar_layer_style_rejects_foreign_field() -> None:
    """stroke belongs in line marks — BarLayerStyle must forbid unknown top-level fields."""
    with pytest.raises(ValidationError, match="extra_forbidden"):
        _layer_ta.validate_python(
            {"type": "bar", "y": "revenue", "style": {"stroke": {"width": 2}}}
        )


def test_area_layer_style_accepts_own_patch() -> None:
    layer = _layer_ta.validate_python(
        {"type": "area", "y": "costs", "style": {"marks": {"area": {"opacity": 0.5}}}}
    )
    assert isinstance(layer, AreaLayer)


def test_scatter_layer_style_accepts_own_patch() -> None:
    layer = _layer_ta.validate_python(
        {"type": "scatter", "y": "v", "style": {"marks": {"point": {"opacity": 0.8}}}}
    )
    assert isinstance(layer, ScatterLayer)


# ── encoding and literal color rejected ───────────────────────────────────


def test_layer_encoding_rejected() -> None:
    with pytest.raises(ValidationError, match="encoding"):
        _layer_ta.validate_python(
            {"type": "line", "y": "v", "encoding": {"x": {"field": "month"}}}
        )


def test_layer_literal_color_rejected() -> None:
    with pytest.raises(ValidationError, match="literal color|bare field name"):
        _layer_ta.validate_python({"type": "bar", "y": "v", "color": "#ff0000"})


# ── layers field on cartesian families ────────────────────────────────────


def test_bar_chart_accepts_layers() -> None:
    chart = BarChart.model_validate(
        {
            "type": "bar",
            "x": "month",
            "y": "revenue",
            "layers": [{"type": "line", "y": "target"}],
        }
    )
    assert chart.layers is not None
    assert len(chart.layers) == 1
    assert isinstance(chart.layers[0], LineLayer)


def test_line_chart_accepts_layers() -> None:
    chart = LineChart.model_validate(
        {
            "type": "line",
            "x": "month",
            "y": "v1",
            "layers": [{"type": "area", "y": "v2"}],
        }
    )
    assert chart.layers is not None


def test_area_chart_accepts_layers() -> None:
    chart = AreaChart.model_validate(
        {
            "type": "area",
            "x": "month",
            "y": "v1",
            "layers": [{"type": "bar", "y": "v2"}],
        }
    )
    assert chart.layers is not None


def test_scatter_chart_accepts_layers() -> None:
    chart = ScatterChart.model_validate(
        {
            "type": "scatter",
            "x": "a",
            "y": "b",
            "layers": [{"type": "line", "y": "b"}],
        }
    )
    assert chart.layers is not None


def test_pie_chart_rejects_layers() -> None:
    with pytest.raises(ValidationError, match="extra_forbidden|layers"):
        PieChart.model_validate(
            {
                "type": "pie",
                "theta": "v",
                "layers": [{"type": "line", "y": "v"}],
            }
        )


def test_kpi_chart_rejects_layers() -> None:
    with pytest.raises(ValidationError, match="extra_forbidden|layers"):
        KpiChart.model_validate(
            {
                "type": "kpi",
                "metric": "v",
                "layers": [{"type": "line", "y": "v"}],
            }
        )


def test_heatmap_chart_rejects_layers() -> None:
    with pytest.raises(ValidationError, match="extra_forbidden|layers"):
        HeatmapChart.model_validate(
            {
                "type": "heatmap",
                "x": "a",
                "y": "b",
                "color": "v",
                "layers": [{"type": "line", "y": "v"}],
            }
        )


# ── resolved layer tests ────────────────────────────────────────────────────
#
# These tests cover MS2: normalized + resolved layers + style cascade.
# Pattern: distinctive-value-plus-propagation (AGENTS.md § Test patterns).


def _default_board_style():
    from dbt_charts.core.compile.config import get_default_theme_name, get_theme_style
    from dbt_charts.core.compile.resolve.style.board import resolve_chart_style_context

    return resolve_chart_style_context(get_theme_style(get_default_theme_name()))


def _default_board_style_no_endpoint_labels():
    """Bar endpoint_labels off — a stacked bar with a series color channel
    otherwise suppresses the legend in favor of direct labeling, but this
    low-level emit()+translate_to_vl() path (unlike the full render pipeline)
    never draws the endpoint-label pane that would replace it, leaving
    neither. Legend-scale/paint tests need the legend back."""
    from dbt_charts.core.compile.config import get_default_theme_name, get_theme_style
    from dbt_charts.core.compile.resolve.style.board import resolve_chart_style_context

    theme = get_theme_style(get_default_theme_name())
    return resolve_chart_style_context(
        theme.model_copy(
            update={
                "charts": theme.charts.model_copy(
                    update={
                        "bar": theme.charts.bar.model_copy(
                            update={
                                "endpoint_labels": theme.charts.bar.endpoint_labels.model_copy(
                                    update={"visible": False}
                                )
                            }
                        )
                    }
                )
            }
        )
    )


def _sql(sql: str = "SELECT 1"):
    from dbt_charts.core.compile.models.query.normalized import SqlQuery

    return SqlQuery(sql=sql, source="t")


def _bar_normalized(**kwargs):  # type: ignore[no-untyped-def]
    from dbt_charts.core.compile.models.chart.normalized import BarChart as NBarChart

    defaults = {
        "id": "bar1",
        "type": "bar",
        "x": "month",
        "y": "revenue",
        "query": _sql(),
        "query_name": "q",
        "variable_dependencies": set(),
    }
    defaults.update(kwargs)
    # Layers validated against the generic union re-validate as a bar chart's.
    if "layers" in kwargs:
        defaults["layers"] = [
            layer.model_dump(exclude_unset=True) for layer in kwargs["layers"]
        ]
    return NBarChart(**defaults)


_BAR_DATA: list[dict] = [
    {"month": "Jan", "revenue": 100.0, "target": 90.0},
    {"month": "Feb", "revenue": 200.0, "target": 180.0},
]

_COLOR_LAYER_DATA: list[dict] = [
    {"month": "Jan", "revenue": 100.0, "target": 90.0, "kind": "Named"},
    {"month": "Jan", "revenue": 50.0, "target": 90.0, "kind": "Non-Named"},
    {"month": "Feb", "revenue": 200.0, "target": 180.0, "kind": "Named"},
    {"month": "Feb", "revenue": 80.0, "target": 180.0, "kind": "Non-Named"},
]

_OVERLAY_COLOR_DATA: list[dict] = [
    {
        "month": "Jan",
        "revenue": 100.0,
        "target": 90.0,
        "kind": "Named",
        "region": "Named",
    },
    {
        "month": "Feb",
        "revenue": 200.0,
        "target": 180.0,
        "kind": "Non-Named",
        "region": "West",
    },
]


def test_bar_chart_with_line_layer_resolves() -> None:
    """A bar chart with a typed line layer resolves to ResolvedBarChart with one layer."""
    from dbt_charts.core.compile.models.chart.resolved import ResolvedBarChart
    from dbt_charts.core.compile.resolve import resolve

    line_layer = LineLayer(type="line", y="target")
    chart = _bar_normalized(layers=[line_layer])
    resolved = resolve(chart, _BAR_DATA, _default_board_style())
    assert isinstance(resolved, ResolvedBarChart)
    assert len(resolved.layers) == 1


def test_line_layer_style_stroke_width_propagates() -> None:
    """marks.line.stroke.width=99 on a line layer survives to ResolvedLineLayer.line_mark."""
    from dbt_charts.core.compile.models.chart.resolved._layer import ResolvedLineLayer
    from dbt_charts.core.compile.resolve import resolve

    line_layer = LineLayer(
        type="line",
        y="target",
        style={"marks": {"line": {"stroke": {"width": 99.0}}}},
    )
    chart = _bar_normalized(layers=[line_layer])
    resolved = resolve(chart, _BAR_DATA, _default_board_style())
    assert len(resolved.layers) == 1
    layer = resolved.layers[0]
    assert isinstance(layer, ResolvedLineLayer)
    assert layer.line_mark.stroke.width == 99.0


def test_line_layer_style_color_token_resolves() -> None:
    """A palette token in a per-layer style (marks.line.stroke.color) resolves to
    a hex value — it must not leak through as a literal token string, matching
    chart-level style token resolution (test_palette_tokens_in_chart_style.py)."""
    from dbt_charts.core.compile.models.chart.resolved._layer import ResolvedLineLayer
    from dbt_charts.core.compile.resolve import resolve

    line_layer = LineLayer(
        type="line",
        y="target",
        style={"marks": {"line": {"stroke": {"color": "dbt-creams.subtitle"}}}},
    )
    chart = _bar_normalized(layers=[line_layer])
    resolved = resolve(chart, _BAR_DATA, _default_board_style())
    layer = resolved.layers[0]
    assert isinstance(layer, ResolvedLineLayer)
    assert layer.line_mark.stroke.color == "#918878"


def test_line_layer_style_unknown_color_token_raises() -> None:
    """An unknown palette token in a per-layer style raises at compile time
    instead of silently leaking through as a literal string (validate-and-error-fast).
    """
    from dbt_charts.core.compile.resolve import resolve
    from dbt_charts.core.compile.resolve.style.palette import UnknownColorError

    line_layer = LineLayer(
        type="line",
        y="target",
        style={"marks": {"line": {"stroke": {"color": "dbt-creams.does-not-exist"}}}},
    )
    chart = _bar_normalized(layers=[line_layer])
    with pytest.raises(UnknownColorError):
        resolve(chart, _BAR_DATA, _default_board_style())


def test_sibling_line_layers_independent_styles() -> None:
    """Two line layers: styled (width=99) and unstyled — their resolved line_mark differ."""
    from dbt_charts.core.compile.models.chart.resolved._layer import ResolvedLineLayer
    from dbt_charts.core.compile.resolve import resolve

    styled = LineLayer(
        type="line",
        y="target",
        style={"marks": {"line": {"stroke": {"width": 99.0}}}},
    )
    unstyled = LineLayer(type="line", y="revenue")
    chart = _bar_normalized(layers=[styled, unstyled])
    resolved = resolve(chart, _BAR_DATA, _default_board_style())
    assert len(resolved.layers) == 2
    styled_layer, unstyled_layer = resolved.layers
    assert isinstance(styled_layer, ResolvedLineLayer)
    assert isinstance(unstyled_layer, ResolvedLineLayer)
    assert styled_layer.line_mark.stroke.width == 99.0
    # Unstyled layer inherits theme default — just verify it's not the authored value.
    assert unstyled_layer.line_mark.stroke.width != 99.0


def test_layer_axis_y_survives_to_resolved() -> None:
    """axis_y.position on a layer is preserved on the resolved layer."""
    from dbt_charts.core.compile.models.chart.resolved._layer import ResolvedLineLayer
    from dbt_charts.core.compile.resolve import resolve

    layer = LineLayer(type="line", y="target", axis_y={"position": "right"})
    chart = _bar_normalized(layers=[layer])
    resolved = resolve(chart, _BAR_DATA, _default_board_style())
    assert len(resolved.layers) == 1
    resolved_layer = resolved.layers[0]
    assert isinstance(resolved_layer, ResolvedLineLayer)
    assert resolved_layer.axis_y is not None
    assert resolved_layer.axis_y.position == "right"


def test_emit_line_layer_returns_halo_fg_hover() -> None:
    """emit_line_layer returns [halo-line, fg-line, invisible-hover] for point-less series."""
    from dbt_charts.core.compile.models.style.resolved._marks import (
        ResolvedLineMarkStyle,
        ResolvedStrokeStyle,
    )
    from dbt_charts.core.compile.models.style.theme import (
        PointLabelsStyle,
        PointMarkStyle,
    )
    from dbt_charts.core.render.chart.emitters._layers import (
        HOVER_TARGET_SIZE,
        emit_line_layer,
    )

    line_mark = ResolvedLineMarkStyle(
        stroke=ResolvedStrokeStyle(width=2.0),
        halo_multiplier=2.0,
        curve=None,
        labels=PointLabelsStyle(),
    )
    # PointMarkStyle().size is None → no point overlay
    layers = emit_line_layer(
        line_mark=line_mark,
        point_mark=PointMarkStyle(),
        halo_color="#ffffff",
        single_series_color="#4e79a7",
        has_color_encoding=False,
        series_encoding={},
        tooltip=[],
    )
    # halo line + fg line + invisible hover target
    assert len(layers) == 3
    hover = layers[-1]
    assert hover.mark == "point"
    assert hover.mark_props["opacity"] == 0
    assert hover.mark_props["size"] == HOVER_TARGET_SIZE


# ── MS4: emit_bar_layer / emit_area_layer / emit_scatter_layer ────────────


def test_emit_bar_layer_returns_chart_spec() -> None:
    """emit_bar_layer wraps mark_props + encoding into a ChartSpec."""
    from dbt_charts.core.compile.models.chart.normalized import BarChart as NBarChart
    from dbt_charts.core.compile.resolve import resolve
    from dbt_charts.core.render.chart.emitters._layers import emit_bar_layer
    from dbt_charts.core.render.chart.spec import ChartSpec

    bar_chart = NBarChart(
        id="b1",
        type="bar",
        x="month",
        y="revenue",
        query=_sql(),
        query_name="q",
        variable_dependencies=set(),
    )
    resolved = resolve(bar_chart, _BAR_DATA, _default_board_style())
    style = resolved.style
    encoding: dict = {
        "x": {"field": "month", "type": "nominal"},
        "y": {"field": "revenue", "type": "quantitative"},
    }
    result = emit_bar_layer(
        bar_mark=style.mark,
        orientation="vertical",
        has_color_encoding=False,
        single_series_color=style.single_series_fill,
        radius=None,
        encoding=encoding,
        data=_BAR_DATA,
        measure_field="revenue",
        start_field=None,
        config={},
        transforms=[],
        x_is_banded=True,
        cat_field="month",
    )
    assert isinstance(result, ChartSpec)
    assert result.mark in ("bar", "layered")


def test_emit_area_layer_returns_sub_layers() -> None:
    """emit_area_layer returns at least two sub-layers (fill + hover)."""
    from dbt_charts.core.compile.models.chart.normalized import AreaChart as NAreaChart
    from dbt_charts.core.compile.resolve import resolve
    from dbt_charts.core.render.chart.emitters._layers import emit_area_layer
    from dbt_charts.core.render.chart.spec import ChartSpec

    area_chart = NAreaChart(
        id="a1",
        type="area",
        x="month",
        y="revenue",
        query=_sql(),
        query_name="q",
        variable_dependencies=set(),
    )
    resolved = resolve(area_chart, _BAR_DATA, _default_board_style())
    layers = emit_area_layer(
        area_mark=resolved.style.area_mark,
        line_mark=resolved.style.line_mark,
        point_mark=resolved.style.point_mark,
        background=resolved.background,
        single_series_fill=resolved.style.single_series_fill,
        has_color_encoding=False,
        series_encoding={},
        tooltip=[],
        is_stacked=False,
        band_transforms=[],
    )
    assert isinstance(layers, list)
    assert len(layers) >= 2
    marks = [s.mark for s in layers]
    assert "area" in marks
    hover = layers[-1]
    assert isinstance(hover, ChartSpec)
    assert hover.mark_props.get("opacity") == 0


def test_emit_scatter_layer_returns_mark_props() -> None:
    """emit_scatter_layer returns a mark_props dict with tooltip=True and fill."""
    from dbt_charts.core.compile.models.style.theme import PointMarkStyle
    from dbt_charts.core.render.chart.emitters._layers import emit_scatter_layer

    result = emit_scatter_layer(
        point_mark=PointMarkStyle(),
        has_color_encoding=False,
        single_series_fill="#aabbcc",
    )
    assert isinstance(result, dict)
    assert result.get("tooltip") is True
    assert result.get("fill") == "#aabbcc"


# ── per-layer style: marks.line + marks.point both propagate ───────────────


def test_line_layer_marks_line_and_point_propagate() -> None:
    """A line layer can set marks.line.stroke.width AND marks.point.size independently."""
    from dbt_charts.core.compile.models.chart.resolved._layer import ResolvedLineLayer
    from dbt_charts.core.compile.resolve import resolve

    layer = LineLayer(
        type="line",
        y="target",
        style={"marks": {"line": {"stroke": {"width": 7.0}}, "point": {"size": 42.0}}},
    )
    chart = _bar_normalized(layers=[layer])
    resolved = resolve(chart, _BAR_DATA, _default_board_style())
    r = resolved.layers[0]
    assert isinstance(r, ResolvedLineLayer)
    assert r.line_mark.stroke.width == 7.0
    assert r.point_mark.size == 42.0


def test_styleless_line_layer_resolves_to_base_marks() -> None:
    """A line layer with no style gets resolved marks from the board base."""
    from dbt_charts.core.compile.models.chart.resolved._layer import ResolvedLineLayer
    from dbt_charts.core.compile.resolve import resolve

    layer = LineLayer(type="line", y="target")
    chart = _bar_normalized(layers=[layer])
    board_style = _default_board_style()
    resolved = resolve(chart, _BAR_DATA, board_style)
    r = resolved.layers[0]
    assert isinstance(r, ResolvedLineLayer)
    # With no layer style, the resolved marks must equal the board-base line marks.
    base_line = board_style.line.marks.line
    assert r.line_mark.stroke.width == base_line.stroke.width
    assert r.point_mark.size == board_style.line.marks.point.size


def test_styleless_same_type_layer_inherits_base_charts_own_style() -> None:
    """A line-type layer on a LINE chart (same family as the base) must inherit
    the CHART's own root `style:` override, not just the raw board theme —
    matching the base series' own point/line marks exactly. A line chart's
    own point_mark is resolved as board theme + chart-root style; a same-type
    layer with no per-layer style of its own must resolve identically.

    Regression: layer resolution merged only onto `chart_style_context.line`, skipping
    the chart's own `style:` patch entirely — a chart-root
    `style.marks.point.size` override was invisible to a same-type layer."""
    from dbt_charts.core.compile.models.chart.normalized import LineChart as NLineChart
    from dbt_charts.core.compile.models.chart.resolved._layer import ResolvedLineLayer
    from dbt_charts.core.compile.models.style.authored.line import LineChartStylePatch
    from dbt_charts.core.compile.resolve import resolve

    layer = LineLayer(type="line", y="target")  # no per-layer style
    chart = NLineChart(
        id="line1",
        type="line",
        x="month",
        y="revenue",
        query=_sql(),
        query_name="q",
        variable_dependencies=set(),
        layers=[layer],
        style=LineChartStylePatch.model_validate({"marks": {"point": {"size": 55.0}}}),
    )
    resolved = resolve(chart, _BAR_DATA, _default_board_style())
    r = resolved.layers[0]
    assert isinstance(r, ResolvedLineLayer)
    assert resolved.style.point_mark.size == 55.0
    assert r.point_mark.size == 55.0


def test_bar_layer_marks_bar_propagates() -> None:
    """bar.padding on a bar layer propagates to resolved bar_mark."""
    from dbt_charts.core.compile.models.chart.resolved._layer import ResolvedBarLayer
    from dbt_charts.core.compile.resolve import resolve

    layer = BarChartBarLayer(
        type="bar",
        y="revenue",
        style={"marks": {"bar": {"padding": 0.3}}},
    )
    chart = _bar_normalized(layers=[layer])
    resolved = resolve(chart, _BAR_DATA, _default_board_style())
    r = resolved.layers[0]
    assert isinstance(r, ResolvedBarLayer)
    assert r.bar_mark.padding == 0.3


def test_area_layer_marks_area_and_line_propagate() -> None:
    """area.opacity and line.stroke/labels on an area layer propagate
    independently.

    Vega-Lite compiles an area's top-edge as a genuine separate line mark,
    so its stroke geometry belongs on `marks.line.stroke` (this chart's
    `line_mark`), not `marks.area`. `marks.area` stays fill-only (opacity,
    curve). `marks.line.curve`/`connect` are REJECTED — curve is a single
    shared value living solely on `marks.area.curve` (see
    test_area_layer_rejects_line_curve).
    """
    from dbt_charts.core.compile.models.chart.resolved._layer import ResolvedAreaLayer
    from dbt_charts.core.compile.resolve import resolve

    layer = AreaLayer(
        type="area",
        y="revenue",
        style={
            "marks": {
                "area": {"opacity": 0.4},
                "line": {"stroke": {"width": 3.0}, "labels": {"visible": True}},
            }
        },
    )
    chart = _bar_normalized(layers=[layer])
    resolved = resolve(chart, _BAR_DATA, _default_board_style())
    r = resolved.layers[0]
    assert isinstance(r, ResolvedAreaLayer)
    assert r.area_mark.opacity == 0.4
    assert r.line_mark.stroke.width == 3.0
    assert r.line_mark.labels.visible is True


def test_area_layer_rejects_line_curve() -> None:
    """`marks.line.curve`/`connect` on an area layer must be rejected — curve
    is a single value shared by the fill AND its edge line (they trace one
    silhouette) and lives solely on `marks.area.curve`; a second,
    differently-scoped `curve` field on `marks.line` would silently do
    nothing (regression for the historical dead-field bug where the whole
    `marks.line` slot on area validated, cascaded, and was never read)."""
    with pytest.raises(ValidationError, match="extra_forbidden"):
        AreaLayer(
            type="area",
            y="revenue",
            style={"marks": {"line": {"curve": "step"}}},
        )


def test_base_area_chart_line_stroke_controls_top_edge() -> None:
    """The exact user-reported bug: `style.marks.line.stroke.width` on `type:
    area` must actually control the rendered top-edge stroke (previously
    validated, cascaded into `resolved.style.line_mark`, and silently never
    read — the emitter used a since-removed `marks.area.stroke` field).

    Wide y (2 bands) keeps this chart on the overlap recipe, where
    `marks.line.stroke` still governs the edge directly -- a colorless,
    single-band area now takes the stacked recipe instead, which routes its
    edge stroke through `marks.area.stacked.stroke` (see
    `test_single_series_area_authored_stacked_stroke_width_survives_above_adaptive_ceiling` in
    `tests/core/compile/resolve/test_area_single_series_fill.py`)."""
    from dbt_charts.core.compile.models.chart.normalized import AreaChart as NAreaChart
    from dbt_charts.core.compile.resolve import resolve

    normalized = NAreaChart(
        id="a1",
        type="area",
        x="month",
        y=["revenue", "target"],
        query=_sql(),
        query_name="q",
        variable_dependencies=set(),
        style={"marks": {"line": {"stroke": {"width": 0}}}},
    )
    resolved = resolve(normalized, _BAR_DATA, _default_board_style())
    assert resolved.style.line_mark.stroke.width == 0


def test_scatter_layer_marks_point_propagates() -> None:
    """point.size on a scatter layer propagates to resolved point_mark."""
    from dbt_charts.core.compile.models.chart.resolved._layer import (
        ResolvedScatterLayer,
    )
    from dbt_charts.core.compile.resolve import resolve

    layer = ScatterLayer(
        type="scatter",
        y="revenue",
        style={"marks": {"point": {"size": 55.0}}},
    )
    chart = _bar_normalized(layers=[layer])
    resolved = resolve(chart, _BAR_DATA, _default_board_style())
    r = resolved.layers[0]
    assert isinstance(r, ResolvedScatterLayer)
    assert r.point_mark.size == 55.0


# ── build_patch_model pattern (anti-hand-written-duplicate) ──────────────────


def test_layer_style_marks_is_none_when_omitted() -> None:
    """Layer style patches generated by build_patch_model default marks to None.

    Fails if hand-written default_factory is used instead.
    """
    for payload in [
        {"type": "line", "y": "v", "style": {}},
        {"type": "bar", "y": "v", "style": {}},
        {"type": "area", "y": "v", "style": {}},
        {"type": "scatter", "y": "v", "style": {}},
    ]:
        layer = _layer_ta.validate_python(payload)
        assert layer.style is not None
        assert layer.style.marks is None, (
            f"{payload['type']} layer style.marks must be None by default "
            f"(build_patch_model generates None, not default_factory)"
        )


def test_bar_with_layers_forces_vertical_orientation() -> None:
    """A bar base with overlay layers must resolve to vertical, even for a nominal
    x that would otherwise infer horizontal — the overlay draws every layer with
    the measure on y, so a horizontal base would desync from the overlays."""
    from dbt_charts.core.compile.models.chart.authored._layer import LineLayer
    from dbt_charts.core.compile.resolve import resolve

    # month is nominal → a bare bar would infer horizontal; layers force vertical.
    chart = _bar_normalized(y="revenue", layers=[LineLayer(type="line", y="target")])
    resolved = resolve(chart, _BAR_DATA, _default_board_style())
    assert resolved.orientation == "vertical"


def _line_layer_encoding_channels(vl, y_field):  # type: ignore[no-untyped-def]
    """Return the layer-wrapper encoding dict for the overlay with the given y."""
    for layer in vl.get("layer", []):
        enc = layer.get("encoding", {})
        if enc.get("y", {}).get("field") == y_field:
            return enc
    return {}


def _render_bar_with_line_layer(layer):  # type: ignore[no-untyped-def]
    from dbt_charts.core.compile.resolve import resolve
    from dbt_charts.core.render.chart.emitters.bar import BarEmitter
    from dbt_charts.core.render.chart.translate import translate_to_vl

    chart = _bar_normalized(y="revenue", layers=[layer])
    resolved = resolve(chart, _BAR_DATA, _default_board_style())
    return translate_to_vl(
        BarEmitter().emit(resolved, _DEFAULT_BOX, regroup((), _BAR_DATA))
    )


def _render_bar_with_layer_data(layer, data, *, datasets=None):  # type: ignore[no-untyped-def]
    from dbt_charts.core.compile.resolve import resolve
    from dbt_charts.core.render.chart.emitters.bar import BarEmitter
    from dbt_charts.core.render.chart.translate import translate_to_vl

    chart = _bar_normalized(y="revenue", layers=[layer])
    resolved = resolve(chart, data, _default_board_style())
    return translate_to_vl(
        BarEmitter().emit(resolved, _DEFAULT_BOX, regroup((), data), datasets=datasets)
    )


def _overlay_encoding(vl, y_field):  # type: ignore[no-untyped-def]
    return next(
        layer["encoding"]
        for layer in vl["layer"]
        if layer.get("encoding", {}).get("y", {}).get("field") == y_field
    )


@pytest.mark.parametrize("layer_type", ["bar", "line", "area", "scatter"])
def test_typed_overlay_color_field_is_encoded(layer_type: str) -> None:
    """Every typed overlay passes its color field through to Vega-Lite."""
    layer = _layer_ta.validate_python(
        {"type": layer_type, "y": "target", "color": "region"}
    )

    color = _overlay_encoding(
        _render_bar_with_layer_data(layer, _OVERLAY_COLOR_DATA), "target"
    )["color"]

    assert color["field"] == "region"
    assert color["type"] == "nominal"
    assert "datum" not in color


def test_overlay_color_uses_own_query_rows_for_numeric_type_inference() -> None:
    """A layer color field is inferred from its own query dataset, not the base."""
    layer = LineLayer(type="line", y="target", color="priority", query="goals")
    goals_rows = [
        {"month": "Jan", "target": 90.0, "priority": 1},
        {"month": "Feb", "target": 180.0, "priority": 2},
    ]

    vl = _render_bar_with_layer_data(layer, _BAR_DATA, datasets={"goals": goals_rows})
    overlay = _overlay_encoding(vl, "target")

    assert overlay["color"]["field"] == "priority"
    assert overlay["color"]["type"] == "quantitative"
    assert (
        next(
            layer_vl["data"]["values"]
            for layer_vl in vl["layer"]
            if layer_vl.get("encoding", {}).get("y", {}).get("field") == "target"
        )
        == goals_rows
    )


def test_numeric_overlay_color_uses_an_independent_scale_and_line_glyph() -> None:
    """A numeric own-query color cannot inherit the base series' category scale."""
    import json

    import vl_convert as vlc

    layer = LineLayer(type="line", y="target", color="priority", query="goals")
    goals_rows = [
        {"month": "Jan", "target": 90.0, "priority": 1},
        {"month": "Feb", "target": 180.0, "priority": 2},
    ]

    vl = _render_bar_with_layer_data(layer, _BAR_DATA, datasets={"goals": goals_rows})
    overlay = _overlay_encoding(vl, "target")
    svg = vlc.vegalite_to_svg(json.dumps(vl))

    assert "<svg" in svg
    assert vl["resolve"]["scale"]["color"] == "independent"
    assert overlay["color"]["legend"]["symbolType"] == "stroke"
    assert "symbolStrokeColor" not in overlay["color"]["legend"]


def test_categorical_overlay_color_cycles_the_palette() -> None:
    """Categorical overlay values follow the regular chart palette cycle."""
    palette = _default_board_style().palette
    layer = LineLayer(type="line", y="target", color="region", query="goals")
    goals_rows = [
        {
            "month": f"2026-{index + 1:02d}",
            "target": float(index),
            "region": f"Region {index}",
        }
        for index in range(len(palette) + 1)
    ]

    vl = _render_bar_with_layer_data(layer, _BAR_DATA, datasets={"goals": goals_rows})
    scale = _overlay_encoding(vl, "target")["color"]["scale"]

    assert len(scale["range"]) == len(scale["domain"])
    assert scale["range"][-1] == palette[(len(scale["range"]) - 1) % len(palette)]


def test_categorical_overlay_color_requires_a_palette() -> None:
    """An empty authored palette fails clearly before categorical scale assembly."""
    from dbt_charts.core.compile.models.style.authored.bar import BarChartStylePatch
    from dbt_charts.core.compile.resolve import resolve
    from dbt_charts.core.diagnostics.chart_data import ChartDataError
    from dbt_charts.core.render.chart.emitters.bar import BarEmitter

    chart = _bar_normalized(
        layers=[LineLayer(type="line", y="target", color="region")],
        style=BarChartStylePatch.model_validate(
            {"color": {"categorical": {"palette": []}}}
        ),
    )
    resolved = resolve(chart, _OVERLAY_COLOR_DATA, _default_board_style())

    with pytest.raises(ChartDataError, match="layered chart has no color palette"):
        BarEmitter().emit(resolved, _DEFAULT_BOX, regroup((), _OVERLAY_COLOR_DATA))


def test_categorical_scatter_overlay_uses_circle_legend_glyph() -> None:
    """A scatter field-color overlay keeps a circle glyph over a bar base."""
    layer = ScatterLayer(type="scatter", y="target", color="region")

    symbol_type = _overlay_encoding(
        _render_bar_with_layer_data(layer, _OVERLAY_COLOR_DATA), "target"
    )["color"]["legend"]["symbolType"]

    if isinstance(symbol_type, dict):
        assert "circle" in symbol_type["expr"]
    else:
        assert symbol_type == "circle"


def test_step_band_line_layer_connect_true_has_no_detail() -> None:
    """A step-band overlay with connect:true gets xOffset (band plateaus) but no
    detail channel — the path stays connected across bands."""
    from dbt_charts.core.compile.models.chart.authored._layer import LineLayer

    layer = LineLayer.model_validate(
        {
            "type": "line",
            "y": "target",
            "style": {"marks": {"line": {"curve": "step", "connect": True}}},
        }
    )
    enc = _line_layer_encoding_channels(_render_bar_with_line_layer(layer), "target")
    assert "xOffset" in enc
    assert "detail" not in enc


def test_step_band_line_layer_connect_false_adds_detail() -> None:
    """connect:false adds a detail channel keyed on x so each band's plateau
    floats alone (no vertical connecting jumps) — distinct from connect:true."""
    from dbt_charts.core.compile.models.chart.authored._layer import LineLayer

    layer = LineLayer.model_validate(
        {
            "type": "line",
            "y": "target",
            "style": {"marks": {"line": {"curve": "step", "connect": False}}},
        }
    )
    enc = _line_layer_encoding_channels(_render_bar_with_line_layer(layer), "target")
    assert "xOffset" in enc
    assert enc.get("detail", {}).get("field") == "month"


def test_step_band_xoffset_spans_full_bandwidth() -> None:
    """The xOffset point scale must span the full band width (range
    [0, bandwidth('x')]) so plateaus cover their bar, not a sliver of it."""
    from dbt_charts.core.compile.models.chart.authored._layer import LineLayer

    layer = LineLayer.model_validate(
        {
            "type": "line",
            "y": "target",
            "style": {"marks": {"line": {"curve": "step", "connect": True}}},
        }
    )
    enc = _line_layer_encoding_channels(_render_bar_with_line_layer(layer), "target")
    assert enc["xOffset"]["scale"]["range"] == [0, {"expr": "bandwidth('x')"}]


def test_step_band_overlay_does_not_shrink_base_bar_width() -> None:
    """A step-band line overlay adds a sibling xOffset scale to the base bar's
    layer array. Vega-Lite's mark.width `{"band": v}` shorthand degrades to a
    static default step once any sibling layer carries a discrete xOffset
    scale, rendering visibly thinner bars. The base bar's width must be pinned
    to an explicit bandwidth('x') expression that preserves the SAME band
    fraction a non-step-band overlay would have used (no hardcoded fraction)."""
    from dbt_charts.core.compile.models.chart.authored._layer import LineLayer

    plain_layer = LineLayer.model_validate({"type": "line", "y": "target"})
    plain_width = _render_bar_with_line_layer(plain_layer)["layer"][0]["mark"]["width"]
    assert isinstance(plain_width, dict) and "band" in plain_width
    band_frac = plain_width["band"]

    step_layer = LineLayer.model_validate(
        {
            "type": "line",
            "y": "target",
            "style": {"marks": {"line": {"curve": "step", "connect": True}}},
        }
    )
    step_width = _render_bar_with_line_layer(step_layer)["layer"][0]["mark"]["width"]
    assert step_width == {"expr": f"{band_frac} * bandwidth('x')"}


def test_authored_overlay_preserves_grouped_base_bar_width() -> None:
    """A grouped bar's width belongs to its xOffset sub-band, not the outer x
    category band. Adding an authored overlay must keep that scale identity or
    every series bar expands to the full category width (N times too wide for
    N series)."""
    from dbt_charts.core.compile.models.chart.authored._layer import LineLayer
    from dbt_charts.core.compile.resolve import resolve
    from dbt_charts.core.render.chart.emitters.bar import BarEmitter
    from dbt_charts.core.render.chart.translate import translate_to_vl

    data = [
        {"month": "Jan", "revenue": 100.0, "target": 90.0, "region": "North"},
        {"month": "Jan", "revenue": 80.0, "target": 90.0, "region": "South"},
        {"month": "Feb", "revenue": 200.0, "target": 180.0, "region": "North"},
        {"month": "Feb", "revenue": 160.0, "target": 180.0, "region": "South"},
    ]
    chart = _bar_normalized(color="region", layers=[LineLayer(type="line", y="target")])
    resolved = resolve(chart, data, _default_board_style())
    vl = translate_to_vl(BarEmitter().emit(resolved, _DEFAULT_BOX, regroup((), data)))

    base = vl["layer"][0]
    assert "xOffset" in base["encoding"]
    width = base["mark"]["width"]
    assert "bandwidth('xOffset')" in width["expr"]
    assert "bandwidth('x')" not in width["expr"]


def test_layer_with_own_query_uses_its_own_dataset() -> None:
    """A layer authored with its own `query:` must render with THAT query's
    rows, not silently share the base chart's data — regression for per-layer
    queries being executed but discarded (only the base series ever rendered,
    the overlay's own series went missing)."""
    from dbt_charts.core.compile.models.chart.authored._layer import LineLayer
    from dbt_charts.core.compile.resolve import resolve
    from dbt_charts.core.render.chart.emitters.bar import BarEmitter
    from dbt_charts.core.render.chart.translate import translate_to_vl

    layer = LineLayer(type="line", y="target", query="goals")
    chart = _bar_normalized(y="revenue", layers=[layer])
    resolved = resolve(chart, _BAR_DATA, _default_board_style())
    assert resolved.layers[0].query_name == "goals"

    goals_rows = [{"month": "Jan", "target": 999.0}]
    vl = translate_to_vl(
        BarEmitter().emit(
            resolved,
            _DEFAULT_BOX,
            regroup((), _BAR_DATA),
            datasets={"goals": goals_rows},
        )
    )
    line_layer_vl = next(
        layer_vl
        for layer_vl in vl["layer"]
        if layer_vl.get("encoding", {}).get("y", {}).get("field") == "target"
    )
    assert line_layer_vl["data"]["values"] == goals_rows


def test_layer_without_query_falls_back_to_base_data() -> None:
    """A layer with no authored `query:` shares the base chart's data — no
    `.data` override, matching pre-existing single-query behavior.

    query_name resolves to the BASE chart's own query_name (baked at resolve
    time), not None — a layer that didn't author an override is never an
    ambiguous "None" at render time; it's a concrete key equal to the base's,
    so a `datasets` lookup by query_name resolves uniformly for base and
    layer alike."""
    from dbt_charts.core.compile.models.chart.authored._layer import LineLayer
    from dbt_charts.core.compile.resolve import resolve
    from dbt_charts.core.render.chart.emitters.bar import BarEmitter
    from dbt_charts.core.render.chart.translate import translate_to_vl

    layer = LineLayer(type="line", y="target")
    chart = _bar_normalized(y="revenue", layers=[layer])
    resolved = resolve(chart, _BAR_DATA, _default_board_style())
    assert resolved.layers[0].query_name == resolved.query_name

    vl = translate_to_vl(
        BarEmitter().emit(
            resolved,
            _DEFAULT_BOX,
            regroup((), _BAR_DATA),
            datasets={"goals": [{"month": "Jan"}]},
        )
    )
    line_layer_vl = next(
        layer_vl
        for layer_vl in vl["layer"]
        if layer_vl.get("encoding", {}).get("y", {}).get("field") == "target"
    )
    assert "data" not in line_layer_vl


def test_layer_sharing_base_query_gets_same_normalized_x_domain_as_base() -> None:
    """A layer with no own `x`/`query` shares the base's query_name (see
    `test_layer_without_query_falls_back_to_base_data`), so the real render
    pipeline's `datasets` dict (populated by `layout_sizing.py` from
    `executor.execute_query` for EVERY query_name including the base's own)
    always carries a `datasets[base.query_name]` entry — raw, pre-normalization
    rows. A non-diverging layer (its resolved `query_name` equals the base's
    own) must NOT read that raw entry at all — it shares the base's already
    ISO-normalized `data` (BarEmitter's own `normalize_labeled_temporal`
    rebind), via the outer spec's `.data`, which `render_cartesian_overlay`
    stamps from that same normalized rows for exactly this reason. Regression:
    on a year-month x ("2025-01".."2025-12"), the line overlay's stamped own
    `.data` (built from the raw `datasets` lookup) used to diverge from the
    base's normalized ISO domain — 24 distinct x categories (12 raw + 12 ISO)
    instead of one shared 12-value domain, splitting bars into the first half
    and the line into the second half of the axis."""
    from dbt_charts.core.compile.models.chart.authored._layer import LineLayer
    from dbt_charts.core.compile.resolve import resolve
    from dbt_charts.core.render.chart.emitters.bar import BarEmitter
    from dbt_charts.core.render.chart.translate import translate_to_vl

    raw_data = [
        {"month": f"2025-{m:02d}", "revenue": m * 10.0, "conversion_pct": m * 0.1}
        for m in range(1, 13)
    ]
    layer = LineLayer(type="line", y="conversion_pct")
    chart = _bar_normalized(y="revenue", layers=[layer])
    resolved = resolve(chart, raw_data, _default_board_style())
    assert resolved.layers[0].query_name == resolved.query_name

    # datasets keyed by the base's OWN query_name with the raw (pre-emit)
    # rows — exactly what layout_sizing.collect_layer_datasets produces.
    vl = translate_to_vl(
        BarEmitter().emit(
            resolved, _DEFAULT_BOX, regroup((), raw_data), datasets={"q": raw_data}
        )
    )
    line_layer_vl = next(
        layer_vl
        for layer_vl in vl["layer"]
        if layer_vl.get("encoding", {}).get("y", {}).get("field") == "conversion_pct"
    )
    # A non-diverging layer carries no own `.data` — it inherits the outer
    # spec's, which must already be the base's ISO-normalized rows.
    assert "data" not in line_layer_vl
    outer_months = {row["month"] for row in vl["data"]["values"]}
    expected_months = {f"2025-{m:02d}-01" for m in range(1, 13)}
    assert outer_months == expected_months, (
        f"outer spec must share the base's ISO-normalized x domain, got {outer_months}"
    )


def test_own_query_overlay_skips_canonicalization_when_base_authors_temporal_type() -> (
    None
):
    """An authored ``axis_x.type: temporal`` escape hatch makes the base's OWN
    ``gap_fill_ordinal_time`` skip canonicalization entirely
    (``resolve_authored_x_type(ax) == "temporal"`` — ``_channels.py``),
    leaving raw ``datetime.datetime`` values that stringify via ``str()``
    ("2024-01-01 00:00:00"). An own-query overlay layer sharing that SAME x
    field must skip too, or it lands on ``canonicalize_and_sort_ordinal_x``'s
    date-only ISO form ("2024-01-01") instead — the two then parse as
    different instants (naive-local vs. UTC-midnight) under any non-UTC
    runtime TZ, invisible under UTC CI. Regression for `_overlay.py`
    re-deriving this verdict from the layer's own data + the base's axis
    style instead of inheriting the base's actual (family-specific) skip
    decision — asserts the emitted row values directly, not a rendered
    position."""
    import datetime as dt

    from dbt_charts.core.compile.models.chart.authored._layer import LineLayer
    from dbt_charts.core.compile.models.style.authored.bar import BarChartStylePatch
    from dbt_charts.core.compile.resolve import resolve
    from dbt_charts.core.render.chart.emitters.bar import BarEmitter
    from dbt_charts.core.render.chart.translate import translate_to_vl

    base_rows = [
        {"month": dt.datetime(2024, m, 1), "revenue": float(m)} for m in (1, 4, 7, 10)
    ]
    own_rows = [
        {"month": dt.datetime(2024, m, 1), "target": float(m)} for m in (1, 4, 7, 10)
    ]
    chart = _bar_normalized(
        y="revenue",
        layers=[LineLayer(type="line", y="target", query="overlay_q")],
        style=BarChartStylePatch.model_validate({"axis_x": {"type": "temporal"}}),
    )
    resolved = resolve(chart, base_rows, _default_board_style())
    vl = translate_to_vl(
        BarEmitter().emit(
            resolved,
            _DEFAULT_BOX,
            regroup((), base_rows),
            datasets={"overlay_q": own_rows},
        )
    )
    line_layer_vl = next(
        layer_vl
        for layer_vl in vl["layer"]
        if layer_vl.get("encoding", {}).get("y", {}).get("field") == "target"
    )
    layer_months = [row["month"] for row in line_layer_vl["data"]["values"]]
    # translate.py's normalize_data_types always stringifies a raw datetime
    # at assembly time — the distinguishing signal is WHICH string form: the
    # base's own skip verdict leaves a naive str() form ("2024-01-01
    # 00:00:00"), never the date-only ISO form canonicalize_and_sort_
    # ordinal_x would have produced ("2024-01-01").
    assert layer_months == [str(dt.datetime(2024, m, 1)) for m in (1, 4, 7, 10)], (
        "own-query overlay layer must skip canonicalization when the base's "
        "own axis_x.type: temporal escape hatch skips it too, else the two "
        f"sides land on different JS-Date-parseable string forms; got {layer_months!r}"
    )


def test_own_query_overlay_layer_does_not_impose_base_time_unit_on_a_different_field() -> (
    None
):
    """A layer authoring its OWN ``x:`` on a genuinely different, finer-grain
    field must not inherit the base's authored ``axis_x.time_unit`` — that
    grain was authored for the base's own field ("month"), not the layer's
    ("ts"). Regression: `_overlay.py` passed the base's whole axis style
    (including its authored ``time_unit``) to ``canonicalize_cartesian_x_
    data`` regardless of which field it was canonicalizing, so a base
    ``time_unit: yearmonth`` silently collapsed the layer's three distinct
    sub-daily timestamps onto one shared date — a pure render-layer data
    mutation with no encoding-level ``timeUnit`` counterpart."""
    import datetime as dt

    from dbt_charts.core.compile.models.chart.authored._layer import LineLayer
    from dbt_charts.core.compile.models.style.authored.bar import BarChartStylePatch
    from dbt_charts.core.compile.resolve import resolve
    from dbt_charts.core.render.chart.emitters.bar import BarEmitter
    from dbt_charts.core.render.chart.translate import translate_to_vl

    base_rows = [
        {"month": "2024-01-01", "revenue": 10.0},
        {"month": "2024-02-01", "revenue": 20.0},
    ]
    own_rows = [
        {"ts": dt.datetime(2024, 1, 15, 9, 0, 0), "value": 1.0},
        {"ts": dt.datetime(2024, 1, 15, 13, 0, 0), "value": 2.0},
        {"ts": dt.datetime(2024, 1, 15, 17, 0, 0), "value": 3.0},
        {"ts": dt.datetime(2024, 2, 15, 9, 0, 0), "value": 4.0},
    ]
    chart = _bar_normalized(
        y="revenue",
        layers=[LineLayer(type="line", x="ts", y="value", query="overlay_q")],
        style=BarChartStylePatch.model_validate({"axis_x": {"time_unit": "yearmonth"}}),
    )
    resolved = resolve(chart, base_rows, _default_board_style())
    vl = translate_to_vl(
        BarEmitter().emit(
            resolved,
            _DEFAULT_BOX,
            regroup((), base_rows),
            datasets={"overlay_q": own_rows},
        )
    )
    line_layer_vl = next(
        layer_vl
        for layer_vl in vl["layer"]
        if layer_vl.get("encoding", {}).get("y", {}).get("field") == "value"
    )
    layer_ts_values = [row["ts"] for row in line_layer_vl["data"]["values"]]
    assert len(set(layer_ts_values)) == 4, (
        "a layer's own finer-grain x field must not be collapsed onto the "
        f"base's authored bucket grain for a DIFFERENT field; got {layer_ts_values!r}"
    )


def test_step_band_on_base_does_not_shrink_bar_layer_width() -> None:
    """When the BASE chart (not an overlay layer) applies curve: step to
    itself on a band x-axis, its own xOffset is a sibling of a bar OVERLAY
    layer's band scale inside the outer layer array — the same VL
    band-width-shorthand degradation applies to the bar layer, not just a
    base bar. Regression: only the base-is-bar case was fixed; band-step-on-
    primary + a bar layer left the bar layer's own width broken."""
    from dbt_charts.core.compile.models.chart.normalized import AreaChart as NAreaChart
    from dbt_charts.core.compile.resolve import resolve
    from dbt_charts.core.render.chart.emitters.area import AreaEmitter
    from dbt_charts.core.render.chart.translate import translate_to_vl

    chart = NAreaChart(
        id="area1",
        type="area",
        x="month",
        y="revenue",
        query=_sql(),
        query_name="q",
        variable_dependencies=set(),
        style={"marks": {"area": {"curve": "step"}}},
        layers=[BarChartBarLayer(type="bar", y="target")],
    )
    resolved = resolve(chart, _BAR_DATA, _default_board_style())
    vl = translate_to_vl(
        AreaEmitter().emit(resolved, _DEFAULT_BOX, regroup((), _BAR_DATA))
    )

    bar_layer_vl = next(
        layer_vl
        for layer_vl in vl["layer"]
        if layer_vl.get("mark", {}).get("type") == "bar"
    )
    width = bar_layer_vl["mark"]["width"]
    assert isinstance(width, dict)
    assert "bandwidth" in width.get("expr", "")


def test_area_layer_suppresses_halo_over_base_series() -> None:
    """An AREA overlay LAYER paints ON TOP of the base series (paint-order
    contract). Its halo — an opaque knockout mask, normally used to give a
    single series a clean edge — must be suppressed for an overlay: painting
    it would opaquely block the base series from showing through underneath,
    defeating the point of a translucent overlay (and making any authored
    opacity < 1 visually indistinguishable from solid, since translucent
    color over opaque white looks identical to a paler solid fill)."""
    from dbt_charts.core.compile.models.chart.authored._layer import AreaLayer
    from dbt_charts.core.compile.resolve import resolve
    from dbt_charts.core.render.chart.emitters.bar import BarEmitter
    from dbt_charts.core.render.chart.translate import translate_to_vl

    layer = AreaLayer(
        type="area", y="target", style={"marks": {"area": {"opacity": 0.5}}}
    )
    chart = _bar_normalized(y="revenue", layers=[layer])
    resolved = resolve(chart, _BAR_DATA, _default_board_style())
    vl = translate_to_vl(
        BarEmitter().emit(resolved, _DEFAULT_BOX, regroup((), _BAR_DATA))
    )

    area_wrapper = next(
        layer_vl
        for layer_vl in vl["layer"]
        if layer_vl.get("encoding", {}).get("y", {}).get("field") == "target"
    )
    area_marks = [
        s["mark"] for s in area_wrapper["layer"] if s["mark"].get("type") == "area"
    ]
    # No opaque (fillOpacity=1) halo area — only the authored-opacity fg.
    assert len(area_marks) == 1
    assert area_marks[0]["fillOpacity"] == 0.5


def test_area_base_halo_not_recolored_by_shared_legend_scale() -> None:
    """A composite area base (halo + fg sub-layers) with a bar overlay layer
    must not have its halo knockout layer recolored by the shared legend color
    scale — the halo pins its own literal color instead. Regression: an
    inherited encoding on the outer wrapper alone recolors the halo too,
    collapsing the area's opacity."""
    from dbt_charts.core.compile.models.chart.normalized import AreaChart as NAreaChart
    from dbt_charts.core.compile.resolve import resolve
    from dbt_charts.core.render.chart.emitters.area import AreaEmitter
    from dbt_charts.core.render.chart.translate import translate_to_vl

    chart = NAreaChart(
        id="area1",
        type="area",
        x="month",
        y="revenue",
        query=_sql(),
        query_name="q",
        variable_dependencies=set(),
        layers=[BarChartBarLayer(type="bar", y="target")],
    )
    resolved = resolve(chart, _BAR_DATA, _default_board_style())
    vl = translate_to_vl(
        AreaEmitter().emit(resolved, _DEFAULT_BOX, regroup((), _BAR_DATA))
    )

    base = vl["layer"][0]
    assert (
        base["encoding"]["color"].get("scale") is not None
    )  # shared scale, on wrapper

    single_series_fill = resolved.style.single_series_fill
    fg = next(s for s in base["layer"] if s["mark"].get("fill") == single_series_fill)
    assert "color" not in fg.get("encoding", {})  # inherits the wrapper's shared scale

    halo = next(
        s
        for s in base["layer"]
        if s["mark"].get("fill") not in (single_series_fill, None)
    )
    assert halo["encoding"]["fill"] == {"value": halo["mark"]["fill"]}


def test_base_with_layers_paints_from_category_palette_not_single_series_ink() -> None:
    """A multi-layer chart is a multi-series chart in disguise: the base series
    must share the category palette with its overlays (slot 0), not the
    single-series ink — matching pre-migration `type: layered` behavior."""
    from dbt_charts.core.compile.models.chart.authored._layer import LineLayer

    palette = _default_board_style().palette
    vl = _render_bar_with_line_layer(LineLayer(type="line", y="target"))

    base_layer = vl["layer"][0]
    base_color = base_layer["encoding"]["color"]
    assert base_color["scale"]["range"][0] == palette[0]

    overlay_color = _line_layer_encoding_channels(vl, "target")["color"]
    assert overlay_color["scale"]["range"][1] == palette[1]


def _render_color_base_with_line_layer(
    base_type,
    layer,
    *,
    style=None,
    data=_COLOR_LAYER_DATA,  # type: ignore[no-untyped-def]
    board_style=None,
):
    from dbt_charts.core.compile.models.chart.normalized import (
        AreaChart as NAreaChart,
        BarChart as NBarChart,
        LineChart as NLineChart,
    )
    from dbt_charts.core.compile.resolve import resolve
    from dbt_charts.core.render.chart.emitters.area import AreaEmitter
    from dbt_charts.core.render.chart.emitters.bar import BarEmitter
    from dbt_charts.core.render.chart.emitters.line import LineEmitter
    from dbt_charts.core.render.chart.translate import translate_to_vl

    chart_types = {
        "area": (NAreaChart, AreaEmitter),
        "bar": (NBarChart, BarEmitter),
        "line": (NLineChart, LineEmitter),
    }
    chart_type, emitter_type = chart_types[base_type]
    chart = chart_type(
        id=f"{base_type}1",
        type=base_type,
        x="month",
        y="revenue",
        color="kind",
        query=_sql(),
        query_name="q",
        variable_dependencies=set(),
        layers=[layer],
        style=style,
    )
    resolved = resolve(chart, data, board_style or _default_board_style())
    vl = translate_to_vl(emitter_type().emit(resolved, _DEFAULT_BOX, regroup((), data)))
    return vl, resolved


def _overlay_line_stroke(vl):  # type: ignore[no-untyped-def]
    wrapper = next(
        item
        for item in vl["layer"]
        if item.get("encoding", {}).get("y", {}).get("field") == "target"
    )
    return next(
        item["mark"]["stroke"]
        for item in wrapper["layer"]
        if item.get("mark", {}).get("type") == "line"
        and item["mark"].get("stroke") is not None
    )


def test_grouped_color_base_and_layer_share_complete_rendered_legend() -> None:
    import json
    import re

    import vl_convert as vlc

    from dbt_charts.core.compile.models.chart.authored._layer import LineLayer
    from dbt_charts.core.compile.models.style.authored.bar import BarChartStylePatch

    vl, _ = _render_color_base_with_line_layer(
        "bar",
        LineLayer(type="line", y="target", label="Goal"),
        style=BarChartStylePatch(stack="none"),
    )
    scale = vl["layer"][0]["encoding"]["color"]["scale"]
    assert scale["domain"] == ["Named", "Non-Named", "Goal"]

    svg = vlc.vegalite_to_svg(json.dumps(vl))
    legend_labels = [
        label
        for label in ("Named", "Non-Named", "Goal")
        if re.search(rf"<text[^>]*>{re.escape(label)}</text>", svg)
    ]
    assert legend_labels == ["Named", "Non-Named", "Goal"]


def test_categorical_overlay_color_extends_shared_scale_and_legend_glyph() -> None:
    """Categorical overlay values reuse and extend the base scale by mark type."""
    from dbt_charts.core.compile.models.chart.authored._layer import LineLayer
    from dbt_charts.core.compile.models.style.authored.bar import BarChartStylePatch

    vl, _ = _render_color_base_with_line_layer(
        "bar",
        LineLayer(type="line", y="target", color="region"),
        style=BarChartStylePatch(stack="none"),
        data=_OVERLAY_COLOR_DATA,
    )
    scale = vl["layer"][0]["encoding"]["color"]["scale"]
    overlay_color = _overlay_encoding(vl, "target")["color"]

    assert scale["domain"] == ["Named", "Non-Named", "West"]
    assert len(scale["range"]) == len(scale["domain"])
    assert scale["domain"].count("Named") == 1
    assert overlay_color["scale"] == scale
    assert "datum" not in overlay_color
    assert "West" in overlay_color["legend"]["symbolType"]["expr"]
    assert "stroke" in overlay_color["legend"]["symbolType"]["expr"]
    assert "symbolStrokeColor" not in overlay_color["legend"]


def test_stacked_color_base_layer_paint_matches_legend_scale() -> None:
    import json
    from xml.etree import ElementTree

    import vl_convert as vlc

    from dbt_charts.core.compile.models.chart.authored._layer import LineLayer
    from dbt_charts.core.compile.models.style.authored.bar import BarChartStylePatch

    vl, _ = _render_color_base_with_line_layer(
        "bar",
        LineLayer(type="line", y="target", label="Goal"),
        style=BarChartStylePatch(stack="zero"),
        board_style=_default_board_style_no_endpoint_labels(),
    )
    scale = vl["layer"][0]["encoding"]["color"]["scale"]
    goal_index = scale["domain"].index("Goal")
    goal_color = scale["range"][goal_index]

    assert goal_color
    assert _overlay_line_stroke(vl) == goal_color

    svg = ElementTree.fromstring(vlc.vegalite_to_svg(json.dumps(vl)))
    legend_symbol_paths = [
        path
        for group in svg.iter()
        if "role-legend-symbol" in group.attrib.get("class", "")
        for path in group.iter()
        if path.tag.endswith("path")
    ]
    goal_paths = [
        path for path in legend_symbol_paths if path.attrib.get("fill") == goal_color
    ]
    assert len(goal_paths) == 1
    assert goal_paths[0].attrib.get("stroke") == goal_color


def test_authored_layer_stroke_drives_paint_and_legend_scale() -> None:
    import json
    from xml.etree import ElementTree

    import vl_convert as vlc

    from dbt_charts.core.compile.models.chart.authored._layer import LineLayer

    layer = LineLayer(
        type="line",
        y="target",
        label="Goal",
        style={"marks": {"line": {"stroke": {"color": "dbt-creams.subtitle"}}}},
    )
    vl, resolved = _render_color_base_with_line_layer("bar", layer)
    scale = vl["layer"][0]["encoding"]["color"]["scale"]
    goal_color = scale["range"][scale["domain"].index("Goal")]
    authored_color = resolved.layers[0].line_mark.stroke.color

    assert goal_color == authored_color
    assert _overlay_line_stroke(vl) == authored_color

    svg = ElementTree.fromstring(vlc.vegalite_to_svg(json.dumps(vl)))
    target_paths = [
        path
        for group in svg.iter()
        if "role-legend-symbol" in group.attrib.get("class", "")
        for path in group.iter()
        if path.tag.endswith("path") and path.attrib.get("fill") == authored_color
    ]
    assert len(target_paths) == 1
    assert target_paths[0].attrib.get("stroke") == authored_color


def test_color_field_base_layer_recycles_palette_when_exhausted() -> None:
    """A layer past the last available palette slot recycles via modulo, matching
    the base's own field-color scale and the non-field-color-base layer branch."""
    from dbt_charts.core.compile.models.chart.authored._layer import LineLayer
    from dbt_charts.core.compile.resolve import resolve
    from dbt_charts.core.render.chart.emitters.bar import BarEmitter
    from dbt_charts.core.render.chart.translate import translate_to_vl

    # 3 distinct `kind` values against a 2-color palette: the base already
    # occupies both slots, so the "Goal" layer lands at layer_ord 3 -- 3 % 2 == 1,
    # a genuine wraparound (not slot 0), so this can't pass by returning a
    # constant.
    palette = ("red", "blue")
    data = [
        {"month": "Jan", "revenue": 100.0, "target": 90.0, "kind": "Named"},
        {"month": "Jan", "revenue": 50.0, "target": 90.0, "kind": "Non-Named"},
        {"month": "Jan", "revenue": 30.0, "target": 90.0, "kind": "Other"},
        {"month": "Feb", "revenue": 200.0, "target": 180.0, "kind": "Named"},
        {"month": "Feb", "revenue": 80.0, "target": 180.0, "kind": "Non-Named"},
        {"month": "Feb", "revenue": 60.0, "target": 180.0, "kind": "Other"},
    ]
    chart = _bar_normalized(
        color="kind",
        layers=[LineLayer(type="line", y="target", label="Goal")],
    )
    resolved = resolve(chart, data, _default_board_style()).model_copy(
        update={"palette": palette}
    )

    vl = translate_to_vl(BarEmitter().emit(resolved, _DEFAULT_BOX, regroup((), data)))
    scale = vl["layer"][0]["encoding"]["color"]["scale"]
    goal_index = scale["domain"].index("Goal")

    assert goal_index == 3
    assert scale["range"][goal_index] == "blue"
    assert _overlay_line_stroke(vl) == "blue"


def test_color_field_base_rejects_layer_label_collision() -> None:
    from dbt_charts.core.compile.models.chart.authored._layer import LineLayer
    from dbt_charts.core.compile.resolve import resolve
    from dbt_charts.core.diagnostics.chart_data import ChartDataError
    from dbt_charts.core.render.chart.emitters.bar import BarEmitter

    data = [
        {"month": "Jan", "revenue": 100.0, "target": 90.0, "kind": "Goal"},
        {"month": "Jan", "revenue": 50.0, "target": 90.0, "kind": "Other"},
    ]
    chart = _bar_normalized(
        color="kind",
        layers=[LineLayer(type="line", y="target", label="Goal")],
    )
    resolved = resolve(chart, data, _default_board_style())

    with pytest.raises(ChartDataError, match="layer label 'Goal'.*color scale"):
        BarEmitter().emit(resolved, _DEFAULT_BOX, regroup((), data))


def test_quantitative_color_base_preserves_non_shared_layer_color() -> None:
    from dbt_charts.core.compile.models.chart.authored._layer import LineLayer
    from dbt_charts.core.compile.resolve import resolve
    from dbt_charts.core.render.chart.emitters.bar import BarEmitter
    from dbt_charts.core.render.chart.translate import translate_to_vl

    data = [
        {"month": "Jan", "revenue": 100.0, "target": 90.0, "score": 1.0},
        {"month": "Feb", "revenue": 200.0, "target": 180.0, "score": 2.0},
    ]
    chart = _bar_normalized(
        color="score",
        layers=[LineLayer(type="line", y="target", label="Goal")],
    )
    resolved = resolve(chart, data, _default_board_style())
    vl = translate_to_vl(BarEmitter().emit(resolved, _DEFAULT_BOX, regroup((), data)))
    overlay_color = _line_layer_encoding_channels(vl, "target")["color"]

    assert "scale" not in overlay_color
    assert _overlay_line_stroke(vl) == resolved.style.single_series_fill


@pytest.mark.parametrize("base_type", ["line", "area"])
def test_color_field_base_layer_shared_scale_family_parity(base_type: str) -> None:
    from dbt_charts.core.compile.models.chart.authored._layer import LineLayer

    vl, _ = _render_color_base_with_line_layer(
        base_type, LineLayer(type="line", y="target", label="Goal")
    )
    scale = vl["layer"][0]["encoding"]["color"]["scale"]

    assert set(scale["domain"]) == {"Named", "Non-Named", "Goal"}
    assert _overlay_line_stroke(vl) == scale["range"][scale["domain"].index("Goal")]


def _layer_text_encodings(vl, field):  # type: ignore[no-untyped-def]
    """Text-channel encodings sourcing ``field`` across the layered spec."""
    found = []
    for sub in vl.get("layer", []):
        for candidate in (sub, *sub.get("layer", [])):
            mark = candidate.get("mark")
            text = candidate.get("encoding", {}).get("text")
            if (
                isinstance(mark, dict)
                and mark.get("type") == "text"
                and isinstance(text, dict)
                and text.get("field") == field
            ):
                found.append(text)
    return found


# Each layer family reads its caption from a different mark slot, so the
# verdict is threaded through a different branch of _build_layer_label_specs.
_LAYER_LABEL_SLOT = {
    "bar": "bar",
    "line": "line",
    "area": "line",  # an area layer labels via its line_mark, like the base does
    "scatter": "point",
}


@pytest.mark.parametrize("layer_type", ["bar", "line", "area", "scatter"])
def test_overlay_layer_text_caption_is_drawn_from_its_own_rows(
    layer_type: str,
) -> None:
    """An overlay layer's caption column is classified against ITS dataset.

    Two things at once, because they fail as one. The caption is text, so it
    must reach Vega as nominal with no number format — formatting a string
    paints NaN. And `caption` exists only in the layer's own query, never in
    the base chart's rows, so a verdict taken from the base data finds no
    column, reads as "not text", and paints that NaN anyway.

    Parametrized over all four families because each threads the verdict
    through its own branch of `_build_layer_label_specs`, reading a different
    mark slot. This is the path a captioned reference mark actually takes; the
    base-chart label tests in test_value_labels.py cannot reach it.
    """
    slot = _LAYER_LABEL_SLOT[layer_type]
    layer = _layer_ta.validate_python(
        {
            "type": layer_type,
            "y": "target",
            "query": "goals",
            "style": {
                "marks": {slot: {"labels": {"visible": True, "field": "caption"}}}
            },
        }
    )
    goals_rows = [
        {"month": "Jan", "target": 90.0, "caption": None},
        {"month": "Feb", "target": 180.0, "caption": "Pace"},
    ]
    assert all("caption" not in row for row in _BAR_DATA), (
        "fixture drift: the base rows must NOT carry the caption column, or "
        "this test stops proving the verdict came from the layer's own rows"
    )

    vl = _render_bar_with_layer_data(layer, _BAR_DATA, datasets={"goals": goals_rows})

    captions = _layer_text_encodings(vl, "caption")
    assert captions, "overlay layer emitted no label sourcing its caption column"
    for enc in captions:
        assert enc["type"] == "nominal", enc
        assert "format" not in enc, enc
