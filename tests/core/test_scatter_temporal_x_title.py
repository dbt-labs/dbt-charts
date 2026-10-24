"""A temporal x axis names itself, so scatter drops its default x title there.

Scatter keeps the x title only when the x axis does not read as a date.
An authored ``x_label`` always shows.
"""

from __future__ import annotations

import datetime as dt
from collections.abc import Iterator

import pytest

from dbt_charts.core.compile.config import get_theme_style, reset_config
from dbt_charts.core.compile.models.chart.normalized import ScatterChart
from dbt_charts.core.compile.models.chart.resolved import ResolvedScatterChart
from dbt_charts.core.compile.models.query.normalized import SqlQuery
from dbt_charts.core.compile.resolve import resolve
from dbt_charts.core.compile.resolve.style.board import resolve_chart_style_context


@pytest.fixture(autouse=True)
def _reset() -> Iterator[None]:
    reset_config()
    yield
    reset_config()


_ROWS: dict[str, list[dict[str, object]]] = {
    "temporal": [{"x": dt.date(2025, m, 1), "y": 10.0 * m} for m in range(1, 7)],
    "quantitative": [{"x": 1.5 * m, "y": 10.0 * m} for m in range(1, 7)],
    "nominal": [
        {"x": name, "y": 10.0 * i}
        for i, name in enumerate(["North", "South", "East", "West"])
    ],
}


def _x_title_visible(kind: str, x_label: str | None = None) -> bool:
    chart = ScatterChart(
        id="probe",
        query=SqlQuery(sql="SELECT 1", source="t"),
        query_name="q",
        type="scatter",
        x="x",
        y="y",
        x_label=x_label,
    )
    ctx = resolve_chart_style_context(get_theme_style())
    resolved = resolve(chart, _ROWS[kind], ctx, width=500.0)
    assert isinstance(resolved, ResolvedScatterChart)
    visible = resolved.style.axis_x.title.visible
    assert isinstance(visible, bool)
    return visible


@pytest.mark.parametrize(
    ("kind", "x_label", "expected"),
    [
        ("temporal", None, False),
        ("temporal", "Day", True),
        ("quantitative", None, True),
        ("nominal", None, True),
    ],
)
def test_scatter_x_title_visibility(
    kind: str, x_label: str | None, expected: bool
) -> None:
    assert _x_title_visible(kind, x_label) is expected
