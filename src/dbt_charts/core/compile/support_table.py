"""Compile-time concerns for the chart.support_table primitive.

Owns the parts of ``chart.support_table`` that are knowable before any query
runs: row pixel geometry (``row_height``, ``axis_offset``) and which
effective style applies after the per-chart-family cascade
(``resolve_effective_support_table_style``).

Strip height depends on ``series_count``, which is derived from query rows
(the size of the color domain), so ``support_table_strip_height`` is a render
concern — see ``dbt_charts.core.render.chart.support_table_attachment``. Data-aware
validation against the query output (``validate_support_table_against_data``)
and the Vega-Lite layer emission itself (``attach_support_table`` and its
helpers) live there too.
"""

from __future__ import annotations

from typing import Any, Literal

from dbt_charts.core.compile.errors import CompilationError
from dbt_charts.core.compile.format import (
    resolve_format_parts,
    resolve_format_parts_for_values,
)
from dbt_charts.core.compile.merge import merge_onto_base
from dbt_charts.core.compile.models.chart.authored import (
    ChartSupportTable,
    ChartSupportTableAggregate,
    ChartSupportTablePerSeries,
)
from dbt_charts.core.compile.models.chart.authored._support_table import (
    ChartSupportTableEntry,
)
from dbt_charts.core.compile.models.chart.resolved import (
    ResolvedSupportTable,
    ResolvedSupportTableAggregate,
    ResolvedSupportTableEntry,
    ResolvedSupportTablePerSeries,
    ResolvedSupportTableSource,
)
from dbt_charts.core.compile.models.primitives import (
    AuthoredFormat,
    FormatAliases,
    ResolvedFormat,
    resolved_as,
)
from dbt_charts.core.compile.models.style.context import ChartStyleContext
from dbt_charts.core.compile.models.style.theme import SupportTableStyle
from dbt_charts.core.compile.resolve.chart._channels import _column_numeric_values
from dbt_charts.core.compile.resolve.style.axis_cascade import resolved_axis_style
from dbt_charts.core.diagnostics.codes_compile import ERR_SUPPORT_TABLE_POSITION_INVALID
from dbt_charts.core.text.predefined_formats import (
    PREDEFINED_SUB_UNIT_FALLBACK,
    PredefinedNumberFormat,
)

# Row geometry. These are the compiler's constants — they live here rather
# than on the style model because they are emitter mechanics, not user-facing
# theme tokens. If a theme wants denser rows, padding.vertical lets it.
_ROW_HEIGHT_BASE = 14.0  # per-row pixel height before padding


def row_height(style: SupportTableStyle) -> float:
    padding = style.row.padding
    return _ROW_HEIGHT_BASE + padding.vertical * 2.0


def axis_offset(
    chart_style_context: ChartStyleContext,
    style: SupportTableStyle,
    x_label_authored: bool,
    chart_type: str,
) -> float:
    """Pixel offset from plot bottom edge to the bottom of the x-axis block.

    Sums whichever axis components are actually visible: a chart with the
    title shown but labels suppressed still needs the title's height to
    sit above the strip, otherwise the strip's first row collides with it.

    label_max_lines × label height is reserved so yearly boundary ticks that
    emit a two-element array (e.g. ['Jan', '2024']) do not overlap the strip
    top edge. style.label_max_lines = 2 is the universal default; themes with
    a year-only cadence may set 1.

    MUST use the fully-merged axis (resolved_axis_style) rather than reading
    charts_style.axis_x directly. ``x_label_authored`` (the caller's own
    ``bool(chart.x_label)`` — truthy, so a blank label reserves no title
    space, matching what the cascade forces) is required, not defaulted, so a caller
    that forgets to thread it fails loudly rather than silently reserving no
    title space. It drives the same Layer-5 label-forcing
    default ``resolved_axis_style`` applies for the emitted VL spec; reading
    the base axis_x (Layers 1+2) misses it, under-reserving title space and
    causing the strip to collide with the rendered axis title.
    """
    # Font metrics come from the base ResolvedAxisStyle (non-None guaranteed).
    # Visibility is read from the fully-merged cascade (includes the Layer-5
    # label-forcing default and every chart-local override) so both label and
    # title heights reflect the actual rendered state.
    merged = resolved_axis_style(
        chart_style_context,
        "axis_x",
        "band",
        chart_type=chart_type,
        label_authored=x_label_authored,
    )
    # labels.padding is always required by build_resolved_axis (the shared
    # ResolvedAxisElementStyle dataclass types it Optional only to let title
    # leave it unset — see AxisTitleStyle).
    assert merged.labels.padding is not None, "axis_x.labels.padding unset"
    label_h = (
        merged.labels.padding + merged.labels.font.size * style.label_max_lines
        if merged.labels.visible is not False and merged.labels.font.size > 0
        else 0.0
    )
    assert merged.title.padding is not None, "axis_x.title.padding unset"
    title_h = (
        merged.title.padding + merged.title.font.size
        if merged.title.visible is not False and merged.title.font.size > 0
        else 0.0
    )
    return label_h + title_h


def resolve_effective_support_table_style(
    chart_style_context: ChartStyleContext, chart_type: str
) -> SupportTableStyle:
    """Resolve the effective support_table style for a chart.

    charts_style.support_table carries the global theme value merged with any
    chart-local board override.  apply_inherit pre-fills per-family support_table
    fields from charts.support_table, so this function only needs to apply the
    per-family theme values (tier-2) on top.
    """
    per_type = getattr(chart_style_context, chart_type, None)
    if per_type is None or not hasattr(per_type, "support_table"):
        return chart_style_context.support_table
    return merge_onto_base(chart_style_context.support_table, per_type.support_table)


