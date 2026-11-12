"""Checks on a mark family's ``style.conditional`` rules, shared by every family."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any

from dbt_charts.core.compile.models.conditional_predicate import PREDICATE_KEYS

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


_Rules = Mapping[str, Sequence[Any]]  # type-state: explicit_any — rule models


def validate_mark_conditional(
    conditional: _Rules | None,
    data: Sequence[Mapping[str, Any]],  # type-state: explicit_any — query rows
    unsupported: Sequence[str] = (),
    where: str = "style.conditional",
    y: str | list[str] | None = None,
) -> None:
    """Raise ValueError for a rule column the query lacks (``where`` names the block), a property under
    ``unsupported`` (one the family accepts in the rule type but never draws), or
    two columns that patch the same property.

    Mark families paint one mark per row, so two columns' rules writing one
    property would make the winner depend on key order. A multi-y ``y`` list
    folds a row into one mark per measure, and a rule on a measure paints only
    that measure's marks, so two measures may patch the same property.
    """
    if not conditional:
        return
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
            body = rule.model_dump(exclude_none=True, exclude=_PREDICATE_FIELDS)
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
