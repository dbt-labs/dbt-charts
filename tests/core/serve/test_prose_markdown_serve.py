"""Integration tests: serve renders prose .md files through symlinks.

Scenario: a symlinked directory under charts/ brings in arbitrary .md files
(e.g. task files with status/owner/milestone frontmatter). The server must:

1. Render any .md resolved under the charts/ tree as a prose board, even when
   the path goes through a symlink.
2. NOT crash on non-.md files (like .json, .jsonl) that may exist in the
   same symlinked tree — directory listings must work, and non-.md file URLs
   should 404 (not 500).
3. Respect the ``metadata_table`` server config flag.
"""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path

from fastapi.testclient import TestClient

from dbt_charts.cli.filesystem_project import FilesystemProject
from dbt_charts.core.serve.server import create_server

# ---------------------------------------------------------------------------
# Shared test content
# ---------------------------------------------------------------------------

_TASK_MD = """\
---
status: in_progress
owner: alice
milestone: v1
priority: p1
---

# Do the thing

Some description with details.
"""


def _make_prose_project(tmp_path: Path, *, metadata_table: bool = False) -> Path:
    """Create a minimal project with a symlinked tasks/ tree under charts/."""
    project = tmp_path / "project"
    project.mkdir()

    # Real tasks tree outside the project
    tasks_dir = tmp_path / "tasks"
    tasks_dir.mkdir()
    (tasks_dir / "my_task.md").write_text(_TASK_MD)
    (tasks_dir / "meta.json").write_text('{"version": 1}')  # non-.md file

    # charts/ dir with a symlink into tasks
    boards = project / "charts"
    boards.mkdir()
    Path(str(boards / "tasks")).symlink_to(str(tasks_dir))

    if metadata_table:
        (project / "dbt_charts.yml").write_text(
            "server:\n  markdown_metadata_table: true\n"
        )

    return project


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


class TestSymlinkedMarkdownServe:
    def test_symlinked_md_renders_as_prose(self, tmp_path: Path) -> None:
        """A .md via symlink under charts/ must render as HTML (200, not 403/404/500)."""
        project = _make_prose_project(tmp_path)
        with TestClient(
            create_server(FilesystemProject(project)),
            raise_server_exceptions=False,
        ) as client:
            response = client.get("/tasks/my_task/")
        assert response.status_code == 200, (
            f"Expected 200, got {response.status_code}: {response.text[:300]}"
        )
        assert "Do the thing" in response.text

    def test_symlinked_md_renders_body_text(self, tmp_path: Path) -> None:
        """The rendered page includes the markdown body content."""
        project = _make_prose_project(tmp_path)
        with TestClient(
            create_server(FilesystemProject(project)),
            raise_server_exceptions=False,
        ) as client:
            response = client.get("/tasks/my_task/")
        assert "Some description" in response.text

    def test_non_md_file_in_symlinked_tree_does_not_crash(self, tmp_path: Path) -> None:
        """A non-.md file (.json) under the symlinked tree must 404, not 500."""
        project = _make_prose_project(tmp_path)
        with TestClient(
            create_server(FilesystemProject(project)),
            raise_server_exceptions=False,
        ) as client:
            response = client.get("/tasks/meta/")
        # Should be 404 (file not found / not a board) not 500
        assert response.status_code in (
            404,
            422,
        ), f"Non-.md file URL should 404/422, got {response.status_code}"

    def test_directory_listing_of_symlinked_dir_works(self, tmp_path: Path) -> None:
        """Directory listing for the symlinked tasks/ dir must succeed (200)."""
        project = _make_prose_project(tmp_path)
        with TestClient(
            create_server(FilesystemProject(project)),
            raise_server_exceptions=False,
        ) as client:
            response = client.get("/tasks/")
        assert response.status_code == 200, (
            f"Directory listing should succeed, got {response.status_code}"
        )

    def test_metadata_table_flag_off_no_table_in_output(self, tmp_path: Path) -> None:
        """With metadata_table=false (default), frontmatter keys not shown as table."""
        project = _make_prose_project(tmp_path, metadata_table=False)
        with TestClient(
            create_server(FilesystemProject(project)),
            raise_server_exceptions=False,
        ) as client:
            response = client.get("/tasks/my_task/")
        assert response.status_code == 200
        # The metadata keys (status, owner, etc.) should NOT appear as a table
        # Note: they might still appear in error messages — check the success path
        text = response.text
        # A markdown table row would look like "| status |" or "| owner |"
        assert "| status |" not in text
        assert "| owner |" not in text

    def test_metadata_table_flag_on_shows_metadata(self, tmp_path: Path) -> None:
        """With metadata_table=true, frontmatter metadata is rendered as a table."""
        project = _make_prose_project(tmp_path, metadata_table=True)
        with TestClient(
            create_server(FilesystemProject(project)),
            raise_server_exceptions=False,
        ) as client:
            response = client.get("/tasks/my_task/")
        assert response.status_code == 200
        # The metadata table should appear in the rendered HTML
        assert "status" in response.text
        assert "in_progress" in response.text


