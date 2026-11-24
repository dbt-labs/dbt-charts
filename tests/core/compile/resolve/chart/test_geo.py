"""Point map resolve: what a basemap's authored colors bake into."""

from __future__ import annotations

import pytest

from dbt_charts.core.compile.config import get_theme_style
from dbt_charts.core.compile.models.chart.authored._base import BasemapConfig
from dbt_charts.core.compile.models.chart.normalized.point_map import PointMapChart
from dbt_charts.core.compile.resolve.chart.geo import _resolve_point_map
from dbt_charts.core.compile.resolve.style.board import resolve_chart_style_context
from dbt_charts.core.compile.resolve.style.palette import UnknownColorError, color


def test_basemap_palette_tokens_resolve_to_hex() -> None:
    chart = PointMapChart(
        id="m",
        type="point_map",
        latitude="lat",
        longitude="lng",
        projection="mercator",
        basemap=BasemapConfig(
            source="us-states", fill="dbt-grays.2", stroke="dbt-grays.4"
        ),
    )

    resolved = _resolve_point_map(
        chart, [], resolve_chart_style_context(get_theme_style()), 800.0, None
    )

    assert resolved.basemap_fill == color("dbt-grays.2")
    assert resolved.basemap_stroke == color("dbt-grays.4")


def test_an_unknown_basemap_token_raises() -> None:
    chart = PointMapChart(
        id="m",
        type="point_map",
        latitude="lat",
        longitude="lng",
        projection="mercator",
        basemap=BasemapConfig(source="us-states", fill="no-such-palette.2"),
    )

    with pytest.raises(UnknownColorError, match="no-such-palette"):
        _resolve_point_map(
            chart, [], resolve_chart_style_context(get_theme_style()), 800.0, None
        )


def test_a_dotted_basemap_source_is_not_read_as_a_color() -> None:
    chart = PointMapChart(
        id="m",
        type="point_map",
        latitude="lat",
        longitude="lng",
        projection="mercator",
        basemap=BasemapConfig(source="regions.v2", fill="#e5e7eb"),
    )

    resolved = _resolve_point_map(
        chart, [], resolve_chart_style_context(get_theme_style()), 800.0, None
    )

    assert resolved.basemap_geo_url == "regions.v2"
    assert resolved.basemap_fill == "#e5e7eb"
