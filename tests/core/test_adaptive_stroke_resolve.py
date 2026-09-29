"""Density-adaptive stroke baked into the resolve layer.

Tests pin BEHAVIOR under variation — monotonicity, author-pin, companion
fields, zero sentinel, fallback, and faceted per-cell width — not theme
literals (per dbt-charts/AGENTS.md).  Pure-helper tests are unit-level;
resolve-level tests construct a minimal normalized chart and call resolve().
"""

from __future__ import annotations

import math
from typing import Any

import pytest
from pydantic import TypeAdapter

from dbt_charts.core.compile.compiler import compile as compile_board
from dbt_charts.core.compile.config import get_chart_rendering, get_theme_style
from dbt_charts.core.compile.models.chart.normalized import Chart
from dbt_charts.core.compile.models.primitives import StrokeStyle
from dbt_charts.core.compile.models.query.normalized import SqlQuery
from dbt_charts.core.compile.models.style.authored import StylePatch
from dbt_charts.core.compile.models.style.theme.marks import (
    AreaStackedMarkStyle,
    PointMarkStyle,
)
from dbt_charts.core.compile.resolve import resolve
from dbt_charts.core.compile.resolve.chart.adaptive_stroke import (
    adaptive_stroke,
    bake_point_companions,
    facet_panel_width,
    max_points_per_series,
    stroke_from_px_per_point,
)
from dbt_charts.core.compile.resolve.style.board import resolve_chart_style_context
from dbt_charts.core.render.chart.vega_lite import generate_vega_lite_spec

_BOARD_STYLE = resolve_chart_style_context(get_theme_style())
_QUERY = SqlQuery(sql="SELECT 1", source="src")


def _board_style_with_patch(charts_patch: dict[str, Any]) -> Any:
    """A ChartStyleContext with a board/theme-tier ``style.charts.*`` override.

    Mirrors what ``extends:``-ing a custom theme or authoring ``style:`` in a
    board/``meta.yml`` produces (``compile_board_resolved_style``'s
    ``own_patch`` -> ``resolve_chart_style_context(base, own_patch)``) -- as
    opposed to ``_BOARD_STYLE``, which carries no author overrides at all,
    and a chart-local ``style:`` patch (``_line_chart``'s own
    ``stroke_width=`` kwarg), which is a third, narrower authoring tier.
    """
    patch = StylePatch.model_validate({"charts": charts_patch})
    return resolve_chart_style_context(get_theme_style(), patch)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _line_chart(
    n_points: int,
    *,
    width: float = 600.0,
    color: str | None = None,
    stroke_width: float | None = None,
    point_size: float | None = None,
    point_stroke_width: float | None = None,
    multiples_columns: str | None = None,
    layer_type: str | None = None,
) -> tuple[Chart, list[dict[str, Any]], float]:
    """Return (chart, data, width) for a simple line chart.

    ``layer_type``: adds one overlay layer of that mark type (``"line"`` or
    a cross-family ``"area"``), reading ``y2`` off the same rows as the base
    series' ``y``.
    """
    marks_patch: dict[str, Any] = {}
    if stroke_width is not None:
        marks_patch["line"] = {"stroke": {"width": stroke_width}}
    if point_size is not None or point_stroke_width is not None:
        point_patch: dict[str, Any] = {}
        if point_size is not None:
            point_patch["size"] = point_size
        if point_stroke_width is not None:
            point_patch["stroke_width"] = point_stroke_width
        marks_patch["point"] = point_patch

    style_dict: dict[str, Any] = {"marks": marks_patch} if marks_patch else {}

    multiples: dict[str, str] | None = (
        {"columns": multiples_columns} if multiples_columns is not None else None
    )

    chart_dict: dict[str, Any] = {
        "id": "t",
        "type": "line",
        "x": "x",
        "y": "y",
        "color": color,
        "query": _QUERY,
        "query_name": "q",
    }
    if style_dict:
        chart_dict["style"] = style_dict
    if multiples is not None:
        chart_dict["multiples"] = multiples
    if layer_type is not None:
        chart_dict["layers"] = [{"type": layer_type, "y": "y2"}]

    chart = TypeAdapter(Chart).validate_python(chart_dict)

    if color is not None:
        # Two series: A and B, each with n_points distinct x values
        data: list[dict[str, Any]] = [
            {"x": i, "y": float(i), "series": "A"} for i in range(n_points)
        ] + [{"x": i, "y": float(i) * 2, "series": "B"} for i in range(n_points)]
    elif multiples_columns is not None:
        # Two facet panels: cat0 and cat1, each with n_points
        data = [{"x": i, "y": float(i), "facet": "cat0"} for i in range(n_points)] + [
            {"x": i, "y": float(i), "facet": "cat1"} for i in range(n_points)
        ]
    elif layer_type is not None:
        data = [{"x": i, "y": float(i), "y2": float(i) * 2} for i in range(n_points)]
    else:
        data = [{"x": i, "y": float(i)} for i in range(n_points)]

    return chart, data, width


def _area_chart(
    n_points: int,
    *,
    width: float = 600.0,
    color: str | None = None,
    stroke_width: float | None = None,
    multiples_columns: str | None = None,
    stack: str | None = None,
    layer_type: str | None = None,
) -> tuple[Chart, list[dict[str, Any]], float]:
    """Return (chart, data, width) for a simple area chart.

    ``layer_type``: adds one overlay layer of that mark type (``"area"`` or
    a cross-family ``"line"``), reading ``y2`` off the same rows as the base
    series' ``y``.
    """
    marks_patch: dict[str, Any] = {}
    if stroke_width is not None:
        marks_patch["line"] = {"stroke": {"width": stroke_width}}

    style_dict: dict[str, Any] = {}
    if marks_patch:
        style_dict["marks"] = marks_patch
    if stack is not None:
        style_dict["stack"] = stack

    chart_dict: dict[str, Any] = {
        "id": "t",
        "type": "area",
        "x": "x",
        "y": "y",
        "color": color,
        "query": _QUERY,
        "query_name": "q",
    }
    if style_dict:
        chart_dict["style"] = style_dict
    if multiples_columns is not None:
        chart_dict["multiples"] = {"columns": multiples_columns}
    if layer_type is not None:
        chart_dict["layers"] = [{"type": layer_type, "y": "y2"}]

    chart = TypeAdapter(Chart).validate_python(chart_dict)

    if color is not None and multiples_columns is not None:
        # Two facet panels, each carrying its own two-series overlap -- color
        # keeps the chart on the (uncapped) overlap recipe so density-adaptive
        # differences between panel widths stay visible; a colorless area is
        # capped at the stacked recipe's fallback width regardless of density.
        data: list[dict[str, Any]] = [
            {"x": i, "y": float(i), "series": s, "facet": f}
            for f in ("cat0", "cat1")
            for s in ("A", "B")
            for i in range(n_points)
        ]
    elif color is not None:
        data = [
            {"x": i, "y": float(i), "y2": float(i) * 0.5, "series": "A"}
            for i in range(n_points)
        ] + [
            {"x": i, "y": float(i) * 2, "y2": float(i), "series": "B"}
            for i in range(n_points)
        ]
    elif multiples_columns is not None:
        # Two facet panels each with n_points sharing the same x values — the bug
        # path (full card width) gives the same stroke as non-faceted; the fix
        # (per-panel width) gives a thinner stroke because px_per_point shrinks.
        data = [{"x": i, "y": float(i), "facet": "cat0"} for i in range(n_points)] + [
            {"x": i, "y": float(i), "facet": "cat1"} for i in range(n_points)
        ]
    else:
        data = [{"x": i, "y": float(i)} for i in range(n_points)]

    return chart, data, width


def _resolved_line_stroke(
    n_points: int, *, width: float = 600.0, color: str | None = None, **kwargs: Any
) -> float:
    chart, data, w = _line_chart(n_points, width=width, color=color, **kwargs)
    resolved = resolve(chart, data, chart_style_context=_BOARD_STYLE, width=w)
    return resolved.style.line_mark.stroke.width


