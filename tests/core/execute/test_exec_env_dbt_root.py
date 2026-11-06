"""build_exec_env detects an external dbt_project_dir.

The sibling rule in env.py must check FilesystemProject.dbt_root
(and its nested `dbt_project` seam), not the dct project root, so a
dbt_project.yml that lives outside the dct project (a monorepo layout, or a
separate repo entirely) is still detected and wired into the registry's dbt context
and the dbt backend.
"""

from __future__ import annotations

from pathlib import Path

from dbt_charts.cli.filesystem_project import FilesystemProject
from dbt_charts.core.execute.env import build_exec_env


def test_links_dbt_project_from_external_dbt_root(tmp_path: Path) -> None:
    project_root = tmp_path / "myproject"
    project_root.mkdir()
    external_dbt = tmp_path / "external_dbt"
    external_dbt.mkdir()
    (external_dbt / "dbt_project.yml").write_text("name: x\nprofile: x\n")

    project = FilesystemProject(project_root, dbt_root=external_dbt)
    registry = build_exec_env(project)

    assert registry.dbt_context is not None
    assert registry.dbt_context.dbt_project_path == external_dbt.resolve()


def test_links_no_dbt_project_when_external_dir_has_no_dbt_project(
    tmp_path: Path,
) -> None:
    project_root = tmp_path / "myproject"
    project_root.mkdir()
    external_dbt = tmp_path / "external_dbt"
    external_dbt.mkdir()  # no dbt_project.yml

    project = FilesystemProject(project_root, dbt_root=external_dbt)
    registry = build_exec_env(project)

    assert registry.dbt_context is None


def test_sibling_rule_unchanged_when_dbt_root_equals_project_root(
    tmp_path: Path,
) -> None:
    """No dbt_project_dir override -> same-dir sibling behavior as before."""
    project_root = tmp_path / "myproject"
    project_root.mkdir()
    (project_root / "dbt_project.yml").write_text("name: x\nprofile: x\n")

    project = FilesystemProject(project_root)
    registry = build_exec_env(project)

    assert registry.dbt_context is not None
    assert registry.dbt_context.dbt_project_path == project_root.resolve()
