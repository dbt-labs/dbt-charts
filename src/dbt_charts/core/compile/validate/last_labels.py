"""Compile-time check for ``labels.visible: last`` where no last point exists.

``last`` labels each series' final mark on the drawn axis, so it needs a line,
area or bar chart's own series: a scatter chart has no ordered series and a
layer none of its own. The renderer refuses the same shapes; this reports them by authored
path before render.
"""

from __future__ import annotations

from pydantic import BaseModel

from dbt_charts.core.compile.errors import CompilationError
from dbt_charts.core.compile.models.board.normalized import Board
from dbt_charts.core.compile.models.chart.normalized import (
    AreaChart,
    BarChart,
    LineChart,
    ScatterChart,
)
from dbt_charts.core.diagnostics.codes_compile import ERR_LABELS_LAST_UNSUPPORTED


def _last_label_path(
    style: BaseModel | None,  # type-state: optional — None when no style is authored
) -> str | None:
    """Path under ``style`` of the first line, point or bar labels saying ``last``."""
    if style is None:
        return None
    authored = style.model_dump(exclude_none=True)
    for mark_name in ("line", "point", "bar"):
        node = authored
        for key in ("marks", mark_name, "labels"):
            if key not in node:
                break
            node = node[key]
        else:
            if "visible" in node and node["visible"] == "last":
                return f"marks.{mark_name}.labels.visible"
    return None


def validate_board_last_labels(board: Board) -> None:
    """Raise for ``visible: last`` on a scatter chart or on a layer."""
    for chart_id, chart in board.charts.items():
        targets = (
            [(f"charts.{chart_id}.style", "a scatter chart", chart.style)]
            if isinstance(chart, ScatterChart)
            else []
        )
        if isinstance(chart, (AreaChart, BarChart, LineChart, ScatterChart)):
            targets += [
                (f"charts.{chart_id}.layers[{i}].style", "a layer", layer.style)
                for i, layer in enumerate(chart.layers)
            ]
        for base, where, style in targets:
            if (suffix := _last_label_path(style)) is not None:
                raise CompilationError.from_code(
                    ERR_LABELS_LAST_UNSUPPORTED,
                    chart_id=chart_id,
                    field_path=f"{base}.{suffix}",
                    where=where,
                )
