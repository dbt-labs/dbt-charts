from __future__ import annotations

from dbt_charts.core.compile.models.chart.normalized import Chart

"""Tests for the Y_ENCODING_MOSTLY_NULL render-warning detector.

Detection rule: fires on any chart where the y-encoding field is >50% NULL
across the chart result rows.

Threshold semantics: strictly greater than 50%.
  - 1/1 NULL  = 100% → fires
  - 2/3 NULL  = 67%  → fires
  - 1/2 NULL  = 50%  → does NOT fire (exactly at boundary)
  - 1/3 NULL  = 33%  → does NOT fire
  - 0/3 NULL  = 0%   → does NOT fire
"""

from typing import Any

from pydantic import TypeAdapter

from dbt_charts.core.compile.models.chart.normalized import (
    CalloutChart,
    KpiChart,
)
from dbt_charts.core.diagnostics import WARN_Y_ENCODING_MOSTLY_NULL, Diagnostic
from dbt_charts.core.render.warnings import (
    WarningContext,
    y_encoding_mostly_null as detector,
)

from ...core._board_utils import make_test_resolved_board, make_test_resolved_chart

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_CHART_DEFAULTS = {
    "id": "c1",
    "type": "bar",
    "query_name": "q",
    "title": "",
}


def _make_chart(**kwargs: object) -> Chart:
    return TypeAdapter(Chart).validate_python(dict(**{**_CHART_DEFAULTS, **kwargs}))


def _make_ctx(chart: Chart, rows: list[dict[str, Any]]) -> WarningContext:
    resolved = make_test_resolved_chart(chart)
    board = make_test_resolved_board(charts={chart.id: resolved})
    return WarningContext(
        board_spec=board,
        chart_results={chart.id: rows},
    )


# ---------------------------------------------------------------------------
# True-positive: all rows NULL (100%)
# ---------------------------------------------------------------------------


def test_fires_when_all_y_values_null() -> None:
    """3/3 NULL (100%) — must fire."""
    chart = _make_chart(y="revenue")
    rows = [
        {"region": "West", "revenue": None},
        {"region": "East", "revenue": None},
        {"region": "North", "revenue": None},
    ]
    ctx = _make_ctx(chart, rows)
    warnings = detector.detect(ctx)
    assert len(warnings) == 1
    w = warnings[0]
    assert isinstance(w, Diagnostic)
    assert w.code == WARN_Y_ENCODING_MOSTLY_NULL.code
    assert w.chart == "c1"
    assert w.field == "revenue"
    assert "revenue" in w.message
    assert w.fix is not None


# ---------------------------------------------------------------------------
# True-positive: majority NULL (2/3 = 67%)
# ---------------------------------------------------------------------------


def test_fires_when_majority_y_values_null() -> None:
    """2/3 NULL (67%) — must fire."""
    chart = _make_chart(y="revenue")
    rows = [
        {"region": "West", "revenue": None},
        {"region": "East", "revenue": None},
        {"region": "North", "revenue": 100},
    ]
    ctx = _make_ctx(chart, rows)
    warnings = detector.detect(ctx)
    assert len(warnings) == 1
    assert warnings[0].field == "revenue"


# ---------------------------------------------------------------------------
# Boundary: exactly 50% NULL (1/2) — must NOT fire
# ---------------------------------------------------------------------------


def test_no_fire_at_exactly_50_percent_null() -> None:
    """1/2 NULL = exactly 50% — strictly greater-than rule means no fire."""
    chart = _make_chart(y="revenue")
    rows = [
        {"region": "West", "revenue": None},
        {"region": "East", "revenue": 200},
    ]
    ctx = _make_ctx(chart, rows)
    assert detector.detect(ctx) == []


# ---------------------------------------------------------------------------
# True-negative: minority NULL (1/3 = 33%)
# ---------------------------------------------------------------------------


def test_no_fire_when_minority_y_values_null() -> None:
    """1/3 NULL (33%) — must not fire."""
    chart = _make_chart(y="revenue")
    rows = [
        {"region": "West", "revenue": None},
        {"region": "East", "revenue": 200},
        {"region": "North", "revenue": 150},
    ]
    ctx = _make_ctx(chart, rows)
    assert detector.detect(ctx) == []


# ---------------------------------------------------------------------------
# True-negative: no NULLs at all
# ---------------------------------------------------------------------------


def test_no_fire_when_no_y_values_null() -> None:
    """0/3 NULL (0%) — must not fire."""
    chart = _make_chart(y="revenue")
    rows = [
        {"region": "West", "revenue": 100},
        {"region": "East", "revenue": 200},
        {"region": "North", "revenue": 150},
    ]
    ctx = _make_ctx(chart, rows)
    assert detector.detect(ctx) == []


# ---------------------------------------------------------------------------
# Guard: zero-row result set — no fire (covered by QUERY_RETURNED_ZERO_ROWS)
# ---------------------------------------------------------------------------


def test_no_fire_when_zero_rows() -> None:
    """Empty result set must not fire; other detector handles zero-rows."""
    chart = _make_chart(y="revenue")
    ctx = _make_ctx(chart, [])
    assert detector.detect(ctx) == []