def _resolved_area_stroke(
    n_points: int, *, width: float = 600.0, color: str | None = None
) -> float:
    chart, data, w = _area_chart(n_points, width=width, color=color)
    resolved = resolve(chart, data, chart_style_context=_BOARD_STYLE, width=w)
    return resolved.style.line_mark.stroke.width


# ---------------------------------------------------------------------------
# Unit tests: adaptive_stroke() formula
# ---------------------------------------------------------------------------


# Explicit clamp bounds for the pure-formula tests (min_w/max_w are required
# args now — no in-code defaults; production passes chart_rendering.stroke).
_MIN_W = 1.5
_MAX_W = 4.0


class TestAdaptiveStrokeFormula:
    def test_midpoint_returns_expected_value(self) -> None:
        """At px_per_point=e^1 ≈ 2.72, raw=1+1=2.0, snapped to 2.0 (within clamp)."""
        result = adaptive_stroke(math.e, _MIN_W, _MAX_W)
        assert result == 2.0

    def test_dense_end_clamps_at_floor(self) -> None:
        """Very dense (1 px/pt) → clamp floor."""
        assert adaptive_stroke(1.0, _MIN_W, _MAX_W) == _MIN_W

    def test_sparse_end_clamps_at_ceiling(self) -> None:
        """Very sparse (1000 px/pt) → clamp ceiling."""
        assert adaptive_stroke(1000.0, _MIN_W, _MAX_W) == _MAX_W

    def test_snap_to_half_integers(self) -> None:
        """Output is always a multiple of 0.5."""
        for px in [1.5, 3.0, 6.0, 15.0, 50.0, 100.0]:
            result = adaptive_stroke(px, _MIN_W, _MAX_W)
            assert result * 2 == round(result * 2), f"px={px}: {result} not half-int"

    def test_negative_px_raises(self) -> None:
        with pytest.raises(ValueError, match="px_per_point must be positive"):
            adaptive_stroke(-1.0, _MIN_W, _MAX_W)

    def test_zero_px_raises(self) -> None:
        with pytest.raises(ValueError, match="px_per_point must be positive"):
            adaptive_stroke(0.0, _MIN_W, _MAX_W)

    def test_monotonic_increasing(self) -> None:
        """More space per point → thicker stroke."""
        widths = [
            adaptive_stroke(px, _MIN_W, _MAX_W) for px in [1.0, 2.0, 5.0, 10.0, 50.0]
        ]
        assert widths == sorted(widths)


class TestStrokeFromPxPerPoint:
    """The one leaf both density_adaptive_stroke and line.py's own resolver
    call to turn a px-per-point density signal into a stroke width -- the
    consolidation point for what was a hand-rolled duplicate in line.py."""

    def test_non_positive_returns_zero(self) -> None:
        assert stroke_from_px_per_point(0.0) == 0.0
        assert stroke_from_px_per_point(-5.0) == 0.0

    def test_positive_matches_adaptive_stroke_with_configured_clamp(self) -> None:
        clamp = get_chart_rendering().stroke
        px = 30.0
        assert stroke_from_px_per_point(px) == adaptive_stroke(
            px, clamp.min_width, clamp.max_width
        )


# ---------------------------------------------------------------------------
# Unit tests: max_points_per_series()
# ---------------------------------------------------------------------------


class TestFacetedDensityFoldsPerPanel:
    """``density_adaptive_stroke`` folds ``max_points_per_series`` across
    ``dataset.panels`` rather than reading the pooled rows, so a faceted
    chart's stroke is sized by its *densest panel*.

    Every other faceted fixture in this file gives both panels the same x
    range (``_line_chart``'s two panels are each ``range(n_points)``), so
    pooled and per-panel counts are identical and those tests pass under
    either implementation. This one makes the panels **disjoint**: pooling
    would see 2n distinct x values where the densest panel has n, halving
    ``effective_width / n_pts`` and baking every faceted line a full ``log``
    step too thin.
    """

    def test_disjoint_panels_size_stroke_by_the_densest_panel(self) -> None:
        n_per_panel = 10
        card_width = 600.0
        chart, _shared_range_data, width = _line_chart(
            n_per_panel, width=card_width, multiples_columns="facet"
        )
        # Disjoint ranges: cat0 over 0..9, cat1 over 10..19. Pooled distinct-x
        # is 20; the densest panel's is 10.
        data: list[dict[str, Any]] = [
            {"x": i, "y": float(i), "facet": "cat0"} for i in range(n_per_panel)
        ] + [
            {"x": i, "y": float(i), "facet": "cat1"}
            for i in range(n_per_panel, n_per_panel * 2)
        ]

        resolved = resolve(chart, data, chart_style_context=_BOARD_STYLE, width=width)
        panel_width = facet_panel_width(
            card_width,
            2,
            has_mirror=bool(resolved.style.axis_y.mirror),
            extra_axis_px=0.0,
        )
        per_panel = adaptive_stroke(panel_width / n_per_panel, _MIN_W, _MAX_W)
        pooled = adaptive_stroke(panel_width / (n_per_panel * 2), _MIN_W, _MAX_W)

        assert resolved.style.line_mark.stroke.width == per_panel
        assert per_panel != pooled, (
            "fixture no longer discriminates: pooled and per-panel counts "
            "must bake different strokes for this test to mean anything"
        )


class TestMaxPointsPerSeries:
    """One panel's rows in, densest series' point count out.

    Faceting is not this function's concern: a faceted chart folds this per
    panel via ``reduce_panels``, covered by
    ``TestFacetedDensityFoldsPerPanel`` above — which is the only faceted
    fixture here with *disjoint* panel x-ranges, and so the only one that
    can tell a per-panel fold from a pooled read.
    """

    def test_single_series_counts_distinct_x(self) -> None:
        data = [{"x": 1, "y": 0}, {"x": 2, "y": 0}, {"x": 2, "y": 1}]
        assert max_points_per_series(data, "x", "") == 2

    def test_multi_series_uses_densest(self) -> None:
        data = [{"x": i, "y": 0, "s": "A"} for i in range(5)] + [
            {"x": i, "y": 0, "s": "B"} for i in range(3)
        ]
        # A has 5 distinct x, B has 3 → densest is 5
        assert max_points_per_series(data, "x", "s") == 5

    def test_empty_data_returns_zero(self) -> None:
        assert max_points_per_series([], "x", "") == 0

    def test_null_x_values_ignored(self) -> None:
        data = [{"x": None, "y": 0}, {"x": 1, "y": 0}, {"x": 2, "y": 0}]
        assert max_points_per_series(data, "x", "") == 2


# ---------------------------------------------------------------------------
# Unit tests: facet_panel_width()
# ---------------------------------------------------------------------------


