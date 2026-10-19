"""Tests for the general project-file tools exposed to the chat agent."""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path
from typing import Literal

import pytest

from dbt_charts.agent_api.files import (
    PatchOperation,
    PatchResult,
    apply_patch,
    delete_file,
    move_file,
    read_file,
    refuse_blind_overwrite,
    write_file,
)
from dbt_charts.cli.filesystem_project import FilesystemProject
from dbt_charts.core.project import Project


@pytest.fixture
def project(
    tmp_path: Path, local_project: Callable[..., FilesystemProject]
) -> FilesystemProject:
    (tmp_path / "charts").mkdir()
    (tmp_path / "charts" / "existing.yml").write_text(
        "title: Existing\n", encoding="utf-8"
    )
    (tmp_path / "notes.txt").write_text("alpha\nbeta\ngamma\n", encoding="utf-8")
    return local_project(tmp_path)


class TestPathConfinement:
    def test_write_rejects_parent_escape(self, project: FilesystemProject) -> None:
        result = write_file("../outside.yml", "x: 1\n", project=project)
        assert result.success is False
        assert "escape" in (result.error or "").lower()
        assert not (project.root.parent / "outside.yml").exists()

    def test_write_rejects_absolute_outside_root(
        self, project: FilesystemProject, tmp_path_factory: pytest.TempPathFactory
    ) -> None:
        outside = tmp_path_factory.mktemp("elsewhere") / "evil.yml"
        result = write_file(str(outside), "x: 1\n", project=project)
        assert result.success is False
        assert not outside.exists()

    def test_read_rejects_escape(self, project: FilesystemProject) -> None:
        result = read_file("../../etc/passwd", project=project)
        assert result.success is False

    def test_write_rejects_symlink_escape(
        self, project: FilesystemProject, tmp_path_factory: pytest.TempPathFactory
    ) -> None:
        outside_dir = tmp_path_factory.mktemp("symlink_target")
        link = project.root / "charts" / "link_out"
        link.symlink_to(outside_dir)
        result = write_file("charts/link_out/evil.yml", "x: 1\n", project=project)
        assert result.success is False
        assert not (outside_dir / "evil.yml").exists()


class TestWriteRead:
    @pytest.mark.windows
    def test_write_then_read_roundtrip(self, project: FilesystemProject) -> None:
        w = write_file("charts/new.yml", "title: New\n", project=project)
        assert w.success, w.error
        assert (project.root / "charts" / "new.yml").read_text(
            encoding="utf-8"
        ) == "title: New\n"
        r = read_file("charts/new.yml", project=project)
        assert r.success
        assert r.content == "title: New\n"

    @pytest.mark.windows
    def test_utf8_roundtrip_survives_non_ascii(
        self, project: FilesystemProject
    ) -> None:
        """Non-ASCII text round-trips through the write/read seam regardless of
        the platform default encoding."""
        text = "héllo wörld — utf-8 ✓\n"
        w = write_file("charts/utf8.yml", text, project=project)
        assert w.success, w.error
        assert read_file("charts/utf8.yml", project=project).content == text

    def test_write_creates_parent_dirs(self, project: FilesystemProject) -> None:
        w = write_file("charts/zendesk/agents.yml", "title: A\n", project=project)
        assert w.success, w.error
        assert (project.root / "charts" / "zendesk" / "agents.yml").exists()

    def test_read_missing_file_errors(self, project: FilesystemProject) -> None:
        r = read_file("charts/nope.yml", project=project)
        assert r.success is False
        assert r.content is None


def _patch(
    project: Project,
    op_type: Literal["create_file", "update_file", "delete_file"],
    path: str,
    diff: str | None = None,
) -> PatchResult:
    return apply_patch(PatchOperation(type=op_type, path=path, diff=diff), project)


