"""Values queries: every column's inline literal must declare one type."""

from __future__ import annotations

from dbt_charts.core.column_types import literal_schema
from dbt_charts.core.compile.models.board.normalized import Board
from dbt_charts.core.compile.models.query.normalized import ValuesQuery
from dbt_charts.core.compile.validate.walk import board_queries, boards


def validate_values_queries(board: Board) -> None:
    """Reject a values query whose column mixes kinds or holds only nulls, on
    this board or any nested board.

    Raises:
        ExecutionError: ERR-RESULT-COLUMN-TYPE naming the column.
    """
    checked: set[int] = set()
    for each in boards(board):
        for query in board_queries(each):
            if isinstance(query, ValuesQuery) and id(query) not in checked:
                checked.add(id(query))
                literal_schema(query.rows)
