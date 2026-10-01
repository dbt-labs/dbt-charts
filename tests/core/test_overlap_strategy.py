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
import datetime
from typing import Any
from unittest.mock import MagicMock, patch

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
        charts, "axis_x", "ordinal", chart_type="", label_authored=False
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
        charts, "axis_x", "temporal", chart_type="", label_authored=False
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
            charts, "axis_x", "ordinal", chart_type="", label_authored=False
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
            charts, "axis_x", "ordinal", chart_type="", label_authored=False
        )
        overlap = axis_x.labels.overlap
        assert overlap is not None
        assert overlap.tilt is True
        assert overlap.skip is True


# ---------------------------------------------------------------------------
# 3. Resolver: directive is render-local (not on the resolved model)
# ---------------------------------------------------------------------------


class TestLabelOverlapSeparateField:
    def test_resolver_does_not_overwrite_overlap_struct(self) -> None:
        """After resolve_axis_x_overlap, the authored overlap struct is intact."""
        from dbt_charts.core.render.chart.emitters._label_overlap import (
            resolve_axis_x_overlap,
        )

        ov = _overlap()
        axis_x = _axis_x_with(overlap=ov)
        data = [{"x": "A"}, {"x": "B"}, {"x": "C"}]
        layout = resolve_axis_x_overlap(
            axis_x,
            "x",
            data,
            1.0,
            edge_labels_flushed=False,
            chart_width=1200.0,
            continuous_temporal=False,
        )
        assert axis_x.labels.overlap is ov
        assert layout.label_overlap == "allow"


# ---------------------------------------------------------------------------
# 4. Resolver: strategy walk outcomes (categorical axes)
# ---------------------------------------------------------------------------


