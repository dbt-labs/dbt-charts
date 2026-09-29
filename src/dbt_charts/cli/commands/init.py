"""Init command — wizard that bootstraps a dbt charts project."""

from __future__ import annotations

import shutil
import sys
from pathlib import Path

import typer

from dbt_charts.cli._project import resolve_init_root


def _resolve(
    flag: bool | None,
    yes: bool,
    prompt: str,
    default: bool,
) -> bool:
    """Resolve a wizard question: explicit flag > yes/non-TTY default > prompt."""
    if flag is not None:
        return flag
    if yes or not sys.stdin.isatty():
        return default
    return typer.confirm(prompt, default=default)


def _resolve_init_root(project_dir: Path | None, *, yes: bool) -> Path:
    """Choose the init root for the wizard without changing lower-level API defaults."""
    cwd = Path.cwd().resolve()
    resolution = resolve_init_root(project_dir, cwd)
    git_root = resolution.git_root
    if git_root is None:
        return resolution.root

    typer.echo(f"  No dbt charts or dbt project found above {cwd}.")
    typer.echo(f"  git root: {git_root}")
    typer.echo(f"  current directory: {cwd}")

    use_git_root = _resolve(
        None,
        yes=yes,
        prompt="Scaffold this repo at the git root instead of the current directory?",
        default=True,
    )
    chosen_root = git_root if use_git_root else cwd
    label = "git root" if use_git_root else "current directory"
    typer.echo(f"  Using {label}: {chosen_root}")
    return chosen_root


def run_wizard(
    *,
    project_dir: Path | None,
    project_dir_explicit: bool,
    force: bool,
    yes: bool,
    skills: bool | None,
    mcp: bool | None,
    vscode: bool | None,
    cursor: bool | None,
) -> None:
    """Run the dct init wizard: prompt, scaffold, dispatch opt-ins."""
    from dbt_charts.agent_api.init import init_project

    root = _resolve_init_root(project_dir, yes=yes)

    if skills is not None:
        do_skills = skills
    else:
        do_skills = _resolve(
            None,
            yes=yes,
            prompt="Install dbt charts workflow skills for AI assistants?",
            default=True,
        )

    # MCP
    do_mcp = _resolve(
        mcp,
        yes=yes,
        prompt="Set up the dbt charts MCP server for AI assistants?",
        default=True,
    )

    # IDE detection — only prompt for IDEs found on PATH
    code_found = shutil.which("code") is not None
    cursor_found = shutil.which("cursor") is not None

    if vscode is not None:
        do_vscode = vscode
    elif code_found:
        do_vscode = _resolve(
            None,
            yes=yes,
            prompt="Install the dbt charts extension into VS Code?",
            default=True,
        )
    else:
        do_vscode = False

    if cursor is not None:
        do_cursor = cursor
    elif cursor_found:
        do_cursor = _resolve(
            None,
            yes=yes,
            prompt="Install the dbt charts extension into Cursor?",
            default=True,
        )
    else:
        do_cursor = False

    if not code_found and not cursor_found and vscode is None and cursor is None:
        typer.echo(
            "  VS Code / Cursor not found on PATH; skipping extension install. "
            "Run 'dct init code' or 'dct init cursor' later if you install one."
        )

    # Core scaffold
    result = init_project(
        project_dir=root,
        force=force,
        eject_inspect=False,
    )

    # Output scaffold results
    if result.dbt_detected:
        typer.echo(f"  dbt project detected: {result.project_dir / 'dbt_project.yml'}")
    for f in result.created_files:
        typer.echo(f"  created {f}")
    for f in result.refreshed_files:
        typer.echo(f"  refreshed {f}")
    for f in result.skipped_files:
        typer.echo(f"  skipped {f} (already exists)")
    for hint in result.hints:
        typer.echo(f"\n  Tip: {hint}")

    # Opt-in dispatches
    install_warnings: list[str] = []

    if do_skills:
        from dbt_charts.cli.commands import skills_init

        for target in ("agents", "claude"):
            skills_init.run_init_skills(
                target=target,
                all_targets=False,
                dir_override=None,
                global_install=False,
                check=False,
                # The user's own --project-dir, not `root` (the wizard's
                # already-resolved scaffold root): skills follow the same
                # git-root default as `dct init skills` unless the user
                # named a directory explicitly.
                project_dir=project_dir,
                project_dir_explicit=project_dir_explicit,
            )

    if do_mcp:
        from dbt_charts.cli._extras import install_extras
        from dbt_charts.cli.commands import mcp_init

        # Writing MCP client config without the `mcp` package leaves the client
        # launching a `dct mcp serve` that exits 1 — and over stdio the install
        # hint goes to stderr, where most clients surface only "server failed to
        # start". Install before wiring so the config points at a working server.
        #
        # Warn rather than abort, like every other opt-in here: a config whose
        # server prints an install hint still beats no config, and failing here
        # would skip the IDE-extension steps the user already said yes to.
        try:
            install_extras("mcp", interactive=False)
        except typer.Exit:
            # No command restated here: install_extras already printed the
            # installer-aware panel (pip vs uv tool) before raising, and
            # hardcoding one would hand uv-tool users something unrunnable.
            install_warnings.append(
                "mcp extra install failed — MCP config was still written, but "
                "`dct mcp serve` will not start until the install above succeeds."
            )

        mcp_init.run_init(
            client=None,
            all_clients=False,
            force=False,
            project_dir=root,
        )

    if do_vscode:
        from dbt_charts.cli.commands import extension as extension_cmd

        rc = extension_cmd.install_extension("code", emit=typer.echo)
        if rc != 0:
            install_warnings.append(
                "VS Code extension install failed (see output above)."
            )

    if do_cursor:
        from dbt_charts.cli.commands import extension as extension_cmd

        rc = extension_cmd.install_extension("cursor", emit=typer.echo)
        if rc != 0:
            install_warnings.append(
                "Cursor extension install failed (see output above)."
            )

    # Final success block
    typer.echo("")
    for warning in install_warnings:
        typer.echo(f"  Warning: {warning}")
    if install_warnings:
        typer.echo("")
    typer.echo(
        "  You're all set. Your project has a charts/ directory with a starter dashboard."
    )
    typer.echo("")
    typer.echo("  Run `dct serve` to preview it in your browser.")
    typer.echo("")
    typer.echo(
        "  New to dbt Charts, or an agent? Read `dct skills intro` before "
        "authoring boards."
    )
