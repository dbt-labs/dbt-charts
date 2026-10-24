"""A layer's ink is decided once at resolve; annotations and renderers read it."""

from __future__ import annotations

from datetime import UTC, date, datetime
from typing import Any

import pytest

from dbt_charts.core.compile.config import get_default_theme_name, get_theme_style
from dbt_charts.core.compile.models.chart.normalized import BarChart, LineChart
from dbt_charts.core.compile.models.chart.resolved.line import ResolvedLineChart
from dbt_charts.core.compile.resolve import resolve
from dbt_charts.core.compile.resolve.chart._layer_ink import (
    LayerBase,
    LayerInk,
    layer_inks,
)
from dbt_charts.core.compile.resolve.style.board import resolve_chart_style_context
from dbt_charts.core.diagnostics.chart_data import ChartDataError

NOW = datetime(2024, 6, 14, 9, 30, tzinfo=UTC)
CTX = resolve_chart_style_context(get_theme_style(get_default_theme_name()))
PALETTE = CTX.palette

MONTHS: list[dict[str, Any]] = [
    {"month": date(2024, m, 1), "revenue": 10.0 + m, "churn_rate": 0.10 - 0.01 * m}
    for m in range(1, 7)
]
BY_REGION: list[dict[str, Any]] = [
    {
        "month": date(2024, m, 1),
        "region": region,
        "revenue": 10.0 + m,
        "churn_rate": (0.10 - 0.01 * m) * scale,
    }
    for m in range(1, 7)
    for region, scale in (("East", 1.0), ("West", 2.0))
]
CHURN = {"type": "line", "y": "churn_rate", "axis_y": {"position": "right"}}


def _line(
    rows: list[dict[str, Any]],
    annotations: list[dict[str, Any]] | None = None,
    **chart: Any,
) -> ResolvedLineChart:
    normalized = LineChart.model_validate(
        {
            "id": "rev",
            "type": "line",
            "x": "month",
            "y": "revenue",
            "query_name": "q",
            "annotations": annotations or [],
            **chart,
        }
    )
    resolved = resolve(normalized, rows, CTX, rendered_at=NOW)
    assert isinstance(resolved, ResolvedLineChart)
    return resolved


def _trend_color(chart: ResolvedLineChart, index: int = 0) -> str:
    line = chart.annotations[index].style.line
    assert line is not None
    return line.stroke.color


# --- the function ----------------------------------------------------------


def _plain(**layer: Any) -> Any:
    from pydantic import TypeAdapter

    from dbt_charts.core.compile.models.chart.authored._layer import CartesianLayer

    return TypeAdapter(CartesianLayer).validate_python({"type": "line", **layer})


def _inks(
    count: int,
    *,
    palette: list[str],
    base: dict[str, str],
    authored: list[str | None] | None = None,
) -> tuple[LayerInk, ...]:
    return layer_inks(
        [_plain(y=f"m{i}") for i in range(count)],
        authored or [None] * count,
        LayerBase(shared=True, series=base),
        chart_id="c",
        palette=palette,
        board_scales=(),
        single_series_fill=palette[0],
        rows_of=lambda _layer: [],
    )


def test_an_exhausted_palette_rotates_instead_of_repeating() -> None:
    palette = ["#aaaaaa", "#bbbbbb", "#cccccc"]
    inks = _inks(3, palette=palette, base=dict(zip("xyz", palette, strict=True)))
    assert [i.ink for i in inks] == palette


def test_a_taken_color_is_compared_without_case() -> None:
    palette = ["#aaaaaa", "#bbbbbb", "#cccccc"]
    [ink] = _inks(1, palette=palette, base={"x": "#AAAAAA", "y": "#bbbbbb"})
    assert ink.ink == "#cccccc"


def test_an_authored_color_is_kept_and_taken_from_the_scale() -> None:
    palette = ["#aaaaaa", "#bbbbbb", "#cccccc"]
    inks = _inks(
        2, palette=palette, base={"base": "#aaaaaa"}, authored=["#bbbbbb", None]
    )
    assert [i.ink for i in inks] == ["#bbbbbb", "#cccccc"]


def test_a_colored_layer_without_a_palette_raises() -> None:
    layer = _plain(y="m", color="region")
    with pytest.raises(ChartDataError, match="no color palette"):
        layer_inks(
            [layer],
            [None],
            LayerBase(shared=True, series={"base": "#aaaaaa"}),
            chart_id="c",
            palette=[],
            board_scales=(),
            single_series_fill="#aaaaaa",
            rows_of=lambda _layer: [{"region": "East"}],
        )


