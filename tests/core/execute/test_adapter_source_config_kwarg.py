"""Integration tests: typed SourceConfig flows to adapter via AdapterRegistry.

Pins that AdapterRegistry.execute() invokes the resolver exactly once per query
and passes the typed SourceConfig to the adapter.
"""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock

from dbt_charts.cli.filesystem_project import FilesystemProject
from dbt_charts.core.column_types import literal_schema
from dbt_charts.core.compile.models.source import DuckDBSourceConfig, SourceConfig
from dbt_charts.core.execute.adapters.adapter_registry import AdapterRegistry
from dbt_charts.core.execute.adapters.base import BaseAdapter, QueryParams, QueryResult
from dbt_charts.core.execute.source_resolver import SourceResolver


class _CapturingAdapter(BaseAdapter):
    """Adapter that records source_config on each _execute call."""

    supported_types = {"sql", "csv", "values"}

    def __init__(self) -> None:
        self.received: list[SourceConfig | None] = []

    def can_execute(
        self, query: Any, source_config: SourceConfig | None = None
    ) -> bool:
        return True

    def _execute(
        self,
        query: Any,
        variables: Any = None,
        params: QueryParams = None,
        source_config: SourceConfig | None = None,
    ) -> QueryResult:
        self.received.append(source_config)
        return QueryResult(
            data=[{"x": 1}], columns=["x"], schema=literal_schema([{"x": 1}], ["x"])
        )


def _make_registry(
    local_project: Callable[..., FilesystemProject],
    resolver: SourceResolver | None = None,
) -> tuple[AdapterRegistry, _CapturingAdapter]:
    registry = AdapterRegistry(project=local_project(Path()), resolver=resolver)
    adapter = _CapturingAdapter()
    registry.register(adapter)
    return registry, adapter


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
    """AdapterRegistry invokes resolver once and passes SourceConfig to adapter."""

    def test_resolver_invoked_once_per_execute(
        self, local_project: Callable[..., FilesystemProject]
    ):
        """Resolver is called exactly once per AdapterRegistry.execute() call."""
        from dbt_charts.core.compile.models.source import DuckDBSourceConfig

        mock_resolver = MagicMock(spec=SourceResolver)
        mock_resolver.resolve.return_value = DuckDBSourceConfig(
            type="duckdb", path=":memory:"
        )

        registry, adapter = _make_registry(local_project, resolver=mock_resolver)
        query = _sql_query(source="db")
        registry.execute(query)

        assert mock_resolver.resolve.call_count == 1

    def test_adapter_receives_typed_source_config(
        self, local_project: Callable[..., FilesystemProject]
    ):
        """Adapter's _execute receives the SourceConfig returned by the resolver."""
        duckdb_config = DuckDBSourceConfig(type="duckdb", path=":memory:")

        mock_resolver = MagicMock(spec=SourceResolver)
        mock_resolver.resolve.return_value = duckdb_config

        registry, adapter = _make_registry(local_project, resolver=mock_resolver)
        registry.execute(_sql_query(source="db"))

        assert len(adapter.received) == 1
        assert adapter.received[0] is duckdb_config

    def test_adapter_receives_none_when_resolver_returns_none(
        self, local_project: Callable[..., FilesystemProject]
    ):
        """Adapter receives source_config=None when resolver returns None."""
        mock_resolver = MagicMock(spec=SourceResolver)
        mock_resolver.resolve.return_value = None

        registry, adapter = _make_registry(local_project, resolver=mock_resolver)
        registry.execute(_sql_query(source="db"))

        assert adapter.received == [None]

    def test_board_sources_passed_to_resolver(
        self, local_project: Callable[..., FilesystemProject]
    ):
        """AdapterRegistry passes board.sources to the resolver."""
        mock_resolver = MagicMock(spec=SourceResolver)
        mock_resolver.resolve.return_value = None

        board_sources = {"my_db": {"type": "duckdb", "path": ":memory:"}}
        board = _compiled_board(sources=board_sources)

        registry, _ = _make_registry(local_project, resolver=mock_resolver)
        query = _sql_query(source="my_db")
        registry.execute(query, board=board)

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

        registry, adapter = _make_registry(local_project, resolver=mock_resolver)
        result = registry.execute(_sql_query(source="bad_db"))

        assert not result.is_success
        assert result.error is not None
        assert len(adapter.received) == 0


class TestDbtContextWireUp:
    """When a DbtAdapter is registered, AdapterRegistry threads a DbtContext
    into the resolver — letting unknown string source names fall through
    instead of raising ERR-SOURCE-NOT-FOUND.
    """

    def test_dbt_adapter_presence_threads_dbt_context_to_resolver(
        self, local_project: Callable[..., FilesystemProject]
    ):
        from dbt_charts.core.execute.source_resolver import DbtContext

        mock_resolver = MagicMock(spec=SourceResolver)
        mock_resolver.resolve.return_value = None

        registry = AdapterRegistry(
            project=local_project(Path()), resolver=mock_resolver
        )
        registry.register(_CapturingAdapter())

        # Without a DbtAdapter registered the resolver sees dbt_context=None.
        registry.execute(_sql_query(source="some_name"))
        assert mock_resolver.resolve.call_args.kwargs["dbt_context"] is None

        # Add a fake adapter that looks like a DbtAdapter to _derive_dbt_context.
        from dbt_charts.core.execute.adapters.dbt_adapter import DbtAdapter

        fake_dbt = MagicMock(spec=DbtAdapter)
        fake_dbt.supported_types = {"sql"}
        fake_dbt.dbt_project_path = Path()
        registry._adapters.insert(0, fake_dbt)

        registry.execute(_sql_query(source="some_name"))
        assert isinstance(
            mock_resolver.resolve.call_args.kwargs["dbt_context"], DbtContext
        )
