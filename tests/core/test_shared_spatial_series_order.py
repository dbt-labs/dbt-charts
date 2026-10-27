"""Shared spatial series order across legend, tooltip, and direct labels.

Covers the tooltip-fidelity initiative's Phase 1 task: legend `scale.domain`,
JS tooltip (which reads the legend's rendered order — PR #5850), and direct
(endpoint) labels must all agree on ONE per-family spatial order, derived from
the same authority that decides how the marks are drawn — never a second,
parallel re-implementation of the same policy. Reordering DISPLAY order must
never change a series' assigned color.

Families in scope: stacked bar/column/area, grouped bar, multi-line, overlap
(unstacked) area. Pie/donut and combo are verify-only — already correct
before this task.
"""

from __future__ import annotations

import re

from dbt_charts.core.compile.config import get_theme_style
from dbt_charts.core.compile.models.chart.normalized import (
    PieChart,
)
from dbt_charts.core.compile.models.style.authored import (
    PieChartStylePatch,
)
from dbt_charts.core.compile.resolve.style.board import resolve_style_and_context
from dbt_charts.core.render.chart.emitters._cartesian import (
    distinct_series_values,
)

from ._engine_render import render_pie_svg
from ._svg_render import legend_label_order, render_board_to_svg

_BOARD_STYLE, _BOARD_CTX = resolve_style_and_context(get_theme_style())

# Legend entry order still has a contract wherever a legend is drawn; stacked
# bars only draw one when direct labeling is off, so pin the order there.
_BOARD_STYLE_NO_ENDPOINT_LABELS, _BOARD_CTX_NO_ENDPOINT_LABELS = (
    resolve_style_and_context(
        get_theme_style().model_copy(
            update={
                "charts": get_theme_style().charts.model_copy(
                    update={
                        "bar": get_theme_style().charts.bar.model_copy(
                            update={
                                "endpoint_labels": get_theme_style().charts.bar.endpoint_labels.model_copy(
                                    update={"visible": False}
                                )
                            }
                        )
                    }
                )
            }
        )
    )
)


# ---------------------------------------------------------------------------
# Shared helper unit tests
# ---------------------------------------------------------------------------


def test_distinct_series_values_sorted_alphabetically():
    data = [{"s": "zeta"}, {"s": "alpha"}, {"s": "zeta"}, {"s": None}]
    assert distinct_series_values(data, "s") == ["alpha", "zeta"]


def test_distinct_series_values_never_admits_a_none_label():
    # A null (or an absent column) must not become the string "None" in the
    # scale domain — that paints nothing while advertising a legend entry.
    # Families guarded by validate_color_series reject this data upstream;
    # the unguarded ones rely on this drop.
    data = [{"s": "alpha"}, {"s": None}, {}]
    assert distinct_series_values(data, "s") == ["alpha"]


# ---------------------------------------------------------------------------
# Stacked vertical bar: legend must follow "top-of-stack first", and
# must agree with the SAME order the direct labels already use.
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# Grouped bar: verify-only. No stack_order concept — the legend must use
# whatever order the marks' own xOffset uses (today, VL's shared default),
# never a separately-computed order.
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# Stacked area: legend and palette follow the same global-total stack order.
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# Overlap (unstacked) area + multi-line: stable order by each series'
# most-recent non-null value, agreeing with endpoint labels.
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# Real-render regression: a VL-JSON assertion on scale.domain/legend.values
# alone would NOT have caught the original bug — Vega's own SVG legend
# renderer re-sorts nominal entries independent of an explicit scale.domain
# override, silently falling back to alphabetical, unless legend.values also
# pins the render order. These render an actual board end-to-end (through
# vl-convert) and read the legend's real DOM text order.
# ---------------------------------------------------------------------------


def test_stacked_bar_real_render_legend_reads_top_of_stack_first():
    svg = render_board_to_svg(
        """
title: Fix1 stacked bar render check
queries:
  q1:
    type: values
    rows:
      - {month: Jan, segment: Enterprise, revenue: 520}
      - {month: Jan, segment: Mid-Market, revenue: 300}
      - {month: Jan, segment: SMB, revenue: 180}
charts:
  c1:
    query: q1
    type: bar
    x: month
    y: revenue
    color: segment
    style:
      orientation: vertical
      stack: zero
      endpoint_labels:
        visible: false
rows:
  - c1
"""
    )
    # SMB is the smallest segment (180) -> top of the stack -> must render
    # first in the legend, ahead of the larger Mid-Market (300) and
    # Enterprise (520, at the baseline). endpoint_labels: false — a stacked
    # bar otherwise names its series directly and draws no legend at all.
    assert legend_label_order(svg) == ["SMB", "Mid-Market", "Enterprise"]


def test_multiline_real_render_legend_reads_endpoint_order():
    svg = render_board_to_svg(
        """
title: Fix2 multi-line render check
queries:
  q1:
    type: values
    rows:
      - {month: Jan, region: South, revenue: 150}
      - {month: Jan, region: North, revenue: 90}
      - {month: Jan, region: West, revenue: 60}
      - {month: Feb, region: South, revenue: 180}
      - {month: Feb, region: North, revenue: 110}
      - {month: Feb, region: West, revenue: 80}
      - {month: Mar, region: South, revenue: 210}
      - {month: Mar, region: North, revenue: 130}
      - {month: Mar, region: West, revenue: 105}
charts:
  c1:
    query: q1
    type: line
    x: month
    y: revenue
    color: region
    style:
      legend:
        visible: true
      endpoint_labels:
        visible: false
rows:
  - c1
"""
    )
    # Mar (last x) ends South(210) > North(130) > West(105) -> that's the
    # stable endpoint order the legend must read, top to bottom.
    assert legend_label_order(svg) == ["South", "North", "West"]


# ---------------------------------------------------------------------------
# Pie/donut: verify-only. enc["sort"] = False + arc order = the row-index
# field mean the legend and the arc paint order already share ONE authority
# (the data's own row order) — nothing to fix here.
# ---------------------------------------------------------------------------


def test_pie_legend_and_arc_paint_order_share_one_authority():
    """The legend lists and the wedges paint in the data's own row order."""
    chart = PieChart(
        id="t",
        query_name="q",
        type="pie",
        theta="val",
        color="cat",
        style=PieChartStylePatch.model_validate({"legend": {"visible": True}}),
    )
    data = [
        {"cat": "zeta", "val": 10},
        {"cat": "alpha", "val": 30},
    ]
    svg = render_pie_svg(chart, data)
    assert legend_label_order(svg) == ["zeta", "alpha"]
    wedge_headers = re.findall(
        r'aria-label="\u2064([^;]+);[^"]*"[^>]*aria-roledescription="arc mark"', svg
    )
    assert wedge_headers == ["zeta", "alpha"]


# ---------------------------------------------------------------------------
# Combo (layered): verify-only. The base series' OWN legend order must still
# follow its family convention inside a combo — this task does not touch the
# overlay-layer legend/tooltip merge (a separate task's scope).
# ---------------------------------------------------------------------------
