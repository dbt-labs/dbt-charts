"""Tests for the WARN_BAR_CATEGORY_TICKS render-warning detector.

Detection rule: a vertical bar chart whose resolved axis_x.ticks.visible is on
draws a tick under every bar, repeating what the bars already mark.
"""

from __future__ import annotations

from typing import Any, Literal

from dbt_charts.core.compile.models.chart.normalized import BarChart, Chart, LineChart
from dbt_charts.core.compile.models.style.authored.bar import BarChartStylePatch
from dbt_charts.core.compile.models.style.authored.line import LineChartStylePatch
from dbt_charts.core.diagnostics import WARN_BAR_CATEGORY_TICKS
from dbt_charts.core.render.warnings import (
    WarningContext,
    bar_category_ticks as detector,
)

from ...core._board_utils import make_test_resolved_board, make_test_resolved_chart

_ROWS = [{"cat": c, "val": i} for i, c in enumerate("abc")]
_TICKS_ON = {"axis_x": {"ticks": {"visible": True}}}


def _ctx(chart: Chart, rows: list[dict[str, Any]] = _ROWS) -> WarningContext:
    resolved = make_test_resolved_chart(chart, rows)
    board = make_test_resolved_board(charts={resolved.id: resolved})
    return WarningContext(board_spec=board, chart_results={resolved.id: rows})


def _bar(
    *,
    orientation: str = "vertical",
    style: dict[str, Any] | None = None,
    chart_type: Literal["bar", "histogram"] = "bar",
) -> BarChart:
    return BarChart(
        id="c1",
        type=chart_type,
        query_name="q",
        x="cat",
        y="val",
        style=BarChartStylePatch.model_validate(
            ({"orientation": orientation} if chart_type == "bar" else {})
            | (style or {})
        ),
    )


def test_authored_ticks_on_band_bar_warn() -> None:
    (w,) = detector.detect(_ctx(_bar(style=_TICKS_ON)))
    assert w.code == WARN_BAR_CATEGORY_TICKS.code
    assert w.path == "charts.c1.style.axis_x.ticks.visible"
    assert w.message == (
        "axis_x.ticks.visible is on, so a tick sits under every bar and repeats "
        "what the bars already mark. Set axis_x.ticks.visible: false to turn the "
        "ticks off."
    )


def test_theme_default_off_stays_silent() -> None:
    assert detector.detect(_ctx(_bar())) == []


def test_line_chart_ticks_stay_silent() -> None:
    chart = LineChart(
        id="c1",
        type="line",
        query_name="q",
        x="cat",
        y="val",
        style=LineChartStylePatch.model_validate(_TICKS_ON),
    )
    assert detector.detect(_ctx(chart)) == []


def test_horizontal_bar_stays_silent() -> None:
    assert detector.detect(_ctx(_bar(orientation="horizontal", style=_TICKS_ON))) == []


def test_numeric_x_vertical_bar_stays_silent() -> None:
    rows = [{"cat": i * 1.5, "val": i} for i in range(20)]
    assert detector.detect(_ctx(_bar(style=_TICKS_ON), rows)) == []


def test_histogram_stays_silent() -> None:
    rows = [{"cat": i * 1.5, "val": i} for i in range(20)]
    chart = _bar(style=_TICKS_ON, chart_type="histogram")
    assert detector.detect(_ctx(chart, rows)) == []
