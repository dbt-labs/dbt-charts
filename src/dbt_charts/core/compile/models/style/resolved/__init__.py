"""Resolved style models — per-family layout.

Stage: COMPILE (parallel stack, types only)

Layout
------
_base.py       — shared post-cascade dataclasses (ResolvedStyle — final board/
                 chrome presentation only — ResolvedAxisStyle, ResolvedLegendStyle, …)
_cartesian.py  — Pydantic base models for cartesian slices (_CartesianResolvedStyle,
                 _SeriesCartesianResolvedStyle)
_marks.py      — shared resolved mark sub-types (ResolvedStrokeStyle,
                 ResolvedAreaMarkStyle, ResolvedLineMarkStyle)
bar.py         — ResolvedBarStyle
line.py        — ResolvedLineStyle
area.py        — ResolvedAreaStyle
scatter.py     — ResolvedScatterStyle
heatmap.py     — ResolvedHeatmapStyle
pie.py         — ResolvedPieStyle
callout.py     — ResolvedCalloutStyle, ResolvedCalloutElementStyle
kpi.py         — ResolvedKpiStyle
table.py       — ResolvedTableStyle
geoshape.py    — ResolvedGeoshapeStyle
point_map.py   — ResolvedPointMapStyle
spark_bar.py   — ResolvedSparkBarStyle

All public names are re-exported here so callers import from
``dbt_charts.core.compile.models.style.resolved`` without knowing the
internal file layout.

``ChartStyleContext`` (chart-family style cascade working state — sparse
axis overlays, patch sentinels, palette/role token bindings, the pre-inherit
tree) is NOT part of this package — it lives at the non-resolved
``dbt_charts.core.compile.models.style.context`` module, since it is not a
final render contract. ``resolve_chart_style_context()``
(``dbt_charts.core.compile.resolve.style.board``) constructs it.

The cascade resolution engine (``resolve_style``, ``resolve_chart_style_context``,
``resolve_cascaded_font``, ``build_resolved_axis``, color-token and emoji
helpers) is not part of this package either — it lives at
``dbt_charts.core.compile.resolve.style`` (``board.py``, ``axis_cascade.py``,
``tokens.py``, ``scale.py``, ``chart_context.py``), which imports from here
rather than the reverse.

VL config mapping (style_to_vega_lite, effective_vega_config) is compile-domain
logic, not a model definition — it lives at
``dbt_charts.core.compile.vega_lite.mapping``.
"""

from dbt_charts.core.compile.models.style.resolved._base import (
    ResolvedAxisElementStyle,
    ResolvedAxisGridStyle,
    ResolvedAxisGridThresholdStyle,
    ResolvedAxisLabelOverlapConfig,
    ResolvedAxisLineStyle,
    ResolvedAxisStyle,
    ResolvedAxisTicksStyle,
    ResolvedChartDefaults,
    ResolvedLegendElementStyle,
    ResolvedLegendPosition,
    ResolvedLegendStyle,
    ResolvedMirrorAxis,
    ResolvedRulerAxis,
    ResolvedScaleContinuousStyle,
    ResolvedScaleLogStyle,
    ResolvedScalePowStyle,
    ResolvedScaleStyle,
    ResolvedScaleSymlogStyle,
    ResolvedStyle,
    ResolvedTickLabel,
    _VLConfig,
    effective_padding,
)
from dbt_charts.core.compile.models.style.resolved._marks import (
    ResolvedAreaLineStyle,
    ResolvedAreaMarkStyle,
    ResolvedBarLabels,
    ResolvedBarMarkStyle,
    ResolvedBarTotalLabel,
    ResolvedLineMarkStyle,
    ResolvedMarkLabels,
    ResolvedPointLabels,
    ResolvedPointMarkStyle,
    ResolvedSeriesLabelStyle,
    ResolvedStrokeStyle,
)
from dbt_charts.core.compile.models.style.resolved.area import ResolvedAreaStyle
from dbt_charts.core.compile.models.style.resolved.bar import ResolvedBarStyle
from dbt_charts.core.compile.models.style.resolved.callout import (
    ResolvedCalloutElementStyle,
    ResolvedCalloutStyle,
)
from dbt_charts.core.compile.models.style.resolved.geoshape import (
    ResolvedGeoshapeChartStyle,
    ResolvedGeoshapeStyle,
    ResolvedStaticGradientColorStyle,
)
from dbt_charts.core.compile.models.style.resolved.heatmap import ResolvedHeatmapStyle
from dbt_charts.core.compile.models.style.resolved.kpi import ResolvedKpiStyle
from dbt_charts.core.compile.models.style.resolved.line import ResolvedLineStyle
from dbt_charts.core.compile.models.style.resolved.pie import (
    ResolvedPieStyle,
    ResolvedTotalStyle,
    ResolvedTotalValueSlot,
)
from dbt_charts.core.compile.models.style.resolved.point_map import (
    ResolvedPointMapStyle,
)
from dbt_charts.core.compile.models.style.resolved.scatter import ResolvedScatterStyle
from dbt_charts.core.compile.models.style.resolved.spark_bar import (
    ResolvedSparkBarStyle,
)
from dbt_charts.core.compile.models.style.resolved.table import (
    ResolvedColumnScaleConfig,
    ResolvedColumnSharedScale,
    ResolvedTableColumnConfig,
    ResolvedTableStyle,
)

__all__ = [
    # _base
    "ResolvedAxisElementStyle",
    "ResolvedAxisGridStyle",
    "ResolvedAxisGridThresholdStyle",
    "ResolvedAxisLabelOverlapConfig",
    "ResolvedAxisLineStyle",
    "ResolvedAxisStyle",
    "ResolvedAxisTicksStyle",
    "ResolvedCalloutElementStyle",
    "ResolvedCalloutStyle",
    "ResolvedChartDefaults",
    "ResolvedLegendElementStyle",
    "ResolvedLegendPosition",
    "ResolvedLegendStyle",
    "ResolvedMirrorAxis",
    "ResolvedRulerAxis",
    "ResolvedScaleContinuousStyle",
    "ResolvedScaleLogStyle",
    "ResolvedScalePowStyle",
    "ResolvedScaleStyle",
    "ResolvedScaleSymlogStyle",
    "ResolvedStyle",
    "ResolvedTickLabel",
    "_VLConfig",
    # _base (public function)
    "effective_padding",
    # _marks
    "ResolvedAreaLineStyle",
    "ResolvedAreaMarkStyle",
    "ResolvedBarLabels",
    "ResolvedBarMarkStyle",
    "ResolvedBarTotalLabel",
    "ResolvedMarkLabels",
    "ResolvedPointLabels",
    "ResolvedPointMarkStyle",
    "ResolvedLineMarkStyle",
    "ResolvedSeriesLabelStyle",
    "ResolvedStrokeStyle",
    # per-family slices
    "ResolvedAreaStyle",
    "ResolvedBarStyle",
    "ResolvedColumnScaleConfig",
    "ResolvedColumnSharedScale",
    "ResolvedGeoshapeChartStyle",
    "ResolvedGeoshapeStyle",
    "ResolvedStaticGradientColorStyle",
    "ResolvedHeatmapStyle",
    "ResolvedKpiStyle",
    "ResolvedLineStyle",
    "ResolvedPieStyle",
    "ResolvedTotalStyle",
    "ResolvedTotalValueSlot",
    "ResolvedPointMapStyle",
    "ResolvedScatterStyle",
    "ResolvedSparkBarStyle",
    "ResolvedTableColumnConfig",
    "ResolvedTableStyle",
]
