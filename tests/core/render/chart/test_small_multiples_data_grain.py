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
    BarChart,
    LineChart,
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


# ---------------------------------------------------------------------------
# Reproduction A: rows-only, 11 rows, 2 families x 2 event types x 4 months
# (sparse tail). Shared with test_stacked_domain_multiples_fold.py via
# ..._small_multiples_repro_a so the two suites can't silently drift on the
# exact row shape.
# ---------------------------------------------------------------------------


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
    the engine's x-axis builder directly and cannot see that divergence.
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


# ---------------------------------------------------------------------------
# Geometry tests — panel width/height.
# ---------------------------------------------------------------------------


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
