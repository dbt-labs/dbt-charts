import pytest

from dbt_charts.core.compile import compile

_BOARD = """
queries:
  data:
    columns: [segment, revenue]
    values:
      - ["A", 100]
charts:
  my_pie:
    query: data
    type: pie
    theta: revenue
    style:
      marks:
        slice:
          labels:
            where: WHERE
rows:
  - my_pie
"""


@pytest.mark.parametrize("where", ['""', '"  "', '"{{ }}"'])
def test_an_empty_where_is_rejected_by_name(where: str) -> None:
    result = compile(_BOARD.replace("WHERE", where))

    assert not result.success
    [error] = result.errors
    assert error.path == "charts.my_pie.pie.style.marks.slice.labels.where"
    assert "must be a non-empty expression or be omitted" in error.message