class TestFacetPanelWidth:
    def test_no_mirror_subtracts_chrome(self) -> None:
        """Without mirror, panel width = (width − chrome) / cols."""
        from dbt_charts.core.compile.config import get_chart_rendering

        cfg = get_chart_rendering().facet
        result = facet_panel_width(600.0, 3, has_mirror=False, extra_axis_px=0.0)
        expected = (600.0 - cfg.chrome_px) / 3
        assert result == pytest.approx(expected)

    def test_mirror_adds_gutter(self) -> None:
        """With mirror, extra mirror_axis_px is subtracted → narrower panels."""
        result_no_mirror = facet_panel_width(
            600.0, 2, has_mirror=False, extra_axis_px=0.0
        )
        result_mirror = facet_panel_width(600.0, 2, has_mirror=True, extra_axis_px=0.0)
        assert result_mirror < result_no_mirror

    def test_panel_shrinks_below_floor_rather_than_exceeding_card_width(self) -> None:
        """The card boundary always wins: panels shrink below min_panel_px rather
        than pushing the card wider than its declared width.

        This is the regression the panel floor used to invert — flooring
        ``usable`` at ``min_panel_px * panel_cols`` before dividing let a high
        panel count silently paint past the card's right edge. RJ's call
        (2026-08-10): the card boundary is non-negotiable, so panels below the
        legibility floor render narrower and a warning tells the author,
        rather than the floor winning and the chart overflowing its box.
        """
        from dbt_charts.core.compile.config import get_chart_rendering

        cfg = get_chart_rendering().facet
        # Very narrow width, 5 columns → panels must shrink well below the floor.
        result = facet_panel_width(10.0, 5, has_mirror=False, extra_axis_px=0.0)
        assert result < cfg.min_panel_px
        assert result == pytest.approx(0.0)

    def test_single_panel_no_facet(self) -> None:
        """1 panel, no mirror: (width − chrome) / 1."""
        from dbt_charts.core.compile.config import get_chart_rendering

        cfg = get_chart_rendering().facet
        result = facet_panel_width(600.0, 1, has_mirror=False, extra_axis_px=0.0)
        expected = 600.0 - cfg.chrome_px
        assert result == pytest.approx(expected)

    def test_panel_width_times_cols_plus_chrome_never_exceeds_card_width(self) -> None:
        """The invariant the floor used to violate: painted panels + chrome must
        fit inside the card at every panel count, not just the ones below the
        old floor's breakeven point."""
        from dbt_charts.core.compile.config import get_chart_rendering

        cfg = get_chart_rendering().facet
        width = 600.0
        for panel_cols in (1, 2, 4, 6, 10):
            panel_w = facet_panel_width(
                width, panel_cols, has_mirror=False, extra_axis_px=0.0
            )
            assert panel_w * panel_cols + cfg.chrome_px <= width + 1e-9


# ---------------------------------------------------------------------------
# Unit tests: bake_point_companions() -- the density on/off trigger and the
# diameter-ratio sizing, tested directly rather than only through resolve().
# ---------------------------------------------------------------------------


class TestBakePointCompanions:
    _BASE_POINT = PointMarkStyle()

    def test_ring_tracks_stroke_when_not_authored(self) -> None:
        result = bake_point_companions(
            self._BASE_POINT, 4.0, 100.0, size_authored=True, ring_authored=False
        )
        assert result.stroke_width == 4.0

    def test_ring_preserved_when_authored(self) -> None:
        pinned = self._BASE_POINT.model_copy(update={"stroke_width": 1.5})
        result = bake_point_companions(
            pinned, 4.0, 100.0, size_authored=True, ring_authored=True
        )
        assert result.stroke_width == 1.5

    def test_size_preserved_when_authored_regardless_of_density(self) -> None:
        """An author-pinned size must survive even at a density that would
        otherwise turn points off -- the density trigger only governs the
        UNauthored default, never overrides an explicit author choice."""
        pinned = self._BASE_POINT.model_copy(update={"size": 99.0})
        result = bake_point_companions(
            pinned, 4.0, 1.0, size_authored=True, ring_authored=True
        )
        assert result.size == 99.0

    def test_size_off_below_threshold(self) -> None:
        threshold = get_chart_rendering().point.min_px_per_point
        result = bake_point_companions(
            self._BASE_POINT,
            4.0,
            threshold - 0.01,
            size_authored=False,
            ring_authored=True,
        )
        assert result.size == 0.0

    def test_size_on_at_threshold_matches_diameter_ratio(self) -> None:
        """At/above the threshold, size == (diameter_ratio * stroke)**2 -- the
        empirically-measured diameter = sqrt(size) relationship, not the
        textbook circle-area pi*r**2 reading."""
        threshold = get_chart_rendering().point.min_px_per_point
        ratio = get_chart_rendering().point.diameter_ratio
        stroke = 4.0
        result = bake_point_companions(
            self._BASE_POINT,
            stroke,
            threshold,
            size_authored=False,
            ring_authored=True,
        )
        assert result.size == pytest.approx((ratio * stroke) ** 2)


# ---------------------------------------------------------------------------
# Resolve-level tests: line chart baked stroke
# ---------------------------------------------------------------------------


