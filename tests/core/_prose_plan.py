"""Shared helpers for tests that size or draw a prose block outside a board."""

import re
import xml.etree.ElementTree as ET
from typing import Literal

from dbt_charts.core.compile.models.board.normalized import ProsePlan
from dbt_charts.core.compile.models.style.resolved import ResolvedStyle
from dbt_charts.core.font_measure import markdown_font_faces
from dbt_charts.core.render.column_packer import choose_grid
from dbt_charts.core.render.sizing import (
    body_text_font_family,
    get_compact_style,
    max_chars_to_px,
)


def full_width_plan(
    resolved_style: ResolvedStyle, width: float, grid: Literal[1, 2, 3] | None = None
) -> ProsePlan:
    """Plan for a block whose slot is ``width``: the board is that slot plus its card padding.

    The grid is the one ``choose_grid`` returns for that board unless given.
    """
    pad = float(resolved_style.frame.card_padding)
    gap = float(resolved_style.layout.cols.gap)
    board_width = width + 2 * pad
    if grid is None:
        style = get_compact_style(resolved_style)
        font = markdown_font_faces(body_text_font_family(resolved_style), style).regular
        char_px = max_chars_to_px(font, float(style.base_font_size), 1)
        grid = choose_grid(board_width, gap, pad, char_px)
    return ProsePlan(grid, board_width, gap)


def card_plan(resolved_style: ResolvedStyle, width: float) -> ProsePlan:
    """Plan for a block that is a card narrower than two spans: one whole-container column."""
    container = width + 2 * float(resolved_style.frame.card_padding)
    gap = float(resolved_style.layout.cols.gap)
    return ProsePlan(2, 2 * container + gap, gap)


def text_nodes(svg: str) -> list[tuple[float, str]]:
    """``(x in the root viewport, text)`` of every ``<text>`` with an x.

    Sums every ``translate`` and nested ``<svg x=...>`` on the way down, so a
    line inside a later column is placed where it is actually painted.
    """
    out: list[tuple[float, str]] = []

    def walk(el: ET.Element, tx: float) -> None:
        tag = el.tag.rsplit("}", 1)[-1]
        if tag == "svg":
            tx += float(el.get("x", 0))
        match = re.match(r"translate\(([-\d.]+)", el.get("transform", ""))
        if match:
            tx += float(match.group(1))
        if tag == "text" and el.get("x") is not None:
            out.append((tx + float(el.get("x", 0)), "".join(el.itertext())))
        for child in el:
            walk(child, tx)

    walk(ET.fromstring(svg), 0.0)
    return out
