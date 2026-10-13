"""Tests for dbt_charts.agent_api.mcp_install typed install verbs."""

from __future__ import annotations

import json
import sys
import tomllib
from pathlib import Path

import pytest

from dbt_charts.agent_api.mcp_install import (
    InstallResult,
    McpClient,
    McpConfigReadError,
    ResolvedDct,
    install_for_client,
    list_clients,
    resolve_dct_executable,
)
from dbt_charts.agent_api.skills import all_skill_names


def test_list_clients_returns_at_least_four() -> None:
    clients = list_clients()
    names = {c.name for c in clients}
    assert {"cursor", "claude-code", "codex", "vscode"}.issubset(names)


def test_list_clients_all_have_valid_formats() -> None:
    for client in list_clients():
        assert client.config_format in ("json", "toml")
        assert client.servers_key
        assert client.config_path


def test_all_skill_names_includes_packaged_workflows() -> None:
    names = all_skill_names()
    assert "board-build" in names
    assert "kpi-row" in names


def test_install_cursor_writes_valid_json(tmp_path: Path) -> None:
    client = McpClient(
        name="cursor",
        config_path=tmp_path / ".cursor/mcp.json",
        servers_key="mcpServers",
        detect_paths=(Path(".cursor"),),
        config_format="json",
        command_workspace_var="${workspaceFolder}",
    )
    result = install_for_client(
        client,
        dct_executable=ResolvedDct(command="dct"),
        server_args=["mcp", "serve"],
        ai_config_root=tmp_path,
    )

    assert isinstance(result, InstallResult)
    assert not result.already_configured
    assert (tmp_path / ".cursor/mcp.json").exists()
    config = json.loads((tmp_path / ".cursor/mcp.json").read_text())
    assert config["mcpServers"]["dbt-charts"]["command"] == "dct"
    assert config["mcpServers"]["dbt-charts"]["args"] == ["mcp", "serve"]


def test_install_codex_writes_valid_toml(tmp_path: Path) -> None:
    client = McpClient(
        name="codex",
        config_path=tmp_path / ".codex/config.toml",
        servers_key="mcp_servers",
        detect_paths=(Path(".codex"),),
        config_format="toml",
        command_workspace_var=None,
    )
    result = install_for_client(
        client,
        dct_executable=ResolvedDct(command="dct"),
        server_args=["mcp", "serve"],
        ai_config_root=tmp_path,
    )

    assert isinstance(result, InstallResult)
    assert not result.already_configured
    config = tomllib.loads((tmp_path / ".codex/config.toml").read_text())
    assert config["mcp_servers"]["dbt-charts"]["args"] == ["mcp", "serve"]


def test_install_idempotent_without_force(tmp_path: Path) -> None:
    client = McpClient(
        name="cursor",
        config_path=tmp_path / ".cursor/mcp.json",
        servers_key="mcpServers",
        detect_paths=(Path(".cursor"),),
        config_format="json",
        command_workspace_var="${workspaceFolder}",
    )
    first = install_for_client(
        client,
        dct_executable=ResolvedDct(command="dct"),
        server_args=["mcp", "serve"],
        ai_config_root=tmp_path,
    )
    assert not first.already_configured

    second = install_for_client(
        client,
        dct_executable=ResolvedDct(command="dct"),
        server_args=["mcp", "serve"],
        ai_config_root=tmp_path,
    )
    assert second.already_configured
    # File content unchanged
    assert (tmp_path / ".cursor/mcp.json").read_text() == json.dumps(
        {"mcpServers": {"dbt-charts": {"command": "dct", "args": ["mcp", "serve"]}}},
        indent=2,
    ) + "\n"