def test_a_gradient_layer_takes_no_slot_on_the_shared_scale() -> None:
    palette = ["#aaaaaa", "#bbbbbb", "#cccccc"]
    inks = layer_inks(
        [_plain(y="m0", color="score"), _plain(y="m1")],
        [None, None],
        LayerBase(shared=True, series={"base": "#aaaaaa"}),
        chart_id="c",
        palette=palette,
        board_scales=(),
        single_series_fill="#aaaaaa",
        rows_of=lambda _layer: [{"score": 1}, {"score": 2}],
    )
    assert [i.ink for i in inks] == ["#aaaaaa", "#bbbbbb"]


# --- the baked field -------------------------------------------------------


def test_an_unauthored_layer_takes_the_first_palette_color_the_base_lacks() -> None:
    chart = _line(MONTHS, layers=[CHURN])
    assert chart.layers[0].ink == PALETTE[1]
    assert chart.layers[0].series_inks == {}


def test_an_authored_stroke_color_is_the_layer_ink() -> None:
    layer = {
        **CHURN,
        "style": {"marks": {"line": {"stroke": {"color": "#c0ffee"}}}},
    }
    chart = _line(MONTHS, layers=[layer, {"type": "line", "y": "revenue"}])
    assert chart.layers[0].ink == "#c0ffee"
    assert chart.layers[1].ink == PALETTE[1]


def test_a_colored_layer_bakes_one_ink_per_value() -> None:
    chart = _line(BY_REGION, layers=[{**CHURN, "color": "region"}])
    layer = chart.layers[0]
    assert layer.ink is None
    assert layer.series_inks == {"East": PALETTE[1], "West": PALETTE[2]}


def test_a_layer_sharing_the_base_color_field_reuses_the_base_colors() -> None:
    chart = _line(BY_REGION, color="region", layers=[{**CHURN, "color": "region"}])
    assert chart.layers[0].series_inks == {"East": PALETTE[0], "West": PALETTE[1]}


def test_an_uncolored_layer_beside_a_colored_base_skips_the_base_series() -> None:
    chart = _line(BY_REGION, color="region", layers=[CHURN])
    assert chart.layers[0].ink == PALETTE[2]


# --- annotations read it ---------------------------------------------------


def test_a_trend_on_an_uncolored_layer_wears_the_layers_default_ink() -> None:
    chart = _line(
        MONTHS,
        [{"type": "trend", "fit": "linear()", "y_column": "churn_rate"}],
        layers=[CHURN],
    )
    assert _trend_color(chart) == chart.layers[0].ink == PALETTE[1]


def test_a_trend_on_an_uncolored_layer_wears_the_layers_authored_color() -> None:
    layer = {**CHURN, "style": {"marks": {"line": {"stroke": {"color": "#c0ffee"}}}}}
    chart = _line(
        MONTHS,
        [{"type": "trend", "fit": "linear()", "y_column": "churn_rate"}],
        layers=[layer],
    )
    assert _trend_color(chart) == "#c0ffee"


def test_a_trend_on_the_base_column_keeps_the_base_ink() -> None:
    chart = _line(
        MONTHS,
        [{"type": "trend", "fit": "linear()", "y_column": "revenue"}],
        layers=[CHURN],
    )
    assert _trend_color(chart) == PALETTE[0]


def test_each_series_of_a_colored_layer_wears_that_layers_series_ink() -> None:
    chart = _line(
        BY_REGION,
        [
            {
                "type": "trend",
                "fit": "linear()",
                "y_column": "churn_rate",
                "series": "each",
            }
        ],
        layers=[{**CHURN, "color": "region"}],
    )
    colors = {a.series: _trend_color(chart, i) for i, a in enumerate(chart.annotations)}
    assert colors == chart.layers[0].series_inks


def test_total_over_a_colored_layer_stays_muted() -> None:
    chart = _line(
        BY_REGION,
        [
            {
                "type": "trend",
                "fit": "linear()",
                "y_column": "churn_rate",
                "series": "total",
            }
        ],
        layers=[{**CHURN, "color": "region"}],
    )
    assert _trend_color(chart) == "#626366"  # clarity's secondary ink


def test_a_rule_on_a_layer_series_wears_that_series_ink() -> None:
    chart = _line(
        BY_REGION,
        [
            {
                "type": "rule",
                "y": "avg()",
                "y_column": "churn_rate",
                "series": ["West"],
            }
        ],
        layers=[{**CHURN, "color": "region"}],
    )
    rule = chart.annotations[0].style.rule
    assert rule is not None
    assert rule.stroke.color == chart.layers[0].series_inks["West"]


def test_a_bar_layer_gets_a_palette_ink_too() -> None:
    bar = BarChart.model_validate(
        {
            "id": "b",
            "type": "bar",
            "x": "region",
            "y": "revenue",
            "query_name": "q",
            "layers": [{"type": "bar", "y": "cost"}],
        }
    )
    rows = [{"region": "East", "revenue": 1.0, "cost": 2.0}]
    resolved = resolve(bar, rows, CTX, rendered_at=NOW)
    assert resolved.layers[0].ink == PALETTE[1]
