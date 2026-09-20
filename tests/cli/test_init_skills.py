"""Tests for `dct init skills`."""

from __future__ import annotations

import os
from pathlib import Path

import pytest
from typer.testing import CliRunner

from dbt_charts.agent_api.skill_install import PRE_NAMESPACE_SKILL_NAMES
from dbt_charts.cli import main as cli_main

runner = CliRunner()

_AS_ROOT = hasattr(os, "geteuid") and os.geteuid() == 0
_needs_non_root = pytest.mark.skipif(
    _AS_ROOT, reason="root bypasses directory permission bits"
)


def _seed_project() -> None:
    Path("dbt_charts.yml").write_text("name: test\n", encoding="utf-8")


def test_init_skills_agents_writes_rendered_workflows(tmp_path: Path) -> None:
    with runner.isolated_filesystem(temp_dir=tmp_path):
        _seed_project()
        result = runner.invoke(cli_main.app, ["init", "skills", "agents"])

        assert result.exit_code == 0, result.output
        skill_md = Path(".agents/skills/dct-board-build/SKILL.md")
        assert skill_md.exists()
        text = skill_md.read_text(encoding="utf-8")
        assert "{{ s_" not in text
        assert not Path(".cursor/skills").exists()
        assert not Path(".codex/skills").exists()
        assert not (Path(".agents/skills") / "kpi-row").exists()


def test_init_skills_claude_target(tmp_path: Path) -> None:
    with runner.isolated_filesystem(temp_dir=tmp_path):
        _seed_project()
        result = runner.invoke(cli_main.app, ["init", "skills", "claude"])

        assert result.exit_code == 0, result.output
        assert Path(".claude/skills/dct-board-build/SKILL.md").exists()


def test_init_skills_project_dir_stays_in_subproject(tmp_path: Path) -> None:
    """--project-dir names the install destination; the enclosing git root
    (a sibling walk unrelated to the named project) must not steal it."""
    git_root = tmp_path / "repo"
    git_root.mkdir()
    (git_root / ".git").mkdir()
    subproject = git_root / "sub"
    subproject.mkdir()
    (subproject / "dbt_charts.yml").write_text("name: test\n", encoding="utf-8")

    result = runner.invoke(
        cli_main.app,
        ["init", "skills", "claude", "--project-dir", str(subproject)],
    )

    assert result.exit_code == 0, result.output
    assert (subproject / ".claude/skills/dct-board-build/SKILL.md").exists()
    assert not (git_root / ".claude").exists()


def test_init_skills_project_dir_without_marker_errors_with_tip(
    tmp_path: Path,
) -> None:
    """--project-dir naming a directory with no project marker must error and
    hint at the enclosing project, not silently install there instead."""
    git_root = tmp_path / "repo"
    git_root.mkdir()
    (git_root / ".git").mkdir()
    (git_root / "dbt_project.yml").write_text("name: test\n", encoding="utf-8")
    newproj = git_root / "newproj"
    newproj.mkdir()

    result = runner.invoke(
        cli_main.app,
        ["init", "skills", "claude", "--project-dir", str(newproj)],
    )

    assert result.exit_code == 1, result.output
    assert "does not contain a dbt charts" in result.output
    assert f"did you mean {git_root}" in result.output
    assert not (git_root / ".claude").exists()
    assert not (newproj / ".claude").exists()


def test_init_skills_project_dir_without_marker_anywhere_errors_with_recovery(
    tmp_path: Path,
) -> None:
    """With no project anywhere above the named directory there is nothing to
    suggest, so the error must still carry a way out. This is the arm a user
    hits bootstrapping a repo that has no dbt project yet."""
    bare = tmp_path / "bare"
    bare.mkdir()

    result = runner.invoke(
        cli_main.app, ["init", "skills", "claude", "--project-dir", str(bare)]
    )

    assert result.exit_code == 1, result.output
    assert "does not contain a dbt charts" in result.output
    assert "did you mean" not in result.output
    assert "Re-run without --project-dir" in result.output
    assert not (bare / ".claude").exists()


