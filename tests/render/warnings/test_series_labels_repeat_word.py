"""Tests for the SERIES_LABELS_REPEAT_WORD render-warning detector.

See ``series_labels_repeat_word.py``'s module docstring for the detection
rule: every series label shares a leading/trailing word run; the title and
y_label only decide whether the fix says drop the word or move it.
"""

from __future__ import annotations

import datetime
from typing import Any

from dbt_charts.core.compile.models.chart.normalized import (
    BarChart,
    LineChart,
    ScatterChart,
)
from dbt_charts.core.diagnostics import WARN_SERIES_LABELS_REPEAT_WORD, Diagnostic
from dbt_charts.core.render.warnings import (
    WarningContext,
    series_labels_repeat_word as detector,
)

from ...core._board_utils import make_test_resolved_board, make_test_resolved_chart


def _ctx(chart: Any, rows: list[dict[str, Any]]) -> WarningContext:
    resolved = make_test_resolved_chart(chart, rows)
    board = make_test_resolved_board(charts={resolved.id: resolved})
    return WarningContext(
        board_spec=board,
        chart_results={resolved.id: rows},
        vega_specs={},
    )


def test_fires_on_wide_measures_sharing_a_word() -> None:
    chart = LineChart(
        id="c1",
        type="line",
        query_name="q",
        x="month",
        y=["documents_created", "documents_completed"],
        title="Document Volume",
    )
    rows = [{"month": "Jan", "documents_created": 1, "documents_completed": 2}]
    warnings = detector.detect(_ctx(chart, rows))
    assert len(warnings) == 1
    w = warnings[0]
    assert isinstance(w, Diagnostic)
    assert w.code == WARN_SERIES_LABELS_REPEAT_WORD.code
    assert w.chart == "c1"
    assert w.path == "charts.c1.y"
    assert "documents" in w.message
    assert w.fix is not None
    assert "'created'" in w.fix and "completed" in w.fix


def test_fires_on_long_form_color_values_sharing_a_word() -> None:
    chart = LineChart(
        id="c1",
        type="line",
        query_name="q",
        x="month",
        y="value",
        color="status",
        title="Document Volume",
    )
    rows = [
        {"month": "Jan", "value": 1, "status": "Documents Created"},
        {"month": "Jan", "value": 2, "status": "Documents Completed"},
    ]
    warnings = detector.detect(_ctx(chart, rows))
    assert len(warnings) == 1
    w = warnings[0]
    assert w.path == "charts.c1.color"
    assert "Documents" in w.message


def test_fires_on_a_trailing_shared_word() -> None:
    chart = LineChart(
        id="c1",
        type="line",
        query_name="q",
        x="month",
        y=["created_documents", "completed_documents"],
        title="Document Volume",
    )
    rows = [{"month": "Jan", "created_documents": 1, "completed_documents": 2}]
    warnings = detector.detect(_ctx(chart, rows))
    assert len(warnings) == 1
    fix = warnings[0].fix
    assert fix is not None
    assert "'created'" in fix and "completed" in fix


def test_silent_on_a_single_series() -> None:
    chart = LineChart(
        id="c1",
        type="line",
        query_name="q",
        x="month",
        y="documents_created",
        title="Document Volume",
    )
    rows = [{"month": "Jan", "documents_created": 1}]
    assert detector.detect(_ctx(chart, rows)) == []


def test_silent_when_a_label_would_become_empty() -> None:
    """One label ("documents") IS the shared run -- stripping it leaves
    nothing, so no fix can be suggested and the detector stays silent."""
    chart = LineChart(
        id="c1",
        type="line",
        query_name="q",
        x="month",
        y=["documents", "documents_created"],
        title="Document Volume",
    )
    rows = [{"month": "Jan", "documents": 1, "documents_created": 2}]
    assert detector.detect(_ctx(chart, rows)) == []


def test_silent_on_wide_measures_crossed_with_color() -> None:
    """Wide + color: composite labels ('<value> - <measure>') are not this
    detector's to shorten."""
    chart = BarChart(
        id="c1",
        type="bar",
        query_name="q",
        x="month",
        y=["documents_created", "documents_completed"],
        color="region",
        title="Document Volume",
    )
    rows = [
        {
            "month": "Jan",
            "documents_created": 1,
            "documents_completed": 2,
            "region": "US",
        }
    ]
    assert detector.detect(_ctx(chart, rows)) == []


def _wide(
    y: list[str],
    title: str | None,
    chart_type: Any = LineChart,
    y_label: str | None = None,
) -> list[Diagnostic]:
    authored = {"title": title, "y_label": y_label}
    chart = chart_type(
        id="c1",
        type="scatter" if chart_type is ScatterChart else "line",
        query_name="q",
        x="month",
        y=y,
        **{k: v for k, v in authored.items() if v is not None},
    )
    return detector.detect(_ctx(chart, [{"month": "Jan", **dict.fromkeys(y, 1)}]))


