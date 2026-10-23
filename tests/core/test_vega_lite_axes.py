"""Tests for Vega-Lite axis orientation and format application.

Split from test_vega_lite.py; axis and format-specific tests.
"""

from dbt_charts.core.compile.config import get_theme_style
from dbt_charts.core.compile.resolve.style.board import (
    resolve_chart_style_context,
)


class TestAxisYLabelAlignInvasion:
    """axis_y.labels.align must never leave labels clipped by growing back
    across the axis line with no reserved gutter.

    Vega-Lite's ``autosize: {type: "fit"}`` sizing estimate reserves gutter
    width for a left/right-oriented axis only when labels grow AWAY from the
    plot (the per-orient smart default VL applies when ``labelAlign`` is
    unset). Explicitly setting ``label.align`` to the axis's OWN side
    (``"right"`` on a right-orient axis, ``"left"`` on a left-orient axis)
    flips the text-anchor so labels grow back toward the plot instead.
    dbt charts computes an explicit ``labelPadding`` from the real (baked)
    tick values and font metrics, which the same vl-convert probe found
    widens the reserved gutter 1:1 regardless of align direction — so the
    combination renders correctly provided dbt charts has baked tick content
    to measure from. When it doesn't (no format, no baked ticks, an authored
    labelExpr, or an upper/lower font.case), own-side align falls back to
    the away-side default instead of reserving an unmeasured gutter — the
    render-time safety net for facts resolve can't see when it maps
    ``inward``/``outward`` to a concrete side (see the ``_falls_back`` tests
    below).
    """

    def test_normalize_stack_resolve_bakes_unit_domain(self):
        """_resolve_bar with stack='normalize' bakes domain=[0.0, 1.0] onto the axis.

        This test catches deletion of the normalize bake block in bar.py (the
        _norm_cont / _norm_scale block).  Without the bake, measure_axis_to_vl
        receives a None domain and estimates labelPadding from million-scale raw
        values instead of the rendered [0, 1] range.
        """
        from dbt_charts.core.compile.config import (
            get_default_theme_name,
            reset_config,
        )
        from dbt_charts.core.compile.models.chart.normalized.bar import BarChart
        from dbt_charts.core.compile.resolve.chart._chart_rows import partition
        from dbt_charts.core.compile.resolve.chart.bar import _resolve_bar

        reset_config()
        ctx = resolve_chart_style_context(get_theme_style(get_default_theme_name()))
        chart = BarChart(
            id="normalize_bake",
            type="bar",
            x="category",
            y="value",
            stack="normalize",
            color="series",
        )
        data = [
            {"category": "A", "series": "X", "value": 1_000_000},
            {"category": "A", "series": "Y", "value": 2_000_000},
            {"category": "B", "series": "X", "value": 3_000_000},
            {"category": "B", "series": "Y", "value": 4_000_000},
        ]
        resolved = _resolve_bar(
            chart, partition(None, data), ctx, 800.0, 800.0, {}, None, None
        )
        ay = resolved.style.axis_y
        # The bake sets domain=[0.0, 1.0] on the resolved axis scale.
        # Without the bake, ay.scale is None or ay.scale.continuous is None,
        # and measure_axis_to_vl estimates from raw million-scale values.
        assert ay.scale is not None, "normalize-stack must bake a scale onto the axis"
        assert ay.scale.continuous is not None, (
            "normalize-stack must bake a continuous scale domain"
        )
        assert ay.scale.continuous.domain == (0.0, 1.0), (
            f"normalize-stack domain must be [0.0, 1.0], got {ay.scale.continuous.domain!r}"
        )

    def test_normalize_stack_resolve_bakes_unit_domain_area(self):
        """_resolve_area with stack='normalize' bakes domain=[0.0, 1.0] onto the axis.

        Mirrors the bar version above. Without the bake in area.py, measure_axis_to_vl
        receives a None domain and estimates labelPadding from million-scale raw values
        instead of the rendered [0, 1] range.
        """
        from dbt_charts.core.compile.config import (
            get_default_theme_name,
            reset_config,
        )
        from dbt_charts.core.compile.models.chart.normalized.area import AreaChart
        from dbt_charts.core.compile.resolve.chart._chart_rows import partition
        from dbt_charts.core.compile.resolve.chart.area import _resolve_area

        reset_config()
        ctx = resolve_chart_style_context(get_theme_style(get_default_theme_name()))
        chart = AreaChart(
            id="normalize_bake_area",
            type="area",
            x="category",
            y="value",
            stack="normalize",
            color="series",
        )
        data = [
            {"category": "A", "series": "X", "value": 1_000_000},
            {"category": "A", "series": "Y", "value": 2_000_000},
            {"category": "B", "series": "X", "value": 3_000_000},
            {"category": "B", "series": "Y", "value": 4_000_000},
        ]
        resolved = _resolve_area(
            chart, partition(None, data), ctx, 800.0, 800.0, {}, None, None
        )
        ay = resolved.style.axis_y
        assert ay.scale is not None, "normalize-stack must bake a scale onto the axis"
        assert ay.scale.continuous is not None, (
            "normalize-stack must bake a continuous scale domain"
        )
        assert ay.scale.continuous.domain == (0.0, 1.0), (
            f"normalize-stack area domain must be [0.0, 1.0], got {ay.scale.continuous.domain!r}"
        )
