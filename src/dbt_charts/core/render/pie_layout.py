"""A pie's layout box, and the mode the size stage decides in it.

How much of a pie its card lets it draw (slice labels and a legend, a table of
the small slices, a table of all of them) depends on the box the wheel is laid
out in, so the decision is the size stage's, made once by the engine's
closed-form solve (``_engine.pie_layout``) and kept by every reader. A pie's
height is not imposed by the layout (``layout_sizing._FIXED_ASPECT_PIE_TYPES``):
its box is a pure function of its width and the board's style, which is what
this module computes.
"""

from __future__ import annotations

from typing import Any

from dbt_charts.core.compile.models.chart.resolved import ResolvedPieChart
from dbt_charts.core.compile.models.primitives import PieMode
from dbt_charts.core.compile.models.style.resolved import ResolvedStyle
from dbt_charts.core.render.chart.spec_builders import additive_padding
from dbt_charts.core.render.engine import decide_pie_mode
from dbt_charts.core.render.sizing import pie_content_height

__all__ = ["pie_box", "sized_pie_mode"]


def pie_box(
    chart: ResolvedPieChart, resolved_style: ResolvedStyle
) -> tuple[float, float, dict[str, float]]:
    """``(width, height, padding)`` of the frame the first render draws a pie in:
    the card's padding around the content height, as ``layout_sizing``'s static
    estimate sizes it."""
    padding = additive_padding(
        float(resolved_style.frame.card_padding), chart.layout_padding
    )
    content = pie_content_height(
        height=chart.height,
        is_donut=chart.is_donut,
        width=chart.resolution_width,
        chart_defaults=resolved_style.chart_defaults,
    )
    return (
        chart.resolution_width,
        content + padding["top"] + padding["bottom"],
        padding,
    )


def sized_pie_mode(
    chart: ResolvedPieChart,
    rows: list[dict[str, Any]],  # type-state: explicit_any — query rows
    resolved_style: ResolvedStyle,
) -> PieMode:
    """The mode the size stage decides for ``chart`` in its own box."""
    width, height, padding = pie_box(chart, resolved_style)
    return decide_pie_mode(
        chart,
        rows,
        width=width,
        height=height,
        padding=padding,
        board_style=resolved_style,
    )
