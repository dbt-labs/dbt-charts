"""Boolean conditions authored in YAML — ``enabled``, ``visible``.

Stage: RENDER
Purpose: Turn a bool, a Jinja expression, or a single-row query probe into the
yes/no a renderer needs.

Three surfaces share this: a variable's ``enabled``, a layout item's
``visible``, and the terminal renderer's copy of the same question. They differ
only in what a missing condition means, which is why ``if_none`` is a parameter
rather than a default baked in here.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from dbt_charts.core.compile.conditions import (
    Visible,
    coerce_bool,
    eval_literal_condition,
    is_drawn,
)
from dbt_charts.core.compile.models.board.normalized import VariableValues
from dbt_charts.core.compile.models.variable.authored import SingleRowBoolProbe
from dbt_charts.core.render.board_variables import current_board_data_provider

if TYPE_CHECKING:
    from dbt_charts.core.execute.chart_data_provider import ChartDataProvider


def eval_bool_condition(
    expr: bool | str | SingleRowBoolProbe | None,
    if_none: bool,
    current_values: dict[str, Any],
    executor: ChartDataProvider | None,
    context: str,
) -> bool:
    """Evaluate a bool/str/probe condition and return the result.

    Shared core for Variable.enabled and LayoutItem.visible; callers pass
    ``if_none`` to control the meaning of a missing (None) value.

    - None, bool, str: ``eval_literal_condition``.
    - SingleRowBoolProbe: execute the named query; expect exactly 1 row; coerce
      the named column to bool.

    Args:
        expr: The condition value (from YAML or a compiled model field).
        if_none: Value to return when expr is None.
        current_values: Current variable values for Jinja resolution.
        executor: Required when expr is a SingleRowBoolProbe.
        context: Human-readable label for error messages (e.g. "Variable 'region' disabled").

    Raises:
        ValueError: Wrong row count, missing column, non-bool-coercible value, or
            no executor for a query-backed condition.
    """
    if not isinstance(expr, SingleRowBoolProbe):
        return eval_literal_condition(expr, if_none, current_values)
    if executor is None:
        raise ValueError(
            f"{context} query '{expr.query}' requires an executor but none was provided"
        )
    rows = executor.execute_query(expr.query, current_values)
    if not rows:
        raise ValueError(
            f"{context} query '{expr.query}' returned no rows; expected exactly 1"
        )
    if len(rows) > 1:
        raise ValueError(
            f"{context} query '{expr.query}' returned {len(rows)} rows; expected exactly 1"
        )
    row = rows[0]
    if expr.column not in row:
        raise ValueError(
            f"{context} query '{expr.query}' has no column '{expr.column}'; "
            f"available: {list(row.keys())}"
        )
    return coerce_bool(row[expr.column])


def evaluate_visible(
    expr: bool | str | SingleRowBoolProbe | None,
    current_values: dict[str, Any],
    executor: ChartDataProvider | None,
    context: str = "layout item",
) -> bool:
    """Return True when the layout item should be rendered.

    Args:
        expr: The visible value from the layout item.
        current_values: Current variable values for Jinja resolution.
        executor: Required when expr is a SingleRowBoolProbe.
        context: Human-readable label for error messages (e.g. "layout item 'kpi_a'").
    """
    return eval_bool_condition(
        expr,
        True,
        current_values,
        executor,
        f"{context} visible",
    )


def item_is_drawn(visible: Visible, current_values: VariableValues) -> bool:
    """Whether a layout item with this ``visible:`` is painted.

    A query-backed probe is read through the scoped data provider; with none
    scoped (compile time, the static skeleton) it counts as drawn.
    """
    provider = current_board_data_provider()
    if isinstance(visible, SingleRowBoolProbe) and provider is not None:
        return evaluate_visible(visible, current_values, provider)
    return is_drawn(visible, current_values)
