"""The table renderer records a slot-squeezed page into the sink.

Item 11 of the render-layer gap register: ``_get_table_height_from_data``
reserves slot height for the rows it expects to draw, ``_largest_safe_page_rows``
decides how many the slot actually fits, and when they disagree the paginator
wins in silence. These tests render real tables and assert the sink reflects the
render — a 4-row table in a slot that fits one row records the squeeze, a slot
that fits every row records nothing, and an authored page size the slot honors
records nothing either.
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
from dbt_charts.core.render.chart.table_page_squeeze import (
    TablePageSqueeze,
    _sinks,
    collect_table_page_squeezes,
)

_BOARD_RS, _BOARD_CTX = resolve_style_and_context(get_theme_style())

# Fits one of the four rows once title, header and padding are taken out — the
# flowed-tile shape the register measured on dashboard 1328.
_SQUEEZING_HEIGHT = 100.0
_ROOMY_HEIGHT = 400.0


def _data(n: int) -> list[dict[str, Any]]:
    return [{"service": f"svc_{i}", "cost": i * 100} for i in range(1, n + 1)]


def _render(
    n_rows: int,
    height: float,
    *,
    page_rows: int | None = None,
    explicit_null: bool = False,
    sink_open: bool = True,
) -> dict[str, TablePageSqueeze]:
    pagination: dict[str, Any] = {"enabled": True}
    if page_rows is not None or explicit_null:
        pagination["page_rows"] = page_rows
    data = _data(n_rows)
    chart = resolve(
        TableChart(
            id="svc",
            query=SqlQuery(sql="SELECT 1", source="test"),
            query_name="q",
            type="table",
            style=TableChartStylePatch(pagination=pagination),
        ),
        data,
        chart_style_context=_BOARD_CTX,
    )
    if not sink_open:
        render_table_svg(chart, data, width=600, height=height, board_style=_BOARD_RS)
        return {}
    with collect_table_page_squeezes() as sink:
        render_table_svg(chart, data, width=600, height=height, board_style=_BOARD_RS)
        return sink


def test_short_slot_records_the_squeeze() -> None:
    """One row drawn where the page holds four — the sink records all three counts."""
    sink = _render(4, _SQUEEZING_HEIGHT)
    assert "svc" in sink
    squeeze = sink["svc"]
    assert squeeze.drawn_rows == 1
    assert squeeze.page_rows == 4
    assert squeeze.total_rows == 4


def test_slot_that_fits_every_row_records_nothing() -> None:
    """The sizer and the paginator agree — nothing to warn about."""
    assert _render(4, _ROOMY_HEIGHT) == {}


def test_honored_authored_page_size_records_nothing() -> None:
    """``page_rows: 2`` over 8 rows in a slot that fits 2 is pagination, not a squeeze."""
    assert _render(8, _ROOMY_HEIGHT, page_rows=2) == {}


def test_page_rows_is_capped_at_the_rows_that_exist() -> None:
    """A 20-row page over 4 rows asks for 4, so the squeeze reads 1-of-4, not 1-of-20."""
    sink = _render(4, _SQUEEZING_HEIGHT, page_rows=20)
    assert sink["svc"].page_rows == 4


def test_no_sink_is_noop() -> None:
    """Rendering without an open sink must not raise, and must open none."""
    _render(4, _SQUEEZING_HEIGHT, sink_open=False)
    assert _sinks.get() == ()


def test_setting_page_rows_to_drawn_rows_clears_the_squeeze() -> None:
    """The Fix line prescribes ``page_rows: <drawn_rows>``; following it clears it."""
    drawn = _render(30, 300.0, page_rows=25)["svc"].drawn_rows
    assert 1 < drawn < 25
    assert _render(30, 300.0, page_rows=drawn) == {}


def test_unlimited_page_rows_records_nothing() -> None:
    """``page_rows: null`` is unlimited: the slot sizes the page, nothing to warn."""
    assert _render(30, 300.0, page_rows=None, explicit_null=True) == {}


def _resolved_page_rows(pagination: dict[str, Any]) -> int | None:
    chart = resolve(
        TableChart(
            id="svc",
            query=SqlQuery(sql="SELECT 1", source="test"),
            query_name="q",
            type="table",
            style=TableChartStylePatch(pagination=pagination),
        ),
        _data(50),
        chart_style_context=_BOARD_CTX,
    )
    assert chart.style.pagination is not None
    return chart.style.pagination.page_rows


def test_omitted_page_rows_inherits_the_theme_default() -> None:
    assert _resolved_page_rows({"enabled": True}) == 20


def test_explicit_null_page_rows_stays_unlimited_for_the_sizer() -> None:
    """The sizer reads this slot; backfilling 20 would paginate an unlimited table."""
    assert _resolved_page_rows({"enabled": True, "page_rows": None}) is None
