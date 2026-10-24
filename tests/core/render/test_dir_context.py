"""Tests for built-in directory-navigation template variables.

Covers:
- list_dir_entries() shared helper (factored out of serve listing)
- lazy_dir_context() producing this_dir, parent_dir, siblings, tree
- url_mount_dir: URLs are stripped of the mounted prefix (charts/ always mounted at /)
- project-root edge cases (parent_dir is None at project root)
- tree depth bounding
- sandbox: no entries outside the project root
- Integration: markdown board at charts/reports/dash.yml renders {{ this_dir.name }}
  as "reports"; {{ parent_dir.url }} as the parent's URL; siblings lists neighbors;
  tree renders an indented listing.
- Serve-level: nav URLs omit the /charts/ prefix (charts/ is mounted at /).
- Serve listing still works after refactor (shared implementation).
- lazy_dir_context: siblings/tree deferred until first use; scans not called on
  boards that never reference those vars.
"""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path
from unittest.mock import patch

from dbt_charts.core.project import Project, ProjectDirectory

# ---------------------------------------------------------------------------
# Unit tests for list_dir_entries (shared helper)
# ---------------------------------------------------------------------------


class TestListDirEntries:
    def test_returns_yml_files(
        self, tmp_path: Path, local_project: Callable[..., Project]
    ) -> None:
        from dbt_charts.core.render.dir_context import list_dir_entries

        (tmp_path / "sales.yml").write_text("title: Sales\n")
        (tmp_path / "ops.yaml").write_text("title: Ops\n")
        project = local_project(tmp_path)
        entries = list_dir_entries(project.directory("."), "/")
        names = [e.name for e in entries]
        assert "sales.yml" in names
        assert "ops.yaml" in names

    def test_returns_md_files(
        self, tmp_path: Path, local_project: Callable[..., Project]
    ) -> None:
        from dbt_charts.core.render.dir_context import list_dir_entries

        (tmp_path / "readme.md").write_text("# Readme\n")
        project = local_project(tmp_path)
        entries = list_dir_entries(project.directory("."), "/")
        assert any(e.name == "readme.md" for e in entries)

    def test_returns_subdirectories(
        self, tmp_path: Path, local_project: Callable[..., Project]
    ) -> None:
        from dbt_charts.core.render.dir_context import list_dir_entries

        (tmp_path / "reports").mkdir()
        project = local_project(tmp_path)
        entries = list_dir_entries(project.directory("."), "/")
        assert any(e.name == "reports" and e.is_dir for e in entries)

    def test_excludes_dotfiles(
        self, tmp_path: Path, local_project: Callable[..., Project]
    ) -> None:
        from dbt_charts.core.render.dir_context import list_dir_entries

        (tmp_path / ".hidden").mkdir()
        (tmp_path / ".secret.yml").write_text("")
        project = local_project(tmp_path)
        entries = list_dir_entries(project.directory("."), "/")
        assert not any(e.name.startswith(".") for e in entries)

    def test_excludes_meta_yaml(
        self, tmp_path: Path, local_project: Callable[..., Project]
    ) -> None:
        """meta.yml/meta.yaml are cascade fragments, never standalone boards
        (ProjectPath.is_meta) — they must not be offered as a nav destination."""
        from dbt_charts.core.render.dir_context import list_dir_entries

        (tmp_path / "meta.yaml").write_text("style: {}\n")
        (tmp_path / "meta.yml").write_text("style: {}\n")
        (tmp_path / "sales.yml").write_text("title: Sales\n")
        project = local_project(tmp_path)
        entries = list_dir_entries(project.directory("."), "/")
        names = {e.name for e in entries}
        assert names == {"sales.yml"}

    def test_excludes_non_board_files(
        self, tmp_path: Path, local_project: Callable[..., Project]
    ) -> None:
        from dbt_charts.core.render.dir_context import list_dir_entries

        (tmp_path / "data.csv").write_text("a,b\n")
        (tmp_path / "notes.txt").write_text("notes\n")
        project = local_project(tmp_path)
        entries = list_dir_entries(project.directory("."), "/")
        assert not any(e.name in ("data.csv", "notes.txt") for e in entries)

    def test_entry_shape(
        self, tmp_path: Path, local_project: Callable[..., Project]
    ) -> None:
        from dbt_charts.core.render.dir_context import list_dir_entries

        (tmp_path / "sales.yml").write_text("title: Sales\n")
        project = local_project(tmp_path)
        entries = list_dir_entries(project.directory("."), "/reports")
        entry = next(e for e in entries if e.name == "sales.yml")
        assert entry.is_dir is False
        assert entry.ext == ".yml"
        assert entry.label == "sales"
        assert entry.url.startswith("/reports")
        # File URLs strip the extension (serve router resolves slugs without extension)
        assert entry.url == "/reports/sales"

    def test_dir_entry_url_has_trailing_slash(
        self, tmp_path: Path, local_project: Callable[..., Project]
    ) -> None:
        from dbt_charts.core.render.dir_context import list_dir_entries

        (tmp_path / "sub").mkdir()
        (tmp_path / "sub" / "a.yml").write_text("title: A\n")
        project = local_project(tmp_path)
        entries = list_dir_entries(project.directory("."), "/root")
        dir_entry = next(e for e in entries if e.is_dir)
        assert dir_entry.url.endswith("/")

    def test_sorts_dirs_before_files(
        self, tmp_path: Path, local_project: Callable[..., Project]
    ) -> None:
        from dbt_charts.core.render.dir_context import list_dir_entries

        (tmp_path / "aaa.yml").write_text("")
        (tmp_path / "bbb").mkdir()
        project = local_project(tmp_path)
        entries = list_dir_entries(project.directory("."), "/")
        assert entries[0].is_dir  # dirs first

    def test_excludes_skip_scan_dirs(
        self, tmp_path: Path, local_project: Callable[..., Project]
    ) -> None:
        """SKIP_SCAN_DIRS directory names are hidden from nav — pins the
        documented intent (project.py: "a dbt project root is a valid
        dbt charts root" and target/dbt_packages/logs/etc. are full of
        .yml/.md files that must not be enumerated as boards) as behavior,
        not just a comment. A normal subdir alongside them still surfaces."""
        from dbt_charts.core.project import SKIP_SCAN_DIRS
        from dbt_charts.core.render.dir_context import list_dir_entries

        non_dotfile_skip_dirs = {d for d in SKIP_SCAN_DIRS if not d.startswith(".")}
        for name in non_dotfile_skip_dirs:
            (tmp_path / name).mkdir()
        (tmp_path / "reports").mkdir()
        project = local_project(tmp_path)
        entries = list_dir_entries(project.directory("."), "/")
        assert {e.name for e in entries} == {"reports"}

    def test_excludes_escaping_symlink(
        self, tmp_path: Path, local_project: Callable[..., Project]
    ) -> None:
        """A symlink whose resolved target escapes the project root is
        excluded — os.scandir would otherwise happily follow it, so the
        exclusion is FilesystemProject.iter_dir's own containment guard, not
        a byproduct of something else. Pins the historical list_dir_entries
        sandbox behavior now that it lives on iter_dir."""
        from dbt_charts.core.render.dir_context import list_dir_entries

        project_root = tmp_path / "project"
        boards = project_root / "charts"
        boards.mkdir(parents=True)
        outside = tmp_path / "tasks"
        outside.mkdir()
        (boards / "tasks").symlink_to(Path("../../tasks"), target_is_directory=True)
        project = local_project(project_root)
        entries = list_dir_entries(project.directory("charts"), "/charts")
        assert not any(e.name == "tasks" for e in entries)


