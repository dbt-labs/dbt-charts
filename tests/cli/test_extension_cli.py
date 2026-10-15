"""Smoke tests for dct init code|cursor|vscode extension dispatch.

IDE-installer smoke depth: assert arg validation + dispatch only.
No real IDE binary, network call, or warehouse connection is reached.
"""

from __future__ import annotations

import subprocess
from pathlib import Path
from unittest.mock import MagicMock, patch

from typer.testing import CliRunner

from dbt_charts.cli.commands import extension
from dbt_charts.cli.main import app

runner = CliRunner()

_PATCH_TARGET = "dbt_charts.cli.commands.extension.install_extension"


def test_init_code_dispatches_to_install_extension() -> None:
    with patch(_PATCH_TARGET, return_value=0) as mock_install:
        result = runner.invoke(app, ["init", "code"], catch_exceptions=False)
    assert result.exit_code == 0
    mock_install.assert_called_once()
    editor_arg = mock_install.call_args[0][0]
    assert editor_arg == "code"


def test_init_cursor_dispatches_with_cursor_target() -> None:
    with patch(_PATCH_TARGET, return_value=0) as mock_install:
        result = runner.invoke(app, ["init", "cursor"], catch_exceptions=False)
    assert result.exit_code == 0
    mock_install.assert_called_once()
    editor_arg = mock_install.call_args[0][0]
    assert editor_arg == "cursor"


def test_init_vscode_aliases_to_code() -> None:
    # `dct init vscode` is an alias for `dct init code` (init_vscode in main.py).
    # It must dispatch with editor='code', not 'vscode'. Renaming the alias or
    # changing the dispatch target surfaces a loud failure here.
    with patch(_PATCH_TARGET, return_value=0) as mock_install:
        result = runner.invoke(app, ["init", "vscode"], catch_exceptions=False)
    assert result.exit_code == 0
    mock_install.assert_called_once()
    editor_arg = mock_install.call_args[0][0]
    assert editor_arg == "code", (
        "dct init vscode (init_vscode) must dispatch with editor='code', not 'vscode'"
    )


def test_install_extension_installs_by_marketplace_id(tmp_path: Path) -> None:
    """install_extension hands the editor `--install-extension dbtLabsInc.dbtcharts`."""
    fake_editor_bin = str(tmp_path / "code")
    Path(fake_editor_bin).write_text("", encoding="utf-8")

    install_calls: list[list[str]] = []

    def fake_run(cmd, **_kwargs):
        install_calls.append(list(cmd))
        return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")

    emitted: list[str] = []

    def _which(name: str) -> str | None:
        return fake_editor_bin if name == "code" else None

    with (
        patch("dbt_charts.cli.commands.extension.shutil.which", side_effect=_which),
        patch("dbt_charts.cli.commands.extension.subprocess.run", side_effect=fake_run),
    ):
        rc = extension.install_extension("code", emit=emitted.append)

    assert rc == 0, f"emitted={emitted}"
    assert install_calls == [
        [fake_editor_bin, "--install-extension", "dbtLabsInc.dbtcharts"]
    ]


def test_install_extension_cursor_installs_by_marketplace_id(tmp_path: Path) -> None:
    """The `cursor` branch installs the same marketplace ID as `code`."""
    fake_editor_bin = str(tmp_path / "cursor")
    Path(fake_editor_bin).write_text("", encoding="utf-8")

    def _which(name: str) -> str | None:
        return fake_editor_bin if name == "cursor" else None

    install_calls: list[list[str]] = []

    def fake_run(cmd, **_kwargs):
        install_calls.append(list(cmd))
        return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")

    with (
        patch("dbt_charts.cli.commands.extension.shutil.which", side_effect=_which),
        patch("dbt_charts.cli.commands.extension.subprocess.run", side_effect=fake_run),
    ):
        rc = extension.install_extension("cursor", emit=MagicMock())

    assert rc == 0
    assert install_calls == [
        [fake_editor_bin, "--install-extension", "dbtLabsInc.dbtcharts"]
    ]
