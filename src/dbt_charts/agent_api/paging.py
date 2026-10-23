"""Cursor paging shared by every list an agent tool returns."""

from __future__ import annotations

from collections.abc import Sequence
from typing import TypeVar

# Keep a full page under the MCP tool-result ceiling (rows are a few hundred bytes).
MAX_PAGE_LIMIT = 200
DEFAULT_PAGE_LIMIT = 100

_T = TypeVar("_T")


def cursor_offset(cursor: str | None) -> int:
    """Decode a ``next`` cursor to the offset it resumes at; ``None`` is the start."""
    if cursor is None:
        return 0
    if not cursor.isdecimal():
        raise ValueError(
            f"{cursor!r} is not a cursor: pass the 'next' value from the "
            "previous result unchanged"
        )
    return int(cursor)


def paginate(
    items: Sequence[_T], *, limit: int, cursor: str | None
) -> tuple[list[_T], str | None]:
    """One page of an already-sorted, already-filtered list, and the cursor to resume at.

    The cursor is ``None`` on the last page. Every host pages through this one
    function so a cursor means the same thing on the connector, in chat, and
    over stdio.
    """
    if limit == 0:
        return [], None  # a cursor here would resume at the same offset forever
    start = cursor_offset(cursor)
    end = start + limit
    return list(items[start:end]), (str(end) if end < len(items) else None)
