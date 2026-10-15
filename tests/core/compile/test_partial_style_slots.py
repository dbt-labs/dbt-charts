"""Partial writes into empty, inherit-linked style slots.

A partial write into an empty slot that inherits from a parent (``style.charts.title``,
``style.charts.<family>.marks.bar.border``, ``style.charts.pie.marks.slice.labels``)
is filled from the parent first, so it never has to be complete on its own.

``style.charts.color.gradient`` has no parent: presence switches the color channel
to gradient mode, so an author writing it supplies the palette.
"""

from __future__ import annotations

import textwrap
from collections.abc import Generator

import pytest

from dbt_charts.core.compile.config import get_theme_style, reset_config
from dbt_charts.core.compile.models.board.normalized import Board
from dbt_charts.core.compile.resolve.style.board import resolve_style


@pytest.fixture(autouse=True)
def _reset() -> Generator[None, None, None]:
    reset_config()
    yield
    reset_config()


def _compile(yaml_str: str) -> Board:
    from dbt_charts.core.compile.compiler import compile as df_compile

    result = df_compile(textwrap.dedent(yaml_str))
    assert result.success, result.errors
    assert result.board is not None
    return result.board


_BOARD_TEMPLATE = """\
title: Board
style:
{style}
queries:
  q:
    type: values
    rows:
      - {{x: 1, y: 2}}
charts:
  c:
    query: q
    type: {chart_type}
{chart_fields}
rows:
  - c
"""

_CARTESIAN_FIELDS = "    x: x\n    y: y"
_PIE_FIELDS = "    theta: y\n    color: x"


def test_partial_charts_title_font_size_alone_compiles() -> None:
    """One leaf under style.charts.title fills its siblings from style.title."""
    board = _compile(
        _BOARD_TEMPLATE.format(
            style=textwrap.indent(
                "charts:\n  title:\n    font:\n      size: 30\n", "  "
            ),
            chart_type="bar",
            chart_fields=_CARTESIAN_FIELDS,
        )
    )
    ctx = board.chart_style_context
    assert ctx.title.font.size == 30
    assert ctx.title.font.color == resolve_style(get_theme_style()).title.font.color


def test_partial_charts_title_unknown_key_still_raises() -> None:
    """An unknown key under style.charts.title still raises."""
    from dbt_charts.core.compile.compiler import compile as df_compile

    result = df_compile(
        textwrap.dedent(
            _BOARD_TEMPLATE.format(
                style=textwrap.indent(
                    "charts:\n"
                    "  title:\n"
                    "    font:\n"
                    "      color: '#222222'\n"
                    "    bogus_field: 1\n",
                    "  ",
                ),
                chart_type="bar",
                chart_fields=_CARTESIAN_FIELDS,
            )
        )
    )
    assert not result.success
    assert any("bogus_field" in e.message for e in result.errors)


def test_partial_bar_marks_border_color_compiles() -> None:
    board = _compile(
        _BOARD_TEMPLATE.format(
            style=textwrap.indent(
                "charts:\n"
                "  bar:\n"
                "    marks:\n"
                "      bar:\n"
                "        border:\n"
                "          color: '#ff0000'\n",
                "  ",
            ),
            chart_type="bar",
            chart_fields=_CARTESIAN_FIELDS,
        )
    )
    ctx = board.chart_style_context
    border = ctx.bar.marks.bar.border
    assert border is not None
    assert border.color == "#ff0000"
    theme_border = get_theme_style().charts.marks.bar.border
    assert theme_border is not None
    assert border.width == theme_border.width
    assert border.radius == theme_border.radius


def test_partial_pie_slice_labels_font_color_compiles() -> None:
    board = _compile(
        _BOARD_TEMPLATE.format(
            style=textwrap.indent(
                "charts:\n"
                "  pie:\n"
                "    marks:\n"
                "      slice:\n"
                "        labels:\n"
                "          font:\n"
                "            color: '#00ff00'\n",
                "  ",
            ),
            chart_type="pie",
            chart_fields=_PIE_FIELDS,
        )
    )
    ctx = board.chart_style_context
    labels = ctx.pie.marks.slice.labels
    assert labels is not None
    assert labels.font.color == "#00ff00"


def test_partial_charts_title_and_bar_border_in_meta_yml_reach_the_chart(
    in_memory_project: type,
) -> None:
    from pathlib import Path

    from dbt_charts.core.compile import compile_file

    meta = (
        "style:\n"
        "  charts:\n"
        "    title:\n"
        "      font:\n"
        "        color: '#222222'\n"
        "    marks:\n"
        "      bar:\n"
        "        border:\n"
        "          color: '#00ff00'\n"
    )
    board = _BOARD_TEMPLATE.format(
        style="", chart_type="bar", chart_fields=_CARTESIAN_FIELDS
    ).replace("style:\n\n", "")
    project = in_memory_project(
        Path("/tmp/test"), {"charts/board.yml": board, "charts/meta.yml": meta}
    )

    result = compile_file(project.path("charts/board.yml").read_board())

    assert result.success, result.errors
    assert result.board is not None
    ctx = result.board.chart_style_context
    assert ctx.title.font.color == "#222222"
    assert ctx.bar.marks.bar.border is not None
    assert ctx.bar.marks.bar.border.color == "#00ff00"


def test_style_title_precedence_charts_title_beats_root_title() -> None:
    """style.title < style.charts.title < a chart's own style.title."""
    board = _compile(
        _BOARD_TEMPLATE.format(
            style=textwrap.indent(
                "title:\n"
                "  font:\n"
                "    color: '#111111'\n"
                "charts:\n"
                "  title:\n"
                "    font:\n"
                "      color: '#222222'\n",
                "  ",
            ),
            chart_type="bar",
            chart_fields=_CARTESIAN_FIELDS,
        )
    )
    ctx = board.chart_style_context
    assert ctx.title.font.color == "#222222"


