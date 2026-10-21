"""Regression tests for dct init → dct serve first-run onboarding bugs.

Anchors:
- root URL on a fresh init (no boards) shows the built-in intro page, not a
  welcome page from index.md (which would hijack / and is never scaffolded).
- inspect/ in root listing: clicking inspect/ links 500 because they need
  ?model=...&column=... params; hide the directory from the listing.
- inspect/ filter must be root-only: nested charts/reports/inspect/ should remain
  visible in the /reports/ listing.
- charts/dbt_charts.yml collision: DCT_ROOT_MARKERS includes "dbt_charts.yml", so
  find_project_root stops at charts/ when charts/dbt_charts.yml exists,
  treating charts/ as the project root — which has no sources: section, causing
  every chart to error "Source not found". The scaffold ships only meta.yml.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from dbt_charts.cli.filesystem_project import FilesystemProject
from dbt_charts.core.serve.server import create_server

# ---------------------------------------------------------------------------
# Shared fixture: a project laid out the way dct init produces
# ---------------------------------------------------------------------------


@pytest.fixture
def init_project(tmp_path: Path) -> Path:
    """Simulate dct init output via init_project()."""
    from dbt_charts.agent_api.init import init_project as _init

    _init(project_dir=tmp_path)
    return tmp_path


# ---------------------------------------------------------------------------
# Anchor 1: root URL on a fresh init project shows directory listing, not welcome page
# ---------------------------------------------------------------------------


class TestRootShowsIntroAfterInit:
    def test_root_of_a_project_with_no_boards_shows_the_intro_page(
        self, init_project: Path
    ) -> None:
        """After dct init there is no starter board: / explains what a board is
        and where to put one, instead of an empty listing."""
        with TestClient(create_server(FilesystemProject(init_project))) as client:
            response = client.get("/")
        assert response.status_code == 200, response.text[:500]
        assert "No boards yet" in response.text
        assert "charts/" in response.text
        assert "type: bar" in response.text
        assert "No boards or directories found" not in response.text

    def test_intro_page_gives_way_once_a_board_exists(self, init_project: Path) -> None:
        (init_project / "charts" / "sales.yml").write_text("title: Sales\n")
        with TestClient(
            create_server(FilesystemProject(init_project)),
            raise_server_exceptions=False,
        ) as client:
            response = client.get("/")
        assert "No boards yet" not in response.text
        assert "sales" in response.text


# ---------------------------------------------------------------------------
# Anchor 2: root listing does not expose inspect/
# ---------------------------------------------------------------------------


class TestInspectHiddenFromListing:
    def test_root_listing_does_not_contain_inspect_link(
        self, init_project: Path
    ) -> None:
        """charts/inspect/ must not appear in the root directory listing.
        inspect/ links 500 without required query params; hiding keeps new-user
        UX clean while leaving /inspect/<template>?model=... working.

        charts/inspect/ is created explicitly here because dct init no longer ejects
        it by default (PR #2900 flipped eject_inspect to False). init also no longer
        scaffolds index.md, so root falls through to the listing without any setup.
        The filter `if entry.name == "inspect" and not url_path: continue` in
        server.py is what this test pins — it only fires when the directory is present.
        """
        app = create_server(FilesystemProject(init_project))
        # Explicitly create charts/inspect/ so the filter has something to suppress,
        # and a board so the root shows the listing rather than the intro page.
        (init_project / "charts" / "inspect").mkdir()
        (init_project / "charts" / "sales.yml").write_text("title: Sales\n")
        with TestClient(app, raise_server_exceptions=False) as client:
            response = client.get("/")
        assert response.status_code == 200
        assert "inspect" not in response.text, (
            "inspect/ should be hidden from the root directory listing. "
            f"Found 'inspect' in body: {response.text[:800]}"
        )

    def test_nested_inspect_visible_in_subdirectory_listing(
        self, init_project: Path
    ) -> None:
        """A user-created charts/reports/inspect/ directory must NOT be hidden from
        the /reports/ listing — the filter is root-only."""
        reports_dir = init_project / "charts" / "reports"
        reports_dir.mkdir()
        (reports_dir / "inspect").mkdir()
        # Add a board so the listing is non-empty
        (reports_dir / "inspect" / "revenue.yml").write_text(
            "title: Revenue\ncharts:\n  rev: {type: table, query: {sql: 'SELECT 1 AS n'}}\n"
        )

        with TestClient(
            create_server(FilesystemProject(init_project)),
            raise_server_exceptions=False,
        ) as client:
            response = client.get("/reports/")
        assert response.status_code == 200
        assert "inspect" in response.text, (
            "charts/reports/inspect/ must appear in the /reports/ listing — "
            "the root-only inspect filter should not suppress nested directories. "
            f"Body: {response.text[:800]}"
        )


# ---------------------------------------------------------------------------
# Anchor 3: charts/dbt_charts.yml collision with DCT_ROOT_MARKERS
# ---------------------------------------------------------------------------


class TestProjectRootConfigNotCollided:
    """dct serve on a board under charts/ must resolve the ROOT dbt_charts.yml (with
    sources:), not a boards-local dbt_charts.yml scaffold that has no sources:.

    Regression for: dct init scaffolded charts/dbt_charts.yml, whose filename
    matches the DCT_ROOT_MARKERS sentinel "dbt_charts.yml" in project_roots.py.
    discover_render_context walks up from a board, stops at charts/dbt_charts.yml,
    and treats charts/ as the project root — so every chart errors "Source not found".
    Fix: scaffold no dbt_charts.yml under charts/.
    """

    def test_board_file_does_not_collide_with_project_markers(
        self, tmp_path: Path
    ) -> None:
        """A board under charts/ must NOT be mistaken for a project root marker.

        Verifies that find_project_root finds the real project root (tmp_path,
        which has dbt_charts.yml) and NOT charts/ (which has only boards).
        """
        from dbt_charts.core.project_roots import find_project_root

        # Simulate dct init layout: project root has dbt_charts.yml, charts/ has a board
        (tmp_path / "dbt_charts.yml").write_text("# project config\n")
        boards_dir = tmp_path / "charts"
        boards_dir.mkdir()
        (boards_dir / "sales.yml").write_text("title: Sales\n")

        project_root = find_project_root(boards_dir, boundary=None)
        assert project_root == tmp_path, (
            f"find_project_root must resolve to the project root ({tmp_path}), "
            f"not the charts/ dir ({boards_dir}). Got: {project_root}. "
            "a board file must not trigger the dbt_charts.yml DCT_ROOT_MARKERS sentinel."
        )

    def test_boards_dbt_charts_yml_would_collide(self, tmp_path: Path) -> None:
        """Confirm the collision: charts/dbt_charts.yml causes find_project_root to
        stop at charts/ instead of the real project root. This is the bug the rename fixes.
        """
        from dbt_charts.core.project_roots import find_project_root

        (tmp_path / "dbt_charts.yml").write_text("# project config\n")
        boards_dir = tmp_path / "charts"
        boards_dir.mkdir()
        # Deliberately plant the colliding file to document the bug
        (boards_dir / "dbt_charts.yml").write_text("title: Oops\n")

        project_root = find_project_root(boards_dir, boundary=None)
        # The collision: boards_dir is returned because dbt_charts.yml exists there
        assert project_root == boards_dir, (
            "Expected charts/ to be mistakenly returned as project root when "
            "charts/dbt_charts.yml exists — this is the collision bug the rename fixes. "
            f"Got: {project_root}"
        )


# ---------------------------------------------------------------------------
# Anchor 4: empty project (no charts/) boots and serves empty
# ---------------------------------------------------------------------------


class TestEmptyProjectServe:
    def test_empty_project_boots_and_serves_empty(self, tmp_path: Path) -> None:
        """A project with no charts/ dir boots and serves an empty surface.

        Root returns 200 (empty listing). Every board URL 404s. No assertion on
        boards_dir.is_dir() at boot.
        """
        with TestClient(
            create_server(FilesystemProject(tmp_path)),
            raise_server_exceptions=True,
        ) as client:
            root = client.get("/")
            assert root.status_code == 200
            sales = client.get("/sales/")
            assert sales.status_code == 404
            boards = client.get("/charts/")
            assert boards.status_code == 404
