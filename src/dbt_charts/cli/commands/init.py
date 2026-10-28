"""Init command — bootstraps a dbt charts project without prompting."""

from __future__ import annotations

from pathlib import Path

import typer

from dbt_charts.cli._project import resolve_init_root

VSCODE_EXTENSION_URL = (
    "https://marketplace.visualstudio.com/items?itemName=dbtLabsInc.dbtcharts"
)


def run_wizard(
    *,
    project_dir: Path | None,
    project_dir_explicit: bool,
    force: bool,
) -> None:
    """Scaffold the project and install the `dct` stub skill."""
    from dbt_charts.agent_api.init import init_project
    from dbt_charts.cli.commands import skills_init

    resolution = resolve_init_root(project_dir, Path.cwd())
    root = resolution.root
    if resolution.via_git_root:
        typer.echo(f"  Using git root: {root}")

    result = init_project(project_dir=root, force=force, eject_inspect=False)

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

    for target in ("agents", "claude"):
        skills_init.run_init_skills(
            target=target,
            all_targets=False,
            dir_override=None,
            global_install=False,
            check=False,
            # The user's own --project-dir, not `root`: skills follow the same
            # git-root default as `dct init skills` unless the user named a
            # directory explicitly.
            project_dir=project_dir,
            project_dir_explicit=project_dir_explicit,
        )

    typer.echo("")
    typer.echo(
        "  You're all set. Your project has a charts/ directory; add a board to it."
    )
    typer.echo("")
    typer.echo(
        "  Run `dct serve` to see an intro page and your boards in your browser."
    )
    typer.echo("")
    typer.echo(f"  Using VS Code? Install the extension: {VSCODE_EXTENSION_URL}")
    typer.echo(
        "  New to dbt Charts, or an agent? Read `dct skills chart` before "
        "authoring boards."
    )
