"""A FormatConfig affix reaching a geo chart's tooltip resolves whole, for the hover
card to compose (``NativeTooltipAffixFeature``)."""

from __future__ import annotations

import html
import re
from typing import Any

import pytest
import yaml

from dbt_charts.core.compile.config import get_theme_style
from dbt_charts.core.compile.models.chart.normalized.geoshape import GeoshapeChart
from dbt_charts.core.compile.models.chart.normalized.point_map import PointMapChart
from dbt_charts.core.compile.models.primitives import FormatConfig
from dbt_charts.core.compile.models.style.context import ChartStyleContext
from dbt_charts.core.compile.resolve.chart.geo import (
    _resolve_geoshape,
    _resolve_point_map,
)
from dbt_charts.core.compile.resolve.style.board import resolve_chart_style_context

from .._svg_render import render_board_to_svg

_GEOSHAPE_KWARGS = {
    "geo_source": "us-states",
    "lookup": "state_id",
    "value": "population",
}
_POINT_MAP_KWARGS = {"latitude": "lat", "longitude": "lng", "projection": "albersUsa"}


def _board_with_tooltip_alias_affix() -> ChartStyleContext:
    compiled = get_theme_style()
    new_tooltip = compiled.charts.tooltip.model_copy(update={"format": "eur"})
    new_charts = compiled.charts.model_copy(update={"tooltip": new_tooltip})
    return resolve_chart_style_context(
        compiled.model_copy(
            update={
                "charts": new_charts,
                "formats": {"eur": FormatConfig(spec=",.0f", prefix="EUR ")},
            }
        )
    )


def test_geoshape_tooltip_affix_alias_resolves_whole() -> None:
    board_style = _board_with_tooltip_alias_affix()
    chart = GeoshapeChart(id="geo1", type="geoshape", **_GEOSHAPE_KWARGS)

    resolved = _resolve_geoshape(chart, [], board_style, 800.0, None)
    assert resolved.style.tooltip_format.prefix == "EUR "


def test_point_map_tooltip_affix_alias_resolves_whole() -> None:
    board_style = _board_with_tooltip_alias_affix()
    chart = PointMapChart(id="map1", type="point_map", **_POINT_MAP_KWARGS)

    resolved = _resolve_point_map(chart, [], board_style, 800.0, None)
    assert resolved.style.tooltip_format.prefix == "EUR "


def test_geoshape_plain_tooltip_alias_compiles_clean() -> None:
    """A spec-only alias (no affix) must not be rejected."""
    compiled = get_theme_style()
    new_tooltip = compiled.charts.tooltip.model_copy(update={"format": "plain"})
    new_charts = compiled.charts.model_copy(update={"tooltip": new_tooltip})
    board_style = resolve_chart_style_context(
        compiled.model_copy(update={"charts": new_charts, "formats": {"plain": ",.0f"}})
    )
    chart = GeoshapeChart(id="geo1", type="geoshape", **_GEOSHAPE_KWARGS)

    resolved = _resolve_geoshape(chart, [], board_style, 800.0, None)
    assert resolved.style.tooltip_format.spec == ",.0f"


def _geo_cards(chart: dict[str, Any]) -> list[str]:
    board = {
        "title": "t",
        "queries": {
            "q": {
                "type": "values",
                "rows": [
                    {"state_id": "06", "lat": 37.0, "lng": -120.0, "pop": 2400.0},
                    {"state_id": "36", "lat": 40.0, "lng": -75.0, "pop": 1200.0},
                ],
            }
        },
        "charts": {"c": {"query": "q", **chart}},
        "rows": ["c"],
        "style": {
            "formats": {"eur": {"spec": ",.0f", "suffix": " €"}},
            "charts": {"tooltip": {"format": "eur"}},
        },
    }
    svg = render_board_to_svg(yaml.safe_dump(board))
    labels = [html.unescape(x) for x in re.findall(r'aria-label="([^"]*)"', svg)]
    return [x for x in labels if ": " in x and "legend" not in x.lower()]


@pytest.mark.parametrize(
    "chart",
    [
        {"type": "geoshape", "geo_source": "us-states", "lookup": "state_id"}
        | {"value": "pop"},
        {
            "type": "point_map",
            "latitude": "lat",
            "longitude": "lng",
            "size": "pop",
            "projection": "albersUsa",
        },
    ],
    ids=["geoshape", "point_map"],
)
def test_a_geo_hover_card_paints_the_affix(chart: dict[str, Any]) -> None:
    cards = _geo_cards(chart)
    assert any("Pop: 1,200 €" in card for card in cards), cards
