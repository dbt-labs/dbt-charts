"""Tests for dbt_charts.core.project.Project and FilesystemProject.

TDD: written before implementation — must fail until the ABC split exists.
"""

from __future__ import annotations

from collections.abc import Callable, Iterator
from functools import cached_property
from pathlib import Path, PurePosixPath, PureWindowsPath
from typing import Any

import pytest

from dbt_charts.cli.filesystem_project import FilesystemFileQueries, FilesystemProject
from dbt_charts.core.compile.config import ProjectSourcesConfig
from dbt_charts.core.diagnostics.execution import ExecutionError
from dbt_charts.core.project import (
    BOARD_CANDIDATE_SUFFIXES,
    SERVED_FILE_SUFFIXES,
    TEMPLATE_SUFFIXES,
    BoardRoot,
    GrepHit,
    Project,
    ProjectDirectory,
    ProjectPath,
    board_directory_candidates,
    board_namespace_for_root,
    board_relpath_candidates,
    board_root_for_relpath,
    board_roots,
    board_slug,
    is_absolute_any_os,
    is_board_candidate,
    is_package_relpath,
    is_private_name,
    iter_dir_from_relpaths,
    package_board_roots,
    posix_relpath,
)
from dbt_charts.core.utils import IMAGE_SUFFIXES

from ..conftest import InMemoryFileQueries


class TestIsPrivateName:
    def test_underscore_prefix_is_private(self) -> None:
        assert is_private_name("_partial.yml") is True

    def test_nested_underscore_basename_is_private(self) -> None:
        """Basename-only: the leading _ on the filename is what matters."""
        assert is_private_name("charts/_partial.yml") is True

    def test_non_underscore_prefix_is_not_private(self) -> None:
        assert is_private_name("revenue.yml") is False

    def test_underscore_in_directory_but_not_basename_is_not_private(self) -> None:
        """_drafts/foo.yml: foo.yml has no leading _, so it is NOT private by this rule."""
        assert is_private_name("_drafts/foo.yml") is False


class TestIsBoardCandidate:
    def test_standard_yml_is_candidate(self) -> None:
        assert is_board_candidate("charts/revenue.yml") is True

    def test_yaml_suffix_is_candidate(self) -> None:
        assert is_board_candidate("charts/revenue.yaml") is True

    def test_markdown_suffix_is_candidate(self) -> None:
        assert is_board_candidate("charts/notes.md") is True

    def test_markdown_long_suffix_is_candidate(self) -> None:
        assert is_board_candidate("charts/notes.markdown") is True

    def test_underscore_prefix_is_not_candidate(self) -> None:
        assert is_board_candidate("charts/_partial.yml") is False

    def test_underscore_dir_with_plain_basename_is_candidate(self) -> None:
        assert is_board_candidate("_drafts/foo.yml") is True

    def test_dotfile_named_yaml_is_not_candidate(self) -> None:
        """.yaml is a dotfile — PurePosixPath('.yaml').suffix == '' so it is NOT a candidate."""
        assert is_board_candidate(".yaml") is False

    def test_csv_is_not_candidate(self) -> None:
        assert is_board_candidate("data.csv") is False


class TestIterBoards:
    def test_excludes_dotfile_named_yaml(
        self, tmp_path: Path, local_project: Callable[..., FilesystemProject]
    ) -> None:
        """iter_boards filters on .suffix, so a bare .yaml dotfile is not a board."""
        (tmp_path / "charts").mkdir()
        (tmp_path / "charts" / ".yaml").write_text("")
        (tmp_path / "charts" / "real.yaml").write_text("title: R\n")
        relpaths = [pf.relpath for pf in local_project(tmp_path).iter_boards()]
        assert "charts/real.yaml" in relpaths
        assert "charts/.yaml" not in relpaths

    def test_excludes_private_underscore_board(
        self, tmp_path: Path, local_project: Callable[..., FilesystemProject]
    ) -> None:
        (tmp_path / "charts").mkdir()
        (tmp_path / "charts" / "_partial.yml").write_text("")
        (tmp_path / "charts" / "revenue.yml").write_text("title: R\n")
        relpaths = [pf.relpath for pf in local_project(tmp_path).iter_boards()]
        assert "charts/revenue.yml" in relpaths
        assert "charts/_partial.yml" not in relpaths

    def test_excludes_dbt_noise_dirs(
        self, tmp_path: Path, local_project: Callable[..., FilesystemProject]
    ) -> None:
        """A dbt project root is a valid dbt charts root; dbt's generated dirs
        (dbt_packages vendored .yml/.md, target compiled copies, logs) must not
        be swept into board enumeration."""
        for noise in ("dbt_packages/some_pkg", "target/compiled", "logs"):
            (tmp_path / noise).mkdir(parents=True)
        (tmp_path / "dbt_packages" / "some_pkg" / "schema.yml").write_text("a: 1\n")
        (tmp_path / "dbt_packages" / "some_pkg" / "README.md").write_text("# pkg\n")
        (tmp_path / "target" / "compiled" / "board.yml").write_text("title: T\n")
        (tmp_path / "logs" / "notes.md").write_text("# log\n")
        (tmp_path / "revenue.yml").write_text("title: R\n")

        relpaths = [pf.relpath for pf in local_project(tmp_path).iter_boards(under=".")]
        assert relpaths == ["revenue.yml"]


class TestProjectConstruction:
    def test_root_is_resolved(
        self, tmp_path: Path, local_project: Callable[..., FilesystemProject]
    ) -> None:
        """root must be an absolute, resolved Path."""
        p = local_project(tmp_path)
        assert p.root == tmp_path.resolve()
        assert p.root.is_absolute()

    def test_root_resolves_relative_path(
        self,
        tmp_path: Path,
        monkeypatch,
        local_project: Callable[..., FilesystemProject],
    ) -> None:
        """Even a relative path gets resolved to absolute."""
        monkeypatch.chdir(tmp_path)
        p = local_project(Path())
        assert p.root.is_absolute()


class TestChartsDirCachedProperty:
    def test_charts_dir_is_root_slash_charts(
        self, tmp_path: Path, local_project: Callable[..., FilesystemProject]
    ) -> None:
        """charts_dir must be project root / 'charts', resolved."""
        p = local_project(tmp_path)
        assert p.charts_dir == tmp_path.resolve() / "charts"


class TestSourcesCachedProperty:
    def test_sources_lazy_and_cached(
        self,
        tmp_path: Path,
        monkeypatch,
        local_project: Callable[..., FilesystemProject],
    ) -> None:
        """Loader not called at construction; called exactly once across reads."""
        from dbt_charts.core.compile import config as config_module

        sentinel = ProjectSourcesConfig(sources={})
        call_count = [0]

        def fake_loader(project: object) -> ProjectSourcesConfig:
            call_count[0] += 1
            return sentinel

        monkeypatch.setattr(config_module, "load_project_sources", fake_loader)
        p = local_project(tmp_path)
        assert call_count[0] == 0
        first = p.sources
        second = p.sources
        assert first is sentinel
        assert second is sentinel
        assert call_count[0] == 1


