"""Tests: v2 geoshape resolver and emitter correctness.

Originally written as parity tests against the V1 oracle generate_map_spec (geo.py).
The V1 oracle is retired; tests now verify V2 behavior directly.

"fake-geo-source" is not in the config's geo_sources, so _resolve_geo_source falls back
to using the string as the URL: url="fake-geo-source", format={"type":"topojson","feature":"features"},
projection="mercator".  No real network or config required.
"""

from __future__ import annotations

from typing import Any

from dbt_charts.core.compile.resolve.chart._chart_rows import regroup
from dbt_charts.core.render.chart.spec import RenderBox

_DEFAULT_BOX = RenderBox(width=600.0, height=300.0)
from dbt_charts.core.compile.config import get_default_theme_name, get_theme_style
from dbt_charts.core.compile.models.chart.normalized.geoshape import GeoshapeChart
from dbt_charts.core.compile.models.chart.resolved import (
    ResolvedGeoshapeChart,
)
from dbt_charts.core.compile.resolve.chart.geo import _resolve_geoshape
from dbt_charts.core.compile.resolve.style.board import resolve_chart_style_context
from dbt_charts.core.render.chart.emitters.geo import GeoshapeEmitter
from dbt_charts.core.render.chart.translate import translate_to_vl

_GEO_SOURCE = "fake-geo-source"


def _board_style() -> Any:
    return resolve_chart_style_context(get_theme_style(get_default_theme_name()))


def _v2_chart(
    *, geo_source: str, lookup_field: str | None = None, value_field: str | None = None
) -> ResolvedGeoshapeChart:
    """v2 ResolvedGeoshapeChart built via _resolve_geoshape for "fake-geo-source".

    Delegates style baking to the resolver so style fields come from the cascade,
    matching what the emitter now reads from chart.style.*.
    """
    compiled = GeoshapeChart(
        id="geo1",
        type="geoshape",
        geo_source=geo_source,
        projection="mercator",
        lookup=lookup_field,
        value=value_field,
    )
    return _resolve_geoshape(compiled, [], _board_style(), 800.0, None)


def test_resolver_bakes_known_geo_source_fields() -> None:
    """_resolve_geoshape bakes correct geo descriptor for a known configured source.

    Exercises _geoshape_kwargs config lookup against "us-states" (in geo_defaults.yml).
    The parity tests hand-bake both sides; this test drives the resolver path directly.
    """
    compiled = GeoshapeChart(
        id="geo1",
        type="geoshape",
        geo_source="us-states",
        lookup="state_id",
        value="population",
    )
    resolved = _resolve_geoshape(
        compiled,
        [],
        resolve_chart_style_context(get_theme_style()),
        800.0,
        None,
    )

    assert resolved.geo_url == "bundled:us-10m.json"
    assert resolved.geo_format_type == "topojson"
    assert resolved.geo_feature == "states"
    assert resolved.geo_join_key == "id"
    assert resolved.geo_projection_type == "albersUsa"
    assert resolved.geo_key_format == "numeric"
    assert resolved.geo_key_examples == ["6", "48", "36"]
    assert resolved.lookup_field == "state_id"
    assert resolved.value_field == "population"


def test_geoshape_no_data() -> None:
    """No-data path: V2 emitter produces a valid VL spec."""
    vc = _v2_chart(geo_source=_GEO_SOURCE)
    v2_vl = translate_to_vl(GeoshapeEmitter().emit(vc, _DEFAULT_BOX, regroup((), [])))
    assert "mark" in v2_vl or "layer" in v2_vl


def test_geoshape_with_data() -> None:
    """Data+join path: lookup transform + choropleth layering."""
    data = [{"state": "CA", "pop": 39_500_000}, {"state": "TX", "pop": 29_000_000}]
    vc = _v2_chart(geo_source=_GEO_SOURCE, lookup_field="state", value_field="pop")
    v2_vl = translate_to_vl(GeoshapeEmitter().emit(vc, _DEFAULT_BOX, regroup((), data)))
    assert "transform" in v2_vl or "layer" in v2_vl


def test_geoshape_choropleth_color_applies_resolved_legend_style() -> None:
    """geoshape's choropleth color encoding must carry ResolvedLegendStyle
    config the same way heatmap.py already does (apply_color_legend) — before
    the fix, geoshape built its color scale via gradient_scale_to_vl directly
    and never called apply_color_legend, so its legend fell back to whatever
    Vega-Lite defaults to instead of our theme's legend style.
    """
    data = [{"state": "CA", "pop": 39_500_000}, {"state": "TX", "pop": 29_000_000}]
    vc = _v2_chart(geo_source=_GEO_SOURCE, lookup_field="state", value_field="pop")
    v2_vl = translate_to_vl(GeoshapeEmitter().emit(vc, _DEFAULT_BOX, regroup((), data)))
    color_enc = v2_vl["layer"][1]["encoding"]["color"]
    assert "legend" in color_enc
    legend_val = color_enc["legend"]
    assert isinstance(legend_val, dict)
    # A real styled key must have landed — not just an empty dict, which
    # `legend_val is None or isinstance(legend_val, dict)` could never catch
    # a regression on (a `{}` from apply_color_legend would still pass it).
    assert "labelColor" in legend_val
