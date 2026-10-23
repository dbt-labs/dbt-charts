"""Regression: the baked domain-spanning tick ladder must survive every
measure-channel emit path — vertical bar, horizontal bar, and multi-metric
line — not just the vertical single-series path.

Two charts pinned to the same authored domain must read on one ruler
regardless of orientation or emit path. Renders a real board end to end
(compile -> render -> SVG) and reads the tick-label text nodes Vega's own SVG
renderer emits, rather than inspecting an intermediate VL spec dict.
"""

from __future__ import annotations

import re

from ..._svg_render import render_board_to_svg

# Authored axis_quantitative.scale.values ladder [10, 20, 30] whose bottom
# rung (10) sits ABOVE the data floor (5, on metric_b) — multi-metric line
# reaches the same domain-floor pin as horizontal bar's above, but through
# resolve_measure_y_scale (emitters/_cartesian.py) rather than
# _emit_horizontal (bar.py): the smart-zero heuristic abstains for this
# close-to-zero data, line.py's resolve step bakes an explicit zero-anchor
# decision onto the scale, and the emitted VL scale used to pin domainMin
# from the authored ladder's own first rung regardless of provenance.
_AUTHORED_LADDER_ABOVE_DATA_FLOOR_LINE_BOARD = """
title: Authored ladder floor above data floor (line)
queries:
  q:
    type: values
    rows:
      - {month: Jan, metric_a: 20, metric_b: 15}
      - {month: Feb, metric_a: 28, metric_b: 5}
      - {month: Mar, metric_a: 10, metric_b: 25}
charts:
  line_multi_metric:
    query: q
    type: line
    x: month
    y: [metric_a, metric_b]
    style:
      axis_quantitative:
        scale:
          values: [10, 20, 30]
rows:
  - line_multi_metric
"""


def test_multi_metric_line_authored_ladder_floor_above_data_does_not_clip() -> None:
    """Regression: an authored ``axis_quantitative.scale.values`` ladder
    whose bottom rung sits above the data floor must not pin a multi-metric
    line's domain floor either. Reads the drawn Y-axis domain straight from
    Vega's own accessibility label (``aria-label="Y-axis ... values from X
    to Y"``, which states the scale's actual rendered bounds) rather than an
    intermediate VL dict — a line mark carries no clip, so a wrongly-pinned
    floor doesn't drop the point, it paints it outside the plot over the
    axis chrome, which only the drawn domain (not mark count) reveals.
    """
    svg = render_board_to_svg(_AUTHORED_LADDER_ABOVE_DATA_FLOOR_LINE_BOARD)
    match = re.search(
        r'aria-label="Y-axis[^"]*values from (-?[\d.]+) to (-?[\d.]+)"', svg
    )
    assert match, f"no Y-axis aria-label found in rendered SVG: {svg!r}"
    domain_min = float(match.group(1))
    assert domain_min <= 5, (
        "authored ladder floor (10) clipped the data floor (5) out of the "
        f"drawn domain: Y-axis domain starts at {domain_min}"
    )
