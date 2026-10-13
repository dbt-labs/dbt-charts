"""A theme- or project-level ``style.charts.line.axis_y.scale.continuous.zero``
pin reaches line charts and no other family."""

from __future__ import annotations

from pydantic import TypeAdapter

from dbt_charts.core.compile.config import get_theme_style, reset_config
from dbt_charts.core.compile.models.chart.normalized import Chart
from dbt_charts.core.compile.models.style.authored import StylePatch
from dbt_charts.core.compile.resolve.style.board import resolve_style_and_context
from dbt_charts.core.render.chart.vega_lite import generate_vega_lite_spec

# min/max = 120/204 > 0.25: the smart-zero heuristic alone fits the span, so
# an anchored result can only come from the pin.
_FAR_RATIO_DATA = [
    {"x": "a", "y": 120},
    {"x": "b", "y": 180},
    {"x": "c", "y": 204},
]

_LINE_ZERO_PIN = {
    "charts": {"line": {"axis_y": {"scale": {"continuous": {"zero": True}}}}}
}


def _y_scale(chart_type: str, chart_style: dict | None = None) -> dict:
    reset_config()
    patch = TypeAdapter(StylePatch).validate_python(_LINE_ZERO_PIN)
    board_style, board_ctx = resolve_style_and_context(get_theme_style(), patch)
    payload: dict = {"id": "t", "type": chart_type, "x": "x", "y": "y"}
    if chart_style is not None:
        payload["style"] = chart_style
    chart = TypeAdapter(Chart).validate_python(payload)
    spec = generate_vega_lite_spec(
        chart,
        _FAR_RATIO_DATA,
        width=400,
        board_style=board_style,
        chart_style_context=board_ctx,
    )
    return spec["encoding"]["y"]["scale"]


def test_line_family_pin_anchors_line_at_zero() -> None:
    assert _y_scale("line")["zero"] is True


def test_line_family_pin_leaves_scatter_to_the_heuristic() -> None:
    assert _y_scale("scatter")["zero"] is False


def test_chart_level_zero_false_overrides_line_family_pin() -> None:
    # Meaningful only because the test above proves the family pin is active.
    chart_style = {"axis_y": {"scale": {"continuous": {"zero": False}}}}
    assert _y_scale("line", chart_style)["zero"] is False
