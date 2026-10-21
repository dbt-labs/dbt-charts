"""Measure-axis headroom above (and below) the data marks.

Editorial rule (RJ, 2026-07-16): unless a chart is `stack: normalize`, data
marks should sit with ~8% breathing room from each visible axis edge.

Two formulas depending on the axis anchor:
- Zero-anchored (bars, narrow-ratio line/area/scatter): top-only multiplicative.
  domain_max = data_max * (1 + headroom), bottom stays at 0.
- Zoomed / non-zero-anchored (high-ratio line/area/scatter): symmetric span-relative.
  span = data_max - data_min
  domain_max = data_max + headroom * span
  domain_min = data_min - headroom * span

An authored `scale.domain` always wins; `stack: normalize` always skips headroom.

Covers: ScaleStyle.headroom validation, the resolve()-baked domain_max/domain_min,
and each emitter (bar vertical/horizontal, stacked bar, line, area, scatter).
"""

from __future__ import annotations

from typing import Any
from unittest.mock import MagicMock

import pytest
from pydantic import TypeAdapter, ValidationError

from dbt_charts.core.compile.config import get_theme_style, reset_config
from dbt_charts.core.compile.models.chart.normalized import Chart
from dbt_charts.core.compile.models.query.normalized import SqlQuery
from dbt_charts.core.compile.models.style.authored import (
    AxisYStylePatch,
    BarChartStylePatch,
    BaseScaleStylePatch,
    ScaleContinuousStylePatch,
)
from dbt_charts.core.compile.models.style.theme.axis import BaseScaleStyle
from dbt_charts.core.compile.resolve import resolve
from dbt_charts.core.compile.resolve.style.board import resolve_style_and_context
from dbt_charts.core.diagnostics.chart_data import ChartDataError
from dbt_charts.core.execute.chart_resolution import resolve_chart_with_runtime_inputs
from dbt_charts.core.render.chart.vega_lite import generate_vega_lite_spec

from .conftest import chart_pane

_BOARD_STYLE, _BOARD_CTX = resolve_style_and_context(get_theme_style())

# A nice-round data max: nice_tick_values would already land a ladder rung
# exactly on it, so any pre-existing "headroom" would be a nice()-rounding
# accident, not our exact multiplier.
_ROUND_MAX = 20000.0
# An off-ladder data max: no nice tick step lands on it.
_OFF_LADDER_MAX = 18700.0
# Tight-zoom data: min/max ratio ≈ 0.98 → smart-zero fires zero:false.
# With span=200: domain_max=10016, domain_min=9784.
_TIGHT_MIN = 9800.0
_TIGHT_MAX = 10000.0
_HEADROOM = 0.08


@pytest.fixture(autouse=True)
def _reset():
    reset_config()
    yield
    reset_config()


def _bar_data(max_value: float) -> list[dict[str, Any]]:
    return [
        {"month": "2024-01", "y": max_value * 0.4},
        {"month": "2024-02", "y": max_value * 0.7},
        {"month": "2024-03", "y": max_value},
    ]


def _tight_data() -> list[dict[str, Any]]:
    """Data with min/max ratio ≈ 0.98 — smart-zero will set zero:false (zoomed axis)."""
    return [
        {"month": "2024-01", "y": _TIGHT_MIN},
        {"month": "2024-02", "y": _TIGHT_MIN + 100},
        {"month": "2024-03", "y": _TIGHT_MAX},
    ]


def _chart(
    chart_type: str,
    data_field_x: str,
    style: Any = None,
) -> Chart:
    return TypeAdapter(Chart).validate_python(
        {
            "id": "t",
            "type": chart_type,
            "x": data_field_x,
            "y": "y",
            "query": SqlQuery(sql="SELECT 1", source="src"),
            "query_name": "q",
            "style": style,
        }
    )


def _y_scale(chart_type: str, data: list[dict[str, Any]], style: Any = None) -> dict:
    chart = _chart(chart_type, "month", style)
    spec = generate_vega_lite_spec(
        chart, data, width=400, board_style=_BOARD_STYLE, chart_style_context=_BOARD_CTX
    )
    return spec["encoding"]["y"]["scale"]


def _layered_bar(layer_position: str | None = None) -> Chart:
    layer: dict[str, Any] = {
        "type": "line",
        "y": "goal",
        "query": "goal_query",
    }
    if layer_position is not None:
        layer["axis_y"] = {"position": layer_position}
    return TypeAdapter(Chart).validate_python(
        {
            "id": "layered",
            "type": "bar",
            "x": "month",
            "y": "actual",
            "query": SqlQuery(sql="SELECT 1", source="src"),
            "query_name": "actual_query",
            "layers": [layer],
        }
    )