class TestReadText:
    def test_read_text_returns_file_contents(
        self, tmp_path: Path, local_project: Callable[..., FilesystemProject]
    ) -> None:
        (tmp_path / "hello.txt").write_text("hello world")
        p = local_project(tmp_path)
        assert p.read_text("hello.txt") == "hello world"

    def test_read_text_accepts_relpath_with_subdir(
        self, tmp_path: Path, local_project: Callable[..., FilesystemProject]
    ) -> None:
        sub = tmp_path / "sub"
        sub.mkdir()
        (sub / "data.txt").write_text("nested")
        p = local_project(tmp_path)
        assert p.read_text("sub/data.txt") == "nested"

    def test_read_text_missing_file_raises(
        self, tmp_path: Path, local_project: Callable[..., FilesystemProject]
    ) -> None:
        p = local_project(tmp_path)
        with pytest.raises(FileNotFoundError):
            p.read_text("nonexistent.txt")

    def test_read_text_rejects_escaping_relpath(
        self, tmp_path: Path, local_project: Callable[..., FilesystemProject]
    ) -> None:
        p = local_project(tmp_path)
        with pytest.raises(ValueError, match="escape"):
            p.read_text("../x")


class TestReadBytes:
    def test_read_bytes_returns_file_contents(
        self, tmp_path: Path, local_project: Callable[..., FilesystemProject]
    ) -> None:
        (tmp_path / "hello.bin").write_bytes(b"hello world")
        p = local_project(tmp_path)
        assert p.read_bytes("hello.bin") == b"hello world"

    def test_read_bytes_rejects_escaping_relpath(
        self, tmp_path: Path, local_project: Callable[..., FilesystemProject]
    ) -> None:
        p = local_project(tmp_path)
        with pytest.raises(ValueError, match="escape"):
            p.read_bytes("../x")


class TestReadYaml:
    def test_read_yaml_returns_parsed_dict(
        self, tmp_path: Path, local_project: Callable[..., FilesystemProject]
    ) -> None:
        (tmp_path / "config.yml").write_text("key: value\nnum: 42\n")
        p = local_project(tmp_path)
        result = p.read_yaml("config.yml")
        assert result == {"key": "value", "num": 42}

    def test_read_yaml_empty_file_returns_none(
        self, tmp_path: Path, local_project: Callable[..., FilesystemProject]
    ) -> None:
        (tmp_path / "empty.yml").write_text("")
        p = local_project(tmp_path)
        assert p.read_yaml("empty.yml") is None

    def test_read_yaml_missing_file_raises(
        self, tmp_path: Path, local_project: Callable[..., FilesystemProject]
    ) -> None:
        p = local_project(tmp_path)
        with pytest.raises(FileNotFoundError):
            p.read_yaml("nonexistent.yml")


class TestExists:
    def test_exists_returns_true_for_existing_file(
        self, tmp_path: Path, local_project: Callable[..., FilesystemProject]
    ) -> None:
        (tmp_path / "present.txt").write_text("x")
        p = local_project(tmp_path)
        assert p.exists("present.txt") is True

    def test_exists_returns_false_for_missing_file(
        self, tmp_path: Path, local_project: Callable[..., FilesystemProject]
    ) -> None:
        p = local_project(tmp_path)
        assert p.exists("absent.txt") is False

    def test_exists_works_with_subdir_path(
        self, tmp_path: Path, local_project: Callable[..., FilesystemProject]
    ) -> None:
        sub = tmp_path / "sub"
        sub.mkdir()
        (sub / "file.txt").write_text("x")
        p = local_project(tmp_path)
        assert p.exists("sub/file.txt") is True
        assert p.exists("sub/missing.txt") is False

    def test_exists_rejects_escaping_relpath(
        self, tmp_path: Path, local_project: Callable[..., FilesystemProject]
    ) -> None:
        p = local_project(tmp_path)
        with pytest.raises(ValueError, match="escape"):
            p.exists("../x")


class TestDeleteText:
    def test_delete_text_removes_existing_file(
        self, tmp_path: Path, local_project: Callable[..., FilesystemProject]
    ) -> None:
        (tmp_path / "present.txt").write_text("x")
        p = local_project(tmp_path)
        p.delete_text("present.txt")
        assert not (tmp_path / "present.txt").exists()

    def test_delete_text_missing_file_raises(
        self, tmp_path: Path, local_project: Callable[..., FilesystemProject]
    ) -> None:
        p = local_project(tmp_path)
        with pytest.raises(FileNotFoundError):
            p.delete_text("absent.txt")

    def test_delete_text_works_with_subdir_path(
        self, tmp_path: Path, local_project: Callable[..., FilesystemProject]
    ) -> None:
        sub = tmp_path / "sub"
        sub.mkdir()
        (sub / "file.txt").write_text("x")
        p = local_project(tmp_path)
        p.delete_text("sub/file.txt")
        assert not (sub / "file.txt").exists()

    def test_delete_text_rejects_escaping_relpath(
        self, tmp_path: Path, local_project: Callable[..., FilesystemProject]
    ) -> None:
        outside = tmp_path.parent / "escape_target.txt"
        outside.write_text("x")
        p = local_project(tmp_path)
        with pytest.raises(ValueError, match="escape"):
            p.delete_text("../escape_target.txt")
        assert outside.exists()


class TestPackageBoardsReadOnly:
    def test_write_text_refuses_package_relpath(
        self, tmp_path: Path, local_project: Callable[..., FilesystemProject]
    ) -> None:
        pkg_charts = tmp_path / "dbt_packages" / "pkg_a" / "charts"
        pkg_charts.mkdir(parents=True)
        (pkg_charts / "overview.yml").write_text("title: O\n")
        p = local_project(tmp_path)
        with pytest.raises(ValueError, match="read-only"):
            p.write_text("dbt_packages/pkg_a/charts/overview.yml", "title: New\n")
        assert (pkg_charts / "overview.yml").read_text() == "title: O\n"

    def test_delete_text_refuses_package_relpath(
        self, tmp_path: Path, local_project: Callable[..., FilesystemProject]
    ) -> None:
        pkg_charts = tmp_path / "dbt_packages" / "pkg_a" / "charts"
        pkg_charts.mkdir(parents=True)
        (pkg_charts / "overview.yml").write_text("title: O\n")
        p = local_project(tmp_path)
        with pytest.raises(ValueError, match="read-only"):
            p.delete_text("dbt_packages/pkg_a/charts/overview.yml")
        assert (pkg_charts / "overview.yml").exists()

    def test_write_text_still_works_for_host_charts(
        self, tmp_path: Path, local_project: Callable[..., FilesystemProject]
    ) -> None:
        (tmp_path / "charts").mkdir()
        p = local_project(tmp_path)
        p.write_text("charts/home.yml", "title: H\n")
        assert (tmp_path / "charts" / "home.yml").read_text() == "title: H\n"

    def test_write_text_refuses_a_dotdot_escape_into_a_package(
        self, tmp_path: Path, local_project: Callable[..., FilesystemProject]
    ) -> None:
        """Regression: is_package_relpath only inspected the raw relpath's
        first component, and nothing normalized ".." before that check -- so
        "charts/../dbt_packages/pkg_a/charts/overview.yml" (first component
        "charts") passed the guard, then resolve() collapsed the ".." back
        onto the package file and the write succeeded. The guard must check
        the resolved path, not the raw string."""
        (tmp_path / "charts").mkdir()
        pkg_charts = tmp_path / "dbt_packages" / "pkg_a" / "charts"
        pkg_charts.mkdir(parents=True)
        (pkg_charts / "overview.yml").write_text("title: O\n")
        p = local_project(tmp_path)
        with pytest.raises(ValueError, match="read-only"):
            p.write_text(
                "charts/../dbt_packages/pkg_a/charts/overview.yml", "title: New\n"
            )
        assert (pkg_charts / "overview.yml").read_text() == "title: O\n"

    def test_delete_text_refuses_a_dotdot_escape_into_a_package(
        self, tmp_path: Path, local_project: Callable[..., FilesystemProject]
    ) -> None:
        (tmp_path / "charts").mkdir()
        pkg_charts = tmp_path / "dbt_packages" / "pkg_a" / "charts"
        pkg_charts.mkdir(parents=True)
        (pkg_charts / "overview.yml").write_text("title: O\n")
        p = local_project(tmp_path)
        with pytest.raises(ValueError, match="read-only"):
            p.delete_text("charts/../dbt_packages/pkg_a/charts/overview.yml")
        assert (pkg_charts / "overview.yml").exists()


