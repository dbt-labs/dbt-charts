"""Unit tests for the M2 palette resolver.

Covers: palette/color public API, shorthand parsing, diverging midpoint skip,
spine-direct resolution (no LUT), reverse, exception classes, and anti-pattern
handling.
"""

from __future__ import annotations

import importlib.util
import itertools
import math
import sys
import warnings
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest

from dbt_charts.cli.filesystem_project import FilesystemProject
from dbt_charts.core.colors import hex_to_oklch, is_sanitizable_color, oklch_to_hex
from dbt_charts.core.compile.config import (
    get_chart_rendering,
    load_config,
    reset_config,
)
from dbt_charts.core.compile.resolve.style.palette import (
    CategoricalOverrequestError,
    SurfaceUnsupportedError,
    ToneAsPaletteError,
    UnknownColorError,
    UnknownPaletteError,
    Variant,
    _downsample,
    _label_ink_step,
    _parse_palette_reference,
    color,
    label_ink,
    list_palettes,
    palette,
    palette_metadata,
    resolve_palette_alias,
    select_default_palette,
    variant,
)

from .._paths import DBT_CHARTS_DIR

# Load palette_deltae_checker as a script (not a package).
# Same pattern as test_palette_cvd.py.
_SCRIPTS_DIR = DBT_CHARTS_DIR / "scripts"


def _load_script(name: str):
    spec = importlib.util.spec_from_file_location(name, _SCRIPTS_DIR / f"{name}.py")
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


_checker = _load_script("palette_deltae_checker")

# ============================================================================
# _parse_palette_reference
# ============================================================================


class TestParsePaletteReference:
    def test_plain_name(self):
        assert _parse_palette_reference("dbt-seq-blue") == ("dbt-seq-blue", None, False)

    def test_steps_shorthand(self):
        assert _parse_palette_reference("dbt-seq-blue:5") == ("dbt-seq-blue", 5, False)

    def test_reverse_shorthand(self):
        assert _parse_palette_reference("dbt-seq-blue_r") == (
            "dbt-seq-blue",
            None,
            True,
        )

    def test_steps_and_reverse(self):
        assert _parse_palette_reference("dbt-seq-blue:5_r") == ("dbt-seq-blue", 5, True)

    def test_hyphenated_name_unaffected(self):
        # The name itself contains hyphens — parser must not break them.
        assert _parse_palette_reference("dbt-div-blue-red") == (
            "dbt-div-blue-red",
            None,
            False,
        )

    def test_hyphenated_with_steps(self):
        assert _parse_palette_reference("dbt-div-blue-red:6") == (
            "dbt-div-blue-red",
            6,
            False,
        )

    def test_invalid_steps_raises(self):
        with pytest.raises(ValueError, match="steps"):
            _parse_palette_reference("dbt-seq-blue:notanumber")

    def test_zero_or_negative_steps_raises(self):
        with pytest.raises(ValueError, match="steps"):
            _parse_palette_reference("dbt-seq-blue:0")


# ============================================================================
# _downsample
# ============================================================================


class TestDownsample:
    def test_identity_when_n_equals_len(self):
        stops = [f"#{i:06x}" for i in range(11)]
        assert _downsample(stops, 11) == stops

    def test_endpoints_always_preserved(self):
        stops = [f"#{i:06x}" for i in range(120)]
        result = _downsample(stops, 5)
        assert result[0] == stops[0]
        assert result[-1] == stops[-1]
        assert len(result) == 5

    def test_diverging_even_n_skips_midpoint(self):
        # 11-stop palette with known midpoint at index 5.
        stops = [f"#{i:06x}" for i in range(11)]
        result = _downsample(stops, 4, skip_midpoint_on_even=True)
        assert len(result) == 4
        # With midpoint skip, the 11-stop spine's midpoint "#000005" must not appear.
        assert stops[5] not in result

    def test_diverging_odd_n_includes_midpoint(self):
        stops = [f"#{i:06x}" for i in range(11)]
        result = _downsample(stops, 5, skip_midpoint_on_even=True)
        assert len(result) == 5
        assert stops[5] in result  # odd N includes midpoint

    def test_identity_when_requesting_exact_count(self):
        """Requesting exactly the number of available stops returns the list unchanged."""
        stops = ["#aaa000", "#bbb000", "#ccc000"]
        result = _downsample(stops, 3)
        assert result == stops


# ============================================================================
# palette() — sequential + diverging
# ============================================================================


class TestPaletteSequential:
    def test_default_returns_11_stops(self):
        stops = palette("dbt-seq-blue")
        assert len(stops) == 11
        assert all(s.startswith("#") and len(s) == 7 for s in stops)

    def test_steps_5_returns_5(self):
        stops = palette("dbt-seq-blue", steps=5)
        assert len(stops) == 5

    def test_shorthand_colon_n(self):
        assert palette("dbt-seq-blue:5") == palette("dbt-seq-blue", steps=5)

    def test_reverse_kwarg(self):
        forward = palette("dbt-seq-blue")
        rev = palette("dbt-seq-blue", reverse=True)
        assert rev == list(reversed(forward))

    def test_reverse_shorthand(self):
        assert palette("dbt-seq-blue_r") == palette("dbt-seq-blue", reverse=True)

    def test_surface_table_returns_wcag_safe_stops(self):
        # surface="table" returns WCAG-safe stops via OKLCH interpolation.
        from dbt_charts.core.colors import wcag_contrast as _wcag_contrast
        from dbt_charts.core.compile.resolve.style.palette import palette

        result = palette("dbt-seq-blue", surface="table")
        assert isinstance(result, list)
        assert len(result) == 11
        for stop in result:
            assert _wcag_contrast("#222222", stop) >= 4.5


class TestPaletteDiverging:
    def test_default_returns_11_stops(self):
        assert len(palette("dbt-div-blue-red")) == 11

    def test_even_n_skips_midpoint(self):
        # Midpoint of dbt-div-blue-red spine is #e4e4e4.
        stops = palette("dbt-div-blue-red", steps=6)
        assert len(stops) == 6
        assert "#e4e4e4" not in [s.lower() for s in stops]

    def test_odd_n_includes_midpoint(self):
        stops = palette("dbt-div-blue-red", steps=5)
        assert len(stops) == 5

    def test_steps_2_does_not_divide_by_zero(self):
        """Regression: n=2 with midpoint-skip on an odd-length spine used to
        divide by zero (half=1, half-1 = 0). Should return the two extremes.
        """
        stops = palette("dbt-div-blue-red", steps=2)
        assert len(stops) == 2
        # Two stops: low extreme + high extreme, midpoint skipped.
        assert stops[0].lower() == "#002f55"
        assert stops[1].lower() == "#590b05"


# ============================================================================
# palette() — categorical + scaffold
# ============================================================================


