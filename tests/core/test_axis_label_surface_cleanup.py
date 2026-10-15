"""Tests: axis label surface — min_gap, max_width, visible, overlap strategy list.

Covers:
  - AxisLabelStyle gains min_gap, max_width (renamed from limit), visible fields
  - overlap is {tilt,skip} bool struct; rejects unknown keys
  - vl_field_maps.py emitter: min_gap → labelSeparation; max_width → labelLimit;
    overlap VL dispatch via label_overlap parameter on axis_to_vl (render-local,
    not on the resolved model); visible → labels (direct)
  - Theme defaults: axis.labels.overlap={tilt,skip}, axis.labels.min_gap=16
  - presentation.py: no more hardcoded labelOverlap / labelLimit / auto-expand
  - axis_to_vl (vl_field_maps.py): no more hardcoded y-axis labelLimit
  - Flat label_overlap removed from BaseAxisStyle and ResolvedAxisElementStyle
  - axis.labels flat bool gone; label.visible nested bool present
"""

from __future__ import annotations

import pytest

from dbt_charts.core.compile.config import get_default_theme_name, get_theme_style


@pytest.fixture(autouse=True)
def _reset():
    from dbt_charts.core.compile.config import reset_config

    reset_config()
    yield
    reset_config()


# =============================================================================
# AxisLabelStyle — new fields
# =============================================================================


class TestCompiledAxisElementStyleNewFields:
    def test_separation_accepts_float(self):
        from dbt_charts.core.compile.models.style.theme import AxisLabelStyle

        e = AxisLabelStyle(min_gap=12.0)
        assert e.min_gap == 12.0

    def test_max_width_field_replaces_limit(self):
        """limit is gone; max_width is the renamed field."""
        from dbt_charts.core.compile.models.style.theme import AxisLabelStyle

        e = AxisLabelStyle(max_width=200.0)
        assert e.max_width == 200.0

    def test_visible_field_accepts_bool(self):
        from dbt_charts.core.compile.models.style.theme import AxisLabelStyle

        assert AxisLabelStyle(visible=False).visible is False
        assert AxisLabelStyle(visible=True).visible is True


class TestOverlapStructValidation:
    def test_overlap_struct_all_enabled(self):
        """The new struct shape: two bools enable/disable each strategy."""
        from dbt_charts.core.compile.models.style.theme import (
            AxisLabelOverlapConfig,
            AxisLabelStyle,
        )

        cfg = AxisLabelOverlapConfig(tilt=True, skip=True)
        e = AxisLabelStyle(overlap=cfg)
        assert e.overlap is cfg

    def test_overlap_struct_partial_authoring(self):
        """Partial struct: only tilt=False; skip cascades from parent."""
        from dbt_charts.core.compile.models.style.theme import (
            AxisLabelOverlapConfig,
            AxisLabelStyle,
        )

        e = AxisLabelStyle(overlap=AxisLabelOverlapConfig(tilt=False))
        assert e.overlap is not None
        assert e.overlap.tilt is False
        assert e.overlap.skip is None  # not authored → cascade

    def test_overlap_none_means_inherit(self):
        """overlap=None means not authored; inherits from cascade."""
        from dbt_charts.core.compile.models.style.theme import AxisLabelStyle

        e = AxisLabelStyle(overlap=None)
        assert e.overlap is None

    def test_overlap_list_rejected(self):
        """Old list format is rejected — overlap is now a struct."""
        from pydantic import ValidationError

        from dbt_charts.core.compile.models.style.theme import AxisLabelStyle

        with pytest.raises(ValidationError):
            AxisLabelStyle(overlap=["tilt", "skip"])  # type: ignore[arg-type]

    def test_overlap_string_allow_rejected(self):
        """Old 'allow' string is rejected — use { tilt: false, skip: false }."""
        from pydantic import ValidationError

        from dbt_charts.core.compile.models.style.theme import AxisLabelStyle

        with pytest.raises(ValidationError):
            AxisLabelStyle(overlap="allow")  # type: ignore[arg-type]

    def test_overlap_string_smart_rejected(self):
        """Old 'smart' string is rejected — omit overlap to inherit from cascade."""
        from pydantic import ValidationError

        from dbt_charts.core.compile.models.style.theme import AxisLabelStyle

        with pytest.raises(ValidationError):
            AxisLabelStyle(overlap="smart")  # type: ignore[arg-type]

    def test_overlap_unknown_key_rejected(self):
        """extra=forbid on the struct: unknown keys raise ValidationError."""
        from pydantic import ValidationError

        from dbt_charts.core.compile.models.style.theme import AxisLabelOverlapConfig

        with pytest.raises(ValidationError, match="extra_forbidden"):
            AxisLabelOverlapConfig(tilt=True, greedy=True)  # type: ignore[call-arg]


