"""Tests for Vega-Lite map chart generation.

Tests the map rendering functionality in dbt_charts.core.render.vega_lite module:
- generate_map_spec() for choropleth rendering
- generate_point_map_spec() for point/bubble maps
- Data joining/lookup transforms
- Projection handling
- Geo source URL resolution
- Terminal fallback behavior
"""

from dbt_charts.core.compile.config import get_theme_style

_KPI_COMPACT = get_theme_style().charts.kpi.value.compact


# Uses make_chart fixture from tests/conftest.py


# ============================================================================
# Tests for generate_map_spec (Choropleth)
# ============================================================================


class TestGenerateMapSpecChoropleth:
    """Tests for generate_map_spec() choropleth rendering."""


class TestGenerateMapSpecEmpty:
    """Tests for generate_map_spec() with no data."""


# ============================================================================
# Tests for World Map Choropleth
# ============================================================================


class TestGenerateWorldMapSpec:
    """Tests for world map choropleth rendering."""


# ============================================================================
# Tests for US Counties Map
# ============================================================================


class TestGenerateUSCountiesMapSpec:
    """Tests for US counties map rendering."""


# ============================================================================
# Tests for generate_point_map_spec (Point/Bubble Maps)
# ============================================================================


class TestGeneratePointMapSpec:
    """Tests for generate_point_map_spec() for point maps."""


class TestGenerateBubbleMapSpec:
    """Tests for generate_point_map_spec() for bubble maps."""


class TestPointMapWithBackground:
    """Tests for point/bubble maps with background geoshape."""


# ============================================================================
# Tests for Projection Handling
# ============================================================================


class TestProjectionHandling:
    """Tests for map projection handling."""


# ============================================================================
# Tests for Geo Source URL Resolution
# ============================================================================


class TestGeoSourceResolution:
    """Tests for geographic source URL resolution."""


# ============================================================================
# Tests for City Neighborhood Presets
# ============================================================================


class TestCityNeighborhoodPresets:
    """Tests for city-level geographic presets."""


# ============================================================================
# Tests for Color Scheme Handling
# ============================================================================


class TestMapColorSchemes:
    """Tests for map color scheme handling."""


# ============================================================================
# Tests for Map Tooltips
# ============================================================================


class TestMapTooltips:
    """Tests for map tooltip generation."""


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
            chart, data, "", width=80, height=20, formats=None, kpi_compact=_KPI_COMPACT
        )

        # Should render as table with data values
        assert "CA" in result or "state_code" in result


# ============================================================================
# Tests for Data Type Handling
# ============================================================================


class TestMapDataTypes:
    """Tests for map data type handling."""


# ============================================================================
# Tests for Map Type Aliases
# ============================================================================


class TestMapTypeAliases:
    """Tests for map type aliases."""


# ============================================================================
# Tests for Autosize Configuration
# ============================================================================


class TestMapAutosize:
    """Tests for map autosize configuration."""


# ============================================================================
# Integration-style Tests
# ============================================================================


class TestMapSpecIntegration:
    """Integration-style tests for complete map specifications."""