class TestResolverStrategyWalk:
    def test_all_disabled_returns_allow(self) -> None:
        """Both strategies disabled → no reduction, directive=allow."""
        from dbt_charts.core.render.chart.emitters._label_overlap import (
            resolve_axis_x_overlap,
        )

        axis_x = _axis_x_with(overlap=_overlap(tilt=False, skip=False))
        data = [{"x": v} for v in ["A", "B", "C"]]
        layout = resolve_axis_x_overlap(
            axis_x,
            "x",
            data,
            1.0,
            edge_labels_flushed=False,
            chart_width=600.0,
            continuous_temporal=False,
        )
        assert layout.label_overlap == "allow"

    def test_authored_angle_short_circuits_to_allow(self) -> None:
        """When angle is authored, strategy config is bypassed; directive = allow."""
        from dbt_charts.core.render.chart.emitters._label_overlap import (
            resolve_axis_x_overlap,
        )

        axis_x = _axis_x_with(overlap=_overlap(), angle=-45.0)
        data = [{"x": v} for v in ["A", "B", "C"]]
        layout = resolve_axis_x_overlap(
            axis_x,
            "x",
            data,
            1.0,
            edge_labels_flushed=False,
            chart_width=600.0,
            continuous_temporal=False,
        )
        assert layout.label_overlap == "allow"
        assert layout.angle == -45.0

    def test_horizontal_bar_short_circuits_to_allow_at_zero(self) -> None:
        """Horizontal bar pins angle=0, directive=allow regardless of strategies."""
        from dbt_charts.core.render.chart.emitters._label_overlap import (
            resolve_axis_x_overlap,
        )

        axis_x = _axis_x_with(overlap=_overlap())
        data = [{"x": v} for v in ["A", "B", "C"]]
        layout = resolve_axis_x_overlap(
            axis_x,
            "x",
            data,
            1.0,
            is_horizontal_bar=True,
            edge_labels_flushed=False,
            chart_width=600.0,
            continuous_temporal=False,
        )
        assert layout.label_overlap == "allow"
        assert layout.angle == 0.0

    def test_short_labels_fit_flat_font_size_unchanged(self) -> None:
        """Short labels at a wide chart fit unrotated; font size never moves."""
        from dbt_charts.core.render.chart.emitters._label_overlap import (
            resolve_axis_x_overlap,
        )

        mock_measurer = _make_mock_measurer(width_per_char=5.0)
        axis_x = _axis_x_with(overlap=_overlap(), font_size=11.0)
        data = [{"x": f"Aa{i:02d}"} for i in range(10)]  # 4-char labels

        with patch(
            "dbt_charts.core.render.chart.emitters._label_overlap.get_font_measurer",
            return_value=mock_measurer,
        ):
            layout = resolve_axis_x_overlap(
                axis_x,
                "x",
                data,
                1.0,
                edge_labels_flushed=False,
                chart_width=300.0,
                continuous_temporal=False,
            )

        assert layout.label_overlap == "allow"
        assert axis_x.labels.font.size == 11.0
        assert layout.angle == 0.0
        assert layout.collision_label_count is None

    def test_tilt_clears_the_collision_reports_no_count(self) -> None:
        """When a steep enough tilt actually clears the collision, the
        ordinal/nominal tilt branch reports no count -- distinct from
        ``test_nominal_tilt_angle_is_picked_against_domain_values_not_data``
        above, where the steepest available tilt still doesn't fit."""
        from dbt_charts.core.render.chart.emitters._label_overlap import (
            resolve_axis_x_overlap,
        )

        mock_measurer = _make_mock_measurer(width_per_char=5.0)
        axis_x = _axis_x_with(
            overlap=_overlap(tilt=True, skip=False),
            tilt_increments=[0.0, -90.0],
            font_size=11.0,
        )
        data = [{"x": f"LongLabel{i}"} for i in range(8)]

        with patch(
            "dbt_charts.core.render.chart.emitters._label_overlap.get_font_measurer",
            return_value=mock_measurer,
        ):
            layout = resolve_axis_x_overlap(
                axis_x,
                "x",
                data,
                1.0,
                edge_labels_flushed=False,
                chart_width=100.0,
                continuous_temporal=False,
            )

        assert layout.angle == -90.0
        assert layout.collision_label_count is None

    def test_tilt_attempted_when_labels_dont_fit_flat(self) -> None:
        """When labels don't fit unrotated, tilt is attempted; font size is untouched."""
        from dbt_charts.core.render.chart.emitters._label_overlap import (
            resolve_axis_x_overlap,
        )

        mock_measurer = _make_mock_measurer(width_per_char=5.0)
        axis_x = _axis_x_with(
            overlap=_overlap(),
            tilt_increments=[0.0, -30.0, -45.0, -60.0, -90.0],
            font_size=11.0,
        )
        data = [{"x": f"Hello{i}"} for i in range(30)]

        with patch(
            "dbt_charts.core.render.chart.emitters._label_overlap.get_font_measurer",
            return_value=mock_measurer,
        ):
            layout = resolve_axis_x_overlap(
                axis_x,
                "x",
                data,
                1.0,
                edge_labels_flushed=False,
                chart_width=200.0,
                continuous_temporal=False,
            )

        assert layout.label_overlap == "allow"
        assert axis_x.labels.font.size == 11.0

    def test_nominal_tilt_angle_is_picked_against_domain_values_not_data(self) -> None:
        """The tilt angle picked for a nominal/ordinal axis must come from the
        same widths the flat-fit gate already measures against
        ``domain_values`` — not re-derived from ``data`` alone.

        ``data`` carries 3 short single-char labels: on their own they fit
        flat (wide per-label band). ``domain_values`` — the union an overlay
        layer widens the shared scale to (``overlay_x_domain_values``) —
        carries 30 same-width labels: a much narrower band that does not fit
        flat and needs the steepest tilt available. Picking the angle from
        ``data`` alone (the pre-fix behavior) returns 0.0 here even though
        the rendered axis has 30 crowded bands, not 3.
        """
        from dbt_charts.core.render.chart.emitters._label_overlap import (
            resolve_axis_x_overlap,
        )

        mock_measurer = _make_mock_measurer(width_per_char=5.0)
        tilt_increments = [0.0, -30.0, -45.0, -60.0, -90.0]
        axis_x = _axis_x_with(
            overlap=_overlap(), tilt_increments=tilt_increments, font_size=11.0
        )
        data = [{"x": f"L{i}"} for i in range(3)]
        domain_values = [f"L{i}" for i in range(30)]

        with patch(
            "dbt_charts.core.render.chart.emitters._label_overlap.get_font_measurer",
            return_value=mock_measurer,
        ):
            layout = resolve_axis_x_overlap(
                axis_x,
                "x",
                data,
                1.0,
                edge_labels_flushed=False,
                chart_width=200.0,
                domain_values=domain_values,
                continuous_temporal=False,
            )

        assert layout.angle == tilt_increments[-1], layout.angle
        # Steepest tilt still doesn't clear 30 crowded bands -- the reported
        # count must be the domain_values-driven 30, never len(data) == 3.
        assert layout.collision_label_count == 30

    def test_skip_strategy_does_not_drop_categorical_labels(self) -> None:
        """Ordinal domains keep every label because skipped categories are unrecoverable."""
        from dbt_charts.core.render.chart.emitters._label_overlap import (
            resolve_axis_x_overlap,
        )

        mock_measurer = _make_mock_measurer(width_per_char=5.0)
        axis_x = _axis_x_with(overlap=_overlap(tilt=False, skip=True), font_size=11.0)
        data = [{"x": f"Cat{i}"} for i in range(20)]

        with patch(
            "dbt_charts.core.render.chart.emitters._label_overlap.get_font_measurer",
            return_value=mock_measurer,
        ):
            layout = resolve_axis_x_overlap(
                axis_x,
                "x",
                data,
                1.0,
                edge_labels_flushed=False,
                chart_width=200.0,
                continuous_temporal=False,
            )

        assert layout.label_overlap == "allow"

    def test_all_strategies_exhausted_produces_allow(self) -> None:
        """When all enabled strategies exhaust without fitting, fall through to allow."""
        from dbt_charts.core.render.chart.emitters._label_overlap import (
            resolve_axis_x_overlap,
        )

        mock_measurer = _make_mock_measurer(width_per_char=5.0)
        axis_x = _axis_x_with(
            overlap=_overlap(tilt=True, skip=False),
            tilt_increments=[0.0],  # only tries horizontal
            font_size=11.0,
        )
        # Very crowded: 50 labels at 200px → band=4px, nothing fits
        data = [{"x": f"LongCat{i}"} for i in range(50)]

        with patch(
            "dbt_charts.core.render.chart.emitters._label_overlap.get_font_measurer",
            return_value=mock_measurer,
        ):
            layout = resolve_axis_x_overlap(
                axis_x,
                "x",
                data,
                1.0,
                edge_labels_flushed=False,
                chart_width=200.0,
                continuous_temporal=False,
            )

        # No skip → exhausted list → allow (tolerate remaining overlap)
        assert layout.label_overlap == "allow"
        # Regression: exhausting every enabled strategy without ever fitting
        # used to discard that fact — nothing downstream could tell "gave up
        # and rendered overlapping" from "picked a layout that fits".
        assert layout.collision_label_count == 50

    def test_pairwise_adjacent_fit_avoids_tilt_when_wide_label_has_short_neighbors(
        self,
    ) -> None:
        """Pairwise check: wide label adjacent to short neighbors fits at current size."""
        from dbt_charts.core.render.chart.emitters._label_overlap import (
            resolve_axis_x_overlap,
        )

        mock_measurer = _make_mock_measurer(width_per_char=5.0)
        axis_x = _axis_x_with(overlap=_overlap(), font_size=11.0)
        data = [
            {"x": "Long Month Name"},  # 15 chars → 75px
            {"x": "Sep"},  # 3 chars → 15px
            {"x": "Oct"},  # 3 chars → 15px
        ]

        with patch(
            "dbt_charts.core.render.chart.emitters._label_overlap.get_font_measurer",
            return_value=mock_measurer,
        ):
            layout = resolve_axis_x_overlap(
                axis_x,
                "x",
                data,
                1.0,
                edge_labels_flushed=False,
                chart_width=200.0,
                continuous_temporal=False,
            )

        assert layout.label_overlap == "allow"
        assert axis_x.labels.font.size == 11.0
        assert layout.angle == 0.0

    def test_skip_disabled_with_crowded_axis_falls_through_to_allow(self) -> None:
        """When skip is False and tilt fails to fit, result is allow (not parity)."""
        from dbt_charts.core.render.chart.emitters._label_overlap import (
            resolve_axis_x_overlap,
        )

        mock_measurer = _make_mock_measurer(width_per_char=5.0)
        axis_x = _axis_x_with(
            overlap=_overlap(tilt=True, skip=False),
            tilt_increments=[0.0],
            font_size=11.0,
        )
        # 100 crowded labels: tilt will exhaust but skip is disabled → allow
        data = [{"x": f"LongLabel{i:03d}"} for i in range(100)]

        with patch(
            "dbt_charts.core.render.chart.emitters._label_overlap.get_font_measurer",
            return_value=mock_measurer,
        ):
            layout = resolve_axis_x_overlap(
                axis_x,
                "x",
                data,
                1.0,
                edge_labels_flushed=False,
                chart_width=200.0,
                continuous_temporal=False,
            )

        assert layout.label_overlap == "allow"

    def test_temporal_cadence_detect_time_unit_gives_up_falls_through_to_none(
        self,
    ) -> None:
        """A temporal x with no recognizable calendar bucket grain (e.g. a
        28-day cadence — same weekday, but too sparse for detect_time_unit's
        weekly gate) must resolve to directive=None, not "allow": "allow"
        forces VL's own labelOverlap off with no custom thinning computed to
        replace it, so every distinct date renders unthinned. None lets VL's
        native adaptive default (parity) do the reduction instead.
        """
        from dbt_charts.core.render.chart.emitters._label_overlap import (
            resolve_axis_x_overlap,
        )

        axis_x = _axis_x_with(overlap=_overlap())
        data = [
            {"x": d}
            for d in [
                "2024-01-07",
                "2024-02-04",
                "2024-03-03",
                "2024-03-31",
                "2024-04-28",
            ]
        ]
        layout = resolve_axis_x_overlap(
            axis_x,
            "x",
            data,
            1.0,
            edge_labels_flushed=False,
            chart_width=600.0,
            continuous_temporal=False,
        )
        assert layout.label_overlap is None

    def test_unrecognized_cadence_with_skip_disabled_honors_never_drop(
        self,
    ) -> None:
        """Same unrecognized cadence, but ``overlap.skip: false`` — an explicit
        author opt-out of ever dropping a label. Must resolve to "allow" (VL's
        own thinning disabled too), not None (which hands dropping back to
        VL's adaptive default and silently overrides the opt-out).
        """
        from dbt_charts.core.render.chart.emitters._label_overlap import (
            resolve_axis_x_overlap,
        )

        axis_x = _axis_x_with(overlap=_overlap(skip=False))
        data = [
            {"x": d}
            for d in [
                "2024-01-07",
                "2024-02-04",
                "2024-03-03",
                "2024-03-31",
                "2024-04-28",
            ]
        ]
        layout = resolve_axis_x_overlap(
            axis_x,
            "x",
            data,
            1.0,
            edge_labels_flushed=False,
            chart_width=600.0,
            continuous_temporal=False,
        )
        assert layout.label_overlap == "allow"


