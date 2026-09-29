"""KPI, callout, and spark-bar chart resolvers."""

from __future__ import annotations

from typing import Any

from dbt_charts.core.compile.errors import CompilationError
from dbt_charts.core.compile.format import (
    finalize_kpi_value_format,
    resolve_label_format,
)
from dbt_charts.core.compile.merge import merge_onto_base, to_padding_style
from dbt_charts.core.compile.models.chart.normalized import (
    CalloutChart,
    KpiChart,
    SparkBarChart,
)
from dbt_charts.core.compile.models.chart.resolved import (
    ResolvedCalloutChart,
    ResolvedKpiChart,
    ResolvedSparkBarChart,
)
from dbt_charts.core.compile.models.primitives import FormatConfig
from dbt_charts.core.compile.models.style.context import ChartStyleContext
from dbt_charts.core.compile.models.style.resolved import (
    ResolvedCalloutStyle,
    ResolvedKpiStyle,
    ResolvedSparkBarStyle,
)
from dbt_charts.core.compile.resolve.chart._channels import _channels_for
from dbt_charts.core.compile.resolve.chart._kwargs import (
    AutomaticLinkCandidate,
    ChartTextVariables,
    _base_kwargs,
    _resolve_text,
    _shared_kwargs,
    _title_font,
)
from dbt_charts.core.compile.resolve.chart._palette import (
    _with_color_tokens,
)
from dbt_charts.core.compile.resolve.style.board import _callout_tone_colors_patch
from dbt_charts.core.compile.resolve.style.chart_context import (
    build_chart_style_context,
)
from dbt_charts.core.utils import coerce_numeric_cell

__all__ = [
    "_resolve_callout",
    "_resolve_kpi",
    "_resolve_spark_bar",
]


def _resolve_kpi(
    normalized: KpiChart,
    data: list[dict[str, Any]],
    chart_style_context: ChartStyleContext,
    width: float,
    automatic_link_candidate: AutomaticLinkCandidate,
    variables: ChartTextVariables,
) -> ResolvedKpiChart:
    chart_local_style_context = build_chart_style_context(
        chart_style_context, normalized
    )
    primary = _with_color_tokens(normalized.style, chart_style_context)
    kpi = merge_onto_base(chart_style_context.kpi, primary)
    channels = _channels_for(normalized, data)
    _tf = _title_font(normalized, chart_local_style_context, width)
    formats = chart_style_context.formats
    # Alias-membership gate: a format that is a theme alias → house narrative;
    # a literal d3 spec → format_native=True, routed to format_kpi_parts'
    # native split (KPI is custom-SVG, not Vega — there is no Vega text.format
    # escape hatch on this path). An explicit notation field on a FormatConfig
    # always forces house rules (author named the register explicitly).
    # Gate on the *spec string* being present, not merely the FormatConfig object
    # existing: FormatConfig(prefix="$") has no spec and must not flip native.
    fmt_raw = kpi.value.format  # merged result; pre-resolve, safe for alias check
    fmt_spec = fmt_raw.spec if isinstance(fmt_raw, FormatConfig) else fmt_raw
    if fmt_spec:
        _, is_house = resolve_label_format(fmt_raw, formats)
        if isinstance(fmt_raw, FormatConfig) and fmt_raw.notation is not None:
            is_house = True
        format_native = not is_house
    else:
        format_native = False
    return ResolvedKpiChart(
        **_base_kwargs(
            normalized,
            chart_style_context,
            channels,
            None,
            # KPI has no series axis and no categorical color channel — no
            # per-chart color.categorical override is possible, so this is
            # always the board-level value, unlike the cartesian/radial
            # families which route through _effective_requested_alias_palette.
            requested_alias_palette=chart_style_context.requested_alias_palette,
            automatic_link_candidate=automatic_link_candidate,
            layout_padding=kpi.padding,
        ),
        chart_type="kpi",
        value=normalized.value,
        label=_resolve_text(normalized.label, variables),
        support=normalized.support,
        variant=normalized.variant,
        format=finalize_kpi_value_format(
            kpi.value.format, _headline_numeric_value(normalized.value, data)
        ),
        format_native=format_native,
        # chart_local_style_context.title is build_chart_style_context()'s own
        # board -> family-theme -> chart-local title merge (shared by every
        # family) -- already the complete, final TitleStyle.
        style=ResolvedKpiStyle(
            kpi=kpi, title_font=_tf, title=chart_local_style_context.title
        ),
    )


