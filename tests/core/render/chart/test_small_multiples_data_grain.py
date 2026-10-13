"""Emitted-spec regression tests for small-multiples data grain and geometry.

Covers both the DATA defects (rows silently dropped, a phantom `null` panel,
a y domain inflated by the panel count, `scale: independent` being inert) and
GEOMETRY (panel width/height using the panel's own dimensions, not the whole
card's).

Reproduction A (rows-only, sparse tail) is the shared ..._small_multiples_repro_a
fixture; reproduction B (columns-only, dense) is built inline below
(TestReproductionBColumnsOnlyDense). Both construct the normalized chart in
Python rather than from committed YAML.
"""

from __future__ import annotations

from typing import Any

import pytest

from dbt_charts.core.compile.config import get_theme_style, reset_config
from dbt_charts.core.compile.models.chart.normalized import (
    AreaChart,
    BarChart,
    LineChart,
)
from dbt_charts.core.compile.models.chart.resolved import PartitionAxis
from dbt_charts.core.compile.resolve import resolve
from dbt_charts.core.compile.resolve.style.board import resolve_style_and_context
from dbt_charts.core.diagnostics.chart_data import ChartDataError
from dbt_charts.core.render.chart.spec import RenderBox

from ..._small_multiples_repro_a import (
    repro_a_chart as _repro_a_chart,
    repro_a_rows as _repro_a_rows,
)

_DEFAULT_BOX = RenderBox(width=600.0, height=300.0)


@pytest.fixture(autouse=True)
def reset():
    reset_config()
    yield
    reset_config()


def _board() -> Any:
    return resolve_style_and_context(get_theme_style("clarity"))


def _v2_vl(norm: Any, data: list[dict[str, Any]]) -> dict[str, Any]:
    from dbt_charts.core.render.chart.session import BoardRenderSession

    board_rs, board_ctx = _board()
    resolved = resolve(norm, data, chart_style_context=board_ctx)
    session = BoardRenderSession.create(board_rs)
    return session.finalize_vl(
        session.emit_chart(resolved, _DEFAULT_BOX, {resolved.query_name: data})
    )


# ---------------------------------------------------------------------------
# Reproduction A: rows-only, 11 rows, 2 families x 2 event types x 4 months
# (sparse tail). Shared with test_stacked_domain_multiples_fold.py via
# ..._small_multiples_repro_a so the two suites can't silently drift on the
# exact row shape.
# ---------------------------------------------------------------------------


class TestReproductionARowsPreserved:
    """Every source row survives, no synthesized-null partition value.

    Gap-fill legitimately adds rows to complete each panel's own bucket
    range (see ``TestReproductionAPerPanelGapFillRange``), so the emitted
    count is >= the source count, not equal to it — every *source* row must
    still be present, and no row's partition value may be None (the
    phantom-panel defect).
    """

    def test_no_row_dropped_and_no_null_partition_value(self):
        source = _repro_a_rows()
        spec = _v2_vl(_repro_a_chart(), source)
        data = spec["data"]["values"]
        assert len(data) >= len(source)
        emitted_keys = {
            (row["month"], row["plan_family"], row["event_type"]) for row in data
        }
        for row in source:
            assert (row["month"], row["plan_family"], row["event_type"]) in emitted_keys
        assert all(row.get("event_type") is not None for row in data)

    def test_panel_count_is_two(self):
        spec = _v2_vl(_repro_a_chart(), _repro_a_rows())
        panel_values = {row["event_type"] for row in spec["data"]["values"]}
        assert panel_values == {"event1", "event2"}


class TestReproductionAPerPanelGapFillRange:
    """event2's panel ends Aug, event1's reaches Sep — different per-panel
    bucket sets. Pins the emitted row count per panel, which
    ``TestReproductionARowsPreserved``'s tests both pass without noticing.

    Each panel completes only its OWN range (never the pooled range —
    see ``gap_fill_ordinal_time_per_panel``'s docstring on why pooling the
    enumerated bucket range would explode a chart with narrow, individually
    dense but far-apart panels). The wire order across panels is still
    chronological (``TestReproductionAWireOrderIsChronologicalAcrossPanels``
    below), via a final sort rather than a shared bucket set.
    """

    def test_each_panel_completes_only_its_own_bucket_range(self):
        spec = _v2_vl(_repro_a_chart(), _repro_a_rows())
        data = spec["data"]["values"]
        event1_months = {row["month"] for row in data if row["event_type"] == "event1"}
        event2_months = {row["month"] for row in data if row["event_type"] == "event2"}
        assert "2026-09-01" in event1_months
        assert "2026-09-01" not in event2_months


class TestReproductionAWireOrderIsChronologicalAcrossPanels:
    """The emitted spec['data']['values'] wire order determines the ordinal
    x scale's shared (encounter-order) domain for a faceted chart. Even
    though event1's and event2's own bucket ranges are disjoint tails
    (previous test), the FLAT order across both panels must still read
    chronologically — the exact regression this pins: panel-order
    concatenation of disjoint per-panel ranges reading non-chronologically
    (e.g. Mar,Apr,May,Jan,Feb instead of Jan..May)."""

    def test_month_field_is_non_decreasing_across_the_flat_wire_order(self):
        spec = _v2_vl(_repro_a_chart(), _repro_a_rows())
        months = [row["month"] for row in spec["data"]["values"]]
        assert months == sorted(months)


class TestAuthoredFillKeepsChronologicalWireOrder:
    """An authored ``fill`` re-bands a chart the scaffold gate sent temporal,
    and the wire order must still read chronologically.

    ``is_temporal`` is decided on the RAW rows, but only ``fill: null`` lets
    it short-circuit gap-fill. Any other authored fill enumerates anyway, and
    the emitter then re-resolves against those deficit-0 filled rows and lands
    back on ORDINAL. An ordinal band scale takes its domain from encounter
    order, so gating the pooled chronological re-sort on the pre-fill verdict
    leaves the axis reading 2020→2021 then 2000→2001. Regression against
    main, where a fine grain always resolved ordinal and the re-sort ran.
    """

    @staticmethod
    def _rows() -> list[dict[str, Any]]:
        import datetime as dt

        out: list[dict[str, Any]] = []
        # Later panel first in query order, so panel-order concatenation is
        # non-chronological unless the pooled re-sort runs.
        for panel, start in (("b", dt.date(2020, 1, 1)), ("a", dt.date(2000, 1, 1))):
            for i in range(10):
                out.append(
                    {
                        "day": (start + dt.timedelta(days=40 * i)).isoformat(),
                        "cnt": i + 1,
                        "panel": panel,
                    }
                )
        return out

    def test_fill_zero_wire_order_is_chronological(self):
        chart = BarChart.model_validate(
            {
                "id": "c",
                "type": "bar",
                "query_name": "q",
                "x": "day",
                "y": "cnt",
                "multiples": {"rows": "panel"},
                "style": {"axis_x": {"fill": "zero"}},
            }
        )
        days = [row["day"] for row in _v2_vl(chart, self._rows())["data"]["values"]]
        assert days == sorted(days)


