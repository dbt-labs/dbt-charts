"""Pie chart resolver."""

from __future__ import annotations

from types import EllipsisType
from typing import Any, Literal

from dbt_charts.core.compile.config import get_chart_rendering
from dbt_charts.core.compile.format import resolve_format_for_values
from dbt_charts.core.compile.merge import merge_onto_base
from dbt_charts.core.compile.models.chart.normalized import (
    PieChart,
    TableChart,
)
from dbt_charts.core.compile.models.chart.resolved import (
    ResolvedPieChart,
)
from dbt_charts.core.compile.models.primitives import ResolvedFontStyle
from dbt_charts.core.compile.models.style.authored import TableChartStylePatch
from dbt_charts.core.compile.models.style.context import ChartStyleContext
from dbt_charts.core.compile.models.style.resolved import (
    ResolvedPieStyle,
)
from dbt_charts.core.compile.models.style.theme import SliceLabelsStyle, SliceMarkStyle
from dbt_charts.core.compile.resolve.chart._channels import (
    _channels_for,
    _column_numeric_values,
)
from dbt_charts.core.compile.resolve.chart._kwargs import (
    AutomaticLinkCandidate,
    ChartTextVariables,
    _base_kwargs,
    _bound_scales,
    _shared_kwargs,
    _title_font,
)
from dbt_charts.core.compile.resolve.chart._palette import (
    _effective_palette,
    _effective_requested_alias_palette,
    _with_color_tokens,
)
from dbt_charts.core.compile.resolve.chart._table import (
    _EMPTY_TABLE_COLUMN_LINKS,
    _resolve_table,
)
from dbt_charts.core.compile.resolve.chart.label_data import (
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
)
from dbt_charts.core.compile.resolve.style.palette import mark_ink
from dbt_charts.core.diagnostics.chart_data import ChartDataError
from dbt_charts.core.text.predefined_formats import PredefinedNumberFormat

__all__ = [
    "_resolve_pie",
]


