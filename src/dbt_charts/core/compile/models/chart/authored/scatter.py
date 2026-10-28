"""Authored scatter chart."""

from __future__ import annotations

from typing import Annotated, ClassVar, Literal

from pydantic import ConfigDict, Field

from dbt_charts.core.compile.models.constraints import (
    Constraint,
    Forbids,
    IsList,
    IsSet,
)
from dbt_charts.core.compile.models.markers import Channel
from dbt_charts.core.compile.models.style.authored import ScatterChartStylePatch

from ._annotation import Annotation
from ._base import SUPPORT_TABLE_TYPE_RULE, _CartesianChartFields
from ._layer import CartesianLayer
from ._tooltip import _TooltipFields


class ScatterChart(_CartesianChartFields, _TooltipFields):
    """Authored patch for scatter charts."""

    model_config = ConfigDict(extra="forbid")

    __constraints__: ClassVar[tuple[Constraint, ...]] = (
        SUPPORT_TABLE_TYPE_RULE,
        Forbids(
            ("tooltip",),
            "tooltip is not supported with layers: an overlay mark's card describes the layer, not a column of the base query.",
            when=(IsSet("layers"),),
        ),
        Forbids(
            ("tooltip",),
            "tooltip is not supported with a multi-measure y: list: a wide scatter draws no structured hover card.",
            when=(IsList("y"),),
        ),
    )

    type: Annotated[Literal["scatter"], Field(description="Selects the chart family.")]
    size: Annotated[
        str | None,
        Channel(),
        Field(
            default=None,
            description="Column used to size-encode data points (quantitative).",
        ),
    ]
    shape: Annotated[
        str | None,
        Channel(),
        Field(
            default=None,
            description="Column used to shape-encode data points (categorical).",
        ),
    ]
    style: Annotated[
        ScatterChartStylePatch | None,
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
