"""Tests for project_roots resolution helpers."""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from dbt_charts.core.compile.errors import CompilationError
from dbt_charts.core.diagnostics.codes_compile import ERR_SOURCE_CONFIG_INVALID
from dbt_charts.core.project_roots import (
    DCT_ROOT_MARKERS,
    find_dct_root,
    find_project_root,
    find_repo_root,
    find_root,
    implicit_dbt_source,
    resolve_dbt_project_dir,
)


def test_find_root_returns_dir_with_marker(tmp_path: Path) -> None:
    (tmp_path / "marker").write_text("x\n")
    assert find_root(tmp_path, ("marker",)) == tmp_path


def test_find_root_walks_up_from_subdir(tmp_path: Path) -> None:
    (tmp_path / "marker").write_text("x\n")
    sub = tmp_path / "a" / "b"
    sub.mkdir(parents=True)
    assert find_root(sub, ("marker",)) == tmp_path


def test_find_root_returns_none_when_no_marker(tmp_path: Path) -> None:
    sub = tmp_path / "empty"
    sub.mkdir()
    assert find_root(sub, ("marker",)) is None


def test_find_root_returns_nearest_marker(tmp_path: Path) -> None:
    (tmp_path / "marker").write_text("outer\n")
    inner = tmp_path / "inner"
    inner.mkdir()
    (inner / "marker").write_text("inner\n")
    deeper = inner / "deeper"
    deeper.mkdir()
    # Walk stops at the nearest ancestor carrying the marker.
    assert find_root(deeper, ("marker",)) == inner


@pytest.mark.parametrize("marker", DCT_ROOT_MARKERS)
def test_find_dct_root_returns_dir_with_each_marker(
    tmp_path: Path, marker: str
) -> None:
    (tmp_path / marker).write_text("name: x\n")
    assert find_dct_root(tmp_path) == tmp_path


def test_find_dct_root_walks_up_from_subdir(tmp_path: Path) -> None:
    (tmp_path / "dbt_charts.yml").write_text("name: x\n")
    sub = tmp_path / "charts"
    sub.mkdir()
    assert find_dct_root(sub) == tmp_path


def test_find_dct_root_returns_none_when_no_marker(tmp_path: Path) -> None:
    sub = tmp_path / "empty"
    sub.mkdir()
    assert find_dct_root(sub) is None


def test_find_repo_root_finds_git_dir(tmp_path: Path) -> None:
    (tmp_path / ".git").mkdir()
    sub = tmp_path / "packages" / "x"
    sub.mkdir(parents=True)
    assert find_repo_root(sub) == tmp_path


def test_find_repo_root_returns_none_without_git(tmp_path: Path) -> None:
    assert find_repo_root(tmp_path) is None


def test_find_project_root_returns_project_root(tmp_path: Path) -> None:
    """find_project_root walks up to the first real dbt_charts.yml."""
    (tmp_path / "dbt_charts.yml").write_text("sources: {}\n")
    boards = tmp_path / "charts"
    boards.mkdir()

    result = find_project_root(boards, tmp_path)

    assert result == tmp_path


def test_find_project_root_accepts_a_bare_dbt_project(tmp_path: Path) -> None:
    """A dbt project with no dbt_charts.yml is a render root."""
    (tmp_path / "dbt_project.yml").write_text("name: shop\nprofile: shop\n")
    boards = tmp_path / "charts"
    boards.mkdir()

    assert find_project_root(boards, tmp_path) == tmp_path


def test_find_project_root_still_skips_a_serve_only_config(tmp_path: Path) -> None:
    (tmp_path / "dbt_project.yml").write_text("name: shop\nprofile: shop\n")
    inner = tmp_path / "inner"
    inner.mkdir()
    (inner / "dbt_charts.yml").write_text("server:\n  port: 1\n")

    assert find_project_root(inner, tmp_path) == tmp_path


def test_implicit_dbt_source_is_named_after_the_profile() -> None:
    assert implicit_dbt_source({}, "name: shop\nprofile: warehouse\n") == (
        "warehouse",
        {"type": "dbt_profile"},
    )


def test_implicit_dbt_source_is_off_once_anything_is_declared() -> None:
    declared = {"mine": {"type": "duckdb"}}
    assert implicit_dbt_source(declared, "profile: warehouse\n") is None


@pytest.mark.parametrize("dbt_project_yaml", [None, "name: shop\n", ""])
def test_implicit_dbt_source_needs_a_dbt_project_with_a_profile(
    dbt_project_yaml: str | None,
) -> None:
    assert implicit_dbt_source({}, dbt_project_yaml) is None


