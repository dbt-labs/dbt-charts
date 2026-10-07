"""Authored heatmap chart."""

from __future__ import annotations

from typing import Annotated, ClassVar, Literal

from pydantic import ConfigDict, Field

from dbt_charts.core.compile.models.constraints import Constraint
from dbt_charts.core.compile.models.style.authored import HeatmapChartStylePatch

from ._base import SUPPORT_TABLE_TYPE_RULE, _CartesianChartFields


class HeatmapChart(_CartesianChartFields):
    """Authored patch for heatmap charts."""

    model_config = ConfigDict(extra="forbid")

    __constraints__: ClassVar[tuple[Constraint, ...]] = (SUPPORT_TABLE_TYPE_RULE,)

    type: Annotated[Literal["heatmap"], Field(description="Selects the chart family.")]
    style: Annotated[
        HeatmapChartStylePatch | None,
        Field(default=None, description="Appearance overrides for this chart alone."),
    ]
