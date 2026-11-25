"""Tests for dbt_charts.agent_api.serve: startup composition for `dct serve`."""

from __future__ import annotations

import socket
import tempfile
from collections.abc import Iterator
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import pytest
from pydantic import ValidationError

from dbt_charts.agent_api.serve import (
    ServeSetup,
    _state_path,
    format_startup_failure,
    prepare_serve,
)
from dbt_charts.cli.filesystem_project import FilesystemProject
from dbt_charts.core.diagnostics import Diagnostic


def _app_double() -> SimpleNamespace:
    """Stands in for `create_server`'s app: an ASGI callable carrying its watch."""
    return SimpleNamespace(state=SimpleNamespace(watcher=object()))


@pytest.fixture
def listener() -> Iterator[socket.socket]:
    sock = socket.socket()
    sock.bind(("127.0.0.1", 0))
    sock.listen()
    yield sock
    sock.close()


@pytest.fixture
def project(tmp_path: Path) -> FilesystemProject:
    (tmp_path / "dbt_charts.yml").write_text("# project marker\n")
    return FilesystemProject(tmp_path)


class TestPrepareServe:
    def test_explicit_dialect_skips_inference(
        self, project: FilesystemProject, listener: socket.socket
    ) -> None:
        sentinel = _app_double()
        with (
            patch("dbt_charts.core.serve.port.resolve_listener", return_value=listener),
            patch("dbt_charts.core.project_roots.infer_dialect_from_dbt") as mock_infer,
            patch(
                "dbt_charts.core.serve.server.create_server",
                return_value=sentinel,
            ),
        ):
            result = prepare_serve(
                project,
                port=None,
                host="localhost",
                dialect="postgres",
                target=None,
                max_workers=None,
                no_cache=False,
                cache_path=None,
            )
        mock_infer.assert_not_called()
        assert isinstance(result, ServeSetup)
        assert result.dialect == "postgres"
        assert result.dialect_inferred is False
        assert result.app is sentinel
        assert result.port == listener.getsockname()[1]

    def test_infers_dialect_when_not_given(
        self, project: FilesystemProject, listener: socket.socket
    ) -> None:
        with (
            patch("dbt_charts.core.serve.port.resolve_listener", return_value=listener),
            patch(
                "dbt_charts.core.project_roots.infer_dialect_from_dbt",
                return_value="bigquery",
            ),
            patch(
                "dbt_charts.core.serve.server.create_server", return_value=_app_double()
            ),
        ):
            result = prepare_serve(
                project,
                port=None,
                host="localhost",
                dialect=None,
                target=None,
                max_workers=None,
                no_cache=False,
                cache_path=None,
            )
        assert isinstance(result, ServeSetup)
        assert result.dialect == "bigquery"
        assert result.dialect_inferred is True

    def test_no_dialect_inferred_falls_back_to_duckdb(
        self, project: FilesystemProject, listener: socket.socket
    ) -> None:
        with (
            patch("dbt_charts.core.serve.port.resolve_listener", return_value=listener),
            patch(
                "dbt_charts.core.project_roots.infer_dialect_from_dbt",
                return_value=None,
            ),
            patch(
                "dbt_charts.core.serve.server.create_server", return_value=_app_double()
            ),
        ):
            result = prepare_serve(
                project,
                port=None,
                host="localhost",
                dialect=None,
                target=None,
                max_workers=None,
                no_cache=False,
                cache_path=None,
            )
        assert isinstance(result, ServeSetup)
        assert result.dialect == "duckdb"
        assert result.dialect_inferred is False

    def test_invalid_default_theme_env_returns_diagnostic(
        self, project: FilesystemProject, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("DCT_DEFAULT_THEME", "nonexistent-theme-xyz")
        result = prepare_serve(
            project,
            port=None,
            host="localhost",
            dialect="duckdb",
            target=None,
            max_workers=None,
            no_cache=False,
            cache_path=None,
        )
        assert isinstance(result, Diagnostic)
        assert result.code == "ERR-INVALID-DEFAULT-THEME"

    def test_private_base_default_theme_env_returns_diagnostic(
        self, project: FilesystemProject, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("DCT_DEFAULT_THEME", "_base")
        result = prepare_serve(
            project,
            port=None,
            host="localhost",
            dialect="duckdb",
            target=None,
            max_workers=None,
            no_cache=False,
            cache_path=None,
        )
        assert isinstance(result, Diagnostic)
        assert result.code == "ERR-INVALID-DEFAULT-THEME"

    def test_valid_default_theme_env_proceeds(
        self,
        project: FilesystemProject,
        monkeypatch: pytest.MonkeyPatch,
        listener: socket.socket,
    ) -> None:
        with (
            patch("dbt_charts.core.serve.port.resolve_listener", return_value=listener),
            patch(
                "dbt_charts.core.project_roots.infer_dialect_from_dbt",
                return_value=None,
            ),
            patch(
                "dbt_charts.core.serve.server.create_server", return_value=_app_double()
            ),
        ):
            monkeypatch.setenv("DCT_DEFAULT_THEME", "paper")
            result = prepare_serve(
                project,
                port=None,
                host="localhost",
                dialect="duckdb",
                target=None,
                max_workers=None,
                no_cache=False,
                cache_path=None,
            )
        assert isinstance(result, ServeSetup)

    def test_dbt_charts_yml_validation_error_propagates_uncaught(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.delenv("DCT_DEFAULT_THEME", raising=False)
        (tmp_path / "dbt_charts.yml").write_text("theme: paper\n", encoding="utf-8")
        project = FilesystemProject(tmp_path)
        with pytest.raises(ValidationError):
            prepare_serve(
                project,
                port=None,
                host="localhost",
                dialect="duckdb",
                target=None,
                max_workers=None,
                no_cache=False,
                cache_path=None,
            )

    def test_a_stale_nav_order_name_returns_a_diagnostic_at_its_line(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.delenv("DCT_DEFAULT_THEME", raising=False)
        (tmp_path / "charts" / "support").mkdir(parents=True)
        (tmp_path / "charts" / "support" / "tickets.yml").write_text("title: T\n")
        (tmp_path / "dbt_charts.yml").write_text(
            "server:\n  nav_order:\n    support:\n      - tickets\n      - gone\n"
        )

        result = prepare_serve(
            FilesystemProject(tmp_path),
            port=None,
            host="localhost",
            dialect="duckdb",
            target=None,
            max_workers=None,
            no_cache=False,
            cache_path=None,
        )

        assert isinstance(result, Diagnostic)
        assert result.code == "ERR-NAV-ORDER-INVALID"
        assert "'gone'" in result.message
        assert result.range is not None
        assert (result.range.file, result.range.start_line) == ("dbt_charts.yml", 5)


class TestPrepareServeDbtProjectDir:
    """Dialect inference reads the linked dbt_root; port allocation stays
    keyed off the dct project root, so the two must not share one variable."""

    def test_dialect_inference_uses_external_dbt_root(
        self, tmp_path: Path, listener: socket.socket
    ) -> None:
        project_root = tmp_path / "myproject"
        project_root.mkdir()
        (project_root / "dbt_charts.yml").write_text("# project marker\n")
        external_dbt = tmp_path / "external_dbt"
        external_dbt.mkdir()
        project = FilesystemProject(project_root, dbt_root=external_dbt)

        with (
            patch(
                "dbt_charts.core.serve.port.resolve_listener", return_value=listener
            ) as mock_resolve_listener,
            patch(
                "dbt_charts.core.project_roots.infer_dialect_from_dbt",
                return_value=None,
            ) as mock_infer,
            patch(
                "dbt_charts.core.serve.server.create_server", return_value=_app_double()
            ),
        ):
            prepare_serve(
                project,
                port=None,
                host="localhost",
                dialect=None,
                target=None,
                max_workers=None,
                no_cache=False,
                cache_path=None,
            )

        mock_infer.assert_called_once_with(external_dbt.resolve(), None)
        mock_resolve_listener.assert_called_once()
        assert (
            mock_resolve_listener.call_args.kwargs["project_dir"]
            == project_root.resolve()
        )

    def test_listens_before_the_server_runs(self, project: FilesystemProject) -> None:
        """A request sent while startup still loads the project waits, not fails."""
        with patch(
            "dbt_charts.core.serve.server.create_server", return_value=_app_double()
        ):
            result = prepare_serve(
                project,
                port=None,
                host="127.0.0.1",
                dialect="duckdb",
                target=None,
                max_workers=None,
                no_cache=False,
                cache_path=None,
            )
        assert isinstance(result, ServeSetup)
        try:
            assert result.listener.getsockname()[1] == result.port
            socket.create_connection(("127.0.0.1", result.port), timeout=1).close()
        finally:
            result.listener.close()


class TestFormatStartupFailure:
    def test_returns_startup_failed_diagnostic_with_detail(self) -> None:
        diagnostic = format_startup_failure(OSError("address already in use"))
        assert diagnostic.code == "ERR-STARTUP-FAILED"
        assert "address already in use" in str(diagnostic.model_dump())


class TestDetachedRecordLocation:
    def test_record_path_ignores_tmpdir(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """--stop must find the record --detach wrote, whatever TMPDIR each saw."""
        paths = []
        for name in ("a", "b"):
            monkeypatch.setattr(tempfile, "tempdir", str(tmp_path / name))
            paths.append(_state_path(8080))
        assert paths[0] == paths[1]
        assert not paths[0].is_relative_to(tmp_path)


@pytest.mark.slow
@pytest.mark.timeout(180)
def test_concurrent_detached_starts_on_one_port_have_one_winner(
    project: FilesystemProject, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Both starts see the port free; the bind alone decides, and the loser raises."""
    from concurrent.futures import ThreadPoolExecutor

    from dbt_charts.agent_api.serve import (
        DetachedServeError,
        start_detached,
        stop_detached,
    )

    monkeypatch.setattr(
        "dbt_charts.agent_api.serve.user_state_dir", lambda: str(tmp_path / "state")
    )
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        port = probe.getsockname()[1]

    def start() -> object:
        try:
            return start_detached(
                project, port=port, host="127.0.0.1", startup_timeout=120
            )
        except DetachedServeError as exc:
            return exc

    with ThreadPoolExecutor(2) as pool:
        futures = [pool.submit(start) for _ in range(2)]
        results = [f.result() for f in futures]
    try:
        errors = [r for r in results if isinstance(r, DetachedServeError)]
        assert len(errors) == 1
        assert str(port) in str(errors[0])
    finally:
        stop_detached(port)