class TestPaletteCategorical:
    def test_vivid_10_default_returns_all_10(self):
        stops = palette("vivid-10")
        assert len(stops) == 10

    def test_hero_6_default_returns_all_6(self):
        stops = palette("hero-6")
        assert len(stops) == 6

    def test_steps_less_than_len_slices(self):
        stops = palette("vivid-10", steps=3)
        assert len(stops) == 3
        # Categorical slice is deterministic first-N.
        assert stops == palette("vivid-10")[:3]

    def test_over_request_raises(self):
        with pytest.raises(CategoricalOverrequestError):
            palette("vivid-10", steps=15)

    def test_surface_on_categorical_raises(self):
        with pytest.raises(SurfaceUnsupportedError):
            palette("vivid-10", surface="table")

    def test_vivid_10_dark_pairs_positionally_with_vivid_10(self):
        # The dark companion exists specifically so direct labels can use
        # the dark twin of each bright slot. If someone retones one
        # palette without the other, slot N stops corresponding and the
        # pairing silently breaks. Length is the cheap structural guard.
        assert len(palette("vivid-10")) == len(palette("vivid-10-dark")) == 10


class TestVividTenDark:
    """Direct-label inking companion to vivid-10. Slot N is positionally
    paired with slot N in vivid-10, sitting measurably darker so endpoint
    and segment labels ink against light canvases."""

    LOCKED_STOPS = [
        "#005998",
        "#00a1c0",
        "#008055",
        "#a07400",
        "#b03e00",
        "#82568d",
        "#4a6c00",
        "#5f3a12",
        "#6c7685",
        "#404852",
    ]

    def test_stops_match_locked_set(self):
        assert palette("vivid-10-dark") == self.LOCKED_STOPS

    def test_resolves_through_categorical_pipeline(self):
        meta = palette_metadata("vivid-10-dark")
        assert meta["family"] == "categorical"


class TestVividTenLight:
    """Moderate light companion to vivid-10. Slot N is positionally paired
    with slot N in vivid-10 and vivid-10-dark, sitting lighter and
    more desaturated than the base, strictly under the ghost tier."""

    LOCKED_STOPS = [
        "#628eba",
        "#8cd2e7",
        "#8cccab",
        "#e0bf83",
        "#d0896f",
        "#b698be",
        "#9ab27e",
        "#917459",
        "#b3b8bf",
        "#686e78",
    ]

    def test_resolves_with_ten_hex_stops(self):
        stops = palette("vivid-10-light")
        assert len(stops) == 10
        for stop in stops:
            assert isinstance(stop, str)
            assert len(stop) == 7 and stop[0] == "#"
            int(stop[1:], 16)  # raises ValueError on non-hex

    def test_stops_match_locked_set(self):
        assert palette("vivid-10-light") == self.LOCKED_STOPS

    def test_positional_oklch_invariant_against_base_dark_and_ghost(self):
        # Structural guarantee, no hex pins. For every slot:
        #   1. dark.L < base.L - 0.05  (-dark is measurably darker: label ink)
        #   2. light.L > base.L + 0.04 AND light.L > dark.L
        #   3. light.L <= ghost.L - 0.03  (tier discipline: the moderate
        #      light band sits strictly under the extreme ghost band; the
        #      pre-retune palette had cyan/gold-light ABOVE their ghosts)
        #   4. companions are no more saturated than the base
        #   5. companions hold the base hue (slot identity is hue-carried)
        # The old light/dark mirror contract is gone — same reasoning as
        # editorial-10 (#4745): the mirror was a derivation convenience that
        # forced bright slots over the ghost tier.
        base = palette("vivid-10")
        dark = palette("vivid-10-dark")
        light = palette("vivid-10-light")
        ghost = palette("vivid-10-ghost")
        assert len(base) == len(dark) == len(light) == len(ghost) == 10

        for slot, (b, d, lt, gh) in enumerate(
            zip(base, dark, light, ghost, strict=True)
        ):
            b_l, b_c, b_h = _checker.hex_to_oklch(b)
            d_l, d_c, d_h = _checker.hex_to_oklch(d)
            lt_l, lt_c, lt_h = _checker.hex_to_oklch(lt)
            gh_l, gh_c, gh_h = _checker.hex_to_oklch(gh)

            assert d_l < b_l - 0.05, (
                f"slot {slot}: dark companion {d!r} OKLCH L={d_l:.3f} should sit "
                f"measurably under base {b!r} OKLCH L={b_l:.3f} — direct labels "
                "don't ink otherwise"
            )
            assert lt_l > b_l + 0.04, (
                f"slot {slot}: light companion {lt!r} OKLCH L={lt_l:.3f} should be "
                f"measurably lighter than base {b!r} OKLCH L={b_l:.3f}"
            )
            assert lt_l > d_l, (
                f"slot {slot}: light companion {lt!r} OKLCH L={lt_l:.3f} should be "
                f"greater than dark companion {d!r} OKLCH L={d_l:.3f}"
            )
            assert lt_l <= gh_l - 0.03, (
                f"slot {slot}: light companion {lt!r} OKLCH L={lt_l:.3f} crosses "
                f"the ghost tier ({gh!r} OKLCH L={gh_l:.3f}) — light must stay a "
                "distinct middle tier under ghost"
            )
            assert lt_c <= b_c + 0.005, (
                f"slot {slot}: light companion {lt!r} OKLCH C={lt_c:.3f} should "
                f"not exceed base {b!r} OKLCH C={b_c:.3f}"
            )
            assert d_c <= b_c + 0.02, (
                f"slot {slot}: dark companion {d!r} OKLCH C={d_c:.3f} may bump "
                f"chroma only slightly over base {b!r} OKLCH C={b_c:.3f}"
            )
            for tier_name, v_h in (("dark", d_h), ("light", lt_h), ("ghost", gh_h)):
                hue_diff = abs(b_h - v_h)
                hue_diff = min(hue_diff, 360 - hue_diff)
                assert hue_diff < 8, (
                    f"slot {slot}: {tier_name} companion drifted in hue from base "
                    f"{b!r} (base H={b_h:.1f}°, {tier_name} H={v_h:.1f}°, "
                    f"Δ={hue_diff:.1f}°) — pairing is hue-carried"
                )

    def test_resolves_through_categorical_pipeline(self):
        # Same loader path as vivid-10-dark; no special-case wiring.
        meta = palette_metadata("vivid-10-light")
        assert meta["family"] == "categorical"


