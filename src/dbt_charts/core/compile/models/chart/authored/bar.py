"""Authored bar / histogram chart."""

from __future__ import annotations

from typing import Annotated, ClassVar, Literal

from pydantic import ConfigDict, Field

from dbt_charts.core.compile.models.constraints import (
    Constraint,
    Forbids,
    IsList,
    IsSet,
    ValueIn,
)
from dbt_charts.core.compile.models.markers import Channel
from dbt_charts.core.compile.models.style.authored import BarChartStylePatch

from ._annotation import Annotation
from ._base import SUPPORT_TABLE_TYPE_RULE, _CartesianChartFields, _PatternChannelField
from ._layer import (
    CARTESIAN_LAYER_SUPPORTED_CHART_TYPES,
    Y_START_NOT_NULL,
    CartesianLayer,
    YStartColumn,
)
from ._tooltip import _TooltipFields


class BarChart(_PatternChannelField, _CartesianChartFields, _TooltipFields):
    """Authored patch for bar and histogram charts; histogram adds automatic x binning."""

    model_config = ConfigDict(extra="forbid")

    __constraints__: ClassVar[tuple[Constraint, ...]] = (
        SUPPORT_TABLE_TYPE_RULE,
        Y_START_NOT_NULL,
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
            ("pattern", "style.pattern"),
            "pattern is not supported on type: histogram: its bars are bins of "
            "rows, so a column has no single value to pattern by.",
            when=(ValueIn("type", ("histogram",)),),
        ),
        Forbids(
            ("style.legend.position.edge",),
            "legend position `endpoints` is not supported on type: histogram: "
            "it has one series, so there are no endpoint labels to print. Set "
            "the edge to `top`, `bottom`, `left` or `right`, or remove it.",
            when=(
                ValueIn("type", ("histogram",)),
                ValueIn("style.legend.position.edge", ("endpoints",)),
            ),
        ),
        Forbids(
            ("annotations",),
            "annotations are not supported on type: histogram: a histogram "
            "bins x, so an anchor has no category or date slot to name.",
            when=(ValueIn("type", ("histogram",)),),
            nonempty=("annotations",),
        ),
        Forbids(
            ("tooltip",),
            "tooltip is not supported with layers: an overlay mark's card describes the layer, not a column of the base query.",
            when=(IsSet("layers"),),
        ),
        Forbids(
            ("tooltip",),
            "tooltip is not supported on type: histogram: a bar there is a "
            "bin of rows, not one row, so a column has no single value to show.",
            when=(ValueIn("type", ("histogram",)),),
        ),
        Forbids(
            ("tooltip",),
            "tooltip is not supported with y_start: a ranged bar's card shows "
            "its start, end and difference.",
            when=(IsSet("y_start"),),
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
    )

    type: Annotated[
        Literal["bar", "histogram"],
        Field(description="Selects the chart family."),
    ]
    y_start: Annotated[
        YStartColumn | None,
        Channel(),
        Field(
            default=None,
            description="Column each bar starts from instead of zero (waterfall, range, Gantt, lollipop). Same kind as y: both numeric, or both dates. Not with stacking.",
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
                "Extra marks on one shared x, each with its own type. Not supported when "
                "type: histogram: a histogram bins x and aggregates to a "
                "count, so there is no shared y measure for an overlay to "
                "plot against."
            ),
        ),
    ]
    annotations: Annotated[
        list[Annotation] | None,
        Field(
            default=None,
            description="Rules, bands, notes and trends drawn on the chart. Not supported when type: histogram.",
        ),
    ]
