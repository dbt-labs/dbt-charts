"""Tests for the TABLE_PAGE_SQUEEZED render-warning detector.

Fires when a table's layout slot fits fewer rows than the page its own
pagination settled on — the sizer and the paginator disagreeing, with the
paginator winning in silence. The detector is policy-only: it reads the real
squeeze the renderer recorded (``WarningContext.table_page_squeezes``) and
emits one warning per squeezed table. Capturing that state from a real render
is covered by ``tests/core/render/chart/test_table_page_squeeze_capture.py``.
"""

from __future__ import annotations

from dbt_charts.core.compile.config import get_theme_style
from dbt_charts.core.compile.models.chart.normalized import TableChart
from dbt_charts.core.compile.models.query.normalized import SqlQuery
from dbt_charts.core.compile.resolve import resolve
from dbt_charts.core.compile.resolve.style.board import resolve_chart_style_context
from dbt_charts.core.diagnostics import WARN_TABLE_PAGE_SQUEEZED
from dbt_charts.core.render.chart.table_page_squeeze import TablePageSqueeze
from dbt_charts.core.render.warnings import (
    WarningContext,
    table_page_squeezed as detector,
)

from ...core._board_utils import make_test_resolved_board

_BOARD = resolve_chart_style_context(get_theme_style())


def _table_board() -> tuple[str, WarningContext]:
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
    board = make_test_resolved_board(charts={chart.id: chart})
    return chart.id, WarningContext(
        board_spec=board, chart_results={chart.id: []}, vega_specs={}
    )


def test_fires_when_slot_squeezed_the_page() -> None:
    """A recorded squeeze produces one warning naming the table and all three counts."""
    chart_id, ctx = _table_board()
    ctx = ctx.model_copy(
        update={
            "table_page_squeezes": {
                chart_id: TablePageSqueeze(drawn_rows=1, page_rows=4, total_rows=4)
            }
        }
    )
    warnings = detector.detect(ctx)
    assert len(warnings) == 1
    w = warnings[0]
    assert w.code == WARN_TABLE_PAGE_SQUEEZED.code
    assert w.chart == chart_id
    assert w.message.endswith(
        "the layout slot fits 1 of the 4 rows a page holds (4 rows total); "
        "the rest moved onto later pages."
    ), w.message
    assert w.fix is not None
    assert "page_rows: 1" in w.fix, w.fix


def test_no_squeeze_no_warning() -> None:
    """Empty capture (slot and page agreed) yields nothing."""
    _chart_id, ctx = _table_board()
    assert detector.detect(ctx) == []


def test_one_warning_per_table() -> None:
    """Two squeezed tables produce two warnings, each keyed to its chart."""
    chart_id, ctx = _table_board()
    ctx = ctx.model_copy(
        update={
            "table_page_squeezes": {
                chart_id: TablePageSqueeze(drawn_rows=1, page_rows=4, total_rows=4),
                "connections": TablePageSqueeze(
                    drawn_rows=13, page_rows=113, total_rows=113
                ),
            }
        }
    )
    warnings = detector.detect(ctx)
    assert {w.chart for w in warnings} == {chart_id, "connections"}