def test_init_skills_dct_project_dir_env_var_walks_to_git_root(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """DCT_PROJECT_DIR is documented as a default, not a typed flag: it must
    not take the explicit-`--project-dir` path and relocate skills into the
    nested project instead of the git root."""
    git_root = tmp_path / "repo"
    git_root.mkdir()
    (git_root / ".git").mkdir()
    subproject = git_root / "sub"
    subproject.mkdir()
    (subproject / "dbt_charts.yml").write_text("name: test\n", encoding="utf-8")
    monkeypatch.chdir(subproject)
    monkeypatch.setenv("DCT_PROJECT_DIR", ".")

    result = runner.invoke(cli_main.app, ["init", "skills", "claude"])

    assert result.exit_code == 0, result.output
    assert (git_root / ".claude/skills/dct-board-build/SKILL.md").exists()
    assert not (subproject / ".claude").exists()


def test_init_skills_dct_project_dir_env_var_in_fresh_repo_still_bootstraps(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A fresh repo with no project marker is the documented bootstrap path
    for a bare `dct init skills`; DCT_PROJECT_DIR being set must not turn that
    into a hard error blaming a flag the user never typed."""
    with runner.isolated_filesystem(temp_dir=tmp_path):
        monkeypatch.setenv("DCT_PROJECT_DIR", ".")
        result = runner.invoke(cli_main.app, ["init", "skills"])

        assert result.exit_code == 0, result.output
        assert Path(".agents/skills/dct-board-build/SKILL.md").exists()


def test_init_skills_all_with_both_markers(tmp_path: Path) -> None:
    with runner.isolated_filesystem(temp_dir=tmp_path):
        _seed_project()
        Path("AGENTS.md").write_text("# agents\n", encoding="utf-8")
        Path("CLAUDE.md").write_text("@AGENTS.md\n", encoding="utf-8")
        result = runner.invoke(cli_main.app, ["init", "skills", "--all"])

        assert result.exit_code == 0, result.output
        assert Path(".agents/skills/dct-board-build/SKILL.md").exists()
        assert Path(".claude/skills/dct-board-build/SKILL.md").exists()


def test_init_skills_prints_a_gitignore_hint(tmp_path: Path) -> None:
    with runner.isolated_filesystem(temp_dir=tmp_path):
        _seed_project()
        result = runner.invoke(cli_main.app, ["init", "skills", "claude"])

        assert result.exit_code == 0, result.output
        assert ".claude/skills/dct-*/" in result.output


def test_init_skills_check_dry_run(tmp_path: Path) -> None:
    with runner.isolated_filesystem(temp_dir=tmp_path):
        _seed_project()
        result = runner.invoke(cli_main.app, ["init", "skills", "agents", "--check"])

        assert result.exit_code == 0, result.output
        assert "Dry run" in result.output
        assert not Path(".agents/skills").exists()


def test_init_skills_check_reports_would_remove_retired_without_deleting(
    tmp_path: Path,
) -> None:
    """`--check` must speak in future tense about a deletion it did not
    perform — "Removed retired" on a dry run claims work that never
    happened."""
    with runner.isolated_filesystem(temp_dir=tmp_path):
        _seed_project()
        stale = Path(".agents/skills") / PRE_NAMESPACE_SKILL_NAMES[0]
        stale.mkdir(parents=True)
        (stale / "SKILL.md").write_text(
            "---\nname: stale-skill\nmetadata:\n  author: fivetran\n---\nstale\n",
            encoding="utf-8",
        )

        result = runner.invoke(cli_main.app, ["init", "skills", "agents", "--check"])

        assert result.exit_code == 0, result.output
        assert "Would remove retired" in result.output
        assert "Removed retired" not in result.output
        assert stale.exists()


def test_init_skills_check_prints_no_gitignore_hint(tmp_path: Path) -> None:
    """A dry run writes nothing to gitignore: the hint would tell the user
    to gitignore a directory that doesn't exist."""
    with runner.isolated_filesystem(temp_dir=tmp_path):
        _seed_project()
        result = runner.invoke(cli_main.app, ["init", "skills", "agents", "--check"])

        assert result.exit_code == 0, result.output
        assert "Gitignore" not in result.output


def test_init_skills_no_markers_defaults_to_agents(tmp_path: Path) -> None:
    """FR-69: bare `dct init skills` in a fresh repo (no .cursor/, AGENTS.md,
    CLAUDE.md) must install to the tool-agnostic `agents` target rather than
    erroring — a fresh project legitimately has none of those markers yet, and
    the docs tell users to run this command bare."""
    with runner.isolated_filesystem(temp_dir=tmp_path):
        _seed_project()
        result = runner.invoke(cli_main.app, ["init", "skills"])

        assert result.exit_code == 0, result.output
        assert Path(".agents/skills/dct-board-build/SKILL.md").exists()
        assert not Path(".claude/skills").exists()


def test_init_skills_all_with_no_markers_still_errors(tmp_path: Path) -> None:
    """`--all` keeps its own contract: install to every *detected* target, so
    it still errors with no markers rather than silently falling back to one."""
    with runner.isolated_filesystem(temp_dir=tmp_path):
        _seed_project()
        result = runner.invoke(cli_main.app, ["init", "skills", "--all"])

        assert result.exit_code == 1, result.output
        assert "No agent skill targets detected" in result.output


def test_init_skills_dir_override(tmp_path: Path) -> None:
    """Outside a git repo there is no enclosing root, so the repo context falls
    back to the project: paths stay project-relative rather than collapsing to
    ".", and a legacy install beside the project is still reported."""
    with runner.isolated_filesystem(temp_dir=tmp_path):
        _seed_project()
        legacy_dir = Path(".cursor/skills/board-build")
        legacy_dir.mkdir(parents=True)
        (legacy_dir / "SKILL.md").write_text("stub\n", encoding="utf-8")

        result = runner.invoke(
            cli_main.app, ["init", "skills", "--dir", "custom-skills"]
        )

        assert result.exit_code == 0, result.output
        assert Path("custom-skills/dct-board-build/SKILL.md").exists()
        assert "→ custom-skills/" in result.output
        assert "Gitignore: custom-skills/dct-*/" in result.output
        assert (
            "Legacy skill install detected at .cursor/skills/board-build"
            in result.output
        )


def test_init_skills_dir_override_ignores_marker_less_project_dir(
    tmp_path: Path,
) -> None:
    """--dir names the destination outright; --project-dir determines nothing
    about it in this mode, so a marker-less --project-dir must not block the
    install."""
    with runner.isolated_filesystem(temp_dir=tmp_path):
        Path("newproj").mkdir()

        result = runner.invoke(
            cli_main.app,
            [
                "init",
                "skills",
                "--dir",
                "custom-skills",
                "--project-dir",
                "newproj",
            ],
        )

        assert result.exit_code == 0, result.output
        assert Path("custom-skills/dct-board-build/SKILL.md").exists()


def test_init_skills_dir_override_with_project_dir_keeps_git_root_context(
    tmp_path: Path,
) -> None:
    """--dir names the install destination outright, but --project-dir still
    picks the project context for display and legacy scanning -- that context
    must stay the git root, not the --project-dir target, or the printed
    Gitignore hint turns into a useless absolute path and a legacy install
    elsewhere in the repo goes unreported."""
    with runner.isolated_filesystem(temp_dir=tmp_path):
        Path(".git").mkdir()
        legacy_dir = Path(".cursor/skills/board-build")
        legacy_dir.mkdir(parents=True)
        (legacy_dir / "SKILL.md").write_text("stub\n", encoding="utf-8")
        sub = Path("sub")
        sub.mkdir()
        (sub / "dbt_charts.yml").write_text("name: test\n", encoding="utf-8")

        result = runner.invoke(
            cli_main.app,
            ["init", "skills", "--dir", "custom-skills", "--project-dir", "sub"],
        )

        assert result.exit_code == 0, result.output
        assert "Gitignore: custom-skills/dct-*/" in result.output
        assert (
            "Legacy skill install detected at .cursor/skills/board-build"
            in result.output
        )


def test_init_skills_explicit_project_dir_keeps_repo_context(
    tmp_path: Path,
) -> None:
    """An explicit --project-dir moves the install root, not the repository.
    Paths stay repo-relative so the printed Gitignore line works pasted at the
    repo root, and a legacy install elsewhere in the repo is still reported."""
    with runner.isolated_filesystem(temp_dir=tmp_path):
        Path(".git").mkdir()
        legacy_dir = Path(".cursor/skills/board-build")
        legacy_dir.mkdir(parents=True)
        (legacy_dir / "SKILL.md").write_text("stub\n", encoding="utf-8")
        sub = Path("sub")
        sub.mkdir()
        (sub / "dbt_charts.yml").write_text("name: test\n", encoding="utf-8")

        result = runner.invoke(
            cli_main.app, ["init", "skills", "claude", "--project-dir", "sub"]
        )

        assert result.exit_code == 0, result.output
        assert (sub / ".claude/skills/dct-board-build/SKILL.md").exists()
        assert not Path(".claude").exists()
        assert "Gitignore: sub/.claude/skills/dct-*/" in result.output
        assert (
            "Legacy skill install detected at .cursor/skills/board-build"
            in result.output
        )


def test_init_skills_global_installs_into_home_claude(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    home = tmp_path / "home"
    home.mkdir()
    (home / ".claude").mkdir()
    monkeypatch.setenv("HOME", str(home))

    with runner.isolated_filesystem(temp_dir=tmp_path):
        _seed_project()
        result = runner.invoke(cli_main.app, ["init", "skills", "--global"])

        assert result.exit_code == 0, result.output
        assert (home / ".claude/skills/dct-board-build/SKILL.md").exists()


def test_init_skills_global_prints_no_gitignore_hint(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A global install lands in the user's home directory, not a repo: a
    gitignore hint naming a repo-relative path is meaningless there."""
    home = tmp_path / "home"
    home.mkdir()
    (home / ".claude").mkdir()
    monkeypatch.setenv("HOME", str(home))

    with runner.isolated_filesystem(temp_dir=tmp_path):
        _seed_project()
        result = runner.invoke(cli_main.app, ["init", "skills", "--global"])

        assert result.exit_code == 0, result.output
        assert "Gitignore" not in result.output


def test_init_skills_global_installs_into_home_agents_via_codex(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    home = tmp_path / "home"
    home.mkdir()
    (home / ".codex").mkdir()
    monkeypatch.setenv("HOME", str(home))

    with runner.isolated_filesystem(temp_dir=tmp_path):
        _seed_project()
        result = runner.invoke(cli_main.app, ["init", "skills", "--global"])

        assert result.exit_code == 0, result.output
        assert (home / ".agents/skills/dct-board-build/SKILL.md").exists()


def test_init_skills_global_no_markers_errors(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))

    with runner.isolated_filesystem(temp_dir=tmp_path):
        _seed_project()
        result = runner.invoke(cli_main.app, ["init", "skills", "--global"])

        assert result.exit_code == 1, result.output
        assert "~/.claude/skills" in result.output
        assert "~/.agents/skills" in result.output
        assert "--dir" in result.output


def test_init_skills_global_with_target_errors(tmp_path: Path) -> None:
    with runner.isolated_filesystem(temp_dir=tmp_path):
        _seed_project()
        result = runner.invoke(cli_main.app, ["init", "skills", "agents", "--global"])

        assert result.exit_code == 1, result.output


def test_init_skills_global_with_all_errors(tmp_path: Path) -> None:
    with runner.isolated_filesystem(temp_dir=tmp_path):
        _seed_project()
        result = runner.invoke(cli_main.app, ["init", "skills", "--all", "--global"])

        assert result.exit_code == 1, result.output


def test_init_skills_global_with_dir_errors(tmp_path: Path) -> None:
    with runner.isolated_filesystem(temp_dir=tmp_path):
        _seed_project()
        result = runner.invoke(
            cli_main.app, ["init", "skills", "--dir", "custom-skills", "--global"]
        )

        assert result.exit_code == 1, result.output


def test_init_mcp_does_not_write_skill_dirs(tmp_path: Path) -> None:
    with runner.isolated_filesystem(temp_dir=tmp_path):
        _seed_project()
        Path(".cursor").mkdir()
        result = runner.invoke(cli_main.app, ["init", "mcp", "cursor"])

        assert result.exit_code == 0, result.output
        assert Path(".cursor/mcp.json").exists()
        assert not Path(".cursor/skills").exists()
        assert not Path(".codex/skills").exists()
        assert not Path(".claude/skills").exists()


def test_init_skills_global_prints_a_home_path_not_a_repo_one(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    home = tmp_path / "home"
    (home / ".claude").mkdir(parents=True)
    monkeypatch.setenv("HOME", str(home))
    with runner.isolated_filesystem(temp_dir=tmp_path):
        _seed_project()
        result = runner.invoke(cli_main.app, ["init", "skills", "--global"])
        assert result.exit_code == 0, result.output
        assert "~/.claude/skills/" in result.output


def test_init_skills_global_ignores_dct_project_dir(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """ProjectDirOption reads DCT_PROJECT_DIR; a global install must neither
    refuse on it nor write into that project."""
    home = tmp_path / "home"
    (home / ".claude").mkdir(parents=True)
    monkeypatch.setenv("HOME", str(home))
    project = tmp_path / "proj"
    project.mkdir()
    monkeypatch.setenv("DCT_PROJECT_DIR", str(project))

    with runner.isolated_filesystem(temp_dir=tmp_path):
        result = runner.invoke(cli_main.app, ["init", "skills", "--global"])

        assert result.exit_code == 0, result.output
        assert (home / ".claude/skills/dct-board-build/SKILL.md").exists()
        assert not (project / ".claude").exists()
        assert not (project / ".agents").exists()


@_needs_non_root
def test_init_skills_unwritable_target_errors_cleanly(tmp_path: Path) -> None:
    """A sandboxed agent's read-only `.agents/` must not surface a raw
    PermissionError traceback — a clean, named error pointing at the two
    working alternatives instead."""
    with runner.isolated_filesystem(temp_dir=tmp_path):
        _seed_project()
        locked = Path(".agents")
        locked.mkdir()
        locked.chmod(0o555)
        try:
            result = runner.invoke(cli_main.app, ["init", "skills", "agents"])
        finally:
            locked.chmod(0o755)

        assert result.exit_code == 1, result.output
        assert result.exception is None or isinstance(result.exception, SystemExit), (
            "must not raise an uncaught exception (no traceback)"
        )
        assert "Traceback" not in result.output
        assert str(locked / "skills") in result.output
        assert "dct init skills --dir <dir>" in result.output
        assert "dct skills <name>" in result.output