# ── ScaleStyle.headroom validation ────────────────────────────────────────


def test_scale_style_rejects_negative_headroom() -> None:
    with pytest.raises(ValidationError, match="headroom"):
        BaseScaleStyle(headroom=-0.01)


def test_scale_style_accepts_zero_and_positive_headroom() -> None:
    assert BaseScaleStyle(headroom=0.0).headroom == 0.0
    assert BaseScaleStyle(headroom=0.2).headroom == 0.2


# ── Theme default ──────────────────────────────────────────────────────────


def test_theme_default_headroom_is_eight_percent() -> None:
    ay = _BOARD_CTX.axis_y
    assert ay.scale is not None
    assert ay.scale.headroom == _HEADROOM


# ── Bar (single-series, vertical) ──────────────────────────────────────────


def test_bar_domain_max_applies_default_headroom_round_data_max() -> None:
    scale = _y_scale("bar", _bar_data(_ROUND_MAX))
    assert scale["domainMax"] == pytest.approx(_ROUND_MAX * (1 + _HEADROOM))


def test_bar_domain_max_applies_default_headroom_off_ladder_data_max() -> None:
    scale = _y_scale("bar", _bar_data(_OFF_LADDER_MAX))
    assert scale["domainMax"] == pytest.approx(_OFF_LADDER_MAX * (1 + _HEADROOM))


def test_layer_query_widens_shared_y_domain() -> None:
    chart = _layered_bar()
    executor = MagicMock()
    executor.execute_query.return_value = [{"month": "2024-01", "goal": 250.0}]

    resolved = resolve_chart_with_runtime_inputs(
        chart,
        [{"month": "2024-01", "actual": 70.5}],
        _BOARD_CTX,
        400,
        executor,
        {},
        filter_variables=frozenset(),
        link_open="page",
    )

    assert resolved.style.axis_y.domain_max == pytest.approx(250.0 * (1 + _HEADROOM))


@pytest.mark.parametrize("position", ["left", "right"])
def test_pinned_axis_layer_query_does_not_widen_primary_y_domain(
    position: str,
) -> None:
    chart = _layered_bar(position)
    executor = MagicMock()
    executor.execute_query.return_value = [{"month": "2024-01", "goal": 250.0}]

    resolved = resolve_chart_with_runtime_inputs(
        chart,
        [{"month": "2024-01", "actual": 70.5}],
        _BOARD_CTX,
        400,
        executor,
        {},
        filter_variables=frozenset(),
        link_open="page",
    )

    assert resolved.style.axis_y.domain_max == pytest.approx(70.5 * (1 + _HEADROOM))
    executor.execute_query.assert_called_once_with("goal_query", {})


def test_unpinned_layer_beside_a_pinned_one_does_not_widen_primary_y_domain() -> None:
    chart = TypeAdapter(Chart).validate_python(
        {
            "id": "layered",
            "type": "bar",
            "x": "month",
            "y": "actual",
            "query": SqlQuery(sql="SELECT 1", source="src"),
            "query_name": "actual_query",
            "layers": [
                {"type": "line", "y": "goal", "query": "goal_query"},
                {"type": "line", "y": "actual", "axis_y": {"position": "left"}},
            ],
        }
    )
    executor = MagicMock()
    executor.execute_query.return_value = [{"month": "2024-01", "goal": 250.0}]

    resolved = resolve_chart_with_runtime_inputs(
        chart,
        [{"month": "2024-01", "actual": 70.5}],
        _BOARD_CTX,
        400,
        executor,
        {},
        filter_variables=frozenset(),
        link_open="page",
    )

    assert resolved.style.axis_y.domain_max == pytest.approx(70.5 * (1 + _HEADROOM))


def test_supplied_datasets_must_cover_shared_layer_queries() -> None:
    chart = _layered_bar()
    base_data = [{"month": "2024-01", "actual": 70.5}]

    with pytest.raises(ChartDataError, match="goal_query"):
        resolve(
            chart,
            base_data,
            chart_style_context=_BOARD_CTX,
            datasets={"actual_query": base_data},
        )


def test_bar_headroom_zero_pins_domain_max_at_the_data_max() -> None:
    """headroom=0 pins domainMax exactly at the data max (no breathing room), so
    the rungs Vega draws are known at resolve."""
    style = BarChartStylePatch(
        axis_y=AxisYStylePatch(scale=BaseScaleStylePatch(headroom=0.0))
    )
    scale = _y_scale("bar", _bar_data(_ROUND_MAX), style)
    assert scale["domainMax"] == _ROUND_MAX, scale


