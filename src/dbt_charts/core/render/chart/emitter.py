"""ChartEmitter protocol — typed interface a family emitter must implement.

Each chart family (bar, line, kpi, …) provides a concrete emitter that takes its
specific ``Resolved*Chart`` type and produces a ``ChartSpec``.  Emitters are pure
functions from (resolved chart, data) → intermediate spec.

Usage::

    class BarEmitter:
        def emit(
            self,
            chart: ResolvedBarChart,
            box: RenderBox,
            dataset: ChartDataset,
        ) -> ChartSpec:
            ...
"""

from __future__ import annotations

from typing import Protocol, TypeVar, runtime_checkable

from dbt_charts.core.compile.models.chart.resolved import ResolvedChart
from dbt_charts.core.compile.resolve.chart._chart_rows import ChartDataset
from dbt_charts.core.render.chart.spec import ChartSpec, RenderBox

# TypeVar used to narrow the ``chart`` argument to a specific resolved family type.
# Each emitter implementation binds this to its own family (e.g. ``ResolvedBarChart``).
ResolvedChartT = TypeVar("ResolvedChartT", bound=ResolvedChart)


@runtime_checkable
class ChartEmitter(Protocol[ResolvedChartT]):
    """Protocol for a per-family chart emitter.

    An emitter translates a fully resolved chart model into a ``ChartSpec``
    intermediate.  It must NOT reach back into compile-time helpers (palette,
    channel resolution, style cascade) — those are already baked into the resolved
    models it receives.

    Args:
        chart: The resolved family model.  The concrete type is narrowed by the
            TypeVar ``ResolvedChartT`` at implementation time.
        box: Render-time slot geometry; emitters read ``box.width`` / ``box.height``
            for layout decisions instead of compile-time chart fields — both are
            already per-panel for a faceted chart (see ``RenderBox`` construction
            in ``vega_lite.py``).
        dataset: The chart's query rows, split into small-multiples panels
            (``axes == ()``, one panel, for a non-faceted chart — the N=1
            case). ``dataset.all_rows()`` is the flat, VL-wire-format row list;
            aggregating logic must iterate ``dataset.panels`` instead, never
            aggregate across ``all_rows()``.

    Returns:
        A mutable ``ChartSpec`` ready for the ``FeaturePipeline``.
    """

    def emit(
        self,
        chart: ResolvedChartT,
        box: RenderBox,
        dataset: ChartDataset,
    ) -> ChartSpec: ...
