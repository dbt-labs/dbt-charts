"""Shared project-discovery decorator for project-touching CLI verbs.

Every verb that operates on an existing project (render, validate, describe,
query, search, serve, chat) discovers its `Project` the same way:
`--project-dir` wins outright; otherwise walk up from cwd for a project
marker; otherwise fail loud with a clean CLI message instead of a silent cwd
fallback. `with_project` is that one rule, injected once.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from functools import wraps
from pathlib import Path
from typing import Any, TypeVar

import typer
import yaml

from dbt_charts.agent_api import Project
from dbt_charts.agent_api._paths import (
    CHARTS_SUBDIR,
    DCT_ROOT_MARKERS as DCT_ROOT_MARKERS,
    find_dct_root,
    find_repo_root,
    resolve_dbt_project_dir,
)
from dbt_charts.cli._error_format import print_error
from dbt_charts.cli.filesystem_project import FilesystemProject

R = TypeVar("R")


class ProjectNotFoundError(Exception):
    """No `--project-dir` given and no dbt_charts.yml found walking up from cwd."""


def _marker_hint(directory: Path) -> str:
    return (
        f"To make this directory a project, create {directory / 'dbt_charts.yml'} "
        "(an empty file is enough) or run `dct init`."
    )


def resolve_project_dir(project_dir: Path | None) -> Path:
    """Resolve the caller's `project_dir`, walking up from cwd when omitted.

    Both paths require a project marker (`dbt_charts.yml`/
    `dbt_project.yml`) at or above the directory: an explicit `--project-dir`
    is validated the same as the cwd walk, so pointing a project-touching verb
    at a non-project fails loud here rather than surfacing later as a confusing
    file-not-found. No silent fallback to cwd.
    """
    if project_dir is not None:
        root = project_dir.resolve()
        if find_dct_root(root) is None:
            raise ProjectNotFoundError(
                f"--project-dir {root} is not a dbt charts project (no dbt_charts.yml "
                f"or dbt_project.yml here or in any parent). {_marker_hint(root)}"
            )
        return root
    cwd = Path.cwd().resolve()
    found = find_dct_root(cwd)
    if found is None:
        raise ProjectNotFoundError(
            "No dbt charts project found in the current directory or any parent "
            "(no dbt_charts.yml). Run from inside your project, or pass "
            f"--project-dir. {_marker_hint(cwd)}"
        )
    return found


def has_charts_marker(start: Path) -> bool:
    """True iff *start* sits inside a dbt charts project that has a ``charts/`` directory.

    Anchors on ``find_dct_root`` so the walk-up stops at the project root rather
    than blindly hunting upward for stray ``charts/`` dirs elsewhere on the filesystem.
    """
    if (start / CHARTS_SUBDIR).is_dir():
        return True
    project = find_dct_root(start)
    return project is not None and (project / CHARTS_SUBDIR).is_dir()


@dataclass(frozen=True)
class InitRootResolution:
    """Result of ``resolve_init_root``.

    ``git_root`` is set only for the ambiguous case (no dct project found, but a git
    root diverges from cwd); ``None`` means unambiguous, use ``root`` as-is.
    """

    root: Path
    git_root: Path | None = None


def resolve_init_root(explicit: Path | None, cwd: Path) -> InitRootResolution:
    """Determine the init scaffold root without prompting the user.

    Explicit dir wins (resolved, no git_root). Else walks up for a dct project —
    if found, that's unambiguous. Else checks for a git repo: git root == cwd is
    unambiguous; git root above cwd is ambiguous and the caller must prompt.
    """
    if explicit is not None:
        return InitRootResolution(root=explicit.resolve())

    project_root = find_dct_root(cwd)
    if project_root is not None:
        return InitRootResolution(root=project_root)

    git_root = find_repo_root(cwd)
    if git_root is None or git_root == cwd.resolve():
        return InitRootResolution(root=cwd.resolve())

    return InitRootResolution(root=cwd.resolve(), git_root=git_root)


@dataclass(frozen=True)
class McpProjectDirResolution:
    """Result of ``resolve_mcp_project_dir``.

    ``ai_config_root`` is always set (nearest dct project root, else git repo root,
    else cwd) — where AI-client configs get written.  ``project_dir`` is the project
    the MCP server actually serves, ``None`` when unresolvable; ``nearest_root``
    gives the caller a "did you mean" hint.
    """

    ai_config_root: Path
    project_dir: Path | None
    nearest_root: Path | None


def resolve_mcp_project_dir(
    explicit: Path | None, cwd: Path
) -> McpProjectDirResolution:
    """Determine both where AI configs are written and which project the MCP server serves."""
    dct_root = find_dct_root(cwd)
    ai_config_root = dct_root or find_repo_root(cwd) or cwd

    if explicit is not None:
        nearest = find_dct_root(explicit)
        project_dir = explicit.resolve() if nearest == explicit.resolve() else None
        return McpProjectDirResolution(
            ai_config_root=ai_config_root,
            project_dir=project_dir,
            nearest_root=nearest,
        )

    return McpProjectDirResolution(
        ai_config_root=ai_config_root,
        project_dir=dct_root,
        nearest_root=dct_root,
    )


@dataclass(frozen=True)
class SkillInstallRootResolution:
    """Result of ``resolve_skill_install_root``.

    ``root`` is ``None`` only when an explicit ``--project-dir`` names a
    directory with no dbt charts project at or above it; ``nearest_root``
    then gives the caller a "did you mean" hint (mirrors
    ``resolve_mcp_project_dir``).
    """

    root: Path | None
    nearest_root: Path | None = None


def resolve_skill_install_root(
    start: Path, *, walk_to_git_root: bool
) -> SkillInstallRootResolution:
    """Return the directory where skills should be installed.

    ``walk_to_git_root=True`` is the cwd-derived default: skills are agent
    tooling that belong at the git root so monorepos with nested dct projects
    don't accumulate divergent skill copies. There is no project marker to
    validate here — a bare ``dct init skills`` is the documented bootstrap
    path for a repo that has none yet.

    ``walk_to_git_root=False`` is for an explicit ``--project-dir``, which
    names the install destination outright and must not be overridden by a
    git root above it. It also must not silently normalize to a dbt charts
    project found by walking *up* from that directory — the named directory
    is validated the same way ``resolve_mcp_project_dir`` validates an
    explicit MCP project dir, and a mismatch is an error, not a fallback.
    """
    if not walk_to_git_root:
        resolved = start.resolve()
        nearest = find_dct_root(resolved)
        if nearest != resolved:
            return SkillInstallRootResolution(root=None, nearest_root=nearest)
        return SkillInstallRootResolution(root=resolved)

    project_root = find_dct_root(start) or start.resolve()
    git_root = find_repo_root(project_root)
    return SkillInstallRootResolution(
        root=git_root if git_root is not None else project_root
    )


def project_dir_was_typed(ctx: typer.Context) -> bool:
    """True when ``--project-dir`` came from argv rather than ``DCT_PROJECT_DIR``.

    The env var is a documented default; only a typed flag is an instruction,
    and only an instruction overrides where skills install.
    """
    source = ctx.get_parameter_source("project_dir")
    if source is None:
        raise RuntimeError("project_dir is not a declared parameter on this command")
    return source.name == "COMMANDLINE"


def with_project(func: Callable[..., R]) -> Callable[..., R]:
    """Replace a command body's `project_dir: Path | None` with an injected `project`.

    The wrapped function must declare `project: Project` (keyword-only) instead
    of `project_dir`. Callers keep passing `project_dir=...` and `dbt_project_dir=...`;
    the decorator resolves both and constructs the `Project`, exiting 1 with a
    clean message (never a traceback) when no project is found, or when
    `dbt_project_dir` (flag/env/config key) cannot be resolved.
    """

    @wraps(func)
    def wrapper(
        *args: Any,
        project_dir: Path | None = None,
        dbt_project_dir: Path | None = None,
        **kwargs: Any,
    ) -> R:
        try:
            resolved_dir = resolve_project_dir(project_dir)
        except ProjectNotFoundError as exc:
            print_error(str(exc))
            raise typer.Exit(1) from None
        try:
            resolved_dbt_dir = resolve_dbt_project_dir(resolved_dir, dbt_project_dir)
        except (
            TypeError,
            FileNotFoundError,
            NotADirectoryError,
            yaml.YAMLError,
        ) as exc:
            print_error(str(exc))
            raise typer.Exit(1) from None
        project: Project = FilesystemProject(resolved_dir, dbt_root=resolved_dbt_dir)
        return func(*args, project=project, **kwargs)

    return wrapper
