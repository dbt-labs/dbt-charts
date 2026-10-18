"""Authored area chart."""

from __future__ import annotations

from typing import Annotated, ClassVar, Literal

from pydantic import ConfigDict, Field

from dbt_charts.core.compile.models.constraints import Constraint, Forbids, IsList
from dbt_charts.core.compile.models.style.authored import AreaChartStylePatch

from ._base import _CartesianChartFields
from ._layer import CartesianLayer


class AreaChart(_CartesianChartFields):
    """Authored patch for area charts."""

    model_config = ConfigDict(extra="forbid")

    __constraints__: ClassVar[tuple[Constraint, ...]] = (
        Forbids(
            ("layers",),
            "Area chart: layers are not supported with multi-metric "
            "(y: [...]) charts. Use a long-form query with one y field.",
            when=(IsList("y"),),
            nonempty=("layers",),
        ),
    )

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
