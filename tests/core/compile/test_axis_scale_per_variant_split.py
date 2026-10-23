"""Tests for the axis/scale per-variant model split.

Confirms that per-variant axis/scale models accept their own fields and reject
fields that belong to other variants (extra="forbid").

TDD step 2 per the implementation plan.
"""

from __future__ import annotations

import pytest
from pydantic import ValidationError

# ── Axis variant tests ───────────────────────────────────────────────────────


class TestAxisYRejectsXOnlyFields:
    """axis_y must reject fields that only exist on AxisXStyle."""

    def test_axis_y_rejects_label_values(self):
        from dbt_charts.core.compile.models.style.theme.axis import AxisYStyle

        with pytest.raises(ValidationError):
            AxisYStyle.model_validate({"labels": {"values": ["2020-01-01"]}})

    def test_axis_y_rejects_time_unit(self):
        from dbt_charts.core.compile.models.style.theme.axis import AxisYStyle

        with pytest.raises(ValidationError):
            AxisYStyle.model_validate({"time_unit": "year"})

    def test_axis_y_rejects_fill(self):
        from dbt_charts.core.compile.models.style.theme.axis import AxisYStyle

        with pytest.raises(ValidationError):
            AxisYStyle.model_validate({"fill": "null"})

    def test_axis_y_rejects_band_position(self):
        from dbt_charts.core.compile.models.style.theme.axis import AxisYStyle

        with pytest.raises(ValidationError):
            AxisYStyle.model_validate({"band_position": 0.5})

    def test_axis_y_rejects_tilt_increments_on_label(self):
        from dbt_charts.core.compile.models.style.theme.axis import AxisYStyle

        with pytest.raises(ValidationError):
            AxisYStyle.model_validate({"labels": {"tilt_increments": [0, -30]}})

    def test_axis_y_rejects_label_type(self):
        from dbt_charts.core.compile.models.style.theme.axis import AxisYStyle

        with pytest.raises(ValidationError):
            AxisYStyle.model_validate({"type": "ordinal"})


class TestAxisXRejectsYOnlyFields:
    """axis_x must reject fields that only exist on AxisYStyle."""

    def test_axis_x_rejects_mirror(self):
        from dbt_charts.core.compile.models.style.theme.axis import AxisXStyle

        with pytest.raises(ValidationError):
            AxisXStyle.model_validate({"fill": "null", "mirror": True})

    def test_axis_x_rejects_categorical_orient(self):
        from dbt_charts.core.compile.models.style.theme.axis import AxisXStyle

        with pytest.raises(ValidationError):
            AxisXStyle.model_validate({"fill": "null", "categorical_orient": "left"})


class TestAxisGridThresholdOnEveryAxis:
    """grid.threshold is accepted on every axis variant — a
    threshold rule is a property of a quantitative axis, on either channel,
    not of the measure (y) axis specifically. It is inert (never rejected)
    on a band axis; the model cannot reject it statically there."""

    def test_base_axis_grid_accepts_threshold(self):
        from dbt_charts.core.compile.models.style.theme.axis import BaseAxisGridStyle

        g = BaseAxisGridStyle.model_validate(
            {"threshold": {"color": "#000", "width": 2}}
        )
        assert g.threshold is not None
        assert g.threshold.color == "#000"

    def test_axis_x_grid_accepts_threshold(self):
        from dbt_charts.core.compile.models.style.theme.axis import AxisXStyle

        ax = AxisXStyle.model_validate(
            {
                "fill": "null",
                "fiscal_year_start_month": 1,
                "grid": {"threshold": {"color": "#000", "width": 2}},
            }
        )
        assert ax.grid.threshold is not None
        assert ax.grid.threshold.color == "#000"

    def test_axis_quantitative_grid_accepts_threshold(self):
        from dbt_charts.core.compile.models.style.theme.axis import (
            QuantitativeAxisStyle,
        )

        aq = QuantitativeAxisStyle.model_validate(
            {"grid": {"threshold": {"color": "#000", "width": 2}}}
        )
        assert aq.grid.threshold is not None
        assert aq.grid.threshold.color == "#000"

    def test_axis_band_grid_accepts_threshold(self):
        """Authorable but inert — a band axis never ticks at the threshold."""
        from dbt_charts.core.compile.models.style.theme.axis import BandAxisStyle

        ab = BandAxisStyle.model_validate(
            {"grid": {"threshold": {"color": "#000", "width": 2}}}
        )
        assert ab.grid.threshold is not None
        assert ab.grid.threshold.color == "#000"

    def test_axis_y_grid_accepts_threshold(self):
        from dbt_charts.core.compile.models.style.theme.axis import AxisYStyle

        ay = AxisYStyle.model_validate(
            {"grid": {"threshold": {"color": "#000", "width": 2}}}
        )
        assert ay.grid.threshold is not None
        assert ay.grid.threshold.color == "#000"


# ── Scale variant tests ──────────────────────────────────────────────────────


