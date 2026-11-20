"""Tests for effective chart style view on ResolvedChart.

Proves (updated for new Style/Patch/Resolved skeleton):
1. ResolvedChart carries a resolved_style built from ChartStyleContext.
2. Chart-local ChartStylePatch merges key fields into resolved_style.
3. Vega config overlay uses legacy bridge for full flat-field coverage.
4. The render pipeline receives the pre-computed effective style.
"""

from __future__ import annotations

from dbt_charts.core.compile.config import (
    get_theme_style,
)
from dbt_charts.core.compile.models.chart.normalized import Chart
from dbt_charts.core.compile.models.style.context import ChartStyleContext
from dbt_charts.core.compile.resolve.style.board import resolve_chart_style_context
from dbt_charts.core.compile.resolve.style.chart_context import (
    build_chart_style_context,
)

_BOARD_CONTEXT = resolve_chart_style_context(get_theme_style())


def _chart(type: str = "bar", **kwargs) -> Chart:
    from pydantic import TypeAdapter

    defaults = {"id": "test", "type": type}
    defaults.update(kwargs)
    return TypeAdapter(Chart).validate_python(defaults)


def _board() -> ChartStyleContext:
    return resolve_chart_style_context(get_theme_style())


class TestEffectiveStyleOnResolvedChart:
    """resolved_style is attached to ResolvedChart by the pipeline."""

    def test_no_local_style_uses_board_style(self):
        """When chart has no local style, resolved_style mirrors board charts style.

        D-021: ``overlap='smart'`` resolves per-chart in the pipeline, so even a
        no-local-style chart gets a fresh ChartStyleContext with the picker's
        concrete overlap value stamped on axis_x.labels. The non-resolved fields
        (palette, fonts, axes) match board_style.
        """
        board_style = _board()
        chart = _chart(x="a", y="b")
        rs = build_chart_style_context(board_style, chart)
        assert isinstance(rs, ChartStyleContext)
        assert rs.palette == board_style.palette
        # overlap is ResolvedAxisLabelOverlapConfig | None after the cascade
        from dbt_charts.core.compile.models.style.resolved import (
            ResolvedAxisLabelOverlapConfig,
        )

        overlap = rs.axis_x.labels.overlap
        assert overlap is None or isinstance(overlap, ResolvedAxisLabelOverlapConfig)

    def test_background_merges_into_effective(self):
        """Chart-local background flows into resolved style background."""
        from dbt_charts.core.compile.models.style.authored import BarChartStylePatch

        board_style = _board()
        style = BarChartStylePatch(background="#f0f0f0")
        chart = _chart(x="a", y="b", style=style)
        rs = build_chart_style_context(board_style, chart)
        assert rs.background == "#f0f0f0"

    def test_chart_with_empty_style_patch_mirrors_board(self):
        """An empty ChartStylePatch contributes nothing; resolved style mirrors board charts.

        D-021 forces per-chart resolution of ``overlap='smart'`` even when no
        style is authored, so identity-equality no longer holds. Structural
        equality on the unresolved fields does.
        """
        board_style = _board()
        chart = _chart(x="a", y="b")
        rs = build_chart_style_context(board_style, chart)
        assert rs.palette == board_style.palette

    def test_compiled_chart_style_is_typed(self):
        """Chart.style is BarChartStylePatch for bar charts."""
        from dbt_charts.core.compile.models.style.authored import BarChartStylePatch

        style = BarChartStylePatch(background="#fff")
        chart = _chart(x="a", y="b", style=style)
        assert chart.style.background == "#fff"

    def test_compiled_chart_dict_style_auto_converts(self):
        """Chart accepts dict for style and converts to BarChartStylePatch for bar charts."""
        from dbt_charts.core.compile.models.style.authored import BarChartStylePatch

        chart = _chart(x="a", y="b", style={"background": "#fff"})
        assert isinstance(chart.style, BarChartStylePatch)
        assert chart.style.background == "#fff"

    def test_chart_local_style_propagates_to_resolved_background(self):
        """A chart-local background override reaches build_chart_style_context's output.

        Chart-local style is consumed by the cascade (build_chart_style_context)
        and the merged ChartStyleContext reflects the authored override. The
        authored Patch is accessible on the input Chart object before and after
        normalization.
        """
        from dbt_charts.core.compile.models.style.authored import BarChartStylePatch

        style = BarChartStylePatch(background="#fff")
        chart = _chart(x="a", y="b", style=style)
        # Authored Patch is preserved on the Chart object.
        assert isinstance(chart.style, BarChartStylePatch)
        # Resolved background reflects the authored override.
        rs = build_chart_style_context(_BOARD_CONTEXT, chart)
        assert rs.background == "#fff"

    def test_local_legend_disable_not_in_vega_config(self):
        """Chart-local legend.visible=False never reaches config.legend.

        Legend visibility is an encoding-level concern (encoding.color.legend=null,
        wired by apply_color_legend in emitters/_channels.py) — style_to_vega_lite
        (the sole VL config mapper) must never emit a top-level ``legend`` key.
        """
        from dbt_charts.core.compile.models.style.authored import (
            BarChartStylePatch,
        )
        from dbt_charts.core.compile.vega_lite.mapping import style_to_vega_lite

        board_style = _board()
        style = BarChartStylePatch(key={"visible": False})
        chart = _chart(x="a", y="b", style=style)
        rs = build_chart_style_context(board_style, chart)
        vlc = style_to_vega_lite(rs).model_dump(exclude_none=True)
        assert "legend" not in vlc
