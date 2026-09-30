"""TDD tests for area chart halo knockout (stroke halo on top edge).

After the fill/stroke split, _map_area emits separate area fill layers and
line stroke layers. The layer structure is:
  With halo:    [halo_fill, halo_line, fg_fill, fg_line, hover_overlay]
  Without halo: [area_fill, fg_line, hover_overlay]

The halo_line mark carries the background-color stroke (wider than fg_line).
"""

from __future__ import annotations

from dbt_charts.core.compile.config import get_theme_style
from dbt_charts.core.compile.resolve import resolve
from dbt_charts.core.compile.resolve.style.board import (
    resolve_chart_style_context,
    resolve_style_and_context,
)
from dbt_charts.core.render.chart.vega_lite import render_resolved_chart

_BOARD_STYLE, _BOARD_CTX = resolve_style_and_context(get_theme_style())


def _make_area_chart(
    make_chart,
    multi_series: bool = True,
    halo_multiplier: float | None = None,
    stroke_width: float | None = None,
):
    """Build a resolved area chart with optional halo_multiplier/stroke_width overrides."""
    if multi_series:
        chart = make_chart("area", x="date", y="value", color="series")
        data = [
            {"date": "2024-01-01", "value": 100, "series": "Revenue"},
            {"date": "2024-02-01", "value": 120, "series": "Revenue"},
            {"date": "2024-01-01", "value": 80, "series": "Costs"},
            {"date": "2024-02-01", "value": 90, "series": "Costs"},
        ]
    else:
        chart = make_chart("area", x="date", y="value")
        data = [
            {"date": "2024-01-01", "value": 100},
            {"date": "2024-02-01", "value": 120},
        ]

    seed = get_theme_style()
    if halo_multiplier is not None or stroke_width is not None:
        # halo_multiplier/stroke live on LineMarkStyle (global marks tier):
        # area's top-edge stroke/halo is a genuine separate line mark,
        # sourced from area's marks.line slot (which inherits from this
        # global marks.line tier by default — see AreaLineStyle).
        line_mark = seed.charts.marks.line
        updates: dict = {}
        if halo_multiplier is not None:
            updates["halo_multiplier"] = halo_multiplier
        if stroke_width is not None:
            updates["stroke"] = line_mark.stroke.model_copy(
                update={"width": stroke_width}
            )
        new_line_mark = line_mark.model_copy(update=updates)
        new_marks = seed.charts.marks.model_copy(update={"line": new_line_mark})
        seed = seed.model_copy(
            update={"charts": seed.charts.model_copy(update={"marks": new_marks})}
        )
    board_style = resolve_chart_style_context(seed)
    resolved = resolve(chart, data, chart_style_context=board_style)
    return resolved, data


def _render_spec(resolved_chart, data):
    """Render the chart and return the inner Vega-Lite chart spec dict.

    Multi-series area charts with endpoint labels produce an hconcat spec;
    layer-level assertions belong on the inner chart pane (hconcat[0]).
    """
    artifact = render_resolved_chart(resolved_chart, data, _BOARD_STYLE, width=400)
    assert artifact.kind == "vega_spec"
    spec = artifact.payload
    return spec["hconcat"][0] if "hconcat" in spec else spec


def _halo_line_mark(spec):
    """Return the halo line mark (background-colored stroke layer)."""
    for layer in spec.get("layer", []):
        m = layer.get("mark", {})
        if (
            isinstance(m, dict)
            and m.get("type") == "line"
            and m.get("tooltip") is False
        ):
            return m
    return {}


def _fg_line_mark(spec):
    """Return the foreground line mark (colored top-edge stroke)."""
    for layer in reversed(spec.get("layer", [])):
        m = layer.get("mark", {})
        if isinstance(m, dict) and m.get("type") == "line":
            return m
    return {}


def test_area_halo_emits_line_stroke_in_background_color(make_chart):
    """Multi-series area halo line mark has stroke == background."""
    resolved, data = _make_area_chart(make_chart, multi_series=True)
    spec = _render_spec(resolved, data)

    assert spec.get("layer"), "Expected layered spec with halo"
    halo_mark = _halo_line_mark(spec)
    layer_types = [layer.get("mark", {}).get("type") for layer in spec.get("layer", [])]
    assert halo_mark, f"No halo line mark found in layers: {layer_types}"
    # Halo knocks out against the chart's opaque composited canvas
    # (ink_canvas), not the raw (possibly transparent) background field.
    canvas = resolved.canvas
    assert halo_mark.get("stroke") == canvas, (
        f"Halo line.stroke must equal canvas {canvas!r}, got {halo_mark.get('stroke')!r}"
    )


def test_area_halo_stroke_wider_than_foreground(make_chart):
    """Halo line strokeWidth > foreground line strokeWidth (multiplier > 1)."""
    resolved, data = _make_area_chart(make_chart, multi_series=True)
    spec = _render_spec(resolved, data)

    halo_mark = _halo_line_mark(spec)
    fg_mark = _fg_line_mark(spec)
    assert halo_mark, "No halo line mark found"

    halo_sw = halo_mark.get("strokeWidth", 0)
    fg_sw = fg_mark.get("strokeWidth", 0)
    assert halo_sw > 0, f"Halo line.strokeWidth must be > 0, got {halo_sw}"
    assert halo_sw > fg_sw, (
        f"Halo strokeWidth ({halo_sw}) must exceed fg strokeWidth ({fg_sw})"
    )


