"""The table renderer records real slot overflow into the sink.

TABLE_COLUMNS_OVERFLOW is driven by what the renderer actually did, not an
estimate. These tests render real tables and assert the sink reflects the
render: a table forced narrower than its columns need records an overflow; a
table with room records nothing; and with no sink open, recording is a no-op.
"""

from __future__ import annotations

from typing import Any

from dbt_charts.core.compile.config import get_theme_style
from dbt_charts.core.compile.models.chart.normalized import TableChart
from dbt_charts.core.compile.models.query.normalized import SqlQuery
from dbt_charts.core.compile.models.style.authored import TableChartStylePatch
from dbt_charts.core.compile.resolve import resolve
from dbt_charts.core.compile.resolve.style.board import resolve_style_and_context
from dbt_charts.core.render.chart.table import render_table_svg
from dbt_charts.core.render.chart.table_overflow import (
    TableOverflow,
    collect_table_overflows,
)

_BOARD_RS, _BOARD_CTX = resolve_style_and_context(get_theme_style())


def _data() -> list[dict[str, Any]]:
    # A long, unbreakable id column plus several more: their honored widths sum
    # well past a narrow slot, so the table must widen past it — the screenshot.
    return [
        {
            "org_id": "act_2zGAXPCi8w3MckVTMpVT3OSQ3Ni",
            "table_name": "weekly_skip_events",
            "skips": 175000,
            "clones": 226,
            "views": 439000000,
        }
        for _ in range(5)
    ]


def _render(width: float, sink_open: bool) -> dict[str, TableOverflow]:
    chart = resolve(
        TableChart(
            id="orgs",
            query=SqlQuery(sql="SELECT 1", source="test"),
            query_name="q",
            type="table",
        ),
        _data(),
        chart_style_context=_BOARD_CTX,
    )
    if not sink_open:
        render_table_svg(chart, _data(), width=width, board_style=_BOARD_RS)
        return {}
    with collect_table_overflows() as sink:
        render_table_svg(chart, _data(), width=width, board_style=_BOARD_RS)
        return sink


def test_cramped_table_records_overflow() -> None:
    """Given far less width than the columns need, the table overflows its slot."""
    sink = _render(width=220, sink_open=True)
    assert "orgs" in sink
    o = sink["orgs"]
    assert o.required_width > o.available_width
    # Given ~220px, the table needs a good deal more to fit these columns.
    assert o.available_width <= 220
    assert o.required_width > 220


def test_roomy_table_records_nothing() -> None:
    """Given ample width, the table fits its slot, so nothing is recorded."""
    sink = _render(width=2000, sink_open=True)
    assert "orgs" not in sink


def test_no_sink_is_noop() -> None:
    """Rendering without an open sink must not raise (recording is a no-op)."""
    assert _render(width=220, sink_open=False) == {}


def test_percentage_pinned_columns_summing_to_100_do_not_spuriously_overflow() -> None:
    """Columns pinned to percentages that sum to exactly 100% must never overflow.

    Sweep a range of slot widths so the test doesn't depend on any one budget
    happening to trigger the rounding direction that reproduces the bug.
    """
    data = [{"a": 1, "b": 2, "c": 3, "d": 4}]
    chart = resolve(
        TableChart(
            id="pct_cols",
            query=SqlQuery(sql="SELECT 1", source="test"),
            query_name="q",
            type="table",
            style=TableChartStylePatch(
                columns={
                    "a": {"width": "10%"},
                    "b": {"width": "20%"},
                    "c": {"width": "30%"},
                    "d": {"width": "40%"},
                }
            ),
        ),
        data,
        chart_style_context=_BOARD_CTX,
    )
    for width in range(200, 1200, 7):
        with collect_table_overflows() as sink:
            render_table_svg(chart, data, width=width, board_style=_BOARD_RS)
        assert "pct_cols" not in sink, (
            f"width={width} spuriously recorded an overflow for columns "
            f"pinned to percentages summing to exactly 100%: {sink.get('pct_cols')}"
        )
