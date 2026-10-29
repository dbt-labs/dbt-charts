"""BoardRenderSession — board-level coordinator for a render pass.

Holds the shared board style and sends each chart to the Rust engine
(``render_svg_family``); every family, geo maps included, is drawn there.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from dbt_charts.core.compile.models.board.normalized import (
    NO_TITLE_SHIFT,
    TitleShift,
)
from dbt_charts.core.compile.models.chart.resolved import (
    ResolvedAreaChart,
    ResolvedBarChart,
    ResolvedCalloutChart,
    ResolvedChart,
    ResolvedCustomChart,
    ResolvedGeoshapeChart,
    ResolvedHeatmapChart,
    ResolvedKpiChart,
    ResolvedLineChart,
    ResolvedPieChart,
    ResolvedPointMapChart,
    ResolvedScatterChart,
    ResolvedSparkBarChart,
    ResolvedTableChart,
)
from dbt_charts.core.compile.models.primitives import PieMode
from dbt_charts.core.compile.models.style.resolved import ResolvedStyle
from dbt_charts.core.render.engine import render_native
from dbt_charts.core.render.pie_layout import pie_box
from dbt_charts.core.render.sizing import custom_chart_height
from dbt_charts.core.utils import Rows


@dataclass
class BoardRenderSession:
    """Per-board context for a render-v2 pass.

    Args:
        board_style: Fully resolved board-level style shared across all charts.
    """

    board_style: ResolvedStyle

    def render_svg_family(
        self,
        chart: ResolvedChart,
        data: list[dict[str, Any]],
        *,
        width: float,
        height: float | None = None,
        is_placeholder: bool = False,
        inset: dict[str, int | float] | None = None,
        padding: dict[str, int | float] | None = None,
        title_shift: TitleShift = NO_TITLE_SHIFT,
        datasets: dict[str | None, Rows] | None = None,
        measuring: bool = False,
        authored_height: float | None = None,
        pie_mode: PieMode | None = None,
    ) -> str:
        """The chart's SVG, drawn by the engine.

        ``inset`` is the padding the caller shrank ``width``/``height`` by; a
        family that paints a card rect grows it back out over that padding.
        ``padding`` is the padding a custom chart frames itself inside at the
        full ``width``/``height``, as a Vega-Lite chart does.
        """
        match chart:
            case ResolvedKpiChart() | ResolvedSparkBarChart():
                # No slot height is the card's own: a KPI's `default_height`, a
                # spark_bar's natural one (the engine's `0.0`).
                if height:
                    slot_height = height
                elif isinstance(chart, ResolvedKpiChart):
                    kpi_style = chart.style.kpi
                    assert kpi_style is not None, "resolve() must populate style.kpi"
                    slot_height = kpi_style.default_height
                else:
                    slot_height = 0.0
                svg, _ = render_native(
                    chart,
                    data,
                    width=width,
                    height=slot_height,
                    padding=inset,
                    board_style=self.board_style,
                    board_width=None,
                    title_shift=title_shift,
                    is_placeholder=is_placeholder,
                )
                return svg
            case ResolvedTableChart():
                return render_native(
                    chart,
                    data,
                    width=width,
                    height=height
                    or 0.0,  # type-state: silent_fallback — 0 asks the engine for natural height
                    padding=inset,
                    board_style=self.board_style,
                    title_shift=title_shift,
                )[0]
            case ResolvedCustomChart():
                # A missing slot height means "no slot": the chart's own
                # aspect-ratio box answers.
                svg, _ = render_native(
                    chart,
                    data,
                    width=width,
                    height=custom_chart_height(
                        chart.aspect_ratio,
                        chart.min_height,
                        chart.max_height,
                        width,
                        self.board_style.chart_defaults,
                    )
                    if height is None
                    else height,
                    padding=padding,
                    board_style=self.board_style,
                    board_width=width,
                    title_shift=title_shift,
                )
                return svg
            case ResolvedPieChart():
                # With no slot the pie draws in the box it was sized in, so the
                # mode it carries holds there.
                _, sized_height, sized_padding = pie_box(chart, self.board_style)
                svg, _ = render_native(
                    chart,
                    data,
                    width=width,
                    height=sized_height if height is None else height,
                    padding=sized_padding
                    if height is None and padding is None
                    else padding,
                    board_style=self.board_style,
                    title_shift=title_shift,
                    is_placeholder=is_placeholder,
                    datasets=datasets,
                    measuring=measuring,
                    authored_height=authored_height,
                    # The mode is the size stage's; a slot an author pinned
                    # smaller keeps it.
                    pie_mode=pie_mode,
                )
                return svg
            case (
                ResolvedGeoshapeChart()
                | ResolvedPointMapChart()
                | ResolvedBarChart()
                | ResolvedLineChart()
                | ResolvedAreaChart()
                | ResolvedScatterChart()
                | ResolvedHeatmapChart()
            ):
                svg, _ = render_native(
                    chart,
                    data,
                    width=width,
                    height=self.board_style.chart_defaults.view.continuous_height
                    if height is None
                    else height,  # type-state: silent_fallback — no slot is the theme's view height
                    padding=padding,
                    board_style=self.board_style,
                    title_shift=title_shift,
                    is_placeholder=is_placeholder,
                    datasets=datasets,
                    measuring=measuring,
                    authored_height=authored_height,
                )
                return svg
            case ResolvedCalloutChart():
                svg, _ = render_native(
                    chart,
                    data,
                    width=width,
                    height=0.0 if height is None else height,
                    padding=None,
                    board_style=self.board_style,
                    board_width=width,
                )
                return svg
