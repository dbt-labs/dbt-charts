"""Neutral resolved-chart primitives shared across compile and render layers.

Stage: COMPILE (neutral)
Purpose: House effective_color_field and the typed
ResolvedLayer discriminated union in a module that carries no dependency on the
normalized chart package.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal, Protocol, runtime_checkable

from dbt_charts.core.compile.models.chart.authored._layer import (
    LayerAxisYGrid,
    LayerAxisYScale,
    LayerAxisYTicks,
)
from dbt_charts.core.compile.models.chart.resolved._channel import ResolvedStyleChannel
from dbt_charts.core.compile.models.primitives import ResolvedFormat
from dbt_charts.core.compile.models.style.resolved._base import ResolvedTickLabel
from dbt_charts.core.compile.models.style.resolved._marks import (
    ResolvedAreaLineStyle,
    ResolvedAreaMarkStyle,
    ResolvedBarMarkStyle,
    ResolvedLineMarkStyle,
    ResolvedPointMarkStyle,
)


class _WithResolvedChannels(Protocol):
    """Structural protocol: any chart model that carries resolved_channels.

    Satisfied by ResolvedChart (V1), all _BaseResolvedChartFields subclasses
    (cartesian/pie/etc.), and ResolvedCalloutChart (empty dict).
    Used as the parameter type of effective_color_field so callers can pass
    the full ResolvedChart | ResolvedChart union without isinstance guards.
    """

    @property
    def resolved_channels(self) -> dict[str, ResolvedStyleChannel]: ...


def effective_color_field(chart: _WithResolvedChannels) -> str | None:
    """Return the color data field from resolved_channels, or None.

    Accepts any chart that carries resolved_channels — V1 ResolvedChart,
    family models (_BaseResolvedChartFields subclasses), and
    ResolvedCalloutChart (always returns None since its dict is empty).
    """
    color_ch = chart.resolved_channels.get("color")
    if color_ch is None or color_ch.mode == "literal":
        return None
    return color_ch.data_field or None


@runtime_checkable
class LayeredResolvedChart(Protocol):
    """Structural protocol: any resolved chart family that carries typed
    overlay ``layers`` (bar/line/area/scatter today — ``.layers`` is declared
    per-family, not on a shared cartesian base, since heatmap/pie/etc. have
    no overlay concept).

    ``isinstance(chart, LayeredResolvedChart)`` derives "can this chart carry
    layers" from the model shape itself, rather than a hand-maintained tuple
    of family classes that silently goes stale the next time a family gains
    (or loses) a ``layers`` field.
    """

    @property
    def layers(self) -> tuple[ResolvedLayer, ...]: ...


# ---------------------------------------------------------------------------
# Typed resolved layer union (BarLayer / LineLayer / AreaLayer / ScatterLayer)
#
# Each carries: identity fields (x/y/label/color/query_name/axis_y) + the
# FULL set of resolved mark styles for its family, mirroring the resolved
# chart-family style (line → line_mark + point_mark; area → area_mark +
# line_mark [top-edge stroke/halo/labels — see ResolvedAreaLineStyle] +
# point_mark; bar → bar_mark; scatter → point_mark).
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class ResolvedLayerAxisYLabels:
    format: ResolvedFormat | None = None


@dataclass(frozen=True)
class ResolvedLayerAxisY:
    """A layer's own y-axis: authored chrome plus its baked format.

    ``tick_label`` is the guarded per-tick label for an SI format (a layer has
    no ladder); ``tooltip_format`` is set only when the layer's values move the
    sub-unit vote off ``labels.format``.
    """

    position: Literal["left", "right"] | None = None
    title: str | None = None
    scale: LayerAxisYScale | None = None
    ticks: LayerAxisYTicks | None = None
    grid: LayerAxisYGrid | None = None
    labels: ResolvedLayerAxisYLabels | None = None
    tick_label: ResolvedTickLabel | None = None
    tooltip_format: ResolvedFormat | None = None


@dataclass(frozen=True)
class ResolvedBarLayer:
    """Resolved bar-type overlay layer."""

    type: Literal["bar"]
    bar_mark: ResolvedBarMarkStyle
    axis_y: ResolvedLayerAxisY = field(default_factory=ResolvedLayerAxisY)
    x: str | None = None
    y: str | None = None
    # Column each bar starts from; absent, bars start at zero.
    y_start: str | None = None
    label: str | None = None
    color: str | None = None
    # Named query this layer's own data comes from, when it overrides the
    # chart-level query (authored `layers[].query`). None → shares the base
    # chart's data. Render looks this name up in the per-chart datasets map.
    query_name: str | None = None
    # True when bar_mark.labels.format is an SI spec that should use the house
    # narrative register. False when the format is a literal d3 spec authored
    # directly (not via a theme alias), matching base-chart provenance logic.
    label_is_house: bool = False


@dataclass(frozen=True)
class ResolvedLineLayer:
    """Resolved line-type overlay layer."""

    type: Literal["line"]
    line_mark: ResolvedLineMarkStyle
    point_mark: ResolvedPointMarkStyle
    axis_y: ResolvedLayerAxisY = field(default_factory=ResolvedLayerAxisY)
    x: str | None = None
    y: str | None = None
    label: str | None = None
    color: str | None = None
    query_name: str | None = None
    label_is_house: bool = False


@dataclass(frozen=True)
class ResolvedAreaLayer:
    """Resolved area-type overlay layer."""

    type: Literal["area"]
    area_mark: ResolvedAreaMarkStyle
    line_mark: ResolvedAreaLineStyle
    point_mark: ResolvedPointMarkStyle
    axis_y: ResolvedLayerAxisY = field(default_factory=ResolvedLayerAxisY)
    x: str | None = None
    y: str | None = None
    label: str | None = None
    color: str | None = None
    query_name: str | None = None
    label_is_house: bool = False


@dataclass(frozen=True)
class ResolvedScatterLayer:
    """Resolved scatter-type overlay layer."""

    type: Literal["scatter"]
    point_mark: ResolvedPointMarkStyle
    axis_y: ResolvedLayerAxisY = field(default_factory=ResolvedLayerAxisY)
    x: str | None = None
    y: str | None = None
    label: str | None = None
    color: str | None = None
    query_name: str | None = None
    label_is_house: bool = False


# Union alias used in resolved chart model fields.
ResolvedLayer = (
    ResolvedBarLayer | ResolvedLineLayer | ResolvedAreaLayer | ResolvedScatterLayer
)
