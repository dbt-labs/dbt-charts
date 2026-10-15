"""Line and area endpoint labels turn off when the chart carries an overlay layer.

The endpoint-label cascade walks the base chart's own series only — it never sees a
layer. Direct labeling in that shape names the base and leaves the overlay anonymous,
so any layer disqualifies direct labeling the same way it already does on bar
(`_bar_endpoint_labels_for_stack`), and the legend must come up in its place even
under a theme (editorial) that hides legends board-wide.
"""

from __future__ import annotations

from collections.abc import Iterator

import pytest

from dbt_charts.core.compile.config import get_theme_style, reset_config
from dbt_charts.core.compile.models.chart.authored._layer import LineLayer
from dbt_charts.core.compile.models.chart.normalized import AreaChart, LineChart
from dbt_charts.core.compile.models.chart.resolved import (
    ResolvedAreaChart,
    ResolvedLineChart,
)
from dbt_charts.core.compile.models.query.normalized import SqlQuery
from dbt_charts.core.compile.models.style.authored import (
    AreaChartStylePatch,
    EndpointLabelsConfigPatch,
    LineChartStylePatch,
    StylePatch,
)
from dbt_charts.core.compile.resolve import resolve
from dbt_charts.core.compile.resolve.style.board import resolve_chart_style_context
from dbt_charts.core.diagnostics.chart_data import ChartDataError

_Row = dict[str, str | int]

# Well clear of the tiny tier, whose own rule swaps endpoint labels for a top legend
# regardless of layers.
_WIDTH = 900.0

_DATA: list[_Row] = [
    {"month": "2024-01-01", "baseline": 10, "overlay": 4},
    {"month": "2024-02-01", "baseline": 8, "overlay": 6},
]

_SPLIT_DATA: list[_Row] = [
    {"month": "2024-01-01", "value": 10, "series": "New", "peak": 12},
    {"month": "2024-02-01", "value": 8, "series": "New", "peak": 12},
    {"month": "2024-01-01", "value": 5, "series": "Won", "peak": 12},
    {"month": "2024-02-01", "value": 3, "series": "Won", "peak": 12},
]


@pytest.fixture(autouse=True)
def _reset() -> Iterator[None]:
    reset_config()
    yield
    reset_config()


def test_line_with_a_layer_keeps_its_legend() -> None:
    chart = LineChart(
        id="t",
        query=SqlQuery(sql="SELECT 1", source="t"),
        query_name="q",
        type="line",
        x="month",
        y="baseline",
        layers=[LineLayer(type="line", y="overlay")],
    )
    resolved = resolve(
        chart, _DATA, resolve_chart_style_context(get_theme_style()), width=_WIDTH
    )
    assert isinstance(resolved, ResolvedLineChart)

    assert resolved.style.endpoint_labels.visible is False
    assert resolved.legend.visible is not False


def test_area_with_a_layer_keeps_its_legend() -> None:
    chart = AreaChart(
        id="t",
        query=SqlQuery(sql="SELECT 1", source="t"),
        query_name="q",
        type="area",
        x="month",
        y="baseline",
        layers=[LineLayer(type="line", y="overlay")],
    )
    resolved = resolve(
        chart, _DATA, resolve_chart_style_context(get_theme_style()), width=_WIDTH
    )
    assert isinstance(resolved, ResolvedAreaChart)

    assert resolved.style.endpoint_labels.visible is False
    assert resolved.legend.visible is not False


def test_line_without_a_layer_still_labels_directly() -> None:
    """The new disqualifier must not fire on a plain, layer-less line."""
    chart = LineChart(
        id="t",
        query=SqlQuery(sql="SELECT 1", source="t"),
        query_name="q",
        type="line",
        x="month",
        y="baseline",
    )
    resolved = resolve(
        chart, _DATA, resolve_chart_style_context(get_theme_style()), width=_WIDTH
    )
    assert isinstance(resolved, ResolvedLineChart)

    assert resolved.style.endpoint_labels.visible is True


def test_color_split_line_with_a_layer_names_the_split_series_and_the_layer() -> None:
    """A `color:`-split base plus one overlay: the split series must not get
    endpoint labels while the overlay goes unnamed — the whole chart falls back
    to one legend that names all of it.
    """
    chart = LineChart(
        id="t",
        query=SqlQuery(sql="SELECT 1", source="t"),
        query_name="q",
        type="line",
        x="month",
        y="value",
        color="series",
        layers=[LineLayer(type="line", y="peak")],
    )
    resolved = resolve(
        chart, _SPLIT_DATA, resolve_chart_style_context(get_theme_style()), width=_WIDTH
    )
    assert isinstance(resolved, ResolvedLineChart)

    assert resolved.style.endpoint_labels.visible is False
    assert resolved.legend.visible is not False


