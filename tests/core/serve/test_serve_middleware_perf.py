"""Regression tests for serve middleware performance fixes."""

from __future__ import annotations

import os
from pathlib import Path
from unittest.mock import patch

from fastapi.testclient import TestClient

from dbt_charts.cli.filesystem_project import FilesystemProject
from dbt_charts.core.serve.alias_index import AliasIndex
from dbt_charts.core.serve.server import create_server

_MINIMAL_BOARD = """\
title: Test
queries:
  q:
    type: values
    rows:
      - {n: 1}
charts:
  t:
    query: q
    type: table
rows:
  - t
"""


def _project(tmp_path: Path) -> Path:
    (tmp_path / "dbt_charts.yml").write_text("name: t\n")
    (tmp_path / "charts").mkdir()
    return tmp_path


def test_health_and_templates_skip_reload(tmp_path: Path) -> None:
    """GET /health and GET /templates skip the middleware's change check entirely."""
    project_dir = _project(tmp_path)
    app = create_server(FilesystemProject(project_dir))
    with TestClient(app) as client:
        # Patch after startup so we only count middleware calls, not the boot probe.
        with patch("dbt_charts.core.serve.server._config_mtime") as config_spy:
            client.get("/health")
            client.get("/templates")
        assert config_spy.call_count == 0


def test_alias_index_not_rebuilt_on_unchanged_requests(tmp_path: Path) -> None:
    """AliasIndex.build is not called on requests when no board files have changed."""
    project_dir = _project(tmp_path)
    app = create_server(FilesystemProject(project_dir))
    with TestClient(app) as client:
        # Lifespan already built the index and stored the mtime.
        with patch.object(AliasIndex, "build", wraps=AliasIndex.build) as build_spy:
            client.get("/")
            client.get("/")
        assert build_spy.call_count == 0


def test_alias_index_rebuilt_when_board_file_touched(tmp_path: Path) -> None:
    """AliasIndex.build is called again when a board file's mtime changes."""
    project_dir = _project(tmp_path)
    board_file = project_dir / "charts" / "sales.yml"
    board_file.write_text(_MINIMAL_BOARD)

    app = create_server(FilesystemProject(project_dir))
    # Prime: lifespan builds and stores mtime; first request does no rebuild.
    with (
        TestClient(app) as client,
        patch.object(AliasIndex, "build", wraps=AliasIndex.build) as build_spy,
    ):
        client.get("/")
        assert build_spy.call_count == 0

        # Advance the mtime by 1 second to guarantee a detectable change.
        st = board_file.stat()
        os.utime(board_file, (st.st_atime, st.st_mtime + 1))

        client.get("/")
        assert build_spy.call_count == 1


def test_alias_index_rebuilt_when_markdown_board_touched(tmp_path: Path) -> None:
    """AliasIndex.build fires on .markdown board changes — BOARD_CANDIDATE_SUFFIXES covers all types."""
    project_dir = _project(tmp_path)
    md_board = project_dir / "charts" / "notes.markdown"
    md_board.write_text("---\ntitle: Notes\n---\n# Notes\n")

    app = create_server(FilesystemProject(project_dir))
    with (
        TestClient(app) as client,
        patch.object(AliasIndex, "build", wraps=AliasIndex.build) as build_spy,
    ):
        client.get("/")
        assert build_spy.call_count == 0

        st = md_board.stat()
        os.utime(md_board, (st.st_atime, st.st_mtime + 1))

        client.get("/")
        assert build_spy.call_count == 1


def test_health_names_the_serving_process_and_project(tmp_path: Path) -> None:
    """`dct serve --detach` identifies its server by asking it, not the OS."""
    import os

    project_dir = _project(tmp_path)
    with TestClient(create_server(FilesystemProject(project_dir))) as client:
        body = client.get("/health").json()

    assert body["pid"] == os.getpid()
    assert Path(body["project"]) == project_dir
