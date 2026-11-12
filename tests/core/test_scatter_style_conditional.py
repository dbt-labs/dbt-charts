"""Scatter and point-map conditional rules live at ``style.conditional``.

Each rule is one predicate plus the ``Conditionable()`` part of the chart style:
``marks.point.{color, fill, filled, size, shape, opacity, stroke_width}`` and, on
scatter, ``marks.text.font.{color, weight}``. Every matching rule applies, the later
match wins per key, and ``default`` applies only when nothing else matched.
"""

from __future__ import annotations

import re
from typing import Any

import pytest
from pydantic import ValidationError

from dbt_charts.core.compile.config import get_theme_style
from dbt_charts.core.compile.models.chart.normalized import PointMapChart, ScatterChart
from dbt_charts.core.compile.models.factories import build_conditional_model
from dbt_charts.core.compile.models.style.theme.point_map import (
    _PointMapChartStyleBase,
)
from dbt_charts.core.compile.models.style.theme.scatter import _ScatterChartStyleBase
from dbt_charts.core.compile.resolve import resolve
from dbt_charts.core.compile.resolve.style.board import resolve_style_and_context

from ._engine_render import render_chart_svg

_BOARD_STYLE, _BOARD_CTX = resolve_style_and_context(get_theme_style())

RED = "#c62828"
GREEN = "#2e7d32"

_ROWS: list[dict[str, Any]] = [
    {"spend": 10, "revenue": 100, "score": 1, "lat": 40.7, "lon": -74.0, "tier": "a"},
    {"spend": 20, "revenue": 180, "score": 9, "lat": 34.0, "lon": -118.2, "tier": "b"},
    {"spend": 30, "revenue": 220, "score": 3, "lat": 41.9, "lon": -87.6, "tier": "b"},
]


def _scatter(conditional: dict[str, Any], **kwargs: Any) -> ScatterChart:
    style = kwargs.pop("style", {})
    return ScatterChart(
        id="s",
        type="scatter",
        x="spend",
        y=kwargs.pop("y", "revenue"),
        style={"conditional": conditional, **style},
        **kwargs,
    )


def _point_map(
    conditional: dict[str, Any], chart_type: str = "point_map", **kwargs: Any
) -> PointMapChart:
    return PointMapChart(
        id="m",
        type=chart_type,
        latitude="lat",
        longitude="lon",
        projection="mercator",
        style={"conditional": conditional},
        **kwargs,
    )


def _svg(chart: Any) -> str:
    return render_chart_svg(
        chart,
        _ROWS,
        width=600,
        height=360,
        board_style=_BOARD_STYLE,
        chart_style_context=_BOARD_CTX,
    )


def _resolve(chart: Any) -> Any:
    return resolve(chart, _ROWS, chart_style_context=_BOARD_CTX)


def _point(**props: Any) -> dict[str, Any]:
    return {"marks": {"point": props}}


def _marks(svg: str) -> list[str]:
    """The opening tag of each data point mark, in paint order."""
    return re.findall(
        r'<path[^>]*aria-roledescription="(?:point|circle)"[^>]*>', svg
    ) or re.findall(r'<path[^>]*class="[^"]*mark[^"]*"[^>]*>', svg)


def test_matched_points_take_color_size_and_shape() -> None:
    inert = _svg(_scatter({"score": [{"gt": 100, **_point(color=RED)}]}))
    svg = _svg(
        _scatter({"score": [{"gt": 5, **_point(color=RED, size=400, shape="diamond")}]})
    )
    assert inert.count(RED) == 0
    assert svg.count(f'fill="{RED}"') == 1


def test_default_restyles_the_rest_and_not_a_match() -> None:
    svg = _svg(
        _scatter(
            {
                "score": [
                    {"gt": 5, **_point(color=RED)},
                    {"default": True, **_point(opacity=0.4)},
                ]
            }
        )
    )
    assert svg.count('opacity="0.4"') == 2
    assert svg.count(RED) == 1


def test_overlapping_rules_paint_the_later_value() -> None:
    svg = _svg(
        _scatter(
            {
                "score": [
                    {"gt": 0, **_point(color=RED)},
                    {"gt": 5, **_point(color=GREEN)},
                ]
            }
        )
    )
    assert svg.count(GREEN) == 1
    assert svg.count(RED) == 2


def test_a_rule_size_overrides_an_authored_size_channel() -> None:
    hit = _svg(_scatter({"score": [{"gt": 5, **_point(size=2000)}]}, size="score"))
    miss = _svg(_scatter({"score": [{"gt": 100, **_point(size=2000)}]}, size="score"))
    assert hit != miss


