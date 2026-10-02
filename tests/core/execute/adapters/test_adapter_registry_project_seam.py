"""Regression: AdapterRegistry must carry a Project, not a raw Path.

Asserts that schema inspection (manifest load, resolver construction) routes
through the Project seam instead of touching the filesystem via a raw Path.
This covers the Cloud cwd fall-through bug where registry.project_root was
Path('.').resolve() on a multi-tenant server — every schema inspection hit
read the gunicorn worker cwd instead of the project's git-blob store.
"""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

import pytest

from dbt_charts.core.compile.config import ProjectSourcesConfig
from dbt_charts.core.execute.adapters import AdapterRegistry
from dbt_charts.core.project import Project, ProjectDirectory, ProjectPath


class _NoFilesystemProject(Project):
    """Project stub that raises on any filesystem read.

    Stands in for CloudManagedProject: the backing store is not the local
    filesystem (it would be a git-blob reader), so any raw-Path access is wrong.
    """

    @property
    def sources(self) -> ProjectSourcesConfig:
        return ProjectSourcesConfig(sources={})

    def read_text(self, relpath: str) -> str:
        raise AssertionError(
            f"filesystem read attempted via read_text({relpath!r}) — "
            "schema inspection must go through Project seam, not raw Path"
        )

    def read_bytes(self, relpath: str) -> bytes:
        raise AssertionError(
            f"filesystem read attempted via read_bytes({relpath!r}) — "
            "schema inspection must go through Project seam, not raw Path"
        )

    def exists(self, relpath: str) -> bool:
        # Returns False so manifest loading degrades cleanly (no manifest found)
        # instead of raising. This mirrors CloudManagedProject without a reader.
        return False

    def iter_files(self, under: str, *, recursive: bool) -> Iterator[str]:
        return iter(())

    def iter_dir(self, under: str) -> Iterator[ProjectPath | ProjectDirectory]:
        return iter(())

    def write_text(self, relpath: str, content: str) -> None:
        raise AssertionError(
            f"filesystem write attempted via write_text({relpath!r}) — "
            "schema inspection must go through Project seam, not raw Path"
        )

    def delete_text(self, relpath: str) -> None:
        raise AssertionError(
            f"filesystem delete attempted via delete_text({relpath!r}) — "
            "schema inspection must go through Project seam, not raw Path"
        )


