"""Tests for the general project-file tools exposed to the chat agent."""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path

import pytest

from dbt_charts.agent_api.files import (
    delete_file,
    edit_file,
    move_file,
    read_file,
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


class TestEdit:
    def test_edit_unique_replace(self, project: FilesystemProject) -> None:
        e = edit_file(
            "charts/existing.yml",
            old_string="Existing",
            new_string="Edited",
            project=project,
        )
        assert e.success, e.error
        assert e.replacements == 1
        assert "Edited" in (project.root / "charts" / "existing.yml").read_text(
            encoding="utf-8"
        )

    def test_edit_missing_string_errors(self, project: FilesystemProject) -> None:
        e = edit_file(
            "charts/existing.yml",
            old_string="NotThere",
            new_string="X",
            project=project,
        )
        assert e.success is False
        assert e.replacements == 0

    def test_edit_ambiguous_string_errors(self, project: FilesystemProject) -> None:
        (project.root / "charts" / "dup.yml").write_text("a\na\n", encoding="utf-8")
        e = edit_file("charts/dup.yml", old_string="a", new_string="b", project=project)
        assert e.success is False
        assert "uniqu" in (e.error or "").lower() or "ambig" in (e.error or "").lower()


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

    def test_edit_updates_store(self, store: Project) -> None:
        e = edit_file(
            "charts/report.yml", old_string="alice", new_string="bob", project=store
        )
        assert e.success, e.error
        assert e.replacements == 1
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
