"""``_categorical_axis_gutter_px`` (compile/resolve/chart/bar.py) must
measure the number format Vega actually paints on a numeric horizontal-bar
category, not the raw row value — the compile-layer twin of
``emitters/bar.py``'s own gutter measurement (same VL "format" property,
applied unconditionally by ``vl_field_maps.axis_to_vl``).
"""

from __future__ import annotations

from dbt_charts.core.compile.config import get_theme_style
from dbt_charts.core.compile.models.chart.normalized import BarChart
from dbt_charts.core.compile.models.query.normalized import SqlQuery
from dbt_charts.core.compile.resolve.chart._plan import plan_cartesian
from dbt_charts.core.compile.resolve.chart.bar import _categorical_axis_gutter_px
from dbt_charts.core.compile.resolve.style.board import resolve_chart_style_context

_BOARD_CTX = resolve_chart_style_context(get_theme_style())


def _plan(data: list[dict]):
    chart = BarChart(
        id="t",
        query=SqlQuery(sql="SELECT 1", source="t"),
        query_name="q",
        type="bar",
        x="bucket",
        y="value",
    )
    return plan_cartesian(
        chart,
        data,
        _BOARD_CTX,
        "bar",
        "nominal",
        "quantitative",
        None,
        chart.y,
        has_quantitative_axis=True,
    )


def test_authored_number_format_widens_the_gutter() -> None:
    data = [{"bucket": str((i + 1) * 1_000_000), "value": 10} for i in range(6)]
    plan = _plan(data)
    bare_ax = plan.axes.x.style
    formatted_labels = bare_ax.labels.model_copy(update={"format": "$,.0f"})
    formatted_ax = bare_ax.model_copy(update={"labels": formatted_labels})

    bare_gutter = _categorical_axis_gutter_px(bare_ax, data, "bucket")
    formatted_gutter = _categorical_axis_gutter_px(formatted_ax, data, "bucket")

    assert formatted_gutter > bare_gutter


def test_authored_label_expr_falls_back_to_raw_values() -> None:
    """An authored labelExpr makes the painted text unknowable from Python —
    the gutter falls back to measuring the raw row value rather than
    guessing, same as the render-layer sites' own labelExpr fallback."""
    data = [{"bucket": str((i + 1) * 1_000_000), "value": 10} for i in range(6)]
    plan = _plan(data)
    bare_ax = plan.axes.x.style
    bare_ax = bare_ax.model_copy(
        update={"labels": bare_ax.labels.model_copy(update={"format": None})}
    )
    bare_gutter = _categorical_axis_gutter_px(bare_ax, data, "bucket")

    expr_labels = bare_ax.labels.model_copy(update={"format": "$,.0f", "expr": "'X'"})
    expr_ax = bare_ax.model_copy(update={"labels": expr_labels})
    expr_gutter = _categorical_axis_gutter_px(expr_ax, data, "bucket")

    assert expr_gutter == bare_gutter
