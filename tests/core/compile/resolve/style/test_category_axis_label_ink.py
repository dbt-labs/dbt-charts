"""Category axis labels print in ink; time axis labels stay muted.

``charts.axis_band`` is the theme's slot for band (category) axes. Themes set it
and ``axis_quantitative`` to ink and leave ``axis.labels`` muted, so a temporal
axis (which takes no type slot) falls through to muted. Colors are compared, not
pinned: the contract is "category == numeric ruler != time".
"""

from __future__ import annotations

from typing import Any

import pytest

from dbt_charts.core.compile.config import get_theme_style
from dbt_charts.core.compile.models.chart.normalized import (
    BarChart,
    HeatmapChart,
    LineChart,
)
from dbt_charts.core.compile.models.query.normalized import SqlQuery
from dbt_charts.core.compile.models.style.authored import StylePatch
from dbt_charts.core.compile.resolve import resolve
from dbt_charts.core.compile.resolve.style.axis_cascade import _merge_axis_cascade
from dbt_charts.core.compile.resolve.style.board import resolve_chart_style_context

_THEMES = ["clarity", "paper", "vivid", "neon"]
_PLACES = [{"place": "Hollis Hill", "n": 3}, {"place": "Bramble Row", "n": 5}]
_MONTHS = [
    {"month": f"2026-0{m}-01", "n": m} for m in (1, 2, 3)
]  # ISO date strings classify temporal
_YEARS = [{"year": 2021, "n": 1}, {"year": 2022, "n": 2}, {"year": 2023, "n": 3}]
_HEAT = [
    {"place": "Hollis Hill", "month": "2026-01-01", "n": 1},
    {"place": "Bramble Row", "month": "2026-02-01", "n": 2},
]


def _board(theme: str, patch: dict[str, Any] | None = None):
    return resolve_chart_style_context(
        get_theme_style(theme), StylePatch.model_validate(patch) if patch else None
    )


def _heatmap() -> HeatmapChart:
    return HeatmapChart(
        id="h",
        type="heatmap",
        x="month",
        y="place",
        query=SqlQuery(sql="SELECT 1", source="t"),
        query_name="q",
    )


def _color(axis) -> str:
    return axis.labels.font.color


@pytest.mark.parametrize("theme", _THEMES)
def test_category_axis_is_ink_like_the_ruler_and_time_axis_is_muted(
    theme: str,
) -> None:
    board = _board(theme)
    bars = resolve(
        BarChart(id="a", type="bar", x="place", y="n"),
        _PLACES,
        chart_style_context=board,
    ).style
    months = resolve(
        BarChart(id="b", type="bar", x="month", y="n"),
        _MONTHS,
        chart_style_context=board,
    ).style
    years = resolve(
        BarChart(id="c", type="bar", x="year", y="n"), _YEARS, chart_style_context=board
    ).style
    line = resolve(
        LineChart(id="d", type="line", x="month", y="n"),
        _MONTHS,
        chart_style_context=board,
    ).style
    ink = _color(bars.axis_y)
    assert _color(bars.axis_x) == ink
    muted = _color(months.axis_x)
    assert muted != ink
    assert _color(years.axis_x) == muted
    assert _color(line.axis_x) == muted
    assert _color(line.axis_y) == ink


@pytest.mark.parametrize("theme", _THEMES)
def test_heatmap_name_axis_is_ink_and_temporal_axis_is_muted(theme: str) -> None:
    board = _board(theme)
    heat = resolve(
        _heatmap(),
        _HEAT,
        chart_style_context=board,
    ).style
    ink = _color(
        resolve(
            BarChart(id="a", type="bar", x="place", y="n"),
            _PLACES,
            chart_style_context=board,
        ).style.axis_y
    )
    assert _color(heat.axis_y) == ink
    assert _color(heat.axis_x) != ink


def test_charts_axis_band_applies_to_band_axes_only() -> None:
    board = _board(
        "clarity", {"charts": {"axis_band": {"labels": {"font": {"color": "#123456"}}}}}
    )
    place = resolve(
        BarChart(id="a", type="bar", x="place", y="n"),
        _PLACES,
        chart_style_context=board,
    ).style
    month = resolve(
        BarChart(id="b", type="bar", x="month", y="n"),
        _MONTHS,
        chart_style_context=board,
    ).style
    assert _color(place.axis_x) == "#123456"
    assert _color(month.axis_x) != "#123456"
    assert _color(place.axis_y) != "#123456"


def test_family_axis_band_beats_charts_axis_band() -> None:
    board = _board(
        "clarity",
        {
            "charts": {
                "axis_band": {"labels": {"font": {"color": "#111111"}}},
                "bar": {"axis_band": {"labels": {"font": {"color": "#222222"}}}},
            }
        },
    )
    place = resolve(
        BarChart(id="a", type="bar", x="place", y="n"),
        _PLACES,
        chart_style_context=board,
    ).style
    assert _color(place.axis_x) == "#222222"


def test_family_axis_band_still_styles_a_temporal_heatmap_axis() -> None:
    board = _board(
        "clarity",
        {
            "charts": {
                "heatmap": {"axis_band": {"labels": {"font": {"color": "#222222"}}}}
            }
        },
    )
    heat = resolve(
        _heatmap(),
        _HEAT,
        chart_style_context=board,
    ).style
    assert _color(heat.axis_x) == "#222222"
    assert _color(heat.axis_y) == "#222222"


def _band_position(
    patch: dict[str, Any], chart_type: str, *, temporal_band: bool
) -> float | None:
    _, position = _merge_axis_cascade(
        _board("clarity", patch),
        "axis_x",
        "nominal",
        chart_type=chart_type,
        labeled=False,
        temporal_band=temporal_band,
    )
    return position


def test_charts_axis_band_position_is_honored_on_a_nominal_axis() -> None:
    patch = {"charts": {"axis_band": {"band_position": 0.4}}}
    assert _band_position(patch, "bar", temporal_band=False) == 0.4


def test_family_axis_band_position_beats_charts_axis_band_position() -> None:
    patch = {
        "charts": {
            "axis_band": {"band_position": 0.4},
            "bar": {"axis_band": {"band_position": 0.3}},
        }
    }
    assert _band_position(patch, "bar", temporal_band=False) == 0.3


def test_charts_axis_band_position_is_suppressed_on_a_temporal_heatmap_axis() -> None:
    patch = {"charts": {"axis_band": {"band_position": 0.4}}}
    assert _band_position(patch, "heatmap", temporal_band=False) == 0.4
    assert _band_position(patch, "heatmap", temporal_band=True) is None
