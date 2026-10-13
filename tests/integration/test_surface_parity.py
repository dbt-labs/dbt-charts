"""CLI/MCP parity test — every agent_api verb must have both wrappers.

Structure:
  _TOOL_TO_CLI  : mcp_tool_name → cli_command_name for verbs wired on both surfaces
  _EXEMPT_TOOLS : mcp_tool_names intentionally skipping CLI parity (with rationale)

New agent_api verb added to TOOL_HANDLERS without a CLI command → CI fails.
"""

from __future__ import annotations

import pytest

from dbt_charts.ai.tools import TOOL_HANDLERS

# Verbs fully wired on both CLI and MCP.  cli_name is the `dct <name>` subcommand.
# Note: execute_query's two forms (sql, and board + query) both map to `dct query` (D10).
_TOOL_TO_CLI: dict[str, str] = {
    "render_board": "render",
    "execute_query": "query",
    "search_boards": "search",
    "docs": "docs",
    "describe_board": "describe",
    "list_skills": "skills",
    "get_skill": "skills",
    "list_diagnostic_codes": "docs",
    "get_diagnostic_code": "docs",
}

# Tools intentionally missing CLI parity (each comment explains why).
_EXEMPT_TOOLS: set[str] = {
    "save_dashboard",  # Cloud-internal; filesystem agents use Edit + dct validate
    # General project-file primitives for the in-process agent (read/write/edit/glob/
    # grep). Deliberately not dct CLI verbs — dct is not a file manager; agents
    # author boards directly with these, then `dct validate`/`dct render`.
    "read_file",
    "write_file",
    "edit_file",
    "glob_files",
    "grep_files",
    "move_file",
    "delete_file",
}


def _cli_commands() -> set[str]:
    """Return the set of top-level `dct` subcommand names."""
    from typer.core import TyperGroup
    from typer.main import get_command

    from dbt_charts.cli.main import app

    click_app = get_command(app)
    assert isinstance(click_app, TyperGroup)
    return set(click_app.commands)


def test_all_mcp_tools_are_accounted_for() -> None:
    """Every TOOL_HANDLERS entry must be in _TOOL_TO_CLI or _EXEMPT_TOOLS.

    Fails when a developer adds an MCP tool without also adding a CLI command
    (or explicitly exempting it).
    """
    unaccounted = set(TOOL_HANDLERS) - set(_TOOL_TO_CLI) - _EXEMPT_TOOLS
    assert not unaccounted, (
        "MCP tools with no CLI command and no exemption:\n"
        + "\n".join(f"  {t}" for t in sorted(unaccounted))
        + "\n\nAdd a CLI command and register it in _TOOL_TO_CLI, "
        "or add to _EXEMPT_TOOLS with a reason."
    )


def test_all_verbs_have_cli_commands() -> None:
    """Every verb in _TOOL_TO_CLI must have its CLI command registered in the app."""
    cli = _cli_commands()
    missing = [
        f"'{tool}' → expected `dct {cli_name}`"
        for tool, cli_name in _TOOL_TO_CLI.items()
        if cli_name not in cli
    ]
    assert not missing, "CLI commands not registered:\n" + "\n".join(
        f"  {m}" for m in missing
    )


def test_all_verbs_have_mcp_tools() -> None:
    """Every verb in _TOOL_TO_CLI must have its MCP tool registered in TOOL_HANDLERS."""
    missing = [t for t in _TOOL_TO_CLI if t not in TOOL_HANDLERS]
    assert not missing, "MCP tools not in TOOL_HANDLERS:\n" + "\n".join(
        f"  {t}" for t in missing
    )


@pytest.mark.parametrize(("tool_name", "cli_name"), sorted(_TOOL_TO_CLI.items()))
def test_verb_help_is_not_empty(tool_name: str, cli_name: str) -> None:
    """Each registered CLI command must have a non-empty help string."""
    from typer.core import TyperGroup
    from typer.main import get_command

    from dbt_charts.cli.main import app

    click_app = get_command(app)
    assert isinstance(click_app, TyperGroup)
    cmd = click_app.commands.get(cli_name)
    assert cmd is not None, f"`dct {cli_name}` not found in CLI app"
    assert cmd.help and len(cmd.help) > 20, (
        f"`dct {cli_name}` has no help text or only a placeholder"
    )
