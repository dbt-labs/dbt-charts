"""JSON render output format.

Serializes the dict produced by board_to_dict as JSON.
"""

import json
from collections.abc import Mapping
from datetime import date, datetime
from decimal import Decimal
from typing import Any

from pydantic import BaseModel

from dbt_charts.core.compile.models.board.normalized import (
    Board,
    VariableValues,
)
from dbt_charts.core.diagnostics import Diagnostic
from dbt_charts.core.execute.chart_data_provider import ResolveDataProvider
from dbt_charts.core.render.board_to_dict import NO_ROW_CAP, board_to_dict


def _json_default(obj: Any) -> Any:
    """Handle non-serializable types.

    Deliberately not ``board_to_dict.clean_value``, despite the overlap: this
    path renders ``Decimal`` as an exact string, while ``clean_value`` renders
    it as int/float for the ``yaml``/``text``/``data`` formats. ``yaml``'s
    re-compile contract needs a numeric column to stay numeric; ``json`` has no
    such contract and keeps full precision instead — a ``NUMERIC(38,2)`` value
    survives here that would lose its last digits through ``float``. Collapsing
    the two would have to pick one of those, silently breaking the other.
    """
    if isinstance(obj, BaseModel):
        return obj.model_dump(exclude_none=True)
    if isinstance(obj, Mapping):
        return dict(obj)
    if isinstance(obj, set):
        return sorted(obj)
    if isinstance(obj, Decimal):
        return str(obj)
    if isinstance(obj, (date, datetime)):
        return obj.isoformat()
    raise TypeError(f"Object of type {type(obj).__name__} is not JSON serializable")


def render_board_json(
    board: Board,
    results: ResolveDataProvider,
    variables: VariableValues,
    error_collector: list[Diagnostic] | None = None,
    max_rows_per_query: int = NO_ROW_CAP,
) -> str:
    """Render a compiled board to JSON.

    Walks the layout tree, executes queries, resolves charts
    (auto type, auto fields), and returns a JSON string with
    the resolved chart semantics and executed data. A row cap
    marks capped items with ``rows_truncated: {head, tail, total}``.
    """
    return json.dumps(
        board_to_dict(board, results, variables, error_collector, max_rows_per_query),
        default=_json_default,
        indent=2,
    )
