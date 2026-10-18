"""`dct serve --detach` serves in the background and checks every board."""

from __future__ import annotations

import json
import os
import re
import socket
import threading
import urllib.error
import urllib.request
from collections.abc import Iterator
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from typing import Any

import pytest
from typer.testing import CliRunner

from dbt_charts.cli.main import app

pytestmark = pytest.mark.windows

runner = CliRunner()

_GOOD = """\
title: Good
queries:
  q:
    type: values
    rows:
      - v: 1
charts:
  c:
    query: q
    type: kpi
    value: v
rows:
  - c
"""
_BAD = _GOOD.replace("title: Good", "title: Bad").replace(
    "value: v", "value: missing_column"
)


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("localhost", 0))
        return int(s.getsockname()[1])


def _pid(output: str) -> int:
    match = re.search(r"pid (\d+)", output)
    assert match, output
    return int(match.group(1))


def _status(url: str) -> int:
    try:
        return urllib.request.urlopen(url, timeout=60).status
    except urllib.error.HTTPError as exc:
        return exc.code


def _answers(port: int) -> bool:
    try:
        urllib.request.urlopen(f"http://localhost:{port}/health", timeout=2)
    except OSError:
        return False
    return True


@pytest.fixture
def project(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    (tmp_path / "dbt_charts.yml").write_text("{}\n")
    (tmp_path / "charts").mkdir()
    (tmp_path / "charts" / "good.yml").write_text(_GOOD)
    monkeypatch.chdir(tmp_path)
    return tmp_path


@pytest.fixture
def port() -> Iterator[int]:
    number = _free_port()
    yield number
    runner.invoke(app, ["serve", "--stop", "--port", str(number)])


@pytest.fixture
def started() -> Iterator[list[str]]:
    """Outputs of detach runs without --port; their servers are stopped after."""
    outputs: list[str] = []
    yield outputs
    for number in {n for out in outputs for n in re.findall(r"localhost:(\d+)/", out)}:
        runner.invoke(app, ["serve", "--stop", "--port", number])


def test_clean_board_reports_ok_and_stop_ends_the_server(
    project: Path, port: int
) -> None:
    result = runner.invoke(app, ["serve", "--detach", "--port", str(port)])

    assert result.exit_code == 0, result.output
    assert f"http://localhost:{port}/good/  ok" in result.output
    assert f"dct serve --stop --port {port}" in result.output
    assert _status(f"http://localhost:{port}/good/") == 200

    stopped = runner.invoke(app, ["serve", "--stop", "--port", str(port)])

    assert stopped.exit_code == 0, stopped.output
    assert f"pid {_pid(result.output)}" in stopped.output
    assert not _answers(port)


def test_broken_chart_is_listed_with_its_error_and_serves_500(
    project: Path, port: int
) -> None:
    (project / "charts" / "bad.yml").write_text(_BAD)

    result = runner.invoke(app, ["serve", "--detach", "--port", str(port)])

    assert result.exit_code == 1, result.output
    assert f"http://localhost:{port}/good/  ok" in result.output
    bad_line = next(
        line for line in result.output.splitlines() if f"{port}/bad/" in line
    )
    assert "1 error" in bad_line
    assert "ERR-" in result.output
    assert "the server is up" in result.output.lower()
    assert _status(f"http://localhost:{port}/bad/") == 500


def test_a_board_that_is_not_valid_yaml_is_checked_too(
    project: Path, port: int
) -> None:
    (project / "charts" / "typo.yml").write_text(
        _GOOD.replace("title: Good", "title: Mix: 2006 vs 2015")
    )

    result = runner.invoke(app, ["serve", "--detach", "--port", str(port)])

    assert result.exit_code == 1, result.output
    typo_line = next(
        line for line in result.output.splitlines() if f"{port}/typo/" in line
    )
    assert "1 error" in typo_line


def test_describe_flag_prints_the_chart_descriptions(project: Path, port: int) -> None:
    quiet = runner.invoke(app, ["serve", "--detach", "--port", str(port)])
    described = runner.invoke(
        app, ["serve", "--detach", "--describe", "--port", str(port)]
    )

    assert "(kpi)" not in quiet.output
    assert "(kpi)" in described.output


def test_second_detach_reuses_the_running_server(project: Path, port: int) -> None:
    first = runner.invoke(app, ["serve", "--detach", "--port", str(port)])
    second = runner.invoke(app, ["serve", "--detach", "--port", str(port)])

    assert second.exit_code == 0, second.output
    assert "reusing" in second.output
    assert _pid(second.output) == _pid(first.output)


def test_port_held_by_another_process_is_a_clear_error(
    project: Path, port: int
) -> None:
    with socket.socket() as blocker:
        blocker.bind(("localhost", port))
        blocker.listen()

        result = runner.invoke(app, ["serve", "--detach", "--port", str(port)])

    assert result.exit_code == 1
    assert f"port {port} is in use" in result.output.lower()


def test_stop_without_a_server_is_an_error(project: Path, port: int) -> None:
    result = runner.invoke(app, ["serve", "--stop", "--port", str(port)])

    assert result.exit_code == 1
    assert "no detached server" in result.output.lower()


def test_detach_replaces_another_projects_detached_server(
    project: Path, port: int, monkeypatch: pytest.MonkeyPatch
) -> None:
    first = runner.invoke(app, ["serve", "--detach", "--port", str(port)])
    other = project / "other_project"
    (other / "charts").mkdir(parents=True)
    (other / "dbt_charts.yml").write_text("{}\n")
    (other / "charts" / "second.yml").write_text(_GOOD)
    monkeypatch.chdir(other)

    second = runner.invoke(app, ["serve", "--detach", "--port", str(port)])

    assert second.exit_code == 0, second.output
    assert f"Replaced the server for {project.resolve()}" in second.output
    assert _pid(second.output) != _pid(first.output)
    assert _status(f"http://localhost:{port}/second/") == 200
    assert _status(f"http://localhost:{port}/good/") == 404


class _OtherServer(BaseHTTPRequestHandler):
    """Answers /health like a dbt Charts server whose pid is not the recorded one."""

    def do_GET(self) -> None:
        body = json.dumps(
            {"service": "dbt-charts-server", "pid": os.getpid() + 1, "project": "/x"}
        ).encode()
        self.send_response(200)
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, format: str, *args: Any) -> None:
        pass


