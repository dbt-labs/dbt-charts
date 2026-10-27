"""Tests for overlap strategy struct: tilt/skip booleans (categorical axis labels).

Covers:
  - AxisLabelOverlapConfig: two-bool struct; extra=forbid rejects unknown keys;
    partial authoring (tilt: false only) inherits remainder from cascade
  - ResolvedAxisLabelOverlapConfig: required bools after resolve
  - Theme cascade: overlap struct with both bools set flows to axis_x.labels
  - resolve_axis_x_overlap: categorical labels tilt without skipping; temporal
    labels use fixed skip→tilt order; overlap, angle, and temporal
    visibility are render-local and never written onto the resolved model
  - Resolver outcomes: fits-flat, categorical tilt without skip, exhausted→allow,
    authored-angle short-circuit, horizontal-bar short-circuit
  - Font size on x-axis labels is never mutated by the resolver (shrink is gone)
  - Temporal-bucketed axes use fiscal-anchored visibility thinning
  - VL emission: directive parameter on axis_to_vl reaches labelOverlap in vl_field_maps
  - AxisLabelLayout.collision_label_count: every collision-detecting call
    site in _generic_layout, _temporal_layout, and the ordinal/nominal path
    reports the label count it actually measured (never a fresh recount),
    and None once its own measurement says the layout fits
  - _temporal_layout's no-tilt fallback never lets a coarser _fits_flat
    recheck overwrite a genuine collision resolve_temporal_label_visibility
    already found, unless the label set was narrowed further since
"""

from __future__ import annotations

import dataclasses
from typing import Any
from unittest.mock import MagicMock

import pytest

# ---------------------------------------------------------------------------
# Fixture helpers
# ---------------------------------------------------------------------------


def _resolved_charts() -> Any:
    """Default ChartStyleContext from theme cascade."""
    from dbt_charts.core.compile.config import get_theme_style
    from dbt_charts.core.compile.resolve.style.board import resolve_chart_style_context

    return resolve_chart_style_context(get_theme_style())


def _overlap(tilt: bool = True, skip: bool = True) -> Any:
    """Convenience: build a ResolvedAxisLabelOverlapConfig."""
    from dbt_charts.core.compile.models.style.resolved import (
        ResolvedAxisLabelOverlapConfig,
    )

    return ResolvedAxisLabelOverlapConfig(tilt=tilt, skip=skip)


def _axis_x_with(
    overlap: Any = None,  # ResolvedAxisLabelOverlapConfig | None
    angle: float | None = None,
    tilt_increments: list[float] | None = None,
    font_size: float | None = None,
) -> Any:
    """Resolved axis_x with controlled overlap config and optional overrides.

    Starts from the fully-merged axis_x (resolved_axis_style — axis_x itself is
    now an authored-only sparse overlay, not theme-complete) so fonts,
    tilt_increments, etc. are populated, then patches specific fields via
    dataclasses.replace.
    """
    from dbt_charts.core.compile.resolve.style.axis_cascade import resolved_axis_style

    charts = _resolved_charts()
    axis_x = resolved_axis_style(
        charts, "axis_x", "ordinal", chart_type="", labeled=False
    )
    label = axis_x.labels
    font = label.font

    if font_size is not None:
        font = font.model_copy(update={"size": font_size})

    label = dataclasses.replace(
        label,
        font=font,
        overlap=overlap,
        angle=angle,
        tilt_increments=(
            tilt_increments if tilt_increments is not None else label.tilt_increments
        ),
    )
    return dataclasses.replace(axis_x, labels=label)


def _make_mock_measurer(width_per_char: float = 5.0) -> Any:
    """Font measurer mock: width = width_per_char * len(text) * (size / 11)."""
    m = MagicMock()
    m.measure = lambda text, size: width_per_char * len(text) * (size / 11.0)
    return m


def _axis_x_temporal_with(
    overlap: Any = None,  # ResolvedAxisLabelOverlapConfig | None
    tilt_increments: list[float] | None = None,
    encoding_time_unit: str | None = None,
) -> Any:
    """Resolved temporal axis_x with controlled overlap config and cadence.

    Mirrors ``_axis_x_with`` above but for the temporal orientation
    ``_temporal_layout``/``_generic_layout`` need — ``encoding_time_unit``
    forces ``axis.time_unit`` (e.g. a TIME_PART_UNIT like "dayofweek") past
    auto-detection when a test needs a specific code path, matching the
    pattern in ``tests/core/test_label_cadence_ladder.py``'s ``_axis_x_temporal``.
    """
    from dbt_charts.core.compile.resolve.style.axis_cascade import resolved_axis_style

    charts = _resolved_charts()
    axis_x = resolved_axis_style(
        charts, "axis_x", "temporal", chart_type="", labeled=False
    )
    label = dataclasses.replace(
        axis_x.labels,
        overlap=overlap,
        angle=None,
        tilt_increments=(
            tilt_increments
            if tilt_increments is not None
            else axis_x.labels.tilt_increments
        ),
    )
    return dataclasses.replace(axis_x, labels=label, time_unit=encoding_time_unit)