def test_style_title_precedence_chart_own_title_beats_charts_title() -> None:
    from dbt_charts.core.compile.models.chart.normalized import BarChart
    from dbt_charts.core.compile.models.style.authored import (
        BarChartStylePatch,
        ChartsStylePatch,
        ChartTitleStylePatch,
        StylePatch,
    )
    from dbt_charts.core.compile.resolve.style.board import resolve_chart_style_context
    from dbt_charts.core.compile.resolve.style.chart_context import (
        build_chart_style_context,
    )

    board_patch = StylePatch(
        charts=ChartsStylePatch(title=ChartTitleStylePatch(font={"color": "#222222"}))
    )
    board = resolve_chart_style_context(get_theme_style(), board_patch)
    assert board.title.font.color == "#222222"

    chart_patch = BarChartStylePatch(
        title=ChartTitleStylePatch(font={"color": "#333333"})
    )
    effective = build_chart_style_context(
        board, BarChart(id="c", type="bar", style=chart_patch)
    )
    assert effective.title.font.color == "#333333"


def test_style_title_field_reaches_charts_title_alongside_charts_title_field() -> None:
    """A leaf set only on style.title still reaches ctx.title."""
    board = _compile(
        _BOARD_TEMPLATE.format(
            style=textwrap.indent(
                "title:\n"
                "  font:\n"
                "    style: italic\n"
                "charts:\n"
                "  title:\n"
                "    font:\n"
                "      color: '#222222'\n",
                "  ",
            ),
            chart_type="bar",
            chart_fields=_CARTESIAN_FIELDS,
        )
    )
    ctx = board.chart_style_context
    assert ctx.title.font.style == "italic"
    assert ctx.title.font.color == "#222222"


def test_charts_title_fill_is_independent_of_patch_order() -> None:
    """Both patches reach the chart title in either order."""
    from dbt_charts.core.compile.models.style.authored import (
        ChartsStylePatch,
        ChartTitleStylePatch,
        StylePatch,
    )
    from dbt_charts.core.compile.resolve.style.board import resolve_chart_style_context

    base = get_theme_style()
    color_patch = StylePatch(
        charts=ChartsStylePatch(title=ChartTitleStylePatch(font={"color": "#222222"}))
    )
    family_patch = StylePatch.model_validate({"title": {"font": {"style": "italic"}}})

    forward = resolve_chart_style_context(base, color_patch, family_patch)
    backward = resolve_chart_style_context(base, family_patch, color_patch)

    assert forward.title == backward.title
    for ctx in (forward, backward):
        assert ctx.title.font.color == "#222222"
        assert ctx.title.font.style == "italic"


def test_charts_title_writes_at_two_cascade_levels_both_survive() -> None:
    """Writes from two cascade levels both land; a later conflict wins."""
    from dbt_charts.core.compile.models.style.authored import (
        ChartsStylePatch,
        ChartTitleStylePatch,
        StylePatch,
    )
    from dbt_charts.core.compile.resolve.style.board import resolve_chart_style_context

    def patch(**font: str) -> StylePatch:
        return StylePatch(
            charts=ChartsStylePatch(title=ChartTitleStylePatch(font=font))
        )

    base = get_theme_style()
    both = resolve_chart_style_context(
        base, patch(color="#222222"), patch(family="Helvetica")
    )
    assert both.title.font.color == "#222222"
    assert both.title.font.family.startswith("Helvetica")

    later_wins = resolve_chart_style_context(
        base, patch(color="#222222"), patch(color="#333333")
    )
    assert later_wins.title.font.color == "#333333"


def test_later_null_is_not_undone_by_the_held_write_replay() -> None:
    from dbt_charts.core.compile.config import get_theme_style
    from dbt_charts.core.compile.models.style.authored import StylePatch
    from dbt_charts.core.compile.resolve.style.board import merge_style

    base = get_theme_style()
    assert base.charts.marks.bar.border is not None
    first = StylePatch.model_validate(
        {
            "charts": {
                "marks": {"bar": {"border": {"color": "#111111"}}},
                "title": {"font": {"color": "#222222"}},
            }
        }
    )
    second = StylePatch.model_validate({"charts": {"marks": {"bar": {"border": None}}}})

    merged = merge_style(base, first, second)

    assert merged.charts.marks.bar.border is None
    assert merged.charts.title is not None
    assert merged.charts.title.font.color == "#222222"


def test_later_null_on_a_held_slot_is_not_undone() -> None:
    from dbt_charts.core.compile.models.style.authored import StylePatch
    from dbt_charts.core.compile.resolve.style.board import merge_style

    first = StylePatch.model_validate(
        {"charts": {"title": {"font": {"color": "#222222"}}}}
    )
    second = StylePatch.model_validate({"charts": {"title": None}})

    merged = merge_style(get_theme_style(), first, second)

    assert merged.charts.title is None


def test_switch_slot_charts_color_gradient_still_errors_on_partial_write() -> None:
    """style.charts.color.gradient still validates as a whole on a partial write."""
    from dbt_charts.core.compile.compiler import compile as df_compile

    result = df_compile(
        textwrap.dedent(
            _BOARD_TEMPLATE.format(
                style=textwrap.indent(
                    "charts:\n  color:\n    gradient:\n      min: 0\n", "  "
                ),
                chart_type="bar",
                chart_fields=_CARTESIAN_FIELDS,
            )
        )
    )
    assert not result.success
    assert any("palette" in e.message for e in result.errors)
