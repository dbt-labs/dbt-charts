"""Mechanical SVG composition for a resolved pie and companion table."""

from __future__ import annotations

import html
import re
from typing import Literal

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
    placement: Literal["below", "right"] = "below",
    heading: str = "",
    heading_font_family: str = "",
    heading_font_size: float = 0.0,
    heading_font_weight: str = "",
    heading_color: str = "",
) -> tuple[str, float, float]:
    """Compose already-rendered child SVGs using finalized placement facts."""
    if heading:
        heading_block_height = heading_font_size + 2 + heading_gap
        escaped_heading = html.escape(heading)
        heading_svg = (
            f'<text x="0" y="{heading_font_size * 0.8:.2f}" '
            f'font-family="{escape_attr(heading_font_family)}" font-size="{escape_attr(int(heading_font_size))}" '
            f'font-weight="{escape_attr(heading_font_weight)}" fill="{escape_attr(heading_color)}">'
            f"{escaped_heading}</text>"
        )
        table_svg = (
            f'<svg xmlns="http://www.w3.org/2000/svg" width="{escape_attr(table_width)}" '
            f'height="{escape_attr(table_height + heading_block_height)}">'
            f"{heading_svg}"
            f'<g transform="translate(0, {escape_attr(heading_block_height)})">{table_svg}</g>'
            f"</svg>"
        )
        table_height += heading_block_height

    outer_width = card_width
    if placement == "right":
        disk_top = max(0.0, (donut_height - _arc_disk_diameter(donut_svg)) / 2.0)
        donut_x, donut_y = 0.0, 0.0
        table_x, table_y = donut_width + gap, disk_top
        outer_height = max(donut_height, disk_top + table_height)
    else:
        outer_height = donut_height + gap + table_height
        donut_x, donut_y = (card_width - donut_width) / 2, 0
        table_x, table_y = (card_width - table_width) / 2, donut_height + gap
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
