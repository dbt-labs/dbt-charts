"""A line or area chart's conditional rules live at ``style.conditional``.

Only what can vary along a series rides a rule: ``marks.point.*`` (both families), on a
line ``marks.text.font.{color, weight}`` for the value labels and ``marks.line.stroke``
``{color, width, dasharray}``, on an area ``marks.area.{color, opacity}``. Whole-series
properties (``curve``, ``connect``, ``stacked``, labels, ``marks.rule``) are absent from
the rule type. Rules evaluate against each point's own row.

A segment between two neighboring drawn points of a series takes a rule's line or area
style key by key, only where both endpoints resolve the same value for the key.
"""

from __future__ import annotations

import re
from typing import Any

import pytest
from pydantic import ValidationError

from dbt_charts.core.compile.config import get_theme_style
from dbt_charts.core.compile.errors import CompilationError
from dbt_charts.core.compile.models.chart.normalized import AreaChart, LineChart
from dbt_charts.core.compile.models.factories import build_conditional_model
from dbt_charts.core.compile.models.style.theme.area import AreaChartStyle
from dbt_charts.core.compile.models.style.theme.line import LineChartStyle
from dbt_charts.core.compile.resolve import resolve
from dbt_charts.core.compile.resolve.style.board import resolve_style_and_context
from dbt_charts.core.diagnostics.chart_data import ChartDataError

from ._engine_render import render_chart_svg

_BOARD_STYLE, _BOARD_CTX = resolve_style_and_context(get_theme_style())

GRAY = "#8a8a8a"
RED = "#c62828"

_ROWS: list[dict[str, Any]] = [
    {"month": "2024-01-01", "revenue": 10, "is_forecast": False},
    {"month": "2024-02-01", "revenue": 14, "is_forecast": False},
    {"month": "2024-03-01", "revenue": 12, "is_forecast": True},
    {"month": "2024-04-01", "revenue": 18, "is_forecast": True},
]
_SERIES_ROWS: list[dict[str, Any]] = [
    {"month": m, "revenue": v, "team": team, "is_forecast": f}
    for team, shift in (("a", 0), ("b", 5))
    for m, v, f in (
        ("2024-01-01", 10 + shift, False),
        ("2024-02-01", 14 + shift, False),
        ("2024-03-01", 12 + shift, team == "a"),
    )
]
_POINTS = {"marks": {"point": {"size": 60}}}


def _chart(kind: str, conditional: dict[str, Any], **kwargs: Any) -> Any:
    style = kwargs.pop("style", _POINTS)
    cls = LineChart if kind == "line" else AreaChart
    return cls(
        id="c",
        type=kind,
        x="month",
        y=kwargs.pop("y", "revenue"),
        style={"conditional": conditional, **style},
        **kwargs,
    )


def _svg(kind: str, conditional: dict[str, Any], data: Any = _ROWS, **kw: Any) -> str:
    return render_chart_svg(
        _chart(kind, conditional, **kw),
        data,
        width=600,
        height=360,
        board_style=_BOARD_STYLE,
        chart_style_context=_BOARD_CTX,
    )


def _marks(svg: str) -> list[str]:
    return re.findall(r"<(?:circle|path)[^>]*?/>", svg)


def _forecast(**point: Any) -> dict[str, Any]:
    return {"is_forecast": [{"eq": True, "marks": {"point": point}}]}


@pytest.mark.parametrize("kind", ["line", "area"])
def test_forecast_points_take_the_rule_opacity(kind: str) -> None:
    svg = _svg(kind, _forecast(opacity=0.4))
    assert svg.count('opacity="0.4"') == 2


@pytest.mark.parametrize("kind", ["line", "area"])
def test_a_rule_color_paints_only_the_matching_points(kind: str) -> None:
    svg = _svg(kind, _forecast(color=RED))
    assert sum(RED in tag for tag in _marks(svg)) == 2


def test_a_rule_size_grows_only_the_matching_points() -> None:
    svg = _svg("line", _forecast(size=400))
    radii = set(re.findall(r'<circle[^>]*? r="([\d.]+)"', svg))
    assert len(radii) >= 2


def test_a_rule_size_draws_points_where_the_chart_has_none() -> None:
    svg = _svg("line", _forecast(size=100), style={"marks": {"point": {"size": 0}}})
    assert len(re.findall(r'<circle[^>]*opacity="1"', svg)) == 2


def test_a_hollow_rule_point_is_a_ring() -> None:
    svg = _svg("line", _forecast(filled=False, stroke_width=3))
    assert sum('stroke-width="3"' in tag for tag in _marks(svg)) == 2


