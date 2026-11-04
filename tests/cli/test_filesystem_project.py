"""Tests for FilesystemProject's dbt_root/dbt_project/manifest_project seams."""

from __future__ import annotations

from pathlib import Path

import pytest

from dbt_charts.cli.filesystem_project import FilesystemProject


def test_dbt_root_defaults_to_project_root(tmp_path: Path) -> None:
    project = FilesystemProject(tmp_path)
    assert project.dbt_root == tmp_path.resolve()


def test_dbt_root_uses_explicit_external_dir(tmp_path: Path) -> None:
    external = tmp_path / "external_dbt"
    external.mkdir()
    project = FilesystemProject(tmp_path, dbt_root=external)
    assert project.dbt_root == external.resolve()


def test_dbt_project_is_self_when_same_dir(tmp_path: Path) -> None:
    project = FilesystemProject(tmp_path)
    assert project.dbt_project is project


def test_dbt_project_is_second_instance_when_external(tmp_path: Path) -> None:
    external = tmp_path / "external_dbt"
    external.mkdir()
    project = FilesystemProject(tmp_path, dbt_root=external)

    assert project.dbt_project is not project
    assert isinstance(project.dbt_project, FilesystemProject)
    assert project.dbt_project.root == external.resolve()


def test_dbt_project_reads_files_from_external_dir(tmp_path: Path) -> None:
    external = tmp_path / "external_dbt"
    external.mkdir()
    (external / "dbt_project.yml").write_text("name: x\n")
    project = FilesystemProject(tmp_path, dbt_root=external)

    assert project.dbt_project.exists("dbt_project.yml")
    assert not project.exists("dbt_project.yml")


def test_manifest_project_returns_self_when_same_dir(tmp_path: Path) -> None:
    project = FilesystemProject(tmp_path)
    assert project.manifest_project() is project


def test_manifest_project_returns_dbt_project_when_external(tmp_path: Path) -> None:
    external = tmp_path / "external_dbt"
    external.mkdir()
    project = FilesystemProject(tmp_path, dbt_root=external)

    assert project.manifest_project() is project.dbt_project


def test_symlinked_package_stays_read_only(tmp_path: Path) -> None:
    pkg = tmp_path / "pkg_src"
    (pkg / "charts").mkdir(parents=True)
    (pkg / "charts" / "x.yml").write_text("title: X\n")
    host = tmp_path / "host"
    (host / "dbt_packages").mkdir(parents=True)
    (host / "dbt_packages" / "pkg").symlink_to(pkg, target_is_directory=True)
    project = FilesystemProject(host)

    with pytest.raises(ValueError, match="escapes the project root"):
        project.write_text("dbt_packages/pkg/charts/x.yml", "title: Y\n")
    with pytest.raises(ValueError, match="escapes the project root"):
        project.delete_text("dbt_packages/pkg/charts/x.yml")
    assert (pkg / "charts" / "x.yml").read_text() == "title: X\n"
