"""Integration tests: typed SourceConfig flows to the backend via ExecEnv.

Pins that run_query() invokes the resolver exactly once per query
and passes the typed SourceConfig to the backend.
"""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock

import nanoarrow as na
import pyarrow as pa
import pytest

from dbt_charts.cli.filesystem_project import FilesystemProject
from dbt_charts.core.compile.models.source import (
    DuckDBSourceConfig,
    ResolvedSourceConfig,
)
from dbt_charts.core.dialects import SQLDialect
from dbt_charts.core.execute.backends.base import BackendRegistry
from dbt_charts.core.execute.compose import ResolvedQuery
from dbt_charts.core.execute.env import ExecEnv, make_exec_env
from dbt_charts.core.execute.run import run_query
from dbt_charts.core.execute.source_resolver import SourceResolver
from dbt_charts.core.execute.sql_literals import INLINE_PLACEHOLDERS


class _CapturingBackend:
    """Backend that records the source it is handed on each execute."""

    param_style: SQLDialect = INLINE_PLACEHOLDERS

    def __init__(self) -> None:
        self.received: list[ResolvedSourceConfig] = []

    def execute(self, source: ResolvedSourceConfig, query: ResolvedQuery) -> na.Array:
        self.received.append(source)
        return na.Array(pa.table({"x": [1]}))

    def interrupt(self) -> None:
        pass

    def close(self) -> None:
        pass


def _make_registry(
    local_project: Callable[..., FilesystemProject],
    resolver: SourceResolver | None = None,
) -> tuple[ExecEnv, _CapturingBackend]:
    backend = _CapturingBackend()
    registry = make_exec_env(
        project=local_project(Path()),
        resolver=resolver,
        backends=BackendRegistry({"duckdb": backend}),
    )
    return registry, backend


def _sql_query(source: Any = "db") -> Any:
    from dbt_charts.core.compile.models.query.normalized import SqlQuery

    return SqlQuery(sql="SELECT 1", source=source)


def _compiled_board(sources: dict[str, dict] | None = None) -> Any:
    from dbt_charts.core.compile.compiler import compile

    yaml = (
        "title: T\n"
        "source: default_db\n"
        "queries:\n  q:\n    sql: SELECT 1\n"
        "charts:\n  c:\n    query: q\n    type: table\n"
        "rows:\n  - c\n"
    )
    result = compile(yaml)
    assert result.success
    board = result.board
    if sources:
        object.__setattr__(board, "sources", sources)
    return board


class TestAdapterReceivesTypedSourceConfig:
    """ExecEnv invokes resolver once and passes SourceConfig to adapter."""

    def test_resolver_invoked_once_per_execute(
        self, local_project: Callable[..., FilesystemProject]
    ):
        """Resolver is called exactly once per run_query() call."""
        from dbt_charts.core.compile.models.source import DuckDBSourceConfig

        mock_resolver = MagicMock(spec=SourceResolver)
        mock_resolver.resolve.return_value = DuckDBSourceConfig(
            type="duckdb", path=":memory:"
        )

        registry, backend = _make_registry(local_project, resolver=mock_resolver)
        query = _sql_query(source="db")
        run_query(registry, query)

        assert mock_resolver.resolve.call_count == 1

    def test_backend_receives_typed_source_config(
        self, local_project: Callable[..., FilesystemProject]
    ):
        """The backend receives the SourceConfig returned by the resolver."""
        duckdb_config = DuckDBSourceConfig(type="duckdb", path=":memory:")

        mock_resolver = MagicMock(spec=SourceResolver)
        mock_resolver.resolve.return_value = duckdb_config

        registry, backend = _make_registry(local_project, resolver=mock_resolver)
        run_query(registry, _sql_query(source="db"))

        assert len(backend.received) == 1
        assert backend.received[0] is duckdb_config

    def test_a_resolver_deferring_with_no_dbt_project_is_an_invariant_failure(
        self, local_project: Callable[..., FilesystemProject]
    ):
        """Only a resolver given a dbt project defers a SQL source; no backend is
        guessed when one defers without it."""
        mock_resolver = MagicMock(spec=SourceResolver)
        mock_resolver.resolve.return_value = None

        registry, backend = _make_registry(local_project, resolver=mock_resolver)
        with pytest.raises(AssertionError, match="no dbt project"):
            run_query(registry, _sql_query(source="db"))

        assert backend.received == []

    def test_board_sources_passed_to_resolver(
        self, local_project: Callable[..., FilesystemProject]
    ):
        """ExecEnv passes board.sources to the resolver."""
        mock_resolver = MagicMock(spec=SourceResolver)
        mock_resolver.resolve.return_value = DuckDBSourceConfig(type="duckdb")

        board_sources = {"my_db": {"type": "duckdb", "path": ":memory:"}}
        board = _compiled_board(sources=board_sources)

        registry, _ = _make_registry(local_project, resolver=mock_resolver)
        query = _sql_query(source="my_db")
        run_query(registry, query, board=board)

        resolver_call = mock_resolver.resolve.call_args
        assert resolver_call.kwargs.get("board_sources") == board_sources or (
            resolver_call.args and resolver_call.args[1] == board_sources
        )

    def test_resolver_error_surfaces_as_query_result_error(
        self, local_project: Callable[..., FilesystemProject]
    ):
        """DbtChartsError from resolver becomes QueryResult(error=...) — never propagates."""
        from dbt_charts.core.diagnostics.base import DbtChartsError
        from dbt_charts.core.diagnostics.codes_compile import ERR_SOURCE_NOT_FOUND

        mock_resolver = MagicMock(spec=SourceResolver)
        mock_resolver.resolve.side_effect = DbtChartsError.from_code(
            ERR_SOURCE_NOT_FOUND,
            query_name="q",
            source="bad_db",
            available=["good_db"],
            advice="",
        )

        registry, backend = _make_registry(local_project, resolver=mock_resolver)
        result = run_query(registry, _sql_query(source="bad_db"))

        assert not result.is_success
        assert result.error is not None
        assert len(backend.received) == 0


class TestDbtContextWireUp:
    """When a dbt project is linked, ExecEnv threads a DbtContext into the
    resolver, letting unknown string source names defer to the dbt project
    instead of raising ERR-SOURCE-NOT-FOUND.
    """

    def test_dbt_project_presence_threads_dbt_context_to_resolver(
        self, local_project: Callable[..., FilesystemProject]
    ):
        from dbt_charts.core.execute.source_resolver import DbtContext

        mock_resolver = MagicMock(spec=SourceResolver)
        mock_resolver.resolve.return_value = DuckDBSourceConfig(type="duckdb")

        registry = make_exec_env(project=local_project(Path()), resolver=mock_resolver)

        # Without a dbt project the resolver sees dbt_context=None.
        run_query(registry, _sql_query(source="some_name"))
        assert mock_resolver.resolve.call_args.kwargs["dbt_context"] is None

        linked = make_exec_env(
            project=local_project(Path()),
            resolver=mock_resolver,
            dbt_context=DbtContext(dbt_project_path=Path(), target=None),
        )

        run_query(linked, _sql_query(source="some_name"))
        assert isinstance(
            mock_resolver.resolve.call_args.kwargs["dbt_context"], DbtContext
        )
