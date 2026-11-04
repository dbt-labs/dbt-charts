"""A chart names its series once: endpoint-label rail or legend, never both.

Shapes accepted and rejected (multi-series, wide enough to keep the rail):

| shape                                                    | outcome  |
|----------------------------------------------------------|----------|
| line, color split, nothing authored                      | accepted |
| area, color split, nothing authored                      | accepted |
| stacked bar, color split, nothing authored               | accepted |
| line, endpoint_labels.visible: false + legend.visible    | accepted |
| line, endpoint_labels.visible: false + legend.position   | accepted |
| line, legend.visible: false                              | accepted |
| line, single series, legend.visible: true                | accepted |
| line, legend.visible: true on a tiny card (rail steps    |          |
|   aside for a top legend)                                | accepted |
| line, legend.visible: true                               | rejected |
| line, legend.position.edge: bottom                       | rejected |
| area, legend.visible: true                               | rejected |
| stacked bar, legend.visible: true                        | rejected |
| layered line, rail on, legend.visible: true              | rejected |
| line, board-wide legend.visible: true                    | rejected |
"""

from __future__ import annotations

from collections.abc import Iterator
from typing import Any

import pytest

from dbt_charts.core.compile.config import get_theme_style, reset_config
from dbt_charts.core.compile.models.chart.authored._layer import LineLayer
from dbt_charts.core.compile.models.chart.normalized import (
    AreaChart,
    BarChart,
    LineChart,
)
from dbt_charts.core.compile.models.query.normalized import SqlQuery
from dbt_charts.core.compile.models.style.authored import (
    AreaChartStylePatch,
    BarChartStylePatch,
    EndpointLabelsConfigPatch,
    LegendStylePatch,
    LineChartStylePatch,
)
from dbt_charts.core.compile.resolve import resolve
from dbt_charts.core.compile.resolve.style.board import resolve_chart_style_context
from dbt_charts.core.diagnostics.chart_data import ChartDataError
from dbt_charts.core.diagnostics.codes_render import (
    ERR_ENDPOINT_LABELS_AND_LEGEND_NAME_SERIES_TWICE,
)

_WIDE = 900.0
_TINY = 200.0

_DATA: list[dict[str, Any]] = [
    {"month": m, "value": v, "series": s, "peak": 12}
    for s, vs in (("New", (10, 8)), ("Won", (5, 3)))
    for m, v in zip(("2024-01-01", "2024-02-01"), vs, strict=True)
]
_ONE_SERIES: list[dict[str, Any]] = [
    {"month": "2024-01-01", "value": 1},
    {"month": "2024-02-01", "value": 2},
]

_ONE_SERIES_WITH_PEAK: list[dict[str, Any]] = [
    {**row, "peak": 12} for row in _ONE_SERIES
]

_LEGEND_VISIBLE = LegendStylePatch(visible=True)
_LEGEND_BOTTOM = LegendStylePatch.model_validate({"position": {"edge": "bottom"}})
_RAIL_OFF = EndpointLabelsConfigPatch(visible=False)
_RAIL_ON = EndpointLabelsConfigPatch(visible=True)

_PATCH: dict[str, Any] = {
    "line": LineChartStylePatch,
    "area": AreaChartStylePatch,
    "bar": BarChartStylePatch,
}
_CHART: dict[str, Any] = {"line": LineChart, "area": AreaChart, "bar": BarChart}


@pytest.fixture(autouse=True)
def _reset() -> Iterator[None]:
    reset_config()
    yield
    reset_config()


def _resolve(
    kind: str,
    *,
    data: list[dict[str, Any]] | None = None,
    width: float = _WIDE,
    color: str | None = "series",
    layers: bool = False,
    theme: Any = None,
    **style: Any,
) -> None:
    chart = _CHART[kind](
        id="t",
        query=SqlQuery(sql="SELECT 1", source="t"),
        query_name="q",
        type=kind,
        x="month",
        y="value",
        **({"color": color} if color else {}),
        **({"layers": [LineLayer(type="line", y="peak")]} if layers else {}),
        style=_PATCH[kind](**style),
    )
    resolve(
        chart,
        data or _DATA,
        resolve_chart_style_context(theme or get_theme_style()),
        width=width,
    )


@pytest.mark.parametrize("kind", ["line", "area", "bar"])
def test_default_chart_names_series_once(kind: str) -> None:
    _resolve(kind, **({"stack": "zero"} if kind == "bar" else {}))


@pytest.mark.parametrize(
    "legend", [_LEGEND_VISIBLE, _LEGEND_BOTTOM], ids=["visible", "position"]
)
def test_rail_off_lets_the_author_keep_a_legend(legend: LegendStylePatch) -> None:
    _resolve("line", endpoint_labels=_RAIL_OFF, legend=legend)


def test_hidden_legend_is_accepted() -> None:
    _resolve("line", legend=LegendStylePatch(visible=False))


def test_single_series_legend_is_accepted() -> None:
    _resolve("line", data=_ONE_SERIES, color=None, legend=_LEGEND_VISIBLE)


def test_tiny_card_swaps_rail_for_a_top_legend() -> None:
    _resolve("line", width=_TINY, legend=_LEGEND_VISIBLE)


@pytest.mark.parametrize(
    ("kind", "extra"),
    [
        ("line", {"legend": _LEGEND_VISIBLE}),
        ("line", {"legend": _LEGEND_BOTTOM}),
        ("area", {"legend": _LEGEND_VISIBLE}),
        ("bar", {"legend": _LEGEND_VISIBLE, "stack": "zero"}),
        (
            "line",
            {
                "legend": _LEGEND_VISIBLE,
                "layers": True,
                "color": None,
                "data": _ONE_SERIES_WITH_PEAK,
                "endpoint_labels": _RAIL_ON,
            },
        ),
    ],
    ids=[
        "line-visible",
        "line-position",
        "area-visible",
        "stacked-bar-visible",
        "layered-line-visible",
    ],
)
def test_rail_plus_legend_is_an_error(kind: str, extra: dict[str, Any]) -> None:
    with pytest.raises(ChartDataError) as exc_info:
        _resolve(kind, **extra)
    err = exc_info.value
    assert err.code is ERR_ENDPOINT_LABELS_AND_LEGEND_NAME_SERIES_TWICE
    assert "'t'" in str(err)
    assert "style.endpoint_labels.visible: false" in str(err)
    assert "legend" in str(err)


def test_board_wide_legend_visible_reports_the_chart(model_copy_at: Any) -> None:
    theme = model_copy_at(
        get_theme_style(),
        "charts.legend",
        get_theme_style().charts.legend.model_copy(update={"visible": True}),
    )
    with pytest.raises(ChartDataError) as exc_info:
        _resolve("line", theme=theme)
    assert exc_info.value.code is ERR_ENDPOINT_LABELS_AND_LEGEND_NAME_SERIES_TWICE
