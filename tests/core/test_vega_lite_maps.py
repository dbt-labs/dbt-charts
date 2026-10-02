"""Tests for Vega-Lite map chart generation.

Tests the map rendering functionality in dbt_charts.core.render.vega_lite module:
- generate_map_spec() for choropleth rendering
- generate_point_map_spec() for point/bubble maps
- Data joining/lookup transforms
- Projection handling
- Geo source URL resolution
- Terminal fallback behavior
"""

from dbt_charts.core.compile.config import get_config, get_theme_style
from dbt_charts.core.compile.resolve import resolve
from dbt_charts.core.compile.resolve.style.board import (
    resolve_chart_style_context,
    resolve_style,
    resolve_style_and_context,
)
from dbt_charts.core.compile.vega_lite import VEGA_LITE_SCHEMA_URL
from dbt_charts.core.render.chart.vega_lite import generate_vega_lite_spec

_BOARD_STYLE = resolve_chart_style_context(get_theme_style())

# Uses make_chart fixture from tests/conftest.py


def _joined_choropleth_overlay(spec: dict) -> dict:
    """Return the data-colored choropleth layer from a joined map spec."""
    assert "layer" in spec, f"joined choropleth should be layered; got {spec!r}"
    assert len(spec["layer"]) == 2
    return spec["layer"][1]


def _choropleth_base_layer(spec: dict) -> dict:
    """Return the neutral geography layer from a joined map spec."""
    assert "layer" in spec, f"joined choropleth should be layered; got {spec!r}"
    assert len(spec["layer"]) == 2
    return spec["layer"][0]


# ============================================================================
# Tests for generate_map_spec (Choropleth)
# ============================================================================


