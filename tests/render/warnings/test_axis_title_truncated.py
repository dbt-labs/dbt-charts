"""Tests for the WARN_AXIS_TITLE_TRUNCATED render-warning detector.

Detection rule: fires when an axis title is pre-wrapped to ≤2 lines and the
text had to be cut with an ellipsis — either because the authored title ran
past two wrapped lines at the given extent, or because a single word was wider
than the budget and got cut to fit. The truncation fact is recorded by the
single wrap site (the engine's axis-title wrap); no re-measurement
occurs downstream.

The warning path and message reference the AUTHORED field name ("x_label" /
"y_label") so the squiggle lands on the YAML line the author actually wrote —
a horizontal bar swaps VL channels (authored y_label renders on VL x), but the
warning still names "y_label".
"""

from __future__ import annotations

from typing import Any

from dbt_charts.core.diagnostics import WARN_AXIS_TITLE_TRUNCATED
from dbt_charts.core.render.chart.text_truncation import (
    TextTruncation,
)
from dbt_charts.core.render.warnings import axis_title_truncated as detector
from dbt_charts.core.render.warnings.base import WarningContext

from ...core._board_utils import make_test_resolved_board, make_test_resolved_chart

# A title long enough to exceed two wrapped lines at a narrow extent.
_VERY_LONG = (
    "Connections Timeline Count Connections Observed Across All Categories "
    "Over The Past Twelve Months"
)
# A short title that fits in one line at normal extents.
_SHORT = "Revenue"


def _make_ctx(
    chart: Any,
    rows: list[dict[str, Any]],
    text_truncations: dict[str, Any] | None = None,
) -> WarningContext:
    resolved = make_test_resolved_chart(chart, rows)
    board = make_test_resolved_board(charts={resolved.id: resolved})
    return WarningContext(
        board_spec=board,
        chart_results={resolved.id: rows},
        text_truncations=text_truncations or {},
    )


# ── truncation-fact threading ─────────────────────────────────────────────────


# ── detector output ───────────────────────────────────────────────────────────


def test_detector_fires_on_truncated_y_title() -> None:
    """Over-long y title → exactly one warning naming chart_id, authored field, text."""
    from dbt_charts.core.compile.models.chart.normalized import BarChart

    chart = BarChart(
        id="c1", type="bar", query_name="q", x="month", y="revenue", y_label=_VERY_LONG
    )
    rows = [{"month": "Jan", "revenue": 10}]
    truncations = {
        "c1": [
            TextTruncation(
                surface="axis_title", authored_field="y_label", authored_text=_VERY_LONG
            )
        ]
    }
    ctx = _make_ctx(chart, rows, truncations)
    warnings = detector.detect(ctx)

    assert len(warnings) == 1
    w = warnings[0]
    assert w.code == WARN_AXIS_TITLE_TRUNCATED.code
    assert w.chart == "c1"
    assert "c1" in w.message
    assert "y_label" in w.message
    assert _VERY_LONG in w.message
    assert w.path == "charts.c1.y_label"


def test_detector_fires_on_truncated_x_title() -> None:
    """Over-long x title → exactly one warning naming chart_id, authored field, text."""
    from dbt_charts.core.compile.models.chart.normalized import BarChart

    chart = BarChart(
        id="c2", type="bar", query_name="q", x="month", y="revenue", x_label=_VERY_LONG
    )
    rows = [{"month": "Jan", "revenue": 10}]
    truncations = {
        "c2": [
            TextTruncation(
                surface="axis_title", authored_field="x_label", authored_text=_VERY_LONG
            )
        ]
    }
    ctx = _make_ctx(chart, rows, truncations)
    warnings = detector.detect(ctx)

    assert len(warnings) == 1
    w = warnings[0]
    assert w.code == WARN_AXIS_TITLE_TRUNCATED.code
    assert "c2" in w.message
    assert "x_label" in w.message
    assert _VERY_LONG in w.message
    assert w.path == "charts.c2.x_label"


def test_detector_silent_on_clean_wrap() -> None:
    """A title that fits within two lines → no warning."""
    from dbt_charts.core.compile.models.chart.normalized import BarChart

    chart = BarChart(
        id="c3", type="bar", query_name="q", x="month", y="revenue", y_label=_SHORT
    )
    rows = [{"month": "Jan", "revenue": 10}]
    ctx = _make_ctx(chart, rows, {})  # no truncations
    assert detector.detect(ctx) == []


def test_detector_produces_one_warning_per_truncated_axis() -> None:
    """Both x and y truncated → two warnings, one per authored field."""
    from dbt_charts.core.compile.models.chart.normalized import BarChart

    chart = BarChart(
        id="c4",
        type="bar",
        query_name="q",
        x="month",
        y="revenue",
        x_label=_VERY_LONG,
        y_label=_VERY_LONG,
    )
    rows = [{"month": "Jan", "revenue": 10}]
    truncations = {
        "c4": [
            TextTruncation(
                surface="axis_title", authored_field="x_label", authored_text=_VERY_LONG
            ),
            TextTruncation(
                surface="axis_title", authored_field="y_label", authored_text=_VERY_LONG
            ),
        ]
    }
    ctx = _make_ctx(chart, rows, truncations)
    warnings = detector.detect(ctx)
    assert len(warnings) == 2
    paths = {w.path for w in warnings}
    assert paths == {"charts.c4.x_label", "charts.c4.y_label"}


# ── horizontal bar: warning names authored field, not VL channel ──────────────


