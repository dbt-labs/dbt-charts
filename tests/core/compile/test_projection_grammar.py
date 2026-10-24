"""The authored projection grammar is closed: Vega's 17 types and its typed params."""

from __future__ import annotations

import warnings
from typing import Any, get_args

import pytest
from pydantic import ValidationError

from dbt_charts.core.compile import compile
from dbt_charts.core.compile.config import get_config, get_theme_style
from dbt_charts.core.compile.errors import CompilationError
from dbt_charts.core.compile.migrations import (
    SchemaMigrationWarning,
    migrate_mapping,
)
from dbt_charts.core.compile.migrations.migrations import _board_migration_context
from dbt_charts.core.compile.models.board.authored import AuthoredBoard
from dbt_charts.core.compile.models.chart.authored import PointMapChart
from dbt_charts.core.compile.models.chart.normalized.geoshape import (
    GeoshapeChart as NormalizedGeoshapeChart,
)
from dbt_charts.core.compile.models.chart.normalized.point_map import (
    PointMapChart as NormalizedPointMapChart,
)
from dbt_charts.core.compile.models.config import ConfigNode
from dbt_charts.core.compile.models.style.authored import StylePatch
from dbt_charts.core.compile.models.vega_lite.contracts import (
    Projection,
    ProjectionType,
)
from dbt_charts.core.compile.resolve.chart.geo import (
    _resolve_geo_projection,
    _resolve_geoshape as _resolve_geoshape_chart,
    _resolve_point_map as _resolve_point_map_chart,
)
from dbt_charts.core.compile.resolve.style.board import resolve_chart_style_context
from dbt_charts.core.compile.schema.renderers.yaml_schema_catalog import (
    YamlSchemaCatalog,
    load_yaml_schema_catalog,
)

VEGA_PROJECTIONS = {
    "mercator",
    "transverseMercator",
    "equirectangular",
    "equalEarth",
    "naturalEarth1",
    "mollweide",
    "azimuthalEqualArea",
    "azimuthalEquidistant",
    "gnomonic",
    "orthographic",
    "stereographic",
    "albers",
    "albersUsa",
    "conicConformal",
    "conicEqualArea",
    "conicEquidistant",
    "identity",
}


def test_projection_type_is_exactly_vegas_seventeen() -> None:
    assert set(get_args(ProjectionType)) == VEGA_PROJECTIONS


@pytest.mark.parametrize("name", sorted(VEGA_PROJECTIONS))
def test_chart_accepts_each_projection_as_string_or_object(name: str) -> None:
    assert PointMapChart(type="point_map", projection=name).projection == name
    obj = PointMapChart(type="point_map", projection={"type": name}).projection
    assert isinstance(obj, Projection)
    assert obj.type == name


def test_chart_accepts_every_typed_param() -> None:
    chart = PointMapChart(
        type="point_map",
        projection={
            "type": "conicConformal",
            "center": [-71.8, 43.8],
            "rotate": [90, 0, 0],
            "scale": 2200,
            "translate": [10, 20],
            "parallels": [30, 45],
            "clipAngle": 90,
            "clipExtent": [[0, 0], [100, 100]],
            "precision": 0.5,
            "reflectX": True,
            "reflectY": False,
            "pointRadius": 3,
        },
    )
    assert isinstance(chart.projection, Projection)
    assert chart.projection.clipAngle == 90


@pytest.mark.parametrize("bad", ["Mercator", "robinson", "", "albers_usa"])
def test_unknown_projection_string_rejected(bad: str) -> None:
    with pytest.raises(ValidationError, match="projection"):
        PointMapChart(type="point_map", projection=bad)


def test_unknown_projection_type_in_object_rejected() -> None:
    with pytest.raises(ValidationError, match="robinson"):
        PointMapChart(type="point_map", projection={"type": "robinson"})


def test_projection_object_requires_type() -> None:
    with pytest.raises(ValidationError, match="type"):
        PointMapChart(type="point_map", projection={"scale": 100})


def test_unknown_projection_key_rejected() -> None:
    with pytest.raises(ValidationError, match="zoom"):
        PointMapChart(type="point_map", projection={"type": "mercator", "zoom": 3})


def test_every_registry_projection_validates_against_the_same_set() -> None:
    for name, info in get_config().geo_sources.items():
        raw = info["projection"]
        value = raw if isinstance(raw, str) else raw.to_plain_dict()
        chart = PointMapChart(type="point_map", projection=value)
        assert chart.projection is not None, name


_BOARD = """\
title: Test
queries:
  q:
    type: values
    rows:
    - lat: 40.7
      lng: -74.0
charts:
  c:
    type: point_map
    query: q
    latitude: lat
    longitude: lng
{extra}
rows:
- c
"""


def test_compile_reports_unknown_projection_type() -> None:
    result = compile(_BOARD.format(extra="    projection: robinson"))
    assert not result.success
    assert "robinson" in str(result.errors)


@pytest.fixture
def catalog() -> YamlSchemaCatalog:
    return load_yaml_schema_catalog()


def _migrate(raw: dict[str, Any], catalog: YamlSchemaCatalog) -> dict[str, Any]:
    _, registry = _board_migration_context()
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", SchemaMigrationWarning)
        return migrate_mapping(raw, catalog=catalog, registry=registry)