# =============================================================================
# VL field mapper — axis_to_vl
# =============================================================================


class TestAxisToVlSeparationMapping:
    def test_separation_maps_to_labelSeparation(self):
        from dbt_charts.core.render.chart.vl_field_maps import axis_to_vl

        result = axis_to_vl(_make_axis(label_override={"min_gap": 12.0}))
        assert result.get("labelSeparation") == 12.0

    def test_separation_none_not_emitted(self):
        from dbt_charts.core.render.chart.vl_field_maps import axis_to_vl

        result = axis_to_vl(_make_axis())
        assert "labelSeparation" not in result


class TestAxisToVlMaxWidthMapping:
    def test_max_width_maps_to_labelLimit(self):
        from dbt_charts.core.render.chart.vl_field_maps import axis_to_vl

        result = axis_to_vl(_make_axis(label_override={"max_width": 200.0}))
        assert result.get("labelLimit") == 200.0

    def test_max_width_none_not_emitted(self):
        from dbt_charts.core.render.chart.vl_field_maps import axis_to_vl

        result = axis_to_vl(_make_axis())
        assert "labelLimit" not in result


class TestAxisToVlOverlapDispatch:
    """axis_to_vl takes label_overlap as a render-local parameter (not read from model)."""

    def test_allow_directive_emits_false(self):
        """label_overlap='allow' → labelOverlap: false (no reduction; labels may overlap)."""
        from dbt_charts.core.render.chart.vl_field_maps import axis_to_vl

        result = axis_to_vl(_make_axis(), label_overlap="allow")
        assert result.get("labelOverlap") is False

    def test_parity_directive_passes_through(self):
        """label_overlap='parity' → labelOverlap: 'parity' passed through to VL."""
        from dbt_charts.core.render.chart.vl_field_maps import axis_to_vl

        result = axis_to_vl(_make_axis(), label_overlap="parity")
        assert result.get("labelOverlap") == "parity"

    def test_none_directive_omits_labelOverlap(self):
        """label_overlap=None (default) → labelOverlap omitted; VL per-scale adaptive fires."""
        from dbt_charts.core.render.chart.vl_field_maps import axis_to_vl

        result = axis_to_vl(_make_axis())
        assert "labelOverlap" not in result


class TestAxisToVlVisibleMapping:
    def test_visible_false_emits_labels_false(self):
        from dbt_charts.core.render.chart.vl_field_maps import axis_to_vl

        result = axis_to_vl(_make_axis(label_override={"visible": False}))
        assert result.get("labels") is False

    def test_visible_true_emits_labels_true(self):
        from dbt_charts.core.render.chart.vl_field_maps import axis_to_vl

        result = axis_to_vl(_make_axis(label_override={"visible": True}))
        assert result.get("labels") is True

    def test_visible_none_no_labels_key(self):
        from dbt_charts.core.render.chart.vl_field_maps import axis_to_vl

        result = axis_to_vl(_make_axis())
        assert "labels" not in result


# =============================================================================
# VegaLiteAxisConfig — labelSeparation field
# =============================================================================


class TestVegaLiteAxisConfigSeparation:
    def test_labelSeparation_field_present(self):
        from dbt_charts.core.compile.models.vega_lite.config import AxisConfig

        cfg = AxisConfig(labelSeparation=16.0)
        assert cfg.model_dump(exclude_none=True).get("labelSeparation") == 16.0

    def test_labelSeparation_none_excluded(self):
        from dbt_charts.core.compile.models.vega_lite.config import AxisConfig

        cfg = AxisConfig()
        assert "labelSeparation" not in cfg.model_dump(exclude_none=True)


# =============================================================================
# Theme defaults
# =============================================================================