def test_a_square_rule_shape_replaces_the_circle() -> None:
    svg = _svg("line", _forecast(shape="square"))
    assert len(re.findall(r'<path[^>]*opacity="1"', svg)) == 2


def test_each_point_of_a_series_is_tested_against_its_own_row() -> None:
    svg = _svg("line", _forecast(color=RED), data=_SERIES_ROWS, color="team")
    assert sum(RED in tag for tag in _marks(svg)) == 1


def test_a_rule_font_color_paints_that_points_value_label() -> None:
    svg = _svg(
        "line",
        {
            "is_forecast": [
                {
                    "eq": True,
                    "marks": {"text": {"font": {"color": GRAY, "weight": "700"}}},
                }
            ]
        },
        style={"marks": {"line": {"labels": {"visible": True}}}},
    )
    labels = re.findall(r"<text[^>]*>", svg)
    assert sum(GRAY in tag and 'font-weight="700"' in tag for tag in labels) == 2


_WIDE_ROWS: list[dict[str, Any]] = [
    {"month": "2024-01-01", "revenue": 10, "cost": 4, "is_forecast": False},
    {"month": "2024-02-01", "revenue": 20, "cost": 6, "is_forecast": False},
    {"month": "2024-03-01", "revenue": 12, "cost": 30, "is_forecast": True},
]


def _wide_red(conditional: dict[str, Any], kind: str = "line") -> int:
    svg = _svg(kind, conditional, data=_WIDE_ROWS, y=["revenue", "cost"])
    return sum(RED in tag for tag in _marks(svg))


@pytest.mark.parametrize("kind", ["line", "area"])
def test_a_rule_on_a_plotted_column_restyles_only_its_own_series(kind: str) -> None:
    rule = {"revenue": [{"gt": 15, "marks": {"point": {"color": RED}}}]}
    assert _wide_red(rule, kind) == 1


def test_rules_on_two_plotted_columns_may_set_the_same_property() -> None:
    red = {"marks": {"point": {"color": RED}}}
    rules = {"revenue": [{"gt": 15, **red}], "cost": [{"gt": 25, **red}]}
    assert _wide_red(rules) == 2


def test_a_rule_on_an_unplotted_column_restyles_every_series_at_its_x() -> None:
    assert _wide_red(_forecast(color=RED)) == 2


@pytest.mark.parametrize("kind", ["line", "area"])
def test_resolve_puts_the_rules_on_the_resolved_style(kind: str) -> None:
    resolved = resolve(
        _chart(kind, _forecast(color=RED)), _ROWS, chart_style_context=_BOARD_CTX
    )
    assert resolved.style.conditional["is_forecast"][0].marks.point.color == RED


@pytest.mark.parametrize("kind", ["line", "area"])
def test_an_unknown_column_is_refused_by_name(kind: str) -> None:
    with pytest.raises(ValueError, match="nope.*not in the query result"):
        resolve(
            _chart(kind, {"nope": [{"eq": 1, "marks": {"point": {"size": 9}}}]}),
            _ROWS,
            chart_style_context=_BOARD_CTX,
        )


def _inner(model: Any, field: str) -> Any:
    return model.model_fields[field].annotation.__args__[0]


def test_the_line_rule_type_keeps_point_text_font_and_the_line_stroke() -> None:
    body = build_conditional_model(LineChartStyle, name="LineChartStyle")
    marks = body.model_fields["marks"].annotation
    assert set(marks.model_fields) == {"line", "point", "text"}
    point = _inner(marks, "point")
    assert set(point.model_fields) == {
        "color",
        "fill",
        "filled",
        "size",
        "shape",
        "opacity",
        "stroke_width",
    }
    font = _inner(_inner(marks, "text"), "font")
    assert set(font.model_fields) == {"color", "weight", "extends"}
    line = _inner(marks, "line")
    assert set(line.model_fields) == {"stroke"}
    assert set(_inner(line, "stroke").model_fields) == {"color", "width", "dasharray"}


def test_the_area_rule_type_keeps_point_the_fill_and_the_edge_stroke() -> None:
    body = build_conditional_model(AreaChartStyle, name="AreaChartStyle")
    marks = body.model_fields["marks"].annotation
    assert set(marks.model_fields) == {"area", "line", "point"}
    assert set(_inner(marks, "area").model_fields) == {"color", "opacity"}
    line = _inner(marks, "line")
    assert set(line.model_fields) == {"stroke"}
    assert set(_inner(line, "stroke").model_fields) == {"color", "width", "dasharray"}


