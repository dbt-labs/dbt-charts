"""Regression tests: authored axis_y.scale.domain propagates to V2 VL scale and ticks.

Covers bar, area, and scatter emitters. Data inside [400, 600] authored domain
forces the tick span to come from the authored domain, not data extent.
"""

from __future__ import annotations

import dataclasses
from typing import Any

# Data fully inside the authored domain [400, 600] — should NOT drive tick span.
_BAND_DATA = [
    {"cat": "A", "val": 520.0},
    {"cat": "B", "val": 580.0},
]

# Scatter's canonical shape is a quantitative x — the same one `scatter_style`
# bakes its axis_x for. Pairing that axis with a category column is a
# combination resolve() never produces, and the emitter now says so.
_SCATTER_BAND_DATA = [
    {"x_val": 1.0, "val": 520.0},
    {"x_val": 2.0, "val": 580.0},
]

# Authored domain that is wider than the data extent above.
_AUTHORED_DOMAIN = [400.0, 600.0]


def _default_legend():
    from dbt_charts.core.compile.config import get_default_theme_name, get_theme_style
    from dbt_charts.core.compile.resolve.style.board import resolve_style

    return resolve_style(
        get_theme_style(get_default_theme_name())
    ).chart_defaults.legend


from dbt_charts.core.compile.models.style.theme import PaddingStyle
from dbt_charts.core.compile.resolve.chart._chart_rows import regroup
from dbt_charts.core.render.chart.spec import RenderBox

from ...conftest import baked_format

_DEFAULT_BOX = RenderBox(width=600.0, height=300.0)


def _default_charts():
    from dbt_charts.core.compile.config import get_default_theme_name, get_theme_style
    from dbt_charts.core.compile.resolve.style.board import resolve_style

    return resolve_style(get_theme_style(get_default_theme_name())).chart_defaults


_DEFAULT_CHARTS = _default_charts()
_ZERO_PADDING = PaddingStyle(left=0.0, right=0.0, top=0.0, bottom=0.0)
_C: dict[str, Any] = {
    "id": "test_chart",
    "variable_dependencies": frozenset(),
    "palette": (),
    "resolved_channels": {},
    "legend": _default_legend(),
    "background": _DEFAULT_CHARTS.background,
    "canvas": _DEFAULT_CHARTS.ink_canvas,
    "title_style": _DEFAULT_CHARTS.title,
    "layout_padding": _ZERO_PADDING,
}


def _axis_y_with_domain(chart_type: str, domain: list[float]) -> Any:
    """Return a baked axis_y for chart_type with scale.domain + pre-baked tick_values.

    D-03: tick_values are baked at resolve(); pre-populate here so emitter tests
    that construct charts directly (bypassing resolve()) still exercise the path.
    """
    from dbt_charts.core.compile.config import get_theme_style
    from dbt_charts.core.compile.models.style.resolved import (
        ResolvedScaleContinuousStyle,
        ResolvedScaleStyle,
    )
    from dbt_charts.core.compile.resolve.chart._axes import _bake_cartesian_axes
    from dbt_charts.core.compile.resolve.style.axis_cascade import (
        AxisOverrides,
        build_resolved_axis,
    )
    from dbt_charts.core.compile.resolve.style.board import resolve_chart_style_context
    from dbt_charts.core.numeric import nice_tick_values

    from ...conftest import fixture_chart_for_type

    chart_style_context = resolve_chart_style_context(get_theme_style())
    baked = _bake_cartesian_axes(
        chart_style_context,
        fixture_chart_for_type(chart_type),
        chart_type,
        "ordinal",
        "quantitative",
        AxisOverrides(),
    )
    ay_merged, ay_band_position = baked.y.style, baked.y.band_position

    ay = build_resolved_axis(
        ay_merged,
        format=baked_format(ay_merged),
        band_position=ay_band_position,
        chart_id="test",
        formats=None,
    )
    scale = ResolvedScaleStyle(continuous=ResolvedScaleContinuousStyle(domain=domain))
    ay = dataclasses.replace(ay, scale=scale)
    if ay.ticks.count is not None:
        tick_vals = tuple(
            nice_tick_values(float(domain[0]), float(domain[1]), ay.ticks.count)
        )
        ay = dataclasses.replace(ay, tick_values=tick_vals)
    return ay


# ---------------------------------------------------------------------------
# Bar
# ---------------------------------------------------------------------------