# ---------------------------------------------------------------------------
# 5. VL emission: label_overlap reaches labelOverlap
# ---------------------------------------------------------------------------


class TestVlEmission:
    def test_label_overlap_parity_emits_to_vl(self) -> None:
        from dbt_charts.core.render.chart.vl_field_maps import axis_to_vl

        axis = _axis_x_with(overlap=_overlap())
        vl = axis_to_vl(axis, label_overlap="parity")
        assert vl.get("labelOverlap") == "parity"

    def test_label_overlap_allow_emits_false_to_vl(self) -> None:
        from dbt_charts.core.render.chart.vl_field_maps import axis_to_vl

        axis = _axis_x_with(overlap=_overlap())
        vl = axis_to_vl(axis, label_overlap="allow")
        assert vl.get("labelOverlap") is False

    def test_label_overlap_none_omits_from_vl(self) -> None:
        from dbt_charts.core.render.chart.vl_field_maps import axis_to_vl

        axis = _axis_x_with(overlap=_overlap())
        vl = axis_to_vl(axis)
        assert "labelOverlap" not in vl


# ---------------------------------------------------------------------------
# 6. collision_label_count: every fits=False site reports what it measured;
#    every fits=True site reports None (nothing for a detector to read)
# ---------------------------------------------------------------------------


