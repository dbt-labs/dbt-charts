"""Tests for dbt_charts.agent_api.skill_install."""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

import pytest

from dbt_charts.agent_api.skill_install import (
    INSTALL_SKILL_NAME,
    PRE_NAMESPACE_SKILL_NAMES,
    SkillInstallError,
    detect_global_skill_targets,
    detect_legacy_skill_dirs,
    detect_skill_targets,
    install_skills,
    stub_is_gitignored,
    stub_skill_text,
    target_dir_for,
)
from dbt_charts.agent_api.skills import all_skill_names

_AS_ROOT = hasattr(os, "geteuid") and os.geteuid() == 0
_needs_non_root = pytest.mark.skipif(
    _AS_ROOT, reason="root bypasses directory permission bits"
)


@pytest.fixture
def install_root(tmp_path: Path) -> Path:
    root = tmp_path / "project"
    root.mkdir()
    (root / "dbt_charts.yml").write_text("name: test\n")
    return root


def _write_wheel_authored(target: Path, name: str) -> None:
    (target / name).mkdir(parents=True)
    (target / name / "SKILL.md").write_text(
        f"---\nname: {name}\nmetadata:\n  author: fivetran\n---\nstale\n"
    )


def test_stub_is_a_versionless_pointer_at_the_cli() -> None:
    """The installed file is a pointer, never a copy of a wheel skill: no
    version to go stale, no surface macro to render, and the ownership marker
    the sweep greps for."""
    text = stub_skill_text()
    assert text.startswith("---\n")
    assert f"name: {INSTALL_SKILL_NAME}\n" in text
    assert "author: fivetran" in text
    assert "version" not in text.split("---", 2)[1]
    assert "{{ s_" not in text
    assert "dct skills chart" in text
    assert "dct skills\n" in text or "`dct skills`" in text


def test_install_writes_one_stub_dir(install_root: Path) -> None:
    target = install_root / ".agents/skills"
    install_skills(target_dir=target, project_root=install_root)

    skill_md = target / INSTALL_SKILL_NAME / "SKILL.md"
    assert skill_md.read_text(encoding="utf-8") == stub_skill_text()
    assert [p.name for p in target.iterdir()] == [INSTALL_SKILL_NAME]


def test_install_removes_the_seven_rendered_copies(install_root: Path) -> None:
    """A project that installed the previous release's `dct-*` copies gets
    them swept on the next install, leaving the stub alone."""
    target = install_root / ".agents/skills"
    for name in all_skill_names():
        _write_wheel_authored(target, f"dct-{name}")

    result = install_skills(target_dir=target, project_root=install_root)

    assert set(result.retired_removed) == {f"dct-{n}" for n in all_skill_names()}
    assert [p.name for p in target.iterdir()] == [INSTALL_SKILL_NAME]


def test_install_removes_pre_namespace_bare_skill_dirs(install_root: Path) -> None:
    target = install_root / ".agents/skills"
    stale = PRE_NAMESPACE_SKILL_NAMES[0]
    _write_wheel_authored(target, stale)

    result = install_skills(target_dir=target, project_root=install_root)

    assert stale in result.retired_removed
    assert not (target / stale).exists()


def test_install_keeps_a_user_authored_dir_that_shares_a_pre_namespace_name(
    install_root: Path,
) -> None:
    target = install_root / ".agents/skills"
    stale = PRE_NAMESPACE_SKILL_NAMES[0]
    (target / stale).mkdir(parents=True)
    (target / stale / "SKILL.md").write_text("---\nname: analyst-runbook\n---\nmine\n")

    result = install_skills(target_dir=target, project_root=install_root)

    assert result.retired_removed == []
    assert (
        target / stale / "SKILL.md"
    ).read_text() == "---\nname: analyst-runbook\n---\nmine\n"


