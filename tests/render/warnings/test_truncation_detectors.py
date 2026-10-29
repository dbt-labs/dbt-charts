"""Tests for the text-truncation detector modules.

One detector per surface: chart_title, kpi_label, kpi_inline_fallback,
table_header, table_cell, callout_text, spark_label.  Each test builds a
WarningContext with a TextTruncation record for the relevant surface and
asserts the detector fires.
"""

from __future__ import annotations

from typing import Any

from dbt_charts.core.diagnostics import (
    WARN_CALLOUT_TEXT_TRUNCATED,
    WARN_CHART_TITLE_TRUNCATED,
    WARN_KPI_ALIGN_OVERFLOW,
    WARN_KPI_INLINE_VARIANT_FALLBACK_TO_STACKED,
    WARN_KPI_LABEL_TRUNCATED,
    WARN_KPI_SUPPORT_TRUNCATED,
    WARN_PIE_SIZED_MODE_MISSES_GUARD,
    WARN_SPARK_LABEL_TRUNCATED,
    WARN_SUPPORT_TABLE_CELL_OVERFLOW,
    WARN_TABLE_TEXT_TRUNCATED,
)
from dbt_charts.core.render.chart.text_truncation import TextTruncation
from dbt_charts.core.render.warnings import (
    callout_text_truncated,
    chart_title_truncated,
    kpi_align_overflow,
    kpi_inline_fallback,
    kpi_label_truncated,
    kpi_support_truncated,
    pie_sized_mode_misses_guard,
    spark_label_truncated,
    support_table_cell_overflow,
    table_text_truncated,
)
from dbt_charts.core.render.warnings.base import WarningContext

from ...core._board_utils import make_test_resolved_board, make_test_resolved_chart


def _ctx(
    chart_id: str,
    truncations: list[TextTruncation],
    rows: list[dict[str, Any]] | None = None,
) -> WarningContext:
    from dbt_charts.core.compile.models.chart.normalized import BarChart

    chart = BarChart(id=chart_id, type="bar", query_name="q", x="x", y="y")
    chart_rows = rows or [{"x": "a", "y": 1}]
    resolved = make_test_resolved_chart(chart, chart_rows)
    board = make_test_resolved_board(charts={resolved.id: resolved})
    return WarningContext(
        board_spec=board,
        chart_results={chart_id: chart_rows},
        text_truncations={chart_id: truncations},
    )


# ── chart_title ────────────────────────────────────────────────────────────────


def test_chart_title_detector_fires_on_title_truncation() -> None:
    ctx = _ctx(
        "c1",
        [
            TextTruncation(
                surface="chart_title",
                authored_field="title",
                authored_text="A Very Long Title",
            )
        ],
    )
    warnings = chart_title_truncated.detect(ctx)
    assert len(warnings) == 1
    w = warnings[0]
    assert w.code == WARN_CHART_TITLE_TRUNCATED.code
    assert w.chart == "c1"
    assert "c1" in w.message
    assert "title" in w.message
    assert w.path == "charts.c1.title"


def test_chart_title_detector_ignores_other_surfaces() -> None:
    ctx = _ctx(
        "c2",
        [
            TextTruncation(
                surface="kpi_label", authored_field="label", authored_text="Long Label"
            )
        ],
    )
    assert chart_title_truncated.detect(ctx) == []


def test_chart_title_detector_silent_when_no_truncations() -> None:
    from dbt_charts.core.compile.models.chart.normalized import BarChart

    chart = BarChart(id="c3", type="bar", query_name="q", x="x", y="y")
    rows: list[dict[str, Any]] = [{"x": "a", "y": 1}]
    resolved = make_test_resolved_chart(chart, rows)
    board = make_test_resolved_board(charts={resolved.id: resolved})
    ctx = WarningContext(
        board_spec=board,
        chart_results={"c3": rows},
    )
    assert chart_title_truncated.detect(ctx) == []


# ── kpi_label ─────────────────────────────────────────────────────────────────


def test_kpi_label_detector_fires_on_label_truncation() -> None:
    ctx = _ctx(
        "k1",
        [
            TextTruncation(
                surface="kpi_label",
                authored_field="label",
                authored_text="A Very Long KPI Label",
            )
        ],
    )
    warnings = kpi_label_truncated.detect(ctx)
    assert len(warnings) == 1
    w = warnings[0]
    assert w.code == WARN_KPI_LABEL_TRUNCATED.code
    assert w.chart == "k1"
    assert "k1" in w.message
    assert w.path == "charts.k1.label"