# ---------------------------------------------------------------------------
# Unit tests for _strip_mount_prefix
# ---------------------------------------------------------------------------


class TestStripMountPrefix:
    def test_strips_boards_prefix(self) -> None:
        from dbt_charts.core.render.dir_context import _strip_mount_prefix

        assert _strip_mount_prefix("/charts/reports/", "charts") == "/reports/"

    def test_strips_boards_prefix_exact_mount(self) -> None:
        from dbt_charts.core.render.dir_context import _strip_mount_prefix

        assert _strip_mount_prefix("/charts/", "charts") == "/"

    def test_noop_when_mount_dir_empty(self) -> None:
        from dbt_charts.core.render.dir_context import _strip_mount_prefix

        assert _strip_mount_prefix("/charts/reports/", "") == "/charts/reports/"

    def test_noop_when_prefix_does_not_match(self) -> None:
        from dbt_charts.core.render.dir_context import _strip_mount_prefix

        assert _strip_mount_prefix("/reports/", "charts") == "/reports/"


# ---------------------------------------------------------------------------
# Unit tests for lazy_dir_context (structural / value tests)
# ---------------------------------------------------------------------------


class TestBuildDirContext:
    def _make_project(
        self, tmp_path: Path, local_project: Callable[..., Project]
    ) -> tuple[Project, ProjectDirectory]:
        """Create: project/charts/reports/x.yml, y.yml.  Return (project, board_dir)."""
        project_root = tmp_path / "project"
        reports = project_root / "charts" / "reports"
        reports.mkdir(parents=True)
        (reports / "x.yml").write_text("title: X\n")
        (reports / "y.yml").write_text("title: Y\n")
        project = local_project(project_root)
        return project, project.directory("charts/reports")

    def test_takes_directory_directly(
        self, tmp_path: Path, local_project: Callable[..., Project]
    ) -> None:
        """lazy_dir_context accepts a ProjectDirectory, not a file path."""
        from dbt_charts.core.render.dir_context import lazy_dir_context

        _project, board_dir = self._make_project(tmp_path, local_project)
        ctx = lazy_dir_context(board_dir)
        assert "this_dir" in ctx

    def test_this_dir_name(
        self, tmp_path: Path, local_project: Callable[..., Project]
    ) -> None:
        from dbt_charts.core.render.dir_context import lazy_dir_context

        _project, board_dir = self._make_project(tmp_path, local_project)
        ctx = lazy_dir_context(board_dir)
        assert ctx["this_dir"]["name"] == "reports"

    def test_this_dir_path_relative_to_project(
        self, tmp_path: Path, local_project: Callable[..., Project]
    ) -> None:
        from dbt_charts.core.render.dir_context import lazy_dir_context

        _project, board_dir = self._make_project(tmp_path, local_project)
        ctx = lazy_dir_context(board_dir)
        assert ctx["this_dir"]["path"] == "charts/reports"

    def test_this_dir_url_without_mount_strip(
        self, tmp_path: Path, local_project: Callable[..., Project]
    ) -> None:
        """Without url_mount_dir, URL includes the full project-relative path."""
        from dbt_charts.core.render.dir_context import lazy_dir_context

        _project, board_dir = self._make_project(tmp_path, local_project)
        ctx = lazy_dir_context(board_dir)
        assert ctx["this_dir"]["url"] == "/charts/reports/"

    def test_this_dir_url_with_boards_mount_dir(
        self, tmp_path: Path, local_project: Callable[..., Project]
    ) -> None:
        """With url_mount_dir='charts', /charts/ prefix is stripped from URLs."""
        from dbt_charts.core.render.dir_context import lazy_dir_context

        _project, board_dir = self._make_project(tmp_path, local_project)
        ctx = lazy_dir_context(board_dir, url_mount_dir="charts")
        assert ctx["this_dir"]["url"] == "/reports/"

    def test_parent_dir_url_with_boards_mount_dir(
        self, tmp_path: Path, local_project: Callable[..., Project]
    ) -> None:
        from dbt_charts.core.render.dir_context import lazy_dir_context

        _project, board_dir = self._make_project(tmp_path, local_project)
        ctx = lazy_dir_context(board_dir, url_mount_dir="charts")
        pd = ctx["parent_dir"]
        assert pd is not None
        assert pd["url"] == "/"

    def test_siblings_url_with_boards_mount_dir(
        self, tmp_path: Path, local_project: Callable[..., Project]
    ) -> None:
        from dbt_charts.core.render.dir_context import lazy_dir_context

        _project, board_dir = self._make_project(tmp_path, local_project)
        ctx = lazy_dir_context(board_dir, url_mount_dir="charts")
        # All sibling URLs should not start with /charts/
        for s in ctx["siblings"]:
            assert not s["url"].startswith("/charts/"), s["url"]

    def test_parent_dir_at_depth(
        self, tmp_path: Path, local_project: Callable[..., Project]
    ) -> None:
        from dbt_charts.core.render.dir_context import lazy_dir_context

        _project, board_dir = self._make_project(tmp_path, local_project)
        ctx = lazy_dir_context(board_dir)
        pd = ctx["parent_dir"]
        assert pd is not None
        assert pd["name"] == "charts"

    def test_parent_dir_none_at_project_root(
        self, tmp_path: Path, local_project: Callable[..., Project]
    ) -> None:
        from dbt_charts.core.render.dir_context import lazy_dir_context

        project_root = tmp_path / "project"
        project_root.mkdir()
        project = local_project(project_root)
        ctx = lazy_dir_context(project.directory("."))
        assert ctx["parent_dir"] is None

    def test_siblings_includes_neighbors(
        self, tmp_path: Path, local_project: Callable[..., Project]
    ) -> None:
        from dbt_charts.core.render.dir_context import lazy_dir_context

        _project, board_dir = self._make_project(tmp_path, local_project)
        ctx = lazy_dir_context(board_dir)
        sibling_names = [s["name"] for s in ctx["siblings"]]
        assert "x.yml" in sibling_names
        assert "y.yml" in sibling_names

    def test_siblings_excludes_meta_yaml(
        self, tmp_path: Path, local_project: Callable[..., Project]
    ) -> None:
        """meta.yml is a cascade fragment, not a board — siblings must not
        offer it as a nav destination (it can never compile as a board)."""
        from dbt_charts.core.render.dir_context import lazy_dir_context

        project, board_dir = self._make_project(tmp_path, local_project)
        (project.root / "charts" / "reports" / "meta.yml").write_text("style: {}\n")
        ctx = lazy_dir_context(board_dir)
        sibling_names = [s["name"] for s in ctx["siblings"]]
        assert "meta.yml" not in sibling_names
        # Guard against a vacuous pass: real siblings still list.
        assert "x.yml" in sibling_names

    def test_siblings_one_level_only(
        self, tmp_path: Path, local_project: Callable[..., Project]
    ) -> None:
        """Siblings are only entries in the board's own directory, not recursive."""
        from dbt_charts.core.render.dir_context import lazy_dir_context

        project, board_dir = self._make_project(tmp_path, local_project)
        sub = project.root / "charts" / "reports" / "subdir"
        sub.mkdir()
        (sub / "deep.yml").write_text("")
        ctx = lazy_dir_context(board_dir)
        sibling_names = [s["name"] for s in ctx["siblings"]]
        assert "deep.yml" not in sibling_names

    def test_tree_is_string(
        self, tmp_path: Path, local_project: Callable[..., Project]
    ) -> None:
        from dbt_charts.core.render.dir_context import lazy_dir_context

        _project, board_dir = self._make_project(tmp_path, local_project)
        ctx = lazy_dir_context(board_dir)
        tree_str = str(ctx["tree"])
        assert isinstance(tree_str, str)
        assert len(tree_str) > 0

    def test_tree_contains_filenames(
        self, tmp_path: Path, local_project: Callable[..., Project]
    ) -> None:
        from dbt_charts.core.render.dir_context import lazy_dir_context

        _project, board_dir = self._make_project(tmp_path, local_project)
        ctx = lazy_dir_context(board_dir)
        assert "x.yml" in str(ctx["tree"]) or "x" in str(ctx["tree"])

    def test_tree_bounded_depth(
        self, tmp_path: Path, local_project: Callable[..., Project]
    ) -> None:
        """Tree depth is bounded — very deep nesting is truncated."""
        from dbt_charts.core.compile.config import get_inspector_config
        from dbt_charts.core.render.dir_context import lazy_dir_context

        project_root = tmp_path / "project"
        deep = project_root / "charts"
        for i in range(get_inspector_config().tree_max_depth + 3):
            deep = deep / f"level{i}"
        deep.mkdir(parents=True)
        (deep / "leaf.yml").write_text("")
        project = local_project(project_root)
        ctx = lazy_dir_context(project.directory("charts"))
        assert "leaf.yml" not in str(ctx["tree"])

    def test_no_entries_outside_project(
        self, tmp_path: Path, local_project: Callable[..., Project]
    ) -> None:
        """Siblings must not expose entries outside the project root."""
        from dbt_charts.core.render.dir_context import lazy_dir_context

        project_root = tmp_path / "project"
        project_root.mkdir()
        boards = project_root / "charts"
        boards.mkdir()
        (tmp_path / "outside.yml").write_text("")
        project = local_project(project_root)
        ctx = lazy_dir_context(project.directory("charts"))
        sibling_names = [s["name"] for s in ctx["siblings"]]
        assert "outside.yml" not in sibling_names


