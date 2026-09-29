"""Regression: `_resolve_cartesian_ticks`'s independent-scale branch
(`_domain.py`, the `elif scale == "independent": return
_CartesianTickResolution((), None)` line) must actually suppress the baked
tick ladder for every non-stacked family that calls it directly — bar
(non-stacked), line, area (non-stacked), scatter.

The only prior coverage was `test_small_multiples_data_grain.py::
TestIndependentScale`, which builds its chart with `stack: "zero"`
(`_repro_a_chart`) — that routes through `_resolve_stacked_bar_ticks`
instead and never reaches this branch. Deleting the `elif` line left that
suite green (see the review this test was written against). This file
parametrizes over the four families that call `_resolve_cartesian_ticks`
directly, on the same rows, asserting `tick_values == ()` and no baked
domain under `independent`, and a non-empty ladder under `shared`.
"""

from __future__ import annotations

from typing import Any

import pytest

from dbt_charts.core.compile.config import get_theme_style, reset_config
from dbt_charts.core.compile.models.chart.normalized import (
    AreaChart,
    BarChart,
    LineChart,
    ScatterChart,
)
from dbt_charts.core.compile.resolve import resolve
from dbt_charts.core.compile.resolve.style.board import resolve_style_and_context


@pytest.fixture(autouse=True)
def reset():
    reset_config()
    yield
    reset_config()


def _board() -> Any:
    return resolve_style_and_context(get_theme_style("clarity"))


def _rows() -> list[dict[str, Any]]:
    """Two panels with genuinely different ranges, so a baked shared ladder
    (spanning the union) is visibly non-trivial and distinguishable from the
    empty independent-scale result."""
    return [
        {"x": 1, "grp": "A", "val": 1},
        {"x": 2, "grp": "A", "val": 5},
        {"x": 1, "grp": "B", "val": 50},
        {"x": 2, "grp": "B", "val": 90},
    ]


@pytest.mark.parametrize(
    ("chart_cls", "chart_type", "extra_fields"),
    [
        (BarChart, "bar", {"stack": "none"}),
        (LineChart, "line", {}),
        (AreaChart, "area", {"stack": "none"}),
        (ScatterChart, "scatter", {}),
    ],
    ids=["bar_non_stacked", "line", "area_non_stacked", "scatter"],
)
def test_independent_scale_bakes_no_ladder_shared_does(
    chart_cls: type, chart_type: str, extra_fields: dict[str, Any]
) -> None:
    _, ctx = _board()

    independent_chart = chart_cls.model_validate(
        {
            "id": "t",
            "type": chart_type,
            "x": "x",
            "y": "val",
            "multiples": {"rows": "grp", "scale": "independent"},
            **extra_fields,
        }
    )
    shared_chart = chart_cls.model_validate(
        {
            "id": "t",
            "type": chart_type,
            "x": "x",
            "y": "val",
            "multiples": {"rows": "grp"},  # scale defaults to "shared"
            **extra_fields,
        }
    )

    rows = _rows()
    independent_resolved = resolve(independent_chart, rows, chart_style_context=ctx)
    shared_resolved = resolve(shared_chart, rows, chart_style_context=ctx)

    assert independent_resolved.style.axis_y.tick_values == ()
    assert independent_resolved.style.axis_y.domain_max is None
    assert shared_resolved.style.axis_y.tick_values != ()
    assert shared_resolved.style.axis_y.domain_max is not None


@pytest.mark.parametrize(
    ("chart_cls", "chart_type", "extra_fields"),
    [
        (BarChart, "bar", {"stack": "none"}),
        (LineChart, "line", {}),
        (AreaChart, "area", {"stack": "none"}),
        (ScatterChart, "scatter", {}),
    ],
    ids=["bar_non_stacked", "line", "area_non_stacked", "scatter"],
)
def test_independent_scale_bakes_no_ladder_even_with_a_fixed_decimal_format(
    chart_cls: type, chart_type: str, extra_fields: dict[str, Any]
) -> None:
    """A format whose resolved min tick step would otherwise floor the baked
    ladder (tick_min_step_for_format, _domain.py) must still respect
    `independent`'s "each panel gets its own scale" contract --
    baking one chart-wide ladder from the format alone would union two
    panels' independent ranges onto one shared domain (e.g. east ~[0, 2],
    west ~[900, 1500] collapsing onto one [0, 1500]-ish scale)."""
    _, ctx = _board()

    independent_chart = chart_cls.model_validate(
        {
            "id": "t",
            "type": chart_type,
            "x": "x",
            "y": "val",
            "format": "integer",
            "multiples": {"rows": "grp", "scale": "independent"},
            **extra_fields,
        }
    )
    resolved = resolve(independent_chart, _rows(), chart_style_context=ctx)
    assert resolved.style.axis_y.tick_values == ()
    assert resolved.style.axis_y.domain_max is None
