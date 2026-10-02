"""Mechanical SVG composition for a resolved pie and companion table."""

from __future__ import annotations

import html
import re

from dbt_charts.core.compile.models.style.theme import LegendAlign, LegendEdge
from dbt_charts.core.render.svg_utils import escape_attr


def _arc_disk_diameter(donut_svg: str) -> float:
    block = re.search(
        r'class="mark-arc role-mark[^"]*"[^>]*>(.*?)</g>', donut_svg, re.DOTALL
    )
    if not block:
        return 0.0
    radii = [
        float(match.group(1))
        for match in re.finditer(r"A\s*([0-9.]+),", block.group(1))
    ]
    return max(radii) * 2 if radii else 0.0


def heading_block_height(heading_font_size: float, heading_gap: float) -> float:
    """Height a hybrid table's heading adds above the table."""
    return heading_font_size + 2 + heading_gap


def _title_group_span(svg: str) -> tuple[int, int] | None:
    """Start and end of the Vega-Lite title group in *svg*, balanced over nested groups."""
    start = svg.find('<g class="mark-group role-title"')
    if start < 0:
        return None
    depth = 0
    for match in re.finditer(r"<g\b|</g>", svg[start:]):
        depth += 1 if match.group() == "<g" else -1
        if depth == 0:
            return start, start + match.end()
    return None


def _slide_title(donut_svg: str, dx: float) -> str:
    """Move the donut's title *dx* sideways, past the donut's own viewport."""
    span = _title_group_span(donut_svg)
    if span is None:
        return donut_svg
    start, end = span
    moved = (
        f'{donut_svg[:start]}<g transform="translate({dx:g},0)">'
        f"{donut_svg[start:end]}</g>{donut_svg[end:]}"
    )
    return moved.replace("<svg ", '<svg overflow="visible" ', 1)


def compose_attached_table_svg(
    donut_svg: str,
    table_svg: str,
    donut_width: float,
    donut_height: float,
    table_width: float,
    table_height: float,
    card_width: float,
    *,
    gap: float,
    heading_gap: float,
    placement: LegendEdge = "bottom",
    heading: str = "",
    heading_font_family: str = "",
    heading_font_size: float = 0.0,
    heading_font_weight: str = "",
    heading_color: str = "",
    align: LegendAlign,
    top_table_y: float = 0.0,
    inset_left: float = 0.0,
    inset_right: float = 0.0,
) -> tuple[str, float, float]:
    """Compose already-rendered child SVGs using finalized placement facts.

    For ``placement="top"`` the donut SVG already reserves the room the table
    fills (``render_arc_attached_table`` pushes its plot down by the table's
    height), so the table is dropped in at ``top_table_y``, below the title.
    ``align`` places the table along its edge: across the card for top and
    bottom (inside ``inset_left``/``inset_right``), down the wheel's disk for
    left and right. For ``placement="left"`` the donut SVG keeps its own left
    padding as part of the gap, so the table sits at ``inset_left``.
    """
    if heading:
        block_height = heading_block_height(heading_font_size, heading_gap)
        escaped_heading = html.escape(heading)
        heading_svg = (
            f'<text x="0" y="{heading_font_size * 0.8:.2f}" '
            f'font-family="{escape_attr(heading_font_family)}" font-size="{escape_attr(int(heading_font_size))}" '
            f'font-weight="{escape_attr(heading_font_weight)}" fill="{escape_attr(heading_color)}">'
            f"{escaped_heading}</text>"
        )
        table_svg = (
            f'<svg xmlns="http://www.w3.org/2000/svg" width="{escape_attr(table_width)}" '
            f'height="{escape_attr(table_height + block_height)}">'
            f"{heading_svg}"
            f'<g transform="translate(0, {escape_attr(block_height)})">{table_svg}</g>'
            f"</svg>"
        )
        table_height += block_height

    outer_width = card_width
    across_x = {
        "start": inset_left,
        "center": (card_width - table_width) / 2,
        "end": card_width - table_width - inset_right,
    }[align]
    if placement in ("left", "right"):
        diameter = _arc_disk_diameter(donut_svg)
        disk_top = max(0.0, (donut_height - diameter) / 2.0)
        table_y = max(
            disk_top,
            {
                "start": disk_top,
                "center": disk_top + (diameter - table_height) / 2,
                "end": disk_top + diameter - table_height,
            }[align],
        )
        donut_y = 0.0
        if placement == "right":
            donut_x, table_x = 0.0, donut_width + gap
        else:
            table_x, donut_x = inset_left, table_width + gap
            donut_svg = _slide_title(donut_svg, -donut_x)
        outer_height = max(donut_height, table_y + table_height)
    elif placement == "top":
        outer_height = donut_height
        table_x, table_y = across_x, top_table_y
        donut_x, donut_y = (card_width - donut_width) / 2, 0
    else:
        outer_height = donut_height + gap + table_height
        donut_x, donut_y = (card_width - donut_width) / 2, 0
        table_x, table_y = across_x, donut_height + gap
    return (
        f'<svg xmlns="http://www.w3.org/2000/svg" '
        f'width="{escape_attr(outer_width)}" height="{escape_attr(outer_height)}" '
        f'viewBox="0 0 {escape_attr(outer_width)} {escape_attr(outer_height)}">'
        f'<g transform="translate({escape_attr(donut_x)}, {escape_attr(donut_y)})">{donut_svg}</g>'
        f'<g transform="translate({escape_attr(table_x)}, {escape_attr(table_y)})">{table_svg}</g>'
        f"</svg>",
        outer_width,
        outer_height,
    )
