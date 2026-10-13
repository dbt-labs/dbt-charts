"""TDD tests for ChartStyle/ChartStylePatch dissolution.

Proves that after the refactor:
1. ChartStylePatch.axis_x/y/quantitative use per-variant axis patches
2. ChartStylePatch.legend uses LegendStylePatch (no LegendStyle)
3. BaseAxisStyle subtypes carry the VL-passthrough fields needed
"""

from __future__ import annotations


class TestAxisStyleHasVLFields:
    """BaseAxisStyle subtypes carry all VL config fields."""

    def test_axis_grid_has_color(self):
        from dbt_charts.core.compile.models.style.theme import (
            AxisGridThresholdStyle,
            BaseAxisGridStyle,
        )

        # threshold lives on the shared BaseAxisGridStyle — every axis
        # variant (x/y/quantitative/band) carries it.
        g = BaseAxisGridStyle(
            visible=True,
            opacity=1.0,
            width=1.0,
            threshold=AxisGridThresholdStyle(color="#000", width=2.0, visible=True),
        )
        assert g.color is None  # optional, None = no override
        # Verify the field round-trips when set
        g_with_color = BaseAxisGridStyle(
            visible=True, opacity=1.0, width=1.0, color="#abc"
        )
        assert g_with_color.color == "#abc"

    def test_axis_domain_has_color(self):
        from dbt_charts.core.compile.models.style.theme import AxisLineStyle

        d = AxisLineStyle(visible=False, width=1.0)
        assert d.color is None

    def test_axis_ticks_has_color_and_length(self):
        from dbt_charts.core.compile.models.style.theme import AxisTicksStyle

        t = AxisTicksStyle(visible=False)
        assert t.color is None
        assert t.length is None

    def test_axis_element_has_max_width_and_angle(self):
        from dbt_charts.core.compile.models.style.theme import AxisLabelStyle

        e = AxisLabelStyle(padding=6.0)
        assert e.max_width is None
        assert e.angle is None


class TestLegendStyleHasVisible:
    def test_resolved_legend_visibility_is_boolean(self) -> None:
        """Resolved whole-legend visibility is a concrete boolean."""
        from dbt_charts.core.compile.config import get_theme_style, reset_config
        from dbt_charts.core.compile.resolve.style.board import (
            resolve_chart_style_context,
        )

        reset_config()
        legend = resolve_chart_style_context(get_theme_style("stark")).legend
        assert isinstance(legend.visible, bool)
        assert legend.model_copy(update={"visible": False}).visible is False


class TestChartStylePatchUsesCompiledTypes:
    """ChartStylePatch.axis_x/y/quantitative use per-variant axis patches."""

    def test_chartstylepatch_axis_accepts_nested_form(self):
        """BarChartStylePatch.axis_y should accept nested axis style form."""
        from dbt_charts.core.compile.models.style.authored import BarChartStylePatch

        patch = BarChartStylePatch.model_validate(
            {
                "axis_y": {
                    "position": "left",
                    "grid": {"visible": False},
                    "labels": {"font": {"color": "#bda964"}},
                }
            }
        )
        assert patch.axis_y is not None
        assert patch.axis_y.position == "left"
        assert patch.axis_y.grid.visible is False

    def test_chartstylepatch_legend_accepts_visible(self):
        """BarChartStylePatch.legend should accept visible: bool."""
        from dbt_charts.core.compile.models.style.authored import BarChartStylePatch

        patch = BarChartStylePatch.model_validate({"legend": {"visible": False}})
        assert patch.legend is not None
        assert patch.legend.visible is False

    def test_axis_grid_color_round_trips_through_patch(self):
        """grid.color propagates through BarChartStylePatch authored-surface."""
        from dbt_charts.core.compile.models.style.authored import BarChartStylePatch

        patch = BarChartStylePatch.model_validate(
            {"axis_x": {"grid": {"color": "#ccc"}}}
        )
        assert patch.axis_x is not None
        assert patch.axis_x.grid.color == "#ccc"
