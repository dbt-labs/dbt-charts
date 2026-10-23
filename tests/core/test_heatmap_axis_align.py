"""Tests for heatmap.axis_y.labels.align: inward — the heatmap half of the
theme own-side-align switch (see dbt-charts/tests/core/test_bar_chart_style.py's
TestBarAxisXLabelAlignInward for the bar half).

Path 2's design: the switch lives on the existing per-family
``charts.heatmap.axis_y`` slot (Layer 4), not ``charts.axis_band`` (which would
set it on every family). ``inward``/``outward`` resolve
to concrete left/right at resolve time (see
dbt-charts/tests/core/compile/test_style_cascade.py's
TestOwnSideAlignEdgeMapping for the mapping itself) — the emitter only ever
sees the resolved left/right value.
"""

from __future__ import annotations

from dbt_charts.core.compile.config import get_theme_style
from dbt_charts.core.compile.merge import merge_onto_base
from dbt_charts.core.compile.models.chart.normalized import HeatmapChart
from dbt_charts.core.compile.models.query.normalized import SqlQuery
from dbt_charts.core.compile.models.style.authored import (
    HeatmapChartStylePatch,
)
from dbt_charts.core.compile.models.style.context import ChartStyleContext
from dbt_charts.core.compile.models.style.resolved import ResolvedStyle
from dbt_charts.core.compile.resolve import resolve
from dbt_charts.core.compile.resolve.style.board import resolve_style_and_context

_BOARD_RS, _BOARD_CTX = resolve_style_and_context(get_theme_style())

SAMPLE_DATA = [
    {"day": "Mon", "hour": "9am", "count": 5},
    {"day": "Tue", "hour": "10am", "count": 8},
]


def _heatmap_chart(**kwargs: object) -> HeatmapChart:
    return HeatmapChart(
        id="test_heatmap",
        query=SqlQuery(sql="SELECT 1", source="test"),
        query_name="q",
        type="heatmap",
        x="day",
        y="hour",
        color="count",
        **kwargs,
    )


def _board_with_heatmap_color_gradient(
    palette: str | list[str],
) -> tuple[ResolvedStyle, ChartStyleContext]:
    """Board-level style with charts.heatmap.color.gradient.palette set,
    built through the same patch-merge cascade a theme YAML author's value
    would go through — mirrors _board_with_heatmap_axis_y_label above."""

    compiled = get_theme_style()
    gradient_patch = HeatmapChartStylePatch.model_validate(
        {"color": {"gradient": {"palette": palette}}}
    )
    new_heatmap = merge_onto_base(compiled.charts.heatmap, gradient_patch)
    custom_charts = compiled.charts.model_copy(update={"heatmap": new_heatmap})
    return resolve_style_and_context(
        compiled.model_copy(update={"charts": custom_charts})
    )


def test_heatmap_chart_local_gradient_override_does_not_crash_on_named_theme_palette() -> (
    None
):
    """A board-level dbt charts named palette (baked resolved_stops) combined
    with a chart-local gradient override to a *different* palette shape
    (Vega scheme, or an inline stop list) must resolve cleanly.

    Regression: an earlier fix baked ``resolved_stops`` inside
    ``ScaleTargetConfig``'s own before-validator. ``merge_onto_base``
    re-validates the merged dict through that same validator on every cascade
    step; when the chart-local patch sets only ``palette``, the merged dict
    inherits the *board's* stale ``resolved_stops`` for the *old* palette,
    and the validator rejected the mismatch — crashing a legal board.
    ``bake_scale_target_stops`` sidesteps this by baking once, explicitly,
    after the cascade is fully resolved, with no intermediate window where a
    stale value could be inherited or rejected.
    """

    board_rs, board_ctx = _board_with_heatmap_color_gradient("dbt-seq-blue")

    scheme_style = HeatmapChartStylePatch.model_validate(
        {"color": {"gradient": {"palette": "viridis"}}}
    )
    scheme_chart = _heatmap_chart(style=scheme_style)
    scheme_resolved = resolve(scheme_chart, SAMPLE_DATA, chart_style_context=board_ctx)
    assert scheme_resolved.style.color_gradient is not None
    assert scheme_resolved.style.color_gradient.palette == "viridis"

    inline_style = HeatmapChartStylePatch.model_validate(
        {"color": {"gradient": {"palette": ["#ffffff", "#000000"]}}}
    )
    inline_chart = _heatmap_chart(style=inline_style)
    inline_resolved = resolve(inline_chart, SAMPLE_DATA, chart_style_context=board_ctx)
    assert inline_resolved.style.color_gradient is not None
    assert inline_resolved.style.color_gradient.palette == ["#ffffff", "#000000"]
