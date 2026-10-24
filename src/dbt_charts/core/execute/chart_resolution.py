"""Gather runtime facts and construct finalized resolved charts.

Stage: EXECUTE. This module executes the queries needed to resolve shared
y-axis datasets, then passes those facts once into compile resolution.
"""

from __future__ import annotations

from collections.abc import Iterator, Mapping, Sequence
from datetime import date
from types import EllipsisType, MappingProxyType
from typing import TYPE_CHECKING, Any

from dbt_charts.core.compile.models.board.normalized import VariableValues
from dbt_charts.core.compile.models.chart.authored import BarLayer
from dbt_charts.core.compile.models.chart.normalized.area import AreaChart
from dbt_charts.core.compile.models.chart.normalized.bar import BarChart
from dbt_charts.core.compile.models.chart.normalized.line import LineChart
from dbt_charts.core.compile.models.chart.normalized.scatter import ScatterChart
from dbt_charts.core.compile.models.primitives import LinkOpen, ResolvedFontStyle
from dbt_charts.core.compile.resolve import (
    AutomaticLinkCandidate,
    layers_own_y_scale,
    resolve,
)
from dbt_charts.core.compile.template.display_text import DisplayTextInputs
from dbt_charts.core.compile.template.query_result import QueryResult
from dbt_charts.core.execute.cache_backend import CacheRows
from dbt_charts.core.execute.chart_columns import raise_missing_chart_column
from dbt_charts.core.execute.column_formats import raise_column_format_error
from dbt_charts.core.execute.date_range_axis import proven_range
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
    own_scales = layers_own_y_scale(chart)
    for layer in chart.layers:
        if own_scales and not (
            isinstance(layer, BarLayer) and layer.y_start is not None
        ):
            continue
        query_name = layer.query
        if query_name is not None and query_name not in datasets:
            datasets[query_name] = executor.execute_query(query_name, variables)
    return datasets


class _LayerRows(Mapping[str, CacheRows]):
    """Shared-scale rows plus every other own-query layer's, read on demand.

    A layer's format vote reads its own rows wherever the layer is drawn, but a
    layer on its own y scale never widens the primary one, so its query is
    executed only when a vote actually reads it.
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


def collect_display_text_inputs(
    chart: Chart,
    executor: Executor,
    variables: VariableValues,
) -> DisplayTextInputs:
    """The variables and query results a chart's display text reads."""
    return DisplayTextInputs(
        variables,
        {
            name: QueryResult(name, executor.execute_query(key, variables))
            for name, key in chart.text_queries.items()
        },
    )


def chart_x_date_range(
    chart: Chart, executor: Executor, variables: VariableValues
) -> tuple[date, date] | None:
    """The range a bar/line/area chart's x column is proven filtered to, or None."""
    if (
        not isinstance(chart, (BarChart, LineChart, AreaChart))
        or chart.x is None
        or chart.query_name is None
        or any(
            layer.query is not None or layer.x not in (None, chart.x)
            for layer in chart.layers
        )
    ):
        return None
    rendered = executor.render_with_range_filters(chart.query_name, variables)
    if rendered is None:
        return None
    sql, dialect, filters = rendered
    return proven_range(sql, dialect, chart.x, filters)


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
    *,
    filter_variables: frozenset[str],
    link_open: LinkOpen,
    title_width: float | None = None,
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
    raise_column_format_error(chart, executor)
    layer_datasets = collect_layer_datasets(chart, data, executor, variables)
    resolved = resolve(
        chart,
        data,
        chart_style_context=chart_style_context,
        width=width or None,
        datasets=layer_datasets,
        automatic_link_candidate=automatic_link_candidate,
        table_column_links=table_column_links,
        table_column_rows=table_column_rows,
        pie_title_font=pie_title_font,
        filter_variables=filter_variables,
        link_open=link_open,
        display_text=collect_display_text_inputs(chart, executor, variables),
        rendered_at=executor.rendered_at,
        x_date_range=chart_x_date_range(chart, executor, variables),
        title_width=title_width,
    )
    # After resolve, so a channel resolve diagnoses more specifically (a literal
    # color where a column belongs) keeps that message.
    raise_missing_chart_column(chart, data, layer_datasets)
    return resolved
