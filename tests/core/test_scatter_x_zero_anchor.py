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


def _far_domain(xs: list[float]) -> tuple[float, float] | None:
    return _domain({}, [{"x": x, "y": float(i)} for i, x in enumerate(xs)])


def _render(
    chart_type: str, xs: list[float], axis_x: dict[str, Any] | None = None
) -> str:

    from pydantic import TypeAdapter

    from dbt_charts.core.compile.models.chart.normalized import Chart

    from ._engine_render import render_chart_svg

    chart = TypeAdapter(Chart).validate_python(
        {
            "id": "t",
            "type": chart_type,
            "x": "x",
            "y": "y",
            **({} if axis_x is None else {"style": {"axis_x": axis_x}}),
        }
    )
    rows = [{"x": x, "y": 1000.0 * (i + 1)} for i, x in enumerate(xs)]
    return render_chart_svg(chart, rows)


def _tick_labels(chart_type: str, xs: list[float]) -> list[str]:
    import re

    return re.findall(r">([^<>]{1,8})</text>", _render(chart_type, xs))


def test_year_x_scatter_fits_its_data_never_zero() -> None:
    labels = _tick_labels("scatter", [2019, 2020, 2021, 2023])
    assert labels[:5] == ["2019", "2020", "2021", "2022", "2023"]


def test_far_from_zero_x_scatter_fits_its_data_like_a_line() -> None:
    """The line rule: near/far > 0.25 keeps the axis data-fitted."""
    xs = [100.0, 110.0, 130.0]
    assert _far_domain(xs) == (100.0, 130.0)
    assert "0" not in _tick_labels("scatter", xs)[:7]
    assert "0" not in _tick_labels("line", xs)[:7]


def test_close_to_zero_x_scatter_still_extends_to_zero() -> None:
    assert "0" in _tick_labels("scatter", [10.0, 50.0, 130.0])[:7]


def _x_domain(
    xs: list[float], axis_x: dict[str, Any] | None = None
) -> tuple[float, float]:
    """The drawn x domain, read from the x axis's own aria description."""
    import re

    svg = _render("scatter", xs, axis_x)
    m = re.search(r'aria-label="X-axis[^"]*values from ([^ ]+) to ([^"]+)"', svg)
    assert m is not None, svg
    lo, hi = (float(v.replace("\u2212", "-")) for v in m.groups())
    return lo, hi


def test_data_fit_x_never_puts_a_point_on_a_nonzero_edge() -> None:
    """Domain nices out to tick boundaries (15 for a min of 18)."""
    lo, hi = _x_domain([18.0, 25.0, 39.0])
    assert lo == 15.0
    assert hi > 39.0 or hi == 40.0


def test_data_min_on_a_tick_boundary_extends_one_step() -> None:
    assert _x_domain([20.0, 30.0, 40.0]) == (15.0, 45.0)


def test_zero_edge_is_not_padded() -> None:
    """Data touching zero joins it (close to zero), and that edge stays 0."""
    assert _x_domain([0.0, 10.0, 50.0])[0] == 0.0


def test_edge_extension_never_crosses_zero() -> None:
    assert _x_domain([-30.0, -20.0, -10.0])[1] <= 0.0
    assert (
        _x_domain([2.0, 3.0, 12.0], {"scale": {"continuous": {"zero": False}}})[0]
        == 0.0
    )
    # An authored tick step can exceed the step the domain was niced to.
    assert _x_domain([3.0, 7.0, 11.0], {"ticks": {"step": 5}})[0] == 0.0


def test_percent_x_with_all_negative_data_tops_out_at_zero() -> None:
    """``zero: true`` joins 0 to both ends, so the top edge is 0, not the data max."""
    rows = [{"x": -0.4, "y": 1.0}, {"x": -0.1, "y": 2.0}]
    assert _domain({"labels": {"format": ".0%"}}, rows) == (-0.4, 0.0)
