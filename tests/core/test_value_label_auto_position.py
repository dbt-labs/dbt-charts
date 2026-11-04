"""An unset line/area/scatter value-label `position` resolves to `auto`.

The resolved labels carry the flag the Rust engine reads; any authored
`position`, `dx` or `dy` pins the label where the author put it.
"""

from __future__ import annotations

from typing import Any

import pytest
from pydantic import TypeAdapter

from dbt_charts.core.compile.config import get_theme_style
from dbt_charts.core.compile.models.chart.normalized import Chart
from dbt_charts.core.compile.models.query.normalized import SqlQuery
from dbt_charts.core.compile.resolve import resolve
from dbt_charts.core.compile.resolve.style.board import resolve_chart_style_context

_BOARD_STYLE = resolve_chart_style_context(get_theme_style())
_DATA = [{"x": i, "y": float(i)} for i in range(6)]


def _labels(kind: str, labels: dict[str, Any]) -> Any:
    mark = "point" if kind == "scatter" else "line"
    chart = TypeAdapter(Chart).validate_python(
        {
            "id": "t",
            "type": kind,
            "x": "x",
            "y": "y",
            "query": SqlQuery(sql="SELECT 1", source="src"),
            "query_name": "q",
            "style": {"marks": {mark: {"labels": {"visible": True, **labels}}}},
        }
    )
    resolved = resolve(chart, _DATA, chart_style_context=_BOARD_STYLE, width=600.0)
    return getattr(resolved.style, f"{mark}_mark").labels


@pytest.mark.parametrize("kind", ["line", "area", "scatter"])
def test_unset_position_is_auto(kind: str) -> None:
    labels = _labels(kind, {})
    assert labels.auto is True
    assert labels.position is None


@pytest.mark.parametrize("kind", ["line", "area", "scatter"])
@pytest.mark.parametrize(
    "authored", [{"position": "top"}, {"position": "bottom"}, {"dx": 2}, {"dy": -6}]
)
def test_any_authored_position_or_offset_pins_the_label(
    kind: str, authored: dict[str, Any]
) -> None:
    assert _labels(kind, authored).auto is False
