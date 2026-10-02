"""Authored ``scale.domain`` governs quantitative axis tick values (V1 + V2).

When a quantitative measure axis pins both ``scale.domain`` and ``ticks.count``,
the renderer emits explicit ``axis.values`` (VL's ``tickCount`` is only advisory).
Those tick values must span the AUTHORED domain, not the data extent — otherwise
gridlines populate only the data band and the authored range renders half-blank.

Regression coverage for the split-brain bug where the scale spanned the authored
domain but the tick ladder was computed from ``min/max`` of the data. Numbers are
chosen so the authored domain endpoints are exact multiples of the nice tick step
(no floating-point rounding drift) and the domain-derived and data-derived tick
sets are unambiguously different.
"""

from __future__ import annotations

from typing import Any

import pytest
from pydantic import TypeAdapter

from dbt_charts.core.compile.resolve.chart._chart_rows import regroup
from dbt_charts.core.render.chart.spec import RenderBox

_DEFAULT_BOX = RenderBox(width=600.0, height=300.0)
from dbt_charts.core.compile.config import get_theme_style, reset_config
from dbt_charts.core.compile.models.chart.normalized import Chart
from dbt_charts.core.compile.models.query.normalized import SqlQuery
from dbt_charts.core.compile.models.style.authored import (
    AxisTicksStylePatch,
    AxisYStylePatch,
    BaseScaleStylePatch,
    LineChartStylePatch,
    ScaleContinuousStylePatch,
    ScatterChartStylePatch,
)
from dbt_charts.core.compile.resolve import resolve
from dbt_charts.core.compile.resolve.style.board import resolve_chart_style_context
from dbt_charts.core.render.chart.vega_lite import generate_vega_lite_spec

from .conftest import baked_format

_BOARD_STYLE = resolve_chart_style_context(get_theme_style())

# Data band [520, 580] sits INSIDE the authored domain [400, 600] (wider on both
# ends). Data-derived ticks would be [520, 540, 560, 580]; domain-derived ticks
# are [400, 450, 500, 550, 600].
_BAND_DATA = [
    {"month": "2024-01", "y": 520},
    {"month": "2024-02", "y": 540},
    {"month": "2024-03", "y": 560},
    {"month": "2024-04", "y": 580},
]
_BAND_MIN = 520

# Data spread [100, 900] is WIDER than the authored domain [400, 600] (narrower on
# both ends). Data-derived ticks would be [0, 500, 1000]; domain-derived ticks are
# confined to [400, 450, 500, 550, 600].
_SPREAD_DATA = [
    {"month": "2024-01", "y": 100},
    {"month": "2024-02", "y": 500},
    {"month": "2024-03", "y": 900},
]
_SPREAD_MIN = 100
_SPREAD_MAX = 900

_WIDE_DOMAIN = [400, 600]
_COUNT = 5


@pytest.fixture(autouse=True)
def _reset():
    reset_config()
    yield
    reset_config()


def _assert_spans_wide_domain(values: list[float]) -> None:
    """Ticks span the wider authored domain, reaching below the data band."""
    assert min(values) <= 400, values
    assert max(values) >= 600, values
    assert min(values) < _BAND_MIN, values


def _assert_confined_to_narrow_domain(values: list[float]) -> None:
    """Ticks stay inside the narrower authored domain, not the data extent."""
    assert min(values) >= 400, values
    assert max(values) <= 600, values
    assert min(values) > _SPREAD_MIN, values
    assert max(values) < _SPREAD_MAX, values


# ── V1 (production renderer) ──────────────────────────────────────────────────


def _v1_y_values(
    chart_type: str,
    family_patch: Any,
    data: list[dict[str, Any]],
) -> list[float]:
    chart = TypeAdapter(Chart).validate_python(
        {
            "id": "t",
            "type": chart_type,
            "x": "month",
            "y": "y",
            "query": SqlQuery(sql="SELECT 1", source="src"),
            "query_name": "q",
            "style": family_patch,
        }
    )
    resolve(chart, data, chart_style_context=_BOARD_STYLE)
    spec = generate_vega_lite_spec(chart, data, width=400)
    return spec["encoding"]["y"]["axis"]["values"]


def _line_patch(domain: list[int]) -> LineChartStylePatch:
    return LineChartStylePatch(
        axis_y=AxisYStylePatch(
            ticks=AxisTicksStylePatch(count=_COUNT),
            scale=BaseScaleStylePatch(
                continuous=ScaleContinuousStylePatch(domain=domain)
            ),
        )
    )


def test_v1_line_y_ticks_span_wider_authored_domain() -> None:
    _assert_spans_wide_domain(
        _v1_y_values("line", _line_patch(_WIDE_DOMAIN), _BAND_DATA)
    )


def test_v1_line_y_ticks_confined_to_narrow_authored_domain() -> None:
    _assert_confined_to_narrow_domain(
        _v1_y_values("line", _line_patch(_WIDE_DOMAIN), _SPREAD_DATA)
    )


