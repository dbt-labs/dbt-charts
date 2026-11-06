"""Validate-time check that every chart-local color token resolves.

A chart's ``style:`` (and each layer's) may name palette stops. The family
resolvers resolve them at render, so a stop no palette has would validate clean
and fail only there. Cartesian charts only: merging the style of other
families (a callout's title elements, say) is not done the same way by their
resolvers.
"""

from __future__ import annotations

from dbt_charts.core.compile.errors import CompilationError
from dbt_charts.core.compile.models.board.normalized import Board
from dbt_charts.core.compile.models.chart.normalized import (
    AreaChart,
    BarChart,
    LineChart,
    ScatterChart,
)
from dbt_charts.core.compile.resolve.chart._palette import _with_color_tokens
from dbt_charts.core.compile.resolve.style.chart_context import (
    build_chart_style_context,
)
from dbt_charts.core.compile.resolve.style.palette import UnknownColorError
from dbt_charts.core.compile.restated import scoped_charts
from dbt_charts.core.diagnostics.codes_compile import ERR_COLOR_TOKEN


def chart_color_token_errors(board: Board) -> list[CompilationError]:
    """An ``ERR-COLOR-TOKEN`` for each chart placed on *board* (nested boards
    included, against their own style) whose style or a layer's names a color
    no palette resolves.

    ``compile()`` leaves these to render, where the one chart's tile errors and
    the rest of the board draws; ``dct validate`` reports them all up front.
    """
    errors: list[CompilationError] = []
    for chart, holder in scoped_charts(board):
        if not isinstance(chart, (AreaChart, BarChart, LineChart, ScatterChart)):
            continue
        try:
            context = build_chart_style_context(holder.chart_style_context, chart)
            for layer in chart.layers:
                _with_color_tokens(layer.style, context)
        except UnknownColorError as exc:
            errors.append(
                CompilationError.from_code(
                    ERR_COLOR_TOKEN, scope=f"chart {chart.id!r}", reason=str(exc)
                )
            )
    return errors
