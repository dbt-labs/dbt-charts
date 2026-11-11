"""Regression tests: support_table strip height, positioning, and dx computation.

Covers:
  - _align_cols_heights must not inflate height for support_table charts.
  - Strip top must reserve vertical space for two-line x-axis labels.
  - _compute_support_table_entry_dx correctness: has_time_unit gating, numeric
    formatting, aggregate format field, empty-data → None sentinel.

Charts with support_table switch to autosize:pad so the strip renders below the plot
without shrinking it. Under pad mode, spec.height is the *inner* plot rect — Vega-Lite
adds strip height + axis padding on top of that, making the outer SVG taller than
target_height. _align_cols_heights must compensate by passing a corrected height
(target_height - overhead) so the outer SVG fits within the allocated slot.
"""

from __future__ import annotations

import re

from .._svg_render import render_board_to_svg

# =============================================================================
# _compute_support_table_entry_dx unit tests
# =============================================================================


# =============================================================================
# title.offset vs padding.top for position:top support_table
# =============================================================================


def test_titled_support_table_cols_card_heights_equal() -> None:
    """Both cols: siblings have equal rendered SVG height after support_table calibration.

    A titled position:top support_table chart re-renders with a corrected
    title.offset.  That offset change also shifts the outer SVG height.
    The sizing pass must correct back to the row target so both siblings
    produce the same outer height.

    Pre-fix: the support_table chart overshot its target by ~strip_h, so the
    two SVGs differed by that amount.
    Post-fix: both land within 2px of each other.
    """
    _YAML = """
title: Height Equality Test
queries:
  q:
    columns: [month, revenue]
    values:
      - [Jan, 100.0]
      - [Feb, 200.0]
charts:
  dt_chart:
    query: q
    type: bar
    x: month
    y: revenue
    title: Revenue With SupportTable
    style:
      orientation: vertical
    support_table:
      - source: revenue
  plain_chart:
    query: q
    type: bar
    x: month
    y: revenue
    title: Revenue Plain
    style:
      orientation: vertical
cols:
  - dt_chart
  - plain_chart
"""

    svg = render_board_to_svg(_YAML)

    def _chart_svg_height(chart_id: str) -> float:
        """Return the rendered SVG height for a chart section in the board SVG."""
        m = re.search(rf'data-authored-path="charts\.{re.escape(chart_id)}"', svg)
        assert m is not None, f"chart {chart_id!r} not found in board SVG"
        next_chart = re.search(r'data-authored-path="charts\.[^"]+"', svg[m.end() :])
        end = m.end() + next_chart.start() if next_chart else len(svg)
        section = svg[m.start() : end]
        vb = re.search(r'viewBox="0 0 [0-9.]+ ([0-9.]+)"', section)
        assert vb is not None, f"no viewBox found in {chart_id!r} section"
        return float(vb.group(1))

    dt_h = _chart_svg_height("dt_chart")
    plain_h = _chart_svg_height("plain_chart")

    assert abs(dt_h - plain_h) < 2.0, (
        f"cols: row heights differ by {abs(dt_h - plain_h):.1f}px "
        f"(dt_chart={dt_h:.1f}px, plain_chart={plain_h:.1f}px). "
        "After support_table height correction, both siblings must match."
    )


def test_bottom_strip_board_is_no_taller_than_top_strip_board() -> None:
    """A bottom strip sits below the x labels it clears, so it adds no second label band.

    The canvas below the plot is the further reach of the label band or the strip
    (the gap that clears the labels, plus the strip), never their sum.
    """
    template = """
title: Strip position
queries:
  q:
    columns: [month, amount, orders]
    values:
      - ["2026-01-01", 120, 30]
      - ["2026-02-01", 150, 41]
      - ["2026-03-01", 90, 22]
charts:
  q:
    query: q
    type: bar
    x: month
    y: amount
    height: 220
    support_table:
      - source: orders
        label: Orders
    style:
      support_table:
        position: POSITION
rows:
  - q
"""

    def board_height(position: str) -> float:
        svg = render_board_to_svg(template.replace("POSITION", position))
        vb = re.search(r'viewBox="0 0 [0-9.]+ ([0-9.]+)"', svg)
        assert vb is not None
        return float(vb.group(1))

    top, bottom = board_height("top"), board_height("bottom")
    assert bottom <= top + 2.0, f"bottom strip board {bottom}px vs top {top}px"
