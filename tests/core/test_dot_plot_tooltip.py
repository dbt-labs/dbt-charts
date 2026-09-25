"""A dot plot's hover reads each category's dots together, in series order."""

from __future__ import annotations

import json
from typing import Any

import pytest
from pydantic import TypeAdapter

from dbt_charts.core.compile.config import get_theme_style, reset_config
from dbt_charts.core.compile.models.chart.normalized import Chart
from dbt_charts.core.compile.models.query.normalized import SqlQuery
from dbt_charts.core.compile.resolve.style.board import (
    resolve_chart_style_context,
    resolve_style,
)
from dbt_charts.core.render.chart.emitters._tooltip import ROLE_HEADER, ROLE_SERIES
from dbt_charts.core.render.chart.vega_lite import generate_vega_lite_spec

_ROWS = [
    {"category": c, "series": s, "value": v}
    for c, s, v in (
        ("Region A", "2019", 40.0),
        ("Region A", "2024", 55.0),
        ("Region B", "2019", 30.0),
        ("Region B", "2024", 25.0),
    )
]


@pytest.fixture(autouse=True)
def _reset():
    reset_config()
    yield
    reset_config()


def _description(**fields: Any) -> str:
    chart = TypeAdapter(Chart).validate_python(
        {
            "id": "t",
            "type": "scatter",
            "query": SqlQuery(sql="SELECT 1", source="src"),
            "query_name": "q",
            **fields,
        }
    )
    spec = generate_vega_lite_spec(
        chart,
        _ROWS,
        width=400,
        board_style=resolve_style(get_theme_style()),
        chart_style_context=resolve_chart_style_context(get_theme_style()),
    )
    return spec["encoding"]["description"]["value"]["expr"]


def test_the_category_heads_and_the_series_follows():
    expr = _description(x="value", y="category", color="series")
    assert f'{json.dumps(ROLE_HEADER)} + (datum["category"])' in expr
    assert f'{json.dumps(ROLE_SERIES)} + (datum["series"])' in expr


def test_a_scatter_of_two_measures_keeps_its_own_tooltip():
    rows_expr = _description(x="value", y="value", color="series")
    assert f'{json.dumps(ROLE_HEADER)} + (datum["category"])' not in rows_expr