class TestFacetedFineGrainGapFillFires:
    """The scaffold-budget gate must reach ``gap_fill_ordinal_time_per_panel``
    with the chart's partition fields.

    That call site decides ``is_temporal``, which routes gap-fill. It is a
    SEPARATE re-derivation from the one ``bar.py`` runs for the encoding, and
    the two are contracted to agree. Measure the budget on pooled rows there
    and a faceted fine-grain bar resolves ``is_temporal=True`` — gap-fill
    early-returns having synthesized nothing — while the emitter resolves the
    x encoding ordinal, leaving a band axis without the scaffold rows the
    ordinal branch is contracted to have. Every other gate test drives
    ``build_cartesian_x_encoding`` directly and cannot see that divergence.
    """

    @staticmethod
    def _chart() -> Any:
        return BarChart.model_validate(
            {
                "id": "c",
                "type": "bar",
                "query_name": "q",
                "x": "day",
                "y": "cnt",
                "multiples": {"rows": "panel"},
            }
        )

    @staticmethod
    def _rows() -> list[dict[str, Any]]:
        # Two panels of contiguous dailies twenty years apart: each panel is
        # individually dense (zero synthesized buckets), so banding stands and
        # gap-fill must run per panel. A pooled span measurement sees a
        # ~7,200-bucket deficit and flips the chart continuous instead.
        import datetime as dt

        # Five interior days are missing from each panel, so gap-fill has
        # something to synthesize: firing and not firing are distinguishable
        # in the emitted row count. Contiguous data would look identical
        # either way and pin nothing.
        out: list[dict[str, Any]] = []
        for panel, start in (("a", dt.date(2000, 1, 1)), ("b", dt.date(2020, 1, 1))):
            for i in range(40):
                if i in (7, 8, 19, 20, 31):
                    continue
                out.append(
                    {
                        "day": (start + dt.timedelta(days=i)).isoformat(),
                        "cnt": i + 1,
                        "panel": panel,
                    }
                )
        return out

    def test_faceted_dense_daily_bands_and_keeps_every_panel_in_its_own_range(self):
        spec = _v2_vl(self._chart(), self._rows())
        data = spec["data"]["values"]
        panel_a = {row["day"] for row in data if row["panel"] == "a"}
        panel_b = {row["day"] for row in data if row["panel"] == "b"}
        # Each panel keeps its own 40-day window and neither is scaffolded
        # across the twenty-year gap between them.
        assert panel_a and panel_b
        assert max(panel_a) < "2001-01-01"
        assert min(panel_b) > "2019-12-31"
        # 35 source rows per panel; gap-fill completes each panel's own
        # 40-day range. A pooled-span measurement resolves the chart temporal
        # instead, gap-fill early-returns, and this stays at 70.
        assert len(data) == 80
        assert "2000-01-08" in panel_a
        assert "2020-01-08" in panel_b
        # The emitted encoding, not just the row set: bar.py re-resolves the x
        # type on the filled rows through its OWN call site, and every
        # assertion above is decided upstream in _channels.py. Without the
        # panel fields there, this silently reads "temporal" — each panel's
        # 40-day window collapsing onto a shared twenty-year continuous
        # domain — while the row count and per-panel membership stay correct.
        x_enc = spec["spec"]["encoding"]["x"]
        assert x_enc["type"] == "ordinal", x_enc
        assert x_enc.get("timeUnit") in (None, "yearmonthdate"), x_enc

    def test_faceted_sparse_daily_resolves_temporal_in_the_emitted_encoding(self):
        # The flip direction: each panel individually sparser than its detected
        # grain is over budget in every panel, so the chart genuinely belongs on
        # a continuous scale. Pins that the gate still fires through bar.py's
        # call site rather than being disabled wholesale by the panel fix.
        import datetime as dt

        rows: list[dict[str, Any]] = []
        for panel, start in (("a", dt.date(2000, 1, 1)), ("b", dt.date(2020, 1, 1))):
            for i in range(10):
                rows.append(
                    {
                        "day": (start + dt.timedelta(days=45 * i)).isoformat(),
                        "cnt": i + 1,
                        "panel": panel,
                    }
                )
        spec = _v2_vl(self._chart(), rows)
        x_enc = spec["spec"]["encoding"]["x"]
        assert x_enc["type"] == "temporal", x_enc
        assert "timeUnit" not in x_enc, x_enc


class TestPooledResortSkippedForNonFacetedChart:
    """The cross-panel chronological re-sort in
    ``gap_fill_ordinal_time_per_panel`` (pinned by the previous test) must
    not run for a NON-faceted (N=1) chart. It is gated on ``x_field and
    is_temporal is not True`` alone, not on panel count — so before the fix
    it ran unconditionally, including on ``complete_ordinal_time_series``'s
    identity-return path (authored ``time_unit`` on a column with no
    parseable dates: bucketing never fires, rows come back unmodified, in
    the query's own order). ``canonicalize_and_sort_ordinal_x`` on that
    non-chronological-by-construction data falls back to alphabetical,
    silently replacing the query's own ORDER BY with alphabetical order.
    No ``multiples:`` anywhere -- this is the plain single-panel path.
    """

    def test_wire_order_matches_query_order_not_alphabetical(self):
        chart = BarChart.model_validate(
            {
                "id": "t",
                "type": "bar",
                "query_name": "q",
                "x": "seg",
                "y": "revenue",
                "style": {"axis_x": {"time_unit": "yearmonth"}},
            }
        )
        # Query's own ORDER BY revenue DESC -- not alphabetical (which would
        # read Amber, Core, Growth).
        rows = [
            {"seg": "Core", "revenue": 60},
            {"seg": "Growth", "revenue": 17},
            {"seg": "Amber", "revenue": 5},
        ]
        spec = _v2_vl(chart, rows)
        wire_order = [row["seg"] for row in spec["data"]["values"]]
        assert wire_order == ["Core", "Growth", "Amber"]


