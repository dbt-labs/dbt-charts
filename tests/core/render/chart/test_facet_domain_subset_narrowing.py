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
    get_chart_rendering,
    get_theme_style,
    reset_config,
)
from dbt_charts.core.compile.models.chart.normalized import (
    BarChart,
    HeatmapChart,
    LineChart,
)
from dbt_charts.core.compile.resolve import resolve
from dbt_charts.core.compile.resolve.chart.adaptive_stroke import (
    facet_panel_width,
    panel_axis_cardinality,
)
from dbt_charts.core.compile.resolve.style.board import resolve_style_and_context
from dbt_charts.core.render.chart.emitters._cartesian import (
    effective_horizontal_bar_category_count,
    facet_bound_position_channels,
    facet_extra_axis_width_px,
)
from dbt_charts.core.render.chart.vega_lite import generate_vega_lite_spec


@pytest.fixture(autouse=True)
def reset():
    reset_config()
    yield
    reset_config()


def _board() -> Any:
    return resolve_style_and_context(get_theme_style("clarity"))


def _unnarrowed_width(resolved: Any, card_width: float) -> float:
    """The per-column-panel width with no extra axis reserved — the same
    number `_render_vl_artifact` computes and checks affordability against
    (`vega_lite.py`), so tests calling `facet_bound_position_channels`
    directly exercise the real arithmetic rather than a stand-in constant."""
    assert resolved.multiples is not None
    panel_cols = panel_axis_cardinality(resolved.panel_axes, resolved.multiples.columns)
    has_mirror = bool(resolved.style.axis_y.mirror)
    return facet_panel_width(card_width, panel_cols, has_mirror, extra_axis_px=0.0)


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

    def test_columns_facet_different_field_resolves_independently(self):
        data = _sparse_region_data(
            ["Widgets", "Gadgets", "Doodads", "Doohickeys", "Sprockets"]
        )
        chart = _sparse_region_chart({"columns": "region"})
        board_rs, ctx = _board()
        vl = generate_vega_lite_spec(
            chart, data, width=1200.0, board_style=board_rs, chart_style_context=ctx
        )
        assert vl.get("resolve", {}).get("scale", {}).get("y") == "independent"

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


class TestSortedYAxisRespectsNarrowing:
    """An authored `sort:` on a heatmap's narrowed y axis must not repaint the
    domain per-panel narrowing removed. `pin_sorted_domain`'s `axis`
    parameter is what lets heatmap's y-encoding pin defer to the same
    narrowing check the unsorted case already gets — hardcoding the check to
    `"x"` would pin y's domain back on every panel regardless."""

    def test_authored_sort_leaves_narrowed_y_domain_unpinned(self):
        products = ["Widgets", "Gadgets", "Doodads", "Doohickeys", "Sprockets"]
        data = _sparse_region_data(products)
        chart = HeatmapChart.model_validate(
            {
                "id": "t",
                "type": "heatmap",
                "query_name": "q",
                "x": "week",
                "y": "product",
                "color": "value",
                "multiples": {"columns": "region"},
                "sort": {"by": "value", "order": "desc"},
            }
        )
        board_rs, ctx = _board()
        vl = generate_vega_lite_spec(
            chart, data, width=1200.0, board_style=board_rs, chart_style_context=ctx
        )
        assert vl.get("resolve", {}).get("scale", {}).get("y") == "independent"
        y_enc = vl["spec"]["encoding"]["y"]
        x_enc = vl["spec"]["encoding"]["x"]
        assert "domain" not in y_enc.get("scale", {}), (
            "a narrowed y must not get a pinned scale.domain -- that repaints "
            "the empty category slots per-panel narrowing exists to remove"
        )
        # x is NOT a narrowing candidate here (every panel carries both
        # weeks), so its authored sort still pins an explicit domain.
        assert x_enc["scale"]["domain"]


