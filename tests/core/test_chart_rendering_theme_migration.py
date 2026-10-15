"""TDD tests: authorable chart_rendering fields migrate to theme YAML.

Verifies that tooltip.format and axis_y.categorical_orient (plus its
label.overlap / label.separation defaults) are served from the resolved
style tree.
"""

from __future__ import annotations

import pytest

from dbt_charts.core.compile.config import (
    get_default_theme_name,
    get_theme_style,
    reset_config,
)
from dbt_charts.core.compile.resolve.style.board import resolve_chart_style_context


@pytest.fixture(autouse=True)
def _reset():
    reset_config()
    yield
    reset_config()


# ---------------------------------------------------------------------------
# Tooltip format
# ---------------------------------------------------------------------------


class TestTooltipFormatFromTheme:
    def test_default_theme_provides_tooltip_format(self):
        """Default theme must expose a non-empty tooltip.format string."""
        style = get_theme_style("clarity")
        assert style.charts.tooltip is not None
        assert style.charts.tooltip.format
        assert isinstance(style.charts.tooltip.format, str)

    def test_tooltip_format_flows_into_resolved_style(self):
        """Resolved style carries the tooltip format from the compiled theme."""
        rs = resolve_chart_style_context(get_theme_style())
        assert rs.tooltip.format
        assert isinstance(rs.tooltip.format, str)

    def test_custom_tooltip_format_overrides_in_cascade(self):
        """A style with a custom tooltip.format produces the right resolved value."""
        base_tooltip = get_theme_style().charts.tooltip
        patched_tooltip = base_tooltip.model_copy(update={"format": "$,.0f"})
        patched = get_theme_style().model_copy(
            deep=True,
            update={
                "charts": get_theme_style().charts.model_copy(
                    deep=True,
                    update={"tooltip": patched_tooltip},
                )
            },
        )
        rs = resolve_chart_style_context(patched)
        assert rs.tooltip.format == "$,.0f"


# ---------------------------------------------------------------------------
# axis_y.categorical_orient and label.overlap / label.separation
# ---------------------------------------------------------------------------


class TestAxisYDefaultsFromTheme:
    def test_axis_label_overlap_from_theme(self):
        """axis.labels.overlap comes from theme as an AxisLabelOverlapConfig struct."""
        from dbt_charts.core.compile.models.style.theme import AxisLabelOverlapConfig

        style = get_theme_style("clarity")
        overlap = style.charts.axis.labels.overlap
        # overlap is AxisLabelOverlapConfig | None; None means "inherit from cascade"
        assert overlap is None or isinstance(overlap, AxisLabelOverlapConfig)

    def test_axis_label_min_gap_from_theme(self):
        """axis.labels.min_gap comes from theme."""
        style = get_theme_style("clarity")
        assert style.charts.axis.labels.min_gap is not None

    def test_custom_axis_y_position_overrides_in_cascade(self):
        """A style with a custom axis_y.position produces the right resolved value."""
        patched = get_theme_style().model_copy(
            deep=True,
            update={
                "charts": get_theme_style().charts.model_copy(
                    deep=True,
                    update={
                        "axis_y": get_theme_style(
                            get_default_theme_name()
                        ).charts.axis_y.model_copy(update={"position": "right"})
                    },
                )
            },
        )
        rs = resolve_chart_style_context(patched)
        assert rs.axis_y.position == "right"
