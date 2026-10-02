"""Slide a legend along its edge to the middle or the end.

Vega-Lite orients a legend to a cardinal (reserving a strip, start-aligned to the
plot) or a corner (floating, inset), and has no centered or end orient. Such a
legend starts from the orient on its edge and is slid along that edge here:
vl-convert's scenegraph supplies the plot and legend rectangles, and the same
translate is rewritten in the rendered SVG, where the i-th ``role-legend`` group
is the i-th legend node (vl-convert serializes the SVG from that scenegraph).
"""

from __future__ import annotations

import re
from typing import Any, NamedTuple

from dbt_charts.core.compile.models.style.theme import LegendAlign, LegendEdge
from dbt_charts.core.diagnostics.chart_data import ChartDataError
from dbt_charts.core.diagnostics.codes_render import ERR_LEGEND_ALIGN_UNSUPPORTED

_LEGEND_TRANSLATE = re.compile(
    r'(class="mark-group role-legend"[^>]*><g transform="translate\()'
    r"(-?[\d.eE+-]+),(-?[\d.eE+-]+)(\)\">)"
)


class _LegendNode(NamedTuple):
    """A legend item and the view group it sits in."""

    legend: Any  # type-state: explicit_any — untyped vl-convert scenegraph item
    view: Any  # type-state: explicit_any — untyped vl-convert scenegraph item


def _collect_legends(
    node: Any,  # type-state: explicit_any — untyped vl-convert scenegraph JSON
    view: Any,  # type-state: explicit_any — untyped vl-convert scenegraph JSON
    out: list[_LegendNode],
) -> None:
    """Append every legend item under ``node`` in document order, with its view.

    The view is the nearest enclosing group item that carries a plot size.
    """
    if not isinstance(node, dict):
        return
    items = node.get(
        "items", ()
    )  # type-state: silent_fallback — a mark without items has none
    if (
        node.get("role") == "legend"
    ):  # type-state: silent_fallback — a mark without a role is not a legend
        out.extend(_LegendNode(item, view) for item in items)
        return
    for item in items:
        sized = isinstance(item, dict) and "width" in item and "height" in item
        _collect_legends(item, item if sized else view, out)


def legend_shifts(
    scenegraph: Any,  # type-state: explicit_any — untyped vl-convert scenegraph JSON
    edge: LegendEdge,
    align: LegendAlign,
    chart_id: str,
) -> list[float]:
    """Per-legend slide, in pixels along ``edge``, that aligns the legends as a block.

    Legends sharing a view move together so stacked legends keep their order.
    Raises ``ERR-LEGEND-ALIGN-UNSUPPORTED`` when there is no legend, no plot
    size to align in, or a legend larger than its plot.
    """
    nodes: list[_LegendNode] = []
    _collect_legends(scenegraph["scenegraph"], None, nodes)
    if not nodes:
        raise ChartDataError.from_code(
            ERR_LEGEND_ALIGN_UNSUPPORTED,
            chart_id=chart_id,
            reason="the rendered chart holds no legend",
        )
    along_x = edge in ("top", "bottom")
    start_key, size_key = ("x", "width") if along_x else ("y", "height")
    shifts: list[float] = []
    for _legend, view in nodes:
        views = [other.legend for other in nodes if other.view is view]
        block_start = min(float(other[start_key]) for other in views)
        block_end = max(
            float(other[start_key]) + float(other[size_key]) for other in views
        )
        span = float(view[size_key])
        block = block_end - block_start
        if span <= 0:
            raise ChartDataError.from_code(
                ERR_LEGEND_ALIGN_UNSUPPORTED,
                chart_id=chart_id,
                reason="its plot has no size to align in",
            )
        if block >= span:
            raise ChartDataError.from_code(
                ERR_LEGEND_ALIGN_UNSUPPORTED,
                chart_id=chart_id,
                reason=f"its legend ({block:g}px) is larger than its plot ({span:g}px)",
            )
        shifts.append(
            (span - block) / 2 - block_start if align == "center" else span - block_end
        )
    return shifts


def apply_legend_shifts(
    svg: str, shifts: list[float], edge: LegendEdge, chart_id: str
) -> str:
    """Rewrite each legend group's translate by its shift along ``edge``."""
    matches = list(_LEGEND_TRANSLATE.finditer(svg))
    if len(matches) != len(shifts):
        raise ChartDataError.from_code(
            ERR_LEGEND_ALIGN_UNSUPPORTED,
            chart_id=chart_id,
            reason=(
                f"the SVG holds {len(matches)} legends where the scenegraph "
                f"holds {len(shifts)}"
            ),
        )
    along_x = edge in ("top", "bottom")
    out: list[str] = []
    cursor = 0
    for match, shift in zip(matches, shifts, strict=True):
        x, y = float(match.group(2)), float(match.group(3))
        x, y = (x + shift, y) if along_x else (x, y + shift)
        out.append(svg[cursor : match.start()])
        out.append(f"{match.group(1)}{x:g},{y:g}{match.group(4)}")
        cursor = match.end()
    out.append(svg[cursor:])
    return "".join(out)