class TestApplyPatch:
    def _file(self, project: FilesystemProject, text: str) -> Path:
        path = project.root / "f.txt"
        path.write_text(text, encoding="utf-8")
        return path

    def test_create_update_delete(self, project: FilesystemProject) -> None:
        assert _patch(project, "create_file", "new.txt", "+a\n+b\n").success
        assert (project.root / "new.txt").read_text() == "a\nb\n"
        assert _patch(project, "update_file", "new.txt", "@@\n a\n-b\n+c\n").success
        assert (project.root / "new.txt").read_text() == "a\nc\n"
        assert _patch(project, "delete_file", "new.txt").success
        assert not (project.root / "new.txt").exists()

    def test_create_refuses_an_existing_file(self, project: FilesystemProject) -> None:
        result = _patch(project, "create_file", "notes.txt", "+x\n")
        assert result.success is False
        assert "already exists" in (result.error or "")

    def test_several_hunks_apply_in_one_write(self, project: FilesystemProject) -> None:
        path = self._file(project, "".join(f"l{i}\n" for i in range(1, 11)))
        diff = "@@\n-l2\n+L2\n@@\n l5\n-l6\n+L6\n@@ l8\n-l9\n+L9\n"
        assert _patch(project, "update_file", "f.txt", diff).success
        assert path.read_text().splitlines()[1::4][:2] == ["L2", "L6"]
        assert "L9" in path.read_text()

    def test_context_header_anchors_a_repeated_line(
        self, project: FilesystemProject
    ) -> None:
        path = self._file(project, "a:\n  x: 1\nb:\n  x: 1\n")
        assert _patch(
            project, "update_file", "f.txt", "@@ b:\n-  x: 1\n+  x: 2\n"
        ).success
        assert path.read_text() == "a:\n  x: 1\nb:\n  x: 2\n"

    def test_trailing_whitespace_is_ignored_when_matching(
        self, project: FilesystemProject
    ) -> None:
        path = self._file(project, "a   \nb\t\n")
        assert _patch(project, "update_file", "f.txt", "@@\n-a\n+A\n").success
        assert path.read_text() == "A\nb\t\n"

    def test_all_whitespace_is_ignored_when_matching(
        self, project: FilesystemProject
    ) -> None:
        path = self._file(project, "    key: 1\nother\n")
        assert _patch(project, "update_file", "f.txt", "@@\n-key: 1\n+key: 2\n").success
        assert path.read_text() == "key: 2\nother\n"

    def test_end_of_file_marker_matches_the_last_occurrence(
        self, project: FilesystemProject
    ) -> None:
        path = self._file(project, "x\ny\nx\n")
        diff = "@@\n-x\n+z\n*** End of File\n"
        assert _patch(project, "update_file", "f.txt", diff).success
        assert path.read_text() == "x\ny\nz\n"

    def test_failure_names_the_hunk_and_the_closest_lines_and_writes_nothing(
        self, project: FilesystemProject
    ) -> None:
        path = self._file(project, "alpha\nbeta\ngamma\n")
        result = _patch(
            project, "update_file", "f.txt", "@@\n-alpha\n+A\n@@\n-betta\n+B\n"
        )
        assert result.success is False
        assert "hunk 2" in (result.error or "")
        assert "beta" in (result.error or "")
        assert path.read_text() == "alpha\nbeta\ngamma\n"

    def test_an_add_only_hunk_appends_after_a_later_hunk_that_grows_the_file(
        self, project: FilesystemProject
    ) -> None:
        path = self._file(project, "a\nb\nc\nd\ne\n")
        diff = "@@\n+appended\n@@\n b\n-c\n+c1\n+c2\n+c3\n"
        assert _patch(project, "update_file", "f.txt", diff).success
        assert path.read_text() == "a\nb\nc1\nc2\nc3\nd\ne\nappended\n"

    def test_punctuation_is_normalized_when_matching(
        self, project: FilesystemProject
    ) -> None:
        path = self._file(project, "title: \u201cSales\u201d \u2013 2026\nother\n")
        diff = '@@\n-title: "Sales" - 2026\n+title: Sales\n'
        assert _patch(project, "update_file", "f.txt", diff).success
        assert path.read_text() == "title: Sales\nother\n"

    def test_a_diff_ending_in_a_blank_context_line_still_applies(
        self, project: FilesystemProject
    ) -> None:
        path = self._file(project, "a\nb\n")
        assert _patch(project, "update_file", "f.txt", "@@\n a\n-b\n+B\n \n").success
        assert path.read_text() == "a\nB\n"

    def test_escape_is_refused(self, project: FilesystemProject) -> None:
        result = _patch(project, "create_file", "../out.txt", "+x\n")
        assert result.success is False


