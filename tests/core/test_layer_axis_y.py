"""Tests for per-layer y-axis chrome extensions (defects #11, #12, #16).

#11 — LayerAxisYStyle extended with scale.domain, ticks.count, grid.visible,
      labels.format; each propagates through resolved → render VL output.
#12 — chart-level axis_y.ticks.count / format reach BOTH dual y-axes.
#16 — chart-level axis_y.scale.domain with split-side dual-axis raises
      ERR-LAYERS-AMBIGUOUS-Y-DOMAIN at compile (resolve) time.
"""

from __future__ import annotations

import pytest

from dbt_charts.core.compile.resolve.chart._chart_rows import regroup
from dbt_charts.core.render.chart.spec import RenderBox

_DEFAULT_BOX = RenderBox(width=600.0, height=300.0)

# ── helpers (mirror test_layers_on_cartesian.py setup) ───────────────────


def _default_board_style():  # type: ignore[no-untyped-def]
    from dbt_charts.core.compile.config import get_default_theme_name, get_theme_style
    from dbt_charts.core.compile.resolve.style.board import resolve_chart_style_context

    return resolve_chart_style_context(get_theme_style(get_default_theme_name()))


def _sql(sql: str = "SELECT 1"):  # type: ignore[no-untyped-def]
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
    return NBarChart(**defaults)


