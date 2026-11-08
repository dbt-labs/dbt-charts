"""Each placed chart's merged style, attached to its board.

Projects ``build_chart_style_context`` and the family style each resolver
paints with onto ``MergedChartStyle``.
"""

from __future__ import annotations

from typing import Any

from pydantic import TypeAdapter

from dbt_charts.core.compile.models.board.normalized import Board
from dbt_charts.core.compile.models.chart.authored import GEO_CHART_TYPES
from dbt_charts.core.compile.models.chart.normalized import Chart
from dbt_charts.core.compile.models.primitives import ScaleTargetConfig
from dbt_charts.core.compile.models.style.authored import (
    CalloutChartStylePatch,
    KpiChartStylePatch,
    SparkBarChartStylePatch,
)
from dbt_charts.core.compile.models.style.context import ChartStyleContext
from dbt_charts.core.compile.models.style.merged import MergedChartStyle
from dbt_charts.core.compile.resolve.chart._kwargs import (
    _FAMILY_SLOT,
    board_family_style,
)
from dbt_charts.core.compile.resolve.chart._palette import _with_color_tokens
from dbt_charts.core.compile.resolve.style.board import unset_auto_support_ink
from dbt_charts.core.compile.resolve.style.chart_context import (
    build_chart_style_context,
    with_chart_memo,
)
from dbt_charts.core.compile.resolve.style.inherit_resolver import merge_unset
from dbt_charts.core.compile.resolve.style.palette import UnknownColorError
from dbt_charts.core.compile.sizing import table_width_is_pinned

_MERGED_STYLE = TypeAdapter(MergedChartStyle)

# Resolvers of these families read the per-chart context's slot; every other
# family's resolver merges the chart-local patch onto the board's slot itself.
_FROM_CHART_CONTEXT = frozenset({"bar", "line", "area", "kpi"})


def _color_gradient(chart: Chart) -> ScaleTargetConfig | None:
    """The gradient the chart's own `style.color` sets, as `normalize_chart_channels`
    reads it. Geo charts ignore it: their channel only names the field. A family
    without a `color` style (kpi, callout, spark_bar) sets none."""
    style = chart.style
    if (
        style is None
        or chart.type in GEO_CHART_TYPES
        or isinstance(
            style, KpiChartStylePatch | CalloutChartStylePatch | SparkBarChartStylePatch
        )
    ):
        return None
    color = style.color
    if color is None or color.gradient is None:
        return None
    return ScaleTargetConfig.model_validate(
        color.gradient.model_dump(exclude_none=True)
    )


def merged_chart_style(
    board_context: ChartStyleContext, chart: Chart
) -> MergedChartStyle:
    """The chart's family style and per-chart fields on ``board_context``.

    The family is what the chart's resolver paints with.
    """
    # The callout resolver never builds a per-chart context: its `title` slot is
    # a callout element, which the per-chart title merge cannot layer.
    context = (
        board_context
        if chart.type == "callout"
        else build_chart_style_context(board_context, chart)
    )
    fields: dict[str, Any] = {  # type-state: explicit_any — validator input
        "type": chart.type,
        "color": context.color,
        "palette": context.palette,
        "single_series_palette": context.single_series_palette,
        "requested_alias_palette": context.requested_alias_palette,
        "background": context.background,
        "ink_canvas": context.ink_canvas,
        "legend": context.legend,
        "title": context.title,
        "pagination": context.pagination,
        "gradient": _color_gradient(chart),
        "axis": context.axis_overrides_global,
        "axis_x": context.axis_overrides_x,
        "axis_y": context.axis_overrides_y,
        "axis_quantitative": context.axis_overrides_quantitative,
        "axis_band": context.axis_overrides_band,
    }
    if chart.type == "custom":
        return _MERGED_STYLE.validate_python(fields)
    if chart.type not in _FROM_CHART_CONTEXT:
        fields["family"] = merge_unset(
            board_family_style(board_context, chart),
            _with_color_tokens(chart.style, board_context),
        )
    elif chart.type == "kpi":
        fields["family"] = unset_auto_support_ink(context.kpi)
    else:
        fields["family"] = getattr(context, _FAMILY_SLOT[chart.type])
    if chart.type == "table":
        fields["support_table"] = context.support_table
        fields["width_pinned"] = table_width_is_pinned(chart)
    return _MERGED_STYLE.validate_python(fields)


def attach_chart_styles(board: Board) -> None:
    """Set ``board.chart_styles`` for the charts this board's own layout places.

    A nested board's charts belong to that board: their merge starts from its
    context, not an ancestor's. A chart whose style names an unknown color gets
    no entry: it compiles, and resolve reports the error on that chart's tile
    alone, as it does for every chart-local style fault.
    """
    # The board owns its context from here: the memo it fills must not sit on a
    # context shared with other boards or a cache.
    board.chart_style_context = with_chart_memo(board.chart_style_context)
    styles: dict[str, MergedChartStyle] = {}
    for item in board.layout.items:
        if item.chart is None:
            continue
        try:
            styles[item.chart.id] = merged_chart_style(
                board.chart_style_context, item.chart
            )
        except UnknownColorError:
            continue
    board.chart_styles = styles


def attach_chart_styles_tree(board: Board) -> None:
    """``attach_chart_styles`` on ``board`` and every nested board."""
    attach_chart_styles(board)
    for item in board.layout.items:
        if item.board is not None:
            attach_chart_styles_tree(item.board)