def _headline_numeric_value(
    value_column: str, data: list[dict[str, Any]]
) -> float | None:
    """Best-effort numeric read of the KPI headline cell, for format sizing only.

    Row-cardinality and missing-column errors are surfaced by render (which
    re-reads this same cell to build the display string); this helper never
    raises, it only decides whether the SI-compaction default applies.
    """
    if len(data) != 1 or value_column not in data[0]:
        return None
    return coerce_numeric_cell(data[0][value_column])


def _resolve_spark_bar(
    normalized: SparkBarChart,
    data: list[dict[str, Any]],
    chart_style_context: ChartStyleContext,
    width: float,
    automatic_link_candidate: AutomaticLinkCandidate,
    variables: ChartTextVariables,
) -> ResolvedSparkBarChart:
    chart_local_style_context = build_chart_style_context(
        chart_style_context, normalized
    )
    primary = _with_color_tokens(normalized.style, chart_style_context)
    spark_bar = merge_onto_base(chart_style_context.spark_bar, primary)
    channels = _channels_for(normalized, data)
    # y is str | list[str] | None on normalized; ResolvedSparkBarChart only takes str | None.
    # Spark bar is single-series — multi-series y is a configuration error.
    if isinstance(normalized.y, list):
        if len(normalized.y) != 1:
            raise CompilationError(
                f"Chart '{normalized.id}' (spark_bar) requires a single y column; "
                f"got {len(normalized.y)}: {normalized.y!r}"
            )
        y: str | None = normalized.y[0]
    else:
        y = normalized.y
    _tf = _title_font(normalized, chart_local_style_context, width)
    return ResolvedSparkBarChart(
        **_base_kwargs(
            normalized,
            chart_style_context,
            channels,
            # spark_bar's style patch has no color-cascade concept at all
            # (SparkBarChartStyle does not inherit _ChartStyleBaseAllOptional,
            # unlike every other family) — color is a plain literal channel
            # (normalized.color below), not a chart-local categorical-palette
            # override. There is nothing for _effective_requested_alias_palette
            # to read from `primary`, so the board value is the whole answer.
            requested_alias_palette=chart_style_context.requested_alias_palette,
            automatic_link_candidate=automatic_link_candidate,
            layout_padding=spark_bar.padding,
        ),
        **_shared_kwargs(normalized, variables, chart_local_style_context),
        chart_type="spark_bar",
        x=normalized.x,
        y=y,
        color=normalized.color,
        sort=normalized.sort,
        style=ResolvedSparkBarStyle(spark_bar=spark_bar, title_font=_tf),
    )


def _resolve_callout(
    normalized: CalloutChart,
    _data: list[dict[str, Any]],
    chart_style_context: ChartStyleContext,
    variables: ChartTextVariables,
) -> ResolvedCalloutChart:
    base_callout = chart_style_context.callout
    resolved_callout: ResolvedCalloutStyle = merge_onto_base(
        base_callout, normalized.style
    )
    resolved_callout = merge_onto_base(
        resolved_callout, _callout_tone_colors_patch(resolved_callout.tone)
    )
    return ResolvedCalloutChart(
        id=normalized.id,
        source_path=normalized.source_path,
        defined_in_other_file=normalized.defined_in_other_file,
        chart_type="callout",
        message=_resolve_text(normalized.message, variables),
        title=(
            _resolve_text(normalized.title, variables)
            if normalized.title is not None
            else None
        ),
        variable_dependencies=normalized.variable_dependencies,
        style=resolved_callout,
        layout_padding=to_padding_style(resolved_callout.padding),
    )
