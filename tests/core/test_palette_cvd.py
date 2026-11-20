"""CVD regression tests for M2 sequential + diverging palettes.

Uses scripts/palette_deltae_checker.py primitives to simulate each palette
under deuteranopia / protanopia / tritanopia and assert adjacent-pair CIEDE2000
separation is above a conservative regression threshold. A failing test means
a spine edit has made the palette less CVD-safe than when it was shipped.
"""

from __future__ import annotations

import importlib.util
import sys

import pytest

from dbt_charts.core.compile.models.palette import PALETTE_WARN_RECOMMENDATIONS
from dbt_charts.core.compile.resolve.style.palette import list_palettes, palette

from .._paths import DBT_CHARTS_DIR

# Load palette_deltae_checker as a module (the file is a script, not a package).
_checker_path = DBT_CHARTS_DIR / "scripts" / "palette_deltae_checker.py"
_spec = importlib.util.spec_from_file_location("palette_deltae_checker", _checker_path)
assert _spec is not None and _spec.loader is not None
_checker = importlib.util.module_from_spec(_spec)
sys.modules["palette_deltae_checker"] = _checker
_spec.loader.exec_module(_checker)


# Conservative regression threshold: adjacent-pair ΔE under any CVD condition
# must be at least 3.0. This is well below the Leonardo discrimination threshold
# (11.0) which targets categorical palettes; sequential adjacent stops are
# expected to be close. 3.0 is enough to catch obvious regressions without
# false-flagging the naturally-close pairs in sequential/diverging.
ADJACENT_MIN_DELTA_E = 3.0
VISION_MODES = ("deuteranopia", "protanopia", "tritanopia")


def _adjacent_pair_delta_e(hex_a: str, hex_b: str, vision_type: str) -> float:
    """Minimum ΔE CIEDE2000 under the given CVD simulation."""
    rgb_a = _checker.hex_to_rgb01(hex_a)
    rgb_b = _checker.hex_to_rgb01(hex_b)
    sim_a = _checker.simulate_vision(rgb_a, vision_type)
    sim_b = _checker.simulate_vision(rgb_b, vision_type)
    lab_a = _checker.linear_rgb_to_lab(sim_a)
    lab_b = _checker.linear_rgb_to_lab(sim_b)
    return _checker.delta_e_ciede2000(lab_a, lab_b)


# Warned palettes (RdYlGn, parula) are drawn as written and are not held to the
# house CVD floor; RdYlGn is warned for exactly that reason.
_SEQUENTIAL = [
    n
    for n in list_palettes(family="sequential")
    if n not in PALETTE_WARN_RECOMMENDATIONS
]
_DIVERGING = [
    n
    for n in list_palettes(family="diverging")
    if n not in PALETTE_WARN_RECOMMENDATIONS
]


class TestSequentialCVD:
    @pytest.mark.parametrize("name", _SEQUENTIAL)
    @pytest.mark.parametrize("vision", VISION_MODES)
    def test_n5_adjacent_pairs_pass_threshold(self, name: str, vision: str):
        """At N=5, every adjacent pair should remain distinguishable under CVD.

        Sequential palettes intentionally have small steps; pure-gray
        dbt-seq-gray has zero chroma so CVD doesn't shift separation, and
        this serves as a sanity floor.
        """
        stops = palette(name, steps=5)
        worst = min(
            _adjacent_pair_delta_e(stops[i], stops[i + 1], vision)
            for i in range(len(stops) - 1)
        )
        assert worst >= ADJACENT_MIN_DELTA_E, (
            f"{name} N=5 under {vision}: worst adjacent ΔE = {worst:.2f} "
            f"(threshold {ADJACENT_MIN_DELTA_E}); spine edit may have "
            "regressed CVD safety."
        )


class TestDivergingCVD:
    @pytest.mark.parametrize("name", _DIVERGING)
    @pytest.mark.parametrize("vision", VISION_MODES)
    def test_extremes_distinguishable(self, name: str, vision: str):
        """Endpoints of a diverging palette must be very distinct under CVD.

        11.0 is the Leonardo discrimination threshold — the same bound used
        in Session 4's R8 discipline for danger/success. dbt-div-crimson-green
        was engineered to squeeze through at ~13.7 under deuteranopia; other
        diverging palettes have endpoint ΔE >= 19 under every primary.
        """
        stops = palette(name, steps=11)
        de = _adjacent_pair_delta_e(stops[0], stops[-1], vision)
        assert de >= 11.0, (
            f"{name} endpoints under {vision}: ΔE = {de:.2f}; "
            "diverging extremes should be >= 11 (Leonardo threshold)."
        )

    @pytest.mark.parametrize("name", _DIVERGING)
    @pytest.mark.parametrize("vision", VISION_MODES)
    def test_n5_adjacent_pairs_pass_threshold(self, name: str, vision: str):
        stops = palette(name, steps=5)
        worst = min(
            _adjacent_pair_delta_e(stops[i], stops[i + 1], vision)
            for i in range(len(stops) - 1)
        )
        assert worst >= ADJACENT_MIN_DELTA_E, (
            f"{name} N=5 under {vision}: worst adjacent ΔE = {worst:.2f} "
            f"(threshold {ADJACENT_MIN_DELTA_E})"
        )


class TestToneR8:
    """Session 4 R8: negative.solid ↔ positive.solid must be CVD-distinguishable."""

    @pytest.mark.parametrize("vision", VISION_MODES)
    def test_negative_vs_positive_under_cvd(self, vision: str):
        from dbt_charts.core.compile.resolve.style.palette import color

        negative = color("negative.solid")
        positive = color("positive.solid")
        de = _adjacent_pair_delta_e(negative, positive, vision)
        # R8 threshold: 11.0 (Leonardo). Pass under every CVD primary.
        assert de >= 11.0, (
            f"R8 violation: negative.solid vs positive.solid under {vision} "
            f"ΔE = {de:.2f} (threshold 11.0). CVD-fail; do not ship."
        )
