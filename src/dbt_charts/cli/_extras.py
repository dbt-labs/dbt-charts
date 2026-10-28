"""Optional-dependency gate for CLI commands that need heavyweight extras."""

from __future__ import annotations

import importlib.metadata
import importlib.util
import os
import re
import shlex
import shutil
import subprocess
import sys
from typing import Literal

import typer
from rich.markup import escape
from rich.panel import Panel

from dbt_charts._install_hint import install_hint
from dbt_charts.cli._console import dct_console

_console = dct_console(stderr=True)


def _extra_requirements(extra: str) -> list[str]:
    """Return requirements declared under [project.optional-dependencies.<extra>].

    The version specifier is kept: it is what stops the install this module
    offers from resolving a major the extra deliberately excludes.
    """
    try:
        reqs = importlib.metadata.requires("dbt-charts") or []
    except importlib.metadata.PackageNotFoundError as exc:
        _console.print(
            f"[red]Cannot read package metadata for 'dbt-charts' in {sys.executable}. "
            "The package may not be installed correctly.[/red]"
        )
        raise typer.Exit(1) from exc

    pattern = re.compile(rf"""extra\s*==\s*['"]({re.escape(extra)})['"]""")
    result = []
    for req in reqs:
        if pattern.search(req):
            # Drop only the environment marker; the specifier before it stays.
            result.append(req.split(";")[0].strip())
    return result


def _dist_name(requirement: str) -> str:
    """Return the bare dist name from a requirement string."""
    return re.split(r"[><=!~\s]", requirement)[0]


def _missing_packages(extra: str) -> list[str]:
    """Return requirements from <extra> whose top-level module cannot be imported."""
    missing = []
    for requirement in _extra_requirements(extra):
        # Holds for every gated extra today (mcp). An extra whose
        # import name diverges further would report missing forever, since the
        # offered install can never satisfy this check.
        dist = _dist_name(requirement)
        if importlib.util.find_spec(dist.replace("-", "_")) is None:
            missing.append(requirement)
    return missing


def _resolve_installer() -> Literal["pip", "uv"] | None:
    """Detect the active installer. Returns 'pip', 'uv', or None if neither available.

    Priority: UV env var (uv set this when it spawned us) → pip importable → uv on PATH.
    """
    if os.environ.get("UV"):
        return "uv"
    if importlib.util.find_spec("pip") is not None:
        return "pip"
    if shutil.which("uv"):
        return "uv"
    return None


def _installer_command(
    installer: Literal["pip", "uv"], missing: list[str]
) -> list[str]:
    """Return the subprocess argv to install missing packages."""
    if installer == "pip":
        return [sys.executable, "-m", "pip", "install", "--no-input", *missing]
    return ["uv", "pip", "install", "--python", sys.executable, *missing]


def _installer_prefix(installer: Literal["pip", "uv"]) -> str:
    """Return the human-readable installer prefix for copy-paste commands."""
    if installer == "pip":
        return "pip install"
    return f"uv pip install --python {shlex.quote(sys.executable)}"


def _installer_display_command(
    installer: Literal["pip", "uv"], missing: list[str]
) -> str:
    """Return the human-readable install command for the missing packages.

    Requirements are shell-quoted: `<` and `>` in a specifier are redirection
    operators, so an unquoted paste both truncates a file and drops the bound.
    """
    return f"{_installer_prefix(installer)} {' '.join(shlex.quote(m) for m in missing)}"


def _build_extras_panel(
    extra: str,
    missing: list[str],
    installer: Literal["pip", "uv"] | None,
) -> Panel:
    if installer is None:
        return Panel(
            f"The [bold]{escape(extra)}[/bold] feature requires optional packages "
            "that are not installed:\n\n"
            + "\n".join(f"  • [cyan]{escape(p)}[/cyan]" for p in missing)
            + "\n\nNo installer found in this environment. "
            "Install uv (https://docs.astral.sh/uv/getting-started/installation/) "
            "or [dim]pip[/dim] into this interpreter, then re-run — "
            "or install these packages by whatever mechanism you used to install "
            "[dim]dbt-charts[/dim] itself.",
            title=f"[yellow]Optional dependencies required for `{escape(extra)}`[/yellow]",
            expand=False,
        )
    pip_cmd = _installer_display_command(installer, missing)
    canonical_hint = install_hint(extra)
    return Panel(
        f"The [bold]{escape(extra)}[/bold] feature requires optional packages "
        "that are not installed:\n\n"
        + "\n".join(f"  • [cyan]{escape(p)}[/cyan]" for p in missing)
        + f"\n\nTo install manually:\n  [dim]{escape(pip_cmd)}[/dim]\n\n"
        + f"Or reinstall dbt charts with the {escape('[' + extra + ']')} extra:\n"
        + f"  [dim]{escape(canonical_hint)}[/dim]",
        title=f"[yellow]Optional dependencies required for `{escape(extra)}`[/yellow]",
        expand=False,
    )


def install_extras(extra: str) -> None:
    """Show the info panel, prompt, and install missing packages for <extra>.

    Raises typer.Exit(1) on install failure or user decline.
    """
    missing = _missing_packages(extra)
    if not missing:
        return

    installer = _resolve_installer()
    if installer is None:
        _console.print(_build_extras_panel(extra, missing, installer=None))
        raise typer.Exit(1)

    _console.print(_build_extras_panel(extra, missing, installer=installer))
    answer = _console.input("Install now? [Y/n] ").strip().lower()
    if answer not in ("", "y", "yes"):
        raise typer.Exit(1)

    display_cmd = _installer_display_command(installer, missing)
    _console.print(f"Installing the {escape(extra)} extra…")
    result = subprocess.run(
        _installer_command(installer, missing),
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    if result.returncode != 0:
        _console.print(result.stdout + result.stderr, markup=False, highlight=False)
        _console.print(f"[red]Install failed. Try manually:\n  {display_cmd}[/red]")
        raise typer.Exit(1)
    importlib.invalidate_caches()
    still_missing = _missing_packages(extra)
    if still_missing:
        _console.print(
            f"[red]Install reported success but packages are still missing "
            f"in {sys.executable}. Try:\n  {display_cmd}[/red]"
        )
        raise typer.Exit(1)


def require_extras(extra: str) -> None:
    """Raise typer.Exit(1) (or offer to install) if <extra> packages are missing.

    - On a TTY without DCT_NO_AUTO_INSTALL=1: prompt the user; install on yes.
    - Otherwise: print the install command and raise Exit(1).
    """
    missing = _missing_packages(extra)
    if not missing:
        return

    interactive = sys.stdin.isatty() and os.environ.get("DCT_NO_AUTO_INSTALL") != "1"

    if interactive:
        install_extras(extra)
    else:
        installer = _resolve_installer()
        _console.print(_build_extras_panel(extra, missing, installer=installer))
        raise typer.Exit(1)