def test_v2_bar_authored_domain_emitted_in_vl_scale(bar_style) -> None:
    """BarEmitter sets scale.domain from authored axis_y.scale.domain."""
    from dbt_charts.core.compile.models.chart.resolved.bar import ResolvedBarChart
    from dbt_charts.core.render.chart.emitters.bar import BarEmitter

    ay = _axis_y_with_domain("bar", _AUTHORED_DOMAIN)
    chart = ResolvedBarChart(
        panel_axes=(),
        chart_type="bar",
        x="cat",
        y="val",
        style=bar_style.model_copy(update={"axis_y": ay}),
        **_C,
    )

    spec = BarEmitter().emit(chart, _DEFAULT_BOX, regroup((), _BAND_DATA))
    y_scale = spec.encoding["y"].get("scale", {})
    assert y_scale.get("domain") == _AUTHORED_DOMAIN, (
        f"VL scale.domain should be {_AUTHORED_DOMAIN}, got {y_scale}"
    )


def test_v2_bar_authored_domain_drives_tick_span(bar_style) -> None:
    """Bar tick values span the authored domain, not the narrower data extent."""
    from dbt_charts.core.compile.config import get_theme_style
    from dbt_charts.core.compile.models.chart.resolved.bar import ResolvedBarChart
    from dbt_charts.core.compile.models.style.resolved import (
        ResolvedScaleContinuousStyle,
        ResolvedScaleStyle,
    )
    from dbt_charts.core.compile.resolve.chart._axes import _bake_cartesian_axes
    from dbt_charts.core.compile.resolve.style.axis_cascade import (
        AxisOverrides,
        build_resolved_axis,
    )
    from dbt_charts.core.compile.resolve.style.board import resolve_chart_style_context
    from dbt_charts.core.numeric import nice_tick_values
    from dbt_charts.core.render.chart.emitters.bar import BarEmitter

    from ...conftest import fixture_chart_for_type

    chart_style_context = resolve_chart_style_context(get_theme_style())
    baked = _bake_cartesian_axes(
        chart_style_context,
        fixture_chart_for_type("bar"),
        "bar",
        "ordinal",
        "quantitative",
        AxisOverrides(),
    )
    ay_merged, ay_band_position = baked.y.style, baked.y.band_position

    ay_base = build_resolved_axis(
        ay_merged,
        format=baked_format(ay_merged),
        band_position=ay_band_position,
        chart_id="test",
        formats=None,
    )
    # D-03: set ticks.count, authored domain, AND pre-baked tick_values (as resolve() does).
    ay = dataclasses.replace(
        ay_base,
        ticks=dataclasses.replace(ay_base.ticks, count=6),
        scale=ResolvedScaleStyle(
            continuous=ResolvedScaleContinuousStyle(domain=_AUTHORED_DOMAIN)
        ),
        tick_values=tuple(
            nice_tick_values(float(_AUTHORED_DOMAIN[0]), float(_AUTHORED_DOMAIN[1]), 6)
        ),
    )
    chart = ResolvedBarChart(
        panel_axes=(),
        chart_type="bar",
        x="cat",
        y="val",
        style=bar_style.model_copy(update={"axis_y": ay}),
        **_C,
    )

    spec = BarEmitter().emit(chart, _DEFAULT_BOX, regroup((), _BAND_DATA))
    values = spec.encoding["y"].get("axis", {}).get("values")
    assert values is not None, (
        "axis.values should be set when ticks.count is configured"
    )
    # Ticks must reach the authored bounds, not just data [520, 580].
    assert min(values) <= _AUTHORED_DOMAIN[0], (
        f"Min tick {min(values)} should be <= authored domain lo {_AUTHORED_DOMAIN[0]}"
    )
    assert max(values) >= _AUTHORED_DOMAIN[1], (
        f"Max tick {max(values)} should be >= authored domain hi {_AUTHORED_DOMAIN[1]}"
    )


def test_v2_stacked_bar_authored_domain_wins_over_stacked_totals(bar_style) -> None:
    """Authored domain takes priority over stacked-bar domainMax."""
    from dbt_charts.core.compile.models.chart.resolved import ResolvedStyleChannel
    from dbt_charts.core.compile.models.chart.resolved.bar import ResolvedBarChart
    from dbt_charts.core.render.chart.emitters.bar import BarEmitter

    # Stack totals: A=520, B=580 → max 580, well inside authored [400, 600].
    # Without authored domain, domainMax=580; with it, explicit domain wins.
    ay = _axis_y_with_domain("bar", _AUTHORED_DOMAIN)
    chart = ResolvedBarChart(
        panel_axes=(),
        chart_type="bar",
        x="cat",
        y="val",
        stack="zero",
        style=bar_style.model_copy(update={"axis_y": ay}),
        resolved_channels={
            "color": ResolvedStyleChannel(
                channel="color", mode="series", data_field="cat"
            )
        },
        **{k: v for k, v in _C.items() if k != "resolved_channels"},
    )

    spec = BarEmitter().emit(chart, _DEFAULT_BOX, regroup((), _BAND_DATA))
    y_scale = spec.encoding["y"].get("scale", {})
    assert y_scale.get("domain") == _AUTHORED_DOMAIN, (
        f"Authored domain must win over stacked domainMax; got scale={y_scale}"
    )
    # Must NOT have a conflicting domainMax key alongside explicit domain.
    assert "domainMax" not in y_scale, (
        f"domainMax must be absent when authored domain is set; got {y_scale}"
    )


