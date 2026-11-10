"""Serve never runs the render-time WARN-RESTATED-* check."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

import dbt_charts.core.render.renderer as renderer
from dbt_charts.cli.filesystem_project import FilesystemProject
from dbt_charts.core.compile.config import reset_config
from dbt_charts.core.serve.server import create_server

_BOARD_YAML = """\
title: Styled
queries:
  q:
    columns: [k, v]
    values:
      - [a, 1]
      - [b, 2]
charts:
  c:
    query: q
    type: bar
    x: k
    y: v
rows:
  - c
"""


@pytest.mark.parametrize("debug", ["true", "false"])
def test_serve_skips_the_render_time_restated_check(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, debug: str
) -> None:
    calls: list[object] = []

    def _spy(*_args: Any, **_kwargs: Any) -> list[object]:
        calls.append(None)
        return []

    monkeypatch.setattr(renderer, "auto_restated_warnings", _spy)
    reset_config()
    (tmp_path / "charts").mkdir()
    (tmp_path / "charts" / "x.yml").write_text(_BOARD_YAML)
    (tmp_path / "dbt_charts.yml").write_text(f"server:\n  debug: {debug}\n")
    try:
        app = create_server(FilesystemProject(tmp_path), no_cache=True)
        with TestClient(app) as client:
            assert client.get("/x").status_code == 200
    finally:
        reset_config()
    assert calls == []
