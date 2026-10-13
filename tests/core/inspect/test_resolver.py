"""Tests for dbt_charts.core.inspect.resolver — LayeredSchemaResolver."""

from __future__ import annotations

from collections.abc import Callable, Iterator

import pytest

from dbt_charts.core.compile.config import ProjectSourcesConfig
from dbt_charts.core.execute.adapters import AdapterRegistry
from dbt_charts.core.inspect.resolver import LayeredSchemaResolver
from dbt_charts.core.project import Project, ProjectDirectory, ProjectPath


class _NonFilesystemProject(Project):
    """A Project stub that is not a FilesystemProject — stands in for a
    non-filesystem host (e.g. Cloud's git-blob store)."""

    @property
    def sources(self) -> ProjectSourcesConfig:
        return ProjectSourcesConfig(sources={})

    def read_text(self, relpath: str) -> str:
        raise AssertionError(f"unexpected read_text({relpath!r})")

    def read_bytes(self, relpath: str) -> bytes:
        raise AssertionError(f"unexpected read_bytes({relpath!r})")

    def exists(self, relpath: str) -> bool:
        return False

    def iter_files(
        self,
        under: str,
        *,
        recursive: bool,
        onerror: Callable[[str, OSError], None] | None = None,
    ) -> Iterator[str]:
        return iter(())

    def iter_dir(self, under: str) -> Iterator[ProjectPath | ProjectDirectory]:
        return iter(())

    def write_text(self, relpath: str, content: str) -> None:
        raise AssertionError(f"unexpected write_text({relpath!r})")

    def delete_text(self, relpath: str) -> None:
        raise AssertionError(f"unexpected delete_text({relpath!r})")


class TestDbtForSqliteWindowsAbsolutePath:
    """``_dbt_for`` must classify a real absolute Windows sqlite path as
    absolute, not route it through the relative-path branch.

    Regression: ``PurePosixPath(path).is_absolute()`` judges a drive-rooted
    path like ``C:\\...`` relative (no leading ``/``), so ``_dbt_for`` fell
    into the ``isinstance(self.project, FilesystemProject)`` branch — which
    raises for any non-filesystem host (Cloud) even though the path was
    already absolute. Drive with a literal string, not a real ``Path``, so
    the regression reproduces on any host, including POSIX CI, via the
    public ``list_schemas`` entry point (no real sqlite file needed:
    ``SQLiteSchemaSource.list_schemas`` is a static ``{"main": {}}`` and
    never opens a connection).
    """

    @pytest.mark.windows
    def test_absolute_windows_path_skips_filesystem_project_branch(self) -> None:
        registry = AdapterRegistry(
            project=_NonFilesystemProject(),
            project_sources=ProjectSourcesConfig(
                sources={"shop": {"type": "sqlite", "path": r"C:\Users\x\bird.sqlite"}}
            ),
        )
        resolver = LayeredSchemaResolver(
            cache=None, adapter_registry=registry, project=registry.project
        )

        # Must not raise — a non-FilesystemProject with an already-absolute
        # path never needs the relative-path resolution branch that requires
        # FilesystemProject.
        result = resolver.list_schemas("shop")
        assert result["sources"]["shop"]["schemas"] == {"main": {}}
