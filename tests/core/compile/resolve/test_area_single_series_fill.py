"""TDD: a single-series unstacked area takes the FULL stacked recipe --
solid ``opacity``, the background-color separator ``stroke``, and
``halo_multiplier: 0.0`` -- exactly what a real stacked chart gets. A lone
band has nothing to overlap and nothing for a trend line to sit on top of,
so treating it like a stack (not an overlap-with-a-halo) is the honest
reading: the line no longer overstates the band's height.
"""

from __future__ import annotations

from typing import get_args

import pytest

from dbt_charts.core.compile.config import (
    get_chart_rendering,
    get_default_theme_name,
    get_theme_style,
)
from dbt_charts.core.compile.errors import CompilationError
from dbt_charts.core.compile.models.chart.normalized import AreaChart
from dbt_charts.core.compile.models.chart.resolved.area import ResolvedAreaChart
from dbt_charts.core.compile.models.schema_names import ThemeName
from dbt_charts.core.compile.models.style.authored import AreaChartStylePatch
from dbt_charts.core.compile.models.style.context import ChartStyleContext
from dbt_charts.core.compile.resolve import resolve
from dbt_charts.core.compile.resolve.style.board import resolve_chart_style_context

_DATA: list[dict] = [
    {"month": "Jan", "revenue": 100, "costs": 80, "segment": "consumer"},
    {"month": "Feb", "revenue": 120, "costs": 90, "segment": "enterprise"},
    {"month": "Mar", "revenue": 90, "costs": 70, "segment": "consumer"},
]


def _board() -> ChartStyleContext:
    return resolve_chart_style_context(get_theme_style(get_default_theme_name()))


def _resolve_area(overrides: dict) -> ResolvedAreaChart:
    chart = AreaChart.model_validate(
        {
            "id": "area1",
            "type": "area",
            "x": "month",
            "y": "revenue",
            "query_name": "q",
            **overrides,
        }
    )
    resolved = resolve(chart, _DATA, _board())
    assert isinstance(resolved, ResolvedAreaChart)
    return resolved


def test_single_series_area_gets_solid_opacity_matching_stacked_recipe():
    """A colorless, single-metric, layerless area resolves opacity to the
    stacked recipe's value — there is nothing to overlap with at one series."""
    single_series = _resolve_area({})
    stacked = _resolve_area({"stack": "zero"})

    assert single_series.style.area_mark.opacity == stacked.style.area_mark.opacity


def test_single_series_area_line_mark_matches_stacked_recipe():
    """The top-edge line takes the stacked recipe's stroke/halo, not the
    overlap recipe's -- a single series is now treated like a stack, so its
    edge is a background-color separator, not a haloed trend line."""
    single_series = _resolve_area({})
    stacked = _resolve_area({"stack": "zero"})

    assert (
        single_series.style.line_mark.halo_multiplier
        == _board().area.marks.area.stacked.halo_multiplier
    )
    assert (
        single_series.style.line_mark.halo_multiplier
        == stacked.style.line_mark.halo_multiplier
    )
    assert single_series.style.line_mark.stroke == stacked.style.line_mark.stroke


def test_single_series_area_line_mark_differs_from_overlap_recipe():
    """A multi-series overlap chart keeps its halo/trend-line recipe -- the
    single-series swap must not leak onto the chart it was carved out of."""
    single_series = _resolve_area({})
    multi_series_overlap = _resolve_area({"color": "segment"})

    assert (
        single_series.style.line_mark.halo_multiplier
        != multi_series_overlap.style.line_mark.halo_multiplier
    )


def test_single_series_area_edge_stroke_width_capped_at_theme_fallback():
    """Regression for the width-cap gate: the density-adaptive formula's
    floor (``chart_rendering.stroke.min_width``, 1.5px) always exceeds the
    stacked recipe's background-color separator width
    (``chart_rendering.stroke.stacked_fallback_width``, 1.0px), so an
    uncapped bake would knock visible pixels off the top of the band on
    every single-series area. Assert against the engine config constant --
    the theme no longer carries a literal here (any tier, theme included,
    may now leave it unset and let this constant apply)."""
    stacked_fallback_width = get_chart_rendering().stroke.stacked_fallback_width

    single_series = _resolve_area({})

    assert single_series.style.line_mark.stroke is not None
    assert single_series.style.line_mark.stroke.width == stacked_fallback_width


def test_single_series_area_resolved_stack_not_mutated():
    """resolved_stack stays 'none'/None for a single-series area -- the fix
    is recipe selection only, not coercing stack mode (rejected option B:
    that would also move the y-domain/tick path)."""
    single_series = _resolve_area({})

    assert single_series.stack in (None, "none")


def test_multi_series_color_area_stays_translucent():
    """A color-encoded area (2+ series) keeps the translucent overlap
    opacity -- the signal survives where it means something."""
    multi_series = _resolve_area({"color": "segment"})
    stacked = _resolve_area({"color": "segment", "stack": "zero"})

    assert multi_series.style.area_mark.opacity != stacked.style.area_mark.opacity


def test_multi_series_wide_y_area_stays_translucent():
    """Wide-y (y: [a, b]) is multi-series even with no color encoding --
    the trap: 'no color' must not be read as 'one series'."""
    wide = _resolve_area({"y": ["revenue", "costs"]})
    stacked = _resolve_area({"y": ["revenue", "costs"], "stack": "zero"})

    assert wide.style.area_mark.opacity != stacked.style.area_mark.opacity


