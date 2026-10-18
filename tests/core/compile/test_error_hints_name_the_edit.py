"""The errors agents hit most on a first build name the one edit that fixes them."""

from __future__ import annotations

from dbt_charts.core.compile.compiler import compile as compile_board
from dbt_charts.core.diagnostics.diagnostic import Diagnostic

_BOARD = """\
title: T
queries:
  q:
    type: values
    rows:
      - a: x
        v: 1
charts:
  c:
{chart}
rows:
  - c
{board_style}"""


def _error(chart: str, board_style: str = "") -> Diagnostic:
    result = compile_board(
        _BOARD.format(chart=chart, board_style=board_style), file="b.yml"
    )
    (error,) = result.errors
    return error


def _text(error: Diagnostic) -> str:
    return f"{error.message} {error.hint or ''}"


def test_table_height_names_page_rows_and_the_layout_row() -> None:
    error = _error("    type: table\n    query: q\n    height: 300")

    assert error.hint is not None
    assert error.hint.startswith("A table is as tall as its rows: delete `height:`.")
    assert "`style.pagination.page_rows: 10`" in error.hint
    assert "rows:\n  - height: 300\n    rows:\n      - c" in error.hint


def test_table_height_under_board_style_names_the_same_edit() -> None:
    error = _error(
        "    type: table\n    query: q",
        "style:\n  charts:\n    table:\n      height: 300\n",
    )

    assert error.hint is not None
    assert error.hint.startswith("A table is as tall as its rows")
    assert "on the chart itself" not in error.hint


def test_family_name_inside_a_charts_own_style_says_write_keys_directly() -> None:
    error = _error(
        "    type: bar\n    query: q\n    x: a\n    y: v\n"
        "    style:\n      bar:\n        orientation: horizontal"
    )

    assert error.hint == (
        "Inside one chart's `style`, write the bar keys directly: delete the "
        "`bar:` line and move its keys up one level, e.g. "
        "`charts.c.style.orientation`. `style.charts.bar` is the board-wide form."
    )


def test_border_on_a_family_without_a_card_names_style_charts_border() -> None:
    error = _error(
        "    type: bar\n    query: q\n    x: a\n    y: v",
        "style:\n  charts:\n    bar:\n      border: 1px solid gray\n",
    )

    assert error.hint is not None
    assert error.hint.startswith("Delete `style.charts.bar.border`")
    assert "`style.charts.border`" in error.hint


def test_scalar_padding_writes_the_four_sides() -> None:
    error = _error(
        "    type: kpi\n    query: q\n    value: v\n    style:\n      padding: 8"
    )

    assert error.hint is not None
    assert "padding:\n  top: 8\n  right: 8\n  bottom: 8\n  left: 8" in error.hint


def test_heading_sizes_scalar_writes_the_six_size_list() -> None:
    error = _error(
        "    type: kpi\n    query: q\n    value: v",
        "style:\n  fonts:\n    heading:\n      sizes: 20\n",
    )

    assert error.hint is not None
    assert "a list of six sizes" in error.hint
    assert "`sizes: [" in error.hint


def test_axis_label_boolean_names_the_title_visible_switch() -> None:
    error = _error(
        "    type: bar\n    query: q\n    x: a\n    y: v\n    x_label: false"
    )

    assert error.hint is not None
    assert 'x_label: "false"' not in error.hint
    assert "`style.axis_x.title.visible: false`" in error.hint


def test_axis_label_mapping_names_the_text_and_the_title_style() -> None:
    error = _error(
        "    type: bar\n    query: q\n    x: a\n    y: v\n    x_label:\n      text: A"
    )

    assert error.hint is not None
    assert '`x_label: "A"`' in error.hint
    assert "`style.axis_x.title`" in error.hint


def test_missing_type_says_add_a_type_line() -> None:
    error = _error("    query: q\n    x: a\n    y: v")

    assert "Add `type:` to `charts.c`" in error.message
    assert "bar, histogram" in error.message


def test_missing_type_with_a_family_key_names_type_and_the_outdent() -> None:
    error = _error("    bar:\n      query: q\n      x: a\n      y: v")

    assert "Write `type: bar` under `charts.c`" in error.message
    assert "move the keys under `bar:` up one level" in error.message


def test_missing_type_with_a_type_like_key_names_the_rename() -> None:
    error = _error("    kind: bar\n    query: q\n    x: a\n    y: v")

    assert "Rename `kind:` to `type:`" in error.message