class TestPooledResortSkippedWhenNoPanelBucketed:
    """The faceted half of the same defect. Gating the pooled re-sort on
    ``len(dataset.panels) > 1`` closed the N=1 case above but left every
    faceted chart exposed: panel count was never the unsafe property.
    ``canonicalize_and_sort_ordinal_x`` is not idempotent on
    ``complete_ordinal_time_series``'s identity-return path, and a faceted
    chart on an unbucketable x column takes that same path -- so the sort ran
    across panels and replaced the query's ORDER BY with alphabetical order
    for every panel's bands.

    The gate is now ``bucketed_any``: whether any panel's rows actually came
    back as a different list object. That closes both halves with one
    condition, and keeps the disjoint-panel chronological fix (pinned by
    ``TestReproductionAWireOrderIsChronologicalAcrossPanels``), where
    bucketing genuinely does fire.
    """

    def test_faceted_wire_order_matches_query_order_not_alphabetical(self):
        chart = BarChart.model_validate(
            {
                "id": "t",
                "type": "bar",
                "query_name": "q",
                "x": "seg",
                "y": "revenue",
                "style": {"axis_x": {"time_unit": "yearmonth"}},
                "multiples": {"rows": "region"},
            }
        )
        rows = [
            {"seg": "Core", "region": "north", "revenue": 60},
            {"seg": "Growth", "region": "north", "revenue": 17},
            {"seg": "Amber", "region": "north", "revenue": 5},
            {"seg": "Core", "region": "south", "revenue": 40},
            {"seg": "Growth", "region": "south", "revenue": 12},
            {"seg": "Amber", "region": "south", "revenue": 3},
        ]
        spec = _v2_vl(chart, rows)
        wire_order = [row["seg"] for row in spec["data"]["values"]]
        # Alphabetical would read Amber, Amber, Core, Core, Growth, Growth.
        assert wire_order == ["Core", "Growth", "Amber"] * 2


class TestGapFillOrdinalTimeRequiresIsTemporal:
    """``gap_fill_ordinal_time`` used to re-derive ``is_temporal`` via
    ``resolve_cartesian_x_type`` whenever its caller passed ``None`` (the
    parameter's old default). That branch was unreachable: its one
    production caller (``gap_fill_ordinal_time_per_panel``) only omits
    ``is_temporal`` when the enclosing ``resolves_cartesian_x`` gate this
    branch also requires is already False -- confirmed by instrumenting the
    branch with an unconditional raise and running the full core suite
    (15677 tests) without it firing once. ``is_temporal`` is now a required
    keyword-only parameter (no default), so the dead branch can't silently
    come back by re-adding one. Also pins that the temporal vs. ordinal
    branch selection still matches ``is_temporal`` byte-for-byte now that
    it's read directly instead of through the deleted recompute.
    """

    @staticmethod
    def _resolved_axis_x():
        chart = LineChart.model_validate(
            {
                "id": "t",
                "type": "line",
                "query_name": "q",
                "x": "month",
                "y": "revenue",
            }
        )
        rows = [
            {"month": "2024-01-01", "revenue": 100},
            {"month": "2024-02-01", "revenue": 200},
            {"month": "2024-05-01", "revenue": 500},
        ]
        board_rs, ctx = _board()
        resolved = resolve(chart, rows, chart_style_context=ctx)
        return resolved.style.axis_x, rows

    def test_is_temporal_is_keyword_only_with_no_default(self):
        from dbt_charts.core.render.chart.emitters._channels import (
            gap_fill_ordinal_time,
        )

        ax, rows = self._resolved_axis_x()
        with pytest.raises(TypeError):
            gap_fill_ordinal_time(  # type: ignore[call-arg]
                ax, "month", None, rows, True, detected_time_unit="yearmonth"
            )

    def test_is_temporal_true_skips_bucket_synthesis(self):
        from dbt_charts.core.render.chart.emitters._channels import (
            gap_fill_ordinal_time,
        )

        ax, rows = self._resolved_axis_x()
        filled, x_authored_temporal = gap_fill_ordinal_time(
            ax,
            "month",
            None,
            rows,
            True,
            is_temporal=True,
            detected_time_unit="yearmonth",
            x_date_range=None,
        )
        assert filled is not None
        # Continuous temporal scale: sorted/canonicalized, no missing
        # (Mar, Apr) buckets synthesized.
        assert len(filled) == 3
        assert x_authored_temporal is False

    def test_is_temporal_false_synthesizes_missing_buckets(self):
        from dbt_charts.core.render.chart.emitters._channels import (
            gap_fill_ordinal_time,
        )

        ax, rows = self._resolved_axis_x()
        filled, x_authored_temporal = gap_fill_ordinal_time(
            ax,
            "month",
            None,
            rows,
            True,
            is_temporal=False,
            detected_time_unit="yearmonth",
            x_date_range=None,
        )
        assert filled is not None
        # Ordinal bucketed scale: Mar and Apr synthesized as null rows.
        assert len(filled) == 5
        assert x_authored_temporal is False


class TestFlatWireOrderMatchesQueryOrderForSparseFacetedNominalX:
    """The flat spec['data']['values'] wire order must match the QUERY's own
    row order, not partition()'s panel-grouped order, for a sparse faceted
    NOMINAL (non-date) x chart with interleaved query rows.

    Vega-Lite derives a nominal x encoding's shared domain from data
    ENCOUNTER order (no authored sort) -- panelizing the rows into West/East
    groups must not silently reshuffle that domain just because the panel
    split happens to interleave differently than the query did. No gap-fill
    is in play here (widget_type is nominal, not a date), so this exercises
    the plain (non-map_panels) all_rows() path end to end.
    """

    def test_flat_data_values_preserve_query_row_order(self):
        rows = [
            {"region": "West", "widget_type": "Alpha", "revenue": 1},
            {"region": "East", "widget_type": "Beta", "revenue": 2},
            {"region": "West", "widget_type": "Gamma", "revenue": 3},
            {"region": "East", "widget_type": "Alpha", "revenue": 4},
        ]
        chart = BarChart.model_validate(
            {
                "id": "c",
                "type": "bar",
                "query_name": "q",
                "x": "widget_type",
                "y": "revenue",
                "multiples": {"rows": "region"},
            }
        )
        spec = _v2_vl(chart, rows)
        data = spec["data"]["values"]
        assert [(row["widget_type"], row["region"]) for row in data] == [
            ("Alpha", "West"),
            ("Beta", "East"),
            ("Gamma", "West"),
            ("Alpha", "East"),
        ]


