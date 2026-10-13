"""Typed MCP client install verbs — per-client config writers for agent surfaces."""

from __future__ import annotations

import json
import os
import shutil
import sys
import tomllib
from pathlib import Path
from typing import Any, Literal

import tomli_w
from pydantic import BaseModel, ConfigDict


class McpClient(BaseModel):
    name: str
    config_path: Path
    servers_key: str
    detect_paths: tuple[Path, ...]
    config_format: Literal["json", "toml"]
    command_workspace_var: str | None
    current_config: dict[str, Any] | None = None


# Only a client with a verified, documented mechanism for resolving a path
# relative to the project root inside `command` itself gets a non-None
# command_workspace_var. Cursor documents "${workspaceFolder}" substitution
# directly in `command`. Every other client — VS Code included — has no such
# verified mechanism: VS Code's own reference states `command` "must be
# available on your system path or contain its full path", its documented
# `cwd` key already defaults to the workspace folder (so setting it changes
# nothing), and no VS Code doc describes a relative `command` resolving
# against `cwd`. Claude Code, Codex, and Copilot likewise have no verified
# mechanism (Claude Code's `cwd` config key is confirmed non-functional; its
# `${CLAUDE_PROJECT_DIR}` is only injected into hook invocations, not the
# session process that resolves `.mcp.json`) — they and Claude Desktop always
# get an absolute command. Kept as a plain dict so tests can patch individual
# entries (e.g. redirect the Claude Desktop path into a tmp directory).
MCP_CLIENTS: dict[str, McpClient] = {
    "cursor": McpClient(
        name="cursor",
        config_path=Path(".cursor/mcp.json"),
        servers_key="mcpServers",
        detect_paths=(Path(".cursor"),),
        config_format="json",
        command_workspace_var="${workspaceFolder}",
    ),
    "codex": McpClient(
        name="codex",
        config_path=Path(".codex/config.toml"),
        servers_key="mcp_servers",
        detect_paths=(Path(".codex"), Path("AGENTS.md")),
        config_format="toml",
        command_workspace_var=None,
    ),
    "claude": McpClient(
        name="claude",
        config_path=Path.home() / ".config" / "claude" / "config.json",
        servers_key="mcpServers",
        detect_paths=(Path.home() / ".config" / "claude",),
        config_format="json",
        command_workspace_var=None,
    ),
    "vscode": McpClient(
        name="vscode",
        config_path=Path(".vscode/mcp.json"),
        servers_key="servers",
        detect_paths=(Path(".vscode"),),
        config_format="json",
        command_workspace_var=None,
    ),
    "claude-code": McpClient(
        name="claude-code",
        config_path=Path(".mcp.json"),
        servers_key="mcpServers",
        detect_paths=(Path("CLAUDE.md"),),
        config_format="json",
        command_workspace_var=None,
    ),
    "copilot": McpClient(
        name="copilot",
        config_path=Path(".github/copilot/mcp.json"),
        servers_key="servers",
        detect_paths=(Path(".github"),),
        config_format="json",
        command_workspace_var=None,
    ),
}


class McpConfigReadError(Exception):
    """An existing MCP client config could not be read and must not be overwritten."""

    def __init__(self, config_path: Path, reason: str) -> None:
        self.config_path = config_path
        self.reason = reason
        super().__init__(
            f"{config_path}: existing MCP config could not be read ({reason}); "
            "refusing to overwrite it. Fix or remove the file and re-run."
        )


class InstallResult(BaseModel):
    model_config = ConfigDict(frozen=True)

    client_name: str
    config_path: Path
    already_configured: bool
    updated: bool
    message: str


class ResolvedDct(BaseModel):
    """The `dct` executable to embed in an MCP client config.

    `command` is always a valid string, safe to write verbatim — absolute, or
    a bare/PATH-relative name such as `"dct"`. `project_relative` is set only
    when `command` was found under the project's own `.venv` — the one case
    where a client with a verified workspace-relative mechanism may rewrite
    the command as a path relative to the project root instead of using
    `command` as-is.
    """

    model_config = ConfigDict(frozen=True)

    command: str
    project_relative: str | None = None


def list_clients() -> list[McpClient]:
    """Return all supported MCP clients from the current MCP_CLIENTS registry."""
    return list(MCP_CLIENTS.values())


def resolve_dct_executable(ai_config_root: Path) -> ResolvedDct:
    """Path to the `dct` install for embedding in MCP client configs.

    Prefers the project-local ``.venv/bin/dct`` (POSIX) or
    ``.venv/Scripts/dct.exe`` (Windows) so that committed configs work on
    every teammate's machine — not just the machine that ran ``dct init mcp``.
    When found, ``project_relative`` is set (relative to ``ai_config_root``,
    computed once here — the one place that already knows the invariant
    holds) so a client with a verified workspace-relative mechanism can
    rewrite the command; ``command`` itself is always the absolute path.

    When no project venv is found, falls through to today's behavior:
    ``sys.argv[0]`` (validated) → ``shutil.which("dct")`` → bare ``"dct"``,
    with ``project_relative`` left unset (``None``).
    """
    venv_bin = (
        ai_config_root / ".venv" / "Scripts" / "dct.exe"
        if os.name == "nt"
        else ai_config_root / ".venv" / "bin" / "dct"
    )
    if venv_bin.is_file():
        return ResolvedDct(
            command=str(venv_bin),
            project_relative=venv_bin.relative_to(ai_config_root).as_posix(),
        )

    invoked = Path(sys.argv[0]).expanduser()
    try:
        resolved = invoked.resolve(strict=False)
    except OSError:
        resolved = invoked
    name = resolved.name.casefold()
    if name in ("dct", "dct.exe") and resolved.is_file():
        return ResolvedDct(command=str(resolved))
    return ResolvedDct(command=shutil.which("dct") or "dct")


