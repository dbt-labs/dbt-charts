"""A layout name that is not a chart key says which edit fixes it."""

from __future__ import annotations

from dbt_charts.core.compile.compiler import compile

BOARD = """\
title: Films
queries:
  q:
    sql: SELECT 1 AS a
charts:
  annual_gross:
    type: bar
    query: q
  monthly_count:
    type: bar
    query: q
rows:
  - {ref}
  - monthly_count
"""


def _layout_error_hint(ref: str) -> str:
    result = compile(BOARD.format(ref=ref), file="f.yml")
    (error,) = result.errors
    assert error.code == "ERR-UNRESOLVED-REFERENCE"
    assert error.hint is not None
    return error.hint


def test_a_close_typo_names_the_chart_key_to_write() -> None:
    hint = _layout_error_hint("annual_gros")

    assert hint.startswith("Did you mean `annual_gross`?")
    assert "`annual_gross`, `monthly_count`" in hint


def test_a_name_with_no_close_key_lists_the_chart_keys() -> None:
    hint = _layout_error_hint("revenue_by_region")

    assert "Did you mean" not in hint
    assert "`annual_gross`, `monthly_count`" in hint
    assert "under `charts:`" in hint
