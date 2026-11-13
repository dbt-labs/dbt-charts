"""A conditional rule's operand can be an aggregate of its own column.

``min()``, ``max()``, ``avg()``, ``median()`` and ``percentile(p)`` compute over
every row of the rule's column, never per series or per panel; resolve writes the
literal into the rule, so render only ever compares with a value.
"""

from __future__ import annotations

import datetime as dt
import re
from typing import Any

import pytest
from pydantic import ValidationError

from dbt_charts.core.compile.config import get_theme_style
from dbt_charts.core.compile.models.aggregate import AggregateAnchor
from dbt_charts.core.compile.models.chart.normalized import (
    AreaChart,
    BarChart,
    HeatmapChart,
    KpiChart,
    LineChart,
    PieChart,
    PointMapChart,
    ScatterChart,
    TableChart,
)
from dbt_charts.core.compile.models.chart.resolved import (
    ResolvedBarChart,
    ResolvedTableChart,
)
from dbt_charts.core.compile.models.conditional_predicate import ConditionalPredicate
from dbt_charts.core.compile.models.query.normalized import SqlQuery
from dbt_charts.core.compile.models.style.resolved.point_map import (
    ResolvedPointMapStyle,
)
from dbt_charts.core.compile.resolve import resolve
from dbt_charts.core.compile.resolve.style.board import (
    resolve_style,
    resolve_style_and_context,
)

from ._engine_render import render_chart_svg, render_table_svg

_BOARD_STYLE, _CTX = resolve_style_and_context(get_theme_style())
_RENDER_STYLE = resolve_style(get_theme_style())
RED = "#c62828"

# Long format: one revenue column holds both regions' series.
_ROWS: list[dict[str, Any]] = [
    {"month": "Jan", "region": "EU", "revenue": 10},
    {"month": "Jan", "region": "US", "revenue": 30},
    {"month": "Feb", "region": "EU", "revenue": 15},
    {"month": "Feb", "region": "US", "revenue": 25},
]


def _bar(conditional: dict[str, Any], **kwargs: Any) -> BarChart:
    return BarChart(
        id="b",
        type="bar",
        x="month",
        y="revenue",
        style={"conditional": conditional},
        **kwargs,
    )


def _bar_rules(conditional: dict[str, Any], rows: list[dict[str, Any]]) -> Any:
    resolved = resolve(_bar(conditional), rows, chart_style_context=_CTX)
    assert isinstance(resolved, ResolvedBarChart)
    return resolved.style.conditional


def _red(rule: dict[str, Any]) -> dict[str, Any]:
    return {**rule, "marks": {"bar": {"color": RED}}}


@pytest.mark.parametrize(
    ("authored", "field", "expected"),
    [
        ({"gt": "avg()"}, "gt", AggregateAnchor(fn="avg")),
        ({"eq": "max()"}, "eq", AggregateAnchor(fn="max")),
        (
            {"between": ["percentile(25)", 40]},
            "between",
            [AggregateAnchor(fn="percentile", percent=25), 40],
        ),
    ],
)
def test_a_call_string_parses_to_an_aggregate(
    authored: dict[str, Any], field: str, expected: object
) -> None:
    rule = ConditionalPredicate.model_validate(authored)
    assert getattr(rule, field) == expected


@pytest.mark.parametrize("field", ["eq", "ne"])
def test_an_extreme_survives_a_dump_round_trip(field: str) -> None:
    dumped = ConditionalPredicate.model_validate({field: "max()"}).model_dump()
    rule = ConditionalPredicate.model_validate(dumped)
    assert getattr(rule, field) == AggregateAnchor(fn="max")


@pytest.mark.parametrize(
    ("authored", "message"),
    [
        ({"eq": "avg()"}, "eq and ne take min() or max()"),
        ({"gt": "sum()"}, "not an aggregate function"),
        ({"lt": "percentile(120)"}, "0 to 100"),
    ],
)
def test_a_bad_call_is_refused(authored: dict[str, Any], message: str) -> None:
    with pytest.raises(ValidationError, match=re.escape(message)):
        ConditionalPredicate.model_validate(authored)


def test_max_is_over_the_whole_column_not_per_series() -> None:
    svg = render_chart_svg(
        _bar({"revenue": [_red({"eq": "max()"})]}, color="region"),
        _ROWS,
        width=600,
        height=360,
        board_style=_BOARD_STYLE,
        chart_style_context=_CTX,
    )
    assert svg.count(RED) == 1


def test_resolve_writes_the_computed_literal() -> None:
    rules = _bar_rules(
        {
            "revenue": [
                _red({"gt": "avg()"}),
                _red({"between": ["percentile(25)", "median()"]}),
            ]
        },
        _ROWS,
    )
    assert rules["revenue"][0].gt == 20.0
    assert rules["revenue"][1].between == [13.75, 20.0]


def test_max_of_a_date_column_is_its_latest_cell() -> None:
    rows = [
        {"month": "Jan", "revenue": 1, "day": dt.date(2026, 1, 31)},
        {"month": "Feb", "revenue": 2, "day": dt.date(2026, 2, 28)},
    ]
    rules = _bar_rules({"day": [_red({"eq": "max()"})]}, rows)
    assert rules["day"][0].eq == dt.date(2026, 2, 28)