class TestReproductionBColumnsOnlyDense:
    """Columns-only, dense grid. All four panels present, every row
    survives (full per-panel cross-product)."""

    @staticmethod
    def _rows() -> list[dict[str, Any]]:
        rows: list[dict[str, Any]] = []
        for month_num in range(1, 5):
            month = f"2026-{month_num:02d}-01"
            for family in ("family1", "family2"):
                for event in ("event1", "event2", "event3", "event4"):
                    rows.append(
                        {
                            "month": month,
                            "plan_family": family,
                            "event_type": event,
                            "cnt": 5,
                        }
                    )
        return rows

    def test_all_panels_present_and_full_cross_product_emitted(self):
        chart = _repro_a_chart(multiples={"columns": "event_type"})
        rows = self._rows()
        spec = _v2_vl(chart, rows)
        data = spec["data"]["values"]
        assert {row["event_type"] for row in data} == {
            "event1",
            "event2",
            "event3",
            "event4",
        }
        assert len(data) == len(rows)


class TestGrid:
    """Grid panel keys are observed (row, col) cells only."""

    def test_no_cell_synthesized_for_an_absent_combination(self):
        rows = [
            {"region": "West", "product": "Widgets", "month": "Jan", "revenue": 10},
            {"region": "East", "product": "Gadgets", "month": "Jan", "revenue": 5},
        ]
        chart = AreaChart.model_validate(
            {
                "id": "t",
                "type": "area",
                "query_name": "q",
                "x": "month",
                "y": "revenue",
                "multiples": {"rows": "region", "columns": "product"},
            }
        )
        spec = _v2_vl(chart, rows)
        data = spec["data"]["values"]
        observed = {(row["region"], row["product"]) for row in data}
        assert observed == {("West", "Widgets"), ("East", "Gadgets")}


class TestIndependentScale:
    """No domainMax/axis.values baked; resolve.scale.y still emitted."""

    def test_no_domain_or_ladder_baked_under_independent(self):
        chart = _repro_a_chart(multiples={"rows": "event_type", "scale": "independent"})
        spec = _v2_vl(chart, _repro_a_rows())
        unit = spec["spec"]
        ay = unit.get("encoding", {}).get("y", {})
        # The emitter bakes the union-of-panels ceiling as "domainMax", never
        # a "domain" key — asserting the latter passes vacuously even when
        # fold_panels' scale argument regresses to "shared".
        assert "domainMax" not in ay.get("scale", {})
        axis = ay.get("axis") or {}
        assert not axis.get("values")
        assert spec.get("resolve", {}).get("scale", {}).get("y") == "independent"


class TestColorSeriesIsAlsoPartitionField:
    """color: series with multiples: {rows: series} — the shipped
    17-multiples-x-family.yml case. Palette domain spans every series;
    gap-fill does not reference the stripped column."""

    def test_palette_domain_spans_every_series_value(self):
        rows = [
            {"month": "Jan", "series": "A", "revenue": 1},
            {"month": "Jan", "series": "B", "revenue": 2},
            {"month": "Feb", "series": "A", "revenue": 3},
        ]
        chart = BarChart.model_validate(
            {
                "id": "t",
                "type": "bar",
                "query_name": "q",
                "x": "month",
                "y": "revenue",
                "color": "series",
                "multiples": {"rows": "series"},
            }
        )
        _, ctx = _board()
        resolved = resolve(chart, rows, chart_style_context=ctx)
        color_ch = resolved.resolved_channels.get("color")
        assert color_ch is not None and color_ch.data_field == "series"
        # Both series values must still resolve panels — the field was never
        # dropped, only stripped from each panel's own row payload.
        assert resolved.panel_axes == (
            PartitionAxis(field="series", values=('"A"', '"B"')),
        )
        # Inside each panel, `color: series` degenerates to that panel's own
        # constant value — one series per panel. The emitted VL `data.values`
        # (what Vega-Lite's automatic nominal-scale domain inference actually
        # scans) must still carry BOTH series values across the panels
        # combined, or the color legend/domain would collapse to whichever
        # panel happened to be emitted last.
        spec = _v2_vl(chart, rows)
        emitted_series = {row["series"] for row in spec["data"]["values"]}
        assert emitted_series == {"A", "B"}

    def test_same_column_on_both_grid_axes_resolves_diagonal_only(self):
        rows = [
            {"series": "A", "x": 1, "revenue": 1},
            {"series": "B", "x": 2, "revenue": 2},
        ]
        chart = BarChart.model_validate(
            {
                "id": "t",
                "type": "bar",
                "query_name": "q",
                "x": "x",
                "y": "revenue",
                "multiples": {"rows": "series", "columns": "series"},
            }
        )
        _, ctx = _board()
        resolved = resolve(chart, rows, chart_style_context=ctx)
        # 2 observed cells, not the 4-cell cross product a naive row x column
        # combinator would synthesize (which would need a genuine off-diagonal
        # pair — impossible here since rows and columns bind the SAME field,
        # but the panel count still proves only the diagonal was built).
        assert len(resolved.panel_axes) == 2
        assert all(axis.values == ('"A"', '"B"') for axis in resolved.panel_axes)
        spec = _v2_vl(chart, rows)
        data = spec["data"]["values"]
        # No row is synthesized or dropped: exactly the 2 source rows emitted.
        assert len(data) == len(rows)
        assert {row["series"] for row in data} == {"A", "B"}
        assert spec["facet"]["row"]["field"] == "series"
        assert spec["facet"]["column"]["field"] == "series"