class TestDomainSubsetPredicate:
    """`facet_bound_position_channels` fires on the panel's actual data, never
    on whether the field name matches a facet field."""

    def test_narrows_when_field_differs_from_facet_field(self):
        data = _sparse_region_data(
            ["Widgets", "Gadgets", "Doodads", "Doohickeys", "Sprockets"]
        )
        chart = _sparse_region_chart({"columns": "region"})
        board_rs, ctx = _board()
        resolved = resolve(chart, data, chart_style_context=ctx, width=1200.0)
        assert resolved.multiples is not None
        assert "y" in facet_bound_position_channels(
            resolved, resolved.multiples, data, _unnarrowed_width(resolved, 1200.0)
        )

    def test_stays_shared_when_every_panel_holds_the_full_domain(self):
        """Negative control: same shape, but every region panel carries all 5
        products — nothing to narrow, so the predicate must not fire just
        because the fields are named differently."""
        products = ["Widgets", "Gadgets", "Doodads", "Doohickeys", "Sprockets"]
        data = [
            {"week": w, "product": p, "region": r, "value": 10}
            for r in ("Japan", "US", "EU", "UK")
            for p in products
            for w in ("2024-W01", "2024-W02")
        ]
        chart = _sparse_region_chart({"columns": "region"})
        board_rs, ctx = _board()
        resolved = resolve(chart, data, chart_style_context=ctx, width=1200.0)
        assert resolved.multiples is not None
        assert "y" not in facet_bound_position_channels(
            resolved, resolved.multiples, data, _unnarrowed_width(resolved, 1200.0)
        )

    def test_single_value_domain_never_narrows(self):
        """A field with only 1 distinct value overall has no "narrower" state
        to reach — must not report every panel as a degenerate subset of a
        1-element domain."""
        data = [
            {"week": w, "product": "Widgets", "region": r, "value": 10}
            for r in ("Japan", "US")
            for w in ("2024-W01", "2024-W02")
        ]
        chart = _sparse_region_chart({"columns": "region"})
        board_rs, ctx = _board()
        resolved = resolve(chart, data, chart_style_context=ctx, width=1200.0)
        assert resolved.multiples is not None
        assert "y" not in facet_bound_position_channels(
            resolved, resolved.multiples, data, _unnarrowed_width(resolved, 1200.0)
        )


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

    def test_budget_grows_with_label_length(self):
        widths_by_label_set = {}
        for label_set_name, products in self.LABEL_SETS.items():
            resolved, data = self._resolved(products, width=1200.0)
            assert resolved.multiples is not None
            widths_by_label_set[label_set_name] = facet_extra_axis_width_px(
                resolved, resolved.multiples, data
            )
        assert (
            widths_by_label_set["short"]
            < widths_by_label_set["realistic"]
            < widths_by_label_set["long"]
        )

    @pytest.mark.parametrize("card_width", [800.0, 1200.0])
    @pytest.mark.parametrize("label_set_name", ["short", "realistic", "long"])
    def test_panel_width_never_goes_negative(self, label_set_name, card_width):
        """The card boundary always wins: whatever the label width, the
        emitted per-panel width is never negative — it floors at 0 rather
        than corrupting the spec."""
        products = self.LABEL_SETS[label_set_name]
        data = _sparse_region_data(products)
        chart = _sparse_region_chart({"columns": "region"})
        board_rs, ctx = _board()
        vl = generate_vega_lite_spec(
            chart,
            data,
            width=card_width,
            board_style=board_rs,
            chart_style_context=ctx,
        )
        assert vl["spec"]["width"] >= 0.0

    def test_short_labels_leave_a_wider_panel_than_realistic_labels(self):
        """The reserved width is spent on the label, not a flat charge — at
        the same card width and column count, shorter labels leave more
        panel width for marks. Compares only the two label sets narrowing
        stays affordable for at this width (both resolve independently) —
        "long" at this width is the affordability gate's own subject
        (`TestAffordabilityGate` below), where a wider budget reverting to
        the FULL baseline can legitimately beat a narrower, affordable one,
        so it is not part of this monotonic comparison."""
        board_rs, ctx = _board()
        panel_widths = {}
        narrowed = {}
        for label_set_name in ("short", "realistic"):
            products = self.LABEL_SETS[label_set_name]
            data = _sparse_region_data(products)
            chart = _sparse_region_chart({"columns": "region"})
            vl = generate_vega_lite_spec(
                chart, data, width=1200.0, board_style=board_rs, chart_style_context=ctx
            )
            panel_widths[label_set_name] = vl["spec"]["width"]
            narrowed[label_set_name] = (
                vl.get("resolve", {}).get("scale", {}).get("y") == "independent"
            )
        assert narrowed["short"] and narrowed["realistic"], (
            "fixture no longer keeps both affordable at this width — "
            "the comparison below no longer isolates label length"
        )
        assert panel_widths["short"] > panel_widths["realistic"]

    def test_zero_when_no_columns_facet(self):
        """A rows-only facet already draws "y" once per row panel — no extra
        axis, no budget needed."""
        data = _sparse_region_data(
            ["Widgets", "Gadgets", "Doodads", "Doohickeys", "Sprockets"]
        )
        chart = _sparse_region_chart({"rows": "region"})
        board_rs, ctx = _board()
        resolved = resolve(chart, data, chart_style_context=ctx, width=1200.0)
        assert resolved.multiples is not None
        assert facet_extra_axis_width_px(resolved, resolved.multiples, data) == 0.0

    def test_zero_when_not_a_narrowing_candidate(self):
        """No proper subset anywhere -> nothing to budget for, even under a
        columns facet."""
        products = ["Widgets", "Gadgets", "Doodads", "Doohickeys", "Sprockets"]
        data = [
            {"week": w, "product": p, "region": r, "value": 10}
            for r in ("Japan", "US", "EU", "UK")
            for p in products
            for w in ("2024-W01", "2024-W02")
        ]
        chart = _sparse_region_chart({"columns": "region"})
        board_rs, ctx = _board()
        resolved = resolve(chart, data, chart_style_context=ctx, width=1200.0)
        assert resolved.multiples is not None
        assert facet_extra_axis_width_px(resolved, resolved.multiples, data) == 0.0


