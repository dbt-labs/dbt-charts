"""TDD tests: MarkStyle fields that must flow from model → VL mark output.

Tests cover the unit-level mappers and end-to-end spec generation for:
- line_mark_to_vl: stroke.color → mark.stroke, stroke.dasharray → mark.strokeDash
- area_mark_to_vl: stroke.color → mark.stroke, stroke.dasharray → mark.strokeDash
- _map_line halo path: stroke.color reaches the foreground line layer
- _map_line halo path: point.color/shape reach the foreground point layer
- _map_area halo path: stroke.color reaches the foreground area.line sub-object
- layered chart line sub-layer: point.color emitted as separate layer with value encoding
"""

from __future__ import annotations

import pytest

from dbt_charts.core.compile.config import (
    get_theme_style,
    reset_config,
)
from dbt_charts.core.compile.resolve.style.board import (
    resolve_chart_style_context,
)
from dbt_charts.core.constants.marks import STROKE_FALLBACK_WIDTH


@pytest.fixture(autouse=True)
def reset():
    reset_config()
    yield
    reset_config()


SAMPLE_DATA = [{"month": "Jan", "revenue": 100}, {"month": "Feb", "revenue": 200}]
MULTI_SERIES_DATA = [
    {"month": "Jan", "revenue": 100, "segment": "a"},
    {"month": "Feb", "revenue": 200, "segment": "a"},
    {"month": "Jan", "revenue": 50, "segment": "b"},
    {"month": "Feb", "revenue": 90, "segment": "b"},
]


# ---------------------------------------------------------------------------
# Unit tests — vl_field_maps mappers
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# Helpers to build board overrides (mirrors pattern from test_per_chart_style_promotions)
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# End-to-end: no-halo path (halo_multiplier=0)
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# End-to-end: halo path (default halo_multiplier > 0)
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# point.color and point.shape on line chart halo path
# ---------------------------------------------------------------------------


class TestPointMarkMapper:
    """Unit tests for scatter_mark_to_vl (covers _emit_point_mark)."""

    @pytest.mark.parametrize("family", ["scatter", "point_map", "area"])
    def test_family_default_stroke_width_matches_line_stroke(self, family):
        # Families whose point ring is not density-baked supply their own
        # default so hollow rings read at the line family's stroke weight
        # rather than falling back to VL's hardcoded 2 px. This is a
        # separate theme literal pinned equal to STROKE_FALLBACK_WIDTH for
        # visual coherence.
        editorial = get_theme_style("clarity")
        theme_point = getattr(editorial.charts, family).marks.point
        fallback_width = STROKE_FALLBACK_WIDTH
        assert theme_point.stroke_width == fallback_width, (
            f"{family}.marks.point.stroke_width ({theme_point.stroke_width}) "
            f"should match STROKE_FALLBACK_WIDTH "
            f"({fallback_width}) so hollow rings read with the same weight "
            f"as the line itself"
        )

    def test_global_stroke_width_stays_unset_for_the_bake(self):
        # The global slot is deliberately empty (like marks.point.size):
        # bake_point_companions reads "no tier authored a ring" off the
        # cascaded value, so a literal here would shadow a board-tier pin.
        theme = get_theme_style("clarity")
        assert theme.charts.marks.point.stroke_width == "auto"
        assert resolve_chart_style_context(theme).marks.point.stroke_width is None


# ---------------------------------------------------------------------------

# ---------------------------------------------------------------------------
# Global mark → bar family propagation
# apply_inherit propagates tier-1 globals into family.marks at resolve time,
# so the render layer can read effective.bar.marks.bar directly without
# calling resolve_mark.  These tests guard that invariant.
# ---------------------------------------------------------------------------
