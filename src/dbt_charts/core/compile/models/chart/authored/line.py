"""Authored line chart."""

from __future__ import annotations

from typing import Annotated, Literal

from pydantic import ConfigDict, Field

from dbt_charts.core.compile.models.style.authored import LineChartStylePatch

from ._base import _CartesianChartFields
from ._layer import CartesianLayer


class LineChart(_CartesianChartFields):
    """Authored patch for line charts.

    Intentionally excludes size, and shape — these channels are structurally
    impossible on line charts. An attempt to set them raises extra_forbidden.
    """

    model_config = ConfigDict(extra="forbid")

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