class TestManifestLoadThroughSeam:
    """Schema inspection routes manifest reads through Project, not raw Path.

    The regression: previously LayeredSchemaResolver read the manifest from a
    raw Path (self.project_root). On Cloud that path was the gunicorn server
    cwd, not the project.

    After the fix, _all_manifest_relationships() reads via project.exists() /
    project.read_text(), so a CloudManagedProject-like seam is honored.
    """

    def test_manifest_load_uses_project_exists_not_raw_path(
        self, tmp_path: Path
    ) -> None:
        """Manifest load calls project.exists() — not Path.exists() on a raw path.

        A registry backed by _NoFilesystemProject raises if ANY raw filesystem
        read goes through os.path / Path. The resolver calls
        _all_manifest_relationships(), which must not raise — it must route
        through project.exists() which returns False (no manifest available).
        """
        from dbt_charts.core.inspect.cache_factory import build_resolver

        registry = AdapterRegistry(
            project=_NoFilesystemProject(),
            project_sources=ProjectSourcesConfig(sources={}),
        )
        resolver = build_resolver(registry)
        # Must not raise — exists() returns False, so no manifest is found.
        rels = resolver._all_manifest_relationships()  # noqa: SLF001
        assert rels == []

    def test_inspect_ignores_an_unusable_dbt_target_path(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """dbt accepts an absolute DBT_TARGET_PATH; inspect only enriches from the
        manifest, so it reads as no manifest rather than failing the profile."""
        from dbt_charts.cli.filesystem_project import FilesystemProject
        from dbt_charts.core.inspect.cache_factory import build_resolver
        from dbt_charts.core.inspect.sources.dbt import DbtSchemaSource

        monkeypatch.setenv("DBT_TARGET_PATH", "/ci/target")
        project = FilesystemProject(tmp_path)
        registry = AdapterRegistry(
            project=project, project_sources=ProjectSourcesConfig(sources={})
        )

        assert build_resolver(registry)._all_manifest_relationships() == []  # noqa: SLF001
        assert DbtSchemaSource(adapter=None, project=project)._load_manifest() is None  # noqa: SLF001

    def test_dbt_schema_source_reads_manifest_via_seam(self) -> None:
        """DbtSchemaSource uses project.exists()/read_text(), not raw Path reads."""
        from dbt_charts.core.inspect.sources.dbt import DbtSchemaSource

        project = _NoFilesystemProject()
        # DbtSchemaSource now takes project= (not project_root=).
        source = DbtSchemaSource(
            adapter=None,  # not called during manifest load check
            project=project,
        )
        # _load_manifest routes through project.exists() → False → None returned.
        result = source._load_manifest()  # noqa: SLF001
        assert result is None

    def test_manifest_hit_path_reads_via_project_read_text(self) -> None:
        """load_manifest returns parsed JSON from project.read_text().

        Covers the HIT path: exists() returns True, read_text() returns fixture
        JSON, and the result is the parsed LoadedManifest — not a raw filesystem
        read.
        """
        import dbt_charts.core.dbt_manifest as _manifest_mod
        from dbt_charts.core.dbt_manifest import load_manifest

        _manifest_mod._memo.clear()

        _FIXTURE = '{"nodes": {}}'

        class _HitProject(Project):
            @property
            def sources(self) -> ProjectSourcesConfig:
                return ProjectSourcesConfig(sources={})

            def exists(self, relpath: str) -> bool:
                return relpath == "target/manifest.json"

            def read_text(self, relpath: str) -> str:
                return _FIXTURE

            def read_bytes(self, relpath: str) -> bytes:
                return _FIXTURE.encode()

            def iter_files(self, under: str, *, recursive: bool) -> Iterator[str]:
                return iter(())

            def iter_dir(self, under: str) -> Iterator[ProjectPath | ProjectDirectory]:
                return iter(())

            def write_text(
                self, relpath: str, content: str
            ) -> None:  # pragma: no cover
                raise AssertionError("write_text should not be called")

            def delete_text(self, relpath: str) -> None:  # pragma: no cover
                raise AssertionError("delete_text should not be called")

        loaded = load_manifest(_HitProject())
        assert loaded is not None
        assert loaded.raw == {"nodes": {}}


class TestBuildAdapterRegistryNonFilesystemProject:
    """build_adapter_registry must not require a FilesystemProject.

    A non-filesystem host (Cloud's git-blob store; this test's seam-purity
    stub) has no `.root` to resolve relative DuckDB/SQLite paths against, and
    no real dbt_project.yml sibling check to make. build_adapter_registry must
    degrade to data_dir=None / no DbtAdapter instead of crashing on `.root`.
    """

    def test_registers_duckdb_adapter_with_no_data_dir(self) -> None:
        from dbt_charts.core.execute.adapters.adapter_registry import (
            build_adapter_registry,
        )
        from dbt_charts.core.execute.adapters.duckdb_adapter import DuckDBAdapter

        registry = build_adapter_registry(_NoFilesystemProject())

        duckdb_adapters = [a for a in registry.adapters if isinstance(a, DuckDBAdapter)]
        assert len(duckdb_adapters) == 1
        assert duckdb_adapters[0]._data_dir is None  # noqa: SLF001

    def test_registers_no_dbt_adapter(self) -> None:
        """_NoFilesystemProject.exists() always returns False, so there is no
        dbt_project.yml sibling to detect — no DbtAdapter should register,
        and detecting that must never touch `.root`."""
        from dbt_charts.core.execute.adapters.adapter_registry import (
            build_adapter_registry,
        )
        from dbt_charts.core.execute.adapters.dbt_adapter import DbtAdapter

        registry = build_adapter_registry(_NoFilesystemProject())

        assert not any(isinstance(a, DbtAdapter) for a in registry.adapters)
