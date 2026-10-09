"""Integration test: vl-convert renders the theme-cascaded weight, not Regular.

Root cause: vl-convert (resvg) cannot bind a variable font's ``wght`` axis from a
numeric ``font-weight`` request. Weight 500/600 rendered byte-identical to weight
400 through the real ``to_png()`` pipeline until ``fonts.WEIGHT_FACE_ALIASES`` +
``font_support.normalize_svg_font_weights_for_vl_convert`` routed those weights to
dedicated static faces selected by family name instead. See fonts/README.md
"Select figure style by family" (weight section).
"""

from __future__ import annotations

import io

import pytest

FONT_STACKS = {
    "Source Serif 4": "'Source Serif 4', Georgia, 'Times New Roman', serif",
    "dbt Sans Tabular": "'dbt Sans Tabular', Inter, system-ui, sans-serif",
    "Inter Variable": "'Inter Variable', Inter, system-ui, sans-serif",
}


def _ink_pixel_count(family: str, weight: int, font_size: float = 80.0) -> int:
    Image = pytest.importorskip("PIL.Image")
    pytest.importorskip("vl_convert")

    from dbt_charts.core.render.converters.png import to_png

    width = int(font_size * 3)
    height = int(font_size * 2)
    svg = (
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{height}">'
        f'<rect width="{width}" height="{height}" fill="white"/>'
        f'<text x="5" y="{int(font_size * 1.2)}" '
        f'font-family="{FONT_STACKS[family]}" '
        f'font-weight="{weight}" '
        f'font-size="{font_size}" '
        f'fill="black">40</text>'
        f"</svg>"
    )
    png_bytes = to_png(svg, scale=2.0)
    image = Image.open(io.BytesIO(png_bytes)).convert("L")
    return sum(1 for pixel in image.getdata() if pixel < 200)


@pytest.mark.parametrize(
    "family", ["Source Serif 4", "dbt Sans Tabular", "Inter Variable"]
)
def test_weight_500_and_600_render_strictly_heavier_than_regular(
    family: str,
) -> None:
    regular = _ink_pixel_count(family, 400)
    medium = _ink_pixel_count(family, 500)
    semibold = _ink_pixel_count(family, 600)

    assert regular < medium < semibold, (
        f"{family} ink density did not strictly increase with weight "
        f"(400={regular}, 500={medium}, 600={semibold}) — vl-convert is not "
        f"binding the weight-specific static face."
    )


@pytest.mark.parametrize(
    "family", ["Source Serif 4", "dbt Sans Tabular", "Inter Variable"]
)
def test_weight_500_is_a_distinct_render_from_regular(family: str) -> None:
    """Byte-level, not just pixel-count — a coincidental count match would pass
    the density check above while still rendering the wrong glyphs."""
    pytest.importorskip("vl_convert")

    from dbt_charts.core.render.converters.png import to_png

    def render(weight: int) -> bytes:
        font_size = 80.0
        width = int(font_size * 3)
        height = int(font_size * 2)
        svg = (
            f'<svg xmlns="http://www.w3.org/2000/svg" width="{width}" '
            f'height="{height}">'
            f'<rect width="{width}" height="{height}" fill="white"/>'
            f'<text x="5" y="{int(font_size * 1.2)}" '
            f'font-family="{FONT_STACKS[family]}" '
            f'font-weight="{weight}" '
            f'font-size="{font_size}" '
            f'fill="black">40</text>'
            f"</svg>"
        )
        return to_png(svg, scale=2.0)

    assert render(400) != render(500)


@pytest.mark.parametrize(
    "family", ["Source Serif 4", "dbt Sans Tabular", "Inter Variable"]
)
def test_inline_style_weight_on_a_tspan_renders_heavier_than_regular(
    family: str,
) -> None:
    """mdsvg paints bold as ``<tspan style="font-weight: 600">`` inside a text
    whose family comes from a ``<style>`` class rule, not from the tag."""
    Image = pytest.importorskip("PIL.Image")
    pytest.importorskip("vl_convert")

    from dbt_charts.core.render.converters.png import to_png

    stack = FONT_STACKS[family].replace("'", "&#x27;")

    def ink(weight: int) -> int:
        svg = (
            '<svg xmlns="http://www.w3.org/2000/svg" width="240" height="160">'
            f"<style>.md-text {{ font-family: {stack}; }}</style>"
            '<rect width="240" height="160" fill="white"/>'
            '<text class="md-text" x="5" y="96" font-size="80" fill="black">'
            f'<tspan style="font-weight: {weight}">40</tspan></text></svg>'
        )
        image = Image.open(io.BytesIO(to_png(svg, scale=2.0))).convert("L")
        return sum(1 for pixel in image.getdata() if pixel < 200)

    regular, semibold = ink(400), ink(600)
    assert regular < semibold, (
        f"{family} inline-style weight 600 painted no heavier than 400 "
        f"(400={regular}, 600={semibold})"
    )
