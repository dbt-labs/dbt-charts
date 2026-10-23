"""Tests for theme-driven single-series mark color.

A non-layered chart with no color encoding paints its mark with the
theme's `style.charts.single_series_palette[0]`. Chart-level `style.color`
still wins. Layered and multi-metric charts switch back to the
categorical palette (color flows through encoding.color).
"""

from __future__ import annotations

import pytest

from dbt_charts.core.compile.config import (
    get_theme_style,
)
from dbt_charts.core.compile.resolve.style.board import (
    resolve_style,
)

# ── Theme corpus: every shipped theme populates single_series_palette ──────────


@pytest.mark.parametrize(
    "theme_name",
    ["stark", "clarity", "paper", "vivid", "neon"],
)
def test_every_shipped_theme_populates_single_series_palette(theme_name):
    """The theme corpus smoke test — every built-in theme resolves a
    non-empty single_series_palette via the cascade."""
    charts = resolve_style(get_theme_style(theme_name)).chart_defaults
    assert isinstance(charts.single_series_palette, list)
    assert len(charts.single_series_palette) >= 1, (
        f"theme {theme_name!r} has empty single_series_palette"
    )
    assert all(
        isinstance(stop, str) and stop.strip() for stop in charts.single_series_palette
    )


# ── Render path: single-series mark gets the theme value ─────────────────────


# ── Override: chart-level style.color still wins ─────────────────────────────


# ── Multi-series: encoding owns color, single_series_palette suppressed ────────


# ── Layered (multi-metric): switches back to multi-series mode ───────────────


# ── Rhythm-palette slot: ResolvedChart.rhythm_slot picks the palette slot ──


# ── Line + visible-point overlay: dot color tracks the line ─────────────────
#
# Regression for the bug where the fg-point on a single-series line chart
# (halo path) fell back to ``config.range.category[0]`` because the chart
# has no ``encoding.color`` for VL to inherit from. The renderer injects
# ``mark.color = single_color`` on the fg-point; VL's color shortcut routes
# the value via the ``filled`` boolean — to fill when filled=true (the
# theme default → solid dot in line color), or to stroke when filled=false
# (knockout opt-in → ring in line color, theme's ``point.fill:
# theme.background`` paints the board).


# ── Emitters that used to drop the resolved ink (VL default #4c78a8) ──────────
#
# Histogram, point/bubble map and the area hover-target point each emitted a
# mark with no fill, so Vega-Lite painted its own stock blue. These pin that the
# resolved value reaches the emitted spec, using a distinctive palette rather
# than a theme literal so a palette retune can't silently satisfy them.
