"""Authored spark_bar chart (compact horizontal bars)."""

from __future__ import annotations

from typing import Annotated, Literal

from pydantic import ConfigDict, Field

from dbt_charts.core.compile.models.markers import Channel
from dbt_charts.core.compile.models.style.authored import SparkBarChartStylePatch

from ._base import _SharedChartFields


class SparkBarChart(_SharedChartFields):
    """Authored patch for spark_bar charts (compact horizontal bars)."""

    model_config = ConfigDict(extra="forbid")

    type: Annotated[
        Literal["spark_bar"], Field(description="Selects the chart family.")
    ]
    # None = auto-detected from query columns.
    x: Annotated[
        str | None,
        Channel(),
        Field(default=None, description="Bar-magnitude (numeric) column name."),
    ]
    # One row of bars against one label column: no second encoding to
    # separate a second series by.
    y: Annotated[
        str | Annotated[list[str], Field(min_length=1, max_length=1)] | None,
        Channel(),
        Field(default=None, description="Bar-label (category) column name(s)."),
    ]
    style: Annotated[
        SparkBarChartStylePatch | None,
        Field(default=None, description="Appearance overrides for this chart alone."),
    ]