def test_install_force_updates_existing(tmp_path: Path) -> None:
    client = McpClient(
        name="cursor",
        config_path=tmp_path / ".cursor/mcp.json",
        servers_key="mcpServers",
        detect_paths=(Path(".cursor"),),
        config_format="json",
        command_workspace_var="${workspaceFolder}",
    )
    (tmp_path / ".cursor").mkdir()
    (tmp_path / ".cursor/mcp.json").write_text(
        json.dumps(
            {"mcpServers": {"dbt-charts": {"command": "/old/dct", "args": ["old"]}}}
        )
    )

    result = install_for_client(
        client,
        dct_executable=ResolvedDct(command="dct"),
        server_args=["mcp", "serve"],
        ai_config_root=tmp_path,
        force=True,
    )

    assert result.updated
    config = json.loads((tmp_path / ".cursor/mcp.json").read_text())
    assert config["mcpServers"]["dbt-charts"]["command"] == "dct"
    assert config["mcpServers"]["dbt-charts"]["args"] == ["mcp", "serve"]


def test_install_fresh_add_sets_updated_false(tmp_path: Path) -> None:
    client = McpClient(
        name="cursor",
        config_path=tmp_path / ".cursor/mcp.json",
        servers_key="mcpServers",
        detect_paths=(Path(".cursor"),),
        config_format="json",
        command_workspace_var="${workspaceFolder}",
    )
    result = install_for_client(
        client,
        dct_executable=ResolvedDct(command="dct"),
        server_args=["mcp", "serve"],
        ai_config_root=tmp_path,
    )
    assert not result.already_configured
    assert not result.updated


def test_install_corrupt_json_config_errors_without_overwriting(tmp_path: Path) -> None:
    client = McpClient(
        name="cursor",
        config_path=tmp_path / ".cursor/mcp.json",
        servers_key="mcpServers",
        detect_paths=(Path(".cursor"),),
        config_format="json",
        command_workspace_var="${workspaceFolder}",
    )
    config_path = tmp_path / ".cursor/mcp.json"
    config_path.parent.mkdir()
    corrupt = "{not valid json"
    config_path.write_text(corrupt)

    with pytest.raises(McpConfigReadError, match=str(config_path)):
        install_for_client(
            client,
            dct_executable=ResolvedDct(command="dct"),
            server_args=["mcp", "serve"],
            ai_config_root=tmp_path,
        )

    assert config_path.read_text() == corrupt


def test_install_non_dict_json_config_errors_without_overwriting(
    tmp_path: Path,
) -> None:
    client = McpClient(
        name="cursor",
        config_path=tmp_path / ".cursor/mcp.json",
        servers_key="mcpServers",
        detect_paths=(Path(".cursor"),),
        config_format="json",
        command_workspace_var="${workspaceFolder}",
    )
    config_path = tmp_path / ".cursor/mcp.json"
    config_path.parent.mkdir()
    corrupt = json.dumps(["not", "an", "object"])
    config_path.write_text(corrupt)

    with pytest.raises(McpConfigReadError, match=str(config_path)):
        install_for_client(
            client,
            dct_executable=ResolvedDct(command="dct"),
            server_args=["mcp", "serve"],
            ai_config_root=tmp_path,
        )

    assert config_path.read_text() == corrupt


def test_install_preserves_other_servers_in_existing_json_config(
    tmp_path: Path,
) -> None:
    client = McpClient(
        name="cursor",
        config_path=tmp_path / ".cursor/mcp.json",
        servers_key="mcpServers",
        detect_paths=(Path(".cursor"),),
        config_format="json",
        command_workspace_var="${workspaceFolder}",
    )
    config_path = tmp_path / ".cursor/mcp.json"
    config_path.parent.mkdir()
    config_path.write_text(
        json.dumps({"mcpServers": {"other-server": {"command": "other"}}})
    )

    install_for_client(
        client,
        dct_executable=ResolvedDct(command="dct"),
        server_args=["mcp", "serve"],
        ai_config_root=tmp_path,
    )

    config = json.loads(config_path.read_text())
    assert config["mcpServers"]["other-server"] == {"command": "other"}
    assert config["mcpServers"]["dbt-charts"]["command"] == "dct"


