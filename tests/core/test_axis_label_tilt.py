"""Tests for the tilt-picker and theme tilt_increments defaults.

Covers:
  - AxisLabelStyle.tilt_increments — list[float] | None, min_length=1
  - _pick_tilt_for_widths(label, widths, usable_width) — fit-based picker
  - Theme default tilt_increments under axis_x.labels
"""

from __future__ import annotations

from typing import Any

import pytest

from dbt_charts.core.compile.config import get_default_theme_name, get_theme_style
from dbt_charts.core.compile.resolve.style.axis_cascade import resolved_axis_style
from dbt_charts.core.font_measure import get_font_measurer
from dbt_charts.core.render.chart.emitters._label_overlap import (
    TiltChoice,
    _pick_tilt_for_widths,
)


def _pick_tilt_angle(
    x_field: str,
    data: list[dict[str, Any]],
    charts: Any,
    chart_width: float,
    label_usable_ratio: float = 1.0,
) -> TiltChoice:
    """Thin adapter: measure ``data``'s own widths and run the tilt picker
    against the fully-merged axis_x.labels.

    charts.axis_x is authored-only (SkipInheritSlots) — resolved_axis_style()
    merges it onto charts.axis to get the complete, theme-cascaded axis state.
    """
    axis_x = resolved_axis_style(
        charts, "axis_x", "ordinal", chart_type="", labeled=False
    )
    label = axis_x.labels
    values = list(dict.fromkeys(str(row.get(x_field, "")) for row in data[:100]))
    values = [value for value in values if value]
    measurer = get_font_measurer(label.font.family)
    widths = [measurer.measure(value, label.font.size) for value in values]
    return _pick_tilt_for_widths(label, widths, chart_width * label_usable_ratio)


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
        from dbt_charts.core.compile.config import (
            get_theme_style,
        )

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


def _resolved_charts():
    """Resolved default ChartStyleContext; the cascade fills axis_x.labels.tilt_increments."""
    from dbt_charts.core.compile.resolve.style.board import resolve_chart_style_context

    return resolve_chart_style_context(get_theme_style())


class TestPickTiltAngle:
    def test_short_labels_fit_at_zero(self):
        """Tiny labels at wide chart should fit at angle=0."""

        charts = _resolved_charts()
        data = [{"x": v} for v in ["A", "B", "C", "D"]]
        tilt = _pick_tilt_angle("x", data, charts, chart_width=1200)
        assert tilt.fits is True
        assert tilt.angle == 0

    def test_long_labels_force_steeper_angle(self):
        """Same chart width with longer labels should pick a non-zero angle."""

        charts = _resolved_charts()
        data = [
            {"x": v}
            for v in [
                "Northern California region",
                "Pacific Northwest region",
                "Greater Bay Area metropolitan",
                "Southern Oregon coast",
                "Eastern Washington plateau",
                "Central California valley",
                "Sierra Nevada foothills",
                "Mojave Desert southeast",
            ]
        ]
        tilt = _pick_tilt_angle("x", data, charts, chart_width=400)
        assert tilt.angle != 0  # at this density, angle 0 cannot fit

    def test_extreme_density_falls_through_to_last(self):
        """Too many long labels — picker exhausts the ladder and returns (last, False)."""

        charts = _resolved_charts()
        # 60 long labels at 200px chart — even at -90 the band width is tiny.
        data = [{"x": f"Very long category label number {i}"} for i in range(60)]
        tilt = _pick_tilt_angle("x", data, charts, chart_width=200)
        assert tilt.fits is False
        # Last entry of the default ladder.
        assert tilt.angle == charts.axis_x.labels.tilt_increments[-1]

    def test_honors_theme_override_ladder(self):
        """Picker uses tilt_increments from the resolved style, not a hardcode."""
        from dbt_charts.core.compile.config import get_config
        from dbt_charts.core.compile.resolve.style.board import (
            resolve_chart_style_context,
        )

        get_config()  # ensure settings initialized
        patched = get_theme_style().model_copy(
            deep=True,
            update={
                "charts": get_theme_style().charts.model_copy(
                    deep=True,
                    update={
                        "axis_x": get_theme_style(
                            get_default_theme_name()
                        ).charts.axis_x.model_copy(
                            update={
                                "labels": get_theme_style(
                                    get_default_theme_name()
                                ).charts.axis_x.labels.model_copy(
                                    update={"tilt_increments": [-90]}
                                )
                            }
                        )
                    },
                )
            },
        )
        charts = resolve_chart_style_context(patched)
        data = [{"x": "A"}, {"x": "B"}]
        tilt = _pick_tilt_angle("x", data, charts, chart_width=1200)
        # Single-entry ladder must pick that entry.
        assert tilt.angle == -90

    def test_no_data_returns_zero_fits_true(self):
        """Empty data — nothing to lay out; angle 0 is the safe default."""

        charts = _resolved_charts()
        tilt = _pick_tilt_angle("x", [], charts, chart_width=1200)
        assert tilt.angle == 0
        assert tilt.fits is True


class TestLadderReachability:
    """Which rungs the picker can actually return, swept over every band.

    The rotated footprint ``w*cos(t) + line_height*sin(t)`` only narrows on
    labels wider than ``line_height * cot(t/2)``, so a rung is reachable for
    long labels and unreachable for short ones — see ``_pick_tilt_for_widths``.
    """

    @staticmethod
    def _angles_over_band_sweep(text: str) -> set[float]:
        label = resolved_axis_style(
            _resolved_charts(), "axis_x", "ordinal", chart_type="", labeled=False
        ).labels
        width = get_font_measurer(label.font.family).measure(text, label.font.size)
        # Two labels share the usable width, so band == usable_width / 2.
        return {
            _pick_tilt_for_widths(label, [width, width], band * 2).angle
            for band in [tenths / 10 for tenths in range(10, 3000)]
        }

    def test_long_label_reaches_every_rung(self):
        """No rung is dead configuration: a label wide enough for tilting to
        pay off is returned at every angle on the ladder."""
        label_increments = _resolved_charts().axis_x.labels.tilt_increments
        assert self._angles_over_band_sweep("Engineering") == set(label_increments)

    def test_short_label_steps_flat_to_vertical(self):
        """Tilting a label narrower than the line height widens its footprint,
        so the intermediate rungs are correctly skipped — flat, then vertical."""
        label_increments = _resolved_charts().axis_x.labels.tilt_increments
        assert self._angles_over_band_sweep("Apr") == {
            label_increments[0],
            label_increments[-1],
        }
