"""The engine decodes a map's geometry once per distinct content: Python hands
it each document as raw JSON text and it keeps the decoded features in a
content-addressed cache, for bundled and fetched sources alike."""

from __future__ import annotations

import json
import uuid

import pytest

from dbt_charts import _engine
from dbt_charts.core.compile.config import get_theme_style
from dbt_charts.core.compile.models.chart.normalized.geoshape import GeoshapeChart
from dbt_charts.core.compile.models.chart.resolved import ResolvedGeoshapeChart
from dbt_charts.core.compile.models.query.normalized import ValuesQuery
from dbt_charts.core.compile.normalize.charts import normalize_chart
from dbt_charts.core.compile.resolve import resolve
from dbt_charts.core.compile.resolve.chart.geo import _resolve_geoshape
from dbt_charts.core.compile.resolve.style.board import (
    resolve_chart_style_context,
    resolve_style_and_context,
)
from dbt_charts.core.diagnostics.chart_data import ChartDataError
from dbt_charts.core.render.engine import chart_geometries, render_native
from dbt_charts.core.render.geo.loader import geometry_fetcher_scope

_FETCHED = "fake-geo-source"


def _chart(geo_source: str) -> ResolvedGeoshapeChart:
    compiled = GeoshapeChart(
        id="geo1", type="geoshape", geo_source=geo_source, projection="mercator"
    )
    return _resolve_geoshape(
        compiled, [], resolve_chart_style_context(get_theme_style()), 400.0, None
    )


def _render(chart: ResolvedGeoshapeChart) -> str:
    style, _ = resolve_style_and_context(get_theme_style())
    svg, _ = render_native(
        chart, [], width=400.0, height=300.0, padding=None, board_style=style
    )
    return svg


def _topology(tag: str) -> bytes:
    """A one-square topology, made unique by *tag* in its feature id."""
    return json.dumps(
        {
            "type": "Topology",
            "arcs": [[[0, 0], [10, 0], [0, 10], [-10, 0], [0, -10]]],
            "objects": {
                "features": {
                    "type": "GeometryCollection",
                    "geometries": [{"type": "Polygon", "arcs": [[0]], "id": tag}],
                }
            },
        }
    ).encode()


def test_payload_carries_the_document_as_unparsed_text() -> None:
    geometries = chart_geometries(_chart("us-states"))

    assert geometries is not None
    (text,) = geometries.values()
    assert isinstance(text, str) and json.loads(text)["type"] == "Topology"


def test_bundled_map_is_decoded_once() -> None:
    chart = _chart("us-states")
    first = _render(chart)
    decodes = _engine.geometry_decodes()

    assert _render(chart) == first
    assert _engine.geometry_decodes() == decodes


def test_fetched_map_is_decoded_once_per_distinct_content() -> None:
    chart = _chart(_FETCHED)
    body = _topology(uuid.uuid4().hex)
    start = _engine.geometry_decodes()
    with geometry_fetcher_scope(lambda url: body):
        first = _render(chart)
        assert _render(chart) == first
    assert _engine.geometry_decodes() == start + 1

    with geometry_fetcher_scope(lambda url: _topology(uuid.uuid4().hex)):
        _render(chart)
    assert _engine.geometry_decodes() == start + 2


def test_a_fetched_document_that_is_not_json_is_an_error_card() -> None:
    chart = _chart(_FETCHED)
    with (
        geometry_fetcher_scope(lambda url: b"not json"),
        pytest.raises(ChartDataError, match=f"{_FETCHED}.*not valid JSON"),
    ):
        _render(chart)


def test_a_fetched_basemap_that_is_not_json_names_the_authored_source() -> None:
    chart_def = {
        "type": "point_map",
        "query": "q",
        "latitude": "lat",
        "longitude": "lon",
        "projection": "mercator",
        "basemap": {"source": "nyc-neighborhoods"},
    }
    rows = [{"lat": 40.7, "lon": -74.0}]
    flat = normalize_chart("c1", chart_def, {"q": ValuesQuery(rows=rows)}, sources={})
    chart = resolve(
        flat, rows, chart_style_context=resolve_chart_style_context(get_theme_style())
    )
    style, _ = resolve_style_and_context(get_theme_style())
    with (
        geometry_fetcher_scope(lambda url: b"not json"),
        pytest.raises(ChartDataError) as raised,
    ):
        render_native(
            chart, rows, width=400.0, height=300.0, padding=None, board_style=style
        )

    assert "nyc-neighborhoods" in str(raised.value)
    assert "https://" not in str(raised.value)
