"""Tests for the AXIS_LABEL_COLLISION render-warning detector.

Policy-only: reads the collision fact the render captured
(``WarningContext.axis_label_collisions``). Capturing that fact from a real
render is covered by ``tests/core/render/chart/test_axis_label_collision_capture.py``.
"""

from __future__ import annotations

from dbt_charts.core.compile.models.chart.normalized import LineChart
from dbt_charts.core.diagnostics import WARN_AXIS_LABEL_COLLISION
from dbt_charts.core.render.chart.axis_label_collision import AxisLabelCollision
from dbt_charts.core.render.warnings import (
    WarningContext,
    axis_label_collision as detector,
)

from ...core._board_utils import make_test_resolved_board, make_test_resolved_chart


def _ctx(collisions: dict[str, AxisLabelCollision]) -> WarningContext:
    chart = LineChart(id="c1", type="line", query_name="q", x="week", y="revenue")
    resolved = make_test_resolved_chart(chart, [])
    board = make_test_resolved_board(charts={resolved.id: resolved})
    return WarningContext(
        board_spec=board,
        chart_results={resolved.id: []},
        axis_label_collisions=collisions,
    )


def test_fires_when_axis_labels_collide() -> None:
    ctx = _ctx({"c1": AxisLabelCollision(field="week", label_count=13)})
    warnings = detector.detect(ctx)
    assert len(warnings) == 1
    w = warnings[0]
    assert w.code == WARN_AXIS_LABEL_COLLISION.code
    assert w.chart == "c1"
    assert w.path == "charts.c1.x"
    assert w.field == "week"
    assert "13" in w.message
    assert "week" in w.message
    assert w.fix is not None


def test_no_fire_when_no_collision_recorded() -> None:
    ctx = _ctx({})
    assert detector.detect(ctx) == []