def test_bar_all_negative_data_emits_no_domain_max() -> None:
    """A non-positive data max has no multiplicative 'top' — emitting it as
    domainMax on a zero-anchored bar would put the domain top below zero
    (contradictory spec: clips/inverts the [max, 0] region). VL must auto-fit."""
    data = [
        {"month": "2024-01", "y": -5000.0},
        {"month": "2024-02", "y": -18700.0},
        {"month": "2024-03", "y": -3000.0},
    ]
    scale = _y_scale("bar", data)
    assert "domainMax" not in scale, scale


def test_line_all_negative_data_gets_span_relative_bounds() -> None:
    """All-negative zoomed data: span-relative headroom expands both edges.
    data_min=-18700, data_max=-3000, span=15700 →
    domainMax = -3000 + 0.08*15700 = -1744; domainMin = -18700 - 0.08*15700 = -19956."""
    data = [
        {"month": "2024-01", "y": -5000.0},
        {"month": "2024-02", "y": -18700.0},
        {"month": "2024-03", "y": -3000.0},
    ]
    scale = _y_scale("line", data)
    assert scale["domainMax"] == pytest.approx(-1744.0), scale
    assert scale["domainMin"] == pytest.approx(-19956.0), scale


def test_bar_authored_domain_wins_over_headroom() -> None:
    style = BarChartStylePatch(
        axis_y=AxisYStylePatch(
            scale=BaseScaleStylePatch(
                continuous=ScaleContinuousStylePatch(domain=[0, 100])
            )
        )
    )
    scale = _y_scale("bar", _bar_data(_ROUND_MAX), style)
    assert scale == {"domain": [0, 100]}, scale


def test_bar_custom_headroom_overrides_theme_default() -> None:
    style = BarChartStylePatch(
        axis_y=AxisYStylePatch(scale=BaseScaleStylePatch(headroom=0.2))
    )
    scale = _y_scale("bar", _bar_data(_ROUND_MAX), style)
    assert scale["domainMax"] == pytest.approx(_ROUND_MAX * 1.2)


# ── Stacked bar (headroom on the stacked TOTAL max) ────────────────────────


def _stacked_bar_data() -> list[dict[str, Any]]:
    # Per-category stacked totals: Jan=120, Feb=200 -> stacked max is 200.
    return [
        {"month": "2024-01", "series": "A", "y": 70},
        {"month": "2024-01", "series": "B", "y": 50},
        {"month": "2024-02", "series": "A", "y": 90},
        {"month": "2024-02", "series": "B", "y": 110},
    ]


def _stacked_bar_chart(stack: str) -> Chart:
    return TypeAdapter(Chart).validate_python(
        {
            "id": "t",
            "type": "bar",
            "x": "month",
            "y": "y",
            "color": "series",
            "stack": stack,
            "query": SqlQuery(sql="SELECT 1", source="src"),
            "query_name": "q",
        }
    )


def test_stacked_bar_domain_max_applies_headroom_to_stack_total() -> None:
    chart = _stacked_bar_chart("zero")
    spec = generate_vega_lite_spec(
        chart,
        _stacked_bar_data(),
        width=400,
        board_style=_BOARD_STYLE,
        chart_style_context=_BOARD_CTX,
    )
    scale = chart_pane(spec)["encoding"]["y"]["scale"]
    assert scale["domainMax"] == pytest.approx(200 * (1 + _HEADROOM))


def test_stacked_bar_headroom_zero_pins_exact_stack_total() -> None:
    """Stacked bars pinned domainMax to the exact stacked total before headroom
    existed (VL's nice-rounding would otherwise add accidental top margin).
    headroom=0 must preserve that flush-exact-total contract, not drop the pin."""
    data = _stacked_bar_data()
    chart = TypeAdapter(Chart).validate_python(
        {
            "id": "t",
            "type": "bar",
            "x": "month",
            "y": "y",
            "color": "series",
            "stack": "zero",
            "query": SqlQuery(sql="SELECT 1", source="src"),
            "query_name": "q",
            "style": {"axis_y": {"scale": {"headroom": 0}}},
        }
    )
    spec = generate_vega_lite_spec(
        chart, data, width=400, board_style=_BOARD_STYLE, chart_style_context=_BOARD_CTX
    )
    scale = chart_pane(spec)["encoding"]["y"]["scale"]
    assert scale["domainMax"] == pytest.approx(200.0)