class TestPackageChartsDirs:
    def test_lists_absolute_paths_of_every_package_charts_dir(
        self, tmp_path: Path, local_project: Callable[..., FilesystemProject]
    ) -> None:
        pkg_charts = tmp_path / "dbt_packages" / "pkg_a" / "charts"
        pkg_charts.mkdir(parents=True)
        p = local_project(tmp_path)
        assert p.package_charts_dirs() == [pkg_charts.resolve()]

    def test_empty_when_no_packages_ship_charts(
        self, tmp_path: Path, local_project: Callable[..., FilesystemProject]
    ) -> None:
        p = local_project(tmp_path)
        assert p.package_charts_dirs() == []


class TestDataPath:
    """data_path is FilesystemProject-specific (no base-Project equivalent)."""

    def test_data_path_returns_root_slash_relpath(
        self, tmp_path: Path, local_project: Callable[..., FilesystemProject]
    ) -> None:
        p = local_project(tmp_path)
        result = p.data_path("data/foo.csv")
        assert result == tmp_path.resolve() / "data" / "foo.csv"

    def test_data_path_dot_returns_root(
        self, tmp_path: Path, local_project: Callable[..., FilesystemProject]
    ) -> None:
        p = local_project(tmp_path)
        assert p.data_path(".") == tmp_path.resolve()

    def test_data_path_nonexistent_does_not_raise(
        self, tmp_path: Path, local_project: Callable[..., FilesystemProject]
    ) -> None:
        p = local_project(tmp_path)
        result = p.data_path("nonexistent/file.csv")
        assert not result.exists()

    def test_data_path_permits_escaping_relpath(
        self, tmp_path: Path, local_project: Callable[..., FilesystemProject]
    ) -> None:
        # data_path is permissive by contract: DuckDB/SQLite paths come from
        # trusted authored config, and a cross-project relative path (e.g. a
        # shared dev database) is legitimate. Unlike the read methods, it does
        # NOT reject an escaping relpath.
        p = local_project(tmp_path)
        result = p.data_path("../shared/db.duckdb")
        assert result == (tmp_path.resolve().parent / "shared" / "db.duckdb")
        assert not result.is_relative_to(tmp_path.resolve())


class TestServedFileSuffixes:
    """SERVED_FILE_SUFFIXES is the closed set of types Cloud fetches from a repo.

    It is keyed on by an extension-only git-blob-fetch allowlist that reads no
    blob content, so a board/source type whose extension is missing here would be
    silently not-fetched. These guards pin completeness and prevent drift.
    """

    def test_every_board_suffix_is_served(self) -> None:
        # The drift guard: a new board suffix (e.g. .markdown) must not slip out
        # of the served set, or those boards silently fail to fetch on Cloud.
        assert set(BOARD_CANDIDATE_SUFFIXES) <= SERVED_FILE_SUFFIXES

    def test_file_source_extensions_are_served(self) -> None:
        # File-source data files (paths are extension-validated to these) must
        # be fetchable.
        assert {".csv", ".json", ".parquet"} <= SERVED_FILE_SUFFIXES
        assert ".lkml" not in SERVED_FILE_SUFFIXES

    def test_image_asset_extensions_are_served(self) -> None:
        # Committed images referenced from markdown/boards are fetched as assets.
        assert set(IMAGE_SUFFIXES) <= SERVED_FILE_SUFFIXES

    def test_template_suffixes_are_served(self) -> None:
        # `type: custom` reads templates/<name>.svg.j2 through the project;
        # Cloud never sees a template whose suffix is missing here.
        assert set(TEMPLATE_SUFFIXES) <= SERVED_FILE_SUFFIXES
        assert "templates/flow.svg.j2".endswith(TEMPLATE_SUFFIXES)

    def test_suffixes_are_lowercase_with_leading_dot(self) -> None:
        # The fetch compares name.lower().endswith(...), so the set must be
        # lowercase and dotted or the comparison silently never matches.
        assert all(s == s.lower() and s.startswith(".") for s in SERVED_FILE_SUFFIXES)


class _MinimalFilePrimitivesProject(Project):
    """Implements only the plain-abstractmethod file primitives, omitting the
    cached_property + abstractmethod members (`sources`/`files`/`name`).

    Used to prove those three enforce abstractness the same way: ABCMeta
    can't block construction (only plain `property` forwards
    `__isabstractmethod__`), so a subclass like this one constructs fine and
    each missing override must instead fail loudly on first access.
    """

    def exists(self, relpath: str) -> bool:
        return False

    def read_text(self, relpath: str) -> str:
        raise FileNotFoundError(relpath)

    def read_bytes(self, relpath: str) -> bytes:
        raise FileNotFoundError(relpath)

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
        raise NotImplementedError

    def delete_text(self, relpath: str) -> None:
        raise NotImplementedError


class TestProjectIsAbstract:
    def test_project_cannot_be_instantiated_directly(self) -> None:
        with pytest.raises(TypeError):
            Project()  # type: ignore[abstract]

    def test_subclass_omitting_sources_constructs_but_raises_on_access(self) -> None:
        project = _MinimalFilePrimitivesProject()
        with pytest.raises(NotImplementedError, match="must implement `sources`"):
            _ = project.sources

    def test_subclass_omitting_name_constructs_but_raises_on_access(self) -> None:
        """Same cached_property + abstractmethod enforcement shape as
        `sources`/`files`: constructs without an override, fails loudly the
        first time `.name` is accessed."""
        project = _MinimalFilePrimitivesProject()
        with pytest.raises(NotImplementedError, match="must implement `name`"):
            _ = project.name

    def test_manifest_project_default_returns_self(self) -> None:
        """A base-Project subclass with no override answers `manifest_project()`
        with itself, same non-abstract-default shape as `config_document()`."""
        project = _MinimalFilePrimitivesProject()
        assert project.manifest_project() is project


