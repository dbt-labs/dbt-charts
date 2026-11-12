"""``Board.chart_styles``."""

from __future__ import annotations

import dataclasses
import json

import pytest
from pydantic import TypeAdapter

from dbt_charts.core.compile import compile as compile_yaml
from dbt_charts.core.compile.config import get_theme_style
from dbt_charts.core.compile.models.board.normalized import Board
from dbt_charts.core.compile.models.chart.normalized import Chart
from dbt_charts.core.compile.models.style.context import ChartStyleContext
from dbt_charts.core.compile.models.style.merged import MergedChartStyle
from dbt_charts.core.compile.normalize.chart_focus import (
    focus_on_chart,
    focus_on_item,
)
from dbt_charts.core.compile.resolve.chart._kwargs import board_family_style
from dbt_charts.core.compile.resolve.chart._palette import _with_color_tokens
from dbt_charts.core.compile.resolve.style import board as style_board, chart_context
from dbt_charts.core.compile.resolve.style.board import (
    resolve_chart_style_context,
    unset_auto_support_ink,
)
from dbt_charts.core.compile.resolve.style.chart_context import (
    build_chart_style_context,
)
from dbt_charts.core.compile.resolve.style.chart_style import ChartStyleOverrides
from dbt_charts.core.compile.resolve.style.inherit_resolver import merge_unset

_QUERY = "query: {sql: 'SELECT 1 AS m, 1 AS r', source: duckdb}"

# One chart per type in the normalized Chart union, with the minimum each needs.
_CHARTS = {
    "c_bar": "type: bar, x: m, y: r",
    "c_hist": "type: histogram, x: m",
    "c_line": "type: line, x: m, y: r",
    "c_area": "type: area, x: m, y: r",
    "c_scatter": "type: scatter, x: m, y: r",
    "c_heat": "type: heatmap, x: m, y: r, color: r",
    "c_pie": "type: pie, theta: r, color: m",
    "c_donut": "type: donut, theta: r, color: m",
    "c_kpi": "type: kpi, value: r",
    "c_table": "type: table",
    "c_spark": "type: spark_bar, x: r",
    "c_geo": "type: geoshape, geo: m, color: r",
    "c_map": "type: map, geo: m, color: r",
    "c_point": "type: point_map, latitude: m, longitude: r",
    "c_bubble": "type: bubble_map, latitude: m, longitude: r",
}
_ALL_TYPES = (
    "charts:\n"
    + "".join(f"  {k}: {{{v}, {_QUERY}}}\n" for k, v in _CHARTS.items())
    + "  c_callout: {type: callout, message: hi}\n"
    + "rows:\n"
    + "".join(f"  - {k}\n" for k in [*_CHARTS, "c_callout"])
)


def _board(yaml_text: str) -> Board:
    result = compile_yaml(yaml_text)
    assert result.success, result.errors
    assert result.board is not None
    return result.board


def test_chart_styles_reflect_board_and_chart_local_overrides() -> None:
    board = _board(
        f"""\
title: T
style:
  charts:
    bar:
      marks:
        bar:
          border:
            width: 3.5
rows:
  - styled
  - plain
charts:
  styled:
    type: bar
    x: m
    y: r
    {_QUERY}
    style:
      background: '#abcdef'
      marks:
        bar:
          color: '#aa0000'
      axis_x:
        title:
          visible: false
  plain:
    type: bar
    x: m
    y: r
    {_QUERY}
"""
    )
    styled, plain = board.chart_styles["styled"], board.chart_styles["plain"]
    assert styled.type == "bar" and plain.type == "bar"
    assert styled.family.marks.bar.border.width == 3.5
    assert plain.family.marks.bar.border.width == 3.5
    assert styled.background == "#abcdef"
    assert styled.family.marks.bar.color == "#aa0000"
    assert styled.axis_x is not None and styled.axis_x.title.visible is False
    assert plain.background != "#abcdef"
    assert plain.family.marks.bar.color is None
    assert plain.axis_x is None