def test_single_series_area_with_layers_stays_translucent():
    """A single-series base chart carrying `layers:` is multi-series in
    disguise (_effective_single_series_fill's own rule) -- it keeps the
    translucent overlap opacity."""
    layered = _resolve_area(
        {"layers": [{"type": "line", "y": "costs"}]},
    )
    stacked = _resolve_area({"stack": "zero"})

    assert layered.style.area_mark.opacity != stacked.style.area_mark.opacity


def test_single_element_wide_y_area_matches_scalar_y_spelling():
    """y: ["revenue"] draws one band, so it must take the same fill opacity
    as the scalar y: "revenue". The two spellings still differ elsewhere --
    a one-element list routes through the folded emitter, so it takes
    categorical ink and an endpoint rail the scalar lacks. Opacity is the
    only thing this pins."""
    scalar_y = _resolve_area({})
    single_element_wide_y = _resolve_area({"y": ["revenue"]})
    stacked = _resolve_area({"stack": "zero"})

    assert (
        single_element_wide_y.style.area_mark.opacity
        == scalar_y.style.area_mark.opacity
        == stacked.style.area_mark.opacity
    )


def test_single_series_area_authored_opacity_is_overridden_by_stacked_recipe():
    """An authored `marks.area.opacity` on a single-series area is discarded
    in favor of the stacked recipe's opacity. Pinned against the resolved
    theme's own stacked opacity, not a literal, per the
    don't-pin-theme-values rule."""
    board = _board()
    stacked_recipe_opacity = board.area.marks.area.stacked.opacity
    assert stacked_recipe_opacity is not None

    authored = _resolve_area(
        {
            "style": AreaChartStylePatch.model_validate(
                {"marks": {"area": {"opacity": 0.3}}}
            )
        }
    )

    assert authored.style.area_mark.opacity == stacked_recipe_opacity
    assert authored.style.area_mark.opacity != 0.3


def test_single_series_area_authored_line_stroke_is_overridden_by_stacked_recipe():
    """An authored `marks.line.stroke` on a single-series area is discarded in
    favor of the stacked recipe's separator stroke, the same way its authored
    `marks.area.opacity` is. Its escape hatch, `marks.area.stacked.stroke`, is
    pinned by the next test."""
    authored_color = "#cc0000"
    stacked = _resolve_area({"stack": "zero"})

    authored = _resolve_area(
        {
            "style": AreaChartStylePatch.model_validate(
                {"marks": {"line": {"stroke": {"color": authored_color}}}}
            )
        }
    )

    assert authored.style.line_mark.stroke is not None
    assert authored.style.line_mark.stroke == stacked.style.line_mark.stroke
    assert authored.style.line_mark.stroke.color != authored_color


def test_single_series_area_authored_stacked_stroke_width_survives_above_adaptive_ceiling():
    """Regression: a single-series area's authored
    ``marks.area.stacked.stroke.width`` is honored rather than getting
    silently capped by the adaptive formula. Pin a width comfortably above
    any adaptive value this density could produce."""
    pinned_width = get_chart_rendering().stroke.stacked_fallback_width + 50.0

    authored = _resolve_area(
        {
            "style": AreaChartStylePatch.model_validate(
                {
                    "marks": {
                        "area": {
                            "stacked": {
                                "stroke": {
                                    "width": pinned_width,
                                    "cap": "butt",
                                    "join": "round",
                                }
                            }
                        }
                    }
                }
            )
        }
    )

    assert authored.style.line_mark.stroke is not None
    assert authored.style.line_mark.stroke.width == pinned_width


def test_area_stacked_mark_style_cleared_to_null_raises_actionable_error():
    """`marks.area.stacked: null` explicitly clears the inherited stacked
    recipe, which stacked and single-series area charts both need, so it
    must raise a diagnosed, author-facing error."""
    with pytest.raises(CompilationError) as exc_info:
        _resolve_area(
            {
                "style": AreaChartStylePatch.model_validate(
                    {"marks": {"area": {"stacked": None}}}
                )
            }
        )

    assert exc_info.value.code is not None
    assert exc_info.value.code.code == "ERR-AREA-STACKED-MARK-STYLE-CLEARED"
    assert "area1" in str(exc_info.value)


def test_area_stacked_stroke_missing_cap_raises_actionable_error():
    """`marks.area.stacked.stroke` replaces `marks.line.stroke` wholesale for
    stacked and single-series charts, so an authored `cap: null` would drop
    the edge's cap instead of inheriting one. That is now reachable from the
    default chart shape, so it must raise a diagnosed, author-facing error."""
    with pytest.raises(CompilationError) as exc_info:
        _resolve_area(
            {
                "style": AreaChartStylePatch.model_validate(
                    {"marks": {"area": {"stacked": {"stroke": {"cap": None}}}}}
                )
            }
        )

    assert exc_info.value.code is not None
    assert exc_info.value.code.code == "ERR-AREA-STACKED-STROKE-INCOMPLETE"
    assert "area1" in str(exc_info.value)


@pytest.mark.parametrize("theme_name", sorted(get_args(ThemeName)))
def test_every_theme_keeps_the_stacked_recipe_halo_off(theme_name: str):
    """A single-series area takes the stacked recipe but keeps the halo
    composition, whose halo gate is ``halo_multiplier != 0``, so the recipe's
    ``halo_multiplier`` is the only thing suppressing the halo on the default
    area chart. Zero is the contract here, not a tunable, so every
    author-selectable theme must resolve it to exactly 0."""
    context = resolve_chart_style_context(get_theme_style(theme_name))
    stacked = context.area.marks.area.stacked
    assert stacked is not None
    assert stacked.halo_multiplier == 0