class TestGenerateMapSpecChoropleth:
    """Tests for generate_map_spec() choropleth rendering."""

    def test_basic_choropleth_map(self, make_chart):
        """Test generating basic choropleth map spec."""
        chart = make_chart(
            "map",
            geo={"source": "us-states"},
            lookup="state_code",
            value="population",
        )
        data = [
            {"state_code": "06", "population": 39500000},
            {"state_code": "48", "population": 29000000},
        ]

        _rc = resolve(chart, data, chart_style_context=_BOARD_STYLE)
        spec = generate_vega_lite_spec(chart, data)
        overlay = _joined_choropleth_overlay(spec)

        # Should use geoshape mark
        assert overlay["mark"]["type"] == "geoshape"
        # Should have geo data URL
        assert overlay["data"]["url"] == get_config().geo_sources["us-states"]["url"]
        # Should have transform for data join
        assert "transform" in overlay
        # Should have color encoding
        assert "color" in overlay["encoding"]
        assert overlay["encoding"]["color"]["field"] == "population"

    def test_joined_choropleth_renders_unjoined_features_on_base_layer(
        self, make_chart
    ):
        """Joined choropleths paint the full geography below the data overlay."""
        chart = make_chart(
            "map",
            geo={"source": "world-countries"},
            lookup="country_id",
            value="payloads",
        )
        data = [{"country_id": "840", "payloads": 10}]

        _rc = resolve(chart, data, chart_style_context=_BOARD_STYLE)
        spec = generate_vega_lite_spec(chart, data)
        base = _choropleth_base_layer(spec)
        overlay = _joined_choropleth_overlay(spec)

        assert base["mark"]["type"] == "geoshape"
        assert base["data"]["url"] == get_config().geo_sources["world-countries"]["url"]
        assert "transform" not in base
        assert "encoding" not in base

        _empty_chart = make_chart("map", geo={"source": "world-countries"})
        _empty_rc = resolve(_empty_chart, [], chart_style_context=_BOARD_STYLE)
        empty_map = generate_vega_lite_spec(_empty_chart, [])
        neutral_fill = resolve_chart_style_context(
            get_theme_style(None)
        ).marks.geoshape.fill
        assert base["mark"]["fill"] == empty_map["mark"]["fill"]
        assert base["mark"]["fill"] == neutral_fill
        assert base["mark"]["fill"] != resolve_style(get_theme_style(None)).background

        assert "transform" in overlay
        assert overlay["encoding"]["color"]["field"] == "payloads"

    def test_choropleth_uses_albersUsa_projection_for_us(self, make_chart):
        """Test US choropleth uses albersUsa projection by default."""
        chart = make_chart(
            "map",
            geo={"source": "us-states"},
            lookup="id",
            value="value",
        )
        data = [{"id": "06", "value": 100}]

        _rc = resolve(chart, data, chart_style_context=_BOARD_STYLE)
        spec = generate_vega_lite_spec(chart, data)

        # US states should default to albersUsa projection
        assert spec["projection"]["type"] == "albersUsa"

    def test_choropleth_with_explicit_projection(self, make_chart):
        """Test choropleth with explicitly specified projection."""
        chart = make_chart(
            "map",
            geo={"source": "us-states"},
            lookup="id",
            value="value",
            projection="mercator",
        )
        data = [{"id": "06", "value": 100}]

        _rc = resolve(chart, data, chart_style_context=_BOARD_STYLE)
        spec = generate_vega_lite_spec(chart, data)

        # Should use the specified projection
        assert spec["projection"]["type"] == "mercator"

    def test_choropleth_mark_has_stroke_styling(self, make_chart):
        """Choropleth strokes are a halo knockout that tracks
        theme.background so region boundaries disappear cleanly into the
        canvas on any theme."""
        chart = make_chart(
            "map",
            geo={"source": "us-states"},
            lookup="id",
            value="value",
        )
        data = [{"id": "06", "value": 100}]

        _rc = resolve(chart, data, chart_style_context=_BOARD_STYLE)
        spec = generate_vega_lite_spec(chart, data)
        overlay = _joined_choropleth_overlay(spec)

        _empty_rc = resolve(
            make_chart("map", geo={"source": "us-states"}),
            [],
            chart_style_context=_BOARD_STYLE,
        )
        empty_map = generate_vega_lite_spec(
            make_chart("map", geo={"source": "us-states"}), []
        )
        assert overlay["mark"]["stroke"].lower() == empty_map["mark"]["stroke"].lower()
        assert overlay["mark"]["strokeWidth"] == 0.5

    def test_choropleth_has_tooltip_enabled(self, make_chart):
        """Test choropleth has tooltip enabled."""
        chart = make_chart(
            "map",
            geo={"source": "us-states"},
            lookup="state_code",
            value="population",
        )
        data = [{"state_code": "06", "population": 39500000}]

        _rc = resolve(chart, data, chart_style_context=_BOARD_STYLE)
        spec = generate_vega_lite_spec(chart, data)
        overlay = _joined_choropleth_overlay(spec)

        # Mark should have tooltip enabled
        assert overlay["mark"]["tooltip"] is True

    def test_choropleth_with_title(self, make_chart):
        """Test choropleth with title."""
        chart = make_chart(
            "map",
            title="US Population Map",
            geo={"source": "us-states"},
            lookup="id",
            value="population",
        )
        data = [{"id": "06", "population": 39500000}]

        _rc = resolve(chart, data, chart_style_context=_BOARD_STYLE)
        spec = generate_vega_lite_spec(chart, data)

        # Should have title
        assert spec["title"]["text"] == "US Population Map"

    def test_choropleth_color_encoding_has_quantitative_type(self, make_chart):
        """Test choropleth color encoding uses quantitative type."""
        chart = make_chart(
            "map",
            geo={"source": "us-states"},
            lookup="id",
            value="population",
        )
        data = [{"id": "06", "population": 39500000}]

        _rc = resolve(chart, data, chart_style_context=_BOARD_STYLE)
        spec = generate_vega_lite_spec(chart, data)
        overlay = _joined_choropleth_overlay(spec)

        assert overlay["encoding"]["color"]["type"] == "quantitative"

    def test_choropleth_with_dimensions(self, make_chart):
        """Test choropleth with explicit width and height."""
        chart = make_chart(
            "map",
            geo={"source": "us-states"},
            lookup="id",
            value="value",
        )
        data = [{"id": "06", "value": 100}]

        _rc = resolve(chart, data, chart_style_context=_BOARD_STYLE)
        spec = generate_vega_lite_spec(chart, data, width=800, height=500)

        assert spec["width"] == 800
        assert spec["height"] == 500

    def test_neighborhood_choropleth_via_geo_source_string_uses_configured_key(
        self, make_chart
    ):
        """geo_source: sf-neighborhoods must join on properties.nhood, not 'id'.

        When geo_source is specified as a top-level string field (not via the
        geo: {source: ...} dict form), the lookup geo_key must be read from the
        geo_sources config entry rather than falling back to the hardcoded "id"
        default. The "id" default is only correct for TopoJSON sources; GeoJSON
        city-neighborhood sources use `properties.<field>` paths.
        """
        chart = make_chart(
            "map",
            geo_source="sf-neighborhoods",
            lookup="nhood",
            value="population",
        )
        data = [{"nhood": "Mission", "population": 58000}]

        _rc = resolve(chart, data, chart_style_context=_BOARD_STYLE)
        spec = generate_vega_lite_spec(chart, data)
        overlay = _joined_choropleth_overlay(spec)

        geo_key = get_config().geo_sources["sf-neighborhoods"]["key"]
        assert overlay["transform"][0]["lookup"] == geo_key
        assert overlay["transform"][0]["from"]["key"] == "nhood"

    def test_nyc_neighborhoods_choropleth_uses_configured_key(self, make_chart):
        """geo_source: nyc-neighborhoods must join on properties.ntaname."""
        chart = make_chart(
            "map",
            geo_source="nyc-neighborhoods",
            lookup="ntaname",
            value="median_income",
        )
        data = [{"ntaname": "Midtown-Times Square", "median_income": 85000}]

        _rc = resolve(chart, data, chart_style_context=_BOARD_STYLE)
        spec = generate_vega_lite_spec(chart, data)
        overlay = _joined_choropleth_overlay(spec)

        geo_key = get_config().geo_sources["nyc-neighborhoods"]["key"]
        assert overlay["transform"][0]["lookup"] == geo_key
        assert overlay["transform"][0]["from"]["key"] == "ntaname"


