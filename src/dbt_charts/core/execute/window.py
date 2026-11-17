"""A window: a bounded slice of a query result, searched, filtered, and sorted.

A page draws a window of a result rather than the whole of it, and asks for the
next window when the reader searches, sorts, or scrolls. The slice runs in the
isolated composition DuckDB over the result's rows.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from dbt_charts.core.constants.window import WINDOW_MAX_ROWS
from dbt_charts.core.execute.cache_composition import (
    CompositionError,
    compose_over_named_rows,
    quote_identifier,
)

if TYPE_CHECKING:
    from dbt_charts.core.column_types import ColumnType
    from dbt_charts.core.execute.cache_backend import CacheRows, ResultRows

_TABLE = "result"
_POSITION = "__dct_window_position"
_TOTAL = "__dct_window_total"
_DIRECTIONS = {"asc": "ASC", "desc": "DESC"}


class WindowError(ValueError):
    """A window names a column the result lacks, or bounds it can't have."""


@dataclass(frozen=True)
class Window:
    """Which slice of a result to return.

    ``search`` keeps rows where any of ``search_columns`` contains it, ignoring
    case; empty matches every row. ``equals`` keeps rows whose column equals one
    of its values. A URL carries every value as text, so each is read as the
    column's own type: ``1`` matches a float 1.0.
    ``order`` sorts by ``(column, "asc" | "desc")`` pairs; ties, and an empty
    ``order``, keep the result's own row order.
    """

    search: str
    search_columns: tuple[str, ...]
    equals: tuple[tuple[str, tuple[str | None, ...]], ...]
    order: tuple[tuple[str, str], ...]
    offset: int
    limit: int


@dataclass(frozen=True)
class WindowRows:
    rows: CacheRows
    schema: dict[str, ColumnType]
    #: Rows matching the search and filters, across every window.
    total: int
    #: Whether matching rows remain past this window.
    more: bool
    #: The result hit a fetch cap: rows past the cap are not searched, and
    #: ``total`` counts only the rows kept.
    truncated: bool


def _check(window: Window, columns: dict[str, ColumnType]) -> None:
    named = [
        *window.search_columns,
        *(c for c, _ in window.equals),
        *(c for c, _ in window.order),
    ]
    if "rowid" in columns:
        # A real column shadows DuckDB's rowid, which keeps the result's order.
        raise WindowError("A result with a column named 'rowid' can't be windowed.")
    unknown = [c for c in named if c not in columns]
    if unknown:
        raise WindowError(
            f"No column {unknown[0]!r} in this result (columns: {list(columns)})."
        )
    empty = [c for c, values in window.equals if not values]
    if empty:
        raise WindowError(f"Equals filter on {empty[0]!r} lists no values.")
    if window.search and not window.search_columns:
        raise WindowError("A window search needs search_columns to look in.")
    bad = [d for _, d in window.order if d not in _DIRECTIONS]
    if bad:
        raise WindowError(f"Sort direction {bad[0]!r} is not 'asc' or 'desc'.")
    if window.offset < 0:
        raise WindowError(f"Window offset {window.offset} is negative.")
    if not 1 <= window.limit <= WINDOW_MAX_ROWS:
        raise WindowError(
            f"Window limit {window.limit} is outside 1..{WINDOW_MAX_ROWS}."
        )


def _where(window: Window, params: list[str | int]) -> str:
    clauses: list[str] = []
    if window.search:
        params.append(window.search.lower())
        needle = f"${len(params)}"
        clauses.append(
            "("
            + " OR ".join(
                f"contains(lower(CAST({quote_identifier(c)} AS VARCHAR)), {needle})"
                for c in window.search_columns
            )
            + ")"
        )
    for column, values in window.equals:
        cell = quote_identifier(column)
        matches: list[str] = []
        for value in values:
            if value is None:
                matches.append(f"{cell} IS NULL")
            else:
                params.append(value)
                matches.append(f"{cell} = ${len(params)}")
        clauses.append("(" + " OR ".join(matches) + ")")
    if not clauses:
        return "TRUE"
    return " AND ".join(clauses)


def slice_rows(
    result: ResultRows, window: Window, time_zone: str, *, truncated: bool
) -> WindowRows:
    """The rows of *result* that *window* selects, with the count that match.

    Args:
        truncated: Whether *result* hit a fetch cap; reported on the window.

    Raises:
        WindowError: The window names a column *result* lacks, sorts in an
            unknown direction, has a negative offset or a limit outside
            ``1..WINDOW_MAX_ROWS``, or filters a column by a value its type
            cannot hold.
    """
    _check(window, result.schema)
    params: list[str | int] = []
    where = _where(window, params)
    order = ", ".join(
        [f"{quote_identifier(c)} {_DIRECTIONS[d]}" for c, d in window.order] + ["rowid"]
    )
    params += [window.offset, window.offset + window.limit + 1]
    lower, upper = f"${len(params) - 1}", f"${len(params)}"
    # One statement answers both the window and the total: an empty window
    # still returns its one left-joined row, carrying the count.
    sql = (
        f"WITH matched AS (SELECT *, row_number() OVER (ORDER BY {order}) "
        f"AS {_POSITION} FROM {_TABLE} WHERE {where}) "
        f"SELECT (SELECT count(*) FROM matched) AS {_TOTAL}, matched.* "
        f"FROM (SELECT 1) AS one LEFT JOIN matched "
        f"ON {_POSITION} > {lower} AND {_POSITION} <= {upper} "
        f"ORDER BY {_POSITION}"
    )
    try:
        rows, _ = compose_over_named_rows(
            sql,
            {_TABLE: result.rows},
            params,
            schemas={_TABLE: result.schema},
            time_zone=time_zone,
        )
    except CompositionError as exc:
        import duckdb

        # Only an equals value is read as a column's type; search casts to text.
        if isinstance(exc.__cause__, duckdb.ConversionException):
            raise WindowError(
                f"An equals value does not fit its column: {exc.__cause__}"
            ) from exc
        raise
    total = rows[0][_TOTAL]
    drawn = [
        {c: row[c] for c in result.schema} for row in rows if row[_POSITION] is not None
    ]
    return WindowRows(
        drawn[: window.limit],
        result.schema,
        total,
        len(drawn) > window.limit,
        truncated,
    )
