"""Shared base models for cartesian resolved style slices.

Stage: COMPILE (resolve).

Hierarchy
---------
_CartesianResolvedStyle
    All six cartesian families (bar, line, area, scatter, heatmap, layered).
    Carries axis_x, axis_y, tooltip_format — the three fields every cartesian
    emitter reads unconditionally.

_SeriesCartesianResolvedStyle (_CartesianResolvedStyle)
    bar / line / area — the "standard series" families that share a common
    series-label contract, endpoint-label feature gate, and the
    fill / ratio / format fields the feature layer reads.
"""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field

from dbt_charts.core.compile.models.anchor import (
    ResolvedCategoryAnchor,
    ResolvedValueAnchor,
)
from dbt_charts.core.compile.models.primitives import ResolvedFontStyle, ResolvedFormat

from ._base import ResolvedAxisStyle
from ._marks import (
    ResolvedAreaLineStyle,
    ResolvedLineMarkStyle,
    ResolvedPointMarkStyle,
    ResolvedSeriesLabelStyle,
)


class _CartesianResolvedStyle(BaseModel):
    """Shared resolved fields for all cartesian chart families.

    All six cartesian style slices inherit this so axes and tooltip_format
    are declared once. The chart's effective canvas color (used by halo
    strokes, inside-mark value labels, and the VL spec root) lives on the
    resolved chart envelope (``ResolvedChart.background``), not here — see
    ``_SharedResolvedChartFields`` in ``compile/models/chart/resolved/_base.py``.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    axis_x: ResolvedAxisStyle = Field(
        description="Baked x-axis style, after the full style cascade.",
    )
    axis_y: ResolvedAxisStyle = Field(
        description="Baked y-axis style, after the full style cascade.",
    )
    tooltip_format: ResolvedFormat = Field(
        description="Resolved d3 tooltip number format ('' = VL default).",
    )
    title_font: ResolvedFontStyle | None = Field(
        default=None,
        description="Baked title font (size/weight/family/…) from the width tier.",
    )


class ResolvedEndpointLabelsConfig(BaseModel):
    """The direct-label config once the engine has decided ``auto``."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    visible: bool = Field(description="Whether the series names print on the chart.")
    label_offset: float = Field(
        description="Gap in pixels between the plot and the series labels."
    )


class ResolvedBarEndpointLabelsConfig(ResolvedEndpointLabelsConfig):
    """A bar's direct-label config, which also sizes the strip above a horizontal stack."""

    height: float = Field(
        description="Height in pixels of the label strip above a horizontal stacked bar."
    )


class ResolvedDirectOverlayX(BaseModel):
    """One series' preferred x for its name inside the plot."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    series: str = Field(description="The series name, as the key names it.")
    x: ResolvedValueAnchor | ResolvedCategoryAnchor = Field(
        description="The x the name prefers, on the chart's x axis."
    )


class ResolvedLineAreaEndpointLabelsConfig(ResolvedEndpointLabelsConfig):
    """A line's or area's direct-label config, which may name series inside the plot."""

    overlay: bool = Field(
        description="Whether the series names print inside the plot beside their marks."
    )
    overlay_x: tuple[ResolvedDirectOverlayX, ...] = Field(
        description="Series whose name prefers an x inside the plot."
    )
    unseen_overlay_x: tuple[str, ...] = Field(
        description="`overlay_x` keys naming a series this render never draws; a warning input."
    )


class _SeriesCartesianResolvedStyle(_CartesianResolvedStyle):
    """Shared resolved fields for the series cartesian families: bar, line, area.

    These three families share a series-label typography contract, an endpoint-label
    feature gate, and the fill / ratio / format fields used by the render feature
    layer (ZeroBaseline, EndpointLabel, overlap heuristic).
    """

    series_label: ResolvedSeriesLabelStyle = Field(
        description="Resolved series-label typography + dark-companion ink palette.",
    )
    endpoint_labels: ResolvedEndpointLabelsConfig = Field(
        description=(
            "Cascade-merged endpoint-label config (visible + label_offset). "
            "Gates the EndpointLabelFeature and color-legend suppression."
        ),
    )
    single_series_fill: str = Field(
        description="Fill/stroke color for single-series marks (ink palette slot 0).",
    )
    label_usable_ratio: float = Field(
        description="Usable-width ratio for the categorical-axis overlap heuristic.",
    )


class _OverlayAttempt(BaseModel):
    """The style a chart takes when its series names go inside the plot."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    axis_y: ResolvedAxisStyle = Field(
        description="The value axis when series names go inside the plot.",
    )
    point_mark: ResolvedPointMarkStyle = Field(
        description="The point mark, with its value labels, in that arrangement.",
    )


class ResolvedLineOverlayAttempt(_OverlayAttempt):
    """A line chart's style when its series names go inside the plot."""

    line_mark: ResolvedLineMarkStyle = Field(
        description="The line mark, with its value labels, in that arrangement.",
    )


class ResolvedAreaOverlayAttempt(_OverlayAttempt):
    """An area chart's style when its series names go inside the plot."""

    line_mark: ResolvedAreaLineStyle = Field(
        description="The edge line, with its value labels, in that arrangement.",
    )


__all__ = [
    "ResolvedAreaOverlayAttempt",
    "ResolvedBarEndpointLabelsConfig",
    "ResolvedEndpointLabelsConfig",
    "ResolvedLineAreaEndpointLabelsConfig",
    "ResolvedLineOverlayAttempt",
    "_CartesianResolvedStyle",
    "_SeriesCartesianResolvedStyle",
]
