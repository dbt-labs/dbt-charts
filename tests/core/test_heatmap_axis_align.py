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

import pytest

from dbt_charts.core.compile.config import get_theme_style, reset_config
from dbt_charts.core.compile.merge import merge_onto_base
from dbt_charts.core.compile.models.chart.normalized import HeatmapChart
from dbt_charts.core.compile.models.primitives import FontStyle
from dbt_charts.core.compile.models.query.normalized import SqlQuery
from dbt_charts.core.compile.models.style.authored import (
    AxisLabelStylePatch,
    AxisYStylePatch,
    HeatmapChartStylePatch,
)
from dbt_charts.core.compile.models.style.context import ChartStyleContext
from dbt_charts.core.compile.models.style.resolved import ResolvedStyle
from dbt_charts.core.compile.resolve import resolve
from dbt_charts.core.compile.resolve.style.board import resolve_style_and_context
from dbt_charts.core.render.chart.vega_lite import generate_vega_lite_spec

_BOARD_RS, _BOARD_CTX = resolve_style_and_context(get_theme_style())

SAMPLE_DATA = [
    {"day": "Mon", "hour": "9am", "count": 5},
    {"day": "Tue", "hour": "10am", "count": 8},
]


@pytest.fixture(autouse=True)
def _reset():
    reset_config()
    yield
    reset_config()


def _board_with_heatmap_axis_y_label(
    **label_overrides: object,
) -> tuple[ResolvedStyle, ChartStyleContext]:
    """Return style pair with heatmap.axis_y.labels fields set to
    distinctive values — mirrors test_bar_chart_style.py's
    _board_with_bar_axis_x_label for the heatmap row axis."""
    compiled = get_theme_style()
    label_patch = AxisLabelStylePatch(**label_overrides)
    axis_y_patch = AxisYStylePatch(labels=label_patch)
    heatmap = compiled.charts.heatmap
    existing_axis_y = heatmap.axis_y
    new_axis_y = (
        merge_onto_base(existing_axis_y, axis_y_patch)
        if existing_axis_y is not None
        else axis_y_patch
    )
    custom_heatmap = heatmap.model_copy(update={"axis_y": new_axis_y})
    custom_charts = compiled.charts.model_copy(update={"heatmap": custom_heatmap})
    return resolve_style_and_context(
        compiled.model_copy(update={"charts": custom_charts})
    )


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