def test_line_with_a_right_axis_layer_still_falls_back_to_a_legend() -> None:
    """A layer on its own right-hand axis is still a layer — it must not dodge
    the disqualifier just because it plots against a different y scale.
    """
    chart = LineChart(
        id="t",
        query=SqlQuery(sql="SELECT 1", source="t"),
        query_name="q",
        type="line",
        x="month",
        y="baseline",
        layers=[LineLayer(type="line", y="overlay", axis_y={"position": "right"})],
    )
    resolved = resolve(
        chart, _DATA, resolve_chart_style_context(get_theme_style()), width=_WIDTH
    )
    assert isinstance(resolved, ResolvedLineChart)

    assert resolved.style.endpoint_labels.visible is False
    assert resolved.legend.visible is not False


def test_layered_line_labels_directly_when_the_author_asks_for_it() -> None:
    """The layer rule steers the default, like every disqualifier on bar. An
    author who writes `endpoint_labels.visible: true` on a layered line with
    no base color channel gets the base series AND the overlay labeled
    directly on the rail (``EndpointLabelFeature._apply_layered_single_series``)
    — the legend would only repeat what the rail already names, so it retires.
    """
    chart = LineChart(
        id="t",
        query=SqlQuery(sql="SELECT 1", source="t"),
        query_name="q",
        type="line",
        x="month",
        y="baseline",
        layers=[LineLayer(type="line", y="overlay")],
        style=LineChartStylePatch(
            endpoint_labels=EndpointLabelsConfigPatch(visible=True)
        ),
    )
    resolved = resolve(
        chart, _DATA, resolve_chart_style_context(get_theme_style()), width=_WIDTH
    )
    assert isinstance(resolved, ResolvedLineChart)

    assert resolved.style.endpoint_labels.visible is True
    # The rail names the base AND the layer directly — nothing left for the
    # legend to repeat.
    assert resolved.legend.visible is False


def test_board_level_opt_in_keeps_the_rail_on_a_layered_line() -> None:
    """A board-level `charts.line.endpoint_labels.visible: true` is as concrete
    as a chart-local one: the layer rule only steers an `auto` default."""
    chart = LineChart(
        id="t",
        query=SqlQuery(sql="SELECT 1", source="t"),
        query_name="q",
        type="line",
        x="month",
        y="baseline",
        layers=[LineLayer(type="line", y="overlay")],
    )
    board_patch = StylePatch.model_validate(
        {"charts": {"line": {"endpoint_labels": {"visible": True}}}}
    )
    resolved = resolve(
        chart,
        _DATA,
        resolve_chart_style_context(get_theme_style(), board_patch),
        width=_WIDTH,
    )
    assert isinstance(resolved, ResolvedLineChart)

    assert resolved.style.endpoint_labels.visible is True
    assert resolved.legend.visible is False


def test_layered_area_labels_directly_when_the_author_asks_for_it() -> None:
    """Area reads the same authored field the same way — the two families
    differed on this before the check was threaded through both.
    """
    chart = AreaChart(
        id="t",
        query=SqlQuery(sql="SELECT 1", source="t"),
        query_name="q",
        type="area",
        x="month",
        y="baseline",
        layers=[LineLayer(type="line", y="overlay")],
        style=AreaChartStylePatch(
            endpoint_labels=EndpointLabelsConfigPatch(visible=True)
        ),
    )
    resolved = resolve(
        chart, _DATA, resolve_chart_style_context(get_theme_style()), width=_WIDTH
    )
    assert isinstance(resolved, ResolvedAreaChart)

    assert resolved.style.endpoint_labels.visible is True
    assert resolved.legend.visible is False


def test_dual_axis_layer_with_opt_in_raises_at_resolve() -> None:
    """A layer pinning its own axis_y.position, opted into the rail, must be
    refused during ``resolve()`` itself — not left to render to discover.

    The trigger (an authored ``axis_y.position`` on a layer, on a chart whose
    rail would otherwise fire) is fully known at resolve time, so compile
    must refuse before it ever bakes an axis flip or a suppressed legend for
    a rail that will never render.
    """
    chart = LineChart(
        id="t",
        query=SqlQuery(sql="SELECT 1", source="t"),
        query_name="q",
        type="line",
        x="month",
        y="baseline",
        layers=[LineLayer(type="line", y="overlay", axis_y={"position": "right"})],
        style=LineChartStylePatch(
            endpoint_labels=EndpointLabelsConfigPatch(visible=True)
        ),
    )
    with pytest.raises(ChartDataError, match="axis_y.position"):
        resolve(
            chart, _DATA, resolve_chart_style_context(get_theme_style()), width=_WIDTH
        )
