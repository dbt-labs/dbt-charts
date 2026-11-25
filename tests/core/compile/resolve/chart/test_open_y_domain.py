"""A one-sided ``axis_y`` domain (``[null, 1]``, ``[0, null]``) resolves to two
numbers: the pinned end exact, the open end fitted like an unauthored axis."""

from __future__ import annotations

from collections.abc import Iterator
from typing import Any

import pytest

from dbt_charts.core.compile.config import get_theme_style, reset_config
from dbt_charts.core.compile.errors import CompilationError
from dbt_charts.core.compile.models.chart.normalized import (
    AreaChart,
    BarChart,
    HeatmapChart,
    LineChart,
    ScatterChart,
)
from dbt_charts.core.compile.resolve import resolve
from dbt_charts.core.compile.resolve.chart._chart_rows import ChartRows
from dbt_charts.core.compile.resolve.style.board import resolve_style_and_context

_Chart = LineChart | BarChart | AreaChart | ScatterChart


@pytest.fixture(autouse=True)
def reset() -> Iterator[None]:
    reset_config()
    yield
    reset_config()


def _axis_y(chart: dict[str, Any], rows: ChartRows) -> Any:
    _, ctx = resolve_style_and_context(get_theme_style("clarity"))
    model = {
        "line": LineChart,
        "bar": BarChart,
        "area": AreaChart,
        "scatter": ScatterChart,
    }[chart["type"]]
    return resolve(
        model.model_validate({"id": "t", "x": "x", "y": "val", **chart}),
        rows,
        chart_style_context=ctx,
    ).style.axis_y


def _domain_style(domain: list[float | None], **extra: Any) -> dict[str, Any]:
    return {"axis_y": {"scale": {"continuous": {"domain": domain, **extra}}}}


RATES = [{"x": i, "val": 0.97 + 0.005 * i} for i in range(6)]


def test_line_open_low_end_pins_top_and_keeps_bottom_headroom() -> None:
    axis = _axis_y(
        {"type": "line", "style": _domain_style([None, 1], zero=False)}, RATES
    )
    assert axis.scale is not None and axis.scale.headroom
    values = [r["val"] for r in RATES]
    span = max(values) - min(values)
    lo, hi = axis.scale.continuous.domain
    assert hi == 1
    assert lo == pytest.approx(min(values) - axis.scale.headroom * span)
    assert max(axis.tick_values) == 1.0
    assert min(axis.tick_values) >= lo


def test_line_open_high_end_pins_bottom_and_keeps_top_headroom() -> None:
    axis = _axis_y(
        {"type": "line", "style": _domain_style([0.9, None], zero=False)}, RATES
    )
    assert axis.scale is not None and axis.scale.headroom
    values = [r["val"] for r in RATES]
    span = max(values) - min(values)
    lo, hi = axis.scale.continuous.domain
    assert lo == 0.9
    assert hi == pytest.approx(max(values) + axis.scale.headroom * span)


def test_zero_anchored_open_high_end_takes_multiplicative_headroom() -> None:
    rows = [{"x": i, "val": 10 + 10 * i} for i in range(5)]
    axis = _axis_y({"type": "bar", "style": _domain_style([0, None])}, rows)
    assert axis.scale is not None and axis.scale.headroom
    assert axis.scale.continuous.domain == (
        0,
        pytest.approx(50 * (1 + axis.scale.headroom)),
    )


def test_stacked_bar_open_high_end_fits_the_stacked_total() -> None:
    rows = [
        {"x": x, "c": c, "val": v}
        for x, vals in (("a", (10, 20)), ("b", (30, 40)))
        for c, v in zip("pq", vals, strict=True)
    ]
    axis = _axis_y(
        {
            "type": "bar",
            "color": "c",
            "stack": "zero",
            "style": _domain_style([0, None]),
        },
        rows,
    )
    assert axis.scale is not None and axis.scale.headroom
    assert axis.scale.continuous.domain == (
        0,
        pytest.approx(70 * (1 + axis.scale.headroom)),
    )


