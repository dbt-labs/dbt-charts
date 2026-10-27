"""Serve stamps each load with its own render time."""

from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path

from fastapi.testclient import TestClient

from dbt_charts.cli.filesystem_project import FilesystemProject
from dbt_charts.core.serve.server import create_server

_BOARD_YAML = """\
title: Cached
queries:
  q:
    columns: [k, v]
    values:
      - [a, {a}]
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


def _ticking_clock() -> Callable[[], datetime]:
    """A clock a whole minute later on every read: two loads always straddle a
    boundary, as they sometimes do on a loaded CI runner."""
    minutes = iter(range(60))
    return lambda: datetime(2001, 2, 3, 4, next(minutes), tzinfo=UTC)


def _project(tmp_path: Path, a: int) -> Path:
    (tmp_path / "charts").mkdir(exist_ok=True)
    (tmp_path / "charts" / "x.yml").write_text(_BOARD_YAML.format(a=a))
    (tmp_path / "dbt_charts.yml").write_text("# project config\n")
    return tmp_path


def test_each_load_stamps_its_own_render_time(tmp_path: Path) -> None:
    """The time lives in the board chrome, read from this load's clock."""
    app = create_server(
        FilesystemProject(_project(tmp_path, 1)), no_cache=True, clock=_ticking_clock()
    )
    with TestClient(app) as client:
        first = client.get("/x").text
        second = client.get("/x").text
    assert 'data-rendered-at="2001-02-03T04:00:00Z"' in first
    assert 'data-rendered-at="2001-02-03T04:00:00Z"' not in second
