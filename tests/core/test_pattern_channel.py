"""The `pattern:` channel and `style.pattern` block: authored rules and resolve."""

from __future__ import annotations

import re
from typing import Any

import pytest
from pydantic import TypeAdapter, ValidationError

from dbt_charts.core.compile.config import get_theme_style, reset_config
from dbt_charts.core.compile.models.chart.authored import AuthoredChart
from dbt_charts.core.compile.models.chart.normalized import (
    AreaChart,
    BarChart,
    HeatmapChart,
    PieChart,
)
from dbt_charts.core.compile.models.chart.resolved import (
    ResolvedAreaChart,
    ResolvedBarChart,
    ResolvedHeatmapChart,
    ResolvedPieChart,
)
from dbt_charts.core.compile.models.query.normalized import SqlQuery
from dbt_charts.core.compile.models.style.authored import (
    AreaChartStylePatch,
    BarChartStylePatch,
    HeatmapChartStylePatch,
    PieChartStylePatch,
)
from dbt_charts.core.compile.resolve import resolve
from dbt_charts.core.compile.resolve.style.board import resolve_chart_style_context

from ._svg_render import render_board_svg_result

_adapter = TypeAdapter(AuthoredChart)
_MIXED_8 = [
    "hatch-45",
    "bluenoise-dots",
    "dash-field",
    "bendday-hex",
    "worms-fine",
    "crosshatch",
    "pebbles-fine",
    "dither",
]


@pytest.fixture(autouse=True)
def _reset() -> Any:
    reset_config()
    yield
    reset_config()


def _authored(chart_type: str, **fields: Any) -> Any:
    base = {"type": chart_type, "query": "q", **fields}
    if chart_type == "pie":
        base["theta"] = "value"
    elif chart_type == "heatmap":
        base |= {"x": "x_time", "y": "series"}
    else:
        base |= {"x": "x_time", "y": "value"}
    return _adapter.validate_python(base)


@pytest.mark.parametrize("chart_type", ["bar", "area", "pie"])
def test_pattern_channel_is_authorable(chart_type: str) -> None:
    chart = _authored(
        chart_type,
        color="series",
        pattern="series",
        style={"pattern": {"color": "#2b4c7e", "ground": "tint", "inverse": True}},
    )
    assert chart.pattern == "series"
    assert chart.style.pattern.color == "#2b4c7e"
    assert chart.style.pattern.ground == "tint"
    assert chart.style.pattern.inverse is True


def test_heatmap_authors_a_pattern() -> None:
    assert _authored("heatmap", color="value", pattern="value").pattern == "value"


def test_heatmap_pattern_is_not_authorable_with_multiples() -> None:
    with pytest.raises(ValidationError, match="multiples"):
        _authored(
            "heatmap",
            color="value",
            pattern="value",
            multiples={"columns": "series"},
        )


@pytest.mark.parametrize("family", ["bar", "area", "pie", "heatmap"])
def test_the_theme_supplies_the_pattern_ground_and_inverse(family: str) -> None:
    pattern = getattr(get_theme_style().charts, family).pattern
    assert (pattern.ground, pattern.inverse) == ("background", False)


@pytest.mark.parametrize("chart_type", ["line", "scatter", "histogram"])
def test_pattern_is_not_a_field_elsewhere(chart_type: str) -> None:
    with pytest.raises(ValidationError, match="pattern"):
        _authored(chart_type, pattern="series")


@pytest.mark.parametrize("chart_type", ["line", "scatter", "histogram"])
def test_style_pattern_is_not_a_field_elsewhere(chart_type: str) -> None:
    with pytest.raises(ValidationError, match="pattern"):
        _authored(chart_type, style={"pattern": {"ground": "tint"}})