def test_kpi_label_detector_ignores_other_surfaces() -> None:
    ctx = _ctx(
        "k2",
        [
            TextTruncation(
                surface="chart_title", authored_field="title", authored_text="Title"
            )
        ],
    )
    assert kpi_label_truncated.detect(ctx) == []


def test_kpi_support_detector_fires_on_support_truncation() -> None:
    ctx = _ctx(
        "k1",
        [
            TextTruncation(
                surface="kpi_support",
                authored_field="support.label",
                authored_text="a long explainer",
            )
        ],
    )
    warnings = kpi_support_truncated.detect(ctx)
    assert len(warnings) == 1
    w = warnings[0]
    assert w.code == WARN_KPI_SUPPORT_TRUNCATED.code
    assert w.chart == "k1"
    assert "a long explainer" in w.message
    assert w.path == "charts.k1.support.label"


def test_kpi_support_detector_ignores_other_surfaces() -> None:
    ctx = _ctx(
        "k2",
        [
            TextTruncation(
                surface="kpi_label", authored_field="label", authored_text="L"
            )
        ],
    )
    assert kpi_support_truncated.detect(ctx) == []


# ── kpi_inline_fallback ───────────────────────────────────────────────────────


def test_kpi_inline_fallback_detector_fires_on_fallback() -> None:
    ctx = _ctx(
        "k3",
        [
            TextTruncation(
                surface="kpi_inline_fallback",
                authored_field="variant",
                authored_text="inline",
            )
        ],
    )
    warnings = kpi_inline_fallback.detect(ctx)
    assert len(warnings) == 1
    w = warnings[0]
    assert w.code == WARN_KPI_INLINE_VARIANT_FALLBACK_TO_STACKED.code
    assert w.chart == "k3"
    assert "k3" in w.message
    assert w.path == "charts.k3.variant"


def test_kpi_inline_fallback_detector_ignores_other_surfaces() -> None:
    ctx = _ctx(
        "k4",
        [
            TextTruncation(
                surface="kpi_label", authored_field="label", authored_text="Long Label"
            )
        ],
    )
    assert kpi_inline_fallback.detect(ctx) == []


# ── table_text ────────────────────────────────────────────────────────────────


def test_table_text_detector_fires_on_cell_truncation() -> None:
    ctx = _ctx(
        "t2",
        [
            TextTruncation(
                surface="table_cell",
                authored_field="description",
                authored_text="A long description value",
            )
        ],
    )
    warnings = table_text_truncated.detect(ctx)
    assert len(warnings) == 1
    w = warnings[0]
    assert w.code == WARN_TABLE_TEXT_TRUNCATED.code
    assert "description" in w.message


def test_table_text_detector_folds_columns_into_one_warning_per_table() -> None:
    """One warning per table; each truncated column is a related location."""
    ctx = _ctx(
        "t3",
        [
            TextTruncation(
                surface="table_cell",
                authored_field="notes",
                authored_text="First long value",
            ),
            TextTruncation(
                surface="table_cell",
                authored_field="notes",
                authored_text="Second long value",
            ),
            TextTruncation(
                surface="table_cell",
                authored_field="other",
                authored_text="Another long value",
            ),
        ],
    )
    warnings = table_text_truncated.detect(ctx)
    assert len(warnings) == 1
    w = warnings[0]
    assert w.path == "charts.t3"
    assert "column(s) notes, other:" in w.message
    assert "3 value(s)" in w.message
    assert [(r.path, r.message) for r in w.related] == [
        ("charts.t3.style.columns.notes", "notes: 2 value(s) cut"),
        ("charts.t3.style.columns.other", "other: 1 value(s) cut"),
    ]


def test_table_text_detector_ignores_other_surfaces() -> None:
    ctx = _ctx(
        "t4",
        [
            TextTruncation(
                surface="chart_title", authored_field="title", authored_text="Title"
            )
        ],
    )
    assert table_text_truncated.detect(ctx) == []


# ── callout_text ──────────────────────────────────────────────────────────────


def test_callout_text_detector_fires_on_hint_truncation() -> None:
    ctx = _ctx(
        "cl1",
        [
            TextTruncation(
                surface="callout_text",
                authored_field="hint",
                authored_text="A very long hint text",
            )
        ],
    )
    warnings = callout_text_truncated.detect(ctx)
    assert len(warnings) == 1
    w = warnings[0]
    assert w.code == WARN_CALLOUT_TEXT_TRUNCATED.code
    assert w.chart == "cl1"
    assert "hint" in w.message
    assert w.path == "charts.cl1.hint"


def test_callout_text_detector_ignores_other_surfaces() -> None:
    ctx = _ctx(
        "cl2",
        [
            TextTruncation(
                surface="kpi_label", authored_field="label", authored_text="Label"
            )
        ],
    )
    assert callout_text_truncated.detect(ctx) == []


