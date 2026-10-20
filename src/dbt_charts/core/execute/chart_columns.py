"""The query columns a chart's channels name, and the check that a result has them.

Shared by ``dct validate --warehouse`` (columns from the warehouse's schema)
and render (columns from the rows the chart is about to draw): a channel naming
a column the query does not return plots nothing, so it is an error in both.
"""

from __future__ import annotations

from collections.abc import Collection, Mapping

from pydantic import BaseModel

from dbt_charts.core.compile.models.chart.normalized import Chart
from dbt_charts.core.compile.models.chart.normalized.area import AreaChart
from dbt_charts.core.compile.models.chart.normalized.bar import BarChart
from dbt_charts.core.compile.models.chart.normalized.kpi import KpiChart
from dbt_charts.core.compile.models.chart.normalized.line import LineChart
from dbt_charts.core.compile.models.chart.normalized.scatter import ScatterChart
from dbt_charts.core.compile.models.chart.normalized.table import TableChart
from dbt_charts.core.compile.normalize.charts import _FIELD_CHANNEL_KEYS
from dbt_charts.core.diagnostics import ERR_CHART_COLUMN_NOT_IN_RESULT
from dbt_charts.core.diagnostics.base import DbtChartsError
from dbt_charts.core.execute.cache_backend import CacheRows

# Every channel is authored as a bare column name (str or list[str]), so a miss
# is unambiguously an error. `geo` is a named boundary source ("us-states") and
# `sort` a sort-order reference, not display channels; excluding by difference
# means a channel added to _FIELD_CHANNEL_KEYS is checked automatically.
_CHANNEL_FIELDS: frozenset[str] = _FIELD_CHANNEL_KEYS - {"geo", "sort"}

ColumnRef = tuple[str, str, str]


def chart_column_refs(chart: Chart) -> list[ColumnRef]:
    """Return (query_name, channel_label, column_name) for every column channel.

    Walks the chart's own channels plus each cartesian ``layer``, which carries
    its own ``x``/``y``/``color`` and is where a mistyped column hides most
    easily. A layer's ``query:`` overrides the chart's, so each ref carries the
    query whose result actually has to contain it.
    """
    refs: list[ColumnRef] = []

    def collect(source: BaseModel, prefix: str, query_name: str | None) -> None:
        if query_name is None:
            return
        for field_name in sorted(_CHANNEL_FIELDS & type(source).model_fields.keys()):
            value = getattr(source, field_name)
            if isinstance(value, str):
                refs.append((query_name, f"{prefix}{field_name}", value))
            elif isinstance(value, list):
                refs.extend(
                    (query_name, f"{prefix}{field_name}[{i}]", item)
                    for i, item in enumerate(value)
                    if isinstance(item, str)
                )
        if isinstance(source, TableChart) and source.columns is not None:
            refs.extend(
                (query_name, f"{prefix}columns[{i}]", column)
                for i, column in enumerate(source.columns)
            )

    collect(chart, "", chart.query_name)
    if isinstance(chart, (BarChart, LineChart, AreaChart, ScatterChart)):
        for i, layer in enumerate(chart.layers):
            collect(layer, f"layers[{i}].", layer.query or chart.query_name)
    return refs


def missing_column_error(
    chart_id: str, ref: ColumnRef, available: Collection[str]
) -> DbtChartsError:
    """The error for a channel whose column the query's result lacks."""
    query_name, channel, column = ref
    return DbtChartsError.from_code(
        ERR_CHART_COLUMN_NOT_IN_RESULT,
        chart=chart_id,
        channel=channel,
        column=column,
        name=query_name,
        columns=sorted(available),
    )


def raise_missing_chart_column(
    chart: Chart, data: CacheRows, layer_datasets: Mapping[str, CacheRows]
) -> None:
    """Raise when a channel names a column absent from its query's rows.

    ``data`` is the chart's own query result; a layer's own query is read from
    ``layer_datasets``, the same rows its draw reads. A zero-row result carries
    no column names and passes — the zero-row warning owns it. Tables and KPIs
    already name a missing column more specifically (ERR-TABLE-PIVOT-FIELD-
    NOT-FOUND, ERR-KPI-VALUE-COLUMN-NOT-FOUND, and resolve's ``background``
    check), so they are left to those.
    """
    if isinstance(chart, (KpiChart, TableChart)):
        return
    result_columns: dict[str, set[str]] = {}
    for ref in chart_column_refs(chart):
        query_name = ref[0]
        if query_name not in result_columns:
            query_rows = (
                data if query_name == chart.query_name else layer_datasets[query_name]
            )
            result_columns[query_name] = set().union(*query_rows)
        available = result_columns[query_name]
        if available and ref[2] not in available:
            raise missing_column_error(chart.id, ref, available)
