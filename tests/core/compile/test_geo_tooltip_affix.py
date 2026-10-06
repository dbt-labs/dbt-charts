"""Regression: a FormatConfig affix reaching a geo chart's tooltip must be rejected at
resolve, not silently dropped.
"""

from __future__ import annotations

import pytest

from dbt_charts.core.compile.config import get_theme_style
from dbt_charts.core.compile.errors import CompilationError
from dbt_charts.core.compile.models.chart.normalized.geoshape import GeoshapeChart
from dbt_charts.core.compile.models.chart.normalized.point_map import PointMapChart
from dbt_charts.core.compile.models.primitives import FormatConfig
from dbt_charts.core.compile.models.style.context import ChartStyleContext
from dbt_charts.core.compile.resolve.chart.geo import (
    _EMPTY_CHART_TEXT_VARIABLES,
    _resolve_geoshape,
    _resolve_point_map,
)
from dbt_charts.core.compile.resolve.style.board import resolve_chart_style_context

_GEOSHAPE_KWARGS = {
    "geo_source": "us-states",
    "lookup": "state_id",
    "value": "population",
}
_POINT_MAP_KWARGS = {"latitude": "lat", "longitude": "lng"}


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


def test_geoshape_tooltip_affix_alias_rejected() -> None:
    board_style = _board_with_tooltip_alias_affix()
    chart = GeoshapeChart(id="geo1", type="geoshape", **_GEOSHAPE_KWARGS)

    with pytest.raises(CompilationError) as exc_info:
        _resolve_geoshape(chart, [], board_style, 800.0, None)
    assert exc_info.value.code.code == "ERR-FORMAT-AFFIX-GEO-TOOLTIP-UNSUPPORTED"


def test_point_map_tooltip_affix_alias_rejected() -> None:
    board_style = _board_with_tooltip_alias_affix()
    chart = PointMapChart(id="map1", type="point_map", **_POINT_MAP_KWARGS)

    with pytest.raises(CompilationError) as exc_info:
        _resolve_point_map(
            chart, [], board_style, 800.0, None, _EMPTY_CHART_TEXT_VARIABLES
        )
    assert exc_info.value.code.code == "ERR-FORMAT-AFFIX-GEO-TOOLTIP-UNSUPPORTED"


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
    assert resolved.style.tooltip_format == ",.0f"
