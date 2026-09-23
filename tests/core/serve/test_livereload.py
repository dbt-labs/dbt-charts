"""Tests for live-reload SSE endpoint and script injection.

Covers:
- /__livereload endpoint responds with text/event-stream content type
- each watch change becomes one `data: reload` event
- closing the watch ends the stream, and the stream leaves no subscriber behind
- HTML board responses include the /__livereload script tag
- Non-HTML responses (SVG, PNG, PDF, YAML) do NOT include the script tag
"""

from __future__ import annotations

from collections.abc import AsyncGenerator
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from dbt_charts.cli.filesystem_project import FilesystemProject
from dbt_charts.core.serve.server import create_server

_LIVERELOAD_SCRIPT = "/__livereload"


@pytest.fixture
def simple_project(tmp_path: Path) -> Path:
    """Minimal project with one board for serve tests."""
    (tmp_path / "dbt_charts.yml").write_text("# project config\n")
    boards_dir = tmp_path / "charts"
    boards_dir.mkdir()
    (boards_dir / "hello.yml").write_text("title: Hello\nrows: []\n")
    return tmp_path


class _ScriptedWatcher:
    """Stands in for the server's FileWatcher: a fixed number of changes, then close."""

    def __init__(self, change_count: int) -> None:
        self._change_count = change_count
        self.open_streams = 0

    async def start(self) -> None: ...

    async def stop(self) -> None: ...

    async def changes(self) -> AsyncGenerator[None, None]:
        self.open_streams += 1
        try:
            for _ in range(self._change_count):
                yield
        finally:
            self.open_streams -= 1


def _served(project: Path, change_count: int) -> tuple[TestClient, _ScriptedWatcher]:
    app = create_server(FilesystemProject(project))
    watcher = _ScriptedWatcher(change_count)
    app.state.watcher = watcher
    return TestClient(app), watcher


class TestLivereloadEndpoint:
    def test_livereload_endpoint_returns_event_stream_content_type(
        self, simple_project: Path
    ) -> None:
        """/__livereload must respond with text/event-stream content type.

        The SSE endpoint must advertise the correct content type so EventSource
        in the browser establishes an SSE connection (not a plain HTTP download).
        """
        client, _ = _served(simple_project, 0)
        with client:
            response = client.get("/__livereload")
        assert "text/event-stream" in response.headers.get("content-type", ""), (
            f"Expected text/event-stream, got: {response.headers.get('content-type')}"
        )

    def test_livereload_endpoint_emits_reload_on_change(
        self, simple_project: Path
    ) -> None:
        """A watched-file change must push a ``data: reload`` SSE event."""
        client, _ = _served(simple_project, 1)
        with client:
            response = client.get("/__livereload")
        assert response.text == "data: reload\n\n", (
            f"Expected exactly one reload event, got: {response.text!r}"
        )

    def test_closing_the_watch_ends_the_stream(self, simple_project: Path) -> None:
        """A closed watch ends the response instead of holding it open.

        This is what lets CTRL-C finish: uvicorn force-cancels a response still
        open when its graceful-shutdown timeout expires, and logs that as an
        unhandled ASGI exception.
        """
        client, _ = _served(simple_project, 0)
        with client:
            response = client.get("/__livereload")
        assert response.text == "", (
            f"Expected a closed watch to end the stream silently, got: {response.text!r}"
        )

    def test_finished_stream_leaves_no_subscriber(self, simple_project: Path) -> None:
        """Every closed tab must drop its queue, or the watch feeds the dead."""
        client, watcher = _served(simple_project, 1)
        with client:
            client.get("/__livereload")
        assert watcher.open_streams == 0


class TestLivereloadScriptInjection:
    def test_html_board_response_contains_livereload_script(
        self, simple_project: Path
    ) -> None:
        """HTML board responses must include a /__livereload EventSource script.

        The script enables automatic browser refresh when watched files change,
        so the user does not need to manually refresh after editing a board.
        """
        with TestClient(
            create_server(FilesystemProject(simple_project)),
        ) as client:
            response = client.get("/hello/")
        assert response.status_code == 200
        assert _LIVERELOAD_SCRIPT in response.text, (
            f"Expected /__livereload in HTML response, body preview: {response.text[:500]}"
        )

    def test_svg_download_does_not_contain_livereload_script(
        self, simple_project: Path
    ) -> None:
        """SVG download responses must NOT include the livereload script tag."""
        with TestClient(
            create_server(FilesystemProject(simple_project)),
        ) as client:
            response = client.get("/hello.svg")
        # SVG download may redirect or 200; either way the script must be absent
        # from the final response body.
        assert _LIVERELOAD_SCRIPT not in response.text, (
            "/__livereload script must not appear in SVG response"
        )

    def test_yaml_source_does_not_contain_livereload_script(
        self, simple_project: Path
    ) -> None:
        """Raw YAML source responses (text/plain) must NOT include the livereload script."""
        with TestClient(
            create_server(FilesystemProject(simple_project)),
        ) as client:
            response = client.get("/hello.yaml")
        assert _LIVERELOAD_SCRIPT not in response.text, (
            "/__livereload script must not appear in YAML source response"
        )
