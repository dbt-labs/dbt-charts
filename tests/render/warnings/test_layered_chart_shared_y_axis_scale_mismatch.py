"""Tests for the LAYERED_CHART_SHARED_Y_AXIS_SCALE_MISMATCH render-warning detector.

Detection rule: fires on a bar/line/area/scatter chart with typed overlay
``layers`` when one y series (the base chart's own y column or a layer's)
reaches under 6% of the shared y-axis, AND no layer pins its own y-axis.
Reach is the series' drawn band: from the axis baseline (zero) when the axis
includes it, else its own variation.

The canonical same-query cases, adapted to the current
``chart.layers`` overlay model (no separate ``type: layered`` chart — the
base chart is a real bar/line/area/scatter chart and contributes its own y
column to the comparison alongside its layers'):
  1. Base + one layer, base median ≈ 5M, layer median ≈ 0.5, no axis_y → fires.
  2. Same as 1 but the layer has axis_y set → no warning (user opted out).
  3. Base + one layer, both medians ≈ 1M → no warning (reach well over 6%).
  4. Chart with no layers → no warning (nothing to compare against).
  5. Base + two layers, columns 1M/10/1M → fires, message names the 10-scale column.
  6. Symmetric signed data → median-of-abs still detects the scale gap.

Per-layer-query cases (a layer carrying its own ``query:``) follow at the end
of the file.
"""

from __future__ import annotations

from decimal import Decimal
from typing import Any

from dbt_charts.core.compile.models.chart.authored._layer import (
    AreaLayer,
    LayerAxisYStyle,
    LineLayer,
)
from dbt_charts.core.compile.models.chart.normalized import BarChart
from dbt_charts.core.diagnostics import (
    WARN_LAYERED_CHART_SHARED_Y_AXIS_SCALE_MISMATCH,
    Diagnostic,
)
from dbt_charts.core.render.warnings import (
    WarningContext,
    layered_chart_shared_y_axis_scale_mismatch as detector,
)

