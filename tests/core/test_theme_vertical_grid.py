"""Vertical (x) gridlines are a per-family decision, not a per-theme one.

`_base` turns the grid on for both axes and opts exactly three families out of the
vertical grid — bar, histogram and heatmap — because a gridline running parallel to
the marks is noise. Line, area and scatter keep theirs.

These tests assert the *relationships* the design encodes, never the widths or colors
themselves (those are tunable theme values, and pinning them is banned — see
`dbt-charts/AGENTS.md`):

  * every theme draws a vertical grid on line/area, and none draws one on bar;
  * where a theme differentiates the two axes, the vertical grid is a different
    color from the horizontal one;
  * scatter is quant x quant, so both of its axes match in color *and* width;
  * the vertical grid clears a legibility floor against the canvas, yet stays exactly
    one scaffold rung quieter than the horizontal grid where the two differ;
  * a vertical tick wears its gridline's color, so the two read as one mark.
"""

from __future__ import annotations

from collections.abc import Iterator

import pytest

from dbt_charts.core.colors import wcag_contrast
from dbt_charts.core.compile.config import (
    get_theme_style,
    reset_config,
    user_facing_theme_names,
)
from dbt_charts.core.compile.models.chart.normalized import (
    AreaChart,
    BarChart,
    LineChart,
    ScatterChart,
)
from dbt_charts.core.compile.models.query.normalized import SqlQuery
from dbt_charts.core.compile.resolve.style.axis_cascade import resolved_axis_style
from dbt_charts.core.compile.resolve.style.board import resolve_chart_style_context
from dbt_charts.core.compile.resolve.style.palette import palette
from dbt_charts.core.render.chart.vega_lite import generate_vega_lite_spec

# The themes that thin every gridline to a hairline. No width headroom left to
# separate the axes, so they separate them by contrast instead. Hardcoded because
# it is a design subset, not a roster — a new theme is not automatically hairline.
HAIRLINE_THEMES = ["vivid", "neon"]
# The canonical roster, so a newly added theme is swept in rather than escaping.
ALL_THEMES = user_facing_theme_names()

# Below this a hairline on the canvas stops reading as a line. The light themes
# clear it by well under one scaffold rung, so this also pins the vertical grid
# against slipping back to the faintest rung.
_VERTICAL_GRID_MIN_CONTRAST = 1.25

_TEMPORAL = [{"month": "2024-01-01", "rev": 100}, {"month": "2024-02-01", "rev": 200}]
_QUANT = [{"spend": 10, "rev": 100}, {"spend": 20, "rev": 260}]


@pytest.fixture(autouse=True)
def _reset() -> Iterator[None]:
    reset_config()
    yield
    reset_config()


def _grid(chart, data: list[dict], channel: str) -> dict:
    """Emitted grid properties for one axis of one chart.

    The theme comes from DCT_DEFAULT_THEME, which each test sets — not from an
    argument here.
    """
    axis = (
        generate_vega_lite_spec(chart, data, width=400)
        .get("encoding", {})
        .get(channel, {})
        .get("axis", {})
    )
    return {k: v for k, v in axis.items() if k.startswith("grid")}


def _query() -> SqlQuery:
    return SqlQuery(sql="SELECT 1", source="src")


def _line() -> LineChart:
    return LineChart(
        id="t", type="line", x="month", y="rev", query=_query(), query_name="q"
    )


def _area() -> AreaChart:
    return AreaChart(
        id="t", type="area", x="month", y="rev", query=_query(), query_name="q"
    )


def _bar() -> BarChart:
    return BarChart(
        id="t", type="bar", x="month", y="rev", query=_query(), query_name="q"
    )


def _scatter() -> ScatterChart:
    return ScatterChart(
        id="t", type="scatter", x="spend", y="rev", query=_query(), query_name="q"
    )


@pytest.mark.parametrize("theme", ALL_THEMES)
@pytest.mark.parametrize(
    ("chart_factory", "data"), [(_line, _TEMPORAL), (_area, _TEMPORAL)]
)
def test_temporal_families_draw_a_vertical_grid(
    theme, chart_factory, data, monkeypatch
):
    """Line and area keep the vertical grid `_base` gives them, in every theme."""
    monkeypatch.setenv("DCT_DEFAULT_THEME", theme)
    assert _grid(chart_factory(), data, "x")["grid"] is True