class TestVividTenGhost:
    """Extreme de-emphasis companion to vivid-10.

    The pale band that ghosts non-focus series while preserving hue identity —
    a distinct tier above the moderate light companion.
    """

    LOCKED_STOPS = [
        "#b9d4f0",
        "#b2d9e5",
        "#b6dbc7",
        "#e2cfad",
        "#f2c6b6",
        "#dccae1",
        "#c8d7b8",
        "#dccec2",
        "#ced1d6",
        "#babec4",
    ]

    def test_stops_match_locked_set(self):
        assert palette("vivid-10-ghost") == self.LOCKED_STOPS

    def test_ghost_is_far_lighter_than_base(self):
        base = palette("vivid-10")
        ghost = palette("vivid-10-ghost")
        for slot, (b, gh) in enumerate(zip(base, ghost, strict=True)):
            b_l, b_c, _ = _checker.hex_to_oklch(b)
            gh_l, gh_c, _ = _checker.hex_to_oklch(gh)
            assert gh_l > b_l + 0.09, (
                f"slot {slot}: ghost companion {gh!r} OKLCH L={gh_l:.3f} should "
                f"sit far above base {b!r} OKLCH L={b_l:.3f}"
            )
            # The ghost tier is a band, not a per-slot lift: every slot sits
            # at L >= 0.79 so ghosted members read uniformly pale. Charcoal
            # sat at L 0.74 pre-retune — visibly heavier than its band-mates
            # (editorial-10-ghost fixed the same defect in #4745).
            assert gh_l >= 0.79, (
                f"slot {slot}: ghost companion {gh!r} OKLCH L={gh_l:.3f} sits "
                "below the ghost band (L >= 0.79)"
            )
            assert gh_c <= b_c + 0.005, (
                f"slot {slot}: ghost companion {gh!r} OKLCH C={gh_c:.3f} should "
                f"not exceed base {b!r} OKLCH C={b_c:.3f}"
            )

    def test_resolves_through_categorical_pipeline(self):
        meta = palette_metadata("vivid-10-ghost")
        assert meta["family"] == "categorical"


class TestVividTenInk:
    """Ink-grade companion to vivid-10 — the symmetric opposite of the ghost
    tier. Every slot drops into a tight dark band (OKLCH L 0.30-0.36); slot
    identity is carried by hue and chroma, not lightness. Consumed by the
    vivid-family themes via inlined `single_series_palette` hexes."""

    LOCKED_STOPS = [
        "#003761",
        "#004554",
        "#00442b",
        "#503900",
        "#621f00",
        "#4d1c5a",
        "#2a4000",
        "#54310b",
        "#2e3641",
        "#252e3d",
    ]

    # Documented ink band (see vivid-10-ink.yml): a tight OKLCH L 0.30-0.36
    # with charcoal-ink anchoring the floor. The small tolerance absorbs sRGB
    # round-tripping at the band edges.
    _BAND_LO = 0.295
    _BAND_HI = 0.365

    def test_stops_match_locked_set(self):
        assert palette("vivid-10-ink") == self.LOCKED_STOPS

    def test_length_matches_base(self):
        assert len(palette("vivid-10")) == len(palette("vivid-10-ink")) == 10

    def test_ink_sits_in_dark_band_and_darker_than_base(self):
        base = palette("vivid-10")
        ink = palette("vivid-10-ink")
        for slot, (b, ik) in enumerate(zip(base, ink, strict=True)):
            b_l, _, b_h = _checker.hex_to_oklch(b)
            ik_l, _, ik_h = _checker.hex_to_oklch(ik)
            assert self._BAND_LO <= ik_l <= self._BAND_HI, (
                f"slot {slot}: ink companion {ik!r} OKLCH L={ik_l:.3f} is "
                f"outside the ink band [{self._BAND_LO}, {self._BAND_HI}]"
            )
            assert ik_l < b_l, (
                f"slot {slot}: ink companion {ik!r} OKLCH L={ik_l:.3f} must sit "
                f"under base {b!r} OKLCH L={b_l:.3f}"
            )
            # Near-neutral slots (gray/charcoal) have unstable hue at low
            # chroma; the chromatic slots hold the base hue within tolerance.
            _, b_c, _ = _checker.hex_to_oklch(b)
            if b_c > 0.04:
                hue_diff = abs(b_h - ik_h)
                hue_diff = min(hue_diff, 360 - hue_diff)
                assert hue_diff < 8, (
                    f"slot {slot}: ink companion drifted in hue from base "
                    f"(base H={b_h:.1f}°, ink H={ik_h:.1f}°, Δ={hue_diff:.1f}°)"
                )

    def test_resolves_through_categorical_pipeline(self):
        meta = palette_metadata("vivid-10-ink")
        assert meta["family"] == "categorical"


class TestPaletteTonal:
    """Tonal monochromatic categorical palettes (M2)."""

    @pytest.mark.parametrize(
        ("name", "anchor"),
        [
            ("category-6-tonal-blue", "#0375c4"),
            ("category-6-tonal-green", "#00875a"),
            ("category-6-tonal-purple", "#9650a8"),
            ("category-6-tonal-orange", "#b74c1f"),
            ("category-6-tonal-brown", "#9a642b"),
        ],
    )
    def test_tonal_palette_ships_six_stops(self, name: str, anchor: str) -> None:
        stops = palette(name)
        assert len(stops) == 6
        assert stops[0].lower() == anchor.lower()

    def test_tonal_palette_strict_safe_core_passes_leonardo(self) -> None:
        """Slots 0-3 of every tonal palette must pairwise pass Leonardo ΔE ≥ 11
        across normal / deuteranopia / protanopia / tritanopia.

        Achromatopsia is intentionally excluded — it's stricter than the
        Leonardo standard four, and the contract advertised in the guide
        and YAML descriptions is Leonardo's four. (The shipped palettes
        do incidentally pass achromatopsia at the same gate, but pinning
        that here would make a hypothetical re-tune that fails *only*
        achromatopsia surprising to a future palette author who read the
        documented contract.)
        See docs/guides/palettes.md#tonal-monochrome-category-6-tonal-
        """
        leonardo_visions = ("normal", "deuteranopia", "protanopia", "tritanopia")
        for name in (
            "category-6-tonal-blue",
            "category-6-tonal-green",
            "category-6-tonal-purple",
            "category-6-tonal-orange",
            "category-6-tonal-brown",
        ):
            core = palette(name)[:4]
            for vision in leonardo_visions:
                matrix = _checker.VISION_MATRICES[vision]
                labs = []
                for hx in core:
                    rgb = _checker.hex_to_rgb01(hx.lower())
                    lin = (
                        _checker.srgb_to_linear(rgb[0]),
                        _checker.srgb_to_linear(rgb[1]),
                        _checker.srgb_to_linear(rgb[2]),
                    )
                    sim = _checker.apply_matrix(lin, matrix)
                    labs.append(_checker.xyz_to_lab(_checker.linear_rgb_to_xyz(sim)))
                pair_deltas = [
                    _checker.delta_e_ciede2000(labs[i], labs[j])
                    for i, j in itertools.combinations(range(4), 2)
                ]
                assert min(pair_deltas) >= 11.0, (
                    f"{name} core-4 fails Leonardo gate under {vision}: "
                    f"min ΔE = {min(pair_deltas):.2f}"
                )

    # test_studio_output_matches_shipped_yaml is intentionally not covered
    # here — it loads ai_notes/palette_studio/tonal_session.py, which is
    # outside dbt-charts/.

    @pytest.mark.parametrize(
        ("name", "anchor_hue", "wobble_tolerance"),
        [
            # All 6 stops within ±8° of anchor (the brief's "tonal" tolerance).
            ("category-6-tonal-blue", 248.8, 8.0),
            ("category-6-tonal-green", 161.3, 8.0),
            ("category-6-tonal-purple", 319.1, 8.0),
            ("category-6-tonal-orange", 41.0, 8.0),
            # Brown is documented as a special case: extended slots 4-5 wobble
            # +14° toward H≈78° to bridge into dbt-creams. So core slots are
            # within ±8°; extended slots can be up to 18° off the anchor.
            ("category-6-tonal-brown", 64.0, 18.0),
        ],
    )
    def test_tonal_palette_hue_stays_in_family(
        self, name: str, anchor_hue: float, wobble_tolerance: float
    ) -> None:
        """Defining property of a tonal palette: every stop shares the anchor
        hue within tolerance. Catches a re-tune that quietly broke single-hue
        identity (e.g. someone replacing a slot with a different hue family
        that still passes Leonardo).
        """
        for hx in palette(name):
            _, _, hue = _checker.hex_to_oklch(hx)
            # Circular distance, in case anchor is near the 0/360 boundary.
            delta = min(abs(hue - anchor_hue), 360 - abs(hue - anchor_hue))
            assert delta <= wobble_tolerance, (
                f"{name} stop {hx} at H={hue:.1f}° is "
                f"{delta:.1f}° from anchor {anchor_hue}° (tol {wobble_tolerance}°)"
            )


