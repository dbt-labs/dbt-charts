"""Authored chart models: per-family discriminated union for AuthoredChart.

Stage: COMPILE (Input)
Purpose: Define all chart-specific authored types that map directly to the YAML schema.

Package layout (one family per file, mirroring the initiative's anti-mega-module goal):
  _base.py                    — base classes (never instantiated directly)
  _type.py                    — ChartType enum and UI display metadata
  _conditional_formatting.py  — predicate rules and column-scoped rule sets
  _support_table.py              — chart.support_table attached mini-table primitive
  _annotations.py             — ChartSort, ChartTotal
  _layer.py                   — per-layer authored input for layered charts
  bar.py        — BarChart
  line.py       — LineChart
  area.py       — AreaChart
  scatter.py    — ScatterChart
  heatmap.py    — HeatmapChart
  pie.py        — PieChart
  kpi.py        — KpiChart, KpiSupportConfig
  table.py      — TableChart
  point_map.py  — PointMapChart
  geoshape.py   — GeoshapeChart
  callout.py    — CalloutChart
  spark_bar.py  — SparkBarChart

Families: BarChart, LineChart, AreaChart, ScatterChart, HeatmapChart, PieChart, KpiChart,
    TableChart, PointMapChart, GeoshapeChart, CalloutChart, SparkBarChart.
    AuthoredChart is a type alias over a discriminated union. type: is mandatory — missing
    or unknown type raises a ValidationError at the authored-model level.
"""

from __future__ import annotations

from typing import Annotated, Any, get_args

from pydantic import BaseModel, Discriminator, Tag

from dbt_charts.core.compile.models.primitives import (
    FontStyle as FontStyle,  # re-exported for external callers
    FormatConfig as FormatConfig,  # re-exported for external callers
    ScaleTargetConfig as ScaleTargetConfig,  # re-exported for external callers
    ToneLiteral as ToneLiteral,  # re-exported from its canonical home
)
from dbt_charts.core.compile.models.style.authored import (
    _SPARK_RENAMED_AWAY as _SPARK_RENAMED_AWAY,  # re-exported for external callers
    ColumnScaleConfig as ColumnScaleConfig,  # re-exported for external callers
    SparkConfig as SparkConfig,  # re-exported for external callers
    SparkTypeLiteral as SparkTypeLiteral,  # re-exported for external callers
    TableColumnConfig as TableColumnConfig,  # re-exported for external callers
)

from ._annotations import ChartSort, ChartTotal
from ._base import (
    BasemapConfig,
    MultiplesConfig,
    _BaseChartFields,
    _CartesianChartFields,
    _GeoChartFields,
    _RadialChartFields,
    _SharedChartFields,
)
from ._conditional_formatting import (
    _PREDICATE_OPS,
    ConditionalRule,
    FieldConditionalFormatting,
    _PredicateBase,
    match_predicate,
)
from ._layer import (
    AreaLayer,
    BarLayer,
    CartesianLayer,
    LayerAxisYStyle,
    LineLayer,
    ScatterLayer,
)
from ._support_table import (
    CHART_SUPPORT_TABLE_SUPPORTED_TYPES,
    ChartSupportTable,
    ChartSupportTableAggregate,
    ChartSupportTableAggregateOp,
    ChartSupportTableEntry,
    ChartSupportTableOrList,
    ChartSupportTablePerSeries,
    ChartSupportTableSource,
    validate_support_table_shape,
)
from ._type import (
    _INTERNAL_CHART_TYPES,
    CHART_TYPE_DISPLAY,
    ChartType,
)
from .area import AreaChart
from .bar import BarChart
from .callout import CalloutChart
from .geoshape import GeoshapeChart
from .heatmap import HeatmapChart
from .kpi import KpiChart, KpiSupportConfig
from .line import LineChart
from .pie import PieChart
from .point_map import PointMapChart
from .scatter import ScatterChart
from .spark_bar import SparkBarChart
from .table import TableChart


def _discriminate_authored_chart(v: Any) -> str | None:
    """Custom discriminator: return type tag for the AuthoredChart union.

    Returns None for unknown or missing type, which triggers Pydantic's
    union_tag_not_found ValidationError — explicit and loud.
    """
    if isinstance(v, dict):
        t = v.get("type")
    elif isinstance(v, _BaseChartFields):
        t = v.type
    elif isinstance(v, BaseModel):
        t = v.__dict__.get("type")
    else:
        return None
    return t if isinstance(t, str) and t in _DISCRIMINATED_AUTHORED_TYPES else None


