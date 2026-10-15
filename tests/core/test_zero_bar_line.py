"""Tests for zero bar line: palette color propagation and tick-coverage extension."""

from __future__ import annotations

import pytest

from dbt_charts.core.compile.config import get_theme_style
from dbt_charts.core.compile.models.chart.normalized import BarChart
from dbt_charts.core.compile.resolve import resolve
from dbt_charts.core.compile.resolve.style.board import (
    resolve_chart_style_context,
    resolve_style_and_context,
)
from dbt_charts.core.render.chart.vega_lite import render_resolved_chart

from .conftest import chart_pane

_BOARD_RS, _BOARD_CTX = resolve_style_and_context(get_theme_style())


def _get_zero_rule_layer(spec: dict) -> dict | None:
    """Return the zero-rule layer from a layered VL spec, or None.

    Handles both vertical bars (datum on y-axis) and horizontal bars (datum on x-axis).
    Endpoint labels may have wrapped the chart in hconcat/vconcat — unwrap to
    the real chart pane first (chart_pane() is a no-op otherwise).
    """
    layers = chart_pane(spec).get("layer", [])
    for layer in layers:
        mark = layer.get("mark", {})
        if isinstance(mark, dict) and mark.get("type") == "rule":
            enc = layer.get("encoding", {})
            y_datum = "y" in enc and enc["y"].get("datum") == 0
            x_datum = "x" in enc and enc["x"].get("datum") == 0
            if y_datum or x_datum:
                return layer
    return None


def _get_top_rule_layer(spec: dict) -> dict | None:
    """Return the top (100%) rule layer from a layered VL spec, or None.

    Mirror of `_get_zero_rule_layer` for the y=1 (normalized-stack ceiling)
    rule.
    """
    layers = chart_pane(spec).get("layer", [])
    for layer in layers:
        mark = layer.get("mark", {})
        if isinstance(mark, dict) and mark.get("type") == "rule":
            enc = layer.get("encoding", {})
            y_datum = "y" in enc and enc["y"].get("datum") == 1
            x_datum = "x" in enc and enc["x"].get("datum") == 1
            if y_datum or x_datum:
                return layer
    return None


def _make_spec_with_ticks_visible(
    chart_type: str = "bar",
    orientation: str = "vertical",
    tick_size: float = 6.0,
    chart_y_orient: str | None = None,
    chart_x_orient: str | None = None,
) -> dict:
    """Build a chart spec where chart-local style makes ticks visible (Layer 12).

    Drives the cascade end-to-end: the chart's ``style.axis_{x,y}.ticks.visible``
    patch overrides the theme's ``bar.axis_{x,y}.ticks.visible`` (Layer 4),
    which is the path real users follow when they author tick visibility.
    """
    from pydantic import TypeAdapter

    from dbt_charts.core.compile.models.chart.normalized import Chart
    from dbt_charts.core.compile.models.style.authored import (
        AreaChartStylePatch,
        AxisXStylePatch,
        AxisYStylePatch,
        BarChartStylePatch,
    )

    if orientation.startswith("horizontal"):
        axis_x_patch: dict = {
            "ticks": {"visible": True, "length": tick_size, "width": 1.0}
        }
        if chart_x_orient is not None:
            axis_x_patch["position"] = chart_x_orient
        bar_kwargs: dict = {
            "axis_x": AxisXStylePatch.model_validate(axis_x_patch),
        }
        if chart_y_orient is not None:
            bar_kwargs["axis_y"] = AxisYStylePatch(position=chart_y_orient)
        style: BarChartStylePatch | AreaChartStylePatch = BarChartStylePatch(
            orientation="horizontal", **bar_kwargs
        )
    else:
        axis_y_patch: dict = {
            "ticks": {"visible": True, "length": tick_size, "width": 1.5}
        }
        if chart_y_orient is not None:
            axis_y_patch["position"] = chart_y_orient
        kwargs: dict = {
            "axis_y": AxisYStylePatch.model_validate(axis_y_patch),
        }
        if chart_x_orient is not None:
            kwargs["axis_x"] = AxisXStylePatch(position=chart_x_orient)
        if chart_type == "area":
            style = AreaChartStylePatch(**kwargs)
        else:
            style = BarChartStylePatch(orientation="vertical", **kwargs)

    data = [{"x": "A", "y": 10}, {"x": "B", "y": -5}]
    chart = TypeAdapter(Chart).validate_python(
        {
            "id": "t",
            "type": chart_type,
            "x": "x",
            "y": "y",
            "query_name": "q",
            "style": style.model_dump(exclude_none=True),
        }
    )
    resolved_chart = resolve(chart, data, chart_style_context=_BOARD_CTX)
    return render_resolved_chart(
        resolved_chart, data, _BOARD_RS, width=400, height=300
    ).payload


