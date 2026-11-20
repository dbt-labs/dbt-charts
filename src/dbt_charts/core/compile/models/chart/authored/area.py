"""Authored area chart."""

from __future__ import annotations

from typing import Annotated, Literal

from pydantic import ConfigDict, Field

from dbt_charts.core.compile.models.style.authored import AreaChartStylePatch

from ._annotation import Annotation
from ._base import _CartesianChartFields, _PatternChannelField
from ._layer import CartesianLayer
from ._tooltip import _TooltipFields


class AreaChart(_PatternChannelField, _CartesianChartFields, _TooltipFields):
    """Authored patch for area charts."""

    model_config = ConfigDict(extra="forbid")

    type: Annotated[Literal["area"], Field(description="Selects the chart family.")]
    style: Annotated[
        AreaChartStylePatch | None,
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
