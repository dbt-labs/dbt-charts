"""WARN-UNREFERENCED-QUERY: a query nothing reads."""

from __future__ import annotations

import pytest

from dbt_charts.core.compile import compile

_HEAD = """
source: examples_db
queries:
  used: SELECT 1 AS x, 2 AS y
{extra_queries}"""

_CHART = """
charts:
  c:
    query: used
    type: bar
    x: x
    y: y
"""


def _unreferenced(yaml: str) -> list[str]:
    result = compile(yaml)
    assert result.success, result.errors
    return [w.message for w in result.warnings if w.code == "WARN-UNREFERENCED-QUERY"]


def _board(extra_queries: str = "", rest: str = "rows:\n  - c\n") -> str:
    return _HEAD.format(extra_queries=extra_queries) + _CHART + rest


def test_unused_query_warns() -> None:
    assert _unreferenced(_board("  spare: SELECT 3 AS z\n")) == [
        "Query `spare` is not used by any chart, variable, text or query; remove it."
    ]


def test_the_charts_own_query_is_used() -> None:
    assert _unreferenced(_board()) == []


@pytest.mark.parametrize(
    ("extra_queries", "rest"),
    [
        pytest.param(
            "  region_options: SELECT 'a' AS region\n",
            "variables:\n  region:\n    options:\n      query: region_options\n"
            "      column: region\nrows:\n  - c\n",
            id="variable options",
        ),
        pytest.param(
            "  region_options: SELECT 'a' AS region\n",
            "variables:\n  region:\n    options:\n      query: queries.region_options\n"
            "      column: region\nrows:\n  - c\n",
            id="variable options named with the queries. prefix",
        ),
        pytest.param(
            "  base: SELECT 1 AS n\n  derived: SELECT n FROM {{ queries.base }}\n",
            "rows:\n  - c\ntext: 'n={{ queries.derived.n }}'\n",
            id="query composed into another that text reads",
        ),
        pytest.param(
            "  headline: SELECT 5 AS n\n",
            "title: 'Total {{ queries.headline.n }}'\nrows:\n  - c\n",
            id="board title",
        ),
        pytest.param(
            "  headline: SELECT 5 AS n\n",
            "rows:\n  - c\ntext: 'Total {{ queries.headline.n }}'\n",
            id="board text",
        ),
    ],
)
def test_other_readers_use_a_query(extra_queries: str, rest: str) -> None:
    assert _unreferenced(_board(extra_queries, rest)) == []


def test_a_second_chart_uses_its_query() -> None:
    yaml = _board("  second: SELECT 1 AS x, 3 AS y\n").replace(
        "rows:\n  - c\n",
        "  d:\n    query: second\n    type: bar\n    x: x\n    y: y\nrows:\n  - c\n  - d\n",
    )
    assert _unreferenced(yaml) == []


def test_query_read_only_by_a_nested_board_is_used() -> None:
    yaml = _board(
        "  inner: SELECT 7 AS n\n",
        "rows:\n  - c\n  - text: 'n={{ queries.inner.n }}'\n",
    )
    assert _unreferenced(yaml) == []


def test_unused_query_defined_in_a_nested_board_warns() -> None:
    yaml = _board(
        rest="rows:\n  - c\n  - queries:\n      spare: SELECT 3 AS z\n    text: hi\n",
    )
    assert _unreferenced(yaml) == [
        "Query `spare` is not used by any chart, variable, text or query; remove it."
    ]


def test_chart_focus_judges_the_unfocused_board() -> None:
    yaml = _board(
        "  headline: SELECT 5 AS n\n  spare: SELECT 3 AS z\n",
        "chart_focus: c\ntitle: 'Total {{ queries.headline.n }}'\nrows:\n  - c\n",
    )
    assert _unreferenced(yaml) == [
        "Query `spare` is not used by any chart, variable, text or query; remove it."
    ]