def test_stacked_bar_all_negative_data_never_gets_headroom_bound() -> None:
    """All-negative stacks have 0.0 positive stacked mass — there is no
    positive total for headroom to multiply. The resolve-baked
    stacked_domain_max must stay unset (headroom must not manufacture a
    bound); the emitter's pre-existing nice-tick-top fallback then puts the
    domain top at exactly 0 (the ladder top for a [-110, 0] extent), never
    at a negative headroom product like -110 * 1.08."""
    data = [
        {"month": "2024-01", "series": "A", "y": -70},
        {"month": "2024-01", "series": "B", "y": -50},
        {"month": "2024-02", "series": "A", "y": -90},
        {"month": "2024-02", "series": "B", "y": -110},
    ]
    chart = _stacked_bar_chart("zero")
    spec = generate_vega_lite_spec(
        chart, data, width=400, board_style=_BOARD_STYLE, chart_style_context=_BOARD_CTX
    )
    scale = spec["encoding"]["y"].get("scale", {})
    assert scale.get("domainMax", 0.0) == pytest.approx(0.0), scale


def test_stacked_bar_normalize_skips_headroom() -> None:
    chart = _stacked_bar_chart("normalize")
    spec = generate_vega_lite_spec(
        chart,
        _stacked_bar_data(),
        width=400,
        board_style=_BOARD_STYLE,
        chart_style_context=_BOARD_CTX,
    )
    scale = chart_pane(spec)["encoding"]["y"].get("scale", {})
    assert "domainMax" not in scale, scale


# ── Horizontal bar (measure axis resolves through the same axis_y path) ────


def test_horizontal_bar_measure_axis_gets_headroom() -> None:
    style = BarChartStylePatch(orientation="horizontal")
    chart = _chart("bar", "month", style)
    data = _bar_data(_ROUND_MAX)
    spec = generate_vega_lite_spec(
        chart, data, width=400, board_style=_BOARD_STYLE, chart_style_context=_BOARD_CTX
    )
    scale = spec["encoding"]["x"]["scale"]
    assert scale["domainMax"] == pytest.approx(_ROUND_MAX * (1 + _HEADROOM))


# ── Line / scatter ──────────────────────────────────────────────────────────
#
# _bar_data(_OFF_LADDER_MAX) has min=7480, max=18700, ratio≈0.4 > 0.25 →
# smart-zero fires zero:false (non-zero-anchored / zoomed) for line/scatter.
# span = 18700 - 7480 = 11220
# domain_max = 18700 + 0.08 * 11220 = 19597.6
# domain_min = 7480 - 0.08 * 11220 = 6582.4
#
# Area is excluded from this ratio-driven zoomed-axis group: it always
# zero-anchors positive data regardless of ratio (see
# test_area_zero_anchored_top_only_headroom below), so it takes the same
# top-only multiplicative headroom path as bar, not this symmetric one.


def test_line_zoomed_axis_span_relative_symmetric_headroom() -> None:
    """Non-zero-anchored line: both edges expand by headroom * span."""
    scale = _y_scale("line", _bar_data(_OFF_LADDER_MAX))
    span = _OFF_LADDER_MAX - _OFF_LADDER_MAX * 0.4
    assert scale["domainMax"] == pytest.approx(_OFF_LADDER_MAX + _HEADROOM * span)
    assert scale["domainMin"] == pytest.approx(_OFF_LADDER_MAX * 0.4 - _HEADROOM * span)


def test_area_zero_anchored_top_only_headroom() -> None:
    """Area at the same ratio (>0.25) that zooms line/scatter still
    zero-anchors: top-only multiplicative headroom, floor pinned at 0."""
    scale = _y_scale("area", _bar_data(_OFF_LADDER_MAX))
    assert scale["zero"] is True
    assert scale["domainMin"] == 0.0
    assert scale["domainMax"] == pytest.approx(_OFF_LADDER_MAX * (1 + _HEADROOM))


def test_scatter_zoomed_axis_span_relative_symmetric_headroom() -> None:
    """Non-zero-anchored scatter: both edges expand by headroom * span."""
    chart = _chart("scatter", "month", None)
    data = _bar_data(_OFF_LADDER_MAX)
    spec = generate_vega_lite_spec(
        chart, data, width=400, board_style=_BOARD_STYLE, chart_style_context=_BOARD_CTX
    )
    scale = spec["encoding"]["y"]["scale"]
    span = _OFF_LADDER_MAX - _OFF_LADDER_MAX * 0.4
    assert scale["domainMax"] == pytest.approx(_OFF_LADDER_MAX + _HEADROOM * span)
    assert scale["domainMin"] == pytest.approx(_OFF_LADDER_MAX * 0.4 - _HEADROOM * span)


