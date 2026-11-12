"""Normalized point map / bubble map chart."""

from __future__ import annotations

from typing import Literal

from pydantic import Field

from dbt_charts.core.compile.models.markers import ColumnRef
from dbt_charts.core.compile.models.style.authored import PointMapChartStylePatch

from ._base import _GeoChartFields


class PointMapChart(_GeoChartFields):
    """Normalized point map or bubble map chart."""

    type: Literal["point_map", "bubble_map"]
    latitude: ColumnRef | None = Field(
        default=None, description="Field supplying latitude values."
    )
    longitude: ColumnRef | None = Field(
        default=None, description="Field supplying longitude values."
    )
    size: ColumnRef | None = Field(
        default=None,
        description="Quantitative column that scales point area. Mutually exclusive with `collapse`.",
    )
    collapse: bool = Field(
        default=False,
        description="Collapse marks sharing an exact latitude/longitude into one mark sized by count.",
    )
    style: PointMapChartStylePatch | None = Field(
        default=None, description="Chart-local style overrides."
    )
