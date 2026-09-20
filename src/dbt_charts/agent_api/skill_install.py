"""File-based install of wheel workflow skills into agent skill directories."""

from __future__ import annotations

import shutil
from collections.abc import Set
from pathlib import Path

import yaml
from pydantic import BaseModel, ConfigDict, Field

from dbt_charts.agent_api.skill_render import render_skill_body
from dbt_charts.agent_api.skills import (
    INSTALL_NAME_PREFIX,
    Skill,
    all_skill_names,
    list_skills,
)

# Bare (pre-`dct-` prefix) directory names from the release just before this
# prefix landed: the 11 install-set skills that wrote bare, plus the 10
# pattern skills retired before this prefix existed. Not exhaustive back
# through every earlier rebrand, only this prefix's own predecessor names.
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

    installed: list[str] = Field(default_factory=list)
    retired_removed: list[str] = Field(default_factory=list)
    legacy_dirs_detected: list[Path] = Field(default_factory=list)


def skills_for_file_install() -> list[Skill]:
    """Workflow skills exposed on the CLI surface.

    All file-backed by construction: no ``project``/``extra_skills`` is passed,
    so only wheel built-ins are listed. Returned bodies/descriptions are
    ``cli``-rendered, same as every other CLI reader. ``_rendered_skill_md``
    re-reads and re-renders each one for the ``install`` surface before
    writing it to disk, so nothing here needs to be install-specific.
    """
    return sorted(
        (s for s in list_skills(surface="cli").skills if s.kind == "workflow"),
        key=lambda s: s.name,
    )


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


def prefixed_install_name(name: str) -> str:
    """The on-disk, namespaced directory/frontmatter name for a file-installed
    skill. Registry name stays bare; this prefix exists only here, on the
    install surface."""
    return f"{INSTALL_NAME_PREFIX}{name}"


class _FoldedDumper(yaml.SafeDumper):
    """safe_dump variant that folds long or multi-line strings with `>`
    instead of quoting them, so a round-tripped `description:` reads like
    the authored source rather than a flow scalar full of doubled quotes."""


def _represent_str(dumper: yaml.SafeDumper, data: str) -> yaml.ScalarNode:
    style = ">" if len(data) > 80 or "\n" in data else None
    return dumper.represent_scalar("tag:yaml.org,2002:str", data, style=style)


_FoldedDumper.add_representer(str, _represent_str)


def _rendered_skill_md(skill: Skill) -> str:
    # Narrowing only — skills_for_file_install lists built-ins, which always
    # have a directory; a file-less one would raise on the `/` below anyway.
    assert skill.directory is not None
    raw = (skill.directory / "SKILL.md").read_text(encoding="utf-8")
    if not raw.startswith("---"):
        raise ValueError(f"{skill.directory / 'SKILL.md'}: missing frontmatter")
    parts = raw.split("---", 2)
    if len(parts) < 3:
        raise ValueError(f"{skill.directory / 'SKILL.md'}: malformed frontmatter")
    # Re-read the raw frontmatter rather than reuse skill.description: `skill`
    # is cli-rendered (bare sibling names), but this is the install surface.
    # Sibling names must come out dct-prefixed, so the render pass below has
    # to see the unrendered `{{ s_ }}` macros, not already-expanded text.
    frontmatter = yaml.safe_load(parts[1]) or {}
    raw_description = frontmatter.get("description", "").strip()
    frontmatter["name"] = prefixed_install_name(skill.name)
    frontmatter["description"] = render_skill_body(raw_description, surface="install")
    rendered_frontmatter = yaml.dump(
        frontmatter, Dumper=_FoldedDumper, sort_keys=False, allow_unicode=True
    )
    rendered_body = render_skill_body(parts[2].lstrip("\n"), surface="install")
    return f"---\n{rendered_frontmatter}---\n{rendered_body}"


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
    """Report, and unless ``check``, remove a file-installed skill
    directory that no longer belongs: a ``dct-*`` dir the wheel doesn't
    currently install (an old release retired it) found by globbing the
    namespace, or a bare pre-namespace dir from before this prefix existed
    found by walking the fixed ``PRE_NAMESPACE_SKILL_NAMES`` list. Both paths
    only ever touch a directory carrying our own wheel-authored SKILL.md.
    """
    current_install_names = {
        prefixed_install_name(s.name) for s in skills_for_file_install()
    }
    removed: list[str] = []
    for candidate in sorted(target_dir.glob(f"{INSTALL_NAME_PREFIX}*")):
        if candidate.name in current_install_names:
            continue
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
    """Install CLI-rendered workflow skills into ``target_dir``, unconditionally
    overwriting an existing ``dct-*`` install (there is no ``--force`` flag).
    ``check=True`` reports what a real install would do without writing or
    deleting anything.
    """
    wheel_names = all_skill_names()
    legacy = detect_legacy_skill_dirs(project_root, wheel_names)
    installed: list[str] = []

    try:
        if not check:
            target_dir.mkdir(parents=True, exist_ok=True)
        retired_removed = _sweep_stale_install_dirs(target_dir, check=check)

        for skill in skills_for_file_install():
            install_name = prefixed_install_name(skill.name)
            dest_dir = target_dir / install_name
            dest_md = dest_dir / "SKILL.md"
            content = _rendered_skill_md(skill)
            if check:
                installed.append(install_name)
                continue
            if dest_dir.is_dir() or dest_dir.is_symlink():
                _remove_install_dir(dest_dir)
            dest_dir.mkdir(parents=True, exist_ok=True)
            dest_md.write_text(content, encoding="utf-8")
            installed.append(install_name)
    except OSError as exc:
        raise SkillInstallError(target_dir, exc) from exc

    return InstallSkillsResult(
        installed=installed,
        retired_removed=retired_removed,
        legacy_dirs_detected=legacy,
    )