def test_zero_rule_layer_is_last_in_spec():
    """Zero rule (discriminated by datum==0) is the last layer so it renders on top."""

    chart = BarChart(id="t", type="bar", x="x", y="y", query_name="q")
    data = [{"x": "A", "y": 10}, {"x": "B", "y": -5}]
    resolved = resolve(chart, data, chart_style_context=_BOARD_CTX)
    spec = render_resolved_chart(
        resolved, data, _BOARD_RS, width=400, height=300
    ).payload

    layers = spec.get("layer", [])
    assert layers, "Expected layered spec with zero rule"
    zero_layer = _get_zero_rule_layer(spec)
    assert zero_layer is not None, "Zero rule layer not found"
    assert zero_layer is layers[-1], (
        f"Zero rule must be the last layer; "
        f"found at {layers.index(zero_layer)}, last is {len(layers) - 1}"
    )


@pytest.mark.xfail(
    reason="V2 zero-rule emitter does not yet emit x2Offset/y2Offset for tick extension",
    strict=True,
)
@pytest.mark.parametrize(
    ("chart_type", "orientation", "chart_y_orient", "offset_key", "expected_offset"),
    [
        # Vertical charts: y-axis ticks extend horizontally outward.
        # Right-orient (default): mark-level x2Offset. This is safe because grouped
        # vertical bars emit xOffset (bar center shift) but never x2Offset, so there
        # is no encoding-level x2Offset neutralizer that could override it.
        # {expr: "width + N"} is NOT an alternative — vl-convert renders it as 0.
        # Left-orient: encoding.x = -tick_size, checked separately (mark.xOffset
        # would be silently overridden by the encoding.xOffset neutralizer).
        ("bar", "vertical", None, "x2Offset", 6.0),  # right-orient (default)
        ("area", "vertical", None, "x2Offset", 6.0),  # area follows same path
        # Horizontal bar: x-axis ticks extend past y=height (bottom) or above y=0 (top).
        # Bottom-orient: mark-level y2Offset — safe for the same reason as x2Offset
        # (no y2Offset neutralizer exists or is needed).
        # Top-orient: encoding.y = -tick_size, checked separately.
        ("bar", "horizontal", None, "y2Offset", 6.0),
    ],
    ids=[
        "vertical-bar-right",
        "area-right",
        "horizontal-bar",
    ],
)
def test_zero_rule_extends_past_axis_ticks_when_visible(
    chart_type: str,
    orientation: str,
    chart_y_orient: str | None,
    offset_key: str,
    expected_offset: float,
) -> None:
    """Axis ticks produce geometric stubs outside chart bounds; rule extends to cover them.

    End-of-rule extensions (right-orient x2Offset, bottom-orient y2Offset) use
    mark-level offsets because no encoding-level neutralizer exists for those
    channels: grouped bars emit xOffset/yOffset for bar center shift, but never
    x2Offset/y2Offset. Mark offsets are only overridable by same-named encoding
    properties, and those encoding properties are never set here.

    Start-of-rule extensions (left-orient, top-orient) MUST use encoding
    coordinates because the xOffset/yOffset neutralizers already exist and would
    silently win over mark.xOffset/yOffset.

    clip: False on the mark allows the rule to render past the plot boundary.
    """
    tick_size = 6.0
    chart_x_orient = "top" if orientation == "horizontal-top" else None
    spec = _make_spec_with_ticks_visible(
        chart_type, orientation, tick_size, chart_y_orient, chart_x_orient
    )

    rule_layer = _get_zero_rule_layer(spec)
    assert rule_layer is not None, (
        f"Expected zero rule layer ({chart_type}/{orientation}/orient={chart_y_orient})"
    )
    mark = rule_layer["mark"]
    assert mark.get(offset_key) == expected_offset, (
        f"Zero rule mark[{offset_key!r}]={mark.get(offset_key)!r} "
        f"!= {expected_offset!r} ({chart_type}/{orientation}/orient={chart_y_orient})"
    )
    assert mark.get("clip") is False, (
        f"Zero rule mark missing clip=False when ticks visible "
        f"({chart_type}/{orientation}): {mark}"
    )


