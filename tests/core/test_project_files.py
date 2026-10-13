"""Contract tests for Project's file-access surface.

Project reads project files through four methods — ``read_text``, ``read_bytes``,
``exists``, and ``iter_files(under, *, recursive)``. The default ``Project`` reads
the local filesystem; an embedding host (Cloud) overrides those methods to serve
from another store. Two implementations must satisfy the same contract:

- ``Project`` — reads under a root path (CLI/local, today's behavior).
- ``InMemoryProject`` — a dict-backed subclass proving the seam is genuinely
  overridable (the Cloud git-blob subclass is a Cloud task, not here).

The same assertions run against both via the ``project`` fixture so neither can
drift from the contract.
"""

from __future__ import annotations

import os
from collections.abc import Callable
from pathlib import Path

import pytest

from dbt_charts.cli.filesystem_project import FilesystemProject
from dbt_charts.core.project import Project

# The contract corpus, identical for every backend.
_CORPUS = {
    "dbt_charts.yml": "server: {}\n",
    "charts/a.yml": "title: A\n",
    "charts/sub/b.yml": "title: B\n",
}


@pytest.fixture(params=["filesystem", "in_memory"])
def project(
    request: pytest.FixtureRequest,
    tmp_path: Path,
    in_memory_project: Callable[[Path, dict[str, str]], Project],
    local_project: Callable[..., FilesystemProject],
) -> Project:
    if request.param == "filesystem":
        for relpath, text in _CORPUS.items():
            target = tmp_path / relpath
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(text)
        return local_project(tmp_path)
    return in_memory_project(tmp_path, dict(_CORPUS))


class TestProjectFileAccessContract:
    def test_exists_true(self, project: Project) -> None:
        assert project.exists("charts/a.yml") is True

    def test_exists_false(self, project: Project) -> None:
        assert project.exists("charts/missing.yml") is False

    def test_read_text(self, project: Project) -> None:
        assert project.read_text("charts/a.yml") == "title: A\n"

    def test_read_bytes(self, project: Project) -> None:
        assert project.read_bytes("charts/a.yml") == b"title: A\n"

    def test_read_text_missing_raises(self, project: Project) -> None:
        with pytest.raises(FileNotFoundError):
            project.read_text("charts/missing.yml")

    def test_iter_files_under_subtree(self, project: Project) -> None:
        # list (not set): contract requires lexicographic order.
        assert list(project.iter_files("charts", recursive=True)) == [
            "charts/a.yml",
            "charts/sub/b.yml",
        ]

    def test_iter_files_non_recursive(self, project: Project) -> None:
        assert list(project.iter_files("charts", recursive=False)) == ["charts/a.yml"]

    def test_iter_files_under_dot_is_whole_project(self, project: Project) -> None:
        # under="." enumerates the whole project in lexicographic order.
        assert list(project.iter_files(".", recursive=True)) == [
            "charts/a.yml",
            "charts/sub/b.yml",
            "dbt_charts.yml",
        ]

    def test_iter_files_missing_subtree_is_empty(self, project: Project) -> None:
        assert list(project.iter_files("nope", recursive=True)) == []


class TestIterFilesFormatNeutral:
    """iter_files must be format-neutral — no suffix filter at the read layer.

    Callers (e.g. Project.iter_boards) are responsible for their own filtering.
    """

    def test_filesystem_yields_non_yaml_files(
        self, tmp_path: Path, local_project: Callable[..., FilesystemProject]
    ) -> None:
        (tmp_path / "charts").mkdir()
        (tmp_path / "charts" / "dash.yml").write_text("title: A\n")
        (tmp_path / "charts" / "data.csv").write_text("a,b\n1,2\n")
        (tmp_path / "charts" / "notes.md").write_text("# Notes\n")
        project = local_project(tmp_path)
        paths = list(project.iter_files("charts", recursive=True))
        assert "charts/data.csv" in paths
        assert "charts/notes.md" in paths
        assert "charts/dash.yml" in paths

    def test_filesystem_non_recursive_yields_non_yaml(
        self, tmp_path: Path, local_project: Callable[..., FilesystemProject]
    ) -> None:
        (tmp_path / "charts").mkdir()
        (tmp_path / "charts" / "dash.yml").write_text("title: A\n")
        (tmp_path / "charts" / "data.csv").write_text("a,b\n1,2\n")
        project = local_project(tmp_path)
        paths = list(project.iter_files("charts", recursive=False))
        assert "charts/data.csv" in paths
        assert "charts/dash.yml" in paths