# ── Tight-zoom regression (reviewer's example: [9800,9900,10000]) ──────────


def test_tight_zoom_line_symmetric_span_headroom() -> None:
    """Tight-zoom data triggers span-relative symmetric headroom on both edges.
    span=200 → domain_max=10016, domain_min=9784.
    The mark at y=10000 sits at (10000-9784)/(10016-9784) = 216/232 ≈ 93% up —
    the correct editorial position, not 20% as the old multiplicative formula gave."""
    scale = _y_scale("line", _tight_data())
    span = _TIGHT_MAX - _TIGHT_MIN  # 200
    assert scale["domainMax"] == pytest.approx(_TIGHT_MAX + _HEADROOM * span)
    assert scale["domainMin"] == pytest.approx(_TIGHT_MIN - _HEADROOM * span)


def test_tight_zoom_scatter_symmetric_span_headroom() -> None:
    """Same tight-zoom correctness check for scatter."""
    chart = _chart("scatter", "month", None)
    spec = generate_vega_lite_spec(
        chart,
        _tight_data(),
        width=400,
        board_style=_BOARD_STYLE,
        chart_style_context=_BOARD_CTX,
    )
    scale = spec["encoding"]["y"]["scale"]
    span = _TIGHT_MAX - _TIGHT_MIN  # 200
    assert scale["domainMax"] == pytest.approx(_TIGHT_MAX + _HEADROOM * span)
    assert scale["domainMin"] == pytest.approx(_TIGHT_MIN - _HEADROOM * span)


# ── Anchored (close-to-zero) regression: no downward floor expansion ────────
#
# Data: min=6_000, max=33_000 → ratio ≈ 0.18 < 0.25 → smart-zero keeps chart
# anchored at zero (no explicit zero:false).  Headroom is top-only (multiplicative);
# domain_min must NOT be set — the floor stays at 0, not pushed below it.
#
# (Single-metric) line's floor still visually lands at 0 via the baseline-rule
# feature's own ``datum: 0`` layer — a separate mechanism from this domainMin
# pin. Multi-metric line can't use that feature (_measure_field has no single
# field name for a list ``y``), so resolve/line.py bakes an explicit
# domainMin for THAT case only (test_measure_axis_tick_ladder_parity.py
# covers it) — scoped narrowly to
# avoid moving this (and every other) single-metric line's domain here.

_ANCHORED_MIN = 6_000.0
_ANCHORED_MAX = 33_000.0


def _anchored_data() -> list[dict[str, Any]]:
    """Revenue-style data close to zero: ratio ≈ 0.18, anchored axis."""
    return [
        {"month": "2024-01", "y": _ANCHORED_MIN},
        {"month": "2024-02", "y": 20_000.0},
        {"month": "2024-03", "y": _ANCHORED_MAX},
    ]


def test_line_anchored_close_to_zero_no_domain_min() -> None:
    """Line with ratio ≤ 0.25: smart-zero keeps the floor at 0.
    domain_min must be absent — no downward floor expansion."""
    scale = _y_scale("line", _anchored_data())
    assert "domainMin" not in scale, scale
    assert scale.get("domainMax") == pytest.approx(_ANCHORED_MAX * (1 + _HEADROOM))


def test_area_anchored_close_to_zero_floor_at_zero() -> None:
    """Area with ratio ≤ 0.25: floor pinned at exactly 0.0 (y_zero_scale closes
    the gap between 0 and the first tick), never pushed below zero by span-relative."""
    scale = _y_scale("area", _anchored_data())
    assert scale.get("domainMin") == pytest.approx(0.0), scale
    assert scale.get("domainMax") == pytest.approx(_ANCHORED_MAX * (1 + _HEADROOM))


def test_scatter_anchored_close_to_zero_no_domain_min() -> None:
    """Scatter with ratio ≤ 0.25: anchored at zero, domain_min absent."""
    chart = _chart("scatter", "month", None)
    spec = generate_vega_lite_spec(
        chart,
        _anchored_data(),
        width=400,
        board_style=_BOARD_STYLE,
        chart_style_context=_BOARD_CTX,
    )
    scale = spec["encoding"]["y"]["scale"]
    assert "domainMin" not in scale, scale
    assert scale.get("domainMax") == pytest.approx(_ANCHORED_MAX * (1 + _HEADROOM))