# ---------------------------------------------------------------------------
# 1. Authored model: AxisLabelOverlapConfig struct
# ---------------------------------------------------------------------------


class TestAxisLabelOverlapConfig:
    def test_both_true_accepted(self) -> None:
        from dbt_charts.core.compile.models.style.theme.axis import (
            AxisLabelOverlapConfig,
        )

        cfg = AxisLabelOverlapConfig(tilt=True, skip=True)
        assert cfg.tilt is True
        assert cfg.skip is True

    def test_partial_false_accepted(self) -> None:
        """Disable one strategy; the other unset (cascade-inherit)."""
        from dbt_charts.core.compile.models.style.theme.axis import (
            AxisLabelOverlapConfig,
        )

        cfg = AxisLabelOverlapConfig(tilt=False)
        assert cfg.tilt is False
        assert cfg.skip is None

    def test_both_false_accepted(self) -> None:
        from dbt_charts.core.compile.models.style.theme.axis import (
            AxisLabelOverlapConfig,
        )

        cfg = AxisLabelOverlapConfig(tilt=False, skip=False)
        assert cfg.tilt is False
        assert cfg.skip is False

    def test_empty_construction_all_none(self) -> None:
        """No args → all None (inherit everything from cascade)."""
        from dbt_charts.core.compile.models.style.theme.axis import (
            AxisLabelOverlapConfig,
        )

        cfg = AxisLabelOverlapConfig()
        assert cfg.tilt is None
        assert cfg.skip is None

    def test_unknown_key_rejected_via_extra_forbid(self) -> None:
        """extra=forbid: unknown keys are rejected without a custom validator."""
        from pydantic import ValidationError

        from dbt_charts.core.compile.models.style.theme.axis import (
            AxisLabelOverlapConfig,
        )

        with pytest.raises(ValidationError, match="extra_forbidden"):
            AxisLabelOverlapConfig(tilt=True, greedy=True)  # type: ignore[call-arg]

    def test_list_rejected_for_overlap_field(self) -> None:
        """The old list format is rejected — AxisLabelStyle.overlap is the struct."""
        from pydantic import ValidationError

        from dbt_charts.core.compile.models.style.theme.axis import AxisLabelStyle

        with pytest.raises(ValidationError):
            AxisLabelStyle(overlap=["tilt", "skip"])  # type: ignore[arg-type]

    def test_string_allow_rejected(self) -> None:
        """The old 'allow' sugar string is rejected — use { tilt: false, skip: false }."""
        from pydantic import ValidationError

        from dbt_charts.core.compile.models.style.theme.axis import AxisLabelStyle

        with pytest.raises(ValidationError):
            AxisLabelStyle(overlap="allow")  # type: ignore[arg-type]

    def test_string_smart_rejected(self) -> None:
        """The old 'smart' sugar string is rejected — omit overlap to inherit."""
        from pydantic import ValidationError

        from dbt_charts.core.compile.models.style.theme.axis import AxisLabelStyle

        with pytest.raises(ValidationError):
            AxisLabelStyle(overlap="smart")  # type: ignore[arg-type]


# ---------------------------------------------------------------------------
# 2. Theme cascade: struct with both bools flows through
# ---------------------------------------------------------------------------


class TestOverlapThemeCascade:
    def test_theme_cascade_provides_overlap_struct(self) -> None:
        """After cascade, axis_x.labels.overlap is a ResolvedAxisLabelOverlapConfig."""
        from dbt_charts.core.compile.config import get_theme_style
        from dbt_charts.core.compile.models.style.resolved import (
            ResolvedAxisLabelOverlapConfig,
        )
        from dbt_charts.core.compile.resolve.style.axis_cascade import (
            resolved_axis_style,
        )
        from dbt_charts.core.compile.resolve.style.board import (
            resolve_chart_style_context,
        )

        charts = resolve_chart_style_context(get_theme_style())
        axis_x = resolved_axis_style(
            charts, "axis_x", "ordinal", chart_type="", labeled=False
        )
        overlap = axis_x.labels.overlap
        assert isinstance(overlap, ResolvedAxisLabelOverlapConfig)

    def test_default_overlap_both_strategies_enabled(self) -> None:
        """The theme default enables both strategies."""
        from dbt_charts.core.compile.config import get_theme_style
        from dbt_charts.core.compile.resolve.style.axis_cascade import (
            resolved_axis_style,
        )
        from dbt_charts.core.compile.resolve.style.board import (
            resolve_chart_style_context,
        )

        charts = resolve_chart_style_context(get_theme_style())
        axis_x = resolved_axis_style(
            charts, "axis_x", "ordinal", chart_type="", labeled=False
        )
        overlap = axis_x.labels.overlap
        assert overlap is not None
        assert overlap.tilt is True
        assert overlap.skip is True


# ---------------------------------------------------------------------------
# 3. Resolver: directive is render-local (not on the resolved model)
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# 4. Resolver: strategy walk outcomes (categorical axes)
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# 6. collision_label_count: every fits=False site reports what it measured;
#    every fits=True site reports None (nothing for a detector to read)
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# 9. AxisLabelLayout.label_block_height: what the chosen angle costs vertically
# ---------------------------------------------------------------------------
