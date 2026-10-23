"""Fast-lane regression tests for chart property propagation.

Derived from the chart property audit (ai_notes/charts/chart-property-audit-2026-03-27.md).
These tests assert that authored properties reach the final Vega-Lite spec through
both the chart-authored and style-authored control paths.

These run in the default CI lane (not marked slow) because they are fast spec-level
checks — no SVG rendering, no external dependencies.
"""

from __future__ import annotations

import pytest

from dbt_charts.core.compile.models.style.authored import (
    BarChartStylePatch,
    ChartStylePatch,
)

DATA = [{"month": "Jan", "revenue": 100}, {"month": "Feb", "revenue": 200}]


# ============================================================================
# Title properties — audit found style path strong, chart path weak
# ============================================================================


# ============================================================================
# Axis properties — audit found chart path strong, style path selective
# ============================================================================


# ============================================================================
# Mark properties — audit found chart path strong, style path weak
# ============================================================================


class TestBarApiNarrowingPins:
    """bar/line/area on ChartStylePatch narrowed from MarkStyle to *StylePatch.

    These pin the narrowing as intentional: MarkStyle fields like opacity/color/
    corner_radius no longer accepted on chart.style.bar — ValidationError is correct.
    """

    def test_bar_rejects_opacity_was_markstyle(self):
        """chart.style.bar.opacity is rejected — BarStylePatch has no opacity."""
        from pydantic import ValidationError

        with pytest.raises(ValidationError, match="opacity"):
            ChartStylePatch(bar={"opacity": 0.5})

    def test_bar_accepts_color_chart_level(self):
        """chart.style.bar.color.static is accepted — it's a chart-level paint override."""
        patch = BarChartStylePatch.model_validate({"color": {"static": "#336699"}})
        assert patch.color is not None
        assert patch.color.static == "#336699"

    def test_bar_rejects_corner_radius_was_markstyle(self):
        """chart.style.bar.corner_radius is rejected — use bar.border.radius instead."""
        from pydantic import ValidationError

        with pytest.raises(ValidationError, match="corner_radius"):
            ChartStylePatch(bar={"corner_radius": 4})
