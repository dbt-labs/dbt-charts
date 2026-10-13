"""Query rows as text: a markdown table for render, CSV lines for the model."""

from __future__ import annotations

from collections.abc import Iterator, Sequence
from typing import Any


def _cell_text(
    value: Any,  # type-state: explicit_any — a raw query cell, dynamically typed
) -> str:
    """Stringify a cell onto one line, trimming float noise (``13356.789999999998``)."""
    if isinstance(value, float):
        value = float(f"{value:.15g}")
    return str(value).replace("\r\n", "\\n").replace("\n", "\\n").replace("\r", "\\n")


def _table_cell(
    value: Any,  # type-state: explicit_any — a raw query cell, dynamically typed
) -> str:
    """Render one markdown cell so a null, ``None`` and ``""`` stay distinct."""
    if value is None:
        return "null"
    if value == "":
        return '""'
    return _cell_text(value).replace("|", "\\|")


def _csv_cell(
    value: Any,  # type-state: explicit_any — a raw query cell, dynamically typed
) -> str:
    """Render one CSV cell: null is empty, ``""`` is a quoted empty string."""
    if value is None:
        return ""
    text = _cell_text(value)
    if text == "" or any(char in text for char in ',"'):
        return '"' + text.replace('"', '""') + '"'
    return text


def rows_csv_lines(
    rows: Sequence[dict[str, Any]],  # type-state: explicit_any — wire rows
    columns: Sequence[str],
) -> Iterator[str]:
    """Yield a header, then one CSV line per row, without line terminators.

    A cell never contains a newline, so one line is always one row. A row
    missing a column yields an empty cell, like a null.
    """
    yield ",".join(_csv_cell(column) for column in columns)
    for row in rows:
        yield ",".join(_csv_cell(row.get(column)) for column in columns)


def row_table(
    rows: Sequence[dict[str, Any]],  # type-state: explicit_any — wire rows
    columns: Sequence[str],
    *,
    gap_after: int | None = None,
) -> str:
    """Render *rows* as a markdown table that names each column once.

    The header is always emitted, even for zero rows. ``gap_after`` inserts a
    ``...`` row after that many rows — the seam between a head chunk and a
    tail chunk of one query.
    """
    lines = [
        f"| {' | '.join(columns)} |",
        f"| {' | '.join(['---'] * len(columns))} |",
    ]
    for i, row in enumerate(rows):
        if gap_after is not None and i == gap_after:
            lines.append(f"| {' | '.join(['...'] * len(columns))} |")
        cells = " | ".join(_table_cell(row.get(col)) for col in columns)
        lines.append(f"| {cells} |")
    return "\n".join(lines)
