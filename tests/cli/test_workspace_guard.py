"""Tests for `dbt_charts.cli._workspace_guard.detect_workspace_mismatch`."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import pytest
from typer.testing import CliRunner

from dbt_charts.agent_api import version_info
from dbt_charts.cli import _workspace_guard
from dbt_charts.cli.main import app

runner = CliRunner()


def _version_info_for(package_dir: Path, *, editable: bool) -> version_info.VersionInfo:
    return version_info.VersionInfo(
        version="0.5.1.dev134",
        package_dir=package_dir,
        python_version="3.13.12",
        python_executable="/usr/bin/python3",
        editable=editable,
    )


def _make_monorepo_checkout(root: Path) -> Path:
    """The fivetran/dataface layout: the package nests under `dbt-charts/`.

    The workspace root carries its own `pyproject.toml` too — the reason the
    layout probe must key on `src/dbt_charts`, not `pyproject.toml` alone.
    """
    (root / ".git").mkdir(parents=True)
    (root / "pyproject.toml").write_text("")
    (root / "dbt-charts" / "src" / "dbt_charts").mkdir(parents=True)
    (root / "dbt-charts" / "pyproject.toml").write_text("")
    return root / "dbt-charts" / "src" / "dbt_charts"


def _make_oss_checkout(root: Path) -> Path:
    """The exported dbt-labs/dbt-charts layout: the package IS the checkout root."""
    (root / ".git").mkdir(parents=True)
    (root / "pyproject.toml").write_text("")
    (root / "src" / "dbt_charts").mkdir(parents=True)
    return root / "src" / "dbt_charts"


def test_no_git_anywhere_above_cwd_returns_none(tmp_path: Path) -> None:
    cwd = tmp_path / "scratch"
    cwd.mkdir()
    assert _workspace_guard.detect_workspace_mismatch(cwd) is None


def test_git_checkout_that_is_not_dbt_charts_returns_none(tmp_path: Path) -> None:
    """Some unrelated repo has `.git` but no dbt_charts package under it."""
    (tmp_path / ".git").mkdir()
    assert _workspace_guard.detect_workspace_mismatch(tmp_path) is None


def test_monorepo_checkout_with_stale_global_build_warns(tmp_path: Path) -> None:
    expected = _make_monorepo_checkout(tmp_path)
    stale = tmp_path / "stale-install" / "dbt_charts"
    with patch.object(
        version_info, "collect", return_value=_version_info_for(stale, editable=False)
    ):
        message = _workspace_guard.detect_workspace_mismatch(tmp_path)
    assert message is not None
    assert str(stale) in message
    assert str(expected) in message
    assert "not editable" in message


def test_monorepo_checkout_with_matching_editable_build_is_silent(
    tmp_path: Path,
) -> None:
    expected = _make_monorepo_checkout(tmp_path)
    with patch.object(
        version_info,
        "collect",
        return_value=_version_info_for(expected, editable=True),
    ):
        assert _workspace_guard.detect_workspace_mismatch(tmp_path) is None


def test_oss_export_checkout_with_matching_editable_build_is_silent(
    tmp_path: Path,
) -> None:
    """dbt-labs/dbt-charts, where dbt-charts/ IS the root — must not be skipped."""
    expected = _make_oss_checkout(tmp_path)
    with patch.object(
        version_info,
        "collect",
        return_value=_version_info_for(expected, editable=True),
    ):
        assert _workspace_guard.detect_workspace_mismatch(tmp_path) is None


def test_oss_export_checkout_with_stale_global_build_warns(tmp_path: Path) -> None:
    expected = _make_oss_checkout(tmp_path)
    stale = tmp_path / "stale-install" / "dbt_charts"
    with patch.object(
        version_info, "collect", return_value=_version_info_for(stale, editable=False)
    ):
        message = _workspace_guard.detect_workspace_mismatch(tmp_path)
    assert message is not None
    assert str(expected) in message


def test_worktree_resolves_against_its_own_checkout(tmp_path: Path) -> None:
    """A linked worktree's `.git` is a file, and it has its own editable install.

    Running from a worktree while a global shim points at the main checkout is
    the mismatch this guard exists to surface.
    """
    main_checkout = tmp_path / "main"
    main_package = _make_monorepo_checkout(main_checkout)
    worktree = tmp_path / "worktrees" / "some-slug"
    worktree.mkdir(parents=True)
    (worktree / ".git").write_text("gitdir: ../../main/.git/worktrees/some-slug\n")
    (worktree / "pyproject.toml").write_text("")
    (worktree / "dbt-charts" / "src" / "dbt_charts").mkdir(parents=True)
    (worktree / "dbt-charts" / "pyproject.toml").write_text("")

    with patch.object(
        version_info,
        "collect",
        return_value=_version_info_for(main_package, editable=True),
    ):
        message = _workspace_guard.detect_workspace_mismatch(worktree)
    assert message is not None
    assert str(main_package) in message
    # The checkout side matters as much: a bug in base selection would still
    # name the right install while pointing at the wrong expected tree.
    assert str(worktree / "dbt-charts" / "src" / "dbt_charts") in message


def test_worktree_matching_its_own_install_is_silent(tmp_path: Path) -> None:
    """The false-positive direction — otherwise the guard nags on every command."""
    main_checkout = tmp_path / "main"
    _make_monorepo_checkout(main_checkout)
    worktree = tmp_path / "worktrees" / "some-slug"
    worktree.mkdir(parents=True)
    (worktree / ".git").write_text("gitdir: ../../main/.git/worktrees/some-slug\n")
    (worktree / "pyproject.toml").write_text("")
    own_package = worktree / "dbt-charts" / "src" / "dbt_charts"
    own_package.mkdir(parents=True)
    (worktree / "dbt-charts" / "pyproject.toml").write_text("")

    with patch.object(
        version_info,
        "collect",
        return_value=_version_info_for(own_package, editable=True),
    ):
        assert _workspace_guard.detect_workspace_mismatch(worktree) is None


def test_mismatch_warns_on_stderr_without_failing_the_command(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _make_monorepo_checkout(tmp_path)
    monkeypatch.chdir(tmp_path)
    stale = _version_info_for(tmp_path / "stale" / "dbt_charts", editable=False)

    with patch.object(version_info, "collect", return_value=stale):
        result = runner.invoke(app, ["docs"])

    assert result.exit_code == 0
    assert "Warning" in result.stderr
    assert stale.version in result.stderr


def test_bracketed_checkout_path_survives_rich_markup(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The payload is entirely filesystem paths, which Rich would read as markup.

    An unescaped `[archive]` segment is silently deleted from the printed path,
    and a `[/]` segment raises MarkupError — which would make this advisory kill
    the command it is only supposed to annotate.
    """
    checkout = tmp_path / "[archive]" / "repo"
    checkout.mkdir(parents=True)
    _make_monorepo_checkout(checkout)
    monkeypatch.chdir(checkout)
    stale = _version_info_for(tmp_path / "stale" / "dbt_charts", editable=False)

    with patch.object(version_info, "collect", return_value=stale):
        result = runner.invoke(app, ["docs"])

    assert result.exit_code == 0
    assert "[archive]" in result.stderr


def test_env_opt_out_silences_the_warning(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _make_monorepo_checkout(tmp_path)
    monkeypatch.chdir(tmp_path)
    stale = _version_info_for(tmp_path / "stale" / "dbt_charts", editable=False)

    with patch.object(version_info, "collect", return_value=stale):
        result = runner.invoke(app, ["docs"], env={"DCT_NO_WORKSPACE_GUARD": "1"})

    assert result.exit_code == 0
    assert "Warning" not in result.stderr


def test_env_opt_out_rejects_garbage(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _make_monorepo_checkout(tmp_path)
    monkeypatch.chdir(tmp_path)
    stale = _version_info_for(tmp_path / "stale" / "dbt_charts", editable=False)

    with patch.object(version_info, "collect", return_value=stale):
        result = runner.invoke(app, ["docs"], env={"DCT_NO_WORKSPACE_GUARD": "maybe"})

    assert result.exit_code == 2