class TestGenerateMapSpecEmpty:
    """Tests for generate_map_spec() with no data."""

    def test_map_without_data_renders_base_map(self, make_chart):
        """Test map without data renders base geographic shapes."""
        chart = make_chart(
            "map",
            geo={"source": "us-states"},
        )
        data = []

        _rc = resolve(chart, data, chart_style_context=_BOARD_STYLE)
        spec = generate_vega_lite_spec(chart, data)

        # Should still render map
        assert spec["mark"]["type"] == "geoshape"
        # No transform for empty data
        assert "transform" not in spec or spec.get("transform") == []
        # Should have a concrete fill color for base geography.
        assert spec["mark"]["fill"]

    def test_map_without_data_uses_active_theme_colors(self, make_chart):
        """Empty maps should derive default fills from the board's resolved style."""
        chart = make_chart(
            "map",
            geo={"source": "us-states"},
        )
        dark_rs, dark_ctx = resolve_style_and_context(get_theme_style("neon"))

        _rc = resolve(chart, [], chart_style_context=dark_ctx)
        spec = generate_vega_lite_spec(
            chart, data=[], board_style=dark_rs, chart_style_context=dark_ctx
        )

        # Empty-map fill uses the dedicated neutral geoshape mark fill.
        assert spec["mark"]["fill"] == dark_ctx.marks.geoshape.fill

    def test_map_without_lookup_shows_base_map(self, make_chart):
        """Test map without lookup field shows base map."""
        chart = make_chart(
            "map",
            geo={"source": "us-states"},
            # No lookup or value specified
        )
        data = [{"some_field": "value"}]

        _rc = resolve(chart, data, chart_style_context=_BOARD_STYLE)
        spec = generate_vega_lite_spec(chart, data)

        # Should render base map
        assert spec["mark"]["type"] == "geoshape"


# ============================================================================
# Tests for World Map Choropleth
# ============================================================================


class TestGenerateWorldMapSpec:
    """Tests for world map choropleth rendering."""

    def test_world_map_uses_equalEarth_projection(self, make_chart):
        """Test world map uses equalEarth projection by default."""
        chart = make_chart(
            "map",
            geo={"source": "world-countries"},
            lookup="country_code",
            value="gdp",
        )
        data = [{"country_code": "840", "gdp": 21000000}]

        _rc = resolve(chart, data, chart_style_context=_BOARD_STYLE)
        spec = generate_vega_lite_spec(chart, data)

        # World maps should default to equalEarth projection
        assert spec["projection"]["type"] == "equalEarth"

    def test_world_map_uses_correct_geo_source(self, make_chart):
        """Test world map uses correct geo source URL."""
        chart = make_chart(
            "map",
            geo={"source": "world-countries"},
            lookup="id",
            value="value",
        )
        data = [{"id": "840", "value": 100}]

        _rc = resolve(chart, data, chart_style_context=_BOARD_STYLE)
        spec = generate_vega_lite_spec(chart, data)

        overlay = _joined_choropleth_overlay(spec)
        assert (
            overlay["data"]["url"] == get_config().geo_sources["world-countries"]["url"]
        )

    def test_world_50m_map_source(self, make_chart):
        """Test world-50m geo source."""
        chart = make_chart(
            "map",
            geo={"source": "world-50m"},
            lookup="id",
            value="value",
        )
        data = [{"id": "840", "value": 100}]

        _rc = resolve(chart, data, chart_style_context=_BOARD_STYLE)
        spec = generate_vega_lite_spec(chart, data)

        overlay = _joined_choropleth_overlay(spec)
        assert overlay["data"]["url"] == get_config().geo_sources["world-50m"]["url"]


# ============================================================================
# Tests for US Counties Map
# ============================================================================


class TestGenerateUSCountiesMapSpec:
    """Tests for US counties map rendering."""

    def test_us_counties_map(self, make_chart):
        """Test US counties choropleth map."""
        chart = make_chart(
            "map",
            geo={"source": "us-counties"},
            lookup="fips",
            value="unemployment",
        )
        data = [{"fips": "06001", "unemployment": 5.5}]

        _rc = resolve(chart, data, chart_style_context=_BOARD_STYLE)
        spec = generate_vega_lite_spec(chart, data)

        overlay = _joined_choropleth_overlay(spec)
        assert overlay["data"]["url"] == get_config().geo_sources["us-counties"]["url"]
        assert spec["projection"]["type"] == "albersUsa"


# ============================================================================
# Tests for generate_point_map_spec (Point/Bubble Maps)
# ============================================================================


class TestGeneratePointMapSpec:
    """Tests for generate_point_map_spec() for point maps."""

    def test_basic_point_map(self, make_chart):
        """Test generating basic point map spec."""
        chart = make_chart(
            "point_map",
            latitude="lat",
            longitude="lng",
        )
        data = [
            {"lat": 34.05, "lng": -118.25, "name": "Los Angeles"},
            {"lat": 40.71, "lng": -74.01, "name": "New York"},
        ]

        _rc = resolve(chart, data, chart_style_context=_BOARD_STYLE)
        spec = generate_vega_lite_spec(chart, data)

        # Should use circle mark for points
        assert spec["mark"]["type"] == "circle"
        # Should have lat/lng encoding
        assert "latitude" in spec["encoding"]
        assert "longitude" in spec["encoding"]
        assert spec["encoding"]["latitude"]["field"] == "lat"
        assert spec["encoding"]["longitude"]["field"] == "lng"

    def test_point_map_with_projection(self, make_chart):
        """Test point map with explicit projection."""
        chart = make_chart(
            "point_map",
            latitude="lat",
            longitude="lng",
            projection="mercator",
        )
        data = [{"lat": 34.05, "lng": -118.25}]

        _rc = resolve(chart, data, chart_style_context=_BOARD_STYLE)
        spec = generate_vega_lite_spec(chart, data)

        assert spec["projection"]["type"] == "mercator"

    def test_point_map_auto_detects_lat_field(self, make_chart):
        """Test point map auto-detects latitude field name."""
        chart = make_chart(
            "point_map",
            # No explicit latitude/longitude
        )
        data = [{"latitude": 34.05, "longitude": -118.25}]

        _rc = resolve(chart, data, chart_style_context=_BOARD_STYLE)
        spec = generate_vega_lite_spec(chart, data)

        assert spec["encoding"]["latitude"]["field"] == "latitude"
        assert spec["encoding"]["longitude"]["field"] == "longitude"

    def test_point_map_default_projection_albersUsa(self, make_chart):
        """Test point map defaults to albersUsa projection."""
        chart = make_chart(
            "point_map",
            latitude="lat",
            longitude="lng",
        )
        data = [{"lat": 34.05, "lng": -118.25}]

        _rc = resolve(chart, data, chart_style_context=_BOARD_STYLE)
        spec = generate_vega_lite_spec(chart, data)

        assert spec["projection"]["type"] == "albersUsa"