class TestLineChartAdaptiveStroke:
    def test_monotonic_nonincreasing_as_n_rises(self) -> None:
        """More data points at same width → thinner or equal stroke."""
        strokes = [_resolved_line_stroke(n, width=600.0) for n in [5, 20, 50, 200]]
        for a, b in zip(strokes, strokes[1:], strict=False):
            assert a >= b, f"stroke should not increase as N increases: {strokes}"

    def test_monotonic_nondecreasing_as_width_rises(self) -> None:
        """More chart width at same N → thicker or equal stroke."""
        strokes = [_resolved_line_stroke(20, width=w) for w in [200, 400, 800]]
        for a, b in zip(strokes, strokes[1:], strict=False):
            assert a <= b, f"stroke should not decrease as width increases: {strokes}"

    def test_multi_series_uses_max_density(self) -> None:
        """Multi-series: adaptive uses the densest series, not total rows."""
        # Two series of 20 pts each → px_per_point = 600 / 20 (densest series)
        # Single series of 20 pts → same density
        stroke_multi = _resolved_line_stroke(20, width=600.0, color="series")
        stroke_single = _resolved_line_stroke(20, width=600.0)
        assert stroke_multi == stroke_single

    def test_author_pin_stroke_width_bypasses_adaptive(self) -> None:
        """Explicit marks.line.stroke.width → baked verbatim, no adaptive override."""
        pinned = 3.7
        chart, data, w = _line_chart(50, width=600.0, stroke_width=pinned)
        resolved = resolve(chart, data, chart_style_context=_BOARD_STYLE, width=w)
        assert resolved.style.line_mark.stroke.width == pinned

    def test_board_level_family_stroke_width_pin_bypasses_adaptive(self) -> None:
        """style.charts.line.marks.line.stroke.width (extends/board/meta.yml
        tier, dbt-labs/dbt-charts#37) → baked verbatim, no adaptive override --
        the chart itself authors no style: block at all."""
        pinned = 3.7
        board_style = _board_style_with_patch(
            {"line": {"marks": {"line": {"stroke": {"width": pinned}}}}}
        )
        chart, data, w = _line_chart(50, width=600.0)
        resolved = resolve(chart, data, chart_style_context=board_style, width=w)
        assert resolved.style.line_mark.stroke.width == pinned

    def test_global_marks_stroke_width_pin_bypasses_adaptive(self) -> None:
        """style.charts.marks.line.stroke.width (global tier, shared across
        every chart-family's line mark) → baked verbatim, no adaptive
        override."""
        pinned = 3.7
        board_style = _board_style_with_patch(
            {"marks": {"line": {"stroke": {"width": pinned}}}}
        )
        chart, data, w = _line_chart(50, width=600.0)
        resolved = resolve(chart, data, chart_style_context=board_style, width=w)
        assert resolved.style.line_mark.stroke.width == pinned

    def test_theme_tier_stroke_width_pin_bypasses_adaptive(self) -> None:
        """A built-in theme's own literal charts.line.marks.line.stroke.width
        (not a board/extends-theme *patch* -- the theme's own resolved Style)
        now bypasses adaptive too, the same as a board/chart pin. Previously
        a theme-tier literal was structurally unable to win (it was always
        the cascade's ``base``, never a patch, and the required-field
        baseline made every theme's width indistinguishable from "unset")."""
        pinned = 3.7
        theme = get_theme_style()
        line_family = theme.charts.line
        pinned_line_mark = line_family.marks.line.model_copy(
            update={
                "stroke": (line_family.marks.line.stroke or StrokeStyle()).model_copy(
                    update={"width": pinned}
                )
            }
        )
        theme_with_pin = theme.model_copy(
            update={
                "charts": theme.charts.model_copy(
                    update={
                        "line": line_family.model_copy(
                            update={
                                "marks": line_family.marks.model_copy(
                                    update={"line": pinned_line_mark}
                                )
                            }
                        )
                    }
                )
            }
        )
        board_style = resolve_chart_style_context(theme_with_pin)
        chart, data, w = _line_chart(50, width=600.0)
        resolved = resolve(chart, data, chart_style_context=board_style, width=w)
        assert resolved.style.line_mark.stroke.width == pinned

    def test_companion_point_stroke_width(self) -> None:
        """When adaptive fires, baked point.stroke_width matches line stroke."""
        chart, data, w = _line_chart(20, width=600.0)
        resolved = resolve(chart, data, chart_style_context=_BOARD_STYLE, width=w)
        line_stroke = resolved.style.line_mark.stroke.width
        assert resolved.style.point_mark.stroke_width == line_stroke

    def test_sparse_default_auto_enables_points_sized_off_stroke(self) -> None:
        """At low x density the default theme (point.size=0) auto-enables points,
        sized as (diameter_ratio * stroke)**2 -- never the theme's own 0 literal
        and never the textbook pi*stroke**2 reading (diameter = sqrt(size) here,
        confirmed by direct SVG measurement, not the circle-area formula)."""
        # n=20 at width=600 -> px_per_point=30, comfortably above the density
        # trigger -- this is the DEFAULT theme, no author or theme override.
        chart, data, w = _line_chart(20, width=600.0)
        resolved = resolve(chart, data, chart_style_context=_BOARD_STYLE, width=w)
        stroke = resolved.style.line_mark.stroke.width
        ratio = get_chart_rendering().point.diameter_ratio
        expected_size = (ratio * stroke) ** 2
        assert resolved.style.point_mark.size == pytest.approx(expected_size, rel=1e-9)

    def test_dense_default_keeps_points_disabled(self) -> None:
        """At high x density, points stay off (0.0) -- a caterpillar of beads
        past the spacing trigger helps nobody, so the default theme's disabled
        point stays disabled rather than being resurrected unconditionally."""
        # n=140 at width=600 -> px_per_point=4.3, well below the density trigger.
        chart, data, w = _line_chart(140, width=600.0)
        resolved = resolve(chart, data, chart_style_context=_BOARD_STYLE, width=w)
        assert resolved.style.point_mark.size == 0.0

    def test_trigger_is_pixel_spacing_not_point_count(self) -> None:
        """The same N must flip on/off purely by changing card width -- proving
        the trigger reads pixel spacing between points, not a point count that
        would travel incorrectly across card widths.
        """
        n = 40
        chart_narrow, data_narrow, w_narrow = _line_chart(n, width=500.0)
        resolved_narrow = resolve(
            chart_narrow, data_narrow, chart_style_context=_BOARD_STYLE, width=w_narrow
        )
        chart_wide, data_wide, w_wide = _line_chart(n, width=1400.0)
        resolved_wide = resolve(
            chart_wide, data_wide, chart_style_context=_BOARD_STYLE, width=w_wide
        )
        assert resolved_narrow.style.point_mark.size == 0.0
        assert resolved_wide.style.point_mark.size > 0.0

    def test_pinned_line_stroke_does_not_disable_point_density_trigger(self) -> None:
        """Authoring marks.line.stroke.width must not couple to the point
        trigger -- regression for the two knobs being wired together, which
        left points permanently off (at any density) once the line stroke
        was pinned."""
        chart, data, w = _line_chart(20, width=600.0, stroke_width=3.7)
        resolved = resolve(chart, data, chart_style_context=_BOARD_STYLE, width=w)
        assert resolved.style.line_mark.stroke.width == 3.7
        assert resolved.style.point_mark.size > 0.0

    def test_layered_line_overlay_gets_point_companions_matching_base(self) -> None:
        """A line-type layer on a line chart must bake its own point
        companions from the same density signal as the base series --
        regression for layers being skipped by bake_point_companions
        entirely, which left overlay lines with no points while the base
        series grew them."""
        from dbt_charts.core.compile.models.chart.resolved._layer import (
            ResolvedLineLayer,
        )

        chart, data, w = _line_chart(20, width=600.0, layer_type="line")
        resolved = resolve(chart, data, chart_style_context=_BOARD_STYLE, width=w)
        assert resolved.style.point_mark.size > 0.0
        assert len(resolved.layers) == 1
        layer = resolved.layers[0]
        assert isinstance(layer, ResolvedLineLayer)
        assert layer.point_mark.size == resolved.style.point_mark.size
        assert layer.point_mark.stroke_width == layer.line_mark.stroke.width

    def test_author_pin_point_size_preserved(self) -> None:
        """Explicit marks.point.size → baked verbatim, adaptive doesn't override."""
        pinned_size = 25.0
        chart, data, w = _line_chart(20, width=600.0, point_size=pinned_size)
        resolved = resolve(chart, data, chart_style_context=_BOARD_STYLE, width=w)
        assert resolved.style.point_mark.size == pinned_size

    def test_author_pin_point_stroke_width_preserved(self) -> None:
        """Explicit marks.point.stroke_width → baked verbatim."""
        pinned_ring = 1.5
        chart, data, w = _line_chart(20, width=600.0, point_stroke_width=pinned_ring)
        resolved = resolve(chart, data, chart_style_context=_BOARD_STYLE, width=w)
        assert resolved.style.point_mark.stroke_width == pinned_ring

    def test_zero_stroke_sentinel_never_resurrected(self) -> None:
        """A baked stroke width of exactly 0 must not be replaced by adaptive."""
        chart, data, w = _line_chart(20, width=600.0, stroke_width=0.0)
        resolved = resolve(chart, data, chart_style_context=_BOARD_STYLE, width=w)
        assert resolved.style.line_mark.stroke.width == 0.0

    def test_zero_stroke_does_not_zero_visible_points(self) -> None:
        """Zero stroke.width in the theme (sentinel = no stroke) must not zero visible
        point companions via the adaptive companion block.

        Scenario: theme sets line.stroke.width=0.0 (the "no line" sentinel) and
        points are visible (point.size > 0) — but the author does NOT pin stroke.
        Because the cascade delivers width=0.0, the bake block is skipped correctly.
        But _adaptive_stroke > 0 is still True (computed before the bake). If the
        companion then reads resolved_line_mark.stroke.width==0.0 and applies
        π·0²=0, it silently kills the visible points.
        """
        theme = get_theme_style()
        line_style = theme.charts.line
        # Set theme-level stroke.width=0.0 (sentinel: no stroke)
        marks_with_zero_stroke = line_style.marks.model_copy(
            update={
                "line": line_style.marks.line.model_copy(
                    update={"stroke": StrokeStyle(width=0.0)}
                ),
                # Enable visible points (size > 0) at the theme level
                "point": line_style.marks.point.model_copy(update={"size": 16.0}),
            }
        )
        line_with_zero = line_style.model_copy(update={"marks": marks_with_zero_stroke})
        board_with_zero_stroke = resolve_chart_style_context(
            theme.model_copy(
                update={
                    "charts": theme.charts.model_copy(update={"line": line_with_zero})
                }
            )
        )
        # Author does NOT pin stroke width — so the adaptive width is > 0,
        # but bake_line_stroke skips it (zero sentinel).
        # Companion block must NOT then zero the visible points.
        chart, data, w = _line_chart(20, width=600.0)  # no authored stroke
        resolved = resolve(
            chart, data, chart_style_context=board_with_zero_stroke, width=w
        )
        # Line stroke remains 0.0 (sentinel respected)
        assert resolved.style.line_mark.stroke.width == 0.0
        # Points remain visible — not zeroed by the companion block
        assert resolved.style.point_mark.size > 0.0

    def test_fallback_when_no_data(self) -> None:
        """When data is empty, fallback to chart_rendering.stroke.fallback_width
        (no crash) -- no tier pinned a width and adaptive cannot compute one."""
        chart, _, w = _line_chart(0, width=600.0)
        resolved = resolve(chart, [], chart_style_context=_BOARD_STYLE, width=w)
        assert (
            resolved.style.line_mark.stroke.width
            == get_chart_rendering().stroke.fallback_width
        )
        # The ring tracks that stroke even with no density to measure — the
        # base-series half of the same rule TestUnbakedLineOverlayRing pins
        # for overlay layers.
        assert (
            resolved.style.point_mark.stroke_width
            == resolved.style.line_mark.stroke.width
        )

    def test_fallback_when_no_width(self) -> None:
        """Width of 0 cannot determine px_per_point; falls back to
        chart_rendering.stroke.fallback_width."""
        chart, data, _ = _line_chart(20, width=0.0)
        resolved = resolve(chart, data, chart_style_context=_BOARD_STYLE, width=0.0)
        assert (
            resolved.style.line_mark.stroke.width
            == get_chart_rendering().stroke.fallback_width
        )
        assert (
            resolved.style.point_mark.stroke_width
            == resolved.style.line_mark.stroke.width
        )