def test_style_pattern_values_are_closed() -> None:
    with pytest.raises(ValidationError):
        _authored("bar", pattern="series", style={"pattern": {"color": "not a color"}})
    with pytest.raises(ValidationError):
        _authored("bar", pattern="series", style={"pattern": {"palette": "vivid-10"}})
    chart = _authored(
        "bar", pattern="series", style={"pattern": {"palette": "pattern_diverging"}}
    )
    assert chart.style.pattern.palette == "pattern_diverging"


_CTX = None


def _ctx() -> Any:
    return resolve_chart_style_context(get_theme_style())


_SERIES = [
    {"x_time": month, "series": f"Series {s}", "value": 10 + s}
    for month in ("2024-01-01", "2024-02-01")
    for s in range(1, 4)
]
_SIGNED = [
    {"x_time": f"2024-0{i}-01", "series": "a", "value": v}
    for i, v in enumerate((-4, -1, 0, 2, 5), 1)
]


def _bar(data: list[dict[str, Any]], **fields: Any) -> ResolvedBarChart:
    chart = BarChart(
        id="t",
        query=SqlQuery(sql="SELECT 1", source="t"),
        query_name="q",
        type="bar",
        x="x_time",
        y="value",
        **fields,
    )
    resolved = resolve(chart, data, _ctx(), width=600.0)
    assert isinstance(resolved, ResolvedBarChart)
    return resolved


def test_discrete_field_resolves_the_category_role() -> None:
    resolved = _bar(_SERIES, color="series", pattern="series")
    pattern = resolved.pattern
    assert pattern is not None
    assert (pattern.field, pattern.scale) == ("series", "category")
    assert list(pattern.motifs or ()) == _MIXED_8
    assert pattern.ramp is None
    assert (pattern.color, pattern.ground, pattern.inverse) == (
        None,
        "background",
        False,
    )


def test_quantitative_field_resolves_the_sequential_role() -> None:
    pattern = _bar(_SERIES, pattern="value").pattern
    assert pattern is not None
    assert (pattern.scale, pattern.ramp, pattern.motifs) == (
        "sequential",
        "dots",
        None,
    )


def test_palette_role_selects_the_diverging_ramp() -> None:
    resolved = _bar(
        _SIGNED,
        pattern="value",
        style=BarChartStylePatch(
            pattern={"palette": "pattern_diverging", "ground": "tint", "inverse": True}
        ),
    )
    pattern = resolved.pattern
    assert pattern is not None
    assert (pattern.scale, pattern.ramp) == ("diverging", "hatch-dots")
    assert (pattern.ground, pattern.inverse) == ("tint", True)


def test_ramp_steps_default_to_the_palette_and_can_be_overridden() -> None:
    pattern = _bar(_SERIES, pattern="value").pattern
    assert pattern is not None and pattern.steps == 6
    pattern = _bar(
        _SERIES,
        pattern="value",
        style=BarChartStylePatch(pattern={"steps": 4}),
    ).pattern
    assert pattern is not None and pattern.steps == 4
    diverging = _bar(
        _SIGNED,
        pattern="value",
        style=BarChartStylePatch(pattern={"palette": "pattern_diverging"}),
    ).pattern
    assert diverging is not None and diverging.steps == 7


def test_steps_on_a_category_scale_is_an_error() -> None:
    with pytest.raises(ValueError, match="steps.*category|category.*steps"):
        _bar(
            _SERIES,
            color="series",
            pattern="series",
            style=BarChartStylePatch(pattern={"steps": 5}),
        )


@pytest.mark.parametrize("steps", [2, 10])
def test_steps_range_is_closed(steps: int) -> None:
    with pytest.raises(ValidationError):
        _authored("bar", pattern="value", style={"pattern": {"steps": steps}})


def test_category_scale_carries_no_steps() -> None:
    pattern = _bar(_SERIES, color="series", pattern="series").pattern
    assert pattern is not None and pattern.steps is None


