"""Tests for build_exec_env's `extra_routes` composition hook.

A host that needs its own backend for a source kind (e.g. Streamlit in
Snowflake's Snowpark session, which must run `snowflake` sources instead of a
dbt adapter) passes `extra_routes`, which replace the default route of the kinds
they name.
"""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest

from dbt_charts.agent_api import ProjectSession
from dbt_charts.cli.filesystem_project import FilesystemProject
from dbt_charts.core.column_types import ColumnType, TypedRows
from dbt_charts.core.compile.models.query.normalized import SqlQuery
from dbt_charts.core.compile.models.source import (
    PostgresSourceConfig,
    SnowflakeSourceConfig,
)
from dbt_charts.core.execute.backends.dbt_core import DbtBackend
from dbt_charts.core.execute.backends.snowpark import SnowparkBackend
from dbt_charts.core.execute.compose import ResolvedQuery
from dbt_charts.core.execute.env import build_exec_env, close_env
from dbt_charts.core.execute.run import run_query
from dbt_charts.core.execute.sql_literals import INLINE_PLACEHOLDERS


class _StubBackend:
    param_style = INLINE_PLACEHOLDERS

    def __init__(self) -> None:
        self.closed = False
        self.ran: list[str] = []

    def execute(self, source: Any, query: ResolvedQuery) -> TypedRows:
        self.ran.append(query.sql)
        return TypedRows(["stub"], [(True,)], [ColumnType(kind="boolean")])

    def interrupt(self) -> None:
        pass

    def close(self) -> None:
        self.closed = True


def _snowflake() -> SnowflakeSourceConfig:
    return SnowflakeSourceConfig(
        type="snowflake", account="xy12345", database="analytics"
    )


class TestExtraRoutes:
    def test_extra_route_runs_its_source_kind(
        self, tmp_path: Path, local_project: Callable[..., FilesystemProject]
    ) -> None:
        stub = _StubBackend()
        registry = build_exec_env(
            local_project(tmp_path), extra_routes={"snowflake": stub}
        )
        run_query(
            registry, SqlQuery(sql="SELECT 1", source="sf"), source_config=_snowflake()
        )
        assert stub.ran == ["SELECT 1"]

    def test_registry_close_closes_extra_routes(
        self, tmp_path: Path, local_project: Callable[..., FilesystemProject]
    ) -> None:
        stub = _StubBackend()
        registry = build_exec_env(
            local_project(tmp_path), extra_routes={"snowflake": stub}
        )
        close_env(registry)
        assert stub.closed

    def test_no_extra_routes_leaves_the_dbt_backend_on_snowflake(
        self, tmp_path: Path, local_project: Callable[..., FilesystemProject]
    ) -> None:
        registry = build_exec_env(local_project(tmp_path))
        assert isinstance(registry.backends.for_source(_snowflake()), DbtBackend)

    def test_extra_route_leaves_other_kinds_alone(
        self, tmp_path: Path, local_project: Callable[..., FilesystemProject]
    ) -> None:
        registry = build_exec_env(
            local_project(tmp_path), extra_routes={"snowflake": _StubBackend()}
        )
        postgres = PostgresSourceConfig(
            type="postgres", host="h", dbname="d", user="u", password="p"
        )
        assert isinstance(registry.backends.for_source(postgres), DbtBackend)


class _NoSession:
    def sql(self, query: str, /) -> Any:
        raise AssertionError("session must not be used")

    def get_current_database(self) -> str | None:
        raise AssertionError("session must not be used")

    def get_current_schema(self) -> str | None:
        raise AssertionError("session must not be used")


def test_from_project_rejects_extra_routes_with_an_injected_registry(
    tmp_path: Path,
) -> None:
    project = FilesystemProject(tmp_path)
    with pytest.raises(ValueError, match="extra_routes_factory"):
        ProjectSession.from_project(
            project,
            env=build_exec_env(project),
            extra_routes_factory=lambda: {
                "snowflake": SnowparkBackend(session=_NoSession())
            },
        )


def test_refresh_builds_fresh_extra_routes(tmp_path: Path) -> None:
    made: list[_StubBackend] = []

    def factory() -> dict[str, Any]:
        made.append(_StubBackend())
        return {"snowflake": made[-1]}

    session = ProjectSession.from_project(
        FilesystemProject(tmp_path), extra_routes_factory=factory
    )
    assert session.env is not None

    session.refresh()
    assert session.env is not None

    assert made[0].closed
    assert not made[1].closed