class TestMoveFile:
    def test_move_renames_file(self, project: FilesystemProject) -> None:
        m = move_file("charts/existing.yml", "charts/renamed.yml", project=project)
        assert m.success, m.error
        assert not (project.root / "charts" / "existing.yml").exists()
        assert (project.root / "charts" / "renamed.yml").read_text(
            encoding="utf-8"
        ) == "title: Existing\n"

    def test_move_rejects_existing_destination(
        self, project: FilesystemProject
    ) -> None:
        (project.root / "charts" / "other.yml").write_text(
            "title: Other\n", encoding="utf-8"
        )
        m = move_file("charts/existing.yml", "charts/other.yml", project=project)
        assert m.success is False
        assert "exist" in (m.error or "").lower()
        # Neither side was touched.
        assert (project.root / "charts" / "existing.yml").exists()
        assert (project.root / "charts" / "other.yml").read_text(
            encoding="utf-8"
        ) == "title: Other\n"

    def test_move_missing_source_errors(self, project: FilesystemProject) -> None:
        m = move_file("charts/nope.yml", "charts/dest.yml", project=project)
        assert m.success is False
        assert not (project.root / "charts" / "dest.yml").exists()

    def test_move_rejects_traversal(self, project: FilesystemProject) -> None:
        m = move_file("charts/existing.yml", "../outside.yml", project=project)
        assert m.success is False
        assert not (project.root.parent / "outside.yml").exists()
        assert (project.root / "charts" / "existing.yml").exists()

    def test_move_rewrites_exact_inbound_link(self, project: FilesystemProject) -> None:
        (project.root / "charts" / "exec.yml").write_text(
            "charts:\n  a:\n    link: existing\n", encoding="utf-8"
        )
        m = move_file("charts/existing.yml", "charts/renamed.yml", project=project)
        assert m.success, m.error
        assert m.links_rewritten == 1
        assert (project.root / "charts" / "exec.yml").read_text(
            encoding="utf-8"
        ) == "charts:\n  a:\n    link: renamed\n"

    def test_move_reports_fuzzy_hit_without_rewriting(
        self, project: FilesystemProject
    ) -> None:
        notes = "title: Notes\nnotes: see the existing dashboard\n"
        (project.root / "charts" / "notes.yml").write_text(notes, encoding="utf-8")
        m = move_file("charts/existing.yml", "charts/renamed.yml", project=project)
        assert m.success, m.error
        assert [h.path for h in m.fuzzy_links] == ["charts/notes.yml"]
        assert (project.root / "charts" / "notes.yml").read_text(
            encoding="utf-8"
        ) == notes


class TestDeleteFile:
    def test_delete_removes_file(self, project: FilesystemProject) -> None:
        d = delete_file("charts/existing.yml", project=project)
        assert d.success, d.error
        assert not (project.root / "charts" / "existing.yml").exists()

    def test_delete_missing_file_errors(self, project: FilesystemProject) -> None:
        d = delete_file("charts/nope.yml", project=project)
        assert d.success is False

    def test_delete_rejects_traversal(self, project: FilesystemProject) -> None:
        outside = project.root.parent / "escape.txt"
        outside.write_text("x", encoding="utf-8")
        d = delete_file("../escape.txt", project=project)
        assert d.success is False
        assert outside.exists()


