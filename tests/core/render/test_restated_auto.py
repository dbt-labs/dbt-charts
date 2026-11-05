"""Render-time restated checks plan their late queries up front."""

import pytest

from dbt_charts.core.compile.compiler import compile
from dbt_charts.core.compile.models.board.normalized import Board
from dbt_charts.core.render.restated_auto import plan_extra

_BOARD = """
source: cars
variables:
  first_year:
    options:
      static: [1970, 1971]
    input: select
  region:
    input: select
    options:
      query: region_options
queries:
  region_options:
    sql: SELECT DISTINCT region FROM cars
  q:
    sql: SELECT model_year, COUNT(*) AS n FROM cars WHERE {{ filter('model_year', first_year, '>=') }} GROUP BY 1
charts:
  c:
    type: bar
    query: q
    x: model_year
    y: n
rows:
  - c
"""


@pytest.fixture
def board() -> Board:
    result = compile(_BOARD)
    assert result.board is not None, result.errors
    return result.board


def test_plan_extra_names_the_probe_and_the_control_stripped_queries(
    board: Board,
) -> None:
    values = {"first_year": 1970, "region": "EU"}
    extra = plan_extra(board, values)
    (probe,) = extra.definitions
    assert extra.definitions[probe].sql == (
        "SELECT DISTINCT model_year FROM cars ORDER BY model_year"
    )
    assert (frozenset({probe}), values) in extra.calls
    assert (frozenset({"region_options"}), {"first_year": 1970}) in extra.calls
