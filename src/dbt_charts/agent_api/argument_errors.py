"""One plain line for a malformed tool call.

Pydantic's own message is a multi-line dump with a docs URL and, for a union
field like a variable's value, one entry per union member. A host model needs
the tool, the argument, and what is wrong with it — nothing else.
"""

from __future__ import annotations

import json

from pydantic import ValidationError
from pydantic_core import ErrorDetails

#: Pydantic names the failing member of a ``str | int | float | bool | list[str]``
#: union in the error location; these are the tags our tool arguments use.
_UNION_MEMBER_TAGS = frozenset({"str", "int", "float", "bool", "list[str]"})


def format_argument_error(tool: str, exc: ValidationError) -> str:
    """``<tool>: <field>: <reason>`` (``; ``-joined when several arguments are bad)."""
    clauses = [
        _clause(group) for group in _group_union_members(exc.errors(include_url=False))
    ]
    return f"{tool}: " + "; ".join(clauses)


def _is_union_member(error: ErrorDetails) -> bool:
    return bool(error["loc"]) and error["loc"][-1] in _UNION_MEMBER_TAGS


def _group_union_members(errors: list[ErrorDetails]) -> list[list[ErrorDetails]]:
    groups: list[list[ErrorDetails]] = []
    for error in errors:
        if (
            groups
            and _is_union_member(error)
            and _is_union_member(groups[-1][0])
            and groups[-1][0]["loc"][:-1] == error["loc"][:-1]
        ):
            groups[-1].append(error)
        else:
            groups.append([error])
    return groups


def _clause(group: list[ErrorDetails]) -> str:
    error = group[0]
    loc = error["loc"]
    if len(group) > 1:
        tags = ", ".join(str(member["loc"][-1]) for member in group)
        field = ".".join(str(part) for part in loc[:-1])
        got = json.dumps(error["input"], default=str)
        return f"{field}: got {got}, expected one of {tags}"
    field = ".".join(str(part) for part in loc)
    if error["type"] == "extra_forbidden":
        reason = "is not an argument of this tool"
    elif error["type"] == "missing":
        reason = "is required"
    else:
        reason = error["msg"].removeprefix("Value error, ")
    return f"{field}: {reason}" if field else reason