class TestGenerateBubbleMapSpec:
    """Tests for generate_point_map_spec() for bubble maps."""

    def test_bubble_map_with_size_encoding(self, make_chart):
        """Test bubble map with size encoding."""
        chart = make_chart(
            "bubble_map",
            latitude="lat",
            longitude="lng",
            size="population",
        )
        data = [
            {"lat": 34.05, "lng": -118.25, "population": 3900000},
            {"lat": 40.71, "lng": -74.01, "population": 8300000},
        ]

        _rc = resolve(chart, data, chart_style_context=_BOARD_STYLE)
        spec = generate_vega_lite_spec(chart, data)

        # Should have size encoding
        assert "size" in spec["encoding"]
        assert spec["encoding"]["size"]["field"] == "population"
        assert spec["encoding"]["size"]["type"] == "quantitative"

    def test_bubble_map_size_scale(self, make_chart):
        """Test bubble map has proper size scale."""
        chart = make_chart(
            "bubble_map",
            latitude="lat",
            longitude="lng",
            size="value",
        )
        data = [{"lat": 34.05, "lng": -118.25, "value": 100}]

        _rc = resolve(chart, data, chart_style_context=_BOARD_STYLE)
        spec = generate_vega_lite_spec(chart, data)

        # Should have scale range for sizes, anchored at 0 so area (VL circle
        # "size" is rendered area) scales proportionally with the value
        # rather than affinely (a non-zero floor under-reports large values).
        assert "scale" in spec["encoding"]["size"]
        assert spec["encoding"]["size"]["scale"]["range"][0] == 0

    def test_bubble_map_with_color(self, make_chart):
        """Test bubble map with color encoding."""
        chart = make_chart(
            "bubble_map",
            latitude="lat",
            longitude="lng",
            size="revenue",
            color="store_type",
        )
        data = [{"lat": 34.05, "lng": -118.25, "revenue": 1000, "store_type": "retail"}]

        _rc = resolve(chart, data, chart_style_context=_BOARD_STYLE)
        spec = generate_vega_lite_spec(chart, data)

        # Should have color encoding
        assert "color" in spec["encoding"]
        assert spec["encoding"]["color"]["field"] == "store_type"


class TestPointMapWithBackground:
    """Tests for point/bubble maps with background geoshape."""

    def test_point_map_with_geo_source_background(self, make_chart):
        """Test point map with geo source creates layered spec."""
        chart = make_chart(
            "bubble_map",
            latitude="lat",
            longitude="lng",
            size="revenue",
            geo_source="us-states",
        )
        data = [{"lat": 34.05, "lng": -118.25, "revenue": 1000}]

        _rc = resolve(chart, data, chart_style_context=_BOARD_STYLE)
        spec = generate_vega_lite_spec(chart, data)

        # Should be a layered spec
        assert "layer" in spec
        assert len(spec["layer"]) >= 2
        # First layer should be background geoshape
        assert spec["layer"][0]["mark"]["type"] == "geoshape"
        # Second layer should be circles
        assert spec["layer"][1]["mark"]["type"] == "circle"

    def test_point_map_background_uses_geo_url(self, make_chart):
        """Test point map background uses geo source URL."""
        chart = make_chart(
            "point_map",
            latitude="lat",
            longitude="lng",
            geo_source="us-states",
        )
        data = [{"lat": 34.05, "lng": -118.25}]

        _rc = resolve(chart, data, chart_style_context=_BOARD_STYLE)
        spec = generate_vega_lite_spec(chart, data)

        # Background layer should use geo URL
        assert (
            spec["layer"][0]["data"]["url"]
            == get_config().geo_sources["us-states"]["url"]
        )

    def test_point_map_background_styling(self, make_chart):
        """Test point map background has proper styling."""
        chart = make_chart(
            "point_map",
            latitude="lat",
            longitude="lng",
            basemap={"source": "us-states", "fill": "#f5f5f5", "stroke": "#cccccc"},
        )
        data = [{"lat": 34.05, "lng": -118.25}]

        _rc = resolve(chart, data, chart_style_context=_BOARD_STYLE)
        spec = generate_vega_lite_spec(chart, data)

        # Background should have specified styling
        bg_mark = spec["layer"][0]["mark"]
        assert bg_mark["fill"] == "#f5f5f5"
        assert bg_mark["stroke"] == "#cccccc"

    def test_point_map_geo_source_without_explicit_fill_uses_theme_neutral_fill(
        self, make_chart
    ):
        """geo_source without basemap.fill must use theme's neutral geoshape fill.

        Regression: when no explicit basemap fill is authored, the emitter left the
        background geoshape mark with no fill, causing Vega-Lite to render it with
        its default blue (#4c78a8) instead of the neutral gray from the theme.
        """
        chart = make_chart(
            "point_map",
            latitude="lat",
            longitude="lng",
            geo_source="us-states",
        )
        data = [{"lat": 34.05, "lng": -118.25}]

        rc = resolve(chart, data, chart_style_context=_BOARD_STYLE)
        spec = generate_vega_lite_spec(chart, data)

        neutral_fill = _BOARD_STYLE.marks.geoshape.fill
        assert neutral_fill is not None, "Theme must supply a neutral geoshape fill"
        assert rc.basemap_fill == neutral_fill
        bg_mark = spec["layer"][0]["mark"]
        assert bg_mark["fill"] == neutral_fill


