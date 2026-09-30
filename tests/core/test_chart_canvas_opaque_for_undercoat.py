"""Undercoat/knockout backgrounds and inside-bar label ink must read a
chart's opaque composited canvas, never its raw (possibly transparent) card
fill.

style.background is not inherited (see normalize/dispatch.py); a chart's
own card fill (ResolvedChart.background, fed by style.charts.background)
can therefore legitimately be "transparent" now -- e.g. any chart placed
in an unstyled nested board, whose own card-fill default falls back to
the board's own (non-inherited) fill. That is correct for the chart's own
Vega-Lite spec.background. It is NOT correct for undercoat/knockout marks
(the area-overlap backdrop, halo strokes) or inside-bar value labels --
those need the chart's own COMPOSITED canvas (card fill over board
canvas), which is always opaque.
"""

from pathlib import Path

from dbt_charts.cli.filesystem_project import FilesystemProject
from dbt_charts.core.colors import parse_css_color
from dbt_charts.core.compile import compile as df_compile
from dbt_charts.core.execute.adapters import build_adapter_registry
from dbt_charts.core.execute.executor import Executor
from dbt_charts.core.render.board_resolve import build_resolved_board
from dbt_charts.core.render.boards import render_board_svg

_YAML = """
title: Root
queries:
  q:
    columns: [month, series, revenue]
    values:
      - [Jan, a, 100]
      - [Feb, a, 120]
      - [Jan, b, 80]
      - [Feb, b, 90]
charts:
  area1:
    query: q
    type: area
    x: month
    y: revenue
    color: series
cols:
  - rows:
      - area1
  - text: sidebar
"""


def _resolve(tmp_path: Path):
    result = df_compile(_YAML)
    assert result.success, result.errors
    assert result.board is not None
    executor = Executor(
        result.board,
        build_adapter_registry(FilesystemProject(tmp_path)),
        query_registry=result.query_registry,
    )
    return executor, build_resolved_board(result.board, executor, {})


def test_area_chart_canvas_is_opaque_even_when_card_fill_is_transparent(
    tmp_path: Path,
) -> None:
    """area1 lives in an unstyled nested cols: wrapper -- its own card fill
    correctly defaults to transparent. Its undercoat canvas must stay
    opaque regardless."""
    _executor, (resolved, _cache) = _resolve(tmp_path)
    chart = resolved.charts["area1"]
    assert chart.background == "transparent"
    assert chart.canvas != "transparent"
    _, _, _, alpha = parse_css_color(chart.canvas)
    assert alpha == 1.0


def test_undercoat_backdrop_paints_the_opaque_canvas_not_transparent(
    tmp_path: Path,
) -> None:
    """The rendered backdrop layer (the opaque knockout mask behind an area
    chart's translucent fill, hiding gridlines beneath it) must paint the
    chart's own composited canvas, not its raw (transparent) card fill."""
    import re

    executor, (resolved, render_cache) = _resolve(tmp_path)
    svg = render_board_svg(
        resolved, executor, {}, resolved.style.background, render_cache=render_cache
    )
    backdrops = re.findall(r'fill="([^"]*)"[^>]*fill-opacity="1"', svg)
    assert backdrops, "expected at least one opaque backdrop layer"
    assert "transparent" not in backdrops


_TWO_BAR_YAML = """
title: T
queries:
  revenue:
    columns: [channel, revenue_eur]
    values:
      - [Web, 168000]
      - [Store, 28000]
      - [Refunds, -41500]
charts:
  value_labels:
    type: bar
    query: revenue
    x: channel
    y: revenue_eur
    style:
      marks:
        bar:
          labels:
            visible: true
            format: ",.0f"
  stacked:
    type: bar
    query: revenue
    x: channel
    y: revenue_eur
    color: channel
    style:
      stack: zero
      marks:
        bar:
          labels:
            visible: true
            position: middle
            format: ",.0f"
rows:
  - cols: [value_labels, stacked]
"""


def test_inside_bar_labels_paint_the_opaque_canvas_in_a_multi_chart_board(
    tmp_path: Path,
) -> None:
    """A chart beside another has a transparent card fill; its inside-bar
    value labels must still get an opaque ink, not the transparent fill."""
    import re

    result = df_compile(_TWO_BAR_YAML)
    assert result.success, result.errors
    assert result.board is not None
    executor = Executor(
        result.board,
        build_adapter_registry(FilesystemProject(tmp_path)),
        query_registry=result.query_registry,
    )
    resolved, render_cache = build_resolved_board(result.board, executor, {})
    for name in ("value_labels", "stacked"):
        assert resolved.charts[name].background == "transparent"
    svg = render_board_svg(
        resolved, executor, {}, resolved.style.background, render_cache=render_cache
    )
    for label in ("168,000", "28,000"):
        tags = re.findall(rf"<text\b([^>]*)>{label}</text>", svg)
        assert len(tags) == 2, f"expected {label} in both charts, got {tags}"
        for attrs in tags:
            fill = re.search(r'\bfill="([^"]*)"', attrs)
            assert fill is not None, f"inside label {label} has no fill: {attrs}"
            assert parse_css_color(fill.group(1))[3] == 1.0