class TestPaletteScaffold:
    def test_dbt_grays_via_palette_raises(self):
        # Scaffolds are flat-mapping palettes; surface= must raise.
        with pytest.raises(SurfaceUnsupportedError):
            palette("dbt-grays", surface="table")

    def test_dbt_grays_via_color_slot(self):
        assert color("dbt-grays.ink").lower() == "#222222"
        assert color("dbt-creams.ink").lower() == "#2a2725"


# ============================================================================
# color() — semantic + scaffold tokens
# ============================================================================


class TestColor:
    def test_tone_slot(self):
        assert color("negative.solid").lower() == "#94001e"
        assert color("positive.solid").lower() == "#00884d"

    def test_unknown_role_raises(self):
        with pytest.raises(UnknownColorError):
            color("nonexistent.solid")

    def test_unknown_slot_raises(self):
        with pytest.raises(UnknownColorError):
            color("negative.nonexistent")

    def test_missing_dot_raises(self):
        with pytest.raises(UnknownColorError):
            color("negative")


# ============================================================================
# Exception classes
# ============================================================================


class TestExceptions:
    def test_unknown_palette_message_suggests_nearest(self):
        with pytest.raises(UnknownPaletteError) as excinfo:
            palette("dbt-seq-bule")  # typo
        assert "dbt-seq-blue" in str(excinfo.value)

    def test_tone_via_palette_raises(self):
        with pytest.raises(ToneAsPaletteError):
            palette("negative")


# ============================================================================
# Anti-patterns (§10)
# ============================================================================


class TestAntiPatterns:
    def test_jet_raises(self):
        with pytest.raises(UnknownPaletteError):
            palette("jet")

    def test_rainbow_raises(self):
        with pytest.raises(UnknownPaletteError):
            palette("rainbow")

    def test_hsv_raises(self):
        with pytest.raises(UnknownPaletteError):
            palette("hsv")

    def test_rdylgn_resolves_without_warning(self):
        """palette() resolves anti-pattern aliases silently — the
        WARN-PALETTE-UNSUPPORTED nudge is now a render-stage detector over the
        compiled board's requested_alias_palette, not an inline emit here."""
        with warnings.catch_warnings(record=True) as w:
            warnings.simplefilter("always")
            stops = palette("RdYlGn")
            assert len(stops) > 0
            assert not w, f"palette() must not warn directly; got {w}"

    def test_parula_resolves_without_warning(self):
        with warnings.catch_warnings(record=True) as w:
            warnings.simplefilter("always")
            stops = palette("parula")
            assert len(stops) > 0
            assert not w, f"palette() must not warn directly; got {w}"

    def test_hard_fail_error_message_points_at_palette_resolver(self):
        with pytest.raises(UnknownPaletteError) as excinfo:
            palette("jet")
        assert "docs/guides/palette-resolver.md#anti-patterns" in str(excinfo.value)


class TestResolvePaletteAlias:
    """resolve_palette_alias() is the single source of anti-pattern detection
    the CategoricalColorStyle model_validator uses to retain the requested
    name for the render-stage detector."""

    def test_warn_alias_name_reports_original_name(self):
        stops, requested = resolve_palette_alias("RdYlGn")
        assert len(stops) > 0
        assert requested == "RdYlGn"

    def test_warn_alias_shorthand_still_reports_base_name(self):
        stops, requested = resolve_palette_alias("RdYlGn:5")
        assert len(stops) == 5
        assert requested == "RdYlGn"

    def test_non_alias_name_reports_none(self):
        stops, requested = resolve_palette_alias("dbt-seq-blue")
        assert len(stops) > 0
        assert requested is None


# ============================================================================
# list_palettes / palette_metadata
# ============================================================================


class TestDiscovery:
    def test_list_all(self):
        names = list_palettes()
        assert "dbt-seq-blue" in names
        assert "vivid-10" in names
        assert "negative" in names
        assert "dbt-grays" in names

    def test_list_filtered_by_family(self):
        seq_names = list_palettes(family="sequential")
        assert "dbt-seq-blue" in seq_names
        assert "vivid-10" not in seq_names
        assert "negative" not in seq_names

    def test_list_tone_family(self):
        tone_names = list_palettes(family="tone")
        assert "negative" in tone_names
        assert "positive" in tone_names
        assert "warning" in tone_names
        assert "info" in tone_names
        assert len(tone_names) == 4

    def test_palette_metadata_basic(self):
        meta = palette_metadata("dbt-seq-blue")
        assert meta["name"] == "dbt-seq-blue"
        assert meta["family"] == "sequential"
        assert "description" in meta


# ============================================================================
# select_default_palette
# ============================================================================


class TestSelectDefault:
    def test_continuous_numeric(self):
        assert select_default_palette("continuous_numeric") == "dbt-seq-blue"

    def test_signed_numeric(self):
        assert select_default_palette("signed_numeric") == "dbt-div-blue-red"

    def test_discrete_enum(self):
        assert select_default_palette("discrete_enum") == "vivid-10"


# ============================================================================
# variant() / label_ink() — literal tiers, canvas-aware ink
# ============================================================================