@pytest.mark.parametrize("family", ["geoshape", "point_map"])
def test_theme_projection_key_is_stripped(
    family: str, catalog: YamlSchemaCatalog
) -> None:
    raw: dict[str, Any] = {
        "style": {
            "charts": {family: {"projection": {"type": "mercator"}, "aspect_ratio": 2}}
        },
        "charts": {"c": {"type": "point_map", "query": "q", "projection": "mercator"}},
        "rows": ["c"],
    }

    migrated = _migrate(raw, catalog)

    assert migrated["style"]["charts"][family] == {"aspect_ratio": 2}
    assert migrated["charts"]["c"]["projection"] == "mercator"
    AuthoredBoard.model_validate(migrated)


@pytest.mark.parametrize("chart_type", ["map", "geoshape", "point_map", "bubble_map"])
def test_chart_local_style_projection_is_stripped(
    chart_type: str, catalog: YamlSchemaCatalog
) -> None:
    raw: dict[str, Any] = {
        "charts": {
            "c": {
                "type": chart_type,
                "query": "q",
                "projection": "albersUsa",
                "style": {"projection": {"type": "mercator"}, "aspect_ratio": 2},
            }
        },
        "rows": ["c"],
    }

    migrated = _migrate(raw, catalog)

    assert migrated["charts"]["c"]["style"] == {"aspect_ratio": 2}
    assert migrated["charts"]["c"]["projection"] == "albersUsa"


def test_board_authoring_theme_projection_still_compiles() -> None:
    text = (
        _BOARD.format(extra="    projection: mercator")
        + "style:\n  charts:\n    point_map:\n      projection:\n"
        + "        type: albersUsa\n"
    )
    result = compile(text)
    assert result.success, result.errors


def _resolve_point_map(**authored: Any) -> Any:
    chart = NormalizedPointMapChart(id="c", type="point_map", **authored)
    style = resolve_chart_style_context(get_theme_style())
    return _resolve_point_map_chart(chart, [], style, 800.0, None)


def _resolve_geoshape(**authored: Any) -> Any:
    chart = NormalizedGeoshapeChart(id="c", type="geoshape", **authored)
    style = resolve_chart_style_context(get_theme_style())
    return _resolve_geoshape_chart(chart, [], style, 800.0, None)


def test_point_map_without_source_or_projection_is_an_error() -> None:
    with pytest.raises(CompilationError) as exc_info:
        _resolve_point_map()
    assert exc_info.value.code.code == "ERR-GEO-PROJECTION-REQUIRED"
    message = str(exc_info.value)
    assert "projection:" in message
    assert "/charts/maps/" in message


def test_geoshape_from_raw_url_without_projection_is_an_error() -> None:
    with pytest.raises(CompilationError) as exc_info:
        _resolve_geoshape(geo_source="https://example.com/regions.json")
    assert exc_info.value.code.code == "ERR-GEO-PROJECTION-REQUIRED"


def test_point_map_projection_wins_over_the_geo_source() -> None:
    resolved = _resolve_point_map(geo_source="us-states", projection="mercator")
    assert resolved.geo_projection_type == "mercator"


def test_point_map_takes_the_geo_source_projection() -> None:
    resolved = _resolve_point_map(geo_source="us-states")
    assert resolved.geo_projection_type == "albersUsa"


def test_projection_params_reach_the_resolved_chart_as_json_lists() -> None:
    resolved = _resolve_geoshape(
        geo_source="us-states",
        projection=Projection(type="mercator", center=(1, 2), scale=5),
    )
    assert resolved.geo_projection_params == {"center": [1.0, 2.0], "scale": 5.0}


def test_registry_params_survive_for_a_city_source() -> None:
    resolved = _resolve_geoshape(geo_source="sf-neighborhoods")
    assert resolved.geo_projection_type == "mercator"
    assert resolved.geo_projection_params["scale"] == 80000


def _project_source(**entry: Any) -> ConfigNode:
    return ConfigNode.model_validate({"url": "bundled:us-10m.json", **entry})


def test_project_geo_source_without_projection_is_the_required_error() -> None:
    chart = NormalizedGeoshapeChart(id="c", type="geoshape", geo_source="mine")
    with pytest.raises(CompilationError) as exc_info:
        _resolve_geo_projection(chart, "mine", _project_source())
    assert exc_info.value.code.code == "ERR-GEO-PROJECTION-REQUIRED"


def test_project_geo_source_with_unknown_projection_names_its_field() -> None:
    chart = NormalizedGeoshapeChart(id="c", type="geoshape", geo_source="mine")
    with pytest.raises(CompilationError) as exc_info:
        _resolve_geo_projection(chart, "mine", _project_source(projection="robinson"))
    assert exc_info.value.code.code == "ERR-VALIDATION-FIELD"
    assert "geo_sources.mine.projection" in str(exc_info.value)


def test_point_map_with_raw_url_basemap_and_no_projection_is_an_error() -> None:
    with pytest.raises(CompilationError) as exc_info:
        _resolve_point_map(basemap={"source": "https://example.com/regions.json"})
    assert exc_info.value.code.code == "ERR-GEO-PROJECTION-REQUIRED"


def test_geo_family_style_border_color_patch_validates() -> None:
    patch = StylePatch.model_validate(
        {"charts": {"geoshape": {"border": {"color": "red"}}}}
    )
    assert patch.charts is not None
    assert patch.charts.geoshape is not None
    assert patch.charts.geoshape.border is not None
    assert patch.charts.geoshape.border.color == "red"
