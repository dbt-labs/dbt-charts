"""File install of the `dct` stub skill into agent skill directories."""

from __future__ import annotations

import shutil
import subprocess
from collections.abc import Set
from importlib.resources import files
from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field

from dbt_charts.agent_api._paths import find_repo_root
from dbt_charts.agent_api.skills import (
    INSTALL_SKILL_NAME,
    RETIRED_INSTALL_PREFIX,
    all_skill_names,
)

# Bare (pre-`dct-` prefix) directory names from the release just before that
# prefix landed: the 11 install-set skills that wrote bare, plus the 10
# pattern skills retired before the prefix existed. Not exhaustive back
# through every earlier rebrand, only that prefix's own predecessor names.
# Never extended; swept once, forever, under the same wheel-authored guard
# as the dct-* sweep below.
PRE_NAMESPACE_SKILL_NAMES: tuple[str, ...] = (
    "analyst-runbook",
    "board-build",
    "board-design",
    "board-replicate",
    "board-review",
    "board-structural-review",
    "board-visual-review",
    "cloud-setup",
    "data-exploration",
    "intro",
    "report-design",
    "before-after-comparison",
    "drill-down-link",
    "faceted-small-multiples",
    "filter-bar-with-variables",
    "kpi-row",
    "single-metric-bignum",
    "table-heavy-ops-dashboard",
    "time-series-trend",
    "top-n-with-detail",
    "two-by-two-grid-overview",
)

_LEGACY_SKILL_ROOTS: tuple[Path, ...] = (
    Path(".cursor/skills"),
    Path(".codex/skills"),
)

_SKILL_TARGET_DIRS: dict[str, Path] = {
    "agents": Path(".agents/skills"),
    "codex": Path(".agents/skills"),
    "claude": Path(".claude/skills"),
}

SKILL_INSTALL_TARGETS: frozenset[str] = frozenset(_SKILL_TARGET_DIRS)

# The one file `dct init skills` writes: a pointer at `dct skills`, never a
# copy of a wheel skill, so a committed install cannot drift from the wheel.
_STUB_PATH = files("dbt_charts.agent_api") / "_skill_stub" / "SKILL.md"


class SkillInstallError(Exception):
    """Installing skills into a target directory failed on the filesystem."""

    def __init__(self, target_dir: Path, reason: OSError) -> None:
        self.target_dir = target_dir
        super().__init__(
            f"{target_dir}: could not install skills ({reason}).\n"
            "Install somewhere else: dct init skills --dir <dir>\n"
            "Or read a skill without installing: dct skills <name>"
        )


class InstallSkillsResult(BaseModel):
    model_config = ConfigDict(frozen=True)

    retired_removed: list[str] = Field(default_factory=list)
    legacy_dirs_detected: list[Path] = Field(default_factory=list)


def stub_skill_text() -> str:
    return _STUB_PATH.read_text(encoding="utf-8")


def target_dir_for(target: str, *, project_root: Path) -> Path:
    rel = _SKILL_TARGET_DIRS[target]
    return project_root / rel


def detect_skill_targets(project_root: Path) -> list[str]:
    """Return install target keys detected under ``project_root``."""
    targets: list[str] = []
    if (project_root / ".cursor").is_dir() or (project_root / "AGENTS.md").is_file():
        targets.append("agents")
    if (project_root / "CLAUDE.md").is_file():
        targets.append("claude")
    return targets


def detect_global_skill_targets() -> list[Path]:
    """Return user-level skill dirs detected under ``Path.home()``.

    Mirrors ``detect_skill_targets`` but at the machine level: installs into
    each agent's directory whose parent config dir exists. An existing
    ``~/.codex/`` also signals ``~/.agents/skills``, since Codex's older
    ``~/.codex/skills`` is deprecated in favor of the shared location.
    """
    home = Path.home()
    targets: list[Path] = []
    if (home / ".claude").is_dir():
        targets.append(home / ".claude" / "skills")
    if (home / ".agents").is_dir() or (home / ".codex").is_dir():
        targets.append(home / ".agents" / "skills")
    return targets


