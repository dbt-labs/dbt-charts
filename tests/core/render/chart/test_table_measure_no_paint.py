"""``measure_table_height`` sizes a table from its layout without painting it."""

from __future__ import annotations

import re
from typing import Any

import pytest

from dbt_charts.core.compile.config import get_theme_style
from dbt_charts.core.compile.models.chart.authored import TableColumnConfig
from dbt_charts.core.compile.models.chart.normalized import TableChart
from dbt_charts.core.compile.models.query.normalized import SqlQuery
from dbt_charts.core.compile.models.style.authored import TableChartStylePatch
from dbt_charts.core.compile.resolve import resolve
from dbt_charts.core.compile.resolve.style.board import resolve_style_and_context
from dbt_charts.core.diagnostics.chart_data import ChartDataError
from dbt_charts.core.render.chart import table
from dbt_charts.core.render.chart.table import (
    _STATIC_MULTI_PAGE_MAX_PAGES,
    measure_table_height,
    render_table_svg,
)
from dbt_charts.core.render.chart.text_truncation import collect_text_truncations

_BOARD_RS, _BOARD_CTX = resolve_style_and_context(get_theme_style())

_WORDS = "alpha beta gamma delta epsilon zeta eta theta iota kappa lambda mu"


def _flat() -> tuple[TableChart, list[dict[str, Any]]]:
    data = [{"name": f"row_{i}", "value": i} for i in range(6)]
    return TableChart(id="t", query_name="q", type="table", query=_q()), data


def _wrapping() -> tuple[TableChart, list[dict[str, Any]]]:
    data = [{"name": f"{_WORDS} {_WORDS}", "value": i} for i in range(4)]
    chart = TableChart(
        id="t",
        query_name="q",
        type="table",
        query=_q(),
        style=TableChartStylePatch(wrap=True),
    )
    return chart, data


def _pivot() -> tuple[TableChart, list[dict[str, Any]]]:
    data = [
        {"region": r, "quarter": q, "sales": i, "units": i * 2}
        for i, (r, q) in enumerate(
            (r, q) for r in ("east", "west", "north") for q in ("q1", "q2")
        )
    ]
    chart = TableChart(
        id="t",
        query_name="q",
        type="table",
        query=_q(),
        rows=["region"],
        columns=["quarter"],
        values=["sales", "units"],
    )
    return chart, data


def _paged(n_rows: int) -> tuple[TableChart, list[dict[str, Any]]]:
    data = [{"name": f"row_{i}", "value": i} for i in range(n_rows)]
    chart = TableChart(
        id="t",
        query_name="q",
        type="table",
        query=_q(),
        style=TableChartStylePatch(pagination={"enabled": True, "page_rows": 5}),
    )
    return chart, data


def _q() -> SqlQuery:
    return SqlQuery(sql="SELECT 1", source="test")


_SHAPES = {
    "flat": _flat,
    "wrapping": _wrapping,
    "pivot": _pivot,
    "paged": lambda: _paged(23),
    "static_capped": lambda: _paged((_STATIC_MULTI_PAGE_MAX_PAGES + 4) * 5),
}


@pytest.mark.parametrize("shape", _SHAPES)
def test_measure_matches_rendered_height_without_painting(
    shape: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    source, data = _SHAPES[shape]()
    chart = resolve(source, data, chart_style_context=_BOARD_CTX)
    svg = render_table_svg(chart, data, width=600, board_style=_BOARD_RS)
    match = re.search(r'<svg[^>]* height="([\d.]+)"', svg)
    assert match
    rendered = float(match.group(1))

    def _no_paint(*_: Any, **__: Any) -> str:
        raise AssertionError("measure_table_height painted")

    for painter in (
        "_render_data_rows",
        "_render_header_section",
        "_render_pagination_controls",
        "_render_static_pagination_cap_note",
    ):
        monkeypatch.setattr(table, painter, _no_paint)

    assert measure_table_height(chart, data, 600, board_style=_BOARD_RS) == rendered


def test_paint_only_failure_is_invisible_to_measure() -> None:
    """A non-color swatch cell fails only in paint: measuring still sizes."""
    source = TableChart(
        id="t",
        query_name="q",
        type="table",
        query=_q(),
        style=TableChartStylePatch(columns={"swatch": TableColumnConfig(swatch=True)}),
    )
    data = [{"swatch": "#3164a3"}, {"swatch": "Mid-Market"}]
    chart = resolve(source, [], chart_style_context=_BOARD_CTX)

    assert measure_table_height(chart, data, 400, board_style=_BOARD_RS) > 0
    with pytest.raises(ChartDataError, match="Mid-Market"):
        render_table_svg(chart, data, width=400, board_style=_BOARD_RS)


def test_measure_records_no_cell_truncation_but_render_does() -> None:
    source = TableChart(
        id="t",
        query_name="q",
        type="table",
        query=_q(),
        style=TableChartStylePatch(
            wrap=False, columns={"name": TableColumnConfig(width=60)}
        ),
    )
    data = [{"name": "x" * 400, "value": 1}]
    chart = resolve(source, data, chart_style_context=_BOARD_CTX)

    with collect_text_truncations() as sizing_sink:
        measure_table_height(chart, data, 300, board_style=_BOARD_RS)
    with collect_text_truncations() as render_sink:
        render_table_svg(chart, data, width=300, board_style=_BOARD_RS)

    assert sizing_sink == {}
    assert render_sink["t"]