# ============================================================================
# Tests for Projection Handling
# ============================================================================


class TestProjectionHandling:
    """Tests for map projection handling."""

    def test_mercator_projection(self, make_chart):
        """Test mercator projection."""
        chart = make_chart(
            "map",
            geo={"source": "world-countries"},
            lookup="id",
            value="value",
            projection="mercator",
        )
        data = [{"id": "840", "value": 100}]

        _rc = resolve(chart, data, chart_style_context=_BOARD_STYLE)
        spec = generate_vega_lite_spec(chart, data)

        assert spec["projection"]["type"] == "mercator"

    def test_equalEarth_projection(self, make_chart):
        """Test equalEarth projection."""
        chart = make_chart(
            "map",
            geo={"source": "world-countries"},
            lookup="id",
            value="value",
            projection="equalEarth",
        )
        data = [{"id": "840", "value": 100}]

        _rc = resolve(chart, data, chart_style_context=_BOARD_STYLE)
        spec = generate_vega_lite_spec(chart, data)

        assert spec["projection"]["type"] == "equalEarth"

    def test_albersUsa_projection(self, make_chart):
        """Test albersUsa projection."""
        chart = make_chart(
            "map",
            geo={"source": "us-states"},
            lookup="id",
            value="value",
            projection="albersUsa",
        )
        data = [{"id": "06", "value": 100}]

        _rc = resolve(chart, data, chart_style_context=_BOARD_STYLE)
        spec = generate_vega_lite_spec(chart, data)

        assert spec["projection"]["type"] == "albersUsa"

    def test_naturalEarth1_projection(self, make_chart):
        """Test naturalEarth1 projection."""
        chart = make_chart(
            "map",
            geo={"source": "world-countries"},
            lookup="id",
            value="value",
            projection="naturalEarth1",
        )
        data = [{"id": "840", "value": 100}]

        _rc = resolve(chart, data, chart_style_context=_BOARD_STYLE)
        spec = generate_vega_lite_spec(chart, data)

        assert spec["projection"]["type"] == "naturalEarth1"

    def test_orthographic_projection(self, make_chart):
        """Test orthographic (globe) projection."""
        chart = make_chart(
            "map",
            geo={"source": "world-countries"},
            lookup="id",
            value="value",
            projection="orthographic",
        )
        data = [{"id": "840", "value": 100}]

        _rc = resolve(chart, data, chart_style_context=_BOARD_STYLE)
        spec = generate_vega_lite_spec(chart, data)

        assert spec["projection"]["type"] == "orthographic"

    def test_projection_dict_config(self, make_chart):
        """Test projection with dict configuration."""
        chart = make_chart(
            "map",
            geo={"source": "world-countries"},
            lookup="id",
            value="value",
            projection={"type": "orthographic", "rotate": [90, -30, 0]},
        )
        data = [{"id": "840", "value": 100}]

        _rc = resolve(chart, data, chart_style_context=_BOARD_STYLE)
        spec = generate_vega_lite_spec(chart, data)

        assert spec["projection"]["type"] == "orthographic"
        assert spec["projection"]["rotate"] == [90, -30, 0]


# ============================================================================
# Tests for Geo Source URL Resolution
# ============================================================================


class TestGeoSourceResolution:
    """Tests for geographic source URL resolution."""

    def test_custom_geo_url(self, make_chart):
        """Test custom geo URL is used directly."""
        chart = make_chart(
            "map",
            geo={"source": "https://example.com/custom-geo.json", "feature": "regions"},
            lookup="id",
            value="value",
        )
        data = [{"id": "1", "value": 100}]

        _rc = resolve(chart, data, chart_style_context=_BOARD_STYLE)
        spec = generate_vega_lite_spec(chart, data)

        overlay = _joined_choropleth_overlay(spec)
        assert overlay["data"]["url"] == "https://example.com/custom-geo.json"

    def test_geo_source_topojson_format(self, make_chart):
        """Test geo source uses TopoJSON format correctly."""
        chart = make_chart(
            "map",
            geo={"source": "us-states"},
            lookup="id",
            value="value",
        )
        data = [{"id": "06", "value": 100}]

        _rc = resolve(chart, data, chart_style_context=_BOARD_STYLE)
        spec = generate_vega_lite_spec(chart, data)

        overlay = _joined_choropleth_overlay(spec)
        assert overlay["data"]["format"]["type"] == "topojson"
        assert overlay["data"]["format"]["feature"] == "states"


# ============================================================================
# Tests for City Neighborhood Presets
# ============================================================================


