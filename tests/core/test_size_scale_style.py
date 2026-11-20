"""`style.size` configures the size channel's area range and scale type."""

from __future__ import annotations

from typing import Any

import pytest
from pydantic import TypeAdapter, ValidationError

from dbt_charts.core.compile.config import get_theme_style
from dbt_charts.core.compile.errors import CompilationError
from dbt_charts.core.compile.models.chart.authored import (
    BarChart,
    PointMapChart,
    ScatterChart,
)
from dbt_charts.core.compile.models.chart.normalized import Chart
from dbt_charts.core.compile.models.primitives import SizeScaleStyle
from dbt_charts.core.compile.models.style.authored import StylePatch
from dbt_charts.core.compile.resolve import resolve
from dbt_charts.core.compile.resolve.chart._annotations import point_radius
from dbt_charts.core.compile.resolve.style.board import resolve_style_and_context


def _scatter(scale: dict[str, Any], **extra: Any) -> ScatterChart:
    return ScatterChart.model_validate(
        {"type": "scatter", "x": "x", "y": "y", "style": {"size": scale}, **extra}
    )


def test_scatter_accepts_range_and_type() -> None:
    chart = _scatter({"range": [16, 1600], "type": "sqrt"}, size="n")
    assert chart.style is not None
    assert chart.style.size is not None
    assert chart.style.size.range == (16, 1600)
    assert chart.style.size.type == "sqrt"


@pytest.mark.parametrize("chart_type", ["point_map", "bubble_map"])
def test_map_accepts_size_block(chart_type: str) -> None:
    chart = PointMapChart.model_validate(
        {
            "type": chart_type,
            "latitude": "lat",
            "longitude": "lon",
            "size": "n",
            "style": {"size": {"range": [9, 2500], "type": "log"}},
        }
    )
    assert chart.style is not None
    assert chart.style.size is not None
    assert chart.style.size.type == "log"


def test_collapsed_map_accepts_size_block() -> None:
    PointMapChart.model_validate(
        {
            "type": "bubble_map",
            "latitude": "lat",
            "longitude": "lon",
            "collapse": True,
            "style": {"size": {"type": "log"}},
        }
    )


@pytest.mark.parametrize(
    ("size_range", "message"),
    [
        ([400, 16], r"style\.size\.range: low \(400\) must be below high \(16\)\."),
        ([16, 16], r"low \(16\) must be below high \(16\)"),
        (
            [-4, 100],
            r"style\.size\.range\.0\n  Input should be greater than or equal to 0",
        ),
        ([16], "Field required"),
        ([1, 2, 3], "at most 2 items"),
        (["a", "b"], "valid number"),
    ],
)
def test_rejects_bad_range(size_range: list[Any], message: str) -> None:
    with pytest.raises(ValidationError, match=message):
        _scatter({"range": size_range}, size="n")


def test_rejects_unknown_scale_type() -> None:
    with pytest.raises(ValidationError, match="type"):
        _scatter({"type": "symlog"}, size="n")


def test_scatter_style_size_requires_a_size_channel() -> None:
    with pytest.raises(
        ValidationError,
        match=r"style\.size configures the size channel, but this scatter chart has no size: field",
    ):
        _scatter({"type": "sqrt"})


def test_map_style_size_requires_size_or_collapse() -> None:
    with pytest.raises(
        ValidationError,
        match=r"style\.size configures the size channel, but this bubble_map chart has no size: field",
    ):
        PointMapChart.model_validate(
            {
                "type": "bubble_map",
                "latitude": "lat",
                "longitude": "lon",
                "style": {"size": {"type": "sqrt"}},
            }
        )


def test_style_size_is_rejected_on_other_chart_types() -> None:
    with pytest.raises(ValidationError, match="size"):
        BarChart.model_validate(
            {"type": "bar", "x": "x", "y": "y", "style": {"size": {"type": "sqrt"}}}
        )


def test_a_conditional_rule_cannot_carry_a_size_block() -> None:
    with pytest.raises(ValidationError, match="size"):
        _scatter(
            {},
            style={"conditional": {"n": [{"gt": 5, "size": {"range": [9, 2500]}}]}},
        )


def test_board_level_default_accepts_size_block() -> None:
    StylePatch.model_validate({"charts": {"scatter": {"size": {"type": "sqrt"}}}})
    StylePatch.model_validate({"charts": {"point_map": {"size": {"range": [9, 2500]}}}})


# ── resolve ──────────────────────────────────────────────────────────────────

_SCATTER_ROWS = [{"x": 1, "y": 2, "n": 3}, {"x": 2, "y": 3, "n": 600}]
_MAP_ROWS = [{"lat": 1, "lon": 2, "n": 3}, {"lat": 2, "lon": 3, "n": 600}]


