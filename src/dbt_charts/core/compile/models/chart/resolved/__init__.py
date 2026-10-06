"""Resolved chart models — per-family discriminated union.

This package is the resolve stage of compile: authored → normalize → **resolve**.
``Resolved*`` prefixed models are the output; each family's model carries a
self-contained style slice (baked from the cascade) and typed channel bindings.

  from dbt_charts.core.compile.models.chart.resolved import (
      ResolvedBarChart,
      ResolvedChart,   # TypeAdapter-friendly discriminated union
      ...
  )
"""

from __future__ import annotations

from typing import Annotated

from pydantic import Discriminator

# ---------------------------------------------------------------------------
# Style slices.
# ---------------------------------------------------------------------------
from dbt_charts.core.compile.models.style.resolved import (
    ResolvedAreaStyle,
    ResolvedBarStyle,
    ResolvedGeoshapeStyle,
    ResolvedHeatmapStyle,
    ResolvedKpiStyle,
    ResolvedLineStyle,
    ResolvedPieStyle,
    ResolvedPointMapStyle,
    ResolvedScatterStyle,
    ResolvedSeriesLabelStyle,
    ResolvedSparkBarStyle,
    ResolvedTableStyle,
)

# ---------------------------------------------------------------------------
# Resolved data types used by both compile and render layers.
# ---------------------------------------------------------------------------
from ._channel import ResolvedStyleChannel
from ._layer import (
    LayeredResolvedChart,
    ResolvedAreaLayer,
    ResolvedBarLayer,
    ResolvedLayer,
    ResolvedLineLayer,
    ResolvedScatterLayer,
    effective_color_field,
)
from ._partition import PartitionAxis
from ._support_table import (
    ResolvedSupportTable,
    ResolvedSupportTableAggregate,
    ResolvedSupportTableEntry,
    ResolvedSupportTablePerSeries,
    ResolvedSupportTableSource,
)

# ---------------------------------------------------------------------------
# Per-family discriminated models.
# ---------------------------------------------------------------------------
from .area import ResolvedAreaChart
from .bar import ResolvedBarChart
from .callout import ResolvedCalloutChart
from .geoshape import ResolvedGeoshapeChart
from .heatmap import ResolvedHeatmapChart
from .kpi import ResolvedKpiChart, ResolvedKpiSupportConfig
from .line import ResolvedLineChart
from .pie import ResolvedPieChart
from .point_map import ResolvedPointMapChart
from .scatter import ResolvedScatterChart
from .spark_bar import ResolvedSparkBarChart
from .table import ResolvedTableChart

# ---------------------------------------------------------------------------
# Discriminated union. chart_type is the discriminator key (differs from
# compiled Chart which uses `type` to avoid shadowing the built-in).
# ---------------------------------------------------------------------------
ResolvedChart = Annotated[
    ResolvedBarChart
    | ResolvedLineChart
    | ResolvedAreaChart
    | ResolvedScatterChart
    | ResolvedHeatmapChart
    | ResolvedPieChart
    | ResolvedKpiChart
    | ResolvedTableChart
    | ResolvedPointMapChart
    | ResolvedGeoshapeChart
    | ResolvedCalloutChart
    | ResolvedSparkBarChart,
    Discriminator("chart_type"),
]

__all__ = [
    # Resolved data types shared across compile and render layers
    "ResolvedStyleChannel",
    "PartitionAxis",
    # _layer helpers
    "ResolvedBarLayer",
    "ResolvedLineLayer",
    "ResolvedAreaLayer",
    "ResolvedScatterLayer",
    "ResolvedLayer",
    "LayeredResolvedChart",
    "effective_color_field",
    # New discriminated family models
    "ResolvedAreaChart",
    "ResolvedBarChart",
    "ResolvedCalloutChart",
    "ResolvedGeoshapeChart",
    "ResolvedHeatmapChart",
    "ResolvedKpiChart",
    "ResolvedSupportTable",
    "ResolvedSupportTableAggregate",
    "ResolvedSupportTableEntry",
    "ResolvedSupportTablePerSeries",
    "ResolvedSupportTableSource",
    "ResolvedKpiSupportConfig",
    "ResolvedLineChart",
    "ResolvedPieChart",
    "ResolvedPointMapChart",
    "ResolvedScatterChart",
    "ResolvedSparkBarChart",
    "ResolvedTableChart",
    # Style slices
    "ResolvedAreaStyle",
    "ResolvedBarStyle",
    "ResolvedGeoshapeStyle",
    "ResolvedHeatmapStyle",
    "ResolvedKpiStyle",
    "ResolvedLineStyle",
    "ResolvedPieStyle",
    "ResolvedPointMapStyle",
    "ResolvedScatterStyle",
    "ResolvedSeriesLabelStyle",
    "ResolvedSparkBarStyle",
    "ResolvedTableStyle",
    # New discriminated union
    "ResolvedChart",
]