class TestCollisionLabelCountThreading:
    """WARN-AXIS-LABEL-COLLISION reads ``AxisLabelLayout.collision_label_count``
    (never a fresh recount over raw input) to size its message. Each
    construction site in ``_generic_layout``, ``_temporal_layout``, and the
    ordinal/nominal path must set it from the label set that site itself
    measured -- and to ``None`` once that measurement says the layout fits,
    since nothing downstream reads a count for a layout that isn't colliding.
    """

    def test_generic_layout_no_tilt_exhausted_reports_measured_count(self) -> None:
        """``_generic_layout``'s final "no strategy left" return (tilt
        disabled, flat+skip both failed) reports the width list it measured,
        not the raw row count."""
        from dbt_charts.core.render.chart.emitters._label_overlap import (
            resolve_axis_x_overlap,
        )

        measurer = _make_mock_measurer(width_per_char=50.0)
        axis = _axis_x_temporal_with(
            overlap=_overlap(tilt=False, skip=False), encoding_time_unit="dayofweek"
        )
        data = [{"x": f"2024-01-{d:02d}"} for d in range(1, 8)]

        with patch(
            "dbt_charts.core.render.chart.emitters._label_overlap.get_font_measurer",
            return_value=measurer,
        ):
            layout = resolve_axis_x_overlap(
                axis,
                "x",
                data,
                1.0,
                edge_labels_flushed=False,
                chart_width=100.0,
                continuous_temporal=False,
            )

        assert layout.collision_label_count == 7

    def test_generic_layout_tilt_still_fails_reports_measured_count(self) -> None:
        """``_generic_layout``'s tilt branch, when the only available angle
        still doesn't clear the collision, reports the width-list length it
        tilted against."""
        from dbt_charts.core.render.chart.emitters._label_overlap import (
            resolve_axis_x_overlap,
        )

        measurer = _make_mock_measurer(width_per_char=50.0)
        axis = _axis_x_temporal_with(
            overlap=_overlap(tilt=True, skip=False),
            tilt_increments=[0.0],
            encoding_time_unit="dayofweek",
        )
        data = [{"x": f"2024-01-{d:02d}"} for d in range(1, 8)]

        with patch(
            "dbt_charts.core.render.chart.emitters._label_overlap.get_font_measurer",
            return_value=measurer,
        ):
            layout = resolve_axis_x_overlap(
                axis,
                "x",
                data,
                1.0,
                edge_labels_flushed=False,
                chart_width=100.0,
                continuous_temporal=False,
            )

        assert layout.collision_label_count == 7

    def test_generic_layout_tilt_fit_reports_no_collision(self) -> None:
        """The same tilt branch, when a steeper angle clears the collision,
        reports no count -- a fitting layout has nothing for a detector to
        read."""
        from dbt_charts.core.render.chart.emitters._label_overlap import (
            resolve_axis_x_overlap,
        )

        measurer = _make_mock_measurer(width_per_char=50.0)
        axis = _axis_x_temporal_with(
            overlap=_overlap(tilt=True, skip=False),
            tilt_increments=[0.0, -90.0],
            encoding_time_unit="dayofweek",
        )
        data = [{"x": f"2024-01-{d:02d}"} for d in range(1, 8)]

        with patch(
            "dbt_charts.core.render.chart.emitters._label_overlap.get_font_measurer",
            return_value=measurer,
        ):
            layout = resolve_axis_x_overlap(
                axis,
                "x",
                data,
                1.0,
                edge_labels_flushed=False,
                chart_width=100.0,
                continuous_temporal=False,
            )

        assert layout.angle == -90.0
        assert layout.collision_label_count is None

    def test_ordinal_no_tilt_flat_fails_reports_measured_count(self) -> None:
        """The plain nominal/ordinal path's flat-only return (tilt disabled)
        reports the measured label count alongside a real collision."""
        from dbt_charts.core.render.chart.emitters._label_overlap import (
            resolve_axis_x_overlap,
        )

        mock_measurer = _make_mock_measurer(width_per_char=5.0)
        axis_x = _axis_x_with(overlap=_overlap(tilt=False, skip=False), font_size=11.0)
        data = [{"x": f"LongCategory{i}"} for i in range(20)]

        with patch(
            "dbt_charts.core.render.chart.emitters._label_overlap.get_font_measurer",
            return_value=mock_measurer,
        ):
            layout = resolve_axis_x_overlap(
                axis_x,
                "x",
                data,
                1.0,
                edge_labels_flushed=False,
                chart_width=100.0,
                continuous_temporal=False,
            )

        assert layout.collision_label_count == 20

    def test_temporal_submonth_tilt_reports_measured_count(self) -> None:
        """The bucket-aligned submonth day-number tilt branch (fewer than 2
        month openers in the span) reports the day-number widths it measured
        when tilt still doesn't clear the collision."""
        from dbt_charts.core.render.chart.emitters._label_overlap import (
            resolve_axis_x_overlap,
        )

        measurer = _make_mock_measurer(width_per_char=100.0)
        axis = _axis_x_temporal_with(
            overlap=_overlap(tilt=True, skip=True), tilt_increments=[0.0]
        )
        # Mondays only, all within Jan 2024 -> fewer than 2 month openers.
        data = [{"x": f"2024-01-{d:02d}"} for d in [1, 8, 15, 22, 29]]

        with patch(
            "dbt_charts.core.render.chart.emitters._label_overlap.get_font_measurer",
            return_value=measurer,
        ):
            layout = resolve_axis_x_overlap(
                axis,
                "x",
                data,
                1.0,
                edge_labels_flushed=False,
                chart_width=10.0,
                bucket_aligned_temporal=True,
                continuous_temporal=False,
            )

        assert layout.format_time_unit == "yearweek"
        assert layout.collision_label_count == 5

    def test_temporal_submonth_tilt_fit_reports_no_collision(self) -> None:
        """The same submonth day-number branch, at a chart width wide enough
        for the day-number widths to fit, reports no count."""
        from dbt_charts.core.render.chart.emitters._label_overlap import (
            resolve_axis_x_overlap,
        )

        measurer = _make_mock_measurer(width_per_char=5.0)
        axis = _axis_x_temporal_with(
            overlap=_overlap(tilt=True, skip=True), tilt_increments=[0.0, -90.0]
        )
        # Mondays only, all within Jan 2024 -> fewer than 2 month openers.
        data = [{"x": f"2024-01-{d:02d}"} for d in [1, 8, 15, 22, 29]]

        with patch(
            "dbt_charts.core.render.chart.emitters._label_overlap.get_font_measurer",
            return_value=measurer,
        ):
            layout = resolve_axis_x_overlap(
                axis,
                "x",
                data,
                1.0,
                edge_labels_flushed=False,
                chart_width=60.0,
                bucket_aligned_temporal=True,
                continuous_temporal=False,
            )

        assert layout.format_time_unit == "yearweek"
        assert layout.collision_label_count is None

    def test_temporal_main_tilt_still_fails_reports_measured_count(self) -> None:
        """The main bucketed-cadence tilt branch, reached once flat coarsening
        alone doesn't clear the collision, reports the visible-date count it
        tilted against -- after year-cadence parity halving, not before --
        when even the steepest angle still doesn't fit."""
        from dbt_charts.core.render.chart.emitters._label_overlap import (
            resolve_axis_x_overlap,
        )

        measurer = _make_mock_measurer(width_per_char=1000.0)
        axis = _axis_x_temporal_with(
            overlap=_overlap(tilt=True, skip=True), tilt_increments=[0.0]
        )
        start = datetime.date(2015, 1, 1)
        dates: list[datetime.date] = []
        cursor = start
        for _ in range(36):
            dates.append(cursor)
            year = cursor.year + cursor.month // 12
            month = cursor.month % 12 + 1
            cursor = datetime.date(year, month, 1)
        data = [{"x": d.isoformat()} for d in dates]

        with patch(
            "dbt_charts.core.render.chart.emitters._label_overlap.get_font_measurer",
            return_value=measurer,
        ):
            layout = resolve_axis_x_overlap(
                axis,
                "x",
                data,
                1.0,
                edge_labels_flushed=False,
                chart_width=10.0,
                continuous_temporal=False,
            )

        assert layout.visibility_time_unit == "year"
        # 36 months -> 3 candidate years, halved once by the year-cadence
        # parity skip -> 2, never the raw 36-row count.
        assert layout.collision_label_count == 2

    def test_temporal_main_tilt_fit_reports_no_collision(self) -> None:
        """The same tilt branch, when a steeper angle clears the collision,
        reports no count."""
        from dbt_charts.core.render.chart.emitters._label_overlap import (
            resolve_axis_x_overlap,
        )

        measurer = _make_mock_measurer(width_per_char=10.0)
        axis = _axis_x_temporal_with(
            overlap=_overlap(tilt=True, skip=True), tilt_increments=[0.0, -90.0]
        )
        start = datetime.date(2015, 1, 1)
        dates: list[datetime.date] = []
        cursor = start
        for _ in range(36):
            dates.append(cursor)
            year = cursor.year + cursor.month // 12
            month = cursor.month % 12 + 1
            cursor = datetime.date(year, month, 1)
        data = [{"x": d.isoformat()} for d in dates]

        with patch(
            "dbt_charts.core.render.chart.emitters._label_overlap.get_font_measurer",
            return_value=measurer,
        ):
            layout = resolve_axis_x_overlap(
                axis,
                "x",
                data,
                1.0,
                edge_labels_flushed=False,
                chart_width=60.0,
                continuous_temporal=False,
            )

        assert layout.visibility_time_unit == "year"
        assert layout.angle == -90.0
        assert layout.collision_label_count is None


