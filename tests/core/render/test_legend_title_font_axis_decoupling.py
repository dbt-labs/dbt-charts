"""Regression: the color/size legend title must not read the axis title's font.

bar (histogram / vertical / horizontal), line, and scatter built the color
legend title with `field_display_text(..., font=<axis title font>)` instead
of `chart.legend.title.font` — pie, geo, and heatmap's pattern. An author
setting `style.axis_x.title.font.case` (or, for scatter, `axis_y`) got their
legend title silently re-cased along with the axis title.

These tests pin two properties per family:

  1. An axis-title-font override leaves the legend title exactly as it
     renders with no override at all (decoupled).
  2. A `legend.title.font.case` override DOES change the legend title
     (still wired, not accidentally orphaned by the fix).
"""

from __future__ import annotations

from typing import Any

import pytest

from dbt_charts.core.compile.config import get_theme_style
from dbt_charts.core.compile.models.chart.normalized import (
    BarChart,
    LineChart,
    ScatterChart,
)
from dbt_charts.core.compile.models.style.authored import (
    BarChartStylePatch,
    LineChartStylePatch,
    ScatterChartStylePatch,
)
from dbt_charts.core.compile.resolve.style.board import resolve_style_and_context
from dbt_charts.core.render.chart.vega_lite import generate_vega_lite_spec

_BOARD_RS, _BOARD_CTX = resolve_style_and_context(get_theme_style())

_CARTESIAN_DATA = [
    {"order_month": "2025-01", "order_revenue": 100, "region_name": "north america"},
    {"order_month": "2025-02", "order_revenue": 200, "region_name": "europe"},
]
_HISTOGRAM_DATA = [
    {"order_revenue": 100, "region_name": "north america"},
    {"order_revenue": 200, "region_name": "europe"},
    {"order_revenue": 150, "region_name": "asia"},
]
_SCATTER_DATA = [
    {
        "order_revenue": 100,
        "order_profit": 10,
        "region_name": "north america",
        "order_count": 5,
    },
    {
        "order_revenue": 200,
        "order_profit": 20,
        "region_name": "europe",
        "order_count": 8,
    },
]

_UPPER_AXIS_TITLE = {"title": {"font": {"case": "upper"}}}
_UPPER_LEGEND_TITLE = {"title": {"font": {"case": "upper"}}}


def _bar_vertical(patch: dict[str, Any] | None) -> BarChart:
    style = BarChartStylePatch.model_validate(patch) if patch else None
    return BarChart(
        id="t",
        type="bar",
        x="order_month",
        y="order_revenue",
        color="region_name",
        style=style,
    )


def _bar_horizontal(patch: dict[str, Any] | None) -> BarChart:
    merged: dict[str, Any] = {"orientation": "horizontal", **(patch or {})}
    return BarChart(
        id="t",
        type="bar",
        x="order_month",
        y="order_revenue",
        color="region_name",
        style=BarChartStylePatch.model_validate(merged),
    )


def _histogram(patch: dict[str, Any] | None) -> BarChart:
    style = BarChartStylePatch.model_validate(patch) if patch else None
    return BarChart(
        id="t", type="histogram", x="order_revenue", color="region_name", style=style
    )


def _line(patch: dict[str, Any] | None) -> LineChart:
    style = LineChartStylePatch.model_validate(patch) if patch else None
    return LineChart(
        id="t",
        type="line",
        x="order_month",
        y="order_revenue",
        color="region_name",
        style=style,
    )


def _scatter(patch: dict[str, Any] | None) -> ScatterChart:
    style = ScatterChartStylePatch.model_validate(patch) if patch else None
    return ScatterChart(
        id="t",
        type="scatter",
        x="order_revenue",
        y="order_profit",
        color="region_name",
        size="order_count",
        style=style,
    )


def _encoding(spec: dict[str, Any]) -> dict[str, Any]:
    # Line renders as an hconcat of layers; the other families are flat.
    return spec["hconcat"][0]["encoding"] if "hconcat" in spec else spec["encoding"]


def _spec(
    build: Any, patch: dict[str, Any] | None, data: list[dict[str, Any]]
) -> dict[str, Any]:
    return generate_vega_lite_spec(
        build(patch), data, board_style=_BOARD_RS, chart_style_context=_BOARD_CTX
    )


_AXIS_X_CASES = [
    pytest.param(_bar_vertical, _CARTESIAN_DATA, id="bar_vertical"),
    pytest.param(_bar_horizontal, _CARTESIAN_DATA, id="bar_horizontal"),
    pytest.param(_histogram, _HISTOGRAM_DATA, id="histogram"),
    pytest.param(_line, _CARTESIAN_DATA, id="line"),
]


@pytest.mark.parametrize(("build", "data"), _AXIS_X_CASES)
def test_axis_x_title_case_leaves_color_legend_title_unchanged(
    build: Any, data: list[dict[str, Any]]
) -> None:
    baseline = _encoding(_spec(build, None, data))["color"]["title"]
    overridden = _encoding(_spec(build, {"axis_x": _UPPER_AXIS_TITLE}, data))["color"][
        "title"
    ]
    assert overridden == baseline, (
        f"style.axis_x.title.font.case moved the color legend title: "
        f"{baseline!r} -> {overridden!r}"
    )


@pytest.mark.parametrize(("build", "data"), _AXIS_X_CASES)
def test_legend_title_case_still_moves_color_legend_title(
    build: Any, data: list[dict[str, Any]]
) -> None:
    baseline = _encoding(_spec(build, None, data))["color"]["title"]
    overridden = _encoding(_spec(build, {"legend": _UPPER_LEGEND_TITLE}, data))[
        "color"
    ]["title"]
    assert overridden != baseline, (
        "style.legend.title.font.case did not affect the color legend title — "
        "the fix must not orphan the legend title font from the legend itself."
    )


def test_axis_y_title_case_leaves_scatter_legend_titles_unchanged() -> None:
    """Scatter's color/size legend read axis_y (not axis_x) — see scatter.py."""
    baseline = _encoding(_spec(_scatter, None, _SCATTER_DATA))
    overridden = _encoding(
        _spec(_scatter, {"axis_y": _UPPER_AXIS_TITLE}, _SCATTER_DATA)
    )
    assert overridden["color"]["title"] == baseline["color"]["title"], (
        f"style.axis_y.title.font.case moved the color legend title: "
        f"{baseline['color']['title']!r} -> {overridden['color']['title']!r}"
    )
    assert overridden["size"]["title"] == baseline["size"]["title"], (
        f"style.axis_y.title.font.case moved the size legend title: "
        f"{baseline['size']['title']!r} -> {overridden['size']['title']!r}"
    )


def test_legend_title_case_still_moves_scatter_legend_titles() -> None:
    baseline = _encoding(_spec(_scatter, None, _SCATTER_DATA))
    overridden = _encoding(
        _spec(_scatter, {"legend": _UPPER_LEGEND_TITLE}, _SCATTER_DATA)
    )
    assert overridden["color"]["title"] != baseline["color"]["title"], (
        "style.legend.title.font.case did not affect the scatter color legend title."
    )
    assert overridden["size"]["title"] != baseline["size"]["title"], (
        "style.legend.title.font.case did not affect the scatter size legend title."
    )
