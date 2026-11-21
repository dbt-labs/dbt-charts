"""Inspect command — OSS template-management verbs only.

The profiler verbs (inspect_command, inspect_all_command, audit_command) live
in the private ``dbt-charts-super-schema`` package and are registered via the
``dbt-charts.cli_plugins`` entry-point when that package is installed.

This module contains only the template-management commands that ship with the
OSS ``dbt-charts`` wheel: eject, templates, and validate-templates.
"""

from __future__ import annotations

from pathlib import Path

import typer

from dbt_charts.agent_api._paths import CHARTS_SUBDIR
from dbt_charts.cli._console import dct_console

console = dct_console()


def eject_command(
    templates: list[str],
    all_templates: bool = False,
    force: bool = False,
    output_dir: Path | None = None,
) -> None:
    from dbt_charts.agent_api import inspect as _api

    target_dir = output_dir or (Path.cwd() / CHARTS_SUBDIR / "inspect")
    to_eject: list[str] | None = None if all_templates else (templates or None)
    if not all_templates and not templates:
        console.print("[bold red]Error:[/bold red] Specify template names or --all")
        console.print(f"Available: {', '.join(t.name for t in _api.list_templates())}")
        raise typer.Exit(1)
    try:
        ejected = _api.eject_templates(target_dir, templates=to_eject, force=force)
    except ValueError as e:
        console.print(f"[bold red]Error:[/bold red] {e}")
        raise typer.Exit(1) from None
    skipped_count = len(to_eject or [t.name for t in _api.list_templates()]) - len(
        ejected
    )
    console.print(f"[dim]Output directory: {target_dir}[/dim]\n")
    for p in ejected:
        console.print(f"  [green]✓[/green] {p.name}")
    console.print()
    if ejected:
        console.print(
            f"[bold green]✓[/bold green] Ejected {len(ejected)} template(s) to {target_dir}"
        )
    if skipped_count > 0:
        console.print(f"[dim]Skipped {skipped_count} existing file(s)[/dim]")
    if ejected and not force:
        console.print(
            "\n[dim]To reset to built-in defaults: dct inspect eject --all --force[/dim]"
        )


def validate_ejected_templates_command(output_dir: Path | None = None) -> None:
    from dbt_charts.agent_api import inspect as _api

    target = output_dir or (Path.cwd() / CHARTS_SUBDIR / "inspect")
    try:
        result = _api.validate_ejected_templates(target_dir=target)
    except FileNotFoundError as e:
        console.print(f"[bold red]Error:[/bold red] {e}")
        raise typer.Exit(1) from None
    for label, names, style in [
        ("Missing ejected templates", result.missing, "bold red"),
        (
            "Upstream changed since eject (manual rebase needed)",
            result.upstream_changed,
            "bold yellow",
        ),
        (
            "Customized templates still aligned to built-ins",
            result.custom_safe,
            "bold cyan",
        ),
        ("Unchanged templates", result.unchanged, "dim"),
    ]:
        if names:
            console.print(f"[{style}]{label}:[/{style}]")
            for name in names:
                console.print(f"  - {name}")
    if not result.success:
        raise typer.Exit(1)
    console.print("\n[bold green]✓[/bold green] Ejected templates are compatible.")


def templates_command() -> None:
    from dbt_charts.agent_api import inspect as _api

    console.print("\n[bold]Available Inspect Templates[/bold]\n")
    for t in _api.list_templates():
        console.print(f"  • {t.name}")
    console.print("\n[dim]To customize a template:[/dim]")
    console.print("  dct inspect eject <template>       # Copy to charts/inspect/")
    console.print("  dct inspect eject --all            # Copy all templates")
    console.print("  dct inspect validate-templates     # Check compatibility")
    console.print()
