"""End-to-end regression test: grid layout items must be sized by their span.

A `grid:` item's height was computed from one bare grid-column unit, ignoring
the item's `col_span` entirely. Correct by coincidence when `col_span == 1`,
wrong for every `col_span > 1` — a `columns: 4, col_span: 2` item (900px
rendered wide) and a `columns: 4, col_span: 4` item (1800px rendered wide)
both got the height of a 450px chart, the bare column unit at `columns: 4`.

These tests go through the real pipeline — `compile()` -> `render()` -> the
emitted SVG's `data-chart-height` attribute on the `dbt-chart` group — never a
hand-built sizing context, since that is exactly the seam the bug hid behind.
"""

from __future__ import annotations

import re

import pytest

from ._svg_render import render_board_to_svg


def _render_svg(yaml_body: str, board_width: float = 1872.0) -> str:
    yaml_content = f"""\
title: Test
extends: clarity
width: {board_width}
queries:
  q:
    type: values
    rows:
      - {{month: Jan, revenue: 100}}
      - {{month: Feb, revenue: 150}}
      - {{month: Mar, revenue: 120}}
      - {{month: Apr, revenue: 180}}
      - {{month: May, revenue: 160}}
{yaml_body}
"""
    return render_board_to_svg(yaml_content)


def _chart_height(svg: str, chart_id: str) -> float:
    """Extract data-chart-height from the <g class="dbt-chart" ...> for chart_id."""
    m = re.search(
        rf'<g class="[^"]*dbt-chart[^"]*"[^>]*data-chart-id="{re.escape(chart_id)}"'
        rf'[^>]*data-chart-height="([\d.]+)"',
        svg,
    )
    assert m is not None, f"no dbt-chart group found for chart_id={chart_id!r} in SVG"
    return float(m.group(1))


def _chart(chart_id: str, chart_type: str = "line") -> str:
    return f"""  {chart_id}:
    query: q
    type: {chart_type}
    x: month
    y: revenue
"""


class TestGridSpanOne:
    """span == 1 is correct today — this is the regression risk."""

    def test_grid_span_one_matches_cols(self) -> None:
        svg_cols = _render_svg("charts:\n" + _chart("c") + "cols:\n  - c\n  - c\n")
        svg_grid = _render_svg(
            "charts:\n"
            + _chart("g")
            + "grid:\n  columns: 2\n  items:\n    - item: g\n      col_span: 1\n"
        )
        assert _chart_height(svg_grid, "g") == pytest.approx(
            _chart_height(svg_cols, "c"), abs=1.0
        )


class TestGridSpanGreaterThanOne:
    """A grid item's height must depend on its span-derived width, not the
    grid's bare column count."""

    def test_full_span_single_item_matches_cols(self) -> None:
        """columns: 4, col_span: 4 (one item spanning the whole row) must
        match a single full-width cols: item at the same rendered width."""
        svg_cols = _render_svg("charts:\n" + _chart("c") + "cols:\n  - c\n")
        svg_grid = _render_svg(
            "charts:\n"
            + _chart("g")
            + "grid:\n  columns: 4\n  items:\n    - item: g\n      col_span: 4\n"
        )
        assert _chart_height(svg_grid, "g") == pytest.approx(
            _chart_height(svg_cols, "c"), abs=1.0
        )

    def test_partial_span_matches_cols_at_same_width(self) -> None:
        """columns: 4, col_span: 2 (900px of 1800px content) must match a
        2-across cols: item (also 900px) — not a 4-across cols: item (450px)."""
        svg_cols_2 = _render_svg("charts:\n" + _chart("c") + "cols:\n  - c\n  - c\n")
        svg_grid = _render_svg(
            "charts:\n"
            + _chart("g")
            + "grid:\n  columns: 4\n  items:\n    - item: g\n      col_span: 2\n"
        )
        assert _chart_height(svg_grid, "g") == pytest.approx(
            _chart_height(svg_cols_2, "c"), abs=1.0
        )

    def test_span_grows_height_below_the_content_plateau(self) -> None:
        """The original bug's smoking gun: at columns: 4, span 2 (900px) and
        span 4 (1800px) produced the identical height (332), both equal to
        the height of a single bare 450px column. Below the height estimator's
        own content plateau (~900px, where a line chart's natural height stops
        growing with width — confirmed independently against `cols:` at 225 /
        450 / 900px), increasing span must increase height.

        `columns: 8` keeps every span below that plateau so growth is visible:
        span 1 = 225px, span 2 = 450px, span 4 = 900px.
        """
        heights = {}
        for span in (1, 2, 4):
            svg = _render_svg(
                "charts:\n"
                + _chart("g")
                + f"grid:\n  columns: 8\n  items:\n    - item: g\n      col_span: {span}\n"
            )
            heights[span] = _chart_height(svg, "g")
        assert heights[1] < heights[2] < heights[4]


