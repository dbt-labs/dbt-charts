"""Pie, heatmap and map charts restyle a mark by its row with ``style.conditional``.

A rule is one predicate plus the part of the family style that can vary per datum:
``marks.slice|rect.{color, opacity, stroke.{color, width, dasharray}}``,
``marks.geoshape.{fill, stroke.{color, width, dasharray}}`` and ``marks.text.font.{color, weight}``.
A rule's color overrides an authored color channel for the matched datum only.
"""

from __future__ import annotations

import re
from typing import Any

import pytest
import yaml
from pydantic import ValidationError

from dbt_charts.core.compile import compile
from dbt_charts.core.compile.models.board.authored import AuthoredBoard
from dbt_charts.core.compile.models.factories import build_conditional_model
from dbt_charts.core.compile.models.style.theme.geoshape import _GeoshapeChartStyleBase
from dbt_charts.core.compile.models.style.theme.heatmap import _HeatmapChartStyleBase
from dbt_charts.core.compile.models.style.theme.pie import _PieChartStyleBase

from ._svg_render import render_board_svg_result

BLUE = "#1f4e99"
RED = "#c62828"

_PIE_ROWS = [
    {"region": "EMEA", "rev": 5},
    {"region": "APAC", "rev": 3},
    {"region": "NA", "rev": 9},
]
_HEAT_ROWS = [
    {"day": "Mon", "hour": "a", "n": 1},
    {"day": "Tue", "hour": "a", "n": 5},
    {"day": "Mon", "hour": "b", "n": 9},
    {"day": "Tue", "hour": "b", "n": 2},
]
_MAP_ROWS = [
    {"state_code": "9", "pop": 742},
    {"state_code": "23", "pop": 44},
    {"state_code": "25", "pop": 901},
]
_PIE = {"type": "pie", "theta": "rev", "color": "region"}
_HEAT = {"type": "heatmap", "x": "day", "y": "hour", "color": "n"}
_MAP = {
    "type": "map",
    "geo": {"source": "us-states"},
    "lookup": "state_code",
    "value": "pop",
}


def _board(
    chart: dict[str, Any], rows: list[dict[str, Any]], style: dict[str, Any]
) -> str:
    return yaml.safe_dump(
        {
            "title": "T",
            "queries": {"q": {"type": "values", "rows": rows}},
            "charts": {"c": {"query": "q", **chart, "style": style}},
            "rows": ["c"],
        },
        sort_keys=False,
    )


def _svg(
    chart: dict[str, Any], rows: list[dict[str, Any]], style: dict[str, Any]
) -> str:
    result = render_board_svg_result(_board(chart, rows, style))
    assert not result.chart_errors
    return result.output


def _tags(svg: str, role: str) -> list[str]:
    return re.findall(rf'<[a-z]+ [^>]*data-dct-role="{role}"[^>]*>', svg)


def _geo_tags(svg: str) -> list[str]:
    return re.findall(r'<path [^>]*aria-roledescription="geoshape"[^>]*>', svg)


def _pie_style(**marks: Any) -> dict[str, Any]:
    return {"conditional": {"region": [{"eq": "EMEA", "marks": marks}]}}


def test_a_matched_slice_takes_the_rule_color_beside_a_color_channel() -> None:
    svg = _svg(_PIE, _PIE_ROWS, _pie_style(slice={"color": BLUE}))
    arcs = _tags(svg, "arc")
    assert sum(BLUE in arc for arc in arcs) == 1
    assert "EMEA" in next(a for a in arcs if BLUE in a)


def test_a_slice_rule_sets_opacity_and_stroke() -> None:
    svg = _svg(
        _PIE,
        _PIE_ROWS,
        _pie_style(slice={"opacity": 0.5, "stroke": {"color": RED, "width": 4}}),
    )
    (arc,) = [a for a in _tags(svg, "arc") if "EMEA" in a]
    assert f'stroke="{RED}"' in arc
    assert 'stroke-width="4"' in arc
    assert "0.5" in arc


def _emea_label(svg: str) -> str:
    return next(t for t in _tags(svg, "text") if "EMEA" in t)


def test_slice_label_ink_follows_the_patched_fill() -> None:
    plain = _svg(_PIE, _PIE_ROWS, {})
    patched = _svg(_PIE, _PIE_ROWS, _pie_style(slice={"color": BLUE}))
    assert _emea_label(patched) != _emea_label(plain)


def test_a_rule_font_color_and_weight_paint_the_slice_label() -> None:
    svg = _svg(
        _PIE,
        _PIE_ROWS,
        _pie_style(
            slice={"color": BLUE}, text={"font": {"color": RED, "weight": "700"}}
        ),
    )
    assert f'fill="{RED}"' in _emea_label(svg)
    assert 'font-weight="700"' in _emea_label(svg)


def test_a_donut_takes_the_same_rule() -> None:
    svg = _svg({**_PIE, "type": "donut"}, _PIE_ROWS, _pie_style(slice={"color": BLUE}))
    assert sum(BLUE in arc for arc in _tags(svg, "arc")) == 1


def test_a_heatmap_cell_over_the_threshold_gets_a_stroke() -> None:
    rule = {"gte": 9, "marks": {"rect": {"stroke": {"color": RED, "width": 3}}}}
    svg = _svg(_HEAT, _HEAT_ROWS, {"conditional": {"n": [rule]}})
    stroked = [r for r in _tags(svg, "rect") if f'stroke="{RED}"' in r]
    assert len(stroked) == 1
    assert "N: 9" in stroked[0]
    assert 'stroke-width="3"' in stroked[0]


