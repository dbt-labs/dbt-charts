"""Heatmap family resolved style slice."""

from __future__ import annotations

from pydantic import ConfigDict, Field

from dbt_charts.core.compile.models.primitives import ResolvedScaleTarget
from dbt_charts.core.compile.models.style.theme import RectMarkStyle
from dbt_charts.core.compile.models.style.theme.heatmap import HeatmapConditionalRule

from ._cartesian import _CartesianResolvedStyle


class ResolvedHeatmapStyle(_CartesianResolvedStyle):
    """Heatmap family style slice — self-contained, globals merged down.

    Inherits axis_x, axis_y, tooltip_format from _CartesianResolvedStyle.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    color_gradient: ResolvedScaleTarget | None = Field(
        default=None,
        description="Gradient scale config for the heatmap color encoding (palette/scheme + domain).",
    )
    single_series_fill: str | None = Field(
        description="The cell fill when no color channel is bound: the rect mark's authored color, else the palette's first color; null when the palette is empty.",
    )
    rect_mark: RectMarkStyle = Field(
        description="Cascade-merged rect mark geometry (opacity, stroke)."
    )
    conditional: dict[str, list[HeatmapConditionalRule]] | None = Field(
        default=None,
        description=(
            "The chart's per-cell rules keyed by column, color tokens resolved. "
            "The renderer evaluates them against each cell's row."
        ),
    )
    label_usable_ratio: float = Field(
        description="Usable-width ratio for the categorical-axis overlap heuristic."
    )


__all__ = ["ResolvedHeatmapStyle"]
