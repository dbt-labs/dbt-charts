"""Tests for resolve_title_font and title_font on resolved chart models.

Verifies:
- resolve_title_font returns a fully-populated ResolvedFontStyle
- All resolved chart families carry title_font at construction time
- title_font values match chart_title_spec for the same width
"""

from __future__ import annotations

import pytest

from dbt_charts.core.compile.config import get_theme_style
from dbt_charts.core.compile.models.primitives import ResolvedFontStyle
from dbt_charts.core.compile.resolve.style.board import resolve_chart_style_context
from dbt_charts.core.compile.resolve.style.typography import (
    chart_title_spec,
    resolve_title_font,
)


def _charts():
    return resolve_chart_style_context(get_theme_style())


# ---------------------------------------------------------------------------
# resolve_title_font
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("width", [200.0, 400.0, 700.0, 1200.0])
def test_resolve_title_font_returns_resolved_font_style(width):
    charts = _charts()
    result = resolve_title_font(charts, width)
    assert isinstance(result, ResolvedFontStyle)


@pytest.mark.parametrize("width", [200.0, 400.0, 700.0, 1200.0])
def test_resolve_title_font_all_fields_populated(width):
    charts = _charts()
    result = resolve_title_font(charts, width)
    assert result.size > 0
    assert result.family
    assert result.color
    assert result.weight


@pytest.mark.parametrize("width", [200.0, 400.0, 700.0, 1200.0])
def test_resolve_title_font_matches_chart_title_spec(width):
    """size/weight/family must match chart_title_spec for the same width."""
    charts = _charts()
    expected_size, expected_weight, expected_family = chart_title_spec(
        width, chart_style_context=charts
    )
    result = resolve_title_font(charts, width)
    assert result.size == expected_size
    assert result.weight == expected_weight
    assert result.family == expected_family


# ---------------------------------------------------------------------------
# title_font on resolved chart models
# ---------------------------------------------------------------------------


def _board_style():
    from dbt_charts.core.compile.config import get_theme_style
    from dbt_charts.core.compile.resolve.style.board import resolve_chart_style_context

    return resolve_chart_style_context(get_theme_style())


def _bar_chart(chart_id: str = "test_bar"):
    from dbt_charts.core.compile.models.chart.normalized import BarChart

    return BarChart(id=chart_id, type="bar", x="month", y="revenue")


def test_resolved_bar_chart_has_title_font():
    from dbt_charts.core.compile.resolve import resolve

    resolved = resolve(
        _bar_chart(), data=[], chart_style_context=_board_style(), width=600.0
    )
    assert isinstance(resolved.style.title_font, ResolvedFontStyle)
    assert resolved.style.title_font.size > 0


def test_resolved_chart_title_font_varies_by_width():
    from dbt_charts.core.compile.resolve import resolve

    board_style = _board_style()
    narrow = resolve(
        _bar_chart(), data=[], chart_style_context=board_style, width=200.0
    )
    wide = resolve(_bar_chart(), data=[], chart_style_context=board_style, width=1200.0)
    # Tiny vs wide — sizes should differ
    assert narrow.style.title_font.size != wide.style.title_font.size


# ---------------------------------------------------------------------------
# Regression: chart-local style.title.font.{color,style} must reach the
# resolved title font for every family resolver, not just family/size/weight.
#
# resolve_title_font only threads family/size/weight through its explicit
# authored_title_font argument; color/style/decoration/case/line_height are
# read straight off chart_style_context.title.font. A resolver that calls
# _title_font() (or resolve_title_font directly) with the board-level context
# instead of the chart-local one silently drops those five fields.
# ---------------------------------------------------------------------------

_TITLE_FONT_OVERRIDE = {"title": {"font": {"color": "#123456", "style": "italic"}}}


