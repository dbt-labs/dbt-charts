"""The plot box pays layout only for ink a reader can see.

Vega's ``autosize: fit`` sizes the plot to fit everything the scenegraph draws,
so any mark spilling past the plot rect pushes the plot box inward. On line and
area charts most of that spill was invisible — an ``opacity: 0`` hover
hit-target and a background-colored halo — and it left the grid ~9px inside the
card padding the chart title sits on.

Clipping is the fix, and it is also why the clipped set is narrow: a clip cuts a
stroke in half wherever it runs along the plot boundary, and takes the radius off
a marker sitting on the domain edge. So the data line (which flatlines on a zero
floor), the stacked band's perimeter and the point overlays keep their
reservation, and only the layers that paint nothing get clipped.
"""

from __future__ import annotations

import re

import pytest

from dbt_charts.core.compile.config import get_theme_style
from dbt_charts.core.compile.resolve.style.board import resolve_style

from ._svg_render import render_board_to_svg

CARD_PADDING = resolve_style(get_theme_style()).frame.card_padding

# A temporal x-axis, as on the reported board: Vega-Lite flushes the first and last
# tick labels against the plot edge, so the axis claims no width of its own there and
# the only thing that can push the plot inward is mark spill. On a nominal x the first
# label is centered on the edge and legitimately overhangs it — that inset is the
# label's, not this bug's.
_LINE_BOARD = """
title: Alignment
queries:
  q1:
    type: values
    rows:
      - {month: 2026-01-01, revenue: 100}
      - {month: 2026-02-01, revenue: 150}
      - {month: 2026-03-01, revenue: 120}
charts:
  c1:
    title: Revenue
    query: q1
    type: CHART_TYPE
    x: month
    y: revenue
rows:
  - c1
"""

# Line/area default to auto-density points now (line-charts-need-density-aware-
# point-defaults task): 3 points at this board's width is well within the
# on-trigger, so an unpinned line chart grows a real visible point layer at
# the domain edge. That's out of scope for "no ink is reserved that a reader
# can't see" -- pin points off so this board isolates the invisible-ink
# question the same way test_visible_point_overlay_keeps_its_radius below
# isolates the opposite (points explicitly on).
_LINE_BOARD_NO_POINTS = _LINE_BOARD.replace(
    "    y: revenue\n",
    "    y: revenue\n    style:\n      marks:\n        point:\n          size: 0\n",
)


def plot_origin_x(svg: str, chart_id: str) -> float:
    """Left edge of the chart's plot box, in the chart SVG's own coordinates.

    Vega emits the plot origin as the root ``<g>``'s translate — the point the
    x/y scales map their zero to — so this is the same number the gridlines and
    the axis domain are drawn against.
    """
    marks = re.search(
        rf'data-chart-id="{re.escape(chart_id)}".*?<g fill="none" '
        r'stroke-miterlimit="10" transform="translate\(([-\d.]+),\s*[-\d.]+\)"',
        svg,
        re.S,
    )
    assert marks, f"no rendered Vega marks for chart {chart_id!r}"
    return float(marks.group(1))


def _spec(chart_type: str, style: dict | None = None) -> dict:
    from pydantic import TypeAdapter

    from dbt_charts.core.compile.models.chart.normalized import Chart
    from dbt_charts.core.compile.models.query.normalized import SqlQuery
    from dbt_charts.core.render.chart.vega_lite import generate_vega_lite_spec

    authored: dict = {
        "id": "c",
        "type": chart_type,
        "x": "month",
        "y": "revenue",
        "query": SqlQuery(sql="SELECT 1", source="src"),
        "query_name": "q",
    }
    if style is not None:
        authored["style"] = style
    chart = TypeAdapter(Chart).validate_python(authored)
    rows = [
        {"month": "2026-01-01", "revenue": 100},
        {"month": "2026-02-01", "revenue": 150},
    ]
    return generate_vega_lite_spec(chart, rows, width=400)


def _marks(spec: dict) -> list[dict]:
    return [layer["mark"] for layer in spec["layer"]]


