"""A chart placed more than once draws each placement with its own ids."""

from __future__ import annotations

import re
from collections import Counter

from ..._svg_render import render_board_to_svg

_QUERY = """
queries:
  q:
    type: values
    rows:
      - {month: Jan, region: East, revenue: 100}
      - {month: Feb, region: East, revenue: 150}
      - {month: Jan, region: West, revenue: 120}
      - {month: Feb, region: West, revenue: 90}
"""

_BAR = """
  c:
    query: q
    type: bar
    x: month
    y: revenue
    color: region
    style:
      marks:
        bar:
          labels:
            visible: true
"""


def test_each_placement_of_a_repeated_chart_defines_its_own_ids() -> None:
    svg = render_board_to_svg(
        f"title: Twice\n{_QUERY}charts:{_BAR}rows:\n  - cols: [c, c]\n"
    )
    # The card wrapper's `chart-<id>` is the chart's link anchor, the first placement's.
    ids = [i for i in re.findall(r'\sid="([^"]+)"', svg) if not i.startswith("chart-")]
    repeated = [i for i, n in Counter(ids).items() if n > 1]
    assert ids, "the labeled bar defines a knockout mask"
    assert repeated == [], f"ids defined twice: {repeated}"
    refs = set(re.findall(r"url\(#([^)]+)\)", svg))
    assert refs <= set(ids), refs - set(ids)
    assert any(r.endswith(".placed1") for r in refs), (
        "the second placement refers to its own"
    )