def test_v1_scatter_y_ticks_span_wider_authored_domain() -> None:
    patch = ScatterChartStylePatch(
        axis_y=AxisYStylePatch(
            ticks=AxisTicksStylePatch(count=_COUNT),
            scale=BaseScaleStylePatch(
                continuous=ScaleContinuousStylePatch(domain=_WIDE_DOMAIN)
            ),
        )
    )
    _assert_spans_wide_domain(_v1_y_values("scatter", patch, _BAND_DATA))


# ── V2 (opt-in renderer) ──────────────────────────────────────────────────────


def _v2_axes(
    chart_type: str, domain: list[float], count: int, zero: bool | None = None
):
    import dataclasses

    from dbt_charts.core.compile.config import get_theme_style
    from dbt_charts.core.compile.resolve.chart._axes import _bake_cartesian_axes
    from dbt_charts.core.compile.resolve.style.axis_cascade import (
        AxisOverrides,
        build_resolved_axis,
    )
    from dbt_charts.core.numeric import nice_tick_values

    from .conftest import fixture_chart_for_type

    chart_style_context = resolve_chart_style_context(get_theme_style())
    overrides = AxisOverrides(
        y=AxisYStylePatch(
            ticks=AxisTicksStylePatch(count=count),
            scale=BaseScaleStylePatch(
                continuous=ScaleContinuousStylePatch(domain=domain, zero=zero)
            ),
        )
    )
    baked = _bake_cartesian_axes(
        chart_style_context,
        fixture_chart_for_type(chart_type),
        chart_type,
        "ordinal",
        "quantitative",
        overrides,
    )
    ax_merged, ay_merged, ax_band_position, ay_band_position = (
        baked.x.style,
        baked.y.style,
        baked.x.band_position,
        baked.y.band_position,
    )

    ax = build_resolved_axis(
        ax_merged,
        format=baked_format(ax_merged),
        band_position=ax_band_position,
        chart_id="test",
        formats=None,
    )
    ay = build_resolved_axis(
        ay_merged,
        format=baked_format(ay_merged),
        band_position=ay_band_position,
        chart_id="test",
        formats=None,
    )
    # D-03: tick_values are baked by resolve(); pre-populate here so emitter tests
    # that construct charts directly (bypassing resolve()) still exercise the path.
    tick_vals = tuple(nice_tick_values(float(domain[0]), float(domain[1]), count))
    ay = dataclasses.replace(ay, tick_values=tick_vals)
    return ax, ay


def _v2_base_kwargs() -> dict[str, Any]:
    from dbt_charts.core.compile.config import get_default_theme_name, get_theme_style
    from dbt_charts.core.compile.models.style.theme import PaddingStyle
    from dbt_charts.core.compile.resolve.style.board import resolve_chart_style_context

    charts = resolve_chart_style_context(get_theme_style(get_default_theme_name()))
    return {
        "variable_dependencies": frozenset(),
        "palette": (),
        "resolved_channels": {},
        "legend": charts.legend,
        "background": charts.background,
        "canvas": charts.ink_canvas,
        "title_style": charts.title,
        "layout_padding": PaddingStyle(left=0.0, right=0.0, top=0.0, bottom=0.0),
    }


def _v2_y_enc(chart, data: list[dict[str, Any]]) -> dict[str, Any]:
    from dbt_charts.core.render.chart.emitters import get_emitter

    return get_emitter(chart).emit(chart, _DEFAULT_BOX, regroup((), data)).encoding["y"]


def _v2_line(line_style, domain: list[int]):
    from dbt_charts.core.compile.models.chart.resolved import ResolvedLineChart

    ax, ay = _v2_axes("line", domain, _COUNT)
    return ResolvedLineChart(
        panel_axes=(),
        id="l",
        chart_type="line",
        x="month",
        y="y",
        style=line_style.model_copy(update={"axis_x": ax, "axis_y": ay}),
        **_v2_base_kwargs(),
    )


def test_v2_line_y_ticks_span_wider_authored_domain(line_style) -> None:
    values = _v2_y_enc(_v2_line(line_style, _WIDE_DOMAIN), _BAND_DATA)["axis"]["values"]
    _assert_spans_wide_domain(values)


def test_v2_line_y_ticks_confined_to_narrow_authored_domain(line_style) -> None:
    values = _v2_y_enc(_v2_line(line_style, _WIDE_DOMAIN), _SPREAD_DATA)["axis"][
        "values"
    ]
    _assert_confined_to_narrow_domain(values)


def test_v2_area_y_ticks_and_scale_span_authored_domain(area_style) -> None:
    from dbt_charts.core.compile.models.chart.resolved import ResolvedAreaChart

    ax, ay = _v2_axes("area", _WIDE_DOMAIN, _COUNT)
    chart = ResolvedAreaChart(
        panel_axes=(),
        id="a",
        chart_type="area",
        x="month",
        y="y",
        style=area_style.model_copy(update={"axis_x": ax, "axis_y": ay}),
        **_v2_base_kwargs(),
    )
    y_enc = _v2_y_enc(chart, _BAND_DATA)
    _assert_spans_wide_domain(y_enc["axis"]["values"])
    # The scale must also carry the domain, else VL clips the domain-spanning ticks.
    assert list(y_enc["scale"]["domain"]) == _WIDE_DOMAIN, y_enc["scale"]