class TestRootBreadcrumbName:
    def test_root_this_dir_name_uses_project_name_not_root_basename(
        self, tmp_path: Path, in_memory_project: Callable[..., Project]
    ) -> None:
        """The root breadcrumb reads ``Project.name``, not ``Project.root``'s
        basename. Under a git-blob host (Cloud), root is the worker CWD, not
        project identity, so a regression back to root-basename would not be
        caught by a ``FilesystemProject``-backed test (there the two happen
        to coincide) — the shared double is given an explicit ``name`` that
        differs from its root's basename to catch it."""
        from dbt_charts.core.render.dir_context import lazy_dir_context

        project = in_memory_project(
            tmp_path / "worker-cwd-abc123", {}, name="Acme Analytics"
        )
        ctx = lazy_dir_context(project.directory("."))
        assert ctx["this_dir"]["name"] == "Acme Analytics"


# ---------------------------------------------------------------------------
# Integration: board text resolves the built-in variables
# ---------------------------------------------------------------------------


def _make_board_with_template(path: Path, template: str) -> None:
    """Write a board YAML with a single-line text expression."""
    path.write_text(f"title: Dir Test\ntext: {template}\n", encoding="utf-8")


class TestDirContextInFaceText:
    """Integration tests: markdown board text resolves the built-in variables.

    We use single-line text values to avoid mdsvg wrapping the output across
    tspan elements (which would split e.g. 'dir_name=reports' mid-word).
    """

    def test_this_dir_name_in_text(
        self, tmp_path: Path, local_project: Callable[..., Project]
    ) -> None:
        from .._svg_render import render_board_file

        reports = tmp_path / "charts" / "reports"
        reports.mkdir(parents=True)
        board = reports / "dash.yml"
        _make_board_with_template(board, "dir={{ this_dir.name }}")
        svg = render_board_file(board, project=local_project(tmp_path))
        assert isinstance(svg, str)
        assert "dir=reports" in svg

    def test_parent_dir_url_in_text(
        self, tmp_path: Path, local_project: Callable[..., Project]
    ) -> None:
        from .._svg_render import render_board_file

        reports = tmp_path / "charts" / "reports"
        reports.mkdir(parents=True)
        board = reports / "dash.yml"
        _make_board_with_template(
            board, "purl={{ parent_dir.url if parent_dir else 'NOPARENT' }}"
        )
        svg = render_board_file(board, project=local_project(tmp_path))
        assert isinstance(svg, str)
        # render_board_file (no link_context) → no mount strip → /charts/ prefix present
        assert "purl=/charts/" in svg

    def test_siblings_list_is_list(
        self, tmp_path: Path, local_project: Callable[..., Project]
    ) -> None:
        from .._svg_render import render_board_file

        reports = tmp_path / "charts" / "reports"
        reports.mkdir(parents=True)
        board = reports / "dash.yml"
        (reports / "other.yml").write_text("title: Other\n")
        _make_board_with_template(board, "sc={{ siblings | length }}")
        svg = render_board_file(board, project=local_project(tmp_path))
        assert isinstance(svg, str)
        assert "sc=2" in svg

    def test_tree_renders_nonempty(
        self, tmp_path: Path, local_project: Callable[..., Project]
    ) -> None:
        from .._svg_render import render_board_file

        reports = tmp_path / "charts" / "reports"
        reports.mkdir(parents=True)
        board = reports / "dash.yml"
        _make_board_with_template(board, "ht={{ 'yes' if tree else 'no' }}")
        svg = render_board_file(board, project=local_project(tmp_path))
        assert isinstance(svg, str)
        assert "ht=yes" in svg

    def test_parent_dir_none_at_project_root(
        self, tmp_path: Path, local_project: Callable[..., Project]
    ) -> None:
        from .._svg_render import render_board_file

        project = tmp_path / "project"
        project.mkdir()
        board = project / "index.yml"
        _make_board_with_template(
            board, "purl={{ parent_dir.url if parent_dir else 'NOPARENT' }}"
        )
        svg = render_board_file(board, project=local_project(project))
        assert isinstance(svg, str)
        assert "purl=NOPARENT" in svg


