"""PNG export snaps the scale of an SVG that carries a pattern fill to a quarter.

Pattern tiles are multiples of 4 px, so only at a scale that is a multiple of 0.25 does every
tile edge land on a whole device pixel.
"""

from __future__ import annotations

import io

import pytest

PLAIN = (
    '<svg xmlns="http://www.w3.org/2000/svg" width="100" height="40" viewBox="0 0 100 40">'
    '<rect width="100" height="40" fill="#fff"/></svg>'
)
PATTERNED = PLAIN.replace(
    "<rect",
    '<defs><pattern id="p" patternUnits="userSpaceOnUse" width="8" height="8">'
    '<rect width="8" height="8" fill="#fff"/></pattern></defs><rect',
    1,
)


def _size(svg: str, scale: float) -> tuple[int, int]:
    pytest.importorskip("vl_convert")
    image_module = pytest.importorskip("PIL.Image")
    from dbt_charts.core.render.converters.png import to_png

    return image_module.open(io.BytesIO(to_png(svg, scale=scale))).size


def test_a_patterned_svg_rasterizes_at_the_nearest_quarter_scale() -> None:
    assert _size(PATTERNED, 1.3) == (125, 50)
    assert _size(PATTERNED, 2.0) == (200, 80)


def test_a_scale_never_snaps_to_nothing() -> None:
    assert _size(PATTERNED, 0.1) == (25, 10)


def test_an_svg_without_a_pattern_keeps_the_scale_it_was_given() -> None:
    assert _size(PLAIN, 1.3) == (130, 52)
