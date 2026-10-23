"""Tests for the TABLE_CRAMPED render-warning detector.

Fires when a table's columns were divided into less width than their content
demanded and headers wrapped onto a second line — the width rung of the table
degradation ladder (the height rung is TABLE_PAGE_SQUEEZED's). The detector is
policy-only: it reads the cramping the renderer recorded
(``WarningContext.table_crampings``). Capturing that state from a real render
is covered by ``tests/core/render/chart/test_table_cramping_capture.py``.
"""

from __future__ import annotations

from dbt_charts.core.compile.config import get_theme_style
from dbt_charts.core.compile.models.chart.normalized import TableChart
from dbt_charts.core.compile.models.query.normalized import SqlQuery
from dbt_charts.core.compile.resolve import resolve
from dbt_charts.core.compile.resolve.style.board import resolve_chart_style_context
from dbt_charts.core.diagnostics import WARN_TABLE_CRAMPED
from dbt_charts.core.render.chart.table_overflow import TableCramping, TableOverflow
from dbt_charts.core.render.warnings import (
    WarningContext,
    table_cramped as detector,
)

from ...core._board_utils import make_test_resolved_board

_BOARD = resolve_chart_style_context(get_theme_style())

_BOARD_WIDTH = 1000.0


def _table_ctx() -> tuple[str, WarningContext]:
    chart = resolve(
        TableChart(
            id="services",
            title="Service Summary",
            query=SqlQuery(sql="SELECT 1", source="test"),
            query_name="q",
            type="table",
        ),
        [{"service": "sync", "cost": 1}],
        chart_style_context=_BOARD,
    )
    board = make_test_resolved_board(charts={chart.id: chart}, width=_BOARD_WIDTH)
    return chart.id, WarningContext(
        board_spec=board,
        chart_results={chart.id: []},
    )


def _cramping(
    required: float,
    available: float,
    wrapped: int = 3,
    columns: int = 10,
    suggested: float = 1350.0,
) -> TableCramping:
    return TableCramping(
        required_width=required,
        available_width=available,
        wrapped_headers=wrapped,
        column_count=columns,
        suggested_width=suggested,
    )


def _suggested_width(fix: str | None) -> float:
    assert fix is not None
    return float(fix.split("style.frame.width to about ")[1].split("px")[0])


def test_fires_on_shortfall_with_wrapped_headers() -> None:
    chart_id, ctx = _table_ctx()
    ctx = ctx.model_copy(
        update={"table_crampings": {chart_id: _cramping(800.0, 700.0)}}
    )
    warnings = detector.detect(ctx)
    assert len(warnings) == 1
    w = warnings[0]
    assert w.code == WARN_TABLE_CRAMPED.code
    assert w.chart == chart_id
    assert "3 of 10" in w.message
    assert "800px" in w.message and "700px" in w.message
    assert w.fix is not None


def test_the_engine_suggested_width_is_used_as_recorded() -> None:
    chart_id, ctx = _table_ctx()
    cramping = _cramping(800.0, 700.0).model_copy(update={"suggested_width": 1350.0})
    ctx = ctx.model_copy(update={"table_crampings": {chart_id: cramping}})
    assert _suggested_width(detector.detect(ctx)[0].fix) == 1350.0


def test_sub_pixel_shortfall_is_measurement_noise_not_a_warning() -> None:
    """Below 1px the message would read two equal rounded widths and demand
    +50px of board for it — noise the author cannot see, so nothing fires."""
    chart_id, ctx = _table_ctx()
    ctx = ctx.model_copy(
        update={"table_crampings": {chart_id: _cramping(700.3, 700.0)}}
    )
    assert detector.detect(ctx) == []


def test_small_real_shortfall_still_suggests_a_larger_width() -> None:
    """A no-op suggestion (the width already set) is worse than none."""
    chart_id, ctx = _table_ctx()
    ctx = ctx.model_copy(
        update={"table_crampings": {chart_id: _cramping(702.0, 700.0)}}
    )
    warnings = detector.detect(ctx)
    assert len(warnings) == 1
    assert _suggested_width(warnings[0].fix) > _BOARD_WIDTH


def test_wrapped_headers_without_shortfall_do_not_fire() -> None:
    chart_id, ctx = _table_ctx()
    ctx = ctx.model_copy(
        update={"table_crampings": {chart_id: _cramping(600.0, 700.0)}}
    )
    assert detector.detect(ctx) == []


def test_shortfall_without_wrapped_headers_does_not_fire() -> None:
    chart_id, ctx = _table_ctx()
    ctx = ctx.model_copy(
        update={"table_crampings": {chart_id: _cramping(800.0, 700.0, wrapped=0)}}
    )
    assert detector.detect(ctx) == []


def test_overflowing_table_is_columns_overflows_case_not_cramping() -> None:
    """A table that paints past its slot is TABLE_COLUMNS_OVERFLOW's; firing
    cramping too would describe one defect twice with a weaker fix."""
    chart_id, ctx = _table_ctx()
    ctx = ctx.model_copy(
        update={
            "table_crampings": {chart_id: _cramping(800.0, 700.0)},
            "table_overflows": {
                chart_id: TableOverflow(required_width=800.0, available_width=700.0)
            },
        }
    )
    assert detector.detect(ctx) == []


def test_non_positive_budget_is_skipped() -> None:
    """A slot narrower than its own chrome has no meaningful width arithmetic."""
    chart_id, ctx = _table_ctx()
    ctx = ctx.model_copy(
        update={"table_crampings": {chart_id: _cramping(100.0, -29.6)}}
    )
    assert detector.detect(ctx) == []


def test_empty_capture_yields_nothing() -> None:
    _chart_id, ctx = _table_ctx()
    assert detector.detect(ctx) == []