def resolve_support_table_position(
    position: Literal["top", "bottom", "left", "right"] | None,
    category_axis_vertical: bool,
    axis_y_orient: Literal["left", "right"],
    chart_id: str,
) -> Literal["top", "bottom", "left", "right"]:
    """Resolve and validate ``style.support_table.position`` against the chart's
    category-axis orientation.

    ``None`` means "not authored": it defaults to ``top`` on a horizontal
    category axis (vertical bar, line, area), or to the side the category
    labels are on (``axis_y_orient``) on a vertical one (a horizontal bar) —
    so the shipped layout is label -> table -> plot without the author
    restating it. An explicit author value is validated against the actual
    orientation and always wins; it is never silently re-mapped to a valid
    side.

    Called at resolve time so the value baked onto ``effective_support_table_style``
    is always concrete — a mismatched `position:` is a compile-time error, not
    something that only surfaces once a chart is rendered.
    """
    if category_axis_vertical:
        if position is None:
            return axis_y_orient
        if position not in ("left", "right"):
            raise CompilationError.from_code(
                ERR_SUPPORT_TABLE_POSITION_INVALID,
                message=(
                    f"style.support_table.position: {position!r} is invalid on "
                    f"chart {chart_id!r} — the category axis is vertical "
                    "(orientation: horizontal). Use 'left' or 'right', or drop "
                    "orientation: horizontal to use 'top'/'bottom' on a vertical "
                    "category axis."
                ),
            )
        return position
    if position is None:
        return "top"
    if position not in ("top", "bottom"):
        raise CompilationError.from_code(
            ERR_SUPPORT_TABLE_POSITION_INVALID,
            message=(
                f"style.support_table.position: {position!r} is invalid on "
                f"chart {chart_id!r} — the category axis is horizontal. Use "
                "'top' or 'bottom', or set orientation: horizontal on a bar "
                "chart to use 'left'/'right' on a vertical category axis."
            ),
        )
    return position


def apply_measure_format_to_support_table(
    support_table: ChartSupportTable, measure_format: AuthoredFormat, y_field: str
) -> ChartSupportTable:
    """Inherit measure_format into support_table entries reading y_field with no authored format.

    Shared by normalize_chart()'s _build_cartesian() — both need to
    push the chart's resolved measure format into support_table entries that don't
    author their own. InheritSlot cannot do this: that mechanism is specific to
    the Style model tree, and the condition "only entries reading y_field
    inherit" is a cross-field identity check (entry.source == y_field), not a
    structural position.
    """
    resolved_entries = []
    for entry in support_table.entries:
        source_field = (
            entry.per_series
            if isinstance(entry, ChartSupportTablePerSeries)
            else entry.source
        )
        if entry.format is None and source_field == y_field:
            entry = entry.model_copy(update={"format": measure_format})
        resolved_entries.append(entry)
    return support_table.model_copy(update={"entries": resolved_entries})


def resolve_support_table(
    support_table: ChartSupportTable,
    formats: FormatAliases | None,
    data: list[dict[str, Any]],  # type-state: explicit_any — query-row boundary values
    y: str | list[str] | None,
    *,
    stamp_number: bool,
) -> ResolvedSupportTable:
    """Bake every entry's format.

    A sub-$1-floor or spec-less-affix format votes its spec over the entry's
    values and then reads as native digits, not house. With ``stamp_number``,
    an entry reading ``y`` with no format takes the engine's ``number``.
    """
    entries: list[ResolvedSupportTableEntry] = []
    for entry in support_table.entries:
        column = (
            entry.per_series
            if isinstance(entry, ChartSupportTablePerSeries)
            else entry.source
        )
        # A per_series entry votes over the chart's y column(s), not its own.
        voted_columns = y if isinstance(entry, ChartSupportTablePerSeries) else column
        fmt: ResolvedFormat | None = None
        if entry.format is not None:
            fmt = resolve_format_parts(entry.format, formats, no_format_default=None)
            if fmt.is_spec_less_affix or fmt.raw in PREDEFINED_SUB_UNIT_FALLBACK:
                fields = (
                    voted_columns
                    if isinstance(voted_columns, list)
                    else [voted_columns]
                    if voted_columns is not None
                    else []
                )
                voted = resolve_format_parts_for_values(
                    entry.format,
                    formats,
                    [v for f in fields for v in _column_numeric_values(data, f)],
                    no_format_default=PredefinedNumberFormat.number,
                )
                if voted.spec != fmt.spec:
                    fmt = voted.model_copy(update={"raw": None})
        elif stamp_number and column == y:
            fmt = resolve_format_parts(
                PredefinedNumberFormat.number, formats, no_format_default=None
            )
        entries.append(_with_resolved_format(entry, fmt))
    return ResolvedSupportTable(entries=entries)


def _with_resolved_format(
    entry: ChartSupportTableEntry, fmt: ResolvedFormat | None
) -> ResolvedSupportTableEntry:
    if isinstance(entry, ChartSupportTablePerSeries):
        return resolved_as(ResolvedSupportTablePerSeries, entry, format=fmt)
    if isinstance(entry, ChartSupportTableAggregate):
        return resolved_as(ResolvedSupportTableAggregate, entry, format=fmt)
    return resolved_as(ResolvedSupportTableSource, entry, format=fmt)
