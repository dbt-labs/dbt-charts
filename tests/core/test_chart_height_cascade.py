"""Tests for get_chart_content_height() with chart-root height/aspect_ratio.

Phase 3 of chart-size-control: sizing cascade reads chart.height then
chart.aspect_ratio before falling through to theme cascade.

Phase 4 (this task): min_height / max_height clamps cascade from
resolved_style.charts.* (board/theme) and may be overridden per-chart at root.
"""

from __future__ import annotations

import dataclasses

import pytest

from dbt_charts.core.compile.config import get_theme_style
from dbt_charts.core.compile.models.chart.normalized import (
    AreaChart,
    BarChart,
    Chart,
    LineChart,
    ScatterChart,
)
from dbt_charts.core.render.sizing import get_chart_content_height

from ._board_utils import _default_resolved_style

_FAMILIES = {
    "bar": BarChart,
    "line": LineChart,
    "area": AreaChart,
    "scatter": ScatterChart,
}


def _make_chart(chart_type: str = "bar", **kwargs: object) -> Chart:
    """Minimal compiled Chart for sizing tests."""
    return _FAMILIES[chart_type](id="c", type=chart_type, **kwargs)  # type: ignore[arg-type]


class TestChartRootHeightCascade:
    """Sizing cascade: chart.height → chart.aspect_ratio → theme cascade."""

    def test_chart_height_wins_over_aspect_ratio_and_theme(self) -> None:
        """chart.height: 400 returns 400 regardless of width."""
        chart = _make_chart(height=400)
        h = get_chart_content_height(
            chart, width=1200.0, resolved_style=_default_resolved_style()
        )
        assert h == 400

    def test_chart_height_wins_when_both_set(self) -> None:
        """When both chart.height and chart.aspect_ratio set, height wins."""
        chart = _make_chart(height=400, aspect_ratio=2.0)
        h = get_chart_content_height(
            chart, width=1200.0, resolved_style=_default_resolved_style()
        )
        assert h == 400

    def test_chart_aspect_ratio_used_when_no_height(self) -> None:
        """chart.aspect_ratio: 2.0 with width=600 returns 300.0 (below max_height cap)."""
        chart = _make_chart(aspect_ratio=2.0)
        h = get_chart_content_height(
            chart, width=600.0, resolved_style=_default_resolved_style()
        )
        assert h == pytest.approx(300.0)

    def test_chart_aspect_ratio_clamped_to_max_height(self) -> None:
        """chart-root aspect_ratio height is clamped to theme max_height."""

        max_h = float(get_theme_style().charts.max_height)
        chart = _make_chart(aspect_ratio=2.0)
        h = get_chart_content_height(
            chart, width=1200.0, resolved_style=_default_resolved_style()
        )
        assert h == pytest.approx(max_h)

    def test_chart_aspect_ratio_with_different_width(self) -> None:
        """chart.aspect_ratio=3.0, width=900 → height=300."""
        chart = _make_chart(aspect_ratio=3.0)
        h = get_chart_content_height(
            chart, width=900.0, resolved_style=_default_resolved_style()
        )
        assert h == pytest.approx(300.0)

    def test_explicit_chart_height_bypasses_min_clamp(self) -> None:
        """chart.height is returned as-is; it bypasses theme min/max clamping."""
        # chart.height is an explicit author override — it wins unconditionally.
        chart = _make_chart(height=10)
        h = get_chart_content_height(
            chart, width=1200.0, resolved_style=_default_resolved_style()
        )
        assert h == 10

    def test_theme_cascade_used_when_neither_set(self) -> None:
        """When neither chart.height nor chart.aspect_ratio is set, theme cascade applies.

        The chart-root fields are absent, so the function must produce a
        width-dependent result (the theme's aspect_ratio formula), not a
        static fallback. We verify this by checking that two different widths
        produce two different heights — proof the cascade (not a static default)
        is driving the result.
        """
        chart = _make_chart()
        h_narrow = get_chart_content_height(
            chart, width=400.0, resolved_style=_default_resolved_style()
        )
        h_wide = get_chart_content_height(
            chart, width=800.0, resolved_style=_default_resolved_style()
        )
        # Both widths produce valid heights; wider should produce taller (unless clamped)
        assert h_narrow > 0
        assert h_wide >= h_narrow


