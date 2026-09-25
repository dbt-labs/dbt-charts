"""TDD tests for legend symbol glyph control on layered charts (defect #206).

Covers:
- Solo-mark glyph gating: single-mark overlay gets mark-aware glyph, not filled circle
- Bar overlay gets a square glyph (previously: gating skipped → VL default circle)
- Line overlay on line base: base field-series get stroke (not 'square' from else branch)
- Author-facing symbol_shape override: constant symbolType wins over mark-derived expr
- Author-facing symbol_fill override: False → symbolFillColor=transparent in VL legend

All tests pin to the stark theme, which has a visible legend (the editorial/default theme
suppresses the legend at the board level, making these render-path tests meaningless).
"""

from __future__ import annotations

from collections.abc import Iterator
from typing import Any

import pytest

from dbt_charts.core.compile.config import get_theme_style, reset_config
from dbt_charts.core.compile.resolve.chart._chart_rows import regroup
from dbt_charts.core.render.chart.spec import RenderBox

_DEFAULT_BOX = RenderBox(width=600.0, height=300.0)


@pytest.fixture(autouse=True)
def stark(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    """Pin to stark so the legend is visible for all tests in this module."""
    reset_config()
    monkeypatch.setenv("DCT_DEFAULT_THEME", "stark")
    yield
    reset_config()


# ── shared helpers ──────────────────────────────────────────────────────────


def _board_style() -> Any:
    from dbt_charts.core.compile.resolve.style.board import resolve_chart_style_context

    return resolve_chart_style_context(get_theme_style("stark"))


def _sql(sql: str = "SELECT 1") -> Any:
    from dbt_charts.core.compile.models.query.normalized import SqlQuery

    return SqlQuery(sql=sql, source="t")


def _bar_normalized(**kwargs: Any) -> Any:
    from dbt_charts.core.compile.models.chart.normalized import BarChart as NBarChart

    defaults: dict[str, Any] = {
        "id": "bar1",
        "type": "bar",
        "x": "month",
        "y": "revenue",
        "query": _sql(),
        "query_name": "q",
        "variable_dependencies": set(),
    }
    defaults.update(kwargs)
    return NBarChart(**defaults)


def _line_normalized(**kwargs: Any) -> Any:
    from dbt_charts.core.compile.models.chart.normalized import LineChart as NLineChart

    defaults: dict[str, Any] = {
        "id": "line1",
        "type": "line",
        "x": "month",
        "y": "revenue",
        "query": _sql(),
        "query_name": "q",
        "variable_dependencies": set(),
    }
    defaults.update(kwargs)
    return NLineChart(**defaults)


def _area_normalized(**kwargs: Any) -> Any:
    from dbt_charts.core.compile.models.chart.normalized import AreaChart as NAreaChart

    defaults: dict[str, Any] = {
        "id": "area1",
        "type": "area",
        "x": "month",
        "y": "revenue",
        "query": _sql(),
        "query_name": "q",
        "variable_dependencies": set(),
    }
    defaults.update(kwargs)
    return NAreaChart(**defaults)


_DATA: list[dict[str, Any]] = [
    {"month": "Jan", "revenue": 100.0, "target": 4000.0, "cat": "A"},
    {"month": "Feb", "revenue": 200.0, "target": 5000.0, "cat": "B"},
    {"month": "Mar", "revenue": 150.0, "target": 4500.0, "cat": "A"},
]


def _render_bar_with_layers(layers: list[Any], **bar_kwargs: Any) -> dict[str, Any]:
    from dbt_charts.core.compile.resolve import resolve
    from dbt_charts.core.render.chart.emitters.bar import BarEmitter
    from dbt_charts.core.render.chart.translate import translate_to_vl

    chart = _bar_normalized(layers=layers, **bar_kwargs)
    resolved = resolve(chart, _DATA, _board_style())
    spec = BarEmitter().emit(resolved, _DEFAULT_BOX, regroup((), _DATA))
    return translate_to_vl(spec)


def _render_line_with_layers(
    layers: list[Any],
    rows: list[dict[str, Any]] | None = None,
    **line_kwargs: Any,
) -> dict[str, Any]:
    from dbt_charts.core.compile.resolve import resolve
    from dbt_charts.core.render.chart.emitters.line import LineEmitter
    from dbt_charts.core.render.chart.translate import translate_to_vl

    chart = _line_normalized(layers=layers, **line_kwargs)
    chart_data = _DATA if rows is None else rows
    resolved = resolve(chart, chart_data, _board_style())
    spec = LineEmitter().emit(resolved, _DEFAULT_BOX, regroup((), chart_data))
    return translate_to_vl(spec)


def _render_area_with_layers(
    layers: list[Any],
    rows: list[dict[str, Any]] | None = None,
    **area_kwargs: Any,
) -> dict[str, Any]:
    from dbt_charts.core.compile.resolve import resolve
    from dbt_charts.core.render.chart.emitters.area import AreaEmitter
    from dbt_charts.core.render.chart.translate import translate_to_vl

    chart = _area_normalized(layers=layers, **area_kwargs)
    chart_data = _DATA if rows is None else rows
    resolved = resolve(chart, chart_data, _board_style())
    spec = AreaEmitter().emit(resolved, _DEFAULT_BOX, regroup((), chart_data))
    return translate_to_vl(spec)


def _overlay_legend(vl: dict[str, Any], datum_label: str) -> dict[str, Any] | None:
    """Return the VL legend dict for the overlay layer with the given datum label."""
    for layer in vl.get("layer", []):
        color = layer.get("encoding", {}).get("color", {})
        if color.get("datum") == datum_label:
            return color.get("legend")
    return None


def _field_legend(vl: dict[str, Any]) -> dict[str, Any] | None:
    """Return the VL legend dict for the first layer whose color is field-encoded."""
    for layer in vl.get("layer", []):
        color = layer.get("encoding", {}).get("color", {})
        if color.get("field") is not None:
            leg = color.get("legend")
            return leg if isinstance(leg, dict) else None
    return None


def _field_legend_recursive(vl: dict[str, Any]) -> dict[str, Any] | None:
    """Like `_field_legend`, but descends into a nested `layer[]` entry. A
    dual-axis zero-baseline rule wraps its own entry in an extra
    `mark="layered"` level with no encoding of its own (see
    `nest_zero_rule`), one level deeper than a shallow scan reaches."""
    for layer in vl.get("layer", []):
        color = layer.get("encoding", {}).get("color", {})
        if color.get("field") is not None:
            leg = color.get("legend")
            return leg if isinstance(leg, dict) else None
        nested = _field_legend_recursive(layer)
        if nested is not None:
            return nested
    return None


# ── Solo-mark glyph gating: line overlay → stroke ─────────────────────────


def test_line_overlay_on_bar_base_gets_stroke_glyph() -> None:
    """A line overlay on a bar base gets a stroke symbolType (not filled circle).

    Regression for #206: single-mark overlay must get the mark-aware glyph.
    Line layers use the open-stroke symbol so they read as lines in the legend.
    """
    from dbt_charts.core.compile.models.chart.authored._layer import LineLayer

    layer = LineLayer(type="line", y="target")
    vl = _render_bar_with_layers([layer])

    legend = _overlay_legend(vl, "target")
    assert legend is not None, "Line overlay has no legend"
    symbol_type = legend.get("symbolType")
    assert symbol_type is not None, "Line overlay legend has no symbolType"
    if isinstance(symbol_type, dict):
        assert "stroke" in symbol_type.get("expr", ""), (
            f"symbolType expr does not encode 'stroke': {symbol_type}"
        )
    else:
        assert symbol_type == "stroke"


# ── Solo-mark glyph gating: bar overlay → square ──────────────────────────


def test_bar_overlay_on_bar_base_gets_square_glyph() -> None:
    """A bar overlay on a bar base gets a square symbolType (not VL default circle).

    Regression for #206: bar overlays were never added to stroke_datums or
    circle_datums, so _apply_mark_legend_symbols was never called → bar overlay
    legend fell through to Vega-Lite's default filled-circle symbol.
    """
    from dbt_charts.core.compile.models.chart.authored._layer import (
        BarChartBarLayer,
    )

    layer = BarChartBarLayer(type="bar", y="target")
    vl = _render_bar_with_layers([layer])

    legend = _overlay_legend(vl, "target")
    assert legend is not None, "Bar overlay has no legend"
    symbol_type = legend.get("symbolType")
    assert symbol_type is not None, (
        "Bar overlay legend has no symbolType — falls through to VL default circle"
    )
    if isinstance(symbol_type, dict):
        assert "square" in symbol_type.get("expr", ""), (
            f"symbolType expr does not encode 'square': {symbol_type}"
        )
    else:
        assert symbol_type == "square"


# ── Solo-mark glyph gating: line base + line overlay → else branch is stroke ─


def test_line_base_field_series_get_stroke_not_square() -> None:
    """Line base (field color) + line overlay: base series get 'stroke', not 'square'.

    The symbolType expression's else branch must use the base chart's mark glyph.
    When base=line, the else value must be 'stroke', not the hardcoded 'square'
    that was previously the fallback.
    """
    from dbt_charts.core.compile.models.chart.authored._layer import LineLayer
    from dbt_charts.core.compile.models.style.authored import LineChartStylePatch

    layer = LineLayer(type="line", y="target")
    # Line chart with field-based color → base spec carries a field-legend.
    # endpoint_labels defaults to visible for multi-series lines (stark theme),
    # which suppresses the color legend entirely — disable it so this test
    # can pin the legend-glyph behavior it's actually about.
    style = LineChartStylePatch.model_validate({"endpoint_labels": {"visible": False}})
    vl = _render_line_with_layers([layer], color="cat", style=style)

    base_legend = _field_legend(vl)
    assert base_legend is not None, "No field-color legend found on base line spec"
    symbol_type = base_legend.get("symbolType")
    assert symbol_type is not None, "Base line field-series legend has no symbolType"
    if isinstance(symbol_type, dict):
        expr = symbol_type.get("expr", "")
        # The ternary's else branch (what non-overlay datums get) must be 'stroke',
        # NOT 'square' — the base is a line chart.
        assert not expr.endswith(": 'square'"), (
            f"Base line field-series else-branch is 'square' (should be 'stroke'): {expr!r}"
        )
        assert "stroke" in expr, f"symbolType expr has no 'stroke' at all: {expr!r}"
    else:
        assert symbol_type == "stroke"


# ── Author-facing override: symbol_shape ──────────────────────────────────


def test_author_symbol_shape_override_emits_constant_symbol_type() -> None:
    """legend.symbol_shape author override emits a constant VL symbolType.

    An authored shape must win over any mark-derived expression.
    """
    from dbt_charts.core.compile.models.chart.authored._layer import LineLayer
    from dbt_charts.core.compile.models.style.authored import (
        BarChartStylePatch,
        LegendStylePatch,
    )

    layer = LineLayer(type="line", y="target")
    style = BarChartStylePatch.model_validate(
        {"legend": LegendStylePatch.model_validate({"symbol_shape": "diamond"})}
    )
    vl = _render_bar_with_layers([layer], style=style)

    legend = _overlay_legend(vl, "target")
    assert legend is not None, "Line overlay has no legend"
    symbol_type = legend.get("symbolType")
    assert symbol_type == "diamond", (
        f"Expected constant symbolType='diamond', got: {symbol_type!r}"
    )


# ── Author-facing override: symbol_fill ───────────────────────────────────


def test_author_symbol_fill_false_emits_transparent_fill_color() -> None:
    """legend.symbol_fill=False emits symbolFillColor='transparent' in the VL legend."""
    from dbt_charts.core.compile.models.chart.authored._layer import LineLayer
    from dbt_charts.core.compile.models.style.authored import (
        BarChartStylePatch,
        LegendStylePatch,
    )

    layer = LineLayer(type="line", y="target")
    style = BarChartStylePatch.model_validate(
        {"legend": LegendStylePatch.model_validate({"symbol_fill": False})}
    )
    vl = _render_bar_with_layers([layer], style=style)

    legend = _overlay_legend(vl, "target")
    assert legend is not None, "Line overlay has no legend"
    assert legend.get("symbolFillColor") == "transparent", (
        f"Expected symbolFillColor='transparent', got: {legend.get('symbolFillColor')!r}"
    )


# ── Base series + authored per-layer colors in one shared color scale ──────
#
# Regressions from the type:layered → layers cutover and the per-layer style
# feature (defects #206/#208/#213): the base series must appear in the legend,
# and an authored per-layer mark color must drive BOTH the painted mark and the
# legend swatch (one shared color scale, no palette/authored divergence).


_VDATA: list[dict[str, Any]] = [
    {"date": "2025-01", "actual": 110.0, "target": 120.0},
    {"date": "2025-02", "actual": 125.0, "target": 128.0},
    {"date": "2025-03", "actual": 118.0, "target": 132.0},
]


def _render_vbar_with_layers(layers: list[Any]) -> dict[str, Any]:
    """Render a vertical bar base (temporal x → measure on y) with overlays."""
    from dbt_charts.core.compile.resolve import resolve
    from dbt_charts.core.render.chart.emitters.bar import BarEmitter
    from dbt_charts.core.render.chart.translate import translate_to_vl

    chart = _bar_normalized(layers=layers, x="date", y="actual")
    resolved = resolve(chart, _VDATA, _board_style())
    spec = BarEmitter().emit(resolved, _DEFAULT_BOX, regroup((), _VDATA))
    return translate_to_vl(spec)


def _color_scale(vl: dict[str, Any]) -> dict[str, Any] | None:
    """Return the first explicit datum color scale (domain+range) in the spec."""
    for layer in vl.get("layer", []):
        color = layer.get("encoding", {}).get("color", {})
        scale = color.get("scale")
        if isinstance(scale, dict) and "domain" in scale and "range" in scale:
            return scale
    return None


def _datum_labels(vl: dict[str, Any]) -> set[str]:
    """All color-datum labels across layers (base + overlays)."""
    out: set[str] = set()
    for layer in vl.get("layer", []):
        datum = layer.get("encoding", {}).get("color", {}).get("datum")
        if isinstance(datum, str):
            out.add(datum)
    return out


def test_base_series_appears_in_overlay_legend() -> None:
    """The single-series base gets a color-datum entry so it shows in the legend.

    Regression: the type:layered → layers cutover dropped the base's legend entry
    (only overlay layers got color:{datum}), so a bar+line combo showed only the
    line in the legend, never the bars.
    """
    from dbt_charts.core.compile.models.chart.authored._layer import LineLayer

    layer = LineLayer(type="line", y="target")
    vl = _render_vbar_with_layers([layer])

    labels = _datum_labels(vl)
    assert "actual" in labels, f"Base series missing from legend datums: {labels}"
    assert "target" in labels, f"Overlay series missing from legend datums: {labels}"


def test_authored_layer_color_drives_shared_scale() -> None:
    """An authored per-layer mark color is the series' color in the shared scale.

    The scale range entry for the overlay's datum equals the authored stroke
    color, so the painted line/point AND the legend swatch match (defects
    #208/#213). Without the fix, the swatch used a palette slot while the mark
    used the authored color.
    """
    from dbt_charts.core.compile.models.chart.authored._layer import LineLayer

    layer = LineLayer.model_validate(
        {
            "type": "line",
            "y": "target",
            "style": {
                "marks": {
                    "line": {"stroke": {"width": 3, "color": "#dea628"}},
                    "point": {"size": 48, "color": "#bada55", "filled": False},
                }
            },
        }
    )
    vl = _render_vbar_with_layers([layer])

    scale = _color_scale(vl)
    assert scale is not None, "No explicit shared color scale on overlay spec"
    domain = scale["domain"]
    range_ = scale["range"]
    assert "actual" in domain and "target" in domain, f"domain: {domain}"
    assert range_[domain.index("target")] == "#dea628", (
        f"Target swatch color != authored line color; scale={scale}"
    )
    overlay = next(
        sub
        for sub in vl["layer"]
        if sub.get("encoding", {}).get("color", {}).get("datum") == "target"
    )
    foreground_point = next(
        sub
        for sub in overlay["layer"]
        if sub["mark"]["type"] == "point" and sub["mark"].get("opacity") == 1
    )
    assert foreground_point["encoding"]["stroke"] == {"value": "#bada55"}
    hover_point = next(
        sub
        for sub in overlay["layer"]
        if sub["mark"]["type"] == "point" and sub["mark"].get("opacity") == 0
    )
    assert hover_point["encoding"]["fill"] == {"value": "#dea628"}


def test_authored_area_point_color_wins_over_parent_series_encoding() -> None:
    """An area overlay's explicit point ink is independent of its series fill."""
    from dbt_charts.core.compile.models.chart.authored._layer import AreaLayer

    layer = AreaLayer.model_validate(
        {
            "type": "area",
            "y": "target",
            "style": {
                "marks": {"point": {"size": 48, "color": "#bada55", "filled": False}}
            },
        }
    )
    vl = _render_bar_with_layers([layer])
    overlay = next(
        sub
        for sub in vl["layer"]
        if sub.get("encoding", {}).get("color", {}).get("datum") == "target"
    )
    foreground_point = next(
        sub
        for sub in overlay["layer"]
        if sub["mark"]["type"] == "point" and sub["mark"].get("opacity") == 1
    )
    assert foreground_point["encoding"]["stroke"] == {"value": "#bada55"}


@pytest.mark.parametrize("base_type", ["line", "area"])
def test_standalone_field_colored_point_keeps_explicit_color(base_type: str) -> None:
    """A regular field-colored composite preserves explicit point paint."""
    import vl_convert as vlc

    from dbt_charts.core.compile.models.style.authored import (
        AreaChartStylePatch,
        LineChartStylePatch,
    )

    style_type = LineChartStylePatch if base_type == "line" else AreaChartStylePatch
    style = style_type.model_validate(
        {"marks": {"point": {"size": 48, "color": "#bada55", "filled": False}}}
    )
    render = (
        _render_line_with_layers if base_type == "line" else _render_area_with_layers
    )
    vl = render([], color="cat", style=style)
    synthetic_mark_type = "line" if base_type == "line" else "area"
    synthetic = next(
        sub
        for sub in vl["layer"]
        if sub["mark"]["type"] == synthetic_mark_type
        and sub["mark"].get("tooltip") is False
    )
    paint_channel = "stroke" if base_type == "line" else "fill"
    assert synthetic["encoding"][paint_channel] == {"value": _board_style().background}
    assert synthetic["encoding"]["detail"]["field"] == "cat"
    foreground_point = next(
        sub
        for sub in vl["layer"]
        if sub["mark"]["type"] == "point"
        and sub["mark"].get("opacity") == 1
        and sub["mark"].get("tooltip") is True
    )
    assert foreground_point["encoding"]["stroke"] == {"value": "#bada55"}
    vl["data"] = {"values": _DATA}
    assert "#bada55" in vlc.vegalite_to_svg(vl).lower()


@pytest.mark.parametrize("base_type", ["line", "area"])
def test_standalone_field_color_keeps_composite_paths_partitioned(
    base_type: str,
) -> None:
    """Literal child paint must not connect interleaved field-color series.

    Points pinned off explicitly (line only): this fixture's 2 distinct x
    values sit well inside the density-auto-on trigger (bake_point_
    companions), which would otherwise add point-halo paths of the same
    white stroke this test counts for the line halo, unrelated to what this
    test checks (composite-path partitioning).
    """
    import re

    import vl_convert as vlc

    from dbt_charts.core.compile.models.style.authored import (
        AreaChartStylePatch,
        LineChartStylePatch,
    )

    rows = [
        {"month": "Jan", "revenue": 10.0, "cat": "A"},
        {"month": "Jan", "revenue": 30.0, "cat": "B"},
        {"month": "Feb", "revenue": 20.0, "cat": "A"},
        {"month": "Feb", "revenue": 40.0, "cat": "B"},
    ]
    style_type = LineChartStylePatch if base_type == "line" else AreaChartStylePatch
    marks_patch: dict = {"line": {"stroke": {"color": "#dea628", "width": 3}}}
    if base_type == "line":
        marks_patch["point"] = {"size": 0.0}
    style = style_type.model_validate({"marks": marks_patch})
    render = (
        _render_line_with_layers if base_type == "line" else _render_area_with_layers
    )
    vl = render([], rows=rows, color="cat", style=style)
    vl["data"] = {"values": rows}
    svg = vlc.vegalite_to_svg(vl)
    foreground_paths = re.findall(r'<path\b[^>]*stroke="#dea628"[^>]*>', svg)
    assert len(foreground_paths) == 2
    if base_type == "line":
        halo_paths = re.findall(r'<path\b[^>]*stroke="#FFFFFF"[^>]*>', svg)
        assert len(halo_paths) == 2
        assert all(re.search(r'\bd="[^"]*L[^"]*"', path) for path in foreground_paths)
        assert all(path.count("L") == 1 for path in foreground_paths)
        assert all(path.count("L") == 1 for path in halo_paths)
    else:
        backdrop_paths = re.findall(r'<path\b[^>]*fill="#FFFFFF"[^>]*>', svg)
        assert len(backdrop_paths) == 2
        assert all(path.count("L") == 1 for path in foreground_paths)


@pytest.mark.parametrize(
    ("base_type", "synthetic_mark_type"),
    [("line", "line"), ("area", "area")],
)
def test_shared_emitter_pins_synthetic_paint_but_inherits_field_color(
    base_type: str, synthetic_mark_type: str
) -> None:
    """Composite children declare color ownership before overlay composition.

    Background knockout marks need a literal color encoding that beats the
    base wrapper's field channel. Foreground and hover marks retain no child
    color encoding so each datum inherits its series color.
    """
    from dbt_charts.core.compile.models.chart.authored._layer import LineLayer

    render = (
        _render_line_with_layers if base_type == "line" else _render_area_with_layers
    )
    vl = render([LineLayer(type="line", y="target")], color="cat")
    base = next(
        sub
        for sub in vl["layer"]
        if sub.get("encoding", {}).get("color", {}).get("field") == "cat"
    )
    synthetic = next(
        sub
        for sub in base["layer"]
        if sub["mark"]["type"] == synthetic_mark_type
        and sub["mark"].get("tooltip") is False
    )
    paint_channel = "stroke" if base_type == "line" else "fill"
    assert synthetic["encoding"][paint_channel] == {"value": _board_style().background}
    assert synthetic["encoding"]["detail"]["field"] == "cat"
    hover = next(
        sub
        for sub in base["layer"]
        if sub["mark"]["type"] == "point" and sub["mark"].get("opacity") == 0
    )
    assert "color" not in hover.get("encoding", {})


# ── Merged legend: overlay stroke entry must not out-size the base's own ──


def test_merged_legend_stroke_entries_share_symbol_geometry() -> None:
    """A line base split by ``color:`` plus a line overlay: every legend row that
    resolves to the 'stroke' glyph shares one geometry (path length + stroke
    width), whether the row belongs to the base's color-split series or the
    overlay layer.

    Regression: the overlay's own datum was hard-gated into a wider/thicker
    stroke glyph (20px, stroke-width 2) while the base's field-color series
    stayed at the plain default (10px, stroke-width 1.5) — even though both
    resolve to the same 'stroke' symbolType. The size bump is a property of
    the *shape* ('stroke' needs more length to read as a line), not of
    *which layer emitted the entry*.
    """
    import re

    import vl_convert as vlc

    from dbt_charts.core.compile.models.chart.authored._layer import LineLayer
    from dbt_charts.core.compile.models.style.authored import LineChartStylePatch

    layer = LineLayer(type="line", y="target")
    style = LineChartStylePatch.model_validate({"endpoint_labels": {"visible": False}})
    vl = _render_line_with_layers([layer], color="cat", style=style)
    vl["data"] = {"values": _DATA}
    svg = vlc.vegalite_to_svg(vl)

    glyphs = re.findall(
        r'<path[^>]*d="(M-\d+,0L\d+,0)"[^>]*stroke-width="([\d.]+)"[^>]*/>', svg
    )
    # Every color-split base series (Jan/Feb -> A/B) plus the overlay ("target")
    # legend row draws a stroke glyph — three rows total for this fixture.
    assert len(glyphs) == 3, f"expected 3 stroke legend glyphs, found: {glyphs}"
    paths, widths = zip(*glyphs, strict=True)
    assert len(set(paths)) == 1, f"stroke glyph path lengths differ: {glyphs}"
    assert len(set(widths)) == 1, f"stroke glyph stroke-widths differ: {glyphs}"


# ── One legend, one casing convention for engine-derived names ─────────────

_UNAUTHORED_DATA: list[dict[str, Any]] = [
    {"month": "2025-01", "order_revenue": 110.0, "order_cost_total": 40.0},
    {"month": "2025-02", "order_revenue": 125.0, "order_cost_total": 55.0},
]


def _render_unauthored_overlay() -> tuple[dict[str, Any], Any, list[dict[str, Any]]]:
    """Bar base + line overlay with NOTHING authored: no y_label, no layer
    label, no color field. Both legend entries are engine-derived, so both
    must follow the same naming rule.

    Multi-word fields on both sides deliberately — a single-word field cannot
    tell ``default_axis_title`` and ``format_display_text`` apart, which is
    how the mixed-convention bug survived the existing tests.
    """
    from dbt_charts.core.compile.models.chart.authored._layer import LineLayer
    from dbt_charts.core.compile.resolve import resolve
    from dbt_charts.core.render.chart.emitters.bar import BarEmitter
    from dbt_charts.core.render.chart.translate import translate_to_vl

    chart = _bar_normalized(
        x="month",
        y="order_revenue",
        layers=[LineLayer(type="line", y="order_cost_total")],
    )
    resolved = resolve(chart, _UNAUTHORED_DATA, _board_style())
    spec = BarEmitter().emit(resolved, _DEFAULT_BOX, regroup((), _UNAUTHORED_DATA))
    return translate_to_vl(spec), resolved, _UNAUTHORED_DATA


def test_unauthored_base_and_layer_share_one_naming_convention() -> None:
    """A layered chart whose base and overlay are both engine-named must not
    put two casing conventions in one legend.

    Default names derive from the bound column and keep its casing, so an
    unauthored layer reads 'order cost total', not 'Order Cost Total', beside
    the base's 'order revenue'. An authored ``label:`` is a separate matter —
    it passes through untouched, exactly like an authored ``y_label``.
    """
    vl, _resolved, _data = _render_unauthored_overlay()
    domain = _color_scale(vl)
    assert domain is not None, "no shared color scale on the overlay spec"
    names = list(domain["domain"])
    assert names == ["order revenue", "order cost total"], (
        f"base and overlay must share one convention; got {names}"
    )


def test_rail_width_estimate_names_match_the_rendered_names() -> None:
    """``_estimated_series_names`` documents itself as mirroring the feature's
    own derivation. If it names the base differently from the string actually
    drawn, it measures a label the chart never shows and the rail is budgeted
    against the wrong text.
    """
    from dbt_charts.core.render.chart.emitters._endpoint_rail import (
        _estimated_series_names,
    )

    vl, resolved, data = _render_unauthored_overlay()
    domain = _color_scale(vl)
    assert domain is not None
    assert _estimated_series_names(resolved, data) == list(domain["domain"]), (
        "rail width estimate must name exactly what the spec names"
    )


def test_dual_axis_base_color_split_legend_gets_glyph_through_nested_zero_rule() -> (
    None
):
    """A dual-axis bar base whose own zero rule nests it inside a wrapper
    ChartSpec (see `nest_zero_rule`) must still get its legend glyph patched.
    `_mixed_mark_legend_symbols` must find the base's own color encoding
    through the wrapper, not only at the top level."""
    from dbt_charts.core.compile.models.chart.authored._layer import LineLayer

    layer = LineLayer(type="line", y="target", axis_y={"position": "right"})
    vl = _render_bar_with_layers([layer], color="cat")

    legend = _field_legend_recursive(vl)
    assert legend is not None, "bar base's color-split legend not found"
    symbol_type = legend.get("symbolType")
    assert symbol_type is not None, (
        "bar base's legend glyph was not patched: _mixed_mark_legend_symbols "
        "must find the base's encoding through nest_zero_rule's wrapper"
    )
