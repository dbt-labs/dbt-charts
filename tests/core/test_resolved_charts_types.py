"""Type-state tests for the ChartStyleContext / ResolvedStyle split.

ResolvedStyle is final board/chrome presentation only — it must not expose
compiler cascade state (sparse axis overlays, palette/role token bindings,
chart-local patch sentinels, the pre-inherit tree). That state lives on the
non-Resolved ChartStyleContext, produced alongside ResolvedStyle by the same
cascade and threaded separately to runtime chart resolution.
"""

from __future__ import annotations

import dataclasses
import typing
from typing import Any

from dbt_charts.core.compile.config import get_theme_style
from dbt_charts.core.compile.models.chart.normalized import KpiChart
from dbt_charts.core.compile.models.style.context import ChartStyleContext
from dbt_charts.core.compile.resolve import resolve
from dbt_charts.core.compile.resolve.style.board import (
    resolve_chart_style_context,
    resolve_style,
    resolve_style_and_context,
)


def test_resolve_style_and_resolve_chart_style_context_share_one_cascade():
    """A no-patch call to both getters for the same base shares one cache entry.

    resolve_style_and_context() caches on id(base); resolve_style() and
    resolve_chart_style_context() are thin getters over its two halves.
    Asserting the cache identity (not just an equal field) proves the two
    products came from the same cascade pass rather than two independent
    resolutions that happen to agree.
    """

    theme = get_theme_style()
    style = resolve_style(theme)
    context = resolve_chart_style_context(theme)
    paired_style, paired_context = resolve_style_and_context(theme)

    assert style is paired_style
    assert context is paired_context


def test_width_preferences_are_named_by_their_layout_contract():
    """Board width is a cap; chart widths are intrinsic preferences."""
    theme = get_theme_style()
    widths = {
        "preferred_width": 611.0,
        "kpi": theme.charts.kpi.model_copy(update={"preferred_width": 311.0}),
        "spark_bar": theme.charts.spark_bar.model_copy(
            update={"preferred_width": 211.0}
        ),
        "callout": theme.charts.callout.model_copy(update={"preferred_width": 321.0}),
        "table": theme.charts.table.model_copy(update={"preferred_width": 811.0}),
    }
    distinctive = theme.model_copy(
        update={
            "frame": theme.frame.model_copy(update={"width": 1237.0}),
            "charts": theme.charts.model_copy(update=widths),
        }
    )
    resolved_style = resolve_style(distinctive)

    context = resolve_chart_style_context(distinctive)

    assert resolved_style.frame.width == 1237.0
    assert context.preferred_width == 611.0
    assert context.kpi.preferred_width == 311.0
    assert context.spark_bar.preferred_width == 211.0
    assert context.callout.preferred_width == 321.0
    assert context.table.preferred_width == 811.0


def test_resolved_chart_style_carries_preferred_width():
    theme = get_theme_style()
    distinctive = theme.model_copy(
        update={
            "charts": theme.charts.model_copy(
                update={
                    "kpi": theme.charts.kpi.model_copy(
                        update={"preferred_width": 347.0}
                    )
                }
            )
        }
    )

    board_context = resolve_chart_style_context(distinctive)
    chart = KpiChart(id="test_kpi", type="kpi", label="Revenue", value="value")

    resolved = resolve(chart, [{"value": 42}], chart_style_context=board_context)

    assert resolved.style.kpi.preferred_width == 347.0


class TestMergedChartsKpiType:
    """kpi must be KpiStyle, not Any."""

    def test_kpi_is_compiled_kpi_style(self):
        from dbt_charts.core.compile.models.style.theme import KpiChartStyle

        hints = typing.get_type_hints(ChartStyleContext)
        assert hints["kpi"] is KpiChartStyle, (
            f"ChartStyleContext.kpi must be KpiChartStyle, got {hints['kpi']}"
        )

    def test_kpi_has_no_any_annotation(self):
        hints = typing.get_type_hints(ChartStyleContext)
        assert hints.get("kpi") is not Any, "kpi must not be typed as Any"

    def test_resolve_style_kpi_is_compiled_kpi(self):
        from dbt_charts.core.compile.models.style.theme import KpiChartStyle

        context = resolve_chart_style_context(get_theme_style())
        assert isinstance(context.kpi, KpiChartStyle)

    def test_kpi_preferred_width_from_compiled_type(self):

        context = resolve_chart_style_context(get_theme_style())
        assert (
            context.kpi.preferred_width is not None and context.kpi.preferred_width > 0
        )

    def test_kpi_border_radius_accessible_nested(self):
        """kpi.border.radius accessible via KpiStyle."""

        context = resolve_chart_style_context(get_theme_style())
        assert context.kpi.border.radius is not None and context.kpi.border.radius >= 0


class TestKpiRendererWithStyleType:
    """KPI renderer works after kpi: Any → KpiStyle."""

    def test_kpi_renders_without_crash(self):
        from ._engine_render import render_kpi_svg

        board_style = resolve_style(get_theme_style())
        board_context = resolve_chart_style_context(get_theme_style())
        data = [{"value": 42}]
        chart = KpiChart(id="test_kpi", type="kpi", label="Revenue", value="value")
        resolved = resolve(chart, data, chart_style_context=board_context)
        svg = render_kpi_svg(resolved, data, width=300, board_style=board_style)
        assert "<svg" in svg

    def test_kpi_value_font_weight_from_compiled(self):
        """The KpiValueStyle.font.weight reaches the rendered SVG.

        Override with a distinctive weight (700) and assert it round-trips —
        survives any default-value tweak. The chart-local KpiChartStyle
        override is applied via ``dataclasses.replace`` on the non-Resolved
        ``ChartStyleContext`` — legal because it carries no Resolved prefix.
        """
        from ._engine_render import render_kpi_svg

        board_style = resolve_style(get_theme_style())
        board_context = resolve_chart_style_context(get_theme_style())
        # Use model_copy to override only weight; size and family remain
        # cascade-filled so the renderer's non-None contract holds.
        cascaded_family = board_context.kpi.value.font.family
        kpi_override = board_context.kpi.model_copy(
            update={
                "value": board_context.kpi.value.model_copy(
                    update={
                        "font": board_context.kpi.value.font.model_copy(
                            update={"weight": 700, "family": cascaded_family}
                        )
                    }
                )
            }
        )
        modified_context = dataclasses.replace(board_context, kpi=kpi_override)
        data = [{"value": 42}]
        chart = KpiChart(id="test_kpi", type="kpi", label="Revenue", value="value")
        resolved = resolve(chart, data, chart_style_context=modified_context)
        svg = render_kpi_svg(resolved, data, width=300, board_style=board_style)
        assert 'font-weight="700"' in svg
