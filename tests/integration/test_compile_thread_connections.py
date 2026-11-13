"""A host that keeps a database connection per thread leaves none open per render.

A render compiles the board in Rust on a short-lived thread beside Python's compile, and that
thread reads project files through the host. A host that opens a connection on first use per
thread (a framework ORM does) gets one there. The env's ``release_thread`` is where the host
closes it, so the count of open backends must not grow with the number of renders, and must not
wait for a garbage collection: the holder here is a reference cycle, as such connection
wrappers are.
"""

from __future__ import annotations

import threading
import time
from pathlib import Path

import psycopg
import pytest
from pytest_postgresql.executors import PostgreSQLExecutor

from dbt_charts.agent_api import ProjectSession
from dbt_charts.cli.filesystem_project import FilesystemProject
from dbt_charts.core.execute.env import make_exec_env
from dbt_charts.core.project import InMemoryBoard

_BOARD = """\
title: t
charts:
  c:
    type: kpi
    value: n
    query:
      columns: [n]
      values:
        - [1]
rows:
  - c
"""

pytestmark = [
    pytest.mark.timeout(180),
    pytest.mark.xdist_group("postgres_cluster"),
]


class _ConnectionHolder:
    """A thread's connection, held through a cycle so only an explicit close frees it."""

    def __init__(self, cluster: PostgreSQLExecutor) -> None:
        self.me = self
        self.connection = psycopg.connect(
            host=cluster.host,
            port=cluster.port,
            user=cluster.user,
            password=cluster.password,
            dbname=cluster.maintenance_dbname,
            autocommit=True,
        )


class _ConnectionPerThreadProject(FilesystemProject):
    """Reads files through a connection each thread opens on first use and keeps."""

    def __init__(self, root: Path, cluster: PostgreSQLExecutor) -> None:
        super().__init__(root)
        self._cluster = cluster
        self._thread_local = threading.local()
        self.threads_that_read: set[str] = set()

    def _touch_database(self) -> None:
        if getattr(self._thread_local, "holder", None) is None:
            self._thread_local.holder = _ConnectionHolder(self._cluster)
        self.threads_that_read.add(threading.current_thread().name)

    def close_thread_connection(self) -> None:
        holder: _ConnectionHolder | None = getattr(self._thread_local, "holder", None)
        if holder is not None:
            holder.connection.close()
            self._thread_local.holder = None

    def read_text(self, relpath: str) -> str:
        self._touch_database()
        return super().read_text(relpath)

    def exists(self, relpath: str) -> bool:
        self._touch_database()
        return super().exists(relpath)


def _open_backends(cluster: PostgreSQLExecutor) -> int:
    with psycopg.connect(
        host=cluster.host,
        port=cluster.port,
        user=cluster.user,
        password=cluster.password,
        dbname=cluster.maintenance_dbname,
        autocommit=True,
    ) as admin:
        row = admin.execute(
            "SELECT count(*) FROM pg_stat_activity "
            "WHERE usename = %s AND datname = %s AND pid <> pg_backend_pid()",
            (cluster.user, cluster.maintenance_dbname),
        ).fetchone()
    assert row is not None
    return int(row[0])


def _backends_once_stable(cluster: PostgreSQLExecutor) -> int:
    """The open-backend count once a server-side close has shown up in ``pg_stat_activity``."""
    deadline = time.monotonic() + 10
    count = _open_backends(cluster)
    while time.monotonic() < deadline:
        time.sleep(0.25)
        latest = _open_backends(cluster)
        if latest == count:
            return latest
        count = latest
    return count


def test_the_engine_compile_thread_leaves_no_connection_per_render(
    tmp_path: Path, pg_cluster: PostgreSQLExecutor
) -> None:
    (tmp_path / "dbt_charts.yml").write_text("sources: {}\n")
    (tmp_path / "charts").mkdir()
    (tmp_path / "charts" / "b.yml").write_text(_BOARD)
    project = _ConnectionPerThreadProject(tmp_path, pg_cluster)
    env = make_exec_env(project, release_thread=project.close_thread_connection)

    with ProjectSession.from_project(project, env=env) as session:
        board = InMemoryBoard(_BOARD, path=project.path("charts/b.yml"))

        def render() -> None:
            result = session.render_board(board=board, format="svg", use_cache=False)
            assert result.status == "ok", result.board_error

        render()
        assert any(name.startswith("dct-plan") for name in project.threads_that_read)
        after_first = _backends_once_stable(pg_cluster)
        for _ in range(3):
            render()
        assert _backends_once_stable(pg_cluster) == after_first