# ---------------------------------------------------------------------------
# Area
# ---------------------------------------------------------------------------


def test_v2_area_authored_domain_emitted_in_vl_scale(area_style) -> None:
    """emit_area sets scale.domain from authored axis_y.scale.domain."""
    from dbt_charts.core.compile.models.chart.resolved.area import ResolvedAreaChart
    from dbt_charts.core.render.chart.emitters.area import AreaEmitter

    ay = _axis_y_with_domain("area", _AUTHORED_DOMAIN)
    chart = ResolvedAreaChart(
        panel_axes=(),
        chart_type="area",
        x="cat",
        y="val",
        stack=None,
        style=area_style.model_copy(update={"axis_y": ay}),
        **_C,
    )

    spec = AreaEmitter().emit(chart, _DEFAULT_BOX, regroup((), _BAND_DATA))
    y_scale = spec.encoding["y"].get("scale", {})
    assert y_scale.get("domain") == _AUTHORED_DOMAIN, (
        f"VL scale.domain should be {_AUTHORED_DOMAIN}, got {y_scale}"
    )


def test_v2_area_authored_domain_drives_tick_span(area_style) -> None:
    """Area tick values span the authored domain, not the narrower data extent."""
    from dbt_charts.core.compile.config import get_theme_style
    from dbt_charts.core.compile.models.chart.resolved.area import ResolvedAreaChart
    from dbt_charts.core.compile.models.style.resolved import (
        ResolvedScaleContinuousStyle,
        ResolvedScaleStyle,
    )
    from dbt_charts.core.compile.resolve.chart._axes import _bake_cartesian_axes
    from dbt_charts.core.compile.resolve.style.axis_cascade import (
        AxisOverrides,
        build_resolved_axis,
    )
    from dbt_charts.core.compile.resolve.style.board import resolve_chart_style_context
    from dbt_charts.core.numeric import nice_tick_values
    from dbt_charts.core.render.chart.emitters.area import AreaEmitter

    from ...conftest import fixture_chart_for_type

    chart_style_context = resolve_chart_style_context(get_theme_style())
    baked = _bake_cartesian_axes(
        chart_style_context,
        fixture_chart_for_type("area"),
        "area",
        "ordinal",
        "quantitative",
        AxisOverrides(),
    )
    ay_merged, ay_band_position = baked.y.style, baked.y.band_position

    ay_base = build_resolved_axis(
        ay_merged,
        format=baked_format(ay_merged),
        band_position=ay_band_position,
        chart_id="test",
        formats=None,
    )
    ay = dataclasses.replace(
        ay_base,
        ticks=dataclasses.replace(ay_base.ticks, count=6),
        scale=ResolvedScaleStyle(
            continuous=ResolvedScaleContinuousStyle(domain=_AUTHORED_DOMAIN)
        ),
        tick_values=tuple(
            nice_tick_values(float(_AUTHORED_DOMAIN[0]), float(_AUTHORED_DOMAIN[1]), 6)
        ),
    )
    chart = ResolvedAreaChart(
        panel_axes=(),
        chart_type="area",
        x="cat",
        y="val",
        stack=None,
        style=area_style.model_copy(update={"axis_y": ay}),
        **_C,
    )

    spec = AreaEmitter().emit(chart, _DEFAULT_BOX, regroup((), _BAND_DATA))
    values = spec.encoding["y"].get("axis", {}).get("values")
    assert values is not None, (
        "axis.values should be set when ticks.count is configured"
    )
    assert min(values) <= _AUTHORED_DOMAIN[0], (
        f"Min tick {min(values)} should be <= authored domain lo {_AUTHORED_DOMAIN[0]}"
    )
    assert max(values) >= _AUTHORED_DOMAIN[1], (
        f"Max tick {max(values)} should be >= authored domain hi {_AUTHORED_DOMAIN[1]}"
    )


# ---------------------------------------------------------------------------
# Scatter
# ---------------------------------------------------------------------------


