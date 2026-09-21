"""Unit tests for CLI project discovery (`dbt_charts.cli._project`)."""

from __future__ import annotations

from pathlib import Path

import pytest

from dbt_charts.cli._project import (
    InitRootResolution,
    ProjectNotFoundError,
    has_charts_marker,
    resolve_init_root,
    resolve_mcp_project_dir,
    resolve_project_dir,
    resolve_skill_install_root,
    with_project,
)
from dbt_charts.cli.filesystem_project import FilesystemProject


def test_resolve_project_dir_walks_up_from_subdir(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Regression: cwd in a project subdir still resolves to the project root."""
    project = tmp_path / "myproject"
    (project / "charts").mkdir(parents=True)
    (project / "dbt_charts.yml").write_text("# project marker\n")
    monkeypatch.chdir(project / "charts")  # cwd is a SUBDIR of the project

    assert resolve_project_dir(None) == project.resolve()


def test_resolve_project_dir_raises_when_no_markers(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """No project markers above cwd → resolve_project_dir raises, no cwd fallback."""
    monkeypatch.chdir(tmp_path)
    (tmp_path / "hello.yml").write_text("# not a project marker\n")

    with pytest.raises(ProjectNotFoundError, match="No dbt charts project found"):
        resolve_project_dir(None)


def test_resolve_project_dir_explicit_requires_marker(tmp_path: Path) -> None:
    """Explicit --project-dir is validated: a non-project dir fails loud here."""
    with pytest.raises(ProjectNotFoundError, match="is not a dbt charts project"):
        resolve_project_dir(tmp_path)


def test_project_not_found_names_the_minimal_marker_to_create(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Both failures say what satisfies them: an empty dbt_charts.yml in that dir."""
    monkeypatch.chdir(tmp_path)
    marker = str(tmp_path.resolve() / "dbt_charts.yml")

    for project_dir in (None, tmp_path):
        with pytest.raises(ProjectNotFoundError) as exc:
            resolve_project_dir(project_dir)
        message = str(exc.value)
        assert marker in message
        assert "empty" in message
        assert "dct init" in message


def test_resolve_project_dir_explicit_accepts_project(tmp_path: Path) -> None:
    """Explicit --project-dir with a project marker resolves to that dir."""
    (tmp_path / "dbt_charts.yml").write_text("# project marker\n")
    assert resolve_project_dir(tmp_path) == tmp_path.resolve()


# ---------------------------------------------------------------------------
# has_charts_marker
# ---------------------------------------------------------------------------


def test_has_charts_marker_true_at_start(tmp_path: Path) -> None:
    """charts/ dir directly at start → True without needing to discover root."""
    (tmp_path / "charts").mkdir()
    assert has_charts_marker(tmp_path) is True


def test_has_charts_marker_true_at_dct_root(tmp_path: Path) -> None:
    """charts/ at the discovered dct root → True even when start is a subdir."""
    project = tmp_path / "proj"
    project.mkdir()
    (project / "dbt_charts.yml").write_text("")
    (project / "charts").mkdir()
    subdir = project / "models"
    subdir.mkdir()

    assert has_charts_marker(subdir) is True


def test_has_charts_marker_false_no_boards(tmp_path: Path) -> None:
    """dct project exists but no charts/ → False."""
    (tmp_path / "dbt_charts.yml").write_text("")
    assert has_charts_marker(tmp_path) is False


def test_has_charts_marker_false_no_project(tmp_path: Path) -> None:
    """No project marker and no charts/ → False."""
    subdir = tmp_path / "sub"
    subdir.mkdir()
    assert has_charts_marker(subdir) is False


# ---------------------------------------------------------------------------
# resolve_init_root
# ---------------------------------------------------------------------------


def test_resolve_init_root_explicit_wins(tmp_path: Path) -> None:
    """Explicit dir is returned resolved, git_root is None (unambiguous)."""
    explicit = tmp_path / "mydir"
    explicit.mkdir()
    result = resolve_init_root(explicit, tmp_path)
    assert result == InitRootResolution(root=explicit.resolve())
    assert result.git_root is None


def test_resolve_init_root_dct_root_found(tmp_path: Path) -> None:
    """Existing dct project found from cwd → returns project root, no git_root."""
    (tmp_path / "dbt_charts.yml").write_text("")
    result = resolve_init_root(None, tmp_path)
    assert result == InitRootResolution(root=tmp_path.resolve())
    assert result.git_root is None


def test_resolve_init_root_no_project_no_git(tmp_path: Path) -> None:
    """No project, no git repo → returns cwd, git_root is None (unambiguous)."""
    cwd = tmp_path / "workdir"
    cwd.mkdir()
    result = resolve_init_root(None, cwd)
    assert result == InitRootResolution(root=cwd.resolve())
    assert result.git_root is None


def test_resolve_init_root_git_equals_cwd_unambiguous(tmp_path: Path) -> None:
    """git root == cwd → cwd returned, git_root is None (no prompt needed)."""
    (tmp_path / ".git").mkdir()
    result = resolve_init_root(None, tmp_path)
    assert result == InitRootResolution(root=tmp_path.resolve())
    assert result.git_root is None


def test_resolve_init_root_git_above_cwd_ambiguous(tmp_path: Path) -> None:
    """git root above cwd and no project → ambiguous, git_root is set for caller prompt."""
    git_root = tmp_path / "repo"
    git_root.mkdir()
    (git_root / ".git").mkdir()
    cwd = git_root / "subdir"
    cwd.mkdir()

    result = resolve_init_root(None, cwd)
    assert result.root == cwd.resolve()
    assert result.git_root == git_root.resolve()


# ---------------------------------------------------------------------------
# resolve_mcp_project_dir
# ---------------------------------------------------------------------------


def test_resolve_mcp_project_dir_explicit_valid(tmp_path: Path) -> None:
    """Explicit dir that is a dct project root → project_dir set, nearest_root matches."""
    (tmp_path / "dbt_charts.yml").write_text("")
    result = resolve_mcp_project_dir(tmp_path, tmp_path)
    assert result.project_dir == tmp_path.resolve()
    assert result.nearest_root == tmp_path.resolve()


def test_resolve_mcp_project_dir_explicit_invalid(tmp_path: Path) -> None:
    """Explicit dir that is not a project root → project_dir is None."""
    not_a_project = tmp_path / "empty"
    not_a_project.mkdir()
    result = resolve_mcp_project_dir(not_a_project, tmp_path)
    assert result.project_dir is None


def test_resolve_mcp_project_dir_explicit_subdir_of_project(tmp_path: Path) -> None:
    """Explicit dir inside a project but not the root → project_dir None, nearest_root hint."""
    (tmp_path / "dbt_charts.yml").write_text("")
    subdir = tmp_path / "models"
    subdir.mkdir()

    result = resolve_mcp_project_dir(subdir, tmp_path)
    assert result.project_dir is None
    assert result.nearest_root == tmp_path.resolve()


def test_resolve_mcp_project_dir_discovers_from_cwd(tmp_path: Path) -> None:
    """No explicit, dct project at cwd → project_dir and nearest_root both set."""
    (tmp_path / "dbt_charts.yml").write_text("")
    result = resolve_mcp_project_dir(None, tmp_path)
    assert result.project_dir == tmp_path.resolve()
    assert result.nearest_root == tmp_path.resolve()


def test_resolve_mcp_project_dir_no_project_falls_back_ai_config_root_to_git(
    tmp_path: Path,
) -> None:
    """No dct project, git root present → ai_config_root is git root, project_dir None."""
    (tmp_path / ".git").mkdir()
    cwd = tmp_path / "sub"
    cwd.mkdir()

    result = resolve_mcp_project_dir(None, cwd)
    assert result.project_dir is None
    assert result.ai_config_root == tmp_path.resolve()


def test_resolve_mcp_project_dir_no_project_no_git_falls_back_to_cwd(
    tmp_path: Path,
) -> None:
    """No dct project, no git → ai_config_root is cwd."""
    cwd = tmp_path / "bare"
    cwd.mkdir()
    result = resolve_mcp_project_dir(None, cwd)
    assert result.project_dir is None
    assert result.ai_config_root == cwd


# ---------------------------------------------------------------------------
# resolve_skill_install_root
# ---------------------------------------------------------------------------


def test_resolve_skill_install_root_uses_git_root_over_dbt_charts_yml(
    tmp_path: Path,
) -> None:
    """When .git is above the dbt_charts.yml, skill install target is the git root."""
    git_root = tmp_path / "repo"
    git_root.mkdir()
    (git_root / ".git").mkdir()
    subproject = git_root / "dbt_ft_prod"
    subproject.mkdir()
    (subproject / "dbt_charts.yml").write_text("")

    result = resolve_skill_install_root(subproject, walk_to_git_root=True)
    assert result.root == git_root.resolve()


def test_resolve_skill_install_root_falls_back_to_project_root_without_git(
    tmp_path: Path,
) -> None:
    """Without .git anywhere, fall back to the dbt charts project root."""
    project = tmp_path / "myproject"
    project.mkdir()
    (project / "dbt_charts.yml").write_text("")

    result = resolve_skill_install_root(project, walk_to_git_root=True)
    assert result.root == project.resolve()


def test_resolve_skill_install_root_no_project_no_git(tmp_path: Path) -> None:
    """No project, no git → returns start.resolve()."""
    bare = tmp_path / "bare"
    bare.mkdir()
    result = resolve_skill_install_root(bare, walk_to_git_root=True)
    assert result.root == bare.resolve()


def test_resolve_skill_install_root_explicit_project_dir_skips_git_walk(
    tmp_path: Path,
) -> None:
    """An explicit --project-dir names the destination outright; the git root
    above it (or above anything else) must not override that choice."""
    git_root = tmp_path / "repo"
    git_root.mkdir()
    (git_root / ".git").mkdir()
    subproject = git_root / "sub"
    subproject.mkdir()
    (subproject / "dbt_charts.yml").write_text("")

    result = resolve_skill_install_root(subproject, walk_to_git_root=False)
    assert result.root == subproject.resolve()
    assert result.nearest_root is None


def test_resolve_skill_install_root_explicit_project_dir_errors_without_marker(
    tmp_path: Path,
) -> None:
    """A dbt charts project above the named directory (but not at it) must not
    silently win: the explicit --project-dir names the destination outright,
    so a missing marker there is an error, not a walk-up."""
    git_root = tmp_path / "repo"
    git_root.mkdir()
    (git_root / ".git").mkdir()
    (git_root / "dbt_project.yml").write_text("")
    named = git_root / "newproj"
    named.mkdir()

    result = resolve_skill_install_root(named, walk_to_git_root=False)
    assert result.root is None
    assert result.nearest_root == git_root.resolve()


# ---------------------------------------------------------------------------
# with_project: dbt_project_dir threading
# ---------------------------------------------------------------------------


def test_with_project_threads_explicit_dbt_project_dir(tmp_path: Path) -> None:
    """A `dbt_project_dir` kwarg reaches the injected project's `dbt_root`."""
    project_dir = tmp_path / "myproject"
    (project_dir / "charts").mkdir(parents=True)
    (project_dir / "dbt_charts.yml").write_text("# project marker\n")
    external_dbt = tmp_path / "external_dbt"
    external_dbt.mkdir()

    seen: dict[str, FilesystemProject] = {}

    @with_project
    def command(*, project: FilesystemProject) -> None:
        seen["project"] = project

    command(project_dir=project_dir, dbt_project_dir=external_dbt)

    assert seen["project"].dbt_root == external_dbt.resolve()


def test_with_project_defaults_dbt_root_to_project_root(tmp_path: Path) -> None:
    """No `dbt_project_dir` kwarg -> the injected project's `dbt_root` is its own root."""
    project_dir = tmp_path / "myproject"
    (project_dir / "charts").mkdir(parents=True)
    (project_dir / "dbt_charts.yml").write_text("# project marker\n")

    seen: dict[str, FilesystemProject] = {}

    @with_project
    def command(*, project: FilesystemProject) -> None:
        seen["project"] = project

    command(project_dir=project_dir)

    assert seen["project"].dbt_root == project_dir.resolve()