@pytest.mark.parametrize("kind", ["line", "area"])
@pytest.mark.parametrize(
    "marks",
    [
        {"line": {"curve": "step"}},
        {"line": {"connect": False}},
        {"line": {"stroke": {"cap": "butt"}}},
        {"line": {"opacity": 0.5}},
        {"line": {"labels": {"visible": True}}},
        {"line": {"halo_multiplier": 2}},
        {"area": {"curve": "step"}},
        {"area": {"stacked": {"opacity": 0.5}}},
        {"area": {"backdrop": True}},
        {"rule": {"stroke": {"width": 3}}},
        {"point": {"labels": {"visible": True}}},
    ],
    ids=[
        "curve",
        "connect",
        "cap",
        "line-opacity",
        "line-labels",
        "halo",
        "area-curve",
        "stacked",
        "backdrop",
        "rule",
        "point-labels",
    ],
)
def test_a_whole_series_property_is_refused(kind: str, marks: dict[str, Any]) -> None:
    with pytest.raises(ValidationError, match="extra_forbidden|Extra inputs"):
        _chart(kind, {"is_forecast": [{"eq": True, "marks": marks}]})


def test_a_line_rule_takes_no_area() -> None:
    with pytest.raises(ValidationError, match="extra_forbidden|Extra inputs"):
        _chart(
            "line", {"is_forecast": [{"eq": True, "marks": {"area": {"color": RED}}}]}
        )


_PATH = re.compile(r"<path\b[^>]*>")
_NUMBER = re.compile(r"-?\d+(?:\.\d+)?")
WHITE = "#ffffff"
# Six months; revenue 10, 14, 12, 18, 15, 20; the last four are forecast.
_RUN_ROWS: list[dict[str, Any]] = [
    {"month": f"2024-0{m}-01", "revenue": v, "is_forecast": m >= 3}
    for m, v in enumerate([10, 14, 12, 18, 15, 20], 1)
]


def _attr(tag: str, name: str) -> str | None:
    found = re.search(rf' {name}="([^"]*)"', tag)
    return found and found.group(1)


def _line_tags(svg: str) -> list[str]:
    """The stroked, unfilled paths: every halo and line run, in paint order.

    A knockout mask redraws the stack's outer edge; it paints nothing, so it is not counted.
    """
    drawn = re.sub(r"<mask.*?</mask>", "", svg, flags=re.S)
    return [t for t in _PATH.findall(drawn) if _attr(t, "fill") == "none"]


def _runs(svg: str, color: str) -> list[str]:
    return [t for t in _line_tags(svg) if _attr(t, "stroke") == color]


def _base_lines(svg: str) -> list[str]:
    """The line runs drawn in the series color (not the canvas halo)."""
    return [t for t in _line_tags(svg) if _attr(t, "stroke") not in (WHITE, GRAY)]


def _coords(tag: str) -> list[float]:
    return [float(n) for n in _NUMBER.findall(_attr(tag, "d") or "")]


def _stroke_rule(column: str = "is_forecast", **stroke: Any) -> dict[str, Any]:
    return {column: [{"eq": True, "marks": {"line": {"stroke": stroke}}}]}


def test_a_run_of_matching_points_restyles_its_segments() -> None:
    svg = _svg("line", _stroke_rule(color=GRAY, dasharray="4 4"), data=_RUN_ROWS)
    (gray,) = _runs(svg, GRAY)
    (base,) = _base_lines(svg)
    assert _attr(gray, "stroke-dasharray") == "4,4"
    assert _attr(base, "stroke-dasharray") is None
    # March is the first forecast row, but the February-March segment has one actual
    # endpoint, so the gray run starts at March and the base line ends there.
    assert _coords(gray)[:2] == _coords(base)[-2:]
    assert len(_coords(gray)) // 2 == 4


def test_the_halo_follows_the_restyled_run() -> None:
    svg = _svg("line", _stroke_rule(width=2, dasharray="4 4"), data=_RUN_ROWS)
    halos = _runs(svg, WHITE)
    assert [_attr(h, "stroke-dasharray") for h in halos] == [None, "4,4"]


def test_an_isolated_matching_point_restyles_no_line() -> None:
    rows = [{**r, "is_forecast": r["month"] == "2024-04-01"} for r in _RUN_ROWS]
    svg = _svg("line", _stroke_rule(color=GRAY), data=rows)
    assert _runs(svg, GRAY) == []
    assert len(_base_lines(svg)) == 1


def test_a_chart_without_a_line_rule_draws_its_line_unchanged() -> None:
    plain = _svg("line", _forecast(color=RED), data=_RUN_ROWS)
    assert len(_base_lines(plain)) == 1
    assert len(_coords(_base_lines(plain)[0])) // 2 == 6