class TestTemporalNoTiltNeverOverwritesAKnownCollision:
    """``_temporal_layout``'s no-tilt fallback (overlap.tilt disabled) must
    not let a coarser ``_fits_flat`` recheck silently disagree with the
    pairwise, flush-edge-aware verdict ``resolve_temporal_label_visibility``
    already reached over the very same label set.
    """

    def test_flush_edge_collision_survives_the_no_tilt_fallback(self) -> None:
        """Regression: the first tick's flushed label reserves its full
        measured width in the real pairwise check (``_pair_clears``), but
        ``_fits_flat``'s uniform-band average only ever reserves half of it.
        At a band between those two thresholds the two checks disagree.

        12 first-of-month 2024 dates, "Jan" wide and every other month
        narrow, band=70px: the flush-aware check needs >=100.5px for the
        first pair (full 100 + half of 1) and fails; the naive average only
        needs >=50.5px (half of each) and would pass. skip/tilt are both
        disabled so neither strategy narrows the label set before this
        fallback runs -- it is the exact set the earlier check already ruled
        out.
        """
        from dbt_charts.core.render.chart.emitters._label_overlap import (
            resolve_axis_x_overlap,
        )

        measurer = MagicMock()
        measurer.measure = lambda text, size: 100.0 if text == "Jan" else 1.0
        axis = _axis_x_temporal_with(overlap=_overlap(tilt=False, skip=False))
        data = [{"x": f"2024-{m:02d}-01"} for m in range(1, 13)]

        with patch(
            "dbt_charts.core.render.chart.emitters._label_overlap.get_font_measurer",
            return_value=measurer,
        ):
            layout = resolve_axis_x_overlap(
                axis,
                "x",
                data,
                1.0,
                edge_labels_flushed=True,
                chart_width=840.0,
                continuous_temporal=False,
            )

        assert layout.format_time_unit == "yearmonth"
        assert layout.collision_label_count == 12

    def test_further_narrowed_set_still_fails_gets_a_fresh_fit_check(self) -> None:
        """Once the year-cadence parity skip actually halves the label set,
        that set was never checked before -- the no-tilt fallback must still
        measure it, and report a real collision when the halved set still
        doesn't fit."""
        from dbt_charts.core.render.chart.emitters._label_overlap import (
            resolve_axis_x_overlap,
        )

        measurer = _make_mock_measurer(width_per_char=10.0)
        axis = _axis_x_temporal_with(
            overlap=_overlap(tilt=False, skip=True), tilt_increments=[0.0]
        )
        start = datetime.date(2015, 1, 1)
        dates: list[datetime.date] = []
        cursor = start
        for _ in range(36):
            dates.append(cursor)
            year = cursor.year + cursor.month // 12
            month = cursor.month % 12 + 1
            cursor = datetime.date(year, month, 1)
        data = [{"x": d.isoformat()} for d in dates]

        with patch(
            "dbt_charts.core.render.chart.emitters._label_overlap.get_font_measurer",
            return_value=measurer,
        ):
            layout = resolve_axis_x_overlap(
                axis,
                "x",
                data,
                1.0,
                edge_labels_flushed=False,
                chart_width=60.0,
                continuous_temporal=False,
            )

        assert layout.visibility_time_unit == "year"
        assert layout.collision_label_count == 2

    def test_further_narrowed_set_that_fits_reports_no_collision(self) -> None:
        """Same halved label set as above, at a chart width wide enough for
        it to actually fit. This test is the one that fails under a
        `fits = False` regression at the no-tilt fallback; the sibling test
        above (a set that still doesn't fit) stays green under that same
        mutation, so together the pair discriminates the branch."""
        from dbt_charts.core.render.chart.emitters._label_overlap import (
            resolve_axis_x_overlap,
        )

        measurer = _make_mock_measurer(width_per_char=10.0)
        axis = _axis_x_temporal_with(
            overlap=_overlap(tilt=False, skip=True), tilt_increments=[0.0]
        )
        start = datetime.date(2015, 1, 1)
        dates: list[datetime.date] = []
        cursor = start
        for _ in range(36):
            dates.append(cursor)
            year = cursor.year + cursor.month // 12
            month = cursor.month % 12 + 1
            cursor = datetime.date(year, month, 1)
        data = [{"x": d.isoformat()} for d in dates]

        with patch(
            "dbt_charts.core.render.chart.emitters._label_overlap.get_font_measurer",
            return_value=measurer,
        ):
            layout = resolve_axis_x_overlap(
                axis,
                "x",
                data,
                1.0,
                edge_labels_flushed=False,
                chart_width=100.0,
                continuous_temporal=False,
            )

        assert layout.visibility_time_unit == "year"
        assert layout.collision_label_count is None