class TestThemeDefaultsOverlapAndSeparation:
    def test_axis_label_default_overlap_and_separation(self):
        """Theme default: axis.labels.overlap both strategies enabled, min_gap=16.

        The default enables both strategies. Categorical axes only tilt;
        temporal-bucketed axes step cadence before tilting.
        min_gap=16px is the label spacing floor.
        """
        from dbt_charts.core.compile.config import (
            get_theme_style,
        )
        from dbt_charts.core.compile.models.style.theme import AxisLabelOverlapConfig

        label = get_theme_style().charts.axis.labels
        assert isinstance(label.overlap, AxisLabelOverlapConfig)
        assert label.overlap.tilt is True
        assert label.overlap.skip is True
        assert label.min_gap == 16

    def test_axis_x_label_separation_field_accessible(self):
        """axis.labels.min_gap is set at the compiled (pre-fill) axis level."""
        from dbt_charts.core.compile.config import (
            get_theme_style,
        )

        style = get_theme_style()
        # The shared axis.labels carries min_gap; axis_x.labels has no override.
        assert style.charts.axis.labels.min_gap is not None
        assert style.charts.axis_x.labels.min_gap is None

    def test_axis_x_label_separation_cascades_at_resolve_time(self):
        """resolved_axis_style() propagates axis.labels.min_gap to axis_x."""
        from dbt_charts.core.compile.config import (
            get_theme_style,
        )
        from dbt_charts.core.compile.resolve.style.axis_cascade import (
            resolved_axis_style,
        )
        from dbt_charts.core.compile.resolve.style.board import (
            resolve_chart_style_context,
        )

        ctx = resolve_chart_style_context(get_theme_style())
        emitted = resolved_axis_style(
            ctx, "axis_x", "ordinal", chart_type="", labeled=False
        )
        assert emitted.labels.min_gap is not None

    def test_axis_y_label_overlap_is_nested(self):
        """axis_y.labels.overlap is None (no override); the nested form is the only path."""
        from dbt_charts.core.compile.config import (
            get_theme_style,
        )

        style = get_theme_style()
        assert style.charts.axis_y.labels.overlap is None


# =============================================================================
# axis_to_vl (vl_field_maps.py) — y-axis labelLimit hardcode gone
# =============================================================================


class TestProfileYAxisNoLabelLimitHardcode:
    def test_default_theme_axis_y_label_max_width_is_none(self):
        """Default theme does not set axis_y.labels.max_width (VL default 180px applies)."""
        from dbt_charts.core.compile.config import (
            get_theme_style,
        )

        style = get_theme_style()
        assert style.charts.axis_y.labels.max_width is None


# =============================================================================
# Regression: theme override of axis_x.labels.overlap is honored
# =============================================================================


class TestThemeOverrideHonored:
    def test_axis_x_label_overlap_struct_survives_cascade(self):
        """axis_x.labels.overlap struct override survives resolve_style.

        axis_to_vl takes the VL directive as a parameter (render-local); it does
        not read a directive from the resolved model. This test verifies the
        authored struct reaches the resolved style intact.
        """
        from dbt_charts.core.compile.models.style.resolved import (
            ResolvedAxisLabelOverlapConfig,
        )
        from dbt_charts.core.compile.models.style.theme import AxisLabelOverlapConfig
        from dbt_charts.core.compile.resolve.style.axis_cascade import (
            resolved_axis_style,
        )
        from dbt_charts.core.compile.resolve.style.board import (
            resolve_chart_style_context,
        )

        skip_only = AxisLabelOverlapConfig(tilt=False, skip=True)
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
                                    update={"overlap": skip_only}
                                )
                            }
                        )
                    },
                )
            },
        )
        ctx = resolve_chart_style_context(patched)
        # The authored struct must survive the cascade to the emitted style.
        emitted = resolved_axis_style(
            ctx, "axis_x", "ordinal", chart_type="", labeled=False
        )
        assert isinstance(emitted.labels.overlap, ResolvedAxisLabelOverlapConfig)
        assert emitted.labels.overlap.tilt is False
        assert emitted.labels.overlap.skip is True

    def test_axis_x_label_max_width_survives_cascade_to_vl(self):
        """axis_x.labels.max_width override survives resolve_style → axis_to_vl."""
        from dbt_charts.core.compile.resolve.style.board import (
            resolve_chart_style_context,
        )
        from dbt_charts.core.render.chart.vl_field_maps import axis_to_vl

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
                                    update={"max_width": 240.0}
                                )
                            }
                        )
                    },
                )
            },
        )
        ctx = resolve_chart_style_context(patched)
        vl = axis_to_vl(ctx.axis_x)
        assert vl.get("labelLimit") == 240.0

    def test_axis_x_label_overlap_parity_directive_emits_correctly(self):
        """axis_to_vl emits labelOverlap='parity' when passed as parameter."""
        from dbt_charts.core.render.chart.vl_field_maps import axis_to_vl

        result = axis_to_vl(_make_axis(), label_overlap="parity")
        assert result.get("labelOverlap") == "parity"


# =============================================================================
# Helpers
# =============================================================================


def _make_axis(label_override: dict | None = None):
    """Build a minimal AxisXStyle for axis_to_vl testing (non-overlap fields)."""
    from dbt_charts.core.compile.models.style.theme import (
        AxisLineStyle,
        AxisTitleStyle,
        AxisXStyle,
        BaseAxisGridStyle,
        DimensionTicksStyle,
    )

    return AxisXStyle(
        labels=label_override or {},
        title=AxisTitleStyle(),
        grid=BaseAxisGridStyle(),
        line=AxisLineStyle(),
        ticks=DimensionTicksStyle(),
        fill="null",
        fiscal_year_start_month=1,
    )