def test_normalize_stack_closes_open_end_to_unit_interval() -> None:
    rows = [{"x": x, "c": c, "val": 1} for x in "ab" for c in "pq"]
    axis = _axis_y(
        {
            "type": "bar",
            "color": "c",
            "stack": "normalize",
            "style": _domain_style([None, 1]),
        },
        rows,
    )
    assert axis.scale is not None
    assert axis.scale.continuous.domain == (0.0, 1)


def test_area_open_end_closes() -> None:
    axis = _axis_y(
        {"type": "area", "style": _domain_style([None, 100])},
        [{"x": i, "val": 10 + i} for i in range(5)],
    )
    assert axis.scale is not None
    lo, hi = axis.scale.continuous.domain
    assert hi == 100 and lo <= 10


def test_scatter_open_end_closes() -> None:
    axis = _axis_y(
        {"type": "scatter", "style": _domain_style([None, 100])},
        [{"x": i, "val": 10 + i} for i in range(5)],
    )
    assert axis.scale is not None
    assert axis.scale.continuous.domain[1] == 100


def test_fitted_end_past_pinned_end_is_an_error() -> None:
    with pytest.raises(CompilationError) as err:
        _axis_y(
            {"type": "line", "style": _domain_style([None, 1], zero=False)},
            [{"x": i, "val": 5 + i} for i in range(5)],
        )
    assert err.value.code.code == "ERR-DOMAIN-OPEN-END-INVERTED"


def test_open_end_with_independent_multiples_is_an_error() -> None:
    with pytest.raises(CompilationError) as err:
        _axis_y(
            {
                "type": "line",
                "color": None,
                "multiples": {"columns": "g", "scale": "independent"},
                "style": _domain_style([None, 100]),
            },
            [{"x": i, "g": "a", "val": 10 + i} for i in range(5)],
        )
    assert err.value.code.code == "ERR-DOMAIN-OPEN-END-INDEPENDENT-SCALE"


def test_open_end_on_x_axis_is_an_error() -> None:
    with pytest.raises(CompilationError) as err:
        _axis_y(
            {
                "type": "scatter",
                "style": {"axis_x": {"scale": {"continuous": {"domain": [None, 9]}}}},
            },
            [{"x": i, "val": i} for i in range(5)],
        )
    assert err.value.code.code == "ERR-DOMAIN-OPEN-END-UNSUPPORTED"


def test_open_domain_on_empty_data_fits_like_no_domain() -> None:
    axis = _axis_y({"type": "line", "style": _domain_style([None, 1])}, [])
    assert (
        axis.scale is None
        or axis.scale.continuous is None
        or (axis.scale.continuous.domain is None)
    )


@pytest.mark.parametrize(
    ("model", "chart"),
    [
        (HeatmapChart, {"x": "x", "y": "x", "color": "val"}),
        (BarChart, {"type": "histogram", "x": "val"}),
    ],
    ids=["heatmap", "histogram"],
)
def test_open_end_on_unfitted_y_axis_is_an_error(
    model: type[Any], chart: dict[str, Any]
) -> None:
    _, ctx = resolve_style_and_context(get_theme_style("clarity"))
    authored = model.model_validate(
        {"type": "heatmap", "id": "t", **chart, "style": _domain_style([0, None])}
    )
    with pytest.raises(CompilationError) as err:
        resolve(authored, RATES, chart_style_context=ctx)
    assert err.value.code.code == "ERR-DOMAIN-OPEN-END-UNSUPPORTED"


def test_horizontal_bar_open_measure_end_closes() -> None:
    axis = _axis_y(
        {
            "type": "bar",
            "style": {"orientation": "horizontal", **_domain_style([0, None])},
        },
        RATES,
    )
    assert axis.scale is not None and axis.scale.continuous is not None
    low, high = axis.scale.continuous.domain
    assert low == 0 and high > max(r["val"] for r in RATES)


def test_normalize_stack_pinned_low_end_past_one_is_an_error() -> None:
    with pytest.raises(CompilationError) as err:
        _axis_y(
            {
                "type": "bar",
                "color": "s",
                "style": {"stack": "normalize", **_domain_style([5, None])},
            },
            [{"x": i % 3, "s": f"s{i % 2}", "val": i + 1} for i in range(6)],
        )
    assert err.value.code.code == "ERR-DOMAIN-OPEN-END-INVERTED"
