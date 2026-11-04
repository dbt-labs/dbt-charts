"""A table's `row_role` names the query column that tags summary and total rows."""

from __future__ import annotations

from typing import Any

import pytest

from dbt_charts.core.compile.config import get_theme_style
from dbt_charts.core.compile.resolve import resolve
from dbt_charts.core.compile.resolve.style.board import (
    resolve_chart_style_context,
    resolve_style,
)
from dbt_charts.core.diagnostics.chart_data import ChartDataError

from ._engine_render import render_table_svg

_DATA = [
    {"company": "Apex", "revenue": 1000, "kind": "value"},
    {"company": "Total", "revenue": 1000, "kind": "total"},
]


def _svg(make_chart: Any, **chart_fields: Any) -> str:
    chart = make_chart("table", x=None, y=None, **chart_fields)
    resolved = resolve(
        chart,
        _DATA,
        chart_style_context=resolve_chart_style_context(get_theme_style()),
    )
    return render_table_svg(
        resolved, _DATA, width=600, board_style=resolve_style(get_theme_style())
    )


def test_row_role_tags_the_total_row(make_chart: Any) -> None:
    svg = _svg(make_chart, row_role="kind")
    assert 'data-dbt-row-role="total"' in svg
    assert ">Kind<" not in svg


def test_without_row_role_no_row_is_a_total(make_chart: Any) -> None:
    assert 'data-dbt-row-role="total"' not in _svg(make_chart)


@pytest.mark.parametrize("name", ["total", "nope"])
def test_row_role_naming_no_query_column_raises(make_chart: Any, name: str) -> None:
    with pytest.raises(ChartDataError, match="is not a query column"):
        _svg(make_chart, row_role=name)