class TestCityNeighborhoodPresets:
    """Tests for city-level geographic presets."""

    def test_city_map_uses_dict_projection(self, make_chart):
        """Test city maps use mercator projection with center/scale from geo_defaults.yml."""
        chart = make_chart(
            "map",
            geo_source="sf-neighborhoods",
            lookup="nhood",
            value="population",
        )
        data = [{"nhood": "Mission", "population": 58000}]

        _rc = resolve(chart, data, chart_style_context=_BOARD_STYLE)
        spec = generate_vega_lite_spec(chart, data)

        assert spec["projection"]["type"] == "mercator"
        assert "center" in spec["projection"]
        assert "scale" in spec["projection"]

    def test_city_map_user_projection_overrides(self, make_chart):
        """Test user-provided projection overrides city default."""
        chart = make_chart(
            "map",
            geo_source="sf-neighborhoods",
            lookup="nhood",
            value="population",
            projection="equalEarth",  # Override the mercator default
        )
        data = [{"nhood": "Mission", "population": 58000}]

        _rc = resolve(chart, data, chart_style_context=_BOARD_STYLE)
        spec = generate_vega_lite_spec(chart, data)

        # User projection should override the city preset
        assert spec["projection"]["type"] == "equalEarth"

    def test_point_map_with_city_geo_source(self, make_chart):
        """Test point map uses city geo source projection for background."""
        chart = make_chart(
            "point_map",
            geo_source="sf-neighborhoods",
            latitude="lat",
            longitude="lng",
        )
        data = [
            {"lat": 37.7599, "lng": -122.4148, "name": "Mission"},
            {"lat": 37.7614, "lng": -122.4349, "name": "Castro"},
        ]

        _rc = resolve(chart, data, chart_style_context=_BOARD_STYLE)
        spec = generate_vega_lite_spec(chart, data)

        # Should use the mercator projection from sf-neighborhoods with center/scale
        assert spec["projection"]["type"] == "mercator"
        assert "center" in spec["projection"]
        assert "scale" in spec["projection"]

    def test_bubble_map_with_city_geo_source(self, make_chart):
        """Test bubble map uses city geo source projection for background."""
        chart = make_chart(
            "bubble_map",
            geo_source="nyc-neighborhoods",
            latitude="lat",
            longitude="lng",
            size="value",
        )
        data = [
            {"lat": 40.7580, "lng": -73.9855, "name": "Midtown", "value": 100},
            {"lat": 40.7831, "lng": -73.9712, "name": "Upper East", "value": 200},
        ]

        _rc = resolve(chart, data, chart_style_context=_BOARD_STYLE)
        spec = generate_vega_lite_spec(chart, data)

        # Should use the mercator projection from nyc-neighborhoods
        assert spec["projection"]["type"] == "mercator"
        assert "center" in spec["projection"]


# ============================================================================
# Tests for Color Scheme Handling
# ============================================================================


class TestMapColorSchemes:
    """Tests for map color scheme handling."""

    def test_default_color_scheme_is_themed_sequential_palette(self, make_chart):
        """The rendered choropleth's color range resolves through the full
        pipeline to the default theme's configured sequential-palette hex
        stops, not Vega-Lite's raw `blues` scheme.

        Reads which `dbt-seq-*` palette the theme currently configures
        (rather than pinning e.g. `dbt-seq-blue` literally — a theme's
        specific palette choice is tunable, `dbt-charts/AGENTS.md`: "don't pin
        theme/default values") and asserts the render actually carries that
        palette's hex stops end to end. The structural claim that every
        theme picks a `dbt-seq-*` palette (never a raw Vega scheme) is
        covered separately, across all themes, by
        `test_geoshape_gradient_palette_is_themed`.
        """
        from dbt_charts.core.compile.resolve.style.palette import (
            palette as resolve_palette,
        )

        chart = make_chart(
            "map",
            geo={"source": "us-states"},
            lookup="id",
            value="value",
        )
        data = [{"id": "06", "value": 100}]

        _rc = resolve(chart, data, chart_style_context=_BOARD_STYLE)
        spec = generate_vega_lite_spec(chart, data)

        themed_palette = get_theme_style().charts.geoshape.color.gradient.palette
        overlay = _joined_choropleth_overlay(spec)
        assert overlay["encoding"]["color"]["scale"]["range"] == resolve_palette(
            themed_palette
        )

    def test_custom_color_scheme(self, make_chart):
        """Test custom color scheme from style."""
        chart = make_chart(
            "map",
            geo={"source": "us-states"},
            lookup="id",
            value="value",
            style={"color": {"gradient": {"palette": "viridis"}}},
        )
        data = [{"id": "06", "value": 100}]

        _rc = resolve(chart, data, chart_style_context=_BOARD_STYLE)
        spec = generate_vega_lite_spec(chart, data)

        overlay = _joined_choropleth_overlay(spec)
        assert overlay["encoding"]["color"]["scale"]["scheme"] == "viridis"

    def test_choropleth_gradient_domain_min_max(self, make_chart):
        """min/max authored on the choropleth gradient must reach the VL scale, not be silently dropped."""
        chart = make_chart(
            "map",
            geo={"source": "us-states"},
            lookup="id",
            value="value",
            style={"color": {"gradient": {"palette": "blues", "min": 0, "max": 100}}},
        )
        data = [{"id": "06", "value": 50}]

        _rc = resolve(chart, data, chart_style_context=_BOARD_STYLE)
        spec = generate_vega_lite_spec(chart, data)

        overlay = _joined_choropleth_overlay(spec)
        scale = overlay["encoding"]["color"]["scale"]
        assert scale["domain"] == [0, 100]