# Every built-in categorical palette that isn't itself one of the hand-tuned
# companion palettes variant()/label_ink() reproduce (the eight
# -dark/-light/-ghost/-ink files), plus four inline user palettes with no
# companion file at all -- the case these two functions exist to serve.
_BASE_PALETTE_NAMES = (
    "vivid-10",
    "editorial-10",
    "tableau",
    "hero-6",
    "category-6-tonal-blue",
    "category-6-tonal-brown",
    "category-6-tonal-green",
    "category-6-tonal-orange",
    "category-6-tonal-purple",
)

# tableau-10 classic (D3/Tableau's own hexes -- distinct from the shipped
# "tableau" built-in, which is a retune).
_USER_TABLEAU_10 = [
    "#4e79a7",
    "#f28e2b",
    "#e15759",
    "#76b7b2",
    "#59a14f",
    "#edc949",
    "#af7aa1",
    "#ff9da7",
    "#9c755f",
    "#bab0ab",
]
# A saturated brand palette with two neutrals -- the shape a user with no
# design-system chroma discipline actually ships.
_USER_BRAND = [
    "#e4002b",
    "#0057b8",
    "#ffb81c",
    "#00a651",
    "#6f2c91",
    "#333333",
    "#999999",
]
# An all-pastel palette, six stops at OKLCH L 0.80-0.90 -- the stress case
# for `light`'s under-pale clamp and for label_ink's floor (base marks this
# light leave label_ink almost nothing to work with).
_USER_PASTEL = [
    oklch_to_hex(0.80, 0.09, 20),
    oklch_to_hex(0.82, 0.09, 80),
    oklch_to_hex(0.84, 0.09, 140),
    oklch_to_hex(0.86, 0.09, 200),
    oklch_to_hex(0.88, 0.09, 260),
    oklch_to_hex(0.90, 0.07, 320),
    # Two near-white stops: the light tier's floor must stop at white
    # rather than ask for an OKLCH L above 1.0 (which is outside sRGB at
    # every chroma, achromatic included).
    "#fafafa",
    "#ffff66",
]
# A mostly-dark corporate palette: six stops at OKLCH L 0.25-0.45, plus one
# light stop -- the shape where `dark`/`deep` have little room left to move.
# The near-black navy stop (L ~0.15, below dark_pole 0.20) is the one base
# in the whole corpus dark enough to exercise the "never lighter than base"
# clamp on dark/deep -- every other palette's darkest stop sits at L >= 0.25.
_USER_CORPORATE = [
    oklch_to_hex(0.25, 0.05, 20),
    oklch_to_hex(0.30, 0.06, 80),
    oklch_to_hex(0.33, 0.06, 140),
    oklch_to_hex(0.38, 0.07, 200),
    oklch_to_hex(0.42, 0.08, 260),
    oklch_to_hex(0.45, 0.08, 320),
    oklch_to_hex(0.78, 0.03, 100),
    oklch_to_hex(0.15, 0.06, 260),
]

_USER_PALETTES: dict[str, list[str]] = {
    "user-tableau-10": _USER_TABLEAU_10,
    "user-brand": _USER_BRAND,
    "user-pastel": _USER_PASTEL,
    "user-corporate": _USER_CORPORATE,
}


def _all_palettes() -> dict[str, list[str]]:
    stops = {name: palette(name) for name in _BASE_PALETTE_NAMES}
    stops.update(_USER_PALETTES)
    return stops


# Companion hexes copied from the hand-tuned palette files these tiers
# reproduce (vivid-10-{dark,light,ghost,ink}.yml,
# editorial-10-{dark,light,ghost,ink}.yml) -- committed-hex regression data.
# Read here as literals, not from the files, so this test still passes once
# those files are retired.
_VIVID_10_DARK_COMMITTED = [
    "#005998",
    "#00a1c0",
    "#008055",
    "#a07400",
    "#b03e00",
    "#82568d",
    "#4a6c00",
    "#5f3a12",
    "#6c7685",
    "#404852",
]
_VIVID_10_LIGHT_COMMITTED = [
    "#628eba",
    "#8cd2e7",
    "#8cccab",
    "#e0bf83",
    "#d0896f",
    "#b698be",
    "#9ab27e",
    "#917459",
    "#b3b8bf",
    "#686e78",
]
_VIVID_10_INK_COMMITTED = [
    "#003761",
    "#004554",
    "#00442b",
    "#503900",
    "#621f00",
    "#4d1c5a",
    "#2a4000",
    "#54310b",
    "#2e3641",
    "#252e3d",
]
_EDITORIAL_10_DARK_COMMITTED = [
    "#23467f",
    "#476b98",
    "#3d614e",
    "#5b3b55",
    "#955902",
    "#8a3f24",
    "#586e6f",
    "#77664a",
    "#54626f",
    "#424c4e",
]
_EDITORIAL_10_LIGHT_COMMITTED = [
    "#7990b6",
    "#a3bad7",
    "#8ea598",
    "#9a8595",
    "#e0b993",
    "#c29180",
    "#bdcacb",
    "#c7bcaa",
    "#a0a9b1",
    "#8a9092",
]
_EDITORIAL_10_INK_COMMITTED = [
    "#0f2c5a",
    "#1c395c",
    "#1f3d2d",
    "#3d2438",
    "#563000",
    "#591d05",
    "#2b3c3c",
    "#43361f",
    "#29343e",
    "#252d2f",
]
_VIVID_10_GHOST_COMMITTED = [
    "#b9d4f0",
    "#b2d9e5",
    "#b6dbc7",
    "#e2cfad",
    "#f2c6b6",
    "#dccae1",
    "#c8d7b8",
    "#dccec2",
    "#ced1d6",
    "#babec4",
]
_EDITORIAL_10_GHOST_COMMITTED = [
    "#c5d2e7",
    "#cdd9e8",
    "#c5d1ca",
    "#d6cad2",
    "#e8d3c0",
    "#e2c7be",
    "#d8e0e0",
    "#dcd7cd",
    "#caced3",
    "#c8cbcc",
]


def _is_multi_hue(stops: list[str]) -> bool:
    """Whether two chromatic stops sit more than 30 degrees of hue apart.

    The separation gate is the rule's guarantee for hue-separated palettes;
    a single-hue tonal family carries its separation in lightness alone,
    the move-shrinks-by-exactly-(1 - dark_k) case -- and CIEDE2000's S_L
    weighting is not linear in that coordinate, so the bound isn't exact
    there (measured shortfall on two of them: tonal-brown 10.07 vs 10.08,
    tonal-purple 9.35 vs 9.86). All five category-6-tonal-* families are
    single-hue and return False here, not just those two -- they carry the
    same lightness-only separation whether or not their own numbers happen
    to clear the bound. Single-hue families are reported, not gated; every
    other palette in the corpus (the four multi-hue built-ins plus the four
    inline user palettes, eight of the thirteen total) is gated.
    """
    hues = [H for L, C, H in map(hex_to_oklch, stops) if C > 0.04]
    return any(_hue_delta(a, b) > 30 for a in hues for b in hues)


def _hue_delta(h1: float, h2: float) -> float:
    d = abs(h1 - h2) % 360
    return min(d, 360 - d)


