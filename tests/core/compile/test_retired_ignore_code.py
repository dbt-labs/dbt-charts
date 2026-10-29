"""A board that suppresses a removed warning code still loads, with one diagnostic."""

from __future__ import annotations

import warnings

import pytest

from dbt_charts.core.compile import compile

_BOARD = """
source: examples_db
queries:
  q:
    sql: SELECT 1 AS x, 2 AS y
    ignore: [{query_code}]
charts:
  c:
    query: q
    type: bar
    x: x
    y: y
    warnings_ignore: [{chart_code}]
rows:
  - c
"""


@pytest.mark.parametrize(
    "code",
    [
        "WARN-LIKELY-CURRENCY-OR-PERCENT-MISSING-FORMATTER",
        "WARN-REDUNDANT-AUTHORED-DEFAULT",
        "WARN-REDUNDANT-AUTHORED-LABEL",
    ],
)
def test_ignoring_a_retired_warning_loads_and_reports_a_diagnostic(code: str) -> None:
    with warnings.catch_warnings():
        warnings.simplefilter("error")
        result = compile(_BOARD.format(query_code=code, chart_code=code))
    assert result.success, result.errors
    retired = [w for w in result.warnings if w.code == "WARN-RETIRED-IGNORE-CODE"]
    assert [(w.path, w.level) for w in retired] == [
        ("charts.c.warnings_ignore", "warning"),
        ("queries.q.ignore", "warning"),
    ]
    assert all(code in w.message for w in retired)


_CODE = "WARN-RETIRED-IGNORE-CODE"
_RETIRED = "WARN-REDUNDANT-AUTHORED-LABEL"


@pytest.mark.parametrize(
    "layout",
    [
        "rows:\n  - cols:\n      - c\n      - d",
        "rows:\n  - cols:\n      - c\n      - d\n  - cols:\n      - c\n      - d",
    ],
    ids=["one-group", "two-groups"],
)
def test_nested_groups_report_a_retired_chart_ignore_once(layout: str) -> None:
    board = f"""
source: examples_db
queries:
  q:
    sql: SELECT 1 AS x, 2 AS y
    ignore: [{_RETIRED}]
charts:
  c:
    query: q
    type: bar
    x: x
    y: y
    warnings_ignore: [{_RETIRED}]
  d:
    query: q
    type: bar
    x: x
    y: y
{layout}
"""
    result = compile(board)
    assert result.success, result.errors
    paths = [w.path for w in result.warnings if w.code == _CODE]
    assert paths == ["charts.c.warnings_ignore", "queries.q.ignore"]