def test_nested_board_chart_gets_its_own_theme_style() -> None:
    board = _board(
        f"""\
theme: paper
charts:
  outer: {{type: bar, x: m, y: r, {_QUERY}}}
  inner: {{type: bar, x: m, y: r, {_QUERY}}}
rows:
  - outer
  - theme: stark
    rows:
      - inner
"""
    )
    nested = board.layout.items[1].board
    assert nested is not None
    assert set(board.chart_styles) == {"outer"}
    assert set(nested.chart_styles) == {"inner"}
    stark = resolve_chart_style_context(get_theme_style("stark"))
    paper = resolve_chart_style_context(get_theme_style("paper"))
    assert stark.palette != paper.palette
    assert nested.chart_styles["inner"].palette == stark.palette
    assert board.chart_styles["outer"].palette == paper.palette


def test_chart_styles_round_trip_through_json() -> None:
    board = _board(_ALL_TYPES)
    dumped = board.model_dump(mode="json")
    assert Board.model_validate(dumped).chart_styles == board.chart_styles
    adapter = TypeAdapter(dict[str, MergedChartStyle])
    assert adapter.validate_python(dumped["chart_styles"]) == board.chart_styles


def test_every_chart_type_is_covered_with_its_family() -> None:
    board = _board(_ALL_TYPES)
    assert {i: s.type for i, s in board.chart_styles.items()} == {
        i: c.type for i, c in board.charts.items()
    }
    assert len(board.chart_styles) == len(_CHARTS) + 1
    table = board.chart_styles["c_table"]
    assert table.support_table == board.chart_style_context.support_table


def test_custom_chart_carries_only_the_shared_fields() -> None:
    board = _board(
        """\
title: T
charts:
  c:
    type: custom
    svg: '<svg xmlns="http://www.w3.org/2000/svg"></svg>'
rows:
  - c
"""
    )
    style = board.chart_styles["c"]
    assert style.type == "custom"
    assert style.palette == board.chart_style_context.palette


def test_chart_styles_equal_what_the_resolvers_paint_with() -> None:
    board = _board(_ALL_TYPES)
    context = board.chart_style_context
    for item in board.layout.items:
        chart = item.chart
        assert chart is not None
        style = board.chart_styles[chart.id]
        if chart.type == "callout":
            built = context
        else:
            built = build_chart_style_context(context, chart)
        shared = {
            "palette": built.palette,
            "single_series_palette": built.single_series_palette,
            "requested_alias_palette": built.requested_alias_palette,
            "background": built.background,
            "ink_canvas": built.ink_canvas,
            "legend": built.legend,
            "title": built.title,
            "pagination": built.pagination,
            "axis": built.axis_overrides_global,
            "axis_x": built.axis_overrides_x,
            "axis_y": built.axis_overrides_y,
            "axis_quantitative": built.axis_overrides_quantitative,
            "axis_band": built.axis_overrides_band,
        }
        assert {k: getattr(style, k) for k in shared} == shared
        if chart.type in ("bar", "line", "area"):
            assert style.family == getattr(built, chart.type)
        elif chart.type == "kpi":
            assert style.family == unset_auto_support_ink(built.kpi)
        else:
            assert style.family == merge_unset(
                board_family_style(context, chart),
                _with_color_tokens(chart.style, context),
            )


def test_table_family_carries_a_chart_local_font_and_background() -> None:
    board = _board(
        f"""\
charts:
  c:
    type: table
    {_QUERY}
    style:
      background: '#abcdef'
      font:
        size: 31
rows:
  - c
"""
    )
    style = board.chart_styles["c"]
    assert style.background == "#abcdef"
    assert style.family.font.size == 31
    assert board.chart_style_context.table.font.size != 31


def test_set_theme_recomputes_chart_styles() -> None:
    board = _board(f"charts:\n  c: {{type: bar, x: m, y: r, {_QUERY}}}\nrows:\n  - c\n")
    before = board.chart_styles["c"].palette
    board.set_theme("stark")
    stark = resolve_chart_style_context(get_theme_style("stark"))
    assert before != stark.palette
    assert board.chart_styles["c"].palette == stark.palette