class TestGridSeveralItemsPerRow:
    """A grid row with several items, each with its own span, all sharing the
    row height — the case one squat chart can no longer drag the row down."""

    def test_two_items_span_two_of_four_match_two_across_cols(self) -> None:
        svg_cols = _render_svg("charts:\n" + _chart("c") + "cols:\n  - c\n  - c\n")
        svg_grid = _render_svg(
            "charts:\n"
            + _chart("g1")
            + _chart("g2")
            + "grid:\n  columns: 4\n  items:\n"
            "    - item: g1\n      col_span: 2\n"
            "    - item: g2\n      col_span: 2\n"
        )
        cols_height = _chart_height(svg_cols, "c")
        assert _chart_height(svg_grid, "g1") == pytest.approx(cols_height, abs=1.0)
        assert _chart_height(svg_grid, "g2") == pytest.approx(cols_height, abs=1.0)


class TestGridColumnsIndependence:
    """The height a grid item receives is a function of its span-derived
    width, not of the grid's column count — columns: 6/span 3 and
    columns: 24/span 12 both cover half the row and must match."""

    def test_columns_6_span_3_matches_columns_24_span_12(self) -> None:
        svg_6 = _render_svg(
            "charts:\n"
            + _chart("g")
            + "grid:\n  columns: 6\n  items:\n    - item: g\n      col_span: 3\n"
        )
        svg_24 = _render_svg(
            "charts:\n"
            + _chart("g")
            + "grid:\n  columns: 24\n  items:\n    - item: g\n      col_span: 12\n"
        )
        assert _chart_height(svg_6, "g") == pytest.approx(
            _chart_height(svg_24, "g"), abs=1.0
        )


class TestGridRowSpan:
    """A grid item's `row_span` distributes its content height across the
    spanned rows — orthogonal to the col_span width fix, and must keep
    working after it. A row_span: 2 item should differ from its row_span: 1
    counterpart by roughly one row gap, not be blown up or collapsed."""

    def test_row_span_two_is_close_to_row_span_one_plus_one_gap(self) -> None:
        svg_span1 = _render_svg(
            "charts:\n" + _chart("g") + "grid:\n  columns: 4\n  items:\n"
            "    - item: g\n      col_span: 4\n      row_span: 1\n"
        )
        svg_span2 = _render_svg(
            "charts:\n" + _chart("g") + "grid:\n  columns: 4\n  items:\n"
            "    - item: g\n      col_span: 4\n      row_span: 2\n"
        )
        height_1 = _chart_height(svg_span1, "g")
        height_2 = _chart_height(svg_span2, "g")
        # row_span=2 adds exactly one interior row gap on top of the
        # width-derived content height — not a doubling, not a collapse.
        assert height_2 > height_1
        assert (height_2 - height_1) < 50.0


class TestGridOtherChartFamily:
    """Every chart family lands on the same wrong height — not just line/area,
    the only families that used to log a sizing warning."""

    def test_bar_chart_span_matches_cols(self) -> None:
        svg_cols = _render_svg("charts:\n" + _chart("c", "bar") + "cols:\n  - c\n")
        svg_grid = _render_svg(
            "charts:\n"
            + _chart("g", "bar")
            + "grid:\n  columns: 4\n  items:\n    - item: g\n      col_span: 4\n"
        )
        assert _chart_height(svg_grid, "g") == pytest.approx(
            _chart_height(svg_cols, "c"), abs=1.0
        )
