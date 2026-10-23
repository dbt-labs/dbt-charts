"""Tests for the tilt-picker and theme tilt_increments defaults.

Covers:
  - AxisLabelStyle.tilt_increments — list[float] | None, min_length=1
  - _pick_tilt_for_widths(label, widths, usable_width) — fit-based picker
  - Theme default tilt_increments under axis_x.labels
"""

from __future__ import annotations

import pytest

from dbt_charts.core.compile.config import get_theme_style


@pytest.fixture(autouse=True)
def _reset():
    from dbt_charts.core.compile.config import reset_config

    reset_config()
    yield
    reset_config()


# =============================================================================
# Schema: tilt_increments field
# =============================================================================


class TestTiltIncrementsField:
    def test_accepts_list_of_floats(self):
        from dbt_charts.core.compile.models.style.theme import DimensionLabelStyle

        e = DimensionLabelStyle(tilt_increments=[0, -30, -45, -60, -90])
        assert e.tilt_increments == [0.0, -30.0, -45.0, -60.0, -90.0]

    def test_accepts_single_element_list(self):
        from dbt_charts.core.compile.models.style.theme import DimensionLabelStyle

        e = DimensionLabelStyle(tilt_increments=[-45])
        assert e.tilt_increments == [-45.0]

    def test_rejects_empty_list(self):
        """Empty list would IndexError in picker fall-through. min_length=1 prevents."""
        from pydantic import ValidationError

        from dbt_charts.core.compile.models.style.theme import DimensionLabelStyle

        with pytest.raises(ValidationError):
            DimensionLabelStyle(tilt_increments=[])

    def test_rejects_non_numeric(self):
        """Non-coercible entries fail. (Pydantic coerces numeric strings → floats
        consistent with other axis float fields like padding.)"""
        from pydantic import ValidationError

        from dbt_charts.core.compile.models.style.theme import DimensionLabelStyle

        with pytest.raises(ValidationError):
            DimensionLabelStyle(tilt_increments=[{"angle": -30}])


# =============================================================================
# Theme default: tilt_increments lives under axis_x.labels
# =============================================================================


class TestThemeTiltIncrementsDefault:
    def test_default_theme_axis_x_tilt_increments(self):

        style = get_theme_style()
        increments = style.charts.axis_x.labels.tilt_increments
        # The descending ladder: starts at 0 (flat), includes only negative angles.
        assert increments is not None
        assert len(increments) > 0
        assert 0 in increments
        assert all(v <= 0 for v in increments), "all angles must be flat or negative"
        # List must be in descending order (0 first, most negative last)
        assert increments == sorted(increments, reverse=True)

    def test_axis_y_has_no_tilt_increments(self):
        """Y-axis label is AxisLabelStyle — tilt_increments is structurally absent."""
        from dbt_charts.core.compile.models.style.theme import AxisLabelStyle

        assert "tilt_increments" not in AxisLabelStyle.model_fields


# =============================================================================
# Picker: _pick_tilt_angle(x_field, data, resolved_style, chart_width)
# =============================================================================
