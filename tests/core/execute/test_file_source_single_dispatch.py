"""A file-source query runs through one materializer and one execute path.

``Executor.execute_query`` and ``AdapterRegistry.execute`` (the entry point
``dct query`` uses) must return the same rows for the same file source, and the
registry's materializer must be the only one built.
"""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from dbt_charts.cli.filesystem_project import FilesystemProject
from dbt_charts.core.compile import compile as compile_board
from dbt_charts.core.compile.models.query.normalized import SqlQuery
from dbt_charts.core.execute.adapters import AdapterRegistry, build_adapter_registry
from dbt_charts.core.execute.executor import Executor
from dbt_charts.core.execute.file_source_materializer import FileSourceMaterializer

_BOARD_YAML = """
title: File Source Board
queries:
  q:
    sql: "SELECT id FROM orders ORDER BY id"
    source: shop
charts:
  c:
    type: table
    query: q
cols:
  - c
"""
_SQL = "SELECT id FROM orders ORDER BY id"


def _project(tmp_path: Path) -> FilesystemProject:
    (tmp_path / "dbt_charts.yml").write_text(
        yaml.dump(
            {"sources": {"shop": {"type": "csv", "files": {"orders": "orders.csv"}}}}
        )
    )
    (tmp_path / "orders.csv").write_text("id\n1\n2\n")
    return FilesystemProject(tmp_path)


def _executor(registry: AdapterRegistry, board_yaml: str = _BOARD_YAML) -> Executor:
    result = compile_board(board_yaml)
    assert result.success, result.errors
    board = result.board
    assert board is not None
    return Executor(
        board, adapter_registry=registry, query_registry=dict(board.queries)
    )


def test_injected_materializer_serves_executor_and_registry(tmp_path: Path) -> None:
    project = _project(tmp_path)
    materializer = FileSourceMaterializer(project)
    registry = build_adapter_registry(project, file_materializer=materializer)

    via_registry = registry.execute(SqlQuery(sql=_SQL, source="shop"))
    via_executor = _executor(registry).execute_query("q")

    assert via_registry.error is None
    assert [r["id"] for r in via_registry.data] == [1, 2]
    assert [r["id"] for r in via_executor] == [1, 2]


def test_default_local_registry_builds_one_materializer(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    project = _project(tmp_path)
    builds: list[FileSourceMaterializer] = []
    original_init = FileSourceMaterializer.__init__

    def counting_init(self: FileSourceMaterializer, project: FilesystemProject) -> None:
        builds.append(self)
        original_init(self, project)

    monkeypatch.setattr(FileSourceMaterializer, "__init__", counting_init)
    registry = build_adapter_registry(project)
    assert builds == []

    via_executor = _executor(registry).execute_query("q")
    via_registry = registry.execute(SqlQuery(sql=_SQL, source="shop"))
    registry.close()

    assert [r["id"] for r in via_executor] == [1, 2]
    assert [r["id"] for r in via_registry.data] == [1, 2]
    assert len(builds) == 1


def test_executor_honors_the_row_ceiling_on_a_file_source(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    project = _project(tmp_path)
    monkeypatch.setattr("dbt_charts.core.compile.config.resolve_max_rows", lambda: 1)
    executor = _executor(build_adapter_registry(project))

    rows = executor.execute_query("q")

    assert [r["id"] for r in rows] == [1]
    assert "q" in executor.truncations()
