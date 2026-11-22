"""Each axis key drives the axis it names: x is the category axis, y the value axis.

On a horizontal bar the category axis takes ``axis_x.position: left | right`` and the
value axis ``axis_y.position: top | bottom``; every other cartesian chart keeps
``axis_x`` on top or bottom and ``axis_y`` on the left or right. A chart's own style
that names an edge its axis cannot take is refused; a board or theme value that only
fits the other orientation does not reach the chart.
"""

from __future__ import annotations

from collections.abc import Iterator
from datetime import UTC, datetime
from typing import Any

import pytest

from dbt_charts.core.compile.config import get_theme_style, reset_config
from dbt_charts.core.compile.errors import CompilationError
from dbt_charts.core.compile.models.chart.normalized import BarChart, LineChart
from dbt_charts.core.compile.models.chart.resolved import (
    ResolvedBarChart,
    ResolvedLineChart,
)
from dbt_charts.core.compile.models.style.authored import StylePatch
from dbt_charts.core.compile.resolve import resolve
from dbt_charts.core.compile.resolve.style.board import resolve_style_and_context
from dbt_charts.core.diagnostics.codes_compile import ERR_AXIS_POSITION_WRONG_EDGE
from dbt_charts.core.diagnostics.codes_render import WARN_AXIS_POSITION_NOT_APPLIED
from dbt_charts.core.render.warnings import axis_position_not_applied
from dbt_charts.core.render.warnings.base import WarningContext

ROWS = [{"cat": "a", "v": 3}, {"cat": "b", "v": 2}, {"cat": "c", "v": 1}]
MONTHS = [{"m": "2026-01-01", "v": 3}, {"m": "2026-02-01", "v": 2}]


@pytest.fixture(autouse=True)
def _reset() -> Iterator[None]:
    reset_config()
    yield
    reset_config()


def _resolve(raw: dict[str, Any], rows: list[dict[str, Any]], board: dict | None):
    patch = StylePatch.model_validate(board) if board else None
    _, ctx = resolve_style_and_context(get_theme_style("clarity"), patch)
    model = LineChart if raw["type"] == "line" else BarChart
    return resolve(
        model.model_validate({"id": "t", "query_name": "q", **raw}),
        rows,
        chart_style_context=ctx,
        rendered_at=datetime(2026, 1, 1, tzinfo=UTC),
    )


def _bar(style: dict[str, Any] | None = None, board: dict | None = None):
    raw = {
        "type": "bar",
        "x": "cat",
        "y": "v",
        "style": {"orientation": "horizontal", **(style or {})},
    }
    out = _resolve(raw, ROWS, board)
    assert isinstance(out, ResolvedBarChart)
    assert out.orientation == "horizontal"
    return out


def _edges(chart: ResolvedBarChart | ResolvedLineChart) -> tuple[str | None, str]:
    return chart.style.axis_x.position, chart.style.axis_y.position


def test_a_horizontal_bar_defaults_to_categories_beside_and_values_below() -> None:
    assert _edges(_bar()) == (None, "bottom")


@pytest.mark.parametrize("side", ["left", "right"])
def test_axis_x_position_places_a_horizontal_bars_categories(side: str) -> None:
    assert _edges(_bar({"axis_x": {"position": side}})) == (side, "bottom")


@pytest.mark.parametrize("side", ["top", "bottom"])
def test_axis_y_position_places_a_horizontal_bars_value_axis(side: str) -> None:
    assert _edges(_bar({"axis_y": {"position": side}})) == (None, side)


@pytest.mark.parametrize(
    ("axis", "side"),
    [("axis_y", "left"), ("axis_y", "right"), ("axis_x", "top"), ("axis_x", "bottom")],
)
def test_a_horizontal_bar_refuses_an_edge_its_axis_cannot_take(
    axis: str, side: str
) -> None:
    with pytest.raises(CompilationError) as err:
        _bar({axis: {"position": side}})
    assert err.value.code == ERR_AXIS_POSITION_WRONG_EDGE
    assert f"{axis}.position: {side}" in str(err.value)


@pytest.mark.parametrize(("axis", "side"), [("axis_x", "left"), ("axis_y", "top")])
def test_a_vertical_chart_refuses_an_edge_its_axis_cannot_take(
    axis: str, side: str
) -> None:
    raw = {"type": "line", "x": "m", "y": "v", "style": {axis: {"position": side}}}
    with pytest.raises(CompilationError) as err:
        _resolve(raw, MONTHS, None)
    assert err.value.code == ERR_AXIS_POSITION_WRONG_EDGE


def test_a_board_value_axis_side_meant_for_vertical_charts_skips_a_horizontal_bar() -> (
    None
):
    board = {"charts": {"axis_y": {"position": "left"}, "axis_x": {"position": "top"}}}
    assert _edges(_bar(board=board)) == (None, "bottom")


def test_a_board_horizontal_side_skips_a_vertical_chart() -> None:
    board = {"charts": {"axis_y": {"position": "top"}, "axis_x": {"position": "right"}}}
    raw = {"type": "line", "x": "m", "y": "v"}
    out = _resolve(raw, MONTHS, board)
    assert isinstance(out, ResolvedLineChart)
    assert out.style.axis_x.position is None
    assert out.style.axis_y.position in ("left", "right")


def test_a_charts_own_auto_value_axis_sits_below_a_horizontal_bar() -> None:
    assert _edges(_bar({"axis_y": {"position": "auto"}})) == (None, "bottom")


def test_a_horizontal_bars_support_table_defaults_to_the_category_side() -> None:
    raw = {
        "type": "bar",
        "x": "cat",
        "y": "v",
        "support_table": {"entries": [{"source": "v"}]},
        "style": {"orientation": "horizontal", "axis_x": {"position": "right"}},
    }
    out = _resolve(raw, ROWS, None)
    assert isinstance(out, ResolvedBarChart)
    assert out.effective_support_table_style is not None
    assert out.effective_support_table_style.position == "right"


def _warnings(chart: ResolvedBarChart | ResolvedLineChart) -> list[str]:
    ctx = WarningContext.model_construct(layout_charts={"t": chart})
    found = axis_position_not_applied.detect(ctx)
    assert {d.code for d in found} <= {WARN_AXIS_POSITION_NOT_APPLIED.code}
    assert all(d.chart == "t" for d in found)
    return [d.message for d in found]


@pytest.mark.parametrize(
    ("board", "key", "instead"),
    [
        (
            {"charts": {"axis_y": {"position": "right"}}},
            "axis_y.position: right",
            "axis_x.position",
        ),
        (
            {"charts": {"bar": {"axis_x": {"position": "top"}}}},
            "axis_x.position: top",
            "axis_y.position",
        ),
    ],
)
def test_a_board_edge_that_skips_a_horizontal_bar_warns_once(
    board: dict[str, Any], key: str, instead: str
) -> None:
    (message,) = _warnings(_bar(board=board))
    assert "'t'" in message and key in message and f"`{instead}" in message


def test_a_horizontal_bar_on_the_default_theme_warns_of_nothing() -> None:
    assert _warnings(_bar()) == []
    assert _warnings(_bar(board={"charts": {"axis_y": {"position": "auto"}}})) == []


def test_a_board_edge_for_a_horizontal_bar_does_not_warn_a_vertical_chart() -> None:
    board = {"charts": {"axis_y": {"position": "right"}}}
    out = _resolve(
        {"type": "bar", "x": "cat", "y": "v", "style": {"orientation": "vertical"}},
        ROWS,
        board,
    )
    assert isinstance(out, ResolvedBarChart) and out.orientation == "vertical"
    assert _warnings(out) == []
