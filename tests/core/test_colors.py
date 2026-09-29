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


class TestCssNamedColorToHex:
    """css_named_color_to_hex covers the full CSS Color Module / SVG 1.1
    keyword set (148 names) -- a superset of _CSS_NAMED_COLORS
    (compile/models/primitives.py, a narrower table scoped to border-
    shorthand extraction). 'transparent' means 'no fill', not a color, and
    has no hex, so it's excluded even though _CSS_NAMED_COLORS carries it."""

    def test_round_trips_every_name_in_the_table(self):
        from dbt_charts.core.colors import css_named_color_to_hex
        from dbt_charts.core.compile.models.primitives import _CSS_NAMED_COLORS

        for name in _CSS_NAMED_COLORS - {"transparent"}:
            hex_value = css_named_color_to_hex(name)
            assert hex_value is not None, name
            assert is_sanitizable_color(hex_value), (name, hex_value)

    def test_covers_names_outside_the_narrower_border_shorthand_table(self):
        """mistyrose/aliceblue motivated widening past _CSS_NAMED_COLORS --
        test_layout_rendering.py's nested-board fixtures author them as
        style.background, and neither is in that narrower table."""
        from dbt_charts.core.colors import css_named_color_to_hex

        assert css_named_color_to_hex("mistyrose") == "#ffe4e1"
        assert css_named_color_to_hex("aliceblue") == "#f0f8ff"

    def test_is_case_insensitive(self):
        from dbt_charts.core.colors import css_named_color_to_hex

        assert css_named_color_to_hex("White") == css_named_color_to_hex("white")
        assert css_named_color_to_hex("WHITE") == css_named_color_to_hex("white")

    def test_transparent_is_not_a_color(self):
        from dbt_charts.core.colors import css_named_color_to_hex

        assert css_named_color_to_hex("transparent") is None

    def test_unknown_name_returns_none(self):
        from dbt_charts.core.colors import css_named_color_to_hex

        assert css_named_color_to_hex("not-a-color") is None

    @pytest.mark.parametrize(
        ("name", "expected"),
        [
            ("black", "#000000"),
            ("white", "#ffffff"),
            ("red", "#ff0000"),
            ("magenta", "#ff00ff"),
            ("fuchsia", "#ff00ff"),
            ("lime", "#00ff00"),
            ("maroon", "#800000"),
            ("navy", "#000080"),
            ("olive", "#808000"),
            ("teal", "#008080"),
            ("aqua", "#00ffff"),
            ("silver", "#c0c0c0"),
        ],
    )
    def test_known_hex_values(self, name: str, expected: str):
        from dbt_charts.core.colors import css_named_color_to_hex

        assert css_named_color_to_hex(name) == expected