def test_pattern_palette_name_may_replace_the_default() -> None:
    pattern = _bar(
        _SERIES,
        color="series",
        pattern="series",
        style=BarChartStylePatch(pattern={"palette": "pattern-mixed-8"}),
    ).pattern
    assert pattern is not None and pattern.scale == "category"


def test_palette_kind_must_suit_the_field() -> None:
    with pytest.raises(ValueError, match="categorical.*quantitative|quantitative"):
        _bar(
            _SERIES,
            pattern="value",
            style=BarChartStylePatch(pattern={"palette": "pattern-mixed-8"}),
        )
    with pytest.raises(ValueError, match="sequential.*discrete|discrete"):
        _bar(
            _SERIES,
            color="series",
            pattern="series",
            style=BarChartStylePatch(pattern={"palette": "pattern-hatch"}),
        )


@pytest.mark.parametrize("family", ["bar", "area", "pie"])
def test_pattern_and_color_must_name_the_same_column(family: str) -> None:
    kwargs = {
        "bar": (BarChart, {"x": "x_time", "y": "value"}),
        "area": (AreaChart, {"x": "x_time", "y": "value"}),
        "pie": (PieChart, {"theta": "value"}),
    }
    cls, fields = kwargs[family]
    chart = cls(
        id="t",
        query=SqlQuery(sql="SELECT 1", source="t"),
        query_name="q",
        type=family,
        color="series",
        pattern="x_time",
        **fields,
    )
    with pytest.raises(ValueError, match=r"pattern.*color.*same column"):
        resolve(chart, _SERIES, _ctx(), width=600.0)


def test_heatmap_pattern_is_one_color_without_a_color_channel() -> None:
    query = SqlQuery(sql="SELECT 1", source="t")
    kwargs = {
        "id": "h",
        "query": query,
        "query_name": "q",
        "type": "heatmap",
        "x": "x_time",
        "y": "series",
        "pattern": "value",
    }
    one_color = resolve(HeatmapChart(**kwargs), _SERIES, _ctx(), width=600.0)
    assert isinstance(one_color, ResolvedHeatmapChart)
    assert one_color.color == "value", "the pattern field is the cells' value"
    assert one_color.pattern is not None and one_color.pattern.color is not None
    with pytest.raises(ValueError, match="same column"):
        resolve(HeatmapChart(color="series", **kwargs), _SERIES, _ctx(), width=600.0)


def test_heatmap_pattern_on_a_date_column_is_an_error() -> None:
    chart = HeatmapChart(
        id="h",
        query=SqlQuery(sql="SELECT 1", source="t"),
        query_name="q",
        type="heatmap",
        x="series",
        y="series",
        pattern="x_time",
    )
    with pytest.raises(ValueError, match=r"pattern.*x_time.*date"):
        resolve(chart, _SERIES, _ctx(), width=600.0)


def test_no_pattern_channel_leaves_the_payload_bare() -> None:
    resolved = _bar(_SERIES, color="series")
    assert resolved.pattern is None
    assert "pattern" not in resolved.style.model_dump()


def test_area_pie_heatmap_resolve_a_pattern() -> None:
    ctx = _ctx()
    query = SqlQuery(sql="SELECT 1", source="t")
    area = resolve(
        AreaChart(
            id="a",
            query=query,
            query_name="q",
            type="area",
            x="x_time",
            y="value",
            color="series",
            pattern="series",
            style=AreaChartStylePatch(pattern={"ground": "tint"}),
        ),
        _SERIES,
        ctx,
        width=600.0,
    )
    assert isinstance(area, ResolvedAreaChart)
    assert area.pattern is not None and area.pattern.ground == "tint"
    pie = resolve(
        PieChart(
            id="p",
            query=query,
            query_name="q",
            type="pie",
            theta="value",
            color="series",
            pattern="series",
            style=PieChartStylePatch(),
        ),
        _SERIES[:3],
        ctx,
        width=400.0,
    )
    assert isinstance(pie, ResolvedPieChart)
    assert pie.pattern is not None and pie.pattern.scale == "category"
    heat = resolve(
        HeatmapChart(
            id="h",
            query=query,
            query_name="q",
            type="heatmap",
            x="x_time",
            y="series",
            color="value",
            pattern="value",
            style=HeatmapChartStylePatch(),
        ),
        _SERIES,
        ctx,
        width=600.0,
    )
    assert isinstance(heat, ResolvedHeatmapChart)
    assert heat.pattern is not None and heat.pattern.scale == "sequential"