@pytest.mark.parametrize("chart_type", ["histogram", "callout"])
def test_chart_local_patch_reaches_the_family(chart_type: str) -> None:
    extra = {"histogram": "x: m", "callout": "message: hi"}[chart_type]
    query = "" if chart_type == "callout" else f", {_QUERY}"
    board = _board(
        f"charts:\n  c: {{type: {chart_type}, {extra}{query}, "
        "style: {preferred_width: 777}}\nrows:\n  - c\n"
    )
    assert board.chart_styles["c"].family.preferred_width == 777


@pytest.mark.parametrize(
    ("chart_type", "width"), [("pie", None), ("donut", 555)], ids=["pie", "donut"]
)
def test_donut_board_override_reaches_donuts_only(
    chart_type: str, width: int | None
) -> None:
    board = _board(
        f"""\
style:
  charts:
    donut:
      preferred_width: 555
charts:
  c: {{type: {chart_type}, theta: r, color: m, {_QUERY}}}
rows:
  - c
"""
    )
    family = board.chart_styles["c"].family
    if width is None:
        assert family.preferred_width == board.chart_style_context.pie.preferred_width
        assert family.preferred_width != 555
    else:
        assert family.preferred_width == width


def test_focused_board_carries_the_focused_charts_styles() -> None:
    board = _board(_ALL_TYPES)
    focused = focus_on_chart(board, "c_line")
    assert set(focused.chart_styles) == {"c_line"}
    assert focused.chart_styles["c_line"] == board.chart_styles["c_line"]
    item_focused = focus_on_item(board, 0)
    assert set(item_focused.chart_styles) == {"c_bar"}


def test_chart_with_an_unknown_color_token_has_no_entry_but_the_board_compiles() -> (
    None
):
    board = _board(
        f"""\
charts:
  good: {{type: bar, x: m, y: r, {_QUERY}}}
  bad:
    type: bar
    x: m
    y: r
    {_QUERY}
    style:
      marks:
        bar:
          color: nosuch.shade
rows:
  - good
  - bad
"""
    )
    assert set(board.chart_styles) == {"good"}


_PLAIN = f"charts:\n  c: {{type: bar, x: m, y: r, {_QUERY}}}\nrows:\n  - c\n"


