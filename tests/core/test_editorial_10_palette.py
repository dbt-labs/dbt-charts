"""Regression tests for the editorial-10 categorical palette.

Editorial-10 is the default categorical palette for the clarity and paper
themes. stark keeps vivid-10. The palette ships under the
core defaults catalog (resolved via `dbt_charts.core.compile.resolve.style.palette.palette`)
and the studio rationale lives in
`ai_notes/palette_studio/session7_editorial_categorical/BRIEF.md`.

Regression contracts pinned here, for the base palette:

  1. Stop identity — the 10 hex stops never drift silently. Editing the YAML
     forces an update here, which catches palette-content changes during review.
  2. N=5 Leonardo — the first five stops (the most-common cardinality for
     editorial charts) must remain pairwise-discriminable at ΔE ≥ 11 across
     the three primary CVD modes (deut / prot / trit).
  3. Theme wiring — clarity + paper resolve to editorial-10;
     stark keeps vivid-10.

The four companion files (dark / light / ghost / ink) are gone; every tier
now derives live from the base stops via variant() -- see the folded
regression at the bottom of this file, and test_categorical_variant_grammar.py
/ test_palette_resolver.py::TestVariant for the ordering/contrast invariants
variant() itself guarantees, shared across every palette.
"""

from __future__ import annotations

import importlib.util
import sys

import pytest

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


# Locked editorial-10 stops. If you change the palette YAML, update this list
# (and the BRIEF and the docs guide).
EDITORIAL_10_STOPS = [
    "#40639c",  # 1  blue
    "#779bc9",  # 2  sky
    "#608470",  # 3  green
    "#775770",  # 4  purple
    "#d49656",  # 5  gold
    "#ae6349",  # 6  rust
    "#a0b6b7",  # 7  teal
    "#ad9c7f",  # 8  brown
    "#7a8895",  # 9  gray
    "#5c6668",  # 10 graphite
]


