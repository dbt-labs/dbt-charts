"""Pins: ExecEnv.__init__ does no I/O; sources loading happens in
build_exec_env (the factory), not inside the registry itself.

The old contract ("build_exec_env with no project_sources reads no disk")
is replaced by: "ExecEnv.__init__ does no disk reads regardless of what
sources are passed into it — the sources are already-loaded data at that point."
"""

from collections.abc import Callable
from pathlib import Path

from dbt_charts.cli.filesystem_project import FilesystemProject
from dbt_charts.core.execute.env import build_exec_env, make_exec_env
from dbt_charts.core.execute.plan_sources import list_sql_sources


def test_build_exec_env_reads_sources_from_project_dbt_charts_yml(
    tmp_path: Path,
    local_project: Callable[..., FilesystemProject],
) -> None:
    """build_exec_env reads sources from dbt_charts.yml via project.sources."""
    (tmp_path / "dbt_charts.yml").write_text(
        "sources:\n  disk_src:\n    type: duckdb\n    path: disk.duckdb\n"
    )

    registry = build_exec_env(local_project(tmp_path))

    names = {s["name"] for s in list_sql_sources(registry.sources)}
    assert "disk_src" in names, (
        f"Registry should reflect the project's dbt_charts.yml sources; got {names!r}"
    )


def test_build_exec_env_empty_project_has_no_sources(
    tmp_path: Path,
    local_project: Callable[..., FilesystemProject],
) -> None:
    """A project with no sources in dbt_charts.yml yields a registry with no configured sources."""
    registry = build_exec_env(local_project(tmp_path))
    names = {s["name"] for s in list_sql_sources(registry.sources)}
    assert names == set(), f"Empty project should yield no SQL sources; got {names!r}."


def test_exec_env_init_does_no_io(
    tmp_path: Path,
    local_project: Callable[..., FilesystemProject],
) -> None:
    """ExecEnv.__init__ does no I/O — sources are supplied as already-loaded data.

    Construct an ExecEnv directly with a pre-built ProjectSourcesConfig;
    even though tmp_path has a dbt_charts.yml on disk, the registry must use only the
    injected config, not fall back to reading the file.
    """
    from dbt_charts.core.compile.config import ProjectSourcesConfig

    (tmp_path / "dbt_charts.yml").write_text(
        "sources:\n  disk_src:\n    type: duckdb\n    path: disk.duckdb\n"
    )

    injected = ProjectSourcesConfig(
        sources={"injected_src": {"type": "duckdb", "path": ":memory:"}},
    )
    # Construct the Cat C ExecEnv directly — bypasses build_exec_env
    registry = make_exec_env(project=local_project(tmp_path), project_sources=injected)

    names = {s["name"] for s in list_sql_sources(registry.sources)}
    assert names == {"injected_src"}, (
        f"ExecEnv.__init__ must use only the injected config; got {names!r}. "
        "A 'disk_src' entry means the registry read from disk."
    )
    assert "disk_src" not in names
