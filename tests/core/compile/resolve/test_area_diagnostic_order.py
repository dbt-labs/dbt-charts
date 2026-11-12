"""Pins the diagnostic order between area's encoding validator and its axis bake.

`_resolve_area` checks `_validate_area_encoding` before `plan_cartesian` runs
its axis-cascade bake (`area.py`'s own comment: "Checked before
plan_cartesian's axis bake so a doubly-invalid chart reports this encoding
error, not the axis bake's."). A chart that is both encoding-invalid (a
categorical y) and axis-bake-invalid (`axis_y.ticks.step` set, which
`_bake_cartesian_axes` rejects via `ERR_TICKS_INTERVAL_MEASURE_AXIS`) must
still report the encoding error.
"""

from __future__ import annotations

import pytest

from dbt_charts.core.compile.config import get_default_theme_name, get_theme_style
from dbt_charts.core.compile.errors import CompilationError
from dbt_charts.core.compile.models.chart.normalized import AreaChart
from dbt_charts.core.compile.models.query.normalized import SqlQuery
from dbt_charts.core.compile.models.style.authored import AreaChartStylePatch
from dbt_charts.core.compile.models.style.context import ChartStyleContext
from dbt_charts.core.compile.resolve import resolve
from dbt_charts.core.compile.resolve.style.board import resolve_chart_style_context

_DATA: list[dict] = [
    {"month": "Jan", "segment": "consumer"},
    {"month": "Feb", "segment": "enterprise"},
    {"month": "Mar", "segment": "consumer"},
]


def _board() -> ChartStyleContext:
    return resolve_chart_style_context(get_theme_style(get_default_theme_name()))


def _sql() -> SqlQuery:
    return SqlQuery(sql="SELECT 1", source="t")


def test_area_encoding_error_wins_over_axis_bake_error() -> None:
    """y is categorical (encoding-invalid) AND axis_y.ticks.step is set
    (axis-bake-invalid) -- the encoding error must win."""
    chart = AreaChart(
        id="area1",
        type="area",
        x="month",
        y="segment",
        query=_sql(),
        query_name="q",
        variable_dependencies=set(),
        style=AreaChartStylePatch.model_validate({"axis_y": {"ticks": {"step": 5}}}),
    )
    with pytest.raises(CompilationError) as exc_info:
        resolve(chart, _DATA, _board())
    assert exc_info.value.code is not None
    assert exc_info.value.code.code == "ERR-AREA-ENCODING-SWAPPED"


def test_area_encoding_error_wins_over_axis_bake_error_with_stack_set() -> None:
    """Same as above, with chart-root `stack` set -- the branch that resolves
    `resolved_stack` without needing the chart-local style context at all.
    `style.marks.area.color` also carries an unknown palette token, which
    `build_chart_style_context` rejects with `UnknownColorError`. If that
    context is built before `_validate_area_encoding` runs, the chart raises
    `UnknownColorError` instead of the encoding error."""
    chart = AreaChart(
        id="area1",
        type="area",
        x="month",
        y="segment",
        stack="zero",
        query=_sql(),
        query_name="q",
        variable_dependencies=set(),
        style=AreaChartStylePatch.model_validate(
            {
                "marks": {"area": {"color": "dbt-creams.does-not-exist"}},
                "axis_y": {"ticks": {"step": 5}},
            }
        ),
    )
    with pytest.raises(CompilationError) as exc_info:
        resolve(chart, _DATA, _board())
    assert exc_info.value.code is not None
    assert exc_info.value.code.code == "ERR-AREA-ENCODING-SWAPPED"


def test_area_encoding_swap_detected_per_panel_not_pooled_across_panels() -> None:
    """`_validate_area_encoding`'s x-cardinality-ratio check must read one
    panel's rows, not `dataset.all_rows()` pooled across every panel.

    Each of the two `region` panels independently has the swapped-x-and-y
    shape the check exists to catch: `seq` (mistakenly used as x) takes 10
    distinct values against series A and only 5 against series B, giving a
    per-panel ratio of 10/15 ≈ 0.667 (> the 0.5 threshold) with 2 colors.
    But both panels repeat the exact same `seq` values, so pooling all 30
    rows together leaves the *distinct* count still at 10 while the sample
    count doubles to 30 — a pooled ratio of 10/30 ≈ 0.333, under the
    threshold. A whole-dataset read stays silent here; a per-panel read
    catches it, because that's the grain Vega-Lite actually stacks at.
    """
    rows: list[dict] = []
    for region in ("West", "East"):
        for i in range(1, 11):
            rows.append({"region": region, "seq": i, "segment": "A", "amount": i})
        for i in range(1, 6):
            rows.append({"region": region, "seq": i, "segment": "B", "amount": i})
    chart = AreaChart.model_validate(
        {
            "id": "area1",
            "type": "area",
            "x": "seq",
            "y": "amount",
            "color": "segment",
            "stack": "center",
            "multiples": {"rows": "region"},
            "query_name": "q",
        }
    )
    with pytest.raises(CompilationError) as exc_info:
        resolve(chart, rows, _board())
    assert exc_info.value.code is not None
    assert exc_info.value.code.code == "ERR-AREA-ENCODING-SWAPPED"
