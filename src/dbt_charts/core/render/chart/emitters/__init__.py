"""Registry of render-v2 family emitters.

``get_emitter(chart)`` dispatches on the resolved chart type and returns the
appropriate emitter instance.  Each emitter exposes an ``emit()`` method that
produces a ``ChartSpec``. Only the geo maps are Vega-Lite; every other family
renders in the Rust engine (``render/engine.py``).
"""

from __future__ import annotations

from typing import Any

from dbt_charts.core.compile.models.chart.resolved import (
    ResolvedGeoshapeChart,
    ResolvedPointMapChart,
)
from dbt_charts.core.diagnostics.codes_render import ERR_EMITTER_NOT_FOUND
from dbt_charts.core.render.chart.emitter import ChartEmitter
from dbt_charts.core.render.chart.emitters.geo import GeoshapeEmitter, PointMapEmitter
from dbt_charts.core.render.errors import RenderError

_GEOSHAPE_EMITTER = GeoshapeEmitter()
_POINT_MAP_EMITTER = PointMapEmitter()


def get_emitter(chart: Any) -> ChartEmitter[Any]:
    """Return the emitter for the given resolved chart type."""
    match chart:
        case ResolvedGeoshapeChart():
            return _GEOSHAPE_EMITTER
        case ResolvedPointMapChart():
            return _POINT_MAP_EMITTER
        case _:
            raise RenderError.from_code(
                ERR_EMITTER_NOT_FOUND, resolved_type=type(chart).__name__
            )