# ── end-to-end wiring test (HIGH-3) ──────────────────────────────────────────


def test_render_dashboard_fires_warning_for_truncated_axis_title(
    tmp_path, local_project
) -> None:
    """render_dashboard wires axis_title_truncations into the warning pipeline.

    This is the wiring test: disabling the axis_title_truncations pass-through
    in renderer.py would leave this test failing even though all unit tests pass.
    A board with an over-long y_label → WARN_AXIS_TITLE_TRUNCATED in result.warnings.
    A board with a short y_label → no WARN_AXIS_TITLE_TRUNCATED.
    """
    from unittest.mock import Mock

    from dbt_charts.core.board import render_dashboard
    from dbt_charts.core.project import InMemoryBoard

    ok = Mock()
    ok.is_success = True
    ok.data = [{"month": "Jan", "revenue": 5}]
    ok.resolved_relations = None
    ok.truncated_reason = None
    registry = Mock()
    registry.execute.return_value = ok
    registry.project_file_sources.return_value = {}

    project = local_project(tmp_path)
    long_label = (
        "Connections Timeline Count Connections Observed Across All Categories "
        "Over The Past Twelve Months"
    )
    truncated_board = InMemoryBoard(
        f"""
source: examples_db
queries:
  q: SELECT month, revenue FROM t
charts:
  c:
    query: q
    type: bar
    x: month
    y: revenue
    y_label: "{long_label}"
    width: 400
    style: {{orientation: vertical}}
rows:
  - c
""",
        path=project.path("charts/_t.yml"),
    )
    result = render_dashboard(
        board=truncated_board,
        adapter_registry=registry,
        format="json",
        project=project,
        result_cache=None,
    )
    codes = {w.code for w in result.warnings}
    assert WARN_AXIS_TITLE_TRUNCATED.code in codes, (
        f"Expected WARN_AXIS_TITLE_TRUNCATED in warnings, got: {codes}"
    )

    # A horizontal bar renders the authored y_label on the VL x channel; the
    # warning must still point at the authored key. This pins the
    # x_authored_field/y_authored_field mapping in bar._emit_horizontal —
    # removing those kwargs flips the path to charts.c.x_label and fails here.
    horizontal_board = InMemoryBoard(
        f"""
source: examples_db
queries:
  q: SELECT month, revenue FROM t
charts:
  c:
    query: q
    type: bar
    x: month
    y: revenue
    y_label: "{long_label}"
    width: 200
    style: {{orientation: horizontal}}
rows:
  - c
""",
        path=project.path("charts/_t.yml"),
    )
    horizontal_result = render_dashboard(
        board=horizontal_board,
        adapter_registry=registry,
        format="json",
        project=project,
        result_cache=None,
    )
    horizontal_warnings = [
        w
        for w in horizontal_result.warnings
        if w.code == WARN_AXIS_TITLE_TRUNCATED.code
    ]
    assert horizontal_warnings, (
        "Expected WARN_AXIS_TITLE_TRUNCATED for the horizontal bar, got: "
        f"{ {w.code for w in horizontal_result.warnings} }"
    )
    assert {w.path for w in horizontal_warnings} == {"charts.c.y_label"}

    # A short label must NOT trigger the warning.
    clean_board = InMemoryBoard(
        """
source: examples_db
queries:
  q: SELECT month, revenue FROM t
charts:
  c:
    query: q
    type: bar
    x: month
    y: revenue
    y_label: Revenue
    width: 400
    height: 220
rows:
  - c
""",
        path=project.path("charts/_t.yml"),
    )
    clean_result = render_dashboard(
        board=clean_board,
        adapter_registry=registry,
        format="json",
        project=project,
        result_cache=None,
    )
    clean_codes = {w.code for w in clean_result.warnings}
    assert WARN_AXIS_TITLE_TRUNCATED.code not in clean_codes


def test_truncated_titles_warn_once_per_chart_and_field(
    tmp_path, local_project
) -> None:
    """A chart renders once to size and once to paint; only the paint speaks."""
    from unittest.mock import Mock

    from dbt_charts.core.board import render_dashboard
    from dbt_charts.core.diagnostics import WARN_CHART_TITLE_TRUNCATED
    from dbt_charts.core.project import InMemoryBoard

    ok = Mock()
    ok.is_success = True
    ok.data = [{"month": "Jan", "revenue": 5}]
    ok.resolved_relations = None
    ok.truncated_reason = None
    registry = Mock()
    registry.execute.return_value = ok
    registry.project_file_sources.return_value = {}

    project = local_project(tmp_path)
    long_text = (
        "Connections Timeline Count Connections Observed Across All Categories "
        "Over The Past Twelve Months"
    )
    board = InMemoryBoard(
        f"""
source: examples_db
queries:
  q: SELECT month, revenue FROM t
charts:
  c:
    query: q
    type: bar
    x: month
    y: revenue
    title: "{long_text}"
    y_label: "{long_text}"
    width: 200
rows:
  - c
""",
        path=project.path("charts/_t.yml"),
    )
    result = render_dashboard(
        board=board,
        adapter_registry=registry,
        format="json",
        project=project,
        result_cache=None,
    )
    paths = sorted(
        str(w.path)
        for w in result.warnings
        if w.code in {WARN_AXIS_TITLE_TRUNCATED.code, WARN_CHART_TITLE_TRUNCATED.code}
    )
    assert paths == ["charts.c.title", "charts.c.y_label"]