AuthoredChart = Annotated[
    Annotated[BarChart, Tag("bar")]
    | Annotated[BarChart, Tag("histogram")]
    | Annotated[LineChart, Tag("line")]
    | Annotated[AreaChart, Tag("area")]
    | Annotated[ScatterChart, Tag("scatter")]
    | Annotated[HeatmapChart, Tag("heatmap")]
    | Annotated[PieChart, Tag("pie")]
    | Annotated[PieChart, Tag("donut")]
    | Annotated[KpiChart, Tag("kpi")]
    | Annotated[TableChart, Tag("table")]
    | Annotated[PointMapChart, Tag("point_map")]
    | Annotated[PointMapChart, Tag("bubble_map")]
    | Annotated[GeoshapeChart, Tag("map")]
    | Annotated[GeoshapeChart, Tag("geoshape")]
    | Annotated[CalloutChart, Tag("callout")]
    | Annotated[SparkBarChart, Tag("spark_bar")],
    Discriminator(_discriminate_authored_chart),
]
"""Discriminated union of per-family authored chart patches.

AuthoredChart is a type alias, not a BaseModel. Use TypeAdapter(AuthoredChart) for
validation. Use isinstance(item, _BaseChartFields) to check if an object
is any kind of chart patch. type: is mandatory; missing or unknown type raises
a union_tag_not_found ValidationError.
"""


def _authored_chart_type_tag(chart_variant: Any) -> str:
    for metadata in get_args(chart_variant)[1:]:
        if isinstance(metadata, Tag):
            return metadata.tag
    raise TypeError(
        f"AuthoredChart variant is missing a Pydantic Tag: {chart_variant!r}"
    )


def _authored_chart_type_tags(authored_chart: Any) -> tuple[str, ...]:
    authored_union = get_args(authored_chart)[0]
    return tuple(
        _authored_chart_type_tag(chart_variant)
        for chart_variant in get_args(authored_union)
    )


def _build_authored_chart_variants(authored_chart: Any) -> dict[str, str]:
    """Return tag -> model class name mapping from the AuthoredChart union."""
    authored_union = get_args(authored_chart)[0]
    result: dict[str, str] = {}
    for chart_variant in get_args(authored_union):
        variant_args = get_args(chart_variant)
        model_cls = variant_args[0]
        tag = _authored_chart_type_tag(chart_variant)
        result[tag] = model_cls.__name__
    return result


# Type values that have a dedicated family patch class in AuthoredChart.
AUTHORED_CHART_TYPE_TAGS: tuple[str, ...] = _authored_chart_type_tags(AuthoredChart)

# tag -> family class name (e.g. "bar" -> "BarChart", "histogram" -> "BarChart").
AUTHORED_CHART_VARIANTS: dict[str, str] = _build_authored_chart_variants(AuthoredChart)
SUPPORTED_AUTHORED_CHART_TYPES: frozenset[str] = frozenset(AUTHORED_CHART_TYPE_TAGS)
_DISCRIMINATED_AUTHORED_TYPES: frozenset[str] = SUPPORTED_AUTHORED_CHART_TYPES

GEO_CHART_TYPES: frozenset[str] = frozenset(
    {"map", "geoshape", "point_map", "bubble_map"}
)

__all__ = [
    # re-exported primitives
    "FontStyle",
    "FormatConfig",
    "ScaleTargetConfig",
    "_SPARK_RENAMED_AWAY",
    "ColumnScaleConfig",
    "SparkConfig",
    "SparkTypeLiteral",
    "TableColumnConfig",
    # chart type enum
    "CHART_TYPE_DISPLAY",
    "ChartType",
    "_INTERNAL_CHART_TYPES",
    # conditional formatting
    "ConditionalRule",
    "FieldConditionalFormatting",
    "_PREDICATE_OPS",
    "_PredicateBase",
    "match_predicate",
    # support_table
    "CHART_SUPPORT_TABLE_SUPPORTED_TYPES",
    "ChartSupportTable",
    "ChartSupportTableAggregate",
    "ChartSupportTableAggregateOp",
    "ChartSupportTableEntry",
    "ChartSupportTableOrList",
    "ChartSupportTablePerSeries",
    "ChartSupportTableSource",
    "validate_support_table_shape",
    # annotations
    "ChartSort",
    "ChartTotal",
    "MultiplesConfig",
    # layer
    "AreaLayer",
    "BarLayer",
    "LayerAxisYStyle",
    "CartesianLayer",
    "LineLayer",
    "ScatterLayer",
    # bases (exported for isinstance checks and type hints)
    "BasemapConfig",
    "_BaseChartFields",
    "_CartesianChartFields",
    "_GeoChartFields",
    "_RadialChartFields",
    "_SharedChartFields",
    # families
    "AreaChart",
    "BarChart",
    "CalloutChart",
    "GeoshapeChart",
    "HeatmapChart",
    "KpiChart",
    "KpiSupportConfig",
    "LineChart",
    "PieChart",
    "PointMapChart",
    "ScatterChart",
    "SparkBarChart",
    "TableChart",
    "ToneLiteral",
    # discriminated union
    "AuthoredChart",
    "AUTHORED_CHART_TYPE_TAGS",
    "AUTHORED_CHART_VARIANTS",
    "SUPPORTED_AUTHORED_CHART_TYPES",
    "GEO_CHART_TYPES",
]