def test_a_segment_takes_only_the_keys_both_endpoints_agree_on() -> None:
    rows = [
        {"month": f"2024-0{m}-01", "revenue": v, "is_forecast": False}
        for m, v in enumerate([10, 20, 20, 20, 15, 12], 1)
    ]
    rules = {
        "revenue": [
            {"gte": 15, "marks": {"line": {"stroke": {"color": GRAY}}}},
            {"gte": 20, "marks": {"line": {"stroke": {"width": 7}}}},
        ]
    }
    svg = _svg("line", rules, data=rows)
    grays = _runs(svg, GRAY)
    # Feb-Apr match both rules: gray and 7px. Apr-May: May has only the color rule, so
    # the width disagrees and the base width applies.
    assert [_attr(t, "stroke-width") for t in grays] == ["7", "4"]
    assert _coords(grays[0])[-2:] == _coords(grays[1])[:2]


def test_each_series_is_cut_on_its_own_rows() -> None:
    rows = [
        {
            "month": f"2024-0{m}-01",
            "revenue": 10 + m + shift,
            "team": team,
            "is_forecast": f,
        }
        for team, shift in (("a", 0), ("b", 9))
        for m in range(1, 6)
        for f in [m >= 3 if team == "a" else m == 5]
    ]
    svg = _svg("line", _stroke_rule(color=GRAY), data=rows, color="team")
    (gray,) = _runs(svg, GRAY)
    assert len(_coords(gray)) // 2 == 3
    # Team a keeps its first two points in the series color; team b is never restyled.
    assert len(_base_lines(svg)) == 2


@pytest.mark.parametrize(
    "curve", ["linear", "monotone", "natural", "cardinal", "step-before", "step-after"]
)
def test_the_runs_trace_the_unstyled_curve(curve: str) -> None:
    style = {"marks": {"line": {"curve": curve}}}
    plain = _svg("line", _stroke_rule(color=GRAY), data=_RUN_ROWS, style=style)
    never = {"revenue": [{"gt": 100, "marks": {"line": {"stroke": {"color": GRAY}}}}]}
    unstyled = _svg("line", never, data=_RUN_ROWS, style=style)
    (whole,) = _base_lines(unstyled)
    (gray,) = _runs(plain, GRAY)
    (base,) = _base_lines(plain)
    base_d, gray_d, whole_d = (_attr(t, "d") or "" for t in (base, gray, whole))
    # The second run opens with its own `M x,y`; the cut itself is not a stroke.
    drawn = re.sub(r"^M[^A-Za-z]*", "", gray_d)
    assert _flatten(base_d + drawn) == _flatten(whole_d)


def _flatten(d: str) -> list[tuple[float, float]]:
    """The outline's vertices, with the collinear ones a cut leaves dropped."""
    nums = [float(n) for n in _NUMBER.findall(d)]
    pts = list(zip(nums[0::2], nums[1::2], strict=True))
    kept = [pts[0]]
    for prev, cur, nxt in zip(pts, pts[1:], pts[2:], strict=False):
        cross = (cur[0] - prev[0]) * (nxt[1] - cur[1]) - (cur[1] - prev[1]) * (
            nxt[0] - cur[0]
        )
        if abs(cross) > 1e-6:
            kept.append(cur)
    return [*kept, pts[-1]]


def test_a_curve_that_misses_its_points_is_refused_by_name() -> None:
    with pytest.raises(ChartDataError, match="basis.*is not supported"):
        _svg(
            "line",
            _stroke_rule(color=GRAY),
            data=_RUN_ROWS,
            style={"marks": {"line": {"curve": "basis"}}},
        )


def test_a_band_step_cannot_be_cut_per_segment() -> None:
    rows = [{**r, "month": f"m{i}"} for i, r in enumerate(_RUN_ROWS)]
    with pytest.raises(ChartDataError, match="is not supported"):
        _svg(
            "line",
            _stroke_rule(color=GRAY),
            data=rows,
            style={"marks": {"line": {"curve": "step"}}},
        )


def _fills(svg: str, color: str) -> list[str]:
    return [t for t in _PATH.findall(svg) if _attr(t, "fill") == color]


def test_an_area_rule_splits_the_fill_at_the_run() -> None:
    rule = {
        "is_forecast": [
            {"eq": True, "marks": {"area": {"color": GRAY, "opacity": 0.4}}}
        ]
    }
    svg = _svg("area", rule, data=_RUN_ROWS)
    (gray,) = _fills(svg, GRAY)
    assert _attr(gray, "fill-opacity") == "0.4"
    d = _attr(gray, "d") or ""
    assert d.startswith("M") and d.endswith("Z")
    # Four top vertices (March to June), then the baseline back: one more L than a
    # four-point line, plus the drop at June.
    assert d.count("L") == 7