@pytest.mark.parametrize("dbt_project_yaml", ["profile: 3\n", "profile: [a\n"])
def test_implicit_dbt_source_rejects_a_malformed_dbt_project_with_a_coded_error(
    dbt_project_yaml: str,
) -> None:
    with pytest.raises(CompilationError) as exc_info:
        implicit_dbt_source({}, dbt_project_yaml)
    assert exc_info.value.code is ERR_SOURCE_CONFIG_INVALID


def test_resolve_dbt_project_dir_explicit_wins(tmp_path: Path) -> None:
    (tmp_path / "dbt_charts.yml").write_text("dbt_project_dir: ignored\n")
    explicit = tmp_path / "explicit_dbt"
    explicit.mkdir()

    assert resolve_dbt_project_dir(tmp_path, explicit) == explicit


def test_resolve_dbt_project_dir_reads_config_key(tmp_path: Path) -> None:
    project_dir = tmp_path / "myproject"
    project_dir.mkdir()
    external = tmp_path / "external_dbt_project"
    external.mkdir()
    (project_dir / "dbt_charts.yml").write_text(
        "dbt_project_dir: ../external_dbt_project\n"
    )

    result = resolve_dbt_project_dir(project_dir, None)

    assert result == external.resolve()


def test_resolve_dbt_project_dir_rejects_non_string_config_value(
    tmp_path: Path,
) -> None:
    (tmp_path / "dbt_charts.yml").write_text("dbt_project_dir: 3\n")

    with pytest.raises(TypeError, match="dbt_project_dir must be a string"):
        resolve_dbt_project_dir(tmp_path, None)


def test_resolve_dbt_project_dir_rejects_missing_config_target(
    tmp_path: Path,
) -> None:
    """An explicitly-configured dbt_project_dir that does not exist must fail
    loud, not silently degrade to WARN-DBT-MANIFEST-MISSING (matches
    resolve_profiles_path's "never falls back silently past a candidate that
    was explicitly named" contract)."""
    (tmp_path / "dbt_charts.yml").write_text("dbt_project_dir: ../typo_dbt\n")

    with pytest.raises(FileNotFoundError, match="does not exist"):
        resolve_dbt_project_dir(tmp_path, None)


def test_resolve_dbt_project_dir_rejects_configured_target_that_is_a_file(
    tmp_path: Path,
) -> None:
    """A configured dbt_project_dir naming an existing file (not a directory)
    must report "is not a directory"."""
    (tmp_path / "board.yaml").write_text("title: t\n")
    (tmp_path / "dbt_charts.yml").write_text("dbt_project_dir: board.yaml\n")

    with pytest.raises(NotADirectoryError, match="is not a directory"):
        resolve_dbt_project_dir(tmp_path, None)


def test_resolve_dbt_project_dir_treats_null_config_value_as_absent(
    tmp_path: Path,
) -> None:
    """A bare `dbt_project_dir:` key (YAML null) is "no key set", matching
    Config.dbt_project_dir's own None-means-sibling-default contract, not a
    non-string value to reject."""
    (tmp_path / "dbt_charts.yml").write_text("dbt_project_dir:\n")

    assert resolve_dbt_project_dir(tmp_path, None) == tmp_path


def test_resolve_dbt_project_dir_rejects_blank_config_value(tmp_path: Path) -> None:
    """An empty/whitespace-only dbt_project_dir must not silently resolve to
    project_dir via `Path(project_dir) / ""` -- a nonsense value landing
    silently on the default is exactly what validate-and-error-fast targets."""
    (tmp_path / "dbt_charts.yml").write_text("dbt_project_dir: ''\n")

    with pytest.raises(TypeError, match="must not be blank"):
        resolve_dbt_project_dir(tmp_path, None)


def test_resolve_dbt_project_dir_propagates_malformed_yaml(tmp_path: Path) -> None:
    """A malformed dbt_charts.yml fails loud immediately, not silently
    defaulting to the sibling rule (round 1 deliberately dropped the
    except yaml.YAMLError swallow)."""
    (tmp_path / "dbt_charts.yml").write_text("dbt_project_dir: [unterminated\n")

    with pytest.raises(yaml.YAMLError):
        resolve_dbt_project_dir(tmp_path, None)


def test_resolve_dbt_project_dir_defaults_to_sibling(tmp_path: Path) -> None:
    (tmp_path / "dbt_charts.yml").write_text("cache:\n  path: .dct_cache.duckdb\n")

    assert resolve_dbt_project_dir(tmp_path, None) == tmp_path


def test_resolve_dbt_project_dir_defaults_to_sibling_when_no_config(
    tmp_path: Path,
) -> None:
    assert resolve_dbt_project_dir(tmp_path, None) == tmp_path
