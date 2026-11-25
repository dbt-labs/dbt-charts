"""A table's ``style.wrap`` of ``false`` or ``true`` becomes ``max_lines: 1`` or ``all`` at 0.13.0."""

from __future__ import annotations

import warnings

import pytest

from dbt_charts.core.compile import compile as compile_board
from dbt_charts.core.compile.migrations import (
    SchemaMigrationWarning,
    migrate_yaml_text,
)
from dbt_charts.core.compile.resolve import resolve

_TABLE = """
queries:
  q:
    type: values
    rows:
      - {a: x}
%(board_style)s
charts:
  t:
    type: table
    query: q
%(chart_style)s
rows:
  - t
"""

_UNSET = ""


def _wrap(board_style: str = _UNSET, chart_style: str = _UNSET) -> int | str:
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", SchemaMigrationWarning)
        result = compile_board(
            _TABLE % {"board_style": board_style, "chart_style": chart_style}
        )
    assert result.success, result.errors
    assert result.board is not None
    resolved = resolve(
        result.board.charts["t"],
        [{"a": "x"}],
        chart_style_context=result.board.chart_style_context,
        width=600.0,
    )
    return resolved.style.table.max_lines


@pytest.mark.parametrize(("wrap", "mode"), [("false", 1), ("true", "all")])
def test_a_chart_s_wrap_keeps_its_rendering(wrap: str, mode: int | str) -> None:
    assert _wrap(chart_style=f"    style:\n      wrap: {wrap}\n") == mode


@pytest.mark.parametrize(("wrap", "mode"), [("false", 1), ("true", "all")])
def test_the_board_tier_wrap_keeps_its_rendering(wrap: str, mode: int | str) -> None:
    board_style = f"style:\n  charts:\n    table:\n      wrap: {wrap}\n"
    assert _wrap(board_style=board_style) == mode


def test_a_board_that_authored_neither_takes_the_trim_outliers_default() -> None:
    assert _wrap() == "trim_outliers"


@pytest.mark.parametrize(
    "value", ["trim_outliers", "p75", "p100", "square", "all", 1, 4]
)
def test_max_lines_is_authored_directly(value: int | str) -> None:
    style = f"    style:\n      max_lines: {value}\n"
    assert _wrap(chart_style=style) == value


def test_a_nested_board_s_wrap_keeps_its_rendering() -> None:
    nested = """
queries:
  q:
    type: values
    rows:
      - {a: x}
charts:
  t:
    type: table
    query: q
rows:
  - style:
      charts:
        table:
          wrap: false
    rows:
      - t
"""
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", SchemaMigrationWarning)
        result = compile_board(nested)
    assert result.success, result.errors
    assert result.board is not None
    inner = result.board.layout.items[0].board
    assert inner.chart_style_context.table.max_lines == 1
    assert result.board.chart_style_context.table.max_lines == "trim_outliers"


@pytest.mark.parametrize(("wrap", "mode"), [("false", "1"), ("true", "all")])
def test_the_text_migration_respells_a_tab_pane_wrap(wrap: str, mode: str) -> None:
    text = (
        "title: T\ntabs:\n  items:\n    - title: a\n      style:\n"
        f"        charts:\n          table:\n            wrap: {wrap}\n"
        "      rows:\n        - text: hi\n"
    )
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", SchemaMigrationWarning)
        migrated = migrate_yaml_text(text)
    assert f"max_lines: {mode}" in migrated
    assert "wrap:" not in migrated