def test_install_sweep_removes_a_symlinked_stale_dct_dir_without_crashing(
    install_root: Path,
) -> None:
    """`shutil.rmtree` refuses to follow a symlink and raises `OSError`. A
    symlinked `dct-*` install dir must not crash the sweep and leave the
    dirs sorted after it unswept."""
    target = install_root / ".agents/skills"
    target.mkdir(parents=True)

    real_dir = install_root / "elsewhere" / "dct-symlinked-thing"
    real_dir.mkdir(parents=True)
    (real_dir / "SKILL.md").write_text(
        "---\nname: dct-symlinked-thing\nmetadata:\n  author: fivetran\n---\nstale\n"
    )
    (target / "dct-symlinked-thing").symlink_to(real_dir, target_is_directory=True)
    _write_wheel_authored(target, "dct-zzz-also-stale")

    result = install_skills(target_dir=target, project_root=install_root)

    assert set(result.retired_removed) == {"dct-symlinked-thing", "dct-zzz-also-stale"}
    assert not (target / "dct-symlinked-thing").exists()
    assert not (target / "dct-zzz-also-stale").exists()
    assert real_dir.exists(), "unlinking the symlink must not touch its target"


def test_install_check_reports_retired_removed_without_deleting(
    install_root: Path,
) -> None:
    target = install_root / ".agents/skills"
    _write_wheel_authored(target, "dct-chart")

    result = install_skills(target_dir=target, project_root=install_root, check=True)

    assert result.retired_removed == ["dct-chart"]
    assert (target / "dct-chart").exists()
    assert not (target / INSTALL_SKILL_NAME).exists()


def test_install_keeps_a_non_wheel_authored_dct_dir(install_root: Path) -> None:
    """A `dct-*` dir without our author line is never ours to sweep."""
    target = install_root / ".agents/skills"
    (target / "dct-someone-elses-thing").mkdir(parents=True)
    (target / "dct-someone-elses-thing" / "SKILL.md").write_text(
        "---\nname: dct-someone-elses-thing\n---\nmine\n"
    )

    result = install_skills(target_dir=target, project_root=install_root)

    assert result.retired_removed == []
    assert (target / "dct-someone-elses-thing").exists()


def test_install_preserves_user_authored_skill(install_root: Path) -> None:
    target = install_root / ".agents/skills"
    custom = target / "my-company-playbook"
    custom.mkdir(parents=True)
    custom_md = custom / "SKILL.md"
    custom_md.write_text(
        "---\nname: my-company-playbook\ndescription: x\nkind: workflow\n---\nbody\n"
    )

    install_skills(target_dir=target, project_root=install_root)

    assert custom_md.read_text(encoding="utf-8") == (
        "---\nname: my-company-playbook\ndescription: x\nkind: workflow\n---\nbody\n"
    )


def test_install_check_writes_nothing(install_root: Path) -> None:
    target = install_root / ".agents/skills"
    install_skills(target_dir=target, project_root=install_root, check=True)

    assert not target.exists()


@pytest.mark.parametrize("check", [False, True])
def test_install_refuses_a_user_authored_dct_dir(
    install_root: Path, check: bool
) -> None:
    """A `dct/` that is not ours is never overwritten, and a dry run says so
    rather than reporting an install that would destroy it."""
    target = install_root / ".agents/skills"
    mine = target / INSTALL_SKILL_NAME
    mine.mkdir(parents=True)
    (mine / "SKILL.md").write_text("---\nname: dct\n---\nmy notes\n")

    with pytest.raises(SkillInstallError, match="not ours"):
        install_skills(target_dir=target, project_root=install_root, check=check)

    assert (mine / "SKILL.md").read_text() == "---\nname: dct\n---\nmy notes\n"


def test_install_overwrites_an_edited_stub(install_root: Path) -> None:
    """A second install restores a stub we wrote, with no `--force` flag:
    there is none. Our author line is what marks it as ours to overwrite."""
    target = install_root / ".agents/skills"
    install_skills(target_dir=target, project_root=install_root)
    skill_md = target / INSTALL_SKILL_NAME / "SKILL.md"
    skill_md.write_text(stub_skill_text() + "\nlocal edit\n")

    install_skills(target_dir=target, project_root=install_root)

    assert skill_md.read_text(encoding="utf-8") == stub_skill_text()


def test_detect_skill_targets_agents_and_claude(install_root: Path) -> None:
    (install_root / "AGENTS.md").write_text("# agents\n")
    (install_root / "CLAUDE.md").write_text("@AGENTS.md\n")
    assert detect_skill_targets(install_root) == ["agents", "claude"]