def test_a_date_column_compares_with_its_min() -> None:
    rows = [
        {"month": "Jan", "revenue": 1, "day": dt.date(2026, 1, 31)},
        {"month": "Feb", "revenue": 2, "day": dt.date(2026, 2, 28)},
    ]
    rules = _bar_rules({"day": [_red({"gt": "min()"})]}, rows)
    assert rules["day"][0].gt == dt.date(2026, 1, 31)
    svg = render_chart_svg(
        _bar({"day": [_red({"gt": "min()"})]}),
        rows,
        width=600,
        height=360,
        board_style=_BOARD_STYLE,
        chart_style_context=_CTX,
    )
    assert svg.count(RED) == 1


def test_a_rule_over_an_all_null_column_is_dropped() -> None:
    rows = [{"month": "Jan", "revenue": 1, "target": None}]
    rules = _bar_rules(
        {"target": [_red({"gt": "avg()"}), _red({"default": True})]}, rows
    )
    assert [r.default for r in rules["target"]] == [True]


def test_avg_of_a_text_column_is_refused() -> None:
    with pytest.raises(ValueError, match="avg\\(\\) needs a numeric column"):
        _bar_rules({"month": [_red({"gt": "avg()"})]}, _ROWS)


def test_max_of_a_text_column_is_refused() -> None:
    with pytest.raises(ValueError, match="max\\(\\) needs a numeric or date column"):
        _bar_rules({"month": [_red({"eq": "max()"})]}, _ROWS)


def test_a_table_rule_is_computed_over_its_column() -> None:
    chart = TableChart(
        id="t",
        type="table",
        style={
            "columns": {
                "revenue": {
                    "conditional": [{"lt": "median()", "background": "#112233"}]
                }
            }
        },
    )
    resolved = resolve(chart, _ROWS, chart_style_context=_CTX)
    assert isinstance(resolved, ResolvedTableChart)
    assert resolved.columns is not None
    rules = resolved.columns["revenue"].conditional
    assert rules is not None
    assert rules[0].lt == 20.0
    svg = render_table_svg(resolved, _ROWS, width=600, board_style=_RENDER_STYLE)
    assert svg.count("#112233") == 2


def test_a_pivot_table_refuses_a_function() -> None:
    chart = TableChart(
        id="t",
        type="table",
        rows=["region"],
        columns=["month"],
        values=["revenue"],
        style={
            "columns": {
                "Jan": {"conditional": [{"gt": "avg()", "background": "#112233"}]}
            }
        },
    )
    with pytest.raises(ValueError, match="pivot"):
        resolve(chart, _ROWS, chart_style_context=_CTX)


def test_a_kpi_refuses_a_function() -> None:
    chart = KpiChart(
        id="k",
        query=SqlQuery(sql="SELECT 1", source="test"),
        query_name="q",
        type="kpi",
        value="total",
        style={
            "conditional": {
                "total": [{"gt": "avg()", "support": {"font": {"color": RED}}}]
            }
        },
    )
    with pytest.raises(ValueError, match="one row"):
        resolve(chart, [{"total": 1}], chart_style_context=_CTX)


def test_a_table_row_rule_is_computed_over_its_column() -> None:
    chart = TableChart(
        id="t",
        type="table",
        style={
            "row": {
                "conditional": {"revenue": [{"eq": "max()", "background": "#112233"}]}
            }
        },
    )
    resolved = resolve(chart, _ROWS, chart_style_context=_CTX)
    assert isinstance(resolved, ResolvedTableChart)
    rules = resolved.style.table.row.conditional
    assert rules is not None
    assert rules["revenue"][0].eq == 30
    svg = render_table_svg(resolved, _ROWS, width=600, board_style=_RENDER_STYLE)
    assert "#112233" in svg


_FAMILY_ROWS: list[dict[str, Any]] = [
    {"m": "Jan", "x": 1, "v": 10, "lat": 1.0, "lon": 2.0},
    {"m": "Feb", "x": 2, "v": 30, "lat": 3.0, "lon": 4.0},
]


@pytest.mark.parametrize(
    ("cls", "mark", "fields"),
    [
        (LineChart, "point", {"type": "line", "x": "m", "y": "v"}),
        (AreaChart, "point", {"type": "area", "x": "m", "y": "v"}),
        (ScatterChart, "point", {"type": "scatter", "x": "x", "y": "v"}),
        (PieChart, "slice", {"type": "pie", "color": "m", "theta": "v"}),
        (
            HeatmapChart,
            "rect",
            {"type": "heatmap", "x": "m", "y": "m", "color": "v"},
        ),
        (
            PointMapChart,
            "point",
            {
                "type": "point_map",
                "latitude": "lat",
                "longitude": "lon",
                "projection": "mercator",
            },
        ),
    ],
)
def test_every_family_carries_the_computed_literal(
    cls: type[Any], mark: str, fields: dict[str, Any]
) -> None:
    rule = {"gt": "avg()", "marks": {mark: {"opacity": 0.5}}}
    chart = cls(id="c", style={"conditional": {"v": [rule]}}, **fields)
    resolved = resolve(chart, _FAMILY_ROWS, chart_style_context=_CTX)
    style = resolved.style
    rules = (
        style.point_map.conditional
        if isinstance(style, ResolvedPointMapStyle)
        else style.conditional
    )
    assert rules["v"][0].gt == 20.0