@pytest.mark.xfail(
    reason="V2 zero-rule emitter does not yet emit encoding.x=-tick_size for left-orient extension",
    strict=True,
)
def test_zero_rule_left_orient_extends_via_encoding_x_not_mark_xoffset():
    """Left-oriented y-axis: tick extension uses encoding.x, not mark.xOffset.

    VL encoding properties override mark properties. Using mark.xOffset=-tick_size
    alongside encoding.xOffset={value:0} (the grouped-bar neutralizer) causes the
    encoding to win and the extension to be silently dropped. The fix moves the
    left-side extension into encoding.x directly.
    """
    tick_size = 6.0
    spec = _make_spec_with_ticks_visible(
        "bar", "vertical", tick_size, chart_y_orient="left"
    )
    rule = _get_zero_rule_layer(spec)
    assert rule is not None
    enc = rule["encoding"]
    mark = rule["mark"]
    assert enc.get("x") == {"value": -tick_size}, (
        f"Left-orient tick extension must be in encoding.x, not mark.xOffset; "
        f"got encoding.x={enc.get('x')!r}, mark.xOffset={mark.get('xOffset')!r}"
    )
    assert "xOffset" not in mark, (
        f"mark.xOffset must not be set for left-orient (encoding.xOffset neutralizer "
        f"would override it); got {mark.get('xOffset')!r}"
    )
    assert mark.get("clip") is False


@pytest.mark.xfail(
    reason="V2 zero-rule emitter does not yet emit encoding.y=-tick_size for top-orient extension",
    strict=True,
)
def test_zero_rule_horizontal_top_extends_via_encoding_y_not_mark_yoffset():
    """Horizontal bar with orient=top: tick extension uses encoding.y, not mark.yOffset.

    Same neutralizer conflict as the left-orient vertical case: encoding.yOffset={value:0}
    overrides mark.yOffset, so the top-side extension must use encoding.y instead.
    """
    tick_size = 6.0
    spec = _make_spec_with_ticks_visible(
        "bar", "horizontal-top", tick_size, chart_x_orient="top"
    )
    rule = _get_zero_rule_layer(spec)
    assert rule is not None
    enc = rule["encoding"]
    mark = rule["mark"]
    assert enc.get("y") == {"value": -tick_size}, (
        f"Top-orient tick extension must be in encoding.y, not mark.yOffset; "
        f"got encoding.y={enc.get('y')!r}, mark.yOffset={mark.get('yOffset')!r}"
    )
    assert "yOffset" not in mark, (
        f"mark.yOffset must not be set for top-orient (encoding.yOffset neutralizer "
        f"would override it); got {mark.get('yOffset')!r}"
    )
    assert mark.get("clip") is False


def test_zero_rule_no_offset_when_ticks_hidden():
    """No tick-coverage extension when axis ticks are hidden (default)."""
    from dbt_charts.core.compile.models.style.authored import (
        BarChartStylePatch,
    )

    chart = BarChart(
        id="t",
        type="bar",
        x="x",
        y="y",
        query_name="q",
        style=BarChartStylePatch(orientation="vertical"),
    )
    data = [{"x": "A", "y": 10}, {"x": "B", "y": -5}]
    resolved = resolve(chart, data, chart_style_context=_BOARD_CTX)
    spec = render_resolved_chart(
        resolved, data, _BOARD_RS, width=400, height=300
    ).payload

    rule_layer = _get_zero_rule_layer(spec)
    assert rule_layer is not None
    enc = rule_layer["encoding"]
    mark = rule_layer["mark"]
    # Encoding endpoints must be the plain pixel constants (no expr extension).
    assert enc.get("x") == {"value": 0}, (
        f"Unexpected x encoding when ticks hidden: {enc.get('x')}"
    )
    assert enc.get("x2") == {"value": "width"}, (
        f"Unexpected x2 encoding: {enc.get('x2')}"
    )
    assert "clip" not in mark, f"Unexpected clip key when ticks hidden: {mark}"


def test_zero_rule_no_offset_when_tick_size_zero():
    """tick_size=0 is treated as 'no stub to cover' — no extension applied."""
    spec = _make_spec_with_ticks_visible("bar", "vertical", tick_size=0.0)
    rule_layer = _get_zero_rule_layer(spec)
    assert rule_layer is not None
    mark = rule_layer["mark"]
    assert "x2Offset" not in mark and "xOffset" not in mark, (
        f"Unexpected mark offset for tick_size=0: {mark}"
    )
    assert "clip" not in mark, f"Unexpected clip key for tick_size=0: {mark}"


