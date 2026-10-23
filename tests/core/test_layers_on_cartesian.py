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
        style={"marks": {"line": {"stroke": {"color": "dbt-creams.7"}}}},
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
    from dbt_charts.core.compile.errors import CompilationError
    from dbt_charts.core.compile.resolve import resolve

    line_layer = LineLayer(
        type="line",
        y="target",
        style={"marks": {"line": {"stroke": {"color": "dbt-creams.does-not-exist"}}}},
    )
    chart = _bar_normalized(layers=[line_layer])
    with pytest.raises(CompilationError, match="chart 'bar1'"):
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


# ── MS4: emit_bar_layer / emit_area_layer / emit_scatter_layer ────────────


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
    from dbt_charts.core.constants.marks import STROKE_FALLBACK_WIDTH

    layer = LineLayer(type="line", y="target")
    chart = _bar_normalized(layers=[layer])
    board_style = _default_board_style()
    resolved = resolve(chart, _BAR_DATA, board_style)
    r = resolved.layers[0]
    assert isinstance(r, ResolvedLineLayer)
    # With no layer style, the resolved marks must equal the board-base line
    # marks. Width is the one field the board base itself leaves unset by
    # design (no cascade tier pins one here) -- it resolves via
    # STROKE_FALLBACK_WIDTH instead of a theme literal.
    base_line = board_style.line.marks.line
    assert base_line.stroke is not None and base_line.stroke.width is None
    assert r.line_mark.stroke.width == STROKE_FALLBACK_WIDTH
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

    layer = BarLayer(
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
