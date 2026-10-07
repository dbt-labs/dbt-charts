"""Query results as templates read them: ``queries.<name>.rows`` / ``.one`` / ``.columns``.

Both types raise only ``ChartDataError``. Jinja falls back from ``getattr`` to
``getitem`` on ``AttributeError``/``KeyError`` and would turn either into an
undefined value, so an error that must reach the author cannot be one of them.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any, SupportsIndex, TypeVar, overload

from dbt_charts.core.diagnostics.chart_data import ChartDataError
from dbt_charts.core.diagnostics.codes_render import (
    ERR_TEXT_QUERY_ACCESS,
    ERR_TEXT_QUERY_INDEX,
    ERR_TEXT_QUERY_ROW_COUNT,
    ERR_TEXT_QUERY_UNKNOWN_COLUMN,
)


class QueryRow:
    """One result row. Cells are reached by name only, so no column can be shadowed."""

    __slots__ = ("_cells", "_query_name")

    def __init__(
        self,
        query_name: str,
        cells: Mapping[str, Any],  # type-state: explicit_any — DB cell values
    ) -> None:
        self._query_name = query_name
        self._cells = cells

    def __getitem__(self, column: str) -> Any:  # type-state: explicit_any — DB cell
        try:
            return self._cells[column]
        except KeyError:
            raise ChartDataError.from_code(
                ERR_TEXT_QUERY_UNKNOWN_COLUMN,
                query_name=self._query_name,
                column=column,
                available=list(self._cells),
            ) from None

    def __contains__(self, column: str) -> bool:
        return column in self._cells

    def __getattr__(self, column: str) -> Any:  # type-state: explicit_any — DB cell
        # Protocol probes (copy, pickle, hasattr) expect AttributeError for dunders.
        if column.startswith("__"):
            raise AttributeError(column)
        return self[column]

    def __str__(self) -> str:
        raise ChartDataError.from_code(
            ERR_TEXT_QUERY_ACCESS,
            query_name=self._query_name,
            access="cannot print a row directly",
            use="row.<col>",
        )

    # A list of rows prints through repr, so repr must refuse too.
    __repr__ = __str__


_T = TypeVar("_T")


class QueryItems(list[_T]):
    """A list that raises ``ChartDataError`` for an index it does not have.

    ``IndexError`` would be swallowed by the sandbox's ``getitem`` and render
    as an empty value.
    """

    def __init__(
        self, items: Sequence[_T], query_name: str, noun: str, extent: str
    ) -> None:
        super().__init__(items)
        self._query_name = query_name
        self._noun = noun
        self._extent = extent

    @overload
    def __getitem__(self, index: SupportsIndex) -> _T: ...
    @overload
    def __getitem__(self, index: slice) -> list[_T]: ...
    def __getitem__(self, index: SupportsIndex | slice) -> _T | list[_T]:
        try:
            return super().__getitem__(index)
        except IndexError:
            raise ChartDataError.from_code(
                ERR_TEXT_QUERY_INDEX,
                query_name=self._query_name,
                noun=self._noun,
                index=index,
                extent=self._extent,
            ) from None


class QueryResult:
    """A query's rows, exposed to templates as ``.rows``, ``.one`` and ``.columns``."""

    __slots__ = ("_columns", "_name", "_rows")

    def __init__(
        self,
        name: str,
        rows: Sequence[Mapping[str, Any]],  # type-state: explicit_any — DB rows
    ) -> None:
        self._name = name
        self._rows = QueryItems(
            [QueryRow(name, row) for row in rows],
            name,
            "row",
            f"returned {len(rows)} rows",
        )
        names = list(rows[0]) if rows else []
        self._columns = QueryItems(names, name, "column", f"{len(names)} columns")

    @property
    def rows(self) -> list[QueryRow]:
        return self._rows

    @property
    def columns(self) -> list[str]:
        """Column names in first-row order; empty when there are no rows."""
        return self._columns

    @property
    def one(self) -> QueryRow:
        if len(self._rows) != 1:
            raise ChartDataError.from_code(
                ERR_TEXT_QUERY_ROW_COUNT,
                query_name=self._name,
                row_count=len(self._rows),
            )
        return self._rows[0]

    def __getattr__(self, attribute: str) -> Any:  # type-state: explicit_any — raises
        if attribute.startswith("__"):
            raise AttributeError(attribute)
        raise ChartDataError.from_code(
            ERR_TEXT_QUERY_ACCESS,
            query_name=self._name,
            access=f"has no attribute {attribute!r}",
            use=(
                f".one['{attribute}'], .rows or .columns"
                if attribute.startswith("_")
                else f".one.{attribute}, .rows or .columns"
            ),
        )

    def __str__(self) -> str:
        raise ChartDataError.from_code(
            ERR_TEXT_QUERY_ACCESS,
            query_name=self._name,
            access="cannot be printed directly",
            use=".one.<col>, .rows or .columns",
        )


class QueryResults:
    """``queries`` in board text. Names resolve only to results, never to methods."""

    __slots__ = ("_results",)

    def __init__(self, results: Mapping[str, QueryResult]) -> None:
        self._results = dict(results)

    def __getitem__(self, name: str) -> QueryResult:
        try:
            return self._results[name]
        except KeyError:
            raise ChartDataError.from_code(
                ERR_TEXT_QUERY_ACCESS,
                query_name=name,
                access="is not a query this text references",
                use="queries.<name> with a query the board defines",
            ) from None

    def __getattr__(self, name: str) -> QueryResult:
        if name.startswith("__"):
            raise AttributeError(name)
        return self[name]


def underscore_access_error(
    obj: object,  # type-state: object_annotation — any template value, narrowed below
    name: str,
) -> ChartDataError | None:
    """The error for ``obj.<_name>`` in board text, which the sandbox hides.

    None when ``obj`` is not a query result type.
    """
    if isinstance(obj, QueryResults):
        return ChartDataError.from_code(
            ERR_TEXT_QUERY_ACCESS,
            query_name=name,
            access="cannot be read as an attribute",
            use=f"queries['{name}']",
        )
    if isinstance(obj, QueryResult):
        query_name, use = obj._name, f".one['{name}'], .rows or .columns"
    elif isinstance(obj, QueryRow):
        query_name, use = obj._query_name, f"row['{name}']"
    else:
        return None
    return ChartDataError.from_code(
        ERR_TEXT_QUERY_ACCESS,
        query_name=query_name,
        access=f"cannot read {name!r} as an attribute",
        use=use,
    )
