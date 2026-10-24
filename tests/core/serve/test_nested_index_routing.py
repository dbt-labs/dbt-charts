"""TDD tests for nested folder index.yml routing in dct serve.

Pins that:
- /support/ renders charts/support/index.yml when present, not a directory listing
- /support/ falls back to directory listing when no index.yml/yaml/md exists
- .yaml and .md index variants also resolve
- root / index behavior unchanged (still uses _resolve_root_index_board path)
- a real file route like /support/tickets still resolves
- trailing-slash and no-slash both work
"""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path

import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient

from dbt_charts.cli.filesystem_project import FilesystemProject
from dbt_charts.core.project import CHARTS_SUBDIR, folder_index_board
from dbt_charts.core.serve.server import (
    _resolve_board_file_path,
    create_server,
)

# ---------------------------------------------------------------------------
# Minimal board YAML that renders successfully (values query, no external DB)
# ---------------------------------------------------------------------------

_SIMPLE_BOARD = """\
title: Index Dashboard
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

_TICKETS_BOARD = """\
title: Tickets Dashboard
queries:
  q:
    type: values
    rows:
      - {n: 2}
charts:
  t:
    query: q
    type: table
rows:
  - t
"""


# ---------------------------------------------------------------------------
# Unit tests for folder_index_board
# ---------------------------------------------------------------------------


class TestResolveFolderIndexBoard:
    def test_returns_index_yml_when_present(
        self, tmp_path: Path, local_project: Callable[..., FilesystemProject]
    ) -> None:
        folder = tmp_path / "support"
        folder.mkdir()
        (folder / "index.yml").write_text(_SIMPLE_BOARD)
        p = local_project(tmp_path)
        result = folder_index_board(p.directory_for_fspath(folder))
        assert result is not None and result.relpath == "support/index.yml"

    def test_returns_index_yaml_when_only_yaml_present(
        self, tmp_path: Path, local_project: Callable[..., FilesystemProject]
    ) -> None:
        folder = tmp_path / "support"
        folder.mkdir()
        (folder / "index.yaml").write_text(_SIMPLE_BOARD)
        p = local_project(tmp_path)
        result = folder_index_board(p.directory_for_fspath(folder))
        assert result is not None and result.relpath == "support/index.yaml"

    def test_returns_index_md_when_only_md_present(
        self, tmp_path: Path, local_project: Callable[..., FilesystemProject]
    ) -> None:
        folder = tmp_path / "support"
        folder.mkdir()
        (folder / "index.md").write_text("# Support\n")
        p = local_project(tmp_path)
        result = folder_index_board(p.directory_for_fspath(folder))
        assert result is not None and result.relpath == "support/index.md"

    def test_returns_none_when_no_index(
        self, tmp_path: Path, local_project: Callable[..., FilesystemProject]
    ) -> None:
        folder = tmp_path / "support"
        folder.mkdir()
        p = local_project(tmp_path)
        assert folder_index_board(p.directory_for_fspath(folder)) is None

    def test_returns_index_markdown_when_only_markdown_present(
        self, tmp_path: Path, local_project: Callable[..., FilesystemProject]
    ) -> None:
        # Guards the docstring-drift fix: index.markdown is the 4th probed name.
        folder = tmp_path / "support"
        folder.mkdir()
        (folder / "index.markdown").write_text("# Support\n")
        p = local_project(tmp_path)
        result = folder_index_board(p.directory_for_fspath(folder))
        assert result is not None and result.relpath == "support/index.markdown"

    def test_prefers_yml_over_yaml(
        self, tmp_path: Path, local_project: Callable[..., FilesystemProject]
    ) -> None:
        folder = tmp_path / "support"
        folder.mkdir()
        (folder / "index.yml").write_text(_SIMPLE_BOARD)
        (folder / "index.yaml").write_text(_SIMPLE_BOARD)
        p = local_project(tmp_path)
        result = folder_index_board(p.directory_for_fspath(folder))
        assert result is not None and result.relpath == "support/index.yml"


class TestResolveBoardFilePathRejectsEscape:
    """A `..` segment in the URL is rejected with 403."""

    def test_dotdot_segment_returns_403(
        self, tmp_path: Path, local_project: Callable[..., FilesystemProject]
    ) -> None:
        boards = local_project(tmp_path).directory(CHARTS_SUBDIR)
        with pytest.raises(HTTPException) as exc:
            _resolve_board_file_path(boards, "a/../secret")
        assert exc.value.status_code == 403

    def test_leading_dotdot_returns_403(
        self, tmp_path: Path, local_project: Callable[..., FilesystemProject]
    ) -> None:
        boards = local_project(tmp_path).directory(CHARTS_SUBDIR)
        with pytest.raises(HTTPException) as exc:
            _resolve_board_file_path(boards, "../../etc/passwd")
        assert exc.value.status_code == 403


# ---------------------------------------------------------------------------
# Integration tests: folder routing via TestClient
# ---------------------------------------------------------------------------


@pytest.fixture
def pack_project(tmp_path: Path) -> Path:
    """Project with charts/support/index.yml and charts/support/tickets.yml."""
    boards = tmp_path / "charts"
    support = boards / "support"
    support.mkdir(parents=True)
    (support / "index.yml").write_text(_SIMPLE_BOARD)
    (support / "tickets.yml").write_text(_TICKETS_BOARD)
    return tmp_path


@pytest.fixture
def pack_project_no_index(tmp_path: Path) -> Path:
    """Project with charts/support/ but no index.yml — listing fallback."""
    boards = tmp_path / "charts"
    support = boards / "support"
    support.mkdir(parents=True)
    (support / "tickets.yml").write_text(_TICKETS_BOARD)
    return tmp_path


@pytest.fixture
def pack_project_yaml_index(tmp_path: Path) -> Path:
    """Project with charts/support/index.yaml (not .yml)."""
    boards = tmp_path / "charts"
    support = boards / "support"
    support.mkdir(parents=True)
    (support / "index.yaml").write_text(_SIMPLE_BOARD)
    return tmp_path


@pytest.fixture
def pack_project_md_index(tmp_path: Path) -> Path:
    """Project with charts/support/index.md."""
    boards = tmp_path / "charts"
    support = boards / "support"
    support.mkdir(parents=True)
    index_md = support / "index.md"
    index_md.write_text("# Support\n")
    return tmp_path


class TestNestedIndexRouting:
    def test_folder_url_renders_index_yml(self, pack_project: Path) -> None:
        """/support/ renders charts/support/index.yml, not the directory listing."""
        with TestClient(
            create_server(FilesystemProject(pack_project)),
            raise_server_exceptions=False,
        ) as client:
            response = client.get("/support/")
        assert response.status_code == 200
        # Index Dashboard is the title in _SIMPLE_BOARD; directory listing shows filenames
        assert "Index Dashboard" in response.text, (
            "/support/ should render charts/support/index.yml (title 'Index Dashboard'), "
            f"not a directory listing. Body preview: {response.text[:600]}"
        )

    def test_folder_url_no_trailing_slash_renders_index_yml(
        self, pack_project: Path
    ) -> None:
        """/support (no trailing slash) also renders index.yml."""
        with TestClient(
            create_server(FilesystemProject(pack_project)),
            raise_server_exceptions=False,
        ) as client:
            response = client.get("/support")
        assert response.status_code == 200
        assert "Index Dashboard" in response.text, (
            "/support (no slash) should render charts/support/index.yml. "
            f"Body preview: {response.text[:600]}"
        )

    def test_folder_url_falls_back_to_listing_when_no_index(
        self, pack_project_no_index: Path
    ) -> None:
        """/support/ falls back to directory listing when no index.yml exists."""
        with TestClient(
            create_server(FilesystemProject(pack_project_no_index)),
            raise_server_exceptions=False,
        ) as client:
            response = client.get("/support/")
        assert response.status_code == 200
        # Directory listing shows filenames; tickets.yml is the only file
        assert "tickets" in response.text, (
            "/support/ should show the directory listing (containing 'tickets') "
            f"when no index.yml exists. Body preview: {response.text[:600]}"
        )
        assert "Index Dashboard" not in response.text

    def test_yaml_index_variant_resolves(self, pack_project_yaml_index: Path) -> None:
        """/support/ renders index.yaml when index.yml is absent."""
        with TestClient(
            create_server(FilesystemProject(pack_project_yaml_index)),
            raise_server_exceptions=False,
        ) as client:
            response = client.get("/support/")
        assert response.status_code == 200
        assert "Index Dashboard" in response.text, (
            "/support/ should render charts/support/index.yaml. "
            f"Body preview: {response.text[:600]}"
        )

    def test_real_file_route_still_resolves(self, pack_project: Path) -> None:
        """/support/tickets still resolves to charts/support/tickets.yml."""
        with TestClient(
            create_server(FilesystemProject(pack_project)),
            raise_server_exceptions=False,
        ) as client:
            response = client.get("/support/tickets")
        assert response.status_code == 200
        assert "Tickets Dashboard" in response.text, (
            "/support/tickets should render charts/support/tickets.yml. "
            f"Body preview: {response.text[:600]}"
        )

    def test_root_index_behavior_unchanged(self, tmp_path: Path) -> None:
        """Root / still renders charts/index.yml (root-index path unchanged)."""
        boards = tmp_path / "charts"
        boards.mkdir()
        (boards / "index.yml").write_text(_SIMPLE_BOARD)
        with TestClient(
            create_server(FilesystemProject(tmp_path)),
            raise_server_exceptions=False,
        ) as client:
            response = client.get("/")
        assert response.status_code == 200
        assert "Index Dashboard" in response.text, (
            "Root / must still render charts/index.yml. "
            f"Body preview: {response.text[:600]}"
        )
