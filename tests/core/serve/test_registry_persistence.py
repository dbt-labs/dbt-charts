"""Tests for cross-request adapter-registry persistence in dct serve.

The per-source connection pool (``SqlAdapter._source_pools``) lives on a
``SqlAdapter`` inside an ``AdapterRegistry``. ``dct serve`` builds one registry at
startup and keeps it warm on ``app.state.adapter_registry`` across requests — it is
rebuilt only when ``dbt_charts.yml`` changes (source config edits) and closed on
shutdown. So a repeat view reuses the already-open warehouse connections.
"""

from __future__ import annotations

import os
from pathlib import Path
from unittest.mock import MagicMock

from fastapi.testclient import TestClient

from dbt_charts.cli.filesystem_project import FilesystemProject
from dbt_charts.core.serve import server as server_mod

_BOARD_YAML = """\
title: Persist test
queries:
  q:
    type: values
    rows:
      - v: 1
charts:
  c: {query: q, type: kpi, value: v}
rows:
  - c
"""


def _make_project(tmp_path: Path) -> Path:
    boards = tmp_path / "charts"
    boards.mkdir()
    (boards / "x.yml").write_text(_BOARD_YAML)
    return tmp_path


class TestRegistryPersistence:
    def test_registry_built_once_and_reused_across_requests(
        self, tmp_path: Path, monkeypatch
    ) -> None:
        """The registry is built once at startup and the same instance serves
        every request (warm pool persists)."""
        project = _make_project(tmp_path)
        spy = MagicMock(wraps=server_mod.build_adapter_registry)
        monkeypatch.setattr(server_mod, "build_adapter_registry", spy)

        app = server_mod.create_server(FilesystemProject(project), no_cache=True)
        with TestClient(app, raise_server_exceptions=False) as client:
            r1 = client.get("/x")
            r2 = client.get("/x")
            live_registry = app.state.adapter_registry

        assert r1.status_code == 200
        assert r2.status_code == 200
        # Built exactly once (at startup); no per-request rebuild.
        assert spy.call_count == 1
        # The same warm registry object served both requests.
        assert live_registry is app.state.adapter_registry

    def test_config_change_rebuilds_and_closes_old_registry(
        self, tmp_path: Path, monkeypatch
    ) -> None:
        """Editing dbt_charts.yml rebuilds the registry and closes the superseded one."""
        project = _make_project(tmp_path)
        config = project / "dbt_charts.yml"
        config.write_text("name: t\n")
        spy = MagicMock(wraps=server_mod.build_adapter_registry)
        monkeypatch.setattr(server_mod, "build_adapter_registry", spy)

        app = server_mod.create_server(FilesystemProject(project), no_cache=True)
        with TestClient(app, raise_server_exceptions=False) as client:
            client.get("/x")
            old = app.state.adapter_registry
            old.close = MagicMock(wraps=old.close)  # type: ignore[method-assign]
            # Advance the config mtime by 1s to guarantee a detectable change.
            st = config.stat()
            os.utime(config, (st.st_atime, st.st_mtime + 1))
            client.get("/x")
            new = app.state.adapter_registry

        assert new is not old  # rebuilt from the edited config
        old.close.assert_called_once()  # old pool released on swap
        assert spy.call_count == 2  # startup build + rebuild

    def test_board_edit_does_not_rebuild_registry(
        self, tmp_path: Path, monkeypatch
    ) -> None:
        """A board edit rides on fresh reads — it must not churn the warm registry."""
        project = _make_project(tmp_path)
        spy = MagicMock(wraps=server_mod.build_adapter_registry)
        monkeypatch.setattr(server_mod, "build_adapter_registry", spy)

        app = server_mod.create_server(FilesystemProject(project), no_cache=True)
        with TestClient(app, raise_server_exceptions=False) as client:
            client.get("/x")
            registry = app.state.adapter_registry
            board = project / "charts" / "x.yml"
            st = board.stat()
            os.utime(board, (st.st_atime, st.st_mtime + 1))
            client.get("/x")

        assert app.state.adapter_registry is registry  # unchanged
        assert spy.call_count == 1  # no rebuild