def test_a_heatmap_rule_color_and_opacity_override_the_gradient_for_that_cell() -> None:
    rule = {"lt": 2, "marks": {"rect": {"color": BLUE, "opacity": 0.4}}}
    svg = _svg(_HEAT, _HEAT_ROWS, {"conditional": {"n": [rule]}})
    (cell,) = [r for r in _tags(svg, "rect") if f'fill="{BLUE}"' in r]
    assert "N: 1" in cell
    assert 'opacity="0.4"' in cell


def test_a_map_region_fill_and_stroke_are_overridden() -> None:
    rule = {
        "eq": "9",
        "marks": {"geoshape": {"fill": BLUE, "stroke": {"color": RED, "width": 2}}},
    }
    svg = _svg(_MAP, _MAP_ROWS, {"conditional": {"state_code": [rule]}})
    (region,) = [g for g in _geo_tags(svg) if f'fill="{BLUE}"' in g]
    assert "State code: 9" in region
    assert f'stroke="{RED}"' in region
    assert 'stroke-width="2"' in region


def _refusal(text: str) -> str:
    try:
        result = render_board_svg_result(text)
    except (AssertionError, ValueError) as exc:
        return str(exc)
    return " ".join(str(error) for error in result.chart_errors)


def test_an_unknown_column_is_refused_by_name() -> None:
    style = {"conditional": {"nope": [{"eq": 1, "marks": {"slice": {"color": BLUE}}}]}}
    assert "nope" in _refusal(_board(_PIE, _PIE_ROWS, style))


def test_two_columns_patching_one_property_are_refused() -> None:
    style = {
        "conditional": {
            "region": [{"eq": "EMEA", "marks": {"slice": {"color": BLUE}}}],
            "rev": [{"gt": 4, "marks": {"slice": {"color": RED}}}],
        }
    }
    assert "marks.slice.color" in _refusal(_board(_PIE, _PIE_ROWS, style))


def test_a_heatmap_rule_cannot_style_text_the_chart_never_draws() -> None:
    rule = {"gte": 9, "marks": {"text": {"font": {"color": RED}}}}
    message = _refusal(_board(_HEAT, _HEAT_ROWS, {"conditional": {"n": [rule]}}))
    assert "marks.text.font.color" in message


@pytest.mark.parametrize(
    ("base", "marks"),
    [
        (_PieChartStyleBase, {"slice", "text"}),
        (_HeatmapChartStyleBase, {"rect", "text"}),
        (_GeoshapeChartStyleBase, {"geoshape"}),
    ],
    ids=["pie", "heatmap", "geoshape"],
)
def test_the_rule_type_keeps_only_conditionable_marks(
    base: type, marks: set[str]
) -> None:
    body = build_conditional_model(base, name="X")
    marks_model = body.model_fields["marks"].annotation
    assert set(marks_model.model_fields) == marks


def test_the_rule_type_keeps_only_per_datum_slice_fields() -> None:
    body = build_conditional_model(_PieChartStyleBase, name="X")
    marks_model = body.model_fields["marks"].annotation
    slice_model = marks_model.model_fields["slice"].annotation.__args__[0]
    assert set(slice_model.model_fields) == {"color", "opacity", "stroke"}
    stroke = slice_model.model_fields["stroke"].annotation.__args__[0]
    assert set(stroke.model_fields) == {"color", "width", "dasharray"}


@pytest.mark.parametrize(
    ("chart", "rows", "column", "marks"),
    [
        (_PIE, _PIE_ROWS, "region", {"slice": {"gap": 0.1}}),
        (_PIE, _PIE_ROWS, "region", {"slice": {"corner_radius": 2}}),
        (_PIE, _PIE_ROWS, "region", {"slice": {"labels": {"visible": True}}}),
        (_HEAT, _HEAT_ROWS, "n", {"rect": {"stroke": {"cap": "round"}}}),
        (_MAP, _MAP_ROWS, "pop", {"geoshape": {"stroke": {"cap": "round"}}}),
        (_PIE, _PIE_ROWS, "region", {"text": {"font": {"size": 12}}}),
    ],
    ids=["gap", "corner-radius", "labels", "rect-cap", "geo-cap", "font-size"],
)
def test_a_property_that_cannot_vary_per_datum_is_refused(
    chart: dict[str, Any],
    rows: list[dict[str, Any]],
    column: str,
    marks: dict[str, Any],
) -> None:
    style = {"conditional": {column: [{"eq": "x", "marks": marks}]}}
    assert not compile(_board(chart, rows, style)).success


def test_a_rule_with_no_style_is_refused() -> None:
    style = {"conditional": {"region": [{"eq": "EMEA"}]}}
    assert not compile(_board(_PIE, _PIE_ROWS, style)).success


@pytest.mark.parametrize(
    ("family", "marks"),
    [
        ("pie", {"slice": {"color": BLUE}}),
        ("heatmap", {"rect": {"color": BLUE}}),
        ("geoshape", {"geoshape": {"fill": BLUE}}),
    ],
)
def test_board_scope_refuses_conditional(family: str, marks: dict[str, Any]) -> None:
    doc = yaml.safe_load(_board(_PIE, _PIE_ROWS, {}))
    doc["style"] = {
        "charts": {
            family: {"conditional": {"region": [{"eq": "EMEA", "marks": marks}]}}
        }
    }
    with pytest.raises(ValidationError, match="chart-local"):
        AuthoredBoard.model_validate(doc)


def test_a_rule_slice_on_a_transparent_chart_inks_its_label_against_the_canvas() -> (
    None
):
    svg = _svg(
        _PIE,
        _PIE_ROWS,
        {
            "background": "transparent",
            "conditional": {
                "region": [{"eq": "EMEA", "marks": {"slice": {"color": BLUE}}}]
            },
        },
    )
    assert BLUE in svg
