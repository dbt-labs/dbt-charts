"""Regression tests for DuckDB backend cwd handling."""

import os
import threading
from collections.abc import Callable
from pathlib import Path

import duckdb

from dbt_charts.cli.filesystem_project import FilesystemProject
from dbt_charts.core.compile.models.query.normalized import SqlQuery
from dbt_charts.core.compile.models.source import DuckDBSourceConfig

from .._backend_runner import DuckDBRunner


class _BlockingConnection:
    """A real in-memory connection whose statement blocks until released."""

    def __init__(
        self,
        entered: threading.Event,
        release: threading.Event | None = None,
    ) -> None:
        self._conn = duckdb.connect()
        self._entered = entered
        self._release = release

    def execute(self, sql: str, params: list[object]) -> duckdb.DuckDBPyConnection:
        self._entered.set()
        if self._release is not None:
            released = self._release.wait(timeout=5)
            assert released, "Timed out waiting for paired query execution"
        return self._conn.execute(sql, params)

    @property
    def description(self) -> list[tuple[object, ...]]:
        return self._conn.description

    def fetchmany(self, size: int) -> list[tuple[object, ...]]:
        return self._conn.fetchmany(size)

    def interrupt(self) -> None:
        self._conn.interrupt()

    def close(self) -> None:
        self._conn.close()


class CloudProject(FilesystemProject):
    """FilesystemProject whose data_path resolves against a per-instance scratch
    dir instead of root, mirroring Cloud's per-tenant scratch-dir override."""

    def __init__(self, root: Path, scratch: Path) -> None:
        super().__init__(root)
        self._scratch = scratch

    def data_path(self, relpath: str) -> Path:
        return self._scratch / relpath if relpath != "." else self._scratch


def test_duckdb_file_search_path_uses_project_data_path_override(
    tmp_path: Path,
) -> None:
    """Relative file paths must resolve against project.data_path('.'), not root.

    Cloud overrides data_path('.') to return a per-tenant scratch dir so
    DuckDB resolves relative read_csv() paths there, not at the project root.
    """
    scratch = tmp_path / "scratch"
    (scratch / "data").mkdir(parents=True)
    (scratch / "data" / "t.csv").write_text("x\n5\n")

    adapter = DuckDBRunner(
        data_dir=CloudProject(tmp_path, scratch).data_path("."),
        source_config=DuckDBSourceConfig(type="duckdb"),
        read_only=False,
    )
    query = SqlQuery(sql="SELECT x FROM read_csv('data/t.csv')", source="test_db")

    try:
        result = adapter.execute(query)
        assert result.error is None
        assert result.data == [{"x": 5}]
    finally:
        adapter.close()


def test_duckdb_file_search_path_set_in_read_only_authoring(tmp_path: Path) -> None:
    """Read-only authoring (external-access opt-in) still sets file_search_path.

    Gating the SET on the effective external-access posture must not drop it for
    the read-only mode that DOES allow relative reads.
    """
    from dbt_charts.core.execute.env import (
        LOCAL_AUTHORING_ENV_KWARGS,
    )

    scratch = tmp_path / "scratch"
    (scratch / "data").mkdir(parents=True)
    (scratch / "data" / "t.csv").write_text("x\n9\n")

    adapter = DuckDBRunner(
        data_dir=CloudProject(tmp_path, scratch).data_path("."),
        source_config=DuckDBSourceConfig(type="duckdb"),
        **LOCAL_AUTHORING_ENV_KWARGS,
    )
    query = SqlQuery(sql="SELECT x FROM read_csv('data/t.csv')", source="test_db")

    try:
        result = adapter.execute(query)
        assert result.error is None, result.error
        assert result.data == [{"x": 9}]
    finally:
        adapter.close()


def test_duckdb_relative_paths_resolve_against_project_not_cwd(
    tmp_path: Path,
    monkeypatch,
    local_project: Callable[..., FilesystemProject],
) -> None:
    """Relative read_csv() resolves against the project root with cwd elsewhere."""
    root = tmp_path / "project"
    (root / "data").mkdir(parents=True)
    (root / "data" / "t.csv").write_text("x\n7\n")
    monkeypatch.chdir(tmp_path)  # cwd deliberately NOT the project root

    adapter = DuckDBRunner(
        data_dir=local_project(root).data_path("."),
        source_config=DuckDBSourceConfig(type="duckdb"),
        read_only=False,
    )
    query = SqlQuery(sql="SELECT x FROM read_csv('data/t.csv')", source="test_db")

    try:
        result = adapter.execute(query)
        assert result.error is None
        assert result.data == [{"x": 7}]
        assert Path.cwd() == tmp_path
    finally:
        adapter.close()


def test_duckdb_execution_does_not_leak_process_cwd_under_concurrency(
    tmp_path, local_project: Callable[..., FilesystemProject]
):
    """Concurrent DuckDB queries are serialized and restore the original cwd."""
    original_cwd = Path.cwd()
    root_a = tmp_path / "project_a"
    root_b = tmp_path / "project_b"
    root_a.mkdir()
    root_b.mkdir()

    adapter_a = DuckDBRunner(
        data_dir=local_project(root_a).data_path("."),
        source_config=DuckDBSourceConfig(type="duckdb"),
    )
    adapter_b = DuckDBRunner(
        data_dir=local_project(root_b).data_path("."),
        source_config=DuckDBSourceConfig(type="duckdb"),
    )
    query = SqlQuery(sql="SELECT 42 AS answer", source="test_db")

    a_entered = threading.Event()
    b_entered = threading.Event()
    release_a = threading.Event()

    conn_a = _BlockingConnection(a_entered, release=release_a)
    conn_b = _BlockingConnection(b_entered)

    adapter_a.backend._pooled_connection = lambda _raw, _path, _zone: conn_a  # type: ignore[method-assign, assignment]
    adapter_b.backend._pooled_connection = lambda _raw, _path, _zone: conn_b  # type: ignore[method-assign, assignment]

    def run_query(adapter: DuckDBRunner) -> None:
        result = adapter.execute(query)
        assert result.error is None

    thread_a = threading.Thread(target=run_query, args=(adapter_a,))
    thread_b = threading.Thread(target=run_query, args=(adapter_b,))

    try:
        thread_a.start()
        assert a_entered.wait(timeout=5), "First query never entered execution"

        thread_b.start()
        assert not b_entered.wait(timeout=0.2), (
            "Second query should block on execute lock"
        )

        release_a.set()

        thread_a.join(timeout=5)
        thread_b.join(timeout=5)

        assert not thread_a.is_alive()
        assert not thread_b.is_alive()
        assert b_entered.is_set(), (
            "Second query should run after the first releases lock"
        )
        assert Path.cwd() == original_cwd
    finally:
        os.chdir(original_cwd)
        adapter_a.close()
        adapter_b.close()