def test_v2_bar_y_ticks_and_scale_span_authored_domain(bar_style) -> None:
    from dbt_charts.core.compile.models.chart.resolved import ResolvedBarChart

    ax, ay = _v2_axes("bar", _WIDE_DOMAIN, _COUNT)
    chart = ResolvedBarChart(
        panel_axes=(),
        id="b",
        chart_type="bar",
        x="month",
        y="y",
        style=bar_style.model_copy(update={"axis_x": ax, "axis_y": ay}),
        **_v2_base_kwargs(),
    )
    y_enc = _v2_y_enc(chart, _BAND_DATA)
    _assert_spans_wide_domain(y_enc["axis"]["values"])
    assert list(y_enc["scale"]["domain"]) == _WIDE_DOMAIN, y_enc["scale"]


def test_v2_stacked_bar_authored_domain_wins_over_stacked_totals(bar_style) -> None:
    """A stacked bar with an authored domain must not let the stacked-totals
    `domainMax` override the authored upper bound (which would desync the axis
    max from the tick ladder)."""
    from dbt_charts.core.compile.models.chart.resolved import ResolvedBarChart

    # Two series per month; stacked totals (1000) far exceed the authored domain's
    # upper bound (600). One row per (month, series) satisfies pre-aggregation
    # validation. Without the authored-domain guard, domainMax=1000.
    from dbt_charts.core.compile.models.chart.resolved._channel import (
        ResolvedStyleChannel,
    )

    stacked_data = [
        {"month": "2024-01", "series": "A", "y": 400},
        {"month": "2024-01", "series": "B", "y": 600},
        {"month": "2024-02", "series": "A", "y": 500},
        {"month": "2024-02", "series": "B", "y": 500},
    ]
    ax, ay = _v2_axes("bar", _WIDE_DOMAIN, _COUNT)
    color_ch = ResolvedStyleChannel(channel="color", mode="series", data_field="series")
    chart = ResolvedBarChart(
        panel_axes=(),
        id="b",
        chart_type="bar",
        x="month",
        y="y",
        color="series",
        stack="zero",
        style=bar_style.model_copy(update={"axis_x": ax, "axis_y": ay}),
        **{**_v2_base_kwargs(), "resolved_channels": {"color": color_ch}},
    )
    y_enc = _v2_y_enc(chart, stacked_data)
    _assert_spans_wide_domain(y_enc["axis"]["values"])
    assert list(y_enc["scale"]["domain"]) == _WIDE_DOMAIN, y_enc["scale"]
    assert "domainMax" not in y_enc["scale"], y_enc["scale"]


def test_v2_scatter_y_ticks_span_authored_domain(scatter_style) -> None:
    import dataclasses

    from dbt_charts.core.compile.models.chart.resolved import ResolvedScatterChart

    ax, ay = _v2_axes("scatter", _WIDE_DOMAIN, _COUNT)
    ay = dataclasses.replace(ay, is_quantitative=True, zero_anchored=True)
    chart = ResolvedScatterChart(
        panel_axes=(),
        id="s",
        chart_type="scatter",
        x="month",
        y="y",
        style=scatter_style.model_copy(update={"axis_x": ax, "axis_y": ay}),
        **_v2_base_kwargs(),
    )
    _assert_spans_wide_domain(_v2_y_enc(chart, _BAND_DATA)["axis"]["values"])


@pytest.mark.parametrize("chart_type", ["line", "area"])
def test_v2_zero_anchored_authored_domain_replaces_scale(chart_type, request) -> None:
    """With scale.zero=True baked alongside an authored domain, the emitted scale
    must be *exactly* the authored domain — not a merge that lets a rounded-down
    `domainMin` (nice_tick_values rounds outward, so the first tick can fall below
    the authored lower bound) override the authored lower bound in VL."""
    from dbt_charts.core.compile.models.chart.resolved import (
        ResolvedAreaChart,
        ResolvedLineChart,
    )

    # domain [0.95, 1.2] + count 7 → nice step 0.05 → first tick rounds to 0.9
    # (< 0.95). Under the old merge shape + zero:True, domainMin=0.9 would leak.
    style = request.getfixturevalue(f"{chart_type}_style")
    ax, ay = _v2_axes(chart_type, [0.95, 1.2], 7, zero=True)
    cls = ResolvedLineChart if chart_type == "line" else ResolvedAreaChart
    chart = cls(
        id="c",
        chart_type=chart_type,
        x="month",
        y="y",
        panel_axes=(),
        style=style.model_copy(update={"axis_x": ax, "axis_y": ay}),
        **_v2_base_kwargs(),
    )
    data = [{"month": f"2024-{i:02d}", "y": v} for i, v in enumerate([1.05, 1.13], 1)]
    scale = _v2_y_enc(chart, data)["scale"]
    assert scale == {"domain": [0.95, 1.2]}, scale
