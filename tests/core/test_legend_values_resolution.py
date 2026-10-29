"""End-to-end coverage for ``style.legend.values``: engine display order is
pinned by ``apply_legend_entry_order`` (``emitters/_channels.py``), the ONE
writer of ``encoding.color.legend.values``; an authored list is resolved
against the real domain and wins outright instead of being clobbered.

Families: stacked bar, wide bar, multi-series line, ``color:`` area, and a
field-colored overlay. An unresolvable entry never silently disappears,
but it never fails the render either: it is always dropped, and the
render-warnings detector (covered separately by
``tests/render/warnings/test_legend_values_unresolved.py``) is what
surfaces it to the author.
"""

from __future__ import annotations

import pytest

from dbt_charts.core.compile.config import get_theme_style
from dbt_charts.core.compile.resolve import resolve
from dbt_charts.core.compile.resolve.chart._wide_fields import (
    wide_measure_labels_for,
)
from dbt_charts.core.compile.resolve.style.board import (
    resolve_chart_style_context,
    resolve_style,
)
from dbt_charts.core.render.chart.vega_lite import render_resolved_chart

from ._engine_render import render_pie_svg
from ._svg_render import legend_label_order, render_board_to_svg


def _render(make_chart, chart_type: str, style: dict, rows, **kwargs):
    theme = get_theme_style()
    context = resolve_chart_style_context(theme)
    chart = make_chart(chart_type, style=style, **kwargs)
    resolved = resolve(chart, rows, chart_style_context=context)
    artifact = render_resolved_chart(resolved, rows, resolve_style(theme))
    return artifact.payload


class TestOtherFamiliesResolveToo:
    """Wide/overlay/bar-color aren't the only families with a legend --
    pie, scatter, heatmap and grouped (stack: none) bar must resolve an
    authored `legend.values` too, not write it through `legend_to_vl`
    verbatim (a grouped-bar author entry matching no real series must not
    paint a phantom swatch)."""

    def test_pie_resolves_and_filters(self, make_chart):
        rows = [
            {"segment": "Direct", "revenue": 10},
            {"segment": "Organic", "revenue": 20},
            {"segment": "Paid", "revenue": 15},
        ]
        chart = make_chart(
            "pie",
            style={"legend": {"visible": True, "values": ["paid", "direct"]}},
            theta="revenue",
            color="segment",
        )
        assert legend_label_order(render_pie_svg(chart, rows, width=800.0)) == [
            "Paid",
            "Direct",
        ]

    def test_geoshape_nominal_choropleth_resolves(self):
        # "SOUTH"/"WEST" (uppercase, against the data order) fold to the real domain entries
        # "south"/"west" in the authored order.
        board = """
queries:
  q:
    type: values
    rows:
      - {state_code: "6", tier: west}
      - {state_code: "48", tier: south}
charts:
  c:
    query: q
    type: map
    geo:
      source: us-states
    lookup: state_code
    value: tier
    style:
      legend:
        values: [SOUTH, WEST]
rows:
  - c
"""
        assert legend_label_order(render_board_to_svg(board)) == ["south", "west"]


class TestWideMeasureAllNullDoesNotRaise:
    def test_a_layer_color_column_that_is_all_null_is_refused_by_name(self, make_chart):
        """A layer colored by an all-null column is refused, whatever the
        authored legend `values` name.

        An earlier version pinned that the base label still resolved in the
        spec when the layer's color column held no values. The spec resolved,
        but the board it describes does not render: Vega-Lite builds the empty
        color scale into a `NaN` legend and every mark collapses to zero height
        (`ERR-CHART-PAINTED-NO-MARKS`). Resolving the legend entry of a chart
        that cannot draw was the wrong thing to protect."""
        from dbt_charts.core.compile.models.chart.authored._layer import LineLayer
        from dbt_charts.core.diagnostics.chart_data import ChartDataError

        rows = [
            {"month": "Jan", "value": 10, "target": 5, "kind": None},
            {"month": "Feb", "value": 15, "target": 8, "kind": None},
        ]
        with pytest.raises(ChartDataError) as err:
            _render(
                make_chart,
                "bar",
                {
                    "orientation": "vertical",
                    "stack": "zero",
                    "legend": {"visible": True, "values": ["Alpha", "Beta"]},
                    "endpoint_labels": {"visible": False},
                },
                rows,
                x="month",
                y="value",
                layers=[
                    LineLayer(type="line", y="target", color="kind", label="Alpha")
                ],
            )
        assert err.value.code is not None
        assert err.value.code.code == "ERR-LAYER-COLOR-ALL-NULL"


