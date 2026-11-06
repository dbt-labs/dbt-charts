"""Source resolution over a project's sources: listing, registration, config."""

from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest

from dbt_charts.cli.filesystem_project import FilesystemProject
from dbt_charts.core.compile.config import load_project_sources
from dbt_charts.core.diagnostics.base import DbtChartsError
from dbt_charts.core.execute.env import ExecEnv, make_exec_env
from dbt_charts.core.execute.plan_sources import list_sql_sources, resolve_source_config


def _registry_with_sources(
    tmp_path: Path, sources_yaml: str, local_project: Callable[..., FilesystemProject]
) -> ExecEnv:
    """Build an ExecEnv whose SourceRegistry reads from tmp_path."""
    (tmp_path / "dbt_charts.yml").write_text(sources_yaml)
    return make_exec_env(
        project=local_project(tmp_path),
        project_sources=load_project_sources(local_project(tmp_path)),
    )


class TestListSqlSources:
    def test_empty_registry_returns_empty_list(
        self, local_project: Callable[..., FilesystemProject]
    ) -> None:
        registry = make_exec_env(project=local_project(Path()))
        assert list_sql_sources(registry.sources) == []

    def test_registry_without_sources_returns_empty_list(
        self, tmp_path: Path, local_project: Callable[..., FilesystemProject]
    ) -> None:
        registry = make_exec_env(project=local_project(tmp_path))
        assert list_sql_sources(registry.sources) == []

    def test_in_memory_duckdb_source_gets_in_memory_marker(
        self, tmp_path: Path, local_project: Callable[..., FilesystemProject]
    ) -> None:
        registry = _registry_with_sources(
            tmp_path,
            "sources:\n  mem:\n    type: duckdb\n    path: ':memory:'\n",
            local_project,
        )

        result = list_sql_sources(registry.sources)
        assert result == [
            {"name": "mem", "type": "duckdb", "path": ":memory:", "in_memory": True}
        ]

    def test_file_backed_duckdb_source_omits_in_memory_marker(
        self, tmp_path: Path, local_project: Callable[..., FilesystemProject]
    ) -> None:
        db_path = tmp_path / "warehouse.duckdb"
        registry = _registry_with_sources(
            tmp_path,
            f"sources:\n  warehouse:\n    type: duckdb\n    path: '{db_path}'\n",
            local_project,
        )

        result = list_sql_sources(registry.sources)
        assert result == [{"name": "warehouse", "type": "duckdb", "path": str(db_path)}]

    def test_results_are_sorted_by_name(
        self, tmp_path: Path, local_project: Callable[..., FilesystemProject]
    ) -> None:
        registry = _registry_with_sources(
            tmp_path,
            (
                "sources:\n"
                "  zeta:\n    type: duckdb\n    path: ':memory:'\n"
                "  alpha:\n    type: duckdb\n    path: ':memory:'\n"
                "  mike:\n    type: duckdb\n    path: ':memory:'\n"
            ),
            local_project,
        )

        result: list[dict[str, Any]] = list_sql_sources(registry.sources)
        assert [s["name"] for s in result] == ["alpha", "mike", "zeta"]


class TestRegisterSource:
    """Synthetic source registration lands on the registry, not the adapter."""

    def test_register_source_appears_in_list(
        self, local_project: Callable[..., FilesystemProject]
    ) -> None:
        registry = make_exec_env(project=local_project(Path()))
        registry.sources.register("synth", {"type": "duckdb", "path": ":memory:"})
        names = [s["name"] for s in list_sql_sources(registry.sources)]
        assert names == ["synth"]

    def test_register_source_is_idempotent_first_wins(
        self, local_project: Callable[..., FilesystemProject]
    ) -> None:
        registry = make_exec_env(project=local_project(Path()))
        registry.sources.register("synth", {"type": "duckdb", "path": ":memory:"})
        registry.sources.register("synth", {"type": "duckdb", "path": "/tmp/a.duckdb"})
        config = resolve_source_config(registry.sources, registry.project, "synth")
        assert config["path"] == ":memory:"

    def test_project_source_wins_over_synthetic(
        self, tmp_path: Path, local_project: Callable[..., FilesystemProject]
    ) -> None:
        registry = _registry_with_sources(
            tmp_path,
            "sources:\n  shared:\n    type: duckdb\n    path: ':memory:'\n",
            local_project,
        )
        registry.sources.register(
            "shared", {"type": "duckdb", "path": "/tmp/synth.duckdb"}
        )
        config = resolve_source_config(registry.sources, registry.project, "shared")
        assert config["path"] == ":memory:"


class TestResolveSourceConfig:
    """resolve_source_config must raise when source is None — no lossy fallback."""

    def test_no_source_raises_even_with_duckdb_adapter_registered(
        self, local_project: Callable[..., FilesystemProject]
    ) -> None:
        """No default-DuckDB fallback: a real registry built via
        build_exec_env() (which registers a DuckDBBackend) still raises
        for source=None — there is no stored default to fall back to."""
        from dbt_charts.core.diagnostics.codes_execute import ERR_SOURCE_NOT_NAMED
        from dbt_charts.core.execute.env import (
            build_exec_env,
        )

        registry = build_exec_env(local_project(Path()))

        with pytest.raises(DbtChartsError) as exc_info:
            resolve_source_config(registry.sources, registry.project, source=None)
        assert exc_info.value.code is ERR_SOURCE_NOT_NAMED

    def test_no_source_no_adapter_still_raises(
        self, local_project: Callable[..., FilesystemProject]
    ) -> None:
        # No registry at all — must also raise (not the "no SQL adapter" branch).
        from dbt_charts.core.diagnostics.codes_execute import ERR_SOURCE_NOT_NAMED

        registry = make_exec_env(project=local_project(Path()))
        with pytest.raises(DbtChartsError) as exc_info:
            resolve_source_config(registry.sources, registry.project, source=None)
        assert exc_info.value.code is ERR_SOURCE_NOT_NAMED
        # No sources configured → `available` is an empty Sequence[str], which the
        # display path renders "none configured" — not "" or a bare list repr.
        assert "none configured" in exc_info.value.message

    def test_source_not_named_error_renders_available_as_joined_names(
        self, tmp_path: Path, local_project: Callable[..., FilesystemProject]
    ) -> None:
        """The sourceless-SQL error passes `available` as a Sequence[str] like
        every other ERR-* caller, so the diagnostic joins the configured names
        for display — never a raw Python list repr. Guards this raise site
        against regressing to a pre-joined string, which would diverge from the
        contract and crash the moment this code gains a hint_generator."""
        from dbt_charts.core.diagnostics.codes_execute import ERR_SOURCE_NOT_NAMED

        registry = _registry_with_sources(
            tmp_path,
            "sources:\n"
            "  alpha:\n    type: duckdb\n    path: ':memory:'\n"
            "  beta:\n    type: duckdb\n    path: ':memory:'\n",
            local_project,
        )

        with pytest.raises(DbtChartsError) as exc_info:
            resolve_source_config(registry.sources, registry.project, source=None)
        assert exc_info.value.code is ERR_SOURCE_NOT_NAMED
        message = exc_info.value.message
        assert "alpha" in message and "beta" in message
        assert "['" not in message

    def test_named_source_still_resolves(
        self, tmp_path: Path, local_project: Callable[..., FilesystemProject]
    ) -> None:
        registry = _registry_with_sources(
            tmp_path,
            "sources:\n  mydb:\n    type: duckdb\n    path: ':memory:'\n",
            local_project,
        )

        config = resolve_source_config(
            registry.sources, registry.project, source="mydb"
        )
        assert config["type"] == "duckdb"
