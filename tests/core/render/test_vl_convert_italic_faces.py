"""Integration test: vl-convert paints ``font-style="italic"`` with the italic face.

resvg never synthesizes an oblique, so an italic request renders upright unless the
family the PNG-bound SVG names carries a real italic face at the requested weight —
including the static weight-alias families of ``fonts.WEIGHT_FACE_ALIASES``.
"""

from __future__ import annotations

import pytest

FONT_STACKS = {
    "Inter": "Inter, system-ui, sans-serif",
    "Source Serif 4": "'Source Serif 4', Georgia, serif",
}


def _render(family: str, weight: int, style: str) -> bytes:
    pytest.importorskip("vl_convert")

    from dbt_charts.core.render.converters.png import to_png

    svg = (
        '<svg xmlns="http://www.w3.org/2000/svg" width="320" height="80">'
        '<rect width="320" height="80" fill="white"/>'
        f'<text x="5" y="56" font-family="{FONT_STACKS[family]}" '
        f'font-weight="{weight}" font-style="{style}" font-size="48" '
        'fill="black">Revenue</text></svg>'
    )
    return to_png(svg)


@pytest.mark.parametrize("weight", [400, 500, 600])
@pytest.mark.parametrize("family", ["Inter", "Source Serif 4"])
def test_italic_renders_distinct_from_upright(family: str, weight: int) -> None:
    assert _render(family, weight, "italic") != _render(family, weight, "normal")


@pytest.mark.parametrize("family", ["Inter", "Source Serif 4"])
def test_italic_keeps_its_weight(family: str) -> None:
    assert _render(family, 400, "italic") != _render(family, 600, "italic")