class TestXFieldIsAlsoTheMultiplesField:
    """`x` and `multiples.rows` naming the same column is a degenerate but
    legal shape: every row in a panel shares one `x` value by construction.
    The bar/line/area emitters rewrite labeled-temporal `x` values (e.g.
    "Q1 2020" -> an ISO bucket) via `normalize_labeled_temporal` before
    validating/gap-filling per panel — when the rewritten field is also the
    partition field, the panel-membership re-derivation must not try to
    match the *rewritten* value against the *pre-rewrite* baked axis.
    """

    @staticmethod
    def _rows() -> list[dict[str, Any]]:
        return [
            {"yr": "Q1 2020", "region": "West", "revenue": 10},
            {"yr": "Q1 2020", "region": "East", "revenue": 20},
            {"yr": "Q2 2020", "region": "West", "revenue": 30},
            {"yr": "Q2 2020", "region": "East", "revenue": 40},
        ]

    def test_bar_does_not_raise_and_preserves_every_row(self):
        rows = self._rows()
        chart = BarChart.model_validate(
            {
                "id": "t",
                "type": "bar",
                "query_name": "q",
                "x": "yr",
                "y": "revenue",
                "color": "region",
                "multiples": {"rows": "yr"},
            }
        )
        spec = _v2_vl(chart, rows)
        data = spec["data"]["values"]
        assert len(data) == len(rows)
        assert {row["revenue"] for row in data} == {10, 20, 30, 40}

    def test_line_does_not_raise_and_preserves_every_row(self):
        rows = self._rows()
        chart = LineChart.model_validate(
            {
                "id": "t",
                "type": "line",
                "query_name": "q",
                "x": "yr",
                "y": "revenue",
                "color": "region",
                "multiples": {"rows": "yr"},
            }
        )
        spec = _v2_vl(chart, rows)
        data = spec["data"]["values"]
        assert len(data) == len(rows)
        assert {row["revenue"] for row in data} == {10, 20, 30, 40}

    def test_area_does_not_raise_and_preserves_every_row(self):
        rows = self._rows()
        chart = AreaChart.model_validate(
            {
                "id": "t",
                "type": "area",
                "query_name": "q",
                "x": "yr",
                "y": "revenue",
                "color": "region",
                "multiples": {"rows": "yr"},
            }
        )
        spec = _v2_vl(chart, rows)
        data = spec["data"]["values"]
        assert len(data) == len(rows)
        assert {row["revenue"] for row in data} == {10, 20, 30, 40}


class TestDuplicatePlotRowGuard:
    """One row per (panel, x) does not trip the duplicate-plot-row guard —
    inside a panel the plot key is the bare key again."""

    def test_one_row_per_panel_x_does_not_raise(self):
        rows = [
            {"month": "Jan", "region": "West", "revenue": 10},
            {"month": "Feb", "region": "West", "revenue": 20},
            {"month": "Jan", "region": "East", "revenue": 5},
            {"month": "Feb", "region": "East", "revenue": 8},
        ]
        chart = BarChart.model_validate(
            {
                "id": "t",
                "type": "bar",
                "query_name": "q",
                "x": "month",
                "y": "revenue",
                "multiples": {"rows": "region"},
            }
        )
        # Must not raise ERR-BAR-DUPLICATE-ROWS.
        _v2_vl(chart, rows)


class TestResolvedWithoutDataRenderedWithRealRows:
    """A faceted chart resolved with no data, then rendered against
    real rows, raises rather than silently rendering unfaceted."""

    def test_raises_rather_than_rendering_unfaceted(self):
        from dbt_charts.core.render.chart.session import BoardRenderSession

        chart = BarChart.model_validate(
            {
                "id": "t",
                "type": "bar",
                "query_name": "q",
                "x": "month",
                "y": "revenue",
                "multiples": {"rows": "region"},
            }
        )
        board_rs, ctx = _board()
        resolved = resolve(chart, [], chart_style_context=ctx)
        assert resolved.panel_axes == ()  # resolved against no data
        real_rows = [{"month": "Jan", "region": "West", "revenue": 10}]
        # The check must fire from BoardRenderSession.emit_chart, before the
        # emitter runs — calling the emitter or FacetFeature directly would
        # miss the exact ordering bug this test pins: the emitter's own
        # per-panel validation (regroup((), real_rows) -> one panel) raises
        # a misleading ERR-BAR-DUPLICATE-ROWS first unless this check runs
        # ahead of it.
        session = BoardRenderSession.create(board_rs)
        with pytest.raises(ChartDataError, match="no data") as exc_info:
            session.emit_chart(resolved, _DEFAULT_BOX, {"q": real_rows})
        assert exc_info.value.code is not None
        assert exc_info.value.code.code == "ERR-MULTIPLES-RESOLVED-WITHOUT-DATA"


class TestOwnQueryLayerPartitionRefusal:
    """An own-query overlay layer whose query returns the partition column
    is refused; one that does not still renders and repeats in every
    panel."""

    @staticmethod
    def _faceted_chart_with_layer(layer_query_returns_partition: bool) -> BarChart:
        layer_row = (
            {"month": "Jan", "region": "West", "target": 99}
            if layer_query_returns_partition
            else {"month": "Jan", "target": 99}
        )
        return BarChart.model_validate(
            {
                "id": "t",
                "type": "bar",
                "query_name": "q",
                "x": "month",
                "y": "revenue",
                "multiples": {"rows": "region"},
                "layers": [
                    {
                        "type": "line",
                        "query": "other",
                        "x": "month",
                        "y": "target",
                    }
                ],
            }
        ), layer_row

    def test_layer_returning_partition_column_is_refused(self):
        from dbt_charts.core.diagnostics.codes_render import (
            ERR_MULTIPLES_LAYER_PARTITION,
        )
        from dbt_charts.core.render.chart.features.facet import FacetFeature
        from dbt_charts.core.render.chart.spec import ChartSpec

        chart, layer_row = self._faceted_chart_with_layer(
            layer_query_returns_partition=True
        )
        rows = [{"month": "Jan", "region": "West", "revenue": 10}]
        _, ctx = _board()
        resolved = resolve(chart, rows, chart_style_context=ctx)
        with pytest.raises(ChartDataError) as exc_info:
            FacetFeature().apply(
                ChartSpec(mark="bar"),
                resolved,
                _DEFAULT_BOX,
                {"q": rows, "other": [layer_row]},
            )
        assert exc_info.value.code is ERR_MULTIPLES_LAYER_PARTITION

    def test_layer_not_returning_partition_column_is_allowed(self):
        from dbt_charts.core.render.chart.features.facet import FacetFeature
        from dbt_charts.core.render.chart.spec import ChartSpec

        chart, layer_row = self._faceted_chart_with_layer(
            layer_query_returns_partition=False
        )
        rows = [{"month": "Jan", "region": "West", "revenue": 10}]
        _, ctx = _board()
        resolved = resolve(chart, rows, chart_style_context=ctx)
        result = FacetFeature().apply(
            ChartSpec(mark="bar"),
            resolved,
            _DEFAULT_BOX,
            {"q": rows, "other": [layer_row]},
        )
        assert result.facet_row == "region"

    def test_generate_vega_lite_spec_does_not_raise_for_faceted_own_query_layer(self):
        """generate_vega_lite_spec() is a standalone entry point with no
        per-query datasets concept at all — it can never supply an own-query
        layer's rows, so a faceted chart with such a layer must still
        produce a spec rather than crash on the layer-partition check's
        lookup of a key that was never going to be there."""
        from dbt_charts.core.render.chart.vega_lite import generate_vega_lite_spec

        chart, _layer_row = self._faceted_chart_with_layer(
            layer_query_returns_partition=True
        )
        rows = [{"month": "Jan", "region": "West", "revenue": 10}]
        spec = generate_vega_lite_spec(chart, rows)
        assert "facet" in spec

    def test_missing_layer_dataset_key_is_tolerated_as_no_data_to_validate(self):
        """`build_chart_datasets` (the real production render path) always
        adds an entry for every layer query, so this check fires correctly
        there even for a genuinely offending layer. Two non-production
        callers pass an incomplete `datasets` map on purpose, though:
        renderer.py's board-level warning-detection pass (only threads the
        base chart's own rows) and generate_vega_lite_spec() (no per-query
        datasets concept at all). Neither can supply this layer's own rows,
        so there is nothing to validate for it — a missing key must be
        tolerated the same way a present-but-empty layer dataset already is,
        never raise a bare KeyError for a caller limitation that isn't an
        authoring error.
        """
        from dbt_charts.core.render.chart.features.facet import FacetFeature
        from dbt_charts.core.render.chart.spec import ChartSpec

        chart, _layer_row = self._faceted_chart_with_layer(
            layer_query_returns_partition=True
        )
        rows = [{"month": "Jan", "region": "West", "revenue": 10}]
        _, ctx = _board()
        resolved = resolve(chart, rows, chart_style_context=ctx)
        result = FacetFeature().apply(
            ChartSpec(mark="bar"),
            resolved,
            _DEFAULT_BOX,
            {"q": rows},  # "other" (the layer's own query) is missing
        )
        assert result.facet_row == "region"


