"""Tests for the QUERY_RETURNED_ZERO_ROWS render-warning detector.

Detection rule: fires on any chart whose query returned zero rows.
"""

from __future__ import annotations

from typing import Any

from pydantic import TypeAdapter

from dbt_charts.core.compile.models.chart.normalized import Chart
from dbt_charts.core.diagnostics import WARN_QUERY_RETURNED_ZERO_ROWS, Diagnostic
from dbt_charts.core.render.warnings import (
    WarningContext,
    query_returned_zero_rows as detector,
)

from ...core._board_utils import make_test_resolved_board

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_chart(chart_id: str = "c1", chart_type: str = "bar") -> Chart:
    fields: dict[str, str] = {"id": chart_id, "type": chart_type, "query_name": "q"}
    if chart_type == "kpi":
        fields["value"] = "val"
    else:
        fields["title"] = ""
    return TypeAdapter(Chart).validate_python(fields)


def _make_ctx(chart: Chart, rows: list[dict[str, Any]]) -> WarningContext:
    board = make_test_resolved_board(charts={chart.id: chart})
    return WarningContext(
        board_spec=board,
        chart_results={chart.id: rows},
    )


# ---------------------------------------------------------------------------
# True-positive: zero rows fires
# ---------------------------------------------------------------------------


def test_fires_when_zero_rows() -> None:
    """Canonical case: query returned nothing — warning must fire."""
    chart = _make_chart()
    ctx = _make_ctx(chart, [])
    warnings = detector.detect(ctx)
    assert len(warnings) == 1
    w = warnings[0]
    assert isinstance(w, Diagnostic)
    assert w.code == WARN_QUERY_RETURNED_ZERO_ROWS.code
    assert w.chart == "c1"
    assert w.field is None
    assert "zero rows" in w.message
    assert w.fix is not None and "WHERE" in w.fix


# ---------------------------------------------------------------------------
# True-negative: rows present — must not fire
# ---------------------------------------------------------------------------


def test_no_fire_when_rows_present() -> None:
    """A chart with rows should not trigger the detector."""
    chart = _make_chart()
    rows = [{"x": "a", "y": 1}]
    ctx = _make_ctx(chart, rows)
    assert detector.detect(ctx) == []


# ---------------------------------------------------------------------------
# Multiple charts: only empty ones fire
# ---------------------------------------------------------------------------


def test_fires_only_for_empty_chart_in_multi_chart_board() -> None:
    """When multiple charts are present, only the empty one fires."""
    chart_empty = _make_chart(chart_id="c_empty")
    chart_full = _make_chart(chart_id="c_full")
    board = make_test_resolved_board(
        charts={"c_empty": chart_empty, "c_full": chart_full}
    )
    ctx = WarningContext(
        board_spec=board,
        chart_results={
            "c_empty": [],
            "c_full": [{"x": "a", "y": 1}],
        },
    )
    warnings = detector.detect(ctx)
    assert len(warnings) == 1
    assert warnings[0].chart == "c_empty"


# ---------------------------------------------------------------------------
# Guard: chart absent from chart_results must be skipped
# ---------------------------------------------------------------------------


def test_no_fire_when_chart_not_in_chart_results() -> None:
    """A chart with no entry in chart_results (failed execute) must be skipped."""
    chart = _make_chart()
    board = make_test_resolved_board(charts={chart.id: chart})
    ctx = WarningContext(
        board_spec=board,
        chart_results={},  # no entry for this chart
    )
    assert detector.detect(ctx) == []


# ---------------------------------------------------------------------------
# Truncated-to-zero: not the "honest empty case" this detector covers
# ---------------------------------------------------------------------------


def test_no_fire_when_zero_rows_is_explained_by_truncation() -> None:
    """A single oversized-first-row result byte-truncates to zero rows —
    that is WARN_QUERY_RESULT_TRUNCATED's story to tell (kept_row_count=0),
    not this detector's: the query did not honestly return nothing, its
    result was cut down to nothing by the max_result_bytes ceiling."""
    from dbt_charts.core.execute.executor import TruncationInfo

    chart = _make_chart()
    board = make_test_resolved_board(charts={chart.id: chart})
    ctx = WarningContext(
        board_spec=board,
        chart_results={chart.id: []},
        chart_truncations={
            chart.id: TruncationInfo(
                query_name="q", kept_row_count=0, reason="max_result_bytes"
            )
        },
    )
    assert detector.detect(ctx) == []