# ---------------------------------------------------------------------------
# Resolve-level tests: area chart baked stroke
# ---------------------------------------------------------------------------


class TestAreaChartAdaptiveStroke:
    def test_monotonic_nonincreasing_as_n_rises(self) -> None:
        """Same monotonicity contract as line.

        Colored (2-series) so the chart stays on the overlap recipe -- a
        colorless area is now capped at the stacked recipe's fallback width
        regardless of density, which would hide the density-adaptive formula
        this test targets (see TestFacetedAreaChartAdaptiveStroke, which
        applies the same fix for the same reason)."""
        strokes = [
            _resolved_area_stroke(n, width=600.0, color="series") for n in [5, 20, 100]
        ]
        for a, b in zip(strokes, strokes[1:], strict=False):
            assert a >= b

    def test_author_pin_area_stroke_bypasses_adaptive(self) -> None:
        """Explicit area line stroke → verbatim, no adaptive override.

        Colored (2-series) so the chart stays on the overlap recipe -- a
        colorless area is capped at the stacked recipe's fallback width, so a
        pin at or below that fallback would pass even if the pin were
        silently ignored. Pinned width (3.7) deliberately avoids coinciding
        with any theme literal."""
        pinned = 3.7
        chart, data, w = _area_chart(
            50, width=600.0, color="series", stroke_width=pinned
        )
        resolved = resolve(chart, data, chart_style_context=_BOARD_STYLE, width=w)
        assert resolved.style.line_mark.stroke.width == pinned

    def test_board_level_family_stroke_width_pin_bypasses_adaptive(self) -> None:
        """style.charts.area.marks.line.stroke.width (extends/board/meta.yml
        tier, dbt-labs/dbt-charts#37) → baked verbatim, no adaptive override --
        the chart itself authors no style: block at all. Overlap recipe
        (colored) -- the top-edge line's own stroke is what paints there."""
        pinned = 3.7
        board_style = _board_style_with_patch(
            {"area": {"marks": {"line": {"stroke": {"width": pinned}}}}}
        )
        chart, data, w = _area_chart(50, width=600.0, color="series")
        resolved = resolve(chart, data, chart_style_context=board_style, width=w)
        assert resolved.style.line_mark.stroke.width == pinned

    def test_global_marks_line_stroke_width_pin_bypasses_adaptive(self) -> None:
        """style.charts.marks.line.stroke.width (global tier) → baked
        verbatim on the overlap recipe (colored), where the shared line-mark
        tier -- not the stacked-perimeter recipe -- is what actually paints
        the edge."""
        pinned = 3.7
        board_style = _board_style_with_patch(
            {"marks": {"line": {"stroke": {"width": pinned}}}}
        )
        chart, data, w = _area_chart(50, width=600.0, color="series")
        resolved = resolve(chart, data, chart_style_context=board_style, width=w)
        assert resolved.style.line_mark.stroke.width == pinned

    def test_theme_tier_overlap_stroke_width_pin_bypasses_adaptive(self) -> None:
        """A built-in theme's own literal charts.area.marks.line.stroke.width
        (the theme's own resolved Style, not a board/extends-theme patch) now
        bypasses adaptive on the overlap recipe -- previously structurally
        unable to win, same reasoning as the line-chart theme-tier test."""
        pinned = 3.7
        theme = get_theme_style()
        area_family = theme.charts.area
        pinned_area_line = area_family.marks.line.model_copy(
            update={
                "stroke": (area_family.marks.line.stroke or StrokeStyle()).model_copy(
                    update={"width": pinned}
                )
            }
        )
        theme_with_pin = theme.model_copy(
            update={
                "charts": theme.charts.model_copy(
                    update={
                        "area": area_family.model_copy(
                            update={
                                "marks": area_family.marks.model_copy(
                                    update={"line": pinned_area_line}
                                )
                            }
                        )
                    }
                )
            }
        )
        board_style = resolve_chart_style_context(theme_with_pin)
        chart, data, w = _area_chart(50, width=600.0, color="series")
        resolved = resolve(chart, data, chart_style_context=board_style, width=w)
        assert resolved.style.line_mark.stroke.width == pinned

    def test_board_level_stacked_family_stroke_width_pin_bypasses_adaptive(
        self,
    ) -> None:
        """style.charts.area.marks.area.stacked.stroke.width (extends/board/
        meta.yml tier) → baked verbatim on the stacked recipe (colorless),
        where marks.area.stacked.stroke -- not marks.line.stroke -- replaces
        the perimeter stroke wholesale."""
        pinned = 3.7
        board_style = _board_style_with_patch(
            {"area": {"marks": {"area": {"stacked": {"stroke": {"width": pinned}}}}}}
        )
        chart, data, w = _area_chart(50, width=600.0)
        resolved = resolve(chart, data, chart_style_context=board_style, width=w)
        assert resolved.style.line_mark.stroke.width == pinned

    def test_global_stacked_marks_stroke_width_pin_bypasses_adaptive(self) -> None:
        """style.charts.marks.area.stacked.stroke.width (global tier) →
        baked verbatim on the stacked recipe (colorless)."""
        pinned = 3.7
        board_style = _board_style_with_patch(
            {"marks": {"area": {"stacked": {"stroke": {"width": pinned}}}}}
        )
        chart, data, w = _area_chart(50, width=600.0)
        resolved = resolve(chart, data, chart_style_context=board_style, width=w)
        assert resolved.style.line_mark.stroke.width == pinned

    def test_theme_tier_stacked_stroke_width_pin_bypasses_adaptive(self) -> None:
        """A built-in theme's own literal
        charts.area.marks.area.stacked.stroke.width (the theme's own resolved
        Style, not a board/extends-theme patch) now bypasses adaptive on the
        stacked recipe (colorless) -- previously structurally unable to win.
        ``marks.area.stacked`` is None straight off ``get_theme_style()`` (its
        ``SkipInheritSlots(cascade=True)`` whole-object inherit only expands
        later, inside ``resolve_chart_style_context``'s per-board pass) --
        same reasoning as ``marks.line.stroke`` in the sibling tests above, so
        this constructs a fresh override rather than reading one back; cap/join
        left unset here are backfilled field-by-field from the base theme by
        that same inherit pass before area.py's completeness check runs (see
        the sibling tests' resolved cap/join for the same backfill in
        action)."""
        pinned = 3.7
        theme = get_theme_style()
        area_family = theme.charts.area
        stacked = area_family.marks.area.stacked or AreaStackedMarkStyle()
        pinned_stacked = stacked.model_copy(
            update={
                "stroke": (stacked.stroke or StrokeStyle()).model_copy(
                    update={"width": pinned}
                )
            }
        )
        theme_with_pin = theme.model_copy(
            update={
                "charts": theme.charts.model_copy(
                    update={
                        "area": area_family.model_copy(
                            update={
                                "marks": area_family.marks.model_copy(
                                    update={
                                        "area": area_family.marks.area.model_copy(
                                            update={"stacked": pinned_stacked}
                                        )
                                    }
                                )
                            }
                        )
                    }
                )
            }
        )
        board_style = resolve_chart_style_context(theme_with_pin)
        chart, data, w = _area_chart(50, width=600.0)
        resolved = resolve(chart, data, chart_style_context=board_style, width=w)
        assert resolved.style.line_mark.stroke.width == pinned

    def test_fallback_when_no_data_overlap(self) -> None:
        """Overlap recipe (colored), no data anywhere in the cascade: falls
        back to chart_rendering.stroke.fallback_width, not the stacked
        recipe's constant -- these are two distinct engine-config values."""
        chart, _, w = _area_chart(0, width=600.0, color="series")
        resolved = resolve(chart, [], chart_style_context=_BOARD_STYLE, width=w)
        assert (
            resolved.style.line_mark.stroke.width
            == get_chart_rendering().stroke.fallback_width
        )

    def test_fallback_when_no_data_stacked(self) -> None:
        """Stacked recipe (colorless), no data anywhere in the cascade: falls
        back to chart_rendering.stroke.stacked_fallback_width -- the
        stacked-recipe's own, thinner constant, not the overlap one. The
        stacked recipe's pre-bake cap comparison must source this same
        constant."""
        chart, _, w = _area_chart(0, width=600.0)
        resolved = resolve(chart, [], chart_style_context=_BOARD_STYLE, width=w)
        assert (
            resolved.style.line_mark.stroke.width
            == get_chart_rendering().stroke.stacked_fallback_width
        )


