"""A percent-formatted quantitative x axis anchors at 0, the way a percent y does.

Resolve bakes ``scale.continuous.zero`` for it (``_bake_zero_flag``, the y
law), so ``effective_measure_domain`` reads a real floor instead of the data
extent. A non-percent x and an author's own ``zero``/``domain`` are untouched.
"""

from __future__ import annotations

from typing import Any

from dbt_charts.core.compile.config import get_theme_style, reset_config
from dbt_charts.core.compile.models.chart.normalized import ScatterChart
from dbt_charts.core.compile.resolve import resolve
from dbt_charts.core.compile.resolve.style.board import resolve_chart_style_context
from dbt_charts.core.render.chart.emitters._cartesian import effective_measure_domain

_ROWS: list[dict[str, Any]] = [
    {"x": 0.35, "y": 1.0},
    {"x": 0.9, "y": 2.0},
]


def _domain(
    axis_x: dict[str, Any], rows: list[dict[str, Any]] = _ROWS
) -> tuple[float, float] | None:
    reset_config()
    ctx = resolve_chart_style_context(get_theme_style())
    chart = ScatterChart(id="t", type="scatter", x="x", y="y", style={"axis_x": axis_x})
    resolved = resolve(chart, rows, chart_style_context=ctx)
    xs = [r["x"] for r in rows]
    return effective_measure_domain(resolved.style.axis_x, (min(xs), max(xs)))


def _zero_flag(axis_x: dict[str, Any]) -> bool | str | None:
    reset_config()
    ctx = resolve_chart_style_context(get_theme_style())
    chart = ScatterChart(id="t", type="scatter", x="x", y="y", style={"axis_x": axis_x})
    cont = resolve(chart, _ROWS, chart_style_context=ctx).style.axis_x.scale.continuous
    return cont.zero if cont is not None else None


def test_percent_x_with_all_positive_data_starts_at_zero() -> None:
    assert _domain({"labels": {"format": ".0%"}}) == (0.0, 0.9)


def test_non_percent_numeric_x_is_unchanged() -> None:
    assert _domain({"labels": {"format": ",.2f"}}) == (0.35, 0.9)


def test_authored_domain_is_not_anchored_on_a_percent_x() -> None:
    """Any spelling, string ends included: the bake abstains, the pair stands."""
    for pair in ([0.2, 0.8], ["0.2", "0.8"]):
        scale = {"continuous": {"domain": pair}}
        assert _zero_flag({"labels": {"format": ".0%"}, "scale": scale}) is None


def test_authored_zero_false_is_respected_on_a_percent_x() -> None:
    scale = {"continuous": {"zero": False}}
    assert _domain({"labels": {"format": ".0%"}, "scale": scale}) == (0.35, 0.9)


_RULE_COLOR = "#abcdef"


def _svg(chart_type: str, rows: list[dict[str, Any]]) -> str:
    from pydantic import TypeAdapter

    from dbt_charts.core.compile.models.chart.normalized import Chart

    from ._engine_render import render_chart_svg

    chart = TypeAdapter(Chart).validate_python(
        {
            "id": "t",
            "type": chart_type,
            "x": "x",
            "y": "y",
            "style": {
                "axis_x": {
                    "labels": {"format": ".0%"},
                    "grid": {
                        "visible": True,
                        "threshold": {"color": _RULE_COLOR, "width": 2.0},
                    },
                }
            },
        }
    )
    return render_chart_svg(chart, rows)


def test_percent_x_scatter_draws_the_zero_rule() -> None:
    """The effect, end to end: data starts at 35% yet the 0% rule is drawn."""
    assert _RULE_COLOR in _svg("scatter", _ROWS)


def test_percent_x_line_and_area_are_not_anchored() -> None:
    """Line/area read the zero flag to stretch their x domain; only scatter bakes it."""
    rows = [{"x": 0.90, "y": 1.0}, {"x": 0.92, "y": 2.0}, {"x": 0.95, "y": 3.0}]
    for chart_type in ("line", "area"):
        svg = _svg(chart_type, rows)
        assert ">90%<" in svg, chart_type
        assert ">0%<" not in svg, chart_type


def test_string_domain_percent_x_scatter_draws_no_zero_rule() -> None:
    from pydantic import TypeAdapter

    from dbt_charts.core.compile.models.chart.normalized import Chart

    from ._engine_render import render_chart_svg

    chart = TypeAdapter(Chart).validate_python(
        {
            "id": "t",
            "type": "scatter",
            "x": "x",
            "y": "y",
            "style": {
                "axis_x": {
                    "labels": {"format": ".0%"},
                    "scale": {"continuous": {"domain": ["0.2", "0.8"]}},
                    "grid": {
                        "visible": True,
                        "threshold": {"color": _RULE_COLOR, "width": 2.0},
                    },
                }
            },
        }
    )
    assert _RULE_COLOR not in render_chart_svg(chart, _ROWS)
