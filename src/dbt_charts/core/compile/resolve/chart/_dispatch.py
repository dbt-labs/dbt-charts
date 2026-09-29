"""Public resolve() dispatch across chart families."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from types import EllipsisType

from dbt_charts.core.compile.models.chart.normalized import (
    AreaChart,
    BarChart,
    Chart,
    HeatmapChart,
    LineChart,
    ScatterChart,
    SparkBarChart,
)
from dbt_charts.core.compile.models.chart.resolved import (
    ResolvedChart,
)
from dbt_charts.core.compile.models.primitives import ResolvedFontStyle
from dbt_charts.core.compile.models.style.context import ChartStyleContext
from dbt_charts.core.compile.resolve.chart._chart_rows import (
    ChartRows,
    LayerDatasets,
    partition,
    require_sort_field,
)
from dbt_charts.core.compile.resolve.chart._kwargs import (
    _EMPTY_CHART_TEXT_VARIABLES,
    AutomaticLinkCandidate,
    ChartTextVariables,
    preferred_chart_width,
)
from dbt_charts.core.compile.resolve.chart._table import (
    _EMPTY_TABLE_COLUMN_LINKS,
    _resolve_table,
)
from dbt_charts.core.compile.resolve.chart.area import _resolve_area
from dbt_charts.core.compile.resolve.chart.bar import (
    _resolve_bar,
    _resolve_histogram,
)
from dbt_charts.core.compile.resolve.chart.geo import (
    _resolve_geoshape,
    _resolve_point_map,
)
from dbt_charts.core.compile.resolve.chart.heatmap import _resolve_heatmap
from dbt_charts.core.compile.resolve.chart.line import _resolve_line
from dbt_charts.core.compile.resolve.chart.pie import _resolve_pie
from dbt_charts.core.compile.resolve.chart.scatter import _resolve_scatter
from dbt_charts.core.compile.resolve.chart.simple import (
    _resolve_callout,
    _resolve_kpi,
    _resolve_spark_bar,
)

__all__ = [
    "resolve",
]


def resolve(
    normalized: Chart,
    data: ChartRows,
    chart_style_context: ChartStyleContext,
    width: float | None = None,
    datasets: LayerDatasets = ...,
    automatic_link_candidate: AutomaticLinkCandidate = None,
    table_column_links: Mapping[str, str] = _EMPTY_TABLE_COLUMN_LINKS,
    table_column_rows: Sequence[Mapping[str, str]] = (),
    variables: ChartTextVariables = _EMPTY_CHART_TEXT_VARIABLES,
    pie_title_font: ResolvedFontStyle | EllipsisType = ...,
) -> ResolvedChart:
    """Dispatch a normalized chart → ResolvedChart.

    Bakes style cascade, channel resolution, and row-sample enrichments
    (type inference, bar orientation) into the resolved model.

    Args:
        normalized: Discriminated normalized chart (from normalize_chart or direct construction).
        data: Query result rows — used for row-sample enrichment only.
        chart_style_context: Board-level ChartStyleContext for the containing board.
        width: Layout pixel width of the chart card; drives title_font tier selection.
            Pie and donut use their family width when None because attachment policy
            needs a concrete width; other families retain the board-level default.
        datasets: Query-name keyed rows for own-query overlay layers. Callers without
            an executor may omit it; same-query layers still resolve from ``data``.
        automatic_link_candidate: Executor-derived root-link candidate. Authored
            links retain precedence during construction.
        table_column_links: Executor-derived FK-link candidates by column name.
        table_column_rows: Factual column metadata used to protect a single row
            identity key from receiving a per-column FK link.
        variables: Request-local board variables used to finalize chart display
            text. An empty mapping keeps authored templates literal for static
            resolution.
        pie_title_font: Title typography already finalized for a repeated pie chart.
            Placement-specific pie policy still uses ``width``; this preserves the
            chart-level title decision shared by every placement of that chart.

    Returns:
        The matching ResolvedXxxChart instance with palette/dashes/channels populated.
    """
    effective_width = (
        float(width)
        if width is not None
        else (
            preferred_chart_width(normalized, chart_style_context)
            if normalized.type in {"pie", "donut"}
            else float(chart_style_context.preferred_width)
        )
    )
    if isinstance(
        normalized,
        (AreaChart, BarChart, HeatmapChart, LineChart, ScatterChart, SparkBarChart),
    ):
        require_sort_field(normalized.sort, data)
    match normalized.type:
        case "bar":
            return _resolve_bar(
                normalized,
                partition(normalized.multiples, data),
                chart_style_context,
                effective_width,
                datasets,
                automatic_link_candidate,
                variables,
            )
        case "histogram":
            return _resolve_histogram(
                normalized,
                partition(normalized.multiples, data),
                chart_style_context,
                effective_width,
                automatic_link_candidate,
                variables,
            )
        case "line":
            return _resolve_line(
                normalized,
                partition(normalized.multiples, data),
                chart_style_context,
                effective_width,
                datasets,
                automatic_link_candidate,
                variables,
            )
        case "area":
            return _resolve_area(
                normalized,
                partition(normalized.multiples, data),
                chart_style_context,
                effective_width,
                datasets,
                automatic_link_candidate,
                variables,
            )
        case "scatter":
            return _resolve_scatter(
                normalized,
                partition(normalized.multiples, data),
                chart_style_context,
                effective_width,
                datasets,
                automatic_link_candidate,
                variables,
            )
        case "heatmap":
            return _resolve_heatmap(
                normalized,
                partition(normalized.multiples, data),
                chart_style_context,
                effective_width,
                automatic_link_candidate,
                variables,
            )
        case "pie" | "donut":
            return _resolve_pie(
                normalized,
                data,
                chart_style_context,
                effective_width,
                automatic_link_candidate,
                variables,
                pie_title_font,
            )
        case "kpi":
            return _resolve_kpi(
                normalized,
                data,
                chart_style_context,
                effective_width,
                automatic_link_candidate,
                variables,
            )
        case "table":
            return _resolve_table(
                normalized,
                data,
                chart_style_context,
                effective_width,
                automatic_link_candidate,
                table_column_links,
                table_column_rows,
                variables,
            )
        case "geoshape" | "map":
            return _resolve_geoshape(
                normalized,
                data,
                chart_style_context,
                effective_width,
                automatic_link_candidate,
                variables,
            )
        case "point_map" | "bubble_map":
            return _resolve_point_map(
                normalized,
                data,
                chart_style_context,
                effective_width,
                automatic_link_candidate,
                variables,
            )
        case "callout":
            return _resolve_callout(normalized, data, chart_style_context, variables)
        case "spark_bar":
            return _resolve_spark_bar(
                normalized,
                data,
                chart_style_context,
                effective_width,
                automatic_link_candidate,
                variables,
            )
        case _:
            raise ValueError(f"Unknown normalized chart type: {normalized.type!r}")
