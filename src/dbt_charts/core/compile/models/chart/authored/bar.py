"""Authored bar / histogram chart."""

from __future__ import annotations

from typing import Annotated, Any, ClassVar, Literal

from pydantic import ConfigDict, Field, model_validator

from dbt_charts.core.compile.models.constraints import (
    Constraint,
    Forbids,
    IsList,
    IsSet,
    Requires,
    ValueIn,
)
from dbt_charts.core.compile.models.markers import Channel
from dbt_charts.core.compile.models.style.authored import BarChartStylePatch

from ._base import SUPPORT_TABLE_TYPE_RULE, _CartesianChartFields
from ._layer import (
    CARTESIAN_LAYER_SUPPORTED_CHART_TYPES,
    CartesianLayer,
    reject_blank_y_start,
)


class BarChart(_CartesianChartFields):
    """Authored patch for bar and histogram charts; histogram adds automatic x binning."""

    model_config = ConfigDict(extra="forbid")

    __constraints__: ClassVar[tuple[Constraint, ...]] = (
        SUPPORT_TABLE_TYPE_RULE,
        Forbids(
            ("layers",),
            "chart.layers is not supported for chart type {type!r}. "
            "Supported chart types: "
            f"{', '.join(sorted(CARTESIAN_LAYER_SUPPORTED_CHART_TYPES))}.",
            when=(
                ValueIn(
                    "type",
                    tuple(sorted(CARTESIAN_LAYER_SUPPORTED_CHART_TYPES)),
                    negate=True,
                ),
            ),
            nonempty=("layers",),
        ),
        Forbids(
            ("y_start",),
            "y_start is not supported on type: histogram — a histogram's "
            "bars count from zero, so it takes no y_start.",
            when=(ValueIn("type", ("histogram",)),),
        ),
        Forbids(
            ("y_start",),
            "y_start sets each bar's start, so it can't be stacked; "
            "remove style.stack or y_start.",
            when=(ValueIn("style.stack", ("zero", "normalize", "center")),),
        ),
        Forbids(
            ("y_start",),
            "y_start sets one start per bar, so y must name a single column, "
            "not a list.",
            when=(IsList("y"),),
        ),
        # A histogram bins x and counts, never reading y, so the multi-series
        # rules below are bar-only.
        Forbids(
            ("layers",),
            "Bar chart: layers are not supported with multi-metric "
            "(y: [...]) charts. Use a long-form query with one y field.",
            when=(ValueIn("type", ("bar",)), IsList("y")),
            nonempty=("layers",),
        ),
        # Folded measures group side by side within each x band.
        Requires(
            ("x",),
            "Bar chart: multi-metric (y: [...]) charts require an x field.",
            when=(ValueIn("type", ("bar",)), IsList("y")),
        ),
        # Bands with nothing to measure: VL cannot compile a null measure field.
        Requires(
            ("y",),
            "Bar chart: x requires a y field — a bar with categories but no "
            "measure has nothing to draw.",
            when=(ValueIn("type", ("bar",)), IsSet("x")),
            nonempty=("y",),
        ),
    )

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
        list[CartesianLayer] | None,
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
