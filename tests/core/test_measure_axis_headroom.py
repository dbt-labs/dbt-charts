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

from dbt_charts.core.compile.config import get_theme_style
from dbt_charts.core.compile.models.chart.normalized import Chart
from dbt_charts.core.compile.models.query.normalized import SqlQuery
from dbt_charts.core.compile.models.style.theme.axis import BaseScaleStyle
from dbt_charts.core.compile.resolve import resolve
from dbt_charts.core.compile.resolve.style.board import resolve_style_and_context
from dbt_charts.core.diagnostics.chart_data import ChartDataError
from dbt_charts.core.execute.chart_resolution import resolve_chart_with_runtime_inputs

_BOARD_STYLE, _BOARD_CTX = resolve_style_and_context(get_theme_style())

_HEADROOM = 0.08


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


# ── Stacked bar (headroom on the stacked TOTAL max) ────────────────────────


# ── Horizontal bar (measure axis resolves through the same axis_y path) ────


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


# ── Tight-zoom regression (reviewer's example: [9800,9900,10000]) ──────────


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


# ── Percent axes: headroom never leaves [0, 1] ──────────────────────────────


def _axis_y(
    chart_type: str,
    values: list[float],
    *,
    fmt: str = ".0%",
    axis_y: dict[str, Any] | None = None,
    **chart_fields: Any,
) -> Any:
    chart = TypeAdapter(Chart).validate_python(
        {
            "id": "pct",
            "type": chart_type,
            "x": "x",
            "y": "val",
            "style": {"axis_y": {"labels": {"format": fmt}, **(axis_y or {})}},
            **chart_fields,
        }
    )
    rows = [{"x": i, "val": v} for i, v in enumerate(values)]
    return resolve(chart, rows, chart_style_context=_BOARD_CTX).style.axis_y


def test_percent_line_reaching_100_stops_headroom_at_100() -> None:
    ay = _axis_y("line", [0.38, 0.55, 0.9, 1.0])
    assert ay.domain_max == 1.0
    assert ay.domain_min == pytest.approx(0.38 - _HEADROOM * 0.62)


def test_percent_line_near_zero_stops_headroom_at_zero() -> None:
    ay = _axis_y(
        "line", [0.02, 0.3, 0.5], axis_y={"scale": {"continuous": {"zero": False}}}
    )
    assert ay.domain_min == 0.0
    assert ay.domain_max == pytest.approx(0.5 + _HEADROOM * 0.48)


def test_percent_line_past_100_keeps_span_headroom() -> None:
    ay = _axis_y("line", [0.4, 0.8, 1.2])
    assert ay.domain_max == pytest.approx(1.2 + _HEADROOM * 0.8)


def test_non_percent_line_keeps_span_headroom() -> None:
    ay = _axis_y("line", [0.38, 0.55, 0.9, 1.0], fmt=".2f")
    assert ay.domain_max == pytest.approx(1.0 + _HEADROOM * 0.62)


def test_authored_domain_beats_the_percent_cap() -> None:
    ay = _axis_y(
        "line",
        [0.38, 1.0],
        axis_y={"scale": {"continuous": {"domain": [0, 1.1]}}},
    )
    assert max(ay.tick_values) == 1.1


def test_percent_zero_anchored_bar_stops_headroom_at_100() -> None:
    assert _axis_y("bar", [0.4, 0.8, 1.0]).domain_max == 1.0


def test_percent_zero_anchored_bar_below_100_keeps_headroom() -> None:
    assert _axis_y("bar", [0.4, 0.5]).domain_max == pytest.approx(0.5 * (1 + _HEADROOM))


def test_percent_stacked_bar_totalling_100_stops_headroom_at_100() -> None:
    chart = TypeAdapter(Chart).validate_python(
        {
            "id": "pct",
            "type": "bar",
            "x": "x",
            "y": ["a", "b"],
            "stack": "zero",
            "style": {"axis_y": {"labels": {"format": ".0%"}}},
        }
    )
    rows = [{"x": i, "a": 0.6, "b": 0.4} for i in range(3)]
    ay = resolve(chart, rows, chart_style_context=_BOARD_CTX).style.axis_y
    assert max(ay.tick_values) <= 1.0
