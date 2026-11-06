"""The table schema and foreign keys the automatic-link pass reads.

Stage: EXECUTE (planned beside the render's queries)
Purpose: Render never asks the warehouse a question. Before it resolves anything,
    the render replays the link pass's lookups against a ``LinkProbe``, which
    runs each one (a ``SchemaQuery`` through ``run_query``, uncached, and the
    foreign keys from the dbt manifest and super-schema) and records the answer.
    ``LinkSchema`` is that record; the link pass reads from it and a lookup the
    replay never made is an error, not a lazy fetch.

Entry Points:
    - LinkProbe(ctx).freeze() -> LinkSchema
    - LinkSchema.columns_of(loc) / fk_edges_of(table)
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass
from typing import Any, Protocol, TypeVar

from dbt_charts.core.compile.models.query.normalized import SchemaQuery
from dbt_charts.core.diagnostics.base import DbtChartsError
from dbt_charts.core.diagnostics.execution import ExecutionError
from dbt_charts.core.execute.plan import ExecContext
from dbt_charts.core.execute.run import run_query
from dbt_charts.core.execute.schema_adapter import fetch_fk_edges

Loc = tuple[str, str, str]
"""``(source, schema, table)`` of the base table a link query reads."""

ColumnRows = list[dict[str, Any]]  # type-state: explicit_any — schema envelope rows
FkEdges = list[dict[str, str]]

_K = TypeVar("_K", Loc, str)
_V = TypeVar("_V")


class UnplannedLinkSchemaError(ExecutionError):
    """The link pass asked for schema nobody planned: a gap between the replay and
    the resolve, never an empty answer."""


class LinkSchemaReader(Protocol):
    def columns_of(self, loc: Loc) -> ColumnRows:
        """Column rows of the table at *loc*; empty when the schema query failed
        or found no columns."""
        ...

    def fk_edges_of(self, table: str) -> FkEdges:
        """Outbound foreign-key edges of the bare table name *table*."""
        ...


@dataclass(frozen=True)
class LinkSchema:
    """``errors`` holds a lookup that failed, by the key it was asked under: the
    chart that reads it raises it at resolve, as one chart's failure."""

    columns: Mapping[Loc, ColumnRows]
    fk_edges: Mapping[str, FkEdges]
    errors: Mapping[Loc | str, DbtChartsError]

    def columns_of(self, loc: Loc) -> ColumnRows:
        if loc in self.errors:
            raise self.errors[loc]
        if loc not in self.columns:
            raise UnplannedLinkSchemaError(
                f"Schema of {'.'.join(loc)} was not planned before render "
                f"(planned: {sorted('.'.join(k) for k in self.columns)})."
            )
        return self.columns[loc]

    def fk_edges_of(self, table: str) -> FkEdges:
        if table in self.errors:
            raise self.errors[table]
        if table not in self.fk_edges:
            raise UnplannedLinkSchemaError(
                f"Foreign keys of {table!r} were not planned before render "
                f"(planned: {sorted(self.fk_edges)})."
            )
        return self.fk_edges[table]


class LinkProbe:
    """Answers each lookup by running it once, remembering the answer or the
    ``DbtChartsError`` it raised (re-raised to every later asker)."""

    def __init__(self, ctx: ExecContext) -> None:
        self._ctx = ctx
        self._columns: dict[Loc, ColumnRows] = {}
        self._fk_edges: dict[str, FkEdges] = {}
        self._errors: dict[Loc | str, DbtChartsError] = {}

    def _answer(self, answers: dict[_K, _V], key: _K, ask: Callable[[], _V]) -> _V:
        if key in self._errors:
            raise self._errors[key]
        if key not in answers:
            try:
                answers[key] = ask()
            except DbtChartsError as exc:
                self._errors[key] = exc
                raise
        return answers[key]

    def columns_of(self, loc: Loc) -> ColumnRows:
        def ask() -> ColumnRows:
            source, schema, table = loc
            result = run_query(
                self._ctx.env,
                SchemaQuery(source=source, schema=schema, table=table),
                board=self._ctx.board,
            )
            return [] if result.error else list(result.data)

        return self._answer(self._columns, loc, ask)

    def fk_edges_of(self, table: str) -> FkEdges:
        return self._answer(
            self._fk_edges,
            table,
            lambda: fetch_fk_edges(self._ctx.env, table),
        )

    def freeze(self) -> LinkSchema:
        return LinkSchema(dict(self._columns), dict(self._fk_edges), dict(self._errors))
