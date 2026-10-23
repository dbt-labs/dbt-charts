"""Parity with Vega: a stack's segments are square but for its outer end and paint their
stroke once; a one-color palette gives every endpoint label that color's companion ink."""

from __future__ import annotations

import re

from .._svg_render import render_board_to_svg


def _board(chart: str, rows: str, style: str = "") -> str:
    return f"""
title: T
{style}
queries:
  q:
    type: values
    rows:
{rows}
charts:
  c:
    query: q
{chart}
rows:
  - c
"""


def test_a_stacks_inner_joins_are_square_and_each_segment_strokes_once() -> None:
    rows = "\n".join(
        f"      - {{m: {m}, s: {s}, v: {v}}}"
        for m in ("Jan", "Feb")
        for s, v in (("a", 3), ("b", 2), ("c", 1))
    )
    for orientation in ("horizontal", "vertical"):
        svg = render_board_to_svg(
            _board(
                f"    type: bar\n    x: m\n    y: v\n    color: s\n    style:\n      stack: zero\n      orientation: {orientation}",
                rows,
            )
        )
        segments = re.findall(
            r'<(?:path|rect)[^>]*stroke="#FFFFFF"[^>]*data-dct-role="bar"[^>]*>', svg
        )
        assert len(segments) == 6, svg
        assert len(re.findall(r'fill="none" stroke="#FFFFFF"', svg)) == 0
        assert sum("A 2.00,2.00" in segment for segment in segments) == 2


def test_a_one_color_palette_gives_every_endpoint_label_the_same_ink() -> None:
    rows = "\n".join(
        f"      - {{m: '2025-0{m}', s: {s}, v: {m + i}}}"
        for i, s in enumerate(("a", "b", "c"))
        for m in (1, 2, 3)
    )
    svg = render_board_to_svg(
        _board(
            "    type: line\n    x: m\n    y: v\n    color: s",
            rows,
            style='style:\n  charts:\n    color:\n      categorical:\n        palette: ["#1f77b4"]',
        )
    )
    inks = re.findall(r'fill="(#[0-9a-fA-F]{6})"[^>]*aria-label="s: [abc]"', svg)
    assert len(inks) == 3 and len(set(inks)) == 1 and inks[0].lower() != "#1f77b4", inks
