"""Integration tests: case transform reaches each render surface.

HIGH-2 gap: the apply_case dispatcher is unit-tested, but prior to this file
no test confirmed the wiring from style.*.font.case through to emitted
SVG / VL spec. These tests pin the contract at three surfaces:

  1. Chart title (VL spec) — style.charts.title.font.case reaches spec.title.text
  2. Axis labelExpr (VL spec) — axis_x.labels.font.case: upper reaches
     encoding.x.axis.labelExpr, even when the axis is temporal (HIGH-1).
  3. KPI label (SVG) — style.charts.kpi.label.font.case reaches rendered text.

HIGH-1 regression: case: upper on a temporal yearmonth axis must wrap the
smart cadence labelExpr, not suppress it.
"""

from __future__ import annotations

from dbt_charts.core.compile.config import (
    get_theme_style,
)
from dbt_charts.core.compile.models.chart.normalized import (
    BarChart,
    HeatmapChart,
    KpiChart,
    LineChart,
)
from dbt_charts.core.compile.models.primitives import FontStyle
from dbt_charts.core.compile.models.query.normalized import SqlQuery
from dbt_charts.core.compile.models.style.authored import (
    AxisLabelStylePatch,
    AxisXStylePatch,
    AxisYStylePatch,
    DimensionLabelStylePatch,
    HeatmapChartStylePatch,
    LineChartStylePatch,
)
from dbt_charts.core.compile.resolve import resolve
from dbt_charts.core.compile.resolve.style.board import resolve_style_and_context
from dbt_charts.core.render.chart.vega_lite import generate_vega_lite_spec

_BOARD_RS, _BOARD_CTX = resolve_style_and_context(get_theme_style())

_DUMMY_QUERY = SqlQuery(sql="SELECT 1", source="test")


def _board_style_with_title_case(case: str):
    base = get_theme_style()
    seed = base.model_copy(
        update={
            "title": base.title.model_copy(
                update={"font": FontStyle.model_validate({"case": case})}
            )
        }
    )
    return resolve_style_and_context(seed)


# ---------------------------------------------------------------------------
# Integration surface 1 — chart title VL spec
# ---------------------------------------------------------------------------


def test_chart_title_case_upper_reaches_vl_spec() -> None:
    """style.title.font.case: upper transforms chart title string in emitted spec."""
    board_rs, board_ctx = _board_style_with_title_case("upper")
    chart = BarChart(
        id="test_title_upper",
        type="bar",
        x="category",
        y="revenue",
        title="net revenue retention",
    )
    data = [{"category": "A", "revenue": 100}, {"category": "B", "revenue": 200}]
    spec = generate_vega_lite_spec(
        chart, data, board_style=board_rs, chart_style_context=board_ctx
    )

    spec_title = spec.get("title", {})
    title_text = spec_title.get("text") if isinstance(spec_title, dict) else spec_title
    assert isinstance(title_text, str), f"Expected str title, got {title_text!r}"
    assert title_text == "NET REVENUE RETENTION", (
        f"Expected upper-case title, got {title_text!r}"
    )


def test_chart_title_case_title_lowers_stopwords_and_preserves_first_word() -> None:
    """style.title.font.case: title (Gruber/Chicago) applied to chart title in VL spec.

    Gruber rule: stopwords (by, of, the, and, ...) are lowercase except when
    first or last. The input "revenue by segment" → "Revenue by Segment".
    """
    board_rs, board_ctx = _board_style_with_title_case("title")
    chart = BarChart(
        id="test_title_tc",
        type="bar",
        x="category",
        y="revenue",
        title="revenue by segment",
    )
    data = [{"category": "A", "revenue": 100}]
    spec = generate_vega_lite_spec(
        chart, data, board_style=board_rs, chart_style_context=board_ctx
    )

    spec_title = spec.get("title", {})
    title_text = spec_title.get("text") if isinstance(spec_title, dict) else spec_title
    assert isinstance(title_text, str), f"Expected str title, got {title_text!r}"
    # "by" is a stopword — lowercased; first word "revenue" capitalized.
    assert title_text == "Revenue by Segment", (
        f"Expected Gruber title case, got {title_text!r}"
    )