def _title_font_chart(chart_type: str):
    from dbt_charts.core.compile.models.chart.normalized import (
        AreaChart,
        BarChart,
        GeoshapeChart,
        HeatmapChart,
        KpiChart,
        LineChart,
        PieChart,
        PointMapChart,
        ScatterChart,
        SparkBarChart,
        TableChart,
    )
    from dbt_charts.core.compile.models.query.normalized import SqlQuery
    from dbt_charts.core.compile.models.style.authored import (
        AreaChartStylePatch,
        BarChartStylePatch,
        GeoshapeChartStylePatch,
        HeatmapChartStylePatch,
        KpiChartStylePatch,
        LineChartStylePatch,
        PieChartStylePatch,
        PointMapChartStylePatch,
        ScatterChartStylePatch,
        SparkBarChartStylePatch,
        TableChartStylePatch,
    )

    sql = SqlQuery(sql="SELECT 1", source="t")
    common = {"id": "c", "query": sql, "query_name": "q"}
    if chart_type == "bar":
        return BarChart(
            **common,
            type="bar",
            x="month",
            y="revenue",
            style=BarChartStylePatch.model_validate(_TITLE_FONT_OVERRIDE),
        )
    if chart_type == "histogram":
        return BarChart(
            **common,
            type="histogram",
            x="value",
            style=BarChartStylePatch.model_validate(_TITLE_FONT_OVERRIDE),
        )
    if chart_type == "line":
        return LineChart(
            **common,
            type="line",
            x="month",
            y="revenue",
            style=LineChartStylePatch.model_validate(_TITLE_FONT_OVERRIDE),
        )
    if chart_type == "area":
        return AreaChart(
            **common,
            type="area",
            x="month",
            y="revenue",
            style=AreaChartStylePatch.model_validate(_TITLE_FONT_OVERRIDE),
        )
    if chart_type == "scatter":
        return ScatterChart(
            **common,
            type="scatter",
            x="month_index",
            y="revenue",
            style=ScatterChartStylePatch.model_validate(_TITLE_FONT_OVERRIDE),
        )
    if chart_type == "heatmap":
        return HeatmapChart(
            **common,
            type="heatmap",
            x="month",
            y="category",
            style=HeatmapChartStylePatch.model_validate(_TITLE_FONT_OVERRIDE),
        )
    if chart_type == "pie":
        return PieChart(
            **common,
            type="pie",
            theta="revenue",
            style=PieChartStylePatch.model_validate(_TITLE_FONT_OVERRIDE),
        )
    if chart_type == "kpi":
        return KpiChart(
            **common,
            type="kpi",
            value="revenue",
            style=KpiChartStylePatch.model_validate(_TITLE_FONT_OVERRIDE),
        )
    if chart_type == "spark_bar":
        return SparkBarChart(
            **common,
            type="spark_bar",
            x="month",
            y="revenue",
            style=SparkBarChartStylePatch.model_validate(_TITLE_FONT_OVERRIDE),
        )
    if chart_type == "table":
        return TableChart(
            **common,
            type="table",
            style=TableChartStylePatch.model_validate(_TITLE_FONT_OVERRIDE),
        )
    if chart_type == "geoshape":
        return GeoshapeChart(
            **common,
            type="geoshape",
            geo_source="fake-geo-source",
            style=GeoshapeChartStylePatch.model_validate(_TITLE_FONT_OVERRIDE),
        )
    if chart_type == "point_map":
        return PointMapChart(
            **common,
            type="point_map",
            style=PointMapChartStylePatch.model_validate(_TITLE_FONT_OVERRIDE),
        )
    raise AssertionError(f"no chart builder for {chart_type!r}")


_TITLE_FONT_DATA = [
    {
        "month": "Jan",
        "month_index": 1,
        "category": "Alpha",
        "revenue": 10.0,
        "value": 1.0,
    },
    {
        "month": "Feb",
        "month_index": 2,
        "category": "Beta",
        "revenue": 20.0,
        "value": 2.0,
    },
]


@pytest.mark.parametrize(
    "chart_type",
    [
        "bar",
        "histogram",
        "line",
        "area",
        "scatter",
        "heatmap",
        "pie",
        pytest.param(
            "spark_bar",
            marks=pytest.mark.skip(
                reason=(
                    "SparkBarChartStylePatch has no `title` field at all — a "
                    "spark_bar chart cannot author a chart-local title.font "
                    "override, so there is nothing for this invariant to check."
                )
            ),
        ),
        "table",
        "geoshape",
        "point_map",
    ],
)
def test_chart_local_title_font_override_reaches_resolved_chart(chart_type):
    from dbt_charts.core.compile.resolve import resolve

    resolved = resolve(
        _title_font_chart(chart_type),
        data=_TITLE_FONT_DATA,
        chart_style_context=_board_style(),
        width=600.0,
    )
    assert resolved.style.title_font.color == "#123456", (
        f"{chart_type}: chart-local style.title.font.color did not reach the "
        f"resolved title font (got {resolved.style.title_font.color!r})"
    )
    assert resolved.style.title_font.style == "italic", (
        f"{chart_type}: chart-local style.title.font.style did not reach the "
        f"resolved title font (got {resolved.style.title_font.style!r})"
    )


def test_kpi_has_no_chart_local_title_override() -> None:
    """A KPI paints a label, not a title: a chart-local style.title is rejected."""
    from pydantic import ValidationError

    with pytest.raises(ValidationError, match="title"):
        _title_font_chart("kpi")