class TestLayerInheritsCascadePinnedStroke:
    """An overlay layer must see a width pinned anywhere in the BASE chart's
    own cascade (chart-local or board/family tier), not just the layer's own
    local style patch. Regression: the layer gate only peeked at the layer's
    own ``style:`` patch, missing an inherited pin and letting the freshly
    computed adaptive value silently overwrite it -- even though the base
    chart's own bake (``bake_line_stroke`` in adaptive_stroke.py) already
    honors the same pin correctly.
    """

    def test_area_layer_honors_chart_local_pin_on_stacked_area(self) -> None:
        pinned = 3.7
        chart, data, w = _area_chart(
            40,
            width=600.0,
            color="series",
            stack="zero",
            stroke_width=pinned,
            layer_type="area",
        )
        resolved = resolve(chart, data, chart_style_context=_BOARD_STYLE, width=w)
        layer = resolved.layers[0]
        assert layer.line_mark.stroke.width == pinned

    def test_area_layer_honors_board_tier_pin_on_stacked_area(self) -> None:
        pinned = 3.7
        board_style = _board_style_with_patch(
            {"area": {"marks": {"line": {"stroke": {"width": pinned}}}}}
        )
        chart, data, w = _area_chart(
            40, width=600.0, color="series", stack="zero", layer_type="area"
        )
        resolved = resolve(chart, data, chart_style_context=board_style, width=w)
        layer = resolved.layers[0]
        assert layer.line_mark.stroke.width == pinned

    def test_line_layer_honors_board_tier_pin_on_line_base(self) -> None:
        """Same-family case: a line-type layer on a line base, pinned at the
        board tier -- pins the line-side gate deleted from ``_layers.py``
        (the area-side gates above already cover that removal for area)."""
        pinned = 3.7
        board_style = _board_style_with_patch(
            {"line": {"marks": {"line": {"stroke": {"width": pinned}}}}}
        )
        chart, data, w = _line_chart(40, width=600.0, layer_type="line")
        resolved = resolve(chart, data, chart_style_context=board_style, width=w)
        layer = resolved.layers[0]
        assert layer.line_mark.stroke.width == pinned


class TestUnpinnedOverlayLayerStillGetsAdaptiveWidth:
    """A pin on the BASE mark must not starve an unrelated, UNPINNED overlay
    layer of its own density-adaptive width. Regression: line.py/area.py each
    ran their own "is the base pinned" check and zeroed the shared adaptive
    value before handing it to ``_resolve_layer_list`` whenever the base was
    pinned -- even for a cross-family overlay (e.g. an area layer on a line
    base) whose own cascade parent carries no pin at all.
    ``bake_line_stroke`` already no-ops on a pinned mark, so the resolver
    doesn't need its own gate; the fix is to always compute the adaptive
    value and let that shared no-op handle the base.
    """

    def test_area_layer_on_pinned_line_base_gets_same_width_as_unpinned_control(
        self,
    ) -> None:
        pinned_style = _board_style_with_patch(
            {"line": {"marks": {"line": {"stroke": {"width": 3.7}}}}}
        )
        chart, data, w = _line_chart(20, width=600.0, layer_type="area")
        pinned = resolve(chart, data, chart_style_context=pinned_style, width=w)
        control = resolve(chart, data, chart_style_context=_BOARD_STYLE, width=w)
        assert (
            pinned.layers[0].line_mark.stroke.width
            == control.layers[0].line_mark.stroke.width
        )

    def test_line_layer_on_pinned_area_base_gets_same_width_as_unpinned_control(
        self,
    ) -> None:
        pinned_style = _board_style_with_patch(
            {"area": {"marks": {"line": {"stroke": {"width": 3.7}}}}}
        )
        chart, data, w = _area_chart(20, width=600.0, color="series", layer_type="line")
        pinned = resolve(chart, data, chart_style_context=pinned_style, width=w)
        control = resolve(chart, data, chart_style_context=_BOARD_STYLE, width=w)
        assert (
            pinned.layers[0].line_mark.stroke.width
            == control.layers[0].line_mark.stroke.width
        )


class TestAuthoredNullStrokeRejected:
    """Authored ``stroke: null`` on a line/area mark must still hit the
    builder's own "stroke is None after cascade" error -- never a silently
    manufactured default stroke. Regression: ``_apply_stroke_width_fallback``
    used to build a full ``StrokeStyle()`` out of nothing (``mark.stroke or
    StrokeStyle()``) whenever ``mark.stroke`` was None, resurrecting the
    exact mark the guard below exists to reject.

    Only the chart's own local ``style:`` patch, or a board/theme-tier patch
    at the GLOBAL ``style.charts.marks.line.stroke`` slot, can genuinely
    clear this cascade slot to None. The chart-local patch is applied via
    the terminal ``merge_onto_base`` (explicit ``None`` clears); the global
    slot is likewise a real null once merged, and both raise on ``main`` and
    here alike. The one slot that can't author a meaningful null is the
    FAMILY tier (``style.charts.line.marks.line.stroke: null``): it merges
    via the cascade-aware ``merge_patches``, where ``stroke``'s
    ``SkipInheritSlots(cascade=True)`` marker treats an unset OR
    explicitly-null family-tier value identically as "inherit the whole
    object from the parent tier" -- so there is nothing to regression-test
    at that one slot.
    """

    def test_chart_local_null_stroke_raises_on_line(self) -> None:
        chart_dict: dict[str, Any] = {
            "id": "t",
            "type": "line",
            "x": "x",
            "y": "y",
            "query": _QUERY,
            "query_name": "q",
            "style": {"marks": {"line": {"stroke": None}}},
        }
        chart = TypeAdapter(Chart).validate_python(chart_dict)
        data = [{"x": i, "y": float(i)} for i in range(20)]
        with pytest.raises(ValueError, match="stroke is None after cascade"):
            resolve(chart, data, chart_style_context=_BOARD_STYLE, width=600.0)

    def test_chart_local_null_stroke_raises_on_area(self) -> None:
        # Multi-series (color) keeps the chart on the overlap recipe, where
        # marks.line.stroke governs the edge directly. The stacked/
        # single-series recipe instead REPLACES marks.line.stroke wholesale
        # with marks.area.stacked.stroke, which would mask this null.
        chart_dict: dict[str, Any] = {
            "id": "t",
            "type": "area",
            "x": "x",
            "y": "y",
            "color": "series",
            "query": _QUERY,
            "query_name": "q",
            "style": {"marks": {"line": {"stroke": None}}},
        }
        chart = TypeAdapter(Chart).validate_python(chart_dict)
        data = [{"x": i, "y": float(i), "series": "A"} for i in range(20)] + [
            {"x": i, "y": float(i) * 2, "series": "B"} for i in range(20)
        ]
        with pytest.raises(ValueError, match="stroke is None after cascade"):
            resolve(chart, data, chart_style_context=_BOARD_STYLE, width=600.0)

    def test_global_tier_null_stroke_raises_on_line(self) -> None:
        """The GLOBAL slot (``style.charts.marks.line.stroke``, not the
        family-scoped ``style.charts.line.marks.line.stroke``) genuinely
        nulls the cascade -- unlike the family tier, it has no parent object
        to inherit from instead."""
        board_style = _board_style_with_patch({"marks": {"line": {"stroke": None}}})
        chart_dict: dict[str, Any] = {
            "id": "t",
            "type": "line",
            "x": "x",
            "y": "y",
            "query": _QUERY,
            "query_name": "q",
        }
        chart = TypeAdapter(Chart).validate_python(chart_dict)
        data = [{"x": i, "y": float(i)} for i in range(20)]
        with pytest.raises(ValueError, match="stroke is None after cascade"):
            resolve(chart, data, chart_style_context=board_style, width=600.0)


