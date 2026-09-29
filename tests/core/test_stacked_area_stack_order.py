"""Stacked area honors ``style.stack_order`` like stacked bar."""

from __future__ import annotations

import pytest

from dbt_charts.core.compile.config import get_theme_style, reset_config
from dbt_charts.core.compile.models.chart.normalized import AreaChart
from dbt_charts.core.compile.models.query.normalized import SqlQuery
from dbt_charts.core.compile.models.style.authored import AreaChartStylePatch
from dbt_charts.core.compile.resolve.style.board import resolve_style_and_context
from dbt_charts.core.render.chart.vega_lite import generate_vega_lite_spec

from .conftest import chart_pane

_BOARD_STYLE, _BOARD_CTX = resolve_style_and_context(get_theme_style())

# Authored order [zeta, alpha, mid], value order [mid, alpha, zeta] and
# alphabetical order [alpha, mid, zeta] all differ.
_SERIES = ("zeta", "alpha", "mid")
_VALUES = {"zeta": 1, "alpha": 5, "mid": 10}
_MONTHS = ("2024-01-01", "2024-02-01", "2024-04-01")
_WIDE_DATA = [{"month": m, **_VALUES} for m in _MONTHS]
_LONG_DATA = [{"month": m, "s": s, "v": _VALUES[s]} for m in _MONTHS for s in _SERIES]


@pytest.fixture(autouse=True)
def _reset():
    reset_config()
    yield
    reset_config()


def _spec(
    stack_order: str | None,
    *,
    wide: bool,
    time_unit: str | None = None,
    rows: list[dict] | None = None,
    color: str | None = None,
) -> dict:
    style_kwargs: dict = {"stack": "zero"}
    if time_unit is not None:
        style_kwargs["axis_x"] = {"time_unit": time_unit}
    if stack_order is not None:
        style_kwargs["stack_order"] = stack_order
    chart = AreaChart(
        id="t",
        query=SqlQuery(sql="SELECT 1", source="t"),
        query_name="q",
        type="area",
        x="month",
        y=list(_SERIES) if wide else "v",
        color=color if wide else "s",
        style=AreaChartStylePatch(**style_kwargs),
    )
    return generate_vega_lite_spec(
        chart,
        rows or (_WIDE_DATA if wide else _LONG_DATA),
        board_style=_BOARD_STYLE,
        chart_style_context=_BOARD_CTX,
    )


def _baseline_calc(spec: dict) -> str:
    pane = chart_pane(spec)
    field = pane["encoding"]["order"]["field"]
    return next(t["calculate"] for t in pane["transform"] if t.get("as") == field)


@pytest.mark.parametrize("wide", [True, False])
def test_stack_order_data_stacks_in_authored_order(wide: bool):
    calc = _baseline_calc(_spec("data", wide=wide))
    assert '"zeta" ? 0' in calc
    assert '"alpha" ? 1' in calc
    assert '"mid" ? 2' in calc


def test_stack_order_data_survives_ordinal_time_gap_fill():
    # yearmonth gap-fills the missing March as buckets x sorted(series), which
    # must not turn SQL row order into alphabetical order.
    calc = _baseline_calc(_spec("data", wide=False, time_unit="yearmonth"))
    assert '"zeta" ? 0' in calc
    assert '"alpha" ? 1' in calc


def test_stack_order_data_survives_gap_fill_for_y_list_with_color():
    rows = [
        {"month": m, "region": r, "zeta": 1, "alpha": 5, "mid": 10}
        for m in _MONTHS
        for r in ("west", "east")
    ]
    calc = _baseline_calc(
        _spec("data", wide=True, time_unit="yearmonth", rows=rows, color="region")
    )
    assert calc.index('"west') < calc.index('"east'), calc


@pytest.mark.parametrize("wide", [True, False])
def test_stack_order_default_stays_value(wide: bool):
    calc = _baseline_calc(_spec(None, wide=wide))
    assert '"mid" ? 0' in calc
    assert '"alpha" ? 1' in calc
