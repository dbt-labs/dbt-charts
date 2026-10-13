"""The table renderer records how a static export paginated it into the sink.

The text format states each paginated table's row, page-size and page counts
from what the renderer actually drew, not an estimate. These tests render real
tables and assert the sink reflects the render: a paginated table records its
page size and counts (capped at the pre-render limit); a table that fits one
page records nothing; and with no sink open, recording is a no-op.
"""

from __future__ import annotations

from typing import Any

from dbt_charts.core.compile.config import get_theme_style
from dbt_charts.core.compile.models.chart.normalized import TableChart
from dbt_charts.core.compile.models.query.normalized import SqlQuery
from dbt_charts.core.compile.models.style.authored import TableChartStylePatch
from dbt_charts.core.compile.resolve import resolve
from dbt_charts.core.compile.resolve.style.board import resolve_style_and_context

_STATIC_MULTI_PAGE_MAX_PAGES = 20
from dbt_charts.core.render.chart.table_static_pagination import (
    StaticPagination,
    collect_static_paginations,
)

from ..._engine_render import render_table_svg

_BOARD_RS, _BOARD_CTX = resolve_style_and_context(get_theme_style())


def _data(n: int) -> list[dict[str, Any]]:
    return [{"name": f"row_{i}", "value": i} for i in range(1, n + 1)]


def _render(n_rows: int, sink_open: bool) -> dict[str, StaticPagination]:
    chart = resolve(
        TableChart(
            id="orgs",
            query=SqlQuery(sql="SELECT 1", source="test"),
            query_name="q",
            type="table",
            style=TableChartStylePatch(pagination={"enabled": True, "page_rows": 5}),
        ),
        _data(n_rows),
        chart_style_context=_BOARD_CTX,
    )
    if not sink_open:
        render_table_svg(chart, _data(n_rows), width=600, board_style=_BOARD_RS)
        return {}
    with collect_static_paginations() as sink:
        render_table_svg(chart, _data(n_rows), width=600, board_style=_BOARD_RS)
        return sink


def test_table_over_cap_records_the_capped_and_real_page_counts() -> None:
    n_rows = (_STATIC_MULTI_PAGE_MAX_PAGES + 5) * 5  # page_rows=5
    pagination = _render(n_rows, sink_open=True)["orgs"]
    assert pagination == StaticPagination(
        page_rows=5,
        total_rows=n_rows,
        rendered_pages=_STATIC_MULTI_PAGE_MAX_PAGES,
        total_pages=_STATIC_MULTI_PAGE_MAX_PAGES + 5,
        cap_note_dropped=False,
    )


def test_table_within_cap_records_every_page_as_rendered() -> None:
    pagination = _render(20, sink_open=True)["orgs"]  # 4 pages of 5
    assert pagination == StaticPagination(
        page_rows=5,
        total_rows=20,
        rendered_pages=4,
        total_pages=4,
        cap_note_dropped=False,
    )


def test_table_on_one_page_records_nothing() -> None:
    assert _render(4, sink_open=True) == {}


def test_no_sink_is_noop() -> None:
    """Rendering without an open sink must not raise (recording is a no-op)."""
    n_rows = (_STATIC_MULTI_PAGE_MAX_PAGES + 5) * 5
    assert _render(n_rows, sink_open=False) == {}
