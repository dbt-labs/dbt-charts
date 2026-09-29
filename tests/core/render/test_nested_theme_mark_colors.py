"""Regression test: nested-theme charts must bake their OWN theme's mark colors.

Bug: the HeightProvider resolved_style threading fix (see
test_height_provider_resolved_style_threading.py) corrected which
resolved_style reaches the render/config layer (background, fonts, axis
chrome) for a chart nested inside a differently-themed board. But
``_require_resolved`` (layout_sizing.py) still resolved every chart's
``ResolvedChart`` — where mark/series colors are baked via
``resolve(..., board_style=...)`` — under ``render_ctx.resolved_style``,
the ROOT board's style, regardless of which nested board's style the chart
actually belongs to. Confirmed live in the committed ``quick-guide_7.svg``
golden: a nested ``theme: stark`` chart renders correct stark chrome
(white background, Inter fonts) but its bars stay ``editorial-10``
(cream's category palette) with ``stroke="#FAF7F0"`` (cream's canvas
token) instead of stark's ``vivid-10``.

This test renders a real board end-to-end through the actual compile +
execute + render pipeline (no mocked renderer, no pre-seeded
``pre_resolved`` cache) — mirroring quick-guide.yml's "4. Charts" nested
``theme: stark`` construct — and asserts the nested chart's bar-mark fill
and stroke colors come from stark's own palette/canvas tokens, not the
root cream board's.
"""

from __future__ import annotations

import re
from collections.abc import Callable
from pathlib import Path

from dbt_charts.core.compile import compile
from dbt_charts.core.execute import Executor
from dbt_charts.core.execute.adapters import build_adapter_registry
from dbt_charts.core.project import Project
from dbt_charts.core.render import render

_NESTED_STARK_YAML = """
title: "Root board"
theme: paper

queries:
  q:
    type: values
    rows:
      - {seg: A, x: a, y: 1}
      - {seg: B, x: a, y: 2}
      - {seg: A, x: b, y: 3}
      - {seg: B, x: b, y: 4}

charts:
  revenue:
    type: bar
    title: "Revenue"
    query: q
    x: x
    y: y
    color: seg

rows:
  - theme: stark
    style:
      background: "#ffffff"
    title: "Nested stark board"
    cols:
      - revenue
"""

# Stark's category role binds vivid-10 (defaults/themes/_base.yaml).
_VIVID_10 = {
    "#0073c2",
    "#00c8ee",
    "#00ad75",
    "#7a5531",
    "#e1a500",
    "#a86c9d",
    "#da5a23",
    "#6a8f47",
    "#949daa",
    "#505b6b",
}
# Cream inherits editorial's rebind to editorial-10 — the leak this test
# guards against.
_EDITORIAL_10 = {
    "#40639c",
    "#779bc9",
    "#608470",
    "#775770",
    "#d49656",
    "#ae6349",
    "#a0b6b7",
    "#ad9c7f",
    "#7a8895",
    "#5c6668",
}
_CREAM_CANVAS = "#FAF7F0"
_STARK_CANVAS = "#ffffff"


def _bar_marks(svg: str) -> list[tuple[str, str]]:
    """(fill, stroke) hex pairs for every rendered bar mark path."""
    return re.findall(
        r'aria-roledescription="bar" d="[^"]*" '
        r'fill="(#[0-9A-Fa-f]{6})" stroke="(#[0-9A-Fa-f]{6})"',
        svg,
    )


def test_nested_theme_chart_bakes_own_palette_not_root(
    local_project: Callable[..., Project],
) -> None:
    """A chart in a `theme: stark` nested board must not bake cream's colors."""
    result = compile(_NESTED_STARK_YAML)
    assert result.success, result.errors
    board = result.board
    executor = Executor(
        board,
        adapter_registry=build_adapter_registry(local_project(Path.cwd())),
        query_registry=result.query_registry,
    )
    svg = render(board, executor, format="svg").output

    marks = _bar_marks(svg)
    assert marks, "Expected at least one bar mark in the rendered SVG"
    fills = {fill for fill, _stroke in marks}
    strokes = {stroke for _fill, stroke in marks}

    assert fills <= _VIVID_10, (
        f"Nested chart under theme: stark must use stark's vivid-10 palette, "
        f"got fills={fills}. Pre-fix: _require_resolved always resolves under "
        "render_ctx.resolved_style (the root cream style), so nested charts "
        "bake editorial-10 (cream's category palette) instead."
    )
    assert not (fills & _EDITORIAL_10), (
        f"Nested stark chart leaked cream/editorial-10 mark colors: "
        f"{fills & _EDITORIAL_10}"
    )
    assert {s.lower() for s in strokes} == {_STARK_CANVAS.lower()}, (
        f"Nested stark chart's bar stroke must be stark's canvas token "
        f"({_STARK_CANVAS!r}), got {strokes}. Pre-fix: marks stroke against "
        f"the root cream canvas ({_CREAM_CANVAS!r})."
    )