class TestParseCssColor:
    """parse_css_color() -> (r, g, b, a), each 0-1 -- the general CSS color
    grammar ink_canvas() needs to composite a translucent background
    (rgba/hsla-shaped nested-section fills) over what's under it."""

    def test_hex_6_digit_has_full_alpha(self):
        from dbt_charts.core.colors import parse_css_color

        assert parse_css_color("#0073c2") == pytest.approx(
            (0x00 / 255, 0x73 / 255, 0xC2 / 255, 1.0)
        )

    def test_hex_3_digit_expands(self):
        from dbt_charts.core.colors import parse_css_color

        assert parse_css_color("#fff") == pytest.approx((1.0, 1.0, 1.0, 1.0))

    def test_hex_8_digit_carries_alpha(self):
        from dbt_charts.core.colors import parse_css_color

        r, g, b, a = parse_css_color("#80ff0080")
        assert (r, g, b) == pytest.approx((0x80 / 255, 0xFF / 255, 0x00 / 255))
        assert a == pytest.approx(0x80 / 255)

    def test_transparent_is_zero_alpha(self):
        from dbt_charts.core.colors import parse_css_color

        assert parse_css_color("transparent") == (0.0, 0.0, 0.0, 0.0)
        assert parse_css_color("none") == (0.0, 0.0, 0.0, 0.0)

    def test_css_keyword_name_has_full_alpha(self):
        from dbt_charts.core.colors import parse_css_color

        assert parse_css_color("white") == (1.0, 1.0, 1.0, 1.0)

    def test_rgba_function_comma_form(self):
        from dbt_charts.core.colors import parse_css_color

        r, g, b, a = parse_css_color("rgba(102, 126, 234, 0.1)")
        assert (r, g, b) == pytest.approx((102 / 255, 126 / 255, 234 / 255), abs=1e-6)
        assert a == pytest.approx(0.1)

    def test_rgb_function_percentage_channels(self):
        from dbt_charts.core.colors import parse_css_color

        r, g, b, a = parse_css_color("rgb(10%, 20%, 30%)")
        assert (r, g, b, a) == pytest.approx((0.10, 0.20, 0.30, 1.0))

    def test_rgb_function_space_separated_with_alpha(self):
        from dbt_charts.core.colors import parse_css_color

        r, g, b, a = parse_css_color("rgb(10 20 30 / 50%)")
        assert (r, g, b) == pytest.approx((10 / 255, 20 / 255, 30 / 255), abs=1e-6)
        assert a == pytest.approx(0.5)

    def test_hsl_function_percentage_saturation_and_lightness(self):
        from dbt_charts.core.colors import parse_css_color

        r, g, b, a = parse_css_color("hsl(220, 60%, 50%)")
        assert (r, g, b) == pytest.approx((0.2, 0.4, 0.8), abs=0.01)
        assert a == pytest.approx(1.0)

    def test_hsl_function_bare_number_saturation_is_rejected(self):
        """Browsers read a bare number here as a percentage sign the author
        forgot, not a 0-1 fraction -- "0.6" means 0.6%, not 60%. A bare
        number used to fall through to the alpha parser's 0-1-or-% parsing
        and get silently misread as a fraction."""
        from dbt_charts.core.colors import parse_css_color

        with pytest.raises(InvalidColorError):
            parse_css_color("hsl(220, 0.6, 50%)")

    def test_hsl_function_bare_number_lightness_is_rejected(self):
        from dbt_charts.core.colors import parse_css_color

        with pytest.raises(InvalidColorError):
            parse_css_color("hsl(220, 60%, 0.5)")

    def test_garbage_raises(self):
        from dbt_charts.core.colors import parse_css_color

        with pytest.raises(InvalidColorError):
            parse_css_color("not-a-color")

    def test_rgb_function_rejects_nan_channel(self):
        """`float()` parses "nan" without raising -- min(1.0, nan) then
        silently clamps it to 1.0 (rgb(nan,0,0) would otherwise become
        pure red, not an error)."""
        from dbt_charts.core.colors import parse_css_color

        with pytest.raises(InvalidColorError):
            parse_css_color("rgb(nan,0,0)")

    def test_rgb_function_rejects_infinity_channel(self):
        from dbt_charts.core.colors import parse_css_color

        with pytest.raises(InvalidColorError):
            parse_css_color("rgb(inf,0,0)")
        with pytest.raises(InvalidColorError):
            parse_css_color("rgb(-infinity,0,0)")

    def test_rgb_function_rejects_nan_alpha(self):
        from dbt_charts.core.colors import parse_css_color

        with pytest.raises(InvalidColorError):
            parse_css_color("rgba(10,20,30,nan)")

    def test_hsl_function_rejects_nan_hue(self):
        """A NaN hue used to survive parse_css_color entirely (no exception
        here), landing as a NaN embedded in the returned tuple -- crashing
        later, far from the authored value, wherever it hit int(x * 255)."""
        from dbt_charts.core.colors import parse_css_color

        with pytest.raises(InvalidColorError):
            parse_css_color("hsl(nan, 60%, 50%)")

    def test_hsl_function_deg_suffix_is_case_insensitive(self):
        """CSS units are case-insensitive; "220DEG" is as legal as "220deg"."""
        from dbt_charts.core.colors import parse_css_color

        assert parse_css_color("hsl(220DEG, 60%, 50%)") == parse_css_color(
            "hsl(220deg, 60%, 50%)"
        )

    def test_rgb_function_rejects_a_double_comma(self):
        r"""A collapsed [\s,]+ split used to read "10,,20,30" as three
        channels (the empty one between the two commas silently vanishing)
        instead of the malformed input it is."""
        from dbt_charts.core.colors import parse_css_color

        with pytest.raises(InvalidColorError):
            parse_css_color("rgb(10,,20,30)")

    def test_rgb_function_rejects_comma_syntax_mixed_with_slash_alpha(self):
        """Comma-separated channels with a slash alpha is not legal CSS in
        either syntax -- comma syntax's alpha is a 4th comma-separated
        argument, space syntax's is after a slash; the two do not mix."""
        from dbt_charts.core.colors import parse_css_color

        with pytest.raises(InvalidColorError):
            parse_css_color("rgb(10, 20, 30 / 0.5)")