def test_install_resolves_relative_path_against_ai_config_root(tmp_path: Path) -> None:
    client_from_list = next(c for c in list_clients() if c.name == "cursor")
    result = install_for_client(
        client_from_list,
        dct_executable=ResolvedDct(command="dct"),
        server_args=["mcp", "serve"],
        ai_config_root=tmp_path,
    )
    assert result.config_path == tmp_path / ".cursor/mcp.json"
    assert (tmp_path / ".cursor/mcp.json").exists()


def _venv_dct(tmp_path: Path) -> ResolvedDct:
    """A ResolvedDct as `resolve_dct_executable` returns when a project venv exists."""
    venv_dct = tmp_path / ".venv" / "bin" / "dct"
    venv_dct.parent.mkdir(parents=True)
    venv_dct.write_text("#!/bin/sh\nexit 0\n")
    return ResolvedDct(command=str(venv_dct), project_relative=".venv/bin/dct")


def test_install_cursor_gets_workspace_relative_command(tmp_path: Path) -> None:
    """Cursor's own docs confirm `${workspaceFolder}` substitution inside `command`."""
    client = next(c for c in list_clients() if c.name == "cursor")
    install_for_client(
        client,
        dct_executable=_venv_dct(tmp_path),
        server_args=["mcp", "serve"],
        ai_config_root=tmp_path,
    )

    config = json.loads((tmp_path / ".cursor/mcp.json").read_text())
    entry = config["mcpServers"]["dbt-charts"]
    assert entry["command"] == "${workspaceFolder}/.venv/bin/dct"
    assert "cwd" not in entry


@pytest.mark.parametrize("client_name", ["vscode", "codex", "copilot", "claude-code"])
def test_install_absolute_only_client_keeps_absolute_command(
    tmp_path: Path, client_name: str
) -> None:
    """VS Code, Codex, Copilot, and Claude Code have no verified
    workspace-relative mechanism, so a detected project venv must still be
    written as an absolute command, not a relative/variable one. VS Code's own
    reference requires `command` to be on PATH or a full path — its `cwd` key
    already defaults to the workspace folder, so setting it doesn't rescue a
    relative `command`. Claude Code's `${CLAUDE_PROJECT_DIR}` does not expand
    in `.mcp.json` (it's only injected into hook invocations, not the session
    process), and its `cwd` config key is confirmed non-functional."""
    resolved = _venv_dct(tmp_path)
    client = next(c for c in list_clients() if c.name == client_name)
    result = install_for_client(
        client,
        dct_executable=resolved,
        server_args=["mcp", "serve"],
        ai_config_root=tmp_path,
    )

    if client.config_format == "toml":
        entry = tomllib.loads(result.config_path.read_text())[client.servers_key][
            "dbt-charts"
        ]
    else:
        entry = json.loads(result.config_path.read_text())[client.servers_key][
            "dbt-charts"
        ]
    assert entry["command"] == resolved.command
    assert "cwd" not in entry


def test_install_absolute_config_client_gets_absolute_command_when_venv_dct_passed(
    tmp_path: Path,
) -> None:
    """Claude Desktop's config is per-machine, user-home, never committed — it
    always gets an absolute command, even when a project venv is detected."""
    resolved = _venv_dct(tmp_path)

    claude_config = tmp_path / "home" / ".config" / "claude" / "config.json"
    client = McpClient(
        name="claude",
        config_path=claude_config,
        servers_key="mcpServers",
        detect_paths=(claude_config.parent,),
        config_format="json",
        command_workspace_var=None,
    )
    install_for_client(
        client,
        dct_executable=resolved,
        server_args=["mcp", "serve"],
        ai_config_root=tmp_path,
    )

    config = json.loads(claude_config.read_text())
    command = config["mcpServers"]["dbt-charts"]["command"]
    assert Path(command).is_absolute()
    assert command == resolved.command