@pytest.mark.parametrize(
    ("chart_type", "body"),
    [
        ("bar", "x: x_time\n    y: value\n    color: series\n"),
        ("area", "x: x_time\n    y: value\n    color: series\n"),
        ("pie", "theta: value\n    color: series\n"),
        ("heatmap", "x: x_time\n    y: series\n    color: value\n"),
    ],
)
def test_pattern_survives_normalization(chart_type: str, body: str) -> None:
    from dbt_charts.core.compile import compile as compile_board

    field = "value" if chart_type == "heatmap" else "series"
    board = compile_board(
        "title: t\n"
        "queries:\n  q:\n    source: s\n    sql: SELECT 1\n"
        "charts:\n  c:\n"
        f"    type: {chart_type}\n    query: q\n    {body}"
        f"    pattern: {field}\n"
        "    style:\n      pattern:\n        inverse: true\n"
        "rows:\n- c\n"
    )
    assert board.board is not None, board.errors
    chart = board.board.charts["c"]
    assert chart.pattern == field
    assert chart.style.pattern.inverse is True


def test_wire_omits_the_other_scales_keys() -> None:
    category = _bar(_SERIES, color="series", pattern="series").pattern
    ramp = _bar(_SERIES, pattern="value").pattern
    assert category is not None and ramp is not None
    assert set(category.model_dump(mode="json")) == {
        "field",
        "scale",
        "motifs",
        "ground",
        "inverse",
    }
    assert set(ramp.model_dump(mode="json")) == {
        "field",
        "scale",
        "ramp",
        "steps",
        "color",
        "ground",
        "inverse",
    }


def test_wire_omits_absent_patterns() -> None:
    wire = _bar(_SERIES, color="series").model_dump(mode="json")
    assert "pattern" not in wire
    assert "pattern" in _bar(_SERIES, pattern="value").model_dump(mode="json")


def _series_chart(cls: Any, **fields: Any) -> Any:
    return cls(
        id="t",
        query=SqlQuery(sql="SELECT 1", source="t"),
        query_name="q",
        **fields,
    )


def test_pattern_alone_splits_series_and_paints_the_single_series_color() -> None:
    single = _bar(_SERIES, pattern="series")
    solo = _bar(_SERIES[:1], pattern="value")
    assert single.color == "series"
    assert single.pattern is not None and solo.pattern is not None
    assert single.pattern.color == solo.style.single_series_fill
    assert single.pattern.ground == "background"


def test_pattern_alone_splits_area_and_pie_series() -> None:
    ctx = _ctx()
    area = resolve(
        _series_chart(AreaChart, type="area", x="x_time", y="value", pattern="series"),
        _SERIES,
        ctx,
        width=600.0,
    )
    pie = resolve(
        _series_chart(PieChart, type="pie", theta="value", pattern="series"),
        _SERIES[:3],
        ctx,
        width=400.0,
    )
    for resolved in (area, pie):
        assert resolved.color == "series"
        assert resolved.pattern is not None and resolved.pattern.color is not None


def test_pattern_on_the_category_axis_keeps_a_single_series_bar() -> None:
    resolved = _bar(_SERIES[:3], pattern="x_time")
    assert resolved.color is None
    assert resolved.pattern is not None


def test_color_channel_paints_each_series_pattern_in_its_hue() -> None:
    pattern = _bar(_SERIES, color="series", pattern="series").pattern
    assert pattern is not None and pattern.color is None


