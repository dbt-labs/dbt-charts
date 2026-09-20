"""Tests for dbt_charts.agent_api.skill_install."""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from dbt_charts.agent_api.skill_install import (
    PRE_NAMESPACE_SKILL_NAMES,
    SkillInstallError,
    detect_global_skill_targets,
    detect_legacy_skill_dirs,
    detect_skill_targets,
    install_skills,
    skills_for_file_install,
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


def test_skills_for_file_install_excludes_patterns() -> None:
    names = {s.name for s in skills_for_file_install()}
    assert "board-build" in names
    assert "mcp-setup" in names
    assert "kpi-row" not in names
    assert all(s.kind == "workflow" for s in skills_for_file_install())


def test_install_renders_cli_surface(install_root: Path) -> None:
    target = install_root / ".agents/skills"
    result = install_skills(target_dir=target, project_root=install_root)

    assert "dct-board-build" in result.installed
    skill_md = target / "dct-board-build" / "SKILL.md"
    assert skill_md.exists()
    text = skill_md.read_text(encoding="utf-8")
    assert "{{ s_" not in text
    assert "dct validate" in text or "dct check" in text


def test_install_renders_sibling_names_dct_prefixed(install_root: Path) -> None:
    """board-build's frontmatter description names board-design and
    troubleshooting as siblings via the `{{ s_skill_name_* }}` macro. The
    file-installed copy must render the on-disk, dct-prefixed name a local
    agent will actually find next to it, not the bare registry name."""
    target = install_root / ".agents/skills"
    install_skills(target_dir=target, project_root=install_root)

    text = (target / "dct-board-build" / "SKILL.md").read_text(encoding="utf-8")
    assert "dct-board-design" in text
    assert "dct-troubleshooting" in text


def test_install_keeps_folded_description_block_scalar(install_root: Path) -> None:
    """The authored `description: >` folded block scalar must survive the
    safe_load/safe_dump round-trip, not collapse into a quoted flow scalar
    with doubled apostrophes."""
    target = install_root / ".agents/skills"
    install_skills(target_dir=target, project_root=install_root)

    text = (target / "dct-board-build" / "SKILL.md").read_text(encoding="utf-8")
    frontmatter = text.split("---", 2)[1]
    assert "description: >" in frontmatter
    assert "''" not in frontmatter


def test_installed_skill_md_carries_the_literal_ownership_marker(
    install_root: Path,
) -> None:
    """`_is_wheel_authored` greps an installed SKILL.md for the literal
    string `author: fivetran`. A frontmatter dumper change that reformats
    the metadata block (quoting, folding, key order) could silently break
    the sweep while every other install test stays green; this pins the
    exact on-disk text the sweep depends on."""
    target = install_root / ".agents/skills"
    install_skills(target_dir=target, project_root=install_root)

    text = (target / "dct-board-build" / "SKILL.md").read_text(encoding="utf-8")
    assert "author: fivetran" in text


def test_install_removes_pre_namespace_bare_skill_dirs(install_root: Path) -> None:
    """A bare, pre-`dct-` directory from an older wheel (either a retired
    pattern skill or one of the 11 install-set skills that used to write
    bare) is swept on the next install."""
    target = install_root / ".agents/skills"
    stale = PRE_NAMESPACE_SKILL_NAMES[0]
    (target / stale).mkdir(parents=True)
    (target / stale / "SKILL.md").write_text(
        "---\nname: kpi-row\nmetadata:\n  author: fivetran\n---\nstale\n"
    )

    result = install_skills(target_dir=target, project_root=install_root)

    assert stale in result.retired_removed
    assert not (target / stale).exists()


def test_install_keeps_a_user_authored_dir_that_shares_a_pre_namespace_name(
    install_root: Path,
) -> None:
    target = install_root / ".agents/skills"
    stale = PRE_NAMESPACE_SKILL_NAMES[0]
    (target / stale).mkdir(parents=True)
    (target / stale / "SKILL.md").write_text("---\nname: kpi-row\n---\nmine\n")

    result = install_skills(target_dir=target, project_root=install_root)

    assert result.retired_removed == []
    assert (
        target / stale / "SKILL.md"
    ).read_text() == "---\nname: kpi-row\n---\nmine\n"


def test_install_removes_stale_wheel_authored_dct_dir(install_root: Path) -> None:
    """A `dct-*` dir the wheel wrote in a past release, but no longer
    installs, is swept — the namespace itself drives removal, no hand-kept
    retirement list."""
    target = install_root / ".agents/skills"
    (target / "dct-old-retired-thing").mkdir(parents=True)
    (target / "dct-old-retired-thing" / "SKILL.md").write_text(
        "---\nname: dct-old-retired-thing\nmetadata:\n  author: fivetran\n---\nstale\n"
    )

    result = install_skills(target_dir=target, project_root=install_root)

    assert "dct-old-retired-thing" in result.retired_removed
    assert not (target / "dct-old-retired-thing").exists()


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

    (target / "dct-zzz-also-stale").mkdir(parents=True)
    (target / "dct-zzz-also-stale" / "SKILL.md").write_text(
        "---\nname: dct-zzz-also-stale\nmetadata:\n  author: fivetran\n---\nstale\n"
    )

    result = install_skills(target_dir=target, project_root=install_root)

    assert "dct-symlinked-thing" in result.retired_removed
    assert "dct-zzz-also-stale" in result.retired_removed
    assert not (target / "dct-symlinked-thing").exists()
    assert not (target / "dct-zzz-also-stale").exists()
    assert real_dir.exists(), "unlinking the symlink must not touch its target"


def test_install_check_reports_retired_removed_without_deleting(
    install_root: Path,
) -> None:
    """`--check` must report the same stale-directory sweep a real install
    performs, without deleting anything. The dry run's whole point is
    previewing the destructive half of the operation."""
    target = install_root / ".agents/skills"
    stale = PRE_NAMESPACE_SKILL_NAMES[0]
    (target / stale).mkdir(parents=True)
    (target / stale / "SKILL.md").write_text(
        "---\nname: kpi-row\nmetadata:\n  author: fivetran\n---\nstale\n"
    )

    result = install_skills(target_dir=target, project_root=install_root, check=True)

    assert stale in result.retired_removed
    assert (target / stale).exists()


def test_install_keeps_a_non_wheel_authored_dct_dir(install_root: Path) -> None:
    """A `dct-*` dir without our author line is never ours to sweep, even if
    its name isn't in the current install set."""
    target = install_root / ".agents/skills"
    (target / "dct-someone-elses-thing").mkdir(parents=True)
    (target / "dct-someone-elses-thing" / "SKILL.md").write_text(
        "---\nname: dct-someone-elses-thing\n---\nmine\n"
    )

    result = install_skills(target_dir=target, project_root=install_root)

    assert result.retired_removed == []
    assert (target / "dct-someone-elses-thing").exists()


def test_install_does_not_sweep_mcp_setup_or_troubleshooting_as_stale(
    install_root: Path,
) -> None:
    """dct-mcp-setup and dct-troubleshooting are current install names — the
    sweep must not treat them as leftover."""
    target = install_root / ".agents/skills"
    result = install_skills(target_dir=target, project_root=install_root)

    assert "dct-mcp-setup" not in result.retired_removed
    assert "dct-troubleshooting" not in result.retired_removed
    assert (target / "dct-mcp-setup" / "SKILL.md").exists()
    assert (target / "dct-troubleshooting" / "SKILL.md").exists()


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
    result = install_skills(target_dir=target, project_root=install_root, check=True)

    assert result.installed
    assert not target.exists()


def test_install_writes_prefixed_directory_and_frontmatter_name(
    install_root: Path,
) -> None:
    target = install_root / ".agents/skills"
    result = install_skills(target_dir=target, project_root=install_root)

    assert "dct-board-build" in result.installed
    assert "board-build" not in result.installed
    skill_md = target / "dct-board-build" / "SKILL.md"
    assert skill_md.is_file()
    assert "name: dct-board-build" in skill_md.read_text(encoding="utf-8")


def test_install_check_reports_same_names_as_real_install(install_root: Path) -> None:
    target = install_root / ".agents/skills"
    check_result = install_skills(
        target_dir=target, project_root=install_root, check=True
    )
    real_result = install_skills(target_dir=target, project_root=install_root)

    assert sorted(check_result.installed) == sorted(real_result.installed)


def test_install_overwrites_existing_with_no_flag(install_root: Path) -> None:
    """A second install overwrites an existing `dct-*` directory with no
    `--force` flag: there is none."""
    target = install_root / ".agents/skills"
    install_skills(target_dir=target, project_root=install_root)
    skill_md = target / "dct-board-build" / "SKILL.md"
    skill_md.write_text("corrupt\n")

    result = install_skills(target_dir=target, project_root=install_root)
    assert "dct-board-build" in result.installed
    assert "{{ s_" not in skill_md.read_text(encoding="utf-8")


def test_detect_skill_targets_agents_and_claude(install_root: Path) -> None:
    (install_root / "AGENTS.md").write_text("# agents\n")
    (install_root / "CLAUDE.md").write_text("@AGENTS.md\n")
    assert detect_skill_targets(install_root) == ["agents", "claude"]


def test_detect_legacy_skill_dirs(install_root: Path) -> None:
    wheel_names = all_skill_names()
    legacy_dir = install_root / ".cursor/skills" / "board-build"
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
    """An unwritable target dir (e.g. a sandboxed agent's read-only `.agents/`)
    must surface a clean, named error instead of a raw PermissionError
    traceback."""
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
