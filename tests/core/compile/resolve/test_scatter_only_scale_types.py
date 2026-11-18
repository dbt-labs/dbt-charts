"""pow, sqrt and symlog bend only a scatter's axes: anywhere else is a compile error."""

from __future__ import annotations

from collections.abc import Iterator
from datetime import date
from typing import Any

import pytest

from dbt_charts.core.compile.config import get_theme_style, reset_config
from dbt_charts.core.compile.errors import CompilationError
from dbt_charts.core.compile.models.chart.normalized import (
    AreaChart,
    BarChart,
    Chart,
    LineChart,
    ScatterChart,
)
from dbt_charts.core.compile.models.query.normalized import SqlQuery
from dbt_charts.core.compile.models.style.authored import (
    AreaChartStylePatch,
    BarChartStylePatch,
    LineChartStylePatch,
    ScatterChartStylePatch,
)
from dbt_charts.core.compile.resolve import resolve
from dbt_charts.core.compile.resolve.style.board import resolve_chart_style_context

_DATA: list[dict[str, Any]] = [
    {"month": "Jan", "n": 1, "day": date(2024, 1, 1), "revenue": 10.0},
    {"month": "Feb", "n": 2, "day": date(2024, 2, 1), "revenue": 200.0},
    {"month": "Mar", "n": 3, "day": date(2024, 3, 1), "revenue": 3000.0},
]

_CHARTS: dict[str, tuple[type[Chart], type[Any], dict[str, Any]]] = {
    "line": (LineChart, LineChartStylePatch, {"x": "month", "y": "revenue"}),
    "area": (AreaChart, AreaChartStylePatch, {"x": "month", "y": "revenue"}),
    "bar": (BarChart, BarChartStylePatch, {"x": "month", "y": "revenue"}),
    "line_numeric_x": (LineChart, LineChartStylePatch, {"x": "n", "y": "revenue"}),
    "line_temporal_x": (LineChart, LineChartStylePatch, {"x": "day", "y": "revenue"}),
    "area_numeric_x": (AreaChart, AreaChartStylePatch, {"x": "n", "y": "revenue"}),
    "area_temporal_x": (AreaChart, AreaChartStylePatch, {"x": "day", "y": "revenue"}),
    "bar_numeric_x": (BarChart, BarChartStylePatch, {"x": "n", "y": "revenue"}),
    "funnel": (BarChart, BarChartStylePatch, {"x": "month", "y": "revenue"}),
    "histogram": (BarChart, BarChartStylePatch, {"x": "revenue", "y": "n"}),
    "scatter": (ScatterChart, ScatterChartStylePatch, {"x": "n", "y": "revenue"}),
}


@pytest.fixture(autouse=True)
def _reset() -> Iterator[None]:
    reset_config()
    yield
    reset_config()


def _resolve(family: str, axis: str, scale_type: str) -> None:
    chart_cls, patch_cls, fields = _CHARTS[family]
    chart = chart_cls(
        id="c",
        type=family.split("_")[0],
        query=SqlQuery(sql="SELECT 1", source="t"),
        query_name="q",
        style=patch_cls.model_validate(
            {axis: {"scale": {"continuous": {"type": scale_type}}}}
        ),
        **fields,
    )
    resolve(chart, _DATA, resolve_chart_style_context(get_theme_style()))


@pytest.mark.parametrize("scale_type", ["pow", "sqrt", "symlog"])
@pytest.mark.parametrize(
    ("family", "axis"),
    [
        ("line", "axis_y"),
        ("area", "axis_y"),
        ("bar", "axis_y"),
        ("histogram", "axis_x"),
        ("histogram", "axis_y"),
        ("funnel", "axis_y"),
        ("line", "axis_x"),
        ("line_numeric_x", "axis_x"),
        ("line_temporal_x", "axis_x"),
        ("area_numeric_x", "axis_x"),
        ("area_temporal_x", "axis_x"),
        ("bar_numeric_x", "axis_x"),
    ],
)
def test_bent_scale_off_a_scatter_is_a_compile_error(
    family: str, axis: str, scale_type: str
) -> None:
    with pytest.raises(CompilationError) as exc_info:
        _resolve(family, axis, scale_type)
    assert exc_info.value.code is not None
    assert exc_info.value.code.code == "ERR-SCALE-TYPE-SCATTER-ONLY"
    message = str(exc_info.value)
    assert f"{axis}.scale.continuous.type: {scale_type}" in message
    assert f"({family.split('_')[0]})" in message
    assert "use a scatter" in message.lower()
    assert "charts/axis-scales" in message


@pytest.mark.parametrize("scale_type", ["pow", "sqrt", "symlog"])
@pytest.mark.parametrize("axis", ["axis_x", "axis_y"])
def test_bent_scale_on_a_scatter_compiles(axis: str, scale_type: str) -> None:
    _resolve("scatter", axis, scale_type)