# ---------------------------------------------------------------------------
# Serve-level integration: /charts/ prefix stripped from nav URLs
# ---------------------------------------------------------------------------


class TestBoardsAtRootUrlStripping:
    """Verify that nav URLs don't include /charts/ (charts/ is mounted at /).

    The serve router mounts charts/ at / and explicitly 404s /charts/* requests.
    lazy_dir_context must strip the charts/ prefix so emitted links resolve.
    """

    def test_this_dir_url_strips_boards_prefix_via_serve(self, tmp_path: Path) -> None:
        from fastapi.testclient import TestClient

        from dbt_charts.cli.filesystem_project import FilesystemProject
        from dbt_charts.core.serve.server import create_server

        reports = tmp_path / "charts" / "reports"
        reports.mkdir(parents=True)
        (reports / "alpha.yml").write_text("title: Alpha\ntext: hello\n")
        # The index board renders this_dir.url into its text
        (reports / "index.yml").write_text(
            "title: Reports\ntext: navurl={{ this_dir.url }}\n"
        )

        with TestClient(
            create_server(FilesystemProject(tmp_path)),
            raise_server_exceptions=False,
        ) as client:
            # /reports/ renders charts/reports/index.yml via the nested-index route
            response = client.get("/reports/")
        assert response.status_code == 200
        # URL must be /reports/ not /charts/reports/ — the latter would 404
        assert "navurl=/reports/" in response.text
        assert "/charts/reports/" not in response.text

    def test_siblings_urls_resolve_via_serve(self, tmp_path: Path) -> None:
        """Sibling URLs generated by the built-ins must actually resolve (not 404)."""
        from fastapi.testclient import TestClient

        from dbt_charts.cli.filesystem_project import FilesystemProject
        from dbt_charts.core.serve.server import create_server

        reports = tmp_path / "charts" / "reports"
        reports.mkdir(parents=True)
        (reports / "alpha.yml").write_text("title: Alpha\ntext: hello\n")
        (reports / "index.yml").write_text(
            "title: Reports\ntext: first_sibling={{ siblings[0].url }}\n"
        )

        with TestClient(
            create_server(FilesystemProject(tmp_path)),
            raise_server_exceptions=False,
        ) as client:
            response = client.get("/reports/")
        assert response.status_code == 200
        # Sibling URL must be /reports/alpha (or /reports/index), not /charts/reports/alpha
        # Extract the sibling URL from the rendered text
        assert "/charts/" not in response.text.split("first_sibling=")[-1].split("<")[0]