# ---------------------------------------------------------------------------
# Integration surface 2 — axis labelExpr in VL spec (non-temporal)
# ---------------------------------------------------------------------------


def test_axis_upper_case_produces_label_expr_in_vl_spec() -> None:
    """axis_x.labels.font.case: upper injects upper(datum.label) into VL spec encoding.

    Line charts use axis_x directly for VL x (no orientation swap). Bar charts
    default to horizontal orientation, swapping axis_x to VL y — so line is the
    cleanest surface to verify axis_x labelExpr injection.
    """
    chart = LineChart(
        id="test_axis_upper",
        type="line",
        x="region",
        y="revenue",
        style=LineChartStylePatch.model_validate(
            {
                "axis_x": AxisXStylePatch.model_validate(
                    {
                        "labels": DimensionLabelStylePatch.model_validate(
                            {"font": FontStyle(case="upper")}
                        )
                    }
                )
            }
        ),
    )
    data = [
        {"region": "north america", "revenue": 100},
        {"region": "europe", "revenue": 200},
    ]
    spec = generate_vega_lite_spec(
        chart, data, board_style=_BOARD_RS, chart_style_context=_BOARD_CTX
    )

    x_axis = spec.get("encoding", {}).get("x", {}).get("axis", {})
    label_expr = x_axis.get("labelExpr")
    assert label_expr is not None, "Expected labelExpr in x axis encoding"
    assert label_expr == "upper(datum.label)", (
        f"Expected 'upper(datum.label)', got {label_expr!r}"
    )


# ---------------------------------------------------------------------------
# HIGH-1 regression — temporal yearmonth axis with case: upper
# ---------------------------------------------------------------------------


def test_temporal_yearmonth_axis_upper_wraps_smart_cadence_expr() -> None:
    """case: upper on a temporal yearmonth axis wraps the smart cadence labelExpr.

    The smart cadence labelExpr for yearmonth uses toDate(datum.value) + utcFormat.
    Before the fix, case injection in _build_encoding_axis set
    labelExpr = 'upper(datum.label)' before the smart-expr block, which then
    saw labelExpr already present and skipped — producing plain datum.label labels.

    After the fix, case injection runs *after* the smart temporal block and
    wraps: upper(<smart_cadence_expr>).
    """
    chart = LineChart(
        id="test_temporal_case",
        type="line",
        x="date",
        y="value",
        style=LineChartStylePatch.model_validate(
            {
                "axis_x": AxisXStylePatch.model_validate(
                    {
                        "type": "temporal",
                        "time_unit": "yearmonth",
                        "labels": DimensionLabelStylePatch.model_validate(
                            {"font": FontStyle(case="upper")}
                        ),
                    }
                )
            }
        ),
    )
    data = [
        {"date": "2025-01-01T00:00:00Z", "value": 10},
        {"date": "2025-02-01T00:00:00Z", "value": 20},
        {"date": "2025-03-01T00:00:00Z", "value": 30},
    ]
    spec = generate_vega_lite_spec(
        chart, data, board_style=_BOARD_RS, chart_style_context=_BOARD_CTX
    )

    x_axis = spec.get("encoding", {}).get("x", {}).get("axis", {})
    label_expr = x_axis.get("labelExpr")
    assert label_expr is not None, (
        "Expected labelExpr on temporal axis with case: upper"
    )
    # Must NOT be the plain datum.label form — that indicates the smart expr was skipped.
    assert label_expr != "upper(datum.label)", (
        "labelExpr is 'upper(datum.label)' — smart cadence expr was suppressed. "
        "Case injection must run *after* the temporal smart-expr block."
    )
    # Must start with 'upper(' — the case wrap is present.
    assert label_expr.startswith("upper("), (
        f"Expected upper(<smart_expr>), got {label_expr!r}"
    )
    # The inner expression references toDate(datum.value) — the cadence gate shape.
    assert "toDate(datum.value)" in label_expr, (
        f"Smart cadence expr must be preserved inside upper(), got {label_expr!r}"
    )


# ---------------------------------------------------------------------------
# Regression: axis_y / boxplot x / rect x — case injection was lost in 16b0d897f
# ---------------------------------------------------------------------------