class TestFacetedStructuredTooltipSuppression:
    """The faceted structured-tooltip suppression still holds."""

    def test_kept_off_when_faceted_on_when_not(self):
        from dbt_charts.core.render.chart.features.structured_tooltip import (
            StructuredTooltipFeature,
        )

        rows = [{"month": "Jan", "region": "West", "revenue": 10}]
        _, ctx = _board()
        faceted = BarChart.model_validate(
            {
                "id": "t",
                "type": "bar",
                "query_name": "q",
                "x": "month",
                "y": "revenue",
                "multiples": {"rows": "region"},
            }
        )
        not_faceted = BarChart.model_validate(
            {
                "id": "t2",
                "type": "bar",
                "query_name": "q",
                "x": "month",
                "y": "revenue",
            }
        )
        resolved_faceted = resolve(faceted, rows, chart_style_context=ctx)
        resolved_plain = resolve(not_faceted, rows, chart_style_context=ctx)
        feature = StructuredTooltipFeature()
        assert feature.applies_to(resolved_faceted) is False
        assert feature.applies_to(resolved_plain) is True


class TestGapFillAgreesWithXEncoding:
    """Regression: per-panel gap-fill must agree with the x-encoding's own
    ordinal-vs-temporal decision, which is made once on the whole (post-fill)
    dataset. Panel A spans 84 distinct months (decides "temporal" even in
    isolation, since 84 > max_ordinal_buckets=60); panel B spans only 11 of
    12 months in 2020 with April missing (decides "ordinal" in isolation,
    since 11 < 60, and self-fills the April gap with a synthesized null row).
    The whole (96-bucket) dataset decides "temporal" for the x-encoding, so
    panel B must NOT independently synthesize the April gap — a continuous
    temporal scale draws no mark for a missing bucket, it needs no synthetic
    row, and panel A never gets one for its own gaps either."""

    @staticmethod
    def _rows() -> list[dict[str, Any]]:
        rows: list[dict[str, Any]] = []
        for year in range(2000, 2007):
            for month in range(1, 13):
                rows.append(
                    {
                        "month": f"{year}-{month:02d}-01",
                        "region": "A",
                        "revenue": 5,
                    }
                )
        for month in range(1, 13):
            if month == 4:
                continue  # gap in panel B's own range
            rows.append(
                {
                    "month": f"2020-{month:02d}-01",
                    "region": "B",
                    "revenue": 5,
                }
            )
        return rows

    def test_small_panel_does_not_synthesize_when_whole_dataset_is_temporal(self):
        rows = self._rows()
        assert len({r["month"] for r in rows if r["region"] == "A"}) == 84
        assert len({r["month"] for r in rows if r["region"] == "B"}) == 11
        chart = BarChart.model_validate(
            {
                "id": "t",
                "type": "bar",
                "query_name": "q",
                "x": "month",
                "y": "revenue",
                "multiples": {"rows": "region"},
            }
        )
        spec = _v2_vl(chart, rows)
        data = spec["data"]["values"]
        # Temporal on both panels: no bucket synthesis anywhere, so the
        # emitted row count is exactly the source row count. A synthesized
        # null row for panel B's April gap would inflate this by one.
        assert len(data) == len(rows)
        panel_b_months = {r["month"] for r in data if r["region"] == "B"}
        assert panel_b_months == {f"2020-{m:02d}-01" for m in range(1, 13) if m != 4}
        assert "2020-04-01" not in panel_b_months