def test_state_naming_a_pid_another_server_now_has_never_kills_it(
    project: Path, port: int
) -> None:
    from dbt_charts.agent_api.serve import _state_path

    _state_path(port).write_text(
        json.dumps(
            {
                "pid": os.getpid(),
                "host": "localhost",
                "settings": {
                    "dialect": "duckdb",
                    "target": None,
                    "max_workers": None,
                    "no_cache": False,
                    "cache_path": None,
                },
            }
        ),
        encoding="utf-8",
    )
    other = HTTPServer(("localhost", port), _OtherServer)
    threading.Thread(target=other.serve_forever, daemon=True).start()
    try:
        result = runner.invoke(app, ["serve", "--detach", "--port", str(port)])
        still_answers = _answers(port)
    finally:
        other.shutdown()
        other.server_close()

    assert result.exit_code == 1
    assert f"port {port} is in use" in result.output.lower()
    assert still_answers


def test_detach_without_port_reuses_the_projects_own_server(
    project: Path, started: list[str]
) -> None:
    first = runner.invoke(app, ["serve", "--detach"])
    started.append(first.output)
    second = runner.invoke(app, ["serve", "--detach"])
    started.append(second.output)

    assert second.exit_code == 0, second.output
    assert "reusing" in second.output
    assert _pid(second.output) == _pid(first.output)


def test_changed_server_options_replace_the_running_server(
    project: Path, port: int
) -> None:
    first = runner.invoke(app, ["serve", "--detach", "--port", str(port)])
    second = runner.invoke(
        app, ["serve", "--detach", "--no-cache", "--port", str(port)]
    )

    assert second.exit_code == 0, second.output
    assert "Replaced" in second.output
    assert _pid(second.output) != _pid(first.output)


def test_unreadable_state_file_is_a_clear_error(project: Path, port: int) -> None:
    from dbt_charts.agent_api.serve import _state_path

    _state_path(port).write_text("not json", encoding="utf-8")

    result = runner.invoke(app, ["serve", "--detach", "--port", str(port)])

    assert result.exit_code == 1
    assert str(_state_path(port)) in result.output
    assert "Traceback" not in result.output


