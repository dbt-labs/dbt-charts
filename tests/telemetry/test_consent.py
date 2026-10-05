"""Consent precedence: DO_NOT_TRACK > flag > env > dbt_project.yml > default."""

from __future__ import annotations

from pathlib import Path

import pytest

from dbt_charts.telemetry.consent import send_anonymous_usage_stats


def _project(tmp_path: Path, flags: str = "") -> Path:
    (tmp_path / "dbt_project.yml").write_text(f"name: demo\n{flags}", encoding="utf-8")
    return tmp_path


def _enabled(project_dir: Path, *, flag: bool = False, **env: str) -> bool:
    return send_anonymous_usage_stats(
        no_send_flag=flag, env=env, project_dir=project_dir
    )


def test_default_is_on_without_a_project(tmp_path: Path) -> None:
    assert _enabled(tmp_path) is True


def test_do_not_track_beats_everything(tmp_path: Path) -> None:
    project = _project(tmp_path, "flags:\n  send_anonymous_usage_stats: true\n")
    assert (
        _enabled(project, DO_NOT_TRACK="1", DBT_SEND_ANONYMOUS_USAGE_STATS="true")
        is False
    )


def test_flag_beats_env_and_project(tmp_path: Path) -> None:
    project = _project(tmp_path, "flags:\n  send_anonymous_usage_stats: true\n")
    assert _enabled(project, flag=True, DBT_SEND_ANONYMOUS_USAGE_STATS="true") is False


def test_env_beats_project(tmp_path: Path) -> None:
    on = _project(tmp_path, "flags:\n  send_anonymous_usage_stats: true\n")
    assert _enabled(on, DBT_SEND_ANONYMOUS_USAGE_STATS="false") is False
    off = _project(tmp_path, "flags:\n  send_anonymous_usage_stats: false\n")
    assert _enabled(off, DBT_SEND_ANONYMOUS_USAGE_STATS="true") is True


def test_project_flag_beats_default(tmp_path: Path) -> None:
    off = _project(tmp_path, "flags:\n  send_anonymous_usage_stats: false\n")
    assert _enabled(off) is False


def test_project_without_flag_defaults_on(tmp_path: Path) -> None:
    assert _enabled(_project(tmp_path)) is True


@pytest.mark.parametrize("value", ["0", "", "true"])
def test_do_not_track_only_honors_one(tmp_path: Path, value: str) -> None:
    assert _enabled(tmp_path, DO_NOT_TRACK=value) is True


def test_invalid_env_value_raises(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="DBT_SEND_ANONYMOUS_USAGE_STATS"):
        _enabled(tmp_path, DBT_SEND_ANONYMOUS_USAGE_STATS="maybe")


def test_non_boolean_project_flag_raises(tmp_path: Path) -> None:
    project = _project(tmp_path, "flags:\n  send_anonymous_usage_stats: maybe\n")
    with pytest.raises(ValueError, match="send_anonymous_usage_stats"):
        _enabled(project)
