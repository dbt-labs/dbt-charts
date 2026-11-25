"""Heatmap family resolved style slice."""

from __future__ import annotations

from pydantic import ConfigDict, Field

from dbt_charts.core.compile.models.primitives import (
    ResolvedFormat,
    ResolvedScaleTarget,
)
from dbt_charts.core.compile.models.style.theme import RectMarkStyle
from dbt_charts.core.compile.models.style.theme.heatmap import HeatmapConditionalRule

from ._cartesian import _CartesianResolvedStyle
from ._marks import ResolvedRectLabels


class ResolvedRectMark(RectMarkStyle):
    """The rect mark with its value-label format resolved."""

    labels: ResolvedRectLabels = Field(
        default_factory=ResolvedRectLabels,
        description="Cell value labels; the format is the color field's unless authored.",
    )


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
    rect_mark: ResolvedRectMark = Field(
        description="Cascade-merged rect mark geometry (opacity, stroke) and cell labels."
    )
    conditional: dict[str, list[HeatmapConditionalRule]] | None = Field(
        default=None,
        description=(
            "The chart's per-cell rules keyed by column, color tokens resolved. "
            "The renderer evaluates them against each cell's row."
        ),
    )
    tints: bool = Field(
        default=False, description="Each cell takes its pattern step's flat tint."
    )
    strip_key: bool = Field(
        default=False, description="The stepped key is one strip of swatches."
    )
    strip_key_format: ResolvedFormat | None = Field(
        default=None,
        description="The strip's two end values print in this format; set with the strip.",
    )
    strip_key_is_house: bool = Field(
        default=False, description="The strip's format paints the narrative register."
    )
    label_divides: bool = Field(
        default=False,
        description="X labels center on the divide where a column starts.",
    )
    label_usable_ratio: float = Field(
        description="Usable-width ratio for the categorical-axis overlap heuristic."
    )


__all__ = ["ResolvedHeatmapStyle"]
