"""Registry of render-v2 family emitters.

``get_emitter(chart)`` dispatches on the resolved chart type and returns the
appropriate emitter instance.  Each emitter exposes an ``emit()`` method that
produces a ``ChartSpec``.
"""

from __future__ import annotations

from typing import Any

from dbt_charts.core.compile.models.chart.resolved import (
    ResolvedAreaChart,
    ResolvedBarChart,
    ResolvedGeoshapeChart,
    ResolvedHeatmapChart,
    ResolvedLineChart,
    ResolvedPointMapChart,
    ResolvedScatterChart,
)
from dbt_charts.core.diagnostics.codes_render import ERR_EMITTER_NOT_FOUND
from dbt_charts.core.render.chart.emitter import ChartEmitter
from dbt_charts.core.render.chart.emitters.area import AreaEmitter
from dbt_charts.core.render.chart.emitters.bar import BarEmitter
from dbt_charts.core.render.chart.emitters.geo import GeoshapeEmitter, PointMapEmitter
from dbt_charts.core.render.chart.emitters.heatmap import HeatmapEmitter
from dbt_charts.core.render.chart.emitters.line import LineEmitter
from dbt_charts.core.render.chart.emitters.scatter import ScatterEmitter
from dbt_charts.core.render.errors import RenderError

_BAR_EMITTER = BarEmitter()
_LINE_EMITTER = LineEmitter()
_AREA_EMITTER = AreaEmitter()
_SCATTER_EMITTER = ScatterEmitter()
_HEATMAP_EMITTER = HeatmapEmitter()
_GEOSHAPE_EMITTER = GeoshapeEmitter()
_POINT_MAP_EMITTER = PointMapEmitter()


def get_emitter(chart: Any) -> ChartEmitter[Any]:
    """Return the emitter for the given resolved chart type."""
    match chart:
        case ResolvedBarChart():
            return _BAR_EMITTER
        case ResolvedLineChart():
            return _LINE_EMITTER
        case ResolvedAreaChart():
            return _AREA_EMITTER
        case ResolvedScatterChart():
            return _SCATTER_EMITTER
        case ResolvedHeatmapChart():
            return _HEATMAP_EMITTER
        case ResolvedGeoshapeChart():
            return _GEOSHAPE_EMITTER
        case ResolvedPointMapChart():
            return _POINT_MAP_EMITTER
        case _:
            raise RenderError.from_code(
                ERR_EMITTER_NOT_FOUND, resolved_type=type(chart).__name__
            )
