"""A bar chart's conditional rules live at ``style.conditional``.

Each rule is one predicate plus a bar chart style restricted to the fields marked
``Conditionable()``: ``marks.bar.{color, opacity, border}`` and
``marks.text.font.{color, weight}``. Rules evaluate in order, every matching rule
applies its keys, the later match wins per key, and ``default`` applies only when
nothing else matched. They bind the base layer, one bar (or stack segment) at a time.
"""

from __future__ import annotations

import re
from typing import Any

import pytest
import yaml
from pydantic import ValidationError

from dbt_charts.core.compile.config import get_theme_style
from dbt_charts.core.compile.models.board.authored import AuthoredBoard
from dbt_charts.core.compile.models.chart.normalized import BarChart
from dbt_charts.core.compile.models.factories import build_conditional_model
from dbt_charts.core.compile.models.style.theme.bar import _BarChartStyleBase
from dbt_charts.core.compile.resolve import resolve
from dbt_charts.core.compile.resolve.style.board import resolve_style_and_context

from ._engine_render import render_chart_svg

_BOARD_STYLE, _BOARD_CTX = resolve_style_and_context(get_theme_style())

RED = "#c62828"
GREEN = "#2e7d32"
BLUE = "#1565c0"

_ROWS: list[dict[str, Any]] = [
    {"region": "North", "profit": 40, "margin": 0.4, "tier": "gold"},
    {"region": "South", "profit": -25, "margin": -0.2, "tier": "tin"},
    {"region": "East", "profit": 10, "margin": 0.1, "tier": "tin"},
]


def _bar(conditional: dict[str, Any], **kwargs: Any) -> BarChart:
    style = kwargs.pop("style", {})
    return BarChart(
        id="b",
        type="bar",
        x=kwargs.pop("x", "region"),
        y=kwargs.pop("y", "profit"),
        style={"conditional": conditional, **style},
        **kwargs,
    )


def _resolve(conditional: dict[str, Any], data: list[dict[str, Any]], **kw: Any):
    return resolve(_bar(conditional, **kw), data, chart_style_context=_BOARD_CTX)


def _svg(conditional: dict[str, Any], data: list[dict[str, Any]], **kw: Any) -> str:
    return render_chart_svg(
        _bar(conditional, **kw),
        data,
        width=600,
        height=360,
        board_style=_BOARD_STYLE,
        chart_style_context=_BOARD_CTX,
    )


def _bar_fills(svg: str) -> list[str]:
    """The fill of each bar mark, in paint order."""
    return re.findall(r'<path[^>]*? fill="(#[0-9a-fA-F]{6})"[^>]*/>', svg) + re.findall(
        r'<rect[^>]*? fill="(#[0-9a-fA-F]{6})"[^>]*/>', svg
    )


def _bars(color: str) -> dict[str, Any]:
    return {"marks": {"bar": {"color": color}}}


def test_a_negative_bar_takes_the_rule_color_with_no_color_channel() -> None:
    svg = _svg({"profit": [{"lt": 0, **_bars(RED)}]}, _ROWS)
    assert svg.count(RED) == 1


def test_default_does_not_fire_beside_a_match() -> None:
    svg = _svg(
        {"profit": [{"lt": 0, **_bars(RED)}, {"default": True, **_bars(BLUE)}]},
        _ROWS,
    )
    assert svg.count(RED) == 1
    assert svg.count(BLUE) == 2


def test_overlapping_rules_apply_together_and_the_later_wins_per_key() -> None:
    svg = _svg(
        {
            "profit": [
                {"gt": 0, "marks": {"bar": {"color": RED, "opacity": 0.5}}},
                {"gt": 20, **_bars(GREEN)},
            ]
        },
        _ROWS,
    )
    assert svg.count(GREEN) == 1
    assert svg.count(RED) == 1
    assert svg.count('fill-opacity="0.5"') == 2