@pytest.mark.xfail(
    reason="V2 zero-rule emitter does not yet read chart-local axis ticks state for extension",
    strict=True,
)
def test_zero_rule_extends_when_chart_local_unhides_ticks():
    """Chart-local style.axis_y.ticks.visible=True (Layer 12) wins over theme bar.axis_y (Layer 4).

    Regression: profile._zero_rule_layer must read the merged-cascade ticks
    state, not resolved_style.axis_y.ticks (Layer 2 only). Without the fix,
    the rule fails to extend past the plot bounds even though Vega renders
    ticks because the chart-local override is applied at emit time.
    """
    from dbt_charts.core.compile.models.style.authored import (
        AxisYStylePatch,
        BarChartStylePatch,
    )

    style = BarChartStylePatch(
        orientation="vertical",
        axis_y=AxisYStylePatch.model_validate(
            {"ticks": {"visible": True, "size": 6.0}}
        ),
    )
    chart = BarChart(id="t", type="bar", x="x", y="y", query_name="q", style=style)
    data = [{"x": "A", "y": 10}, {"x": "B", "y": -5}]
    resolved = resolve(chart, data, chart_style_context=_BOARD_CTX)
    spec = render_resolved_chart(
        resolved, data, _BOARD_RS, width=400, height=300
    ).payload

    rule_layer = _get_zero_rule_layer(spec)
    assert rule_layer is not None
    mark = rule_layer["mark"]
    assert mark.get("x2Offset") == 6.0, (
        f"Zero rule must extend to cover ticks; got x2Offset={mark.get('x2Offset')!r}"
    )
    assert mark.get("clip") is False, (
        f"Zero rule mark must set clip=False when ticks visible: {mark}"
    )


class TestNormalizeStackTopRule:
    """100%-normalized stacked bar/area gets a top-rule at y=1, mirror of
    the zero-baseline rule at y=0. Visually anchors the "totality" edge so
    every column reads as filling the full domain.

    Fires only when ``stack: 'normalize'`` is set on the chart (not on
    stack: 'zero' or unstacked charts).
    """

    def _stacked_bar(self, stack_mode: str | None):

        return BarChart(
            id="t",
            type="bar",
            x="date",
            y="value",
            color="region",
            stack=stack_mode,
            query_name="q",
        )

    _DATA = [
        {"date": "2024-01", "region": "North", "value": 30000},
        {"date": "2024-01", "region": "South", "value": 25000},
        {"date": "2024-02", "region": "North", "value": 32000},
        {"date": "2024-02", "region": "South", "value": 24000},
    ]

    def _render(self, chart):

        resolved = resolve(chart, self._DATA, chart_style_context=_BOARD_CTX)
        return render_resolved_chart(
            resolved, self._DATA, _BOARD_RS, width=400, height=300
        ).payload

    def test_normalize_stack_emits_top_rule_at_y1(self):
        spec = self._render(self._stacked_bar("normalize"))
        rule = _get_top_rule_layer(spec)
        assert rule is not None, (
            "stack: normalize should emit a top-rule at y=1, mirror of the "
            "zero baseline at y=0. Got layers: "
            f"{[layer.get('mark', {}).get('type') for layer in spec.get('layer', [])]}"
        )
        # The rule's color should match the resolved grid.threshold.color (same
        # token used for the zero baseline — a single visual contract).
        assert rule["mark"].get("color"), (
            "top rule should carry an explicit color from the cascade"
        )

    def test_normalize_stack_top_rule_color_matches_zero_rule_color(self):
        """Both the zero and the top rule pull from grid.threshold.color — the
        cascade owns the single visual contract for these emphasis rules."""
        spec = self._render(self._stacked_bar("normalize"))
        zero_rule = _get_zero_rule_layer(spec)
        top_rule = _get_top_rule_layer(spec)
        assert zero_rule is not None and top_rule is not None
        assert zero_rule["mark"].get("color") == top_rule["mark"].get("color")

    def test_zero_stack_does_not_emit_top_rule(self):
        """Inverse: stack: zero (the default for color-encoded bar) should
        NOT emit a top rule. The top rule is normalize-mode-specific."""
        spec = self._render(self._stacked_bar(None))  # default = zero stack
        rule = _get_top_rule_layer(spec)
        assert rule is None, (
            f"non-normalized stack must not emit a top-rule; got {rule!r}"
        )


def test_zero_rule_color_matches_resolved_grid_zero_color():
    """Zero rule color in VL spec matches the resolved grid.threshold.color (not hardcoded)."""
    from dbt_charts.core.compile.resolve.style.axis_cascade import resolved_axis_style

    ctx = resolve_chart_style_context(get_theme_style())
    axis_y = resolved_axis_style(
        ctx, "axis_y", "quantitative", chart_type="", labeled=False
    )
    expected_color = axis_y.grid.threshold.color

    chart = BarChart(id="t", type="bar", x="x", y="y", query_name="q")
    data = [{"x": "A", "y": 10}, {"x": "B", "y": -5}]
    resolved = resolve(chart, data, chart_style_context=_BOARD_CTX)
    spec = render_resolved_chart(
        resolved, data, _BOARD_RS, width=400, height=300
    ).payload

    rule_layer = _get_zero_rule_layer(spec)
    assert rule_layer is not None
    mark = rule_layer["mark"]
    assert mark.get("color") == expected_color, (
        f"Zero rule color {mark.get('color')!r} != resolved zero.color {expected_color!r}"
    )