class _InMemoryProject(Project):
    """Minimal in-memory Project: the file-access primitives + sources, no disk access."""

    def __init__(self, files: dict[str, str]) -> None:
        self._files = files

    def exists(self, relpath: str) -> bool:
        return relpath in self._files

    def read_text(self, relpath: str) -> str:
        if relpath not in self._files:
            raise FileNotFoundError(relpath)
        return self._files[relpath]

    def read_bytes(self, relpath: str) -> bytes:
        return self.read_text(relpath).encode()

    def iter_files(
        self,
        under: str,
        *,
        recursive: bool,
        onerror: Callable[[str, OSError], None] | None = None,
    ) -> Iterator[str]:
        prefix = "" if under == "." else under.rstrip("/") + "/"
        for relpath in sorted(self._files):
            if not relpath.startswith(prefix):
                continue
            remainder = relpath[len(prefix) :]
            if not recursive and "/" in remainder:
                continue
            yield relpath

    def iter_dir(self, under: str) -> Iterator[ProjectPath | ProjectDirectory]:
        return iter_dir_from_relpaths(self, under, self._files)

    def write_text(self, relpath: str, content: str) -> None:
        self._files[relpath] = content

    def delete_text(self, relpath: str) -> None:
        if relpath not in self._files:
            raise FileNotFoundError(relpath)
        del self._files[relpath]

    @property
    def sources(self) -> ProjectSourcesConfig:
        return ProjectSourcesConfig(sources={})

    @cached_property
    def files(self) -> InMemoryFileQueries:
        return InMemoryFileQueries(self)


class TestMinimalProjectSubclass:
    def test_handles_read_through_in_memory_project_without_disk(self) -> None:
        """A non-filesystem Project works through ProjectPath/ProjectDirectory."""
        project = _InMemoryProject({"charts/revenue.yml": "title: Revenue\n"})
        handle = project.directory() / "charts/revenue.yml"
        assert handle.exists() is True
        assert handle.read_yaml() == {"title": "Revenue"}

    def test_iter_boards_applies_filters_without_disk(self) -> None:
        project = _InMemoryProject(
            {
                "charts/revenue.yml": "title: Revenue\n",
                "charts/_partial.yml": "",
                "data/ignored.csv": "a,b\n",
            },
        )
        relpaths = [pf.relpath for pf in project.iter_boards()]
        assert relpaths == ["charts/revenue.yml"]

    def test_grep_reads_via_seam_not_root(self) -> None:
        """FilesystemFileQueries.grep sources bytes from the Project seam.

        _InMemoryProject holds no filesystem root at all — content lives only
        in the dict store. A hit proves grep reads through read_bytes, not a
        filesystem path.
        """
        project = _InMemoryProject({"charts/a.yml": "title: needle\n"})
        hits = list(FilesystemFileQueries(project).grep("needle"))
        assert [(h.relpath, h.line) for h in hits] == [
            ("charts/a.yml", "title: needle")
        ]


class TestProjectDirectoryParent:
    def test_nested_directory_parent(
        self, tmp_path: Path, local_project: Callable[..., FilesystemProject]
    ) -> None:
        project = local_project(tmp_path)
        directory: ProjectDirectory = project.directory("a/b")
        assert directory.parent.relpath == "a"

    def test_root_directory_is_its_own_parent(
        self, tmp_path: Path, local_project: Callable[..., FilesystemProject]
    ) -> None:
        project = local_project(tmp_path)
        directory: ProjectDirectory = project.directory(".")
        assert directory.parent.relpath == "."


_QUERY_CORPUS = {
    "dbt_charts.yml": "project: demo\n",
    "charts/rev.yml": "charts: {}\n",
    "charts/sub/deep.yml": "title: deep\n",
    "charts/notes.md": "# revenue notes\n",
    "models/one.sql": "SELECT 1\n",
}


class TestProjectFileQueriesContract:
    """The Project.files query-seam contract, exercised on a non-filesystem host.

    Every host implementation (filesystem, Cloud git-blob, dict double) must
    satisfy these; they are written against the dict double so nothing here
    can accidentally lean on disk behavior.
    """

    @pytest.fixture
    def project(
        self,
        in_memory_project: type,
        tmp_path: Path,
    ) -> Project:
        return in_memory_project(tmp_path, dict(_QUERY_CORPUS))

    def test_glob_yields_sorted_project_path_handles(self, project: Project) -> None:
        hits = list(project.files.glob("charts/**/*.yml"))
        assert [p.relpath for p in hits] == ["charts/rev.yml", "charts/sub/deep.yml"]
        assert all(isinstance(p, ProjectPath) for p in hits)
        # Handles read through the owning project — no disk involved.
        assert hits[0].read_text() == "charts: {}\n"

    def test_glob_star_does_not_cross_path_segments(self, project: Project) -> None:
        assert [p.relpath for p in project.files.glob("charts/*.yml")] == [
            "charts/rev.yml"
        ]

    def test_glob_question_mark_matches_one_character(self, project: Project) -> None:
        assert [p.relpath for p in project.files.glob("charts/re?.yml")] == [
            "charts/rev.yml"
        ]

    def test_glob_rejects_absolute_pattern(self, project: Project) -> None:
        with pytest.raises(ValueError, match="must be relative"):
            list(project.files.glob("/etc/*"))

    def test_glob_rejects_escaping_pattern(self, project: Project) -> None:
        with pytest.raises(ValueError, match="escape"):
            list(project.files.glob("../*.yml"))

    def test_glob_skip_dir_literal_prefix_yields_nothing(
        self, project: Project
    ) -> None:
        """A pattern rooted inside a skip dir matches nothing on any host —
        the guard operates on PROJECT-relative paths, at the query layer."""
        assert list(project.files.glob("node_modules/*.json")) == []

    def test_grep_yields_hits_in_path_order(self, project: Project) -> None:
        hits = list(project.files.grep("revenue"))
        assert hits == [GrepHit("charts/notes.md", 1, "# revenue notes")]

    def test_grep_is_case_sensitive_substring(self, project: Project) -> None:
        assert list(project.files.grep("REVENUE")) == []

    def test_grep_ignore_case_folds_ascii_letters_only(
        self, in_memory_project: type, tmp_path: Path
    ) -> None:
        corpus = dict(_QUERY_CORPUS, **{"charts/accent.yml": "title: Éclair\n"})
        project = in_memory_project(tmp_path, corpus)
        assert list(project.files.grep("REVENUE", ignore_case=True)) == [
            GrepHit("charts/notes.md", 1, "# revenue notes")
        ]
        assert list(project.files.grep("title: éclair", ignore_case=True)) == []

    def test_grep_glob_narrows_the_search(self, project: Project) -> None:
        assert list(project.files.grep("SELECT", glob="charts/**/*")) == []
        hits = list(project.files.grep("SELECT", glob="models/*.sql"))
        assert [h.relpath for h in hits] == ["models/one.sql"]

    def test_grep_reports_line_numbers(
        self,
        in_memory_project: type,
        tmp_path: Path,
    ) -> None:
        corpus = dict(_QUERY_CORPUS, **{"charts/multi.yml": "a: 1\nb: needle\n"})
        project = in_memory_project(tmp_path, corpus)
        hits = list(project.files.grep("needle"))
        assert hits == [GrepHit("charts/multi.yml", 2, "b: needle")]

    def test_grep_skips_files_over_host_size_cap(self) -> None:
        """A host that raises ExecutionError for an oversized file (e.g.
        Cloud's git-blob size-cap guard) must be skipped, not propagated —
        the same "skipped, never raised" contract as OSError/UnicodeDecodeError."""

        class _SizeCappedProject(Project):
            _corpus = {
                "charts/rev.yml": "revenue: 1\n",
                "charts/huge.yml": "revenue: 2\n",
            }

            def exists(self, relpath: str) -> bool:
                return relpath in self._corpus

            def read_text(self, relpath: str) -> str:
                if relpath == "charts/huge.yml":
                    raise ExecutionError("file exceeds host size cap")
                return self._corpus[relpath]

            def read_bytes(self, relpath: str) -> bytes:
                return self.read_text(relpath).encode()

            def iter_files(
                self,
                under: str,
                *,
                recursive: bool,
                onerror: Callable[[str, OSError], None] | None = None,
            ) -> Iterator[str]:
                return iter(sorted(self._corpus))

            def iter_dir(self, under: str) -> Iterator[ProjectPath | ProjectDirectory]:
                return iter(())

            def write_text(self, relpath: str, content: str) -> None:
                raise NotImplementedError

            def delete_text(self, relpath: str) -> None:
                raise NotImplementedError

            @property
            def sources(self) -> ProjectSourcesConfig:
                return ProjectSourcesConfig(sources={})

            @cached_property
            def files(self) -> InMemoryFileQueries:
                return InMemoryFileQueries(self)

        project = _SizeCappedProject()
        hits = list(project.files.grep("revenue"))
        assert [h.relpath for h in hits] == ["charts/rev.yml"]

    def test_files_is_abstract_and_fails_loudly_when_omitted(self) -> None:
        """Same enforcement shape as `sources`: constructs, raises on access."""

        class _NoQueriesProject(Project):
            def exists(self, relpath: str) -> bool:
                return False

            def read_text(self, relpath: str) -> str:
                raise FileNotFoundError(relpath)

            def read_bytes(self, relpath: str) -> bytes:
                raise FileNotFoundError(relpath)

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
                raise NotImplementedError

            def delete_text(self, relpath: str) -> None:
                raise NotImplementedError

            @property
            def sources(self) -> ProjectSourcesConfig:
                return ProjectSourcesConfig(sources={})

        project = _NoQueriesProject()
        with pytest.raises(NotImplementedError, match="must implement `files`"):
            _ = project.files


