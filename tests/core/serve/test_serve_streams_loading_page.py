"""A browser navigation to a board gets the nav and a loading state before the board renders."""

from __future__ import annotations

from collections.abc import Awaitable, Callable, MutableMapping
from pathlib import Path
from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from dbt_charts.cli.filesystem_project import FilesystemProject
from dbt_charts.core.board import BoardRenderResult
from dbt_charts.core.serve import server
from dbt_charts.core.serve.server import EnvLeases, create_server

_BOARD_YAML = """\
title: Slow
queries:
  q:
    columns: [k, v]
    values:
      - [a, 1]
charts:
  c:
    query: q
    type: bar
    x: k
    y: v
"""

_BROKEN_YAML = """\
title: Broken
charts:
  c:
    query: missing
    type: bar
    x: k
    y: v
"""

_NAVIGATION = {"Sec-Fetch-Dest": "document"}

Message = MutableMapping[str, Any]


def _project(tmp_path: Path, board: str) -> FilesystemProject:
    (tmp_path / "charts").mkdir()
    (tmp_path / "charts" / "x.yml").write_text(board)
    (tmp_path / "charts" / "y.yml").write_text(_BOARD_YAML)
    (tmp_path / "dbt_charts.yml").write_text("# project config\n")
    return FilesystemProject(tmp_path)


class _Recorder:
    """Records, in order, each body chunk the app sends and each render it starts."""

    def __init__(self, app: FastAPI, monkeypatch: pytest.MonkeyPatch) -> None:
        self.app = app
        self.events: list[str] = []
        self.leased_during_render: list[bool] = []
        real = server.render_dashboard

        def recording(*args: Any, **kwargs: Any) -> BoardRenderResult:
            self.events.append("render")
            leases: EnvLeases = app.state.env_leases
            self.leased_during_render.append(kwargs["env"] in leases._counts)
            return real(*args, **kwargs)

        monkeypatch.setattr(server, "render_dashboard", recording)

    async def __call__(
        self,
        scope: Message,
        receive: Callable[[], Awaitable[Message]],
        send: Callable[[Message], Awaitable[None]],
    ) -> None:
        async def recording_send(message: Message) -> None:
            if message["type"] == "http.response.body" and message.get("body"):
                self.events.append(message["body"].decode("utf-8"))
            await send(message)

        await self.app(scope, receive, recording_send)


def test_navigation_flushes_nav_and_loading_state_before_rendering(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    recorder = _Recorder(create_server(_project(tmp_path, _BOARD_YAML)), monkeypatch)
    with TestClient(recorder) as client:
        response = client.get("/x", headers=_NAVIGATION)

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/html")
    first = recorder.events[0]
    assert first.startswith("<!DOCTYPE html>")
    assert "dbt-nav" in first
    assert "dbt-page-pending" in first
    assert "data-dbt-board" not in first
    assert recorder.events.index("render") > 0
    assert "data-dbt-board" in response.text
    assert response.text.rstrip().endswith("</html>")


def test_streamed_render_holds_its_env_lease(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A config reload must not close the env a streamed render is still using."""
    recorder = _Recorder(create_server(_project(tmp_path, _BOARD_YAML)), monkeypatch)
    with TestClient(recorder) as client:
        client.get("/x", headers=_NAVIGATION)

    assert recorder.leased_during_render == [True]


def test_streamed_render_error_follows_the_loading_state(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Once the 200 is on the wire, a failed render is shown in the page body."""
    recorder = _Recorder(create_server(_project(tmp_path, _BROKEN_YAML)), monkeypatch)
    with TestClient(recorder) as client:
        response = client.get("/x", headers=_NAVIGATION)

    assert response.status_code == 200
    assert "dbt-page-pending" in recorder.events[0]
    assert "ERR-" in response.text
    assert response.text.count('class="dbt-nav"') == 1


@pytest.mark.parametrize(
    ("url", "headers"),
    [
        ("/x", {}),
        ("/x?_embed=1", {"Sec-Fetch-Dest": "iframe"}),
    ],
    ids=["programmatic-client", "embed-frame"],
)
def test_other_requests_get_the_whole_page_at_once(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    url: str,
    headers: dict[str, str],
) -> None:
    recorder = _Recorder(create_server(_project(tmp_path, _BOARD_YAML)), monkeypatch)
    with TestClient(recorder) as client:
        response = client.get(url, headers=headers)

    assert response.status_code == 200
    assert recorder.events[0] == "render"
    assert "dbt-page-pending" not in response.text


def test_programmatic_client_keeps_the_error_status(tmp_path: Path) -> None:
    app = create_server(_project(tmp_path, _BROKEN_YAML))
    with TestClient(app) as client:
        response = client.get("/x")

    assert response.status_code >= 400


def test_download_is_never_streamed(tmp_path: Path) -> None:
    app = create_server(_project(tmp_path, _BOARD_YAML))
    with TestClient(app) as client:
        response = client.get("/x?format=svg", headers=_NAVIGATION)

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("image/svg+xml")
    assert "dbt-page-pending" not in response.text
