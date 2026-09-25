"""Authored bar / histogram chart."""

from __future__ import annotations

from typing import Annotated, Any, Literal

from pydantic import ConfigDict, Field, model_validator

from dbt_charts.core.compile.models.markers import Channel
from dbt_charts.core.compile.models.style.authored import BarChartStylePatch

from ._base import (
    _CartesianChartFields,
    reject_multi_series_channel_conflicts,
)
from ._layer import (
    CARTESIAN_LAYER_SUPPORTED_CHART_TYPES,
    BarChartLayer,
    reject_blank_y_start,
)


class BarChart(_CartesianChartFields):
    """Authored patch for bar and histogram charts; histogram adds automatic x binning."""

    model_config = ConfigDict(extra="forbid")

    type: Annotated[
        Literal["bar", "histogram"],
        Field(description="Selects the chart family."),
    ]
    y_start: Annotated[
        str | None,
        Channel(),
        Field(
            default=None,
            description="Column each bar starts from, so it runs from y_start to y instead of from zero. Same kind as y: both numeric, or both dates. Not with stacking.",
        ),
    ]
    style: Annotated[
        BarChartStylePatch | None,
        Field(default=None, description="Appearance overrides for this chart alone."),
    ]
    layers: Annotated[
        list[BarChartLayer] | None,
        Field(
            default=None,
            description=(
                "Extra marks drawn over this chart, each with its own type and columns. Not supported when "
                "type: histogram: a histogram bins x and aggregates to a "
                "count, so there is no shared y measure for an overlay to "
                "plot against."
            ),
        ),
    ]

    @model_validator(mode="before")
    @classmethod
    def _reject_blank_y_start(
        cls,
        data: Any,  # type-state: explicit_any — mode="before" validator input; raw YAML value
    ) -> Any:  # type-state: explicit_any — passthrough of the same boundary value
        return reject_blank_y_start(data)

    @model_validator(mode="after")
    def _reject_histogram_layers(self) -> BarChart:
        # The guard lives here, on BarChart, not on the shared
        # _CartesianChartFields base: HeatmapChart also inherits that base but
        # declares no `layers` field at all, so a base-class validator
        # referencing self.layers would AttributeError on every authored
        # heatmap. See _validate_support_table for the same field-per-type-support
        # shape on a field (support_table) that IS shared on the base.
        if self.type not in CARTESIAN_LAYER_SUPPORTED_CHART_TYPES and self.layers:
            supported = ", ".join(sorted(CARTESIAN_LAYER_SUPPORTED_CHART_TYPES))
            raise ValueError(
                f"chart.layers is not supported for chart type {self.type!r}. "
                f"Supported chart types: {supported}."
            )
        return self

    @model_validator(mode="after")
    def _validate_y_start(self) -> BarChart:
        if self.y_start is None:
            return self
        if self.type == "histogram":
            raise ValueError(
                "y_start is not supported on type: histogram — a histogram's "
                "bars count from zero, so it takes no y_start."
            )
        if self.style is not None and self.style.stack in (
            "zero",
            "normalize",
            "center",
        ):
            raise ValueError(
                "y_start sets each bar's start, so it can't be stacked; "
                "remove style.stack or y_start."
            )
        if isinstance(self.y, list):
            raise ValueError(
                "y_start sets one start per bar, so y must name a single column, "
                "not a list."
            )
        return self

    @model_validator(mode="after")
    def _validate_multi_series(self) -> BarChart:
        # A histogram bins x and derives its measure by counting, so it never
        # reads y at all — the emitter returns into the histogram path before
        # the multi-metric branch. None of these conflicts apply to it.
        if self.type != "bar":
            return self
        reject_multi_series_channel_conflicts("Bar", self.y, self.layers)
        # Folded measures are grouped side by side within each x band; without
        # an x there are no bands to group them into.
        if isinstance(self.y, list) and self.x is None:
            raise ValueError(
                "Bar chart: multi-metric (y: [...]) charts require an x field."
            )
        # The mirror: bands with nothing to measure. The emitter would hand
        # Vega-Lite a null measure field, which it cannot compile; an empty
        # list or name is the same nothing.
        if self.x is not None and not self.y:
            raise ValueError(
                "Bar chart: x requires a y field — a bar with categories but no "
                "measure has nothing to draw."
            )
        return self