def _spy_merges(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    calls: list[str] = []
    real = chart_context.merge_chart_style

    def spy(board_ctx: ChartStyleContext, chart: Chart) -> ChartStyleOverrides:
        calls.append(chart.id)
        return real(board_ctx, chart)

    monkeypatch.setattr(chart_context, "merge_chart_style", spy)
    return calls


def test_per_chart_context_is_merged_once_per_chart_object(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    board = _board(_PLAIN)
    chart = board.charts["c"]
    calls = _spy_merges(monkeypatch)
    first = build_chart_style_context(board.chart_style_context, chart)
    assert build_chart_style_context(board.chart_style_context, chart) is first
    assert calls == []


def test_a_copied_chart_misses_the_memo(monkeypatch: pytest.MonkeyPatch) -> None:
    board = _board(_PLAIN)
    chart = board.charts["c"]
    calls = _spy_merges(monkeypatch)
    build_chart_style_context(board.chart_style_context, chart)
    build_chart_style_context(board.chart_style_context, chart.model_copy())
    assert calls == ["c"]


def test_attach_populates_the_memo_so_later_builds_do_not_merge(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    board = _board(_PLAIN)
    calls = _spy_merges(monkeypatch)
    for _ in range(3):
        build_chart_style_context(board.chart_style_context, board.charts["c"])
    assert calls == []


def test_replaced_context_starts_with_an_empty_memo(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    board = _board(_PLAIN)
    replaced = dataclasses.replace(board.chart_style_context, preferred_width=1.0)
    calls = _spy_merges(monkeypatch)
    build_chart_style_context(replaced, board.charts["c"])
    assert calls == ["c"]


def test_memo_stays_out_of_schema_dump_equality_and_artifact() -> None:
    board = _board(_PLAIN)
    schema_before = json.dumps(TypeAdapter(Board).json_schema(), sort_keys=True)
    build_chart_style_context(board.chart_style_context, board.charts["c"])
    assert "memo" not in schema_before
    assert "memo" not in json.dumps(board.model_dump(mode="json"))
    assert "memo" not in json.dumps(TypeAdapter(Board).dump_python(board, mode="json"))
    assert json.dumps(TypeAdapter(Board).json_schema(), sort_keys=True) == (
        schema_before
    )
    assert _board(_PLAIN).chart_style_context == board.chart_style_context
    assert not any("memo" in f.name for f in dataclasses.fields(ChartStyleContext))
    assert any("memo" in key for key in vars(board.chart_style_context))


def test_category_scales_keep_the_memo_with_the_scales_set(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    board = _board(_PLAIN)
    chart = board.charts["c"]
    scales = board.chart_style_context.category_colors
    updated = chart_context.context_with_category_colors(
        board.chart_style_context, scales
    )
    calls = _spy_merges(monkeypatch)
    assert build_chart_style_context(updated, chart) is updated
    assert calls == []
    assert updated is not board.chart_style_context


def test_shared_theme_contexts_never_carry_a_memo() -> None:
    first = _board(_PLAIN)
    second = _board(_PLAIN.replace("x: m", "x: r"))
    assert first.chart_style_context is not second.chart_style_context
    shared = resolve_chart_style_context(get_theme_style())
    build_chart_style_context(shared, first.charts["c"])
    assert "_chart_memo" not in shared.__dict__
    for board in (first, second):
        assert "_chart_memo" in board.chart_style_context.__dict__
    assert style_board._RESOLVED_STYLE_CACHE
    for _, _, cached in style_board._RESOLVED_STYLE_CACHE.values():
        assert "_chart_memo" not in cached.__dict__


def test_context_without_a_memo_stays_without_one_through_scales() -> None:
    shared = resolve_chart_style_context(get_theme_style())
    updated = chart_context.context_with_category_colors(shared, ())
    assert "_chart_memo" not in updated.__dict__
    owned = chart_context.with_chart_memo(shared)
    carried = chart_context.context_with_category_colors(owned, ())
    assert carried.__dict__["_chart_memo"] is not owned.__dict__["_chart_memo"]


def test_table_style_records_whether_the_chart_pins_its_width() -> None:
    board = _board(
        f"""\
title: T
rows:
  - pinned
  - free
charts:
  pinned:
    type: table
    {_QUERY}
    style:
      preferred_width: 640
  free:
    type: table
    {_QUERY}
"""
    )
    pinned, free = board.chart_styles["pinned"], board.chart_styles["free"]
    assert pinned.type == "table" and free.type == "table"
    assert pinned.width_pinned is True
    assert free.width_pinned is False


def test_a_board_level_table_width_leaves_the_table_unpinned() -> None:
    board = _board(
        f"""\
title: T
style:
  charts:
    table:
      preferred_width: 640
rows:
  - t
charts:
  t:
    type: table
    {_QUERY}
"""
    )
    style = board.chart_styles["t"]
    assert style.type == "table"
    assert style.width_pinned is False


def test_style_carries_the_charts_own_color_gradient_and_only_that() -> None:
    board = _board(
        f"""\
title: T
style:
  charts:
    bar:
      color:
        gradient: {{palette: viridis}}
rows:
  - graded
  - plain
  - geo
charts:
  graded:
    type: bar
    x: m
    y: r
    color: r
    {_QUERY}
    style:
      color:
        gradient: {{palette: [white, blue]}}
  plain:
    type: bar
    x: m
    y: r
    {_QUERY}
  geo:
    type: geoshape
    geo: m
    color: r
    {_QUERY}
    style:
      color:
        gradient: {{palette: viridis}}
"""
    )
    graded, plain = board.chart_styles["graded"], board.chart_styles["plain"]
    assert graded.gradient is not None and graded.gradient.palette == ["white", "blue"]
    # The board's theme gradient is not the chart's own channel upgrade.
    assert plain.gradient is None
    # Geo channels only consume the field name: Python ignores the gradient there.
    assert board.chart_styles["geo"].gradient is None