# ============================================================================
# Tests for Map Tooltips
# ============================================================================


class TestMapTooltips:
    """Tests for map tooltip generation."""

    def test_choropleth_tooltip_includes_fields(self, make_chart):
        """Test choropleth tooltip includes lookup and value fields."""
        chart = make_chart(
            "map",
            geo={"source": "us-states"},
            lookup="state_code",
            value="population",
        )
        data = [{"state_code": "06", "population": 39500000}]

        _rc = resolve(chart, data, chart_style_context=_BOARD_STYLE)
        spec = generate_vega_lite_spec(chart, data)

        overlay = _joined_choropleth_overlay(spec)
        tooltip = overlay["encoding"]["tooltip"]
        fields = [t["field"] for t in tooltip]

        assert "state_code" in fields
        assert "population" in fields

    def test_point_map_tooltip(self, make_chart):
        """Test point map has tooltip encoding."""
        chart = make_chart(
            "point_map",
            latitude="lat",
            longitude="lng",
        )
        data = [{"lat": 34.05, "lng": -118.25, "name": "LA"}]

        _rc = resolve(chart, data, chart_style_context=_BOARD_STYLE)
        spec = generate_vega_lite_spec(chart, data)

        assert "tooltip" in spec["encoding"]


# ============================================================================
# Tests for Terminal Fallback
# ============================================================================


class TestMapTerminalFallback:
    """Tests for map terminal fallback behavior."""

    def test_map_falls_back_to_table(self):
        """Test map type falls back to table in terminal mode."""
        from dbt_charts.core.render.terminal_charts import _get_terminal_chart_type

        assert _get_terminal_chart_type("map") == "table"

    def test_point_map_falls_back_to_table(self):
        """Test point_map falls back to table in terminal mode."""
        from dbt_charts.core.render.terminal_charts import _get_terminal_chart_type

        assert _get_terminal_chart_type("point_map") == "table"

    def test_bubble_map_falls_back_to_table(self):
        """Test bubble_map falls back to table in terminal mode."""
        from dbt_charts.core.render.terminal_charts import _get_terminal_chart_type

        assert _get_terminal_chart_type("bubble_map") == "table"

    def test_geoshape_falls_back_to_table(self):
        """Test geoshape falls back to table in terminal mode."""
        from dbt_charts.core.render.terminal_charts import _get_terminal_chart_type

        assert _get_terminal_chart_type("geoshape") == "table"

    def test_terminal_render_shows_data_table(self, make_chart):
        """Test terminal rendering of map shows data as table."""
        from dbt_charts.core.render.terminal_charts import render_chart_terminal

        chart = make_chart(
            "map",
            geo={"source": "us-states"},
            lookup="state_code",
            value="population",
        )
        data = [
            {"state_code": "CA", "population": 39500000},
            {"state_code": "TX", "population": 29000000},
        ]

        result = render_chart_terminal(
            chart, data, "", width=80, height=20, formats=None
        )

        # Should render as table with data values
        assert "CA" in result or "state_code" in result


# ============================================================================
# Tests for Data Type Handling
# ============================================================================


class TestMapDataTypes:
    """Tests for map data type handling."""

    def test_decimal_values_converted(self, make_chart):
        """Test Decimal values are converted to float."""
        from decimal import Decimal

        chart = make_chart(
            "map",
            geo={"source": "us-states"},
            lookup="id",
            value="value",
        )
        data = [{"id": "06", "value": Decimal("123.45")}]

        _rc = resolve(chart, data, chart_style_context=_BOARD_STYLE)
        spec = generate_vega_lite_spec(chart, data)

        # Lookup transform data should have float values
        overlay = _joined_choropleth_overlay(spec)
        lookup_data = overlay["transform"][0]["from"]["data"]["values"]
        assert isinstance(lookup_data[0]["value"], float)

    def test_integer_values_preserved(self, make_chart):
        """Test integer values work correctly."""
        chart = make_chart(
            "map",
            geo={"source": "us-states"},
            lookup="id",
            value="population",
        )
        data = [{"id": "06", "population": 39500000}]

        _rc = resolve(chart, data, chart_style_context=_BOARD_STYLE)
        spec = generate_vega_lite_spec(chart, data)

        overlay = _joined_choropleth_overlay(spec)
        lookup_data = overlay["transform"][0]["from"]["data"]["values"]
        assert lookup_data[0]["population"] == 39500000


# ============================================================================
# Tests for Map Type Aliases
# ============================================================================