from ...core._board_utils import (
    make_test_resolved_board,
    make_test_resolved_chart,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_CHART_DEFAULTS: dict[str, Any] = {
    "id": "c1",
    "type": "bar",
    "query_name": "q",
    "title": "",
    "x": "month",
}


def _make_chart(**kwargs: object) -> BarChart:
    return BarChart(**{**_CHART_DEFAULTS, **kwargs})


def _make_ctx(
    chart: BarChart,
    rows: list[dict[str, Any]],
    layer_rows: dict[str, list[dict[str, Any]]] | None = None,
) -> WarningContext:
    """Build a context; ``layer_rows`` maps a layer's own query name to its rows."""
    resolved = make_test_resolved_chart(chart, rows)
    board = make_test_resolved_board(charts={resolved.id: resolved})
    return WarningContext(
        board_spec=board,
        chart_results={resolved.id: rows},
        layer_results={resolved.id: layer_rows} if layer_rows is not None else {},
    )


# ---------------------------------------------------------------------------
# Test 1: base + one layer, base median ≈ 5M, layer median ≈ 0.5 → fires
# ---------------------------------------------------------------------------


def test_fires_base_plus_layer_tiny_reach() -> None:
    """Canonical case: base revenue at 5M scale vs layer rate at 0.5 scale fires."""
    chart = _make_chart(y="revenue", layers=[LineLayer(type="line", y="rate")])
    rows = [
        {"month": "Jan", "revenue": 5_000_000.0, "rate": 0.45},
        {"month": "Feb", "revenue": 4_800_000.0, "rate": 0.55},
        {"month": "Mar", "revenue": 5_200_000.0, "rate": 0.50},
    ]
    ctx = _make_ctx(chart, rows)
    warnings = detector.detect(ctx)

    assert len(warnings) == 1
    w = warnings[0]
    assert isinstance(w, Diagnostic)
    assert w.code == WARN_LAYERED_CHART_SHARED_Y_AXIS_SCALE_MISMATCH.code
    assert w.chart == "c1"
    assert w.field is None  # cross-column, not column-scoped
    assert "revenue" in w.message and "rate" in w.message
    assert w.fix is not None


# ---------------------------------------------------------------------------
# Test 2: same but the layer has axis_y set → no warning
# ---------------------------------------------------------------------------


def test_no_fire_when_axis_y_set_on_extreme_layer() -> None:
    """Layer with axis_y already set means user opted into split scales — no warning."""
    chart = _make_chart(
        y="revenue",
        layers=[
            LineLayer(type="line", y="rate", axis_y=LayerAxisYStyle(position="right"))
        ],
    )
    rows = [
        {"month": "Jan", "revenue": 5_000_000.0, "rate": 0.45},
        {"month": "Feb", "revenue": 4_800_000.0, "rate": 0.55},
        {"month": "Mar", "revenue": 5_200_000.0, "rate": 0.50},
    ]
    ctx = _make_ctx(chart, rows)
    assert detector.detect(ctx) == []


# ---------------------------------------------------------------------------
# Test 3: base + one layer, both medians ≈ 1M → no warning (reach over threshold)
# ---------------------------------------------------------------------------


def test_no_fire_when_reach_over_threshold() -> None:
    """Base + layer at similar scale (both ~1M) must not fire."""
    chart = _make_chart(y="revenue", layers=[LineLayer(type="line", y="profit")])
    rows = [
        {"month": "Jan", "revenue": 1_000_000.0, "profit": 900_000.0},
        {"month": "Feb", "revenue": 1_100_000.0, "profit": 950_000.0},
        {"month": "Mar", "revenue": 1_050_000.0, "profit": 1_000_000.0},
    ]
    ctx = _make_ctx(chart, rows)
    assert detector.detect(ctx) == []


# ---------------------------------------------------------------------------
# Test 4: chart with no layers → no warning
# ---------------------------------------------------------------------------


def test_no_fire_without_layers() -> None:
    """A chart with no chart.layers has nothing to compare against — skip."""
    chart = _make_chart(y="revenue")
    rows = [
        {"month": "Jan", "revenue": 5_000_000.0},
        {"month": "Feb", "revenue": 4_800_000.0},
    ]
    ctx = _make_ctx(chart, rows)
    assert detector.detect(ctx) == []


# ---------------------------------------------------------------------------
# Test 5: base + two layers, 1M/10/1M → fires, message names the 10-scale column
# ---------------------------------------------------------------------------


def test_fires_base_plus_two_layers_one_outlier_named_in_message() -> None:
    """Base + two layers where the layer 'count' is the outlier (10 vs 1M)."""
    chart = _make_chart(
        y="revenue",
        layers=[
            LineLayer(type="line", y="count"),
            AreaLayer(type="area", y="profit"),
        ],
    )
    rows = [
        {"month": "Jan", "revenue": 1_000_000.0, "count": 10.0, "profit": 1_050_000.0},
        {"month": "Feb", "revenue": 1_100_000.0, "count": 12.0, "profit": 950_000.0},
        {"month": "Mar", "revenue": 950_000.0, "count": 8.0, "profit": 1_000_000.0},
    ]
    ctx = _make_ctx(chart, rows)
    warnings = detector.detect(ctx)

    assert len(warnings) == 1
    w = warnings[0]
    assert w.code == WARN_LAYERED_CHART_SHARED_Y_AXIS_SCALE_MISMATCH.code
    # Message must name both ends: the outlier (count ~10) and a high-scale column.
    assert "count" in w.message
    assert "revenue" in w.message or "profit" in w.message


# ---------------------------------------------------------------------------
# Signed / symmetric data: median of abs preserves scale magnitude
# ---------------------------------------------------------------------------


def test_fires_for_symmetric_signed_data() -> None:
    """P&L deltas symmetric around zero still fire — median of abs sees ~100k scale."""
    chart = _make_chart(y="pnl", layers=[LineLayer(type="line", y="rate")])
    # pnl symmetric around 0 with magnitude ~100k; naive abs(median) would be 0
    rows = [
        {"month": "Jan", "pnl": -100_000.0, "rate": 0.5},
        {"month": "Feb", "pnl": -50_000.0, "rate": 0.4},
        {"month": "Mar", "pnl": 50_000.0, "rate": 0.6},
        {"month": "Apr", "pnl": 100_000.0, "rate": 0.5},
    ]
    ctx = _make_ctx(chart, rows)
    warnings = detector.detect(ctx)
    assert len(warnings) == 1
    assert warnings[0].code == WARN_LAYERED_CHART_SHARED_Y_AXIS_SCALE_MISMATCH.code


def test_marks_both_y_series_not_the_whole_chart_block() -> None:
    """The reported case: a 30-line chart got 30 lines of squiggle.

    The complaint is the ratio between two series, so the mark belongs on the
    two `y:` lines that disagree — the base chart's own and the layer's — not
    on the chart block that contains them.
    """
    chart = _make_chart(
        y="revenue_annual_recurring_revenue",
        layers=[LineLayer(type="line", y="arr")],
    )
    rows = [
        {
            "month": f"2026-{m:02d}",
            "revenue_annual_recurring_revenue": 1_000_000.0 * m,
            "arr": 5.0 * m,
        }
        for m in range(1, 13)
    ]
    warnings = detector.detect(_make_ctx(chart, rows))

    assert len(warnings) == 1
    w = warnings[0]
    # Anchored on one of the two y columns, never the bare chart block.
    assert w.path in {"charts.c1.y", "charts.c1.layers.0.y"}
    assert w.path != "charts.c1"
    # And the other half of the pair is named too, with a label saying why.
    assert len(w.related) == 1
    assert w.related[0].path in {"charts.c1.y", "charts.c1.layers.0.y"}
    assert w.related[0].path != w.path
    assert w.related[0].message is not None


# ---------------------------------------------------------------------------
# Regression: Decimal columns must not raise TypeError (Decimal / float)
# ---------------------------------------------------------------------------


def test_fires_with_decimal_and_float_columns_no_type_error() -> None:
    """Decimal revenue column alongside float rate column must not raise TypeError.

    Before the fix, abs_values collected Decimal values; statistics.median
    returned Decimal for the first column and float for the second.  The
    cross-column ratio (max_median / min_median) then raised
    ``TypeError: unsupported operand type(s) for /: 'decimal.Decimal' and 'float'``.
    """
    chart = _make_chart(y="revenue", layers=[LineLayer(type="line", y="rate")])
    rows = [
        {"month": "Jan", "revenue": Decimal("5000000"), "rate": 0.45},
        {"month": "Feb", "revenue": Decimal("4800000"), "rate": 0.55},
        {"month": "Mar", "revenue": Decimal("5200000"), "rate": 0.50},
    ]
    ctx = _make_ctx(chart, rows)
    # Must not raise; must still detect the scale mismatch.
    warnings = detector.detect(ctx)
    assert len(warnings) == 1
    assert warnings[0].code == WARN_LAYERED_CHART_SHARED_Y_AXIS_SCALE_MISMATCH.code


# ---------------------------------------------------------------------------
# Per-layer queries: a layer carrying its own `query:` is the shape the
# deterministic Looker migrator emits (one query per chart layer). It draws on
# the same shared y-axis as the base, so it must be compared, not skipped.
# ---------------------------------------------------------------------------


def test_fires_when_layer_carries_its_own_query() -> None:
    """Base and layer at wildly different scales, split across two queries.

    Base 'revenue' (median 5.0M) and layer 'rate' (median 0.50) come from
    different named queries and share one y-axis at a 10,000,000x ratio — the
    rate line is drawn as a flat zero. The detector used to skip the whole
    chart the moment any layer overrode the base query, which is exactly the
    migrated multi-metric tile this diagnostic exists to protect.
    """
    chart = _make_chart(
        y="revenue", layers=[LineLayer(type="line", y="rate", query="q2")]
    )
    base_rows = [
        {"month": "Jan", "revenue": 5_000_000.0},
        {"month": "Feb", "revenue": 4_800_000.0},
        {"month": "Mar", "revenue": 5_200_000.0},
    ]
    layer_rows = [
        {"month": "Jan", "rate": 0.45},
        {"month": "Feb", "rate": 0.55},
        {"month": "Mar", "rate": 0.50},
    ]
    warnings = detector.detect(_make_ctx(chart, base_rows, {"q2": layer_rows}))

    assert len(warnings) == 1
    w = warnings[0]
    assert w.code == WARN_LAYERED_CHART_SHARED_Y_AXIS_SCALE_MISMATCH.code
    assert "revenue" in w.message and "rate" in w.message
    assert w.path == "charts.c1.y"
    assert [r.path for r in w.related] == ["charts.c1.layers.0.y"]


def test_no_fire_when_layer_query_is_at_the_same_scale() -> None:
    """Per-layer query at the same magnitude as the base: nothing is crushed."""
    chart = _make_chart(
        y="revenue", layers=[LineLayer(type="line", y="profit", query="q2")]
    )
    base_rows = [
        {"month": "Jan", "revenue": 5_000_000.0},
        {"month": "Feb", "revenue": 4_800_000.0},
    ]
    layer_rows = [
        {"month": "Jan", "profit": 4_500_000.0},
        {"month": "Feb", "profit": 4_900_000.0},
    ]
    assert detector.detect(_make_ctx(chart, base_rows, {"q2": layer_rows})) == []


def test_fires_when_per_layer_queries_reuse_the_same_column_name() -> None:
    """Two queries both selecting 'value' are two series, not one shared scale.

    The migrator names every tile's measure column identically, so deduping on
    column name alone would silently drop the second series and land back on
    silence. Series identity is (query, column).
    """
    chart = _make_chart(
        y="value", layers=[LineLayer(type="line", y="value", query="q2")]
    )
    base_rows = [
        {"month": "Jan", "value": 5_000_000.0},
        {"month": "Feb", "value": 5.2e6},
    ]
    layer_rows = [{"month": "Jan", "value": 0.45}, {"month": "Feb", "value": 0.55}]
    warnings = detector.detect(_make_ctx(chart, base_rows, {"q2": layer_rows}))

    assert len(warnings) == 1
    assert warnings[0].path == "charts.c1.y"
    assert [r.path for r in warnings[0].related] == ["charts.c1.layers.0.y"]


def test_no_fire_when_a_layer_query_result_is_missing() -> None:
    """A layer whose query failed to execute contributes no series — stay quiet.

    The base rows carry a ``rate`` column of their own at a >100x different
    scale, so this also pins that the missing result is *skipped* rather than
    silently read off the base rows: falling back to ``base_rows`` here would
    find those values and fire.
    """
    chart = _make_chart(
        y="revenue", layers=[LineLayer(type="line", y="rate", query="q2")]
    )
    base_rows = [
        {"month": "Jan", "revenue": 5_000_000.0, "rate": 0.45},
        {"month": "Feb", "revenue": 4_800_000.0, "rate": 0.55},
    ]
    assert detector.detect(_make_ctx(chart, base_rows, {})) == []


def test_no_fire_when_layer_with_own_query_has_axis_y_set() -> None:
    """The axis_y opt-out survives the cross-query comparison."""
    chart = _make_chart(
        y="revenue",
        layers=[
            LineLayer(
                type="line",
                y="rate",
                query="q2",
                axis_y=LayerAxisYStyle(position="right"),
            )
        ],
    )
    base_rows = [
        {"month": "Jan", "revenue": 5_000_000.0},
        {"month": "Feb", "revenue": 4_800_000.0},
    ]
    layer_rows = [{"month": "Jan", "rate": 0.45}, {"month": "Feb", "rate": 0.55}]
    assert detector.detect(_make_ctx(chart, base_rows, {"q2": layer_rows})) == []


# ---------------------------------------------------------------------------
# Reach threshold: accept / reject table
#
# Reach = the series' drawn band over the shared axis span, the band running
# from zero when the axis includes it. Fires under 6%.
#
#   series on a shared axis                        | zero | reach | verdict
#   -----------------------------------------------|------|-------|--------
#   sales 42-78 (bar) + conversion 2.6-4.6 (line)  | yes  | 5.5%  | fires (15:1)
#   revenue 5M + rate 0.5 (bar + line)             | yes  | ~0%   | fires
#   sales 42-78 (line) + conversion 2.6-4.6 (line) | no   | 2.3%  | fires
#   revenue 50-100 (bar) + margin 20-35 (line)     | yes  | 33%   | silent
#   revenue 80-120 (bar) + constant target 100     | yes  | 83%   | silent
#   revenue 4.8-5.0M (bar) + profit 6.0-6.1M       | yes  | 73%   | silent
#   line 42-78 + line 50-70                        | no   | 56%   | silent
#   line 42-78 + constant target 60                | no   | n/a   | silent (flat by data)
#   either layer pins axis_y.position              | -    | -     | silent
# ---------------------------------------------------------------------------


def _line_chart(**kwargs: object) -> Any:
    from dbt_charts.core.compile.models.chart.normalized import LineChart

    return LineChart(**{**_CHART_DEFAULTS, "type": "line", **kwargs})


def _fires(chart: Any, rows: list[dict[str, Any]]) -> bool:
    return bool(detector.detect(_make_ctx(chart, rows)))


_SALES = [42.0, 60.0, 78.0]


def _two_series(second: str, values: list[float]) -> list[dict[str, Any]]:
    return [
        {"month": m, "sales": s, second: v}
        for m, s, v in zip(["Jan", "Feb", "Mar"], _SALES, values, strict=True)
    ]


def test_fires_on_the_fifteen_to_one_bar_and_line() -> None:
    chart = _make_chart(y="sales", layers=[LineLayer(type="line", y="conv")])
    assert _fires(chart, _two_series("conv", [2.6, 3.4, 4.6]))


def test_fires_on_the_fifteen_to_one_line_and_line() -> None:
    chart = _line_chart(y="sales", layers=[LineLayer(type="line", y="conv")])
    assert _fires(chart, _two_series("conv", [2.6, 3.4, 4.6]))


def test_silent_on_a_shared_scale_layer() -> None:
    chart = _make_chart(y="sales", layers=[LineLayer(type="line", y="margin")])
    assert not _fires(chart, _two_series("margin", [20.0, 28.0, 35.0]))


def test_silent_on_a_constant_target_line_over_bars() -> None:
    chart = _make_chart(y="sales", layers=[LineLayer(type="line", y="target")])
    assert not _fires(chart, _two_series("target", [60.0, 60.0, 60.0]))


def test_silent_on_a_steady_bar_under_a_larger_line() -> None:
    chart = _make_chart(y="sales", layers=[LineLayer(type="line", y="profit")])
    rows = [
        {"month": "Jan", "sales": 4_800_000.0, "profit": 6_000_000.0},
        {"month": "Feb", "sales": 5_000_000.0, "profit": 6_100_000.0},
    ]
    assert not _fires(chart, rows)


def test_silent_on_overlapping_lines() -> None:
    chart = _line_chart(y="sales", layers=[LineLayer(type="line", y="avg")])
    assert not _fires(chart, _two_series("avg", [50.0, 60.0, 70.0]))


def test_silent_on_a_constant_target_over_a_zoomed_line() -> None:
    chart = _line_chart(y="sales", layers=[LineLayer(type="line", y="target")])
    assert not _fires(chart, _two_series("target", [60.0, 60.0, 60.0]))


def _styled_line(domain_or_type: dict[str, Any]) -> Any:
    return _line_chart(
        y="sales",
        layers=[LineLayer(type="line", y="conv")],
        style={"axis_y": {"scale": {"continuous": domain_or_type}}},
    )


def test_silent_when_an_authored_domain_makes_the_small_series_fill_the_axis() -> None:
    # conv 2.6-4.6 fills a third of an authored 0-6 axis; sales is the clipped one.
    chart = _styled_line({"domain": [0, 6]})
    assert not _fires(chart, _two_series("conv", [2.6, 3.4, 4.6]))


def test_silent_on_a_log_axis() -> None:
    chart = _styled_line({"type": "log"})
    rows = [
        {"month": "Jan", "sales": 1.0, "conv": 2.0},
        {"month": "Feb", "sales": 100.0, "conv": 3.0},
        {"month": "Mar", "sales": 1000.0, "conv": 5.0},
    ]
    assert not _fires(chart, rows)


def test_reach_threshold_sits_between_five_and_seven_percent() -> None:
    # Bar axis 0-100: a layer topping out at 4 reaches ~4%, at 8 reaches ~8%.
    def fires(top: float) -> bool:
        chart = _make_chart(y="sales", layers=[LineLayer(type="line", y="conv")])
        rows = [
            {"month": "Jan", "sales": 100.0, "conv": top / 2},
            {"month": "Feb", "sales": 90.0, "conv": top},
        ]
        return _fires(chart, rows)

    assert fires(4.0)
    assert not fires(8.0)


def test_bar_reach_is_measured_on_the_axis_the_bar_renders() -> None:
    # Bar base 50-95 + conv 5.5-5.9: the axis is 0-~100, conv reaches 5.9%.
    chart = _make_chart(y="sales", layers=[LineLayer(type="line", y="conv")])
    rows = [
        {"month": "Jan", "sales": 50.0, "conv": 5.5},
        {"month": "Feb", "sales": 95.0, "conv": 5.9},
    ]
    assert _fires(chart, rows)


def test_a_pinned_non_extreme_layer_silences_the_whole_chart() -> None:
    chart = _make_chart(
        y="sales",
        layers=[
            LineLayer(type="line", y="conv"),
            LineLayer(
                type="line", y="margin", axis_y=LayerAxisYStyle(position="right")
            ),
        ],
    )
    rows = [
        {"month": "Jan", "sales": 100.0, "conv": 2.0, "margin": 40.0},
        {"month": "Feb", "sales": 90.0, "conv": 3.0, "margin": 50.0},
    ]
    assert not _fires(chart, rows)