def install_for_client(
    client: McpClient,
    *,
    dct_executable: ResolvedDct,
    server_args: list[str],
    ai_config_root: Path,
    force: bool = False,
) -> InstallResult:
    """Write the MCP server entry for one client config file.

    dct_executable.project_relative is only set when the project venv's `dct`
    was found; it's used only when the client also declares
    `command_workspace_var` (Cursor: a documented `${workspaceFolder}` prefix
    inside `command`). Every other combination — no project venv, or a client
    with no workspace var (VS Code, Claude Code, Codex, Copilot, Claude
    Desktop) — writes `dct_executable.command` verbatim.
    server_args: argv after the command, e.g. ["mcp", "serve"].
    ai_config_root: directory the client's relative config paths are written under.
    """
    if (
        dct_executable.project_relative is not None
        and client.command_workspace_var is not None
    ):
        command = f"{client.command_workspace_var}/{dct_executable.project_relative}"
    else:
        command = dct_executable.command

    server_entry: dict[str, Any] = {
        "command": command,
        "args": server_args,
    }
    abs_path = (
        client.config_path
        if client.config_path.is_absolute()
        else ai_config_root / client.config_path
    )
    if client.config_format == "toml":
        outcome = _upsert_toml_mcp_config(
            abs_path, client.servers_key, server_entry, force
        )
    else:
        outcome = _upsert_mcp_config(abs_path, client.servers_key, server_entry, force)

    already = outcome is None
    message = (
        f"  {abs_path} already has dbt-charts (use -f to update)"
        if already
        else f"  {'Updated' if outcome == 'updated' else 'Added dbt-charts to'} {abs_path}"
    )
    return InstallResult(
        client_name=client.name,
        config_path=abs_path,
        already_configured=already,
        updated=outcome == "updated",
        message=message,
    )


def _upsert_mcp_config(
    config_path: Path,
    servers_key: str,
    server_entry: dict[str, Any],
    force: bool,
) -> Literal["added", "updated"] | None:
    """Add dbt-charts to a JSON MCP config file, preserving existing content.

    Returns "added"/"updated", or None if skipped (already configured).
    Raises McpConfigReadError, without touching the file, if an existing
    config can't be read or isn't a JSON object — never silently discarded.
    """
    config_path.parent.mkdir(parents=True, exist_ok=True)

    existing: dict[str, Any] = {}
    if config_path.exists():
        try:
            loaded = json.loads(config_path.read_text(encoding="utf-8"))
        except json.JSONDecodeError as exc:
            raise McpConfigReadError(config_path, f"invalid JSON: {exc}") from exc
        except OSError as exc:
            raise McpConfigReadError(config_path, str(exc)) from exc
        if not isinstance(loaded, dict):
            raise McpConfigReadError(
                config_path,
                f"top-level JSON must be an object, got {type(loaded).__name__}",
            )
        existing = loaded

    already_has = servers_key in existing and "dbt-charts" in existing.get(
        servers_key, {}
    )
    if already_has and not force:
        return None

    existing.setdefault(servers_key, {})
    existing[servers_key]["dbt-charts"] = server_entry
    config_path.write_text(json.dumps(existing, indent=2) + "\n", encoding="utf-8")
    return "updated" if already_has else "added"


def _upsert_toml_mcp_config(
    config_path: Path,
    servers_key: str,
    server_entry: dict[str, Any],
    force: bool,
) -> Literal["added", "updated"] | None:
    """Add dbt-charts to a TOML MCP config file, preserving existing content.

    Returns "added"/"updated", or None if skipped (already configured).
    """
    config_path.parent.mkdir(parents=True, exist_ok=True)

    existing: dict[str, Any] = {}
    if config_path.exists():
        try:
            existing = tomllib.loads(config_path.read_text(encoding="utf-8"))
        except tomllib.TOMLDecodeError as exc:
            raise McpConfigReadError(config_path, f"invalid TOML: {exc}") from exc
        except OSError as exc:
            raise McpConfigReadError(config_path, str(exc)) from exc

    already_has = "dbt-charts" in existing.get(servers_key, {})
    if already_has and not force:
        return None

    existing.setdefault(servers_key, {})
    existing[servers_key]["dbt-charts"] = server_entry
    config_path.write_text(tomli_w.dumps(existing), encoding="utf-8")
    return "updated" if already_has else "added"
