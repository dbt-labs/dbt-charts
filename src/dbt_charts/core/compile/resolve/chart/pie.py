"""Pie chart resolver."""

from __future__ import annotations

from types import EllipsisType
from typing import Any, Literal

from dbt_charts.core.compile.errors import CompilationError
from dbt_charts.core.compile.format import column_format, resolve_format_parts
from dbt_charts.core.compile.models.chart.authored import ChartTotal
from dbt_charts.core.compile.models.chart.normalized import (
    PieChart,
    TableChart,
)
from dbt_charts.core.compile.models.chart.resolved import (
    ResolvedPieChart,
)
from dbt_charts.core.compile.models.primitives import ResolvedFontStyle, resolved_as
from dbt_charts.core.compile.models.style.authored import TableChartStylePatch
from dbt_charts.core.compile.models.style.context import ChartStyleContext
from dbt_charts.core.compile.models.style.resolved import (
    ResolvedPieStyle,
    ResolvedTotalStyle,
    ResolvedTotalValueSlot,
)
from dbt_charts.core.compile.models.style.theme import (
    LegendAlign,
    SliceLabelsStyle,
    SliceMarkStyle,
)
from dbt_charts.core.compile.resolve.chart._axes import (
    color_legend_entries,
    legend_row_fits,
)
from dbt_charts.core.compile.resolve.chart._channels import (
    _channels_for,
    _column_numeric_values,
)
from dbt_charts.core.compile.resolve.chart._kwargs import (
    AutomaticLinkCandidate,
    _base_kwargs,
    _bound_scales,
    _shared_kwargs,
    donut_over_pie,
)
from dbt_charts.core.compile.resolve.chart._palette import (
    _effective_palette,
    _effective_requested_alias_palette,
    _pin_companions,
    _with_color_tokens,
)
from dbt_charts.core.compile.resolve.chart._table import (
    _EMPTY_TABLE_COLUMN_LINKS,
    _resolve_table,
)
from dbt_charts.core.compile.resolve.chart.label_data import (
    default_slice_label_template,
    pie_presentation_fingerprint,
)
from dbt_charts.core.compile.resolve.chart.pie_attachment import (
    ArcRenderMode,
    AttachmentPlan,
    classify_arc_render_mode,
    compute_shares,
    finalize_pie_label_lines,
    infer_implicit_color_field,
    plan_attachment,
    resolve_hybrid_heading_font,
    validate_theta_values,
)
from dbt_charts.core.compile.resolve.style.chart_context import (
    build_chart_style_context,
    declared_column_format,
)
from dbt_charts.core.compile.resolve.style.inherit_resolver import (
    merge_unset,
    settled,
)
from dbt_charts.core.compile.resolve.style.legend_position import (
    merge_authored_position,
    merge_legend,
    placed_align,
    placed_edge,
)
from dbt_charts.core.compile.resolve.style.palette import mark_ink
from dbt_charts.core.compile.resolve.style.typography import resolve_title_font
from dbt_charts.core.compile.template.display_text import ChartDisplayText
from dbt_charts.core.constants.pie import (
    ATTACHED_TABLE_GAP_PX,
    HYBRID_HEADING_GAP_PX,
    OUTER_FRACTION,
)
from dbt_charts.core.diagnostics.chart_data import ChartDataError
from dbt_charts.core.diagnostics.codes_compile import ERR_PIE_TOTAL_WITHOUT_HOLE
from dbt_charts.core.text.case import apply_case, inferred_display_name
from dbt_charts.core.text.format_d3 import board_word
from dbt_charts.core.text.predefined_formats import PredefinedNumberFormat

__all__ = [
    "_resolve_pie",
]


def _auto_total_label(normalized: PieChart) -> str:
    label = normalized.field_labels.get(normalized.theta)
    if label is None:
        slug = inferred_display_name(normalized.theta)
        label = (
            slug if slug.isupper() and " " not in slug else apply_case(slug, "title")
        )
    if board_word("pie.total_word").lower() in label.lower():
        return label
    return board_word("pie.total", label=label)