def test_fires_on_a_wide_scatter() -> None:
    warnings = _wide(
        ["documents_created", "documents_completed"], "Document Volume", ScatterChart
    )
    assert len(warnings) == 1
    assert warnings[0].path == "charts.c1.y"


def test_a_short_leading_word_does_not_hide_the_repeat_behind_it() -> None:
    warnings = _wide(
        ["us_documents_created", "us_documents_completed"], "Document Volume"
    )
    assert len(warnings) == 1
    assert warnings[0].fix is not None
    assert "'us created'" in warnings[0].fix


def test_a_repeat_at_both_ends_is_dropped_from_both() -> None:
    warnings = _wide(
        ["documents_created_trend", "documents_completed_trend"],
        "Document Volume Trend",
    )
    assert len(warnings) == 1
    assert warnings[0].fix is not None
    assert "'created', 'completed'" in warnings[0].fix


def test_fires_on_snake_case_color_values() -> None:
    chart = LineChart(
        id="c1",
        type="line",
        query_name="q",
        x="month",
        y="value",
        color="status",
        title="Document Volume",
    )
    rows = [
        {"month": "Jan", "value": 1, "status": "documents_created"},
        {"month": "Jan", "value": 2, "status": "documents_completed"},
    ]
    assert len(detector.detect(_ctx(chart, rows))) == 1


def test_silent_when_the_shared_word_is_filler() -> None:
    assert _wide(["ad_clicks", "ad_views"], "Ad Performance") == []


def test_silent_when_the_fix_would_fold_two_series_into_one() -> None:
    assert _wide(["paid_order", "paid_orders"], "Order Volume") == []


def test_silent_on_a_color_column_with_one_value() -> None:
    chart = LineChart(
        id="c1",
        type="line",
        query_name="q",
        x="month",
        y="value",
        color="status",
        title="Document Volume",
    )
    rows = [{"month": "Jan", "value": 1, "status": "Documents Created"}]
    assert detector.detect(_ctx(chart, rows)) == []


_DOCS = ["documents_created", "documents_completed"]


def test_a_word_the_title_already_says_is_dropped() -> None:
    (w,) = _wide(_DOCS, "Document Volume")
    assert w.fix is not None
    assert "already says" in w.fix and "'Document Volume'" in w.fix


def test_a_word_the_y_label_already_says_is_dropped() -> None:
    (w,) = _wide(_DOCS, "Volume Over Time", y_label="Documents")
    assert w.fix is not None
    assert "already says" in w.fix


def test_a_word_the_title_lacks_moves_to_the_title() -> None:
    (w,) = _wide(["avg_order_value", "avg_basket_size"], "Basket Metrics")
    assert w.fix is not None
    assert "already says" not in w.fix
    assert "'order value', 'basket size'" in w.fix


def test_fires_without_a_title() -> None:
    (w,) = _wide(_DOCS, None)
    assert w.fix is not None
    assert "already says" not in w.fix


def test_silent_when_the_remainder_cannot_stand_alone() -> None:
    assert _wide(["region_a", "region_b"], "Sales") == []


def test_silent_on_date_color_values() -> None:
    chart = LineChart(
        id="c1",
        type="line",
        query_name="q",
        x="month",
        y="value",
        color="cohort",
    )
    rows = [
        {"month": "Jan", "value": 1, "cohort": datetime.date(2026, m, 1)}
        for m in (1, 2, 3)
    ]
    assert detector.detect(_ctx(chart, rows)) == []


def test_title_punctuation_does_not_hide_the_word() -> None:
    (w,) = _wide(_DOCS, "Documents: Created vs Completed")
    assert w.fix is not None
    assert "already says" in w.fix


def _color(
    values: list[str], title: str, y_label: str | None = None
) -> list[Diagnostic]:
    chart = LineChart(
        id="c1",
        type="line",
        query_name="q",
        x="month",
        y="value",
        color="g",
        title=title,
        y_label=y_label,
    )
    rows = [{"month": "Jan", "value": 1, "g": v} for v in values]
    return detector.detect(_ctx(chart, rows))


def test_silent_on_color_values_sharing_a_word_the_title_lacks() -> None:
    assert _color(["North America", "South America"], "Sales by Region") == []
    assert _color(["New York", "New Jersey", "New Mexico"], "Customers") == []


def test_fires_on_color_values_sharing_a_word_the_title_says() -> None:
    (w,) = _color(["Pro Plan", "Starter Plan"], "Revenue by Plan")
    assert w.fix is not None
    assert "already says" in w.fix


def test_fires_on_color_values_sharing_a_word_the_y_label_says() -> None:
    assert len(_color(["Google Ads", "Bing Ads"], "Signups", y_label="Ad Signups")) == 1
