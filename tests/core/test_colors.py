"""Tests for the neutral CSS/SVG color-validation primitives.

sanitize_color and is_sanitizable_color validate foreign SVG/CSS strings, not
compile policy, so they live in dbt_charts.core.colors (a neutral leaf) rather
than under compile/.

color_at/ink_at (board-slot → paint resolution) live one layer up, in
dbt_charts.core.compile.models.style.theme.category_colors — see
dbt-charts/tests/core/compile/models/style/theme/test_category_colors.py.
"""

from __future__ import annotations

import pytest

from dbt_charts.core.colors import (
    InvalidColorError,
    ensure_readable_ink,
    is_light_canvas,
    is_sanitizable_color,
    sanitize_color,
    wcag_contrast,
)


def test_sanitize_color_invalid_raises():
    with pytest.raises(InvalidColorError, match="Invalid color value: 'not-a-color'"):
        sanitize_color("not-a-color")


def test_sanitize_color_none_returns_none():
    assert sanitize_color(None) is None


def test_sanitize_color_valid_hex_passes():
    assert sanitize_color("#fff") == "#fff"
    assert sanitize_color("#FFFFFF") == "#FFFFFF"


def test_sanitize_color_transparent_passes():
    assert sanitize_color("transparent") == "transparent"
    assert sanitize_color("none") == "transparent"


def test_sanitize_color_with_explicit_fallback_for_none():
    """When caller supplies an explicit fallback, None input returns it."""
    assert sanitize_color(None, "#ff0000") == "#ff0000"


def test_is_sanitizable_color_accepts_hex_and_keywords():
    assert is_sanitizable_color("#fff") is True
    assert is_sanitizable_color("#FFFFFF") is True
    assert is_sanitizable_color("transparent") is True
    assert is_sanitizable_color("none") is True


def test_is_sanitizable_color_rejects_invalid():
    assert is_sanitizable_color("not-a-color") is False


def test_ensure_readable_ink_returns_color_unchanged_when_already_readable():
    assert ensure_readable_ink("#000000", "#ffffff") == "#000000"


def test_ensure_readable_ink_darkens_a_light_fill_on_a_light_background():
    ink = ensure_readable_ink("#d7d7d7", "#ffffff")
    assert ink != "#d7d7d7"
    assert wcag_contrast(ink, "#ffffff") >= 4.5


def test_ensure_readable_ink_lightens_a_dark_fill_on_a_dark_background():
    ink = ensure_readable_ink("#222222", "#000000")
    assert ink != "#222222"
    assert wcag_contrast(ink, "#000000") >= 4.5


def test_ensure_readable_ink_picks_the_higher_contrast_endpoint_for_a_midtone_background():
    """HIGH regression: the black/white equal-contrast crossover is
    luminance ≈0.18, not the 0.5 midpoint -- a background between those two
    thresholds must still search toward the endpoint that actually wins.
    ``#808080`` (luminance ≈0.216) sits in exactly that band: black gives
    5.32:1, white only 3.95:1, so a 0.5-pivoted search picks the WRONG
    (lower-contrast, still-failing) direction.
    """
    for bg in ("#808080", "#949494", "#b0b0b0"):
        ink = ensure_readable_ink("#ffffff", bg)
        assert wcag_contrast(ink, bg) >= 4.5, (bg, ink)
        # The winning direction is toward black -- confirms the search moved
        # the RIGHT way, not just that it eventually stumbled onto a legal L.
        assert wcag_contrast("#000000", bg) > wcag_contrast("#ffffff", bg)
        from dbt_charts.core.colors import hex_to_oklch

        assert hex_to_oklch(ink)[0] < 0.5, (bg, ink)


def test_is_light_canvas_crossover_below_0_18_luminance_is_dark():
    """#727272 sits just below the ~0.18-luminance crossover: white still
    wins the contrast race, so the canvas reads as dark (not light)."""
    assert is_light_canvas("#727272") is False
    assert wcag_contrast("#ffffff", "#727272") > wcag_contrast("#000000", "#727272")


def test_is_light_canvas_crossover_above_0_18_luminance_is_light():
    """#7a7a7a sits just above the crossover: black now wins, so the canvas
    reads as light -- one shade lighter than #727272 flips the verdict."""
    assert is_light_canvas("#7a7a7a") is True
    assert wcag_contrast("#000000", "#7a7a7a") > wcag_contrast("#ffffff", "#7a7a7a")


def test_is_light_canvas_white_and_near_black():
    assert is_light_canvas("#ffffff") is True
    assert is_light_canvas("#161616") is False


def test_is_light_canvas_agrees_with_ensure_readable_ink_direction():
    """ensure_readable_ink must move toward the SAME endpoint is_light_canvas
    picks -- a regression here means extracting the shared predicate
    silently changed which direction ensure_readable_ink searches."""
    for canvas in ("#727272", "#7a7a7a", "#ffffff", "#161616", "#faf7f0"):
        ink = ensure_readable_ink("#808080", canvas, min_ratio=4.6)
        from dbt_charts.core.colors import hex_to_oklch

        moved_dark = hex_to_oklch(ink)[0] < hex_to_oklch("#808080")[0]
        assert moved_dark == is_light_canvas(canvas), canvas


def test_ensure_readable_ink_raises_when_no_ink_clears_the_floor():
    """HIGH regression: a background where NEITHER black nor white can
    clear an unreasonably high ratio must raise, not silently return ink
    that still fails -- ``_table_surface_seq``'s own precedent
    (``compile/resolve/style/palette.py``) for a WCAG floor that can't be met.
    """
    with pytest.raises(ValueError, match="no ink clears"):
        ensure_readable_ink("#808080", "#808080", min_ratio=21.0)


@pytest.mark.parametrize("lightness", [1.05, -0.01])
def test_oklch_to_hex_refuses_a_lightness_outside_the_unit_range(
    lightness: float,
) -> None:
    from dbt_charts.core.colors import oklch_to_hex

    with pytest.raises(ValueError, match="outside"):
        oklch_to_hex(lightness, 0.1, 90)