def test_a_rule_on_another_column_tests_that_columns_value() -> None:
    svg = _svg({"tier": [{"eq": "gold", **_bars(GREEN)}]}, _ROWS)
    assert svg.count(GREEN) == 1


def test_a_rule_on_two_columns_for_different_properties_coexists() -> None:
    svg = _svg(
        {
            "profit": [{"lt": 0, **_bars(RED)}],
            "tier": [{"eq": "gold", "marks": {"bar": {"opacity": 0.4}}}],
        },
        _ROWS,
    )
    assert svg.count(RED) == 1
    assert svg.count('fill-opacity="0.4"') == 1


def test_a_stacked_segment_is_tested_against_its_own_row() -> None:
    rows = [
        {"month": "Jan", "kind": "a", "amount": 5},
        {"month": "Jan", "kind": "b", "amount": -3},
        {"month": "Feb", "kind": "a", "amount": 7},
        {"month": "Feb", "kind": "b", "amount": 2},
    ]
    svg = _svg(
        {"amount": [{"lt": 0, **_bars(RED)}]},
        rows,
        x="month",
        y="amount",
        color="kind",
        style={"stack": "zero"},
    )
    assert svg.count(RED) == 1


def test_a_horizontal_bar_takes_the_rule_color() -> None:
    svg = _svg(
        {"profit": [{"lt": 0, **_bars(RED)}]},
        _ROWS,
        style={"orientation": "horizontal"},
    )
    assert svg.count(RED) == 1


def test_a_rule_font_color_paints_that_bars_value_label() -> None:
    svg = _svg(
        {
            "profit": [
                {
                    "lt": 0,
                    "marks": {"text": {"font": {"color": RED, "weight": "700"}}},
                }
            ]
        },
        _ROWS,
        style={"marks": {"bar": {"labels": {"visible": True}}}},
    )
    labels = re.findall(r"<text[^>]*>", svg)
    assert sum(RED in tag and 'font-weight="700"' in tag for tag in labels) == 1


def test_resolve_puts_the_rules_on_the_resolved_style() -> None:
    resolved = _resolve({"profit": [{"lt": 0, **_bars(RED)}]}, _ROWS)
    rule = resolved.style.conditional["profit"][0]
    assert rule.marks.bar.color == RED


def test_color_tokens_in_a_rule_resolve() -> None:
    resolved = _resolve({"profit": [{"lt": 0, **_bars("category[1]")}]}, _ROWS)
    assert resolved.style.conditional["profit"][0].marks.bar.color.startswith("#")


def test_an_unknown_column_is_refused_by_name() -> None:
    with pytest.raises(ValueError, match="nope.*not in the query result"):
        _resolve({"nope": [{"eq": 1, **_bars(RED)}]}, _ROWS)


def test_two_columns_patching_the_same_property_are_refused_naming_both() -> None:
    with pytest.raises(ValueError, match=r"profit.*tier.*marks\.bar\.color"):
        _resolve(
            {
                "profit": [{"lt": 0, **_bars(RED)}],
                "tier": [{"eq": "gold", **_bars(GREEN)}],
            },
            _ROWS,
        )


_WIDE_ROWS: list[dict[str, Any]] = [
    {"region": "North", "q1": 40, "q2": 30, "tier": "gold"},
    {"region": "South", "q1": -25, "q2": 15, "tier": "tin"},
]


def test_a_rule_on_a_plotted_column_restyles_only_its_own_bars() -> None:
    svg = _svg({"q1": [{"lt": 0, **_bars(RED)}]}, _WIDE_ROWS, y=["q1", "q2"])
    assert _bar_fills(svg).count(RED) == 1


def test_rules_on_two_plotted_columns_may_set_the_same_property() -> None:
    rules = {"q1": [{"lt": 0, **_bars(RED)}], "q2": [{"gt": 20, **_bars(RED)}]}
    svg = _svg(rules, _WIDE_ROWS, y=["q1", "q2"])
    assert _bar_fills(svg).count(RED) == 2


