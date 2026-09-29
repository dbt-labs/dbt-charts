"""Slot-order regression tests for the vivid-10 categorical palette.

vivid-10 is the categorical family for the `stark`, `vivid`, and `neon` themes.
Its derived tiers (dark/light/pale/deep) are covered in
`test_palette_resolver.py` — this file does not repeat any of that.

What it pins is the base palette's own contracts:

  1. Stop identity — the ten base stops.
  2. Slot *order* — the CVD worst-case curve as the first N stops accumulate.
     Stop identity cannot catch a reorder; the set is unchanged under any
     permutation. This curve is what fails.
  3. The neon contrast floor that decides the order — the two stops that fall
     under 3:1 on a near-black canvas are the two placed past slot 7.
  4. Theme wiring for all three vivid-family themes.
"""

from __future__ import annotations

import importlib.util
import itertools
import sys

import pytest

from dbt_charts.core.colors import wcag_contrast as _wcag_contrast
from dbt_charts.core.compile.config import get_theme_style
from dbt_charts.core.compile.resolve.style.palette import palette

from .._paths import DBT_CHARTS_DIR as _DBT_CHARTS_DIR

# Load palette_deltae_checker as a module (it's a script, not a package).
_checker_path = _DBT_CHARTS_DIR / "scripts" / "palette_deltae_checker.py"
_spec = importlib.util.spec_from_file_location("palette_deltae_checker", _checker_path)
assert _spec is not None and _spec.loader is not None
_checker = importlib.util.module_from_spec(_spec)
sys.modules["palette_deltae_checker"] = _checker
_spec.loader.exec_module(_checker)


# Locked vivid-10 stops. Edit in lock-step with vivid-10.yml and the swatch
# strip in docs/guides/palettes.md.
VIVID_10_STOPS = [
    "#0073c2",  # 1  blue
    "#00c8ee",  # 2  cyan
    "#00ad75",  # 3  green
    "#e1a500",  # 4  gold
    "#da5a23",  # 5  orange
    "#a86c9d",  # 6  purple
    "#6a8f47",  # 7  moss
    "#7a5531",  # 8  brown
    "#949daa",  # 9  gray
    "#505b6b",  # 10 charcoal
]

PRIMARY_CVD_MODES = ("deuteranopia", "protanopia", "tritanopia")
LEONARDO_THRESHOLD = 11.0

# Floor this palette's purple/moss retune exists to protect: worst-case
# CVD ΔE must not drop below this for any series count past the gate.
CVD_FLOOR_PAST_GATE = 9.0

# Neon's canvas (dbt-grays.void) — same constant as test_dark_palette_forks.
_NEON_VOID = "#161616"
# WCAG floor for a non-text graphical object.
_GRAPHICAL_CONTRAST_FLOOR = 3.0
_CLEARS_NEON_THROUGH_SLOT = 7

# Worst-case CIEDE2000 across the three primary CVD modes as the first N stops
# accumulate, N=2..10.
#
# Only four pairs sit under the Leonardo gate: orange+moss 9.01 and purple+gray
# 9.18 (deuteranopia), blue+purple 9.02 and green+moss 9.99 (protanopia). The
# N=10 floor of 9.01 is a property of the ten hexes and holds under any order.
# What the order controls is how long the curve stays above 11: with brown,
# charcoal, and gray held past slot 7, slots 1-7 are the seven hues, and purple
# and moss each have a sub-11 partner among the other five, so placing them at
# 6 and 7 holds 11.97 through N=5.
VIVID_10_CVD_PREFIX_CURVE = [
    23.27,  # N=2   blue-cyan (tritanopia)
    11.97,  # N=3   cyan-green (tritanopia)
    11.97,  # N=4
    11.97,  # N=5
    9.02,  # N=6   blue-purple (protanopia)
    9.01,  # N=7   orange-moss (deuteranopia)
    9.01,  # N=8
    9.01,  # N=9
    9.01,  # N=10
]


def _delta_e_under_cvd(hex_a: str, hex_b: str, mode: str) -> float:
    rgb_a = _checker.hex_to_rgb01(hex_a)
    rgb_b = _checker.hex_to_rgb01(hex_b)
    sim_a = _checker.simulate_vision(rgb_a, mode)
    sim_b = _checker.simulate_vision(rgb_b, mode)
    return _checker.delta_e_ciede2000(
        _checker.linear_rgb_to_lab(sim_a),
        _checker.linear_rgb_to_lab(sim_b),
    )


def test_vivid_10_stops_match_locked_set():
    """Stop identity regression. Drift fails loudly."""
    assert palette("vivid-10", steps=10) == VIVID_10_STOPS, (
        "vivid-10 has drifted from the locked set. If intentional, update "
        "VIVID_10_STOPS here AND docs/guides/palettes.md."
    )