def _data_lines(chart_type: str) -> list[dict]:
    """The foreground data line layer(s) — the only stroked mark a reader reads."""
    lines = [
        m
        for m in _marks(_spec(chart_type))
        if m["type"] == "line" and m.get("tooltip") is True
    ]
    assert lines, "expected a foreground data line"
    return lines


@pytest.mark.parametrize("chart_type", ["line", "area"])
def test_plot_box_reserves_nothing_for_invisible_ink(chart_type: str):
    """The only inset left is the data line's own stroke, plus the grid's half."""
    svg = render_board_to_svg(_LINE_BOARD_NO_POINTS.replace("CHART_TYPE", chart_type))

    inset = plot_origin_x(svg, "c1") - CARD_PADDING

    # The bound is the data line's own stroke — a fixed reference. Taking the widest
    # unclipped stroke instead would move with the code: pre-fix the halo (2x the
    # line) is unclipped too, and the assertion would follow the bug up to 9px.
    line_stroke = max(m["strokeWidth"] for m in _data_lines(chart_type))
    assert 0 <= inset <= line_stroke + 1


@pytest.mark.parametrize("chart_type", ["line", "area"])
def test_every_layer_that_paints_nothing_is_clipped(chart_type: str):
    """The hit target and every halo clip — the four sites, not just the first.

    A halo is invisible by color, not by opacity: it paints the theme background
    at 1.0. Matching on ``opacity == 0`` alone would leave three of the four clip
    sites unpinned.

    Background-colored stroke/fill alone no longer means invisible: a
    colorless single-series area now draws its real top-edge separator in
    the background color too (the stacked recipe's band-knockout idiom),
    and that mark carries ``tooltip: True`` same as any other visible data
    mark -- ``tooltip is not True`` is the same visible/invisible split
    ``_data_lines`` above uses, so the background-color match only catches
    the genuinely invisible halo/backdrop sub-layers.
    """
    # Halos knock out against the chart's opaque composited canvas
    # (ink_canvas), not the raw background field -- the two can differ in
    # hex case (ink_canvas always lowercases; background preserves
    # authoring), so compare case-insensitively.
    canvas = resolve_style(get_theme_style()).chart_defaults.ink_canvas.lower()
    marks = _marks(_spec(chart_type, {"marks": {"point": {"size": 400}}}))

    paints_nothing = [
        m
        for m in marks
        if m.get("opacity") == 0
        or (
            str(m.get("stroke")).lower() == canvas
            and m["type"] == "line"
            and m.get("tooltip") is not True
        )
        or (
            str(m.get("fill")).lower() == canvas
            and m["type"] == "area"
            and m.get("tooltip") is not True
        )
    ]
    assert len(paints_nothing) >= 2, marks

    assert all(m.get("clip") for m in paints_nothing), paints_nothing


@pytest.mark.parametrize("chart_type", ["line", "area"])
def test_visible_layers_keep_their_reservation(chart_type: str):
    """Anything a reader can see is unclipped, so it keeps its space."""
    marks = _marks(_spec(chart_type, {"marks": {"point": {"size": 400}}}))

    visible_points = [
        m for m in marks if m["type"] == "point" and m.get("opacity") != 0
    ]
    assert visible_points, marks

    assert not any(m.get("clip") for m in visible_points), visible_points


@pytest.mark.parametrize("chart_type", ["line", "area"])
def test_data_line_keeps_its_full_stroke_on_the_plot_floor(chart_type: str):
    """A stroke that runs along the plot boundary must not be clipped in half.

    A series resting on a zero floor renders that run at half weight the moment
    its layer is clipped — measured at 4 device rows against 2.
    """
    assert not any(m.get("clip") for m in _data_lines(chart_type))


def test_visible_point_overlay_keeps_its_radius():
    """A visible marker at the domain edge still gets room — no half-discs."""
    board = _LINE_BOARD.replace("CHART_TYPE", "line").replace(
        "    y: revenue\n",
        "    y: revenue\n    style:\n      marks:\n        point:\n          size: 400\n",
    )

    svg = render_board_to_svg(board)

    # size 400 → Vega bounds the symbol at √400/2 = 10px beyond the first point.
    assert plot_origin_x(svg, "c1") > CARD_PADDING + 5.0