def test_an_area_rule_leaves_the_edge_line_whole() -> None:
    rule = {"is_forecast": [{"eq": True, "marks": {"area": {"color": GRAY}}}]}
    svg = _svg("area", rule, data=_RUN_ROWS)
    assert len(_line_tags(svg)) == 1


def _edge_rule(**stroke: Any) -> dict[str, Any]:
    return _stroke_rule(**stroke)


_STACK_ROWS: list[dict[str, Any]] = [
    {
        "month": f"2024-0{m}-01",
        "revenue": 10 + m + shift,
        "team": team,
        "is_forecast": m >= 3,
    }
    for team, shift in (("a", 0), ("b", 9))
    for m in range(1, 6)
]


def test_a_stacked_area_splits_every_band_and_keeps_one_perimeter() -> None:
    rule = {"is_forecast": [{"eq": True, "marks": {"area": {"color": GRAY}}}]}
    svg = _svg("area", rule, data=_STACK_ROWS, color="team", style={"stack": "zero"})
    assert len(_fills(svg, GRAY)) == 2
    assert all(_attr(t, "stroke") is None for t in _fills(svg, GRAY))
    # One whole-perimeter stroke per band, so no separator crosses a band at the cut.
    assert len(_line_tags(svg)) == 2


def _author_and_resolve(kind: str, marks: dict[str, Any]) -> None:
    chart = _chart(kind, {"is_forecast": [{"eq": True, "marks": marks}]})
    resolve(chart, _ROWS, chart_style_context=_BOARD_CTX)


@pytest.mark.parametrize(
    ("kind", "marks"),
    [
        ("line", {"line": {"stroke": {"color": "auto"}}}),
        ("line", {"line": {"stroke": {"width": "auto"}}}),
        ("area", {"area": {"color": "auto"}}),
        ("line", {"point": {"color": "auto"}}),
        ("area", {"point": {"size": "auto"}}),
    ],
    ids=["stroke-color", "stroke-width", "area-color", "point-color", "point-size"],
)
def test_a_rule_cannot_set_auto(kind: str, marks: dict[str, Any]) -> None:
    with pytest.raises((ValueError, CompilationError), match="auto|valid|number|color"):
        _author_and_resolve(kind, marks)


def test_an_area_rule_restyles_a_run_of_the_top_edge() -> None:
    svg = _svg(
        "area",
        _edge_rule(color=GRAY, dasharray="4 4"),
        data=_STACK_ROWS,
        color="team",
        style={},
    )
    grays = _runs(svg, GRAY)
    bases = _base_lines(svg)
    assert len(grays) == 2 and len(bases) == 2
    assert all(len(_coords(t)) // 2 == 3 for t in grays)
    assert all(len(_coords(t)) // 2 == 3 for t in bases)
    # The segment stepping into the run has one unmatched end point: it stays base.
    assert _coords(grays[0])[:2] == _coords(bases[0])[-2:]
    assert _fills(svg, GRAY) == []


def test_an_area_edge_rule_restyles_the_halo_with_it() -> None:
    svg = _svg(
        "area",
        _edge_rule(dasharray="4 4"),
        data=_STACK_ROWS,
        color="team",
        style={},
    )
    assert len([t for t in _runs(svg, WHITE) if _attr(t, "stroke-dasharray")]) == 2


def test_a_stacked_band_cuts_its_top_edge_and_keeps_the_rest_of_the_perimeter() -> None:
    svg = _svg(
        "area",
        _edge_rule(color=GRAY, width=3),
        data=_STACK_ROWS,
        color="team",
        style={"stack": "zero"},
    )
    grays = _runs(svg, GRAY)
    assert len(grays) == 2
    assert {_attr(t, "stroke-width") for t in grays} == {"3"}
    perimeters = [t for t in _line_tags(svg) if _attr(t, "stroke") != GRAY]
    assert len(perimeters) == 2
    # The styled top edge is left out of the perimeter, which keeps the other three sides.
    assert all(
        "ZM" not in (_attr(t, "d") or "") and "Z" not in (_attr(t, "d") or "")
        for t in perimeters
    )


def test_an_edge_rule_leaves_the_fill_whole() -> None:
    svg = _svg(
        "area",
        _edge_rule(color=GRAY),
        data=_STACK_ROWS,
        color="team",
        style={"stack": "zero"},
    )
    assert len(_fills(svg, GRAY)) == 0
