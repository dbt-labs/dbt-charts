"""Checks on ``style.conditional`` rules and their aggregate operands, shared by every family."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from datetime import date
from typing import Any, TypeVar

from dbt_charts.core.compile.models.aggregate import AggregateAnchor
from dbt_charts.core.compile.models.conditional_predicate import (
    PREDICATE_KEYS,
    ConditionalPredicate,
)
from dbt_charts.core.compile.resolve.chart._annotation_stats import aggregate
from dbt_charts.core.utils import coerce_numeric_cell

_PREDICATE_FIELDS = frozenset(PREDICATE_KEYS) | {"in_"}


def _patched_properties(
    node: Mapping[str, Any],  # type-state: explicit_any — dumped rule tree
    prefix: str = "",
) -> set[str]:
    """Dotted paths of the leaves a rule sets."""
    paths: set[str] = set()
    for key, value in node.items():
        path = f"{prefix}.{key}" if prefix else key
        if isinstance(value, Mapping):
            paths |= _patched_properties(value, path)
        else:
            paths.add(path)
    return paths


_Rows = Sequence[Mapping[str, Any]]  # type-state: explicit_any — query rows
R = TypeVar("R", bound=ConditionalPredicate)
_COMPARISONS = ("eq", "ne", "lt", "lte", "gt", "gte")


def _name(call: AggregateAnchor) -> str:
    return (
        f"percentile({call.percent:g})" if call.fn == "percentile" else f"{call.fn}()"
    )


def _computed(
    call: AggregateAnchor,
    cells: Sequence[Any],  # type-state: explicit_any — non-null cells
    where: str,
) -> Any:  # type-state: explicit_any — a cell, or a number
    """``call`` over ``cells``. ``min()``/``max()`` give the extreme cell itself,
    so an ``eq`` compares like with like; the rest need numbers."""
    numbers = [coerce_numeric_cell(cell) for cell in cells]
    if call.fn in ("min", "max"):
        pick = min if call.fn == "min" else max
        if all(n is not None for n in numbers):
            return cells[numbers.index(pick(n for n in numbers if n is not None))]
        if all(isinstance(cell, date) for cell in cells):
            return pick(cells)
        raise ValueError(
            f"{where}: {_name(call)} needs a numeric or date column, got "
            f"{next(c for c, n in zip(cells, numbers, strict=True) if n is None)!r}"
        )
    values = [n for n in numbers if n is not None]
    if len(values) != len(cells):
        raise ValueError(
            f"{where}: {_name(call)} needs a numeric column, got "
            f"{next(c for c, n in zip(cells, numbers, strict=True) if n is None)!r}"
        )
    return aggregate(call.fn, call.percent, values)


def resolve_rule_aggregates(
    rules: Sequence[R], column: str, data: _Rows, where: str
) -> list[R]:
    """``rules`` with each aggregate operand computed over every row's ``column``.

    A rule whose column holds no value matches no row, so it is dropped.
    """
    cells = [row[column] for row in data if row.get(column) is not None]
    out: list[R] = []
    for rule in rules:
        if not has_aggregate(rule):
            out.append(rule)
            continue
        if not cells:
            continue
        update: dict[str, Any] = {  # type-state: explicit_any — computed operands
            key: _computed(value, cells, where)
            for key in _COMPARISONS
            if isinstance(value := getattr(rule, key), AggregateAnchor)
        }
        if rule.between is not None:
            update["between"] = [
                _computed(b, cells, where) if isinstance(b, AggregateAnchor) else b
                for b in rule.between
            ]
        out.append(rule.model_copy(update=update))
    return out


def has_aggregate(rule: ConditionalPredicate) -> bool:
    operands = [getattr(rule, key) for key in _COMPARISONS]
    if rule.between is not None:
        operands += rule.between
    return any(isinstance(o, AggregateAnchor) for o in operands)


def resolve_mark_conditional(
    conditional: Mapping[str, Sequence[R]] | None,
    data: _Rows,
    unsupported: Sequence[str] = (),
    where: str = "style.conditional",
    y: str | list[str] | None = None,
) -> dict[str, list[R]] | None:
    """The rules with their aggregates computed, after refusing a rule column the
    query lacks (``where`` names the block), a property under ``unsupported`` (one the family accepts in the
    rule type but never draws), or two columns that patch the same property.

    Mark families paint one mark per row, so two columns' rules writing one
    property would make the winner depend on key order. A multi-y ``y`` list
    folds a row into one mark per measure, and a rule on a measure paints only
    that measure's marks, so two measures may patch the same property.
    """
    if not conditional:
        return None if conditional is None else {}
    measures = set(y) if isinstance(y, list) else set()
    available = set(data[0]) if data else set()
    owner: dict[str, str] = {}
    for column, rules in conditional.items():
        if available and column not in available:
            raise ValueError(
                f"{where} targets column '{column}' which is not in the "
                f"query result. Available: {sorted(available)}"
            )
        for rule in rules:
            body = rule.model_dump(exclude_none=True, exclude=set(_PREDICATE_FIELDS))
            for path in _patched_properties(body):
                if any(path.startswith(prefix) for prefix in unsupported):
                    raise ValueError(
                        f"{where}.{column} sets {path}, which this chart "
                        "does not draw per row"
                    )
                first = owner.setdefault(path, column)
                if first != column and not {first, column} <= measures:
                    raise ValueError(
                        f"{where} columns '{first}' and '{column}' both "
                        f"patch {path}; rules on two columns may not set the same "
                        "property"
                    )
    resolved = {
        column: resolve_rule_aggregates(rules, column, data, f"{where}.{column}")
        for column, rules in conditional.items()
    }
    return {column: rules for column, rules in resolved.items() if rules}