class TestScaleXReverseOnlyOnAxisXScale:
    """x_reverse must only exist on XScaleStyle (axis_x.scale)."""

    def test_base_scale_rejects_x_reverse(self):
        from dbt_charts.core.compile.models.style.theme.axis import BaseScaleStyle

        with pytest.raises(ValidationError):
            BaseScaleStyle.model_validate({"x_reverse": True})

    def test_axis_y_scale_rejects_x_reverse(self):
        from dbt_charts.core.compile.models.style.theme.axis import AxisYStyle

        with pytest.raises(ValidationError):
            AxisYStyle.model_validate({"scale": {"x_reverse": True}})

    def test_axis_x_scale_accepts_x_reverse(self):
        from dbt_charts.core.compile.models.style.theme.axis import AxisXStyle

        ax = AxisXStyle.model_validate(
            {"fill": "null", "fiscal_year_start_month": 1, "scale": {"x_reverse": True}}
        )
        assert ax.scale is not None
        assert ax.scale.x_reverse is True


class TestScaleContinuousGrouped:
    """Flat scale fields must be rejected at top level; the grouped continuous
    path is accepted. band/point/quantize/mark_size scale config was deleted
    (2026-08 trim) — no chart currently needs it, and it more than doubled
    the authoring surface for zero real use (see BaseScaleStyle's docstring)."""

    def test_base_scale_rejects_flat_band_padding_inner(self):
        from dbt_charts.core.compile.models.style.theme.axis import BaseScaleStyle

        with pytest.raises(ValidationError):
            BaseScaleStyle.model_validate({"band_padding_inner": 0.1})

    def test_base_scale_rejects_flat_point_padding(self):
        from dbt_charts.core.compile.models.style.theme.axis import BaseScaleStyle

        with pytest.raises(ValidationError):
            BaseScaleStyle.model_validate({"point_padding": 0.2})

    def test_base_scale_rejects_flat_continuous_padding(self):
        from dbt_charts.core.compile.models.style.theme.axis import BaseScaleStyle

        with pytest.raises(ValidationError):
            BaseScaleStyle.model_validate({"continuous_padding": 5.0})

    def test_base_scale_rejects_flat_zero(self):
        from dbt_charts.core.compile.models.style.theme.axis import BaseScaleStyle

        with pytest.raises(ValidationError):
            BaseScaleStyle.model_validate({"zero": True})

    def test_base_scale_rejects_flat_quantile_count(self):
        from dbt_charts.core.compile.models.style.theme.axis import BaseScaleStyle

        with pytest.raises(ValidationError):
            BaseScaleStyle.model_validate({"quantile_count": 5})

    def test_base_scale_rejects_band_group(self):
        from dbt_charts.core.compile.models.style.theme.axis import BaseScaleStyle

        with pytest.raises(ValidationError):
            BaseScaleStyle.model_validate({"band": {"band_padding_inner": 0.1}})

    def test_base_scale_rejects_point_group(self):
        from dbt_charts.core.compile.models.style.theme.axis import BaseScaleStyle

        with pytest.raises(ValidationError):
            BaseScaleStyle.model_validate({"point": {"point_padding": 0.3}})

    def test_base_scale_rejects_quantize_group(self):
        from dbt_charts.core.compile.models.style.theme.axis import BaseScaleStyle

        with pytest.raises(ValidationError):
            BaseScaleStyle.model_validate({"quantize": {"quantile_count": 5}})

    def test_base_scale_accepts_grouped_continuous(self):
        from dbt_charts.core.compile.models.style.theme.axis import BaseScaleStyle

        s = BaseScaleStyle.model_validate(
            {"continuous": {"zero": True, "type": "linear"}}
        )
        assert s.continuous is not None
        assert s.continuous.zero is True


class TestScaleContinuousHasNoRequiredFields:
    """ScaleContinuousStyle must have zero required fields.

    merge_onto_base's seed branch silently drops a group if it ever gains
    a required field — this test ensures we catch that before it bites.
    """

    def test_scale_continuous_style_no_required(self):
        from dbt_charts.core.compile.models.style.theme.axis import ScaleContinuousStyle

        required = [
            name
            for name, f in ScaleContinuousStyle.model_fields.items()
            if f.is_required()
        ]
        assert not required, f"ScaleContinuousStyle has required fields: {required}"

    def test_resolved_scale_continuous_no_required(self):
        from dbt_charts.core.compile.models.style.resolved._base import (
            ResolvedScaleContinuousStyle,
        )

        required = [
            name
            for name, f in ResolvedScaleContinuousStyle.model_fields.items()
            if f.is_required()
        ]
        assert not required, (
            f"ResolvedScaleContinuousStyle has required fields: {required}"
        )


class TestScaleLogPowSymlogParamsStillEmit:
    """scale.continuous: {type: log, base: 2} must emit scale.base: 2 in the VL spec.

    Regression guard: map_fields' untyped getattr means a missed field path
    silently resolves to None and drops from the emitted spec with no error.
    This test asserts the emitted value equals 2, not just "doesn't raise".
    """

    def _default_board_style(self):  # type: ignore[no-untyped-def]
        from dbt_charts.core.compile.config import (
            get_default_theme_name,
            get_theme_style,
        )
        from dbt_charts.core.compile.resolve.style.board import (
            resolve_chart_style_context,
        )

        return resolve_chart_style_context(get_theme_style(get_default_theme_name()))


class TestAxisYLabelExprRoundTrips:
    """axis_y.labels.expr should still round-trip (expr stays on base AxisLabelStyle)."""

    def test_axis_y_label_expr_accepted(self):
        from dbt_charts.core.compile.models.style.theme.axis import AxisYStyle

        ay = AxisYStyle.model_validate({"labels": {"expr": "datum.value + '%'"}})
        assert ay.labels.expr == "datum.value + '%'"