def _resolved(
    chart_type: str,
    rows: list[dict[str, Any]],
    *,
    style: dict[str, Any] | None = None,
    board_style: dict[str, Any] | None = None,
) -> Any:  # noqa: ANN401 — a ResolvedChart union narrowed per test
    fields = (
        {"x": "x", "y": "y"}
        if chart_type == "scatter"
        else {"latitude": "lat", "longitude": "lon", "projection": "mercator"}
    )
    chart = TypeAdapter(Chart).validate_python(
        {
            "id": "t",
            "type": chart_type,
            "size": "n",
            **fields,
            **({"style": style} if style else {}),
        }
    )
    patch = StylePatch.model_validate(board_style) if board_style else None
    _, context = resolve_style_and_context(get_theme_style(), patch)
    return resolve(chart, rows, chart_style_context=context)


def test_scatter_chart_style_size_reaches_the_resolved_style() -> None:
    resolved = _resolved(
        "scatter",
        _SCATTER_ROWS,
        style={"size": {"range": [17, 1234], "type": "sqrt"}},
    )
    assert resolved.style.size.range == (17, 1234)
    assert resolved.style.size.type == "sqrt"


def test_scatter_board_default_reaches_the_resolved_style() -> None:
    resolved = _resolved(
        "scatter",
        _SCATTER_ROWS,
        board_style={"charts": {"scatter": {"size": {"range": [11, 777]}}}},
    )
    assert resolved.style.size.range == (11, 777)


@pytest.mark.parametrize("chart_type", ["point_map", "bubble_map"])
def test_map_chart_and_board_size_reach_the_resolved_style(chart_type: str) -> None:
    own = _resolved(
        chart_type, _MAP_ROWS, style={"size": {"range": [9, 2500], "type": "sqrt"}}
    )
    assert own.style.point_map.size.range == (9, 2500)
    assert own.style.point_map.size.type == "sqrt"
    board = _resolved(
        chart_type,
        _MAP_ROWS,
        board_style={
            "charts": {"point_map": {"size": {"range": [9, 2500], "type": "log"}}}
        },
    )
    assert board.style.point_map.size.type == "log"


@pytest.mark.parametrize("chart_type", ["scatter", "point_map"])
def test_log_size_scale_rejects_non_positive_values(chart_type: str) -> None:
    rows = [
        {"x": 1, "y": 2, "lat": 1, "lon": 2, "n": 0},
        {"x": 2, "y": 3, "lat": 2, "lon": 3, "n": 5},
    ]
    with pytest.raises(
        CompilationError,
        match=(
            r"Chart 't': size column 'n' has a value <= 0, but style\.size\.type: "
            r"log requires strictly positive data"
        ),
    ):
        _resolved(chart_type, rows, style={"size": {"range": [9, 2500], "type": "log"}})


def test_log_size_scale_accepts_positive_values() -> None:
    _resolved("scatter", _SCATTER_ROWS, style={"size": {"type": "log"}})


_LOG_FROM_ZERO = (
    r"style\.size\.type: log needs a range starting above 0; the smallest value "
    r"would draw with no area\. Set style\.size\.range, e\.g\. \[9, 2500\]\."
)


@pytest.mark.parametrize("chart_type", ["point_map", "bubble_map"])
def test_map_log_scale_on_the_default_zero_based_range_is_rejected(
    chart_type: str,
) -> None:
    with pytest.raises(ValidationError, match=_LOG_FROM_ZERO):
        _resolved(chart_type, _MAP_ROWS, style={"size": {"type": "log"}})
    with pytest.raises(ValidationError, match=_LOG_FROM_ZERO):
        _resolved(
            chart_type,
            _MAP_ROWS,
            board_style={"charts": {"point_map": {"size": {"type": "log"}}}},
        )


def test_map_log_scale_with_a_raised_range_compiles() -> None:
    resolved = _resolved(
        "bubble_map", _MAP_ROWS, style={"size": {"range": [9, 2500], "type": "log"}}
    )
    assert resolved.style.point_map.size.type == "log"


def test_the_merged_size_scale_style_rejects_log_from_zero() -> None:
    with pytest.raises(ValidationError, match=_LOG_FROM_ZERO):
        SizeScaleStyle.model_validate({"range": [0, 1000], "type": "log"})


@pytest.mark.parametrize(
    ("scale", "cells", "area"),
    [
        ({"range": [4, 361], "type": "linear"}, [5, 50], 361),
        ({"range": [16, 1600], "type": "sqrt"}, [5, 50], 1600),
        ({"range": [16, 1600], "type": "log"}, [5, 50], 1600),
        ({"range": [16, 1600], "type": "linear"}, [0, 0], 808),
        ({"range": [16, 1600], "type": "log"}, [7, 7], 808),
    ],
)
def test_point_radius_is_the_largest_marks_radius(
    scale: dict[str, Any], cells: list[float], area: float
) -> None:
    radius = point_radius(
        None, size_channel=(cells, SizeScaleStyle.model_validate(scale))
    )
    assert radius == pytest.approx(area**0.5 / 2)
