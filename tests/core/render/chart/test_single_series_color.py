"""Tests for theme-driven single-series mark color.

A non-layered chart with no color encoding paints its mark with the
theme's `style.charts.single_series_palette[0]`. Chart-level `style.color`
still wins. Layered and multi-metric charts switch back to the
categorical palette (color flows through encoding.color).
"""

from __future__ import annotations

from typing import Any

import pytest

from dbt_charts.core.compile.config import (
    get_theme_style,
    reset_config,
)
from dbt_charts.core.compile.models.chart.normalized import (
    AreaChart,
    BarChart,
    Chart,
    LineChart,
    PointMapChart,
)
from dbt_charts.core.compile.models.query.normalized import SqlQuery
from dbt_charts.core.compile.models.style.authored import BarChartStylePatch
from dbt_charts.core.compile.models.style.context import ChartStyleContext
from dbt_charts.core.compile.models.style.resolved import ResolvedStyle
from dbt_charts.core.compile.resolve import resolve
from dbt_charts.core.compile.resolve.style.board import (
    resolve_chart_style_context,
    resolve_style,
    resolve_style_and_context,
)
from dbt_charts.core.render.chart.vega_lite import generate_vega_lite_spec

_BOARD_STYLE = resolve_chart_style_context(get_theme_style())

_QUERY = SqlQuery(sql="SELECT 1", source="test")
_DATA = [{"month": "Jan", "revenue": 100}, {"month": "Feb", "revenue": 200}]
_MULTI_DATA = [
    {"month": "Jan", "category": "A", "revenue": 100},
    {"month": "Jan", "category": "B", "revenue": 200},
    {"month": "Feb", "category": "A", "revenue": 150},
    {"month": "Feb", "category": "B", "revenue": 250},
]
_MULTI_METRIC_DATA = [
    {"month": "Jan", "revenue": 100, "target": 120},
    {"month": "Feb", "revenue": 200, "target": 180},
]


def _bar(**kwargs: Any) -> Chart:
    defaults = {
        "id": "t",
        "type": "bar",
        "query": _QUERY,
        "query_name": "q",
        "x": "month",
        "y": "revenue",
    }
    defaults.update(kwargs)
    return BarChart(**defaults)


def _mark(spec: dict[str, Any]) -> dict[str, Any]:
    m = spec.get("mark", {})
    if isinstance(m, dict) and m:
        return m
    layers = spec.get("layer", [])
    return layers[0].get("mark", {}) if layers else {}


@pytest.fixture(autouse=True)
def _reset():  # pyright: ignore[reportUnusedFunction]
    reset_config()
    yield
    reset_config()


# ── Theme corpus: every shipped theme populates single_series_palette ──────────


@pytest.mark.parametrize(
    "theme_name",
    ["stark", "clarity", "paper", "vivid", "neon"],
)
def test_every_shipped_theme_populates_single_series_palette(theme_name):
    """The theme corpus smoke test — every built-in theme resolves a
    non-empty single_series_palette via the cascade."""
    charts = resolve_style(get_theme_style(theme_name)).chart_defaults
    assert isinstance(charts.single_series_palette, list)
    assert len(charts.single_series_palette) >= 1, (
        f"theme {theme_name!r} has empty single_series_palette"
    )
    assert all(
        isinstance(stop, str) and stop.strip() for stop in charts.single_series_palette
    )


# ── Render path: single-series mark gets the theme value ─────────────────────


@pytest.mark.parametrize("theme_name", ["stark", "clarity", "paper", "neon"])
def test_single_series_bar_uses_theme_single_series_palette(
    monkeypatch: pytest.MonkeyPatch, theme_name: str
) -> None:
    """Non-layered, single-encoding bar chart paints mark.fill from the
    theme's single_series_palette, not palette[0]."""
    monkeypatch.setenv("DCT_DEFAULT_THEME", theme_name)
    expected = resolve_style(
        get_theme_style(theme_name)
    ).chart_defaults.single_series_palette[0]
    _rc = resolve(_bar(), _DATA, chart_style_context=_BOARD_STYLE)
    spec = generate_vega_lite_spec(_bar(), _DATA)
    assert _mark(spec).get("fill") == expected