class TestGapFillTimeUnitCentralizedAcrossPanels:
    """The auto-detected time_unit, like the ordinal-vs-temporal verdict,
    must be decided ONCE against the whole (pooled) dataset and threaded to
    every panel — not re-derived per panel from a much smaller denominator.

    Panel A: 10 distinct, clean months (2019, well over the ≥10%
    unparseable-value raise threshold's safety margin on its own). Panel B:
    10 rows cycling through only 3 distinct clean months, plus one
    unparseable value as an 11th row (kept out of both panels' own
    type-inference SAMPLE, which only looks at the first 10 rows of
    whatever row list it's given — pooled or per-panel).

    Per panel B alone: 3 clean distinct + 1 bad = 4 distinct, 1 bad -> 25%,
    over the ≥10% threshold -> detect_time_unit raises if re-derived from
    panel B's own values. Pooled across both panels: 10 (A) + 3 (B, new) +
    1 bad = 14 distinct, 1 bad -> ~7% -> under the threshold -> resolves a
    real time_unit with no raise. A chart shaped exactly like this must
    render, not raise, once the detection denominator is the whole dataset.
    """

    @staticmethod
    def _rows() -> list[dict[str, Any]]:
        # "series" is a unique-per-row throwaway color dimension, not the
        # subject under test — it exists only so panel B's repeated month
        # values don't collide with the duplicate-plotted-key guard
        # (bar's plot key is (x, color) once a color channel is authored).
        rows: list[dict[str, Any]] = [
            {
                "month": f"2019-{m:02d}-01",
                "region": "A",
                "series": f"a{m}",
                "revenue": 5,
            }
            for m in range(1, 11)
        ]
        b_months = ["2020-02-01", "2020-03-01", "2020-05-01"]
        rows += [
            {"month": b_months[i % 3], "region": "B", "series": f"b{i}", "revenue": 5}
            for i in range(10)
        ]
        # 11th row of panel B, and last overall — outside the first-10-row
        # type-inference sample of both panel B's own rows and the pooled
        # (query-order) row list.
        rows.append(
            {"month": "not-a-date", "region": "B", "series": "b10", "revenue": 5}
        )
        return rows

    def test_sparse_panel_with_one_unparseable_value_does_not_raise(self):
        rows = self._rows()
        # Must not raise — a pre-centralization re-derivation would hit
        # detect_time_unit's ≥10% unparseable-value ValueError for panel B.
        spec = _v2_vl(
            BarChart.model_validate(
                {
                    "id": "t",
                    "type": "bar",
                    "query_name": "q",
                    "x": "month",
                    "y": "revenue",
                    "color": "series",
                    "multiples": {"rows": "region"},
                }
            ),
            rows,
        )
        assert "facet" in spec
        # Pins the actual pooled GRAIN, not just "did not raise" — a
        # re-derivation landing on a different (coarser/finer) time_unit
        # would still produce a spec with "facet" in it, silently masking a
        # wrong grain. yearmonth is the only grain consistent with panel A's
        # 10 distinct clean 2019 months all surviving as distinct buckets.
        data = spec["data"]["values"]
        panel_a_months = {row["month"] for row in data if row["region"] == "A"}
        assert panel_a_months == {f"2019-{m:02d}-01" for m in range(1, 11)}


# ---------------------------------------------------------------------------
# Geometry tests — panel width/height.
# ---------------------------------------------------------------------------


def test_width_dependent_decision_uses_panel_width(monkeypatch):
    """The width handed to resolve_axis_x_overlap is the panel width, not the
    card width — spy on the emitter's own call (through the real render
    pipeline) rather than calling the helper directly and re-implementing the
    box math, per core/AGENTS.md's ban on test-side re-implementations of
    production formulas."""
    from dbt_charts.core.compile.resolve.chart.adaptive_stroke import (
        facet_panel_width,
    )
    from dbt_charts.core.render.chart.emitters import bar as bar_emitter
    from dbt_charts.core.render.chart.vega_lite import generate_vega_lite_spec

    chart = BarChart.model_validate(
        {
            "id": "t",
            "type": "bar",
            "query_name": "q",
            "x": "month",
            "y": "revenue",
            "multiples": {"columns": "region"},
        }
    )
    rows = [
        {"month": "Jan", "region": r, "revenue": 10} for r in ("West", "East", "North")
    ]
    board_rs, ctx = _board()
    resolved = resolve(chart, rows, chart_style_context=ctx, width=600.0)
    n_columns = len(resolved.panel_axes[0].values)
    expected_width = facet_panel_width(
        600.0, n_columns, bool(resolved.style.axis_y.mirror), 0.0
    )

    captured: list[float] = []
    real = bar_emitter.resolve_axis_x_overlap

    def _spy(*args: Any, **kwargs: Any) -> Any:
        captured.append(kwargs["chart_width"])
        return real(*args, **kwargs)

    monkeypatch.setattr(bar_emitter, "resolve_axis_x_overlap", _spy)
    generate_vega_lite_spec(
        chart, rows, width=600.0, board_style=board_rs, chart_style_context=ctx
    )
    assert captured
    assert captured[0] == expected_width


def test_scatter_or_heatmap_columns_uses_panel_width(monkeypatch):
    """The card-width-vs-panel-width bug is family-independent — the
    bar-only audit (test 4) does not cover it. Heatmap's own
    resolve_axis_x_overlap call site (emitters/heatmap.py) must see the
    panel width, not the card width."""
    from dbt_charts.core.compile.models.chart.normalized import HeatmapChart
    from dbt_charts.core.compile.resolve.chart.adaptive_stroke import (
        facet_panel_width,
    )
    from dbt_charts.core.render.chart.emitters import heatmap as heatmap_emitter
    from dbt_charts.core.render.chart.vega_lite import generate_vega_lite_spec

    chart = HeatmapChart.model_validate(
        {
            "id": "t",
            "type": "heatmap",
            "query_name": "q",
            "x": "month",
            "y": "region",
            "color": "revenue",
            "multiples": {"columns": "segment"},
        }
    )
    rows = [
        {"month": "Jan", "region": "West", "segment": s, "revenue": 10}
        for s in ("A", "B", "C")
    ]
    board_rs, ctx = _board()
    resolved = resolve(chart, rows, chart_style_context=ctx, width=600.0)
    n_columns = len(resolved.panel_axes[0].values)
    expected_width = facet_panel_width(
        600.0, n_columns, bool(resolved.style.axis_y.mirror), 0.0
    )

    captured: list[float] = []
    real = heatmap_emitter.resolve_axis_x_overlap

    def _spy(*args: Any, **kwargs: Any) -> Any:
        captured.append(kwargs["chart_width"])
        return real(*args, **kwargs)

    monkeypatch.setattr(heatmap_emitter, "resolve_axis_x_overlap", _spy)
    generate_vega_lite_spec(
        chart, rows, width=600.0, board_style=board_rs, chart_style_context=ctx
    )
    assert captured
    assert captured[0] == expected_width