@pytest.mark.parametrize("theme", ALL_THEMES)
def test_bar_draws_no_vertical_grid(theme, monkeypatch):
    """The bar family opt-out survives in every theme — gridlines parallel to bars."""
    monkeypatch.setenv("DCT_DEFAULT_THEME", theme)
    assert _grid(_bar(), _TEMPORAL, "x")["grid"] is False


@pytest.mark.parametrize("theme", HAIRLINE_THEMES)
def test_hairline_themes_separate_the_axes_by_contrast(theme, monkeypatch):
    """A hairline theme has no width headroom, so color must carry the hierarchy.

    Both axes sit at the same width here by design; if the two grids also shared a
    color there would be no hierarchy left at all.
    """
    monkeypatch.setenv("DCT_DEFAULT_THEME", theme)
    x = _grid(_line(), _TEMPORAL, "x")
    y = _grid(_line(), _TEMPORAL, "y")
    assert x["gridColor"] != y["gridColor"]


@pytest.mark.parametrize("theme", ALL_THEMES)
def test_scatter_grid_has_axis_parity(theme, monkeypatch):
    """Quant x quant: neither axis is subordinate, so both grids match exactly."""
    monkeypatch.setenv("DCT_DEFAULT_THEME", theme)
    x = _grid(_scatter(), _QUANT, "x")
    y = _grid(_scatter(), _QUANT, "y")
    assert x["grid"] is True
    assert x["gridColor"] == y["gridColor"]
    assert x["gridWidth"] == y["gridWidth"]


def _resolved_axes(theme: str) -> tuple[str, str, str, str]:
    """(canvas, vertical grid, vertical tick, horizontal grid) colors for a line."""
    style = get_theme_style(theme)
    ctx = resolve_chart_style_context(style)
    x = resolved_axis_style(ctx, "axis_x", "temporal", chart_type="line", labeled=False)
    y = resolved_axis_style(
        ctx, "axis_y", "quantitative", chart_type="line", labeled=False
    )
    return style.background, x.grid.color, x.ticks.color, y.grid.color


def _rung(hex_color: str) -> tuple[str, int]:
    """(ladder, index) of a chrome color; raises if it is on no scaffold ladder."""
    for name in ("dbt-grays", "dbt-creams"):
        ladder = [c.upper() for c in palette(name)]
        if hex_color.upper() in ladder:
            return name, ladder.index(hex_color.upper())
    raise AssertionError(f"{hex_color} is not a scaffold rung")


@pytest.mark.parametrize("theme", ALL_THEMES)
def test_vertical_grid_clears_the_contrast_floor(theme):
    """However quiet the vertical grid is, it still reads against the canvas."""
    canvas, x_grid, _, _ = _resolved_axes(theme)
    assert wcag_contrast(x_grid, canvas) >= _VERTICAL_GRID_MIN_CONTRAST


@pytest.mark.parametrize("theme", ALL_THEMES)
def test_vertical_grid_sits_one_rung_below_the_horizontal(theme):
    """Where color separates the axes, one rung is the whole gap.

    Quieter means less contrast with the canvas, on light and dark themes alike.
    """
    canvas, x_grid, _, y_grid = _resolved_axes(theme)
    if x_grid == y_grid:
        pytest.skip(f"{theme} separates its axes by width, not color")
    x_ladder, x_rung = _rung(x_grid)
    y_ladder, y_rung = _rung(y_grid)
    assert x_ladder == y_ladder
    assert abs(x_rung - y_rung) == 1
    assert wcag_contrast(x_grid, canvas) < wcag_contrast(y_grid, canvas)


@pytest.mark.parametrize("theme", ALL_THEMES)
def test_vertical_tick_wears_its_gridline_color(theme):
    """A tick and its gridline mark the same value, so they read as one mark."""
    _, x_grid, x_tick, _ = _resolved_axes(theme)
    assert x_tick == x_grid
