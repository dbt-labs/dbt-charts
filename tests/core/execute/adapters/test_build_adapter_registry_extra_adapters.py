"""Tests for build_adapter_registry's `extra_adapters` composition hook.

A host that needs to inject its own adapter ahead of the standard ones (e.g.
Streamlit in Snowflake's Snowpark-session adapter, which must claim `snowflake`
sources before `SqlAdapter` does) passes `extra_adapters`, registered first so
it wins the registry's first-match-wins ordering.
"""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest

from dbt_charts.agent_api import ProjectSession
from dbt_charts.cli.filesystem_project import FilesystemProject
from dbt_charts.core.compile.models.query.normalized import SqlQuery
from dbt_charts.core.compile.models.source import (
    DuckDBSourceConfig,
    SnowflakeSourceConfig,
)
from dbt_charts.core.execute.adapters.adapter_registry import build_adapter_registry
from dbt_charts.core.execute.adapters.base import BaseAdapter, QueryParams, QueryResult
from dbt_charts.core.execute.adapters.snowpark_adapter import SnowparkAdapter
from dbt_charts.core.execute.adapters.sql_adapter import SqlAdapter


class _StubAdapter(BaseAdapter):
    """Claims snowflake-resolved SQL only, same eligibility shape as SnowparkAdapter."""

    @property
    def supported_types(self) -> set[str]:
        return {"sql"}

    def _can_execute(self, query: object, source_config: object) -> bool:
        return getattr(source_config, "type", None) == "snowflake"

    def _execute(
        self,
        query: object,
        variables: object = None,
        params: QueryParams = None,
        source_config: object = None,
    ) -> QueryResult:
        return QueryResult(data=[{"stub": True}])


class TestExtraAdapters:
    def test_extra_adapter_is_registered(
        self, tmp_path: Path, local_project: Callable[..., FilesystemProject]
    ) -> None:
        stub = _StubAdapter()
        registry = build_adapter_registry(
            local_project(tmp_path), extra_adapters=[stub]
        )
        assert stub in registry.adapters

    def test_extra_adapter_wins_over_sql_adapter_for_its_claimed_source(
        self, tmp_path: Path, local_project: Callable[..., FilesystemProject]
    ) -> None:
        stub = _StubAdapter()
        registry = build_adapter_registry(
            local_project(tmp_path), extra_adapters=[stub]
        )
        query = SqlQuery(sql="SELECT 1", source="sf")
        source_config = SnowflakeSourceConfig(
            type="snowflake", account="xy12345", database="analytics"
        )
        adapter = registry.get_adapter(query, source_config)
        assert adapter is stub

    def test_no_extra_adapters_falls_back_to_sql_adapter(
        self, tmp_path: Path, local_project: Callable[..., FilesystemProject]
    ) -> None:
        registry = build_adapter_registry(local_project(tmp_path))
        query = SqlQuery(sql="SELECT 1", source="sf")
        source_config = SnowflakeSourceConfig(
            type="snowflake", account="xy12345", database="analytics"
        )
        adapter = registry.get_adapter(query, source_config)
        assert isinstance(adapter, SqlAdapter)

    def test_extra_adapter_does_not_claim_duckdb_source(
        self, tmp_path: Path, local_project: Callable[..., FilesystemProject]
    ) -> None:
        stub = _StubAdapter()
        registry = build_adapter_registry(
            local_project(tmp_path), extra_adapters=[stub]
        )
        query = SqlQuery(sql="SELECT 1", source="db")
        source_config = DuckDBSourceConfig(type="duckdb", path=":memory:")
        adapter = registry.get_adapter(query, source_config)
        assert adapter is not stub


class _NoSession:
    def sql(self, query: str, /) -> Any:
        raise AssertionError("session must not be used")

    def get_current_database(self) -> str | None:
        raise AssertionError("session must not be used")

    def get_current_schema(self) -> str | None:
        raise AssertionError("session must not be used")


def test_allowed_types_gates_extra_adapters(tmp_path: Path) -> None:
    project = FilesystemProject(tmp_path)
    registry = build_adapter_registry(
        project,
        allowed_types={"values"},
        extra_adapters=[SnowparkAdapter(session=_NoSession(), project=project)],
    )
    assert not any(isinstance(a, SnowparkAdapter) for a in registry.adapters)


def test_from_project_rejects_extra_adapters_with_an_injected_registry(
    tmp_path: Path,
) -> None:
    project = FilesystemProject(tmp_path)
    with pytest.raises(ValueError, match="extra_adapters_factory"):
        ProjectSession.from_project(
            project,
            adapter_registry=build_adapter_registry(project),
            extra_adapters_factory=lambda: [
                SnowparkAdapter(session=_NoSession(), project=project)
            ],
        )


class _ClosableStub(_StubAdapter):
    def __init__(self) -> None:
        super().__init__()
        self.closed = False

    def close(self) -> None:
        self.closed = True


def test_refresh_builds_fresh_extra_adapters(tmp_path: Path) -> None:
    made: list[_ClosableStub] = []

    def factory() -> list[BaseAdapter]:
        made.append(_ClosableStub())
        return [made[-1]]

    session = ProjectSession.from_project(
        FilesystemProject(tmp_path), extra_adapters_factory=factory
    )
    first = session.adapter_registry
    assert made[0] in first.adapters

    session.refresh()
    rebuilt = session.adapter_registry

    assert made[0].closed
    assert not made[1].closed
    assert made[1] in rebuilt.adapters
    assert made[0] not in rebuilt.adapters