def _resolve_pie(
    normalized: PieChart,
    data: list[dict[str, Any]],
    chart_style_context: ChartStyleContext,
    width: float,
    automatic_link_candidate: AutomaticLinkCandidate,
    display_text: ChartDisplayText = None,
    title_font: ResolvedFontStyle | EllipsisType = ...,
) -> ResolvedPieChart:
    validate_theta_values(normalized.id, normalized.theta, data)
    chart_local_style_context = build_chart_style_context(
        chart_style_context, normalized
    )
    primary = _with_color_tokens(normalized.style, chart_style_context)
    board_pie = (
        donut_over_pie(chart_style_context)
        if normalized.is_donut
        else chart_style_context.pie
    )
    pie = merge_unset(board_pie, primary)
    # The center total and every slice tooltip vote on one set (theta values
    # plus their sum) so they never disagree about the same sub-$1 value --
    # see resolve_format_parts_for_values's per-set contract.
    theta_values = _column_numeric_values(data, normalized.theta)
    format_vote_values = [*theta_values, sum(theta_values)] if theta_values else []
    theta_declared = declared_column_format(
        normalized, normalized.theta, chart_style_context.formats, time=False
    )
    channels = _channels_for(normalized, data)
    # `pie` is the family merged over its board tier, so the chart's own
    # color.categorical beats the board family's, which beats the global palette.
    eff_palette = _effective_palette(chart_style_context, pie)
    if not eff_palette:
        raise ChartDataError(
            f"Pie chart {normalized.id!r} needs a resolved palette.",
            chart_id=normalized.id,
        )

    # Branch on the *resolved* color channel (not normalized.color) so this
    # agrees by construction with the emitter, which reads
    # chart.resolved_channels — the same `channels` dict baked below via
    # _base_kwargs. A color: field that fails to resolve to a real data
    # column (e.g. missing from query rows) must take the single-series path
    # on both sides, not just one.
    color_channel = channels.get("color")

    # The field labels and the attached table use to identify each wedge.
    # Prefers the resolved color channel (matches the emitter's paint
    # decision) when it's a real series binding. Falls back to the raw
    # authored color, then -- when the author bound neither -- the query's
    # sole non-theta column, if it's unambiguous (see
    # infer_implicit_color_field). This never touches `channels`/
    # `color_channel` itself, so it never turns into a paint encoding: a
    # no-color pie's wedges stay palette[0] and its dark-companion ink stays
    # single-stop either way -- only the label text and attached-table name
    # column gain an identity.
    resolved_color_field: str | None = (
        color_channel.data_field
        if color_channel is not None
        else normalized.color or infer_implicit_color_field(normalized.theta, data)
    )

    # Bake dark-companion stops for label color encoding.  Computed here so
    # render/chart/ never needs to import compile.palette. Multi-series pies
    # get the full companion palette, parallel index-for-index with
    # `chart.palette` — a value's board slot can land anywhere in that range,
    # not just within this chart's own distinct-value count, so the emitter
    # must be able to index a companion by ANY slot the board hands it, not
    # only the ones this particular pie happens to draw. Single-series pies
    # get one stop for palette[0] — the same value the emitter paints the
    # wedge with when there's no color channel, so label ink and wedge fill
    # always match. marks.slice.labels.font.color is never read here — an
    # authored value (single-series only; the merged sentinel is real, so no
    # patch-peeking is needed) is applied later by the engine's pie family, which prefers
    # it over dark_companion_stops[0]. Multi-series pies never honor font.color.
    dark_stops: tuple[str, ...]
    canvas = chart_local_style_context.ink_canvas
    if color_channel is not None and color_channel.data_field:
        dark_stops = tuple(mark_ink(c, canvas) for c in eff_palette)
    else:
        dark_stops = (mark_ink(eff_palette[0], canvas),)

    # template/where live under style.marks.slice.labels (mirrors the authored
    # surface); the default_template fallback fires when template is None
    # (author omitted it). Bake the effective template into the slice_mark
    # actually used to build ResolvedPieStyle below — no separate chart-level
    # labels model.
    _slice_labels = settled(pie.marks.slice.labels)
    tooltip_format = column_format(
        "number",
        chart_style_context.formats,
        normalized.format,
        board=chart_style_context.tooltip.format,
        column=theta_declared,
        values=format_vote_values,
    )
    effective_template = _slice_labels.template
    if effective_template is None:
        effective_template = default_slice_label_template(
            _slice_labels,
            bool(resolved_color_field),
            tooltip_format,
            normalized.id,
        )
    resolved_labels = SliceLabelsStyle(
        offset=_slice_labels.offset,
        line_height=_slice_labels.line_height,
        font=_slice_labels.font,
        default_template=_slice_labels.default_template,
        template=effective_template,
        where=_slice_labels.where,
    )

    # Voted against the slice tooltip's set so the two never disagree.
    is_donut_shape = pie.inner_radius > 0
    total = normalized.total
    # Resolved here, not at normalize: the hole can come from the board or theme
    # cascade, which normalize never sees.
    if (
        is_donut_shape
        and (
            normalized.is_donut
            or (
                normalized.style is not None
                and "inner_radius" in normalized.style.model_fields_set
            )
        )
        and (total is None or total.label is None)
    ):
        auto_label = _auto_total_label(normalized)
        total = (
            ChartTotal(visible=True, label=auto_label)
            if total is None
            else total.model_copy(update={"label": auto_label})
        )
    if total is not None and total.visible and not is_donut_shape:
        raise CompilationError.from_code(
            ERR_PIE_TOTAL_WITHOUT_HOLE, field_path=f"charts.{normalized.id}.total"
        )
    chart_total_format = (
        primary.total.value.format
        if primary is not None and primary.total is not None and primary.total.value
        else None
    )
    board_total_format = board_pie.total.value.format
    total_format = (
        None
        if chart_total_format is None
        and board_total_format is None
        and not is_donut_shape
        else column_format(
            "number",
            chart_style_context.formats,
            chart_total_format,
            board=board_total_format,
            column=theta_declared,
            engine_default=lambda: PredefinedNumberFormat.integer,
            values=format_vote_values,
        )
    )
    total_style = resolved_as(
        ResolvedTotalStyle,
        pie.total,
        value=resolved_as(ResolvedTotalValueSlot, pie.total.value, format=total_format),
    )

    label_font_family = settled(resolved_labels.font.family)
    label_font_size = settled(resolved_labels.font.size)
    shares = compute_shares(normalized.theta, data)
    slice_label_lines = finalize_pie_label_lines(
        normalized.theta,
        resolved_color_field,
        data,
        resolved_labels,
        shares,
    )
    measured_label_lines = [
        line for row_lines in slice_label_lines if row_lines for line in row_lines
    ]

    def dominance_mode(available_width: float) -> ArcRenderMode:
        return classify_arc_render_mode(
            shares,
            measured_label_lines,
            label_font_family,
            label_font_size,
            resolved_labels.offset,
            available_width,
        )

    # This chart's own board-wide category-color binding, narrowed the same
    # way `category_colors` on the resolved chart is (`_bound_scales` below,
    # mirrored again at the bottom of this function via `_base_kwargs`) --
    # so the attached table's swatch and the wedge fill it names always read
    # off the identical scale. Empty when the field isn't board-bound (or
    # there is no color field at all): the table then keeps its existing
    # positional swatch, matching an unbound pie's uniform wedge fill.
    chart_category_colors = _bound_scales(normalized, chart_style_context, eff_palette)
    # The key's edge as authored at any tier; None leaves the attached table to
    # the width heuristic. (The resolved legend always carries a concrete edge,
    # which cannot say whether anyone chose it.)
    authored_position = merge_authored_position(
        chart_style_context.cascaded.charts.legend.position, pie.legend
    )
    authored_edge = placed_edge(authored_position.edge)

    slice_authored = (
        normalized.format if normalized.format is not None else theta_declared
    )
    slice_format = (
        resolve_format_parts(slice_authored, chart_style_context.formats)
        if slice_authored is not None
        else None
    )

    def plan_for(arc_mode: ArcRenderMode) -> AttachmentPlan:
        return plan_attachment(
            arc_mode,
            shares,
            data,
            eff_palette,
            resolved_color_field,
            normalized.theta,
            slice_format,
            chart_style_context.table,
            width,
            chart_category_colors,
            authored_edge,
            placed_align(authored_position.align),
        )

    mode = dominance_mode(width)
    plan = plan_for(mode) if mode != "direct" else None
    if mode == "hybrid":
        assert plan is not None  # every non-direct mode plans a table
        # Dominance was first judged against the whole card, before the
        # companion table had been measured. A right-placed table takes that
        # width back, and labels that clear the floor on the card can still
        # starve the narrower wheel left over — collapsing the plotting box to
        # a sliver. Re-judge at the width the wheel actually gets; the full
        # table drops the labels and hands the wheel its space back.
        if dominance_mode(plan.wheel_width) == "full_table":
            mode = "full_table"
            plan = plan_for(mode)

    slice_mark = pie.marks.slice
    effective_slice_mark = SliceMarkStyle(
        opacity=slice_mark.opacity,
        gap=slice_mark.gap,
        corner_radius=slice_mark.corner_radius,
        stroke=slice_mark.stroke,
        labels=None if mode == "full_table" else resolved_labels,
    )
    if mode == "full_table":
        slice_label_lines = tuple(() for _ in data)

    attached_table = None
    attached_row_indices: tuple[int, ...] = ()
    placement: Literal["none", "left", "right", "top", "bottom"] = "none"
    table_align: LegendAlign = "start"
    table_width = 0.0
    wheel_width = 0.0
    heading = ""
    heading_font = None
    if plan is not None:
        attached_row_indices = plan.row_indices
        placement = plan.placement
        table_align = plan.align
        table_width = plan.table_width
        wheel_width = plan.wheel_width
        synthetic_table = TableChart(
            id=f"{normalized.id}__attached_table",
            type="table",
            style=TableChartStylePatch.model_validate(
                {
                    "header": {"visible": False},
                    "pagination": {"enabled": False},
                    # Explicit null (not an absent key) so this clears the
                    # theme's inherited stripe rather than letting it win:
                    # a wedge legend is a key, not a dense data table, and
                    # striping a 2-row key reads as a selection, not texture.
                    "row": {"stripe": {"color": None}},
                    "columns": plan.columns,
                }
            ),
        )
        attached_table = _resolve_table(
            synthetic_table,
            plan.rows,
            chart_style_context,
            table_width,
            None,
            _EMPTY_TABLE_COLUMN_LINKS,
            (),
            display_text,
            # A legend entry is read beside its own slice, not scanned down
            # a column -- an ANCHOR-mode magnitude declared once on the top
            # entry and stripped from the rest reads as data loss here, not
            # alignment (see _with_resolved_scale_stops's own docstring).
            allow_shared_scale=False,
            filter_variables=frozenset(),
            link_open="page",
        )
        heading = board_word("pie.too_small_to_label") if mode == "hybrid" else ""
        heading_font = resolve_hybrid_heading_font(attached_table.style.table)

    _tf = (
        resolve_title_font(chart_local_style_context, width)
        if title_font is ...
        else title_font
    )
    return ResolvedPieChart(
        **_base_kwargs(
            normalized,
            chart_style_context,
            channels,
            pie.legend,
            eff_palette,
            requested_alias_palette=_effective_requested_alias_palette(
                chart_style_context, pie
            ),
            automatic_link_candidate=automatic_link_candidate,
            layout_padding=pie.padding,
            suppress_legend=True,
            drop_legend=mode != "direct",
            radial_legend=True,
            wrap_legend=not legend_row_fits(
                color_legend_entries(channels, data, resolved_color_field),
                width,
                merge_legend(chart_style_context.legend, pie.legend),
            ),
            legend_position_overridden_by_width=(
                plan.overridden_edge if plan is not None else None
            ),
        ),
        **_shared_kwargs(normalized, display_text, chart_local_style_context),
        chart_type="pie",
        theta=normalized.theta,
        field_labels=normalized.field_labels,
        color=normalized.color,
        identity_field=resolved_color_field,
        total=total,
        format=slice_format,
        style=ResolvedPieStyle(
            inner_radius=pie.inner_radius,
            slice_mark=effective_slice_mark,
            tooltip_format=tooltip_format,
            total_style=total_style,
            title_font=_tf,
        ),
        dark_companion_stops=dark_stops,
        pin_companions=_pin_companions(chart_style_context, canvas),
        resolution_width=width,
        outer_fraction=OUTER_FRACTION,
        attached_table_gap=ATTACHED_TABLE_GAP_PX,
        hybrid_heading_gap=HYBRID_HEADING_GAP_PX,
        presentation_fingerprint=pie_presentation_fingerprint(data),
        slice_label_indices=tuple(
            index for index, lines in enumerate(slice_label_lines) if lines
        ),
        attached_table=attached_table,
        attached_row_indices=attached_row_indices,
        attached_table_placement=placement,
        attached_table_align=table_align,
        attached_table_width=table_width,
        wheel_width=wheel_width,
        attached_heading=heading,
        attached_heading_font=heading_font,
    )
