"""`labels.font.color: series` on a layer resolves to the layer's ink, kept readable."""

from __future__ import annotations

import re

from dbt_charts.core.colors import ensure_readable_ink

from ._svg_render import render_board_to_svg


def test_a_layer_label_paints_its_layer_ink_not_the_keyword() -> None:
    svg = render_board_to_svg("""
title: T
queries:
  q:
    type: values
    rows:
      - {m: a, v: 1, w: 37}
      - {m: b, v: 2, w: 49}
charts:
  c:
    query: q
    type: bar
    x: m
    y: v
    layers:
      - type: line
        y: w
        style:
          marks:
            line:
              labels:
                visible: true
                font:
                  color: series
rows:
  - c
""")
    labels = re.findall(r'fill="([^"]+)"[^>]*>(?:37|49)</text>', svg)
    assert labels
    # The layer's own stroke, at the nearest lightness that reads 3:1 on the white canvas.
    strokes = set(re.findall(r'<path[^>]*stroke="([^"]+)"', svg))
    assert set(labels) <= {
        ensure_readable_ink(s, "#ffffff", 3.0) for s in strokes if len(s) == 7
    }