def detect_legacy_skill_dirs(
    project_root: Path, wheel_skill_names: Set[str]
) -> list[Path]:
    """Legacy triplicate dirs that still contain a wheel skill name."""
    found: list[Path] = []
    for rel_root in _LEGACY_SKILL_ROOTS:
        root = project_root / rel_root
        if not root.is_dir():
            continue
        for child in root.iterdir():
            if child.is_dir() and child.name in wheel_skill_names:
                found.append(child)
    return found


def _is_wheel_authored(skill_md: Path) -> bool:
    """Only a SKILL.md this tool wrote carries the wheel's author line; a
    user's own skill that happens to share a retired name is not ours to sweep."""
    return skill_md.is_file() and "author: fivetran" in skill_md.read_text(
        encoding="utf-8"
    )


def _remove_install_dir(path: Path) -> None:
    """Remove a file-installed skill directory. ``shutil.rmtree`` refuses to
    follow a symlink (raises ``OSError``); unlink it directly instead, which
    also correctly leaves whatever it points to untouched."""
    if path.is_symlink():
        path.unlink()
    else:
        shutil.rmtree(path)


def _sweep_stale_install_dirs(target_dir: Path, *, check: bool = False) -> list[str]:
    """Report, and unless ``check``, remove what earlier releases installed:
    one ``dct-*`` copy per wheel skill, found by globbing that namespace, and
    the bare pre-namespace dirs before those, found by walking the fixed
    ``PRE_NAMESPACE_SKILL_NAMES`` list. Both only ever touch a directory
    carrying our own wheel-authored SKILL.md.
    """
    removed: list[str] = []
    for candidate in sorted(target_dir.glob(f"{RETIRED_INSTALL_PREFIX}*")):
        if _is_wheel_authored(candidate / "SKILL.md"):
            if not check:
                _remove_install_dir(candidate)
            removed.append(candidate.name)
    for name in PRE_NAMESPACE_SKILL_NAMES:
        candidate = target_dir / name
        if _is_wheel_authored(candidate / "SKILL.md"):
            if not check:
                _remove_install_dir(candidate)
            removed.append(name)
    return removed


def install_skills(
    *,
    target_dir: Path,
    project_root: Path,
    check: bool = False,
) -> InstallSkillsResult:
    """Write the stub skill into ``target_dir``, overwriting the one a previous
    install wrote (there is no ``--force`` flag). A ``dct/`` directory that is
    not ours is refused, in a dry run too. ``check=True`` reports what a real
    install would do without writing or deleting anything.
    """
    legacy = detect_legacy_skill_dirs(project_root, all_skill_names())
    dest_dir = target_dir / INSTALL_SKILL_NAME
    if (dest_dir.is_dir() or dest_dir.is_symlink()) and not _is_wheel_authored(
        dest_dir / "SKILL.md"
    ):
        raise SkillInstallError(
            target_dir, OSError(f"{dest_dir} is a skill of your own, not ours")
        )
    try:
        if not check:
            target_dir.mkdir(parents=True, exist_ok=True)
        retired_removed = _sweep_stale_install_dirs(target_dir, check=check)
        if not check:
            if dest_dir.is_dir() or dest_dir.is_symlink():
                _remove_install_dir(dest_dir)
            dest_dir.mkdir(parents=True, exist_ok=True)
            (dest_dir / "SKILL.md").write_text(stub_skill_text(), encoding="utf-8")
    except OSError as exc:
        raise SkillInstallError(target_dir, exc) from exc

    return InstallSkillsResult(
        retired_removed=retired_removed,
        legacy_dirs_detected=legacy,
    )


def stub_is_gitignored(target_dir: Path) -> bool:
    """Whether git ignores the installed stub under ``target_dir``.

    Outside a git repository, or without a git binary, there is nothing to
    commit, so nothing is ignored.
    """
    rel_path = f"{INSTALL_SKILL_NAME}/SKILL.md"
    if not (target_dir / rel_path).is_file() or find_repo_root(target_dir) is None:
        return False
    try:
        proc = subprocess.run(
            ["git", "check-ignore", "-q", rel_path],
            cwd=target_dir,
            capture_output=True,
            text=True,
            check=False,
        )
    except FileNotFoundError:
        return False
    # 0: ignored; 1: not ignored.
    if proc.returncode not in (0, 1):
        raise RuntimeError(f"git check-ignore failed: {proc.stderr.strip()}")
    return proc.returncode == 0
