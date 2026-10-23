"""Tests for the QUERY_RESULT_TRUNCATED render-warning detector.

Detection rule: fires on any chart whose query's result was truncated by
execution.max_rows or max_result_bytes this render (executor.truncations()).
"""

from __future__ import annotations

from typing import Any

from pydantic import TypeAdapter

from dbt_charts.core.compile.models.chart.normalized import Chart
from dbt_charts.core.diagnostics import WARN_QUERY_RESULT_TRUNCATED, Diagnostic
from dbt_charts.core.execute.executor import TruncationInfo
from dbt_charts.core.render.warnings import (
    WarningContext,
    query_result_truncated as detector,
)

from ...core._board_utils import make_test_resolved_board


def _make_chart(chart_id: str = "c1", chart_type: str = "bar") -> Chart:
    fields: dict[str, str] = {"id": chart_id, "type": chart_type, "query_name": "q"}
    if chart_type == "kpi":
        fields["value"] = "val"
    else:
        fields["title"] = ""
    return TypeAdapter(Chart).validate_python(fields)


def _make_ctx(
    chart: Chart,
    rows: list[dict[str, Any]],
    chart_truncations: dict[str, TruncationInfo] | None = None,
) -> WarningContext:
    board = make_test_resolved_board(charts={chart.id: chart})
    return WarningContext(
        board_spec=board,
        chart_results={chart.id: rows},
        chart_truncations=chart_truncations or {},
    )


def test_fires_when_chart_query_truncated() -> None:
    chart = _make_chart()
    truncation = TruncationInfo(query_name="q", kept_row_count=5, reason="max_rows")
    ctx = _make_ctx(chart, [{"x": 1}] * 5, chart_truncations={"c1": truncation})
    warnings = detector.detect(ctx)
    assert len(warnings) == 1
    w = warnings[0]
    assert isinstance(w, Diagnostic)
    assert w.code == WARN_QUERY_RESULT_TRUNCATED.code
    assert w.chart == "c1"
    assert w.level == "warning"
    assert "max_rows" in w.message
    assert "5" in w.message


def test_fires_with_max_result_bytes_reason() -> None:
    chart = _make_chart()
    truncation = TruncationInfo(
        query_name="q", kept_row_count=2, reason="max_result_bytes"
    )
    ctx = _make_ctx(chart, [{"x": 1}] * 2, chart_truncations={"c1": truncation})
    warnings = detector.detect(ctx)
    assert len(warnings) == 1
    assert "max_result_bytes" in warnings[0].message


def test_no_fire_when_not_truncated() -> None:
    chart = _make_chart()
    ctx = _make_ctx(chart, [{"x": 1}])
    assert detector.detect(ctx) == []


def test_fires_for_unattributed_truncation_with_chart_none() -> None:
    """A truncated upstream query that no chart owns directly (e.g. a cache-ref
    upstream demand-executed by a composed query) must still surface a warning.
    The diagnostic must carry chart=None and path=queries.<name>."""
    chart = _make_chart()
    board = make_test_resolved_board(charts={chart.id: chart})
    truncation = TruncationInfo(
        query_name="upstream_q", kept_row_count=2, reason="max_rows"
    )
    ctx = WarningContext(
        board_spec=board,
        chart_results={chart.id: [{"x": 1}]},
        unattributed_truncations={"upstream_q": truncation},
    )
    warnings = detector.detect(ctx)
    assert len(warnings) == 1
    w = warnings[0]
    assert w.code == WARN_QUERY_RESULT_TRUNCATED.code
    assert w.chart is None
    assert w.path == "queries.upstream_q"
    assert w.level == "warning"
    assert "max_rows" in w.message
    assert "2" in w.message


def test_fires_for_both_attributed_and_unattributed_truncations() -> None:
    """Both chart-attributed and board-level truncations are emitted together."""
    chart = _make_chart()
    board = make_test_resolved_board(charts={chart.id: chart})
    chart_trunc = TruncationInfo(query_name="q", kept_row_count=5, reason="max_rows")
    board_trunc = TruncationInfo(
        query_name="upstream_q", kept_row_count=3, reason="max_result_bytes"
    )
    ctx = WarningContext(
        board_spec=board,
        chart_results={chart.id: [{"x": 1}] * 5},
        chart_truncations={"c1": chart_trunc},
        unattributed_truncations={"upstream_q": board_trunc},
    )
    warnings = detector.detect(ctx)
    assert len(warnings) == 2
    chart_w = next(w for w in warnings if w.chart == "c1")
    board_w = next(w for w in warnings if w.chart is None)
    assert chart_w.path == "charts.c1.query"
    assert board_w.path == "queries.upstream_q"


def test_fires_only_for_truncated_chart_in_multi_chart_board() -> None:
    chart_truncated = _make_chart(chart_id="c_truncated")
    chart_clean = _make_chart(chart_id="c_clean")
    board = make_test_resolved_board(
        charts={"c_truncated": chart_truncated, "c_clean": chart_clean}
    )
    truncation = TruncationInfo(query_name="q", kept_row_count=3, reason="max_rows")
    ctx = WarningContext(
        board_spec=board,
        chart_results={"c_truncated": [{"x": 1}] * 3, "c_clean": [{"x": 1}]},
        chart_truncations={"c_truncated": truncation},
    )
    warnings = detector.detect(ctx)
    assert len(warnings) == 1
    assert warnings[0].chart == "c_truncated"