def test_authored_pattern_color_is_the_pattern_color() -> None:
    pattern = _bar(
        _SERIES,
        pattern="series",
        style=BarChartStylePatch(pattern={"color": "#2b4c7e"}),
    ).pattern
    assert pattern is not None and pattern.color == "#2b4c7e"


def test_pattern_color_with_a_color_channel_is_an_error() -> None:
    with pytest.raises(ValueError, match=r"style\.pattern\.color.*color:"):
        _bar(
            _SERIES,
            color="series",
            pattern="series",
            style=BarChartStylePatch(pattern={"color": "#2b4c7e"}),
        )


def test_ground_is_background_tint_a_color_or_a_palette() -> None:
    def ground(value: str) -> Any:
        chart = _bar(
            _SERIES,
            pattern="series",
            style=BarChartStylePatch(pattern={"ground": value}),
        )
        assert chart.pattern is not None
        return chart.pattern.ground

    assert ground("tint") == "tint"
    assert ground("#fff3d6") == "#fff3d6"
    slots = ground("category-6-tonal-blue")
    assert isinstance(slots, tuple) and len(slots) == 6
    assert all(slot.startswith("#") for slot in slots)


def test_ground_palette_is_for_category_patterns() -> None:
    with pytest.raises(ValueError, match=r"ground.*color palette"):
        _bar(
            _SERIES,
            pattern="value",
            style=BarChartStylePatch(pattern={"ground": "category-6-tonal-blue"}),
        )


def _series_text_fills(chart: str) -> set[str]:
    rows = "".join(
        f"      - {{x: m{m}, s: S{s}, v: {10 + s + m}}}\n"
        for m in range(1 if "pie" in chart else 3)
        for s in range(1, 7)
    )
    board = (
        f"title: T\nqueries:\n  q:\n    type: values\n    rows:\n{rows}"
        f"charts:\n  c:\n    query: q\n{chart}\nrows:\n  - c\n"
    )
    result = render_board_svg_result(board)
    assert not result.chart_errors
    return set(
        re.findall(
            r'<text[^>]*?\bfill="(#[0-9a-fA-F]{6})"[^>]*data-dct-role="text"',
            result.output,
        )
    )


@pytest.mark.parametrize(
    "chart",
    [
        "    type: pie\n    theta: v\n    pattern: s\n    width: 400",
        "    type: bar\n    x: x\n    y: v\n    pattern: s\n    style:\n"
        "      stack: zero\n",
        "    type: area\n    x: x\n    y: v\n    pattern: s",
    ],
)
def test_single_color_pattern_series_text_takes_one_color(chart: str) -> None:
    assert len(_series_text_fills(chart)) == 1


def test_a_theme_pattern_color_does_not_conflict_with_a_color_channel() -> None:
    theme = get_theme_style()
    bar_style = theme.charts.bar
    inked = bar_style.model_copy(
        update={"pattern": bar_style.pattern.model_copy(update={"color": "#2b4c7e"})}
    )
    ctx = resolve_chart_style_context(
        theme.model_copy(
            update={"charts": theme.charts.model_copy(update={"bar": inked})}
        )
    )
    chart = BarChart(
        id="t",
        query=SqlQuery(sql="SELECT 1", source="t"),
        query_name="q",
        type="bar",
        x="x_time",
        y="value",
        color="series",
        pattern="series",
    )
    resolved = resolve(chart, _SERIES, ctx, width=600.0)
    assert isinstance(resolved, ResolvedBarChart)
    assert resolved.pattern is not None and resolved.pattern.color is None
    plain = resolve(chart.model_copy(update={"color": None}), _SERIES, ctx, width=600.0)
    assert isinstance(plain, ResolvedBarChart)
    assert plain.pattern is not None and plain.pattern.color == "#2b4c7e"
