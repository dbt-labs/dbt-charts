"""Area family resolved style slice."""

from __future__ import annotations

from typing import Literal

from pydantic import ConfigDict, Field

from ._cartesian import _SeriesCartesianResolvedStyle
from ._marks import ResolvedAreaLineStyle, ResolvedAreaMarkStyle, ResolvedPointMarkStyle


class ResolvedAreaStyle(_SeriesCartesianResolvedStyle):
    """Area family style slice — self-contained, globals merged down.

    Every field is concrete after resolve; the area emitter reads only these.
    Inherits axis_x, axis_y, tooltip_format, series_label,
    endpoint_labels, single_series_fill, label_usable_ratio
    from the cartesian base hierarchy.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    stack_order: Literal["value", "data", "alphabetical"] | None = Field(
        default=None,
        description="Z-order of stacked bands. None / 'value' = largest at bottom.",
    )
    area_mark: ResolvedAreaMarkStyle = Field(
        description="Cascade-merged area fill mark geometry — opacity and curve.",
    )
    line_mark: ResolvedAreaLineStyle = Field(
        description=(
            "Cascade-merged top-edge line geometry — stroke, halo, and "
            "value labels (see ResolvedAreaLineStyle)."
        ),
    )
    point_mark: ResolvedPointMarkStyle = Field(
        description=(
            "Point-overlay mark at each plotted value; size 0 (default) "
            "renders no points, matching the line-chart precedent."
        ),
    )
    dashes: list[list[int]] = Field(
        description=(
            "Ordered strokeDash arrays for categorical area encoding; [] = no dash "
            "emission. Baked from board_style.charts.dashes at resolve time."
        ),
    )
    label_is_house: bool = Field(
        default=False,
        description="True when the area line mark's value label uses the house narrative register.",
    )


__all__ = ["ResolvedAreaStyle"]