def test_detect_legacy_skill_dirs(install_root: Path) -> None:
    wheel_names = all_skill_names()
    legacy_dir = install_root / ".cursor/skills" / "chart"
    legacy_dir.mkdir(parents=True)
    (legacy_dir / "SKILL.md").write_text("old\n")

    found = detect_legacy_skill_dirs(install_root, wheel_names)

    assert legacy_dir in found


def test_target_dir_for_aliases() -> None:
    root = Path("/proj")
    assert target_dir_for("agents", project_root=root) == root / ".agents/skills"
    assert target_dir_for("codex", project_root=root) == root / ".agents/skills"
    assert target_dir_for("claude", project_root=root) == root / ".claude/skills"


def test_detect_global_skill_targets_claude_only(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("HOME", str(tmp_path))
    (tmp_path / ".claude").mkdir()

    assert detect_global_skill_targets() == [tmp_path / ".claude" / "skills"]


def test_detect_global_skill_targets_agents_only(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("HOME", str(tmp_path))
    (tmp_path / ".agents").mkdir()

    assert detect_global_skill_targets() == [tmp_path / ".agents" / "skills"]


def test_detect_global_skill_targets_codex_signals_agents(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("HOME", str(tmp_path))
    (tmp_path / ".codex").mkdir()

    assert detect_global_skill_targets() == [tmp_path / ".agents" / "skills"]


def test_detect_global_skill_targets_none(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("HOME", str(tmp_path))

    assert detect_global_skill_targets() == []


@_needs_non_root
def test_install_unwritable_target_raises_named_error(install_root: Path) -> None:
    locked_parent = install_root / ".agents"
    locked_parent.mkdir()
    target = locked_parent / "skills"
    locked_parent.chmod(0o555)
    try:
        with pytest.raises(SkillInstallError, match=str(target)):
            install_skills(target_dir=target, project_root=install_root)
    finally:
        locked_parent.chmod(0o755)


@_needs_non_root
def test_reinstall_into_a_read_only_target_raises_the_install_error(
    install_root: Path,
) -> None:
    """The second run is the common one: the target already holds an install,
    so `mkdir(exist_ok=True)` succeeds and the failure comes from replacing it."""
    target = install_root / ".agents" / "skills"
    install_skills(target_dir=target, project_root=install_root)
    target.chmod(0o555)
    try:
        with pytest.raises(SkillInstallError, match=str(target)):
            install_skills(target_dir=target, project_root=install_root)
    finally:
        target.chmod(0o755)


def test_stub_is_gitignored_when_a_rule_covers_its_directory(
    install_root: Path,
) -> None:
    """A `.claude/` rule meant for personal settings also drops the stub from
    every commit, which defeats its purpose."""
    subprocess.run(["git", "init", "-q", str(install_root)], check=True)
    (install_root / ".gitignore").write_text(".claude/\n", encoding="utf-8")
    target = install_root / ".claude" / "skills"
    install_skills(target_dir=target, project_root=install_root)

    assert stub_is_gitignored(target)


def test_stub_is_not_gitignored_by_default(install_root: Path) -> None:
    subprocess.run(["git", "init", "-q", str(install_root)], check=True)
    target = install_root / ".claude" / "skills"
    install_skills(target_dir=target, project_root=install_root)

    assert not stub_is_gitignored(target)


def test_stub_is_gitignored_is_false_outside_a_repository(
    install_root: Path,
) -> None:
    target = install_root / ".claude" / "skills"
    install_skills(target_dir=target, project_root=install_root)

    assert not stub_is_gitignored(target)


def test_stub_is_gitignored_is_false_without_git_on_path(
    install_root: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """With no git binary, git ignores nothing: the install must not crash."""
    target = install_root / ".claude" / "skills"
    install_skills(target_dir=target, project_root=install_root)
    empty_bin = tmp_path / "empty-bin"
    empty_bin.mkdir()
    monkeypatch.setenv("PATH", str(empty_bin))

    assert not stub_is_gitignored(target)


def test_stub_is_gitignored_raises_when_git_fails_inside_a_repository(
    install_root: Path,
) -> None:
    """Inside a repository, a git failure (here a corrupt `.git`) is an
    error, never reported as "not ignored"."""
    (install_root / ".git").mkdir()
    target = install_root / ".claude" / "skills"
    install_skills(target_dir=target, project_root=install_root)

    with pytest.raises(RuntimeError, match="git check-ignore failed"):
        stub_is_gitignored(target)
