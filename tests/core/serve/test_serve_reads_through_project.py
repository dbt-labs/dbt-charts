"""Tests for serve-layer board reads.

``dct serve`` is filesystem-only: ``create_server`` takes a ``FilesystemProject``,
not the abstract ``Project`` base. Two kinds of coverage live here:

- The direct-helper tests (``TestHandleInspectRouteReadsCustomTemplateViaProject``,
  ``TestRenderBoardFileExistenceCheckViaProject``) call ``_handle_inspect_route`` /
  ``_render_board_file`` directly against an in-memory ``Project`` double, pinning
  that those two helpers still read via ``project.exists()``/``project.read_text()``
  rather than a raw ``Path`` — a unit-level seam-purity check independent of the
  full app.
- The full-app tests (``TestRawYamlEndpointReadsViaProject``,
  ``TestFolderIndexBoardResolvesViaProject``, ``TestBoardFileResolutionViaProject``)
  drive a real ``create_server(FilesystemProject)`` through ``TestClient``: FS-backed
  behavioral coverage of URL -> rendered-board resolution (raw .yaml endpoint,
  project-root index-board discovery, markdown-board resolution), not seam-vs-disk
  discrimination — there is only disk now.
"""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path
from unittest.mock import patch

import pytest

from dbt_charts.cli.filesystem_project import FilesystemProject
from dbt_charts.core.project import Project

# ---------------------------------------------------------------------------
# _handle_inspect_route custom template reads through project
# ---------------------------------------------------------------------------


class TestHandleInspectRouteReadsCustomTemplateViaProject:
    """Custom inspect template at charts/inspect/<name>.yml must be read via
    project.read_text, not by Path.read_text on the raw filesystem path."""

    def test_custom_inspect_template_comes_from_project(
        self,
        tmp_path: Path,
        in_memory_project: Callable[[Path, dict[str, str]], Project],
    ) -> None:
        """When project.exists('charts/inspect/model.yml') is True,
        _handle_inspect_route must call project.read_text to get the YAML,
        not Path.read_text on the raw filesystem path.

        Failing state: code calls custom_path.is_file() which returns False
        (file is not on disk) → falls back to built-in template → the custom
        marker never reaches render_inspect_dashboard.

        Passing state: code calls project.exists(...) → True → project.read_text
        → custom YAML is passed to render_inspect_dashboard.
        """
        from dbt_charts.core.serve.server import _handle_inspect_route

        custom_yaml = "title: Custom Model Inspect\nrows: []\n"

        project = in_memory_project(
            tmp_path,
            {"charts/inspect/model.yml": custom_yaml},
        )

        captured: list[str] = []

        def fake_render(template_yaml: str, *args: object, **kwargs: object) -> str:
            captured.append(template_yaml)
            return "<html>ok</html>"

        with patch(
            "dbt_charts.core.serve.server.render_inspect_dashboard",
            side_effect=fake_render,
        ):
            _handle_inspect_route(
                template_name="model",
                variables={},
                project=project,
            )

        assert captured, "render_inspect_dashboard was not called"
        assert captured[0] == custom_yaml, (
            "Expected render_inspect_dashboard to be called with the custom template "
            f"from project.read_text, but got: {captured[0][:200]!r}"
        )


# ---------------------------------------------------------------------------
# _render_board_file existence check goes through project.exists
# ---------------------------------------------------------------------------


class TestRenderBoardFileExistenceCheckViaProject:
    """The existence check before compile in _render_board_file must go through
    project.exists, not Path.exists on the raw filesystem path."""

    def test_board_not_in_project_returns_404(
        self,
        tmp_path: Path,
        in_memory_project: Callable[[Path, dict[str, str]], Project],
    ) -> None:
        """When project.exists(relpath) is False, _render_board_file must return
        a 404 response even if the physical file exists on disk.

        Failing state: code calls file_path.exists() — True because the file IS
        on disk — so no 404 is raised and render proceeds.

        Passing state: code uses project to check existence; InMemoryProject.exists
        returns False → 404.
        """
        from fastapi import HTTPException

        from dbt_charts.core.execute.adapters.adapter_registry import AdapterRegistry
        from dbt_charts.core.execute.executor import utc_now
        from dbt_charts.core.render.svg_cache import MemorySvgStore
        from dbt_charts.core.serve.server import _render_board_file

        # Write a real board file on disk so Path.exists() returns True.
        boards_dir = tmp_path / "charts"
        boards_dir.mkdir()
        board_path = boards_dir / "test.yml"
        board_path.write_text("title: Test\nrows: []\n")

        # in_memory_project has NO entry for this file → project.exists returns False.
        project = in_memory_project(tmp_path, {})

        with pytest.raises(HTTPException) as exc_info:
            _render_board_file(
                file_path=project.path("charts/test.yml"),
                variables={},
                project=project,
                adapter_registry=AdapterRegistry(project=project),
                svg_store=MemorySvgStore(),
                clock=utc_now,
            )

        assert exc_info.value.status_code == 404


