"""KPI, callout, and spark-bar chart resolvers."""

from __future__ import annotations

from typing import Any, Literal

from dbt_charts.core.compile.errors import CompilationError
from dbt_charts.core.compile.file_refs import check_images_written_out
from dbt_charts.core.compile.format import (
    declared_column_format,
    finalize_kpi_value_format,
    kpi_format_native,
    resolve_format_parts,
)
from dbt_charts.core.compile.format_value import (
    raise_if_format_cannot_print_number,
    text_format_link,
)
from dbt_charts.core.compile.merge import to_padding_style
from dbt_charts.core.compile.models.chart.normalized import (
    CalloutChart,
    KpiChart,
    SparkBarChart,
)
from dbt_charts.core.compile.models.chart.resolved import (
    ResolvedCalloutChart,
    ResolvedKpiChart,
    ResolvedKpiSupportConfig,
    ResolvedSparkBarChart,
)
from dbt_charts.core.compile.models.chart.resolved.kpi import KpiGoodRule
from dbt_charts.core.compile.models.markers import AUTO
from dbt_charts.core.compile.models.primitives import (
    ResolvedFormat,
    resolved_as,
)
from dbt_charts.core.compile.models.style.context import ChartStyleContext
from dbt_charts.core.compile.models.style.resolved import (
    ResolvedKpiStyle,
    ResolvedSparkBarStyle,
)
from dbt_charts.core.compile.models.style.theme.kpi import (
    KpiBackgroundGradient,
    KpiChartStyle,
    KpiGlyphSet,
)
from dbt_charts.core.compile.resolve.chart._channels import _channels_for
from dbt_charts.core.compile.resolve.chart._kwargs import (
    AutomaticLinkCandidate,
    _base_kwargs,
    _resolve_text,
    _shared_kwargs,
    board_family_style,
)
from dbt_charts.core.compile.resolve.chart._palette import (
    _with_color_tokens,
)
from dbt_charts.core.compile.resolve.chart.channel import kpi_background_channel
from dbt_charts.core.compile.resolve.style.board import (
    build_resolved_callout_chart,
    unset_auto_support_ink,
)
from dbt_charts.core.compile.resolve.style.chart_context import (
    build_chart_style_context,
)
from dbt_charts.core.compile.resolve.style.inherit_resolver import merge_unset
from dbt_charts.core.compile.resolve.style.typography import resolve_title_font
from dbt_charts.core.compile.template.display_text import ChartDisplayText
from dbt_charts.core.diagnostics.chart_data import ChartDataError
from dbt_charts.core.diagnostics.codes_render import ERR_KPI_NO_DATA
from dbt_charts.core.text.predefined_formats import PREDEFINED_TEXT_NAMES
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
    display_text: ChartDisplayText = None,
) -> ResolvedKpiChart:
    value = normalized.value
    if value is None:
        # resolve() binds value from the rows; only a zero-row result still
        # arrives without one, and that fails the way it fails a named KPI.
        raise ChartDataError.from_code(ERR_KPI_NO_DATA, chart_id=normalized.id)
    primary = _with_color_tokens(normalized.style, chart_style_context)
    # The chart-local context re-runs inherit, so a chart's own `style.font`
    # reaches every slot font.
    kpi = unset_auto_support_ink(
        build_chart_style_context(chart_style_context, normalized).kpi
    )
    available = set(data[0]) if data else set()
    _validate_kpi_conditional(kpi, available)
    channels = (
        {"background": kpi_background_channel(kpi.background, available)}
        if isinstance(kpi.background, KpiBackgroundGradient)
        else {}
    )
    formats = chart_style_context.formats
    headline = _headline_numeric_value(value, data)
    chart_value = (
        primary.value.format if primary is not None and primary.value else None
    )
    board_value = chart_style_context.kpi.value.format
    query_formats = normalized.query.formats if normalized.query else None
    base = _base_kwargs(
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
        legendless=True,
        wrap_legend=False,
    )
    support = normalized.support
    support_rules = (
        _good_rules(
            normalized.id,
            "support.good",
            "style.support.glyph",
            support.value,
            support.good,
            data,
            kpi.support.glyph,
        )
        # The authored validator rejects `good` without `value`.
        if support is not None
        and support.good is not None
        and support.value is not None
        else ()
    )
    value_rules = (
        _good_rules(
            normalized.id,
            "good",
            "style.value.glyph",
            value,
            normalized.good,
            data,
            kpi.value.glyph,
        )
        if normalized.good is not None
        else None
    )
    value_format = finalize_kpi_value_format(
        chart_value,
        headline,
        formats,
        compact=kpi.value.compact,
        board=board_value,
        query_formats=query_formats,
        columns=value,
    )
    support_format = kpi.support.format
    if (
        support_format is None
        and normalized.support is not None
        and normalized.support.value is not None
    ):
        support_format = declared_column_format(
            query_formats,
            normalized.support.value,
            formats,
            time=_headline_numeric_value(normalized.support.value, data) is None,
            text=True,
        )
    text_link = _kpi_text_link(
        value_format, data, value, normalized.id, bool(base["link"])
    )
    return ResolvedKpiChart(
        **base,
        text_link=text_link,
        chart_type="kpi",
        value=value,
        label=_resolve_text(
            normalized.label, display_text, formats, normalized.id, "label"
        ),
        support=(
            None
            if normalized.support is None
            else resolved_as(
                ResolvedKpiSupportConfig,
                normalized.support,
                label=(
                    None
                    if normalized.support.label is None
                    else _resolve_text(
                        normalized.support.label,
                        display_text,
                        formats,
                        normalized.id,
                        "support.label",
                    )
                ),
                format=(
                    None
                    if support_format is None
                    else resolve_format_parts(support_format, formats)
                ),
                rules=support_rules or None,
            )
        ),
        glyph=normalized.glyph,
        good=normalized.good,
        value_rules=value_rules,
        variant=kpi.variant,
        format=value_format,
        format_native=kpi_format_native(
            formats,
            chart_value,
            board=board_value,
            query_formats=query_formats,
            columns=value,
        ),
        style=ResolvedKpiStyle(kpi=kpi),
    )


