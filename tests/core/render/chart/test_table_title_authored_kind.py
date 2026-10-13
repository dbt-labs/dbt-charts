"""A table's title/subtitle text runs carry the same ``data-authored-kind``
leaf marker Vega-drawn chart titles do (``authored_kind_attr`` in
``svg_utils.py``), so the canvas's inline-edit overlay can resolve them —
before this fix a table's title only selected the chart (the block), never
opened the leaf editor, unlike a bar chart's title.
"""

from __future__ import annotations

from dbt_charts.core.compile.config import get_theme_style
from dbt_charts.core.compile.models.chart.normalized import TableChart
from dbt_charts.core.compile.models.query.normalized import ValuesQuery
from dbt_charts.core.compile.resolve import resolve
from dbt_charts.core.compile.resolve.style.board import resolve_style_and_context

from ..._engine_render import render_table_svg

_ROWS = [{"revenue": 128_000}]


def _render(*, title: str = "", subtitle: str = "") -> str:
    board_rs, board_ctx = resolve_style_and_context(get_theme_style())
    chart = TableChart(
        id="t1",
        type="table",
        title=title,
        subtitle=subtitle,
        query=ValuesQuery(rows=_ROWS),
    )
    resolved = resolve(chart, _ROWS, chart_style_context=board_ctx)
    return render_table_svg(resolved, _ROWS, width=400.0, board_style=board_rs)


def test_table_title_carries_authored_kind() -> None:
    svg = _render(title="Revenue")
    assert 'data-authored-kind="title"' in svg


def test_table_subtitle_carries_authored_kind() -> None:
    svg = _render(title="Revenue", subtitle="last 30 days")
    assert 'data-authored-kind="subtitle"' in svg


def test_table_without_title_has_no_title_leaf() -> None:
    svg = _render()
    assert 'data-authored-kind="title"' not in svg
    assert 'data-authored-kind="subtitle"' not in svg
