"""Authored line chart."""

from __future__ import annotations

from typing import Annotated, ClassVar, Literal

from pydantic import ConfigDict, Field

from dbt_charts.core.compile.models.constraints import Constraint, Forbids, IsSet
from dbt_charts.core.compile.models.style.authored import LineChartStylePatch

from ._annotation import Annotation
from ._base import _CartesianChartFields
from ._layer import CartesianLayer
from ._tooltip import _TooltipFields


class LineChart(_CartesianChartFields, _TooltipFields):
    """Authored patch for line charts.

    Intentionally excludes size, and shape — these channels are structurally
    impossible on line charts. An attempt to set them raises extra_forbidden.
    """

    model_config = ConfigDict(extra="forbid")

    __constraints__: ClassVar[tuple[Constraint, ...]] = (
        Forbids(
            ("tooltip",),
            "tooltip is not supported with layers: an overlay mark's card describes the layer, not a column of the base query.",
            when=(IsSet("layers"),),
        ),
    )

    type: Annotated[Literal["line"], Field(description="Selects the chart family.")]
    style: Annotated[
        LineChartStylePatch | None,
        Field(default=None, description="Appearance overrides for this chart alone."),
    ]
    layers: Annotated[
        list[CartesianLayer] | None,
        Field(
            default=None,
            description="Extra marks on one shared x, each with its own type.",
        ),
    ]
    annotations: Annotated[
        list[Annotation] | None,
        Field(
            default=None,
            description="Rules, bands, notes and trends drawn on the chart.",
        ),
    ]