def _resolve_pie(
    normalized: PieChart,
    data: list[dict[str, Any]],
    chart_style_context: ChartStyleContext,
    width: float,
    automatic_link_candidate: AutomaticLinkCandidate,
    variables: ChartTextVariables,
    title_font: ResolvedFontStyle | EllipsisType = ...,
) -> ResolvedPieChart:
    validate_theta_values(normalized.id, normalized.theta, data)
    chart_local_style_context = build_chart_style_context(
        chart_style_context, normalized
    )
    primary = _with_color_tokens(normalized.style, chart_style_context)
    pie = merge_onto_base(chart_style_context.pie, primary)
    # The center total and every slice tooltip vote on one set (theta values
    # plus their sum) so they never disagree about the same sub-$1 value --
    # see resolve_format_for_values's per-set contract.
    theta_values = _column_numeric_values(data, normalized.theta)
    format_vote_values = [*theta_values, sum(theta_values)] if theta_values else []
    channels = _channels_for(normalized, data)
    # Pie patches are radial (_RadialChartStyle). Use _effective_palette to pick up
    # chart-local color.categorical override or the board-level palette.
    eff_palette = _effective_palette(chart_style_context, primary)
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
    # patch-peeking is needed) is applied later by PieEmitter, which prefers
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
    _slice_labels = pie.marks.slice.labels
    if _slice_labels is None:
        raise ValueError(
            "pie.marks.slice.labels is None after cascade — check theme defaults"
        )
    effective_template = _slice_labels.template
    if effective_template is None:
        effective_template = (
            _slice_labels.default_template.with_color
            if resolved_color_field
            else _slice_labels.default_template.no_color
        )
    resolved_labels = SliceLabelsStyle(
        offset=_slice_labels.offset,
        line_height=_slice_labels.line_height,
        font=_slice_labels.font,
        default_template=_slice_labels.default_template,
        template=effective_template,
        where=_slice_labels.where,
    )

    total = normalized.total

    # Donut center value format: style-cascade field (pie.total.value.format),
    # resolved the same way and against the same vote set as the slice tooltip
    # so the two never disagree about the same sub-$1 value. A donut whose
    # cascade never authored a format still defaults to plain integers -- the
    # same fallback the field used to get from normalize-time auto-injection,
    # moved here because the cascade isn't resolved yet at normalize time.
    is_donut_shape = pie.inner_radius is not None and pie.inner_radius > 0
    raw_total_format = pie.total.value.format
    if raw_total_format is None and is_donut_shape:
        raw_total_format = str(PredefinedNumberFormat.integer)
    total_style = pie.total
    if raw_total_format is not None:
        resolved_total_format = resolve_format_for_values(
            raw_total_format, chart_style_context.formats, format_vote_values
        )
        total_style = pie.total.model_copy(
            update={
                "value": pie.total.value.model_copy(
                    update={"format": resolved_total_format}
                )
            }
        )

    label_font_family = resolved_labels.font.family
    label_font_size = resolved_labels.font.size
    if label_font_family is None or label_font_size is None:
        raise ValueError("pie slice label font is incomplete after cascade")
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

    def plan_for(arc_mode: ArcRenderMode) -> AttachmentPlan:
        return plan_attachment(
            arc_mode,
            shares,
            data,
            eff_palette,
            resolved_color_field,
            normalized.theta,
            normalized.format,
            chart_style_context.table,
            width,
            chart_category_colors,
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
    placement: Literal["none", "below", "right"] = "none"
    table_width = 0.0
    wheel_width = 0.0
    heading = ""
    heading_font = None
    if plan is not None:
        attached_row_indices = plan.row_indices
        placement = plan.placement
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
            variables,
            # A legend entry is read beside its own slice, not scanned down
            # a column -- an ANCHOR-mode magnitude declared once on the top
            # entry and stripped from the rest reads as data loss here, not
            # alignment (see _with_resolved_scale_stops's own docstring).
            allow_shared_scale=False,
        )
        heading = "Too small to label" if mode == "hybrid" else ""
        heading_font = resolve_hybrid_heading_font(attached_table.style.table)

    _tf = (
        _title_font(normalized, chart_local_style_context, width)
        if title_font is ...
        else title_font
    )
    pie_config = get_chart_rendering().pie
    return ResolvedPieChart(
        **_base_kwargs(
            normalized,
            chart_style_context,
            channels,
            pie.legend,
            eff_palette,
            requested_alias_palette=_effective_requested_alias_palette(
                chart_style_context, primary
            ),
            automatic_link_candidate=automatic_link_candidate,
            layout_padding=pie.padding,
            suppress_legend=mode != "direct",
        ),
        **_shared_kwargs(normalized, variables, chart_local_style_context),
        chart_type="pie",
        theta=normalized.theta,
        color=normalized.color,
        identity_field=resolved_color_field,
        total=total,
        format=normalized.format,
        style=ResolvedPieStyle(
            inner_radius=pie.inner_radius if pie.inner_radius is not None else 0.0,
            slice_mark=effective_slice_mark,
            tooltip_format=resolve_format_for_values(
                chart_style_context.tooltip.format,
                chart_style_context.formats,
                format_vote_values,
            ),
            total_style=total_style,
            title_font=_tf,
        ),
        dark_companion_stops=dark_stops,
        resolution_width=width,
        outer_fraction=pie_config.outer_fraction,
        attached_table_gap=pie_config.attached_table_gap_px,
        hybrid_heading_gap=pie_config.hybrid_heading_gap_px,
        presentation_fingerprint=pie_presentation_fingerprint(data),
        slice_label_indices=tuple(
            index for index, lines in enumerate(slice_label_lines) if lines
        ),
        attached_table=attached_table,
        attached_row_indices=attached_row_indices,
        attached_table_placement=placement,
        attached_table_width=table_width,
        wheel_width=wheel_width,
        attached_heading=heading,
        attached_heading_font=heading_font,
    )