class TestCompositeOver:
    def test_ten_percent_tint_over_white_matches_the_nested_layouts_hex(self):
        """Hand-computed: this is the exact rgba(...) card-tint shape
        examples/playground/charts/cards/nested-layouts.yml authors --
        pins the real value, not just "still close to white"."""
        from dbt_charts.core.colors import composite_over, parse_css_color, rgb01_to_hex

        tint = parse_css_color("rgba(102, 126, 234, 0.1)")
        white = parse_css_color("#ffffff")
        r, g, b, a = composite_over(tint, white)
        assert a == pytest.approx(1.0)
        assert rgb01_to_hex(r, g, b) == "#f0f2fd"

    def test_opaque_top_wins_outright(self):
        from dbt_charts.core.colors import composite_over, parse_css_color

        top = parse_css_color("#112233")
        under = parse_css_color("#ffffff")
        assert composite_over(top, under) == pytest.approx((*top[:3], 1.0))

    def test_transparent_top_passes_under_through(self):
        from dbt_charts.core.colors import composite_over, parse_css_color

        top = parse_css_color("transparent")
        under = parse_css_color("#112233")
        assert composite_over(top, under) == pytest.approx(under)

    def test_two_translucent_layers_stay_translucent(self):
        from dbt_charts.core.colors import composite_over, parse_css_color

        top = parse_css_color("rgba(0, 0, 0, 0.5)")
        under = parse_css_color("rgba(255, 255, 255, 0.5)")
        _, _, _, a = composite_over(top, under)
        assert a == pytest.approx(0.75)


class TestRgb01ToHex:
    def test_round_trips_through_parse_css_color(self):
        from dbt_charts.core.colors import parse_css_color, rgb01_to_hex

        for hex_value in ("#0073c2", "#ffffff", "#000000", "#e1a500"):
            r, g, b, _a = parse_css_color(hex_value)
            assert rgb01_to_hex(r, g, b) == hex_value


