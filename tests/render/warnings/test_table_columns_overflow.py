"""Tests for the TABLE_COLUMNS_OVERFLOW render-warning detector.

Fires when a table needs more width than the slot it was given — its columns'
minimum readable widths sum past the tile, so the table overflows its slot and
columns clip or collide.

The detector is policy-only: it reads the real, post-layout overflow the renderer
recorded (``WarningContext.table_overflows``) and emits one warning per table.
There is no magnitude threshold — the renderer's own widen boundary is the line.
Capturing that state from a real render is covered by
``test_table_overflow_capture.py``.
"""

from __future__ import annotations

from dbt_charts.core.compile.config import get_theme_style
from dbt_charts.core.compile.models.chart.normalized import TableChart
from dbt_charts.core.compile.models.query.normalized import SqlQuery
from dbt_charts.core.compile.resolve import resolve
from dbt_charts.core.compile.resolve.style.board import resolve_chart_style_context
from dbt_charts.core.diagnostics import WARN_TABLE_COLUMNS_OVERFLOW
from dbt_charts.core.render.chart.table_overflow import TableOverflow
from dbt_charts.core.render.warnings import (
    WarningContext,
    table_columns_overflow as detector,
)

from ...core._board_utils import make_test_resolved_board

_BOARD = resolve_chart_style_context(get_theme_style())


def _table_board() -> tuple[str, WarningContext]:
    chart = resolve(
        TableChart(
            id="orgs",
            title="Weekly Skip Rate, by Org",
            query=SqlQuery(sql="SELECT 1", source="test"),
            query_name="q",
            type="table",
        ),
        [{"org_id": "act_x", "skips": 1}],
        chart_style_context=_BOARD,
    )
    board = make_test_resolved_board(charts={chart.id: chart})
    return chart.id, WarningContext(
        board_spec=board,
        chart_results={chart.id: []},
    )


def test_fires_when_table_overflows_slot() -> None:
    """A recorded overflow produces one warning naming the table and both widths."""
    chart_id, ctx = _table_board()
    ctx = ctx.model_copy(
        update={
            "table_overflows": {
                chart_id: TableOverflow(required_width=479.0, available_width=220.0)
            }
        }
    )
    warnings = detector.detect(ctx)
    assert len(warnings) == 1
    w = warnings[0]
    assert w.code == WARN_TABLE_COLUMNS_OVERFLOW.code
    assert w.chart == chart_id
    assert "479" in w.message and "220" in w.message
    assert w.fix is not None


def test_no_overflow_no_warning() -> None:
    """Empty capture (table fit its slot) yields nothing."""
    _chart_id, ctx = _table_board()
    assert detector.detect(ctx) == []


def test_one_warning_per_table() -> None:
    """Two overflowing tables produce two warnings, each keyed to its chart."""
    chart_id, ctx = _table_board()
    ctx = ctx.model_copy(
        update={
            "table_overflows": {
                chart_id: TableOverflow(required_width=479.0, available_width=220.0),
                "other": TableOverflow(required_width=300.0, available_width=150.0),
            }
        }
    )
    warnings = detector.detect(ctx)
    assert {w.chart for w in warnings} == {chart_id, "other"}