def _find_layer_with_stroke(
    spec: dict[str, Any], exclude_strokes: set[str]
) -> dict[str, Any]:
    """Return the first layer whose mark.stroke is set to a value not in
    exclude_strokes. Used to find the fg line layer in halo-layered specs
    (halo layers stroke with the background color; fg layer carries the
    real ink)."""
    for layer in spec.get("layer", []):
        if not isinstance(layer, dict):
            continue
        m = layer.get("mark", {})
        if not isinstance(m, dict):
            continue
        s = m.get("stroke")
        if s is not None and s not in exclude_strokes:
            return m
    return {}


@pytest.mark.parametrize("theme_name", ["stark", "clarity", "paper", "neon"])
def test_single_series_line_uses_theme_single_series_palette(
    monkeypatch: pytest.MonkeyPatch, theme_name: str
) -> None:
    """Single-series line chart's foreground stroke matches the theme's
    single_series_palette[0]. Halo layers stroke with the canvas, but the
    fg layer carries the ink."""
    monkeypatch.setenv("DCT_DEFAULT_THEME", theme_name)
    charts = resolve_style(get_theme_style(theme_name)).chart_defaults
    expected = charts.single_series_palette[0]
    # Halo layers now stroke with the composited canvas (ink_canvas), not the
    # raw background field -- the two can differ in hex case (ink_canvas
    # always lowercases; background preserves authored casing).
    canvas = charts.ink_canvas

    chart = LineChart(
        id="t",
        type="line",
        query=_QUERY,
        query_name="q",
        x="month",
        y="orders",
    )
    spec = generate_vega_lite_spec(
        chart, [{"month": "Jan", "orders": 100}, {"month": "Feb", "orders": 200}]
    )
    fg = _find_layer_with_stroke(spec, exclude_strokes={canvas})
    assert fg.get("stroke") == expected, (
        f"expected fg stroke {expected!r} for {theme_name}, got {fg.get('stroke')!r}"
    )


@pytest.mark.parametrize("theme_name", ["stark", "clarity", "paper", "neon"])
def test_single_series_area_uses_theme_single_series_palette(
    monkeypatch: pytest.MonkeyPatch, theme_name: str
) -> None:
    """Single-series area chart's foreground fill matches the theme's
    single_series_palette[0]."""
    monkeypatch.setenv("DCT_DEFAULT_THEME", theme_name)
    charts = resolve_style(get_theme_style(theme_name)).chart_defaults
    expected = charts.single_series_palette[0]
    # Halo layers now fill with the composited canvas (ink_canvas), not the
    # raw background field -- see the line-chart test above for why.
    canvas = charts.ink_canvas

    chart = AreaChart(
        id="t",
        type="area",
        query=_QUERY,
        query_name="q",
        x="month",
        y="orders",
    )
    spec = generate_vega_lite_spec(
        chart, [{"month": "Jan", "orders": 100}, {"month": "Feb", "orders": 200}]
    )
    # Find fg area-fill layer (halo fills with background)
    fg_area_fill = None
    for layer in spec.get("layer", []):
        if not isinstance(layer, dict):
            continue
        m = layer.get("mark", {})
        if m.get("type") == "area" and m.get("fill") not in (None, canvas):
            fg_area_fill = m
            break
    assert fg_area_fill is not None, (
        f"expected single-series fg area fill for {theme_name}"
    )
    assert fg_area_fill.get("fill") == expected


# ── Override: chart-level style.color still wins ─────────────────────────────


