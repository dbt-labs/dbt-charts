"""Shared title band for the titled items of one ``cols:`` row.

Stage: RENDER (sizing)
Purpose: Titles in a row start at one top edge, so titles of different size put
their first baselines, and the bodies beneath them, at different heights. The row
computes one baseline and one body top from the titles actually present in it.

Every titled item's first baseline sits on the largest ascent in the row. Every
titled item's body starts where the lowest shifted title block ends, so a wrapped
title's taller block drops its neighbors' bodies. An untitled item reserves no
band. A row whose titles agree computes all-zero shifts.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from typing import TYPE_CHECKING

from dbt_charts.core.compile.config import get_chart_rendering
from dbt_charts.core.compile.models.board.normalized import (
    Board,
    TitleShift,
    VariableValues,
)
from dbt_charts.core.compile.models.chart.normalized import PAINTS_MARKS
from dbt_charts.core.compile.models.chart.resolved import (
    ResolvedChart,
    ResolvedPieChart,
    ResolvedSparkBarChart,
    ResolvedTableChart,
)
from dbt_charts.core.compile.models.chart.resolved._base import (
    _SharedResolvedChartFields,
)
from dbt_charts.core.compile.models.style.resolved import effective_padding
from dbt_charts.core.render.chart.spec_builders import (
    VEGA_SUBTITLE_PADDING,
    VEGA_TITLE_OFFSET,
)
from dbt_charts.core.render.chart.table import (
    compute_table_title_block_layout,
    title_ascent,
)
from dbt_charts.core.render.sizing import get_title_height, title_baseline_offset

if TYPE_CHECKING:
    from dbt_charts.core.compile.models.style.resolved import ResolvedStyle

__all__ = [
    "TitleMetrics",
    "board_title_metrics",
    "chart_title_metrics",
    "row_title_shifts",
]


@dataclass(frozen=True)
class TitleMetrics:
    """One item's natural title geometry, measured from the item's top edge."""

    ascent: float  # top edge to the first baseline
    tail: float  # first baseline to the top of the body


def row_title_shifts(metrics: Sequence[TitleMetrics | None]) -> list[TitleShift]:
    """Per-item shifts that put every title on one baseline and every body on one top.

    ``None`` marks an item with no title; it gets a zero shift and adds nothing
    to the band.
    """
    titled = [m for m in metrics if m is not None]
    if not titled:
        return [TitleShift() for _ in metrics]
    baseline = max(m.ascent for m in titled)
    # Each title block, once shifted onto the baseline, ends at baseline + tail.
    body_top = baseline + max(m.tail for m in titled)
    return [
        TitleShift()
        if m is None
        else TitleShift(
            title_dy=baseline - m.ascent,
            body_dy=body_top - m.ascent - m.tail,
        )
        for m in metrics
    ]


def chart_title_metrics(
    chart: ResolvedChart, card_padding: float, slot_width: float
) -> TitleMetrics | None:
    """Title geometry of a chart's title block, or None when it has no title.

    Vega-Lite puts the first baseline ``title_ascent`` below the padded top and
    the plot ``title.offset`` below the title block, whose height is the title's
    font size plus its subtitle's. Nothing about the plot itself enters: two
    charts whose titles agree compute the same tail, and keep whatever the plots
    do with axis labels or a support_table strip.

    Hand-drawn families (table, spark_bar) read their own layout. A KPI has no
    title slot: its label sits under its value, and a callout's card is drawn
    without one, so both are untitled for the band. A Vega-Lite title is one
    line: a wrapping overflow mode is not modeled.
    """
    if not isinstance(chart, _SharedResolvedChartFields) or not chart.title:
        return None
    # The composite anchors its table from the wheel's own height, which a shift
    # does not change, so it stays out of the band.
    if isinstance(chart, ResolvedPieChart) and chart.attached_table is not None:
        return None
    title_font = chart.style.title_font
    assert title_font is not None, "a titled chart resolves its title font"
    size = float(title_font.size)
    inset_top = card_padding + chart.layout_padding.top
    if isinstance(chart, ResolvedTableChart):
        padding = int(chart.style.table.outer_padding)
        block = compute_table_title_block_layout(
            chart_title=chart.title,
            chart_subtitle=chart.subtitle
            or "",  # type-state: silent_fallback — no subtitle is the empty string the title-block helper reads as absent
            table_width=slot_width,
            tc=chart.style.table,
            padding=padding,
            title_style=chart.style.title,
            card_padding=card_padding,
            title_font=title_font,
        )
        ascent = title_ascent(size)
        return TitleMetrics(
            ascent=inset_top + padding + ascent, tail=block.height - ascent
        )
    if isinstance(chart, ResolvedSparkBarChart):
        spark = get_chart_rendering().spark_bar
        block_height = spark.title_height + (int(size) if chart.subtitle else 0)
        return TitleMetrics(
            ascent=inset_top + spark.title_baseline_y,
            tail=block_height - spark.title_baseline_y,
        )
    if chart.chart_type not in PAINTS_MARKS:
        return None
    offset = chart.title_style.position.offset
    ascent = title_ascent(size)
    # One subtitle line: its font size plus Vega's default subtitle padding.
    subtitle = 0.0
    if chart.subtitle:
        subtitle_size = chart.title_style.subtitle.font.size
        assert subtitle_size is not None, "the cascade fills the subtitle font size"
        subtitle = float(subtitle_size) + VEGA_SUBTITLE_PADDING
    return TitleMetrics(
        ascent=inset_top + ascent,
        tail=size
        + subtitle
        + (VEGA_TITLE_OFFSET if offset is None else offset)
        - ascent,
    )


def board_title_metrics(
    board: Board,
    variable_values: VariableValues,
    inline_band: bool,
) -> TitleMetrics | None:
    """Title geometry of a nested board drawn with a plain title above its content.

    ``inline_band`` is whether the board's title shares a band with its variable
    controls; that layout sizes itself and is not aligned.
    """
    if not board.title or inline_band:
        return None
    style: ResolvedStyle = board.resolved_style
    card_padding = float(style.frame.card_padding)
    margin_top = style.margin.top if style.margin else 0.0
    inner = max(board.layout.content_width - 2 * card_padding, 1.0)
    block = max(
        get_title_height(
            board.title,
            inner,
            variable_values,
            level=board.level,
            resolved_style=style,
        ),
        float(style.title.min_height),
    )
    baseline = title_baseline_offset(style, board.level)
    # A title flows straight into text below it. Layout items and controls sit one
    # gap down, and an item's ink starts a card padding inside its own box.
    child_gap = style.gap if style.gap is not None else 0.0
    if board.text:
        gap_below = 0.0
    elif board.layout.items:
        gap_below = child_gap + card_padding
    elif board.visible_variables:
        gap_below = child_gap
    else:
        gap_below = 0.0
    return TitleMetrics(
        ascent=margin_top + effective_padding(style).top + card_padding + baseline,
        tail=block - baseline + gap_below,
    )