class TestStreamgraphStrokeCap:
    """Stacked/streamgraph edges are band separators: never thicker, can thin."""

    @staticmethod
    def _stream_stroke(n_cats: int, width: float, stack: str | None) -> float:
        # Categorical x so stacked/streamgraph validation accepts it (a numeric
        # x with high cardinality is rejected as continuous). Two series stacked.
        chart_dict: dict[str, Any] = {
            "id": "t",
            "type": "area",
            "x": "x",
            "y": "y",
            "color": "series",
            "query": _QUERY,
            "query_name": "q",
        }
        if stack is not None:
            chart_dict["style"] = {"stack": stack}
        chart = TypeAdapter(Chart).validate_python(chart_dict)
        data = [
            {"x": f"c{i}", "y": float(i + 1), "series": s}
            for s in ("A", "B")
            for i in range(n_cats)
        ]
        resolved = resolve(chart, data, chart_style_context=_BOARD_STYLE, width=width)
        return resolved.style.line_mark.stroke.width

    def test_streamgraph_edge_not_thickened(self) -> None:
        """The sparse-end rule thickens an overlap (trend-line) edge toward the
        ceiling; the streamgraph edge is capped at its fallback, so it never
        picks up that weight — the fix for the thick-separator report."""
        sparse_overlap = self._stream_stroke(5, 600.0, stack="none")
        sparse_stream = self._stream_stroke(5, 600.0, stack="center")
        assert sparse_stream < sparse_overlap

    def test_streamgraph_edge_holds_at_thin_fallback(self) -> None:
        """The stacked-area edge default is already thinner than the adaptive
        floor, so capping holds it at that thin separator weight regardless of
        density — never thickened, and it does not ride the density curve the way
        an unstacked trend edge does."""
        stream_sparse = self._stream_stroke(5, 600.0, stack="center")
        stream_dense = self._stream_stroke(300, 600.0, stack="center")
        assert stream_sparse == stream_dense

    def test_streamgraph_edge_caps_at_stacked_fallback_width_constant(self) -> None:
        """The cap value itself is chart_rendering.stroke.stacked_fallback_width:
        the pre-bake cap comparison in area.py must source this engine
        constant, not the (now-deleted) marks.area.stacked.stroke.width theme
        literal, or an unpinned dense streamgraph would ride the uncapped
        adaptive curve (1.5-4.0px) instead of staying at this thin separator
        weight."""
        stream_dense = self._stream_stroke(300, 600.0, stack="center")
        assert stream_dense == get_chart_rendering().stroke.stacked_fallback_width


# ---------------------------------------------------------------------------
# Resolve-level tests: faceted (multiples) area chart
# ---------------------------------------------------------------------------


class TestFacetedAreaChartAdaptiveStroke:
    def test_faceted_uses_per_panel_width(self) -> None:
        """Faceted area stroke is computed from per-panel width, not full card width.

        With 2 panels and the same n x-values in each panel, the buggy path uses
        the full card width → same px/pt as a non-faceted chart → same stroke.
        The fixed path uses the per-panel width (≈ half the card width) → smaller
        px/pt → strictly thinner stroke.  50 points at 600px stays well below the
        4.0 ceiling so the two paths produce distinct values.

        Colored (2-series) so the chart stays on the overlap recipe -- a
        colorless area is now capped at the stacked recipe's fallback width
        regardless of density, which would hide the per-panel-width effect
        this test targets.
        """
        n = 50
        card_width = 600.0

        # Non-faceted reference: full card_width / n → some stroke value
        stroke_nonfacet = _resolved_area_stroke(n, width=card_width, color="series")

        # Faceted (2 panels, same n x-values per panel)
        chart, data, w = _area_chart(
            n, width=card_width, color="series", multiples_columns="facet"
        )
        resolved = resolve(chart, data, chart_style_context=_BOARD_STYLE, width=w)
        stroke_facet = resolved.style.line_mark.stroke.width

        # Per-panel width < full card width → smaller px/pt → strictly thinner stroke.
        # A bug that uses the full card width gives the same stroke as the non-faceted
        # path; the fix shrinks it to the per-panel value.
        assert stroke_facet < stroke_nonfacet


# ---------------------------------------------------------------------------
# Resolve-level tests: faceted (multiples) line chart
# ---------------------------------------------------------------------------


class TestFacetedLineChartAdaptiveStroke:
    def test_faceted_uses_per_cell_width(self) -> None:
        """Faceted stroke is computed from per-cell width, not full card width."""
        n = 10
        card_width = 600.0

        # Non-faceted: uses full card_width / n → wider per-point → thicker stroke
        stroke_nonfacet = _resolved_line_stroke(n, width=card_width)

        # Faceted (2 panels): per-cell width is narrower → adaptive sees less px/pt
        chart, data, w = _line_chart(n, width=card_width, multiples_columns="facet")
        resolved_facet = resolve(chart, data, chart_style_context=_BOARD_STYLE, width=w)
        stroke_facet = resolved_facet.style.line_mark.stroke.width

        # Narrower panel → smaller px/pt → thinner or equal stroke
        assert stroke_facet <= stroke_nonfacet

    def test_faceted_stroke_matches_helper(self) -> None:
        """Faceted baked stroke agrees with facet_panel_width + adaptive_stroke."""
        n_dense = 10  # densest panel has 10 pts; other has 10 too (same data)
        card_width = 600.0

        chart, data, w = _line_chart(
            n_dense, width=card_width, multiples_columns="facet"
        )
        resolved_facet = resolve(chart, data, chart_style_context=_BOARD_STYLE, width=w)
        stroke_facet = resolved_facet.style.line_mark.stroke.width

        # 2 panels, no mirror (single column field, no mirror auto-set for columns-only
        # when chart has shared scale — but default is columns + shared scale → auto-mirror
        # may fire. Check the resolved ay.mirror to know has_mirror.)
        # We check against the formula for 2 panels, both mirror and no-mirror cases.
        panel_w_no_mirror = facet_panel_width(
            card_width, 2, has_mirror=False, extra_axis_px=0.0
        )
        panel_w_mirror = facet_panel_width(
            card_width, 2, has_mirror=True, extra_axis_px=0.0
        )
        expected_no_mirror = adaptive_stroke(
            panel_w_no_mirror / n_dense, _MIN_W, _MAX_W
        )
        expected_mirror = adaptive_stroke(panel_w_mirror / n_dense, _MIN_W, _MAX_W)

        # Baked stroke must equal one of the two, depending on mirror resolution
        assert stroke_facet in (expected_no_mirror, expected_mirror)


