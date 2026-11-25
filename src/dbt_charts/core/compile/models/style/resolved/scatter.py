"""Scatter family resolved style slice."""

from __future__ import annotations

from typing import Literal

from pydantic import ConfigDict, Field

from dbt_charts.core.compile.models.primitives import SizeScaleStyle
from dbt_charts.core.compile.models.style.theme.scatter import (
    ScatterConditionalRule,
    ScatterSeriesSymbol,
)

from ._cartesian import _CartesianResolvedStyle
from ._marks import ResolvedRingedPointMarkStyle


class ResolvedScatterStyle(_CartesianResolvedStyle):
    """Scatter family style slice — self-contained, globals merged down.

    Inherits axis_x, axis_y, tooltip_format from _CartesianResolvedStyle.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    point_mark: ResolvedRingedPointMarkStyle = Field(
        description="Cascade-merged point mark geometry."
    )
    size: SizeScaleStyle = Field(
        description="Area range and scale type of the `size:` channel."
    )
    conditional: dict[str, list[ScatterConditionalRule]] | None = Field(
        default=None,
        description=(
            "The chart's per-point rules keyed by column, color tokens resolved. "
            "The renderer evaluates them against each base-layer point's row."
        ),
    )
    frame: Literal["full", "range"] = Field(
        description="`range` draws the axes as a range frame; `full` draws them whole."
    )
    series_symbols: tuple[ScatterSeriesSymbol, ...] | None = Field(
        default=None,
        description="The symbol each series of a framed chart takes, in series order.",
    )
    single_series_fill: str = Field(
        description="Fill color for single-series points (ink palette slot 0)."
    )
    label_is_house: bool = Field(
        default=False,
        description="True when the scatter point label uses the house narrative register.",
    )
    label_usable_ratio: float = Field(
        description="Usable-width ratio for the categorical-axis overlap heuristic."
    )


__all__ = ["ResolvedScatterStyle"]