def test_v2_scatter_authored_domain_emitted_in_vl_scale(scatter_style) -> None:
    """emit_scatter preserves authored axis_y.scale.domain in VL scale (via SCALE_ENCODING_FIELD_MAP)."""
    from dbt_charts.core.compile.config import get_theme_style
    from dbt_charts.core.compile.models.chart.resolved.scatter import (
        ResolvedScatterChart,
    )
    from dbt_charts.core.compile.models.style.resolved import (
        ResolvedScaleContinuousStyle,
        ResolvedScaleStyle,
    )
    from dbt_charts.core.compile.resolve.chart._axes import _bake_cartesian_axes
    from dbt_charts.core.compile.resolve.style.axis_cascade import (
        AxisOverrides,
        build_resolved_axis,
    )
    from dbt_charts.core.compile.resolve.style.board import resolve_chart_style_context
    from dbt_charts.core.render.chart.emitters.scatter import ScatterEmitter

    from ...conftest import fixture_chart_for_type

    chart_style_context = resolve_chart_style_context(get_theme_style())
    baked = _bake_cartesian_axes(
        chart_style_context,
        fixture_chart_for_type("scatter"),
        "scatter",
        "quantitative",
        "quantitative",
        AxisOverrides(),
    )
    ay_merged, ay_band_position = baked.y.style, baked.y.band_position

    ay_base = build_resolved_axis(
        ay_merged,
        format=baked_format(ay_merged),
        band_position=ay_band_position,
        chart_id="test",
        is_quantitative=True,
        zero_anchored=True,
        formats=None,
    )
    ay = dataclasses.replace(
        ay_base,
        scale=ResolvedScaleStyle(
            continuous=ResolvedScaleContinuousStyle(domain=_AUTHORED_DOMAIN)
        ),
    )
    chart = ResolvedScatterChart(
        panel_axes=(),
        chart_type="scatter",
        x="x_val",
        y="val",
        style=scatter_style.model_copy(update={"axis_y": ay}),
        **_C,
    )

    spec = ScatterEmitter().emit(chart, _DEFAULT_BOX, regroup((), _SCATTER_BAND_DATA))
    y_scale = spec.encoding["y"].get("scale", {})
    assert y_scale.get("domain") == _AUTHORED_DOMAIN, (
        f"VL scale.domain should be {_AUTHORED_DOMAIN}, got {y_scale}"
    )


def test_v2_scatter_authored_domain_drives_tick_span(scatter_style) -> None:
    """Scatter tick values span the authored domain, not the narrower data extent."""
    from dbt_charts.core.compile.config import get_theme_style
    from dbt_charts.core.compile.models.chart.resolved.scatter import (
        ResolvedScatterChart,
    )
    from dbt_charts.core.compile.models.style.resolved import (
        ResolvedScaleContinuousStyle,
        ResolvedScaleStyle,
    )
    from dbt_charts.core.compile.resolve.chart._axes import _bake_cartesian_axes
    from dbt_charts.core.compile.resolve.style.axis_cascade import (
        AxisOverrides,
        build_resolved_axis,
    )
    from dbt_charts.core.compile.resolve.style.board import resolve_chart_style_context
    from dbt_charts.core.numeric import nice_tick_values
    from dbt_charts.core.render.chart.emitters.scatter import ScatterEmitter

    from ...conftest import fixture_chart_for_type

    chart_style_context = resolve_chart_style_context(get_theme_style())
    baked = _bake_cartesian_axes(
        chart_style_context,
        fixture_chart_for_type("scatter"),
        "scatter",
        "quantitative",
        "quantitative",
        AxisOverrides(),
    )
    ay_merged, ay_band_position = baked.y.style, baked.y.band_position

    ay_base = build_resolved_axis(
        ay_merged,
        format=baked_format(ay_merged),
        band_position=ay_band_position,
        chart_id="test",
        is_quantitative=True,
        zero_anchored=True,
        formats=None,
    )
    ay = dataclasses.replace(
        ay_base,
        ticks=dataclasses.replace(ay_base.ticks, count=6),
        scale=ResolvedScaleStyle(
            continuous=ResolvedScaleContinuousStyle(domain=_AUTHORED_DOMAIN)
        ),
        tick_values=tuple(
            nice_tick_values(float(_AUTHORED_DOMAIN[0]), float(_AUTHORED_DOMAIN[1]), 6)
        ),
    )
    chart = ResolvedScatterChart(
        panel_axes=(),
        chart_type="scatter",
        x="x_val",
        y="val",
        style=scatter_style.model_copy(update={"axis_y": ay}),
        **_C,
    )

    spec = ScatterEmitter().emit(chart, _DEFAULT_BOX, regroup((), _SCATTER_BAND_DATA))
    y_axis = spec.encoding["y"].get("axis", {})
    values = y_axis.get("values") if isinstance(y_axis, dict) else None
    assert values is not None, (
        "axis.values should be set when ticks.count is configured"
    )
    assert min(values) <= _AUTHORED_DOMAIN[0], (
        f"Min tick {min(values)} should be <= authored domain lo {_AUTHORED_DOMAIN[0]}"
    )
    assert max(values) >= _AUTHORED_DOMAIN[1], (
        f"Max tick {max(values)} should be >= authored domain hi {_AUTHORED_DOMAIN[1]}"
    )