def test_a_plotted_and_an_unplotted_column_may_not_set_one_property() -> None:
    rules = {"q1": [{"lt": 0, **_bars(RED)}], "tier": [{"eq": "gold", **_bars(GREEN)}]}
    with pytest.raises(ValueError, match=r"q1.*tier.*marks\.bar\.color"):
        _resolve(rules, _WIDE_ROWS, y=["q1", "q2"])


def test_the_rule_type_keeps_only_conditionable_fields() -> None:
    body = build_conditional_model(_BarChartStyleBase)
    assert set(body.model_fields) == {
        "marks",
        *("eq", "ne", "lt", "lte", "gt", "gte", "between", "in_", "is_null", "default"),
    }
    marks = body.model_fields["marks"].annotation
    assert set(marks.model_fields) == {"bar", "text"}
    bar = marks.model_fields["bar"].annotation.__args__[0]
    assert set(bar.model_fields) == {"color", "opacity", "border"}
    border = bar.model_fields["border"].annotation.__args__[0]
    assert set(border.model_fields) == {"color", "width"}
    text = marks.model_fields["text"].annotation.__args__[0]
    font = text.model_fields["font"].annotation.__args__[0]
    assert set(font.model_fields) == {"color", "weight", "extends"}


@pytest.mark.parametrize(
    "body",
    [
        {"orientation": "horizontal"},
        {"marks": {"bar": {"band_width": 0.5}}},
        {"marks": {"bar": {"labels": {"visible": True}}}},
        {"marks": {"text": {"font": {"size": 12}}}},
    ],
    ids=["orientation", "band-width", "labels", "font-size"],
)
def test_a_style_that_cannot_vary_per_row_is_refused(body: dict[str, Any]) -> None:
    with pytest.raises(ValidationError, match="extra_forbidden|Extra inputs"):
        _bar({"profit": [{"lt": 0, **body}]})


def test_a_rule_with_no_style_is_refused() -> None:
    with pytest.raises(ValidationError, match=r"(?s)0\.marks.*Field required"):
        _bar({"profit": [{"lt": 0}]})


def test_default_must_be_last() -> None:
    with pytest.raises(ValidationError, match="must be the last entry"):
        _bar({"profit": [{"default": True, **_bars(RED)}, {"lt": 0, **_bars(BLUE)}]})


_BOARD = """title: T
queries:
  q1: {{sql: "select 1 as profit", source: test}}
{board_style}charts:
  c1:
    type: bar
    query: q1
    x: profit
    y: profit
{chart_style}rows: [c1]
"""
_CONDITIONAL = """conditional:
  profit:
    - lt: 0
      marks:
        bar:
          color: "#c62828"
"""


def _indent(text: str, spaces: int) -> str:
    pad = " " * spaces
    return "".join(f"{pad}{line}\n" for line in text.splitlines())


def test_theme_or_board_scope_refuses_conditional() -> None:
    board_style = "style:\n  charts:\n    bar:\n" + _indent(_CONDITIONAL, 6)
    doc = yaml.safe_load(_BOARD.format(board_style=board_style, chart_style=""))
    with pytest.raises(
        ValidationError, match=r"`conditional` is chart-local.*style\.charts\.bar"
    ):
        AuthoredBoard.model_validate(doc)


def test_chart_scope_accepts_conditional() -> None:
    chart_style = "    style:\n" + _indent(_CONDITIONAL, 6)
    doc = yaml.safe_load(_BOARD.format(board_style="", chart_style=chart_style))
    AuthoredBoard.model_validate(doc)


def test_the_bar_rule_type_publishes_its_given_name() -> None:
    assert (
        build_conditional_model(
            _BarChartStyleBase, name="BarChartStyleConditional"
        ).__name__
        == "BarChartStyleConditional"
    )