def _line_normalized(**kwargs):  # type: ignore[no-untyped-def]
    from dbt_charts.core.compile.models.chart.normalized import LineChart as NLineChart

    defaults = {
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


_DATA: list[dict] = [
    {"month": "Jan", "revenue": 100.0, "target": 4000.0},
    {"month": "Feb", "revenue": 200.0, "target": 5000.0},
    {"month": "Mar", "revenue": 150.0, "target": 4500.0},
]


def _render_bar_with_layers(layers: list, **bar_kwargs) -> dict:  # type: ignore[no-untyped-def]
    """Resolve + emit a bar chart with layers; return the outermost VL dict."""
    from dbt_charts.core.compile.resolve import resolve
    from dbt_charts.core.render.chart.emitters.bar import BarEmitter
    from dbt_charts.core.render.chart.translate import translate_to_vl

    chart = _bar_normalized(layers=layers, **bar_kwargs)
    resolved = resolve(chart, _DATA, _default_board_style())
    spec = BarEmitter().emit(resolved, _DEFAULT_BOX, regroup((), _DATA))
    return translate_to_vl(spec)


def _walk_layers(vl: dict):  # type: ignore[no-untyped-def]
    """Yield every VL layer dict under ``vl["layer"]``, recursing into nested
    ``layer`` arrays. A dual-axis zero-baseline rule nests one level inside
    the entry whose scale it shares (see ``emitters/_cartesian.py``'s
    ``nest_zero_rule``), so a shallow scan of the top-level array alone can
    miss a base or overlay entry."""
    for layer in vl.get("layer", []):
        yield layer
        yield from _walk_layers(layer)


def _find_layer_y_axis(vl: dict, orient: str) -> dict | None:
    """Walk VL spec layers to find a y-encoding axis with the given orient."""
    for layer in _walk_layers(vl):
        enc = layer.get("encoding", {})
        y = enc.get("y", {})
        axis = y.get("axis")
        if isinstance(axis, dict) and axis.get("orient") == orient:
            return axis
    return None


def _find_layer_y_scale(vl: dict, orient: str) -> dict | None:
    """Walk VL spec layers to find a y-encoding scale for a given orient."""
    for layer in _walk_layers(vl):
        enc = layer.get("encoding", {})
        y = enc.get("y", {})
        axis = y.get("axis")
        if isinstance(axis, dict) and axis.get("orient") == orient:
            return y.get("scale")
    return None


# ── #11: LayerAxisYStyle authoring accepts new fields ────────────────────


def test_layer_axis_y_scale_domain_parses() -> None:
    """axis_y.scale.domain on a layer parses as a list of floats."""
    from dbt_charts.core.compile.models.chart.authored._layer import LineLayer

    layer = LineLayer.model_validate(
        {"type": "line", "y": "target", "axis_y": {"scale": {"domain": [0, 5000]}}}
    )
    assert layer.axis_y is not None
    assert layer.axis_y.scale is not None
    assert layer.axis_y.scale.domain == [0.0, 5000.0]


def test_layer_axis_y_ticks_count_parses() -> None:
    """axis_y.ticks.count on a layer parses as int."""
    from dbt_charts.core.compile.models.chart.authored._layer import LineLayer

    layer = LineLayer.model_validate(
        {"type": "line", "y": "target", "axis_y": {"ticks": {"count": 4}}}
    )
    assert layer.axis_y is not None
    assert layer.axis_y.ticks is not None
    assert layer.axis_y.ticks.count == 4


def test_layer_axis_y_grid_visible_parses() -> None:
    """axis_y.grid.visible on a layer parses as bool."""
    from dbt_charts.core.compile.models.chart.authored._layer import LineLayer

    layer = LineLayer.model_validate(
        {"type": "line", "y": "target", "axis_y": {"grid": {"visible": False}}}
    )
    assert layer.axis_y is not None
    assert layer.axis_y.grid is not None
    assert layer.axis_y.grid.visible is False


def test_layer_axis_y_label_format_parses() -> None:
    """axis_y.labels.format on a layer parses as a string."""
    from dbt_charts.core.compile.models.chart.authored._layer import LineLayer

    layer = LineLayer.model_validate(
        {"type": "line", "y": "target", "axis_y": {"labels": {"format": "$,.0f"}}}
    )
    assert layer.axis_y is not None
    assert layer.axis_y.labels is not None
    assert layer.axis_y.labels.format == "$,.0f"


# ── #11: per-layer fields survive through resolve ─────────────────────────


def test_layer_axis_y_scale_domain_survives_resolve() -> None:
    """axis_y.scale.domain on a layer survives _resolve_one_layer unchanged."""
    from dbt_charts.core.compile.models.chart.authored._layer import LineLayer
    from dbt_charts.core.compile.models.chart.resolved._layer import ResolvedLineLayer
    from dbt_charts.core.compile.resolve import resolve

    layer = LineLayer(
        type="line", y="target", axis_y={"scale": {"domain": [0.0, 5000.0]}}
    )
    chart = _bar_normalized(layers=[layer])
    resolved = resolve(chart, _DATA, _default_board_style())
    rl = resolved.layers[0]
    assert isinstance(rl, ResolvedLineLayer)
    assert rl.axis_y.scale is not None
    assert rl.axis_y.scale.domain == [0.0, 5000.0]


def test_layer_axis_y_ticks_count_survives_resolve() -> None:
    """axis_y.ticks.count on a layer survives _resolve_one_layer unchanged."""
    from dbt_charts.core.compile.models.chart.authored._layer import LineLayer
    from dbt_charts.core.compile.models.chart.resolved._layer import ResolvedLineLayer
    from dbt_charts.core.compile.resolve import resolve

    layer = LineLayer(type="line", y="target", axis_y={"ticks": {"count": 3}})
    chart = _bar_normalized(layers=[layer])
    resolved = resolve(chart, _DATA, _default_board_style())
    rl = resolved.layers[0]
    assert isinstance(rl, ResolvedLineLayer)
    assert rl.axis_y.ticks is not None
    assert rl.axis_y.ticks.count == 3


# ── #11: per-layer fields reach VL output ────────────────────────────────


def test_layer_axis_y_scale_domain_emits_to_vl() -> None:
    """axis_y.scale.domain=[0,5000] on a right-side layer sets scale.domain in VL."""
    from dbt_charts.core.compile.models.chart.authored._layer import LineLayer

    layer = LineLayer(
        type="line",
        y="target",
        axis_y={"position": "right", "scale": {"domain": [0.0, 5000.0]}},
    )
    vl = _render_bar_with_layers([layer])
    scale = _find_layer_y_scale(vl, orient="right")
    assert scale is not None, "No right-orient y scale in VL output"
    assert scale.get("domain") == [0.0, 5000.0]


def test_layer_axis_y_ticks_count_emits_tickCount_to_vl() -> None:
    """axis_y.ticks.count=4 on a right-side layer sets tickCount in VL axis."""
    from dbt_charts.core.compile.models.chart.authored._layer import LineLayer

    layer = LineLayer(
        type="line",
        y="target",
        axis_y={"position": "right", "ticks": {"count": 4}},
    )
    vl = _render_bar_with_layers([layer])
    axis = _find_layer_y_axis(vl, orient="right")
    assert axis is not None, "No right-orient y axis in VL output"
    assert axis.get("tickCount") == 4


def test_layer_axis_y_grid_visible_false_emits_to_vl() -> None:
    """axis_y.grid.visible=False on a right-side layer sets grid=False in VL axis."""
    from dbt_charts.core.compile.models.chart.authored._layer import LineLayer

    layer = LineLayer(
        type="line",
        y="target",
        axis_y={"position": "right", "grid": {"visible": False}},
    )
    vl = _render_bar_with_layers([layer])
    axis = _find_layer_y_axis(vl, orient="right")
    assert axis is not None, "No right-orient y axis in VL output"
    assert axis.get("grid") is False


def test_layer_axis_y_label_format_emits_to_vl() -> None:
    """axis_y.labels.format on a right-side layer sets format in VL axis."""
    from dbt_charts.core.compile.models.chart.authored._layer import LineLayer

    layer = LineLayer(
        type="line",
        y="target",
        axis_y={"position": "right", "labels": {"format": "$,.0f"}},
    )
    vl = _render_bar_with_layers([layer])
    axis = _find_layer_y_axis(vl, orient="right")
    assert axis is not None, "No right-orient y axis in VL output"
    assert axis.get("format") == "$,.0f"


def test_layer_axis_y_label_format_inline_d3_is_native() -> None:
    """axis_y.labels.format authored as a literal d3 spec passes through unmodified.

    Inline d3 specs are on the native-d3 path — no round-aware trim, no notation
    post-process. An author who wants trim writes ".2~s" explicitly. Both axes
    must show the exact authored spec."""
    from dbt_charts.core.compile.models.chart.authored._layer import LineLayer

    layer = LineLayer(
        type="line",
        y="target",
        axis_y={"position": "right", "labels": {"format": ".2s"}},
    )
    vl = _render_bar_with_layers([layer], format=".2s")
    left_axis = _find_layer_y_axis(vl, orient="left")
    right_axis = _find_layer_y_axis(vl, orient="right")
    assert left_axis is not None and right_axis is not None
    assert left_axis.get("format") == ".2s"
    assert right_axis.get("format") == ".2s"


# ── #12: chart-level tick count reaches both dual y-axes ─────────────────


def _bar_with_right_layer_and_style(**style_kwargs):  # type: ignore[no-untyped-def]
    """NBarChart with a right-pinned line overlay and chart-level style overrides."""
    from dbt_charts.core.compile.models.chart.authored._layer import LineLayer
    from dbt_charts.core.compile.models.chart.normalized import BarChart as NBarChart
    from dbt_charts.core.compile.models.style.authored import BarChartStylePatch

    return NBarChart(
        id="bar1",
        type="bar",
        x="month",
        y="revenue",
        query=_sql(),
        query_name="q",
        variable_dependencies=set(),
        layers=[LineLayer(type="line", y="target", axis_y={"position": "right"})],
        style=BarChartStylePatch.model_validate(style_kwargs) if style_kwargs else None,
    )


def test_chart_level_tick_count_reaches_right_overlay_axis() -> None:
    """style.axis_y.ticks.count must emit tickCount on the right overlay axis.

    The overlay layer's axis is built in render_cartesian_overlay from ay_vl.
    Previously, ay_vl lacked tickCount entirely (only the base chart y encoding
    had computed tick_values). This test pins that tickCount reaches the right side.
    """
    from dbt_charts.core.compile.resolve import resolve
    from dbt_charts.core.render.chart.emitters.bar import BarEmitter
    from dbt_charts.core.render.chart.translate import translate_to_vl

    chart = _bar_with_right_layer_and_style(axis_y={"ticks": {"count": 5}})
    resolved = resolve(chart, _DATA, _default_board_style())
    vl = translate_to_vl(BarEmitter().emit(resolved, _DEFAULT_BOX, regroup((), _DATA)))

    right_axis = _find_layer_y_axis(vl, orient="right")
    assert right_axis is not None, "No right-orient axis in VL output"
    assert right_axis.get("tickCount") == 5, f"tickCount missing: {right_axis}"


def test_chart_level_format_reaches_right_overlay_axis() -> None:
    """style.axis_y.labels.format must appear on the right overlay axis in a dual-y chart."""
    from dbt_charts.core.compile.resolve import resolve
    from dbt_charts.core.render.chart.emitters.bar import BarEmitter
    from dbt_charts.core.render.chart.translate import translate_to_vl

    chart = _bar_with_right_layer_and_style(axis_y={"labels": {"format": ",.1f"}})
    resolved = resolve(chart, _DATA, _default_board_style())
    vl = translate_to_vl(BarEmitter().emit(resolved, _DEFAULT_BOX, regroup((), _DATA)))

    right_axis = _find_layer_y_axis(vl, orient="right")
    assert right_axis is not None, "No right-orient axis"
    assert right_axis.get("format") == ",.1f", f"format missing: {right_axis}"


# ── #16: ambiguous chart-level domain on split-scale dual-axis ────────────


def test_chart_level_domain_with_split_dual_axis_raises_error() -> None:
    """axis_y.scale.domain set on a chart with independent dual y-axes must raise.

    Error code: ERR-LAYERS-AMBIGUOUS-Y-DOMAIN — caught at resolve time.
    """
    from dbt_charts.core.compile.errors import CompilationError
    from dbt_charts.core.compile.resolve import resolve

    # left base + right overlay → independent y scales
    chart = _bar_with_right_layer_and_style(
        axis_y={"scale": {"continuous": {"domain": [0, 300]}}}
    )

    with pytest.raises(CompilationError) as exc_info:
        resolve(chart, _DATA, _default_board_style())
    assert exc_info.value.code is not None
    assert exc_info.value.code.code == "ERR-LAYERS-AMBIGUOUS-Y-DOMAIN"


def test_chart_level_domain_single_axis_does_not_raise() -> None:
    """axis_y.scale.domain on a single-y chart (no dual-axis) must NOT raise."""
    from dbt_charts.core.compile.models.chart.normalized import BarChart as NBarChart
    from dbt_charts.core.compile.models.style.authored import BarChartStylePatch
    from dbt_charts.core.compile.resolve import resolve
    from dbt_charts.core.render.chart.emitters.bar import BarEmitter

    chart = NBarChart(
        id="bar1",
        type="bar",
        x="month",
        y="revenue",
        query=_sql(),
        query_name="q",
        variable_dependencies=set(),
        style=BarChartStylePatch.model_validate(
            {"axis_y": {"scale": {"continuous": {"domain": [0, 300]}}}}
        ),
    )
    resolved = resolve(chart, _DATA, _default_board_style())
    BarEmitter().emit(resolved, _DEFAULT_BOX, regroup((), _DATA))


def test_chart_level_domain_same_side_layers_does_not_raise() -> None:
    """axis_y.scale.domain with layers all on the same side must NOT raise."""
    from dbt_charts.core.compile.models.chart.authored._layer import LineLayer
    from dbt_charts.core.compile.models.chart.normalized import BarChart as NBarChart
    from dbt_charts.core.compile.models.style.authored import BarChartStylePatch
    from dbt_charts.core.compile.resolve import resolve
    from dbt_charts.core.render.chart.emitters.bar import BarEmitter

    # Both base and overlay are on the same side — no independent y
    chart = NBarChart(
        id="bar1",
        type="bar",
        x="month",
        y="revenue",
        query=_sql(),
        query_name="q",
        variable_dependencies=set(),
        layers=[LineLayer(type="line", y="target")],
        style=BarChartStylePatch.model_validate(
            {"axis_y": {"scale": {"continuous": {"domain": [0, 300]}}}}
        ),
    )
    resolved = resolve(chart, _DATA, _default_board_style())
    BarEmitter().emit(resolved, _DEFAULT_BOX, regroup((), _DATA))


# ── Dual-axis base orientation ────────────────────────────────────────────
#
# Regression: a right-pinned overlay on a base whose theme-default y-axis is
# ALSO "right" (e.g. stark) must push the base to the opposite (left) side, not
# collide both series on the right. The overlay pins the base to the free side
# rather than assuming the theme default is "left".


def _find_measure_y_orient(vl: dict, field: str) -> str | None:  # type: ignore[no-untyped-def]
    """Return the axis orient of the y encoding for the given measure field."""
    for layer in _walk_layers(vl):
        y = layer.get("encoding", {}).get("y", {})
        if y.get("field") == field and isinstance(y.get("axis"), dict):
            return y["axis"].get("orient")
    return None


def test_right_pinned_overlay_pushes_base_to_left() -> None:
    """Base (revenue) → left, right-pinned line (target) → right; independent y."""
    from dbt_charts.core.compile.models.chart.authored._layer import (
        LayerAxisYStyle,
        LineLayer,
    )
    from dbt_charts.core.compile.models.chart.normalized import BarChart as NBarChart
    from dbt_charts.core.compile.resolve import resolve
    from dbt_charts.core.render.chart.emitters.bar import BarEmitter
    from dbt_charts.core.render.chart.translate import translate_to_vl

    vdata = [
        {"date": "2025-01", "revenue": 42000.0, "target": 3.1},
        {"date": "2025-02", "revenue": 51000.0, "target": 3.4},
    ]
    chart = NBarChart(
        id="bar1",
        type="bar",
        x="date",
        y="revenue",
        query=_sql(),
        query_name="q",
        variable_dependencies=set(),
        layers=[
            LineLayer(type="line", y="target", axis_y=LayerAxisYStyle(position="right"))
        ],
    )
    resolved = resolve(chart, vdata, _default_board_style())
    vl = translate_to_vl(BarEmitter().emit(resolved, _DEFAULT_BOX, regroup((), vdata)))

    assert _find_measure_y_orient(vl, "revenue") == "left"
    assert _find_measure_y_orient(vl, "target") == "right"
    assert vl.get("resolve", {}).get("scale", {}).get("y") == "independent"


def _has_datum_zero_rule(vl: dict) -> bool:  # type: ignore[no-untyped-def]
    for layer in vl.get("layer", []):
        mark = layer.get("mark")
        mtype = mark.get("type") if isinstance(mark, dict) else mark
        if mtype == "rule" and layer.get("encoding", {}).get("y", {}).get("datum") == 0:
            return True
    return False


def _render_dual_axis() -> dict:  # type: ignore[no-untyped-def]
    from dbt_charts.core.compile.models.chart.authored._layer import (
        LayerAxisYStyle,
        LineLayer,
    )
    from dbt_charts.core.compile.models.chart.normalized import BarChart as NBarChart
    from dbt_charts.core.compile.resolve import resolve
    from dbt_charts.core.render.chart.emitters.bar import BarEmitter
    from dbt_charts.core.render.chart.translate import translate_to_vl

    vdata = [
        {"date": "2025-01", "revenue": 42000.0, "target": 3.1},
        {"date": "2025-02", "revenue": 51000.0, "target": 3.4},
    ]
    chart = NBarChart(
        id="bar1",
        type="bar",
        x="date",
        y="revenue",
        query=_sql(),
        query_name="q",
        variable_dependencies=set(),
        layers=[
            LineLayer(type="line", y="target", axis_y=LayerAxisYStyle(position="right"))
        ],
    )
    resolved = resolve(chart, vdata, _default_board_style())
    return translate_to_vl(
        BarEmitter().emit(resolved, _DEFAULT_BOX, regroup((), vdata))
    )


def test_dual_axis_base_axis_shows_title() -> None:
    """A dual-axis chart labels BOTH sides: the base (left) axis title is restored
    from its field even on a dual-axis overlay, which needs both sides labeled
    to distinguish them."""
    vl = _render_dual_axis()
    checked = False
    for layer in _walk_layers(vl):
        y = layer.get("encoding", {}).get("y", {})
        if y.get("field") == "revenue" and isinstance(y.get("axis"), dict):
            assert y["axis"].get("title") == "revenue"
            checked = True
    assert checked, "no layer's y encoding matched revenue/axis — spec shape changed"


def _render_dual_axis_with_label() -> dict:  # type: ignore[no-untyped-def]
    """Same dual-axis shape as ``_render_dual_axis``, but the base chart
    authors ``y_label`` with no explicit ``title.visible`` override — the
    flag-True-but-not-suppressed case: the base axis title must still show."""
    from dbt_charts.core.compile.models.chart.authored._layer import (
        LayerAxisYStyle,
        LineLayer,
    )
    from dbt_charts.core.compile.models.chart.normalized import BarChart as NBarChart
    from dbt_charts.core.compile.resolve import resolve
    from dbt_charts.core.render.chart.emitters.bar import BarEmitter
    from dbt_charts.core.render.chart.translate import translate_to_vl

    vdata = [
        {"date": "2025-01", "revenue": 42000.0, "target": 3.1},
        {"date": "2025-02", "revenue": 51000.0, "target": 3.4},
    ]
    chart = NBarChart(
        id="bar1",
        type="bar",
        x="date",
        y="revenue",
        y_label="Rev",
        query=_sql(),
        query_name="q",
        variable_dependencies=set(),
        layers=[
            LineLayer(type="line", y="target", axis_y=LayerAxisYStyle(position="right"))
        ],
    )
    resolved = resolve(chart, vdata, _default_board_style())
    return translate_to_vl(
        BarEmitter().emit(resolved, _DEFAULT_BOX, regroup((), vdata))
    )


def test_dual_axis_labeled_not_suppressed_shows_title() -> None:
    """A y_label authored with no explicit title.visible:false must still show
    the base axis title on a dual-axis overlay (the flag=True-but-not-suppressed
    case the suppression gate must not treat as suppressed)."""
    vl = _render_dual_axis_with_label()
    checked = False
    for layer in _walk_layers(vl):
        y = layer.get("encoding", {}).get("y", {})
        if y.get("field") == "revenue" and isinstance(y.get("axis"), dict):
            axis = y["axis"]
            assert "title" in axis and axis["title"] == "Rev"
            checked = True
    assert checked, "no layer's y encoding matched revenue/axis — spec shape changed"


def _render_dual_axis_with_suppressed_base_title() -> dict:  # type: ignore[no-untyped-def]
    """Same dual-axis shape as ``_render_dual_axis``, but the base chart
    authors ``y_label`` alongside an explicit ``axis_y.title.visible: false``
    — the precedence fix's own motivating case, on the overlay path."""
    from dbt_charts.core.compile.models.chart.authored._layer import (
        LayerAxisYStyle,
        LineLayer,
    )
    from dbt_charts.core.compile.models.chart.normalized import BarChart as NBarChart
    from dbt_charts.core.compile.models.style.authored import BarChartStylePatch
    from dbt_charts.core.compile.resolve import resolve
    from dbt_charts.core.render.chart.emitters.bar import BarEmitter
    from dbt_charts.core.render.chart.translate import translate_to_vl

    vdata = [
        {"date": "2025-01", "revenue": 42000.0, "target": 3.1},
        {"date": "2025-02", "revenue": 51000.0, "target": 3.4},
    ]
    chart = NBarChart(
        id="bar1",
        type="bar",
        x="date",
        y="revenue",
        y_label="Rev",
        query=_sql(),
        query_name="q",
        variable_dependencies=set(),
        style=BarChartStylePatch.model_validate(
            {"axis_y": {"title": {"visible": False}}}
        ),
        layers=[
            LineLayer(type="line", y="target", axis_y=LayerAxisYStyle(position="right"))
        ],
    )
    resolved = resolve(chart, vdata, _default_board_style())
    return translate_to_vl(
        BarEmitter().emit(resolved, _DEFAULT_BOX, regroup((), vdata))
    )


def test_dual_axis_honors_explicit_title_visible_false() -> None:
    """An explicitly authored axis_y.title.visible:false must stay suppressed
    on a dual-axis overlay base — the emit-time dual-axis title restore must
    not resurrect it (regression: the restore used to fire unconditionally
    whenever a layer pinned a side, discarding the author's own false)."""
    vl = _render_dual_axis_with_suppressed_base_title()
    checked = False
    for layer in _walk_layers(vl):
        y = layer.get("encoding", {}).get("y", {})
        if y.get("field") == "revenue" and isinstance(y.get("axis"), dict):
            axis = y["axis"]
            assert "title" in axis and axis["title"] is None
            # The label still carries the encoding title (tooltip/legend).
            assert y.get("title") == "Rev"
            checked = True
    assert checked, "no layer's y encoding matched revenue/axis — spec shape changed"


def _render_horizontal_layered(suppress: str | None) -> dict:  # type: ignore[no-untyped-def]
    """Dual-axis overlay on a *horizontal* bar.

    Horizontal puts the CATEGORY axis on VL y (governed by axis_x/x_label);
    the measure axis is VL x. ``suppress`` picks which axis authors
    ``title.visible: false`` alongside its label: "x" (the category axis
    actually drawn on VL y) or "y" (the measure axis, which is not).
    """
    from dbt_charts.core.compile.models.chart.authored._layer import LineLayer
    from dbt_charts.core.compile.models.chart.normalized import BarChart as NBarChart
    from dbt_charts.core.compile.models.style.authored import BarChartStylePatch
    from dbt_charts.core.compile.resolve import resolve
    from dbt_charts.core.render.chart.emitters.bar import BarEmitter
    from dbt_charts.core.render.chart.translate import translate_to_vl

    vdata = [
        {"date": "2025-01", "revenue": 42000.0, "target": 3.1},
        {"date": "2025-02", "revenue": 51000.0, "target": 3.4},
    ]
    axis_key = "axis_x" if suppress == "x" else "axis_y"
    chart = NBarChart(
        id="bar1",
        type="bar",
        x="date",
        y="revenue",
        x_label="Month",
        y_label="Rev",
        query=_sql(),
        query_name="q",
        variable_dependencies=set(),
        style=BarChartStylePatch.model_validate(
            {
                "orientation": "horizontal",
                **({axis_key: {"title": {"visible": False}}} if suppress else {}),
            }
        ),
        # No axis_y.position: a horizontal base measures along VL x, whose
        # sides are top and bottom, so pinning a left/right side raises. The
        # property under test is the CATEGORY axis title, which a plain layered
        # horizontal bar draws just the same.
        layers=[LineLayer(type="line", y="target")],
    )
    resolved = resolve(chart, vdata, _default_board_style())
    return translate_to_vl(
        BarEmitter().emit(resolved, _DEFAULT_BOX, regroup((), vdata))
    )


def _base_category_axis(vl: dict) -> dict | None:  # type: ignore[no-untyped-def]
    """The base layer's VL y axis — the category axis on a horizontal bar."""
    for layer in vl.get("layer", []):
        y = layer.get("encoding", {}).get("y", {})
        if y.get("field") == "date" and isinstance(y.get("axis"), dict):
            return y["axis"]
    return None


def test_horizontal_layered_bar_titles_its_category_axis_from_axis_x() -> None:
    """On a horizontal bar the category axis is drawn on VL y, so it is
    axis_x/x_label that governs it — not axis_y/y_label.

    Suppressing axis_x must suppress the category title; suppressing axis_y
    (the measure axis, drawn on VL x) must leave the category title alone.
    Reading axis_y for both would delete a title the author never suppressed.
    """
    suppressed_x = _base_category_axis(_render_horizontal_layered("x"))
    assert suppressed_x is not None
    assert "title" in suppressed_x and suppressed_x["title"] is None, (
        "axis_x.title.visible:false must suppress the category axis title "
        "that a horizontal bar draws on VL y"
    )

    suppressed_y = _base_category_axis(_render_horizontal_layered("y"))
    assert suppressed_y is not None
    # Not-suppressed is the ABSENCE of an explicit override, not a title
    # stamped onto the axis: the category axis inherits its text from the
    # encoding. (The overlay's dual-axis path used to stamp one here, which is
    # why this once read as a truthy title — an artifact of a path a horizontal
    # base no longer takes, not the property.)
    assert "title" not in suppressed_y, (
        "suppressing the measure axis (axis_y) must not touch the category "
        "axis title, which axis_x governs"
    )


def test_dual_axis_skips_floating_zero_rule() -> None:
    """No zero rule sits at the TOP level of `layer[]` as a floating,
    unbound sibling. `_has_datum_zero_rule` only scans that shallow level,
    one above where `nest_zero_rule` actually nests a dual-axis rule.

    `_render_dual_axis()` calls `BarEmitter().emit()` directly and never runs
    the feature pipeline, so this pins the bare emitter's own output shape,
    not a `BaselineFeature` verdict. Regression coverage for per-scale
    zero-rule insertion (which entry gets a rule, and which doesn't) lives in
    `test_zero_baseline.py`'s `test_dual_axis_*` tests, which run the full
    pipeline via `generate_vega_lite_spec` and assert the nested shape
    directly."""
    vl = _render_dual_axis()
    assert not _has_datum_zero_rule(vl)


# ── Stale labelPadding after base orient repin ────────────────────────────
#
# Regression: when a layer pins its y-axis to "right", the overlay emitter
# re-pins the base to "left" (line 663 of _overlay.py). Before the fix,
# labelPadding and labelAlign computed for the original right-side (by the
# house-format force) survived the repin as stale values on the left-side base.


def test_dual_axis_base_repin_strips_stale_label_padding_and_align() -> None:
    """Re-pinning base axis from right to left must strip stale labelPadding/labelAlign.

    A base bar chart with a house-format alias (format="percent") on the y-axis
    computes labelPadding for the right side (where _force_right fires). When a
    layer pins to "right", the overlay emitter re-pins the base to "left". The
    labelPadding and labelAlign from the right-side computation are stale for the
    left-side base and must be dropped.
    """
    from dbt_charts.core.compile.models.chart.authored._layer import (
        LayerAxisYStyle,
        LineLayer,
    )
    from dbt_charts.core.compile.models.chart.normalized import BarChart as NBarChart
    from dbt_charts.core.compile.models.style.authored import (
        AxisLabelStylePatch,
        AxisYStylePatch,
        BarChartStylePatch,
    )
    from dbt_charts.core.compile.resolve import resolve
    from dbt_charts.core.render.chart.emitters.bar import BarEmitter
    from dbt_charts.core.render.chart.translate import translate_to_vl

    # Numeric x forces vertical bar orientation: ay_is_quantitative=True so
    # _force_right can fire when format is a house alias.
    data = [
        {"month": 1, "revenue": 1_000_000, "target": 0.4},
        {"month": 2, "revenue": 2_000_000, "target": 0.5},
        {"month": 3, "revenue": 1_500_000, "target": 0.45},
    ]
    chart = NBarChart(
        id="repin_test",
        type="bar",
        x="month",
        y="revenue",
        query=_sql(),
        query_name="q",
        variable_dependencies=set(),
        # format="percent" is a house alias -> _force_right fires on the base.
        style=BarChartStylePatch(
            axis_y=AxisYStylePatch(labels=AxisLabelStylePatch(format="percent"))
        ),
        # Layer pins to "right" -> overlay emitter repins base to "left".
        layers=[
            LineLayer(type="line", y="target", axis_y=LayerAxisYStyle(position="right"))
        ],
    )
    resolved = resolve(chart, data, _default_board_style())
    vl = translate_to_vl(BarEmitter().emit(resolved, _DEFAULT_BOX, regroup((), data)))

    left_axis = _find_layer_y_axis(vl, "left")
    assert left_axis is not None, "expected a left-orient axis from base repin"
    assert "labelPadding" not in left_axis, (
        f"stale right-side labelPadding survived base orient repin to left: {left_axis}"
    )
    assert "labelAlign" not in left_axis, (
        f"stale right-side labelAlign survived base orient repin to left: {left_axis}"
    )


def test_same_edge_overlay_preserves_base_label_padding_and_align() -> None:
    """Same-edge overlay must NOT strip the base axis labelPadding/labelAlign.

    When a left-pinned layer overlay leaves the base on its original right edge,
    the base axis never repins. The labelPadding and labelAlign computed for the
    right side remain valid and must survive — stripping them would silently
    disable the house-format feature for any chart with an overlay layer.
    """
    from dbt_charts.core.compile.models.chart.authored._layer import (
        LayerAxisYStyle,
        LineLayer,
    )
    from dbt_charts.core.compile.models.chart.normalized import BarChart as NBarChart
    from dbt_charts.core.compile.models.style.authored import (
        AxisLabelStylePatch,
        AxisYStylePatch,
        BarChartStylePatch,
    )
    from dbt_charts.core.compile.resolve import resolve
    from dbt_charts.core.render.chart.emitters.bar import BarEmitter
    from dbt_charts.core.render.chart.translate import translate_to_vl

    data = [
        {"month": 1, "revenue": 1_000_000, "target": 0.4},
        {"month": 2, "revenue": 2_000_000, "target": 0.5},
        {"month": 3, "revenue": 1_500_000, "target": 0.45},
    ]
    chart = NBarChart(
        id="same_edge_test",
        type="bar",
        x="month",
        y="revenue",
        query=_sql(),
        query_name="q",
        variable_dependencies=set(),
        # House alias on a right-edge vertical bar -> _force_right fires.
        style=BarChartStylePatch(
            axis_y=AxisYStylePatch(labels=AxisLabelStylePatch(format="percent"))
        ),
        # Layer pins to "left" -> base stays on right, no repin happens.
        layers=[
            LineLayer(type="line", y="target", axis_y=LayerAxisYStyle(position="left"))
        ],
    )
    resolved = resolve(chart, data, _default_board_style())
    vl = translate_to_vl(BarEmitter().emit(resolved, _DEFAULT_BOX, regroup((), data)))

    right_axis = _find_layer_y_axis(vl, "right")
    assert right_axis is not None, "expected a right-orient base axis"
    # The base axis stayed on the right side; its labelPadding and labelAlign
    # (computed by _force_right for the right edge) must remain intact.
    assert "labelPadding" in right_axis, (
        f"same-edge overlay must not strip right-side labelPadding: {right_axis}"
    )
    assert right_axis.get("labelAlign") == "right", (
        f"same-edge overlay must not strip right-side labelAlign: {right_axis}"
    )


def _find_all_layer_y_axes(vl: dict, orient: str) -> list:  # type: ignore[no-untyped-def]
    """Every y-encoding axis dict (base + each overlay) matching orient, in
    paint order -- unlike _find_layer_y_axis, which stops at the first match
    (always the base's, since it paints first) and so cannot see a shared-
    scale overlay's own axis dict when both sides agree.

    A layer's own "encoding" lives on whichever dict actually carries it --
    the wrapper when its own marks are a nested "layer" list (encoding
    pulled up, shared across sub-marks), or the layer dict itself otherwise
    -- so both must be checked, not just one or the other.
    """
    found = []
    for layer in vl.get("layer", []):
        for sub in [layer, *(layer.get("layer", []) or [])]:
            axis = sub.get("encoding", {}).get("y", {}).get("axis", {})
            if axis.get("orient") == orient:
                found.append(axis)
    return found


def test_shared_scale_overlay_axis_matches_base_when_base_cannot_measure() -> None:
    """Regression: a shared-scale overlay's own axis dict must not inherit an
    own-side labelAlign from a bare axis_to_vl(axis_y) reconstruction when
    the base itself falls back to VL's away-from-plot default.

    An authored axis_y.labels.expr makes the axis unmeasurable (mirrors
    measure_axis_to_vl's own gate) -- the base's OWN emitted axis correctly
    drops labelAlign via _fallback_reversed_label_align. Before the fix,
    render_cartesian_overlay rebuilt the layer's axis template from a raw
    axis_to_vl(axis_y) call that never ran that safety net, so a
    theme-default house-format axis (a predefined name, force_right) still
    carried labelAlign="right" into the overlay's own axis dict with only
    the flat, unmeasured labels.padding reserved -- right-anchoring the
    layer's tick labels with no gutter, so the mark drew over them.
    """
    from dbt_charts.core.compile.models.chart.authored._layer import LineLayer
    from dbt_charts.core.compile.models.chart.normalized import BarChart as NBarChart
    from dbt_charts.core.compile.models.style.authored import (
        AxisLabelStylePatch,
        AxisYStylePatch,
        BarChartStylePatch,
    )
    from dbt_charts.core.compile.resolve import resolve
    from dbt_charts.core.render.chart.emitters.bar import BarEmitter
    from dbt_charts.core.render.chart.translate import translate_to_vl

    data = [
        {"month": 1, "revenue": 1_000_000, "target": 4000.0},
        {"month": 2, "revenue": 2_000_000, "target": 5000.0},
        {"month": 3, "revenue": 1_500_000, "target": 4500.0},
    ]
    chart = NBarChart(
        id="shared_scale_unmeasurable_test",
        type="bar",
        x="month",
        y="revenue",
        query=_sql(),
        query_name="q",
        variable_dependencies=set(),
        # No format authored -> the theme default ("number") is a predefined
        # name -> _force_right fires. An authored labels.expr makes the axis
        # unmeasurable, exactly like measure_axis_to_vl's own "labelExpr
        # present" gate.
        style=BarChartStylePatch(
            axis_y=AxisYStylePatch(
                position="right",
                labels=AxisLabelStylePatch(expr="'$' + datum.value"),
            )
        ),
        # No axis_y.position on the layer -> shared scale, same edge as base.
        layers=[LineLayer(type="line", y="target")],
    )
    resolved = resolve(chart, data, _default_board_style())
    vl = translate_to_vl(BarEmitter().emit(resolved, _DEFAULT_BOX, regroup((), data)))

    right_axes = _find_all_layer_y_axes(vl, "right")
    assert len(right_axes) >= 2, f"expected base + overlay axes on the right: {vl}"
    base_axis, overlay_axis = right_axes[0], right_axes[1]

    assert "labelAlign" not in base_axis, (
        f"base axis with labelExpr must fall back (no labelAlign): {base_axis}"
    )
    assert overlay_axis.get("labelAlign") == base_axis.get("labelAlign"), (
        f"shared-scale overlay axis must match the base's own safety-netted "
        f"labelAlign, not a bare re-derivation: base={base_axis}, "
        f"overlay={overlay_axis}"
    )
    assert overlay_axis.get("labelPadding") == base_axis.get("labelPadding"), (
        f"shared-scale overlay axis must match the base's own measured "
        f"labelPadding, not a bare re-derivation: base={base_axis}, "
        f"overlay={overlay_axis}"
    )
