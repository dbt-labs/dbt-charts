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

from dbt_charts.core.compile.models.primitives import ResolvedFontStyle, ResolvedFormat
from dbt_charts.core.compile.models.style.authored import EndpointLabelsConfig

from ._base import ResolvedAxisStyle
from ._marks import ResolvedSeriesLabelStyle


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


class _SeriesCartesianResolvedStyle(_CartesianResolvedStyle):
    """Shared resolved fields for the series cartesian families: bar, line, area.

    These three families share a series-label typography contract, an endpoint-label
    feature gate, and the fill / ratio / format fields used by the render feature
    layer (ZeroBaseline, EndpointLabel, overlap heuristic).
    """

    series_label: ResolvedSeriesLabelStyle = Field(
        description="Resolved series-label typography + dark-companion ink palette.",
    )
    endpoint_labels: EndpointLabelsConfig = Field(
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


__all__ = ["_CartesianResolvedStyle", "_SeriesCartesianResolvedStyle"]