# ---------------------------------------------------------------------------
# Regression: _render_directory_listing still works after refactor
# (the shared implementation powers both the serve listing and the variables)
# ---------------------------------------------------------------------------


class TestServeListingStillWorksAfterRefactor:
    def test_serve_listing_renders(self, tmp_path: Path) -> None:
        from fastapi.testclient import TestClient

        from dbt_charts.cli.filesystem_project import FilesystemProject
        from dbt_charts.core.serve.server import create_server

        boards = tmp_path / "charts"
        boards.mkdir()
        (boards / "alpha.yml").write_text("title: Alpha\ntext: hello\n")

        with TestClient(
            create_server(FilesystemProject(tmp_path)),
            raise_server_exceptions=False,
        ) as client:
            response = client.get("/")
        assert response.status_code == 200
        assert "alpha" in response.text


# ---------------------------------------------------------------------------
# lazy_dir_context: scans deferred until first use
# ---------------------------------------------------------------------------


def _make_project(
    tmp_path: Path, local_project: Callable[..., Project]
) -> tuple[Project, ProjectDirectory]:
    """Create project/charts/reports/x.yml, y.yml. Return (project, board_dir)."""
    project_root = tmp_path / "project"
    reports = project_root / "charts" / "reports"
    reports.mkdir(parents=True)
    (reports / "x.yml").write_text("title: X\n")
    (reports / "y.yml").write_text("title: Y\n")
    project = local_project(project_root)
    return project, project.directory("charts/reports")


