"""Scatter family resolved style slice."""

from __future__ import annotations

from pydantic import ConfigDict, Field

from ._cartesian import _CartesianResolvedStyle
from ._marks import ResolvedPointMarkStyle


class ResolvedScatterStyle(_CartesianResolvedStyle):
    """Scatter family style slice — self-contained, globals merged down.

    Inherits axis_x, axis_y, tooltip_format from _CartesianResolvedStyle.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    point_mark: ResolvedPointMarkStyle = Field(
        description="Cascade-merged point mark geometry."
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