# ---------------------------------------------------------------------------
# 9. AxisLabelLayout.label_block_height: what the chosen angle costs vertically
# ---------------------------------------------------------------------------


class TestLabelBlockHeight:
    """The label block's pixel HEIGHT at the resolved angle.

    A consumer reserving space under the axis (the bottom support_table strip)
    cannot re-derive this: the angle is a render-time decision, and on a
    temporal axis only this module holds the formatted label strings.
    """

    @staticmethod
    def _gapless_measurer() -> Any:
        """Mock measurer whose spaces are zero-width.

        The tilt ladder measures BAND widths (label + inter-label gap); zeroing
        the gap lets the assertions below name the label text alone.
        """
        m = MagicMock()
        m.measure = lambda text, size: (
            0.0 if text == " " else 10.0 * len(text) * (size / 11.0)
        )
        return m

    def test_flat_labels_report_one_line(self) -> None:
        from dbt_charts.core.render.chart.emitters._label_overlap import (
            resolve_axis_x_overlap,
        )

        axis = _axis_x_with(overlap=_overlap())
        data = [{"x": v} for v in ["A", "B", "C"]]
        layout = resolve_axis_x_overlap(
            axis,
            "x",
            data,
            1.0,
            edge_labels_flushed=False,
            chart_width=600.0,
            continuous_temporal=False,
        )
        assert layout.angle == 0.0
        assert layout.label_block_height == axis.labels.font.size

    def test_vertical_tilt_reports_the_widest_label(self) -> None:
        """At -90 the block is as tall as the widest label is wide."""
        from dbt_charts.core.render.chart.emitters._label_overlap import (
            resolve_axis_x_overlap,
        )

        measurer = self._gapless_measurer()
        axis = _axis_x_with(overlap=_overlap(), tilt_increments=[0.0, -90.0])
        labels = [f"Category {i:02d}" for i in range(20)]
        data = [{"x": v} for v in labels]

        with patch(
            "dbt_charts.core.render.chart.emitters._label_overlap.get_font_measurer",
            return_value=measurer,
        ):
            layout = resolve_axis_x_overlap(
                axis,
                "x",
                data,
                1.0,
                edge_labels_flushed=False,
                chart_width=200.0,
                continuous_temporal=False,
            )

        assert layout.angle == -90.0
        widest = max(measurer.measure(label, axis.labels.font.size) for label in labels)
        assert layout.label_block_height == pytest.approx(widest)

    def test_partial_tilt_is_between_one_line_and_the_widest_label(self) -> None:
        from dbt_charts.core.render.chart.emitters._label_overlap import (
            resolve_axis_x_overlap,
        )

        measurer = self._gapless_measurer()
        axis = _axis_x_with(overlap=_overlap(), tilt_increments=[0.0, -45.0])
        labels = [f"Category {i:02d}" for i in range(20)]
        data = [{"x": v} for v in labels]

        with patch(
            "dbt_charts.core.render.chart.emitters._label_overlap.get_font_measurer",
            return_value=measurer,
        ):
            layout = resolve_axis_x_overlap(
                axis,
                "x",
                data,
                1.0,
                edge_labels_flushed=False,
                chart_width=200.0,
                continuous_temporal=False,
            )

        assert layout.angle == -45.0
        widest = max(measurer.measure(label, axis.labels.font.size) for label in labels)
        assert axis.labels.font.size < layout.label_block_height < widest

    def test_authored_angle_is_measured_too(self) -> None:
        """The authored-angle short-circuit never reaches the tilt ladder, but a
        pinned -90 costs exactly as much vertical room as a picked one."""
        from dbt_charts.core.render.chart.emitters._label_overlap import (
            resolve_axis_x_overlap,
        )

        measurer = _make_mock_measurer(width_per_char=10.0)
        axis = _axis_x_with(overlap=_overlap(), angle=-90.0)
        labels = ["Alpha", "Beta", "Gamma"]
        data = [{"x": v} for v in labels]

        with patch(
            "dbt_charts.core.render.chart.emitters._label_overlap.get_font_measurer",
            return_value=measurer,
        ):
            layout = resolve_axis_x_overlap(
                axis,
                "x",
                data,
                1.0,
                edge_labels_flushed=False,
                chart_width=600.0,
                continuous_temporal=False,
            )

        assert layout.angle == -90.0
        widest = max(measurer.measure(label, axis.labels.font.size) for label in labels)
        assert layout.label_block_height == pytest.approx(widest)

    def test_authored_angle_on_a_temporal_axis_measures_formatted_labels(self) -> None:
        """The raw datum is ``2024-01-01``; the axis draws ``Jan``. Sizing against
        the datum would reserve room for text nothing paints."""
        from dbt_charts.core.render.chart.emitters._label_overlap import (
            resolve_axis_x_overlap,
        )

        measurer = _make_mock_measurer(width_per_char=10.0)
        axis = _axis_x_temporal_with(overlap=_overlap())
        axis = dataclasses.replace(
            axis, labels=dataclasses.replace(axis.labels, angle=-90.0)
        )
        data = [{"x": f"2024-{month:02d}-01"} for month in range(1, 13)]

        with patch(
            "dbt_charts.core.render.chart.emitters._label_overlap.get_font_measurer",
            return_value=measurer,
        ):
            layout = resolve_axis_x_overlap(
                axis,
                "x",
                data,
                1.0,
                edge_labels_flushed=False,
                chart_width=600.0,
                continuous_temporal=False,
            )

        font_size = axis.labels.font.size
        assert layout.label_block_height == pytest.approx(
            measurer.measure("Jan", font_size)
        )
        assert layout.label_block_height < measurer.measure("2024-01-01", font_size)
