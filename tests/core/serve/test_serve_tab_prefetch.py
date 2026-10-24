"""Serve warms the tabs one click away after it answers a board request."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from dbt_charts.cli.filesystem_project import FilesystemProject
from dbt_charts.core.board import BoardRenderResult
from dbt_charts.core.serve import server
from dbt_charts.core.serve.server import create_server

_BOARD_YAML = """\
title: Tabs
cache:
  prefetch: true
queries:
  shown:
    columns: [k, v]
    values:
      - [a, 1]
  hidden:
    columns: [k, v]
    values:
      - [a, 2]
charts:
  shown_chart:
    query: shown
    type: bar
    x: k
    y: v
  hidden_chart:
    query: hidden
    type: bar
    x: k
    y: v
tabs:
  id: view
  items:
    - title: Shown
      rows: [shown_chart]
    - title: Hidden
      rows: [hidden_chart]
"""


def _project(tmp_path: Path, board: str) -> Path:
    (tmp_path / "charts").mkdir()
    (tmp_path / "charts" / "x.yml").write_text(board)
    (tmp_path / "dbt_charts.yml").write_text("# project config\n")
    return tmp_path


@pytest.fixture
def prefetched(monkeypatch: pytest.MonkeyPatch) -> list[dict[str, Exception | None]]:
    outcomes: list[dict[str, Exception | None]] = []
    real = server.prefetch_dashboard

    def recording(*args: Any, **kwargs: Any) -> dict[str, Exception | None]:
        outcome = real(*args, **kwargs)
        outcomes.append(outcome)
        return outcome

    monkeypatch.setattr(server, "prefetch_dashboard", recording)
    return outcomes


def test_board_load_prefetches_the_hidden_tab(
    tmp_path: Path, prefetched: list[dict[str, Exception | None]]
) -> None:
    app = create_server(FilesystemProject(_project(tmp_path, _BOARD_YAML)))
    with TestClient(app) as client:
        response = client.get("/x")

    assert response.status_code == 200
    assert prefetched == [{"hidden": None}]


def test_board_load_prefetches_nothing_by_default(
    tmp_path: Path, prefetched: list[dict[str, Exception | None]]
) -> None:
    board = _BOARD_YAML.replace("cache:\n  prefetch: true\n", "", 1)
    app = create_server(FilesystemProject(_project(tmp_path, board)))
    with TestClient(app) as client:
        assert client.get("/x").status_code == 200

    assert prefetched == []


def test_board_without_tabs_prefetches_nothing(
    tmp_path: Path, prefetched: list[dict[str, Exception | None]]
) -> None:
    board = _BOARD_YAML.split("tabs:")[0] + "rows:\n  - shown_chart\n"
    app = create_server(FilesystemProject(_project(tmp_path, board)))
    with TestClient(app) as client:
        response = client.get("/x")

    assert response.status_code == 200
    assert prefetched == []


def test_tab_click_after_prefetch_reads_the_cache(
    tmp_path: Path,
    prefetched: list[dict[str, Exception | None]],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    rendered: list[BoardRenderResult] = []
    real = server.render_dashboard

    def recording(*args: Any, **kwargs: Any) -> BoardRenderResult:
        result = real(*args, **kwargs)
        rendered.append(result)
        return result

    monkeypatch.setattr(server, "render_dashboard", recording)
    app = create_server(FilesystemProject(_project(tmp_path, _BOARD_YAML)))
    with TestClient(app) as client:
        assert client.get("/x").status_code == 200
        assert client.get("/x?view=hidden").status_code == 200

    assert prefetched[0] == {"hidden": None}
    assert rendered[1].cache_hits == 1
