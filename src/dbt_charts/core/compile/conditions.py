"""The data-free part of a layout item's ``visible:`` and a variable's ``enabled:``.

Stage: COMPILE
Purpose: Turn a bool or a Jinja expression over variables into a yes/no, and
decide which layout items are drawn, so compile-time width arithmetic and the
render pass share one definition. A query-backed probe needs a data source
and is read by ``render.conditions``.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from typing import Any

from dbt_charts.core.compile.models.board.normalized import LayoutItem, VariableValues
from dbt_charts.core.compile.models.variable.authored import SingleRowBoolProbe
from dbt_charts.core.compile.template.jinja import resolve_jinja_template

Visible = bool | str | SingleRowBoolProbe | None
DrawnPredicate = Callable[[Visible, VariableValues], bool]


def coerce_bool(
    value: Any,  # type-state: explicit_any — rendered text or a DB cell
) -> bool:
    """Coerce value to bool; raise ValueError for unrecognized inputs."""
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        if value == 1:
            return True
        if value == 0:
            return False
    if isinstance(value, str):
        lower = value.strip().lower()
        if lower in ("true", "1", "yes"):
            return True
        if lower in ("false", "0", "no"):
            return False
    raise ValueError(f"Cannot coerce {value!r} to bool; expected true/false/1/0/yes/no")


def eval_literal_condition(
    expr: bool | str | None, if_none: bool, current_values: VariableValues
) -> bool:
    """A condition that needs no data: None is ``if_none``, a bool is itself.

    A string is auto-wrapped in ``{{ }}`` when it carries none (bare variable
    names and expressions are fine), rendered against ``current_values`` and
    coerced to bool.
    """
    if expr is None:
        return if_none
    if isinstance(expr, bool):
        return expr
    jinja_expr = expr if "{{" in expr else f"{{{{ {expr} }}}}"
    return coerce_bool(resolve_jinja_template(jinja_expr, variables=current_values))


def is_drawn(visible: Visible, current_values: VariableValues) -> bool:
    """Whether a layout item with this ``visible:`` is drawn, with no data source.

    A probe cannot be read without one, so it counts as drawn.
    """
    if isinstance(visible, SingleRowBoolProbe):
        return True
    return eval_literal_condition(visible, True, current_values)


def drawn_items(
    items: Sequence[LayoutItem], current_values: VariableValues, drawn: DrawnPredicate
) -> list[LayoutItem]:
    """The items ``drawn`` keeps: a hidden item takes no space."""
    return [item for item in items if drawn(item.visible, current_values)]
