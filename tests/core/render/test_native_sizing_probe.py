"""A measuring render returns the box the layout sizes a slot from."""

from __future__ import annotations

from typing import Any

import pytest

from dbt_charts.core.compile.config import get_theme_style
from dbt_charts.core.compile.models.chart.normalized import BarChart
from dbt_charts.core.compile.models.chart.resolved import ResolvedBarChart
from dbt_charts.core.compile.models.style.resolved import ResolvedStyle
from dbt_charts.core.compile.resolve import resolve
from dbt_charts.core.compile.resolve.style.board import resolve_style_and_context
from dbt_charts.core.render import board_resolve
from dbt_charts.core.render.engine import render_native
from dbt_charts.core.render.layout_sizing import RenderCache
from dbt_charts.core.render.svg_utils import extract_svg_dimensions

from .._svg_render import SAMPLE_BOARD_YAML, render_board_to_svg

_ROWS = [{"cat": f"c{i}", "val": i + 1} for i in range(5)]


def _bar() -> tuple[ResolvedBarChart, ResolvedStyle]:
    board_style, context = resolve_style_and_context(get_theme_style())
    chart = resolve(
        BarChart(id="c", type="bar", query_name="q", x="cat", y="val"),
        _ROWS,
        chart_style_context=context,
        width=600,
    )
    assert isinstance(chart, ResolvedBarChart)
    return chart, board_style


def test_a_measuring_render_reports_the_height_it_drew() -> None:
    chart, board_style = _bar()
    svg, natural_height = render_native(
        chart,
        _ROWS,
        width=600,
        height=300,
        padding=None,
        board_style=board_style,
        measuring=True,
    )
    assert natural_height == extract_svg_dimensions(svg).height


def test_a_render_at_the_settled_slot_reports_no_natural_height() -> None:
    chart, board_style = _bar()
    _, natural_height = render_native(
        chart,
        _ROWS,
        width=600,
        height=300,
        padding=None,
        board_style=board_style,
    )
    assert natural_height is None


def test_sizing_caches_a_cartesian_chart_rendered_at_its_settled_slot(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A probe measures; the slot's own render is what the render pass reuses."""
    caches: list[RenderCache] = []
    real = board_resolve.build_resolved_board

    def spy(*args: Any, **kwargs: Any) -> tuple[Any, RenderCache]:
        resolved, cache = real(*args, **kwargs)
        caches.append(cache)
        return resolved, cache

    monkeypatch.setattr(board_resolve, "build_resolved_board", spy)
    render_board_to_svg(SAMPLE_BOARD_YAML)
    assert [key[:3] for key in caches[0]] == [("c1", 600.0, 408.0)]
