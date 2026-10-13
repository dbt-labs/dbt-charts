"""`dct init mcp` thin wrapper: parse args, call agent_api.mcp_install, format output."""

from __future__ import annotations

import json
from pathlib import Path

import typer

from dbt_charts.agent_api import mcp_install
from dbt_charts.cli._project import DCT_ROOT_MARKERS, resolve_mcp_project_dir


def run_init(
    client: str | None,
    all_clients: bool,
    force: bool,
    project_dir: Path | None = None,
) -> None:
    """Shared implementation for `dct init mcp`."""
    cwd = Path.cwd()
    resolution = resolve_mcp_project_dir(project_dir, cwd)
    if resolution.project_dir is None:
        markers_str = ", ".join(DCT_ROOT_MARKERS)
        if project_dir is not None:
            msg = (
                f"Error: --project-dir {project_dir} does not contain a dbt charts "
                f"or dbt project.\n"
                f"Looked for: {markers_str}."
            )
            if resolution.nearest_root is not None:
                msg += f"\nTip: did you mean {resolution.nearest_root}?"
        else:
            msg = (
                f"Error: No dbt charts or dbt project found at or above {cwd}.\n"
                f"Looked for: {markers_str}.\n"
                f"Re-run from inside your dbt charts project, "
                f"or pass --project-dir <path>."
            )
        typer.echo(msg, err=True)
        raise typer.Exit(1)
    ai_config_root = resolution.ai_config_root
    project_dir = resolution.project_dir

    dct_executable = mcp_install.resolve_dct_executable(ai_config_root)
    server_args: list[str] = ["mcp", "serve"]
    if project_dir != ai_config_root:
        server_args += ["--project-dir", str(project_dir)]

    if client and client.lower() == "print":
        # print is not committed — dct_executable.command is whatever resolved
        # (absolute path, or the last-resort bare "dct") for the user to paste.
        entry = {"command": dct_executable.command, "args": server_args}
        typer.echo(json.dumps({"mcpServers": {"dbt-charts": entry}}, indent=2))
        return

    if client and all_clients:
        typer.echo("Specify either a client or --all, not both.", err=True)
        raise typer.Exit(1)

    all_known = mcp_install.list_clients()
    known_names = {c.name for c in all_known}

    if all_clients:
        targets = all_known
    elif client:
        target = client.lower()
        if target not in known_names:
            typer.echo(
                f"Unknown client: {client}. "
                f"Supported: {', '.join(sorted(known_names) + ['print'])}",
                err=True,
            )
            raise typer.Exit(1)
        targets = [c for c in all_known if c.name == target]
    else:
        targets = [
            c
            for c in all_known
            if any((ai_config_root / dp).exists() for dp in c.detect_paths)
        ]
        if not targets:
            typer.echo(
                "No AI client markers detected (.cursor/, .codex/, .vscode/, "
                ".github/, CLAUDE.md, AGENTS.md, ~/.config/claude/)."
            )
            typer.echo("")
            typer.echo("To configure manually, specify a client:")
            typer.echo("  dct init mcp cursor        # Cursor")
            typer.echo("  dct init mcp vscode        # VS Code")
            typer.echo("  dct init mcp claude        # Claude Desktop")
            typer.echo("  dct init mcp claude-code   # Claude Code")
            typer.echo("  dct init mcp codex         # OpenAI Codex CLI")
            typer.echo("  dct init mcp copilot       # GitHub Copilot Coding Agent")
            typer.echo("  dct init mcp --all         # Write every supported config")
            typer.echo("  dct init mcp print         # Print JSON config to stdout")
            return

    configured = []
    had_error = False
    for c in targets:
        try:
            result = mcp_install.install_for_client(
                c,
                dct_executable=dct_executable,
                server_args=server_args,
                ai_config_root=ai_config_root,
                force=force,
            )
        except mcp_install.McpConfigReadError as exc:
            typer.echo(f"  Error: {exc}", err=True)
            had_error = True
            continue
        typer.echo(result.message)
        if not result.already_configured:
            configured.append(c.name)

    if configured:
        typer.echo("")
        # Trailer pinned to dbt_charts.ai.tool_schemas.ALL_TOOLS and
        # dbt_charts.ai.mcp.server._BASE_RESOURCES by
        # test_configured_trailer_advertises_every_tool_and_resource_family.
        # Update these strings when either registry changes.
        typer.echo(
            "  MCP tools: describe_board, docs, execute_query, "
            "get_diagnostic_code, get_skill, list_diagnostic_codes, list_skills, "
            "render_board, search_boards"
        )
        typer.echo(
            "  Resources: dct://boards, dct://board/{path}, "
            "dct://docs/{all,reference,<topic>}, "
            "dct://guide/{board-design,board-build,board-review,report-design}"
        )

    typer.echo("")
    if configured:
        if "cursor" in configured:
            typer.echo(
                "  Next: Open Cursor Settings → MCP and enable the 'dbt-charts' server."
            )
        elif "claude" in configured:
            typer.echo("  Restart Claude Desktop for changes to take effect.")
        else:
            clients_str = ", ".join(sorted(configured))
            typer.echo(f"  Restart {clients_str} for changes to take effect.")
        typer.echo(
            "  Install workflow skills: dct init skills (separate from MCP wiring)."
        )

    if had_error:
        raise typer.Exit(1)
