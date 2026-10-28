"""Tests for the `dct init` sub-app and its sub-subcommands.

Covers:
- `dct init` (no subcommand) still scaffolds and now suggests mcp + extension.
- `dct init mcp [client]` writes the requested MCP client config files.
- `dct init cursor` / `dct init code` / `dct init vscode` are wired and
  surface clear errors when the editor binary or `gh` is missing.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from typer.testing import CliRunner

from dbt_charts.cli import main as cli_main
from dbt_charts.cli.commands import extension as extension_cmd

runner = CliRunner()
# Click 8.2 separates result.stdout / result.stderr by default — no mix_stderr
# kwarg is needed (and isn't accepted). Tests asserting stderr separation can
# just use the same runner.


# ---------------------------------------------------------------------------
# `dct init` (no subcommand) scaffolds + suggests mcp/extension
# ---------------------------------------------------------------------------


class TestInitNoSubcommandScaffolds:
    def test_runs_scaffold_and_lists_next_steps(self, tmp_path: Path) -> None:
        result = runner.invoke(
            cli_main.app,
            [
                "init",
                "--project-dir",
                str(tmp_path),
            ],
        )
        assert result.exit_code == 0, result.stdout
        assert (tmp_path / "charts").is_dir()
        # Success block always shows the primary next step
        assert "dct serve" in result.stdout

    def test_subcommand_passthrough_does_not_run_scaffold(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """When a subcommand IS invoked, the no-subcommand scaffold must NOT run.

        Validates the `if ctx.invoked_subcommand is not None: return` guard in
        `init_default`. `--help` is intercepted by Click before the callback,
        so we use a real subcommand (`init mcp print`, side-effect-free) to
        actually exercise the guard.
        """
        monkeypatch.chdir(tmp_path)
        (tmp_path / "dbt_charts.yml").write_text("name: test\n")
        result = runner.invoke(cli_main.app, ["init", "mcp", "print"])
        assert result.exit_code == 0, result.stdout
        # The scaffold writes charts/ — it must NOT have run.
        assert not (tmp_path / "charts").exists()


# ---------------------------------------------------------------------------
# `dct init mcp <client>` writes the requested client config
# ---------------------------------------------------------------------------


class TestInitMcpForwardsToSharedImpl:
    def test_dct_init_mcp_print_emits_config(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.chdir(tmp_path)
        (tmp_path / "dbt_charts.yml").write_text("name: test\n")
        result = runner.invoke(cli_main.app, ["init", "mcp", "print"])
        assert result.exit_code == 0
        # `print` mode dumps {"mcpServers": {"dbt-charts": ...}} to stdout
        payload = json.loads(result.stdout)
        assert "mcpServers" in payload
        assert "dbt-charts" in payload["mcpServers"]

    def test_dct_init_mcp_writes_config(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.chdir(tmp_path)
        (tmp_path / "dbt_charts.yml").write_text("name: test\n")
        result = runner.invoke(cli_main.app, ["init", "mcp", "vscode"])
        assert result.exit_code == 0, result.stdout
        config = tmp_path / ".vscode" / "mcp.json"
        assert config.exists()
        payload = json.loads(config.read_text())
        assert "dbt-charts" in payload["servers"]


# ---------------------------------------------------------------------------
# `dct init cursor` / `code` / `vscode` wiring
# ---------------------------------------------------------------------------


class TestExtensionSubcommandWiring:
    def test_unknown_editor_via_function(self) -> None:
        captured: list[str] = []
        rc = extension_cmd.install_extension("nano", emit=captured.append)
        assert rc == 2
        assert "Unknown editor" in captured[0]

    def test_missing_editor_binary(self, monkeypatch: pytest.MonkeyPatch) -> None:
        # Pretend cursor is not on PATH.
        monkeypatch.setattr(extension_cmd.shutil, "which", lambda _: None)
        captured: list[str] = []
        rc = extension_cmd.install_extension("cursor", emit=captured.append)
        assert rc == 1
        assert any("not found on PATH" in line for line in captured)

    def test_vscode_alias_resolves_to_code_binary(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """`dct init vscode` is a thin alias for `dct init code`."""
        seen: list[str] = []

        def fake_which(name: str) -> str | None:
            seen.append(name)
            return None  # Force early exit before subprocess call.

        monkeypatch.setattr(extension_cmd.shutil, "which", fake_which)
        captured: list[str] = []
        rc = extension_cmd.install_extension("vscode", emit=captured.append)
        assert rc == 1
        assert seen == ["code"]  # alias mapped to the code binary

    def test_help_lists_subcommands(self) -> None:
        result = runner.invoke(cli_main.app, ["init", "--help"])
        assert result.exit_code == 0
        for sub in ("cursor", "code", "vscode", "mcp"):
            assert sub in result.stdout