class TestProseMarkdownCompileDirect:
    """Unit tests: .md files with arbitrary frontmatter compile cleanly."""

    def test_task_md_in_boards_dir_compiles(
        self, tmp_path: Path, local_project: Callable[..., FilesystemProject]
    ) -> None:
        from dbt_charts.core.compile.compiler import compile_file

        p = tmp_path / "charts" / "task.md"
        p.parent.mkdir()
        p.write_text(_TASK_MD)
        project = local_project(tmp_path)
        result = compile_file(project.path("charts/task.md").read_board())
        assert result.success, f"Compile errors: {result.errors}"

    def test_task_md_has_no_extra_field_errors(
        self, tmp_path: Path, local_project: Callable[..., FilesystemProject]
    ) -> None:
        """The ERR-EXTRA-FIELD error must not appear for task-style frontmatter."""
        from dbt_charts.core.compile.compiler import compile_file

        p = tmp_path / "charts" / "task.md"
        p.parent.mkdir()
        p.write_text(_TASK_MD)
        project = local_project(tmp_path)
        result = compile_file(project.path("charts/task.md").read_board())
        error_text = " ".join(str(e) for e in result.errors).lower()
        assert "extra" not in error_text or "field" not in error_text, (
            f"Got extra-field errors: {result.errors}"
        )


class TestDirectoryListingFiltersNonBoardFiles:
    """Directory listing must include .md/.yaml/.yml and subdirs, exclude non-board files."""

    def _make_mixed_project(self, tmp_path: Path) -> Path:
        """Create a project with a charts/ subdir containing mixed file types."""
        project = tmp_path / "project"
        boards = project / "charts"
        mixed = boards / "mixed"
        mixed.mkdir(parents=True)

        (mixed / "report.md").write_text("---\nstatus: done\n---\n# Report\n")
        (mixed / "dashboard.yaml").write_text("title: Dash\ntext: hello\n")
        (mixed / "subdir").mkdir()
        (mixed / "subdir" / "inner.yml").write_text("title: Inner\ntext: hi\n")
        # Non-board files — must NOT appear in listing
        (mixed / "meta.json").write_text('{"version": 1}')
        (mixed / "log.jsonl").write_text('{"event": "x"}\n')
        (mixed / "notes.txt").write_text("some notes\n")

        return project

    def test_listing_includes_md_and_yaml(self, tmp_path: Path) -> None:
        """The directory listing page includes .md and .yaml files."""
        project = self._make_mixed_project(tmp_path)
        with TestClient(
            create_server(FilesystemProject(project)),
            raise_server_exceptions=False,
        ) as client:
            response = client.get("/mixed/")
        assert response.status_code == 200, f"Expected 200, got {response.status_code}"
        assert "report" in response.text
        assert "dashboard" in response.text

    def test_listing_includes_subdirectories(self, tmp_path: Path) -> None:
        """The directory listing page includes subdirectories."""
        project = self._make_mixed_project(tmp_path)
        with TestClient(
            create_server(FilesystemProject(project)),
            raise_server_exceptions=False,
        ) as client:
            response = client.get("/mixed/")
        assert response.status_code == 200
        assert "subdir" in response.text

    def test_listing_excludes_json_files(self, tmp_path: Path) -> None:
        """The directory listing page must NOT include .json or .jsonl files."""
        project = self._make_mixed_project(tmp_path)
        with TestClient(
            create_server(FilesystemProject(project)),
            raise_server_exceptions=False,
        ) as client:
            response = client.get("/mixed/")
        assert response.status_code == 200
        assert "meta.json" not in response.text
        assert "log.jsonl" not in response.text
        assert "notes.txt" not in response.text


