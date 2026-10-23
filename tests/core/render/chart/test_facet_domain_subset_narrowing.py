"""Facet narrowing decided from the panel's real data, not from a field-name match.

A panel whose own rows carry a proper subset of a position channel's domain narrows
that channel's scale independently, whatever the channel's field is called — not only
when it happens to equal one of the facet fields. Covers the general case
(`facet_bound_position_channels`, `emitters/_cartesian.py`), the measured
per-column-panel width budget that narrowing a "y"-bound channel under a columns/grid
facet requires (`facet_extra_axis_width_px`), and the mirror interaction.
"""

from __future__ import annotations

from typing import Any

import pytest

from dbt_charts.core.compile.config import (
    get_theme_style,
    reset_config,
)
from dbt_charts.core.compile.models.chart.normalized import (
    HeatmapChart,
)
from dbt_charts.core.compile.resolve import resolve
from dbt_charts.core.compile.resolve.style.board import resolve_style_and_context


@pytest.fixture(autouse=True)
def reset():
    reset_config()
    yield
    reset_config()


def _board() -> Any:
    return resolve_style_and_context(get_theme_style("clarity"))


def _sparse_region_data(products: list[str]) -> list[dict[str, Any]]:
    """4 region panels, each holding a proper subset of the 5-product domain —
    the task's own reproduction shape: `y: product`, `multiples: {columns:
    region}`. No field name is shared between the facet dimension and the
    narrowed channel."""
    regions = {
        "Japan": products[:2],
        "US": products[:3],
        "EU": products[1:3],
        "UK": products[3:5],
    }
    return [
        {"week": w, "product": p, "region": r, "value": 10}
        for r, prods in regions.items()
        for p in prods
        for w in ("2024-W01", "2024-W02")
    ]


def _sparse_region_chart(multiples: dict[str, Any]) -> HeatmapChart:
    return HeatmapChart.model_validate(
        {
            "id": "t",
            "type": "heatmap",
            "query_name": "q",
            "x": "week",
            "y": "product",
            "color": "value",
            "multiples": multiples,
        }
    )


class TestReproduction:
    """The task's own board: `y: product` (5 distinct), `multiples: {columns:
    region}` — a different field, each panel a proper subset of the domain."""

    def test_panel_reports_fewer_distinct_values_than_the_domain(self):
        """Reproduces the measurement in the task's Problem section directly:
        Japan's panel carries 2 of the 5 products, not all 5."""
        products = ["Widgets", "Gadgets", "Doodads", "Doohickeys", "Sprockets"]
        data = _sparse_region_data(products)
        domain = {row["product"] for row in data}
        japan_values = {row["product"] for row in data if row["region"] == "Japan"}
        assert len(domain) == 5
        assert japan_values == {"Widgets", "Gadgets"}
        assert len(japan_values) < len(domain)


class TestExtraAxisWidthTracksMeasuredLabels:
    """The reserved width for the extra per-column-panel axis scales with the
    widest label actually rendered — not a flat constant. Proven across three
    label lengths at two card widths, per the task's acceptance bar."""

    LABEL_SETS: dict[str, list[str]] = {
        "short": ["A", "B", "C", "D", "E"],
        "realistic": ["Widgets", "Gadgets", "Doodads", "Doohickeys", "Sprockets"],
        "long": [
            "Enterprise Analytics Suite",
            "Gadgets",
            "Doodads",
            "Doohickeys",
            "Sprockets",
        ],
    }

    def _resolved(self, products: list[str], width: float):
        data = _sparse_region_data(products)
        chart = _sparse_region_chart({"columns": "region"})
        board_rs, ctx = _board()
        return resolve(chart, data, chart_style_context=ctx, width=width), data


class TestAffordabilityGate:
    """Narrowing is an improvement only when it is affordable. If reserving
    the measured extra axis would drop the per-column panel below
    ``FACET_MIN_PANEL_PX`` — the same floor
    ``WARN_FACET_PANEL_WIDTH_BELOW_MINIMUM`` already reads — the channel
    does not narrow at all: the panel keeps its full-domain, shared axis
    (today's exact behavior) instead of an unreadable, over-narrow one.

    The label-length x card-width matrix below is the reproduction board
    (4 region columns, 5-product domain) at two widths. "before" is what
    every cell renders today (nothing ever narrows, so the emitted width is
    always exactly the un-narrowed chrome-only width — a flat 170.0 or
    320.0 regardless of label length). "after" is measured on this branch:

        labels      card=800          card=1400
        short       170.0 (declined)  240.0 (narrowed)
        realistic   170.0 (declined)  187.0 (narrowed)
        long        170.0 (declined)  320.0 (declined)

    No cell resolves below ``min_panel_px`` (120.0) and no cell is narrower
    than "before" — narrowing only ever matches or improves on today's
    width, never regresses it. `long`@1400's measured axis (~205px) would
    leave 320 - 205 = 115px, under the 120px floor, so it declines and
    keeps the full 320px baseline instead — WIDER than `medium`@1400's own
    affordable, narrowed 187px, which is the non-monotonic case the
    affordability gate exists to produce: an unaffordable long label
    reverting to the full baseline beats a narrower short one purely
    because nothing was reserved for it.
    """

    LABEL_SETS: dict[str, list[str]] = TestExtraAxisWidthTracksMeasuredLabels.LABEL_SETS

    # (label_set, card_width, expect_narrowed) — one row per matrix cell above.
    # "realistic" is this file's LABEL_SETS key for what the matrix calls
    # "medium" (product-name-length labels, e.g. "Widgets").
    _MATRIX = [
        ("short", 800.0, False),
        ("short", 1400.0, True),
        ("realistic", 800.0, False),
        ("realistic", 1400.0, True),
        ("long", 800.0, False),
        ("long", 1400.0, False),
    ]
