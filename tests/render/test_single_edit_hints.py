"""Misplaced or mistyped keys fail with the one edit that fixes them."""

from __future__ import annotations

from pathlib import Path

import pytest
from typer.testing import CliRunner

from dbt_charts.cli.main import app

_HEAD = """\
{board_head}source: d
queries:
  q:
    type: values
    columns: [origin, n]
    values:
      - [USA, 3]
      - [Japan, 2]
charts:
  c:
    type: bar
    query: q
    x: origin
    y: n
{chart_tail}rows:
  - c
"""


@pytest.fixture(autouse=True)
def in_project(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    (tmp_path / "dbt_charts.yml").write_text("{}\n")
    monkeypatch.chdir(tmp_path)


def _check(tmp_path: Path, board_head: str = "title: T\n", chart_tail: str = "") -> str:
    board = tmp_path / "b.yml"
    board.write_text(_HEAD.format(board_head=board_head, chart_tail=chart_tail))
    result = CliRunner().invoke(app, ["render", str(board), "--format", "text"])
    return " ".join(result.output.split())


def _position(orientation: str, position: str) -> str:
    return (
        f"    style:\n{orientation}      marks:\n        bar:\n          labels:\n"
        f"            position: {position}\n"
    )


def test_root_subtitle_names_text_with_the_authors_value(tmp_path: Path) -> None:
    out = _check(tmp_path, 'title: T\nsubtitle: "Cars: by origin"\n')

    assert 'text: "Cars: by origin"' in out
    assert "no `subtitle`" in out
    assert "Allowed keys" not in out


def test_root_subtitle_fix_is_clean(tmp_path: Path) -> None:
    out = _check(tmp_path, 'title: T\ntext: "Cars by origin"\n')

    assert "ERR-" not in out


def test_horizontal_bar_right_names_above(tmp_path: Path) -> None:
    out = _check(
        tmp_path, chart_tail=_position("      orientation: horizontal\n", "right")
    )

    assert "horizontal bar" in out
    assert "write `position: above`" in out
    assert "middle_aligned" not in out


def test_vertical_bar_outside_names_above(tmp_path: Path) -> None:
    out = _check(
        tmp_path, chart_tail=_position("      orientation: vertical\n", "outside")
    )

    assert "vertical bar" in out
    assert "write `position: above`" in out


def test_vertical_bar_left_says_vertical_bars_have_no_sides(tmp_path: Path) -> None:
    out = _check(
        tmp_path, chart_tail=_position("      orientation: vertical\n", "left")
    )

    assert "no left or right" in out


def test_unknown_word_keeps_the_allowed_list(tmp_path: Path) -> None:
    out = _check(tmp_path, chart_tail=_position("", "sideways"))

    assert "'above', 'top', 'middle'" in out


def test_tooltip_visible_says_tooltips_cannot_be_hidden(tmp_path: Path) -> None:
    out = _check(
        tmp_path, "title: T\nstyle:\n  charts:\n    tooltip:\n      visible: false\n"
    )

    assert "no `visible`" in out
    assert "hover_emphasis" not in out


def test_empty_cols_in_rows_says_to_indent_the_items(tmp_path: Path) -> None:
    board = tmp_path / "b.yml"
    board.write_text("title: T\nrows:\n  - cols:\n  - c\n")
    out = " ".join(
        CliRunner()
        .invoke(app, ["render", str(board), "--format", "text"])
        .output.split()
    )

    assert "`cols:` is empty" in out
    assert "indent" in out


def test_number_where_string_expected_shows_the_quoted_value(tmp_path: Path) -> None:
    out = _check(tmp_path, chart_tail="    x_label: 2013\n")

    assert 'x_label: "2013"' in out


def test_variant_typo_names_the_nearest_value(tmp_path: Path) -> None:
    board = tmp_path / "b.yml"
    board.write_text(
        "title: T\nsource: d\nqueries:\n  q:\n    type: values\n    columns: [n]\n"
        "    values:\n      - [3]\ncharts:\n  k:\n    type: kpi\n    query: q\n"
        "    value: n\n    style:\n      variant: compact_1\nrows:\n  - k\n"
    )
    out = " ".join(
        CliRunner()
        .invoke(app, ["render", str(board), "--format", "text"])
        .output.split()
    )

    assert "Write `variant: compact`" in out


def _check_file(tmp_path: Path, text: str) -> str:
    board = tmp_path / "b.yml"
    board.write_text(text)
    result = CliRunner().invoke(app, ["render", str(board), "--format", "text"])
    return " ".join(result.output.split())


_TABLE_BOARD = """\
title: T
source: d
queries:
  q:
    type: values
    columns: [n]
    values:
      - [3]
charts:
  t:
    type: table
    query: q
{tail}rows:
  - t
"""


def test_table_height_key_names_the_chart_height(tmp_path: Path) -> None:
    out = _check_file(
        tmp_path,
        "title: T\nstyle:\n  charts:\n    table:\n      default_height: 300\n",
    )

    assert "delete `default_height:`" in out
    assert "belongs at `style.charts.kpi`" not in out


def test_unquoted_colon_title_shows_the_quoted_line(tmp_path: Path) -> None:
    out = _check_file(tmp_path, "title: Share of workers: 1950\nrows: []\n")

    assert 'Write: title: "Share of workers: 1950"' in out


def test_double_alias_after_a_query_reference_says_to_drop_the_alias(
    tmp_path: Path,
) -> None:
    (tmp_path / "data.csv").write_text("n\n1\n")
    out = _check_file(
        tmp_path,
        "title: T\nsource: data.csv\nqueries:\n  base: SELECT 1 AS n\n"
        "  q: SELECT n FROM {{ queries.base }} AS b\n"
        "charts:\n  k:\n    type: kpi\n    query: q\n    value: n\nrows:\n  - k\n",
    )

    assert "delete the `AS" in out


def test_number_format_on_a_year_axis_names_the_time_spec(tmp_path: Path) -> None:
    out = _check_file(
        tmp_path,
        "title: T\nsource: d\nqueries:\n  q:\n    type: values\n"
        "    columns: [day, n]\n    values:\n      - [2020-01-01, 3]\n"
        "      - [2021-01-01, 4]\ncharts:\n  c:\n    type: line\n    query: q\n"
        "    x: day\n    y: n\n    style:\n      axis_x:\n        labels:\n"
        "          format: d\nrows:\n  - c\n",
    )

    assert 'format: "%Y"' in out


@pytest.mark.parametrize("word", ["No", "Yes", "On", "Off", "0.50"])
def test_coerced_word_is_quoted_as_the_author_typed_it(
    tmp_path: Path, word: str
) -> None:
    out = _check(tmp_path, chart_tail=f"    subtitle: {word}  # note\n")

    assert f'subtitle: "{word}"' in out