def test_axis_y_upper_case_produces_label_expr_in_vl_spec() -> None:
    """axis_y.labels.font.case: upper injects upper(datum.label) into VL y-axis encoding.

    16b0d897f regression: case injection was moved to the tail of map_x_encoding only,
    leaving map_y_encoding with no injection. This test confirms the fix.

    An explicit axis_y.labels.format is authored so no numeral producer
    (ruler, or its non-compacting sibling tick_label) bakes its own
    labelExpr ahead of case injection — this test is about case injection
    reaching the Y axis at all, not about what a producer wraps.
    """
    chart = LineChart(
        id="test_axis_y_upper",
        type="line",
        x="month",
        y="revenue",
        style=LineChartStylePatch.model_validate(
            {
                "axis_y": AxisYStylePatch.model_validate(
                    {
                        "labels": AxisLabelStylePatch.model_validate(
                            {"font": FontStyle(case="upper"), "format": ",.0f"}
                        )
                    }
                )
            }
        ),
    )
    data = [
        {"month": "2025-01", "revenue": 100},
        {"month": "2025-02", "revenue": 200},
    ]
    spec = generate_vega_lite_spec(
        chart, data, board_style=_BOARD_RS, chart_style_context=_BOARD_CTX
    )

    y_axis = spec.get("encoding", {}).get("y", {}).get("axis", {})
    label_expr = y_axis.get("labelExpr")
    assert label_expr is not None, "Expected labelExpr in y axis encoding"
    assert label_expr == "upper(datum.label)", (
        f"Expected 'upper(datum.label)', got {label_expr!r}"
    )


def test_rect_x_axis_upper_case_produces_label_expr() -> None:
    """axis_x.labels.font.case: upper injects upper(datum.label) on rect/heatmap x-axis.

    16b0d897f regression: _map_rect calls _build_encoding_axis directly; same as boxplot.
    """
    chart = HeatmapChart(
        id="test_rect_x_upper",
        type="heatmap",
        x="category",
        y="metric",
        color="value",
        style=HeatmapChartStylePatch.model_validate(
            {
                "axis_x": AxisXStylePatch.model_validate(
                    {
                        "labels": DimensionLabelStylePatch.model_validate(
                            {"font": FontStyle(case="upper")}
                        )
                    }
                )
            }
        ),
    )
    data = [
        {"category": "north", "metric": "sales", "value": 100},
        {"category": "south", "metric": "sales", "value": 80},
    ]
    spec = generate_vega_lite_spec(
        chart, data, board_style=_BOARD_RS, chart_style_context=_BOARD_CTX
    )

    x_axis = spec.get("encoding", {}).get("x", {}).get("axis", {})
    label_expr = x_axis.get("labelExpr")
    assert label_expr is not None, "Expected labelExpr in rect/heatmap x axis encoding"
    assert label_expr == "upper(datum.label)", (
        f"Expected 'upper(datum.label)', got {label_expr!r}"
    )


# ---------------------------------------------------------------------------
# Integration surface 3 — KPI label SVG
# ---------------------------------------------------------------------------


def test_kpi_label_upper_case_reaches_rendered_svg() -> None:
    """style.charts.kpi.label.font.case: upper transforms KPI label text in SVG output."""
    from .._engine_render import render_kpi_svg

    base = get_theme_style()
    seed = base.model_copy(
        update={
            "charts": base.charts.model_copy(
                update={
                    "kpi": base.charts.kpi.model_copy(
                        update={
                            "label": base.charts.kpi.label.model_copy(
                                update={"font": FontStyle(case="upper")}
                            )
                        }
                    )
                }
            )
        }
    )
    board_rs, board_ctx = resolve_style_and_context(seed)

    chart = KpiChart(
        id="test_kpi_upper",
        type="kpi",
        value="revenue",
        label="net revenue retention",
    )
    data = [{"revenue": 1_500_000}]
    resolved = resolve(chart, data, chart_style_context=board_ctx)
    svg = render_kpi_svg(resolved, data, width=300, height=180, board_style=board_rs)
    assert "NET REVENUE RETENTION" in svg, (
        f"Expected upper-case KPI label in SVG. SVG snippet: {svg[:500]!r}"
    )