class TestLazyDirContext:
    def test_lazy_dir_context_exists(
        self, tmp_path: Path, local_project: Callable[..., Project]
    ) -> None:
        """lazy_dir_context is importable and returns a dict."""
        from dbt_charts.core.render.dir_context import lazy_dir_context

        _project, board_dir = _make_project(tmp_path, local_project)
        ctx = lazy_dir_context(board_dir)
        assert "siblings" in ctx
        assert "tree" in ctx
        assert "this_dir" in ctx
        assert "parent_dir" in ctx

    def test_scans_not_called_on_construction(
        self, tmp_path: Path, local_project: Callable[..., Project]
    ) -> None:
        """Building lazy_dir_context must NOT call list_dir_entries or _render_tree."""
        from dbt_charts.core.render.dir_context import lazy_dir_context

        _project, board_dir = _make_project(tmp_path, local_project)

        with (
            patch("dbt_charts.core.render.dir_context.list_dir_entries") as mock_list,
            patch("dbt_charts.core.render.dir_context._render_tree") as mock_tree,
        ):
            lazy_dir_context(board_dir)
            assert mock_list.call_count == 0
            assert mock_tree.call_count == 0

    def test_siblings_triggers_scan_on_access(
        self, tmp_path: Path, local_project: Callable[..., Project]
    ) -> None:
        """Iterating siblings triggers exactly one list_dir_entries call."""
        from dbt_charts.core.render.dir_context import DirEntry, lazy_dir_context

        project, board_dir = _make_project(tmp_path, local_project)

        def fake_list_dir(
            dir_handle: ProjectDirectory, url_prefix: str
        ) -> list[DirEntry]:
            return [
                DirEntry(
                    name="x.yml",
                    url="/reports/x",
                    is_dir=False,
                    ext=".yml",
                    label="x",
                    handle=project.path("charts/reports/x.yml"),
                )
            ]

        with patch(
            "dbt_charts.core.render.dir_context.list_dir_entries",
            side_effect=fake_list_dir,
        ) as mock_list:
            ctx = lazy_dir_context(board_dir)
            assert mock_list.call_count == 0
            siblings = list(ctx["siblings"])
            assert mock_list.call_count == 1
            # Iterating again must NOT trigger a second call (memoized).
            _ = list(ctx["siblings"])
            assert mock_list.call_count == 1
        assert any(s["name"] == "x.yml" for s in siblings)

    def test_tree_triggers_render_on_access(
        self, tmp_path: Path, local_project: Callable[..., Project]
    ) -> None:
        """Accessing tree as str triggers exactly one _render_tree call."""
        from dbt_charts.core.render.dir_context import lazy_dir_context

        _project, board_dir = _make_project(tmp_path, local_project)

        with patch(
            "dbt_charts.core.render.dir_context._render_tree",
            return_value=["- [x.yml](/reports/x)"],
        ) as mock_tree:
            ctx = lazy_dir_context(board_dir)
            assert mock_tree.call_count == 0
            tree_str = str(ctx["tree"])
            assert mock_tree.call_count == 1
            # Access again — should not recompute.
            _ = str(ctx["tree"])
            assert mock_tree.call_count == 1
        assert "x.yml" in tree_str

    def test_this_dir_and_parent_dir_are_eager(
        self, tmp_path: Path, local_project: Callable[..., Project]
    ) -> None:
        """this_dir and parent_dir are real dicts, not lazy proxies."""
        from dbt_charts.core.render.dir_context import lazy_dir_context

        _project, board_dir = _make_project(tmp_path, local_project)

        with (
            patch("dbt_charts.core.render.dir_context.list_dir_entries") as mock_list,
            patch("dbt_charts.core.render.dir_context._render_tree") as mock_tree,
        ):
            ctx = lazy_dir_context(board_dir)
            assert ctx["this_dir"]["name"] == "reports"
            assert ctx["parent_dir"] is not None
            assert ctx["parent_dir"]["name"] == "charts"
            # Still no scans triggered
            assert mock_list.call_count == 0
            assert mock_tree.call_count == 0


class TestRendererUsesLazyDirContext:
    """Render-level: scans not called when a board never references siblings/tree."""

    def _make_board(self, tmp_path: Path, template: str) -> tuple[Path, Path]:
        reports = tmp_path / "charts" / "reports"
        reports.mkdir(parents=True)
        (reports / "other.yml").write_text("title: Other\n")
        board = reports / "dash.yml"
        board.write_text(f"title: Dir Test\ntext: {template}\n")
        return tmp_path, board

    def test_no_scan_when_board_does_not_reference_siblings_or_tree(
        self, tmp_path: Path, local_project: Callable[..., Project]
    ) -> None:
        """A board that only uses this_dir must not trigger list_dir_entries or _render_tree."""
        from .._svg_render import render_board_file

        project, board = self._make_board(tmp_path, "name={{ this_dir.name }}")

        with (
            patch("dbt_charts.core.render.dir_context.list_dir_entries") as mock_list,
            patch("dbt_charts.core.render.dir_context._render_tree") as mock_tree,
        ):
            svg = render_board_file(board, project=local_project(project))
            assert mock_list.call_count == 0
            assert mock_tree.call_count == 0
        assert isinstance(svg, str)
        assert "name=reports" in svg

    def test_scan_called_when_board_references_siblings(
        self, tmp_path: Path, local_project: Callable[..., Project]
    ) -> None:
        """A board using {{ siblings | length }} triggers the scan exactly once."""
        from .._svg_render import render_board_file

        project, board = self._make_board(tmp_path, "sc={{ siblings | length }}")
        svg = render_board_file(board, project=local_project(project))
        assert isinstance(svg, str)
        # dash.yml + other.yml = 2 siblings
        assert "sc=2" in svg

    def test_scan_called_when_board_references_tree(
        self, tmp_path: Path, local_project: Callable[..., Project]
    ) -> None:
        """A board that uses {{ tree }} renders a non-empty tree string."""
        from .._svg_render import render_board_file

        project, board = self._make_board(tmp_path, "ht={{ 'yes' if tree else 'no' }}")
        svg = render_board_file(board, project=local_project(project))
        assert isinstance(svg, str)
        assert "ht=yes" in svg


