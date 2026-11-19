"""Compile-time check for ``labels.visible`` anchors where no ordered series exists.

Anchors pick marks from each series, so they need a line, area or bar chart's
own series, or a line, area or scatter layer over such a chart's x. A scatter
chart has no ordered series, so neither it nor its layers take them; a bar
layer's labels have no anchor path.
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
from dbt_charts.core.compile.models.style.theme.marks import label_anchors
from dbt_charts.core.diagnostics.codes_compile import ERR_LABELS_ANCHOR_UNSUPPORTED


def _anchored_label_path(
    style: BaseModel | None,  # type-state: optional — None when no style is authored
) -> str | None:
    """Path under ``style`` of the first line, point or bar labels naming anchors."""
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
            if "visible" in node and label_anchors(node["visible"]):
                return f"marks.{mark_name}.labels.visible"
    return None


def validate_board_label_anchors(board: Board) -> None:
    """Raise for ``labels.visible`` anchors on a scatter chart, its layers or a bar layer."""
    for chart_id, chart in board.charts.items():
        targets: list[tuple[str, str, BaseModel | None]] = (
            [(f"charts.{chart_id}.style", "a scatter chart", chart.style)]
            if isinstance(chart, ScatterChart)
            else []
        )
        if isinstance(chart, (AreaChart, BarChart, LineChart, ScatterChart)):
            for i, layer in enumerate(chart.layers):
                if layer.type == "bar" or isinstance(chart, ScatterChart):
                    where = "a bar layer" if layer.type == "bar" else "a layer"
                    targets.append(
                        (f"charts.{chart_id}.layers[{i}].style", where, layer.style)
                    )
        for base, where, style in targets:
            if (suffix := _anchored_label_path(style)) is not None:
                raise CompilationError.from_code(
                    ERR_LABELS_ANCHOR_UNSUPPORTED,
                    chart_id=chart_id,
                    field_path=f"{base}.{suffix}",
                    where=where,
                )