class TestBoardTierPointRingAuthoring:
    """A board-authored point ring width must survive the adaptive bake at
    every authoring tier."""

    _BOARD = """\
title: Board point ring
{style_block}queries:
  q1:
    columns: [day, visits, target]
    values:
      - ["2024-01-01", 120, 110]
      - ["2024-01-02", 145, 130]
      - ["2024-01-03", 132, 140]
      - ["2024-01-04", 178, 150]
      - ["2024-01-05", 165, 160]
      - ["2024-01-06", 190, 170]
      - ["2024-01-07", 172, 180]
charts:
  c1:
    query: q1
    type: line
    x: day
    y: visits
{layers_block}rows:
  - c1
"""

    _LAYERS = """\
    layers:
      - type: line
        y: target
        label: Target
"""

    _AUTHORED_RING = 8.0

    @staticmethod
    def _spec(style_block: str, layers_block: str = "") -> dict[str, Any]:
        """Compile a board from the template and render chart c1's VL spec."""
        return TestBoardTierPointRingAuthoring._spec_for_source(
            TestBoardTierPointRingAuthoring._BOARD.format(
                style_block=style_block, layers_block=layers_block
            )
        )

    @staticmethod
    def _spec_for_source(
        source: str, measures: tuple[str, str] = ("visits", "target")
    ) -> dict[str, Any]:
        """Render chart c1's VL spec from whole board source.

        ``measures`` names the board's two value columns.
        """
        result = compile_board(source)
        assert result.success, result.errors
        board = result.board
        chart = board.charts["c1"]
        first, second = measures
        data = [
            {"day": f"2024-01-0{i}", first: v, second: t}
            for i, (v, t) in enumerate(
                zip(
                    [120, 145, 132, 178, 165, 190, 172],
                    [110, 130, 140, 150, 160, 170, 180],
                    strict=True,
                ),
                start=1,
            )
        ]
        return generate_vega_lite_spec(
            chart,
            data,
            board_style=board.resolved_style,
            chart_style_context=board.chart_style_context,
            width=400,
        )

    @staticmethod
    def _visible_marks(spec: dict[str, Any], mark_type: str) -> list[dict[str, Any]]:
        """Every visible mark of a type, in spec order.

        A layered line chart nests one layer group per series, so the marks are
        not all at ``spec["layer"][*]`` -- walk to them. Zero-opacity marks are
        the invisible hover targets, never a rendered mark.
        """
        found: list[dict[str, Any]] = []

        def _collect(node: Any) -> None:
            if isinstance(node, dict):
                mark = node.get("mark")
                if (
                    isinstance(mark, dict)
                    and mark.get("type") == mark_type
                    and mark.get("opacity", 1) != 0
                ):
                    found.append(mark)
                for value in node.values():
                    _collect(value)
            elif isinstance(node, list):
                for value in node:
                    _collect(value)

        _collect(spec)
        assert found, spec
        return found

    @staticmethod
    def _ring_widths(spec: dict[str, Any]) -> list[float | None]:
        """Every visible point mark's ``strokeWidth``, in spec order.

        A layered line chart emits one halo point mark for the chart, ahead of
        the per-series rings and carrying no ``strokeWidth`` of its own -- that
        is the single leading ``None`` in the expected lists below.
        """
        return [
            mark.get("strokeWidth")
            for mark in TestBoardTierPointRingAuthoring._visible_marks(spec, "point")
        ]

    def test_global_marks_tier_ring_width_survives_the_bake(self) -> None:
        spec = self._spec(
            "style:\n"
            "  charts:\n"
            "    marks:\n"
            "      point:\n"
            f"        stroke_width: {self._AUTHORED_RING}\n"
        )
        assert self._ring_widths(spec) == [None, self._AUTHORED_RING]

    def test_family_tier_ring_width_survives_the_bake(self) -> None:
        spec = self._spec(
            "style:\n"
            "  charts:\n"
            "    line:\n"
            "      marks:\n"
            "        point:\n"
            f"          stroke_width: {self._AUTHORED_RING}\n"
        )
        assert self._ring_widths(spec) == [None, self._AUTHORED_RING]

    def test_overlay_layer_ring_width_survives_the_bake(self) -> None:
        """The line-layer call site in _layers.py, not just the base series."""
        spec = self._spec(
            "style:\n"
            "  charts:\n"
            "    marks:\n"
            "      point:\n"
            f"        stroke_width: {self._AUTHORED_RING}\n",
            layers_block=self._LAYERS,
        )
        # Base series + overlay, both at the authored width; leading halo ring
        # carries no strokeWidth of its own.
        assert self._ring_widths(spec) == [
            None,
            self._AUTHORED_RING,
            self._AUTHORED_RING,
        ]

    def test_unauthored_ring_still_tracks_the_baked_line_stroke(self) -> None:
        """The bake must still fire when no tier above the theme authored a
        ring — otherwise this fix would freeze every chart at the theme literal.

        Asserted against the line stroke in the same spec, not against the theme
        literal: "ring tracks the line" is the actual contract, and it stays
        true however ``chart_rendering.stroke`` is retuned.
        """
        spec = self._spec("")
        baked_line = self._visible_marks(spec, "line")[-1].get("strokeWidth")
        assert baked_line is not None
        assert self._ring_widths(spec)[-1] == baked_line

    def test_chart_root_ring_pin_reaches_its_overlay_layers(self) -> None:
        """One chart must not render two ring weights.

        A pin on the chart's own ``style:`` block reaches the base series
        through the cascade; the overlay layer inherits the same cascaded mark
        (``base_family_style`` is the chart-local family style), so it must
        land at the same width rather than keeping the baked stroke.
        """
        board = self._BOARD.format(
            style_block="",
            layers_block=self._LAYERS,
        ).replace(
            "    y: visits\n",
            "    y: visits\n"
            "    style:\n"
            "      marks:\n"
            "        point:\n"
            f"          stroke_width: {self._AUTHORED_RING}\n",
        )
        assert self._ring_widths(self._spec_for_source(board)) == [
            None,
            self._AUTHORED_RING,
            self._AUTHORED_RING,
        ]


class TestUnbakedLineOverlayRing:
    """A line layer on a non-line base has no density signal at all.

    ``bar.py`` / ``scatter.py`` / ``area.py`` pass ``line_px_per_point=0.0``
    into ``_resolve_layer_list``, so the size half of the companion derivation
    has no input. The ring half must still fire: without it the ring falls
    through to Vega-Lite's hardcoded 2px, a value no theme can name and no
    author can move.
    """

    _BOARD = """\
title: Combo ring
queries:
  q1:
    columns: [day, actual, target]
    values:
      - ["2024-01-01", 120, 110]
      - ["2024-01-02", 145, 130]
      - ["2024-01-03", 132, 140]
charts:
  c1:
    query: q1
    type: bar
    x: day
    y: actual
    layers:
      - type: line
        y: target
        label: Target
        style:
          marks:
            line:
              stroke:
                width: {stroke}
            point:
              size: 48
rows:
  - c1
"""

    @staticmethod
    def _rings(source: str) -> list[float | None]:
        return TestBoardTierPointRingAuthoring._ring_widths(
            TestBoardTierPointRingAuthoring._spec_for_source(
                source, measures=("actual", "target")
            )
        )

    @pytest.mark.parametrize("stroke", [2.0, 4.0])
    def test_ring_tracks_the_layer_stroke_without_a_density_signal(
        self, stroke: float
    ) -> None:
        """Parametrized because one case cannot tell a mechanism from a
        coincidence: VL's own point default is 2, so a lone ``stroke: 2`` case
        passes just as well with no ring logic at all."""
        rings = self._rings(self._BOARD.format(stroke=stroke))
        assert stroke in rings, rings

    def test_layer_authored_ring_still_wins(self) -> None:
        """The tracking must not overwrite an authored ring on this path."""
        rings = self._rings(
            self._BOARD.format(stroke=4.0).replace(
                "              size: 48\n",
                "              size: 48\n              stroke_width: 1.5\n",
            )
        )
        assert 1.5 in rings and 4.0 not in rings, rings

    def test_no_density_signal_leaves_size_alone(self) -> None:
        """The size half must be skipped, not zeroed."""
        source = self._BOARD.format(stroke=4.0).replace("              size: 48\n", "")
        result = compile_board(source)
        assert result.success, result.errors
        data = [
            {"day": f"2024-01-0{i}", "actual": a, "target": t}
            for i, (a, t) in enumerate(
                zip([120, 145, 132], [110, 130, 140], strict=True), start=1
            )
        ]
        resolved = resolve(
            result.board.charts["c1"],
            data,
            chart_style_context=result.board.chart_style_context,
            width=400,
        )
        assert resolved.layers[0].point_mark.size is None