# ---------------------------------------------------------------------------
# raw .yaml endpoint serves the board's real content
# ---------------------------------------------------------------------------


class TestRawYamlEndpointReadsViaProject:
    """The raw .yaml text endpoint serves a board's on-disk source verbatim."""

    def test_raw_yaml_content_comes_from_project(
        self,
        tmp_path: Path,
        local_project: Callable[..., FilesystemProject],
    ) -> None:
        """A .yaml URL request is served as plain text from the board file on disk."""
        from fastapi.testclient import TestClient

        from dbt_charts.core.serve.server import create_server

        boards_dir = tmp_path / "charts"
        boards_dir.mkdir()
        (boards_dir / "revenue.yml").write_text("title: Revenue\nrows: []\n")

        project = local_project(tmp_path)
        app = create_server(project)
        with TestClient(app, raise_server_exceptions=False) as client:
            response = client.get("/revenue.yaml")

        assert response.status_code == 200, (
            f"Expected 200 for /revenue.yaml, got {response.status_code}: {response.text[:200]}"
        )
        assert "title: Revenue" in response.text, (
            "Expected the board's raw YAML source to be served, "
            f"but got: {response.text[:200]!r}"
        )


# ---------------------------------------------------------------------------
# Project-root index board is served at '/'
# ---------------------------------------------------------------------------


class TestFolderIndexBoardResolvesViaProject:
    """A project-root index.yml is served at '/' instead of the directory listing.

    Uses a project-root-level index.yml (not charts/index.yml): the get_board
    route's "project-level index board for root" check is unconditional, while
    the charts/index.yml fallback path is additionally gated by a raw disk
    dir_path.is_dir() check (directory-listing debt, out of scope here) — this
    test isolates the root-index behavior from that separate gate.
    """

    def test_root_index_board_served_from_project_store(
        self,
        tmp_path: Path,
        local_project: Callable[..., FilesystemProject],
    ) -> None:
        """A project-root index.yml on disk is served at '/', not skipped in
        favor of the (empty) directory listing."""
        from fastapi.testclient import TestClient

        from dbt_charts.core.serve.server import create_server

        (tmp_path / "index.yml").write_text(
            "title: Root Index\ntext: MARKERROOTINDEX\n"
        )

        project = local_project(tmp_path)
        app = create_server(project)
        with TestClient(app, raise_server_exceptions=False) as client:
            response = client.get("/")

        assert response.status_code == 200, (
            f"Expected 200 for '/', got {response.status_code}: {response.text[:200]}"
        )
        assert "MARKERROOTINDEX" in response.text
        assert "No boards or directories found" not in response.text


# ---------------------------------------------------------------------------
# Board-file resolution picks the correct real-file suffix
# ---------------------------------------------------------------------------


class TestBoardFileResolutionViaProject:
    """_resolve_board_file_path must resolve to the real board file on disk,
    picking the correct suffix among the candidate list."""

    def test_markdown_board_resolved_from_project_store(
        self,
        tmp_path: Path,
        local_project: Callable[..., FilesystemProject],
    ) -> None:
        """A URL whose only real candidate is a .md board (no .yml sibling)
        must resolve to that .md file."""
        from fastapi.testclient import TestClient

        from dbt_charts.core.serve.server import create_server

        boards_dir = tmp_path / "charts"
        boards_dir.mkdir()
        # No underscores in the marker: markdown parses `_..._` as emphasis,
        # which would split the literal string across a <tspan>.
        (boards_dir / "notes.md").write_text("# Notes\n\nMARKERMDBOARD\n")

        project = local_project(tmp_path)
        app = create_server(project)
        with TestClient(app, raise_server_exceptions=False) as client:
            response = client.get("/notes/")

        assert response.status_code == 200, (
            f"Expected 200 for '/notes/', got {response.status_code}: {response.text[:200]}"
        )
        assert "MARKERMDBOARD" in response.text