class TestHeatmapAxisYLabelAlignInward:
    def test_row_axis_own_side_align_from_inward(self) -> None:
        """align: inward resolves to heatmap's left-orient row axis's own
        side and reserves a measured gutter, same as authoring align="left"."""
        board_rs, board_ctx = _board_with_heatmap_axis_y_label(align="inward")
        chart = _heatmap_chart()
        resolve(chart, SAMPLE_DATA, chart_style_context=board_ctx)
        spec = generate_vega_lite_spec(
            chart, SAMPLE_DATA, board_style=board_rs, chart_style_context=board_ctx
        )
        y_axis = spec["encoding"]["y"]["axis"]
        assert y_axis.get("labelAlign") == "left"
        assert y_axis.get("labelPadding", 0) > 0

    def test_column_axis_untouched_by_row_axis_inward(self) -> None:
        """heatmap.axis_y.labels.align must never leak onto the column
        (axis_x, bottom-orient) axis — a different theme slot entirely, and
        one with no left/right edge regardless."""
        board_rs, board_ctx = _board_with_heatmap_axis_y_label(align="inward")
        chart = _heatmap_chart()
        resolve(chart, SAMPLE_DATA, chart_style_context=board_ctx)
        spec = generate_vega_lite_spec(
            chart, SAMPLE_DATA, board_style=board_rs, chart_style_context=board_ctx
        )
        x_axis = spec["encoding"]["x"]["axis"]
        assert x_axis.get("labelAlign") is None

    def test_authored_align_wins_over_inward(self) -> None:
        """An explicit chart-local style.axis_y.labels.align still wins over
        the theme's inward switch — chart-local (Layer 12) always wins over
        the theme's chart-type patch (Layer 4)."""
        board_rs, board_ctx = _board_with_heatmap_axis_y_label(align="inward")
        style = HeatmapChartStylePatch(
            axis_y=AxisYStylePatch(labels=AxisLabelStylePatch(align="right"))
        )
        chart = _heatmap_chart(style=style)
        resolve(chart, SAMPLE_DATA, chart_style_context=board_ctx)
        spec = generate_vega_lite_spec(
            chart, SAMPLE_DATA, board_style=board_rs, chart_style_context=board_ctx
        )
        y_axis = spec["encoding"]["y"]["axis"]
        assert y_axis.get("labelAlign") == "right"

    def test_stark_ships_no_align(self) -> None:
        """The neutral base theme leaves row labels away-from-plot (no own-side
        align), so stark/plain render unchanged by this feature."""
        stark_rs, stark_ctx = resolve_style_and_context(get_theme_style("stark"))
        chart = _heatmap_chart()
        resolve(chart, SAMPLE_DATA, chart_style_context=stark_ctx)
        spec = generate_vega_lite_spec(
            chart, SAMPLE_DATA, board_style=stark_rs, chart_style_context=stark_ctx
        )
        y_axis = spec["encoding"]["y"]["axis"]
        assert y_axis.get("labelAlign") is None

    def test_editorial_ships_align_inward(self) -> None:
        """The default (editorial) theme opts heatmap rows into own-side align."""
        chart = _heatmap_chart()
        resolve(chart, SAMPLE_DATA, chart_style_context=_BOARD_CTX)
        spec = generate_vega_lite_spec(
            chart, SAMPLE_DATA, board_style=_BOARD_RS, chart_style_context=_BOARD_CTX
        )
        y_axis = spec["encoding"]["y"]["axis"]
        assert y_axis.get("labelAlign") == "left"
        assert y_axis.get("labelPadding", 0) > 0

    def test_editorial_default_theme_empty_data_no_error(self) -> None:
        """Zero rows means no row labels to measure — own-side align must
        fall back to the away-from-plot default (no own-side align, no
        RenderError), not invade with nothing to size the gutter from.

        Regression: editorial's default axis_y.labels.align: inward turned a
        valid empty render into an error card on any zero-row heatmap.
        """
        chart = _heatmap_chart()
        resolve(chart, [], chart_style_context=_BOARD_CTX)
        spec = generate_vega_lite_spec(
            chart, [], board_style=_BOARD_RS, chart_style_context=_BOARD_CTX
        )
        y_axis = spec["encoding"]["y"]["axis"]
        assert y_axis.get("labelAlign") is None

    def test_editorial_default_theme_font_case_upper_no_error(self) -> None:
        """label.font.case: upper wraps the rendered text in a Vega labelExpr
        (inject_axis_label_case, run after measure_axis_to_vl returns) — a
        d3-format-derived (or here, raw-value) measurement taken before that
        wrap would silently mismeasure what actually renders. Under
        editorial's default axis_y.labels.align: inward, this must render
        cleanly (no RenderError), falling back to the away-from-plot default
        rather than raising.
        """
        style = HeatmapChartStylePatch(
            axis_y=AxisYStylePatch(
                labels=AxisLabelStylePatch(font=FontStyle(case="upper"))
            )
        )
        chart = _heatmap_chart(style=style)
        resolve(chart, SAMPLE_DATA, chart_style_context=_BOARD_CTX)
        spec = generate_vega_lite_spec(
            chart, SAMPLE_DATA, board_style=_BOARD_RS, chart_style_context=_BOARD_CTX
        )
        y_axis = spec["encoding"]["y"]["axis"]
        assert y_axis.get("labelAlign") is None

    def test_editorial_default_theme_label_expr_no_error(self) -> None:
        """An authored label.expr means VL renders that expression instead of
        the raw row value — measuring the raw value while a wider expr
        output actually renders would silently under-reserve the gutter, so
        own-side align must not invade here either."""
        style = HeatmapChartStylePatch(
            axis_y=AxisYStylePatch(
                labels=AxisLabelStylePatch(expr="upper(datum.label)")
            )
        )
        chart = _heatmap_chart(style=style)
        resolve(chart, SAMPLE_DATA, chart_style_context=_BOARD_CTX)
        spec = generate_vega_lite_spec(
            chart, SAMPLE_DATA, board_style=_BOARD_RS, chart_style_context=_BOARD_CTX
        )
        y_axis = spec["encoding"]["y"]["axis"]
        assert y_axis.get("labelAlign") is None


def _board_with_heatmap_color_gradient(
    palette: str | list[str],
) -> tuple[ResolvedStyle, ChartStyleContext]:
    """Board-level style with charts.heatmap.color.gradient.palette set,
    built through the same patch-merge cascade a theme YAML author's value
    would go through — mirrors _board_with_heatmap_axis_y_label above."""
    from dbt_charts.core.compile.models.style.authored import HeatmapChartStylePatch

    compiled = get_theme_style()
    gradient_patch = HeatmapChartStylePatch.model_validate(
        {"color": {"gradient": {"palette": palette}}}
    )
    new_heatmap = merge_onto_base(compiled.charts.heatmap, gradient_patch)
    custom_charts = compiled.charts.model_copy(update={"heatmap": new_heatmap})
    return resolve_style_and_context(
        compiled.model_copy(update={"charts": custom_charts})
    )


def test_heatmap_theme_gradient_dbt_charts_named_palette_resolves_to_stops() -> None:
    """A dbt charts named palette set at the theme level (charts.heatmap.color.
    gradient), not at chart-local style, reaches ``_resolve_heatmap`` via the
    fully cascaded ``HeatmapChartStyle`` — a different construction path than
    a chart-local channel scale. Regression: before ``bake_scale_target_stops``
    was called explicitly at this construction site, only the channel-scoped
    construction path baked resolved_stops, so this theme-cascade gradient
    reached render unresolved and crashed with ``ChartDataError``."""
    from dbt_charts.core.compile.resolve.style.palette import palette as resolve_palette

    board_rs, board_ctx = _board_with_heatmap_color_gradient("dbt-seq-blue")

    chart = _heatmap_chart()
    resolved = resolve(chart, SAMPLE_DATA, chart_style_context=board_ctx)
    assert resolved.style.color_gradient is not None
    assert resolved.style.color_gradient.resolved_stops == tuple(
        resolve_palette("dbt-seq-blue")
    )
    spec = generate_vega_lite_spec(
        chart, SAMPLE_DATA, board_style=board_rs, chart_style_context=board_ctx
    )
    assert spec["encoding"]["color"]["scale"]["range"] == resolve_palette(
        "dbt-seq-blue"
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
    from dbt_charts.core.compile.models.style.authored import HeatmapChartStylePatch

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
