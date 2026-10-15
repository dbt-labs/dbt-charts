"""A non-default grouped padding or disconnected step width authored on a
chart's own ``style:`` reaches the render."""

from __future__ import annotations

from typing import Any

from dbt_charts.core.compile.config import get_theme_style
from dbt_charts.core.compile.models.chart.normalized import BarChart, LineChart
from dbt_charts.core.compile.models.query.normalized import SqlQuery
from dbt_charts.core.compile.models.style.authored import (
    BarChartStylePatch,
    LineChartStylePatch,
)
from dbt_charts.core.compile.resolve import resolve
from dbt_charts.core.compile.resolve.chart._chart_rows import regroup
from dbt_charts.core.compile.resolve.style.board import (
    resolve_chart_style_context,
    resolve_style_and_context,
)
from dbt_charts.core.render.chart.emitters.bar import BarEmitter
from dbt_charts.core.render.chart.spec import RenderBox
from dbt_charts.core.render.chart.vega_lite import (
    generate_vega_lite_spec,
)

_BOX = RenderBox(width=600.0, height=300.0)
_QUERY = {"query": SqlQuery(sql="SELECT 1", source="t"), "query_name": "q"}

_STACK_DATA: list[dict[str, Any]] = [
    {"month": "Jan", "value": 100, "series": "A"},
    {"month": "Jan", "value": 50, "series": "B"},
    {"month": "Feb", "value": 200, "series": "A"},
    {"month": "Feb", "value": 80, "series": "B"},
]


def _bar(style: dict[str, Any], **kwargs: Any) -> BarChart:
    return BarChart(
        id="t",
        type="bar",
        style=BarChartStylePatch.model_validate(style),
        **_QUERY,
        **kwargs,
    )


def _resolved(chart: Any, data: list[dict[str, Any]], **kwargs: Any) -> Any:
    ctx = resolve_chart_style_context(get_theme_style())
    return resolve(chart, data, chart_style_context=ctx, **kwargs)


def test_authored_grouped_padding_reaches_the_scale() -> None:
    chart = _bar(
        {"stack": "none", "grouped": {"padding_inner": 0.35, "padding_outer": 0.15}},
        x="month",
        y="value",
        color="series",
    )
    rc = _resolved(chart, _STACK_DATA)
    spec = BarEmitter().emit(rc, _BOX, regroup((), _STACK_DATA))
    cat_ch = "y" if "yOffset" in spec.encoding else "x"
    scale = spec.encoding[cat_ch]["scale"]
    assert (scale["paddingInner"], scale["paddingOuter"]) == (0.35, 0.15)


def test_authored_disconnected_width_insets_the_step_plateaus() -> None:
    def band_range(width: float) -> list[Any]:
        chart = LineChart(
            id="t",
            type="line",
            x="month",
            y="v",
            style=LineChartStylePatch.model_validate(
                {
                    "marks": {
                        "line": {
                            "curve": "step",
                            "connect": False,
                            "disconnected_width": width,
                        }
                    }
                }
            ),
            **_QUERY,
        )
        data = [{"month": m, "v": i} for i, m in enumerate(["Jan", "Feb", "Mar"])]
        board_rs, ctx = resolve_style_and_context(get_theme_style())
        spec = generate_vega_lite_spec(
            chart, data, board_style=board_rs, chart_style_context=ctx
        )
        return spec["encoding"]["xOffset"]["scale"]["range"]

    assert band_range(0.5)[0]["expr"] == "bandwidth('x')*0.25"
    assert band_range(0.5)[1]["expr"] == "bandwidth('x')*0.75"
