"""A trend is gated by the mark that draws the column it reads, not the chart type."""

from __future__ import annotations

from datetime import UTC, date, datetime
from typing import Any

import pytest

from dbt_charts.core.compile.config import get_default_theme_name, get_theme_style
from dbt_charts.core.compile.errors import CompilationError
from dbt_charts.core.compile.models.chart.normalized import (
    AreaChart,
    BarChart,
    LineChart,
)
from dbt_charts.core.compile.models.chart.resolved.area import ResolvedAreaChart
from dbt_charts.core.compile.models.chart.resolved.bar import ResolvedBarChart
from dbt_charts.core.compile.models.chart.resolved.line import ResolvedLineChart
from dbt_charts.core.compile.resolve import resolve
from dbt_charts.core.compile.resolve.style.board import resolve_chart_style_context

NOW = datetime(2024, 6, 14, 9, 30, tzinfo=UTC)
MONTHS = [date(2024, m, 1) for m in range(1, 7)]
ROWS: list[dict[str, Any]] = [
    {"month": m, "region": region, "revenue": 10.0 + i + j, "churn": 0.1 - 0.01 * i}
    for i, m in enumerate(MONTHS)
    for j, region in enumerate(("East", "West"))
]
TREND = {"type": "trend", "fit": "linear()"}
FIX = "draw that column as a line or scatter"


def _resolve(
    model: type[AreaChart | BarChart | LineChart],
    kind: str,
    annotations: list[dict[str, Any]],
    **chart: Any,
) -> ResolvedAreaChart | ResolvedBarChart | ResolvedLineChart:
    normalized = model.model_validate(
        {
            "id": "c",
            "type": kind,
            "x": "month",
            "y": "revenue",
            "query_name": "q",
            "annotations": annotations,
            **chart,
        }
    )
    ctx = resolve_chart_style_context(get_theme_style(get_default_theme_name()))
    resolved = resolve(normalized, ROWS, ctx, rendered_at=NOW)
    assert isinstance(
        resolved, ResolvedAreaChart | ResolvedBarChart | ResolvedLineChart
    )
    return resolved


def _area(annotations: list[dict[str, Any]], **chart: Any) -> ResolvedAreaChart:
    resolved = _resolve(AreaChart, "area", annotations, **chart)
    assert isinstance(resolved, ResolvedAreaChart)
    return resolved


def _bar(annotations: list[dict[str, Any]], **chart: Any) -> ResolvedBarChart:
    resolved = _resolve(BarChart, "bar", annotations, **chart)
    assert isinstance(resolved, ResolvedBarChart)
    return resolved


SCATTER = {"type": "scatter", "y": "churn"}
LINE = {"type": "line", "y": "churn"}
AREA = {"type": "area", "y": "churn"}


@pytest.mark.parametrize("annotation", [TREND, {**TREND, "interval": "confidence(95)"}])
def test_a_trend_on_an_area_errors(annotation: dict[str, Any]) -> None:
    with pytest.raises(CompilationError, match=FIX):
        _area([annotation])


def test_a_series_total_trend_on_a_stacked_area_errors() -> None:
    with pytest.raises(CompilationError, match=FIX):
        _area([{**TREND, "series": "total"}], color="region", stack="zero")


def test_a_total_trend_on_a_stacked_bar_warns() -> None:
    chart = _bar([{**TREND, "series": "total"}], color="region", stack="zero")
    assert chart.annotation_bar_trends == (0,)


def test_a_per_segment_trend_on_a_stacked_bar_still_errors() -> None:
    with pytest.raises(CompilationError, match="total"):
        _bar([{**TREND, "series": "each"}], color="region", stack="zero")


def test_a_trend_on_an_area_layer_of_a_line_chart_errors() -> None:
    with pytest.raises(CompilationError, match=FIX):
        _resolve(
            LineChart,
            "line",
            [{**TREND, "y_column": "churn"}],
            layers=[AREA],
        )


def test_a_trend_on_an_unstacked_bar_warns_once_per_annotation() -> None:
    chart = _bar([{**TREND, "series": "each"}], color="region", stack="none")
    assert chart.annotation_bar_trends == (0,)
    assert len(chart.annotations) == 2


def test_a_trend_on_an_unstacked_bar_layer_warns() -> None:
    chart = _resolve(
        LineChart,
        "line",
        [{**TREND, "y_column": "churn"}],
        layers=[{"type": "bar", "y": "churn"}],
    )
    assert chart.annotation_bar_trends == (0,)


def test_a_trend_on_a_scatter_layer_of_an_area_chart_is_clean() -> None:
    chart = _area([{**TREND, "y_column": "churn"}], layers=[SCATTER])
    assert chart.annotation_bar_trends == ()
    assert chart.annotations


def test_a_trend_on_a_line_layer_of_a_bar_chart_is_clean() -> None:
    chart = _bar([{**TREND, "y_column": "churn"}], layers=[LINE])
    assert chart.annotation_bar_trends == ()
    assert chart.annotations


def test_a_trend_on_the_area_base_of_a_layered_chart_errors() -> None:
    with pytest.raises(CompilationError, match=FIX):
        _area([{**TREND, "y_column": "revenue"}], layers=[SCATTER])


def test_rules_bands_and_notes_on_area_and_bar_are_untouched() -> None:
    others = [
        {"type": "rule", "y": 12},
        {"type": "band", "y": {"from": 11, "to": 13}},
        {"type": "note", "x": "2024-03-01", "y": 12, "text": "hi"},
    ]
    assert not _area(others).annotation_bar_trends
    assert not _bar(others, color="region", stack="none").annotation_bar_trends