class TestFilesystemIterOrdering:
    """Project.iter_files must produce a globally sorted sequence.

    A corpus where the per-directory walk order differs from global lex order
    pins the fix: 'charts/b.yml' sorts after 'charts/a_sub/a.yml' globally,
    but a naive per-dir approach visits 'charts/' first (yielding b.yml) before
    descending into 'charts/a_sub/' (yielding a.yml).
    """

    def test_recursive_global_sort(
        self, tmp_path: Path, local_project: Callable[..., FilesystemProject]
    ) -> None:
        (tmp_path / "charts" / "a_sub").mkdir(parents=True)
        (tmp_path / "charts" / "b.yml").write_text("b\n")
        (tmp_path / "charts" / "a_sub" / "a.yml").write_text("a\n")
        project = local_project(tmp_path)
        paths = list(project.iter_files("charts", recursive=True))
        # 'charts/a_sub/a.yml' must come before 'charts/b.yml' in global sort.
        assert paths == ["charts/a_sub/a.yml", "charts/b.yml"]


class TestIterFilesOnError:
    """`iter_files`'s optional `onerror` callback -- without it, `os.walk`'s
    own default (`onerror=None`) silently skips a directory it cannot
    enter, so a caller with no way to observe that gets a confidently wrong
    empty result. `list_boards` is the first caller that needs to know."""

    def test_default_onerror_silently_skips_an_unreadable_directory(
        self, tmp_path: Path, local_project: Callable[..., FilesystemProject]
    ) -> None:
        """Pin today's baseline: with no `onerror`, an unreadable directory
        is invisible, not an error -- this is `os.walk`'s own default, not
        a bug in `iter_files` itself; `list_boards` is what must not trust
        it alone."""
        charts = tmp_path / "charts"
        charts.mkdir()
        (charts / "good.yml").write_text("title: Good\n")
        blocked = charts / "blocked"
        blocked.mkdir()
        (blocked / "hidden.yml").write_text("title: Hidden\n")
        blocked.chmod(0o000)
        try:
            if os.access(blocked, os.R_OK):
                pytest.skip("running as root or on a filesystem that ignores chmod")
            project = local_project(tmp_path)
            paths = list(project.iter_files("charts", recursive=True))
        finally:
            blocked.chmod(0o755)

        assert paths == ["charts/good.yml"]

    def test_onerror_is_called_with_the_relpath_and_the_exception(
        self, tmp_path: Path, local_project: Callable[..., FilesystemProject]
    ) -> None:
        charts = tmp_path / "charts"
        charts.mkdir()
        (charts / "good.yml").write_text("title: Good\n")
        blocked = charts / "blocked"
        blocked.mkdir()
        blocked.chmod(0o000)
        errors: list[tuple[str, OSError]] = []
        try:
            if os.access(blocked, os.R_OK):
                pytest.skip("running as root or on a filesystem that ignores chmod")
            project = local_project(tmp_path)
            paths = list(
                project.iter_files(
                    "charts",
                    recursive=True,
                    onerror=lambda relpath, exc: errors.append((relpath, exc)),
                )
            )
        finally:
            blocked.chmod(0o755)

        assert paths == ["charts/good.yml"]
        assert len(errors) == 1
        relpath, exc = errors[0]
        assert relpath == "charts/blocked"
        assert isinstance(exc, OSError)

    def test_no_onerror_means_no_behavior_change(
        self, tmp_path: Path, local_project: Callable[..., FilesystemProject]
    ) -> None:
        """Every existing caller passes no `onerror` -- confirms the
        parameter is additive, not a signature break."""
        (tmp_path / "charts").mkdir()
        (tmp_path / "charts" / "a.yml").write_text("title: A\n")
        project = local_project(tmp_path)

        assert list(project.iter_files("charts", recursive=True)) == ["charts/a.yml"]


class TestSubclassOverridesReads:
    """A Project subclass serves reads from its override, no disk access."""

    def test_in_memory_reads_from_override(
        self,
        tmp_path: Path,
        in_memory_project: Callable[[Path, dict[str, str]], Project],
    ) -> None:
        files = {"charts/x.yml": "title: From Override\n"}
        project = in_memory_project(tmp_path, files)
        # Nothing is on disk; the read must come from the subclass override.
        assert project.read_text("charts/x.yml") == "title: From Override\n"
        assert project.exists("charts/x.yml") is True
        assert project.exists("charts/x.yml-not") is False

    def test_default_project_reads_filesystem(
        self, tmp_path: Path, local_project: Callable[..., FilesystemProject]
    ) -> None:
        (tmp_path / "charts").mkdir()
        (tmp_path / "charts" / "x.yml").write_text("title: On Disk\n")
        project = local_project(tmp_path)
        assert project.read_text("charts/x.yml") == "title: On Disk\n"