class TestMapTypeAliases:
    """Tests for map type aliases."""

    def test_map_type_generates_geoshape(self, make_chart):
        """Test 'map' type generates geoshape mark."""
        chart = make_chart(
            "map",
            geo={"source": "us-states"},
            lookup="id",
            value="value",
        )
        data = [{"id": "06", "value": 100}]

        _rc = resolve(chart, data, chart_style_context=_BOARD_STYLE)
        spec = generate_vega_lite_spec(chart, data)

        overlay = _joined_choropleth_overlay(spec)
        assert overlay["mark"]["type"] == "geoshape"

    def test_choropleth_type_generates_geoshape(self, make_chart):
        """Test 'choropleth' type generates geoshape mark."""
        chart = make_chart(
            "map",
            geo={"source": "us-states"},
            lookup="id",
            value="value",
        )
        data = [{"id": "06", "value": 100}]

        _rc = resolve(chart, data, chart_style_context=_BOARD_STYLE)
        spec = generate_vega_lite_spec(chart, data)

        overlay = _joined_choropleth_overlay(spec)
        assert overlay["mark"]["type"] == "geoshape"

    def test_geoshape_type_generates_geoshape(self, make_chart):
        """Test 'geoshape' type generates geoshape mark."""
        chart = make_chart(
            "geoshape",
            geo={"source": "us-states"},
            lookup="id",
            value="value",
        )
        data = [{"id": "06", "value": 100}]

        _rc = resolve(chart, data, chart_style_context=_BOARD_STYLE)
        spec = generate_vega_lite_spec(chart, data)

        overlay = _joined_choropleth_overlay(spec)
        assert overlay["mark"]["type"] == "geoshape"


# ============================================================================
# Tests for Autosize Configuration
# ============================================================================


class TestMapAutosize:
    """Tests for map autosize configuration."""

    def test_map_has_fit_autosize(self, make_chart):
        """Test map has fit autosize configuration."""
        chart = make_chart(
            "map",
            geo={"source": "us-states"},
            lookup="id",
            value="value",
        )
        data = [{"id": "06", "value": 100}]

        _rc = resolve(chart, data, chart_style_context=_BOARD_STYLE)
        spec = generate_vega_lite_spec(chart, data)

        assert spec["autosize"]["type"] == "fit"
        assert spec["autosize"]["contains"] == "padding"

    def test_map_has_null_background(self, make_chart):
        """Test map has null background for transparency."""
        chart = make_chart(
            "map",
            geo={"source": "us-states"},
            lookup="id",
            value="value",
        )
        data = [{"id": "06", "value": 100}]

        _rc = resolve(chart, data, chart_style_context=_BOARD_STYLE)
        spec = generate_vega_lite_spec(chart, data)

        assert spec["background"] is None


# ============================================================================
# Integration-style Tests
# ============================================================================


class TestMapSpecIntegration:
    """Integration-style tests for complete map specifications."""

    def test_complete_us_choropleth_spec(self, make_chart):
        """Test complete US choropleth specification."""
        chart = make_chart(
            "map",
            title="US Population by State",
            geo={"source": "us-states", "key": "id"},
            lookup="state_id",
            value="population",
            projection="albersUsa",
            style={"color": {"gradient": {"palette": "blues"}}},
        )
        data = [
            {"state_id": "06", "population": 39500000, "name": "California"},
            {"state_id": "48", "population": 29000000, "name": "Texas"},
            {"state_id": "12", "population": 21500000, "name": "Florida"},
        ]

        _rc = resolve(chart, data, chart_style_context=_BOARD_STYLE)
        spec = generate_vega_lite_spec(chart, data, width=800, height=500)

        # Verify all key components
        assert spec["$schema"] == VEGA_LITE_SCHEMA_URL
        assert spec["title"]["text"] == "US Population by State"
        assert spec["projection"]["type"] == "albersUsa"
        overlay = _joined_choropleth_overlay(spec)
        assert overlay["mark"]["type"] == "geoshape"
        assert overlay["data"]["url"] == get_config().geo_sources["us-states"]["url"]
        assert overlay["encoding"]["color"]["field"] == "population"
        assert overlay["encoding"]["color"]["scale"]["scheme"] == "blues"
        assert spec["width"] == 800
        assert spec["height"] == 500
        assert len(overlay["transform"]) >= 1
        assert overlay["transform"][0]["lookup"] == "id"

    def test_complete_world_choropleth_spec(self, make_chart):
        """Test complete world choropleth specification."""
        chart = make_chart(
            "map",
            title="World GDP",
            geo={"source": "world-countries"},
            lookup="country_id",
            value="gdp",
            projection="equalEarth",
        )
        data = [
            {"country_id": "840", "gdp": 21000000, "name": "USA"},
            {"country_id": "156", "gdp": 14000000, "name": "China"},
        ]

        _rc = resolve(chart, data, chart_style_context=_BOARD_STYLE)
        spec = generate_vega_lite_spec(chart, data)

        assert spec["projection"]["type"] == "equalEarth"
        overlay = _joined_choropleth_overlay(spec)
        assert "world-110m.json" in overlay["data"]["url"]

    def test_complete_bubble_map_spec(self, make_chart):
        """Test complete bubble map specification."""
        chart = make_chart(
            "bubble_map",
            title="Store Locations",
            latitude="lat",
            longitude="lng",
            size="revenue",
            color="type",
            geo_source="us-states",
            projection="albersUsa",
        )
        data = [
            {"lat": 34.05, "lng": -118.25, "revenue": 1000000, "type": "flagship"},
            {"lat": 40.71, "lng": -74.01, "revenue": 800000, "type": "regular"},
        ]

        _rc = resolve(chart, data, chart_style_context=_BOARD_STYLE)
        spec = generate_vega_lite_spec(chart, data)

        # Should be layered with background
        assert "layer" in spec
        # Background layer
        assert spec["layer"][0]["mark"]["type"] == "geoshape"
        # Points layer
        assert spec["layer"][1]["mark"]["type"] == "circle"
        assert spec["layer"][1]["encoding"]["size"]["field"] == "revenue"
        assert spec["layer"][1]["encoding"]["color"]["field"] == "type"