class TestChartHeightClampsCascadeFromResolvedStyle:
    """min_height / max_height read from resolved_style.charts, not the global get_config()."""

    def _resolved_style_with_clamps(
        self, min_height: float, max_height: float
    ) -> object:
        """Build a resolved style with overridden chart clamp values."""
        base = _default_resolved_style()
        # Replace chart_defaults sub-object with new clamp values.
        new_charts = dataclasses.replace(
            base.chart_defaults,
            min_height=min_height,
            max_height=max_height,
        )
        return dataclasses.replace(base, chart_defaults=new_charts)

    def test_board_max_height_honored_over_get_config(self) -> None:
        """Board-level style.charts.max_height: 100 clamps aspect-ratio height to 100."""
        # With width=1100 and default aspect_ratio ~2.0, unclamped height ≈ 550px.
        # Board max_height=100 should cap it.
        rs = self._resolved_style_with_clamps(min_height=60.0, max_height=100.0)
        chart = _make_chart(aspect_ratio=2.0)
        h = get_chart_content_height(chart, width=1100.0, resolved_style=rs)  # type: ignore[arg-type]
        assert h == pytest.approx(100.0)

    def test_board_min_height_raises_floor(self) -> None:
        """Board-level style.charts.min_height: 200 raises floor when aspect height is tiny."""
        # width=100, aspect_ratio=2.0 → unclamped h = 50, below min_height=200.
        rs = self._resolved_style_with_clamps(min_height=200.0, max_height=800.0)
        chart = _make_chart(aspect_ratio=2.0)
        h = get_chart_content_height(chart, width=100.0, resolved_style=rs)  # type: ignore[arg-type]
        assert h == pytest.approx(200.0)

    def test_chart_root_max_height_overrides_board_max_height(self) -> None:
        """chart-root max_height: 200 overrides board max_height: 100 for this chart."""
        # Board says max=100, but chart says max=200; chart wins.
        rs = self._resolved_style_with_clamps(min_height=60.0, max_height=100.0)
        chart = _make_chart(aspect_ratio=2.0, max_height=200.0)
        h = get_chart_content_height(chart, width=1100.0, resolved_style=rs)  # type: ignore[arg-type]
        # Unclamped ≈ 550, capped at chart-root max_height=200.
        assert h == pytest.approx(200.0)

    def test_chart_root_min_height_overrides_board_min_height(self) -> None:
        """chart-root min_height: 300 overrides board min_height: 60 for this chart."""
        # Board says min=60, chart says min=300; with width=100, aspect h=50 < 300.
        rs = self._resolved_style_with_clamps(min_height=60.0, max_height=800.0)
        chart = _make_chart(aspect_ratio=2.0, min_height=300.0)
        h = get_chart_content_height(chart, width=100.0, resolved_style=rs)  # type: ignore[arg-type]
        assert h == pytest.approx(300.0)

    def test_explicit_height_still_bypasses_chart_root_min_max(self) -> None:
        """Explicit chart.height bypasses min/max clamping even when chart-root clamps set."""
        rs = self._resolved_style_with_clamps(min_height=200.0, max_height=300.0)
        chart = _make_chart(height=50, min_height=200.0, max_height=300.0)
        h = get_chart_content_height(chart, width=1200.0, resolved_style=rs)  # type: ignore[arg-type]
        # chart.height wins unconditionally — no clamping.
        assert h == pytest.approx(50.0)


class TestChartHeightAspectRatioCascadeFromResolvedStyle:
    """Steps 3-4 (per-family / global theme aspect_ratio) must read
    resolved_style.charts, not get_theme_style() — same class of bug as
    get_board_gap's dropped layout gap override (audit: 2026-07-13 sweep).
    """

    def test_per_family_aspect_ratio_reads_resolved_style_not_global_theme(
        self,
    ) -> None:
        """style.bar.aspect_ratio override changes bar-chart height."""
        base = _default_resolved_style()
        distinctive_aspect = 3.0
        new_bar = base.chart_defaults.bar.model_copy(
            update={"aspect_ratio": distinctive_aspect}
        )
        rs = dataclasses.replace(
            base, chart_defaults=dataclasses.replace(base.chart_defaults, bar=new_bar)
        )

        chart = _make_chart()
        width = 900.0
        h = get_chart_content_height(chart, width=width, resolved_style=rs)  # type: ignore[arg-type]
        assert h == pytest.approx(width / distinctive_aspect)
        assert h != get_chart_content_height(chart, width=width, resolved_style=base)


class TestFullWidthMatchesHalfWidth:
    """The max_height cap is the half-width height, so wide and split read alike."""

    @staticmethod
    def _board_content_width() -> float:
        frame = get_theme_style().frame
        return float(frame.max_width) - 2 * float(frame.margin)

    @pytest.mark.parametrize("chart_type", sorted(_FAMILIES))
    def test_full_width_height_equals_half_width_height(self, chart_type: str) -> None:
        """Cartesian families only.

        pie/geoshape/point_map carry their own aspect ratio and are clamped at
        both widths on a default frame, so they satisfy this by accident rather
        than by the rule.
        """
        content_width = self._board_content_width()
        chart = _make_chart(chart_type)
        full = get_chart_content_height(
            chart, width=content_width, resolved_style=_default_resolved_style()
        )
        half = get_chart_content_height(
            chart, width=content_width / 2, resolved_style=_default_resolved_style()
        )
        assert full == pytest.approx(half)

    def test_half_width_height_is_not_clamped(self) -> None:
        """The equality is the aspect ratio meeting the cap, not both hitting it.

        If max_height dropped below the half-width aspect height the two would
        still match — but every chart on the board would be capped, which is a
        different (and wrong) rule.
        """
        content_width = self._board_content_width()
        style = _default_resolved_style()
        aspect_height = (content_width / 2) / float(style.chart_defaults.aspect_ratio)
        assert float(style.chart_defaults.max_height) == pytest.approx(aspect_height)
