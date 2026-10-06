"""Normalized chart models: per-family discriminated union.

Stage: COMPILE (Output — parallel stack, types only)
Purpose: Discriminated-union mirror of authored families at the normalized stage.

Package layout (one family per file):
  _base.py      — base classes (never instantiated directly)
  bar.py        — BarChart
  line.py       — LineChart
  area.py       — AreaChart
  scatter.py    — ScatterChart
  heatmap.py    — HeatmapChart
  pie.py        — PieChart
  kpi.py        — KpiChart
  table.py      — TableChart
  point_map.py  — PointMapChart
  geoshape.py   — GeoshapeChart
  callout.py    — CalloutChart
  spark_bar.py  — SparkBarChart

Chart is a type alias over a Discriminator("type") union — not a BaseModel.
Use TypeAdapter(Chart) for validation.
"""

from __future__ import annotations

from typing import Annotated

from pydantic import Discriminator

from ._base import (
    NON_ASPECT_RATIO_TYPES,
    PAINTS_MARKS,
    SVG_LAYOUT_PADDED_TYPES,
    ChartDependencies,
    _BaseChartFields,
    _CartesianChartFields,
    _GeoChartFields,
    _SharedChartFields,
)
from .area import AreaChart
from .bar import BarChart
from .callout import CalloutChart
from .geoshape import GeoshapeChart
from .heatmap import HeatmapChart
from .kpi import KpiChart
from .line import LineChart
from .pie import PieChart
from .point_map import PointMapChart
from .scatter import ScatterChart
from .spark_bar import SparkBarChart
from .table import TableChart

Chart = Annotated[
    BarChart
    | LineChart
    | AreaChart
    | ScatterChart
    | HeatmapChart
    | PieChart
    | KpiChart
    | TableChart
    | PointMapChart
    | GeoshapeChart
    | CalloutChart
    | SparkBarChart,
    Discriminator("type"),
]
"""Normalized chart union discriminated on ``type:``.

Use ``TypeAdapter(Chart).validate_python(data)`` to obtain the correct family
model. Each variant's ``type:`` Literal is the discriminator key.
"""


__all__ = [
    # constants and helpers
    "NON_ASPECT_RATIO_TYPES",
    "PAINTS_MARKS",
    "SVG_LAYOUT_PADDED_TYPES",
    "ChartDependencies",
    # discriminated union
    "Chart",
    # families
    "AreaChart",
    "BarChart",
    "CalloutChart",
    "GeoshapeChart",
    "HeatmapChart",
    "KpiChart",
    "LineChart",
    "PieChart",
    "PointMapChart",
    "ScatterChart",
    "SparkBarChart",
    "TableChart",
    # bases (exported for isinstance checks and type hints in tests/resolve v2)
    "_BaseChartFields",
    "_CartesianChartFields",
    "_GeoChartFields",
    "_SharedChartFields",
]