class TestHclInterpolation:
    """CIE Lab-derived LCh(ab) -- distinct from this module's OKLCH space --
    matching Vega-Lite's own `interpolate: "hcl"` continuous color-scale
    interpolator (and its default: confirmed empirically, see the module
    docstring on `interpolate_hcl`, by compiling `{"domain": [0, 1], "range":
    ["#2166ac", "#b2182b"]}` through `vl_convert.vegalite_to_svg` with no
    `interpolate` set, with `interpolate: "rgb"`, and with `interpolate:
    "hcl"` -- the unset case matches "hcl" exactly and differs from "rgb" at
    every interior sample). These expected values are that real Vega-rendered
    output, not a hand-derived guess.
    """

    def test_interpolate_hcl_endpoints_are_exact(self):
        from dbt_charts.core.colors import interpolate_hcl

        assert interpolate_hcl("#2166ac", "#b2182b", 0.0) == "#2166ac"
        assert interpolate_hcl("#2166ac", "#b2182b", 1.0) == "#b2182b"

    def test_interpolate_hcl_matches_vega_lite_rendered_output(self):
        """Every sampled t against the real Vega-rendered fills for this
        exact diverging pair (see class docstring) -- not just the midpoint,
        so a hue-direction or lightness-curve bug at an interior point can't
        hide behind a single lucky sample."""
        from dbt_charts.core.colors import (
            interpolate_hcl,
            parse_css_color,
            rgb01_to_hex,
        )

        expected_rgb = {
            0.0: (33, 102, 172),
            0.1: (66, 96, 174),
            0.2: (91, 89, 173),
            0.3: (114, 80, 167),
            0.4: (134, 70, 158),
            0.5: (151, 57, 144),
            0.6: (164, 43, 128),
            0.7: (174, 28, 109),
            0.8: (180, 15, 88),
            0.9: (181, 12, 66),
            1.0: (178, 24, 43),
        }
        for t, (er, eg, eb) in expected_rgb.items():
            result = interpolate_hcl("#2166ac", "#b2182b", t)
            r, g, b, _a = parse_css_color(result)
            got = rgb01_to_hex(r, g, b)
            assert got == rgb01_to_hex(er / 255, eg / 255, eb / 255), (t, result)

    def test_interpolate_hcl_takes_the_short_way_around_the_hue_circle(self):
        """A hue pair straddling the 0/360 seam (a warm red near hue 0/360 to
        a warm orange just past it) must move through the seam, not the long
        way around through the cool hues -- the same 'short way around'
        contract d3-interpolate's hue lerp guarantees."""
        from dbt_charts.core.colors import _hue_lerp

        # 350 -> 10 is a 20-degree step through 0/360, never the 340-degree
        # step the other way.
        assert _hue_lerp(350.0, 10.0, 0.5) % 360 == pytest.approx(0.0)
        assert _hue_lerp(10.0, 350.0, 0.5) % 360 == pytest.approx(0.0)

    def test_interpolate_hcl_blends_alpha_linearly(self):
        """Alpha has no analog in Lab/LCh space, so it is interpolated
        linearly and independently of the perceptual L/C/H path -- matching
        d3-interpolate's own separate `opacity` lerp alongside `hcl`."""
        from dbt_charts.core.colors import interpolate_hcl, parse_css_color

        result = interpolate_hcl("#2166ac", "rgba(178, 24, 43, 0.0)", 0.5)
        _, _, _, a = parse_css_color(result)
        assert a == pytest.approx(0.5)

    def test_interpolate_hcl_opaque_result_is_plain_hex(self):
        from dbt_charts.core.colors import interpolate_hcl

        result = interpolate_hcl("#2166ac", "#b2182b", 0.5)
        assert result.startswith("#") and len(result) == 7

    # ------------------------------------------------------------------
    # Achromatic (gray/white/black) and `transparent` endpoints.
    #
    # d3-color (which Vega's `interpolate: "hcl"` is built on) gives an
    # achromatic color an UNDEFINED hue (and, at pure black/white, an
    # undefined chroma too), and `transparent` an entirely undefined color.
    # d3-interpolate's own hue/chroma/lightness lerp then BORROWS whichever
    # endpoint's value IS defined and holds it CONSTANT for the whole ramp,
    # rather than forcing hue 0 -- forcing hue 0 swings the ramp through
    # red/magenta instead. Each expected value below is the real
    # Vega-rendered fill (vl_convert), confirmed at multiple t values per
    # pair, not a hand-derived guess.
    # ------------------------------------------------------------------

    def test_interpolate_hcl_almost_gray_stops_have_no_spurious_hue(self):
        """A gray whose Lab a/b lands on exact 0.0 only by floating-point
        luck (most gray levels don't) must still read as fully achromatic --
        d3-color's own shortcut (see `_rgb01_to_lab`) sets a=b=0 for any
        r==g==b input BY CONSTRUCTION, not by hoping matrix math rounds to
        exactly zero. `#e4e4e4` (this repo's own diverging-palette neutral
        stop), `#cccccc`, and `#808080` all leave nonzero (~1e-14) noise
        without that shortcut."""
        from dbt_charts.core.colors import _rgb01_to_lab

        for hex_value in (
            "#e4e4e4",
            "#cccccc",
            "#d9d9d9",
            "#dddddd",
            "#e5e5e5",
            "#808080",
        ):
            r = int(hex_value[1:3], 16) / 255
            g = int(hex_value[3:5], 16) / 255
            b = int(hex_value[5:7], 16) / 255
            _lightness, a, b_component = _rgb01_to_lab(r, g, b)
            assert a == 0.0
            assert b_component == 0.0

    def test_interpolate_hcl_almost_gray_pairs_match_vega(self):
        """Direct two-color pairs for the exact almost-gray levels that
        slipped through both prior rounds -- `#e4e4e4` (paired with the
        other real neutral of this repo's own diverging fixture, `#b2182b`),
        `#cccccc`, and `#808080` -- each checked at three interior t values
        against the real Vega-rendered fill."""
        from dbt_charts.core.colors import (
            interpolate_hcl,
            parse_css_color,
            rgb01_to_hex,
        )

        cases = {
            ("#e4e4e4", "#b2182b"): {
                0.25: (223, 182, 177),
                0.5: (213, 136, 129),
                0.75: (198, 88, 84),
            },
            ("#2166ac", "#cccccc"): {
                0.25: (90, 126, 181),
                0.5: (130, 151, 189),
                0.75: (168, 177, 197),
            },
            ("#2166ac", "#808080"): {
                0.25: (71, 108, 161),
                0.5: (94, 115, 150),
                0.75: (112, 121, 139),
            },
        }
        for (c1, c2), by_t in cases.items():
            for t, (er, eg, eb) in by_t.items():
                result = interpolate_hcl(c1, c2, t)
                r, g, b, _a = parse_css_color(result)
                assert rgb01_to_hex(r, g, b) == rgb01_to_hex(
                    er / 255, eg / 255, eb / 255
                ), (c1, c2, t, result)

    def test_interpolate_hcl_gray_neutral_stop_matches_vega_diverging_repro(self):
        """The exact failure this repro class produced: a synthetic
        boundary stop straddling a truncated arm, with THIS repo's own
        diverging-palette neutral (`#e4e4e4`) as the center stop. Before the
        `_rgb01_to_lab` fix, the ~1e-14 floating-point noise on `#e4e4e4`
        gave it a spurious hue, tinting the synthetic stop toward the
        positive-arm color (red) instead of holding the negative arm's own
        (blue) hue -- the exact expected value below is Vega's own
        rendering of the equivalent untruncated ramp."""
        from dbt_charts.core.colors import (
            interpolate_hcl,
            parse_css_color,
            rgb01_to_hex,
        )

        # Reproduces gradient_scale_to_vl's own asymmetric truncation math
        # for palette=[#2166ac, #e4e4e4, #b2182b], bounds=[-1, 10], hinge=0:
        # the shared denominator is max(0-(-1), 10-0) = 10, so the
        # theoretical (untruncated) negative breakpoint sits at -10, and the
        # real bound (-1) crosses that segment at
        # t = (-1 - -10) / (0 - -10) = 9/10.
        result = interpolate_hcl("#2166ac", "#e4e4e4", 9 / 10)
        r, g, b, _a = parse_css_color(result)
        assert rgb01_to_hex(r, g, b) == "#d3d7df"

    def test_interpolate_hcl_gray_center_holds_blue_hue(self):
        """A genuine gray endpoint (#f7f7f7, not pure black/white) has a
        defined chroma of 0 but an undefined hue -- Vega holds the OTHER
        endpoint's hue constant while chroma and lightness interpolate
        normally toward the gray's own (0) chroma."""
        from dbt_charts.core.colors import (
            interpolate_hcl,
            parse_css_color,
            rgb01_to_hex,
        )

        expected_rgb = {
            0.0: (33, 102, 172),
            0.25: (100, 136, 191),
            0.5: (151, 171, 210),
            0.75: (199, 208, 228),
            1.0: (247, 247, 247),
        }
        for t, (er, eg, eb) in expected_rgb.items():
            result = interpolate_hcl("#2166ac", "#f7f7f7", t)
            r, g, b, _a = parse_css_color(result)
            assert rgb01_to_hex(r, g, b) == rgb01_to_hex(
                er / 255, eg / 255, eb / 255
            ), (
                t,
                result,
            )

    def test_interpolate_hcl_white_center_holds_blue_hue_and_chroma(self):
        """Pure white has BOTH hue and chroma undefined -- Vega holds the
        other endpoint's hue AND chroma constant (only lightness moves),
        letting sRGB gamut clipping do the desaturating near white rather
        than the interpolator itself. Forcing hue 0 here would produce a
        pink tint instead of this blue-hued ramp."""
        from dbt_charts.core.colors import (
            interpolate_hcl,
            parse_css_color,
            rgb01_to_hex,
        )

        expected_rgb = {
            0.0: (33, 102, 172),
            0.25: (82, 139, 212),
            0.5: (124, 177, 254),
            0.75: (166, 217, 255),
            1.0: (209, 255, 255),  # NOT pure white -- see class docstring
        }
        for t, (er, eg, eb) in expected_rgb.items():
            result = interpolate_hcl("#2166ac", "#ffffff", t)
            r, g, b, _a = parse_css_color(result)
            assert rgb01_to_hex(r, g, b) == rgb01_to_hex(
                er / 255, eg / 255, eb / 255
            ), (
                t,
                result,
            )

    def test_interpolate_hcl_black_center_holds_blue_hue_and_chroma(self):
        """Pure black also has undefined hue and chroma -- same
        constant-hold rule as white, at the opposite lightness pole."""
        from dbt_charts.core.colors import (
            interpolate_hcl,
            parse_css_color,
            rgb01_to_hex,
        )

        expected_rgb = {
            0.0: (33, 102, 172),
            0.25: (0, 77, 144),
            0.5: (0, 54, 117),
            0.75: (0, 32, 91),
            1.0: (0, 6, 66),
        }
        for t, (er, eg, eb) in expected_rgb.items():
            result = interpolate_hcl("#2166ac", "#000000", t)
            r, g, b, _a = parse_css_color(result)
            assert rgb01_to_hex(r, g, b) == rgb01_to_hex(
                er / 255, eg / 255, eb / 255
            ), (
                t,
                result,
            )

    def test_interpolate_hcl_reversed_achromatic_pair_mirrors_forward(self):
        """Swapping which endpoint is achromatic must swap which endpoint's
        hue/chroma gets held constant too -- not always hold color1's."""
        from dbt_charts.core.colors import (
            interpolate_hcl,
            parse_css_color,
            rgb01_to_hex,
        )

        forward = [interpolate_hcl("#2166ac", "#ffffff", t / 10) for t in range(11)]
        reverse = [interpolate_hcl("#ffffff", "#2166ac", t / 10) for t in range(11)]

        def to_hex(color: str) -> str:
            r, g, b, _a = parse_css_color(color)
            return rgb01_to_hex(r, g, b)

        assert [to_hex(c) for c in forward] == [to_hex(c) for c in reversed(reverse)]

    def test_interpolate_hcl_transparent_keeps_the_other_colors_hue_and_fades_alpha(
        self,
    ) -> None:
        """`transparent` is entirely undefined (not black) in d3-color --
        Vega keeps the OTHER endpoint's color fixed and only fades alpha.
        The pre-fix bug parsed `transparent` as opaque black (0, 0, 0),
        darkening the ramp toward black instead."""
        from dbt_charts.core.colors import interpolate_hcl, parse_css_color

        for t, expected_alpha in ((0.25, 0.75), (0.5, 0.5), (0.75, 0.25)):
            result = interpolate_hcl("#2166ac", "transparent", t)
            r, g, b, a = parse_css_color(result)
            assert (round(r * 255), round(g * 255), round(b * 255)) == (33, 102, 172)
            assert a == pytest.approx(expected_alpha)