# ---------------------------------------------------------------------------
# Regression: dir-context injection seam — builtin_variables param
# These tests pin the behavior that the seam move (render() loses project_dir/
# board_dir/url_mount_dir, callers pass builtin_variables) must preserve.
# ---------------------------------------------------------------------------


class TestBuiltinVariables:
    """render() accepts builtin_variables merged UNDER user variables."""

    def test_render_accepts_builtin_variables(
        self, local_project: Callable[..., Project]
    ) -> None:
        """render() must accept a builtin_variables kwarg and expose its values in templates."""
        from dbt_charts.core.compile import compile
        from dbt_charts.core.execute import Executor
        from dbt_charts.core.execute.adapters import AdapterRegistry
        from dbt_charts.core.render.renderer import render

        result = compile("title: Test\ntext: val={{ builtin_key }}\n")
        assert result.board is not None
        executor = Executor(
            result.board,
            adapter_registry=AdapterRegistry(project=local_project(Path())),
            query_registry=result.query_registry,
            result_cache=None,
        )
        render_result = render(
            result.board, executor, builtin_variables={"builtin_key": "hello"}
        )
        assert render_result.output is not None
        assert "val=hello" in str(render_result.output)

    def test_builtin_variables_sit_under_user_variables(
        self, local_project: Callable[..., Project]
    ) -> None:
        """User-declared variable defaults shadow builtin_variables (builtin goes in first)."""
        from dbt_charts.core.compile import compile
        from dbt_charts.core.execute import Executor
        from dbt_charts.core.execute.adapters import AdapterRegistry
        from dbt_charts.core.render.renderer import render

        result = compile(
            "title: Precedence\ntext: val={{ my_var }}\n"
            "variables:\n  my_var:\n    default: from_var\n"
        )
        assert result.board is not None
        executor = Executor(
            result.board,
            adapter_registry=AdapterRegistry(project=local_project(Path())),
            query_registry=result.query_registry,
            result_cache=None,
        )
        # builtin supplies "from_builtin"; user registry default "from_var" must win
        render_result = render(
            result.board,
            executor,
            builtin_variables={"my_var": "from_builtin"},
        )
        assert render_result.output is not None
        assert "val=from_var" in str(render_result.output)

    def test_runtime_variables_shadow_builtin_variables(
        self, local_project: Callable[..., Project]
    ) -> None:
        """Runtime variables= passed by the caller shadow builtin_variables values.

        Guards the merge order: {**builtin_variables, **merged_variables} — a reversed
        merge would only be caught by the registry-default test above for one of the
        two precedence layers.
        """
        from dbt_charts.core.compile import compile
        from dbt_charts.core.execute import Executor
        from dbt_charts.core.execute.adapters import AdapterRegistry
        from dbt_charts.core.render.renderer import render

        result = compile("title: Runtime Shadow\ntext: val={{ my_var }}\n")
        assert result.board is not None
        executor = Executor(
            result.board,
            adapter_registry=AdapterRegistry(project=local_project(Path())),
            query_registry=result.query_registry,
            result_cache=None,
        )
        # builtin supplies "from_builtin"; runtime caller passes "from_url" — must win
        render_result = render(
            result.board,
            executor,
            variables={"my_var": "from_url"},
            builtin_variables={"my_var": "from_builtin"},
        )
        assert render_result.output is not None
        assert "val=from_url" in str(render_result.output)

    def test_none_builtin_variables_is_valid(
        self, local_project: Callable[..., Project]
    ) -> None:
        """builtin_variables=None must work the same as omitting it."""
        from dbt_charts.core.compile import compile
        from dbt_charts.core.execute import Executor
        from dbt_charts.core.execute.adapters import AdapterRegistry
        from dbt_charts.core.render.renderer import render

        result = compile("title: Test\ntext: val={{ builtin_key }}\n")
        assert result.board is not None
        executor = Executor(
            result.board,
            adapter_registry=AdapterRegistry(project=local_project(Path())),
            query_registry=result.query_registry,
            result_cache=None,
        )
        # builtin_key is undefined — Jinja renders '' for undefined
        render_result = render(result.board, executor, builtin_variables=None)
        assert render_result.output is not None