def _validate_kpi_conditional(kpi: KpiChartStyle, available: set[str]) -> None:
    """Raise ValueError for a rule the card cannot paint.

    A rule column must be a query column. ``auto`` is the support ink's own
    default, not a color a rule can name.
    """
    if kpi.conditional is None:
        return
    for column, rules in kpi.conditional.items():
        if available and column not in available:
            raise ValueError(
                f"style.conditional targets column '{column}' which is not in the "
                f"query result. Available: {sorted(available)}"
            )
        for rule in rules:
            support_font = rule.support.font if rule.support is not None else None
            if support_font is not None and support_font.color == AUTO:
                raise ValueError(
                    f"style.conditional.{column} sets support.font.color to 'auto'; "
                    "name a color, or set support.tone."
                )


def _good_rules(
    chart_id: str,
    key: str,
    glyph_slot: str,
    column: str,
    good: Literal["up", "down"],
    data: list[dict[str, Any]],  # type-state: explicit_any — rows are raw query output
    glyphs: KpiGlyphSet,
) -> tuple[KpiGoodRule, ...]:
    """The sign rules ``good`` stands for on ``column``; render applies them
    before the authored ``style.conditional`` rules."""
    cell = data[0].get(column) if len(data) == 1 else None
    if cell is not None and coerce_numeric_cell(cell) is None:
        raise ValueError(
            f"KPI {chart_id!r}: `{key}` needs a numeric `{column}` "
            f"column to read a sign from, got {cell!r}."
        )
    if glyphs.up is None or glyphs.down is None:
        raise ValueError(
            f"KPI {chart_id!r}: `{key}` draws `{glyph_slot}.up` and "
            "`.down`; the style cascade left one of them unset."
        )
    up_tone, down_tone = (
        ("positive", "negative") if good == "up" else ("negative", "positive")
    )
    rules = [
        KpiGoodRule(gt=0, glyph=glyphs.up, tone=up_tone),
        KpiGoodRule(lt=0, glyph=glyphs.down, tone=down_tone),
    ]
    if glyphs.neutral is not None:
        rules.append(KpiGoodRule(eq=0, glyph=glyphs.neutral))
    return tuple(rules)


def _kpi_text_link(
    value_format: ResolvedFormat | None,
    data: list[dict[str, Any]],  # type-state: explicit_any — rows are raw query output
    value_column: str,
    chart_id: str,
    has_link: bool,
) -> str | None:
    """The link an ``email`` / ``url`` headline implies, below the chart's own.

    A number under a text format is an error even when the chart's own link wins;
    only the ``url`` shape check is skipped then.
    """
    if value_format is None or value_format.raw not in PREDEFINED_TEXT_NAMES:
        return None
    cell = data[0].get(value_column) if len(data) == 1 else None
    if coerce_numeric_cell(cell) is not None:
        raise_if_format_cannot_print_number(value_format.raw)
    if has_link:
        return None
    return text_format_link(value_format.raw, cell, f"KPI {chart_id!r}")


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
    display_text: ChartDisplayText = None,
) -> ResolvedSparkBarChart:
    chart_local_style_context = build_chart_style_context(
        chart_style_context, normalized
    )
    primary = _with_color_tokens(normalized.style, chart_style_context)
    spark_bar = merge_unset(
        board_family_style(chart_style_context, normalized), primary
    )
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
    _tf = resolve_title_font(chart_local_style_context, width)
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
            legendless=True,
            wrap_legend=False,
        ),
        **_shared_kwargs(normalized, display_text, chart_local_style_context),
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
    display_text: ChartDisplayText = None,
) -> ResolvedCalloutChart:
    callout = merge_unset(
        board_family_style(chart_style_context, normalized),
        _with_color_tokens(normalized.style, chart_style_context),
    )
    resolved_callout = build_resolved_callout_chart(
        callout,
        chart_style_context.bold_font_weight,
        chart_style_context.code_family,
        callout.tone,
    )
    message = _resolve_text(
        normalized.message,
        display_text,
        chart_style_context.formats,
        normalized.id,
        "message",
    )
    check_images_written_out(normalized.message, message)
    title = None
    if normalized.title is not None:
        title = _resolve_text(
            normalized.title,
            display_text,
            chart_style_context.formats,
            normalized.id,
            "title",
        )
        check_images_written_out(normalized.title, title)
    return ResolvedCalloutChart(
        id=normalized.id,
        source_path=normalized.source_path,
        defined_in_other_file=normalized.defined_in_other_file,
        base_dir=normalized.base_dir,
        chart_type="callout",
        message=message,
        title=title,
        variable_dependencies=normalized.variable_dependencies,
        style=resolved_callout,
        layout_padding=to_padding_style(resolved_callout.padding),
    )
