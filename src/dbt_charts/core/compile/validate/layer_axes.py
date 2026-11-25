"""Compile-time check that a layer's own y-axis title or style has an axis to land on.

A layer gets its own y axis only once some layer on the chart pins
``axis_y.position``; until then every layer shares the base chart's axis, so a
layer ``axis_y.title``, ``color`` or grid style has nowhere to go.
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
from dbt_charts.core.diagnostics.codes_compile import (
    ERR_LAYER_AXIS_STYLE_WITHOUT_POSITION,
    ERR_LAYER_AXIS_TITLE_WITHOUT_POSITION,
)


def validate_board_layer_axes(board: Board) -> None:
    """Raise for a layer y-axis title or style on a chart whose layers share one axis."""
    for chart_id, chart in board.charts.items():
        if not isinstance(chart, (AreaChart, BarChart, LineChart, ScatterChart)):
            continue
        if any(
            layer.axis_y is not None and layer.axis_y.position is not None
            for layer in chart.layers
        ):
            continue
        for index, layer in enumerate(chart.layers):
            axis = layer.axis_y
            if axis is None:
                continue
            field_path = f"charts.{chart_id}.layers.{index}.axis_y"
            if axis.title is not None:
                raise CompilationError.from_code(
                    ERR_LAYER_AXIS_TITLE_WITHOUT_POSITION,
                    field_path=f"{field_path}.title",
                )
            if axis.color is not None or (
                axis.grid is not None and axis.grid.model_fields_set - {"visible"}
            ):
                raise CompilationError.from_code(
                    ERR_LAYER_AXIS_STYLE_WITHOUT_POSITION, field_path=field_path
                )
