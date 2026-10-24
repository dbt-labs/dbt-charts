"""Import commands — thin wrappers over the ``agent_api`` source-bundle importers."""

from __future__ import annotations

from pathlib import Path

import typer

from dbt_charts.agent_api import (
    BundleError,
    import_hex,
    import_looker,
    import_omni,
    import_sigma,
    list_hex_projects,
    list_omni_dashboards,
    list_sigma_workbooks,
)
from dbt_charts.cli._console import dct_console
from dbt_charts.cli._error_format import print_error


def import_looker_command(
    dashboard: str, *, out: Path | None, base_url: str | None, force: bool
) -> None:
    """Import one Looker dashboard and print the bundle path."""
    try:
        bundle = import_looker(dashboard, out=out, base_url=base_url, force=force)
    except BundleError as exc:
        print_error(str(exc))
        raise typer.Exit(1) from None
    dct_console().print(f"Wrote {bundle}. Start with outline.md")


def import_omni_command(
    dashboard: str | None,
    *,
    list_only: bool,
    out: Path | None,
    base_url: str | None,
    force: bool,
) -> None:
    """Import one Omni dashboard, or with ``list_only`` list the dashboards."""
    if list_only == (dashboard is not None):
        raise typer.BadParameter(
            "pass either a dashboard or --list, not both or neither"
        )
    try:
        if dashboard is None:
            for record in list_omni_dashboards(base_url=base_url):
                typer.echo(
                    f"{record.identifier}\t{record.scope}\t{record.name}\t{record.url}"
                )
            return
        bundle = import_omni(dashboard, out=out, base_url=base_url, force=force)
    except BundleError as exc:
        print_error(str(exc))
        raise typer.Exit(1) from None
    dct_console().print(f"Wrote {bundle}. Start with outline.md")


def import_sigma_command(
    workbook: str | None,
    *,
    list_only: bool,
    out: Path | None,
    base_url: str | None,
    force: bool,
) -> None:
    """Import one Sigma workbook and print the bundle path, or list workbooks."""
    if list_only and (out is not None or force):
        raise typer.BadParameter("--out and --force do not apply with --list")
    try:
        if list_only:
            for record in list_sigma_workbooks(workbook, base_url=base_url):
                typer.echo(
                    f"{record.updated_at[:10]}\t{record.name}\t[{record.path}]\t{record.url}"
                )
            return
        if workbook is None:
            raise typer.BadParameter("pass a workbook URL or id, or --list")
        bundle = import_sigma(workbook, out=out, base_url=base_url, force=force)
    except BundleError as exc:
        print_error(str(exc))
        raise typer.Exit(1) from None
    dct_console().print(f"Wrote {bundle}. Start with outline.md")


def import_hex_command(
    project: str | None,
    *,
    list_projects: bool,
    out: Path | None,
    base_url: str | None,
    force: bool,
) -> None:
    """List Hex projects, or import one and print the bundle path."""
    if list_projects == (project is not None):
        raise typer.BadParameter("pass either a project URL or id, or --list")
    try:
        if project is None:
            # Plain echo: titles like "[Ad-Hoc] ..." are not console markup.
            for item in list_hex_projects(base_url):
                published = item.last_published_at or "unpublished"
                typer.echo(f"{item.id}\t{published}\t{item.title}")
            return
        bundle = import_hex(project, out=out, base_url=base_url, force=force)
    except BundleError as exc:
        print_error(str(exc))
        raise typer.Exit(1) from None
    dct_console().print(f"Wrote {bundle}. Start with outline.md")