def _oklab(hex_color: str) -> tuple[float, float, float]:
    L, C, H = hex_to_oklch(hex_color)
    return L, C * math.cos(math.radians(H)), C * math.sin(math.radians(H))


def _oklab_delta_e_x100(hex_a: str, hex_b: str) -> float:
    La, aa, ba = _oklab(hex_a)
    Lb, ab, bb = _oklab(hex_b)
    return 100 * math.sqrt((La - Lb) ** 2 + (aa - ab) ** 2 + (ba - bb) ** 2)


def _checker_lab(hex_color: str) -> tuple[float, float, float]:
    r, g, b = _checker.hex_to_rgb01(hex_color)
    linear = (
        _checker.srgb_to_linear(r),
        _checker.srgb_to_linear(g),
        _checker.srgb_to_linear(b),
    )
    return _checker.linear_rgb_to_lab(linear)


def _worst_pair_delta_e(hexes: list[str]) -> float:
    labs = [_checker_lab(h) for h in hexes]
    return min(
        _checker.delta_e_ciede2000(a, b) for a, b in itertools.combinations(labs, 2)
    )


class TestVariant:
    """`variant()`'s four literal tiers and `label_ink()`'s canvas-aware ink,
    over every built-in categorical palette plus four inline user palettes
    with no companion file."""

    @pytest.mark.parametrize("name", sorted(_all_palettes()))
    def test_ordering_per_stop(self, name: str) -> None:
        """Per stop with base chroma > 0.04: light strictly above base, dark
        never above base, dark above deep once base clears L 0.33 (below
        that the two converge by construction -- the category-6-tonal-*
        slot-3 anchors at L 0.25 are the shipped case), pale above light
        whenever base doesn't already sit above the pale band, and light
        lifts at least light_min_gap above base."""
        stops = _all_palettes()[name]
        for i, base in enumerate(stops):
            L, C, _H = hex_to_oklch(base)
            if C <= 0.04:
                continue
            light_l = hex_to_oklch(variant(base, "light"))[0]
            dark_l = hex_to_oklch(variant(base, "dark"))[0]
            pale_l = hex_to_oklch(variant(base, "pale"))[0]
            deep_l = hex_to_oklch(variant(base, "deep"))[0]

            assert light_l > L, (name, i, "light must be strictly lighter")
            assert dark_l - 1e-6 <= L, (name, i, "dark must never be lighter than base")
            if L >= 0.33:
                assert dark_l > deep_l - 1e-6, (name, i, "dark must sit above deep")
            else:
                assert deep_l <= L + 1e-6, (
                    name,
                    i,
                    "deep must never lighten a base < L 0.33",
                )
            if L <= 0.80:
                assert pale_l > light_l - 1e-6, (name, i, "pale must sit above light")
            assert light_l >= min(1.0, L + 0.03) - 2e-3, (
                name,
                i,
                "light must lift at least light_min_gap above base",
            )

    @pytest.mark.parametrize("name", sorted(_all_palettes()))
    def test_hue_held_within_2_degrees(self, name: str) -> None:
        """Hue drift stays under 2 degrees for every tier -- gated only when
        BOTH the base and the derived tier keep chroma > 0.03: below that,
        hue is not perceptually meaningful (a near-gray's hue angle is
        numerically unstable through the sRGB 8-bit round trip), the same
        floor test_ordering_per_stop already applies to the base."""
        stops = _all_palettes()[name]
        for base in stops:
            L, C, H = hex_to_oklch(base)
            if C <= 0.04:
                continue
            for kind in ("light", "dark", "pale", "deep"):
                Lv, Cv, Hv = hex_to_oklch(variant(base, kind))
                if Cv <= 0.03:
                    continue
                assert _hue_delta(H, Hv) <= 2.0, (name, base, kind, H, Hv)

    @pytest.mark.parametrize("name", sorted(_all_palettes()))
    def test_light_and_pale_chroma_never_exceed_base(self, name: str) -> None:
        stops = _all_palettes()[name]
        for base in stops:
            _L, C, _H = hex_to_oklch(base)
            if C <= 0.04:
                continue
            assert hex_to_oklch(variant(base, "light"))[1] <= C + 1e-6
            assert hex_to_oklch(variant(base, "pale"))[1] <= C + 1e-6

    def test_dark_and_deep_never_lighten_a_near_black_stop(self) -> None:
        """The corpus' next-darkest base sits at L 0.25 -- above both
        dark_pole (0.20) and deep_pole (0.28) close enough that the "never
        lighter than base" clamp on dark/deep is never exercised by
        test_ordering_per_stop alone. A base darker than both poles
        (L ~0.15) is the case the clamp exists for: unclamped, the pole
        arithmetic would pull it UP toward the pole instead of leaving it
        alone."""
        base = "#0a0a12"
        base_l = hex_to_oklch(base)[0]
        dark_l = hex_to_oklch(variant(base, "dark"))[0]
        deep_l = hex_to_oklch(variant(base, "deep"))[0]
        assert dark_l <= base_l + 1e-9, "dark must not lighten a near-black stop"
        assert deep_l <= base_l + 1e-9, "deep must not lighten a near-black stop"

    def test_proportional_move_shrinks_toward_the_pole(self) -> None:
        """Two bases at the same hue, different L: the lighter one's `dark`
        and `deep` drop further in absolute L (it started farther from the
        dark/deep pole near black), and its `light` lift is smaller (it
        started closer to the light pole, white)."""
        hue, chroma = 250.0, 0.12
        darker_base = oklch_to_hex(0.40, chroma, hue)
        lighter_base = oklch_to_hex(0.65, chroma, hue)
        darker_l = hex_to_oklch(darker_base)[0]
        lighter_l = hex_to_oklch(lighter_base)[0]

        for kind in ("dark", "deep"):
            drop_darker = darker_l - hex_to_oklch(variant(darker_base, kind))[0]
            drop_lighter = lighter_l - hex_to_oklch(variant(lighter_base, kind))[0]
            assert drop_lighter > drop_darker, kind

        lift_darker = hex_to_oklch(variant(darker_base, "light"))[0] - darker_l
        lift_lighter = hex_to_oklch(variant(lighter_base, "light"))[0] - lighter_l
        assert lift_lighter < lift_darker

    @pytest.mark.parametrize(
        ("family", "base_name", "committed", "kind", "threshold"),
        [
            ("vivid-10", "vivid-10", _VIVID_10_LIGHT_COMMITTED, "light", 4.0),
            ("vivid-10", "vivid-10", _VIVID_10_DARK_COMMITTED, "dark", 4.0),
            ("vivid-10", "vivid-10", _VIVID_10_INK_COMMITTED, "deep", 4.0),
            (
                "editorial-10",
                "editorial-10",
                _EDITORIAL_10_LIGHT_COMMITTED,
                "light",
                2.5,
            ),
            ("editorial-10", "editorial-10", _EDITORIAL_10_DARK_COMMITTED, "dark", 2.5),
            ("editorial-10", "editorial-10", _EDITORIAL_10_INK_COMMITTED, "deep", 2.5),
            ("vivid-10", "vivid-10", _VIVID_10_GHOST_COMMITTED, "pale", 4.0),
            (
                "editorial-10",
                "editorial-10",
                _EDITORIAL_10_GHOST_COMMITTED,
                "pale",
                2.5,
            ),
        ],
    )
    def test_committed_hex_regression(
        self,
        family: str,
        base_name: str,
        committed: list[str],
        kind: Variant,
        threshold: float,
    ) -> None:
        """Pinned as a regression, not a golden: mean OKLab ΔE x100 between
        the derived tier and the hand-tuned companion hexes it replaces
        stays under the fitted threshold, so a constant tweak that drifts
        the two families away from their prior hand-tuning is caught.
        Measured at authoring time: vivid light/dark/deep/pale
        3.2/1.9/1.2/0.6, editorial light/dark/deep/pale 0.5/2.1/1.1/1.6 --
        all comfortably under their thresholds (4.0 vivid, 2.5 editorial)."""
        base = palette(base_name)
        deltas = [
            _oklab_delta_e_x100(variant(b, kind), c)
            for b, c in zip(base, committed, strict=True)
        ]
        mean = sum(deltas) / len(deltas)
        print(f"{family} {kind}: mean ΔE×100={mean:.2f} max={max(deltas):.2f}")
        assert mean < threshold, f"{family} {kind}: mean {mean:.2f} >= {threshold}"

    @pytest.mark.parametrize("name", sorted(_all_palettes()))
    def test_label_ink_clears_contrast_floor_on_every_canvas(self, name: str) -> None:
        from dbt_charts.core.colors import wcag_contrast

        stops = _all_palettes()[name]
        min_contrast = get_chart_rendering().color_variants.label_ink_min_contrast
        for base in stops:
            for canvas in ("#ffffff", "#fafafa", "#faf7f0", "#161616"):
                ink = label_ink(base, canvas)
                assert wcag_contrast(ink, canvas) >= min_contrast - 1e-6, (
                    name,
                    base,
                    canvas,
                )

    @pytest.mark.parametrize("name", sorted(_all_palettes()))
    def test_label_ink_is_lighter_than_base_on_a_dark_canvas(self, name: str) -> None:
        stops = _all_palettes()[name]
        for base in stops:
            ink = label_ink(base, "#161616")
            assert hex_to_oklch(ink)[0] > hex_to_oklch(base)[0], (name, base)

    def test_label_ink_step_matches_dark_k_times_pole_distance(self) -> None:
        """The pre-floor move (`_label_ink_step`) shifts L by
        `dark_k * |pole - L|` -- exercised directly (not through the
        ensure_readable_ink floor) so the rule's own arithmetic is pinned,
        not just its downstream contrast outcome."""
        from dbt_charts.core.colors import is_light_canvas

        config = get_chart_rendering().color_variants
        for base in (palette("vivid-10")[0], palette("editorial-10")[4]):
            L = hex_to_oklch(base)[0]
            for canvas in ("#ffffff", "#161616"):
                pole = config.dark_pole if is_light_canvas(canvas) else 1.0
                stepped_l = hex_to_oklch(_label_ink_step(base, canvas, config))[0]
                expected_move = config.dark_k * abs(pole - L)
                assert abs(abs(L - stepped_l) - expected_move) < 2e-3, (base, canvas)

    def test_label_ink_step_never_moves_toward_the_canvas(self) -> None:
        """Regression: unclamped, a base darker than dark_pole on a light
        canvas stepped UP toward the pole -- i.e. toward the canvas,
        backwards from what the docstring promises. Clamped, the step is a
        no-op for that base (`#0a0a12`, L ~0.149, below dark_pole 0.20):
        it clears white's contrast floor on its own, so `label_ink` returns
        it unmoved rather than lightened.

        `#f4f4f4` (L ~0.967) on `#161616` pins the unclamped dark-canvas
        step: the pole is white, at or above any base L, so the step
        always moves away from the canvas and needs no clamp.
        """
        from dbt_charts.core.colors import wcag_contrast

        config = get_chart_rendering().color_variants

        near_black, light_canvas = "#0a0a12", "#ffffff"
        base_l = hex_to_oklch(near_black)[0]
        stepped_l = hex_to_oklch(_label_ink_step(near_black, light_canvas, config))[0]
        assert abs(stepped_l - base_l) < 1e-9, "clamp must hold the step at base L"
        ink = label_ink(near_black, light_canvas)
        assert wcag_contrast(ink, light_canvas) >= config.label_ink_min_contrast - 1e-6

        near_white, dark_canvas = "#f4f4f4", "#161616"
        base_l = hex_to_oklch(near_white)[0]
        stepped_l = hex_to_oklch(_label_ink_step(near_white, dark_canvas, config))[0]
        assert stepped_l >= base_l - 1e-9, (
            "the dark-canvas step is unclamped and moves away from the canvas"
        )
        ink = label_ink(near_white, dark_canvas)
        assert wcag_contrast(ink, dark_canvas) >= config.label_ink_min_contrast - 1e-6

    @pytest.mark.parametrize("base", ["#ffffff", "#fafafa", "#faf7f0", "#ffff66"])
    def test_light_of_a_near_white_base_stays_in_gamut(self, base: str) -> None:
        """A base within light_min_gap of white cannot lift by the full gap;
        the floor stops at L 1.0 instead of asking for a lightness no sRGB
        color has (which used to come back as black)."""
        light = variant(base, "light")
        assert light != "#000000", base
        assert hex_to_oklch(light)[0] >= hex_to_oklch(base)[0] - 1e-6, base

    def test_variant_raises_on_an_unknown_kind(self) -> None:
        with pytest.raises(ValueError, match="unknown variant"):
            variant("#0073c2", "darker")  # intentionally not a valid Variant

    @pytest.mark.parametrize("name", sorted(_all_palettes()))
    def test_separation_gate_dark_tier(self, name: str) -> None:
        """The move-only `dark` tier's worst CIEDE2000 pair never drops
        below `(1 - dark_k)` of the base palette's own worst pair -- the
        rule's own guarantee: an unclamped move shrinks every pairwise
        OKLab lightness gap by exactly that factor. `light`/`pale`/`deep`
        compress separation by construction (chroma scaling, a flat band)
        and are reported, never gated -- see
        test_other_tiers_worst_pair_is_reported_not_gated.

        Gated for multi-hue palettes only; single-hue tonal families are
        printed -- see _is_multi_hue.
        """
        stops = _all_palettes()[name]
        config = get_chart_rendering().color_variants
        base_worst = _worst_pair_delta_e(stops)
        dark_worst = _worst_pair_delta_e([variant(s, "dark") for s in stops])
        required = (1 - config.dark_k) * base_worst
        print(
            f"{name}: base={base_worst:.2f} dark={dark_worst:.2f} required={required:.2f}"
        )
        if not _is_multi_hue(stops):
            return
        assert dark_worst >= required - 1e-6

    @pytest.mark.parametrize("name", sorted(_all_palettes()))
    def test_other_tiers_worst_pair_is_reported_not_gated(self, name: str) -> None:
        stops = _all_palettes()[name]
        for kind in ("light", "pale", "deep"):
            worst = _worst_pair_delta_e([variant(s, kind) for s in stops])
            print(f"{name} {kind}: worst pair={worst:.2f}")
        ink_worst = _worst_pair_delta_e([label_ink(s, "#ffffff") for s in stops])
        print(f"{name} label_ink(white): worst pair={ink_worst:.2f}")

    def test_reloading_config_changes_the_derived_color(
        self, tmp_path: Path, local_project: Callable[..., FilesystemProject]
    ) -> None:
        """Reloading with a different color_variants constant must change
        variant()'s output color -- asserts the color changes, not that the
        cache invalidates (it doesn't need to: the frozen config is the
        cache key)."""
        reset_config()
        try:
            base = palette("vivid-10")[0]
            before = variant(base, "dark")

            (tmp_path / "dbt_charts.yml").write_text(
                "chart_rendering:\n  color_variants:\n    dark_k: 0.6\n"
            )
            load_config(local_project(tmp_path))
            after = variant(base, "dark")

            assert after != before
        finally:
            reset_config()


