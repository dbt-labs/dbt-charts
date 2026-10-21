"""Dark-canvas palette forks (neon/midnight) and the neon continuous-color rebind.

These assert the *design properties* the forks must hold on a dark canvas — not
pinned hex values, which are tunable. The contract:
  - sequential dark forks climb luminance recede→pop (low value recedes into the
    dark canvas, high value glows), and the recede end stays above canvas void.
  - diverging dark forks have a receding midpoint (the neutral valley is darker
    than both saturated poles) — the inverse of a light-canvas diverging ramp.
  - neon overrides the inherited light `color_scheme` default for the two families
    that theme a continuous-color default (heatmap, choropleth), so unauthored
    dark dashboards don't render a light-tuned ramp.
"""

from __future__ import annotations

import importlib

import pytest

palette = importlib.import_module("dbt_charts.core.compile.resolve.style.palette")
config = importlib.import_module("dbt_charts.core.compile.config")

_VOID = "#161616"  # neon canvas (dbt-grays.12)

SEQ_DARK = [
    f"dbt-seq-{h}-dark"
    for h in ("amber", "blue", "brown", "gray", "green", "purple", "rust", "teal")
]
DIV_DARK = [
    f"dbt-div-{h}-dark"
    for h in ("blue-red", "coolwarm", "crimson-green", "orange-teal", "sunset")
]


def _luma(hex_str: str) -> float:
    r = int(hex_str[1:3], 16)
    g = int(hex_str[3:5], 16)
    b = int(hex_str[5:7], 16)
    return 0.2126 * r + 0.7152 * g + 0.0722 * b


@pytest.mark.parametrize("name", SEQ_DARK)
def test_sequential_dark_fork_climbs_recede_to_pop(name: str):
    stops = palette.palette(name)
    assert len(stops) == 11
    lumas = [_luma(s) for s in stops]
    # Strictly increasing: recede (dark, low value) → pop (bright, high value).
    assert all(a < b for a, b in zip(lumas, lumas[1:], strict=False)), (
        f"{name} not monotonic"
    )
    # Recede end sits above canvas void (does not merge into the background).
    assert lumas[0] > _luma(_VOID), f"{name} recede end merges into void"


@pytest.mark.parametrize("name", DIV_DARK)
def test_diverging_dark_fork_recedes_at_midpoint(name: str):
    stops = palette.palette(name)
    assert len(stops) == 11
    lumas = [_luma(s) for s in stops]
    mid = lumas[5]
    # The neutral midpoint is the recede point: darker than both poles.
    assert mid < lumas[0] and mid < lumas[-1], f"{name} midpoint does not recede"


@pytest.mark.parametrize("name", SEQ_DARK + DIV_DARK)
def test_table_carve_keeps_stops_legible_for_light_text(name: str):
    """The surface='table' carve must keep only fills that hold the given text.
    With light text (dark theme), every dark fork resolves without raising and
    every surviving stop clears WCAG 4.5:1 against that light text. Covers all
    13 forks, not just the two exercised in the render QA."""
    light = "#fafafa"
    stops = palette.palette(name, surface="table", text_color=light)
    assert stops
    for s in stops:
        assert palette.wcag_contrast(light, s) >= palette._WCAG_TABLE_MIN, (name, s)


# --------------------------------------------------------------------------
# Dark-canvas swap: a pinned light seq/div table palette resolves to its -dark
# twin when the cell text is light (dark canvas). Mirrors the categorical
# light/dark model's <name>-dark convention; the trigger is the text_color
# already threaded into palette(). Light themes (dark text) never swap.
# --------------------------------------------------------------------------

LIGHT_TO_DARK = [
    ("dbt-div-crimson-green", "dbt-div-crimson-green-dark"),
    ("dbt-seq-blue", "dbt-seq-blue-dark"),
]

_NEON_TEXT = "#EDEFF2"  # neon table body text (light)
_LIGHT_THEME_TEXT = "#222222"  # dark table body text (light theme)


@pytest.mark.parametrize(("light_name", "dark_name"), LIGHT_TO_DARK)
def test_pinned_palette_swaps_to_dark_twin_for_light_text(
    light_name: str, dark_name: str
):
    """On a dark canvas (light table text), a pinned seq/div palette resolves to
    its -dark twin, so the neutral holds the light text instead of the light
    palette's near-white neutral failing WCAG and dropping the whole chart."""
    swapped = palette.palette(light_name, surface="table", text_color=_NEON_TEXT)
    twin = palette.palette(dark_name, surface="table", text_color=_NEON_TEXT)
    assert swapped == twin, f"{light_name} did not swap to {dark_name}"
    for s in swapped:
        assert palette.wcag_contrast(_NEON_TEXT, s) >= palette._WCAG_TABLE_MIN


@pytest.mark.parametrize(("light_name", "_dark_name"), LIGHT_TO_DARK)
def test_pinned_palette_keeps_light_variant_for_dark_text(
    light_name: str, _dark_name: str
):
    """Light theme (dark table text): no swap — the light palette resolves as
    before, keeping its light neutral. Guards the byte-identical light path."""
    stops = palette.palette(light_name, surface="table", text_color=_LIGHT_THEME_TEXT)
    mid = stops[len(stops) // 2]
    assert _luma(mid) > 128, f"{light_name} unexpectedly swapped under dark text: {mid}"


def test_no_double_swap_when_already_dark_twin():
    """A pinned -dark palette on a dark canvas is used as-is, never swapped again
    to a nonexistent <name>-dark-dark."""
    stops = palette.palette(
        "dbt-div-crimson-green-dark", surface="table", text_color=_NEON_TEXT
    )
    assert stops
    for s in stops:
        assert palette.wcag_contrast(_NEON_TEXT, s) >= palette._WCAG_TABLE_MIN


def test_swap_only_applies_to_table_surface():
    """The swap is scoped to surface='table'. Chart-fill resolution (surface
    default) of a light palette is unchanged even when text_color is light."""
    fill = palette.palette("dbt-div-crimson-green", text_color=_NEON_TEXT)
    light_fill = palette.palette("dbt-div-crimson-green")
    assert fill == light_fill


def test_neon_overrides_light_continuous_color_scheme():
    """neon must NOT inherit the light-canvas `color_scheme` default for the two
    families that theme a continuous default; asserting difference from the base
    (not a specific scheme name) keeps the exact scheme tunable."""
    neon = config.get_theme_style("neon")
    base = config.get_theme_style("stark")
    # color.gradient.palette is the new color_scheme (renamed)
    neon_hm_palette = (
        neon.charts.heatmap.color.gradient.palette
        if neon.charts.heatmap.color and neon.charts.heatmap.color.gradient
        else None
    )
    base_hm_palette = (
        base.charts.heatmap.color.gradient.palette
        if base.charts.heatmap.color and base.charts.heatmap.color.gradient
        else None
    )
    assert neon_hm_palette != base_hm_palette
    neon_geo_palette = (
        neon.charts.geoshape.color.gradient.palette
        if neon.charts.geoshape.color and neon.charts.geoshape.color.gradient
        else None
    )
    base_geo_palette = (
        base.charts.geoshape.color.gradient.palette
        if base.charts.geoshape.color and base.charts.geoshape.color.gradient
        else None
    )
    assert neon_geo_palette != base_geo_palette
