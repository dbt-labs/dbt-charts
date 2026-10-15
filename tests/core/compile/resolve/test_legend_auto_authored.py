"""An authored ``legend.visible: auto`` or ``legend.title.visible: auto`` resolves
at the chart and at the board's family tier, for every legend-drawing family."""

from __future__ import annotations

from typing import Any

import pytest

from dbt_charts.core.compile.config import get_theme_style
from dbt_charts.core.compile.models.chart.resolved import ResolvedChart
from dbt_charts.core.compile.models.query.normalized import ValuesQuery
from dbt_charts.core.compile.models.style.authored import StylePatch
from dbt_charts.core.compile.normalize.charts import normalize_chart
from dbt_charts.core.compile.resolve import resolve
from dbt_charts.core.compile.resolve.style.board import resolve_chart_style_context

_ROWS: list[dict[str, Any]] = [
    {"m": "Jan", "c": "A", "v": 1.0, "w": 2.0, "lat": 40.0, "lng": -100.0},
    {"m": "Feb", "c": "A", "v": 3.0, "w": 1.0, "lat": 41.0, "lng": -101.0},
    {"m": "Jan", "c": "B", "v": 2.0, "w": 4.0, "lat": 42.0, "lng": -102.0},
    {"m": "Feb", "c": "B", "v": 4.0, "w": 3.0, "lat": 43.0, "lng": -103.0},
]

_CHARTS: dict[str, dict[str, Any]] = {
    "line": {"type": "line", "query": "q", "x": "m", "y": "v", "color": "c"},
    "bar": {"type": "bar", "query": "q", "x": "m", "y": "v", "color": "c"},
    "scatter": {"type": "scatter", "query": "q", "x": "v", "y": "w", "color": "c"},
    "heatmap": {"type": "heatmap", "query": "q", "x": "m", "y": "c", "color": "v"},
    "pie": {"type": "pie", "query": "q", "theta": "v", "color": "c"},
    "point_map": {
        "type": "point_map",
        "query": "q",
        "latitude": "lat",
        "longitude": "lng",
        "color": "c",
    },
}

_AUTO_LEGEND = {"visible": "auto", "title": {"visible": "auto"}}


def _resolve(
    chart_def: dict[str, Any], board_patch: dict[str, Any] | None = None
) -> ResolvedChart:
    flat = normalize_chart("c1", chart_def, {"q": ValuesQuery(rows=_ROWS)}, sources={})
    patches = [StylePatch.model_validate(board_patch)] if board_patch else []
    ctx = resolve_chart_style_context(get_theme_style(), *patches)
    return resolve(flat, _ROWS, chart_style_context=ctx)


@pytest.mark.parametrize("family", list(_CHARTS))
def test_chart_level_auto_legend_resolves(family: str) -> None:
    resolved = _resolve({**_CHARTS[family], "style": {"legend": _AUTO_LEGEND}})
    assert isinstance(resolved.legend.visible, bool)


@pytest.mark.parametrize("family", list(_CHARTS))
def test_board_family_level_auto_legend_resolves(family: str) -> None:
    resolved = _resolve(
        _CHARTS[family], {"charts": {family: {"legend": {"visible": "auto"}}}}
    )
    assert isinstance(resolved.legend.visible, bool)


@pytest.mark.parametrize("visible", ["auto", False])
@pytest.mark.parametrize("family", list(_CHARTS))
def test_board_family_level_title_visible_resolves(
    family: str, visible: bool | str
) -> None:
    resolved = _resolve(
        _CHARTS[family],
        {"charts": {family: {"legend": {"title": {"visible": visible}}}}},
    )
    assert isinstance(resolved.legend.visible, bool)


@pytest.mark.parametrize("family", list(_CHARTS))
def test_board_family_level_legend_label_resolves(family: str) -> None:
    resolved = _resolve(
        _CHARTS[family], {"charts": {family: {"legend": {"label": {"padding": 5}}}}}
    )
    assert resolved.legend.label.padding == 5


def test_board_family_level_false_hides_the_legend() -> None:
    resolved = _resolve(
        _CHARTS["line"], {"charts": {"line": {"legend": {"visible": False}}}}
    )
    assert resolved.legend.visible is False