# ---------------------------------------------------------------------------
# Guard: chart has no y encoding (kpi, callout)
# ---------------------------------------------------------------------------


def test_no_fire_when_no_y_encoding_kpi() -> None:
    """KPI chart without y encoding must not fire."""
    chart = KpiChart(id="kpi1", type="kpi", query_name="q", value="value")
    rows = [{"value": None}, {"value": None}]
    board = make_test_resolved_board(charts={chart.id: chart})
    ctx = WarningContext(
        board_spec=board,
        chart_results={chart.id: rows},
    )
    assert detector.detect(ctx) == []


def test_no_fire_when_no_y_encoding_callout() -> None:
    """Callout chart without y encoding must not fire."""
    chart = CalloutChart(id="t1", type="callout", message="note")
    rows = [{"label": None}, {"label": None}]
    board = make_test_resolved_board(charts={chart.id: chart})
    ctx = WarningContext(
        board_spec=board,
        chart_results={chart.id: rows},
    )
    assert detector.detect(ctx) == []


# ---------------------------------------------------------------------------
# Guard: chart_id not in chart_results (failed execution)
# ---------------------------------------------------------------------------


def test_no_fire_when_chart_not_in_chart_results() -> None:
    """Detector must skip charts absent from ctx.chart_results (execution failed)."""
    chart = _make_chart(y="revenue")
    board = make_test_resolved_board(charts={chart.id: chart})
    ctx = WarningContext(
        board_spec=board,
        chart_results={},  # chart absent — execution failed
    )
    assert detector.detect(ctx) == []


# ---------------------------------------------------------------------------
# Multi-y: one y column mostly NULL, others fine → fires for that column only
# ---------------------------------------------------------------------------


def test_fires_for_multi_y_when_one_column_mostly_null() -> None:
    """Multi-y chart: one column 2/3 NULL fires once (for that column)."""
    chart = _make_chart(y=["revenue", "profit"])
    rows = [
        {"region": "West", "revenue": None, "profit": 10},
        {"region": "East", "revenue": None, "profit": 20},
        {"region": "North", "revenue": 100, "profit": 30},
    ]
    ctx = _make_ctx(chart, rows)
    warnings = detector.detect(ctx)
    assert len(warnings) == 1
    assert warnings[0].field == "revenue"


def test_fires_once_per_offending_y_column_in_multi_y() -> None:
    """Multi-y chart: both columns mostly NULL → two warnings."""
    chart = _make_chart(y=["revenue", "profit"])
    rows = [
        {"region": "West", "revenue": None, "profit": None},
        {"region": "East", "revenue": None, "profit": None},
        {"region": "North", "revenue": 100, "profit": 30},
    ]
    ctx = _make_ctx(chart, rows)
    warnings = detector.detect(ctx)
    assert len(warnings) == 2
    fields = {w.field for w in warnings}
    assert fields == {"revenue", "profit"}


def test_no_fire_for_multi_y_when_no_column_mostly_null() -> None:
    """Multi-y chart where neither column crosses threshold must not fire."""
    chart = _make_chart(y=["revenue", "profit"])
    rows = [
        {"region": "West", "revenue": 100, "profit": None},
        {"region": "East", "revenue": 200, "profit": 20},
        {"region": "North", "revenue": 150, "profit": 30},
    ]
    ctx = _make_ctx(chart, rows)
    assert detector.detect(ctx) == []


# ---------------------------------------------------------------------------
# Missing y key in row dicts — rows without the key count as NULL
# ---------------------------------------------------------------------------


def test_fires_for_multi_y_scatter_when_one_column_mostly_null() -> None:
    """Same as ``test_fires_for_multi_y_when_one_column_mostly_null``, for
    scatter's own wide fold -- scatter joined the wide-measures shape this
    detector already covers for bar/area/line. Reads the real authored
    measure columns, not the synthetic WIDE_VALUE_FIELD (absent from every
    row), which would otherwise read as 100% null regardless of the data."""
    chart = _make_chart(type="scatter", x="region", y=["revenue", "profit"])
    rows = [
        {"region": "West", "revenue": None, "profit": 10},
        {"region": "East", "revenue": None, "profit": 20},
        {"region": "North", "revenue": 100, "profit": 30},
    ]
    ctx = _make_ctx(chart, rows)
    warnings = detector.detect(ctx)
    assert len(warnings) == 1
    assert warnings[0].field == "revenue"


def test_missing_y_key_in_row_counts_as_null() -> None:
    """Rows where the y key is absent are treated as NULL (same semantics as None value)."""
    chart = _make_chart(y="revenue")
    # 2 rows missing the key entirely, 1 row has a real value — 2/3 → fires
    rows = [
        {"region": "West"},  # key absent → counts as NULL
        {"region": "East"},  # key absent → counts as NULL
        {"region": "North", "revenue": 100},
    ]
    ctx = _make_ctx(chart, rows)
    warnings = detector.detect(ctx)
    assert len(warnings) == 1
    assert warnings[0].field == "revenue"