def test_vivid_10_cvd_prefix_curve_matches_locked_values():
    """Slot order regression: worst-case CVD ΔE as the prefix grows.

    Stop identity does not pin order — a reorder leaves the set untouched.
    This curve is what fails when slots move.
    """
    actual = [
        round(
            min(
                _delta_e_under_cvd(VIVID_10_STOPS[i], VIVID_10_STOPS[j], mode)
                for i, j in itertools.combinations(range(prefix_size), 2)
                for mode in PRIMARY_CVD_MODES
            ),
            2,
        )
        for prefix_size in range(2, 11)
    ]
    assert actual == VIVID_10_CVD_PREFIX_CURVE


def test_vivid_10_cvd_floor_holds_past_leonardo_gate():
    """Inequality gate for N=6..10: worst-case CVD ΔE never drops below the floor.

    The prefix curve test above pins exact values and catches drift; this is
    the semantic floor those values must satisfy — the region this palette's
    purple/moss retune exists to protect, and the acceptance bar a reverse of
    that retune would fail.
    """
    for prefix_size in range(6, 11):
        worst_value, worst_pair, worst_mode = min(
            (
                (
                    _delta_e_under_cvd(VIVID_10_STOPS[i], VIVID_10_STOPS[j], mode),
                    (i, j),
                    mode,
                )
                for i, j in itertools.combinations(range(prefix_size), 2)
                for mode in PRIMARY_CVD_MODES
            ),
            key=lambda item: item[0],
        )
        assert worst_value >= CVD_FLOOR_PAST_GATE, (
            f"vivid-10 N={prefix_size} worst-case CVD ΔE is {worst_value:.2f}, under "
            f"the {CVD_FLOOR_PAST_GATE} floor. Binding pair: "
            f"{VIVID_10_STOPS[worst_pair[0]]}+{VIVID_10_STOPS[worst_pair[1]]} under "
            f"{worst_mode}."
        )


@pytest.mark.parametrize("vision", PRIMARY_CVD_MODES)
def test_n5_prefix_passes_leonardo_under_cvd(vision: str):
    """The first 5 stops stay pairwise-discriminable under each primary CVD mode.

    1-5 series is the cardinality most charts hit; below ΔE >= 11 (Leonardo) at
    least one pair loses discrimination.
    """
    stops = VIVID_10_STOPS[:5]
    failing = [
        (stops[i], stops[j], round(_delta_e_under_cvd(stops[i], stops[j], vision), 2))
        for i, j in itertools.combinations(range(len(stops)), 2)
        if _delta_e_under_cvd(stops[i], stops[j], vision) < LEONARDO_THRESHOLD
    ]
    assert not failing, f"vivid-10 N=5 prefix fails Leonardo under {vision}: {failing}."


@pytest.mark.parametrize("slot", range(_CLEARS_NEON_THROUGH_SLOT))
def test_vivid_10_early_slots_clear_neon_contrast_floor(slot: int):
    """Slots 1-7 are legible as marks on neon's near-black canvas.

    This is the ordering constraint, asserted rather than assumed: a reorder
    that pulls a low-contrast stop forward fails here.
    """
    contrast = _wcag_contrast(VIVID_10_STOPS[slot], _NEON_VOID)
    assert contrast >= _GRAPHICAL_CONTRAST_FLOOR, (
        f"vivid-10 slot {slot + 1} ({VIVID_10_STOPS[slot]}) is {contrast:.2f}:1 "
        f"against the neon canvas, under the {_GRAPHICAL_CONTRAST_FLOOR}:1 "
        "floor. The low-contrast stops belong past slot 7."
    )


def test_vivid_10_late_slots_are_the_low_contrast_ones():
    """The converse: brown and charcoal really are the stops that fail.

    Without this, the floor above could be satisfied by a palette with no
    low-contrast stops at all, losing the reason the order is what it is.
    """
    failing = [
        stop
        for stop in VIVID_10_STOPS
        if _wcag_contrast(stop, _NEON_VOID) < _GRAPHICAL_CONTRAST_FLOOR
    ]
    assert failing == [VIVID_10_STOPS[7], VIVID_10_STOPS[9]], (
        "expected brown (slot 8) and charcoal (slot 10) to be the only stops "
        f"under {_GRAPHICAL_CONTRAST_FLOOR}:1 on neon; got {failing}."
    )


@pytest.mark.parametrize("theme", ["stark", "vivid", "neon"])
def test_vivid_family_themes_use_vivid_10(theme: str):
    """Theme cascade: all three vivid-voiced themes resolve to vivid-10."""
    cat = get_theme_style(theme).charts.color.categorical
    assert cat is not None and cat.palette is not None
    assert cat.palette == VIVID_10_STOPS, (
        f"{theme} theme no longer resolves to vivid-10. Got: {cat.palette[:3]}..."
    )
