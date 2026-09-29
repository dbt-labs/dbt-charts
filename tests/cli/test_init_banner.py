"""Guard the init-reminder banner on `dct` root help.

The banner nudges users toward `dct init` when they invoke `dct`,
`dct -h`, or `dct --help` in a directory that hasn't been scaffolded.
"charts/" (created by `dct init`) is the canonical "this directory is a
dbt charts project" marker — `dbt_charts.yml` is optional and several
canonical example projects don't have one.
"""

from __future__ import annotations

import io
import re
from pathlib import Path
from unittest.mock import patch

import pytest
from rich.console import Console
from typer.testing import CliRunner

from dbt_charts.cli.main import _render_init_banner, app

runner = CliRunner()

BANNER_PHRASE = "Welcome to dbt charts"

_ANSI = re.compile(r"\x1b\[[0-9;]*m")
_BOX_CHARS = re.compile(r"[╭╮╰╯│─]")


def _strip_ansi(text: str) -> str:
    return _ANSI.sub("", text)


def _render_banner_to_string(*, plain: bool) -> str:
    buf = io.StringIO()
    fake = Console(file=buf, force_terminal=not plain, no_color=plain, width=100)
    with (
        patch("dbt_charts.cli.main.is_plain_output", return_value=plain),
        patch("dbt_charts.cli.main.Console", return_value=fake),
    ):
        _render_init_banner()
    return buf.getvalue()


def test_init_banner_plain_mode_has_no_panel_chrome() -> None:
    """In plain mode the banner renders as text with no Panel borders."""
    output = _render_banner_to_string(plain=True)
    assert BANNER_PHRASE in output
    assert "dct init" in output
    assert not _BOX_CHARS.search(output), f"Box chars in plain banner:\n{output!r}"


def test_init_banner_rich_mode_keeps_panel_chrome() -> None:
    """In rich mode the banner still renders the Welcome Panel."""
    output = _render_banner_to_string(plain=False)
    assert BANNER_PHRASE in output
    assert _BOX_CHARS.search(output), (
        f"Expected Panel chrome in rich banner:\n{output!r}"
    )


def test_banner_shown_on_root_help_when_no_boards_dir(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.chdir(tmp_path)
    result = runner.invoke(app, ["--help"])
    assert result.exit_code == 0, result.output
    output = _strip_ansi(result.output)
    assert BANNER_PHRASE in output
    assert "dct init" in output


def test_banner_names_skills_intro_as_where_to_start(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.chdir(tmp_path)
    result = runner.invoke(app, ["--help"])
    assert result.exit_code == 0, result.output
    assert "dct skills intro" in _strip_ansi(result.output)


def test_banner_hidden_when_boards_dir_in_cwd(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    (tmp_path / "charts").mkdir()
    monkeypatch.chdir(tmp_path)
    result = runner.invoke(app, ["--help"])
    assert result.exit_code == 0, result.output
    assert BANNER_PHRASE not in _strip_ansi(result.output)


def test_banner_hidden_from_subdir_of_scaffolded_project(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    (tmp_path / "charts").mkdir()
    (tmp_path / "dbt_charts.yml").write_text("name: demo\n")
    nested = tmp_path / "sub" / "deeper"
    nested.mkdir(parents=True)
    monkeypatch.chdir(nested)
    result = runner.invoke(app, ["--help"])
    assert result.exit_code == 0, result.output
    assert BANNER_PHRASE not in _strip_ansi(result.output)


def test_banner_shown_in_bare_dbt_repo_without_boards(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    (tmp_path / "dbt_project.yml").write_text("name: demo\n")
    monkeypatch.chdir(tmp_path)
    result = runner.invoke(app, ["--help"])
    assert BANNER_PHRASE in _strip_ansi(result.output)


@pytest.mark.parametrize("args", [[], ["-h"], ["--help"]])
def test_banner_shown_on_bare_short_and_long_help(
    args: list[str], tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.chdir(tmp_path)
    result = runner.invoke(app, args)
    assert BANNER_PHRASE in _strip_ansi(result.output)


@pytest.mark.parametrize("args", [["serve", "--help"], ["init", "--help"]])
def test_banner_absent_on_subcommand_help(
    args: list[str], tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.chdir(tmp_path)
    result = runner.invoke(app, args)
    assert result.exit_code == 0, result.output
    assert BANNER_PHRASE not in _strip_ansi(result.output)


def test_rich_banner_names_skills_intro_as_where_to_start() -> None:
    assert "dct skills intro" in _strip_ansi(_render_banner_to_string(plain=False))