class TestMarkInk:
    """mark_ink() routes every non-token/non-identifier mark color through
    parse_css_color(), not label_ink() directly. No breaking change: a mark
    color dbt Charts cannot read is inked as itself, the same contract the
    pre-live-derivation companion lookup had for a color with no `-dark`
    twin -- render paints it as authored; there is no ink to derive."""

    def test_six_digit_hex_inks_normally(self):
        from dbt_charts.core.compile.resolve.style.palette import mark_ink

        ink = mark_ink("#0073c2", "#ffffff")
        assert ink != "#0073c2"
        assert is_sanitizable_color(ink)

    def test_three_digit_short_hex_does_not_raise(self):
        """#fff used to crash inside hex_to_oklch (int('', 16))."""
        from dbt_charts.core.compile.resolve.style.palette import mark_ink

        ink = mark_ink("#fff", "#161616")
        assert is_sanitizable_color(ink)

    def test_eight_digit_hex_with_alpha_inks_normally(self):
        from dbt_charts.core.compile.resolve.style.palette import mark_ink

        ink = mark_ink("#4e79a7ff", "#ffffff")
        assert is_sanitizable_color(ink)

    def test_rgb_function_inks_normally(self):
        from dbt_charts.core.compile.resolve.style.palette import mark_ink

        ink = mark_ink("rgb(10, 20, 30)", "#ffffff")
        assert is_sanitizable_color(ink)

    def test_malformed_five_digit_hex_falls_back_unchanged(self):
        """#12345 used to silently truncate through hex_to_oklch (5 hex
        digits sliced into a bogus 2/2/1-digit r/g/b); parse_css_color
        correctly rejects it, and mark_ink falls back to painting it as
        authored rather than raising."""
        from dbt_charts.core.compile.resolve.style.palette import mark_ink

        assert mark_ink("#12345", "#ffffff") == "#12345"

    def test_css_keyword_name_inks_against_its_real_hex(self):
        from dbt_charts.core.compile.resolve.style.palette import mark_ink

        white_ink = mark_ink("white", "#161616")
        # "white" resolves to #ffffff first, then label_ink()'s dark move
        # toward white (the pole, on this dark canvas) -- already there,
        # so it legitimately inks to #ffffff exactly, unmoved.
        assert white_ink == "#ffffff"

    def test_color_token_shape_passes_through_unresolved(self):
        from dbt_charts.core.compile.resolve.style.palette import mark_ink

        assert mark_ink("category[2]", "#ffffff") == "category[2]"
        assert mark_ink("chrome.ink", "#ffffff") == "chrome.ink"

    def test_bare_identifier_passes_through_unresolved(self):
        from dbt_charts.core.compile.resolve.style.palette import mark_ink

        assert mark_ink("category", "#ffffff") == "category"

    def test_currentcolor_falls_back_unchanged_not_deferred(self):
        """`currentColor` is bare-identifier-shaped like a candidate role
        name, but it's a reserved CSS keyword the browser owns, never a
        role -- `is_deferred_color_reference` excludes it, so it takes the
        parse-failure fallback path here, not the role-deferral path
        "category" above takes. Both happen to return the value unchanged,
        but via different code -- this pins the reserved-keyword carve-out
        stays live even though its output is indistinguishable from the
        general fallback."""
        from dbt_charts.core.compile.resolve.style.palette import mark_ink

        assert mark_ink("currentColor", "#ffffff") == "currentColor"

    def test_transparent_mark_passes_through_unchanged(self):
        """A fully transparent mark has no ink to derive against any
        canvas -- "transparent"/"none" also match the bare-identifier
        shape, but pass through as themselves rather than deferring or
        raising."""
        from dbt_charts.core.compile.resolve.style.palette import mark_ink

        assert mark_ink("transparent", "#ffffff") == "transparent"
        assert mark_ink("none", "#ffffff") == "none"

    def test_garbage_falls_back_unchanged(self):
        from dbt_charts.core.compile.resolve.style.palette import mark_ink

        assert mark_ink("not-a-color!!!", "#ffffff") == "not-a-color!!!"