def test_area_halo_disabled_when_multiplier_zero(make_chart):
    """halo_multiplier=0 → no halo LINE layer, but the fill backdrop stays
    (it's gated on marks.area.backdrop, not halo_multiplier — a fill has no
    "width" for halo_multiplier to scale, so the two are independent)."""
    resolved, data = _make_area_chart(
        make_chart, multi_series=True, halo_multiplier=0.0
    )
    spec = _render_spec(resolved, data)

    layers = spec.get("layer", [])
    # backdrop stays: [halo_fill, fg_fill, zero_rule, fg_line, point_overlay]
    # — 5 layers. Area always zero-anchors this all-positive data, so the
    # baseline rule is inserted after the last area fill (see _insert_rule).
    assert len(layers) == 5, (
        f"halo_multiplier=0 must drop only the halo LINE (backdrop stays), "
        f"got {len(layers)}: {[layer.get('mark', {}).get('type') for layer in layers]}"
    )
    assert layers[0].get("mark", {})["type"] == "area"
    assert layers[1].get("mark", {})["type"] == "area"
    assert layers[2].get("mark", {})["type"] == "rule"
    assert layers[3].get("mark", {})["type"] == "line"
    assert layers[4].get("mark", {})["type"] == "point"
    assert layers[4].get("mark", {})["opacity"] == 0

    # No background-colored stroke layer
    halo_mark = _halo_line_mark(spec)
    assert not halo_mark, f"Expected no halo line mark, found: {halo_mark}"


def test_area_backdrop_disabled_when_backdrop_false(make_chart):
    """`marks.area.backdrop: false` removes the fill backdrop entirely —
    independent of halo_multiplier, which stays controlling only the edge
    line's halo."""
    seed = get_theme_style()
    new_area_mark = seed.charts.marks.area.model_copy(update={"backdrop": False})
    new_marks = seed.charts.marks.model_copy(update={"area": new_area_mark})
    seed = seed.model_copy(
        update={"charts": seed.charts.model_copy(update={"marks": new_marks})}
    )
    board_style = resolve_chart_style_context(seed)
    chart = make_chart("area", x="date", y="value", color="series")
    data = [
        {"date": "2024-01-01", "value": 100, "series": "Revenue"},
        {"date": "2024-02-01", "value": 120, "series": "Revenue"},
        {"date": "2024-01-01", "value": 80, "series": "Costs"},
        {"date": "2024-02-01", "value": 90, "series": "Costs"},
    ]
    resolved = resolve(chart, data, chart_style_context=board_style)
    spec = _render_spec(resolved, data)

    layers = spec.get("layer", [])
    layer_types = [layer.get("mark", {}).get("type") for layer in layers]
    # No backdrop: [halo_line, fg_fill, zero_rule, fg_line, point_overlay] —
    # the edge halo line stays (halo_multiplier untouched, still non-zero).
    # Area always zero-anchors this all-positive data, so the baseline rule
    # is inserted after the last area fill (see _insert_rule).
    assert layer_types == [
        "line",
        "area",
        "rule",
        "line",
        "point",
    ], f"backdrop=false must drop only the fill backdrop; got {layer_types}"


def test_area_halo_fill_survives_zero_stroke_width(make_chart):
    """`marks.line.stroke.width: 0` must remove only the edge line (and its
    halo line) — the halo AREA (the opaque fill backdrop) must stay, since
    it's what makes the fg fill's opacity read consistently regardless of
    what's behind it. Regression: stroke_width==0 used to kill the ENTIRE
    halo pair (fill + line), silently flattening a translucent area to a
    plain opaque fill whenever the author only meant to hide the edge."""
    resolved, data = _make_area_chart(make_chart, multi_series=True, stroke_width=0.0)
    spec = _render_spec(resolved, data)

    layers = spec.get("layer", [])
    layer_types = [layer.get("mark", {}).get("type") for layer in layers]
    # [halo_fill (area), fg_fill (area), zero_rule, hover (point)] — no line
    # marks at all (no edge stroke, no halo line to knock it out). Area
    # always zero-anchors this all-positive data, so the baseline rule is
    # inserted after the last area fill (see _insert_rule).
    assert layer_types == ["area", "area", "rule", "point"], (
        f"stroke.width=0 must keep the halo fill area, drop the edge/halo "
        f"lines; got {layer_types}"
    )
    halo_fill, fg_fill = layers[0]["mark"], layers[1]["mark"]
    # Halo knocks out against the chart's opaque composited canvas
    # (ink_canvas), not the raw (possibly transparent) background field.
    canvas = resolved.canvas
    assert halo_fill.get("fill") == canvas, (
        f"Halo area fill must equal canvas {canvas!r}, got {halo_fill.get('fill')!r}"
    )
    assert halo_fill.get("fillOpacity") == 1, "Halo area must stay fully opaque"
    assert fg_fill.get("fillOpacity") != 1 or fg_fill.get("fill") != canvas, (
        "Foreground fill must remain the series tint, not the canvas"
    )