class TestDirContextThroughCallers:
    """Behavior-preservation: dir-context injection reaches render via both callers."""

    def test_render_dashboard_injects_this_dir_name(
        self, tmp_path: Path, local_project: Callable[..., Project]
    ) -> None:
        """render_dashboard must inject this_dir.name so a board can render the dirname."""
        from dbt_charts.core.board import render_dashboard
        from dbt_charts.core.execute.adapters import build_adapter_registry

        reports = tmp_path / "charts" / "reports"
        reports.mkdir(parents=True)
        board_path = reports / "dash.yml"
        board_path.write_text("title: Dir Test\ntext: dn={{ this_dir.name }}\n")

        project = local_project(tmp_path)
        registry = build_adapter_registry(project, read_only=True)
        try:
            result = render_dashboard(
                board=project.path("charts/reports/dash.yml").read_board(),
                project=project,
                adapter_registry=registry,
                result_cache=None,
                format="svg",
            )
        finally:
            registry.close()

        assert result.status == "ok"
        assert result.data is not None
        assert "dn=reports" in str(result.data)

    def test_render_dashboard_explicit_builtin_variables_override_wins(
        self, tmp_path: Path, local_project: Callable[..., Project]
    ) -> None:
        """An explicit builtin_variables override takes priority over the
        dir-nav vars render_dashboard would otherwise derive from board.path.

        This is the capability the markdown integration relies on to anchor an
        in-memory board at a real on-disk location that differs from the
        BoardFile's own anchor. render_dashboard does no filesystem walking of
        its own once builtin_variables is supplied.
        """
        from dbt_charts.core.board import render_dashboard
        from dbt_charts.core.execute.adapters import build_adapter_registry
        from dbt_charts.core.project import InMemoryBoard
        from dbt_charts.core.render.dir_context import lazy_dir_context

        reports = tmp_path / "charts" / "reports"
        reports.mkdir(parents=True)
        yaml_content = "title: Dir Test\ntext: dn={{ this_dir.name }}\n"

        project = local_project(tmp_path)
        registry = build_adapter_registry(project, read_only=True)
        try:
            result = render_dashboard(
                board=InMemoryBoard(
                    yaml_content, path=project.path("charts/_elsewhere.yml")
                ),
                project=project,
                adapter_registry=registry,
                result_cache=None,
                builtin_variables=lazy_dir_context(project.directory("charts/reports")),
                format="svg",
            )
        finally:
            registry.close()

        assert result.status == "ok"
        assert "dn=reports" in str(result.data)

    def test_located_board_gets_dir_nav_but_pathless_board_does_not(
        self, tmp_path: Path, local_project: Callable[..., Project]
    ) -> None:
        """A store-located board (path is not None) gets dir-nav vars from its
        anchor; a pathless in-memory board (path=None) does not — there is no
        real anchor to trigger a filesystem dir scan (matching the Cloud
        git-blob path, which never has a local anchor to scan)."""
        from dbt_charts.core.board import render_dashboard
        from dbt_charts.core.execute.adapters import build_adapter_registry
        from dbt_charts.core.project import InMemoryBoard

        project = local_project(tmp_path)
        registry = build_adapter_registry(project, read_only=True)
        yaml_content = (
            "title: InMemory\n"
            "text: has_dir={{ 'yes' if this_dir is defined else 'no' }}\n"
        )
        (tmp_path / "charts").mkdir(exist_ok=True)
        (tmp_path / "charts" / "_t.yml").write_text(yaml_content)
        try:
            located = render_dashboard(
                board=project.path("charts/_t.yml").read_board(),
                project=project,
                adapter_registry=registry,
                result_cache=None,
                format="svg",
            )
            pathless = render_dashboard(
                board=InMemoryBoard(yaml_content, path=None),
                project=project,
                adapter_registry=registry,
                result_cache=None,
                format="svg",
            )
        finally:
            registry.close()

        assert located.status == "ok"
        assert "has_dir=yes" in str(located.data)
        assert pathless.status == "ok"
        assert "has_dir=no" in str(pathless.data)

    def test_render_dashboard_empty_builtin_variables_override_suppresses_dir_nav(
        self, tmp_path: Path, local_project: Callable[..., Project]
    ) -> None:
        """A caller can still opt out of dir-nav injection via an explicit
        empty builtin_variables override."""
        from dbt_charts.core.board import render_dashboard
        from dbt_charts.core.execute.adapters import build_adapter_registry
        from dbt_charts.core.project import InMemoryBoard

        project = local_project(tmp_path)
        registry = build_adapter_registry(project, read_only=True)
        yaml_content = (
            "title: InMemory\n"
            "text: has_dir={{ 'yes' if this_dir is defined else 'no' }}\n"
        )
        try:
            result = render_dashboard(
                board=InMemoryBoard(yaml_content, path=project.path("charts/_t.yml")),
                project=project,
                adapter_registry=registry,
                result_cache=None,
                builtin_variables={},
                format="svg",
            )
        finally:
            registry.close()

        assert result.status == "ok"
        assert "has_dir=no" in str(result.data)


class TestDirectoryListing:
    """The listing page a host shows for a directory with no index board."""

    def test_lists_folders_first_then_boards_under_an_up_link(
        self, tmp_path: Path, local_project: Callable[..., Project]
    ) -> None:
        from dbt_charts.core.compile import compile
        from dbt_charts.core.render.dir_context import directory_listing

        (tmp_path / "charts" / "sales" / "emea").mkdir(parents=True)
        (tmp_path / "charts" / "sales" / "trend.yml").write_text("title: Trend\n")
        project = local_project(tmp_path)

        board_text, variables = directory_listing(
            [project.directory("charts/sales")], "sales", project
        )

        assert variables == {
            "directory_title": "/sales",
            "listing_markdown": "- [../](/)\n- [emea/](/sales/emea/)\n"
            "- [trend.yml](/sales/trend)",
        }
        assert compile(board_text).board is not None

    def test_the_root_listing_has_no_up_link(
        self, tmp_path: Path, local_project: Callable[..., Project]
    ) -> None:
        from dbt_charts.core.render.dir_context import directory_listing

        (tmp_path / "charts").mkdir()
        (tmp_path / "charts" / "overview.yml").write_text("title: Overview\n")
        project = local_project(tmp_path)

        _, variables = directory_listing([project.directory("charts")], "", project)

        assert variables == {
            "directory_title": "/",
            "listing_markdown": "- [overview.yml](/overview)",
        }