def test_horizontal_bar_panel_height_floor():
    """A 2-row-panel horizontal bar with 8 categories each: naively dividing
    the height-floored slot height by the row cardinality puts every panel
    under min_height_for_horizontal_bar_categories. The floor must be
    multiplied by the row cardinality before it is divided, so each panel
    still clears it."""
    from dbt_charts.core.render.chart.emitters._cartesian import (
        min_height_for_horizontal_bar_categories,
    )
    from dbt_charts.core.render.chart.vega_lite import generate_vega_lite_spec
    from dbt_charts.core.render.chart.vl_field_maps import effective_bar_size

    categories = [f"cat{i}" for i in range(8)]
    chart = BarChart.model_validate(
        {
            "id": "t",
            "type": "bar",
            "query_name": "q",
            "x": "category",
            "y": "revenue",
            "style": {"orientation": "horizontal"},
            "multiples": {"rows": "region"},
        }
    )
    rows = [
        {"category": c, "region": r, "revenue": 5}
        for r in ("North", "South")
        for c in categories
    ]
    board_rs, ctx = _board()
    resolved = resolve(chart, rows, chart_style_context=ctx)
    min_h = min_height_for_horizontal_bar_categories(
        len(categories), resolved.style.axis_x, effective_bar_size(resolved.style.mark)
    )
    vl = generate_vega_lite_spec(
        chart,
        rows,
        width=600.0,
        height=min_h,
        board_style=board_rs,
        chart_style_context=ctx,
    )
    assert vl["spec"]["height"] >= min_h


def test_render_time_width_not_resolve_time_width(monkeypatch):
    """The inactive-tab path resolves a chart with no real width (item.width
    == 0.0 -> None -> preferred_width). It is later rendered at a different
    real slot width. Panel width must come from that render-time width, not
    anything computed or cached from the resolve-time call — this is exactly
    why panel width is never baked onto the resolved model. Spies on the
    emitter's real call site (through render_resolved_chart, no re-resolve)
    rather than calling resolve_axis_x_overlap directly, so the width
    genuinely flows through the render-time RenderBox, not a hand-picked
    literal."""
    from dbt_charts.core.compile.resolve.chart.adaptive_stroke import (
        facet_panel_width,
    )
    from dbt_charts.core.render.chart.emitters import bar as bar_emitter
    from dbt_charts.core.render.chart.vega_lite import render_resolved_chart

    chart = BarChart.model_validate(
        {
            "id": "t",
            "type": "bar",
            "query_name": "q",
            "x": "month",
            "y": "revenue",
            "multiples": {"columns": "region"},
        }
    )
    rows = [
        {"month": "Jan", "region": r, "revenue": 10} for r in ("West", "East", "North")
    ]
    board_rs, ctx = _board()
    # Resolved with no real width — the inactive-tab path.
    resolved = resolve(chart, rows, chart_style_context=ctx, width=None)
    # Rendered later at a real, different slot width than resolve ever saw.
    render_width = 900.0
    n_columns = len(resolved.panel_axes[0].values)
    expected_width = facet_panel_width(
        render_width, n_columns, bool(resolved.style.axis_y.mirror), 0.0
    )

    captured: list[float] = []
    real = bar_emitter.resolve_axis_x_overlap

    def _spy(*args: Any, **kwargs: Any) -> Any:
        captured.append(kwargs["chart_width"])
        return real(*args, **kwargs)

    monkeypatch.setattr(bar_emitter, "resolve_axis_x_overlap", _spy)
    render_resolved_chart(resolved, rows, board_rs, width=render_width, height=300.0)
    assert captured
    assert captured[0] == expected_width


def test_emitted_spec_carries_panel_width_and_height():
    """The row-truncated render path resolves against the full result but
    can render only a subset of rows (board_to_dict.py truncates the emitted
    rows, never the baked axes). The stamped height must divide by the baked
    row-axis cardinality, not by re-deriving panel count from whatever subset
    of rows happens to reach render — that re-derivation is exactly
    `_apply_facet_layout`'s pre-phase-2 mechanism and gives a different
    (wrong) answer once render holds fewer panels' worth of rows than resolve
    saw."""
    from dbt_charts.core.render.chart.vega_lite import render_resolved_chart

    chart = BarChart.model_validate(
        {
            "id": "t",
            "type": "bar",
            "query_name": "q",
            "x": "month",
            "y": "revenue",
            # Explicit vertical: a single-word "month" x column with no dates
            # auto-detects as categorical -> horizontal, which would entangle
            # this test with the horizontal-bar min-height floor (test 12's
            # concern, not this one).
            "style": {"orientation": "vertical"},
            "multiples": {"rows": "region"},
        }
    )
    full_rows = [
        {"month": "Jan", "region": r, "revenue": 10} for r in ("West", "East", "North")
    ]
    board_rs, ctx = _board()
    resolved = resolve(chart, full_rows, chart_style_context=ctx, width=600.0)
    row_cardinality = len(resolved.panel_axes[0].values)
    assert row_cardinality == 3
    truncated_rows = [row for row in full_rows if row["region"] != "North"]
    artifact = render_resolved_chart(
        resolved, truncated_rows, board_rs, width=600.0, height=300.0
    )
    vl = artifact.payload
    assert vl["spec"]["width"] is not None
    assert vl["spec"]["height"] == 300.0 / row_cardinality


class TestGapFillPerPanelAuthoredTemporalWithZeroRows:
    """`gap_fill_ordinal_time_per_panel` must return the real
    `x_authored_temporal` verdict even when its `dataset` holds zero panels
    (a faceted chart resolved with no render rows) — it is a pure function
    of the resolved axis style, not of any panel's rows, so it must not
    regress to the function's own initialized-`False` default just because
    `fill_one_panel` never ran to overwrite it via its `nonlocal`.
    """

    def test_authored_temporal_survives_zero_render_rows(self):
        from dbt_charts.core.compile.resolve.chart._chart_rows import regroup
        from dbt_charts.core.render.chart.emitters._channels import (
            gap_fill_ordinal_time_per_panel,
        )

        chart = LineChart.model_validate(
            {
                "id": "t",
                "type": "line",
                "x": "month",
                "y": "value",
                "multiples": {"rows": "region"},
                "style": {"axis_x": {"type": "temporal"}},
            }
        )
        rows = [
            {"month": "2024-01-01", "region": "West", "value": 1},
            {"month": "2024-01-01", "region": "East", "value": 2},
        ]
        _, ctx = _board()
        resolved = resolve(chart, rows, chart_style_context=ctx)

        # Zero render rows -> zero panels, but the baked axes survive.
        empty_dataset = regroup(resolved.panel_axes, [])
        _filled, x_authored_temporal = gap_fill_ordinal_time_per_panel(
            resolved.style.axis_x,
            "month",
            None,
            empty_dataset,
            "line",
            False,
            True,
            None,
        )

        assert x_authored_temporal is True