def test_palette_index_and_spine_use_iterdir_and_read_text_not_glob_or_open(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    no_glob_traversable: Callable[[Path], Any],
) -> None:
    """Regression: building the palette index and loading a spine must use
    iterdir()/read_text(), never glob()/.open() — this test's double
    implements only the importlib.resources.Traversable protocol."""
    from dbt_charts.core.compile.resolve.style import palette as palette_mod

    cat_dir = tmp_path / "categorical"
    cat_dir.mkdir()
    (cat_dir / "zz-fake-b.yml").write_text("name: zz-fake-b\ncolors: ['#111111']\n")
    (cat_dir / "zz-fake-a.yml").write_text("name: zz-fake-a\ncolors: ['#222222']\n")

    monkeypatch.setattr(palette_mod, "_PALETTES_DIR", no_glob_traversable(tmp_path))
    monkeypatch.setattr(palette_mod, "_index", None)
    try:
        names = palette_mod.list_palettes(family="categorical")
        assert names == ["zz-fake-a", "zz-fake-b"]

        spine = palette_mod._load_spine("zz-fake-a")
        assert spine.colors == ["#222222"]
    finally:
        monkeypatch.setattr(palette_mod, "_index", None)
        palette_mod._spine_cache.pop("zz-fake-a", None)
        palette_mod._spine_cache.pop("zz-fake-b", None)
