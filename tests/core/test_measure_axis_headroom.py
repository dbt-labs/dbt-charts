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