class TestPathTraversalThroughSymlinkIsBlocked:
    """Percent-encoded ``..`` must not escape the project root via a charts/ symlink.

    ``charts/tasks`` symlinks to ``tmp_path/tasks``; ``..`` from there reaches
    ``tmp_path``, where a secret file sits outside the project root. Collapsing
    ``..`` textually would land inside charts/ (containment "passes") while the
    OS dereferences the symlink first and escapes — so ``..`` must be rejected
    outright.
    """

    def _project_with_external_secret(self, tmp_path: Path) -> Path:
        project = _make_prose_project(tmp_path)
        secret = tmp_path / "secret.md"
        secret.write_text("---\nstatus: stolen\n---\n# EXTERNAL SECRET\nconfidential\n")
        return project

    def test_encoded_dotdot_does_not_read_file_outside_project(
        self, tmp_path: Path
    ) -> None:
        project = self._project_with_external_secret(tmp_path)
        with TestClient(
            create_server(FilesystemProject(project)),
            raise_server_exceptions=False,
        ) as client:
            for url in (
                "/tasks/%2e%2e/secret/",
                "/tasks/%2e%2e%2fsecret/",
                "/tasks/%2e%2e/secret.md",
            ):
                r = client.get(url)
                assert r.status_code != 200, f"{url} leaked (status {r.status_code})"
                assert "EXTERNAL SECRET" not in r.text
                assert "confidential" not in r.text

    def test_encoded_dotdot_listing_does_not_disclose_external_dir(
        self, tmp_path: Path
    ) -> None:
        project = self._project_with_external_secret(tmp_path)
        with TestClient(
            create_server(FilesystemProject(project)),
            raise_server_exceptions=False,
        ) as client:
            r = client.get("/tasks/%2e%2e/")
            assert r.status_code != 200 or "secret" not in r.text


class TestCanonicalSuffixRedirect:
    """Board-file suffix URLs 301 to the clean URL; .markdown == .md."""

    def _project(self, tmp_path: Path) -> Path:
        project = tmp_path / "project"
        boards = project / "charts"
        boards.mkdir(parents=True)
        (boards / "report.md").write_text("---\nstatus: ok\n---\n# Report\n")
        (boards / "longform.markdown").write_text("---\nstatus: ok\n---\n# Longform\n")
        (boards / "dash.yml").write_text("title: Dash\ntext: hi\n")
        return project

    def _client(self, tmp_path: Path) -> TestClient:
        return TestClient(
            create_server(FilesystemProject(self._project(tmp_path))),
            raise_server_exceptions=False,
        )

    def test_md_suffix_redirects_to_clean_url(self, tmp_path: Path) -> None:
        with self._client(tmp_path) as c:
            r = c.get("/report.md", follow_redirects=False)
        assert r.status_code == 301
        assert r.headers["location"] == "/report"

    def test_markdown_suffix_redirects_and_renders(self, tmp_path: Path) -> None:
        with self._client(tmp_path) as c:
            r = c.get("/longform.markdown", follow_redirects=False)
            assert r.status_code == 301
            assert r.headers["location"] == "/longform"
            # .markdown is a first-class markdown board and renders at the clean URL.
            assert c.get("/longform/").status_code == 200

    def test_yml_suffix_redirects(self, tmp_path: Path) -> None:
        with self._client(tmp_path) as c:
            r = c.get("/dash.yml", follow_redirects=False)
        assert r.status_code == 301
        assert r.headers["location"] == "/dash"

    def test_suffix_redirect_preserves_query(self, tmp_path: Path) -> None:
        with self._client(tmp_path) as c:
            r = c.get("/report.md?x=1", follow_redirects=False)
        assert r.status_code == 301
        assert r.headers["location"] == "/report?x=1"


class TestMarkdownExtensionInListing:
    """A .markdown file is a first-class board: it shows in directory listings."""

    def test_markdown_file_appears_in_directory_listing(self, tmp_path: Path) -> None:
        project = tmp_path / "project"
        boards = project / "charts" / "docs"
        boards.mkdir(parents=True)
        (boards / "note.markdown").write_text("---\nstatus: ok\n---\n# Note\n")
        (boards / "other.md").write_text("# Other\n")
        with TestClient(
            create_server(FilesystemProject(project)),
            raise_server_exceptions=False,
        ) as client:
            r = client.get("/docs/")
        assert r.status_code == 200
        assert "note.markdown" in r.text
        assert "other.md" in r.text