class TestNonFilesystemProject:
    """A non-filesystem project (e.g. Cloud's git-blob store) must never fall
    through to the host filesystem: reads and writes both route through the
    backing store via the Project write seam, never touching disk."""

    @pytest.fixture
    def store(
        self,
        tmp_path: Path,
        in_memory_project: Callable[[Path, dict[str, str]], Project],
    ) -> Project:
        return in_memory_project(
            tmp_path / "store-root",
            {"charts/report.yml": "title: In Store\nowner: alice\n"},
        )

    def test_write_updates_store(self, store: Project, tmp_path: Path) -> None:
        w = write_file("charts/new.yml", "title: New\n", project=store)
        assert w.success, w.error
        r = read_file("charts/new.yml", project=store)
        assert r.success
        assert r.content == "title: New\n"
        # The write went to the backing store, not the (non-existent) disk root
        # `in_memory_project` was constructed with.
        assert not (tmp_path / "store-root" / "charts" / "new.yml").exists()

    def test_apply_patch_updates_store(self, store: Project) -> None:
        e = _patch(
            store,
            "update_file",
            "charts/report.yml",
            "@@\n-owner: alice\n+owner: bob\n",
        )
        assert e.success, e.error
        r = read_file("charts/report.yml", project=store)
        assert r.success
        assert "bob" in (r.content or "")

    def test_read_uses_backing_store(self, store: Project) -> None:
        r = read_file("charts/report.yml", project=store)
        assert r.success
        assert r.content == "title: In Store\nowner: alice\n"

    def test_read_missing_in_store_is_not_found(self, store: Project) -> None:
        r = read_file("charts/absent.yml", project=store)
        assert r.success is False


class TestPackageBoardCopyOnWrite:
    PKG_BOARD = "dbt_packages/zendesk/charts/overview.yml"

    @pytest.fixture
    def pkg_project(self, project: FilesystemProject) -> FilesystemProject:
        pkg = project.root / "dbt_packages" / "zendesk" / "charts"
        pkg.mkdir(parents=True)
        (pkg / "overview.yml").write_text("title: Pkg\n", encoding="utf-8")
        return project

    def test_edit_saves_host_copy_and_slug_resolves_to_it(
        self, pkg_project: FilesystemProject
    ) -> None:
        from pathlib import PurePosixPath

        from dbt_charts.agent_api._paths import resolve_board_relpath

        result = _patch(
            pkg_project,
            "update_file",
            self.PKG_BOARD,
            "@@\n-title: Pkg\n+title: Mine\n",
        )
        assert result.success is True
        assert result.path == "charts/zendesk/overview.yml"
        root = pkg_project.root
        assert (root / "charts/zendesk/overview.yml").read_text() == "title: Mine\n"
        assert (root / self.PKG_BOARD).read_text() == "title: Pkg\n"
        resolved = resolve_board_relpath(PurePosixPath("zendesk/overview"), pkg_project)
        assert resolved.relpath == "charts/zendesk/overview.yml"

        again = _patch(
            pkg_project,
            "update_file",
            self.PKG_BOARD,
            "@@\n-title: Mine\n+title: Again\n",
        )
        assert again.success is True
        assert (root / "charts/zendesk/overview.yml").read_text() == "title: Again\n"

    def test_create_reports_the_host_path(self, pkg_project: FilesystemProject) -> None:
        result = _patch(pkg_project, "create_file", self.PKG_BOARD, "+title: New\n")
        assert result.success is True
        assert result.path == "charts/zendesk/overview.yml"
        assert (pkg_project.root / self.PKG_BOARD).read_text() == "title: Pkg\n"

    def test_write_reports_the_host_path(self, pkg_project: FilesystemProject) -> None:
        result = write_file(self.PKG_BOARD, "title: New\n", project=pkg_project)
        assert result.success is True
        assert result.path == "charts/zendesk/overview.yml"
        assert (pkg_project.root / self.PKG_BOARD).read_text() == "title: Pkg\n"

    def test_read_returns_the_host_copy_and_keeps_the_overwrite_guard_honest(
        self, pkg_project: FilesystemProject
    ) -> None:
        copy = pkg_project.root / "charts/zendesk/overview.yml"
        copy.parent.mkdir(parents=True)
        copy.write_text("title: Mine\n", encoding="utf-8")

        assert read_file(self.PKG_BOARD, pkg_project).content == "title: Mine\n"
        refusal = refuse_blind_overwrite(self.PKG_BOARD, pkg_project, set())
        assert refusal is not None
        assert (
            refuse_blind_overwrite(self.PKG_BOARD, pkg_project, {self.PKG_BOARD})
            is None
        )

    def test_non_board_package_files_are_not_redirected(
        self, pkg_project: FilesystemProject
    ) -> None:
        sql = "dbt_packages/zendesk/charts/q.sql"
        assert write_file(sql, "select 1", pkg_project).success is False