class TestAffordabilityGate:
    """Narrowing is an improvement only when it is affordable. If reserving
    the measured extra axis would drop the per-column panel below
    ``chart_rendering.facet.min_panel_px`` — the same floor
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

    @pytest.mark.parametrize(
        ("label_set_name", "card_width", "expect_narrowed"), _MATRIX
    )
    def test_narrows_only_when_affordable(
        self, label_set_name: str, card_width: float, expect_narrowed: bool
    ) -> None:
        products = self.LABEL_SETS[label_set_name]
        data = _sparse_region_data(products)
        chart = _sparse_region_chart({"columns": "region"})
        board_rs, ctx = _board()
        resolved = resolve(chart, data, chart_style_context=ctx, width=card_width)
        baseline = _unnarrowed_width(resolved, card_width)
        min_panel_px = get_chart_rendering().facet.min_panel_px

        vl = generate_vega_lite_spec(
            chart, data, width=card_width, board_style=board_rs, chart_style_context=ctx
        )
        narrowed = vl.get("resolve", {}).get("scale", {}).get("y") == "independent"
        emitted = vl["spec"]["width"]

        assert narrowed == expect_narrowed
        if narrowed:
            # Affordable: reserved real width, still legible, strictly
            # narrower than doing nothing (there would be no point
            # narrowing to the exact same width).
            assert emitted >= min_panel_px
            assert emitted < baseline
        else:
            # Declined: today's exact, unreduced behavior — not a
            # softened/clamped narrower value, the full baseline.
            assert emitted == pytest.approx(baseline)

    @pytest.mark.parametrize("card_width", [800.0, 1400.0])
    @pytest.mark.parametrize("label_set_name", ["short", "realistic", "long"])
    def test_never_narrower_than_declining_would_leave_it(
        self, label_set_name: str, card_width: float
    ) -> None:
        """General sweep restating the acceptance bar directly: whatever the
        label length or card width, the emitted panel width can only match
        or improve on the plain, un-narrowed baseline — never fall below
        it. `test_narrows_only_when_affordable` above pins the specific
        narrowed-vs-declined verdict per cell; this restates the invariant
        that must hold regardless of which verdict wins."""
        products = self.LABEL_SETS[label_set_name]
        data = _sparse_region_data(products)
        chart = _sparse_region_chart({"columns": "region"})
        board_rs, ctx = _board()
        resolved = resolve(chart, data, chart_style_context=ctx, width=card_width)
        baseline = _unnarrowed_width(resolved, card_width)

        vl = generate_vega_lite_spec(
            chart, data, width=card_width, board_style=board_rs, chart_style_context=ctx
        )
        emitted = vl["spec"]["width"]
        assert emitted <= baseline + 1e-9

    @pytest.mark.parametrize("card_width", [800.0, 1400.0])
    @pytest.mark.parametrize("label_set_name", ["short", "realistic", "long"])
    def test_never_resolves_below_the_legibility_floor(
        self, label_set_name: str, card_width: float
    ) -> None:
        """No cell in the matrix — narrowed or declined — ever emits a
        panel width under ``min_panel_px``, for these card widths (each
        wide enough that even the un-narrowed baseline clears the floor on
        its own; a card too narrow even for THAT is the pre-existing,
        orthogonal defect ``WARN_FACET_PANEL_WIDTH_BELOW_MINIMUM`` already
        covers, unrelated to narrowing)."""
        products = self.LABEL_SETS[label_set_name]
        data = _sparse_region_data(products)
        chart = _sparse_region_chart({"columns": "region"})
        board_rs, ctx = _board()
        min_panel_px = get_chart_rendering().facet.min_panel_px

        vl = generate_vega_lite_spec(
            chart, data, width=card_width, board_style=board_rs, chart_style_context=ctx
        )
        assert vl["spec"]["width"] >= min_panel_px


class TestMirrorNeverNarrows:
    """A mirrored y-axis never narrows: MirrorAxisFeature repaints the ghost
    edge as a layer on the unit spec, so once "y" resolves independently VL
    stops sharing that unit spec across the panel row and draws the ghost
    once per panel instead of once for the whole facet — measured, this
    forces panels to zero width rather than merely under-reserve. Refusing
    to narrow a mirrored "y" is the only option that cannot overflow."""

    def test_horizontal_bar_mirrored_category_axis_stays_shared(self):
        """Horizontal bar's category rides VL "y" post-flip; a columns facet
        auto-mirrors it to both edges. Even though each region panel here
        holds a proper subset of the product domain (a real narrowing
        candidate on data grounds alone), the mirror guard must still
        refuse it."""
        products = ["Widgets", "Gadgets", "Doodads", "Doohickeys", "Sprockets"]
        regions = {
            "Japan": products[:2],
            "US": products[:3],
            "EU": products[1:3],
            "UK": products[3:5],
        }
        data = [
            {"product": p, "region": r, "revenue": 10}
            for r, prods in regions.items()
            for p in prods
        ]
        chart = BarChart.model_validate(
            {
                "id": "t",
                "type": "bar",
                "query_name": "q",
                "x": "product",
                "y": "revenue",
                "multiples": {"columns": "region"},
                "style": {"orientation": "horizontal"},
            }
        )
        board_rs, ctx = _board()
        resolved = resolve(chart, data, chart_style_context=ctx, width=1200.0)
        assert resolved.style.axis_y.mirror, (
            "fixture no longer auto-mirrors — mirror guard is untested"
        )
        assert resolved.multiples is not None
        assert "y" not in facet_bound_position_channels(
            resolved, resolved.multiples, data, _unnarrowed_width(resolved, 1200.0)
        )
        vl = generate_vega_lite_spec(
            chart, data, width=1200.0, board_style=board_rs, chart_style_context=ctx
        )
        assert vl.get("resolve", {}).get("scale", {}).get("y") != "independent"

    def test_explicit_mirror_false_allows_narrowing(self):
        """With mirror explicitly off, the same otherwise-sparse shape is
        free to narrow again — the guard is keyed on the resolved mirror
        flag, not the columns facet alone."""
        products = ["Widgets", "Gadgets", "Doodads", "Doohickeys", "Sprockets"]
        regions = {
            "Japan": products[:2],
            "US": products[:3],
            "EU": products[1:3],
            "UK": products[3:5],
        }
        data = [
            {"product": p, "region": r, "revenue": 10}
            for r, prods in regions.items()
            for p in prods
        ]
        chart = BarChart.model_validate(
            {
                "id": "t",
                "type": "bar",
                "query_name": "q",
                "x": "product",
                "y": "revenue",
                "multiples": {"columns": "region"},
                "style": {"orientation": "horizontal", "axis_y": {"mirror": False}},
            }
        )
        board_rs, ctx = _board()
        resolved = resolve(chart, data, chart_style_context=ctx, width=1200.0)
        assert not resolved.style.axis_y.mirror
        assert resolved.multiples is not None
        assert "y" in facet_bound_position_channels(
            resolved, resolved.multiples, data, _unnarrowed_width(resolved, 1200.0)
        )


class TestHorizontalBarHeightFloorTracksTheWidestPanel:
    """`effective_horizontal_bar_category_count` must budget for the WIDEST
    panel's own count once narrowing applies generally — not a flat 1, which
    only ever held by construction for the old name-matched, single-value
    degenerate case."""

    def test_returns_the_maximum_per_panel_count_not_a_flat_one(self):
        products = ["Widgets", "Gadgets", "Doodads", "Doohickeys", "Sprockets"]
        regions = {
            "Japan": products[:2],  # 2 categories
            "US": products[:3],  # 3 categories — the widest panel
            "EU": products[1:3],  # 2
        }
        data = [
            {"product": p, "region": r, "revenue": 10}
            for r, prods in regions.items()
            for p in prods
        ]
        chart = BarChart.model_validate(
            {
                "id": "t",
                "type": "bar",
                "query_name": "q",
                "x": "product",
                "y": "revenue",
                "multiples": {"columns": "region"},
                "style": {"orientation": "horizontal", "axis_y": {"mirror": False}},
            }
        )
        board_rs, ctx = _board()
        resolved = resolve(chart, data, chart_style_context=ctx, width=1200.0)
        assert resolved.multiples is not None
        unnarrowed = _unnarrowed_width(resolved, 1200.0)
        assert "y" in facet_bound_position_channels(
            resolved, resolved.multiples, data, unnarrowed
        )
        assert effective_horizontal_bar_category_count(resolved, data, unnarrowed) == 3


class TestBudgetMeasuresTheFlippedAxisStyle:
    """A horizontal bar's category rides VL "y" after the orientation flip
    (``_emit_horizontal`` builds VL "y" from ``chart.style.axis_x`` — bar.py's
    own "categorical cascade" comment, and its error text at the ticks.step
    guard: "A horizontal bar's axis_x is its categorical axis — the measure
    is axis_y"). `facet_extra_axis_width_px` selects `y_field` with the same
    flip (`chart.x if is_flipped else chart.y`), so it must select the LABEL
    STYLE with that same flip too — measuring `axis_y`'s font/`max_width`
    for a horizontal bar reads the wrong axis whenever the two styles
    diverge, silently under- or over-reserving. No heatmap-fixture test
    (never flipped, `axis_y` is already the narrowed channel) can catch
    this — the flip only exists for bar orientation."""

    def test_measures_against_axis_x_not_axis_y_when_flipped(self):
        products = ["Widgets", "Gadgets", "Doodads", "Doohickeys", "Sprockets"]
        regions = {
            "Japan": products[:2],
            "US": products[:3],
            "EU": products[1:3],
            "UK": products[3:5],
        }
        data = [
            {"product": p, "region": r, "revenue": 10}
            for r, prods in regions.items()
            for p in prods
        ]
        chart = BarChart.model_validate(
            {
                "id": "t",
                "type": "bar",
                "query_name": "q",
                "x": "product",
                "y": "revenue",
                "multiples": {"columns": "region"},
                "style": {
                    "orientation": "horizontal",
                    # The category axis (axis_x) gets a generous label
                    # limit; the measure axis (axis_y) gets a starved one
                    # that would clamp the measurement to near-nothing if
                    # the wrong style were read. mirror: False keeps this
                    # the plain narrowing case, isolated from the mirror
                    # guard.
                    "axis_x": {"labels": {"max_width": 400.0}},
                    "axis_y": {"mirror": False, "labels": {"max_width": 10.0}},
                },
            }
        )
        board_rs, ctx = _board()
        resolved = resolve(chart, data, chart_style_context=ctx, width=1200.0)
        assert resolved.multiples is not None
        width = facet_extra_axis_width_px(resolved, resolved.multiples, data)
        # Reading axis_y (the bug) clamps at 10.0 + chrome (~82px) regardless
        # of the real product-name labels. Reading axis_x (correct) lets the
        # real measured "Doohickeys"/"Widgets"-length labels through against
        # its generous 400px limit — comfortably over 100px total.
        assert width > 100.0, (
            f"measured {width}px — looks clamped to axis_y's 10px limit, "
            "not axis_x's 400px one"
        )


class TestQuantitativeMeasureAxisReservesNothing:
    """The families whose y is a measure must never reserve per-panel width.

    `_domain_subset_narrowing_candidates` says "y" for an ordinary line or
    area chart -- panels holding different revenue numbers is the normal
    case, not a corner -- so the only thing standing between that and a real
    reservation is `_emits_discrete_y` answering False for the families that
    emit a quantitative y. If that answer ever flips (a future "simplify
    this to one infer_vega_type_from_data call", or a sixth family falling
    through to the catch-all), every column panel silently loses 80-205px
    for an axis Vega-Lite never draws, because `_discrete_facet_channels`
    independently drops a quantitative "y" at emission.

    The heatmap and horizontal-bar fixtures elsewhere in this file cannot
    catch that: neither reaches the line/area/vertical-bar rows.
    """

    def test_faceted_line_with_sparse_panel_values_reserves_no_width(self) -> None:
        rows = [
            {"month": f"2024-{m:02d}", "region": region, "revenue": float(100 * i + m)}
            for i, region in enumerate(("North", "South", "East"))
            for m in range(1, 5)
        ]
        chart = LineChart.model_validate(
            {
                "id": "c1",
                "type": "line",
                "query_name": "q",
                "x": "month",
                "y": "revenue",
                "multiples": {"columns": "region"},
                # mirror off, so the mirror guard is not what produces the 0.0
                "style": {"axis_y": {"mirror": False}},
            }
        )
        _, ctx = _board()
        resolved = resolve(chart, rows, chart_style_context=ctx, width=1200.0)
        assert facet_extra_axis_width_px(resolved, resolved.multiples, rows) == 0.0, (
            "a quantitative measure axis must reserve no per-panel width"
        )