PRIMARY_CVD_MODES = ("deuteranopia", "protanopia", "tritanopia")
LEONARDO_THRESHOLD = 11.0
VISION_WEIGHTS = {
    "normal": 1.0,
    "deuteranopia": 1.0,
    "protanopia": 1.0,
    "tritanopia": 0.15,
    "achromatopsia": 0.05,
}
EDITORIAL_10_PREFIX_CURVE = [
    100.0,
    99.479167,
    99.218750,
    99.375000,
    97.083333,
    97.767857,
    97.042411,
    97.352431,
    96.354167,
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


def test_editorial_10_stops_match_locked_set():
    """Stop identity regression. Drift fails loudly."""
    stops = palette("editorial-10", steps=10)
    assert stops == EDITORIAL_10_STOPS, (
        "editorial-10 palette has drifted from the locked set. If this is "
        "intentional, update EDITORIAL_10_STOPS in this test AND "
        "ai_notes/palette_studio/session7_editorial_categorical/BRIEF.md AND "
        "docs/guides/palettes.md#editorial-10-default-for-editorial-themes to match."
    )


@pytest.mark.parametrize("vision", PRIMARY_CVD_MODES)
def test_n5_prefix_passes_leonardo_under_cvd(vision: str):
    """The first 5 stops are pairwise-discriminable under each primary CVD mode.

    1-5 series is the cardinality range where editorial-10's cycling
    posture matters most. Below ΔE >= 11 (Leonardo) we lose CVD discrimination
    on at least one pair, which would break the editorial-restraint contract.
    """
    stops = EDITORIAL_10_STOPS[:5]
    failing_pairs = []
    for i in range(len(stops)):
        for j in range(i + 1, len(stops)):
            de = _delta_e_under_cvd(stops[i], stops[j], vision)
            if de < LEONARDO_THRESHOLD:
                failing_pairs.append((stops[i], stops[j], round(de, 2)))
    assert not failing_pairs, (
        f"editorial-10 N=5 prefix fails Leonardo under {vision}: "
        f"{failing_pairs}. Lower-N is the cardinality editorial boards "
        "actually hit; a regression here is a ship-blocker."
    )


def test_editorial_10_weighted_prefix_curve_matches_locked_values() -> None:
    actual: list[float] = []
    for prefix_size in range(2, 11):
        passing_weight = 0.0
        for first in range(prefix_size):
            for second in range(first + 1, prefix_size):
                for vision, weight in VISION_WEIGHTS.items():
                    if (
                        _delta_e_under_cvd(
                            EDITORIAL_10_STOPS[first],
                            EDITORIAL_10_STOPS[second],
                            vision,
                        )
                        >= LEONARDO_THRESHOLD
                    ):
                        passing_weight += weight
        denominator = 3.2 * (prefix_size * (prefix_size - 1) / 2)
        actual.append(round(100 * passing_weight / denominator, 6))

    assert actual == EDITORIAL_10_PREFIX_CURVE


def test_clarity_themes_use_editorial_10():
    """Theme cascade: clarity + paper resolve to editorial-10."""
    clarity_cat = get_theme_style("clarity").charts.color.categorical
    paper_cat = get_theme_style("paper").charts.color.categorical
    assert clarity_cat is not None and clarity_cat.palette is not None
    assert paper_cat is not None and paper_cat.palette is not None
    clarity = clarity_cat.palette
    paper = paper_cat.palette
    assert clarity == EDITORIAL_10_STOPS, (
        f"clarity theme no longer resolves to editorial-10. Got: {clarity[:3]}..."
    )
    assert paper == EDITORIAL_10_STOPS, (
        f"paper theme no longer resolves to editorial-10. Got: {paper[:3]}..."
    )


def test_clarity_themes_rebind_category_role():
    """Theme-relative role refs must follow the theme's palette family.

    `style.palettes` binds the `category` role that theme-portable refs
    like `category[2]` or `category[2].dark` resolve through. The _base
    cascade root binds it to the vivid-10 family; editorial-voiced themes
    must rebind it to editorial-10, otherwise a board authored with role
    refs keeps stark's colors after a theme switch. Every literal tier
    (dark/light/pale/deep) now derives live from whichever family `category`
    is bound to (variant()) -- no companion role to rebind alongside it.
    """
    for theme in ("clarity", "paper"):
        bound = get_theme_style(theme).palettes
        assert bound["category"] == "editorial-10", (
            f"{theme} theme leaves the `category` role on "
            f"{bound['category']!r} — role refs won't follow the theme."
        )


def test_structural_root_keeps_category_role_family():
    """stark (and the cascade root) stay on the vivid-10 family."""
    bound = get_theme_style("stark").palettes
    assert bound["category"] == "vivid-10"


def test_geoshape_basemap_fill_comes_from_the_scaffold_not_a_data_palette():
    """Basemap terrain is chrome, so it comes from the scaffold ladder.

    A categorical swatch is chosen to hold its own against other series;
    a basemap has the opposite job — it must recede behind the marks drawn
    on it. Four light themes previously sourced this from a categorical
    palette, and the darkest of them rendered a mid-tone mark effectively
    invisible against the terrain.

    Each theme takes its terrain from the scaffold matching its canvas.
    Cream sits a step lighter than the gray themes on purpose: the warm
    ladder is tinted at every step, so the value that reads as neutral
    ground on a gray canvas reads as a tan continent on cream.
    """
    gray_step = palette("dbt-grays", steps=12)[4]
    cream_step = palette("dbt-creams", steps=12)[2]
    expected = {
        "stark": gray_step,
        "clarity": gray_step,
        "paper": cream_step,
        "vivid": gray_step,
    }
    for theme, want in expected.items():
        fill = get_theme_style(theme).charts.marks.geoshape.fill
        assert fill == want, (
            f"{theme} basemap fill is {fill!r}; expected the scaffold step "
            f"{want!r}. Basemap chrome must not come from a categorical "
            f"data palette."
        )


def test_structural_root_theme_still_uses_vivid_10():
    """Regression guard: stark keeps vivid-10 unchanged.

    The structural-root theme (stark, opt-in for the stripped-back look)
    continues to use vivid-10. The shipped default theme uses editorial-10
    (covered by test_clarity_themes_use_editorial_10 above).
    """
    stark_cat = get_theme_style("stark").charts.color.categorical
    assert stark_cat is not None and stark_cat.palette is not None
    # stark must differ from editorial-10 slot 1 (distinct palette identity).
    assert stark_cat.palette[0] != EDITORIAL_10_STOPS[0], (
        "stark theme palette slot 1 matches editorial-10 slot 1 — "
        "stark must keep vivid-10, not editorial-10."
    )


# ----------------------------------------------------------------------------
# Literal tiers — folded onto variant(), no companion file
# ----------------------------------------------------------------------------
# The dark/light/pale/deep tiers used to be four separate companion files
# (editorial-10-dark.yml etc.), each hex-locked here individually (~300
# lines, one assertion per slot per tier). They are gone: every tier now
# derives live from EDITORIAL_10_STOPS via variant() (compile/resolve/style/
# palette.py). Ordering, contrast, and separation invariants for variant()
# itself are pinned generically, once, for every shipped and user palette,
# in tests/core/test_categorical_variant_grammar.py and
# tests/core/test_palette_resolver.py::TestVariant -- editorial-10 does not
# need its own copy of those. What editorial-10 still owns is proving its
# own stops feed variant() correctly.


def test_editorial_10_tiers_equal_variant_of_the_locked_stops():
    from dbt_charts.core.compile.resolve.style.palette import variant

    for word in ("dark", "light", "pale", "deep"):
        assert palette(f"editorial-10.{word}") == [
            variant(stop, word) for stop in EDITORIAL_10_STOPS
        ]
