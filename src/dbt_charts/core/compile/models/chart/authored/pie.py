"""Authored pie / donut chart."""

from __future__ import annotations

from typing import Annotated, Literal

from pydantic import ConfigDict, Field

from dbt_charts.core.compile.models.style.authored import PieChartStylePatch

from ._base import _RadialChartFields
from ._tooltip import _TooltipFields


class PieChart(_RadialChartFields, _TooltipFields):
    """Authored patch for pie and donut charts; donut defaults `style.inner_radius` to 0.6.

    Pie charts use theta (angular) and color (segment) channels.
    x/y/format/sort and other cartesian fields are not valid here.
    """

    model_config = ConfigDict(extra="forbid")

    type: Annotated[
        Literal["pie", "donut"],
        Field(description="Selects the chart family."),
    ]
    style: Annotated[
        PieChartStylePatch | None,
        Field(default=None, description="Appearance overrides for this chart alone."),
    ]
