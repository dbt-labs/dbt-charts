"""Smoke-depth tests for the dct serve CLI command.

Covers argument-validation paths and the startup banner. Does NOT start the HTTP
server. All assertions pin exit codes to the documented values (0, 1, 2, and the
3 uvicorn reserves for a server that never started — never != 0).
"""

from __future__ import annotations

import re
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import pytest
import uvicorn
from fastapi.testclient import TestClient
from typer.testing import CliRunner

from dbt_charts.cli.filesystem_project import FilesystemProject
from dbt_charts.cli.main import app
from dbt_charts.core.serve.server import create_server

runner = CliRunner()

_ANSI_RE = re.compile(r"\x1b\[[0-9;]*m")


def _run_started(server: uvicorn.Server) -> None:
    """Stand-in for Server.run(): reports a served lifetime without binding a port."""
    server.started = True


# Every URL the startup banner prints, reduced to its path+query. The bare-path
# alternative matters as much as the absolute one: a route hint is as likely to be
# spelled "/data/<source>/" as a full URL, and either spelling has to resolve.
_BANNER_URL_RE = re.compile(r"https?://[^/\s]+(?P<path>/\S*)?|(?P<bare>/\S+)")


class TestDftServeHelp:
    """dct serve --help exits 0 and lists the documented options."""

    @pytest.mark.parametrize(
        "option",
        [
            "--port",
            "--host",
            "--project-dir",
            "--dialect",
            "--target",
        ],
    )
    def test_help_shows_option(self, option: str) -> None:
        result = runner.invoke(app, ["serve", "--help"])
        assert result.exit_code == 0
        # Rich splits `--name` across SGR escapes when highlighting; strip ANSI
        # before substring check so `--port` matches across the styling boundary.
        assert option in _ANSI_RE.sub("", result.output)


class TestDftServeInvalidPort:
    """dct serve --port with a non-integer value exits 2 (Typer parse error)."""

    def test_non_integer_port_exits_2(self) -> None:
        result = runner.invoke(app, ["serve", "--port", "notanumber"])
        assert result.exit_code == 2, result.output


class TestDftServeNonexistentProjectDir:
    """dct serve --project-dir with a nonexistent path exits 2 (Typer exists=True)."""

    def test_nonexistent_project_dir_exits_2(self, tmp_path: Path) -> None:
        result = runner.invoke(
            app,
            ["serve", "--project-dir", str(tmp_path / "does_not_exist")],
        )
        assert result.exit_code == 2, result.output


class TestDftServeCacheFlag:
    """--cache PATH flows to create_server; --cache/--no-cache are mutually exclusive."""

    def test_cache_flag_flows_to_create_server(self, tmp_path: Path) -> None:
        (tmp_path / "dbt_charts.yml").write_text("# project marker\n")
        captured: dict[str, object] = {}

        def fake_create_server(_project: object, **kwargs: object) -> object:
            captured.update(kwargs)
            return SimpleNamespace(state=SimpleNamespace(watcher=None))

        cache_path = tmp_path / "cache.duckdb"
        with (
            patch(
                "dbt_charts.core.serve.server.create_server",
                side_effect=fake_create_server,
            ),
            patch("uvicorn.Server.run", _run_started),
        ):
            result = runner.invoke(
                app,
                [
                    "serve",
                    "--project-dir",
                    str(tmp_path),
                    "--cache",
                    str(cache_path),
                ],
            )

        assert result.exit_code == 0, result.output
        assert captured["cache_path"] == cache_path
        assert captured["no_cache"] is False

    def test_cache_and_no_cache_are_mutually_exclusive(self, tmp_path: Path) -> None:
        result = runner.invoke(
            app,
            [
                "serve",
                "--project-dir",
                str(tmp_path),
                "--cache",
                str(tmp_path / "cache.duckdb"),
                "--no-cache",
            ],
        )
        assert result.exit_code == 2, result.output
        combined = result.output + (result.stderr or "")
        assert "mutually exclusive" in combined.lower()


