"""One `color` per y axis, baked at resolve into the slots the engine paints.

An axis `color` (a color, or `auto` for its series' ink) fills the axis's grid
lines, tick labels and title. A slot the chart authors on its own (`grid.color`,
`labels.font.color`, `title.font.color`) keeps its value. Grid lines keep the
theme grid's contrast against the canvas, so a colored ladder reads as quietly
as a gray one; an authored `grid.opacity` wins. The engine payload carries
colors only, never `auto`.
"""

from __future__ import annotations

from dbt_charts.core.colors import (
    ensure_readable_ink,
    opacity_for_contrast,
    wcag_contrast,
)
from dbt_charts.core.compile.errors import CompilationError
from dbt_charts.core.compile.models.chart.authored._layer import (
    LayerAxisYGrid,
    LayerAxisYStyle,
)
from dbt_charts.core.compile.models.markers import AUTO
from dbt_charts.core.compile.models.style.authored import AxisYStylePatch
from dbt_charts.core.compile.models.style.context import ChartStyleContext
from dbt_charts.core.compile.models.style.theme import AxisYStyle
from dbt_charts.core.compile.resolve.chart._layer_ink import LayerInk
from dbt_charts.core.compile.resolve.style.inherit_resolver import settled
from dbt_charts.core.diagnostics.codes_compile import ERR_AXIS_COLOR_AUTO_NO_INK

__all__ = [
    "bake_axis_color",
    "base_axis_ink",
    "layer_axis_color",
]


def _ink_or_raise(ink: str | None, field_path: str, reason: str) -> str:
    if ink is None:
        raise CompilationError.from_code(
            ERR_AXIS_COLOR_AUTO_NO_INK, field_path=field_path, reason=reason
        )
    return ink


def base_axis_ink(
    chart_type: str,
    colored_by_field: bool,
    several_measures: bool,
    layers_share_axis: bool,
    series_ink: str,
) -> tuple[str | None, str]:
    """The one ink the base chart's y axis holds, and why there is none if not."""
    if chart_type not in ("line", "bar", "area", "scatter"):
        return None, f"a {chart_type} chart draws no series color"
    if colored_by_field:
        return (
            None,
            "the chart colors its marks by a field, so the axis holds several series",
        )
    if several_measures:
        return None, "several measures share this axis"
    if layers_share_axis:
        return None, "the layers share this axis, so it holds several series"
    return series_ink, ""


def _grid_opacity(color: str, ctx: ChartStyleContext) -> float:
    """The opacity that lands ``color`` at the theme grid's canvas contrast."""
    theme_grid = settled(ctx.axis_y.grid.color or ctx.axis.grid.color)
    return opacity_for_contrast(
        color, ctx.ink_canvas, wcag_contrast(theme_grid, ctx.ink_canvas)
    )


def bake_axis_color(
    ay: AxisYStyle,
    authored: AxisYStylePatch | None,
    ink: tuple[str | None, str],
    chart_style_context: ChartStyleContext,
    chart_id: str,
) -> AxisYStyle:
    """``ay`` with its ``color`` painted into every slot the chart did not author."""
    if ay.color is None:
        return ay
    color = (
        _ink_or_raise(ink[0], f"charts.{chart_id}.style.axis_y.color", ink[1])
        if ay.color == AUTO
        else ay.color
    )
    text = ensure_readable_ink(color, chart_style_context.ink_canvas)
    grid = authored.grid if authored is not None else None
    labels = authored.labels if authored is not None else None
    title = authored.title if authored is not None else None
    own_grid = grid is not None and grid.color is not None
    own_opacity = grid is not None and grid.opacity is not None
    own_labels = (
        labels is not None
        and "font" in labels.model_fields_set
        and labels.font.color is not None
    )
    own_title = (
        title is not None
        and "font" in title.model_fields_set
        and title.font.color is not None
    )
    return ay.model_copy(
        update={
            "grid": ay.grid
            if own_grid
            else ay.grid.model_copy(
                update={
                    "color": color,
                    "opacity": ay.grid.opacity
                    if own_opacity
                    else _grid_opacity(color, chart_style_context),
                }
            ),
            "labels": ay.labels
            if own_labels
            else ay.labels.model_copy(
                update={"font": ay.labels.font.model_copy(update={"color": text})}
            ),
            "title": ay.title
            if own_title
            else ay.title.model_copy(
                update={"font": ay.title.font.model_copy(update={"color": text})}
            ),
        }
    )


def layer_axis_color(
    axis_y: LayerAxisYStyle,
    ink: LayerInk,
    chart_style_context: ChartStyleContext,
    field_path: str,
) -> tuple[LayerAxisYGrid | None, str | None]:
    """A layer axis's grid with its color baked, and the readable text color."""
    if axis_y.color is None:
        return axis_y.grid, None
    color = (
        _ink_or_raise(
            None if ink.gradient else ink.ink,
            f"{field_path}.axis_y.color",
            "the layer colors its marks by a field, so the axis holds several series",
        )
        if axis_y.color == AUTO
        else axis_y.color
    )
    grid = axis_y.grid or LayerAxisYGrid()
    if grid.color is None:
        grid = grid.model_copy(
            update={
                "color": color,
                "opacity": grid.opacity
                if grid.opacity is not None
                else _grid_opacity(color, chart_style_context),
            }
        )
    return grid, ensure_readable_ink(color, chart_style_context.ink_canvas)