class TestFilesystemProjectQueries:
    """FilesystemProject's native query implementation (FilesystemFileQueries):
    it owns glob directly (iter_files-backed) and grep is a bytes-scan — read
    the file once as bytes, ``bytes.find`` for the needle, decode only the
    files that actually contain it. Host-specific contract points:
    SKIP_SCAN_DIRS exclusion and unreadable-file tolerance."""

    @pytest.fixture
    def project(self, tmp_path: Path) -> FilesystemProject:
        (tmp_path / "charts" / "sub").mkdir(parents=True)
        (tmp_path / "node_modules" / "pkg").mkdir(parents=True)
        (tmp_path / "dbt_charts.yml").write_text("project: demo\n")
        (tmp_path / "charts" / "rev.yml").write_text("charts: {}\n")
        (tmp_path / "charts" / "sub" / "deep.yml").write_text("title: deep\n")
        (tmp_path / "charts" / "blob.yml").write_bytes(b"\xff\xfe\x00bad")
        (tmp_path / "node_modules" / "pkg" / "x.yml").write_text("skip: me\n")
        return FilesystemProject(tmp_path)

    def test_glob_excludes_skip_scan_dirs(self, project: FilesystemProject) -> None:
        relpaths = [p.relpath for p in project.files.glob("**/*.yml")]
        assert "node_modules/pkg/x.yml" not in relpaths
        assert "charts/rev.yml" in relpaths

    def test_glob_skip_dir_literal_prefix_yields_nothing(
        self, project: FilesystemProject
    ) -> None:
        assert list(project.files.glob("node_modules/**/*.yml")) == []

    def test_grep_skips_undecodable_files(self, project: FilesystemProject) -> None:
        hits = list(project.files.grep("title"))
        assert [h.relpath for h in hits] == ["charts/sub/deep.yml"]

    def test_grep_ignore_case_matches_the_line_as_authored(
        self, project: FilesystemProject
    ) -> None:
        hits = list(project.files.grep("TITLE: DEEP", ignore_case=True))
        assert hits == [GrepHit("charts/sub/deep.yml", 1, "title: deep")]

    def test_queries_are_the_native_filesystem_implementation(
        self, project: FilesystemProject
    ) -> None:
        """FilesystemProject.files is the native filesystem implementation."""
        assert isinstance(project.files, FilesystemFileQueries)

    def test_grep_never_decodes_a_no_match_file(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """The bytes-scan does one `bytes.find` per file before ever decoding —
        a file with no occurrence of the needle must never pay for a decode."""
        (tmp_path / "charts").mkdir()
        (tmp_path / "dbt_charts.yml").write_text("project: demo\n")
        (tmp_path / "charts" / "a.yml").write_text("alpha: 1\n")
        (tmp_path / "charts" / "b.yml").write_text("beta: needle\n")
        (tmp_path / "charts" / "c.yml").write_text("gamma: 3\n")
        project = FilesystemProject(tmp_path)

        decode_calls = {"count": 0}
        original_read_bytes = Path.read_bytes

        class _CountingDecodeBytes(bytes):
            def decode(self, *args: Any, **kwargs: Any) -> str:
                decode_calls["count"] += 1
                return super().decode(*args, **kwargs)

        def counting_read_bytes(self: Path) -> bytes:
            return _CountingDecodeBytes(original_read_bytes(self))

        monkeypatch.setattr(Path, "read_bytes", counting_read_bytes)

        hits = list(project.files.grep("needle"))

        assert [h.relpath for h in hits] == ["charts/b.yml"]
        assert decode_calls["count"] == 1

    def test_grep_skips_undecodable_file_with_earlier_hit(self, tmp_path: Path) -> None:
        """A file whose invalid bytes follow valid matching lines must be
        skipped whole — no partial hits from the valid prefix."""
        (tmp_path / "charts").mkdir()
        (tmp_path / "dbt_charts.yml").write_text("project: demo\n")
        payload = b"title: term\nbody: keep\n" + b"\xff\xfe\n"
        (tmp_path / "charts" / "mixed.yml").write_bytes(payload)
        project = FilesystemProject(tmp_path)

        assert list(project.files.grep("term")) == []

    def test_grep_includes_nul_containing_valid_utf8_line(self, tmp_path: Path) -> None:
        """A NUL byte doesn't make a file binary — valid UTF-8 containing \\x00
        must still be scanned; there is no NUL short-circuit."""
        (tmp_path / "charts").mkdir()
        (tmp_path / "dbt_charts.yml").write_text("project: demo\n")
        (tmp_path / "charts" / "nul.yml").write_bytes(b"has\x00null: term\n")
        project = FilesystemProject(tmp_path)

        hits = list(project.files.grep("term"))

        assert hits == [GrepHit("charts/nul.yml", 1, "has\x00null: term")]

    def test_grep_strips_crlf_line_ending(self, tmp_path: Path) -> None:
        (tmp_path / "charts").mkdir()
        (tmp_path / "dbt_charts.yml").write_text("project: demo\n")
        (tmp_path / "charts" / "crlf.yml").write_bytes(b"a\r\nb: term\r\n")
        project = FilesystemProject(tmp_path)

        hits = list(project.files.grep("term"))

        assert hits == [GrepHit("charts/crlf.yml", 2, "b: term")]

    def test_grep_multibyte_term(self, tmp_path: Path) -> None:
        """bytes-scan pre-filter is sound for arbitrary UTF-8 terms.

        `term.encode("utf-8") in data` works for multi-byte sequences because
        UTF-8 is self-synchronizing: no byte of one code point is a valid
        suffix of another, so a substring search in byte space is identical to
        a substring search in decoded string space.
        """
        (tmp_path / "charts").mkdir()
        (tmp_path / "dbt_charts.yml").write_text("project: demo\n")
        (tmp_path / "charts" / "intl.yml").write_text(
            "greeting: café\nother: 日本語\n", encoding="utf-8"
        )
        project = FilesystemProject(tmp_path)

        café_hits = list(project.files.grep("café"))
        jp_hits = list(project.files.grep("日本語"))

        assert café_hits == [GrepHit("charts/intl.yml", 1, "greeting: café")]
        assert jp_hits == [GrepHit("charts/intl.yml", 2, "other: 日本語")]


class TestProjectPathLexicalAccessors:
    """`name`/`stem`/`is_meta`/ordering — PurePosixPath lexical math
    exposed as ProjectPath properties, so call sites stop re-deriving them."""

    def test_name_stem_for_nested_relpath(
        self, tmp_path: Path, in_memory_project: Callable[..., Project]
    ) -> None:
        project = in_memory_project(tmp_path, {"charts/sub/report.yml": "title: R\n"})
        pf = ProjectPath(project, "charts/sub/report.yml")
        assert pf.name == "report.yml"
        assert pf.stem == "report"

    def test_is_meta_true_for_meta_yaml(
        self, tmp_path: Path, in_memory_project: Callable[..., Project]
    ) -> None:
        project = in_memory_project(tmp_path, {"charts/meta.yml": "sources: {}\n"})
        pf = ProjectPath(project, "charts/meta.yml")
        assert pf.is_meta is True

    def test_is_meta_false_for_normal_board(
        self, tmp_path: Path, in_memory_project: Callable[..., Project]
    ) -> None:
        project = in_memory_project(tmp_path, {"charts/revenue.yml": "title: R\n"})
        pf = ProjectPath(project, "charts/revenue.yml")
        assert pf.is_meta is False

    def test_ordering_matches_posix_parts_not_str(
        self, tmp_path: Path, in_memory_project: Callable[..., Project]
    ) -> None:
        """`a-b.yml` sorts before `a/b.yml` by plain string comparison ('-' <
        '/'), but PurePosixPath.parts ordering puts `a/b.yml` first (`('a',
        'b.yml')` vs `('a-b.yml',)`: 'a' < 'a-b.yml'). Assert we reproduce the
        parts order, proving __lt__ isn't a str-sort in disguise."""
        relpaths = ["a-b.yml", "a/b.yml"]
        project = in_memory_project(tmp_path, dict.fromkeys(relpaths, ""))
        paths = [ProjectPath(project, rp) for rp in relpaths]

        parts_order = sorted(relpaths, key=lambda rp: PurePosixPath(rp).parts)
        assert sorted(relpaths) != parts_order  # confirms the two orders diverge

        assert [pf.relpath for pf in sorted(paths)] == parts_order


class TestPosixRelpath:
    """File identity must always be a POSIX relpath, regardless of host OS.

    ``str(Path.relative_to(...))`` emits the OS-native separator, which is a
    no-op on POSIX (str == as_posix there) but breaks on Windows — the
    reported crash (`ValueError: file ref must be relative, got
    'charts\\\\README.md'`). Drive with PureWindowsPath, not a real Path, so
    the regression reproduces on any host, including POSIX CI.
    """

    def test_normalizes_windows_separators(self) -> None:
        root = PureWindowsPath(r"C:\proj")
        path = PureWindowsPath(r"C:\proj\charts\README.md")
        assert posix_relpath(path, root) == "charts/README.md"

    def test_posix_path_is_unchanged(self) -> None:
        assert (
            posix_relpath(PurePosixPath("/proj/charts/a.yml"), PurePosixPath("/proj"))
            == "charts/a.yml"
        )


class TestIsAbsoluteAnyOs:
    """Host-independent absoluteness check for a config/user-supplied path string.

    ``Path(s).is_absolute()`` only recognizes the running host's own
    convention (a POSIX-absolute string is never "absolute" under
    ``PureWindowsPath`` without a drive, and vice versa) — the reported
    Windows defect was a real absolute ``C:\\...`` path misclassified as
    relative by ``PurePosixPath(s).is_absolute()``. Drive with literal
    strings, not a real ``Path``, so both classifications are exercised on
    any host, including POSIX CI.
    """

    @pytest.mark.windows
    def test_windows_drive_path_is_absolute(self) -> None:
        assert is_absolute_any_os(r"C:\Users\x\bird.sqlite") is True

    def test_posix_absolute_path_is_absolute(self) -> None:
        assert is_absolute_any_os("/etc/passwd") is True

    def test_relative_path_is_not_absolute(self) -> None:
        assert is_absolute_any_os("sub/db.sqlite") is False


class TestIsPackageRelpath:
    def test_dbt_packages_relpath_is_package(self) -> None:
        assert is_package_relpath("dbt_packages/pkg_a/charts/overview.yml") is True

    def test_host_relpath_is_not_package(self) -> None:
        assert is_package_relpath("charts/overview.yml") is False


class TestBoardSlug:
    def test_host_board(self) -> None:
        assert board_slug("charts/home.yml") == "home"

    def test_host_nested_board(self) -> None:
        assert board_slug("charts/finance/rev.yml") == "finance/rev"

    def test_a_malformed_segment_survives_into_the_slug(self) -> None:
        """Path validation downstream rejects ``.``; collapsing it here would
        turn a malformed path into a valid-looking slug."""
        assert board_slug("charts/./x.yml") == "./x"

    def test_host_index_board_keeps_its_leaf(self) -> None:
        """board_slug is a file-identity slug, not a URL: the ``index`` leaf
        stays so every board file maps to a unique slug. Collapsing ``index``
        for display is a URL-layer concern (``alias_index.url_for_file``),
        never this function's job -- collapsing it here broke relative-link
        resolution, CLI move, and Cloud's access-key lookup for index boards
        (all of which need the slug to identify the exact file)."""
        assert board_slug("charts/index.yml") == "index"

    def test_nested_index_board_keeps_its_leaf(self) -> None:
        assert board_slug("charts/reports/index.yml") == "reports/index"

    def test_package_board(self) -> None:
        assert board_slug("dbt_packages/pkg_a/charts/overview.yml") == "pkg_a/overview"

    def test_package_nested_board(self) -> None:
        assert (
            board_slug("dbt_packages/pkg_a/charts/reports/q1.yml") == "pkg_a/reports/q1"
        )

    def test_package_index_board_keeps_its_leaf(self) -> None:
        assert board_slug("dbt_packages/pkg_a/charts/index.yml") == "pkg_a/index"

    def test_markdown_board(self) -> None:
        assert board_slug("charts/notes.md") == "notes"

    def test_directory_relpath(self) -> None:
        """Works for a bare directory relpath too — no suffix, no index-collapse."""
        assert board_slug("dbt_packages/pkg_a/charts/reports") == "pkg_a/reports"

    def test_package_root_directory(self) -> None:
        assert board_slug("dbt_packages/pkg_a/charts") == "pkg_a"

    def test_host_root_directory(self) -> None:
        assert board_slug("charts") == ""

    def test_relpath_outside_any_root_raises(self) -> None:
        with pytest.raises(ValueError, match="not under a board root"):
            board_slug("models/schema.yml")

    def test_dbt_packages_without_charts_segment_raises(self) -> None:
        with pytest.raises(ValueError, match="not under a board root"):
            board_slug("dbt_packages/pkg_a/models/schema.yml")


class TestBoardNamespaceForRoot:
    def test_host_root_has_no_namespace(self) -> None:
        assert board_namespace_for_root("charts") == ""

    def test_package_root_namespace_is_package_name(self) -> None:
        assert board_namespace_for_root("dbt_packages/pkg_a/charts") == "pkg_a"

    def test_non_root_relpath_raises(self) -> None:
        with pytest.raises(ValueError, match="not itself a board root"):
            board_namespace_for_root("charts/home")


class TestBoardRelpathCandidates:
    _roots = [BoardRoot("charts", ""), BoardRoot("dbt_packages/pkg_a/charts", "pkg_a")]

    def test_host_slug_only_matches_host_root(self) -> None:
        candidates = board_relpath_candidates("home", self._roots)
        assert "charts/home.yml" in candidates
        assert not any(c.startswith("dbt_packages/") for c in candidates)

    def test_package_slug_matches_only_that_package_root(self) -> None:
        candidates = board_relpath_candidates("pkg_a/overview", self._roots)
        assert "dbt_packages/pkg_a/charts/overview.yml" in candidates
        # The host root also matches lexically — a real host `charts/pkg_a/overview.yml`
        # would be a collision (rejected at AliasIndex.build), but candidate
        # generation itself stays root-table-driven, not host-only.
        assert "charts/pkg_a/overview.yml" in candidates

    def test_bare_package_namespace_resolves_to_its_index(self) -> None:
        candidates = board_relpath_candidates("pkg_a", self._roots)
        assert "dbt_packages/pkg_a/charts/index.yml" in candidates

    def test_extension_already_present_matches_bare_join(self) -> None:
        candidates = board_relpath_candidates("sub/x.yaml", self._roots)
        assert "charts/sub/x.yaml" in candidates

    def test_unrelated_namespace_does_not_match_package_root(self) -> None:
        candidates = board_relpath_candidates("pkg_b/overview", self._roots)
        assert not any(c.startswith("dbt_packages/pkg_a/") for c in candidates)


class TestBoardDirectoryCandidates:
    """The directory-URL counterpart of board_relpath_candidates -- shared by
    the alias index's directory-listing check and dct serve's directory
    router, so package-name matching is written exactly once."""

    _roots = [BoardRoot("charts", ""), BoardRoot("dbt_packages/pkg_a/charts", "pkg_a")]

    def test_host_clean_path_matches_only_host_root(self) -> None:
        candidates = board_directory_candidates("sales", self._roots)
        assert (BoardRoot("charts", ""), "sales") in candidates
        assert not any(root.namespace for root, _rest in candidates)

    def test_package_namespace_prefix_matches_that_root_with_rest_stripped(
        self,
    ) -> None:
        candidates = board_directory_candidates("pkg_a/reports", self._roots)
        assert (
            BoardRoot("dbt_packages/pkg_a/charts", "pkg_a"),
            "reports",
        ) in candidates

    def test_bare_package_namespace_matches_its_own_root(self) -> None:
        candidates = board_directory_candidates("pkg_a", self._roots)
        assert (BoardRoot("dbt_packages/pkg_a/charts", "pkg_a"), "") in candidates

    def test_unrelated_namespace_does_not_match_package_root(self) -> None:
        candidates = board_directory_candidates("pkg_b/reports", self._roots)
        assert not any(root.namespace == "pkg_a" for root, _rest in candidates)

    def test_empty_clean_path_matches_only_host_root_at_its_own_relpath(self) -> None:
        candidates = board_directory_candidates("", self._roots)
        assert candidates == [(BoardRoot("charts", ""), "")]


class TestBoardRootForRelpath:
    _roots = [BoardRoot("charts", ""), BoardRoot("dbt_packages/pkg_a/charts", "pkg_a")]

    def test_matches_host_root(self) -> None:
        assert board_root_for_relpath("charts/home.yml", self._roots) == BoardRoot(
            "charts", ""
        )

    def test_matches_package_root(self) -> None:
        assert board_root_for_relpath(
            "dbt_packages/pkg_a/charts/overview.yml", self._roots
        ) == BoardRoot("dbt_packages/pkg_a/charts", "pkg_a")

    def test_no_match_returns_none(self) -> None:
        assert board_root_for_relpath("models/schema.yml", self._roots) is None


class TestPackageBoardRoots:
    def test_package_with_charts_dir_is_a_root(
        self, tmp_path: Path, local_project: Callable[..., FilesystemProject]
    ) -> None:
        (tmp_path / "dbt_packages" / "pkg_a" / "charts").mkdir(parents=True)
        (tmp_path / "dbt_packages" / "pkg_a" / "charts" / "overview.yml").write_text(
            "title: O\n"
        )
        project = local_project(tmp_path)
        assert package_board_roots(project) == [
            BoardRoot("dbt_packages/pkg_a/charts", "pkg_a")
        ]

    def test_package_without_charts_dir_is_not_a_root(
        self, tmp_path: Path, local_project: Callable[..., FilesystemProject]
    ) -> None:
        (tmp_path / "dbt_packages" / "pkg_c" / "models").mkdir(parents=True)
        project = local_project(tmp_path)
        assert package_board_roots(project) == []

    def test_no_dbt_packages_dir_yields_no_roots(
        self, tmp_path: Path, local_project: Callable[..., FilesystemProject]
    ) -> None:
        project = local_project(tmp_path)
        assert package_board_roots(project) == []

    def test_symlinked_package_is_excluded(
        self, tmp_path: Path, local_project: Callable[..., FilesystemProject]
    ) -> None:
        """A `local:`-installed package is a symlink outside the project root —
        excluded by the same containment guard FilesystemProject.iter_dir
        already applies (pinned separately in test_dir_context.py)."""
        outside = tmp_path.parent / f"{tmp_path.name}_outside_pkg_b"
        (outside / "charts").mkdir(parents=True)
        (outside / "charts" / "x.yml").write_text("title: X\n")
        project_dir = tmp_path / "project"
        project_dir.mkdir()
        (project_dir / "dbt_packages").mkdir()
        (project_dir / "dbt_packages" / "pkg_b").symlink_to(
            outside, target_is_directory=True
        )
        project = local_project(project_dir)
        assert package_board_roots(project) == []

    def test_board_roots_includes_host_first_then_packages(
        self, tmp_path: Path, local_project: Callable[..., FilesystemProject]
    ) -> None:
        (tmp_path / "charts").mkdir()
        (tmp_path / "dbt_packages" / "pkg_a" / "charts").mkdir(parents=True)
        project = local_project(tmp_path)
        assert board_roots(project) == [
            BoardRoot("charts", ""),
            BoardRoot("dbt_packages/pkg_a/charts", "pkg_a"),
        ]

    def test_in_memory_host_with_no_dbt_packages_stays_host_only(self) -> None:
        """Cloud's git-blob store never has a `dbt_packages/` entry (gitignored,
        never committed) — `iter_dir(DBT_PACKAGES_SUBDIR)` yields nothing on any
        such host, so the board-root table degrades to host-only with no
        Cloud-specific code. Proven on the in-memory double, not FilesystemProject."""
        project = _InMemoryProject({"charts/a.yml": "title: A\n"})
        assert package_board_roots(project) == []
        assert board_roots(project) == [BoardRoot("charts", "")]

    def test_reader_without_a_tree_walk_still_yields_host_boards(self) -> None:
        """A `Project` whose `iter_dir` has no tree-walk capability (Cloud's
        shape: `CloudManagedProject.iter_dir` requires a reader-level
        recursive walk that not every reader implements) must still
        enumerate host boards -- package discovery is gated behind an
        `exists()` probe first, so a host lacking `dbt_packages/` never
        reaches `iter_dir` at all. Regression for the AttributeError CI hit
        when `_FakeBlobReader` (a minimal reader double with no tree walk)
        backed `iter_boards()`."""

        class _NoTreeWalkProject(_InMemoryProject):
            def iter_dir(self, under: str) -> Iterator[ProjectPath | ProjectDirectory]:
                raise AttributeError("this reader has no tree walk")

        project = _NoTreeWalkProject({"charts/revenue.yml": "title: Revenue\n"})
        assert [pf.relpath for pf in project.iter_boards()] == ["charts/revenue.yml"]


class TestBoardRootsCollision:
    """Regression: a host `charts/<pkg>` entry colliding with an installed
    package's own namespace must raise from `board_roots()` itself -- the
    single function every consumer (iter_boards, resolve_board_relpath,
    search, the alias index, ...) calls to get the root table. Before this
    fix, only `dct serve`'s own AliasIndex.build enforced the check, so
    every other surface silently picked the host board over the package."""

    def test_host_directory_collision_raises_from_board_roots(
        self, tmp_path: Path, local_project: Callable[..., FilesystemProject]
    ) -> None:
        (tmp_path / "charts" / "pkg_a").mkdir(parents=True)
        (tmp_path / "charts" / "pkg_a" / "x.yml").write_text("title: X\n")
        (tmp_path / "dbt_packages" / "pkg_a" / "charts").mkdir(parents=True)
        project = local_project(tmp_path)
        with pytest.raises(ValueError, match="(?i)collision"):
            board_roots(project)

    def test_host_file_collision_raises_from_board_roots(
        self, tmp_path: Path, local_project: Callable[..., FilesystemProject]
    ) -> None:
        (tmp_path / "charts").mkdir(parents=True)
        (tmp_path / "charts" / "pkg_a.yml").write_text("title: X\n")
        (tmp_path / "dbt_packages" / "pkg_a" / "charts").mkdir(parents=True)
        project = local_project(tmp_path)
        with pytest.raises(ValueError, match="(?i)collision"):
            board_roots(project)

    def test_no_collision_stays_silent(
        self, tmp_path: Path, local_project: Callable[..., FilesystemProject]
    ) -> None:
        (tmp_path / "charts").mkdir(parents=True)
        (tmp_path / "dbt_packages" / "pkg_a" / "charts").mkdir(parents=True)
        project = local_project(tmp_path)
        assert board_roots(project) == [
            BoardRoot("charts", ""),
            BoardRoot("dbt_packages/pkg_a/charts", "pkg_a"),
        ]

    def test_non_serve_consumer_now_raises_instead_of_silently_picking_host(
        self, tmp_path: Path, local_project: Callable[..., FilesystemProject]
    ) -> None:
        """A collision must not be silently resolved by a non-serve surface
        (e.g. resolve_board_relpath, used by validate/render/migrate/Cloud
        generation) -- it must raise the same way `dct serve` does."""
        from pathlib import PurePosixPath

        from dbt_charts.agent_api._paths import resolve_board_relpath

        (tmp_path / "charts" / "pkg_a").mkdir(parents=True)
        (tmp_path / "charts" / "pkg_a" / "overview.yml").write_text("title: Host\n")
        pkg_charts = tmp_path / "dbt_packages" / "pkg_a" / "charts"
        pkg_charts.mkdir(parents=True)
        (pkg_charts / "overview.yml").write_text("title: Pkg\n")
        project = local_project(tmp_path)
        with pytest.raises(ValueError, match="(?i)collision"):
            resolve_board_relpath(PurePosixPath("pkg_a/overview"), project)


class TestIterBoardsAllRoots:
    def test_default_scans_host_and_package_roots(
        self, tmp_path: Path, local_project: Callable[..., FilesystemProject]
    ) -> None:
        (tmp_path / "charts").mkdir()
        (tmp_path / "charts" / "home.yml").write_text("title: H\n")
        pkg_charts = tmp_path / "dbt_packages" / "pkg_a" / "charts"
        pkg_charts.mkdir(parents=True)
        (pkg_charts / "overview.yml").write_text("title: O\n")
        pkg_models = tmp_path / "dbt_packages" / "pkg_a" / "models"
        pkg_models.mkdir()
        (pkg_models / "schema.yml").write_text("a: 1\n")
        (tmp_path / "dbt_packages" / "pkg_a" / "README.md").write_text("# pkg\n")

        relpaths = {pf.relpath for pf in local_project(tmp_path).iter_boards()}
        assert relpaths == {"charts/home.yml", "dbt_packages/pkg_a/charts/overview.yml"}

    def test_explicit_under_stays_scoped_to_one_root(
        self, tmp_path: Path, local_project: Callable[..., FilesystemProject]
    ) -> None:
        (tmp_path / "charts").mkdir()
        (tmp_path / "charts" / "home.yml").write_text("title: H\n")
        pkg_charts = tmp_path / "dbt_packages" / "pkg_a" / "charts"
        pkg_charts.mkdir(parents=True)
        (pkg_charts / "overview.yml").write_text("title: O\n")

        relpaths = {
            pf.relpath for pf in local_project(tmp_path).iter_boards(under="charts")
        }
        assert relpaths == {"charts/home.yml"}
