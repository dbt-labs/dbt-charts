"""`dct init skills` thin wrapper: parse args, call agent_api.skill_install."""

from __future__ import annotations

from pathlib import Path

import typer

from dbt_charts.agent_api import skill_install
from dbt_charts.agent_api._paths import find_repo_root
from dbt_charts.cli._project import DCT_ROOT_MARKERS, resolve_skill_install_root

_VALID_TARGETS = skill_install.SKILL_INSTALL_TARGETS


def _resolve_targets(
    target: str | None,
    *,
    all_targets: bool,
    dir_override: Path | None,
    install_root: Path,
) -> list[Path]:
    if dir_override is not None:
        return [dir_override.resolve()]

    if target is not None:
        key = target.lower()
        if key not in _VALID_TARGETS:
            typer.echo(
                f"Unknown target: {target}. "
                f"Supported: {', '.join(sorted(_VALID_TARGETS))}",
                err=True,
            )
            raise typer.Exit(1)
        return [skill_install.target_dir_for(key, project_root=install_root)]

    detected = skill_install.detect_skill_targets(install_root)
    if all_targets:
        if not detected:
            typer.echo(
                "No agent skill targets detected (.cursor/, AGENTS.md, CLAUDE.md).\n"
                "Pass an explicit target: dct init skills agents | claude",
                err=True,
            )
            raise typer.Exit(1)
        keys = detected
    elif detected:
        keys = detected
    else:
        # Bare `dct init skills` with nothing detected is the documented
        # onboarding path for a fresh repo, which by definition has none of
        # these markers yet -- error here would contradict the docs. Default
        # to "agents", the tool-agnostic target every other client discovers.
        keys = ["agents"]

    return [skill_install.target_dir_for(k, project_root=install_root) for k in keys]


def _display_path(path: Path, repo_root: Path, global_install: bool) -> Path:
    """Repo-relative for a repo install; `~/...` for a global one, so the two
    never print the same `.claude/skills/` line."""
    if global_install:
        return Path("~") / path.relative_to(repo_root)
    try:
        return path.relative_to(repo_root)
    except ValueError:
        return path


def _format_legacy_note(
    legacy_dirs: list[Path], repo_root: Path, global_install: bool
) -> str | None:
    if not legacy_dirs:
        return None
    rel = _display_path(legacy_dirs[0], repo_root, global_install)
    rerun = "dct init skills --global" if global_install else "dct init skills agents"
    return (
        f"Legacy skill install detected at {rel} — "
        f"re-run `{rerun}` and remove the legacy dir manually."
    )


def run_init_skills(
    target: str | None,
    all_targets: bool,
    dir_override: Path | None,
    global_install: bool,
    check: bool,
    project_dir: Path | None = None,
    project_dir_explicit: bool = False,
) -> None:
    """Shared implementation for ``dct init skills``.

    Two roots, deliberately separate. ``install_root`` is the base whose
    ``.claude/skills`` / ``.agents/skills`` receive the install;
    ``repo_root`` is the repository that destination sits in, and is what
    printed paths are relative to and what legacy installs are scanned
    under. They coincide for a bare run and diverge under ``--project-dir``
    or ``--dir``.

    ``project_dir_explicit`` distinguishes a typed ``--project-dir`` from
    ``DCT_PROJECT_DIR`` env-var fallthrough: the env var is a documented
    default, so it keeps the walk-to-git-root behavior, while a typed flag
    names the install root outright and is validated. ``--dir`` names the
    destination itself, which leaves ``--project-dir`` selecting nothing, so
    it is neither honored nor validated there.
    """
    modes_given = sum(
        [target is not None, all_targets, dir_override is not None, global_install]
    )
    if modes_given > 1:
        typer.echo(
            "Specify one of: a target name, --all, --dir, or --global, not combined.",
            err=True,
        )
        raise typer.Exit(1)

    if global_install:
        target_dirs = skill_install.detect_global_skill_targets()
        if not target_dirs:
            typer.echo(
                "No user-level agent directories detected (~/.claude/, "
                "~/.agents/, ~/.codex/).\n"
                "Supported install targets: ~/.claude/skills, ~/.agents/skills.\n"
                "Pass an explicit target: dct init skills --dir PATH",
                err=True,
            )
            raise typer.Exit(1)
        repo_root = Path.home()
    else:
        start = project_dir if project_dir is not None else Path.cwd()
        # --project-dir names the install root only when nothing else does;
        # --dir names the destination itself, which leaves --project-dir
        # selecting nothing, so there is no destination to validate.
        names_install_root = project_dir_explicit and dir_override is None
        resolution = resolve_skill_install_root(
            start, walk_to_git_root=not names_install_root
        )
        if resolution.root is None:
            markers_str = ", ".join(DCT_ROOT_MARKERS)
            msg = (
                f"Error: --project-dir {project_dir} does not contain a dbt charts "
                f"or dbt project.\nLooked for: {markers_str}."
            )
            if resolution.nearest_root is not None:
                msg += f"\nTip: did you mean {resolution.nearest_root}?"
            else:
                msg += (
                    "\nRe-run without --project-dir to install at the git root, "
                    "or point it at a directory with a dbt charts or dbt project."
                )
            typer.echo(msg, err=True)
            raise typer.Exit(1)
        install_root = resolution.root
        target_dirs = _resolve_targets(
            target,
            all_targets=all_targets,
            dir_override=dir_override,
            install_root=install_root,
        )
        # Anchored on the invocation, never on the destination: outside a
        # repository there is no enclosing root, and falling back to the
        # destination makes every printed path "." and points the legacy scan
        # at the install target, where a legacy dir cannot be.
        destination = (
            dir_override.resolve() if dir_override is not None else install_root
        )
        repo_root = find_repo_root(destination) or install_root

    prefix = "Would install" if check else "Installed"
    any_legacy = False
    had_error = False
    for target_dir in target_dirs:
        try:
            result = skill_install.install_skills(
                target_dir=target_dir,
                project_root=repo_root,
                check=check,
            )
        except skill_install.SkillInstallError as exc:
            typer.echo(f"  Error: {exc}", err=True)
            had_error = True
            continue
        rel = _display_path(target_dir, repo_root, global_install)
        if result.installed:
            typer.echo(f"  {prefix} workflow skills ({len(result.installed)}) → {rel}/")
            for name in sorted(result.installed):
                typer.echo(f"    ✓ {name}")
            if not check and not global_install:
                typer.echo(f"  Gitignore: {rel}/{skill_install.INSTALL_NAME_PREFIX}*/")
        if result.retired_removed:
            retired_verb = "Would remove" if check else "Removed"
            typer.echo(f"  {retired_verb} retired: {', '.join(result.retired_removed)}")
        legacy_note = _format_legacy_note(
            result.legacy_dirs_detected, repo_root, global_install
        )
        if legacy_note:
            any_legacy = True
            typer.echo(f"  {legacy_note}")

    if check:
        typer.echo("")
        typer.echo("  Dry run — no files written.")
    elif any_legacy:
        typer.echo("")
        typer.echo(
            "  Re-run after removing legacy .cursor/skills or .codex/skills dirs."
        )

    if had_error:
        raise typer.Exit(1)