def test_chart_level_style_color_overrides_single_series_palette(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Authored chart-local `style.color` wins over the theme default."""
    monkeypatch.setenv("DCT_DEFAULT_THEME", "clarity")
    chart = _bar(
        style=BarChartStylePatch.model_validate({"color": {"static": "#abc123"}})
    )
    _rc = resolve(chart, _DATA, chart_style_context=_BOARD_STYLE)
    spec = generate_vega_lite_spec(chart, _DATA)
    assert _mark(spec).get("fill") == "#abc123"


def test_chart_level_palette_override_beats_single_series_palette(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Authored chart-local `style.<family>.palette` wins over the theme's
    single_series_palette — when an author commits to a palette, palette[0]
    expresses their intent."""
    from dbt_charts.core.compile.resolve.style.palette import palette as resolve_palette

    monkeypatch.setenv("DCT_DEFAULT_THEME", "clarity")
    chart = _bar(
        style=BarChartStylePatch.model_validate(
            {"color": {"categorical": {"palette": "dbt-seq-rust"}}}
        )
    )
    _rc = resolve(chart, _DATA, chart_style_context=_BOARD_STYLE)
    spec = generate_vega_lite_spec(chart, _DATA)
    assert _mark(spec).get("fill") == resolve_palette("dbt-seq-rust")[0]


# ── Multi-series: encoding owns color, single_series_palette suppressed ────────


def test_multi_series_chart_has_no_single_series_fill(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """When a color encoding is present, the VL color scale owns mark
    color and mark.fill must not be set to single_series_palette."""
    monkeypatch.setenv("DCT_DEFAULT_THEME", "clarity")
    ssc = resolve_style(
        get_theme_style("clarity")
    ).chart_defaults.single_series_palette[0]
    chart = _bar(color="category")
    _rc = resolve(chart, _MULTI_DATA, chart_style_context=_BOARD_STYLE)
    spec = generate_vega_lite_spec(chart, _MULTI_DATA)
    assert _mark(spec).get("fill") != ssc


def test_multi_series_line_with_color_encoding_does_not_collapse_to_single_ink(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A line chart with `color: <field>` (multi-series via encoding) must
    NOT have every foreground line stroke set to single_series_palette. The
    halo path's single-series fallback must yield to the color encoding.
    Regression for the dundersign-commercial-finance Win Rate chart bug."""
    monkeypatch.setenv("DCT_DEFAULT_THEME", "clarity")
    ssc = resolve_style(
        get_theme_style("clarity")
    ).chart_defaults.single_series_palette[0]
    chart = LineChart(
        id="t",
        type="line",
        query=_QUERY,
        query_name="q",
        x="month",
        y="rate",
        color="source",
    )
    data = [
        {"month": "Jan", "source": "A", "rate": 0.1},
        {"month": "Jan", "source": "B", "rate": 0.2},
        {"month": "Feb", "source": "A", "rate": 0.15},
        {"month": "Feb", "source": "B", "rate": 0.25},
    ]
    _rc = resolve(chart, data, chart_style_context=_BOARD_STYLE)
    spec = generate_vega_lite_spec(chart, data)
    # Every layer's mark stroke (when present) must NOT be the single_series_palette.
    # The encoding-driven color scale owns per-series color.
    for layer in spec.get("layer", []):
        if not isinstance(layer, dict):
            continue
        stroke = layer.get("mark", {}).get("stroke")
        assert stroke != ssc, (
            f"multi-series line layer must not collapse to single_series_palette {ssc!r}"
        )


def test_multi_series_area_with_color_encoding_does_not_collapse_to_single_ink(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Area chart variant of the same bug — multi-series areas must not
    paint every fg fill with single_series_palette."""
    monkeypatch.setenv("DCT_DEFAULT_THEME", "clarity")
    ssc = resolve_style(
        get_theme_style("clarity")
    ).chart_defaults.single_series_palette[0]
    chart = AreaChart(
        id="t",
        type="area",
        query=_QUERY,
        query_name="q",
        x="month",
        y="rate",
        color="source",
    )
    data = [
        {"month": "Jan", "source": "A", "rate": 0.1},
        {"month": "Jan", "source": "B", "rate": 0.2},
    ]
    _rc = resolve(chart, data, chart_style_context=_BOARD_STYLE)
    spec = generate_vega_lite_spec(chart, data)
    for layer in spec.get("layer", []):
        if not isinstance(layer, dict):
            continue
        mark = layer.get("mark", {})
        assert mark.get("fill") != ssc, (
            f"multi-series area layer must not collapse to single_series_palette {ssc!r}"
        )


# ── Layered (multi-metric): switches back to multi-series mode ───────────────


# ── Rhythm-palette slot: ResolvedChart.rhythm_slot picks the palette slot ──


def test_rhythm_slot_picks_indexed_palette_stop(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A non-None rhythm_slot makes the render reach into the right stop
    of single_series_palette, not slot 0. Pinned at render to confirm the
    Chart → ResolvedChart slot field reaches _effective_single_series_fill.

    Uses ``paper``, not ``clarity``: clarity's single_series_palette is one
    fixed ink (no rotation to pick a slot from); paper keeps its warm
    three-ink rotation."""
    monkeypatch.setenv("DCT_DEFAULT_THEME", "paper")
    palette = resolve_style(
        get_theme_style("paper")
    ).chart_defaults.single_series_palette
    assert len(palette) >= 2, "paper palette must have multiple stops for this test"
    chart = _bar()
    chart.rhythm_slot = 1
    _rc = resolve(chart, _DATA, chart_style_context=_BOARD_STYLE)
    spec = generate_vega_lite_spec(chart, _DATA)
    assert _mark(spec).get("fill") == palette[1]


def test_rhythm_slot_wraps_past_palette_length(monkeypatch: pytest.MonkeyPatch) -> None:
    """slot >= len(palette) wraps modulo palette length.

    Uses ``paper``, not ``clarity``: clarity's single_series_palette is one
    fixed ink, so modulo and clamping are indistinguishable there. Same
    reasoning as its two siblings above."""
    monkeypatch.setenv("DCT_DEFAULT_THEME", "paper")
    palette = resolve_style(
        get_theme_style("paper")
    ).chart_defaults.single_series_palette
    assert len(palette) >= 2, "paper palette must have multiple stops for this test"
    chart = _bar()
    chart.rhythm_slot = len(palette)  # wraps back to slot 0
    _rc = resolve(chart, _DATA, chart_style_context=_BOARD_STYLE)
    spec = generate_vega_lite_spec(chart, _DATA)
    assert _mark(spec).get("fill") == palette[0]


def test_multi_metric_layered_chart_does_not_use_single_series_palette(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Layered or multi-metric charts switch back to multi-series mode —
    no dict-mark layer should be painted with the theme's single_series_palette.

    V2 emits string marks ("bar") for simple bar layers; only dict marks can
    carry a literal fill.  Checking isinstance guards against calling .get() on
    a string while still catching any dict-mark layer that accidentally bakes SSC.
    """
    monkeypatch.setenv("DCT_DEFAULT_THEME", "clarity")
    ssc = resolve_style(
        get_theme_style("clarity")
    ).chart_defaults.single_series_palette[0]
    chart = _bar(y=["revenue", "target"])
    _rc = resolve(chart, _MULTI_METRIC_DATA, chart_style_context=_BOARD_STYLE)
    spec = generate_vega_lite_spec(chart, _MULTI_METRIC_DATA)
    layers = spec.get("layer", [])
    assert layers, "expected layered output for multi-metric y"
    for i, layer in enumerate(layers):
        if not isinstance(layer, dict):
            continue
        mark = layer.get("mark")
        if not isinstance(mark, dict):
            continue
        assert mark.get("fill") != ssc, (
            f"layer {i} should not be painted with single_series_palette {ssc!r}"
        )


def test_multi_metric_layered_line_does_not_bake_literal_stroke_over_color_encoding(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Regression: multi-metric (``y: [a, b]``) line must use a top-level
    field-based color encoding; the foreground line layer must not carry a
    literal ``mark.stroke`` that would shadow VL's inherited color.

    The fold-based approach puts the color encoding at the top level. The halo
    sub-layer carries a literal background-colored stroke intentionally (it is
    a knockout mask). The foreground line must not — a literal stroke there
    collapses all metrics to one color."""
    monkeypatch.setenv("DCT_DEFAULT_THEME", "clarity")
    chart = LineChart(
        id="t",
        type="line",
        query=_QUERY,
        query_name="q",
        x="month",
        y=["revenue", "target"],
    )
    _rc = resolve(chart, _MULTI_METRIC_DATA, chart_style_context=_BOARD_STYLE)
    spec = generate_vega_lite_spec(chart, _MULTI_METRIC_DATA)
    # The fold path puts color at the top level. With endpoint labels (clarity
    # theme default) the spec is wrapped in an hconcat; unwrap to the unit spec.
    unit = spec.get("hconcat", [spec])[0]
    top_color = unit.get("encoding", {}).get("color", {})
    assert top_color.get("field") == "__dbt_charts_wide_label__", (
        f"fold-based multi-metric line must use a field-based top-level color; "
        f"got {top_color!r}"
    )
    # The foreground line (tooltip=True) must not carry a literal mark.stroke.
    # (The halo layer intentionally carries a background stroke; that's excluded
    # by the tooltip filter below.)
    fg_line_layers = [
        layer
        for layer in unit.get("layer", [])
        if isinstance(layer, dict)
        and isinstance(layer.get("mark"), dict)
        and layer["mark"].get("type") == "line"
        and layer["mark"].get("tooltip") is True
    ]
    assert fg_line_layers, "Expected at least one foreground line layer"
    for layer in fg_line_layers:
        stroke = layer["mark"].get("stroke")
        assert stroke is None, (
            f"foreground line must not carry a literal mark.stroke — VL "
            f"prioritizes it over the inherited encoding.color, collapsing all "
            f"metrics to one color: {layer['mark']!r}"
        )


def test_multi_metric_layered_area_does_not_bake_literal_fill_over_color_encoding(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Area chart variant of the same bug — a literal ``mark.fill`` baked
    identically onto every metric layer must not shadow that layer's own
    ``encoding.color``."""
    monkeypatch.setenv("DCT_DEFAULT_THEME", "clarity")
    chart = AreaChart(
        id="t",
        type="area",
        query=_QUERY,
        query_name="q",
        x="month",
        y=["revenue", "target"],
    )
    _rc = resolve(chart, _MULTI_METRIC_DATA, chart_style_context=_BOARD_STYLE)
    spec = generate_vega_lite_spec(chart, _MULTI_METRIC_DATA)
    unit = spec.get("hconcat", [spec])[0]
    color = unit["encoding"]["color"]
    assert color["type"] == "nominal"
    assert color["field"] == "__dbt_charts_wide_label__"
    area_layers = [
        layer
        for layer in unit["layer"]
        if layer.get("mark", {}).get("type") == "area"
        and layer["mark"].get("tooltip") is True
    ]
    assert len(area_layers) == 1
    assert area_layers[0]["mark"].get("fill") is None


# ── Line + visible-point overlay: dot color tracks the line ─────────────────
#
# Regression for the bug where the fg-point on a single-series line chart
# (halo path) fell back to ``config.range.category[0]`` because the chart
# has no ``encoding.color`` for VL to inherit from. The renderer injects
# ``mark.color = single_color`` on the fg-point; VL's color shortcut routes
# the value via the ``filled`` boolean — to fill when filled=true (the
# theme default → solid dot in line color), or to stroke when filled=false
# (knockout opt-in → ring in line color, theme's ``point.fill:
# theme.background`` paints the board).


_LINE_DATA = [{"month": "Jan", "orders": 100}, {"month": "Feb", "orders": 200}]


def _board_with_visible_points(
    theme_name: str = "clarity", size: float = 40.0, **point_overrides: Any
):
    """Resolve a board with ``style.line.marks.point.size > 0`` so the
    halo-path fg-point layer is emitted. Mirrors
    ``test_markstyles_renderer_wiring._board_with_point_override``.

    Returns (ResolvedStyle, ChartStyleContext) — unpack as ``rs, ctx``."""
    compiled = get_theme_style(theme_name)
    line_style = compiled.charts.line
    family_point = line_style.marks.point or compiled.charts.marks.point
    new_point = family_point.model_copy(update={"size": size, **point_overrides})
    updated_marks = line_style.marks.model_copy(update={"point": new_point})
    updated_line = line_style.model_copy(update={"marks": updated_marks})
    charts = compiled.charts.model_copy(update={"line": updated_line})
    return resolve_style_and_context(compiled.model_copy(update={"charts": charts}))


def _fg_point_layer(spec: dict[str, Any]) -> dict[str, Any]:
    """Return the visible foreground point mark (opacity > 0, tooltip=True)."""
    for layer in spec.get("layer", []):
        m = layer.get("mark", {}) if isinstance(layer, dict) else {}
        if not isinstance(m, dict):
            continue
        if (
            m.get("type") == "point"
            and m.get("opacity", 0) > 0
            and m.get("tooltip") is True
        ):
            return m
    return {}


@pytest.mark.parametrize("theme_name", ["stark", "clarity", "paper", "neon"])
def test_single_series_line_with_points_color_matches_line(
    monkeypatch: pytest.MonkeyPatch, theme_name: str
) -> None:
    """The fg-point mark must carry ``color = single_series_palette[slot]``
    so VL routes it onto the dot. With theme default ``filled: true`` that
    paints the board (solid dot in line color); with ``filled: false`` it
    would paint the ring stroke and the theme's ``point.fill`` paints the
    knockout board.

    Pinned across themes because the bug had the fg-point fall back to
    ``config.range.category[0]`` regardless of theme.
    """
    monkeypatch.setenv("DCT_DEFAULT_THEME", theme_name)
    board_rs, board_ctx = _board_with_visible_points(theme_name)
    expected = resolve_style(
        get_theme_style(theme_name)
    ).chart_defaults.single_series_palette[0]
    chart = LineChart(
        id="t",
        type="line",
        query=_QUERY,
        query_name="q",
        x="month",
        y="orders",
    )
    _rc = resolve(chart, _LINE_DATA, chart_style_context=board_ctx)
    spec = generate_vega_lite_spec(
        chart, _LINE_DATA, board_style=board_rs, chart_style_context=board_ctx
    )
    fg_pt = _fg_point_layer(spec)
    assert fg_pt.get("color") == expected, (
        f"expected fg point color {expected!r} for {theme_name}, got {fg_pt!r}"
    )


def test_single_series_line_with_points_rhythm_slot_reaches_dot(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The fg-point's color must follow the chart's ``rhythm_slot``, not
    slot 0 — confirms the rhythm allocator's choice reaches the point
    overlay (the bug ignored the slot).

    Uses ``paper``, not ``clarity``: clarity's single_series_palette is one
    fixed ink with no rotation to pick a slot from."""
    monkeypatch.setenv("DCT_DEFAULT_THEME", "paper")
    board_rs, board_ctx = _board_with_visible_points("paper")
    palette = resolve_style(
        get_theme_style("paper")
    ).chart_defaults.single_series_palette
    assert len(palette) >= 2, "paper palette must have multiple stops"
    chart = LineChart(
        id="t",
        type="line",
        query=_QUERY,
        query_name="q",
        x="month",
        y="orders",
    )
    chart.rhythm_slot = 1
    _rc = resolve(chart, _LINE_DATA, chart_style_context=board_ctx)
    spec = generate_vega_lite_spec(
        chart, _LINE_DATA, board_style=board_rs, chart_style_context=board_ctx
    )
    fg_pt = _fg_point_layer(spec)
    assert fg_pt.get("color") == palette[1]


def test_line_points_authored_point_color_wins_over_single_series_ink(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Authored ``point.color`` (whole-dot override) wins outright: the
    injection's ``"color" not in fg_point_mark`` guard defers when the
    author has expressed a point-level color preference.

    Regression for the paper-themed doc fence that authors
    ``point.color: "#7c2d12"``.
    """
    monkeypatch.setenv("DCT_DEFAULT_THEME", "paper")
    board_rs, board_ctx = _board_with_visible_points("paper", color="#7c2d12")
    chart = LineChart(
        id="t",
        type="line",
        query=_QUERY,
        query_name="q",
        x="month",
        y="orders",
    )
    _rc = resolve(chart, _LINE_DATA, chart_style_context=board_ctx)
    spec = generate_vega_lite_spec(
        chart, _LINE_DATA, board_style=board_rs, chart_style_context=board_ctx
    )
    fg_pt = _fg_point_layer(spec)
    assert fg_pt.get("color") == "#7c2d12", (
        f"authored point.color must reach fg point, got {fg_pt!r}"
    )


# ── Emitters that used to drop the resolved ink (VL default #4c78a8) ──────────
#
# Histogram, point/bubble map and the area hover-target point each emitted a
# mark with no fill, so Vega-Lite painted its own stock blue. These pin that the
# resolved value reaches the emitted spec, using a distinctive palette rather
# than a theme literal so a palette retune can't silently satisfy them.

_DISTINCTIVE_INK = "#7f00ff"


def _board_with_single_series_ink(
    ink: str = _DISTINCTIVE_INK,
) -> tuple[ResolvedStyle, ChartStyleContext]:
    """Resolve a board style whose single_series_palette is one distinctive stop."""
    compiled = get_theme_style()
    color = compiled.charts.color
    categorical = color.categorical.model_copy(update={"single_series_palette": [ink]})
    charts = compiled.charts.model_copy(
        update={"color": color.model_copy(update={"categorical": categorical})}
    )
    return resolve_style_and_context(compiled.model_copy(update={"charts": charts}))


def _histogram(**kwargs: Any) -> Chart:
    defaults: dict[str, Any] = {
        "id": "t",
        "type": "histogram",
        "query": _QUERY,
        "query_name": "q",
        "x": "revenue",
    }
    defaults.update(kwargs)
    return BarChart(**defaults)


_HISTOGRAM_DATA = [
    {"revenue": float(n), "region": "N" if n % 2 else "S"} for n in range(30)
]
_POINT_MAP_DATA = [
    {"latitude": 34.05, "longitude": -118.24, "units": 5, "kind": "flagship"},
    {"latitude": 40.71, "longitude": -74.01, "units": 9, "kind": "outlet"},
]


def _point_map(**kwargs: Any) -> PointMapChart:
    defaults: dict[str, Any] = {
        "id": "t",
        "type": "point_map",
        "query": _QUERY,
        "query_name": "q",
        "latitude": "latitude",
        "longitude": "longitude",
    }
    defaults.update(kwargs)
    return PointMapChart(**defaults)


def test_single_series_histogram_mark_carries_resolved_ink() -> None:
    """A histogram with no color channel paints mark.fill from the resolved
    single_series_fill. Without it Vega-Lite falls back to its own default."""
    board_rs, board_ctx = _board_with_single_series_ink()
    chart = _histogram()
    expected = resolve(
        chart, _HISTOGRAM_DATA, chart_style_context=board_ctx
    ).style.single_series_fill
    spec = generate_vega_lite_spec(
        chart, _HISTOGRAM_DATA, board_style=board_rs, chart_style_context=board_ctx
    )
    assert _mark(spec).get("fill") == expected


def test_histogram_with_color_channel_leaves_fill_to_the_encoding() -> None:
    """A color channel owns the ink — the mark must not be pinned to the
    single-series value, which would flatten every group to one color."""
    board_rs, board_ctx = _board_with_single_series_ink()
    chart = _histogram(color="region")
    ink = resolve(
        chart, _HISTOGRAM_DATA, chart_style_context=board_ctx
    ).style.single_series_fill
    spec = generate_vega_lite_spec(
        chart, _HISTOGRAM_DATA, board_style=board_rs, chart_style_context=board_ctx
    )
    assert _mark(spec).get("fill") != ink


def test_single_series_point_map_mark_carries_resolved_ink() -> None:
    """A point map with no color channel paints its circles with the resolved
    single_series_fill."""
    board_rs, board_ctx = _board_with_single_series_ink()
    chart = _point_map()
    expected = resolve(
        chart, _POINT_MAP_DATA, chart_style_context=board_ctx
    ).style.single_series_fill
    spec = generate_vega_lite_spec(
        chart, _POINT_MAP_DATA, board_style=board_rs, chart_style_context=board_ctx
    )
    assert _mark(spec).get("fill") == expected


def test_single_series_bubble_map_mark_carries_resolved_ink() -> None:
    """bubble_map shares the point-map emitter: a size encoding with no color
    channel is still single-series and must carry the resolved ink."""
    board_rs, board_ctx = _board_with_single_series_ink()
    chart = _point_map(type="bubble_map", size="units")
    expected = resolve(
        chart, _POINT_MAP_DATA, chart_style_context=board_ctx
    ).style.single_series_fill
    spec = generate_vega_lite_spec(
        chart, _POINT_MAP_DATA, board_style=board_rs, chart_style_context=board_ctx
    )
    assert _mark(spec).get("fill") == expected


def test_point_map_with_color_channel_leaves_fill_to_the_encoding() -> None:
    """As with the histogram, an explicit color channel owns the ink."""
    board_rs, board_ctx = _board_with_single_series_ink()
    chart = _point_map(color="kind")
    ink = resolve(
        chart, _POINT_MAP_DATA, chart_style_context=board_ctx
    ).style.single_series_fill
    spec = generate_vega_lite_spec(
        chart, _POINT_MAP_DATA, board_style=board_rs, chart_style_context=board_ctx
    )
    assert _mark(spec).get("fill") != ink


def _hover_target_point_layer(spec: dict[str, Any]) -> dict[str, Any]:
    """Return the invisible large-target hover point an area chart layers on top."""
    for layer in spec.get("layer", []):
        m = layer.get("mark", {}) if isinstance(layer, dict) else {}
        if not isinstance(m, dict):
            continue
        if m.get("type") == "point" and m.get("opacity") == 0:
            return m
    return {}


def test_single_series_area_hover_point_carries_resolved_ink() -> None:
    """The area chart's hover-target point is invisible but still emitted; it
    must not carry Vega-Lite's default ink, which is what put #4c78a8 in the
    DOM of an otherwise correctly-inked area chart."""
    board_rs, board_ctx = _board_with_single_series_ink()
    chart = AreaChart(
        id="t", type="area", query=_QUERY, query_name="q", x="month", y="revenue"
    )
    expected = resolve(
        chart, _DATA, chart_style_context=board_ctx
    ).style.single_series_fill
    spec = generate_vega_lite_spec(
        chart, _DATA, board_style=board_rs, chart_style_context=board_ctx
    )
    hover = _hover_target_point_layer(spec)
    assert hover, "expected an invisible hover-target point layer"
    assert hover.get("fill") == expected


def test_multi_series_area_hover_point_leaves_fill_to_the_encoding() -> None:
    """With a color encoding the hover target inherits the series color."""
    board_rs, board_ctx = _board_with_single_series_ink()
    chart = AreaChart(
        id="t",
        type="area",
        query=_QUERY,
        query_name="q",
        x="month",
        y="revenue",
        color="category",
    )
    ink = resolve(
        chart, _MULTI_DATA, chart_style_context=board_ctx
    ).style.single_series_fill
    spec = generate_vega_lite_spec(
        chart, _MULTI_DATA, board_style=board_rs, chart_style_context=board_ctx
    )
    assert _hover_target_point_layer(spec).get("fill") != ink


def test_single_series_line_hover_point_carries_resolved_ink() -> None:
    """The line chart layers the same invisible hover-target point as area, and
    it leaked the same Vega-Lite default."""
    board_rs, board_ctx = _board_with_single_series_ink()
    chart = LineChart(
        id="t", type="line", query=_QUERY, query_name="q", x="month", y="revenue"
    )
    expected = resolve(
        chart, _DATA, chart_style_context=board_ctx
    ).style.single_series_fill
    spec = generate_vega_lite_spec(
        chart, _DATA, board_style=board_rs, chart_style_context=board_ctx
    )
    hover = _hover_target_point_layer(spec)
    assert hover, "expected an invisible hover-target point layer"
    assert hover.get("fill") == expected


def test_multi_series_line_hover_point_leaves_fill_to_the_encoding() -> None:
    """With a color encoding the hover target inherits the series color."""
    board_rs, board_ctx = _board_with_single_series_ink()
    chart = LineChart(
        id="t",
        type="line",
        query=_QUERY,
        query_name="q",
        x="month",
        y="revenue",
        color="category",
    )
    ink = resolve(
        chart, _MULTI_DATA, chart_style_context=board_ctx
    ).style.single_series_fill
    spec = generate_vega_lite_spec(
        chart, _MULTI_DATA, board_style=board_rs, chart_style_context=board_ctx
    )
    assert _hover_target_point_layer(spec).get("fill") != ink