# ---------------------------------------------------------------------------
# resolve_dct_executable — unit tests
#
# Regression coverage for the `spawn dct ENOENT` bug class. When `init mcp`
# resolves the binary to embed in MCP client configs via `shutil.which("dct")`
# alone, a non-activated venv invocation writes the literal string `"dct"`
# into `.cursor/mcp.json` etc., and Cursor (or any GUI MCP host) then fails
# to spawn the server with `spawn dct ENOENT`. The helper prefers
# `sys.argv[0]` when it points at a real `dct` binary so the bare name is
# the last resort, not the first.
# ---------------------------------------------------------------------------


def test_resolve_dct_executable_uses_sys_argv0_when_real_dct(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    fake_dct = tmp_path / "pyenv" / "bin" / "dct"
    fake_dct.parent.mkdir(parents=True)
    fake_dct.write_text("#!/bin/sh\nexit 0\n")
    fake_dct.chmod(0o755)
    monkeypatch.setattr(sys, "argv", [str(fake_dct), "init", "mcp"])

    def _which_none(_: str) -> None:
        return None

    monkeypatch.setattr("shutil.which", _which_none)

    # ai_config_root is a directory unrelated to fake_dct's location, so the
    # `.venv/bin/dct` project-venv probe structurally cannot fire here — the
    # sys.argv[0] branch is what's under test.
    ai_config_root = tmp_path / "elsewhere"
    ai_config_root.mkdir()
    result = resolve_dct_executable(ai_config_root)
    assert result.command == str(fake_dct)
    assert result.project_relative is None


def test_resolve_dct_executable_falls_through_when_argv0_is_not_dct(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    python_bin = tmp_path / "venv" / "bin" / "python"
    python_bin.parent.mkdir(parents=True)
    python_bin.write_text("#!/bin/sh\nexit 0\n")
    python_bin.chmod(0o755)
    real_dct = tmp_path / "homebrew" / "bin" / "dct"
    real_dct.parent.mkdir(parents=True)
    real_dct.write_text("#!/bin/sh\nexit 0\n")
    real_dct.chmod(0o755)

    monkeypatch.setattr(sys, "argv", [str(python_bin), "-m", "dbt_charts.cli"])

    def _which_dct(name: str) -> str | None:
        return str(real_dct) if name == "dct" else None

    monkeypatch.setattr("shutil.which", _which_dct)

    result = resolve_dct_executable(tmp_path)
    assert result.command == str(real_dct)
    assert result.project_relative is None


def test_resolve_dct_executable_falls_back_to_bare_name_when_nothing_resolves(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(sys, "argv", ["/some/random/script", "anything"])

    def _which_none(_: str) -> None:
        return None

    monkeypatch.setattr("shutil.which", _which_none)

    result = resolve_dct_executable(tmp_path)
    assert result.command == "dct"
    assert result.project_relative is None


def test_resolve_dct_executable_prefers_project_venv_when_present(
    tmp_path: Path,
) -> None:
    venv_dct = tmp_path / ".venv" / "bin" / "dct"
    venv_dct.parent.mkdir(parents=True)
    venv_dct.write_text("#!/bin/sh\nexit 0\n")
    venv_dct.chmod(0o755)

    result = resolve_dct_executable(tmp_path)

    assert result.command == str(venv_dct)
    assert result.project_relative == ".venv/bin/dct"


def test_install_corrupt_toml_config_errors_without_overwriting(
    tmp_path: Path,
) -> None:
    client = McpClient(
        name="codex",
        config_path=tmp_path / ".codex/config.toml",
        servers_key="mcp_servers",
        detect_paths=(Path(".codex"),),
        config_format="toml",
        command_workspace_var=None,
    )
    config_path = tmp_path / ".codex/config.toml"
    config_path.parent.mkdir()
    corrupt = "[not valid toml"
    config_path.write_text(corrupt)

    with pytest.raises(McpConfigReadError, match="config.toml"):
        install_for_client(
            client,
            dct_executable=ResolvedDct(command="dct"),
            server_args=["mcp", "serve"],
            ai_config_root=tmp_path,
        )

    assert config_path.read_text() == corrupt
