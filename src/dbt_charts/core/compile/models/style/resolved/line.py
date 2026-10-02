"""Line family resolved style slice."""

from __future__ import annotations

from pydantic import ConfigDict, Field

from ._cartesian import _SeriesCartesianResolvedStyle
from ._marks import ResolvedLineMarkStyle, ResolvedPointMarkStyle


class ResolvedLineStyle(_SeriesCartesianResolvedStyle):
    """Line family style slice — self-contained, globals merged down.

    Every field is concrete after resolve; the line emitter reads only these.
    Inherits axis_x, axis_y, tooltip_format, series_label,
    endpoint_labels, single_series_fill, label_usable_ratio
    from the cartesian base hierarchy.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    line_mark: ResolvedLineMarkStyle = Field(
        description="Cascade-merged line mark geometry (stroke, halo, labels).",
    )
    point_mark: ResolvedPointMarkStyle = Field(
        description="Cascade-merged point mark geometry; size=None or <=0 means no point overlay.",
    )
    dashes: list[list[int]] = Field(
        description=(
            "Ordered strokeDash arrays for categorical line encoding; [] = no dash "
            "emission. Baked from board_style.charts.dashes at resolve time."
        ),
    )
    line_label_is_house: bool = Field(
        default=False,
        description="True when the line mark's value label uses the house narrative register.",
    )
    point_label_is_house: bool = Field(
        default=False,
        description="True when the point overlay's value label uses the house narrative register.",
    )


__all__ = ["ResolvedLineStyle"]