def test_a_rule_shape_overrides_an_authored_shape_channel() -> None:
    hit = _svg(
        _scatter({"score": [{"gt": 5, **_point(shape="diamond")}]}, shape="tier")
    )
    miss = _svg(
        _scatter({"score": [{"gt": 100, **_point(shape="diamond")}]}, shape="tier")
    )
    assert hit != miss


def test_filled_false_with_a_fill_draws_a_ring() -> None:
    svg = _svg(
        _scatter(
            {
                "score": [
                    {
                        "gt": 5,
                        **_point(
                            filled=False, fill="#ffffff", color=RED, stroke_width=3
                        ),
                    }
                ]
            }
        )
    )
    assert f'stroke="{RED}"' in svg
    assert 'stroke-width="3"' in svg


def test_a_rule_font_paints_the_point_value_label() -> None:
    rule = {
        "score": [
            {"gt": 5, "marks": {"text": {"font": {"color": RED, "weight": "700"}}}}
        ]
    }
    svg = _svg(
        _scatter(rule, style={"marks": {"point": {"labels": {"visible": True}}}})
    )
    labels = re.findall(r"<text[^>]*>", svg)
    assert sum(RED in tag and 'font-weight="700"' in tag for tag in labels) == 1


@pytest.mark.parametrize("prop", ["color", "size", "shape", "opacity"])
def test_auto_is_not_a_rule_value(prop: str) -> None:
    with pytest.raises(ValidationError):
        _scatter({"score": [{"gt": 1, **_point(**{prop: "auto"})}]})


def test_point_labels_in_a_rule_are_refused() -> None:
    with pytest.raises(ValidationError, match="extra_forbidden|Extra inputs"):
        _scatter({"score": [{"gt": 1, **_point(labels={"visible": True})}]})


def test_resolve_puts_the_rules_on_the_resolved_style() -> None:
    resolved = _resolve(_scatter({"score": [{"gt": 5, **_point(color="category[1]")}]}))
    assert resolved.style.conditional["score"][0].marks.point.color.startswith("#")


def test_an_unknown_column_is_refused_by_name() -> None:
    with pytest.raises(ValueError, match="nope.*not in the query result"):
        _resolve(_scatter({"nope": [{"eq": 1, **_point(color=RED)}]}))


def test_two_columns_patching_the_same_property_are_refused() -> None:
    with pytest.raises(ValueError, match=r"score.*tier.*marks\.point\.color"):
        _resolve(
            _scatter(
                {
                    "score": [{"gt": 5, **_point(color=RED)}],
                    "tier": [{"eq": "a", **_point(color=GREEN)}],
                }
            )
        )


def test_a_rule_on_a_plotted_column_restyles_only_its_own_series() -> None:
    chart = _scatter(
        {"revenue": [{"gt": 150, **_point(color=RED)}]}, y=["revenue", "score"]
    )
    assert sum(RED in tag for tag in _marks(_svg(chart))) == 2


def test_the_rule_types_keep_only_conditionable_fields() -> None:
    point = {"color", "fill", "filled", "size", "shape", "opacity", "stroke_width"}
    scatter = build_conditional_model(_ScatterChartStyleBase, name="ScatterChartStyle")
    marks = scatter.model_fields["marks"].annotation
    assert set(marks.model_fields) == {"point", "text"}
    kept = marks.model_fields["point"].annotation.__args__[0]
    assert set(kept.model_fields) == point
    geo = build_conditional_model(_PointMapChartStyleBase, name="PointMapChartStyle")
    marks = geo.model_fields["marks"].annotation
    assert set(marks.model_fields) == {"point"}


def test_point_map_matched_points_take_the_rule() -> None:
    svg = _svg(
        _point_map(
            {"score": [{"gt": 5, **_point(color=RED, size=900, shape="square")}]}
        )
    )
    assert svg.count(f'fill="{RED}"') == 1


def test_bubble_map_takes_the_same_rules() -> None:
    chart = _point_map(
        {"tier": [{"eq": "a", **_point(color=RED)}]},
        chart_type="bubble_map",
        size="score",
    )
    assert _svg(chart).count(f'fill="{RED}"') == 1


def test_point_map_default_restyles_the_rest() -> None:
    svg = _svg(
        _point_map(
            {
                "score": [
                    {"gt": 5, **_point(color=RED)},
                    {"default": True, **_point(opacity=0.4)},
                ]
            }
        )
    )
    assert svg.count('opacity="0.4"') == 2


def test_point_map_refuses_collapse_with_rules() -> None:
    with pytest.raises(ValueError, match="collapse"):
        _resolve(_point_map({"score": [{"gt": 5, **_point(color=RED)}]}, collapse=True))


def test_point_map_has_no_text_rules() -> None:
    with pytest.raises(ValidationError, match="extra_forbidden|Extra inputs"):
        _point_map({"score": [{"gt": 5, "marks": {"text": {"font": {"color": RED}}}}]})