class TestDftServeDiagnostics:
    """dct serve with startup errors renders ERR-* structured codes to stderr."""

    def test_invalid_default_theme_env_renders_structured_code(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        """DCT_DEFAULT_THEME=nonexistent → exit 1, ERR-INVALID-DEFAULT-THEME in stderr."""
        (tmp_path / "dbt_charts.yml").write_text("# project marker\n")
        monkeypatch.setenv("DCT_DEFAULT_THEME", "nonexistent-theme-xyz")
        result = runner.invoke(app, ["serve", "--project-dir", str(tmp_path)])
        assert result.exit_code == 1
        combined = result.output + (result.stderr or "")
        assert "ERR-INVALID-DEFAULT-THEME" in combined
        assert "Docs:" in combined

    def test_dbt_charts_yml_theme_key_rejected_with_validation_error(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        """dbt_charts.yml: theme: is no longer a valid key — raises pydantic validation error.

        `theme:` must live in charts/meta.yml: extends: <name>, not dbt_charts.yml.
        Serve exits 1 with a ValidationError message.
        """
        monkeypatch.delenv("DCT_DEFAULT_THEME", raising=False)
        (tmp_path / "dbt_charts.yml").write_text("theme: paper\n", encoding="utf-8")
        result = runner.invoke(app, ["serve", "--project-dir", str(tmp_path)])
        assert result.exit_code == 1
        combined = result.output + (result.stderr or "")
        assert "validation error" in combined.lower()

    def test_uvicorn_catch_all_renders_structured_code(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        """uvicorn Server.run raising OSError → exit 1, ERR-STARTUP-FAILED in stderr with detail."""
        (tmp_path / "dbt_charts.yml").write_text("# project marker\n")
        monkeypatch.delenv("DCT_DEFAULT_THEME", raising=False)
        with patch("uvicorn.Server.run") as mock_run:
            mock_run.side_effect = OSError("address already in use")
            result = runner.invoke(app, ["serve", "--project-dir", str(tmp_path)])
        assert result.exit_code == 1
        assert result.exception is None or isinstance(result.exception, SystemExit), (
            result.exception
        )
        combined = result.output + (result.stderr or "")
        assert "ERR-STARTUP-FAILED" in combined
        assert "address already in use" in combined
        assert "Docs:" in combined


class TestDftServeStartupFailure:
    """A server that never started must not report success."""

    def test_server_that_never_started_exits_3(self, tmp_path: Path) -> None:
        """A lifespan startup exception returns from run() without raising."""
        (tmp_path / "dbt_charts.yml").write_text("# project marker\n")
        with patch("uvicorn.Server.run"):
            result = runner.invoke(app, ["serve", "--project-dir", str(tmp_path)])
        assert result.exit_code == 3, result.output


class TestDftServeBanner:
    """The startup banner may only advertise URLs the server actually serves."""

    def test_advertised_urls_render(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        """Every URL in the banner must render, not merely route.

        Regression: the banner advertised /profile/model/ and
        /profile/numeric_column/ long after those routes were renamed to
        /inspect/*, so following them 404'd.

        Asserts 200 rather than "not 404" — the server renders some failures as
        an error page under a routed status, so "not 404" would pass a banner
        link that resolves to a stack trace.
        """
        monkeypatch.delenv("DCT_DEFAULT_THEME", raising=False)
        (tmp_path / "dbt_charts.yml").write_text("# project marker\n")
        (tmp_path / "charts").mkdir()
        (tmp_path / "charts" / "sales.yml").write_text("title: Sales\ntext: hi\n")

        with patch("uvicorn.Server.run", _run_started):
            result = runner.invoke(app, ["serve", "--project-dir", str(tmp_path)])
        assert result.exit_code == 0, result.output

        advertised = [
            match.group("path") or match.group("bare") or "/"
            for match in _BANNER_URL_RE.finditer(_ANSI_RE.sub("", result.output))
        ]
        assert advertised, f"banner printed no URLs: {result.output}"

        with TestClient(create_server(FilesystemProject(tmp_path))) as client:
            for url in advertised:
                assert client.get(url).status_code == 200, (
                    f"banner advertises {url}, which the server does not serve"
                )