class TestWideMeasureHumanizationCollisionFallsBackToRawNames:
    def test_a_non_colliding_measure_in_the_same_y_list_keeps_its_humanized_label(
        self, make_chart
    ):
        """Only the colliding group falls back to raw names -- a third,
        non-colliding measure in the same y: list keeps its humanized
        label."""
        theme = get_theme_style()
        context = resolve_chart_style_context(theme)
        chart = make_chart(
            "bar",
            style={"orientation": "vertical", "stack": "zero"},
            x="month",
            y=["churn_pct", "churn_percent", "revenue_usd"],
        )
        rows = [
            {
                "month": "Jan",
                "churn_pct": 1,
                "churn_percent": 2,
                "revenue_usd": 3,
            }
        ]
        resolved = resolve(chart, rows, chart_style_context=context)
        assert wide_measure_labels_for(
            resolved.wide_measures, resolved.field_labels
        ) == {
            "churn_pct": "churn_pct",
            "churn_percent": "churn_percent",
            "revenue_usd": "Revenue ($)",
        }

    def test_a_second_order_collision_through_the_raw_fallback_itself_falls_back_too(
        self, make_chart
    ):
        """Regression: the raw-name fallback is only collision-free if a
        raw name can never equal another measure's humanized label --
        false here. ``orders (Count)`` and ``Orders (Count)`` both
        humanize (lowercased) to ``orders (count)`` and fall back to
        their own raw names; but ``orders_cnt`` independently humanizes
        to ``orders (Count)`` -- which is now IDENTICAL to the first
        measure's raw-name fallback. A single fallback pass would leave
        two distinct measures both labeled ``orders (Count)``, exactly
        the silent-fold bug the whole mechanism exists to prevent. The
        fix must keep resolving until every measure's label is unique,
        cascading the fallback to ``orders_cnt`` too."""
        theme = get_theme_style()
        context = resolve_chart_style_context(theme)
        chart = make_chart(
            "bar",
            style={"orientation": "vertical", "stack": "zero"},
            x="month",
            y=["orders (Count)", "Orders (Count)", "orders_cnt"],
        )
        rows = [
            {
                "month": "Jan",
                "orders (Count)": 1,
                "Orders (Count)": 2,
                "orders_cnt": 3,
            }
        ]
        resolved = resolve(chart, rows, chart_style_context=context)
        labels = wide_measure_labels_for(resolved.wide_measures, resolved.field_labels)
        assert len(set(labels.values())) == 3, labels


class TestEmptyPaletteDoesNotSkipResolution:
    """Resolution must run at the five sites covered here (bar
    histogram/stacked-vertical/stacked-horizontal, line, area) even when
    `style.color.categorical.palette: []` is authored (legal YAML --
    CategoricalColorStyle has no non-empty validator) -- an empty
    palette governs only the color scale's RANGE, never the legend's own
    entry set. Authors lowercase spellings against an UPPERCASE real
    domain so a raw, unresolved pass-through (apply_color_legend copies
    `legend.values` verbatim) is distinguishable from the real,
    fold-resolved series spelling."""

    _EMPTY_PALETTE = {"color": {"categorical": {"palette": []}}}