# ── spark_label ───────────────────────────────────────────────────────────────


def test_spark_label_detector_fires_on_label_truncation() -> None:
    ctx = _ctx(
        "s1",
        [
            TextTruncation(
                surface="spark_label",
                authored_field="y",
                authored_text="A Very Long Category Label",
            ),
            TextTruncation(
                surface="spark_label",
                authored_field="y",
                authored_text="Another Long Label",
            ),
        ],
    )
    warnings = spark_label_truncated.detect(ctx)
    assert len(warnings) == 1
    w = warnings[0]
    assert w.code == WARN_SPARK_LABEL_TRUNCATED.code
    assert w.chart == "s1"
    assert "2" in w.message  # truncation_count
    assert w.path == "charts.s1.y"


def test_spark_label_detector_ignores_other_surfaces() -> None:
    ctx = _ctx(
        "s2",
        [
            TextTruncation(
                surface="table_cell", authored_field="col", authored_text="value"
            )
        ],
    )
    assert spark_label_truncated.detect(ctx) == []


def test_kpi_align_overflow_detector_fires_on_overflow() -> None:
    ctx = _ctx(
        "k9",
        [
            TextTruncation(
                surface="kpi_align_overflow",
                authored_field="value",
                authored_text="Revenue",
            )
        ],
    )
    warnings = kpi_align_overflow.detect(ctx)
    assert len(warnings) == 1
    w = warnings[0]
    assert w.code == WARN_KPI_ALIGN_OVERFLOW.code
    assert w.chart == "k9"
    assert "k9" in w.message
    assert w.path == "charts.k9.style.align"


def test_kpi_align_overflow_detector_ignores_other_surfaces() -> None:
    ctx = _ctx(
        "k10",
        [
            TextTruncation(
                surface="kpi_label", authored_field="label", authored_text="Long"
            )
        ],
    )
    assert kpi_align_overflow.detect(ctx) == []


def test_kpi_align_overflow_detector_fires_once_per_chart() -> None:
    """Three overflowing runs on one card are one authoring mistake, not three."""
    ctx = _ctx(
        "k11",
        [
            TextTruncation(
                surface="kpi_align_overflow", authored_field="value", authored_text="a"
            )
            for _ in range(3)
        ],
    )
    assert len(kpi_align_overflow.detect(ctx)) == 1


def test_support_table_cell_overflow_detector_fires_once_per_chart() -> None:
    ctx = _ctx(
        "s1",
        [
            TextTruncation(
                surface="support_table_cell_overflow",
                authored_field="support_table",
                authored_text="46px needed, 22px per band",
            )
        ],
    )
    warnings = support_table_cell_overflow.detect(ctx)
    assert len(warnings) == 1
    w = warnings[0]
    assert w.code == WARN_SUPPORT_TABLE_CELL_OVERFLOW.code
    assert w.chart == "s1"
    assert "46px needed, 22px per band" in w.message
    assert w.path == "charts.s1.support_table"


def test_support_table_cell_overflow_detector_ignores_other_surfaces() -> None:
    ctx = _ctx(
        "s2",
        [
            TextTruncation(
                surface="kpi_label", authored_field="label", authored_text="Long"
            )
        ],
    )
    assert support_table_cell_overflow.detect(ctx) == []


# ── pie_wheel_starved ─────────────────────────────────────────────────────────


def test_pie_sized_mode_detector_reports_the_mode_and_ratio() -> None:
    ctx = _ctx(
        "p1",
        [
            TextTruncation(
                surface="pie_wheel_starved",
                authored_field="style.slice_mark.labels",
                authored_text="direct|0.41|0.6",
            )
        ],
    )
    (w,) = pie_sized_mode_misses_guard.detect(ctx)
    assert w.code == WARN_PIE_SIZED_MODE_MISSES_GUARD.code
    assert w.chart == "p1"
    assert "direct labels" in w.message
    assert "41%" in w.message
    assert "60%" in w.message


def test_pie_sized_mode_engine_warning_reaches_its_detector() -> None:
    from dbt_charts.core.render.chart.text_truncation import collect_text_truncations
    from dbt_charts.core.render.engine import _WARNING_SINKS

    with collect_text_truncations() as records:
        _WARNING_SINKS["WARN-PIE-SIZED-MODE-MISSES-GUARD"](
            {"chart_id": "p2", "mode": "hybrid", "ratio": 0.5, "floor": 0.6}
        )
    (record,) = records["p2"]
    assert record.surface == "pie_wheel_starved"
    assert record.authored_text == "hybrid|0.5|0.6"
