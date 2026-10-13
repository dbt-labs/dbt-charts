"""The invocation emitter: identity, payload, consent gating, CLI wiring."""

from __future__ import annotations

import hashlib
import os
import uuid
from pathlib import Path

import pytest
import typer.main
import yaml
from typer.core import TyperGroup
from typer.testing import CliRunner

from dbt_charts.cli.main import _option_path, app
from dbt_charts.telemetry import emitter
from dbt_charts.telemetry.emitter import (
    CliInvocation,
    begin_invocation,
    command_path,
    get_user_id,
    user_file_path,
)


@pytest.fixture(autouse=True)
def tracking_on(monkeypatch: pytest.MonkeyPatch) -> None:
    # The suite-wide conftest sets DO_NOT_TRACK; this package tests emission itself.
    monkeypatch.delenv("DO_NOT_TRACK")
    monkeypatch.delenv("DBT_SEND_ANONYMOUS_USAGE_STATS", raising=False)


@pytest.fixture
def sent(monkeypatch: pytest.MonkeyPatch) -> list[CliInvocation]:
    events: list[CliInvocation] = []
    monkeypatch.setattr(emitter, "send", events.append)
    return events


@pytest.fixture
def user_file(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    profiles_dir = tmp_path / "home" / ".dbt"
    monkeypatch.setenv("DBT_PROFILES_DIR", str(profiles_dir))
    return profiles_dir / ".user.yml"


def test_user_id_is_created_once(user_file: Path) -> None:
    first = get_user_id(user_file)
    assert str(uuid.UUID(first)) == first
    assert yaml.safe_load(user_file.read_text(encoding="utf-8")) == {"id": first}
    assert get_user_id(user_file) == first


def test_user_id_reads_existing_dbt_cookie(user_file: Path) -> None:
    user_file.parent.mkdir(parents=True)
    user_file.write_text("id: 11111111-1111-4111-8111-111111111111\n", encoding="utf-8")
    assert get_user_id(user_file) == "11111111-1111-4111-8111-111111111111"


def test_user_id_regenerates_when_file_is_empty(user_file: Path) -> None:
    user_file.parent.mkdir(parents=True)
    user_file.write_text("", encoding="utf-8")
    new = get_user_id(user_file)
    assert str(uuid.UUID(new)) == new
    assert get_user_id(user_file) == new


def test_user_id_malformed_file_raises(user_file: Path) -> None:
    user_file.parent.mkdir(parents=True)
    user_file.write_text("- not\n- a mapping\n", encoding="utf-8")
    with pytest.raises(ValueError, match=str(user_file)):
        get_user_id(user_file)


def test_user_file_follows_dbt_profiles_dir(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("DBT_PROFILES_DIR", str(tmp_path / "p"))
    assert user_file_path(os.environ) == tmp_path / "p" / ".user.yml"
    monkeypatch.delenv("DBT_PROFILES_DIR")
    assert user_file_path(os.environ) == Path.home() / ".dbt" / ".user.yml"


def test_first_run_notice_prints_once_on_creation(
    tmp_path: Path,
    sent: list[CliInvocation],
    user_file: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    begin_invocation("render", no_send_flag=False, project_dir=tmp_path)
    notice = capsys.readouterr().err
    assert "sending anonymous usage stats to dbt Labs" in notice
    assert "DO_NOT_TRACK=1" in notice
    assert "--no-send-anonymous-usage-stats" in notice
    assert "/reference/telemetry" in notice
    begin_invocation("render", no_send_flag=False, project_dir=tmp_path)
    assert capsys.readouterr().err == ""


def test_no_notice_and_no_file_when_consent_is_off(
    tmp_path: Path,
    sent: list[CliInvocation],
    user_file: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    begin_invocation("render", no_send_flag=True, project_dir=tmp_path)
    assert capsys.readouterr().err == ""
    assert not user_file.parent.exists()


def test_start_and_end_payloads(
    tmp_path: Path, sent: list[CliInvocation], user_file: Path
) -> None:
    (tmp_path / "dbt_project.yml").write_text(
        "name: my_secret_project\n", encoding="utf-8"
    )
    invocation = begin_invocation(
        "cloud connect", no_send_flag=False, project_dir=tmp_path
    )
    assert invocation is not None
    invocation.end(None)

    start, end = sent
    assert (start.progress, end.progress) == ("start", "end")
    assert start.command == end.command == "cloud connect"
    assert start.invocation_id == end.invocation_id
    assert start.event_id != end.event_id
    assert start.project_id == hashlib.md5(b"my_secret_project").hexdigest()
    assert start.dbt_local_cookie_user_id == get_user_id(user_file)
    assert (start.result_type, start.duration_ms) == (None, None)
    assert end.result_type == "ok"
    assert end.duration_ms is not None and end.duration_ms >= 0
    assert "my_secret_project" not in repr(sent)


def test_no_dbt_project_means_no_project_id(
    tmp_path: Path, sent: list[CliInvocation], user_file: Path
) -> None:
    begin_invocation("render", no_send_flag=False, project_dir=tmp_path)
    assert sent[0].project_id is None


@pytest.mark.parametrize(
    ("exc", "result"),
    [
        (None, "ok"),
        (SystemExit(0), "ok"),
        (SystemExit(None), "ok"),
        (SystemExit(2), "error"),
        (RuntimeError("boom"), "error"),
    ],
)
def test_result_type(
    tmp_path: Path,
    sent: list[CliInvocation],
    user_file: Path,
    exc: BaseException | None,
    result: str,
) -> None:
    invocation = begin_invocation("render", no_send_flag=False, project_dir=tmp_path)
    assert invocation is not None
    invocation.end(exc)
    assert sent[-1].result_type == result


def test_do_not_track_emits_nothing_and_creates_no_identity(
    tmp_path: Path,
    sent: list[CliInvocation],
    user_file: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("DO_NOT_TRACK", "1")
    assert begin_invocation("render", no_send_flag=False, project_dir=tmp_path) is None
    assert sent == []
    assert not user_file.exists()


def test_no_send_flag_emits_nothing(
    tmp_path: Path, sent: list[CliInvocation], user_file: Path
) -> None:
    assert begin_invocation("render", no_send_flag=True, project_dir=tmp_path) is None
    assert sent == []


def test_command_path_walks_groups_and_never_includes_args() -> None:
    root = typer.main.get_command(app)
    assert isinstance(root, TyperGroup)
    assert command_path(root, ["cloud", "--org", "acme", "login"]) == "cloud login"
    assert command_path(root, ["render", "secret/board.yml"]) == "render"
    assert command_path(root, []) == ""


class TestCliWiring:
    """Real CLI invocations through the root callback."""

    def test_emits_start_and_end_with_no_credentials(
        self,
        sent: list[CliInvocation],
        user_file: Path,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        monkeypatch.chdir(tmp_path)
        monkeypatch.setenv("DCT_NO_WORKSPACE_GUARD", "1")
        result = CliRunner().invoke(app, ["docs"])
        assert result.exit_code == 0
        assert [e.progress for e in sent] == ["start", "end"]
        assert sent[0].command == "docs"
        assert sent[1].result_type == "ok"

    def test_failing_command_reports_error(
        self,
        sent: list[CliInvocation],
        user_file: Path,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        monkeypatch.chdir(tmp_path)
        result = CliRunner().invoke(app, ["docs", "--no-such-option"])
        assert result.exit_code != 0
        assert sent[-1].progress == "end"
        assert sent[-1].result_type == "error"

    def test_opt_out_flag(
        self,
        sent: list[CliInvocation],
        user_file: Path,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        monkeypatch.chdir(tmp_path)
        result = CliRunner().invoke(app, ["--no-send-anonymous-usage-stats", "docs"])
        assert result.exit_code == 0
        assert sent == []

    @pytest.mark.parametrize("failing", ["start", "end"])
    def test_emitter_failure_leaves_exit_code_untouched(
        self,
        failing: str,
        user_file: Path,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        def boom(event: CliInvocation) -> None:
            if event.progress == failing:
                raise RuntimeError("vortex down")

        monkeypatch.chdir(tmp_path)
        monkeypatch.setattr(emitter, "send", boom)
        result = CliRunner().invoke(app, ["docs"])
        assert result.exit_code == 0


class TestOptionPath:
    """The root callback reads the project-dir options as click will."""

    def test_last_occurrence_wins(self, tmp_path: Path) -> None:
        args = ["--project-dir", "a", "--project-dir=b", "--project-dir", "c"]
        assert _option_path(args, "--project-dir", "X") == Path("c").resolve()

    def test_stops_at_the_double_dash_terminator(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.delenv("X", raising=False)
        args = ["--project-dir", "a", "--", "--project-dir", "b"]
        assert _option_path(args, "--project-dir", "X") == Path("a").resolve()

    def test_flag_beats_env(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("X", "from_env")
        assert (
            _option_path(["--project-dir", "a"], "--project-dir", "X")
            == Path("a").resolve()
        )
        assert _option_path([], "--project-dir", "X") == Path("from_env").resolve()


class TestTargetedProject:
    """Consent and project_id come from the targeted project, not cwd."""

    @staticmethod
    def _project(root: Path, *, send: bool) -> Path:
        root.mkdir()
        (root / "dbt_project.yml").write_text(
            f"name: demo\nflags:\n  send_anonymous_usage_stats: {str(send).lower()}\n",
            encoding="utf-8",
        )
        return root

    @pytest.mark.parametrize("option", ["--project-dir", "--dbt-project-dir"])
    def test_option_target_opts_out(
        self,
        option: str,
        sent: list[CliInvocation],
        user_file: Path,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        target = self._project(tmp_path / "target", send=False)
        elsewhere = tmp_path / "elsewhere"
        elsewhere.mkdir()
        monkeypatch.chdir(elsewhere)
        result = CliRunner().invoke(app, ["render", option, str(target)])
        assert result.exit_code == 2
        assert sent == []

    @pytest.mark.parametrize(
        ("env", "option"),
        [
            ("DCT_PROJECT_DIR", "--project-dir"),
            ("DBT_PROJECT_DIR", "--dbt-project-dir"),
        ],
    )
    def test_env_and_equals_forms_target_the_project(
        self,
        env: str,
        option: str,
        sent: list[CliInvocation],
        user_file: Path,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        target = self._project(tmp_path / "target", send=False)
        elsewhere = tmp_path / "elsewhere"
        elsewhere.mkdir()
        monkeypatch.chdir(elsewhere)
        monkeypatch.setenv(env, str(target))
        assert CliRunner().invoke(app, ["render"]).exit_code == 2
        assert sent == []
        monkeypatch.delenv(env)
        result = CliRunner().invoke(app, ["render", f"{option}={target}"])
        assert result.exit_code == 2
        assert sent == []

    def test_targeted_project_supplies_project_id(
        self,
        sent: list[CliInvocation],
        user_file: Path,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        target = self._project(tmp_path / "target", send=True)
        monkeypatch.chdir(tmp_path)
        CliRunner().invoke(app, ["render", "--project-dir", str(target)])
        assert sent[0].project_id == hashlib.md5(b"demo").hexdigest()

    def test_invalid_consent_warns_on_stderr_and_sends_nothing(
        self,
        sent: list[CliInvocation],
        user_file: Path,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        monkeypatch.chdir(tmp_path)
        monkeypatch.setenv("DBT_SEND_ANONYMOUS_USAGE_STATS", "maybe")
        result = CliRunner().invoke(app, ["docs"])
        assert result.exit_code == 0
        assert "DBT_SEND_ANONYMOUS_USAGE_STATS" in result.stderr
        assert sent == []
