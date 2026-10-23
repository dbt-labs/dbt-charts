"""Tests for the LAYER_X_DOMAIN_PAINT_ORDER render-warning detector.

The detector is policy-only: ``rendered_x_domain`` records the charts whose
shared x domain it declined to reorder, and this turns each record into a
diagnostic. What *earns* a record is pinned in
``tests/core/render/chart/test_x_domain_paint_order_capture.py``; these tests
pin the reporting — one diagnostic per record, and what the message says.
"""

from __future__ import annotations

from dbt_charts.core.render.chart.x_domain_paint_order import XDomainPaintOrder
from dbt_charts.core.render.warnings import (
    WarningContext,
    layer_x_domain_paint_order as detector,
)

from ...core._board_utils import make_test_resolved_board


def _ctx(**recorded: XDomainPaintOrder) -> WarningContext:
    return WarningContext(
        board_spec=make_test_resolved_board(charts={}),
        chart_results={},
        x_domain_paint_orders=dict(recorded),
    )


def test_reports_a_recorded_paint_order() -> None:
    ctx = _ctx(c1=XDomainPaintOrder(x_field="month", layer_only=("Feb", "Apr")))
    (diag,) = detector.detect(ctx)
    assert diag.code == "WARN-LAYER-X-DOMAIN-PAINT-ORDER"
    assert diag.chart == "c1"
    assert diag.field == "month"
    assert diag.path == "charts.c1.x"
    assert "2 x categories ('Feb', 'Apr')" in diag.message


def test_one_category_reads_singular() -> None:
    ctx = _ctx(c1=XDomainPaintOrder(x_field="region", layer_only=("EMEA",)))
    (diag,) = detector.detect(ctx)
    assert "1 x category ('EMEA')" in diag.message


def test_long_layer_contributions_are_sampled() -> None:
    ctx = _ctx(
        c1=XDomainPaintOrder(x_field="month", layer_only=("a", "b", "c", "d", "e"))
    )
    (diag,) = detector.detect(ctx)
    assert "5 x categories ('a', 'b', 'c', …)" in diag.message
    assert "'d'" not in diag.message


def test_each_recorded_chart_gets_its_own_diagnostic() -> None:
    ctx = _ctx(
        c1=XDomainPaintOrder(x_field="month", layer_only=("Feb",)),
        c2=XDomainPaintOrder(x_field="region", layer_only=("EMEA",)),
    )
    assert [d.chart for d in detector.detect(ctx)] == ["c1", "c2"]


def test_silent_when_nothing_was_recorded() -> None:
    """Every chart whose domain the render path could order records nothing."""
    assert detector.detect(_ctx()) == []
