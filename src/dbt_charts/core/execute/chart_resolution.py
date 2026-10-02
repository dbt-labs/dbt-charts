"""Gather runtime facts and construct finalized resolved charts.

Stage: EXECUTE. This module executes the queries needed to resolve shared
y-axis datasets, then passes those facts once into compile resolution.
"""

from __future__ import annotations

from collections.abc import Iterator, Mapping, Sequence
from types import EllipsisType, MappingProxyType
from typing import TYPE_CHECKING, Any

from dbt_charts.core.compile.models.board.normalized import VariableValues
from dbt_charts.core.compile.models.chart.authored import BarLayer
from dbt_charts.core.compile.models.chart.normalized.area import AreaChart
from dbt_charts.core.compile.models.chart.normalized.bar import BarChart
from dbt_charts.core.compile.models.chart.normalized.line import LineChart
from dbt_charts.core.compile.models.chart.normalized.scatter import ScatterChart
from dbt_charts.core.compile.models.primitives import ResolvedFontStyle
from dbt_charts.core.compile.resolve import (
    AutomaticLinkCandidate,
    resolve,
)
from dbt_charts.core.execute.cache_backend import CacheRows
from dbt_charts.core.execute.executor import Executor

if TYPE_CHECKING:
    from dbt_charts.core.compile.models.chart.normalized import Chart
    from dbt_charts.core.compile.models.chart.resolved import ResolvedChart
    from dbt_charts.core.compile.models.style.context import ChartStyleContext

_EMPTY_TABLE_COLUMN_LINKS: Mapping[str, str] = MappingProxyType({})


def collect_shared_y_datasets(
    chart: Chart,
    data: CacheRows,
    executor: Executor,
    variables: VariableValues,
) -> dict[str, CacheRows]:
    """Return base and own-query rows drawn against the primary y scale, plus
    any bar layer with y_start, whose span columns compile checks."""
    datasets: dict[str, CacheRows] = {}
    if not isinstance(chart, (BarChart, LineChart, AreaChart, ScatterChart)):
        return datasets
    if chart.query_name is not None:
        datasets[chart.query_name] = data
    for layer in chart.layers:
        if (
            layer.axis_y is not None
            and layer.axis_y.position == "right"
            and not (isinstance(layer, BarLayer) and layer.y_start is not None)
        ):
            continue
        query_name = layer.query
        if query_name is not None and query_name not in datasets:
            datasets[query_name] = executor.execute_query(query_name, variables)
    return datasets


class _LayerRows(Mapping[str, CacheRows]):
    """Shared-scale rows plus every other own-query layer's, read on demand.

    A layer's format vote reads its own rows wherever the layer is drawn, but a
    right-axis layer never widens the primary scale, so its query is executed
    only when a vote actually reads it.
    """

    def __init__(
        self,
        rows: dict[str, CacheRows],
        deferred: set[str],
        executor: Executor,
        variables: VariableValues,
    ) -> None:
        self._rows = rows
        self._deferred = deferred - rows.keys()
        self._executor = executor
        self._variables = variables

    def __getitem__(self, query_name: str) -> CacheRows:
        if query_name not in self._rows:
            if query_name not in self._deferred:
                raise KeyError(query_name)
            self._rows[query_name] = self._executor.execute_query(
                query_name, self._variables
            )
        return self._rows[query_name]

    def __contains__(
        self,
        query_name: object,  # type-state: object_annotation — Mapping override
    ) -> bool:
        return query_name in self._rows or query_name in self._deferred

    def __iter__(self) -> Iterator[str]:
        yield from self._rows
        yield from self._deferred - self._rows.keys()

    def __len__(self) -> int:
        return len(self._rows.keys() | self._deferred)


def collect_layer_datasets(
    chart: Chart,
    data: CacheRows,
    executor: Executor,
    variables: VariableValues,
) -> Mapping[str, CacheRows]:
    """``collect_shared_y_datasets`` plus lazy access to every other layer query."""
    shared = collect_shared_y_datasets(chart, data, executor, variables)
    if not isinstance(chart, (BarChart, LineChart, AreaChart, ScatterChart)):
        return shared
    own_queries = {layer.query for layer in chart.layers if layer.query is not None}
    return _LayerRows(shared, own_queries, executor, variables)


def resolve_chart_with_runtime_inputs(
    chart: Chart,
    data: list[dict[str, Any]],
    chart_style_context: ChartStyleContext,
    width: float,
    executor: Executor,
    variables: VariableValues,
    automatic_link_candidate: AutomaticLinkCandidate = None,
    table_column_links: Mapping[str, str] = _EMPTY_TABLE_COLUMN_LINKS,
    table_column_rows: Sequence[Mapping[str, str]] = (),
    pie_title_font: ResolvedFontStyle | EllipsisType = ...,
) -> ResolvedChart:
    """Gather shared-layer datasets, then construct one final chart.

    Compile owns every semantic axis decision. Execute only fetches the sibling
    layer queries drawn against the same scale and supplies their rows.

    ``width`` follows ``LayoutItem``'s own "unmeasured" convention: a real
    caller always measures a nonzero width, so its literal default of
    ``0.0`` (never actually placed) is passed to ``resolve()`` as ``None``
    rather than a concrete zero — the same substitution every non-placed
    chart gets, so pie's family-aware default (authored ``width:`` first)
    applies instead of a literal divide-by-zero width.
    """
    layer_datasets = collect_layer_datasets(chart, data, executor, variables)
    return resolve(
        chart,
        data,
        chart_style_context=chart_style_context,
        width=width or None,
        datasets=layer_datasets,
        automatic_link_candidate=automatic_link_candidate,
        table_column_links=table_column_links,
        table_column_rows=table_column_rows,
        variables=variables,
        pie_title_font=pie_title_font,
    )
