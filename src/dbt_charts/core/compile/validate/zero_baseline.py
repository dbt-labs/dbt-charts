"""Compile-time check for an authored ``zero: false`` on a bar or area chart.

A bar's length and an area's fill are measured from zero, so cropping the y
axis away from it misstates the quantity. A bar cannot paint at all once its
baseline is cut (it surfaces later as ``ERR-CHART-PAINTED-NO-MARKS``), so it
is rejected here; an area paints cropped, which is sometimes deliberate, so it
warns. A floating bar (``y_start``) has no zero baseline and is exempt.
"""

from __future__ import annotations

from collections.abc import Mapping

from pydantic import BaseModel

from dbt_charts.core.compile.errors import CompilationError
from dbt_charts.core.compile.models.board.normalized import Board
from dbt_charts.core.compile.models.chart.normalized import AreaChart, BarChart
from dbt_charts.core.compile.models.style.authored import (
    AreaChartStylePatch,
    BarChartStylePatch,
)
from dbt_charts.core.diagnostics import Diagnostic
from dbt_charts.core.diagnostics.codes_compile import (
    ERR_BAR_ZERO_BASELINE_CROPPED,
    WARN_AREA_ZERO_BASELINE_CROPPED,
)


def validate_board_zero_baselines(
    board: Board,
    chart_styles: Mapping[
        str, BaseModel | None
    ],  # type-state: optional — None when the chart authors no style
) -> list[Diagnostic]:
    """Raise for a cropped bar baseline; return a warning per cropped area.

    ``chart_styles`` maps chart id to the style patch the chart itself authored.
    """
    warnings: list[Diagnostic] = []
    for chart_id, chart in board.charts.items():
        if not isinstance(chart, (BarChart, AreaChart)):
            continue
        style = chart_styles[chart_id]
        axis_y = (
            style.axis_y
            if isinstance(style, (BarChartStylePatch, AreaChartStylePatch))
            else None
        )
        scale = axis_y.scale if axis_y is not None else None
        continuous = scale.continuous if scale is not None else None
        if continuous is None or continuous.zero is not False:
            continue
        path = f"charts.{chart_id}.style.axis_y.scale.continuous.zero"
        if isinstance(chart, AreaChart):
            warnings.append(
                Diagnostic.from_code(
                    WARN_AREA_ZERO_BASELINE_CROPPED,
                    chart=chart_id,
                    field="axis_y.scale.continuous.zero",
                    path=path,
                    message=WARN_AREA_ZERO_BASELINE_CROPPED.message_template.format(
                        chart_id=chart_id
                    ),
                    fix=WARN_AREA_ZERO_BASELINE_CROPPED.fix_template,
                )
            )
        elif chart.y_start is None:
            raise CompilationError.from_code(
                ERR_BAR_ZERO_BASELINE_CROPPED, chart_id=chart_id, field_path=path
            )
    return warnings