def test_changed_dbt_target_env_replaces_the_running_server(
    project: Path, port: int, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("DBT_TARGET", raising=False)
    first = runner.invoke(app, ["serve", "--detach", "--port", str(port)])
    monkeypatch.setenv("DBT_TARGET", "prod")

    second = runner.invoke(app, ["serve", "--detach", "--port", str(port)])

    assert second.exit_code == 0, second.output
    assert "Replaced" in second.output
    assert _pid(second.output) != _pid(first.output)


@pytest.mark.parametrize(
    ("bind", "probe"),
    [
        ("0.0.0.0", "127.0.0.1"),  # noqa: S104
        ("", "127.0.0.1"),
        ("::", "::1"),
        ("localhost", "localhost"),
    ],
)
def test_health_probe_address_for_a_bind_host(bind: str, probe: str) -> None:
    from dbt_charts.agent_api.serve import _probe_host

    assert _probe_host(bind) == probe


def test_check_and_server_get_their_registry_from_the_same_settings(
    project: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from fastapi.testclient import TestClient

    from dbt_charts.agent_api import Diagnostic
    from dbt_charts.agent_api.serve import _check_boards, prepare_serve
    from dbt_charts.cli.filesystem_project import FilesystemProject
    from dbt_charts.core.serve import server as server_mod

    seen: list[dict[str, Any]] = []
    real = server_mod.build_adapter_registry

    def spy(project: Any, **kwargs: Any) -> Any:
        seen.append(kwargs)
        return real(project, **kwargs)

    monkeypatch.setattr(server_mod, "build_adapter_registry", spy)
    monkeypatch.setenv("DBT_TARGET", "prod")
    fs_project = FilesystemProject(project)

    _check_boards(fs_project, {}, settings=_settings(fs_project), describe=False)
    setup = prepare_serve(
        fs_project,
        port=_free_port(),
        host="localhost",
        dialect=None,
        target=None,
        max_workers=None,
        no_cache=False,
        cache_path=None,
    )
    assert not isinstance(setup, Diagnostic)
    with TestClient(setup.app):
        pass

    assert seen[0] == seen[-1]
    assert seen[0]["target"] == "prod"


def _settings(project: Any) -> Any:
    from dbt_charts.agent_api.serve import resolve_serve_settings

    settings, _ = resolve_serve_settings(
        project,
        dialect=None,
        target=None,
        max_workers=None,
        no_cache=False,
        cache_path=None,
    )
    return settings


def test_stop_returns_only_once_the_port_is_released(
    project: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The OS can hold the listener after the server stops answering /health."""
    from dbt_charts.agent_api import serve
    from dbt_charts.core.serve import port as port_mod

    number = _free_port()
    serve._state_path(number).write_text(
        json.dumps(
            {
                "pid": 4242,
                "host": "localhost",
                "settings": {
                    "dialect": "duckdb",
                    "target": None,
                    "max_workers": None,
                    "no_cache": False,
                    "cache_path": None,
                },
            }
        ),
        encoding="utf-8",
    )
    answering = iter([True])

    def recorded(*_: Any) -> dict[str, Any] | None:
        return {} if next(answering, False) else None

    def no_kill(*_: Any) -> None:
        return None

    monkeypatch.setattr(serve, "_recorded_server", recorded)
    monkeypatch.setattr(serve.os, "kill", no_kill)
    probes = iter([False, False, True])
    calls: list[int] = []

    def held_twice(*_: Any) -> bool:
        calls.append(1)
        return next(probes)

    monkeypatch.setattr(port_mod, "is_port_available", held_twice)

    assert serve.stop_detached(number) == 4242
    assert len(calls) == 3


def test_stop_reports_a_port_that_stays_bound(
    project: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from dbt_charts.agent_api import serve
    from dbt_charts.core.serve import port as port_mod

    number = _free_port()
    serve._state_path(number).write_text(
        json.dumps(
            {
                "pid": 4242,
                "host": "localhost",
                "settings": {
                    "dialect": "duckdb",
                    "target": None,
                    "max_workers": None,
                    "no_cache": False,
                    "cache_path": None,
                },
            }
        ),
        encoding="utf-8",
    )
    answering = iter([True])

    def recorded(*_: Any) -> dict[str, Any] | None:
        return {} if next(answering, False) else None

    def no_kill(*_: Any) -> None:
        return None

    def always_bound(*_: Any) -> bool:
        return False

    monkeypatch.setattr(serve, "_recorded_server", recorded)
    monkeypatch.setattr(serve.os, "kill", no_kill)
    monkeypatch.setattr(serve, "_PORT_RELEASE_TIMEOUT", 0.0)
    monkeypatch.setattr(port_mod, "is_port_available", always_bound)

    with pytest.raises(
        serve.DetachedServeError, match=f"port {number} is still in use"
    ):
        serve.stop_detached(number)
