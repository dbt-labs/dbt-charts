"""A real ``ExecEnv`` over a scripted backend, for tests that choose the rows a query returns.

The env, its resolver and its ``BackendRegistry`` are the production types; only
the backend is a double, so a query takes the same plan, compose, run and observe
path it takes against a warehouse. ``backend.execute`` is a ``Mock`` the test can
inspect: it receives ``(source, resolved_query)``.
"""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path
from typing import Any
from unittest.mock import Mock
from weakref import WeakKeyDictionary

from dbt_charts.cli.filesystem_project import FilesystemProject
from dbt_charts.core.column_types import ColumnType, TypedRows, literal_schema
from dbt_charts.core.compile.config import ProjectSourcesConfig
from dbt_charts.core.compile.models.source import (
    DuckDBSourceConfig,
    ResolvedSourceConfig,
    SourceConfig,
)
from dbt_charts.core.dialects import SQLDialect, get_dialect
from dbt_charts.core.execute.backends.base import BackendRegistry
from dbt_charts.core.execute.env import ExecEnv, make_exec_env
from dbt_charts.core.execute.query_result import QueryResult
from dbt_charts.core.execute.source_resolver import DbtContext
from dbt_charts.core.project import Project

Rows = list[dict[str, Any]]  # type-state: explicit_any — test rows

_PROJECT = FilesystemProject(Path(__file__).parent)


def typed_rows(rows: Rows, schema: dict[str, ColumnType] | None = None) -> TypedRows:
    """*rows* as a warehouse client's typed result, columns in first-row order."""
    schema = schema or literal_schema(rows, untyped=ColumnType(kind="string"))
    return TypedRows(
        columns=list(schema),
        rows=[tuple(row.get(column) for column in schema) for row in rows],
        types=list(schema.values()),
    )


class ScriptedBackend:
    """A ``Backend`` whose ``execute`` returns scripted rows.

    Give it ``rows`` to answer every query alike, ``sequence`` to answer each call
    with the next rows (an ``Exception`` in the sequence is raised), or
    ``side_effect`` to compute the answer from ``(source, resolved_query)``.
    """

    param_style: SQLDialect

    def __init__(
        self,
        rows: Rows | QueryResult | None = None,
        *,
        sequence: list[Rows | QueryResult | Exception] | None = None,
        side_effect: Callable[
            [ResolvedSourceConfig, Any], Rows | TypedRows | QueryResult
        ]
        | None = None,  # type-state: explicit_any — a test's ResolvedQuery
        error: Exception | None = None,
        dialect: str = "duckdb",
    ) -> None:
        self.param_style = get_dialect(dialect)
        self.rows = rows
        answers = iter(sequence) if sequence is not None else None

        def answer(
            source: ResolvedSourceConfig, query: Any
        ) -> TypedRows:  # type-state: explicit_any — a test's ResolvedQuery
            if error is not None:
                raise error
            if side_effect is not None:
                result = side_effect(source, query)
            elif answers is not None:
                result = next(answers)
                if isinstance(result, Exception):
                    raise result
            else:
                result = self.rows if self.rows is not None else []
            if isinstance(result, QueryResult):
                if result.error is not None:
                    raise result.error
                result = typed_rows(result.data or [], result.schema)
            typed = result if isinstance(result, TypedRows) else typed_rows(result)
            # A backend never fetches past the limit it was handed.
            return typed._replace(rows=typed.rows[: query.limit.fetch_limit])

        self.execute = Mock(side_effect=answer)
        self.interrupt = Mock()
        self.close = Mock()


def rows_by_sql(
    answers: dict[str, Rows],
) -> Callable[
    [ResolvedSourceConfig, Any], Rows
]:  # type-state: explicit_any — a test's ResolvedQuery
    """A ``ScriptedBackend`` side effect answering each query whose SQL contains one of
    the keys with that key's rows."""

    def answer(
        _source: ResolvedSourceConfig, query: Any
    ) -> Rows:  # type-state: explicit_any — a test's ResolvedQuery
        return next(rows for sql, rows in answers.items() if sql in query.sql)

    return answer


class FixedSourceResolver:
    """Resolves a query to the source it names in *named*, else to *source*."""

    def __init__(
        self,
        source: ResolvedSourceConfig,
        named: dict[str, ResolvedSourceConfig] | None = None,
    ) -> None:
        self._source = source
        self._named = named or {}
        self.calls = 0

    def resolve(
        self,
        authored: str
        | dict[str, Any]
        | SourceConfig
        | None,  # type-state: explicit_any — SourceResolver protocol
        board_sources: dict[
            str, dict[str, Any]
        ],  # type-state: explicit_any — SourceResolver protocol
        project_sources: ProjectSourcesConfig,
        dbt_context: DbtContext | None,
        query_name: str = "",
    ) -> ResolvedSourceConfig:
        self.calls += 1
        if isinstance(authored, str) and authored in self._named:
            return self._named[authored]
        return self._source


_BACKENDS: WeakKeyDictionary[ExecEnv, ScriptedBackend] = WeakKeyDictionary()


def backend_of(env: ExecEnv) -> ScriptedBackend:
    """The ``ScriptedBackend`` a ``scripted_env`` runs its queries on."""
    return _BACKENDS[env]


def scripted_env(
    backend: ScriptedBackend,
    *,
    source: ResolvedSourceConfig | None = None,
    named_sources: dict[str, ResolvedSourceConfig] | None = None,
    project: Project = _PROJECT,
    **env_args: Any,  # type-state: explicit_any — make_exec_env arguments
) -> ExecEnv:
    """An env that runs every query, whatever source it names or omits, on *backend*
    as *source* (default DuckDB); a name in *named_sources* resolves to its own config."""
    source = source or DuckDBSourceConfig(type="duckdb", path=":memory:")
    env = make_exec_env(
        project=project,
        project_sources=ProjectSourcesConfig(
            sources={
                "scripted": {"type": source.type},
                **{
                    name: config.model_dump(exclude_none=True)
                    for name, config in (named_sources or {}).items()
                },
            },
            implicit_source="scripted",
        ),
        resolver=FixedSourceResolver(source, named_sources),
        backends=BackendRegistry({source.type: backend}),
        **env_args,
    )
    _BACKENDS[env] = backend
    return env
